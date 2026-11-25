"""Run the self-hosted deposit worker with `python -m bot.crypto_worker`."""

import asyncio
import logging
import sqlite3

import httpx
from telegram import Bot
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError

from .config import BOT_TOKEN, CRYPTO_PAYMENTS_CONFIG, logger
from .crypto_chains import ChainScanner
from .crypto_payments import (
    CryptoPaymentError,
    init_crypto_db,
    load_payment_config,
    mark_invoice_notified,
    unnotified_invoices,
)
from .database import init_db


async def poll_network(scanner: ChainScanner, network: str) -> None:
    scans = {"btc": scanner.scan_btc, "eth": scanner.scan_eth, "ton": scanner.scan_ton, "tron": scanner.scan_tron}
    while True:
        try:
            await scans[network]()
        except CryptoPaymentError as exc:
            logger.warning("Проверка криптоплатежей %s: %s", network, exc)
        except (httpx.HTTPError, OSError, sqlite3.Error, ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
            logger.warning("Проверка криптоплатежей %s: %s", network, type(exc).__name__)
        await asyncio.sleep(8)


async def notify_clients(bot: Bot) -> None:
    while True:
        try:
            for invoice in unnotified_invoices():
                try:
                    await bot.send_message(
                        chat_id=invoice["user_id"],
                        text=f"✅ Криптоплатёж подтверждён. На баланс зачислено {invoice['rubles']} ₽.\nСчёт №{invoice['id']}",
                    )
                except (BadRequest, Forbidden):
                    # Chat is no longer reachable. The credit remains in the balance.
                    pass
                except RetryAfter as exc:
                    delay = exc.retry_after
                    await asyncio.sleep(delay.total_seconds() if hasattr(delay, "total_seconds") else delay)
                    break
                except TelegramError:
                    continue
                mark_invoice_notified(invoice["id"])
        except sqlite3.Error as exc:
            logger.warning("Уведомление о криптоплатеже: %s", type(exc).__name__)
        await asyncio.sleep(10)


async def run_worker() -> None:
    if not CRYPTO_PAYMENTS_CONFIG or not BOT_TOKEN:
        raise RuntimeError("Укажите CRYPTO_PAYMENTS_CONFIG и BOT_TOKEN в .env.")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    config = load_payment_config(CRYPTO_PAYMENTS_CONFIG)
    init_db()
    init_crypto_db()
    logging_notice = ", ".join(config.networks)
    logger.info("Служба криптоплатежей запущена. Сети: %s", logging_notice)
    async with httpx.AsyncClient(timeout=20, trust_env=False) as http, Bot(BOT_TOKEN) as bot:
        scanner = ChainScanner(http, config)
        await asyncio.gather(
            *(poll_network(scanner, network) for network in config.networks),
            notify_clients(bot),
        )


if __name__ == "__main__":
    asyncio.run(run_worker())
