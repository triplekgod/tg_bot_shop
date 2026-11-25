import asyncio
import base64
import json
import sqlite3
import time
import unittest
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx
from pytoniq_core import Address, Builder
from telegram import User

from bot import crypto_chains as chains
from bot import crypto_payments as pay
from bot import database


ETH = "0x" + "01" * 20
TON = "0:" + "02" * 32
JETTON_WALLET = "0:" + "03" * 32
SENDER = "0:" + "04" * 32
BTC = "1BoatSLRHtKNngkdXEeobR76b53LETtpyT"
TRON = "TJRabPrwbZy45sbavfcjinPJC18kjpRTv8"


def telegram_user(uid):
    return User(uid, "Test", False)


class TestConnection(sqlite3.Connection):
    def __exit__(self, *exc):
        try:
            return super().__exit__(*exc)
        finally:
            self.close()


class CryptoPaymentsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "payments.sqlite3"

        def connect():
            connection = sqlite3.connect(path, factory=TestConnection)
            connection.row_factory = sqlite3.Row
            return connection

        for module in (database, pay):
            patcher = patch.object(module, "db", connect)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.init_db()
        pay.init_crypto_db()
        for uid in (10, 20):
            database.upsert_user(telegram_user(uid))
        self.now = int(time.time())
        addresses = {"eth": ETH, "ton": TON, "btc": BTC, "tron": TRON}
        self.config = pay.PaymentConfig(
            {
                network: pay.Network(
                    f"http://{network}.example.test", ((address, pay.address_key(network, address)),),
                )
                for network, address in addresses.items()
            },
            {"eth": Decimal("200000"), "btc": Decimal("8000000"), "gram": Decimal("100"),
             "usdt-tron": Decimal("100"), "usdt-ton": Decimal("100")},
            1800,
        )

    def ready(self, network):
        pay.save_network_state(network, 90, ready_at=self.now, config_hash=self.config.fingerprint(network))

    def make_invoice(self, asset, rubles=200, uid=10):
        self.ready(pay.ASSETS[asset].network)
        return pay.create_invoice(self.config, uid, asset, rubles, now=self.now)

    async def test_balance_and_referral_are_credited_once_with_partial_transfers(self):
        asset = "eth"
        with patch.object(pay, "REFERRAL_PERCENT", 25):
            invoice = self.make_invoice(asset)
        database.set_referrer(10, 20)
        amount = int(invoice["expected"])
        first = pay.Deposit(asset, "first-tx", ETH, amount // 2, self.now + 1)
        second = pay.Deposit(asset, "second-tx", ETH, amount - first.amount, self.now + 2)
        self.assertFalse(pay.record_deposit(first))
        self.assertTrue(pay.record_deposit(second))
        self.assertFalse(pay.record_deposit(first))
        self.assertFalse(pay.record_deposit(second))
        self.assertEqual(database.balance_of(10), 200)
        self.assertEqual(database.balance_of(20), 50)
        self.assertEqual(pay.invoice_received(invoice["id"]), amount)
        self.assertIsNone(pay.get_invoice(20, invoice["id"]))

    async def test_invoice_cannot_be_issued_until_config_and_worker_match(self):
        with self.assertRaises(pay.CryptoPaymentError):
            pay.create_invoice(self.config, 10, "eth", 200, now=self.now)
        pay.save_network_state("eth", 90, ready_at=self.now, config_hash="old")
        with self.assertRaises(pay.CryptoPaymentError):
            pay.create_invoice(self.config, 10, "eth", 200, now=self.now)
        invoice = self.make_invoice("eth")
        same = pay.create_invoice(self.config, 10, "eth", 200, now=self.now)
        self.assertEqual(invoice["id"], same["id"])
        with self.assertRaises(pay.CryptoPaymentError):
            pay.create_invoice(self.config, 20, "eth", 200, now=self.now)

    async def test_concurrent_workers_credit_the_same_deposit_once(self):
        invoice = self.make_invoice("eth")
        deposit = pay.Deposit("eth", "same-tx", ETH, int(invoice["expected"]), self.now)
        credit_results = await asyncio.gather(*(asyncio.to_thread(pay.record_deposit, deposit) for _ in range(4)))
        self.assertEqual(credit_results.count(True), 1)
        self.assertEqual(database.balance_of(10), 200)

    async def test_late_or_wrong_ton_deposits_do_not_credit(self):
        invoice = self.make_invoice("usdt-ton")
        amount = int(invoice["expected"])
        for deposit in (
            pay.Deposit("gram", "wrong-asset", TON, amount, self.now + 1, invoice["memo"]),
            pay.Deposit("usdt-ton", "wrong-memo", TON, amount, self.now + 1, ""),
            pay.Deposit("usdt-ton", "before-invoice", TON, amount, self.now - 1, invoice["memo"]),
            pay.Deposit("usdt-ton", "late-tx", TON, amount, invoice["expires_at"] + 1, invoice["memo"]),
        ):
            self.assertFalse(pay.record_deposit(deposit))
        self.assertEqual(database.balance_of(10), 0)
        self.assertEqual(pay.invoice_received(invoice["id"]), amount)

    async def test_eth_requires_a_successful_receipt_in_finalized_block(self):
        invoice = self.make_invoice("eth")
        block = {"number": hex(91), "hash": "block-hash", "timestamp": hex(self.now)}
        contract_transfer = {"hash": "failed", "to": ETH, "value": hex(int(invoice["expected"]))}
        transfer = dict(contract_transfer, hash="paid")

        def respond(request):
            data = json.loads(request.content)
            method, params = data["method"], data["params"]
            if method == "eth_chainId":
                return httpx.Response(200, json={"result": "0x1"})
            if method == "eth_getBlockByNumber":
                result = block if params[0] == "finalized" else dict(block, transactions=[contract_transfer, transfer])
            else:
                result = {"blockHash": block["hash"], "status": "0x1" if params == ["paid"] else "0x0"}
            return httpx.Response(200, json={"result": result})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            scanner = chains.ChainScanner(http, self.config)
            await scanner.scan_eth()
            await scanner.scan_eth()
        self.assertEqual(pay.network_state("eth")[0], 91)
        self.assertEqual(database.balance_of(10), 200)
        self.assertEqual(pay.invoice_received(invoice["id"]), int(invoice["expected"]))

    async def test_tron_credits_only_tether_contract_on_solidified_chain(self):
        invoice = self.make_invoice("usdt-tron")
        amount = int(invoice["expected"])
        recipient = pay.tron_address_key(TRON)
        log = {
            "address": pay.tron_address_key(pay.TRON_USDT),
            "topics": [chains.TRANSFER_EVENT, "0" * 64, recipient.zfill(64)],
            "data": f"{amount:064x}",
        }
        header = {"block_header": {"raw_data": {"number": 91, "timestamp": self.now * 1000}}}
        receipt = {
            "id": "payment", "blockNumber": 91, "receipt": {"result": "SUCCESS"},
            "log": [dict(log, address="a" * 40), log],
        }

        def respond(request):
            if request.url.path.endswith("/gettransactioninfobyblocknum"):
                return httpx.Response(200, json=[receipt])
            if request.url.path.endswith("/getblockbynum"):
                return httpx.Response(200, json=dict(header, transactions=[{"txID": "payment"}]))
            return httpx.Response(200, json=header)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            await chains.ChainScanner(http, self.config).scan_tron()
        self.assertEqual(database.balance_of(10), 200)
        self.assertEqual(pay.invoice_received(invoice["id"]), amount)

    async def test_btc_watch_only_observes_confirmed_vouts(self):
        invoice = self.make_invoice("btc")
        responses = {
            "getblockchaininfo": {"chain": "main", "initialblockdownload": False},
            "getwalletinfo": {"private_keys_enabled": False},
            "getaddressinfo": {"iswatchonly": True},
            "listreceivedbyaddress": [{"address": BTC, "txids": ["tx1"]}],
            "gettransaction": {
                "confirmations": 3, "blocktime": self.now,
                "details": [{"address": BTC, "category": "receive", "vout": 0, "amount": 0.000025}],
            },
        }

        def respond(request):
            return httpx.Response(200, json={"result": responses[json.loads(request.content)["method"]]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            scanner = chains.ChainScanner(http, self.config)
            await scanner.scan_btc()
            await scanner.scan_btc()
        self.assertEqual(database.balance_of(10), 200)
        self.assertEqual(pay.invoice_received(invoice["id"]), 2500)

    async def test_ton_scan_matches_gram_memo_and_verified_usdt_wallet(self):
        gram_invoice = self.make_invoice("gram")
        usdt_invoice = self.make_invoice("usdt-ton", uid=20)
        note_gram = Builder().store_uint(0, 32).store_snake_string(gram_invoice["memo"]).end_cell()
        note_usdt = Builder().store_uint(0, 32).store_snake_string(usdt_invoice["memo"]).end_cell()
        usdt_body = (
            Builder().store_uint(chains.JETTON_NOTIFICATION, 32).store_uint(0, 64)
            .store_coins(int(usdt_invoice["expected"])).store_address(Address(SENDER))
            .store_bit(1).store_ref(note_usdt).end_cell()
        )

        def transaction(lt, sender, body, value):
            return {
                "account": TON, "lt": str(lt), "prev_trans_lt": str(lt - 1),
                "mc_block_seqno": 500, "now": self.now, "hash": f"hash{lt}",
                "description": {"aborted": False}, "out_msgs": [],
                "in_msg": {"source": sender, "destination": TON, "value": value, "bounced": False,
                           "message_content": {"body": base64.b64encode(body.to_boc()).decode()}},
            }

        transactions = [
            transaction(91, SENDER, note_gram, gram_invoice["expected"]),
            transaction(92, SENDER, usdt_body, "1"),
            transaction(93, JETTON_WALLET, usdt_body, "1"),
        ]
        derived = Builder().store_address(Address(JETTON_WALLET)).end_cell()

        def respond(request):
            if request.url.path.endswith("/runGetMethod"):
                self.assertEqual(json.loads(request.content)["address"], pay.TON_USDT)
                return httpx.Response(200, json={"exit_code": 0, "stack": [
                    {"type": "slice", "value": base64.b64encode(derived.to_boc()).decode()},
                ]})
            self.assertEqual(request.url.params["account"], TON)
            start = int(request.url.params["start_lt"])
            return httpx.Response(200, json={"transactions": [tx for tx in transactions if int(tx["lt"]) >= start]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            scanner = chains.ChainScanner(http, self.config)
            await scanner.scan_ton()
            await scanner.scan_ton()
        self.assertEqual(database.balance_of(10), 200)
        self.assertEqual(database.balance_of(20), 200)
        self.assertEqual(pay.network_state("ton")[0], 93)
        with closing(database.db()) as connection:
            ids = {row[0] for row in connection.execute("SELECT event_id FROM crypto_transfers")}
        self.assertEqual(ids, {"hash91", "hash93"})
