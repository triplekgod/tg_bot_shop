import asyncio
import html
import json
import logging
import os
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Optional
from urllib.parse import quote

import httpx
from dotenv import load_dotenv
from telegram import BotCommandScopeChat, BotCommandScopeDefault, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Message, ReplyKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)

load_dotenv()


def parse_int_csv(value: str) -> list[int]:
    result: list[int] = []
    for item in (value or "").replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            result.append(int(item))
        except ValueError:
            logging.warning("Некорректный ID inbound в .env: %s", item)
    return result


def slugify_email_part(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9_.-]+", "_", value)
    value = value.strip("._-")
    return value or "client"


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DB_PATH = os.getenv("DB_PATH", "support_bot.sqlite3").strip() or "support_bot.sqlite3"

# Настройки 3x-ui. XUI_BASE_URL можно указать вместе с секретным URI Path панели,
# например: https://example.com:2053/randompath
XUI_BASE_URL = os.getenv("XUI_BASE_URL", "").strip().rstrip("/")
XUI_USERNAME = os.getenv("XUI_USERNAME", "").strip()
XUI_PASSWORD = os.getenv("XUI_PASSWORD", "").strip()
XUI_API_TOKEN = os.getenv("XUI_API_TOKEN", "").strip()
XUI_VERIFY_SSL = os.getenv("XUI_VERIFY_SSL", "true").strip().lower() not in {"0", "false", "no", "нет"}
XUI_TIMEOUT = float(os.getenv("XUI_TIMEOUT", "20") or 20)
XUI_RESTART_XRAY_AFTER_RENEW = os.getenv("XUI_RESTART_XRAY_AFTER_RENEW", "false").strip().lower() in {"1", "true", "yes", "да"}
XUI_DEFAULT_RENEW_DAYS = int(os.getenv("XUI_DEFAULT_RENEW_DAYS", "30") or 30)
XUI_CLIENTS_PER_PAGE = max(1, int(os.getenv("XUI_CLIENTS_PER_PAGE", "10") or 10))
# Для 3x-ui Node клиенты являются отдельными сущностями Clients и могут быть
# прикреплены сразу к нескольким inbounds. В списке показываем клиента один раз.
XUI_GROUP_CLIENTS_BY_EMAIL = os.getenv("XUI_GROUP_CLIENTS_BY_EMAIL", "true").strip().lower() in {"1", "true", "yes", "да"}

# Режим продления:
# clients_api — новый 3x-ui Node API. По умолчанию продление делается через
# /panel/api/clients/bulkAdjust, то есть именно добавляются дни подписки,
# а не перезаписывается весь объект клиента.
# legacy_inbound — старый API: /panel/api/inbounds/updateClient/{clientId}.
XUI_RENEW_MODE = os.getenv("XUI_RENEW_MODE", "clients_api").strip().lower()
if XUI_RENEW_MODE not in {"clients_api", "legacy_inbound"}:
    XUI_RENEW_MODE = "clients_api"

# Только для XUI_RENEW_MODE=clients_api:
# bulk_adjust — правильно для 3x-ui Node: добавляет дни через /panel/api/clients/bulkAdjust.
# update — запасной режим для старых/форкнутых сборок: выставляет expiryTime через /panel/api/clients/update/{email}.
XUI_CLIENTS_RENEW_METHOD = os.getenv("XUI_CLIENTS_RENEW_METHOD", "bulk_adjust").strip().lower()
if XUI_CLIENTS_RENEW_METHOD not in {"bulk_adjust", "update"}:
    XUI_CLIENTS_RENEW_METHOD = "bulk_adjust"

# Если подписка уже истекла, продлеваем от текущего момента, а не от старой даты окончания.
# Для активной подписки срок по-прежнему добавляется к текущему expiryTime.
XUI_RENEW_EXPIRED_FROM_NOW = os.getenv("XUI_RENEW_EXPIRED_FROM_NOW", "true").strip().lower() in {"1", "true", "yes", "да"}

# Используется только в legacy_inbound режиме. Для clients_api не нужен.
XUI_MAIN_INBOUND_ID_RAW = os.getenv("XUI_MAIN_INBOUND_ID", "").strip()
XUI_MAIN_INBOUND_ID_ERROR = ""
try:
    XUI_MAIN_INBOUND_ID = int(XUI_MAIN_INBOUND_ID_RAW) if XUI_MAIN_INBOUND_ID_RAW else None
except ValueError:
    XUI_MAIN_INBOUND_ID = None
    XUI_MAIN_INBOUND_ID_ERROR = f"XUI_MAIN_INBOUND_ID должен быть числом, сейчас указано: {XUI_MAIN_INBOUND_ID_RAW!r}"

XUI_MAX_RENEW_MONTHS = max(1, int(os.getenv("XUI_MAX_RENEW_MONTHS", "24") or 24))
XUI_DAYS_PER_MONTH = max(1, int(os.getenv("XUI_DAYS_PER_MONTH", "30") or 30))
XUI_SYNC_TGID_TO_PANEL_ON_LINK = os.getenv("XUI_SYNC_TGID_TO_PANEL_ON_LINK", "true").strip().lower() in {"1", "true", "yes", "да"}

