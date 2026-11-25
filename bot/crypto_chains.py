"""Read-only blockchain adapters. Credentials in this module cannot sign payments."""

import base64
import re
import time
from decimal import Decimal

import httpx

from .crypto_payments import (
    TON_USDT,
    TRON_USDT,
    CryptoPaymentError,
    Deposit,
    PaymentConfig,
    invoice_targets,
    network_state,
    record_deposit,
    save_network_state,
    ton_address_key,
    tron_address_key,
)


TRANSFER_EVENT = "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
JETTON_NOTIFICATION = 0x7362D09C
BATCH_SIZE = 80


class ChainScanner:
    def __init__(self, client: httpx.AsyncClient, config: PaymentConfig):
        self.client = client
        self.config = config
        self.btc_addresses_verified = False
        self.btc_seen = set()
        self.ton_usdt_wallet = None

    async def request(self, network: str, path: str = "", *, params=None, body=None):
        cfg = self.config.networks[network]
        auth = (cfg.username, cfg.password) if cfg.username or cfg.password else None
        response = await self.client.request(
            "GET" if body is None else "POST", cfg.url + path,
            json=body, params=params, auth=auth,
        )
        response.raise_for_status()
        return response.json(parse_float=Decimal)

    async def rpc(self, network: str, method: str, *params):
        response = await self.request(network, body={"jsonrpc": "2.0", "id": 1, "method": method, "params": list(params)})
        if response.get("error") or "result" not in response:
            raise CryptoPaymentError(f"Ошибка RPC {network} при вызове {method}.")
        return response["result"]

    def initial_cursor(self, network: str, head: int) -> int:
        cursor, _ = network_state(network)
        if cursor is None:
            if invoice_targets(network):
                raise CryptoPaymentError(f"Потеряна позиция считывания {network}. Восстановите базу платежей.")
            save_network_state(network, head)
            return head
        return cursor

    def ready(self, network: str, cursor: int) -> None:
        save_network_state(network, cursor, ready_at=int(time.time()), config_hash=self.config.fingerprint(network))

    async def scan_eth(self) -> None:
        if await self.rpc("eth", "eth_chainId") != "0x1":
            raise CryptoPaymentError("RPC Ethereum должен указывать на Ethereum Mainnet.")
        head = await self.rpc("eth", "eth_getBlockByNumber", "finalized", False)
        head_height = int(head["number"], 16)
        cursor = self.initial_cursor("eth", head_height)
        targets = invoice_targets("eth")
        for height in range(cursor + 1, min(cursor + BATCH_SIZE, head_height) + 1):
            block = await self.rpc("eth", "eth_getBlockByNumber", hex(height), True)
            if int(block["number"], 16) != height:
                raise CryptoPaymentError("Нода Ethereum вернула неожиданный блок.")
            block_time = int(block["timestamp"], 16)
            for tx in block["transactions"]:
                address = str(tx.get("to") or "").lower()
                if address not in targets or int(tx["value"], 16) <= 0:
                    continue
                receipt = await self.rpc("eth", "eth_getTransactionReceipt", tx["hash"])
                if not receipt or receipt["blockHash"] != block["hash"]:
                    raise CryptoPaymentError("Нода Ethereum ещё не вернула квитанцию.")
                if receipt["status"] == "0x1":
                    record_deposit(Deposit("eth", tx["hash"].lower(), address, int(tx["value"], 16), block_time))
            cursor = height
            save_network_state("eth", cursor)
        if cursor == head_height and int(time.time()) - int(head["timestamp"], 16) <= 1800:
            self.ready("eth", cursor)

    async def scan_tron(self) -> None:
        head = await self.request("tron", "/walletsolidity/getnowblock", body={})
        header = head["block_header"]["raw_data"]
        head_height = int(header["number"])
        cursor = self.initial_cursor("tron", head_height)
        targets = invoice_targets("tron")
        for height in range(cursor + 1, min(cursor + BATCH_SIZE, head_height) + 1):
            block = await self.request("tron", "/walletsolidity/getblockbynum", body={"num": height})
            block_header = block["block_header"]["raw_data"]
            if int(block_header["number"]) != height:
                raise CryptoPaymentError("Нода TRON вернула неожиданный блок.")
            if targets and block.get("transactions"):
                receipts = await self.request("tron", "/walletsolidity/gettransactioninfobyblocknum", body={"num": height})
                if {row["id"] for row in receipts} != {tx["txID"] for tx in block["transactions"]}:
                    raise CryptoPaymentError("Нода TRON ещё не вернула все квитанции блока.")
                for receipt in receipts:
                    if int(receipt["blockNumber"]) != height:
                        raise CryptoPaymentError("Квитанция TRON не соответствует блоку.")
                    for deposit in tron_deposits(receipt, targets, int(block_header["timestamp"]) // 1000):
                        record_deposit(deposit)
            cursor = height
            save_network_state("tron", cursor)
        if cursor == head_height and int(time.time()) - int(header["timestamp"]) // 1000 <= 300:
            self.ready("tron", cursor)

    async def scan_btc(self) -> None:
        cfg = self.config.networks["btc"]
        chain = await self.rpc("btc", "getblockchaininfo")
        wallet = await self.rpc("btc", "getwalletinfo")
        if chain["chain"] != "main" or wallet["private_keys_enabled"] is not False:
            raise CryptoPaymentError("Нужен watch-only кошелёк Bitcoin Mainnet.")
        if chain["initialblockdownload"] or wallet.get("scanning"):
            raise CryptoPaymentError("Синхронизация Bitcoin ещё не завершена.")
        wallet_height = (wallet.get("lastprocessedblock") or {}).get("height")
        if wallet_height is not None and wallet_height < chain["blocks"]:
            raise CryptoPaymentError("Наблюдающий кошелёк Bitcoin ещё догоняет ноду.")
        if not self.btc_addresses_verified:
            watches = invoice_targets("btc") | {address for address, _ in cfg.addresses}
            for address in watches:
                info = await self.rpc("btc", "getaddressinfo", address)
                if not (info.get("iswatchonly") or info.get("ismine")):
                    raise CryptoPaymentError("В watch-only кошельке Bitcoin отсутствует адрес из пула.")
            self.btc_addresses_verified = True
        targets = invoice_targets("btc")
        if targets:
            received = await self.rpc("btc", "listreceivedbyaddress", cfg.confirmations, False, True)
            txids = {txid for entry in received if entry["address"] in targets for txid in entry["txids"]}
            for txid in sorted(txids - self.btc_seen):
                tx = await self.rpc("btc", "gettransaction", txid, True)
                if int(tx["confirmations"]) < cfg.confirmations:
                    continue
                for detail in tx["details"]:
                    if detail.get("category") == "receive" and detail.get("address") in targets:
                        sats = int(Decimal(str(detail["amount"])) * 100_000_000)
                        record_deposit(Deposit(
                            "btc", f"{txid}:{detail['vout']}", detail["address"], sats,
                            int(tx.get("timereceived") or tx["blocktime"]),
                        ))
                self.btc_seen.add(txid)
        self.ready("btc", 0)

    async def scan_ton(self) -> None:
        from pytoniq_core import Address, Builder, Cell

        address = self.config.networks["ton"].addresses[0][1]
        if invoice_targets("ton") - {address}:
            raise CryptoPaymentError("Адрес TON отличается от адреса ранее выставленных счетов.")
        if self.ton_usdt_wallet is None and "usdt-ton" in self.config.rates:
            owner = Builder().store_address(Address(address)).end_cell()
            state = await self.request("ton", "/runGetMethod", body={
                "address": TON_USDT, "method": "get_wallet_address",
                "stack": [{"type": "slice", "value": base64.b64encode(owner.to_boc()).decode("ascii")}],
            })
            if state.get("exit_code") not in {0, 1}:
                raise CryptoPaymentError("Не удалось определить кошелёк USDT в TON.")
            cell = Cell.one_from_boc(base64.b64decode(state["stack"][0]["value"]))
            wallet = cell.begin_parse().load_address()
            self.ton_usdt_wallet = wallet.to_str(is_user_friendly=False).lower()

        cursor, _ = network_state("ton")
        if cursor is None:
            latest = await self.request("ton", "/transactions", params={"account": address, "sort": "desc", "limit": 1})
            transactions = latest["transactions"]
            cursor = self.initial_cursor("ton", int(transactions[0]["lt"]) if transactions else 0)
        result = await self.request("ton", "/transactions", params={
            "account": address, "sort": "asc", "limit": BATCH_SIZE, "start_lt": str(cursor + 1),
        })
        transactions = result["transactions"]
        for tx in transactions:
            if not isinstance(tx.get("mc_block_seqno"), int) or tx["mc_block_seqno"] <= 0:
                # Don't advance past a transaction waiting for masterchain inclusion.
                return
            if int(tx["lt"]) <= cursor or ton_address_key(tx["account"]) != address:
                raise CryptoPaymentError("Индексатор TON вернул данные вне запрошенного диапазона.")
            if int(tx["prev_trans_lt"]) != cursor:
                raise CryptoPaymentError("Индексатор TON ещё не вернул предыдущую транзакцию кошелька.")
            deposit = ton_deposit(tx, address, self.ton_usdt_wallet)
            if deposit and deposit.asset in self.config.rates:
                record_deposit(deposit)
            cursor = int(tx["lt"])
            save_network_state("ton", cursor)
        if len(transactions) < BATCH_SIZE:
            self.ready("ton", cursor)


def tron_deposits(receipt: dict, targets: set[str], block_time: int):
    if receipt.get("receipt", {}).get("result") != "SUCCESS" or receipt.get("result") == "FAILED":
        return
    contract = tron_address_key(TRON_USDT)
    for index, log in enumerate(receipt.get("log", [])):
        address = log.get("address", "").lower().removeprefix("0x")
        if address not in {contract, "41" + contract}:
            continue
        topics = log.get("topics", [])
        if len(topics) != 3 or topics[0].lower().removeprefix("0x") != TRANSFER_EVENT:
            continue
        recipient = topics[2].lower().removeprefix("0x")
        amount = log.get("data", "").lower().removeprefix("0x")
        if not re.fullmatch(r"0{24}[0-9a-f]{40}", recipient) or not re.fullmatch(r"[0-9a-f]{64}", amount):
            continue
        if recipient[24:] in targets:
            yield Deposit("usdt-tron", f"{receipt['id']}:{index}", recipient[24:], int(amount, 16), block_time)


def ton_deposit(tx: dict, address: str, usdt_wallet: str | None) -> Deposit | None:
    from pytoniq_core import Cell

    message = tx.get("in_msg") or {}
    if tx.get("description", {}).get("aborted") is not False or message.get("bounced"):
        return None
    if not message.get("source") or not message.get("destination"):
        return None
    if ton_address_key(message["destination"]) != address:
        return None
    try:
        cell = Cell.one_from_boc(base64.b64decode(message["message_content"]["body"]))
        payload = cell.begin_parse()
        opcode = payload.load_uint(32)
        if opcode == 0:
            asset, amount = "gram", int(message["value"])
        elif opcode == JETTON_NOTIFICATION and usdt_wallet == ton_address_key(message["source"]) and not tx["out_msgs"]:
            payload.load_uint(64)
            asset, amount = "usdt-ton", payload.load_coins()
            payload.load_address()
            if payload.load_bit():
                payload = payload.load_ref().begin_parse()
            if payload.load_uint(32) != 0:
                return None
        else:
            return None
        memo = payload.load_snake_string()
    except Exception:
        # Invalid on-chain message bodies are untrusted input.
        return None
    if amount > 0 and re.fullmatch(r"topup-[0-9a-f]{20}", memo):
        return Deposit(asset, tx["hash"], address, amount, int(tx["now"]), memo)
    return None
