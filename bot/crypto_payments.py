"""Invoices and atomic RUB balance credits for self-hosted crypto payments."""

import base64
import binascii
import hashlib
import json
import re
import secrets
import time
from contextlib import closing
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

from .config import BALANCE_MAX_TOPUP_RUB, BALANCE_MIN_TOPUP_RUB, REFERRAL_PERCENT
from .database import db, now_iso


class CryptoPaymentError(ValueError):
    pass


@dataclass(frozen=True)
class Asset:
    network: str
    decimals: int
    symbol: str
    label: str


ASSETS = {
    "gram": Asset("ton", 9, "GRAM", "GRAM · TON"),
    "usdt-ton": Asset("ton", 6, "USDT", "USDT · TON"),
    "usdt-tron": Asset("tron", 6, "USDT", "USDT · TRON (TRC-20)"),
    "btc": Asset("btc", 8, "BTC", "BTC · Bitcoin"),
    "eth": Asset("eth", 18, "ETH", "ETH · Ethereum Mainnet"),
}
TRON_USDT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
TON_USDT = "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs"


def tron_address_key(address: str) -> str:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    if len(address) != 34 or not address.startswith("T"):
        raise CryptoPaymentError("Неверный адрес TRON.")
    try:
        number = 0
        for char in address:
            number = number * 58 + alphabet.index(char)
        decoded = number.to_bytes(25, "big")
    except (ValueError, OverflowError):
        raise CryptoPaymentError("Неверный адрес TRON.") from None
    payload, checksum = decoded[:-4], decoded[-4:]
    if payload[0] != 0x41 or hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4] != checksum:
        raise CryptoPaymentError("Неверная контрольная сумма адреса TRON.")
    return payload[1:].hex()


def ton_address_key(address: str) -> str:
    if re.fullmatch(r"-?\d+:[0-9a-fA-F]{64}", address):
        workchain, account = address.split(":")
        return f"{int(workchain)}:{account.lower()}"
    try:
        decoded = base64.b64decode(address, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error):
        raise CryptoPaymentError("Неверный адрес TON.") from None
    if len(decoded) != 36 or decoded[0] not in {0x11, 0x51}:
        raise CryptoPaymentError("Требуется адрес TON Mainnet.")
    if binascii.crc_hqx(decoded[:-2], 0).to_bytes(2, "big") != decoded[-2:]:
        raise CryptoPaymentError("Неверная контрольная сумма адреса TON.")
    return f"{int.from_bytes(decoded[1:2], 'big', signed=True)}:{decoded[2:-2].hex()}"


def address_key(network: str, address: str) -> str:
    if network == "ton":
        return ton_address_key(address)
    if network == "tron":
        return tron_address_key(address)
    if network == "eth":
        if re.fullmatch(r"0x[0-9a-fA-F]{40}", address) and int(address, 16):
            return address.lower()
    if network == "btc":
        if re.fullmatch(r"(?:bc1[0-9a-z]{11,87}|[13][1-9A-HJ-NP-Za-km-z]{25,34})", address):
            return address
    raise CryptoPaymentError(f"Неверный адрес для сети {network}.")


@dataclass(frozen=True)
class Network:
    url: str
    addresses: tuple[tuple[str, str], ...]
    username: str = ""
    password: str = ""
    confirmations: int = 3


@dataclass(frozen=True)
class PaymentConfig:
    networks: dict[str, Network]
    rates: dict[str, Decimal]
    invoice_seconds: int

    def fingerprint(self, network: str) -> str:
        cfg = self.networks[network]
        assets = sorted(code for code in self.rates if ASSETS[code].network == network)
        identity = (cfg.url, cfg.addresses, cfg.confirmations, assets)
        return hashlib.sha256(repr(identity).encode()).hexdigest()


def load_payment_config(path: str) -> PaymentConfig:
    raw = json.loads(Path(path).read_text(encoding="utf-8"), parse_float=Decimal)
    networks = {}
    rates = {}
    for code, value in raw["rates_rub"].items():
        if code not in ASSETS:
            raise CryptoPaymentError("Неизвестный актив в rates_rub.")
        try:
            rate = Decimal(str(value))
        except InvalidOperation:
            raise CryptoPaymentError(f"Неверный курс {code}.") from None
        if not rate.is_finite() or not Decimal("0.000001") <= rate <= Decimal("1000000000000"):
            raise CryptoPaymentError(f"Неверный курс {code}.")
        rates[code] = rate

    for name in sorted({ASSETS[code].network for code in rates}):
        config = raw["networks"][name]
        url = config["url"].rstrip("/")
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
            raise CryptoPaymentError(f"Неверный URL ноды {name}.")
        addresses = [config["address"]] if name == "ton" else config["addresses"]
        pool = tuple((address, address_key(name, address)) for address in addresses)
        if not pool or len({key for _, key in pool}) != len(pool):
            raise CryptoPaymentError(f"Проверьте список уникальных адресов {name}.")
        confirmations = int(config.get("confirmations", 3))
        if name == "btc" and not 1 <= confirmations <= 100:
            raise CryptoPaymentError("Подтверждения BTC должны быть от 1 до 100.")
        networks[name] = Network(
            url, pool, config.get("username", ""), config.get("password", ""), confirmations,
        )
    minutes = int(raw.get("invoice_minutes", 30))
    if not rates or not 5 <= minutes <= 1440:
        raise CryptoPaymentError("Укажите активы и срок счёта от 5 до 1440 минут.")
    return PaymentConfig(networks, rates, minutes * 60)