# Оформление новой подписки: добавление нового клиента в 3x-ui.
# Для 3x-ui Node лучше указать inboundIds, к которым нужно прикреплять новых клиентов.
# Пример: XUI_NEW_CLIENT_INBOUND_IDS=1,2,3
XUI_NEW_CLIENT_INBOUND_IDS = parse_int_csv(os.getenv("XUI_NEW_CLIENT_INBOUND_IDS", ""))
XUI_NEW_CLIENT_TOTAL_GB = max(0, int(os.getenv("XUI_NEW_CLIENT_TOTAL_GB", "0") or 0))
XUI_NEW_CLIENT_LIMIT_IP = max(0, int(os.getenv("XUI_NEW_CLIENT_LIMIT_IP", "0") or 0))
XUI_NEW_CLIENT_EMAIL_PREFIX = slugify_email_part(os.getenv("XUI_NEW_CLIENT_EMAIL_PREFIX", "client"))
XUI_NEW_CLIENT_FLOW = os.getenv("XUI_NEW_CLIENT_FLOW", "").strip()
XUI_NEW_CLIENT_GROUP = os.getenv("XUI_NEW_CLIENT_GROUP", "").strip()
XUI_NEW_CLIENT_COMMENT = os.getenv("XUI_NEW_CLIENT_COMMENT", "Создано ботом техподдержки").strip()

# Ссылка подписки, которую бот отправляет клиенту после создания нового клиента.
# Обычно 3x-ui выдаёт ссылку вида https://host:port/sub/<subId>.
# Если у вас отдельный домен/URI для подписок, укажите XUI_PUBLIC_SUBSCRIPTION_BASE_URL
# или полностью переопределите шаблон XUI_SUBSCRIPTION_LINK_TEMPLATE.
XUI_PUBLIC_SUBSCRIPTION_BASE_URL = os.getenv("XUI_PUBLIC_SUBSCRIPTION_BASE_URL", XUI_BASE_URL).strip().rstrip("/")
XUI_SUBSCRIPTION_LINK_TEMPLATE = os.getenv("XUI_SUBSCRIPTION_LINK_TEMPLATE", "{base}/sub/{subId}").strip()

# Telegram Stars: оплата цифровой услуги продления прямо внутри Telegram.
# Для Stars provider_token должен быть пустым, валюта — XTR.
STARS_PAYMENTS_ENABLED = os.getenv("STARS_PAYMENTS_ENABLED", "true").strip().lower() in {"1", "true", "yes", "да"}
STARS_MIN_AMOUNT = max(1, int(os.getenv("STARS_MIN_AMOUNT", "1") or 1))
STARS_MAX_AMOUNT = max(STARS_MIN_AMOUNT, int(os.getenv("STARS_MAX_AMOUNT", "100000") or 100000))
# Цена одного месяца продления в Telegram Stars.
# Если поставить 0, клиентский вариант ⭐️ Tg Stars будет скрыт.
STARS_PRICE_PER_MONTH = max(0, int(os.getenv("STARS_PRICE_PER_MONTH", "250") or 0))
STARS_INVOICE_TITLE = os.getenv("STARS_INVOICE_TITLE", "Продление подписки").strip() or "Продление подписки"
STARS_INVOICE_DESCRIPTION = os.getenv("STARS_INVOICE_DESCRIPTION", "Оплата продления подписки через Telegram Stars").strip() or "Оплата продления подписки через Telegram Stars"

# Внутренний баланс и вознаграждение рефералам хранятся в целых рублях.
BALANCE_PRICE_PER_MONTH = max(1, int(os.getenv("BALANCE_PRICE_PER_MONTH", "250") or 250))
BALANCE_MIN_TOPUP_RUB = max(1, int(os.getenv("BALANCE_MIN_TOPUP_RUB", "1") or 1))
BALANCE_MAX_TOPUP_RUB = max(BALANCE_MIN_TOPUP_RUB, int(os.getenv("BALANCE_MAX_TOPUP_RUB", "100000") or 100000))
# Криптопополнение оставлено в коде, но по умолчанию скрыто из клиентского меню.
# Укажите true, чтобы вернуть кнопку без изменения кода.
CRYPTO_TOPUP_ENABLED = os.getenv("CRYPTO_TOPUP_ENABLED", "false").strip().lower() in {"1", "true", "yes", "да"}
REFERRAL_PERCENT = max(0, min(100, int(os.getenv("REFERRAL_PERCENT", "25") or 25)))


def parse_ids(value: str) -> set[int]:
    result: set[int] = set()
    for item in (value or "").replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            result.add(int(item))
        except ValueError:
            logging.warning("Некорректный Telegram ID в .env: %s", item)
    return result


ENV_ADMIN_IDS = parse_ids(os.getenv("ADMIN_IDS", ""))
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID") or (next(iter(ENV_ADMIN_IDS), 0)))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