def init_crypto_db() -> None:
    with closing(db()) as connection, connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS crypto_invoices (
                id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, asset TEXT NOT NULL,
                network TEXT NOT NULL, address TEXT NOT NULL, address_key TEXT NOT NULL,
                memo TEXT NOT NULL, rubles INTEGER NOT NULL, expected TEXT NOT NULL, referral_percent INTEGER NOT NULL,
                created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, credited_at INTEGER,
                notified_at INTEGER, UNIQUE(network, address_key, memo)
            );
            CREATE INDEX IF NOT EXISTS crypto_invoice_user ON crypto_invoices(user_id, created_at);
            CREATE TABLE IF NOT EXISTS crypto_transfers (
                network TEXT NOT NULL, event_id TEXT NOT NULL, invoice_id TEXT NOT NULL,
                amount TEXT NOT NULL, block_time INTEGER NOT NULL,
                PRIMARY KEY (network, event_id)
            );
            CREATE INDEX IF NOT EXISTS crypto_transfer_invoice ON crypto_transfers(invoice_id);
            CREATE TABLE IF NOT EXISTS crypto_scan (
                network TEXT PRIMARY KEY, cursor TEXT NOT NULL, ready_at INTEGER NOT NULL DEFAULT 0,
                config_hash TEXT NOT NULL DEFAULT ''
            );
        """)


def network_state(network: str) -> tuple[int | None, int]:
    with closing(db()) as connection:
        row = connection.execute("SELECT cursor, ready_at FROM crypto_scan WHERE network = ?", (network,)).fetchone()
    return (int(row["cursor"]), row["ready_at"]) if row else (None, 0)


def save_network_state(network: str, cursor: int, *, ready_at: int | None = None, config_hash: str | None = None) -> None:
    with closing(db()) as connection, connection:
        connection.execute(
            """INSERT INTO crypto_scan (network, cursor, ready_at, config_hash) VALUES (?, ?, COALESCE(?, 0), COALESCE(?, ''))
               ON CONFLICT(network) DO UPDATE SET cursor=excluded.cursor,
               ready_at=COALESCE(?, crypto_scan.ready_at), config_hash=COALESCE(?, crypto_scan.config_hash)""",
            (network, str(cursor), ready_at, config_hash, ready_at, config_hash),
        )


def create_invoice(config: PaymentConfig, user_id: int, asset_code: str, rubles: int, *, now: int | None = None):
    if asset_code not in config.rates or not BALANCE_MIN_TOPUP_RUB <= rubles <= BALANCE_MAX_TOPUP_RUB:
        raise CryptoPaymentError("Выберите доступный актив и допустимую сумму пополнения.")
    now = int(time.time()) if now is None else now
    asset = ASSETS[asset_code]
    numerator, denominator = config.rates[asset_code].as_integer_ratio()
    expected = (rubles * 10**asset.decimals * denominator + numerator - 1) // numerator
    invoice_id = secrets.token_hex(10)
    with closing(db()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        ready = connection.execute(
            "SELECT ready_at, config_hash FROM crypto_scan WHERE network=?", (asset.network,),
        ).fetchone()
        if (
            not ready or ready["config_hash"] != config.fingerprint(asset.network)
            or not 0 <= now - ready["ready_at"] <= 180 or ready["ready_at"] <= 0
        ):
            raise CryptoPaymentError("Сеть пока недоступна. Попробуйте позже.")
        existing = connection.execute(
            """SELECT * FROM crypto_invoices WHERE user_id=? AND asset=? AND rubles=?
               AND credited_at IS NULL AND expires_at>=? ORDER BY created_at DESC LIMIT 1""",
            (user_id, asset_code, rubles, now),
        ).fetchone()
        if existing:
            return existing
        active = connection.execute(
            "SELECT COUNT(*) FROM crypto_invoices WHERE user_id=? AND credited_at IS NULL AND expires_at>=?",
            (user_id, now),
        ).fetchone()[0]
        if active >= 5:
            raise CryptoPaymentError("У вас уже пять активных счетов. Откройте их в истории пополнений.")
        pool = config.networks[asset.network].addresses
        if asset.network == "ton":
            address, key = pool[0]
            memo = f"topup-{invoice_id}"
        else:
            used = {row[0] for row in connection.execute(
                "SELECT address_key FROM crypto_invoices WHERE network=?", (asset.network,),
            )}
            available = next(((address, key) for address, key in pool if key not in used), None)
            if not available:
                raise CryptoPaymentError("Свободные адреса закончились. Выберите другую сеть или напишите в поддержку.")
            address, key = available
            memo = ""
        connection.execute(
            """INSERT INTO crypto_invoices
               (id, user_id, asset, network, address, address_key, memo, rubles, expected, referral_percent, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                invoice_id, user_id, asset_code, asset.network, address, key, memo, rubles, str(expected),
                REFERRAL_PERCENT, now, now + config.invoice_seconds,
            ),
        )
        return connection.execute("SELECT * FROM crypto_invoices WHERE id=?", (invoice_id,)).fetchone()


def get_invoice(user_id: int, invoice_id: str):
    with closing(db()) as connection:
        return connection.execute(
            "SELECT * FROM crypto_invoices WHERE id=? AND user_id=?", (invoice_id, user_id),
        ).fetchone()


def user_invoices(user_id: int):
    with closing(db()) as connection:
        return connection.execute(
            "SELECT * FROM crypto_invoices WHERE user_id=? ORDER BY created_at DESC, rowid DESC LIMIT 10",
            (user_id,),
        ).fetchall()


def invoice_targets(network: str) -> set[str]:
    with closing(db()) as connection:
        return {row[0] for row in connection.execute(
            "SELECT DISTINCT address_key FROM crypto_invoices WHERE network=?", (network,),
        )}


def invoice_received(invoice_id: str) -> int:
    with closing(db()) as connection:
        return sum(int(row[0]) for row in connection.execute(
            "SELECT amount FROM crypto_transfers WHERE invoice_id=?", (invoice_id,),
        ))


@dataclass(frozen=True)
class Deposit:
    asset: str
    event_id: str
    address_key: str
    amount: int
    block_time: int
    memo: str = ""


def record_deposit(deposit: Deposit) -> bool:
    """Returns true only when this deposit causes the invoice to be credited."""
    if deposit.amount <= 0 or deposit.block_time <= 0 or not deposit.event_id:
        return False
    network = ASSETS[deposit.asset].network
    with closing(db()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        invoice = connection.execute(
            """SELECT * FROM crypto_invoices WHERE network=? AND asset=? AND address_key=? AND memo=?
               AND created_at<=?""",
            (network, deposit.asset, deposit.address_key, deposit.memo, deposit.block_time),
        ).fetchone()
        if not invoice:
            return False
        insert = connection.execute(
            """INSERT INTO crypto_transfers(network,event_id,invoice_id,amount,block_time) VALUES(?,?,?,?,?)
               ON CONFLICT(network,event_id) DO NOTHING""",
            (network, deposit.event_id, invoice["id"], str(deposit.amount), deposit.block_time),
        )
        if not insert.rowcount or invoice["credited_at"] is not None:
            return False
        timely = sum(int(row[0]) for row in connection.execute(
            "SELECT amount FROM crypto_transfers WHERE invoice_id=? AND block_time<=?",
            (invoice["id"], invoice["expires_at"]),
        ))
        if timely < int(invoice["expected"]):
            return False
        connection.execute("UPDATE crypto_invoices SET credited_at=? WHERE id=?", (int(time.time()), invoice["id"]))
        note = f"Криптопополнение #{invoice['id']}"
        connection.execute(
            "INSERT INTO balance_transactions(user_id,amount,kind,note,created_at) VALUES(?,?,'topup',?,?)",
            (invoice["user_id"], invoice["rubles"], note, now_iso()),
        )
        referrer = connection.execute(
            "SELECT referrer_user_id FROM referrals WHERE referral_user_id=?", (invoice["user_id"],),
        ).fetchone()
        reward = invoice["rubles"] * invoice["referral_percent"] // 100
        if referrer and reward:
            connection.execute(
                "INSERT INTO balance_transactions(user_id,amount,kind,note,created_at) VALUES(?,?,'referral_reward',?,?)",
                (referrer[0], reward, note, now_iso()),
            )
        return True


def unnotified_invoices():
    with closing(db()) as connection:
        return connection.execute(
            "SELECT id,user_id,rubles FROM crypto_invoices WHERE credited_at IS NOT NULL AND notified_at IS NULL LIMIT 50",
        ).fetchall()


def mark_invoice_notified(invoice_id: str) -> None:
    with closing(db()) as connection, connection:
        connection.execute("UPDATE crypto_invoices SET notified_at=? WHERE id=?", (int(time.time()), invoice_id))


def crypto_amount(amount: int, decimals: int) -> str:
    whole, fraction = divmod(amount, 10**decimals)
    return f"{whole}.{fraction:0{decimals}d}".rstrip("0").rstrip(".")
