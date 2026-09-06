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

# Внутренний баланс хранится в целых Stars-эквивалентах. Так пользователь может
# оплатить подписку накопленными средствами, а 25% реферального вознаграждения
# начисляется без конвертации валют.
BALANCE_PRICE_PER_MONTH = max(1, int(os.getenv("BALANCE_PRICE_PER_MONTH", str(STARS_PRICE_PER_MONTH or 250)) or 250))
REFERRAL_PERCENT = max(0, min(100, int(os.getenv("REFERRAL_PERCENT", "25") or 25)))
CRYPTO_PAYMENT_DETAILS = os.getenv("CRYPTO_PAYMENT_DETAILS", "").strip()


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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                is_banned INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                added_by INTEGER,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                closed_at TEXT,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER NOT NULL,
                user_id INTEGER,
                admin_id INTEGER,
                direction TEXT NOT NULL,
                telegram_message_id INTEGER,
                content_type TEXT,
                text TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(ticket_id) REFERENCES tickets(id)
            );

            CREATE TABLE IF NOT EXISTS admin_message_map (
                admin_id INTEGER NOT NULL,
                admin_chat_message_id INTEGER NOT NULL,
                ticket_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(admin_id, admin_chat_message_id)
            );

            CREATE TABLE IF NOT EXISTS xui_links (
                telegram_user_id INTEGER PRIMARY KEY,
                xui_email TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS renewal_requests (
                ticket_id INTEGER PRIMARY KEY,
                telegram_user_id INTEGER NOT NULL,
                xui_email TEXT,
                months INTEGER NOT NULL,
                days INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                renewed_at TEXT,
                renewed_by INTEGER,
                rejected_at TEXT,
                rejected_by INTEGER,
                payment_confirmed_at TEXT,
                payment_confirmed_by INTEGER,
                payment_details_sent_at TEXT,
                payment_details_sent_by INTEGER,
                payment_method TEXT,
                stars_amount INTEGER,
                stars_invoice_sent_at TEXT,
                stars_invoice_sent_by INTEGER,
                stars_charge_id TEXT,
                FOREIGN KEY(ticket_id) REFERENCES tickets(id),
                FOREIGN KEY(telegram_user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS subscription_requests (
                ticket_id INTEGER PRIMARY KEY,
                telegram_user_id INTEGER NOT NULL,
                xui_email TEXT,
                months INTEGER NOT NULL,
                days INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                created_at_panel TEXT,
                created_by INTEGER,
                rejected_at TEXT,
                rejected_by INTEGER,
                payment_confirmed_at TEXT,
                payment_confirmed_by INTEGER,
                payment_details_sent_at TEXT,
                payment_details_sent_by INTEGER,
                payment_method TEXT,
                stars_amount INTEGER,
                stars_invoice_sent_at TEXT,
                stars_invoice_sent_by INTEGER,
                stars_charge_id TEXT,
                FOREIGN KEY(ticket_id) REFERENCES tickets(id),
                FOREIGN KEY(telegram_user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS subscription_welcome_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sort_order INTEGER NOT NULL,
                source_chat_id INTEGER NOT NULL,
                source_message_id INTEGER NOT NULL,
                admin_id INTEGER,
                content_type TEXT,
                text TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS referrals (
                referral_user_id INTEGER PRIMARY KEY,
                referrer_user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(referral_user_id) REFERENCES users(user_id),
                FOREIGN KEY(referrer_user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS balance_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                kind TEXT NOT NULL,
                note TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS balance_topups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                method TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                confirmed_at TEXT,
                confirmed_by INTEGER,
                stars_charge_id TEXT,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            );
            """
        )
        # Миграция для уже созданных баз: старые версии бота могли создать
        # таблицу renewal_requests без полей подтверждения оплаты и отправки реквизитов.
        columns = {row[1] for row in conn.execute("PRAGMA table_info(renewal_requests)").fetchall()}
        if "rejected_at" not in columns:
            conn.execute("ALTER TABLE renewal_requests ADD COLUMN rejected_at TEXT")
        if "rejected_by" not in columns:
            conn.execute("ALTER TABLE renewal_requests ADD COLUMN rejected_by INTEGER")
        if "payment_confirmed_at" not in columns:
            conn.execute("ALTER TABLE renewal_requests ADD COLUMN payment_confirmed_at TEXT")
        if "payment_confirmed_by" not in columns:
            conn.execute("ALTER TABLE renewal_requests ADD COLUMN payment_confirmed_by INTEGER")
        if "payment_details_sent_at" not in columns:
            conn.execute("ALTER TABLE renewal_requests ADD COLUMN payment_details_sent_at TEXT")
        if "payment_details_sent_by" not in columns:
            conn.execute("ALTER TABLE renewal_requests ADD COLUMN payment_details_sent_by INTEGER")
        if "payment_method" not in columns:
            conn.execute("ALTER TABLE renewal_requests ADD COLUMN payment_method TEXT")
        if "stars_amount" not in columns:
            conn.execute("ALTER TABLE renewal_requests ADD COLUMN stars_amount INTEGER")
        if "stars_invoice_sent_at" not in columns:
            conn.execute("ALTER TABLE renewal_requests ADD COLUMN stars_invoice_sent_at TEXT")
        if "stars_invoice_sent_by" not in columns:
            conn.execute("ALTER TABLE renewal_requests ADD COLUMN stars_invoice_sent_by INTEGER")
        if "stars_charge_id" not in columns:
            conn.execute("ALTER TABLE renewal_requests ADD COLUMN stars_charge_id TEXT")

        for admin_id in ENV_ADMIN_IDS | ({SUPER_ADMIN_ID} if SUPER_ADMIN_ID else set()):
            if admin_id:
                conn.execute(
                    "INSERT OR IGNORE INTO admins(user_id, added_by, created_at) VALUES (?, ?, ?)",
                    (admin_id, SUPER_ADMIN_ID or admin_id, now_iso()),
                )


def get_admin_ids() -> list[int]:
    with db() as conn:
        rows = conn.execute("SELECT user_id FROM admins ORDER BY user_id").fetchall()
    return [int(row["user_id"]) for row in rows]


def is_admin(user_id: Optional[int]) -> bool:
    if not user_id:
        return False
    with db() as conn:
        row = conn.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,)).fetchone()
    return row is not None


def is_super_admin(user_id: Optional[int]) -> bool:
    return bool(user_id and SUPER_ADMIN_ID and user_id == SUPER_ADMIN_ID)


def upsert_user(user) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO users(user_id, username, first_name, last_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_name = excluded.last_name,
                updated_at = excluded.updated_at
            """,
            (
                user.id,
                user.username,
                user.first_name,
                user.last_name,
                now_iso(),
                now_iso(),
            ),
        )


def get_user(user_id: int) -> Optional[sqlite3.Row]:
    with db() as conn:
        return conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()


def is_banned(user_id: int) -> bool:
    row = get_user(user_id)
    return bool(row and row["is_banned"])


def set_ban(user_id: int, banned: bool) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE users SET is_banned = ?, updated_at = ? WHERE user_id = ?",
            (1 if banned else 0, now_iso(), user_id),
        )


def set_xui_link(telegram_user_id: int, xui_email: str) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO xui_links(telegram_user_id, xui_email, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(telegram_user_id) DO UPDATE SET
                xui_email = excluded.xui_email,
                updated_at = excluded.updated_at
            """,
            (telegram_user_id, xui_email, now_iso(), now_iso()),
        )


def get_xui_link(telegram_user_id: int) -> Optional[str]:
    with db() as conn:
        row = conn.execute(
            "SELECT xui_email FROM xui_links WHERE telegram_user_id = ?",
            (telegram_user_id,),
        ).fetchone()
    return str(row["xui_email"]) if row else None


def delete_xui_link(telegram_user_id: int) -> bool:
    with db() as conn:
        cur = conn.execute("DELETE FROM xui_links WHERE telegram_user_id = ?", (telegram_user_id,))
        return cur.rowcount > 0


def set_referrer(referral_user_id: int, referrer_user_id: int) -> bool:
    """Привязка возможна единожды и только между разными пользователями."""
    if referral_user_id == referrer_user_id:
        return False
    with db() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO referrals(referral_user_id, referrer_user_id, created_at) VALUES (?, ?, ?)",
            (referral_user_id, referrer_user_id, now_iso()),
        )
    return cur.rowcount > 0


def referral_stats(user_id: int) -> tuple[int, int]:
    with db() as conn:
        referrals = conn.execute("SELECT COUNT(*) FROM referrals WHERE referrer_user_id = ?", (user_id,)).fetchone()[0]
        earned = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM balance_transactions WHERE user_id = ? AND kind = 'referral_reward'", (user_id,)).fetchone()[0]
    return int(referrals), int(earned)


def balance_of(user_id: int) -> int:
    with db() as conn:
        value = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM balance_transactions WHERE user_id = ?", (user_id,)).fetchone()[0]
    return int(value)


def add_balance_transaction(user_id: int, amount: int, kind: str, note: str = "") -> None:
    with db() as conn:
        conn.execute("INSERT INTO balance_transactions(user_id, amount, kind, note, created_at) VALUES (?, ?, ?, ?, ?)", (user_id, amount, kind, note, now_iso()))


def spend_balance(user_id: int, amount: int, note: str) -> bool:
    with db() as conn:
        current = int(conn.execute("SELECT COALESCE(SUM(amount), 0) FROM balance_transactions WHERE user_id = ?", (user_id,)).fetchone()[0])
        if current < amount:
            return False
        conn.execute("INSERT INTO balance_transactions(user_id, amount, kind, note, created_at) VALUES (?, ?, 'subscription_payment', ?, ?)", (user_id, -amount, note, now_iso()))
    return True


def create_balance_topup(user_id: int, amount: int, method: str) -> int:
    with db() as conn:
        cur = conn.execute("INSERT INTO balance_topups(user_id, amount, method, created_at) VALUES (?, ?, ?, ?)", (user_id, amount, method, now_iso()))
    return int(cur.lastrowid)


def get_balance_topup(topup_id: int) -> Optional[sqlite3.Row]:
    with db() as conn:
        return conn.execute("SELECT * FROM balance_topups WHERE id = ?", (topup_id,)).fetchone()


def confirm_balance_topup(topup_id: int, admin_id: int, charge_id: str = "") -> Optional[sqlite3.Row]:
    """Пополнение и реферальная награда выполняются в одной транзакции и только раз."""
    with db() as conn:
        topup = conn.execute("SELECT * FROM balance_topups WHERE id = ?", (topup_id,)).fetchone()
        if not topup or str(topup["status"]) != "pending":
            return None
        conn.execute("UPDATE balance_topups SET status='confirmed', confirmed_at=?, confirmed_by=?, stars_charge_id=? WHERE id=?", (now_iso(), admin_id, charge_id, topup_id))
        conn.execute("INSERT INTO balance_transactions(user_id, amount, kind, note, created_at) VALUES (?, ?, 'topup', ?, ?)", (topup["user_id"], topup["amount"], f"Пополнение #{topup_id}", now_iso()))
        ref = conn.execute("SELECT referrer_user_id FROM referrals WHERE referral_user_id = ?", (topup["user_id"],)).fetchone()
        if ref:
            reward = int(int(topup["amount"]) * REFERRAL_PERCENT / 100)
            if reward:
                conn.execute("INSERT INTO balance_transactions(user_id, amount, kind, note, created_at) VALUES (?, ?, 'referral_reward', ?, ?)", (ref["referrer_user_id"], reward, f"{REFERRAL_PERCENT}% с пополнения реферала #{topup_id}", now_iso()))
    return topup


def add_subscription_welcome_message(admin_id: int, message: Message) -> int:
    """Сохранить сообщение админа, которое нужно отправлять новым клиентам после создания подписки."""
    with db() as conn:
        row = conn.execute("SELECT COALESCE(MAX(sort_order), 0) AS n FROM subscription_welcome_messages").fetchone()
        sort_order = int(row["n"] or 0) + 1
        cur = conn.execute(
            """
            INSERT INTO subscription_welcome_messages(
                sort_order, source_chat_id, source_message_id, admin_id, content_type, text, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sort_order,
                int(message.chat_id),
                int(message.message_id),
                int(admin_id),
                message_type(message),
                message.text or message.caption,
                now_iso(),
            ),
        )
        return int(cur.lastrowid)


def clear_subscription_welcome_messages() -> int:
    with db() as conn:
        cur = conn.execute("DELETE FROM subscription_welcome_messages")
        return int(cur.rowcount or 0)


def list_subscription_welcome_messages() -> list[sqlite3.Row]:
    with db() as conn:
        return conn.execute(
            """
            SELECT * FROM subscription_welcome_messages
            ORDER BY sort_order ASC, id ASC
            """
        ).fetchall()


def count_subscription_welcome_messages() -> int:
    with db() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM subscription_welcome_messages").fetchone()
        return int(row["c"] or 0)


def build_subscription_link(result: dict[str, Any]) -> Optional[str]:
    """Собрать ссылку подписки для нового клиента.

    Поддерживает разные варианты ответа 3x-ui: готовую ссылку, subId/sub_id/subID
    или шаблон, где можно использовать email. Это важно, потому что разные версии
    3x-ui возвращают поле подписки по-разному.
    """
    ready_link = str(
        result.get("subscriptionLink")
        or result.get("subscriptionUrl")
        or result.get("subLink")
        or result.get("link")
        or ""
    ).strip()
    if ready_link.startswith(("http://", "https://")):
        return ready_link

    sub_id = str(
        result.get("subId")
        or result.get("sub_id")
        or result.get("subID")
        or result.get("subid")
        or ""
    ).strip()
    email = str(result.get("email") or "").strip()

    base = XUI_PUBLIC_SUBSCRIPTION_BASE_URL or XUI_BASE_URL
    if not base:
        return None

    template = XUI_SUBSCRIPTION_LINK_TEMPLATE or "{base}/sub/{subId}"

    # Если шаблон требует subId, а 3x-ui его не вернул, ссылку безопасно собрать нельзя.
    # Если админ задал шаблон через {email}, разрешаем формирование без subId.
    if "{subId}" in template and not sub_id:
        return None
    if "{email}" in template and not email:
        return None

    try:
        return template.format(
            base=base.rstrip("/"),
            subId=quote(sub_id, safe=""),
            email=quote(email, safe=""),
        )
    except Exception:
        if sub_id:
            return f"{base.rstrip('/')}/sub/{quote(sub_id, safe='')}"
        return None


async def send_subscription_welcome_messages(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> int:
    """Скопировать клиенту заранее сохранённые админом сообщения после создания подписки."""
    rows = list_subscription_welcome_messages()
    sent = 0
    for row in rows:
        try:
            await context.bot.copy_message(
                chat_id=user_id,
                from_chat_id=int(row["source_chat_id"]),
                message_id=int(row["source_message_id"]),
            )
            sent += 1
        except TelegramError as exc:
            logger.warning(
                "Не удалось отправить приветственное сообщение подписки #%s пользователю %s: %s",
                row["id"],
                user_id,
                exc,
            )
    return sent


async def resolve_xui_email_for_user(telegram_user_id: int) -> tuple[Optional[str], str]:
    """Получить email клиента по Telegram ID.

    Приоритет:
    1) локальная привязка бота /linksub;
    2) поле tgId в панели 3x-ui.
    Возвращает (email, source), где source: local, panel_tgId или empty.
    """
    local_email = get_xui_link(telegram_user_id)
    if local_email:
        return local_email, "local"

    try:
        async with XuiClient() as api:
            panel_email = await api.find_client_email_by_tg_id(telegram_user_id)
    except XuiApiError as exc:
        logger.warning("Не удалось автоматически найти 3x-ui клиента по Telegram ID %s: %s", telegram_user_id, exc)
        return None, "empty"

    if panel_email:
        set_xui_link(telegram_user_id, panel_email)
        return panel_email, "panel_tgId"
    return None, "empty"


class XuiApiError(RuntimeError):
    pass


class XuiClient:
    def __init__(self) -> None:
        if not XUI_BASE_URL:
            raise XuiApiError("Не указан XUI_BASE_URL в .env")
        if not XUI_API_TOKEN and (not XUI_USERNAME or not XUI_PASSWORD):
            raise XuiApiError("Укажите XUI_API_TOKEN или XUI_USERNAME + XUI_PASSWORD в .env")

        headers = {"Accept": "application/json"}
        if XUI_API_TOKEN:
            headers["Authorization"] = f"Bearer {XUI_API_TOKEN}"

        self.base_url = XUI_BASE_URL
        self._client = httpx.AsyncClient(
            headers=headers,
            timeout=XUI_TIMEOUT,
            verify=XUI_VERIFY_SSL,
            follow_redirects=True,
        )
        self._logged_in = bool(XUI_API_TOKEN)

    def url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    async def __aenter__(self) -> "XuiClient":
        if not self._logged_in:
            await self.login()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self._client.aclose()

    async def login(self) -> None:
        last_error = ""
        for kwargs in (
            {"data": {"username": XUI_USERNAME, "password": XUI_PASSWORD}},
            {"json": {"username": XUI_USERNAME, "password": XUI_PASSWORD}},
        ):
            try:
                response = await self._client.post(self.url("/login"), **kwargs)
                last_error = response.text[:300]
                if response.status_code >= 400:
                    continue

                if response.text.strip():
                    try:
                        data = response.json()
                    except ValueError:
                        data = None
                    if isinstance(data, dict) and data.get("success") is False:
                        last_error = str(data.get("msg") or last_error)
                        continue

                self._logged_in = True
                return
            except httpx.HTTPError as exc:
                last_error = str(exc)

        raise XuiApiError(f"Не удалось войти в 3x-ui: {last_error or 'пустой ответ'}")

    async def request(self, method: str, path: str, **kwargs) -> Any:
        try:
            response = await self._client.request(method, self.url(path), **kwargs)
        except httpx.HTTPError as exc:
            raise XuiApiError(f"Ошибка подключения к 3x-ui: {exc}") from exc

        if response.status_code in {401, 403} and not XUI_API_TOKEN:
            self._logged_in = False
            await self.login()
            try:
                response = await self._client.request(method, self.url(path), **kwargs)
            except httpx.HTTPError as exc:
                raise XuiApiError(f"Ошибка подключения к 3x-ui: {exc}") from exc

        if response.status_code >= 400:
            raise XuiApiError(f"3x-ui вернул HTTP {response.status_code}: {response.text[:300]}")

        text = response.text.strip()
        if not text:
            return {}
        try:
            data = response.json()
        except ValueError:
            return {"raw": text}

        if isinstance(data, dict) and data.get("success") is False:
            raise XuiApiError(str(data.get("msg") or "3x-ui вернул success=false"))
        return data

    async def list_inbounds(self) -> list[dict[str, Any]]:
        data = await self.request("GET", "/panel/api/inbounds/list")
        obj = data.get("obj", data) if isinstance(data, dict) else data
        if obj is None:
            return []
        if not isinstance(obj, list):
            raise XuiApiError("Неожиданный ответ /panel/api/inbounds/list")
        return [item for item in obj if isinstance(item, dict)]

    @staticmethod
    def _settings(inbound: dict[str, Any]) -> dict[str, Any]:
        raw = inbound.get("settings") or {}
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                data = json.loads(raw)
                return data if isinstance(data, dict) else {}
            except ValueError:
                return {}
        return {}

    @staticmethod
    def _client_identifier(inbound: dict[str, Any], client: dict[str, Any]) -> str:
        protocol = str(inbound.get("protocol") or "").lower()
        if protocol == "trojan" and client.get("password"):
            return str(client["password"])
        if protocol in {"shadowsocks", "ss"} and client.get("email"):
            return str(client["email"])
        return str(client.get("id") or client.get("email") or "")

    async def find_clients(self, email: str) -> list[dict[str, Any]]:
        """Найти все копии одного клиента во всех inbounds.

        Для 3x-ui Node один и тот же клиент часто присутствует сразу в нескольких
        inbounds/подписках. Список клиентов группируется; продление в режиме
        clients_api выполняется через глобальный Clients API.
        """
        wanted = email.strip().casefold()
        matches: list[dict[str, Any]] = []
        if not wanted:
            return matches

        for inbound in await self.list_inbounds():
            settings = self._settings(inbound)
            clients = settings.get("clients") or []
            if not isinstance(clients, list):
                continue
            for client in clients:
                if not isinstance(client, dict):
                    continue
                values = [client.get("email"), client.get("id"), client.get("subId"), client.get("tgId")]
                if any(str(value).strip().casefold() == wanted for value in values if value is not None):
                    matches.append({"inbound": inbound, "client": client})
        return matches

    async def find_client(self, email: str) -> Optional[dict[str, Any]]:
        matches = await self.find_clients(email)
        return matches[0] if matches else None

    async def list_clients_raw(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for inbound in await self.list_inbounds():
            inbound_id = inbound.get("id")
            inbound_remark = inbound.get("remark") or "без названия"
            protocol = inbound.get("protocol") or "unknown"

            stats_by_email: dict[str, dict[str, Any]] = {}
            client_stats = inbound.get("clientStats") or inbound.get("client_stats") or []
            if isinstance(client_stats, list):
                for stat in client_stats:
                    if not isinstance(stat, dict):
                        continue
                    email = str(stat.get("email") or "").strip().casefold()
                    if email:
                        stats_by_email[email] = stat

            settings = self._settings(inbound)
            clients = settings.get("clients") or []
            if not isinstance(clients, list):
                continue

            for client in clients:
                if not isinstance(client, dict):
                    continue

                email = str(client.get("email") or "").strip()
                stat = stats_by_email.get(email.casefold(), {}) if email else {}
                up = safe_int(stat.get("up"))
                down = safe_int(stat.get("down"))
                total_limit = safe_int(client.get("totalGB") or stat.get("total"))
                expiry_ms = safe_int(client.get("expiryTime") or stat.get("expiryTime"))
                enabled = client.get("enable", stat.get("enable", True))

                result.append(
                    {
                        "email": email or "без email",
                        "key": (email or str(client.get("id") or client.get("subId") or client.get("tgId") or "без email")).strip().casefold(),
                        "inbound_id": inbound_id,
                        "inbound_remark": inbound_remark,
                        "protocol": protocol,
                        "enabled": bool(enabled),
                        "expiry_ms": expiry_ms,
                        "total_limit": total_limit,
                        "up": up,
                        "down": down,
                        "used": up + down,
                        "tgId": client.get("tgId") or "",
                        "subId": client.get("subId") or "",
                        "client_id": client.get("id") or "",
                    }
                )

        result.sort(key=lambda item: (str(item["email"]).casefold(), safe_int(item.get("inbound_id"))))
        return result

    @staticmethod
    def _merge_client_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
        enabled = any(bool(row.get("enabled")) for row in rows)
        expiry_values = [safe_int(row.get("expiry_ms")) for row in rows]
        positive_expiry = [value for value in expiry_values if value > 0]
        expiry_min = min(positive_expiry) if positive_expiry else 0
        expiry_max = max(positive_expiry) if positive_expiry else 0
        expiry_different = len(set(positive_expiry)) > 1

        total_limits = [safe_int(row.get("total_limit")) for row in rows]
        positive_limits = [value for value in total_limits if value > 0]
        total_limit = max(positive_limits) if positive_limits else 0

        protocols = sorted({str(row.get("protocol") or "unknown") for row in rows})
        tg_ids = sorted({str(row.get("tgId") or "").strip() for row in rows if str(row.get("tgId") or "").strip()})
        sub_ids = sorted({str(row.get("subId") or "").strip() for row in rows if str(row.get("subId") or "").strip()})
        inbounds = [
            {
                "id": row.get("inbound_id"),
                "remark": row.get("inbound_remark"),
                "protocol": row.get("protocol"),
            }
            for row in rows
        ]

        return {
            "email": rows[0].get("email") or "без email",
            "enabled": enabled,
            "expiry_ms": expiry_min,
            "expiry_ms_min": expiry_min,
            "expiry_ms_max": expiry_max,
            "expiry_different": expiry_different,
            "total_limit": total_limit,
            "up": sum(safe_int(row.get("up")) for row in rows),
            "down": sum(safe_int(row.get("down")) for row in rows),
            "used": sum(safe_int(row.get("used")) for row in rows),
            "tgId": ", ".join(tg_ids),
            "subId": ", ".join(sub_ids),
            "protocol": ", ".join(protocols),
            "inbound_count": len(rows),
            "inbounds": inbounds,
            "inbound_id": "-",
            "inbound_remark": f"{len(rows)} inbound(ов)",
        }

    async def list_clients(self) -> list[dict[str, Any]]:
        # Для 3x-ui Node сначала используем новый Clients API, чтобы не показывать
        # одного клиента по каждой подписке/inbound. Если API недоступен на старой
        # версии панели, возвращаемся к старому чтению inbounds.
        if XUI_RENEW_MODE == "clients_api":
            try:
                clients_api_rows = await self.list_clients_from_clients_api()
                if clients_api_rows:
                    return clients_api_rows
            except XuiApiError as exc:
                logger.warning("Clients API list недоступен, fallback на inbounds/list: %s", exc)

        raw_clients = await self.list_clients_raw()
        if not XUI_GROUP_CLIENTS_BY_EMAIL:
            return raw_clients

        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in raw_clients:
            key = str(item.get("key") or item.get("email") or "").casefold()
            grouped.setdefault(key, []).append(item)

        result = [self._merge_client_rows(rows) for rows in grouped.values()]
        result.sort(key=lambda item: str(item["email"]).casefold())
        return result

    @staticmethod
    def _tg_id_matches(value: Any, telegram_user_id: int) -> bool:
        actual = str(value or "").strip()
        if not actual:
            return False
        target = str(telegram_user_id).strip()
        # В разных версиях/форках поле может храниться как число, строка или строка с пробелами.
        return actual == target

    async def find_client_email_by_tg_id(self, telegram_user_id: int) -> Optional[str]:
        """Найти email клиента в 3x-ui по полю tgId.

        Это нужно для 3x-ui Node, когда Telegram аккаунты клиентов уже
        привязаны в самой панели. Тогда боту не требуется отдельная локальная
        команда /linksub — он сам сопоставляет пользователя Telegram с клиентом.
        """
        # Сначала пробуем новый Clients API.
        if XUI_RENEW_MODE == "clients_api":
            try:
                for row in await self.list_clients_from_clients_api():
                    if self._tg_id_matches(row.get("tgId"), telegram_user_id):
                        email = str(row.get("email") or "").strip()
                        if email and email != "без email":
                            return email
            except XuiApiError as exc:
                logger.warning("Не удалось найти клиента по tgId через Clients API: %s", exc)

        # Fallback: читаем клиентов из inbounds/settings.clients.
        for row in await self.list_clients_raw():
            if self._tg_id_matches(row.get("tgId"), telegram_user_id):
                email = str(row.get("email") or "").strip()
                if email and email != "без email":
                    return email
        return None

    async def get_client_summary(self, email: str) -> Optional[dict[str, Any]]:
        if XUI_RENEW_MODE == "clients_api":
            try:
                record = await self.get_client_record(email)
                row = self._client_api_row_from_record(record)
                client = dict(record.get("client") or {})
                inbound_ids = record.get("inboundIds") or []
                row["copies"] = [
                    {
                        "email": row.get("email") or email,
                        "inbound_id": inbound_id,
                        "inbound_remark": "attached inbound",
                        "protocol": "client",
                        "enabled": row.get("enabled", True),
                        "expiry_ms": row.get("expiry_ms", 0),
                        "total_limit": row.get("total_limit", 0),
                        "up": row.get("up", 0),
                        "down": row.get("down", 0),
                        "used": row.get("used", 0),
                        "tgId": row.get("tgId") or "",
                        "subId": row.get("subId") or "",
                        "limitIp": client.get("limitIp", 0),
                        "client_id": client.get("id") or client.get("uuid") or "",
                    }
                    for inbound_id in (inbound_ids if isinstance(inbound_ids, list) else [])
                ]
                if not row["copies"]:
                    row["copies"] = [{
                        "email": row.get("email") or email,
                        "inbound_id": "-",
                        "inbound_remark": "Clients API",
                        "protocol": "client",
                        "enabled": row.get("enabled", True),
                        "expiry_ms": row.get("expiry_ms", 0),
                        "total_limit": row.get("total_limit", 0),
                        "up": row.get("up", 0),
                        "down": row.get("down", 0),
                        "used": row.get("used", 0),
                        "tgId": row.get("tgId") or "",
                        "subId": row.get("subId") or "",
                        "limitIp": client.get("limitIp", 0),
                        "client_id": client.get("id") or client.get("uuid") or "",
                    }]
                return row
            except XuiApiError as exc:
                logger.warning("Clients API get недоступен, fallback на inbounds/list: %s", exc)

        matches = await self.find_clients(email)
        if not matches:
            return None

        raw_rows: list[dict[str, Any]] = []
        for match in matches:
            inbound = match["inbound"]
            client = match["client"]
            inbound_id = inbound.get("id")
            inbound_remark = inbound.get("remark") or "без названия"
            protocol = inbound.get("protocol") or "unknown"
            raw_rows.append(
                {
                    "email": str(client.get("email") or email).strip(),
                    "key": str(client.get("email") or email).strip().casefold(),
                    "inbound_id": inbound_id,
                    "inbound_remark": inbound_remark,
                    "protocol": protocol,
                    "enabled": bool(client.get("enable", True)),
                    "expiry_ms": safe_int(client.get("expiryTime")),
                    "total_limit": safe_int(client.get("totalGB")),
                    "up": 0,
                    "down": 0,
                    "used": 0,
                    "tgId": client.get("tgId") or "",
                    "subId": client.get("subId") or "",
                    "limitIp": client.get("limitIp", 0),
                    "client_id": client.get("id") or "",
                }
            )

        summary = self._merge_client_rows(raw_rows)
        summary["copies"] = raw_rows
        return summary

    @staticmethod
    def _format_found_inbound_ids(matches: list[dict[str, Any]]) -> str:
        ids = []
        for match in matches:
            inbound = match.get("inbound") or {}
            inbound_id = safe_int(inbound.get("id"))
            remark = str(inbound.get("remark") or "без названия")
            ids.append(f"{inbound_id} ({remark})")
        return ", ".join(ids)

    def _select_main_inbound_matches(self, matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not matches:
            return []

        if XUI_MAIN_INBOUND_ID_ERROR:
            raise XuiApiError(XUI_MAIN_INBOUND_ID_ERROR)

        if XUI_MAIN_INBOUND_ID is None:
            if len(matches) == 1:
                return matches
            found = self._format_found_inbound_ids(matches)
            raise XuiApiError(
                "Клиент найден в нескольких inbounds. Чтобы не продлить не тот inbound, "
                "укажите основной inbound в .env: XUI_MAIN_INBOUND_ID=ID. "
                f"Найдено: {found}. Список inbound можно посмотреть командой /inbounds"
            )

        selected = [
            match
            for match in matches
            if safe_int((match.get("inbound") or {}).get("id")) == XUI_MAIN_INBOUND_ID
        ]
        if not selected:
            found = self._format_found_inbound_ids(matches)
            raise XuiApiError(
                f"Клиент найден, но не в основном inbound #{XUI_MAIN_INBOUND_ID}. "
                f"Найдено в: {found}. Проверьте XUI_MAIN_INBOUND_ID или перенесите клиента в основной inbound."
            )
        return selected

    async def get_server_status(self) -> dict[str, Any]:
        data = await self.request("GET", "/panel/api/server/status")
        if isinstance(data, dict):
            obj = data.get("obj")
            return obj if isinstance(obj, dict) else data
        return {}

    async def restart_xray(self) -> None:
        await self.request("POST", "/panel/api/server/restartXrayService")

    async def get_client_record(self, email: str) -> dict[str, Any]:
        """Получить глобальную запись клиента из нового Clients API 3x-ui Node."""
        email_clean = email.strip()
        if not email_clean:
            raise XuiApiError("Email клиента не указан")

        encoded_email = quote(email_clean, safe="")
        data = await self.request("GET", f"/panel/api/clients/get/{encoded_email}")
        obj = data.get("obj", data) if isinstance(data, dict) else data

        if isinstance(obj, dict) and isinstance(obj.get("client"), dict):
            client = dict(obj["client"])
            inbound_ids = obj.get("inboundIds") or obj.get("inbound_ids") or client.get("inboundIds") or []
        elif isinstance(obj, dict) and (obj.get("email") or obj.get("id") or obj.get("uuid")):
            client = dict(obj)
            inbound_ids = client.get("inboundIds") or client.get("inbound_ids") or []
        else:
            raise XuiApiError(f"Клиент '{email_clean}' не найден через Clients API")

        if not client.get("email"):
            client["email"] = email_clean

        if not isinstance(inbound_ids, list):
            inbound_ids = []

        return {"client": client, "inboundIds": inbound_ids}

    @staticmethod
    def _client_api_row_from_record(record: dict[str, Any]) -> dict[str, Any]:
        client = dict(record.get("client") or {})
        inbound_ids = record.get("inboundIds") or client.get("inboundIds") or []
        if not isinstance(inbound_ids, list):
            inbound_ids = []

        up = safe_int(client.get("up") or client.get("usedUp"))
        down = safe_int(client.get("down") or client.get("usedDown"))
        used_gb = safe_int(client.get("usedGB"))
        used = up + down if up or down else used_gb

        total_limit = safe_int(client.get("totalGB") or client.get("total"))
        expiry_ms = safe_int(client.get("expiryTime"))
        enabled = bool(client.get("enable", True))
        email = str(client.get("email") or "без email").strip()

        return {
            "email": email,
            "key": email.casefold(),
            "inbound_id": "-",
            "inbound_remark": f"{len(inbound_ids)} inbound(ов)",
            "protocol": str(client.get("protocol") or client.get("security") or "client"),
            "enabled": enabled,
            "expiry_ms": expiry_ms,
            "expiry_ms_min": expiry_ms,
            "expiry_ms_max": expiry_ms,
            "expiry_different": False,
            "total_limit": total_limit,
            "up": up,
            "down": down,
            "used": used,
            "tgId": client.get("tgId") or "",
            "subId": client.get("subId") or "",
            "client_id": client.get("id") or client.get("uuid") or "",
            "inbound_count": len(inbound_ids),
            "inbounds": [{"id": item, "remark": "", "protocol": ""} for item in inbound_ids],
        }

    async def list_clients_from_clients_api(self) -> list[dict[str, Any]]:
        data = await self.request("GET", "/panel/api/clients/list")
        obj = data.get("obj", data) if isinstance(data, dict) else data

        if isinstance(obj, dict):
            if isinstance(obj.get("clients"), list):
                items = obj["clients"]
            elif isinstance(obj.get("records"), list):
                items = obj["records"]
            elif isinstance(obj.get("data"), list):
                items = obj["data"]
            else:
                items = []
        elif isinstance(obj, list):
            items = obj
        else:
            items = []

        result: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("client"), dict):
                record = {"client": item["client"], "inboundIds": item.get("inboundIds") or item.get("inbound_ids") or []}
            else:
                record = {"client": item, "inboundIds": item.get("inboundIds") or item.get("inbound_ids") or []}
            row = self._client_api_row_from_record(record)
            if row["email"]:
                result.append(row)

        result.sort(key=lambda item: str(item["email"]).casefold())
        return result

    @staticmethod
    def _sanitize_client_update_payload(client: dict[str, Any], email: str, inbound_ids: list[Any]) -> dict[str, Any]:
        """
        Запасной режим update/{email}.
        В некоторых версиях 3x-ui поле Client.id в Go-структуре имеет тип string,
        а Clients API может вернуть внутренний числовой id БД. Поэтому для update
        приводим id-поля к строкам и не отправляем None.
        """
        payload = {key: value for key, value in dict(client).items() if value is not None}
        payload["email"] = str(payload.get("email") or email).strip()

        for key in ("id", "uuid", "subId", "comment", "group", "security", "flow"):
            if key in payload and payload[key] is not None:
                payload[key] = str(payload[key])

        # В текущих версиях 3x-ui tgId в Go-структуре Client имеет тип int64.
        # Поэтому его нельзя отправлять строкой, иначе панель вернёт ошибку unmarshal.
        if "tgId" in payload and payload["tgId"] not in (None, ""):
            try:
                payload["tgId"] = int(payload["tgId"])
            except (TypeError, ValueError):
                payload["tgId"] = 0

        # Числовые поля оставляем числами, но аккуратно нормализуем типы.
        for key in ("expiryTime", "totalGB", "limitIp", "reset", "up", "down"):
            if key in payload and payload[key] not in (None, ""):
                try:
                    payload[key] = int(payload[key])
                except (TypeError, ValueError):
                    pass

        if "enable" in payload:
            payload["enable"] = bool(payload["enable"])

        if inbound_ids:
            clean_inbound_ids: list[int] = []
            for item in inbound_ids:
                try:
                    clean_inbound_ids.append(int(item))
                except (TypeError, ValueError):
                    continue
            if clean_inbound_ids:
                payload["inboundIds"] = clean_inbound_ids

        return payload

    async def set_client_tg_id(self, email: str, telegram_user_id: int) -> dict[str, Any]:
        """Записать tgId клиента в саму панель 3x-ui через Clients API.

        Используется, когда админ вручную указал email клиента в заявке:
        бот сохраняет привязку не только локально, но и в панели.
        """
        if XUI_RENEW_MODE != "clients_api":
            raise XuiApiError("Запись tgId в панель поддерживается только в режиме XUI_RENEW_MODE=clients_api")

        email_clean = email.strip()
        if not email_clean:
            raise XuiApiError("Email клиента не указан")

        record = await self.get_client_record(email_clean)
        client = dict(record["client"])
        inbound_ids = record.get("inboundIds") or []

        payload = self._sanitize_client_update_payload(client, email_clean, inbound_ids)
        payload["tgId"] = int(telegram_user_id)
        update_email = str(payload.get("email") or email_clean).strip() or email_clean
        encoded_email = quote(update_email, safe="")

        await self.request("POST", f"/panel/api/clients/update/{encoded_email}", json=payload)

        checked = await self.get_client_record(update_email)
        checked_client = checked.get("client") or {}
        if not self._tg_id_matches(checked_client.get("tgId"), telegram_user_id):
            raise XuiApiError(
                "3x-ui ответил без ошибки, но tgId у клиента не изменился. "
                "Проверьте API Docs панели для /panel/api/clients/update/{email}."
            )

        return {
            "email": update_email,
            "tgId": int(telegram_user_id),
            "old_tgId": str(client.get("tgId") or ""),
        }

    async def get_new_client_inbound_ids(self) -> list[int]:
        """Вернуть inboundIds для нового клиента.

        Лучше указать XUI_NEW_CLIENT_INBOUND_IDS в .env. Если настройка пустая,
        бот попробует взять все multi-client inbounds через /inbounds/options
        или через /inbounds/list.
        """
        if XUI_NEW_CLIENT_INBOUND_IDS:
            return list(XUI_NEW_CLIENT_INBOUND_IDS)

        ids: list[int] = []
        try:
            data = await self.request("GET", "/panel/api/inbounds/options")
            obj = data.get("obj", data) if isinstance(data, dict) else data
            if isinstance(obj, dict):
                items = obj.get("inbounds") or obj.get("options") or obj.get("data") or []
            else:
                items = obj
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    protocol = str(item.get("protocol") or "").lower()
                    inbound_id = safe_int(item.get("id"))
                    if inbound_id and protocol in {"vless", "vmess", "trojan", "shadowsocks", "ss", "hysteria", "hysteria2", "portfallback"}:
                        ids.append(inbound_id)
        except XuiApiError as exc:
            logger.warning("/panel/api/inbounds/options недоступен, fallback на inbounds/list: %s", exc)

        if not ids:
            for inbound in await self.list_inbounds():
                protocol = str(inbound.get("protocol") or "").lower()
                inbound_id = safe_int(inbound.get("id"))
                if inbound_id and protocol in {"vless", "vmess", "trojan", "shadowsocks", "ss", "hysteria", "hysteria2", "portfallback"}:
                    ids.append(inbound_id)

        if not ids:
            raise XuiApiError(
                "Не удалось определить inboundIds для нового клиента. "
                "Укажите их в .env: XUI_NEW_CLIENT_INBOUND_IDS=1,2,3"
            )
        return sorted(set(ids))

    async def add_client_by_clients_api(self, email: str, days: int, telegram_user_id: Optional[int] = None) -> dict[str, Any]:
        """Создать нового глобального клиента через Clients API 3x-ui Node."""
        if days <= 0:
            raise XuiApiError("Количество дней должно быть больше 0")
        email_clean = email.strip()
        if not email_clean:
            raise XuiApiError("Email нового клиента не указан")

        # Если такой email уже есть, не создаём дубль.
        try:
            await self.get_client_record(email_clean)
            raise XuiApiError(f"Клиент с email '{email_clean}' уже существует в 3x-ui")
        except XuiApiError as exc:
            if "уже существует" in str(exc):
                raise
            # Ошибка поиска — ожидаемый сценарий для нового клиента.

        inbound_ids = await self.get_new_client_inbound_ids()
        now_ms = int(time.time() * 1000)
        expiry_ms = now_ms + int(days) * 24 * 60 * 60 * 1000
        client_uuid = str(uuid.uuid4())
        sub_id = uuid.uuid4().hex[:16]

        client_payload: dict[str, Any] = {
            "id": client_uuid,
            "email": email_clean,
            "enable": True,
            "expiryTime": expiry_ms,
            "totalGB": int(XUI_NEW_CLIENT_TOTAL_GB),
            "limitIp": int(XUI_NEW_CLIENT_LIMIT_IP),
            "tgId": int(telegram_user_id or 0),
            "subId": sub_id,
        }
        if XUI_NEW_CLIENT_FLOW:
            client_payload["flow"] = XUI_NEW_CLIENT_FLOW
        if XUI_NEW_CLIENT_GROUP:
            client_payload["group"] = XUI_NEW_CLIENT_GROUP
        if XUI_NEW_CLIENT_COMMENT:
            client_payload["comment"] = XUI_NEW_CLIENT_COMMENT

        # В актуальном 3x-ui /panel/api/clients/add принимает SaveCreatePayload:
        # {"client": {...}, "inboundIds": [...]}. Если отправить поля клиента
        # плоским объектом, Go-сервер видит пустой client и возвращает
        # ошибку "client email is required".
        payload: dict[str, Any] = {
            "client": client_payload,
            "inboundIds": inbound_ids,
        }

        try:
            await self.request("POST", "/panel/api/clients/add", json=payload)
        except XuiApiError as exc:
            if "HTTP 404" in str(exc):
                raise XuiApiError(
                    "В этой версии 3x-ui не найден endpoint /panel/api/clients/add. "
                    "Откройте API Docs в панели и проверьте endpoint создания клиента."
                ) from exc
            raise

        if XUI_RESTART_XRAY_AFTER_RENEW:
            await self.restart_xray()

        checked = await self.get_client_record(email_clean)
        checked_client = checked.get("client") or {}
        return {
            "email": str(checked_client.get("email") or email_clean),
            "new_expiry_ms": safe_int(checked_client.get("expiryTime") or expiry_ms),
            "days": int(days),
            "tgId": int(telegram_user_id or 0),
            "inbound_ids": checked.get("inboundIds") or inbound_ids,
            "client_id": str(checked_client.get("id") or client_uuid),
            "subId": str(checked_client.get("subId") or sub_id),
            "created_mode": "clients_api/add",
        }

    async def add_client_legacy_inbound(self, email: str, days: int, telegram_user_id: Optional[int] = None) -> dict[str, Any]:
        """Запасной режим для старого API: добавить клиента в основной inbound."""
        if XUI_MAIN_INBOUND_ID_ERROR:
            raise XuiApiError(XUI_MAIN_INBOUND_ID_ERROR)
        if XUI_MAIN_INBOUND_ID is None:
            raise XuiApiError("Для старого режима добавления укажите XUI_MAIN_INBOUND_ID в .env")
        if days <= 0:
            raise XuiApiError("Количество дней должно быть больше 0")

        email_clean = email.strip()
        matches = await self.find_clients(email_clean)
        if matches:
            raise XuiApiError(f"Клиент с email '{email_clean}' уже существует в 3x-ui")

        now_ms = int(time.time() * 1000)
        expiry_ms = now_ms + int(days) * 24 * 60 * 60 * 1000
        client = {
            "id": str(uuid.uuid4()),
            "email": email_clean,
            "enable": True,
            "expiryTime": expiry_ms,
            "totalGB": int(XUI_NEW_CLIENT_TOTAL_GB),
            "limitIp": int(XUI_NEW_CLIENT_LIMIT_IP),
            "tgId": int(telegram_user_id or 0),
            "subId": uuid.uuid4().hex[:16],
        }
        if XUI_NEW_CLIENT_FLOW:
            client["flow"] = XUI_NEW_CLIENT_FLOW

        payload = {"id": int(XUI_MAIN_INBOUND_ID), "settings": json.dumps({"clients": [client]}, ensure_ascii=False)}
        await self.request("POST", "/panel/api/inbounds/addClient", json=payload)

        if XUI_RESTART_XRAY_AFTER_RENEW:
            await self.restart_xray()

        return {
            "email": email_clean,
            "new_expiry_ms": expiry_ms,
            "days": int(days),
            "tgId": int(telegram_user_id or 0),
            "inbound_ids": [int(XUI_MAIN_INBOUND_ID)],
            "client_id": client["id"],
            "subId": client["subId"],
            "created_mode": "legacy_inbound/addClient",
        }

    async def add_client(self, email: str, days: int, telegram_user_id: Optional[int] = None) -> dict[str, Any]:
        if XUI_RENEW_MODE == "legacy_inbound":
            return await self.add_client_legacy_inbound(email, days, telegram_user_id)
        return await self.add_client_by_clients_api(email, days, telegram_user_id)

    async def renew_client_by_clients_api(self, email: str, days: int) -> dict[str, Any]:
        """Продлить глобальную подписку клиента через новый Clients API."""
        if XUI_CLIENTS_RENEW_METHOD == "update":
            return await self.renew_client_by_clients_api_update(email, days)
        return await self.renew_client_by_clients_api_bulk_adjust(email, days)

    async def renew_client_by_clients_api_bulk_adjust(self, email: str, days: int) -> dict[str, Any]:
        """
        Правильное продление для 3x-ui Node: не перезаписывает клиента,
        а добавляет дни подписки через /panel/api/clients/bulkAdjust.
        """
        if days <= 0:
            raise XuiApiError("Количество дней должно быть больше 0")

        email_clean = email.strip()
        record = await self.get_client_record(email_clean)
        client = dict(record["client"])
        inbound_ids = record.get("inboundIds") or []

        old_expiry_ms = safe_int(client.get("expiryTime"))
        now_ms = int(time.time() * 1000)

        # Важная правка для просроченных подписок.
        # bulkAdjust добавляет дни к старому expiryTime. Если клиент истёк неделю назад,
        # обычный addDays=30 даст фактически только ~23 дня от текущего момента.
        # Поэтому для уже истёкших подписок выставляем точный expiryTime через update/{email}:
        # новый срок = сейчас + DAYS. Для активных подписок оставляем bulkAdjust.
        if XUI_RENEW_EXPIRED_FROM_NOW and old_expiry_ms > 0 and old_expiry_ms < now_ms:
            result = await self.renew_client_by_clients_api_update(email_clean, days)
            result["renew_mode"] = "clients_api/update_expired_from_now"
            result["expired_before_renew"] = True
            return result

        payload = {
            "emails": [str(client.get("email") or email_clean).strip() or email_clean],
            "addDays": int(days),
            "addBytes": 0,
        }

        try:
            result_data = await self.request("POST", "/panel/api/clients/bulkAdjust", json=payload)
        except XuiApiError as exc:
            message = str(exc)
            if "HTTP 404" in message:
                raise XuiApiError(
                    "В этой версии 3x-ui не найден endpoint /panel/api/clients/bulkAdjust. "
                    "Откройте API Docs в панели и проверьте название endpoint для Bulk Adjust. "
                    "Как временный вариант можно поставить XUI_CLIENTS_RENEW_METHOD=update, "
                    "но основной режим продления должен быть bulk_adjust."
                ) from exc
            raise

        if XUI_RESTART_XRAY_AFTER_RENEW:
            await self.restart_xray()

        checked = await self.get_client_record(email_clean)
        checked_client = checked.get("client") or {}
        checked_expiry = safe_int(checked_client.get("expiryTime"))
        checked_inbound_ids = checked.get("inboundIds") or inbound_ids

        skipped = []
        updated_count = 1
        if isinstance(result_data, dict):
            obj = result_data.get("obj", result_data)
            if isinstance(obj, dict):
                raw_skipped = obj.get("skipped") or obj.get("failed") or []
                if isinstance(raw_skipped, list):
                    skipped = raw_skipped
                updated_count = safe_int(obj.get("updated") or obj.get("success") or obj.get("count") or 1) or 1

        if skipped:
            raise XuiApiError(f"3x-ui не продлил клиента: {skipped}")

        return {
            "email": str(checked_client.get("email") or client.get("email") or email_clean),
            "old_expiry_ms": old_expiry_ms,
            "old_expiry_ms_min": old_expiry_ms,
            "old_expiry_ms_max": old_expiry_ms,
            "old_expiry_different": False,
            "new_expiry_ms": checked_expiry,
            "days": days,
            "enabled": bool(checked_client.get("enable", True)),
            "updated_count": updated_count,
            "matched_count": 1,
            "found_count": 1,
            "skipped_count": 0,
            "main_inbound_id": "подписка клиента",
            "renew_mode": "clients_api/bulk_adjust",
            "updated_items": [{"inbound_id": "подписка", "inbound_remark": "Clients API bulkAdjust", "protocol": "client"}],
            "failures": [],
            "inbound_id": "подписка",
            "inbound_remark": f"Clients API bulkAdjust, attached inbounds: {len(checked_inbound_ids) if isinstance(checked_inbound_ids, list) else 0}",
            "protocol": "client",
        }

    async def renew_client_by_clients_api_update(self, email: str, days: int) -> dict[str, Any]:
        """Запасной режим: выставить expiryTime через /panel/api/clients/update/{email}."""
        if days <= 0:
            raise XuiApiError("Количество дней должно быть больше 0")

        email_clean = email.strip()
        record = await self.get_client_record(email_clean)
        client = dict(record["client"])
        inbound_ids = record.get("inboundIds") or []

        old_expiry_ms = safe_int(client.get("expiryTime"))
        now_ms = int(time.time() * 1000)
        base_ms = old_expiry_ms if old_expiry_ms > now_ms else now_ms
        new_expiry_ms = base_ms + days * 24 * 60 * 60 * 1000

        payload = self._sanitize_client_update_payload(client, email_clean, inbound_ids)
        payload["expiryTime"] = new_expiry_ms
        payload["enable"] = True

        update_email = str(payload.get("email") or email_clean).strip() or email_clean
        encoded_email = quote(update_email, safe="")
        await self.request("POST", f"/panel/api/clients/update/{encoded_email}", json=payload)

        if XUI_RESTART_XRAY_AFTER_RENEW:
            await self.restart_xray()

        checked = await self.get_client_record(email_clean)
        checked_expiry = safe_int((checked.get("client") or {}).get("expiryTime"))
        if checked_expiry < new_expiry_ms:
            raise XuiApiError(
                "3x-ui ответил без ошибки, но срок подписки не изменился. "
                "Проверьте API Docs панели для /panel/api/clients/update/{email}."
            )

        checked_inbound_ids = checked.get("inboundIds") or inbound_ids
        return {
            "email": payload["email"],
            "old_expiry_ms": old_expiry_ms,
            "old_expiry_ms_min": old_expiry_ms,
            "old_expiry_ms_max": old_expiry_ms,
            "old_expiry_different": False,
            "new_expiry_ms": checked_expiry,
            "days": days,
            "enabled": True,
            "updated_count": 1,
            "matched_count": 1,
            "found_count": 1,
            "skipped_count": 0,
            "main_inbound_id": "подписка клиента",
            "renew_mode": "clients_api/update",
            "updated_items": [{"inbound_id": "подписка", "inbound_remark": "Clients API update", "protocol": "client"}],
            "failures": [],
            "inbound_id": "подписка",
            "inbound_remark": f"Clients API update, attached inbounds: {len(checked_inbound_ids) if isinstance(checked_inbound_ids, list) else 0}",
            "protocol": "client",
        }

    async def renew_client(self, email: str, days: int) -> dict[str, Any]:
        if XUI_RENEW_MODE == "legacy_inbound":
            return await self.renew_client_legacy_inbound(email, days)
        return await self.renew_client_by_clients_api(email, days)

    async def renew_client_legacy_inbound(self, email: str, days: int) -> dict[str, Any]:
        if days <= 0:
            raise XuiApiError("Количество дней должно быть больше 0")

        all_matches = await self.find_clients(email)
        if not all_matches:
            raise XuiApiError(f"Клиент '{email}' не найден в 3x-ui")

        matches = self._select_main_inbound_matches(all_matches)

        now_ms = int(time.time() * 1000)
        old_expiry_values = [safe_int(match["client"].get("expiryTime")) for match in matches]
        positive_old_expiry = [value for value in old_expiry_values if value > 0]
        old_expiry_min = min(positive_old_expiry) if positive_old_expiry else 0
        old_expiry_max = max(positive_old_expiry) if positive_old_expiry else 0
        base_ms = old_expiry_max if old_expiry_max > now_ms else now_ms
        new_expiry_ms = base_ms + days * 24 * 60 * 60 * 1000

        updated_items: list[dict[str, Any]] = []
        failures: list[str] = []

        for match in matches:
            inbound = match["inbound"]
            client = dict(match["client"])
            inbound_id = int(inbound.get("id"))
            client_id = self._client_identifier(inbound, client)
            if not client_id:
                failures.append(f"inbound {inbound_id}: не удалось определить clientId")
                continue

            client["expiryTime"] = new_expiry_ms
            client["enable"] = True

            payload = {
                "id": inbound_id,
                "settings": json.dumps({"clients": [client]}, ensure_ascii=False),
            }
            try:
                await self.request("POST", f"/panel/api/inbounds/updateClient/{quote(client_id, safe='')}", json=payload)
                updated_items.append(
                    {
                        "inbound_id": inbound_id,
                        "inbound_remark": inbound.get("remark") or "без названия",
                        "protocol": inbound.get("protocol") or "unknown",
                    }
                )
            except XuiApiError as exc:
                failures.append(f"inbound {inbound_id}: {exc}")

        if not updated_items:
            raise XuiApiError("Не удалось обновить клиента ни в одном inbound: " + "; ".join(failures))

        if XUI_RESTART_XRAY_AFTER_RENEW:
            await self.restart_xray()

        # Контрольная проверка: перечитать клиента и убедиться, что legacy inbound изменился.
        checked_matches = await self.find_clients(email)
        checked_by_inbound = {
            safe_int(match["inbound"].get("id")): safe_int(match["client"].get("expiryTime"))
            for match in checked_matches
        }
        changed_bad = [
            str(item["inbound_id"])
            for item in updated_items
            if checked_by_inbound.get(safe_int(item["inbound_id"]), 0) < new_expiry_ms
        ]
        if changed_bad:
            raise XuiApiError(
                "3x-ui ответил без ошибки, но expiryTime не изменился в inbound: "
                + ", ".join(changed_bad)
                + ". Проверьте версию панели и права API-токена."
            )

        return {
            "email": matches[0]["client"].get("email") or email,
            "old_expiry_ms": old_expiry_max,
            "old_expiry_ms_min": old_expiry_min,
            "old_expiry_ms_max": old_expiry_max,
            "old_expiry_different": len(set(positive_old_expiry)) > 1,
            "new_expiry_ms": new_expiry_ms,
            "days": days,
            "enabled": True,
            "updated_count": len(updated_items),
            "matched_count": len(matches),
            "found_count": len(all_matches),
            "skipped_count": max(0, len(all_matches) - len(matches)),
            "main_inbound_id": updated_items[0]["inbound_id"],
            "renew_mode": "main_inbound",
            "updated_items": updated_items,
            "failures": failures,
            # Поля оставлены для совместимости со старым текстом ответа.
            "inbound_id": updated_items[0]["inbound_id"],
            "inbound_remark": updated_items[0]["inbound_remark"],
            "protocol": updated_items[0]["protocol"],
        }


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def format_xui_datetime(ms: int) -> str:
    if not ms:
        return "без ограничения"
    if ms < 0:
        return f"относительное значение 3x-ui: {ms}"
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return dt.strftime("%d.%m.%Y %H:%M UTC")


def format_bytes(value: Any) -> str:
    try:
        num = int(value or 0)
    except (TypeError, ValueError):
        return "0 Б"
    if num <= 0:
        return "без ограничения"
    units = ["Б", "КБ", "МБ", "ГБ", "ТБ"]
    size = float(num)
    unit_index = 0
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    return f"{size:.2f} {units[unit_index]}"


def format_used_bytes(value: Any) -> str:
    try:
        num = int(value or 0)
    except (TypeError, ValueError):
        num = 0
    if num <= 0:
        return "0 Б"
    units = ["Б", "КБ", "МБ", "ГБ", "ТБ"]
    size = float(num)
    unit_index = 0
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    return f"{size:.2f} {units[unit_index]}"


def format_xui_expiry_range(client: dict[str, Any]) -> str:
    if client.get("expiry_different"):
        return (
            f"с {format_xui_datetime(safe_int(client.get('expiry_ms_min')))} "
            f"до {format_xui_datetime(safe_int(client.get('expiry_ms_max')))}"
        )
    return format_xui_datetime(safe_int(client.get("expiry_ms")))


def format_xui_clients_page(clients: list[dict[str, Any]], page: int) -> tuple[str, InlineKeyboardMarkup | None]:
    total = len(clients)
    pages = max(1, (total + XUI_CLIENTS_PER_PAGE - 1) // XUI_CLIENTS_PER_PAGE)
    page = min(max(page, 1), pages)
    start = (page - 1) * XUI_CLIENTS_PER_PAGE
    end = start + XUI_CLIENTS_PER_PAGE

    mode_text = "уникальные клиенты" if XUI_GROUP_CLIENTS_BY_EMAIL else "клиенты по inbounds"
    lines = [f"Клиенты 3x-ui: {total} ({mode_text})", f"Страница {page}/{pages}", ""]
    for index, client in enumerate(clients[start:end], start=start + 1):
        status = "✅ включён" if client["enabled"] else "⛔ выключен"
        used = safe_int(client.get("used"))
        limit = safe_int(client.get("total_limit"))
        traffic = format_used_bytes(used)
        if limit > 0:
            traffic += f" / {format_bytes(limit)}"
        else:
            traffic += " / без ограничения"

        tg_id = str(client.get("tgId") or "").strip()
        tg_line = f"\n   tgId: <code>{html.escape(tg_id)}</code>" if tg_id else ""

        inbound_count = safe_int(client.get("inbound_count"), 1)
        if inbound_count > 1:
            inbound_line = f"   Inbounds: <code>{inbound_count}</code> шт."
        else:
            inbound_line = (
                f"   Inbound: <code>{html.escape(str(client.get('inbound_id')))}</code> — "
                f"{html.escape(str(client.get('inbound_remark')))}"
            )

        lines.append(
            f"<b>{index}. {html.escape(str(client['email']))}</b>\n"
            f"{inbound_line}\n"
            f"   Протоколы: <code>{html.escape(str(client.get('protocol') or 'unknown'))}</code> | {status}\n"
            f"   Истекает: <code>{format_xui_expiry_range(client)}</code>\n"
            f"   Трафик: <code>{traffic}</code>"
            f"{tg_line}"
        )

    keyboard = None
    if pages > 1:
        buttons: list[InlineKeyboardButton] = []
        if page > 1:
            buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"clients:{page - 1}"))
        if page < pages:
            buttons.append(InlineKeyboardButton("Вперёд ➡️", callback_data=f"clients:{page + 1}"))
        if buttons:
            keyboard = InlineKeyboardMarkup([buttons])

    return "\n".join(lines), keyboard


def create_ticket(user_id: int) -> int:
    """Создать новое обращение, не переиспользуя уже открытые."""
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO tickets(user_id, status, created_at, updated_at) VALUES (?, 'open', ?, ?)",
            (user_id, now_iso(), now_iso()),
        )
        return int(cur.lastrowid)


def get_or_create_open_ticket(user_id: int) -> int:
    with db() as conn:
        row = conn.execute(
            "SELECT id FROM tickets WHERE user_id = ? AND status = 'open' ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        if row:
            ticket_id = int(row["id"])
            conn.execute("UPDATE tickets SET updated_at = ? WHERE id = ?", (now_iso(), ticket_id))
            return ticket_id

    return create_ticket(user_id)


def get_ticket(ticket_id: int) -> Optional[sqlite3.Row]:
    with db() as conn:
        return conn.execute(
            """
            SELECT t.*, u.username, u.first_name, u.last_name, u.is_banned
            FROM tickets t
            JOIN users u ON u.user_id = t.user_id
            WHERE t.id = ?
            """,
            (ticket_id,),
        ).fetchone()


def log_message(
    ticket_id: int,
    direction: str,
    telegram_message_id: Optional[int],
    content_type: str,
    text: Optional[str],
    user_id: Optional[int] = None,
    admin_id: Optional[int] = None,
) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO messages(ticket_id, user_id, admin_id, direction, telegram_message_id, content_type, text, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ticket_id, user_id, admin_id, direction, telegram_message_id, content_type, text, now_iso()),
        )
        conn.execute("UPDATE tickets SET updated_at = ? WHERE id = ?", (now_iso(), ticket_id))


def close_ticket(ticket_id: int) -> bool:
    with db() as conn:
        cur = conn.execute(
            "UPDATE tickets SET status = 'closed', updated_at = ?, closed_at = ? WHERE id = ? AND status != 'closed'",
            (now_iso(), now_iso(), ticket_id),
        )
        return cur.rowcount > 0


def save_admin_message_map(admin_id: int, admin_message_id: int, ticket_id: int, user_id: int) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO admin_message_map(admin_id, admin_chat_message_id, ticket_id, user_id, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (admin_id, admin_message_id, ticket_id, user_id, now_iso()),
        )


def find_ticket_by_admin_message(admin_id: int, admin_message_id: int) -> Optional[sqlite3.Row]:
    with db() as conn:
        return conn.execute(
            """
            SELECT * FROM admin_message_map
            WHERE admin_id = ? AND admin_chat_message_id = ?
            """,
            (admin_id, admin_message_id),
        ).fetchone()


def create_renewal_request(
    ticket_id: int,
    telegram_user_id: int,
    xui_email: Optional[str],
    months: int,
    days: int,
    payment_method: str = "manual",
) -> None:
    payment_method = "stars" if str(payment_method).lower() == "stars" else "manual"
    with db() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO renewal_requests(
                ticket_id, telegram_user_id, xui_email, months, days, status, created_at, payment_method
            )
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (ticket_id, telegram_user_id, xui_email, months, days, now_iso(), payment_method),
        )


def get_renewal_request(ticket_id: int) -> Optional[sqlite3.Row]:
    with db() as conn:
        return conn.execute(
            """
            SELECT rr.*, t.status AS ticket_status, u.username, u.first_name, u.last_name
            FROM renewal_requests rr
            JOIN tickets t ON t.id = rr.ticket_id
            JOIN users u ON u.user_id = rr.telegram_user_id
            WHERE rr.ticket_id = ?
            """,
            (ticket_id,),
        ).fetchone()


def mark_renewal_request_renewed(ticket_id: int, admin_id: int) -> None:
    with db() as conn:
        conn.execute(
            """
            UPDATE renewal_requests
            SET status = 'renewed', renewed_at = ?, renewed_by = ?
            WHERE ticket_id = ?
            """,
            (now_iso(), admin_id, ticket_id),
        )


def mark_renewal_request_rejected(ticket_id: int, admin_id: int) -> None:
    with db() as conn:
        conn.execute(
            """
            UPDATE renewal_requests
            SET status = 'rejected', rejected_at = ?, rejected_by = ?
            WHERE ticket_id = ? AND status = 'pending'
            """,
            (now_iso(), admin_id, ticket_id),
        )


def mark_renewal_payment_confirmed(ticket_id: int, user_id: int) -> None:
    with db() as conn:
        conn.execute(
            """
            UPDATE renewal_requests
            SET payment_confirmed_at = ?, payment_confirmed_by = ?
            WHERE ticket_id = ? AND telegram_user_id = ? AND status = 'pending'
            """,
            (now_iso(), user_id, ticket_id, user_id),
        )


def mark_renewal_payment_details_sent(ticket_id: int, admin_id: int) -> None:
    with db() as conn:
        conn.execute(
            """
            UPDATE renewal_requests
            SET payment_details_sent_at = COALESCE(payment_details_sent_at, ?),
                payment_details_sent_by = COALESCE(payment_details_sent_by, ?),
                payment_method = COALESCE(payment_method, 'manual')
            WHERE ticket_id = ? AND status = 'pending'
            """,
            (now_iso(), admin_id, ticket_id),
        )


def mark_renewal_stars_invoice_sent(ticket_id: int, admin_id: int, stars_amount: int) -> None:
    with db() as conn:
        conn.execute(
            """
            UPDATE renewal_requests
            SET payment_details_sent_at = COALESCE(payment_details_sent_at, ?),
                payment_details_sent_by = COALESCE(payment_details_sent_by, ?),
                payment_method = 'stars',
                stars_amount = ?,
                stars_invoice_sent_at = COALESCE(stars_invoice_sent_at, ?),
                stars_invoice_sent_by = COALESCE(stars_invoice_sent_by, ?)
            WHERE ticket_id = ? AND status = 'pending'
            """,
            (now_iso(), admin_id, stars_amount, now_iso(), admin_id, ticket_id),
        )


def mark_renewal_stars_paid(ticket_id: int, user_id: int, charge_id: str, stars_amount: int) -> None:
    with db() as conn:
        conn.execute(
            """
            UPDATE renewal_requests
            SET payment_confirmed_at = COALESCE(payment_confirmed_at, ?),
                payment_confirmed_by = COALESCE(payment_confirmed_by, ?),
                payment_method = 'stars',
                stars_charge_id = COALESCE(stars_charge_id, ?),
                stars_amount = COALESCE(stars_amount, ?)
            WHERE ticket_id = ? AND telegram_user_id = ? AND status = 'pending'
            """,
            (now_iso(), user_id, charge_id, stars_amount, ticket_id, user_id),
        )


def renewal_payment_details_sent(ticket_id: int) -> bool:
    request = get_renewal_request(ticket_id)
    return bool(request and request["payment_details_sent_at"])


def find_latest_pending_renewal_for_user(telegram_user_id: int) -> Optional[sqlite3.Row]:
    with db() as conn:
        return conn.execute(
            """
            SELECT * FROM renewal_requests
            WHERE telegram_user_id = ? AND status = 'pending'
            ORDER BY ticket_id DESC
            LIMIT 1
            """,
            (telegram_user_id,),
        ).fetchone()


TICKETS_PER_PAGE = 10
TICKET_HISTORY_PER_PAGE = 10
RENEWAL_REQUESTS_PER_PAGE = 10


def count_open_tickets() -> int:
    with db() as conn:
        return int(conn.execute("SELECT COUNT(*) AS c FROM tickets WHERE status = 'open'").fetchone()["c"])


def list_open_tickets(page: int = 1, per_page: int = TICKETS_PER_PAGE) -> tuple[list[sqlite3.Row], int, int]:
    total = count_open_tickets()
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(max(page, 1), pages)
    offset = (page - 1) * per_page
    with db() as conn:
        rows = conn.execute(
            """
            SELECT t.id, t.user_id, t.status, t.updated_at, u.username, u.first_name, u.last_name,
                   (SELECT text FROM messages m WHERE m.ticket_id = t.id ORDER BY m.id DESC LIMIT 1) AS last_text
            FROM tickets t
            JOIN users u ON u.user_id = t.user_id
            WHERE t.status = 'open'
            ORDER BY t.updated_at DESC
            LIMIT ? OFFSET ?
            """,
            (per_page, offset),
        ).fetchall()
    return list(rows), page, pages


def format_tickets_list_page(rows: list[sqlite3.Row], page: int, pages: int, total: int) -> tuple[str, InlineKeyboardMarkup | None]:
    if not rows:
        return "Открытых обращений нет.", None

    lines = [f"Открытые обращения: {total}", f"Страница {page}/{pages}", "", "Нажмите на обращение, чтобы открыть его."]
    keyboard_rows: list[list[InlineKeyboardButton]] = []
    for row in rows:
        name = " ".join(filter(None, [row["first_name"], row["last_name"]])).strip() or "Без имени"
        last = html.escape((row["last_text"] or "без текста")[:70])
        lines.append(f"#{row['id']} — {html.escape(name)} ({row['user_id']}): {last}")
        keyboard_rows.append([InlineKeyboardButton(f"#{row['id']} — {name[:24]}", callback_data=f"ticket:{row['id']}")])

    nav_buttons: list[InlineKeyboardButton] = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"tickets:{page - 1}"))
    if page < pages:
        nav_buttons.append(InlineKeyboardButton("Вперёд ➡️", callback_data=f"tickets:{page + 1}"))
    if nav_buttons:
        keyboard_rows.append(nav_buttons)

    return "\n".join(lines), InlineKeyboardMarkup(keyboard_rows) if keyboard_rows else None




def count_all_tickets() -> int:
    with db() as conn:
        return int(conn.execute("SELECT COUNT(*) AS c FROM tickets").fetchone()["c"])


def list_all_tickets(page: int = 1, per_page: int = TICKET_HISTORY_PER_PAGE) -> tuple[list[sqlite3.Row], int, int, int]:
    total = count_all_tickets()
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(max(page, 1), pages)
    offset = (page - 1) * per_page
    with db() as conn:
        rows = conn.execute(
            """
            SELECT t.id, t.user_id, t.status, t.created_at, t.updated_at, t.closed_at,
                   u.username, u.first_name, u.last_name,
                   (SELECT COUNT(*) FROM messages m WHERE m.ticket_id = t.id) AS messages_count,
                   (SELECT text FROM messages m WHERE m.ticket_id = t.id ORDER BY m.id DESC LIMIT 1) AS last_text
            FROM tickets t
            JOIN users u ON u.user_id = t.user_id
            ORDER BY t.id DESC
            LIMIT ? OFFSET ?
            """,
            (per_page, offset),
        ).fetchall()
    return list(rows), page, pages, total


def format_ticket_history_page(rows: list[sqlite3.Row], page: int, pages: int, total: int) -> tuple[str, InlineKeyboardMarkup | None]:
    if not rows:
        return "История обращений пока пустая.", None

    lines = [
        f"📚 История обращений: {total}",
        f"Страница {page}/{pages}",
        "",
        "Нажмите на обращение, чтобы открыть полный чат.",
    ]
    keyboard_rows: list[list[InlineKeyboardButton]] = []
    for row in rows:
        name = " ".join(filter(None, [row["first_name"], row["last_name"]])).strip() or "Без имени"
        status = str(row["status"])
        icon = "🟢" if status == "open" else "⚪"
        last = html.escape((row["last_text"] or "без текста")[:70])
        messages_count = int(row["messages_count"] or 0)
        lines.append(
            f"{icon} #{row['id']} — {html.escape(name)} ({row['user_id']})\n"
            f"   Статус: <code>{html.escape(status)}</code>, сообщений: <code>{messages_count}</code>\n"
            f"   Последнее: {last}"
        )
        keyboard_rows.append([
            InlineKeyboardButton(
                f"{icon} #{row['id']} — {name[:22]}",
                callback_data=f"historyticket:{row['id']}",
            )
        ])

    nav_buttons: list[InlineKeyboardButton] = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"history:{page - 1}"))
    if page < pages:
        nav_buttons.append(InlineKeyboardButton("Вперёд ➡️", callback_data=f"history:{page + 1}"))
    if nav_buttons:
        keyboard_rows.append(nav_buttons)

    return "\n".join(lines), InlineKeyboardMarkup(keyboard_rows) if keyboard_rows else None


def _message_history_line(row: sqlite3.Row) -> str:
    created = html.escape(str(row["created_at"]).replace("T", " ")[:19])
    direction = str(row["direction"])
    if direction == "user_to_admin":
        author = "👤 Клиент"
    elif direction == "admin_to_user":
        author = "🛠 Админ"
    else:
        author = html.escape(direction)

    content_type = str(row["content_type"] or "message")
    raw_text = row["text"]
    if raw_text:
        text = html.escape(str(raw_text))
        if content_type == "renew_request":
            text = f"🔄 {text}"
        elif content_type == "payment_done":
            text = f"✅ {text}"
        elif content_type == "payment_details":
            text = f"💳 {text}"
        elif content_type == "stars_invoice":
            text = f"⭐️ {text}"
        elif content_type == "stars_payment":
            text = f"⭐️✅ {text}"
        elif content_type and content_type not in {"text"}:
            text = f"[{html.escape(content_type)}] {text}"
    else:
        text = f"[{html.escape(content_type)}]"

    return f"<code>{created}</code> <b>{author}</b>: {text}"


def build_ticket_full_chat_chunks(ticket_id: int, chunk_limit: int = 3600) -> list[str] | None:
    ticket = get_ticket(ticket_id)
    if not ticket:
        return None

    with db() as conn:
        rows = conn.execute(
            """
            SELECT direction, content_type, text, created_at
            FROM messages
            WHERE ticket_id = ?
            ORDER BY id ASC
            """,
            (ticket_id,),
        ).fetchall()

    status = html.escape(str(ticket["status"]))
    closed = ticket["closed_at"] or "—"
    header = (
        f"📚 Полный чат по обращению <b>#{ticket_id}</b>\n"
        f"Статус: <code>{status}</code>\n"
        f"Пользователь: {user_display_from_row(ticket)}\n"
        f"Создано: <code>{html.escape(str(ticket['created_at']))}</code>\n"
        f"Закрыто: <code>{html.escape(str(closed))}</code>\n"
        f"Сообщений: <code>{len(rows)}</code>\n\n"
    )

    if not rows:
        return [header + "Сообщений в этом обращении нет."]

    chunks: list[str] = []
    current = header
    for row in rows:
        line = _message_history_line(row)
        # Telegram ограничивает длину сообщения. Если одно сообщение клиента очень длинное,
        # режем уже экранированный текст безопасными фрагментами.
        parts: list[str] = []
        while len(line) > chunk_limit:
            parts.append(line[:chunk_limit])
            line = line[chunk_limit:]
        parts.append(line)

        for part in parts:
            addition = part + "\n\n"
            if len(current) + len(addition) > chunk_limit:
                chunks.append(current.rstrip())
                current = ""
            current += addition

    if current.strip():
        chunks.append(current.rstrip())

    total = len(chunks)
    if total > 1:
        chunks = [f"{chunk}\n\n<i>Часть {idx}/{total}</i>" for idx, chunk in enumerate(chunks, start=1)]
    return chunks

def build_ticket_detail(ticket_id: int) -> tuple[str, InlineKeyboardMarkup] | None:
    ticket = get_ticket(ticket_id)
    if not ticket:
        return None

    with db() as conn:
        messages_count = conn.execute(
            "SELECT COUNT(*) AS cnt FROM messages WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()["cnt"]
        last_messages = conn.execute(
            """
            SELECT direction, content_type, text, created_at
            FROM messages
            WHERE ticket_id = ?
            ORDER BY id DESC
            LIMIT 8
            """,
            (ticket_id,),
        ).fetchall()

    renewal = get_renewal_request(ticket_id)
    subscription = get_subscription_request(ticket_id)
    request_text = ""
    keyboard = ticket_keyboard(ticket_id, int(ticket["user_id"]))
    if renewal:
        email = (renewal["xui_email"] or get_xui_link(int(ticket["user_id"])) or "не указан")
        payment_method = str(renewal["payment_method"] or "manual")
        method_label = payment_method_label(payment_method)
        if renewal['payment_confirmed_at']:
            payment_status = f"подтверждена: {html.escape(str(renewal['payment_confirmed_at']))}"
        elif payment_method == "stars" and renewal['payment_details_sent_at']:
            payment_status = "ожидается оплата Telegram Stars"
        else:
            payment_status = "не подтверждена клиентом"
        if payment_method == "stars" and renewal['payment_details_sent_at']:
            details_status = f"счёт Stars отправлен: {html.escape(str(renewal['payment_details_sent_at']))}, сумма: {renewal['stars_amount'] or '?'} ⭐"
        else:
            details_status = (
                f"отправлены: {html.escape(str(renewal['payment_details_sent_at']))}"
                if renewal['payment_details_sent_at']
                else "не отправлены"
            )
        request_text = (
            "\n<b>Заявка на продление:</b>\n"
            f"Статус: <code>{html.escape(str(renewal['status']))}</code>\n"
            f"Способ оплаты: <code>{html.escape(method_label)}</code>\n"
            f"Реквизиты/счёт: <code>{details_status}</code>\n"
            f"Оплата: <code>{payment_status}</code>\n"
            f"Срок из заявки: <code>{renewal['days']} дн.</code> ({html.escape(month_word(int(renewal['months'])))})\n"
            f"Email 3x-ui: <code>{html.escape(str(email))}</code>\n"
        )
        keyboard = renewal_ticket_keyboard(ticket_id, int(ticket["user_id"]))
    elif subscription:
        email = subscription["xui_email"] or generate_subscription_email(int(ticket["user_id"]), ticket["username"])
        payment_method = str(subscription["payment_method"] or "manual")
        method_label = payment_method_label(payment_method)
        if subscription['payment_confirmed_at']:
            payment_status = f"подтверждена: {html.escape(str(subscription['payment_confirmed_at']))}"
        elif payment_method == "stars" and subscription['payment_details_sent_at']:
            payment_status = "ожидается оплата Telegram Stars"
        else:
            payment_status = "не подтверждена клиентом"
        if payment_method == "stars" and subscription['payment_details_sent_at']:
            details_status = f"счёт Stars отправлен: {html.escape(str(subscription['payment_details_sent_at']))}, сумма: {subscription['stars_amount'] or '?'} ⭐"
        else:
            details_status = (
                f"отправлены: {html.escape(str(subscription['payment_details_sent_at']))}"
                if subscription['payment_details_sent_at']
                else "не отправлены"
            )
        request_text = (
            "\n<b>Заявка на оформление подписки:</b>\n"
            f"Статус: <code>{html.escape(str(subscription['status']))}</code>\n"
            f"Способ оплаты: <code>{html.escape(method_label)}</code>\n"
            f"Реквизиты/счёт: <code>{details_status}</code>\n"
            f"Оплата: <code>{payment_status}</code>\n"
            f"Срок из заявки: <code>{subscription['days']} дн.</code> ({html.escape(month_word(int(subscription['months'])))})\n"
            f"Email нового клиента: <code>{html.escape(str(email))}</code>\n"
        )
        keyboard = subscription_ticket_keyboard(ticket_id, int(ticket["user_id"]))

    text = (
        f"Обращение <b>#{ticket_id}</b>\n"
        f"Статус: <code>{html.escape(ticket['status'])}</code>\n"
        f"Пользователь: {user_display_from_row(ticket)}\n"
        f"Сообщений: <code>{messages_count}</code>\n"
        f"Создано: <code>{html.escape(ticket['created_at'])}</code>\n"
        f"{request_text}\n"
        "Последние сообщения:\n"
    )
    for msg in reversed(last_messages):
        direction = "Пользователь" if msg["direction"] == "user_to_admin" else "Админ"
        content = html.escape((msg["text"] or f"[{msg['content_type']}]")[:350])
        text += f"• <b>{direction}</b>: {content}\n"

    return text, keyboard


def count_renewal_requests() -> int:
    with db() as conn:
        return int(conn.execute("SELECT COUNT(*) AS c FROM renewal_requests").fetchone()["c"])


def list_renewal_requests(page: int = 1, per_page: int = RENEWAL_REQUESTS_PER_PAGE) -> tuple[list[sqlite3.Row], int, int, int]:
    total = count_renewal_requests()
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(max(page, 1), pages)
    offset = (page - 1) * per_page
    with db() as conn:
        rows = conn.execute(
            """
            SELECT rr.*, t.status AS ticket_status, u.username, u.first_name, u.last_name,
                   xl.xui_email AS linked_email
            FROM renewal_requests rr
            JOIN tickets t ON t.id = rr.ticket_id
            JOIN users u ON u.user_id = rr.telegram_user_id
            LEFT JOIN xui_links xl ON xl.telegram_user_id = rr.telegram_user_id
            ORDER BY CASE rr.status WHEN 'pending' THEN 0 ELSE 1 END, rr.ticket_id DESC
            LIMIT ? OFFSET ?
            """,
            (per_page, offset),
        ).fetchall()
    return list(rows), page, pages, total


def format_renewal_requests_page(rows: list[sqlite3.Row], page: int, pages: int, total: int) -> tuple[str, InlineKeyboardMarkup | None]:
    if not rows:
        return "Заявок на продление пока нет.", None

    lines = [
        f"Заявки на продление: {total}",
        f"Страница {page}/{pages}",
        "",
        "Нажмите на заявку, чтобы открыть её. Отправьте реквизиты или выставьте счёт Stars, затем после оплаты продлите подписку.",
    ]
    keyboard_rows: list[list[InlineKeyboardButton]] = []
    for row in rows:
        name = " ".join(filter(None, [row["first_name"], row["last_name"]])).strip() or "Без имени"
        status = str(row["status"])
        status_icon = "🟡" if status == "pending" else "✅" if status == "renewed" else "❌" if status == "rejected" else "⚪"
        email = row["linked_email"] or row["xui_email"] or "email не указан"
        method = str(row["payment_method"] or "manual")
        if method == "stars":
            payment_line = "Stars оплачены" if row['payment_confirmed_at'] else "ожидаем оплату Stars"
            details_line = f"счёт Stars отправлен ({row['stars_amount'] or '?'} ⭐)" if row['payment_details_sent_at'] else "нужно выставить счёт"
        else:
            payment_line = "оплата подтверждена" if row['payment_confirmed_at'] else "ожидаем оплату"
            details_line = "реквизиты отправлены" if row['payment_details_sent_at'] else "нужно отправить реквизиты"
        lines.append(
            f"{status_icon} #{row['ticket_id']} — {html.escape(name)} ({row['telegram_user_id']})\n"
            f"   Срок: <code>{row['days']} дн.</code>, email: <code>{html.escape(str(email))}</code>, статус: <code>{html.escape(status)}</code>\n"
            f"   Способ: <code>{html.escape(payment_method_label(method))}</code>; реквизиты: <code>{details_line}</code>; оплата: <code>{payment_line}</code>"
        )
        keyboard_rows.append([InlineKeyboardButton(f"{status_icon} Заявка #{row['ticket_id']} — {name[:20]}", callback_data=f"ticket:{row['ticket_id']}")])

    nav_buttons: list[InlineKeyboardButton] = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"renewals:{page - 1}"))
    if page < pages:
        nav_buttons.append(InlineKeyboardButton("Вперёд ➡️", callback_data=f"renewals:{page + 1}"))
    if nav_buttons:
        keyboard_rows.append(nav_buttons)

    return "\n".join(lines), InlineKeyboardMarkup(keyboard_rows) if keyboard_rows else None


def update_renewal_request_input(ticket_id: int, email: Optional[str], days: int) -> None:
    with db() as conn:
        if email:
            conn.execute(
                "UPDATE renewal_requests SET xui_email = ?, days = ? WHERE ticket_id = ?",
                (email, days, ticket_id),
            )
        else:
            conn.execute("UPDATE renewal_requests SET days = ? WHERE ticket_id = ?", (days, ticket_id))

def generate_subscription_email(user_id: int, username: Optional[str] = None) -> str:
    # Email для новой подписки должен быть уникальным даже для одного и того же Telegram-пользователя.
    # Поэтому после нижнего подчёркивания используем текущий timestamp в миллисекундах.
    # Пример: client_1783161234567 или ivan_1783161234567.
    base = slugify_email_part(username or "") if username else ""
    if not base or base in {"client", "user"}:
        base = XUI_NEW_CLIENT_EMAIL_PREFIX
    timestamp_ms = int(time.time() * 1000)
    return f"{base}_{timestamp_ms}"


def create_subscription_request(
    ticket_id: int,
    telegram_user_id: int,
    xui_email: Optional[str],
    months: int,
    days: int,
    payment_method: str = "manual",
) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO subscription_requests(
                ticket_id, telegram_user_id, xui_email, months, days, status, created_at, payment_method
            ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (ticket_id, telegram_user_id, xui_email, months, days, now_iso(), payment_method),
        )


def get_subscription_request(ticket_id: int) -> Optional[sqlite3.Row]:
    with db() as conn:
        return conn.execute(
            """
            SELECT sr.*, t.status AS ticket_status, u.username, u.first_name, u.last_name,
                   xl.xui_email AS linked_email
            FROM subscription_requests sr
            JOIN tickets t ON t.id = sr.ticket_id
            JOIN users u ON u.user_id = sr.telegram_user_id
            LEFT JOIN xui_links xl ON xl.telegram_user_id = sr.telegram_user_id
            WHERE sr.ticket_id = ?
            """,
            (ticket_id,),
        ).fetchone()


def mark_subscription_request_created(ticket_id: int, admin_id: int) -> None:
    with db() as conn:
        conn.execute(
            """
            UPDATE subscription_requests
            SET status = 'created', created_at_panel = ?, created_by = ?
            WHERE ticket_id = ?
            """,
            (now_iso(), admin_id, ticket_id),
        )


def mark_subscription_request_rejected(ticket_id: int, admin_id: int) -> None:
    with db() as conn:
        conn.execute(
            """
            UPDATE subscription_requests
            SET status = 'rejected', rejected_at = ?, rejected_by = ?
            WHERE ticket_id = ?
            """,
            (now_iso(), admin_id, ticket_id),
        )


def mark_subscription_payment_confirmed(ticket_id: int, user_id: int) -> None:
    with db() as conn:
        conn.execute(
            """
            UPDATE subscription_requests
            SET payment_confirmed_at = ?, payment_confirmed_by = ?
            WHERE ticket_id = ?
            """,
            (now_iso(), user_id, ticket_id),
        )


def mark_subscription_payment_details_sent(ticket_id: int, admin_id: int) -> None:
    with db() as conn:
        conn.execute(
            """
            UPDATE subscription_requests
            SET payment_details_sent_at = ?, payment_details_sent_by = ?
            WHERE ticket_id = ?
            """,
            (now_iso(), admin_id, ticket_id),
        )


def mark_subscription_stars_invoice_sent(ticket_id: int, admin_id: int, stars_amount: int) -> None:
    with db() as conn:
        conn.execute(
            """
            UPDATE subscription_requests
            SET payment_details_sent_at = COALESCE(payment_details_sent_at, ?),
                payment_details_sent_by = COALESCE(payment_details_sent_by, ?),
                stars_invoice_sent_at = ?, stars_invoice_sent_by = ?, stars_amount = ?
            WHERE ticket_id = ?
            """,
            (now_iso(), admin_id, now_iso(), admin_id, stars_amount, ticket_id),
        )


def mark_subscription_stars_paid(ticket_id: int, user_id: int, charge_id: str, stars_amount: int) -> None:
    with db() as conn:
        conn.execute(
            """
            UPDATE subscription_requests
            SET payment_confirmed_at = COALESCE(payment_confirmed_at, ?),
                payment_confirmed_by = COALESCE(payment_confirmed_by, ?),
                stars_charge_id = ?, stars_amount = COALESCE(stars_amount, ?)
            WHERE ticket_id = ?
            """,
            (now_iso(), user_id, charge_id, stars_amount, ticket_id),
        )


def update_subscription_request_input(ticket_id: int, email: Optional[str], days: int) -> None:
    with db() as conn:
        if email:
            conn.execute(
                "UPDATE subscription_requests SET xui_email = ?, days = ? WHERE ticket_id = ?",
                (email, days, ticket_id),
            )
        else:
            conn.execute("UPDATE subscription_requests SET days = ? WHERE ticket_id = ?", (days, ticket_id))


def count_subscription_requests() -> int:
    with db() as conn:
        return int(conn.execute("SELECT COUNT(*) AS c FROM subscription_requests").fetchone()["c"])


def list_subscription_requests(page: int = 1, per_page: int = RENEWAL_REQUESTS_PER_PAGE) -> tuple[list[sqlite3.Row], int, int, int]:
    total = count_subscription_requests()
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(max(page, 1), pages)
    offset = (page - 1) * per_page
    with db() as conn:
        rows = conn.execute(
            """
            SELECT sr.*, t.status AS ticket_status, u.username, u.first_name, u.last_name,
                   xl.xui_email AS linked_email
            FROM subscription_requests sr
            JOIN tickets t ON t.id = sr.ticket_id
            JOIN users u ON u.user_id = sr.telegram_user_id
            LEFT JOIN xui_links xl ON xl.telegram_user_id = sr.telegram_user_id
            ORDER BY CASE sr.status WHEN 'pending' THEN 0 ELSE 1 END, sr.ticket_id DESC
            LIMIT ? OFFSET ?
            """,
            (per_page, offset),
        ).fetchall()
    return list(rows), page, pages, total


def format_subscription_requests_page(rows: list[sqlite3.Row], page: int, pages: int, total: int) -> tuple[str, InlineKeyboardMarkup | None]:
    if not rows:
        return "Заявок на оформление подписки пока нет.", None
    lines = [
        f"Заявки на оформление подписки: {total}",
        f"Страница {page}/{pages}",
        "",
        "Нажмите на заявку, чтобы открыть её.",
    ]
    keyboard_rows: list[list[InlineKeyboardButton]] = []
    for row in rows:
        name = " ".join(filter(None, [row["first_name"], row["last_name"]])).strip() or "Без имени"
        status = str(row["status"])
        status_icon = "🟡" if status == "pending" else "✅" if status == "created" else "❌" if status == "rejected" else "⚪"
        method = str(row["payment_method"] or "manual")
        email = row["xui_email"] or "будет создан автоматически"
        lines.append(
            f"{status_icon} #{row['ticket_id']} — {html.escape(name)} ({row['telegram_user_id']})\n"
            f"   Срок: <code>{row['days']} дн.</code>, email: <code>{html.escape(str(email))}</code>, статус: <code>{html.escape(status)}</code>\n"
            f"   Способ: <code>{html.escape(payment_method_label(method))}</code>"
        )
        keyboard_rows.append([InlineKeyboardButton(f"{status_icon} Оформление #{row['ticket_id']} — {name[:20]}", callback_data=f"ticket:{row['ticket_id']}")])
    nav_buttons: list[InlineKeyboardButton] = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"subscriptions:{page - 1}"))
    if page < pages:
        nav_buttons.append(InlineKeyboardButton("Вперёд ➡️", callback_data=f"subscriptions:{page + 1}"))
    if nav_buttons:
        keyboard_rows.append(nav_buttons)
    return "\n".join(lines), InlineKeyboardMarkup(keyboard_rows) if keyboard_rows else None


def user_display_from_row(row: sqlite3.Row) -> str:
    name = " ".join(filter(None, [row["first_name"], row["last_name"]])).strip() or "Без имени"
    username = f"@{row['username']}" if row["username"] else "без username"
    return f"{html.escape(name)} ({html.escape(username)}), ID: <code>{row['user_id']}</code>"


def user_display_from_update(update: Update) -> str:
    user = update.effective_user
    if not user:
        return "Неизвестный пользователь"
    name = " ".join(filter(None, [user.first_name, user.last_name])).strip() or "Без имени"
    username = f"@{user.username}" if user.username else "без username"
    return f"{html.escape(name)} ({html.escape(username)}), ID: <code>{user.id}</code>"


def parse_months_from_text(text: str) -> Optional[int]:
    match = re.search(r"\d+", text or "")
    if not match:
        return None
    try:
        months = int(match.group(0))
    except ValueError:
        return None
    if months < 1 or months > XUI_MAX_RENEW_MONTHS:
        return None
    return months


def parse_stars_amount(text: str) -> Optional[int]:
    match = re.search(r"\d+", text or "")
    if not match:
        return None
    try:
        amount = int(match.group(0))
    except ValueError:
        return None
    if amount < STARS_MIN_AMOUNT or amount > STARS_MAX_AMOUNT:
        return None
    return amount


def make_stars_payload(ticket_id: int, stars_amount: int, kind: str = "renewal") -> str:
    kind = "subscription" if kind == "subscription" else "renewal"
    return f"{kind}:{ticket_id}:{stars_amount}"


def parse_stars_payload_kind(payload: str) -> tuple[str, Optional[int], Optional[int]]:
    parts = (payload or "").split(":")
    if len(parts) != 3 or parts[0] not in {"renewal", "subscription"}:
        return "", None, None
    try:
        return parts[0], int(parts[1]), int(parts[2])
    except ValueError:
        return "", None, None


def parse_stars_payload(payload: str) -> tuple[Optional[int], Optional[int]]:
    kind, ticket_id, stars_amount = parse_stars_payload_kind(payload)
    if kind != "renewal":
        return None, None
    return ticket_id, stars_amount


def month_word(months: int) -> str:
    last_two = months % 100
    last = months % 10
    if 11 <= last_two <= 14:
        word = "месяцев"
    elif last == 1:
        word = "месяц"
    elif 2 <= last <= 4:
        word = "месяца"
    else:
        word = "месяцев"
    return f"{months} {word}"




def payment_method_label(method: Optional[str]) -> str:
    return "⭐️ Tg Stars" if str(method or "manual").lower() == "stars" else "P2P(Перевод)"


def renewal_payment_choice_keyboard(months: int) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("💰 С баланса", callback_data=f"renewpay:balance:{months}")]]
    if STARS_PAYMENTS_ENABLED and STARS_PRICE_PER_MONTH > 0:
        rows.append([InlineKeyboardButton("⭐️ Tg Stars", callback_data=f"renewpay:stars:{months}")])
    return InlineKeyboardMarkup(rows)


def subscription_payment_choice_keyboard(months: int) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("💰 С баланса", callback_data=f"subpay:balance:{months}")]]
    if STARS_PAYMENTS_ENABLED and STARS_PRICE_PER_MONTH > 0:
        rows.append([InlineKeyboardButton("⭐️ Tg Stars", callback_data=f"subpay:stars:{months}")])
    return InlineKeyboardMarkup(rows)


def renewal_stars_amount_for_months(months: int) -> int:
    return int(months) * STARS_PRICE_PER_MONTH

def ticket_keyboard(ticket_id: int, user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✉️ Ответить", callback_data=f"reply:{ticket_id}")],
            [
                InlineKeyboardButton("✅ Закрыть", callback_data=f"close:{ticket_id}"),
                InlineKeyboardButton("🚫 Заблокировать", callback_data=f"ban:{user_id}"),
            ],
        ]
    )


def renewal_ticket_keyboard(ticket_id: int, user_id: int) -> InlineKeyboardMarkup:
    request = get_renewal_request(ticket_id)
    rows: list[list[InlineKeyboardButton]] = []

    if request and str(request["status"]) == "pending":
        method = str(request["payment_method"] or "manual")
        if method == "stars":
            if not request["payment_details_sent_at"]:
                if STARS_PAYMENTS_ENABLED:
                    rows.append([InlineKeyboardButton("⭐️ Отправить счёт Stars", callback_data=f"starsinvoice:{ticket_id}")])
                rows.append([InlineKeyboardButton("✉️ Написать клиенту", callback_data=f"reply:{ticket_id}")])
            elif not request["payment_confirmed_at"]:
                rows.append([InlineKeyboardButton("⏳ Ожидаем оплату Stars", callback_data=f"noop:{ticket_id}")])
                rows.append([InlineKeyboardButton("✉️ Написать клиенту", callback_data=f"reply:{ticket_id}")])
            else:
                rows.append([InlineKeyboardButton("✅ Продлить по заявке", callback_data=f"renewticket:{ticket_id}")])
                rows.append([InlineKeyboardButton("✍️ Ввести email/дни вручную", callback_data=f"renewsel:{ticket_id}")])
                rows.append([InlineKeyboardButton("📧 Продлить по email без привязки", callback_data=f"renewnolink:{ticket_id}")])
        else:
            if not request["payment_details_sent_at"]:
                rows.append([InlineKeyboardButton("💳 Отправить реквизиты", callback_data=f"paydetails:{ticket_id}")])
            else:
                rows.append([InlineKeyboardButton("✅ Продлить по заявке", callback_data=f"renewticket:{ticket_id}")])
                rows.append([InlineKeyboardButton("✍️ Ввести email/дни вручную", callback_data=f"renewsel:{ticket_id}")])
                rows.append([InlineKeyboardButton("📧 Продлить по email без привязки", callback_data=f"renewnolink:{ticket_id}")])
    else:
        rows.append([InlineKeyboardButton("✉️ Ответить", callback_data=f"reply:{ticket_id}")])

    if request and not request["payment_details_sent_at"]:
        rows.append([InlineKeyboardButton("✉️ Обычный ответ", callback_data=f"reply:{ticket_id}")])
    elif not request:
        rows.append([InlineKeyboardButton("✉️ Ответить", callback_data=f"reply:{ticket_id}")])

    if request and str(request["status"]) == "pending":
        rows.append([InlineKeyboardButton("❌ Отклонить заявку", callback_data=f"rejectrenew:{ticket_id}")])

    rows.append([
        InlineKeyboardButton("✅ Закрыть", callback_data=f"close:{ticket_id}"),
        InlineKeyboardButton("🚫 Заблокировать", callback_data=f"ban:{user_id}"),
    ])
    return InlineKeyboardMarkup(rows)


def subscription_ticket_keyboard(ticket_id: int, user_id: int) -> InlineKeyboardMarkup:
    request = get_subscription_request(ticket_id)
    rows: list[list[InlineKeyboardButton]] = []

    if request and str(request["status"]) == "pending":
        method = str(request["payment_method"] or "manual")
        if method == "stars":
            if not request["payment_details_sent_at"]:
                if STARS_PAYMENTS_ENABLED:
                    rows.append([InlineKeyboardButton("⭐️ Отправить счёт Stars", callback_data=f"substarsinvoice:{ticket_id}")])
                rows.append([InlineKeyboardButton("✉️ Написать клиенту", callback_data=f"reply:{ticket_id}")])
            elif not request["payment_confirmed_at"]:
                rows.append([InlineKeyboardButton("⏳ Ожидаем оплату Stars", callback_data=f"noop:{ticket_id}")])
                rows.append([InlineKeyboardButton("✉️ Написать клиенту", callback_data=f"reply:{ticket_id}")])
            else:
                rows.append([InlineKeyboardButton("✅ Создать клиента", callback_data=f"subcreate:{ticket_id}")])
                rows.append([InlineKeyboardButton("✍️ Ввести email/дни вручную", callback_data=f"subcreatesel:{ticket_id}")])
        else:
            if not request["payment_details_sent_at"]:
                rows.append([InlineKeyboardButton("💳 Отправить реквизиты", callback_data=f"subpaydetails:{ticket_id}")])
            else:
                rows.append([InlineKeyboardButton("✅ Создать клиента", callback_data=f"subcreate:{ticket_id}")])
                rows.append([InlineKeyboardButton("✍️ Ввести email/дни вручную", callback_data=f"subcreatesel:{ticket_id}")])
    elif request and str(request["status"]) == "created":
        rows.append([InlineKeyboardButton("✅ Клиент уже создан", callback_data=f"noop:{ticket_id}")])
        rows.append([InlineKeyboardButton("✉️ Ответить", callback_data=f"reply:{ticket_id}")])
    else:
        rows.append([InlineKeyboardButton("✉️ Ответить", callback_data=f"reply:{ticket_id}")])

    if request and str(request["status"]) == "pending":
        rows.append([InlineKeyboardButton("❌ Отклонить заявку", callback_data=f"rejectsub:{ticket_id}")])

    rows.append([
        InlineKeyboardButton("✅ Закрыть", callback_data=f"close:{ticket_id}"),
        InlineKeyboardButton("🚫 Заблокировать", callback_data=f"ban:{user_id}"),
    ])
    return InlineKeyboardMarkup(rows)


def client_payment_done_keyboard(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Платёж выполнен", callback_data=f"paymentdone:{ticket_id}")]]
    )


def client_subscription_payment_done_keyboard(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Платёж выполнен", callback_data=f"subpaymentdone:{ticket_id}")]]
    )


def should_show_payment_done_button(ticket_id: int) -> bool:
    request = get_renewal_request(ticket_id)
    return bool(
        request
        and str(request["status"]) == "pending"
        and request["payment_details_sent_at"]
        and not request["payment_confirmed_at"]
    )


def should_show_subscription_payment_done_button(ticket_id: int) -> bool:
    request = get_subscription_request(ticket_id)
    return bool(
        request
        and str(request["status"]) == "pending"
        and request["payment_details_sent_at"]
        and not request["payment_confirmed_at"]
    )


CLIENT_BUTTON_SUBSCRIBE = "🆕 Оформить подписку"
CLIENT_BUTTON_RENEW = "🔄 Продлить подписку"
CLIENT_BUTTON_BALANCE = "💰 Баланс"
CLIENT_BUTTON_REFERRALS = "👥 Рефералы"
CLIENT_BUTTON_TICKET = "🆘 Обращение"

ADMIN_BUTTON_TICKETS = "📩 Обращения"
ADMIN_BUTTON_RENEW_REQUESTS = "🧾 Заявки на продление"
ADMIN_BUTTON_SUBSCRIPTION_REQUESTS = "🆕 Заявки на оформление"
ADMIN_BUTTON_NEW_CLIENT_MESSAGES = "📝 Сообщения новым клиентам"
ADMIN_BUTTON_HISTORY = "📚 История обращений"
ADMIN_BUTTON_CLIENTS = "👥 Клиенты 3x-ui"
ADMIN_BUTTON_INBOUNDS = "📡 Inbounds"
ADMIN_BUTTON_RENEW = "🔄 Продлить"
ADMIN_BUTTON_XUI_STATUS = "🔌 Статус 3x-ui"
ADMIN_BUTTON_STATS = "📊 Статистика"
ADMIN_BUTTON_USERS = "👤 Пользователи"
ADMIN_BUTTON_BROADCAST = "📣 Рассылка"
ADMIN_BUTTON_REFRESH_MENUS = "🔄 Обновить меню"
ADMIN_BUTTON_HELP = "ℹ️ Помощь"


def client_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[CLIENT_BUTTON_SUBSCRIBE, CLIENT_BUTTON_RENEW], [CLIENT_BUTTON_BALANCE, CLIENT_BUTTON_REFERRALS], [CLIENT_BUTTON_TICKET]],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )


def balance_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐ Пополнить Stars", callback_data="balance:stars")],
        [InlineKeyboardButton("💳 Пополнить P2P", callback_data="balance:p2p")],
        [InlineKeyboardButton("₿ Пополнить криптовалютой", callback_data="balance:crypto")],
    ])


def balance_topup_confirm_keyboard(topup_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Подтвердить и зачислить", callback_data=f"topupconfirm:{topup_id}")]])


def admin_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [ADMIN_BUTTON_TICKETS, ADMIN_BUTTON_RENEW_REQUESTS],
            [ADMIN_BUTTON_SUBSCRIPTION_REQUESTS, ADMIN_BUTTON_NEW_CLIENT_MESSAGES],
            [ADMIN_BUTTON_HISTORY],
            [ADMIN_BUTTON_CLIENTS, ADMIN_BUTTON_INBOUNDS],
            [ADMIN_BUTTON_RENEW],
            [ADMIN_BUTTON_XUI_STATUS, ADMIN_BUTTON_STATS],
            [ADMIN_BUTTON_USERS, ADMIN_BUTTON_BROADCAST],
            [ADMIN_BUTTON_REFRESH_MENUS, ADMIN_BUTTON_HELP],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие или используйте команду",
    )


def message_type(message) -> str:
    if message.text:
        return "text"
    if message.photo:
        return "photo"
    if message.document:
        return "document"
    if message.video:
        return "video"
    if message.voice:
        return "voice"
    if message.audio:
        return "audio"
    if message.sticker:
        return "sticker"
    if message.location:
        return "location"
    if message.contact:
        return "contact"
    if message.animation:
        return "animation"
    return "other"


def require_admin(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id if update.effective_user else None
        if not is_admin(user_id):
            if update.message:
                await update.message.reply_text("Эта команда доступна только администратору.")
            return
        return await func(update, context)

    return wrapper


def require_super_admin(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id if update.effective_user else None
        if not is_super_admin(user_id):
            if update.message:
                await update.message.reply_text("Эта команда доступна только главному администратору.")
            return
        return await func(update, context)

    return wrapper


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not update.message:
        return

    upsert_user(user)

    # deep-link формата https://t.me/<bot>?start=ref_<telegram_id>
    if context.args and context.args[0].startswith("ref_"):
        try:
            referrer_id = int(context.args[0][4:])
        except ValueError:
            referrer_id = 0
        if referrer_id and get_user(referrer_id) and set_referrer(user.id, referrer_id):
            await update.message.reply_text("✅ Вы зарегистрированы по реферальной ссылке. Вашему пригласившему будет начисляться 25% с ваших пополнений.")

    if is_admin(user.id):
        await update.message.reply_text(
            "Панель техподдержки.\n\n"
            "Снизу есть меню с быстрыми кнопками для обращений, заявок на продление/оформление, клиентов 3x-ui, продления, статистики и рассылки.\n\n"
            "Основные команды:\n"
            "/tickets — открытые обращения\n"
            "/history — история обращений\n"
            "/renewals — заявки на продление\n"
            "/subscriptions — заявки на оформление подписки\n/submessages — сообщения, которые отправляются новым клиентам\n"
            "/reply ID текст — ответить пользователю\n"
            "/close ID — закрыть обращение\n"
            "/stats — статистика\n"
            "/renewticket TICKET_ID — продлить по заявке\n"
            "/renew EMAIL DAYS [TICKET_ID] — продлить подписку 3x-ui\n"
            "/subinfo EMAIL — информация о клиенте 3x-ui\n"
            "/clients — список всех клиентов 3x-ui\n"
            "/inbounds — список inbound\n"
            "/updatemenus — отправить новое меню всем пользователям\n"
            "/help — полный список команд",
            reply_markup=admin_main_keyboard(),
        )
        return

    if is_banned(user.id):
        await update.message.reply_text("Вы заблокированы и не можете отправлять обращения.")
        return

    await update.message.reply_text(
        "Здравствуйте! Выберите действие в меню ниже.",
        reply_markup=client_main_keyboard(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id if update.effective_user else None
    if not update.message:
        return

    if is_admin(user_id):
        text = (
            "Команды администратора:\n\n"
            "/tickets — список открытых обращений с кнопками\n"
            "/history — история всех обращений и полный чат\n"
            "/renewals — заявки на продление с кнопками\n"
            "/subscriptions — заявки на оформление подписки с кнопками\n/submessages — настроить сообщения новым клиентам\n"
            "/ticket ID — информация по обращению\n"
            "/reply ID текст — ответ пользователю\n"
            "/close ID — закрыть обращение\n"
            "/ban USER_ID — заблокировать пользователя\n"
            "/unban USER_ID — разблокировать пользователя\n"
            "/users — последние пользователи\n"
            "/stats — статистика\n"
            "/broadcast текст — рассылка всем незаблокированным пользователям\n"
            "/updatemenus — отправить новое меню всем пользователям и админам\n"
            "/admins — список админов\n"
            "/addadmin USER_ID — добавить админа, только главный админ\n"
            "/deladmin USER_ID — удалить админа, только главный админ\n"
            "/cancel — отменить режим ответа\n"
            "/xui_status — проверить подключение к 3x-ui\n"
            "/clients [PAGE] — список всех клиентов 3x-ui\n"
            "/inbounds — список inbound\n"
            "/subinfo EMAIL — информация о клиенте 3x-ui\n"
            "/renewticket TICKET_ID [DAYS] — продлить по заявке и уведомить клиента\n"
            "/renew EMAIL DAYS [TICKET_ID] — продлить подписку клиента через Clients API\n"
            "/linksub USER_ID EMAIL — привязать Telegram-пользователя к клиенту 3x-ui\n"
            "/renewuser USER_ID DAYS [TICKET_ID] — продлить привязанную подписку\n"
            "/unlinksub USER_ID — удалить привязку\n"
            "/id — узнать свой Telegram ID"
        )
    else:
        text = "Выберите действие в меню ниже."
    reply_markup = admin_main_keyboard() if is_admin(user_id) else client_main_keyboard()
    await update.message.reply_text(text, reply_markup=reply_markup)


async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user and update.message:
        await update.message.reply_text(f"Ваш Telegram ID: {update.effective_user.id}")


async def notify_admins_about_user_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    ticket_id: int,
) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    header = (
        f"📩 Новое сообщение по обращению <b>#{ticket_id}</b>\n"
        f"Пользователь: {user_display_from_update(update)}\n"
        f"Тип: <code>{html.escape(message_type(message))}</code>"
    )
    if message.text:
        header += f"\n\nТекст:\n{html.escape(message.text)}"
    elif message.caption:
        header += f"\n\nПодпись:\n{html.escape(message.caption)}"

    for admin_id in get_admin_ids():
        try:
            sent_header = await context.bot.send_message(
                chat_id=admin_id,
                text=header,
                parse_mode=ParseMode.HTML,
                reply_markup=ticket_keyboard(ticket_id, user.id),
            )
            save_admin_message_map(admin_id, sent_header.message_id, ticket_id, user.id)

            if not message.text:
                copied = await context.bot.copy_message(
                    chat_id=admin_id,
                    from_chat_id=user.id,
                    message_id=message.message_id,
                )
                save_admin_message_map(admin_id, copied.message_id, ticket_id, user.id)
        except TelegramError as exc:
            logger.warning("Не удалось отправить обращение админу %s: %s", admin_id, exc)


async def notify_admins_about_renew_request(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    ticket_id: int,
    xui_email: Optional[str],
    months: int,
    payment_method: str = "manual",
) -> None:
    user = update.effective_user
    if not user:
        return

    renew_days = months * XUI_DAYS_PER_MONTH
    method = "stars" if str(payment_method).lower() == "stars" else "manual"
    linked_line = (
        f"\nПривязанный клиент 3x-ui: <code>{html.escape(xui_email)}</code>"
        if xui_email
        else "\nПривязанный клиент 3x-ui: <code>не найден</code>"
    )
    method_line = f"\nСпособ оплаты: <b>{html.escape(payment_method_label(method))}</b>"
    if method == "stars":
        if xui_email:
            command_hint = (
                "\n\nКлиент выбрал оплату <b>⭐️ Tg Stars</b>. "
                "После успешной оплаты бот автоматически продлит подписку, потому что клиент найден по tgId/email."
            )
        else:
            command_hint = (
                "\n\nКлиент выбрал оплату <b>⭐️ Tg Stars</b>. "
                "После оплаты бот не сможет продлить автоматически, пока не будет указан email. "
                "Откройте заявку и используйте ручной ввод email/дней."
            )
    else:
        command_hint = (
            "\n\nКлиент выбрал <b>P2P(Перевод)</b>. Сначала нажмите <b>💳 Отправить реквизиты</b> "
            "и отправьте клиенту сумму/реквизиты. Только после этого в заявке появится кнопка <b>✅ Продлить по заявке</b>."
            if xui_email
            else (
                "\n\nКлиент выбрал <b>P2P(Перевод)</b>. Бот не нашёл email подписки автоматически. "
                "Сначала отправьте реквизиты, а после оплаты используйте ручной ввод email/дней "
                f"или команду <code>/linksub {user.id} EMAIL</code>."
            )
        )
    header = (
        f"🔄 Новая заявка на продление подписки. Обращение <b>#{ticket_id}</b>\n"
        f"Пользователь: {user_display_from_update(update)}\n"
        f"Желаемый срок: <b>{month_word(months)}</b> (~{renew_days} дн.)"
        f"{method_line}"
        f"{linked_line}"
        f"{command_hint}\n\n"
        "Админ видит выбранный клиентом способ оплаты в карточке заявки."
    )

    for admin_id in get_admin_ids():
        try:
            sent_header = await context.bot.send_message(
                chat_id=admin_id,
                text=header,
                parse_mode=ParseMode.HTML,
                reply_markup=renewal_ticket_keyboard(ticket_id, user.id),
            )
            save_admin_message_map(admin_id, sent_header.message_id, ticket_id, user.id)
        except TelegramError as exc:
            logger.warning("Не удалось отправить заявку на продление админу %s: %s", admin_id, exc)


async def notify_admins_about_payment_done(
    context: ContextTypes.DEFAULT_TYPE,
    ticket_id: int,
    user_id: int,
) -> None:
    ticket = get_ticket(ticket_id)
    if not ticket:
        return

    request = get_renewal_request(ticket_id)
    payment_time = request["payment_confirmed_at"] if request else now_iso()
    name = " ".join(filter(None, [ticket["first_name"], ticket["last_name"]])).strip() or "Без имени"
    username = f"@{ticket['username']}" if ticket["username"] else "без username"
    method = str(request["payment_method"] or "manual") if request else "manual"
    if method == "stars":
        tail = (
            "Оплата Telegram Stars поступила, но автоматическое продление не было выполнено. "
            "Если бот не нашёл клиента по tgId, откройте заявку и укажите email вручную."
        )
        title = "⭐️ Клиент оплатил Telegram Stars"
    else:
        tail = "Проверьте поступление денег. Если оплата пришла, нажмите <b>✅ Продлить по заявке</b>."
        title = "✅ Клиент подтвердил оплату"
    text = (
        f"{title} по обращению <b>#{ticket_id}</b>\n"
        f"Пользователь: <b>{html.escape(name)}</b> ({html.escape(username)}), ID: <code>{user_id}</code>\n"
        f"Способ оплаты: <b>{html.escape(payment_method_label(method))}</b>\n"
        f"Время подтверждения: <code>{html.escape(str(payment_time))}</code>\n\n"
        f"{tail}"
    )

    for admin_id in get_admin_ids():
        try:
            sent = await context.bot.send_message(
                chat_id=admin_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=renewal_ticket_keyboard(ticket_id, user_id),
            )
            save_admin_message_map(admin_id, sent.message_id, ticket_id, user_id)
        except TelegramError as exc:
            logger.warning("Не удалось отправить подтверждение оплаты админу %s: %s", admin_id, exc)


async def send_stars_invoice_to_client(
    context: ContextTypes.DEFAULT_TYPE,
    ticket_id: int,
    user_id: int,
    stars_amount: int,
    sent_by_admin_id: int = 0,
) -> bool:
    request = get_renewal_request(ticket_id)
    if not request:
        return False
    if not STARS_PAYMENTS_ENABLED or STARS_PRICE_PER_MONTH <= 0:
        return False
    if stars_amount < STARS_MIN_AMOUNT or stars_amount > STARS_MAX_AMOUNT:
        return False

    payload = make_stars_payload(ticket_id, stars_amount)
    description = (
        f"{STARS_INVOICE_DESCRIPTION}\n"
        f"Обращение #{ticket_id}. Срок: {request['days']} дн. ({month_word(int(request['months']))})."
    )
    try:
        await context.bot.send_invoice(
            chat_id=user_id,
            title=STARS_INVOICE_TITLE,
            description=description[:255],
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label=f"Продление #{ticket_id}", amount=stars_amount)],
        )
    except TelegramError as exc:
        logger.warning("Не удалось отправить Stars invoice пользователю %s: %s", user_id, exc)
        return False

    mark_renewal_stars_invoice_sent(ticket_id, sent_by_admin_id, stars_amount)
    log_message(
        ticket_id=ticket_id,
        direction="admin_to_user",
        telegram_message_id=None,
        content_type="stars_invoice",
        text=f"Выставлен счёт Telegram Stars на {stars_amount} ⭐",
        admin_id=sent_by_admin_id,
    )
    return True


async def process_client_renewal_payment_choice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    months: int,
    payment_method: str,
) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return

    method = str(payment_method).lower()
    xui_email, _ = await resolve_xui_email_for_user(user.id)
    renew_days = months * XUI_DAYS_PER_MONTH
    if method == "balance":
        cost = months * BALANCE_PRICE_PER_MONTH
        if not xui_email:
            await message.reply_text(
                "Подписка не привязана к вашему Telegram ID. Администратор должен привязать её командой /linksub, после этого продление с баланса станет доступно.",
                reply_markup=client_main_keyboard(),
            )
            return
        if balance_of(user.id) < cost:
            await message.reply_text(f"Недостаточно средств: нужно {cost} ⭐, доступно {balance_of(user.id)} ⭐. Пополните баланс в разделе «💰 Баланс».", reply_markup=client_main_keyboard())
            return
        try:
            async with XuiClient() as api:
                result = await api.renew_client(xui_email, renew_days)
        except XuiApiError as exc:
            await message.reply_text(f"Не удалось продлить подписку: {exc}. Средства не списаны.", reply_markup=client_main_keyboard())
            return
        if not spend_balance(user.id, cost, f"Продление на {months} мес."):
            await message.reply_text("Баланс изменился, средств уже недостаточно. Проверьте его и повторите попытку.", reply_markup=client_main_keyboard())
            return
        await message.reply_text(f"✅ Подписка продлена на {renew_days} дн. Списано {cost} ⭐. Остаток: {balance_of(user.id)} ⭐.", reply_markup=client_main_keyboard())
        return

    method = "stars" if method == "stars" else "manual"
    ticket_id = create_ticket(user.id)
    create_renewal_request(ticket_id, user.id, xui_email, months, renew_days, payment_method=method)
    log_message(
        ticket_id=ticket_id,
        direction="user_to_admin",
        telegram_message_id=message.message_id,
        content_type="renew_request",
        text=f"Клиент запросил продление подписки на {month_word(months)}. Способ оплаты: {payment_method_label(method)}",
        user_id=user.id,
    )

    if method == "stars":
        stars_amount = renewal_stars_amount_for_months(months)
        invoice_sent = await send_stars_invoice_to_client(context, ticket_id, user.id, stars_amount, sent_by_admin_id=0)
        await notify_admins_about_renew_request(update, context, ticket_id, xui_email, months, payment_method="stars")
        if invoice_sent:
            await message.reply_text(
                f"Заявка на продление создана. Номер обращения: #{ticket_id}.\n\n"
                f"Способ оплаты: ⭐️ Tg Stars.\n"
                f"Сумма: {stars_amount} ⭐.\n\n"
                "Я отправил счёт Telegram Stars. После оплаты бот попробует продлить подписку автоматически.",
                reply_markup=client_main_keyboard(),
            )
        else:
            await message.reply_text(
                f"Заявка на продление создана. Номер обращения: #{ticket_id}.\n\n"
                "Не удалось автоматически отправить счёт Telegram Stars. Администратор увидит заявку и поможет с оплатой.",
                reply_markup=client_main_keyboard(),
            )
        return

    await notify_admins_about_renew_request(update, context, ticket_id, xui_email, months, payment_method="manual")
    await message.reply_text(
        f"Заявка на продление подписки на {month_word(months)} передана в техподдержку.\n"
        f"Номер обращения: #{ticket_id}.\n\n"
        "Способ оплаты: P2P(Перевод). Администратор пришлёт реквизиты и сумму в этом чате.",
        reply_markup=client_main_keyboard(),
    )


async def notify_admins_about_auto_stars_renewal(
    context: ContextTypes.DEFAULT_TYPE,
    ticket_id: int,
    user_id: int,
    result: dict[str, Any],
    days: int,
) -> None:
    text = (
        f"⭐️ Оплата Telegram Stars получена по обращению <b>#{ticket_id}</b>.\n"
        "Подписка была продлена автоматически.\n\n"
        f"{format_renew_success_admin_text(result, days, ticket_id)}"
    )
    for admin_id in get_admin_ids():
        try:
            sent = await context.bot.send_message(
                chat_id=admin_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=renewal_ticket_keyboard(ticket_id, user_id),
            )
            save_admin_message_map(admin_id, sent.message_id, ticket_id, user_id)
        except TelegramError as exc:
            logger.warning("Не удалось отправить уведомление об автопродлении админу %s: %s", admin_id, exc)


async def notify_admins_about_stars_auto_error(
    context: ContextTypes.DEFAULT_TYPE,
    ticket_id: int,
    user_id: int,
    error_text: str,
) -> None:
    text = (
        f"⭐️ Оплата Telegram Stars получена по обращению <b>#{ticket_id}</b>, "
        "но автоматическое продление не выполнено.\n\n"
        f"Причина: <code>{html.escape(error_text)}</code>\n\n"
        "Откройте заявку и укажите email вручную либо продлите по email без привязки."
    )
    for admin_id in get_admin_ids():
        try:
            sent = await context.bot.send_message(
                chat_id=admin_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=renewal_ticket_keyboard(ticket_id, user_id),
            )
            save_admin_message_map(admin_id, sent.message_id, ticket_id, user_id)
        except TelegramError as exc:
            logger.warning("Не удалось отправить ошибку автопродления админу %s: %s", admin_id, exc)


async def try_auto_renew_after_stars_payment(
    context: ContextTypes.DEFAULT_TYPE,
    message,
    ticket_id: int,
    user_id: int,
) -> bool:
    request = get_renewal_request(ticket_id)
    if not request or str(request["status"]) != "pending":
        return False

    days = int(request["days"])
    xui_email = (get_xui_link(user_id) or request["xui_email"] or "").strip()
    try:
        async with XuiClient() as api:
            if not xui_email:
                xui_email = (await api.find_client_email_by_tg_id(user_id) or "").strip()
                if xui_email:
                    set_xui_link(user_id, xui_email)
                    update_renewal_request_input(ticket_id, xui_email, days)

            if not xui_email:
                await notify_admins_about_stars_auto_error(
                    context,
                    ticket_id,
                    user_id,
                    f"не найден клиент 3x-ui по tgId {user_id}",
                )
                await message.reply_text(
                    f"✅ Оплата Telegram Stars получена по обращению #{ticket_id}.\n\n"
                    "Я не смог автоматически найти вашу подписку по Telegram ID. "
                    "Администратор укажет email и завершит продление.",
                    reply_markup=client_main_keyboard(),
                )
                return False

            result = await api.renew_client(xui_email, days)
    except XuiApiError as exc:
        await notify_admins_about_stars_auto_error(context, ticket_id, user_id, str(exc))
        await message.reply_text(
            f"✅ Оплата Telegram Stars получена по обращению #{ticket_id}.\n\n"
            "Автоматическое продление не удалось. Администратор уже получил уведомление и проверит заявку.",
            reply_markup=client_main_keyboard(),
        )
        return False

    mark_renewal_request_renewed(ticket_id, 0)
    close_ticket(ticket_id)
    await notify_user_about_successful_renewal(context, ticket_id, user_id, result, days)
    await notify_admins_about_auto_stars_renewal(context, ticket_id, user_id, result, days)
    return True

async def notify_admins_about_subscription_request(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    ticket_id: int,
    xui_email: str,
    months: int,
    payment_method: str = "manual",
) -> None:
    user = update.effective_user
    if not user:
        return
    days = months * XUI_DAYS_PER_MONTH
    method = "stars" if str(payment_method).lower() == "stars" else "manual"
    if method == "stars":
        hint = (
            "Клиент выбрал <b>⭐️ Tg Stars</b>. После успешной оплаты бот попробует автоматически "
            "создать клиента в 3x-ui и привязать tgId."
        )
    else:
        hint = (
            "Клиент выбрал <b>P2P(Перевод)</b>. Сначала нажмите <b>💳 Отправить реквизиты</b>. "
            "После подтверждения оплаты создайте клиента кнопкой <b>✅ Создать клиента</b>."
        )
    text = (
        f"🆕 Новая заявка на оформление подписки. Обращение <b>#{ticket_id}</b>\n"
        f"Пользователь: {user_display_from_update(update)}\n"
        f"Срок: <b>{month_word(months)}</b> (~{days} дн.)\n"
        f"Способ оплаты: <b>{html.escape(payment_method_label(method))}</b>\n"
        f"Email нового клиента: <code>{html.escape(xui_email)}</code>\n\n"
        f"{hint}"
    )
    for admin_id in get_admin_ids():
        try:
            sent = await context.bot.send_message(
                chat_id=admin_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=subscription_ticket_keyboard(ticket_id, user.id),
            )
            save_admin_message_map(admin_id, sent.message_id, ticket_id, user.id)
        except TelegramError as exc:
            logger.warning("Не удалось отправить заявку на оформление админу %s: %s", admin_id, exc)


async def send_subscription_stars_invoice_to_client(
    context: ContextTypes.DEFAULT_TYPE,
    ticket_id: int,
    user_id: int,
    stars_amount: int,
    sent_by_admin_id: int = 0,
) -> bool:
    request = get_subscription_request(ticket_id)
    if not request:
        return False
    if not STARS_PAYMENTS_ENABLED or STARS_PRICE_PER_MONTH <= 0:
        return False
    if stars_amount < STARS_MIN_AMOUNT or stars_amount > STARS_MAX_AMOUNT:
        return False
    payload = make_stars_payload(ticket_id, stars_amount, kind="subscription")
    description = (
        f"Оформление подписки через Telegram Stars\n"
        f"Обращение #{ticket_id}. Срок: {request['days']} дн. ({month_word(int(request['months']))})."
    )
    try:
        await context.bot.send_invoice(
            chat_id=user_id,
            title="Оформление подписки",
            description=description[:255],
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label=f"Оформление #{ticket_id}", amount=stars_amount)],
        )
    except TelegramError as exc:
        logger.warning("Не удалось отправить Stars invoice на оформление пользователю %s: %s", user_id, exc)
        return False
    mark_subscription_stars_invoice_sent(ticket_id, sent_by_admin_id, stars_amount)
    log_message(
        ticket_id=ticket_id,
        direction="admin_to_user",
        telegram_message_id=None,
        content_type="subscription_stars_invoice",
        text=f"Выставлен счёт Telegram Stars на оформление: {stars_amount} ⭐",
        admin_id=sent_by_admin_id,
    )
    return True


async def process_client_subscription_payment_choice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    months: int,
    payment_method: str,
) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return
    method = str(payment_method).lower()
    days = months * XUI_DAYS_PER_MONTH
    email = generate_subscription_email(user.id, user.username)
    if method == "balance":
        cost = months * BALANCE_PRICE_PER_MONTH
        if balance_of(user.id) < cost:
            await message.reply_text(f"Недостаточно средств: нужно {cost} ⭐, доступно {balance_of(user.id)} ⭐. Пополните баланс в разделе «💰 Баланс».", reply_markup=client_main_keyboard())
            return
        try:
            async with XuiClient() as api:
                result = await api.add_client(email, days, user.id)
        except XuiApiError as exc:
            await message.reply_text(f"Не удалось создать подписку: {exc}. Средства не списаны.", reply_markup=client_main_keyboard())
            return
        if not spend_balance(user.id, cost, f"Новая подписка на {months} мес."):
            await message.reply_text("Баланс изменился, средств уже недостаточно. Созданную подписку проверьте у администратора.", reply_markup=client_main_keyboard())
            return
        set_xui_link(user.id, str(result.get("email") or email))
        link = build_subscription_link(result)
        link_text = f"\nСсылка подписки: {link}" if link else ""
        await message.reply_text(f"✅ Подписка создана на {days} дн. Списано {cost} ⭐. Остаток: {balance_of(user.id)} ⭐.{link_text}", reply_markup=client_main_keyboard())
        return

    method = "stars" if method == "stars" else "manual"
    ticket_id = create_ticket(user.id)
    create_subscription_request(ticket_id, user.id, email, months, days, payment_method=method)
    log_message(
        ticket_id=ticket_id,
        direction="user_to_admin",
        telegram_message_id=message.message_id,
        content_type="subscription_request",
        text=f"Клиент запросил оформление подписки на {month_word(months)}. Способ оплаты: {payment_method_label(method)}. Email: {email}",
        user_id=user.id,
    )

    await notify_admins_about_subscription_request(update, context, ticket_id, email, months, payment_method=method)

    if method == "stars":
        stars_amount = renewal_stars_amount_for_months(months)
        invoice_sent = await send_subscription_stars_invoice_to_client(context, ticket_id, user.id, stars_amount, sent_by_admin_id=0)
        if invoice_sent:
            await message.reply_text(
                f"Заявка на оформление подписки создана. Номер обращения: #{ticket_id}.\n\n"
                f"Способ оплаты: ⭐️ Tg Stars.\n"
                f"Сумма: {stars_amount} ⭐.\n\n"
                "Я отправил счёт Telegram Stars. После оплаты бот попробует автоматически оформить подписку.",
                reply_markup=client_main_keyboard(),
            )
        else:
            await message.reply_text(
                f"Заявка на оформление подписки создана. Номер обращения: #{ticket_id}.\n\n"
                "Не удалось автоматически отправить счёт Telegram Stars. Администратор увидит заявку и поможет с оплатой.",
                reply_markup=client_main_keyboard(),
            )
        return

    await message.reply_text(
        f"Заявка на оформление подписки на {month_word(months)} передана в техподдержку.\n"
        f"Номер обращения: #{ticket_id}.\n\n"
        "Способ оплаты: P2P(Перевод). Администратор пришлёт реквизиты и сумму в этом чате.",
        reply_markup=client_main_keyboard(),
    )


async def notify_user_about_successful_subscription(
    context: ContextTypes.DEFAULT_TYPE,
    ticket_id: int,
    user_id: int,
    result: dict[str, Any],
    days: int,
) -> dict[str, Any]:
    """Уведомить клиента после создания подписки и вернуть подробный отчёт.

    Раньше функция возвращала только True/False, из-за чего админ не видел,
    какая именно часть не сработала: ссылка, инструкции или само сообщение.
    Теперь отчёт сохраняется и используется в админском уведомлении.
    """
    new_expiry = format_xui_expiry(safe_int(result.get("new_expiry_ms")))
    subscription_link = build_subscription_link(result)
    report: dict[str, Any] = {
        "main_message_sent": False,
        "link_sent": False,
        "welcome_messages_sent": 0,
        "errors": [],
        "subscription_link": subscription_link,
    }

    text = (
        f"✅ Подписка успешно оформлена по обращению #{ticket_id}.\n\n"
        f"Логин/email: {result.get('email')}\n"
        f"Добавлено дней: {days}.\n"
        f"Дата окончания: {new_expiry}"
    )

    try:
        await context.bot.send_message(chat_id=user_id, text=text, reply_markup=client_main_keyboard())
        report["main_message_sent"] = True
    except TelegramError as exc:
        err = f"Не удалось отправить клиенту основное сообщение: {exc}"
        logger.warning("%s", err)
        report["errors"].append(err)
        result["_notify_report"] = report
        return report

    if subscription_link:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"🔗 Ваша ссылка подписки:\n{subscription_link}",
                reply_markup=client_main_keyboard(),
            )
            report["link_sent"] = True
            log_message(
                ticket_id=ticket_id,
                direction="admin_to_user",
                telegram_message_id=None,
                content_type="subscription_link_sent",
                text=f"Клиенту отправлена ссылка подписки: {subscription_link}",
                admin_id=0,
            )
        except TelegramError as exc:
            err = f"Не удалось отправить клиенту ссылку подписки: {exc}"
            logger.warning("%s", err)
            report["errors"].append(err)
    else:
        err = (
            "Ссылка подписки не сформирована: в ответе 3x-ui нет subId/subscriptionLink "
            "или неправильно настроен XUI_PUBLIC_SUBSCRIPTION_BASE_URL / XUI_SUBSCRIPTION_LINK_TEMPLATE."
        )
        logger.warning("%s result=%s", err, result)
        report["errors"].append(err)
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    "Подписка создана, но ссылку подписки не удалось сформировать автоматически. "
                    "Администратор отправит её вручную."
                ),
                reply_markup=client_main_keyboard(),
            )
        except TelegramError as exc:
            report["errors"].append(f"Не удалось отправить клиенту предупреждение о ссылке: {exc}")

    try:
        sent_count = await send_subscription_welcome_messages(context, user_id)
        report["welcome_messages_sent"] = sent_count
        if sent_count:
            log_message(
                ticket_id=ticket_id,
                direction="admin_to_user",
                telegram_message_id=None,
                content_type="subscription_welcome_messages_sent",
                text=f"Клиенту отправлены заранее заданные сообщения: {sent_count} шт.",
                admin_id=0,
            )
    except Exception as exc:
        err = f"Не удалось отправить заранее заданные сообщения: {exc}"
        logger.exception("%s", err)
        report["errors"].append(err)

    result["_notify_report"] = report
    return report

async def notify_admins_about_subscription_created(
    context: ContextTypes.DEFAULT_TYPE,
    ticket_id: int,
    user_id: int,
    result: dict[str, Any],
    days: int,
) -> None:
    subscription_link = build_subscription_link(result)
    report = result.get("_notify_report") or {}
    errors = report.get("errors") or []
    welcome_count = int(report.get("welcome_messages_sent") or 0)

    text = (
        f"✅ Клиент успешно создан по обращению <b>#{ticket_id}</b>.\n\n"
        f"Клиент: <code>{html.escape(str(result.get('email')))}</code>\n"
        f"Срок: <code>{days} дн.</code>\n"
        f"Дата окончания: <code>{html.escape(format_xui_expiry(safe_int(result.get('new_expiry_ms'))))}</code>\n"
        f"tgId: <code>{html.escape(str(result.get('tgId') or user_id))}</code>\n"
        f"Inbounds: <code>{html.escape(', '.join(map(str, result.get('inbound_ids') or [])))}</code>\n\n"
        f"Сообщение клиенту: {'✅ отправлено' if report.get('main_message_sent') else '⚠️ не отправлено'}\n"
        f"Ссылка клиенту: {'✅ отправлена' if report.get('link_sent') else '⚠️ не отправлена'}\n"
        f"Заданные сообщения: <code>{welcome_count}</code> шт."
    )
    if subscription_link:
        text += f"\nСсылка: <code>{html.escape(subscription_link)}</code>"
    if errors:
        text += "\n\n⚠️ Детали:\n" + "\n".join(f"• {html.escape(str(e))}" for e in errors[:5])
    text += "\n\nКнопка «✅ Создать клиента» скрыта, обращение закрыто."

    for admin_id in get_admin_ids():
        try:
            sent = await context.bot.send_message(
                chat_id=admin_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=admin_main_keyboard(),
            )
            save_admin_message_map(admin_id, sent.message_id, ticket_id, user_id)
        except TelegramError as exc:
            logger.warning("Не удалось уведомить админа %s об оформлении: %s", admin_id, exc)

async def finalize_subscription_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE, ticket_id: int) -> None:
    source_message = update.effective_message
    admin = update.effective_user
    request = get_subscription_request(ticket_id)
    ticket = get_ticket(ticket_id)
    if not source_message or not admin:
        return
    if not request or not ticket:
        await source_message.reply_text("Заявка на оформление не найдена.", reply_markup=admin_main_keyboard())
        return
    if str(request["status"]) == "created":
        await source_message.reply_text("Клиент по этой заявке уже создан.", reply_markup=admin_main_keyboard())
        return
    if str(request["status"]) == "rejected":
        await source_message.reply_text("Эта заявка отклонена.", reply_markup=admin_main_keyboard())
        return

    method = str(request["payment_method"] or "manual")
    if not request["payment_details_sent_at"]:
        await source_message.reply_text("Сначала отправьте реквизиты или счёт клиенту.", reply_markup=subscription_ticket_keyboard(ticket_id, int(request["telegram_user_id"])))
        return
    if method == "stars" and not request["payment_confirmed_at"]:
        await source_message.reply_text("Сначала дождитесь успешной оплаты Telegram Stars.", reply_markup=subscription_ticket_keyboard(ticket_id, int(request["telegram_user_id"])))
        return

    days = int(request["days"])
    email = str(request["xui_email"] or "").strip()
    if not email:
        email = generate_subscription_email(int(request["telegram_user_id"]), request["username"])
        update_subscription_request_input(ticket_id, email, days)

    try:
        async with XuiClient() as api:
            result = await api.add_client(email, days, int(request["telegram_user_id"]))
    except XuiApiError as exc:
        await source_message.reply_text(
            f"Ошибка создания клиента: {exc}\n\n"
            "Можно нажать «✍️ Ввести email/дни вручную» и указать другой email.",
            reply_markup=subscription_ticket_keyboard(ticket_id, int(request["telegram_user_id"])),
        )
        return
    except Exception as exc:
        logger.exception("Неожиданная ошибка создания клиента по обращению #%s", ticket_id)
        await source_message.reply_text(
            f"Неожиданная ошибка создания клиента: {exc}\n\n"
            "Проверьте логи systemd: sudo journalctl -u telegram-support-bot -n 80",
            reply_markup=subscription_ticket_keyboard(ticket_id, int(request["telegram_user_id"])),
        )
        return

    set_xui_link(int(request["telegram_user_id"]), str(result.get("email") or email))
    mark_subscription_request_created(ticket_id, admin.id)
    close_ticket(ticket_id)
    log_message(
        ticket_id=ticket_id,
        direction="admin_to_user",
        telegram_message_id=None,
        content_type="subscription_created",
        text=f"Подписка оформлена. Email: {result.get('email')}, дней: {days}",
        admin_id=admin.id,
    )
    notify_report = await notify_user_about_successful_subscription(context, ticket_id, int(request["telegram_user_id"]), result, days)
    await notify_admins_about_subscription_created(context, ticket_id, int(request["telegram_user_id"]), result, days)

    link_status = "✅ ссылка отправлена" if notify_report.get("link_sent") else "⚠️ ссылка не отправлена"
    welcome_count = int(notify_report.get("welcome_messages_sent") or 0)
    errors = notify_report.get("errors") or []
    admin_text = (
        f"✅ Клиент успешно создан по обращению <b>#{ticket_id}</b>.\n\n"
        f"Email: <code>{html.escape(str(result.get('email') or email))}</code>\n"
        f"Срок: <code>{days} дн.</code>\n"
        f"Клиенту: {link_status}, заданные сообщения: <code>{welcome_count}</code> шт.\n"
        f"Обращение закрыто. Кнопка создания клиента скрыта."
    )
    if errors:
        admin_text += "\n\n⚠️ Детали:\n" + "\n".join(f"• {html.escape(str(e))}" for e in errors[:5])

    if update.callback_query and update.callback_query.message:
        try:
            await update.callback_query.edit_message_text(
                admin_text,
                parse_mode=ParseMode.HTML,
                reply_markup=None,
            )
        except TelegramError:
            try:
                await update.callback_query.edit_message_reply_markup(reply_markup=None)
            except TelegramError:
                pass
    await source_message.reply_text(admin_text, parse_mode=ParseMode.HTML, reply_markup=admin_main_keyboard())


async def try_auto_create_subscription_after_stars_payment(
    context: ContextTypes.DEFAULT_TYPE,
    message,
    ticket_id: int,
    user_id: int,
) -> bool:
    request = get_subscription_request(ticket_id)
    if not request or str(request["status"]) != "pending":
        return False
    days = int(request["days"])
    email = str(request["xui_email"] or "").strip() or generate_subscription_email(user_id, request["username"])
    update_subscription_request_input(ticket_id, email, days)
    try:
        async with XuiClient() as api:
            result = await api.add_client(email, days, user_id)
    except XuiApiError as exc:
        for admin_id in get_admin_ids():
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        f"⭐️ Оплата Telegram Stars получена по заявке на оформление <b>#{ticket_id}</b>, "
                        f"но клиент не был создан.\n\nОшибка: <code>{html.escape(str(exc))}</code>\n\n"
                        "Откройте заявку и укажите другой email вручную."
                    ),
                    parse_mode=ParseMode.HTML,
                    reply_markup=subscription_ticket_keyboard(ticket_id, user_id),
                )
            except TelegramError:
                pass
        await message.reply_text(
            f"✅ Оплата Telegram Stars получена по обращению #{ticket_id}.\n\n"
            "Автоматическое оформление не удалось. Администратор уже получил уведомление и завершит оформление вручную.",
            reply_markup=client_main_keyboard(),
        )
        return False

    set_xui_link(user_id, str(result.get("email") or email))
    mark_subscription_request_created(ticket_id, 0)
    close_ticket(ticket_id)
    await notify_user_about_successful_subscription(context, ticket_id, user_id, result, days)
    await notify_admins_about_subscription_created(context, ticket_id, user_id, result, days)
    return True


async def handle_client_menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    message = update.effective_message
    if not user or not message or not message.text:
        return False

    text = message.text.strip()

    if text == CLIENT_BUTTON_BALANCE:
        context.user_data.pop("balance_topup_method", None)
        await message.reply_text(
            f"💰 Ваш баланс: <b>{balance_of(user.id)} ⭐</b>\n\n"
            "Пополнение P2P и криптовалютой проверяет администратор. После зачисления выберите срок подписки и оплатите её с баланса.",
            parse_mode=ParseMode.HTML,
            reply_markup=balance_keyboard(),
        )
        return True

    if text == CLIENT_BUTTON_REFERRALS:
        count, earned = referral_stats(user.id)
        bot_username = (await context.bot.get_me()).username
        link = f"https://t.me/{bot_username}?start=ref_{user.id}" if bot_username else f"ref_{user.id}"
        await message.reply_text(
            f"👥 Реферальная программа\n\nВаша ссылка:\n<code>{html.escape(link)}</code>\n\n"
            f"Рефералов: <b>{count}</b>\nЗаработано: <b>{earned} ⭐</b>\n\n"
            f"Вы получаете {REFERRAL_PERCENT}% с каждого подтверждённого пополнения реферала. Вознаграждение автоматически поступает на баланс.",
            parse_mode=ParseMode.HTML,
            reply_markup=client_main_keyboard(),
        )
        return True

    if text == CLIENT_BUTTON_TICKET:
        context.user_data.pop("client_waiting_renew_months", None)
        context.user_data.pop("client_waiting_subscribe_months", None)
        context.user_data["client_waiting_ticket_text"] = True
        await message.reply_text(
            "Опишите проблему одним или несколькими сообщениями. "
            "Я передам их в техподдержку, а ответ придёт в этот чат.",
            reply_markup=client_main_keyboard(),
        )
        return True

    if text == CLIENT_BUTTON_RENEW:
        context.user_data["client_waiting_renew_months"] = True
        context.user_data.pop("client_waiting_subscribe_months", None)
        context.user_data.pop("client_waiting_ticket_text", None)
        await message.reply_text(
            "На сколько месяцев хотите продлить подписку?\n\n"
            "Напишите число, например: 1, 3, 6 или 12.",
            reply_markup=client_main_keyboard(),
        )
        return True

    if text == CLIENT_BUTTON_SUBSCRIBE:
        context.user_data["client_waiting_subscribe_months"] = True
        context.user_data.pop("client_waiting_renew_months", None)
        context.user_data.pop("client_waiting_ticket_text", None)
        await message.reply_text(
            "На сколько месяцев хотите оформить подписку?\n\n"
            "Напишите число, например: 1, 3, 6 или 12.",
            reply_markup=client_main_keyboard(),
        )
        return True

    if context.user_data.get("client_waiting_renew_months"):
        months = parse_months_from_text(text)
        if months is None:
            await message.reply_text(
                f"Укажите количество месяцев числом от 1 до {XUI_MAX_RENEW_MONTHS}.\n"
                "Например: 1, 3, 6 или 12.",
                reply_markup=client_main_keyboard(),
            )
            return True

        context.user_data.pop("client_waiting_renew_months", None)
        context.user_data["client_renew_months"] = months
        await message.reply_text(
            f"Вы выбрали продление на {month_word(months)}.\n\n"
            "Выберите способ оплаты:",
            reply_markup=renewal_payment_choice_keyboard(months),
        )
        return True

    if context.user_data.get("balance_topup_method"):
        try:
            amount = int(text)
        except ValueError:
            amount = 0
        if amount < STARS_MIN_AMOUNT or amount > STARS_MAX_AMOUNT:
            await message.reply_text(f"Введите целое число от {STARS_MIN_AMOUNT} до {STARS_MAX_AMOUNT}.")
            return True
        method = str(context.user_data.pop("balance_topup_method"))
        topup_id = create_balance_topup(user.id, amount, method)
        if method == "stars":
            try:
                await context.bot.send_invoice(chat_id=user.id, title="Пополнение баланса", description=f"Зачисление {amount} ⭐ на внутренний баланс", payload=f"balance:{topup_id}:{amount}", provider_token="", currency="XTR", prices=[LabeledPrice(label="Пополнение баланса", amount=amount)])
            except TelegramError:
                await message.reply_text("Не удалось отправить счёт Stars. Повторите попытку позднее.")
            return True
        details = CRYPTO_PAYMENT_DETAILS if method == "crypto" else "Администратор пришлёт реквизиты P2P в этом чате."
        if method == "p2p":
            for admin_id in get_admin_ids():
                try:
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=f"💳 Новая P2P-заявка на пополнение #{topup_id}: пользователь {user.id}, сумма {amount} ⭐. Отправьте ему реквизиты через обычный ответ.",
                    )
                except TelegramError:
                    pass
        await message.reply_text(
            f"Заявка на пополнение #{topup_id}: <b>{amount} ⭐</b>.\n\n{html.escape(details or 'Криптореквизиты ещё не настроены. Администратор свяжется с вами.')}\n\nПосле перевода нажмите кнопку ниже.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Я перевёл", callback_data=f"topuppaid:{topup_id}")]]),
        )
        return True

    if context.user_data.get("client_waiting_subscribe_months"):
        months = parse_months_from_text(text)
        if months is None:
            await message.reply_text(
                f"Укажите количество месяцев числом от 1 до {XUI_MAX_RENEW_MONTHS}.\n"
                "Например: 1, 3, 6 или 12.",
                reply_markup=client_main_keyboard(),
            )
            return True

        context.user_data.pop("client_waiting_subscribe_months", None)
        context.user_data["client_subscribe_months"] = months
        await message.reply_text(
            f"Вы выбрали оформление подписки на {month_word(months)}.\n\n"
            "Выберите способ оплаты:",
            reply_markup=subscription_payment_choice_keyboard(months),
        )
        return True

    return False


async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return

    upsert_user(user)

    if is_banned(user.id):
        await message.reply_text("Вы заблокированы и не можете отправлять обращения.")
        return

    if await handle_client_menu_button(update, context):
        return

    context.user_data.pop("client_waiting_ticket_text", None)

    ticket_id = get_or_create_open_ticket(user.id)
    log_message(
        ticket_id=ticket_id,
        direction="user_to_admin",
        telegram_message_id=message.message_id,
        content_type=message_type(message),
        text=message.text or message.caption,
        user_id=user.id,
    )
    await notify_admins_about_user_message(update, context, ticket_id)
    await message.reply_text(
        f"Ваше сообщение передано в техподдержку. Номер обращения: #{ticket_id}.",
        reply_markup=client_main_keyboard(),
    )


async def send_admin_reply(
    context: ContextTypes.DEFAULT_TYPE,
    ticket_id: int,
    admin_id: int,
    admin_message,
    text_override: Optional[str] = None,
) -> bool:
    ticket = get_ticket(ticket_id)
    if not ticket:
        return False

    user_id = int(ticket["user_id"])
    if ticket["status"] == "closed":
        with db() as conn:
            conn.execute(
                "UPDATE tickets SET status = 'open', closed_at = NULL, updated_at = ? WHERE id = ?",
                (now_iso(), ticket_id),
            )

    try:
        sending_payment_details = context.user_data.get("admin_sending_payment_details_ticket_id") == ticket_id
        sending_subscription_payment_details = context.user_data.get("admin_sending_subscription_payment_details_ticket_id") == ticket_id
        payment_reply_markup = None
        if sending_subscription_payment_details or should_show_subscription_payment_done_button(ticket_id):
            payment_reply_markup = client_subscription_payment_done_keyboard(ticket_id)
        elif sending_payment_details or should_show_payment_done_button(ticket_id):
            payment_reply_markup = client_payment_done_keyboard(ticket_id)
        if text_override is not None:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"💬 Ответ поддержки по обращению #{ticket_id}:\n\n{text_override}",
                reply_markup=payment_reply_markup,
            )
            content_type = "text"
            text_to_log = text_override
            telegram_message_id = None
        else:
            if admin_message.text:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"💬 Ответ поддержки по обращению #{ticket_id}:\n\n{admin_message.text}",
                    reply_markup=payment_reply_markup,
                )
            else:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"💬 Ответ поддержки по обращению #{ticket_id}:",
                    reply_markup=payment_reply_markup,
                )
                await context.bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=admin_id,
                    message_id=admin_message.message_id,
                )
            content_type = message_type(admin_message)
            text_to_log = admin_message.text or admin_message.caption
            telegram_message_id = admin_message.message_id

        if sending_subscription_payment_details:
            mark_subscription_payment_details_sent(ticket_id, admin_id)
            content_type = "subscription_payment_details"
        elif sending_payment_details:
            mark_renewal_payment_details_sent(ticket_id, admin_id)
            content_type = "payment_details"

        log_message(
            ticket_id=ticket_id,
            direction="admin_to_user",
            telegram_message_id=telegram_message_id,
            content_type=content_type,
            text=text_to_log,
            admin_id=admin_id,
        )
        return True
    except TelegramError as exc:
        logger.warning("Не удалось отправить ответ пользователю %s: %s", user_id, exc)
        return False


def parse_admin_renew_input(text: str, has_email: bool) -> tuple[Optional[str], Optional[int], Optional[str]]:
    parts = (text or "").strip().split()
    if not parts:
        return None, None, "Пустой ввод."

    if has_email:
        # Если у заявки уже есть привязка, основной сценарий — админ вводит только количество дней.
        # Но для удобства можно переуказать email: EMAIL DAYS.
        if len(parts) == 1:
            try:
                days = int(parts[0])
            except ValueError:
                return None, None, "Укажите количество дней числом, например: 30"
            return None, days, None
        if len(parts) >= 2:
            email = parts[0].strip()
            try:
                days = int(parts[1])
            except ValueError:
                return None, None, "После email нужно указать количество дней числом, например: client@example.com 30"
            return email, days, None

    if len(parts) < 2:
        return None, None, "У этой заявки нет привязанного email. Укажите email и количество дней, например: ivan 30"
    email = parts[0].strip()
    try:
        days = int(parts[1])
    except ValueError:
        return None, None, "Количество дней должно быть числом, например: ivan 30"
    return email, days, None


async def handle_admin_renewal_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    message = update.effective_message
    if not message or not message.text:
        return False

    ticket_id = context.user_data.get("admin_waiting_renew_ticket_id")
    no_link_ticket_id = context.user_data.get("admin_waiting_renew_no_link_ticket_id")
    bind_email_to_user = True
    if no_link_ticket_id:
        ticket_id = no_link_ticket_id
        bind_email_to_user = False
    if not ticket_id:
        return False

    request = get_renewal_request(int(ticket_id))
    if not request:
        context.user_data.pop("admin_waiting_renew_ticket_id", None)
        context.user_data.pop("admin_waiting_renew_no_link_ticket_id", None)
        await message.reply_text("Заявка на продление больше не найдена.", reply_markup=admin_main_keyboard())
        return True

    user_id = int(request["telegram_user_id"])
    existing_email = (get_xui_link(user_id) or request["xui_email"] or "").strip()
    email_override, days, error = parse_admin_renew_input(message.text, bool(existing_email) and bind_email_to_user)
    if error:
        await message.reply_text(error + "\n\nДля отмены используйте /cancel.", reply_markup=admin_main_keyboard())
        return True
    if not days or days < 1:
        await message.reply_text("Количество дней должно быть положительным числом.", reply_markup=admin_main_keyboard())
        return True

    context.user_data.pop("admin_waiting_renew_ticket_id", None)
    context.user_data.pop("admin_waiting_renew_no_link_ticket_id", None)
    await finalize_renewal_ticket(
        update,
        context,
        int(ticket_id),
        days_override=days,
        email_override=email_override,
        bind_email_to_user=bind_email_to_user,
    )
    return True


async def ask_admin_for_renewal_input(
    source_message,
    context: ContextTypes.DEFAULT_TYPE,
    ticket_id: int,
    *,
    bind_email_to_user: bool = True,
) -> None:
    request = get_renewal_request(ticket_id)
    if not request:
        await source_message.reply_text("Заявка на продление не найдена.")
        return

    if str(request["status"]) == "renewed":
        await source_message.reply_text(
            f"Заявка #{ticket_id} уже продлена. Дата продления: {request['renewed_at'] or 'неизвестно'}."
        )
        return

    user_id = int(request["telegram_user_id"])
    existing_email = (get_xui_link(user_id) or request["xui_email"] or "").strip()
    context.user_data.pop("admin_waiting_renew_ticket_id", None)
    context.user_data.pop("admin_waiting_renew_no_link_ticket_id", None)
    if bind_email_to_user:
        context.user_data["admin_waiting_renew_ticket_id"] = ticket_id
    else:
        context.user_data["admin_waiting_renew_no_link_ticket_id"] = ticket_id

    name = " ".join(filter(None, [request["first_name"], request["last_name"]])).strip() or "Без имени"
    base = (
        f"Выбрана заявка на продление <b>#{ticket_id}</b>.\n"
        f"Пользователь: <b>{html.escape(name)}</b>, ID: <code>{user_id}</code>\n"
        f"Срок из заявки клиента: <code>{request['days']} дн.</code> ({html.escape(month_word(int(request['months'])))})\n"
    )

    if not bind_email_to_user:
        text = (
            base
            + "Режим: <b>продление по email без привязки</b>.\n"
            + "Email не будет сохранён ни в базе бота, ни в поле tgId панели 3x-ui.\n\n"
            + "Введите email клиента и количество дней через пробел, например:\n"
            + f"<code>client_email {request['days']}</code>"
        )
    elif existing_email:
        text = (
            base
            + f"Привязанный email 3x-ui: <code>{html.escape(existing_email)}</code>\n\n"
            "Введите количество дней для продления, например:\n"
            f"<code>{request['days']}</code>\n\n"
            "Либо переукажите email и дни. При переуказании бот привяжет этот email к Telegram ID клиента локально и в панели 3x-ui:\n"
            f"<code>{html.escape(existing_email)} {request['days']}</code>"
        )
    else:
        text = (
            base
            + "Привязанный email 3x-ui: <code>не найден</code>\n\n"
            "Введите email клиента и количество дней через пробел. После успешной проверки бот запишет tgId клиента в панель 3x-ui и сохранит локальную привязку:\n"
            f"<code>client_email {request['days']}</code>"
        )
    await source_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=admin_main_keyboard())


async def handle_admin_subscription_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    message = update.effective_message
    if not message or not message.text:
        return False
    ticket_id = context.user_data.get("admin_waiting_subscription_ticket_id")
    if not ticket_id:
        return False
    request = get_subscription_request(int(ticket_id))
    if not request:
        context.user_data.pop("admin_waiting_subscription_ticket_id", None)
        await message.reply_text("Заявка на оформление больше не найдена.", reply_markup=admin_main_keyboard())
        return True

    parts = message.text.strip().split()
    if len(parts) == 1 and (request["xui_email"] or "").strip():
        email = str(request["xui_email"]).strip()
        try:
            days = int(parts[0])
        except ValueError:
            await message.reply_text("Укажите дни числом, например: 30", reply_markup=admin_main_keyboard())
            return True
    elif len(parts) >= 2:
        email = parts[0].strip()
        try:
            days = int(parts[1])
        except ValueError:
            await message.reply_text("Количество дней должно быть числом. Пример: client_email 30", reply_markup=admin_main_keyboard())
            return True
    else:
        await message.reply_text("Введите email и дни. Пример: client_email 30", reply_markup=admin_main_keyboard())
        return True

    if days < 1:
        await message.reply_text("Количество дней должно быть больше 0.", reply_markup=admin_main_keyboard())
        return True

    context.user_data.pop("admin_waiting_subscription_ticket_id", None)
    update_subscription_request_input(int(ticket_id), email, days)
    await finalize_subscription_ticket(update, context, int(ticket_id))
    return True


async def ask_admin_for_subscription_input(source_message, context: ContextTypes.DEFAULT_TYPE, ticket_id: int) -> None:
    request = get_subscription_request(ticket_id)
    if not request:
        await source_message.reply_text("Заявка на оформление не найдена.")
        return
    if str(request["status"]) == "created":
        await source_message.reply_text(f"По заявке #{ticket_id} клиент уже создан.")
        return
    context.user_data["admin_waiting_subscription_ticket_id"] = ticket_id
    email = str(request["xui_email"] or "").strip()
    name = " ".join(filter(None, [request["first_name"], request["last_name"]])).strip() or "Без имени"
    base = (
        f"Выбрана заявка на оформление <b>#{ticket_id}</b>.\n"
        f"Пользователь: <b>{html.escape(name)}</b>, ID: <code>{request['telegram_user_id']}</code>\n"
        f"Срок из заявки: <code>{request['days']} дн.</code> ({html.escape(month_word(int(request['months'])))})\n"
    )
    if email:
        text = (
            base
            + f"Email нового клиента: <code>{html.escape(email)}</code>\n\n"
            + "Введите количество дней, например:\n"
            + f"<code>{request['days']}</code>\n\n"
            + "Либо переукажите email и дни:\n"
            + f"<code>{html.escape(email)} {request['days']}</code>"
        )
    else:
        text = base + "Введите email нового клиента и количество дней:\n" + f"<code>client_email {request['days']}</code>"
    await source_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=admin_main_keyboard())


async def send_stars_invoice_for_renewal(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    ticket_id: int,
    stars_amount: int,
) -> None:
    message = update.effective_message
    admin = update.effective_user
    if not message or not admin:
        return

    if not STARS_PAYMENTS_ENABLED:
        await message.reply_text("Оплата Telegram Stars отключена в .env: STARS_PAYMENTS_ENABLED=false")
        return

    request = get_renewal_request(ticket_id)
    ticket = get_ticket(ticket_id)
    if not request or not ticket:
        await message.reply_text("Заявка на продление не найдена.", reply_markup=admin_main_keyboard())
        return
    if str(request["status"]) == "renewed":
        await message.reply_text("Эта заявка уже продлена.", reply_markup=admin_main_keyboard())
        return
    if request["payment_details_sent_at"]:
        await message.reply_text("По этой заявке уже отправлен способ оплаты.", reply_markup=renewal_ticket_keyboard(ticket_id, int(request["telegram_user_id"])))
        return

    user_id = int(request["telegram_user_id"])
    payload = make_stars_payload(ticket_id, stars_amount)
    description = (
        f"{STARS_INVOICE_DESCRIPTION}\n"
        f"Обращение #{ticket_id}. Срок: {request['days']} дн. ({month_word(int(request['months']))})."
    )
    try:
        await context.bot.send_invoice(
            chat_id=user_id,
            title=STARS_INVOICE_TITLE,
            description=description[:255],
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label=f"Продление #{ticket_id}", amount=stars_amount)],
        )
    except TelegramError as exc:
        logger.warning("Не удалось отправить Stars invoice пользователю %s: %s", user_id, exc)
        await message.reply_text(f"Не удалось отправить счёт Telegram Stars: {exc}", reply_markup=admin_main_keyboard())
        return

    mark_renewal_stars_invoice_sent(ticket_id, admin.id, stars_amount)
    log_message(
        ticket_id=ticket_id,
        direction="admin_to_user",
        telegram_message_id=None,
        content_type="stars_invoice",
        text=f"Выставлен счёт Telegram Stars на {stars_amount} ⭐",
        admin_id=admin.id,
    )
    await message.reply_text(
        f"Счёт Telegram Stars на {stars_amount} ⭐ отправлен клиенту по обращению #{ticket_id}.\n"
        "Кнопки продления появятся после успешной оплаты.",
        reply_markup=renewal_ticket_keyboard(ticket_id, user_id),
    )


async def handle_admin_stars_invoice_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    message = update.effective_message
    if not message or not message.text:
        return False

    ticket_id = context.user_data.get("admin_waiting_stars_invoice_ticket_id")
    if not ticket_id:
        return False

    stars_amount = parse_stars_amount(message.text)
    if not stars_amount:
        await message.reply_text(
            f"Укажите сумму в Telegram Stars числом от {STARS_MIN_AMOUNT} до {STARS_MAX_AMOUNT}.\n"
            "Например: 250\n\nДля отмены используйте /cancel.",
            reply_markup=admin_main_keyboard(),
        )
        return True

    context.user_data.pop("admin_waiting_stars_invoice_ticket_id", None)
    await send_stars_invoice_for_renewal(update, context, int(ticket_id), stars_amount)
    return True


async def handle_admin_menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    message = update.effective_message
    if not message or not message.text:
        return False

    text = message.text.strip()

    if text == ADMIN_BUTTON_TICKETS:
        await tickets_command(update, context)
        return True

    if text == ADMIN_BUTTON_HISTORY:
        await history_command(update, context)
        return True

    if text == ADMIN_BUTTON_NEW_CLIENT_MESSAGES:
        await subscription_messages_command(update, context)
        return True

    if text == ADMIN_BUTTON_RENEW_REQUESTS:
        await renewal_requests_command(update, context)
        return True

    if text == ADMIN_BUTTON_SUBSCRIPTION_REQUESTS:
        await subscription_requests_command(update, context)
        return True

    if text == ADMIN_BUTTON_CLIENTS:
        await clients_command(update, context)
        return True

    if text == ADMIN_BUTTON_INBOUNDS:
        await inbounds_command(update, context)
        return True

    if text == ADMIN_BUTTON_XUI_STATUS:
        await xui_status_command(update, context)
        return True

    if text == ADMIN_BUTTON_STATS:
        await stats_command(update, context)
        return True

    if text == ADMIN_BUTTON_USERS:
        await users_command(update, context)
        return True

    if text == ADMIN_BUTTON_BROADCAST:
        await message.reply_text(
            "Для рассылки используйте команду:\n"
            "/broadcast текст сообщения\n\n"
            "Пример: /broadcast Сегодня будут технические работы с 22:00 до 23:00",
            reply_markup=admin_main_keyboard(),
        )
        return True

    if text == ADMIN_BUTTON_REFRESH_MENUS:
        await update_menus_command(update, context)
        return True

    if text == ADMIN_BUTTON_HELP:
        await help_command(update, context)
        return True

    if text == ADMIN_BUTTON_RENEW:
        await message.reply_text(
            "Для обычной заявки на продление используйте номер обращения:\n"
            "/renewticket TICKET_ID — продлить по заявке и уведомить клиента\n\n"
            "Также доступны ручные команды:\n"
            f"/renew EMAIL {XUI_DEFAULT_RENEW_DAYS} [TICKET_ID] — продлить по email\n"
            f"/renewuser USER_ID {XUI_DEFAULT_RENEW_DAYS} [TICKET_ID] — продлить привязанного пользователя\n\n"
            "Продление выполняется через /panel/api/clients/bulkAdjust — добавляются дни к подписке.",
            reply_markup=admin_main_keyboard(),
        )
        return True

    return False


async def handle_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admin = update.effective_user
    message = update.effective_message
    if not admin or not message:
        return

    if await handle_admin_subscription_message_recording(update, context):
        return

    if await handle_admin_menu_button(update, context):
        return

    if await handle_admin_stars_invoice_input(update, context):
        return

    if await handle_admin_renewal_input(update, context):
        return

    if await handle_admin_subscription_input(update, context):
        return

    pending_ticket_id = context.user_data.get("reply_to_ticket_id")
    if pending_ticket_id:
        ok = await send_admin_reply(context, int(pending_ticket_id), admin.id, message)
        sending_details = context.user_data.get("admin_sending_payment_details_ticket_id") == int(pending_ticket_id)
        sending_sub_details = context.user_data.get("admin_sending_subscription_payment_details_ticket_id") == int(pending_ticket_id)
        context.user_data.pop("reply_to_ticket_id", None)
        context.user_data.pop("admin_sending_payment_details_ticket_id", None)
        context.user_data.pop("admin_sending_subscription_payment_details_ticket_id", None)
        if ok and sending_sub_details:
            ticket = get_ticket(int(pending_ticket_id))
            await message.reply_text(
                "Реквизиты отправлены клиенту. Теперь в заявке доступны кнопки создания клиента.",
                reply_markup=subscription_ticket_keyboard(int(pending_ticket_id), int(ticket["user_id"])) if ticket else admin_main_keyboard(),
            )
        elif ok and sending_details:
            ticket = get_ticket(int(pending_ticket_id))
            await message.reply_text(
                "Реквизиты отправлены клиенту. Теперь в заявке доступны кнопки продления.",
                reply_markup=renewal_ticket_keyboard(int(pending_ticket_id), int(ticket["user_id"])) if ticket else admin_main_keyboard(),
            )
        else:
            await message.reply_text("Ответ отправлен пользователю." if ok else "Не удалось отправить ответ.")
        return

    if message.reply_to_message:
        mapped = find_ticket_by_admin_message(admin.id, message.reply_to_message.message_id)
        if mapped:
            ok = await send_admin_reply(context, int(mapped["ticket_id"]), admin.id, message)
            await message.reply_text("Ответ отправлен пользователю." if ok else "Не удалось отправить ответ.")
            return

    await message.reply_text(
        "Я не понял, кому отправить это сообщение.\n"
        "Нажмите кнопку «Ответить» под обращением или используйте: /reply ID текст"
    )


async def route_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id if update.effective_user else None
    if is_admin(user_id):
        await handle_admin_message(update, context)
    else:
        await handle_user_message(update, context)


@require_admin
async def tickets_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    page = 1
    if context.args:
        try:
            page = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Страница должна быть числом. Например: /tickets 2")
            return

    rows, page, pages = list_open_tickets(page)
    total = count_open_tickets()
    text, keyboard = format_tickets_list_page(rows, page, pages, total)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)



@require_admin
async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    page = 1
    if context.args:
        try:
            page = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Страница должна быть числом. Например: /history 2")
            return

    rows, page, pages, total = list_all_tickets(page)
    text, keyboard = format_ticket_history_page(rows, page, pages, total)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

@require_admin
async def renewal_requests_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    page = 1
    if context.args:
        try:
            page = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Страница должна быть числом. Например: /renewals 2")
            return

    rows, page, pages, total = list_renewal_requests(page)
    text, keyboard = format_renewal_requests_page(rows, page, pages, total)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


@require_admin
async def subscription_requests_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    page = 1
    if context.args:
        try:
            page = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Страница должна быть числом. Например: /subscriptions 2")
            return

    rows, page, pages, total = list_subscription_requests(page)
    text, keyboard = format_subscription_requests_page(rows, page, pages, total)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


@require_admin
async def subscription_messages_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    count = count_subscription_welcome_messages()
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📝 Записать заново", callback_data="submsgrecord:1")],
            [InlineKeyboardButton("👁 Предпросмотр", callback_data="submsgpreview:1")],
            [InlineKeyboardButton("🗑 Очистить", callback_data="submsgclear:1")],
        ]
    )
    await update.message.reply_text(
        "Сообщения новым клиентам после создания подписки.\n\n"
        f"Сейчас сохранено сообщений: {count}.\n\n"
        "Чтобы задать сообщения, нажмите «📝 Записать заново» или используйте /setsubmessages. "
        "Затем отправьте в бот нужные сообщения по порядку и завершите командой /done.",
        reply_markup=keyboard,
    )


@require_admin
async def set_subscription_messages_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    clear_subscription_welcome_messages()
    context.user_data["admin_recording_subscription_messages"] = True
    await update.message.reply_text(
        "Режим записи сообщений для новых клиентов включён.\n\n"
        "Отправьте в бот все сообщения, которые нужно автоматически отправлять клиенту после создания подписки. "
        "Можно отправлять текст, фото, документы и другие сообщения.\n\n"
        "Когда закончите, напишите /done. Для отмены — /cancel.",
        reply_markup=admin_main_keyboard(),
    )


@require_admin
async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    if context.user_data.pop("admin_recording_subscription_messages", None):
        await update.message.reply_text(
            f"Готово. Сохранено сообщений: {count_subscription_welcome_messages()}.",
            reply_markup=admin_main_keyboard(),
        )
    else:
        await update.message.reply_text("Сейчас нет активного режима записи.", reply_markup=admin_main_keyboard())


@require_admin
async def clear_subscription_messages_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    deleted = clear_subscription_welcome_messages()
    context.user_data.pop("admin_recording_subscription_messages", None)
    await update.message.reply_text(f"Удалено сообщений: {deleted}.", reply_markup=admin_main_keyboard())


async def preview_subscription_messages(message: Message, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = list_subscription_welcome_messages()
    if not rows:
        await message.reply_text("Сообщения для новых клиентов пока не заданы.", reply_markup=admin_main_keyboard())
        return
    await message.reply_text(f"Предпросмотр сообщений для новых клиентов: {len(rows)} шт.")
    for row in rows:
        try:
            await context.bot.copy_message(
                chat_id=message.chat_id,
                from_chat_id=int(row["source_chat_id"]),
                message_id=int(row["source_message_id"]),
            )
        except TelegramError as exc:
            await message.reply_text(f"Не удалось показать сообщение #{row['id']}: {exc}")


async def handle_admin_subscription_message_recording(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    admin = update.effective_user
    message = update.effective_message
    if not admin or not message:
        return False
    if not context.user_data.get("admin_recording_subscription_messages"):
        return False
    if message.text and message.text.startswith("/"):
        return False
    msg_id = add_subscription_welcome_message(admin.id, message)
    await message.reply_text(
        f"Сообщение сохранено для новых клиентов. ID: {msg_id}.\n"
        "Отправьте следующее сообщение или завершите командой /done.",
        reply_markup=admin_main_keyboard(),
    )
    return True


@require_admin
async def ticket_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /ticket ID")
        return
    try:
        ticket_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID обращения должен быть числом.")
        return

    detail = build_ticket_detail(ticket_id)
    if not detail:
        await update.message.reply_text("Обращение не найдено.")
        return
    text, keyboard = detail
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


@require_admin
async def reply_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text("Использование: /reply ID текст ответа")
        return
    try:
        ticket_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID обращения должен быть числом.")
        return

    text = " ".join(context.args[1:]).strip()
    ok = await send_admin_reply(context, ticket_id, update.effective_user.id, update.effective_message, text)
    await update.message.reply_text("Ответ отправлен пользователю." if ok else "Не удалось отправить ответ.")


@require_admin
async def close_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /close ID")
        return
    try:
        ticket_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID обращения должен быть числом.")
        return

    ticket = get_ticket(ticket_id)
    if not ticket:
        await update.message.reply_text("Обращение не найдено.")
        return

    changed = close_ticket(ticket_id)
    if changed:
        try:
            await context.bot.send_message(
                chat_id=int(ticket["user_id"]),
                text=f"Обращение #{ticket_id} закрыто. Если проблема осталась, напишите новое сообщение.",
            )
        except TelegramError:
            pass
    await update.message.reply_text("Обращение закрыто." if changed else "Обращение уже было закрыто.")


@require_admin
async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /ban USER_ID")
        return
    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("USER_ID должен быть числом.")
        return
    set_ban(user_id, True)
    await update.message.reply_text(f"Пользователь {user_id} заблокирован.")


@require_admin
async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /unban USER_ID")
        return
    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("USER_ID должен быть числом.")
        return
    set_ban(user_id, False)
    await update.message.reply_text(f"Пользователь {user_id} разблокирован.")


@require_admin
async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT user_id, username, first_name, last_name, is_banned, updated_at
            FROM users
            ORDER BY updated_at DESC
            LIMIT 20
            """
        ).fetchall()
    if not rows:
        await update.message.reply_text("Пользователей пока нет.")
        return

    lines = ["Последние пользователи:"]
    for row in rows:
        name = " ".join(filter(None, [row["first_name"], row["last_name"]])).strip() or "Без имени"
        username = f"@{row['username']}" if row["username"] else "без username"
        banned = " 🚫" if row["is_banned"] else ""
        lines.append(f"{row['user_id']} — {name} ({username}){banned}")
    await update.message.reply_text("\n".join(lines))


@require_admin
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with db() as conn:
        stats = {
            "users": conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"],
            "banned": conn.execute("SELECT COUNT(*) AS c FROM users WHERE is_banned = 1").fetchone()["c"],
            "open": conn.execute("SELECT COUNT(*) AS c FROM tickets WHERE status = 'open'").fetchone()["c"],
            "closed": conn.execute("SELECT COUNT(*) AS c FROM tickets WHERE status = 'closed'").fetchone()["c"],
            "messages": conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"],
            "admins": conn.execute("SELECT COUNT(*) AS c FROM admins").fetchone()["c"],
        }
    await update.message.reply_text(
        "Статистика:\n"
        f"Пользователей: {stats['users']}\n"
        f"Заблокировано: {stats['banned']}\n"
        f"Открытых обращений: {stats['open']}\n"
        f"Закрытых обращений: {stats['closed']}\n"
        f"Сообщений в базе: {stats['messages']}\n"
        f"Админов: {stats['admins']}"
    )


@require_admin
async def clients_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    page = 1
    if context.args:
        try:
            page = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Страница должна быть числом. Например: /clients 2")
            return

    try:
        async with XuiClient() as api:
            clients = await api.list_clients()
    except XuiApiError as exc:
        await update.message.reply_text(f"Ошибка 3x-ui: {exc}")
        return

    if not clients:
        await update.message.reply_text("В 3x-ui не найдено клиентов.")
        return

    text, keyboard = format_xui_clients_page(clients, page)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


@require_admin
async def xui_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        async with XuiClient() as api:
            status = await api.get_server_status()
    except XuiApiError as exc:
        await update.message.reply_text(f"Ошибка 3x-ui: {exc}")
        return

    if not status:
        await update.message.reply_text("Подключение к 3x-ui есть, но статус сервера пустой.")
        return

    lines = ["3x-ui доступен."]
    for key in ["xray", "uptime", "cpu", "mem", "disk", "netIO", "netTraffic"]:
        if key in status:
            lines.append(f"{key}: {status[key]}")
    if len(lines) == 1:
        lines.append(str(status)[:1200])
    await update.message.reply_text("\n".join(lines))


@require_admin
async def inbounds_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    try:
        async with XuiClient() as api:
            inbounds = await api.list_inbounds()
    except XuiApiError as exc:
        await update.message.reply_text(f"Ошибка 3x-ui: {exc}")
        return

    if not inbounds:
        await update.message.reply_text("В 3x-ui не найдено ни одного inbound.")
        return

    lines = ["<b>Inbounds 3x-ui</b>"]
    if XUI_RENEW_MODE == "legacy_inbound":
        if XUI_MAIN_INBOUND_ID is None:
            lines.append("Основной inbound для legacy-режима: <b>не указан</b>")
            lines.append("Добавьте в .env строку: <code>XUI_MAIN_INBOUND_ID=ID</code>")
        else:
            lines.append(f"Основной inbound для legacy-режима: <code>{XUI_MAIN_INBOUND_ID}</code>")
    else:
        lines.append("Продление подписок сейчас идёт через <code>/panel/api/clients/bulkAdjust</code>, основной inbound не используется.")
    lines.append("")

    for inbound in inbounds:
        inbound_id = safe_int(inbound.get("id"))
        marker = " ⭐ основной" if XUI_RENEW_MODE == "legacy_inbound" and XUI_MAIN_INBOUND_ID is not None and inbound_id == XUI_MAIN_INBOUND_ID else ""
        settings = XuiClient._settings(inbound)
        clients = settings.get("clients") or []
        client_count = len(clients) if isinstance(clients, list) else 0
        remark = html.escape(str(inbound.get("remark") or "без названия"))
        protocol = html.escape(str(inbound.get("protocol") or "unknown"))
        port = html.escape(str(inbound.get("port") or "-"))
        enabled = "вкл" if inbound.get("enable", True) else "выкл"
        lines.append(
            f"• ID <code>{inbound_id}</code>{marker} — <b>{remark}</b> "
            f"(<code>{protocol}</code>, порт <code>{port}</code>, {enabled}, клиентов: <code>{client_count}</code>)"
        )

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


@require_admin
async def subinfo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /subinfo EMAIL")
        return
    email = context.args[0].strip()

    try:
        async with XuiClient() as api:
            summary = await api.get_client_summary(email)
    except XuiApiError as exc:
        await update.message.reply_text(f"Ошибка 3x-ui: {exc}")
        return

    if not summary:
        await update.message.reply_text(f"Клиент '{email}' не найден.")
        return

    copies = summary.get("copies") or []
    lines = [
        f"Клиент 3x-ui: <b>{html.escape(str(summary.get('email') or email))}</b>",
        f"Записей в inbounds: <code>{len(copies)}</code>",
        f"Включён хотя бы в одном inbound: <code>{'да' if summary.get('enabled') else 'нет'}</code>",
        f"Истекает: <code>{format_xui_expiry_range(summary)}</code>",
        f"Лимит трафика: <code>{format_bytes(summary.get('total_limit'))}</code>",
    ]

    tg_id = str(summary.get("tgId") or "").strip()
    if tg_id:
        lines.append(f"tgId: <code>{html.escape(tg_id)}</code>")
    sub_id = str(summary.get("subId") or "").strip()
    if sub_id:
        lines.append(f"subId: <code>{html.escape(sub_id)}</code>")

    lines.append("")
    lines.append("Где найден клиент:")
    for copy in copies[:30]:
        lines.append(
            f"• Inbound <code>{html.escape(str(copy.get('inbound_id')))}</code> — "
            f"{html.escape(str(copy.get('inbound_remark')))} "
            f"(<code>{html.escape(str(copy.get('protocol') or 'unknown'))}</code>), "
            f"истекает: <code>{format_xui_datetime(safe_int(copy.get('expiry_ms')))}</code>"
        )
    if len(copies) > 30:
        lines.append(f"...и ещё {len(copies) - 30}")

    lines.append("")
    lines.append(f"Продлить подписку: <code>/renew {html.escape(email)} {XUI_DEFAULT_RENEW_DAYS}</code>")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def notify_user_about_successful_renewal(
    context: ContextTypes.DEFAULT_TYPE,
    ticket_id: int,
    user_id: int,
    result: dict[str, Any],
    days: int,
) -> bool:
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                f"✅ Подписка успешно продлена по обращению #{ticket_id}.\n\n"
                f"Добавлено дней: {days}.\n"
                f"Новая дата окончания: {format_xui_datetime(int(result['new_expiry_ms']))}."
            ),
        )
        log_message(
            ticket_id=ticket_id,
            direction="admin_to_user",
            telegram_message_id=None,
            content_type="renew_success",
            text=f"Подписка успешно продлена на {days} дн. Новая дата: {format_xui_datetime(int(result['new_expiry_ms']))}",
            admin_id=None,
        )
        return True
    except TelegramError as exc:
        logger.warning("Не удалось отправить уведомление о продлении пользователю %s: %s", user_id, exc)
        return False


def format_renew_success_admin_text(result: dict[str, Any], days: int, ticket_id: Optional[int] = None) -> str:
    old_text = format_xui_datetime(int(result.get("old_expiry_ms") or 0))
    if result.get("old_expiry_different"):
        old_text = (
            f"с {format_xui_datetime(int(result.get('old_expiry_ms_min') or 0))} "
            f"до {format_xui_datetime(int(result.get('old_expiry_ms_max') or 0))}"
        )

    prefix = f"Подписка продлена по обращению <b>#{ticket_id}</b>." if ticket_id else "Подписка продлена через Clients API."
    return (
        f"{prefix}\n"
        f"Клиент: <b>{html.escape(str(result['email']))}</b>\n"
        f"Обновлена подписка клиента: <code>{result.get('updated_count')}</code>\n"
        f"Режим: <code>{html.escape(str(result.get('renew_mode')))}</code>\n"
        f"Было: <code>{old_text}</code>\n"
        f"Стало: <code>{format_xui_datetime(int(result['new_expiry_ms']))}</code>\n"
        f"Добавлено дней: <code>{days}</code>"
    )


async def finalize_renewal_ticket(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    ticket_id: int,
    days_override: Optional[int] = None,
    email_override: Optional[str] = None,
    bind_email_to_user: bool = True,
) -> None:
    message = update.effective_message
    admin = update.effective_user
    if not message or not admin:
        return

    request = get_renewal_request(ticket_id)
    if not request:
        await message.reply_text("По этому номеру обращения нет заявки на продление.")
        return
    if str(request["status"]) == "renewed":
        renewed_at = request["renewed_at"] or "неизвестно"
        await message.reply_text(f"Заявка по обращению #{ticket_id} уже была продлена. Дата: {renewed_at}")
        return
    if not request["payment_details_sent_at"]:
        await message.reply_text(
            "Сначала отправьте клиенту реквизиты через кнопку «💳 Отправить реквизиты» "
            "или выставьте счёт Telegram Stars.\n"
            "После ручных реквизитов или успешной оплаты Stars появятся кнопки для продления."
        )
        return
    if str(request["payment_method"] or "manual") == "stars" and not request["payment_confirmed_at"]:
        await message.reply_text(
            "По этой заявке выставлен счёт Telegram Stars, но оплата ещё не поступила. "
            "Продление станет доступно после успешной оплаты."
        )
        return

    user_id = int(request["telegram_user_id"])
    days = int(days_override or request["days"])
    xui_email = (email_override or get_xui_link(user_id) or request["xui_email"] or "").strip()
    panel_link_info: Optional[dict[str, Any]] = None

    try:
        async with XuiClient() as api:
            if not xui_email:
                # Если Telegram ID клиента привязан прямо в панели 3x-ui,
                # находим email автоматически и не просим админа вводить его вручную.
                xui_email = (await api.find_client_email_by_tg_id(user_id) or "").strip()
                if xui_email and bind_email_to_user:
                    set_xui_link(user_id, xui_email)
                    update_renewal_request_input(ticket_id, xui_email, days)

            if not xui_email:
                await message.reply_text(
                    "Не удалось автоматически найти клиента 3x-ui по Telegram ID.\n\n"
                    "Проверьте, что в панели 3x-ui у клиента заполнено поле tgId именно этим Telegram ID:\n"
                    f"<code>{user_id}</code>\n\n"
                    "Либо нажмите «✍️ Ввести email/дни вручную» в заявке или используйте:\n"
                    f"<code>/linksub {user_id} EMAIL</code>",
                    parse_mode=ParseMode.HTML,
                )
                return

            if email_override and bind_email_to_user and XUI_SYNC_TGID_TO_PANEL_ON_LINK:
                # Сначала записываем tgId в панель. Если это не получилось,
                # продление не выполняем: админ может выбрать отдельную кнопку
                # «Продлить по email без привязки».
                panel_link_info = await api.set_client_tg_id(xui_email, user_id)

            result = await api.renew_client(xui_email, days)
    except XuiApiError as exc:
        await message.reply_text(f"Ошибка продления: {exc}")
        return

    if email_override:
        if bind_email_to_user:
            set_xui_link(user_id, email_override)
        update_renewal_request_input(ticket_id, email_override, days)
    elif days_override:
        update_renewal_request_input(ticket_id, None, days)

    mark_renewal_request_renewed(ticket_id, admin.id)
    close_ticket(ticket_id)
    notified = await notify_user_about_successful_renewal(context, ticket_id, user_id, result, days)

    text = format_renew_success_admin_text(result, days, ticket_id)
    if panel_link_info:
        text += f"\n\n🔗 Email привязан к Telegram ID в панели 3x-ui: <code>{html.escape(str(panel_link_info.get('tgId')))}</code>."
    elif email_override and not bind_email_to_user:
        text += "\n\nℹ️ Продление выполнено по email без создания привязки."
    text += "\n\nКлиенту отправлено уведомление об успешном продлении." if notified else "\n\nПодписка продлена, но уведомление клиенту отправить не удалось."
    await message.reply_text(text, parse_mode=ParseMode.HTML)


@require_admin
async def renewticket_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /renewticket TICKET_ID [DAYS]")
        return
    try:
        ticket_id = int(context.args[0])
        days_override = int(context.args[1]) if len(context.args) >= 2 else None
    except ValueError:
        await update.message.reply_text("TICKET_ID и DAYS должны быть числами. Например: /renewticket 15 или /renewticket 15 30")
        return

    await finalize_renewal_ticket(update, context, ticket_id, days_override)


@require_admin
async def renew_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(f"Использование: /renew EMAIL DAYS [TICKET_ID]\nНапример: /renew ivan 30")
        return

    email = context.args[0].strip()
    ticket_id: Optional[int] = None
    try:
        days = int(context.args[1]) if len(context.args) >= 2 else XUI_DEFAULT_RENEW_DAYS
        ticket_id = int(context.args[2]) if len(context.args) >= 3 else None
    except ValueError:
        await update.message.reply_text("DAYS и TICKET_ID должны быть числами. Например: /renew ivan 30 15")
        return

    if ticket_id:
        request = get_renewal_request(ticket_id)
        if request and not request["payment_details_sent_at"]:
            await update.message.reply_text(
                "Сначала отправьте клиенту реквизиты через кнопку «💳 Отправить реквизиты» "
                "или выставьте счёт Telegram Stars."
            )
            return
        if request and str(request["payment_method"] or "manual") == "stars" and not request["payment_confirmed_at"]:
            await update.message.reply_text(
                "По этой заявке выставлен счёт Telegram Stars, но оплата ещё не поступила. "
                "Продление станет доступно после успешной оплаты."
            )
            return

    try:
        async with XuiClient() as api:
            result = await api.renew_client(email, days)
    except XuiApiError as exc:
        await update.message.reply_text(f"Ошибка продления: {exc}")
        return

    text = format_renew_success_admin_text(result, days, ticket_id)
    if ticket_id:
        ticket = get_ticket(ticket_id)
        if ticket:
            mark_renewal_request_renewed(ticket_id, update.effective_user.id) if get_renewal_request(ticket_id) else None
            close_ticket(ticket_id)
            notified = await notify_user_about_successful_renewal(context, ticket_id, int(ticket["user_id"]), result, days)
            text += "\n\nКлиенту отправлено уведомление об успешном продлении." if notified else "\n\nПодписка продлена, но уведомление клиенту отправить не удалось."
        else:
            text += f"\n\nОбращение #{ticket_id} не найдено, поэтому уведомление клиенту не отправлено."

    if result.get("failures"):
        text += "\n\nНе удалось обновить:\n" + "\n".join(html.escape(str(x)) for x in result["failures"][:10])
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


@require_admin
async def linksub_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text("Использование: /linksub USER_ID EMAIL")
        return
    try:
        telegram_user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("USER_ID должен быть числом.")
        return

    xui_email = context.args[1].strip()
    try:
        async with XuiClient() as api:
            found = await api.find_client(xui_email)
            if not found:
                await update.message.reply_text(f"Клиент '{xui_email}' не найден в 3x-ui. Привязка не создана.")
                return
            panel_link_info = None
            if XUI_SYNC_TGID_TO_PANEL_ON_LINK:
                panel_link_info = await api.set_client_tg_id(xui_email, telegram_user_id)
    except XuiApiError as exc:
        await update.message.reply_text(
            f"Ошибка 3x-ui при привязке: {exc}\n\n"
            "Локальная привязка не создана, потому что tgId не был записан в панель."
        )
        return

    set_xui_link(telegram_user_id, xui_email)
    suffix = ""
    if panel_link_info:
        suffix = f"\nТакже tgId записан в панели 3x-ui: {telegram_user_id}"
    await update.message.reply_text(f"Пользователь Telegram {telegram_user_id} привязан к клиенту 3x-ui: {xui_email}{suffix}")


@require_admin
async def renewuser_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /renewuser USER_ID DAYS [TICKET_ID]")
        return
    try:
        telegram_user_id = int(context.args[0])
        days = int(context.args[1]) if len(context.args) >= 2 else XUI_DEFAULT_RENEW_DAYS
        ticket_id = int(context.args[2]) if len(context.args) >= 3 else None
    except ValueError:
        await update.message.reply_text("USER_ID, DAYS и TICKET_ID должны быть числами. Например: /renewuser 123456789 30 15")
        return

    xui_email = get_xui_link(telegram_user_id)

    if ticket_id:
        request = get_renewal_request(ticket_id)
        if request and not request["payment_details_sent_at"]:
            await update.message.reply_text(
                "Сначала отправьте клиенту реквизиты через кнопку «💳 Отправить реквизиты» "
                "или выставьте счёт Telegram Stars."
            )
            return
        if request and str(request["payment_method"] or "manual") == "stars" and not request["payment_confirmed_at"]:
            await update.message.reply_text(
                "По этой заявке выставлен счёт Telegram Stars, но оплата ещё не поступила. "
                "Продление станет доступно после успешной оплаты."
            )
            return

    try:
        async with XuiClient() as api:
            if not xui_email:
                xui_email = await api.find_client_email_by_tg_id(telegram_user_id)
                if xui_email:
                    set_xui_link(telegram_user_id, xui_email)
            if not xui_email:
                await update.message.reply_text(
                    "Для этого Telegram ID нет привязанного клиента. "
                    "Проверьте tgId в панели 3x-ui или используйте /linksub USER_ID EMAIL"
                )
                return
            result = await api.renew_client(xui_email, days)
    except XuiApiError as exc:
        await update.message.reply_text(f"Ошибка продления: {exc}")
        return

    text = format_renew_success_admin_text(result, days, ticket_id)
    text += f"\nTelegram ID: <code>{telegram_user_id}</code>"

    if ticket_id:
        ticket = get_ticket(ticket_id)
        if ticket and int(ticket["user_id"]) == telegram_user_id:
            mark_renewal_request_renewed(ticket_id, update.effective_user.id) if get_renewal_request(ticket_id) else None
            close_ticket(ticket_id)
            notified = await notify_user_about_successful_renewal(context, ticket_id, telegram_user_id, result, days)
            text += "\n\nКлиенту отправлено уведомление об успешном продлении." if notified else "\n\nПодписка продлена, но уведомление клиенту отправить не удалось."
        elif ticket:
            text += "\n\nОбращение найдено, но оно принадлежит другому Telegram ID. Уведомление не отправлено."
        else:
            text += f"\n\nОбращение #{ticket_id} не найдено, поэтому уведомление клиенту не отправлено."

    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


@require_admin
async def unlinksub_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /unlinksub USER_ID")
        return
    try:
        telegram_user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("USER_ID должен быть числом.")
        return
    changed = delete_xui_link(telegram_user_id)
    await update.message.reply_text("Привязка удалена." if changed else "Привязка не найдена.")


@require_admin
async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("Использование: /broadcast текст рассылки")
        return

    with db() as conn:
        rows = conn.execute("SELECT user_id FROM users WHERE is_banned = 0").fetchall()

    ok = 0
    failed = 0
    for row in rows:
        try:
            await context.bot.send_message(chat_id=int(row["user_id"]), text=text)
            ok += 1
        except TelegramError:
            failed += 1

    await update.message.reply_text(f"Рассылка завершена. Отправлено: {ok}, ошибок: {failed}.")


async def send_actual_menu_to_user(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    """Отправляет пользователю актуальную reply-клавиатуру.

    Telegram не умеет менять нижнюю клавиатуру молча: новая клавиатура
    появляется только вместе с новым сообщением от бота.
    """
    try:
        if is_admin(user_id):
            await context.bot.send_message(
                chat_id=user_id,
                text="🔄 Меню администратора обновлено. Используйте кнопки ниже.",
                reply_markup=admin_main_keyboard(),
            )
        else:
            if is_banned(user_id):
                return False
            await context.bot.send_message(
                chat_id=user_id,
                text="🔄 Меню обновлено. Используйте кнопки ниже.",
                reply_markup=client_main_keyboard(),
            )
        return True
    except TelegramError as exc:
        logger.warning("Не удалось отправить обновлённое меню пользователю %s: %s", user_id, exc)
        return False


@require_admin
async def update_menus_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    with db() as conn:
        rows = conn.execute("SELECT user_id FROM users").fetchall()

    user_ids = {int(row["user_id"]) for row in rows}
    user_ids.update(get_admin_ids())

    ok = 0
    failed = 0
    skipped = 0
    for user_id in sorted(user_ids):
        if not is_admin(user_id) and is_banned(user_id):
            skipped += 1
            continue
        if await send_actual_menu_to_user(context, user_id):
            ok += 1
        else:
            failed += 1

    await update.message.reply_text(
        f"Обновление меню завершено. Отправлено: {ok}, ошибок: {failed}, пропущено: {skipped}.",
        reply_markup=admin_main_keyboard(),
    )


@require_admin
async def admins_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admins = get_admin_ids()
    lines = ["Администраторы:"]
    for admin_id in admins:
        mark = " 👑" if is_super_admin(admin_id) else ""
        lines.append(f"{admin_id}{mark}")
    await update.message.reply_text("\n".join(lines))


@require_super_admin
async def add_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /addadmin USER_ID")
        return
    try:
        admin_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("USER_ID должен быть числом.")
        return
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO admins(user_id, added_by, created_at) VALUES (?, ?, ?)",
            (admin_id, update.effective_user.id, now_iso()),
        )
    await update.message.reply_text(f"Админ {admin_id} добавлен.")


@require_super_admin
async def del_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /deladmin USER_ID")
        return
    try:
        admin_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("USER_ID должен быть числом.")
        return
    if admin_id == SUPER_ADMIN_ID:
        await update.message.reply_text("Нельзя удалить главного администратора.")
        return
    with db() as conn:
        conn.execute("DELETE FROM admins WHERE user_id = ?", (admin_id,))
    await update.message.reply_text(f"Админ {admin_id} удалён.")


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    context.user_data.pop("reply_to_ticket_id", None)
    context.user_data.pop("admin_waiting_renew_ticket_id", None)
    context.user_data.pop("admin_waiting_renew_no_link_ticket_id", None)
    context.user_data.pop("admin_sending_payment_details_ticket_id", None)
    context.user_data.pop("admin_waiting_stars_invoice_ticket_id", None)
    context.user_data.pop("admin_sending_subscription_payment_details_ticket_id", None)
    context.user_data.pop("admin_waiting_subscription_ticket_id", None)
    context.user_data.pop("admin_recording_subscription_messages", None)
    context.user_data.pop("client_waiting_subscribe_months", None)
    context.user_data.pop("client_subscribe_months", None)
    await update.message.reply_text("Действие отменено.")


async def reject_renewal_ticket(
    context: ContextTypes.DEFAULT_TYPE,
    ticket_id: int,
    admin_id: int,
    reply_message: Message,
) -> None:
    request = get_renewal_request(ticket_id)
    ticket = get_ticket(ticket_id)
    if not request or not ticket:
        await reply_message.reply_text("Заявка на продление не найдена.")
        return

    if str(request["status"]) == "renewed":
        await reply_message.reply_text("Эта заявка уже продлена, отклонить её нельзя.")
        return

    if str(request["status"]) == "rejected":
        await reply_message.reply_text("Эта заявка уже была отклонена.")
        return

    mark_renewal_request_rejected(ticket_id, admin_id)
    close_ticket(ticket_id)
    log_message(
        ticket_id=ticket_id,
        direction="admin_to_user",
        telegram_message_id=None,
        content_type="renew_rejected",
        text="Заявка на продление отклонена администратором",
        admin_id=admin_id,
    )

    try:
        await context.bot.send_message(
            chat_id=int(request["telegram_user_id"]),
            text=(
                f"❌ Заявка на продление по обращению #{ticket_id} отклонена.\n\n"
                "Если вопрос остался, создайте новое обращение через кнопку «🆘 Обращение»."
            ),
            reply_markup=client_main_keyboard(),
        )
    except TelegramError:
        pass

    await reply_message.reply_text(
        f"Заявка на продление по обращению #{ticket_id} отклонена. Обращение закрыто.",
        reply_markup=admin_main_keyboard(),
    )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return
    await query.answer()

    data = query.data or ""
    action, _, value = data.partition(":")

    if action == "noop":
        return

    if action == "balance":
        if value not in {"stars", "p2p", "crypto"}:
            return
        context.user_data["balance_topup_method"] = value
        await query.message.reply_text(
            f"Введите сумму пополнения в ⭐ (от {STARS_MIN_AMOUNT} до {STARS_MAX_AMOUNT}).\n"
            "Эта сумма будет зачислена на внутренний баланс после оплаты/подтверждения.",
            reply_markup=client_main_keyboard(),
        )
        return

    if action == "topuppaid":
        try:
            topup_id = int(value)
        except ValueError:
            return
        topup = get_balance_topup(topup_id)
        if not topup or int(topup["user_id"]) != user.id or str(topup["status"]) != "pending":
            await query.message.reply_text("Заявка на пополнение не найдена или уже обработана.")
            return
        for admin_id in get_admin_ids():
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"💰 Пользователь {user.id} подтвердил {topup['method'].upper()}-перевод по пополнению #{topup_id}: {topup['amount']} ⭐.",
                    reply_markup=balance_topup_confirm_keyboard(topup_id),
                )
            except TelegramError:
                pass
        await query.message.reply_text("Подтверждение передано администратору. Баланс будет зачислен после проверки.", reply_markup=client_main_keyboard())
        return

    if action == "topupconfirm":
        if not is_admin(user.id):
            return
        try:
            topup_id = int(value)
        except ValueError:
            return
        topup = confirm_balance_topup(topup_id, user.id)
        if not topup:
            await query.message.reply_text("Это пополнение уже обработано или не найдено.")
            return
        try:
            await context.bot.send_message(chat_id=int(topup["user_id"]), text=f"✅ Баланс пополнен на {topup['amount']} ⭐. Текущий баланс: {balance_of(int(topup['user_id']))} ⭐.", reply_markup=client_main_keyboard())
        except TelegramError:
            pass
        await query.message.reply_text(f"Пополнение #{topup_id} зачислено. Реферальная награда (если есть) начислена автоматически.")
        return

    if action == "renewpay":
        # Формат callback: renewpay:<manual|stars>:<months>
        parts = data.split(":")
        if len(parts) != 3:
            await query.message.reply_text("Некорректный выбор способа оплаты.", reply_markup=client_main_keyboard())
            return
        method = parts[1]
        try:
            months = int(parts[2])
        except ValueError:
            await query.message.reply_text("Некорректный срок продления.", reply_markup=client_main_keyboard())
            return
        if method not in {"balance", "stars"}:
            await query.message.reply_text("Некорректный способ оплаты.", reply_markup=client_main_keyboard())
            return
        if method == "stars" and (not STARS_PAYMENTS_ENABLED or STARS_PRICE_PER_MONTH <= 0):
            await query.message.reply_text("Оплата Telegram Stars сейчас недоступна. Выберите P2P(Перевод).", reply_markup=client_main_keyboard())
            return
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except TelegramError:
            pass
        context.user_data.pop("client_renew_months", None)
        await process_client_renewal_payment_choice(update, context, months, method)
        return

    if action == "subpay":
        # Формат callback: subpay:<manual|stars>:<months>
        parts = data.split(":")
        if len(parts) != 3:
            await query.message.reply_text("Некорректный выбор способа оплаты.", reply_markup=client_main_keyboard())
            return
        method = parts[1]
        try:
            months = int(parts[2])
        except ValueError:
            await query.message.reply_text("Некорректный срок подписки.", reply_markup=client_main_keyboard())
            return
        if method not in {"balance", "stars"}:
            await query.message.reply_text("Некорректный способ оплаты.", reply_markup=client_main_keyboard())
            return
        if method == "stars" and (not STARS_PAYMENTS_ENABLED or STARS_PRICE_PER_MONTH <= 0):
            await query.message.reply_text("Оплата Telegram Stars сейчас недоступна. Выберите P2P(Перевод).", reply_markup=client_main_keyboard())
            return
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except TelegramError:
            pass
        context.user_data.pop("client_subscribe_months", None)
        await process_client_subscription_payment_choice(update, context, months, method)
        return

    if action == "paymentdone":
        try:
            ticket_id = int(value)
        except ValueError:
            await query.message.reply_text("Некорректный номер обращения.")
            return

        request = get_renewal_request(ticket_id)
        ticket = get_ticket(ticket_id)
        if not request or not ticket or int(request["telegram_user_id"]) != user.id:
            await query.message.reply_text("Эта кнопка не относится к вашей заявке.")
            return
        if str(request["status"]) == "renewed":
            await query.message.reply_text("Эта заявка уже продлена.", reply_markup=client_main_keyboard())
            await query.edit_message_reply_markup(reply_markup=None)
            return
        if not request["payment_details_sent_at"]:
            await query.message.reply_text("Реквизиты по этой заявке ещё не были отправлены.", reply_markup=client_main_keyboard())
            await query.edit_message_reply_markup(reply_markup=None)
            return
        if str(request["payment_method"] or "manual") == "stars":
            await query.message.reply_text("Для этой заявки выставлен счёт Telegram Stars. Оплата подтверждается автоматически после оплаты счёта.", reply_markup=client_main_keyboard())
            await query.edit_message_reply_markup(reply_markup=None)
            return
        if request["payment_confirmed_at"]:
            await query.message.reply_text(
                f"Вы уже подтвердили оплату по обращению #{ticket_id}. Техподдержка проверит платёж.",
                reply_markup=client_main_keyboard(),
            )
            await query.edit_message_reply_markup(reply_markup=None)
            return

        mark_renewal_payment_confirmed(ticket_id, user.id)
        log_message(
            ticket_id=ticket_id,
            direction="user_to_admin",
            telegram_message_id=None,
            content_type="payment_done",
            text="Клиент нажал кнопку «Платёж выполнен»",
            user_id=user.id,
        )
        await notify_admins_about_payment_done(context, ticket_id, user.id)
        await query.message.reply_text(
            f"Спасибо. Техподдержка получила подтверждение оплаты по обращению #{ticket_id}.",
            reply_markup=client_main_keyboard(),
        )
        await query.edit_message_reply_markup(reply_markup=None)
        return

    if action == "subpaymentdone":
        try:
            ticket_id = int(value)
        except ValueError:
            await query.message.reply_text("Некорректный номер обращения.")
            return
        request = get_subscription_request(ticket_id)
        ticket = get_ticket(ticket_id)
        if not request or not ticket or int(request["telegram_user_id"]) != user.id:
            await query.message.reply_text("Эта кнопка не относится к вашей заявке.")
            return
        if str(request["status"]) == "created":
            await query.message.reply_text("Подписка по этой заявке уже оформлена.", reply_markup=client_main_keyboard())
            await query.edit_message_reply_markup(reply_markup=None)
            return
        if not request["payment_details_sent_at"]:
            await query.message.reply_text("Реквизиты по этой заявке ещё не были отправлены.", reply_markup=client_main_keyboard())
            await query.edit_message_reply_markup(reply_markup=None)
            return
        if str(request["payment_method"] or "manual") == "stars":
            await query.message.reply_text("Для этой заявки выставлен счёт Telegram Stars. Оплата подтверждается автоматически после оплаты счёта.", reply_markup=client_main_keyboard())
            await query.edit_message_reply_markup(reply_markup=None)
            return
        if request["payment_confirmed_at"]:
            await query.message.reply_text(
                f"Вы уже подтвердили оплату по обращению #{ticket_id}. Техподдержка проверит платёж.",
                reply_markup=client_main_keyboard(),
            )
            await query.edit_message_reply_markup(reply_markup=None)
            return
        mark_subscription_payment_confirmed(ticket_id, user.id)
        log_message(
            ticket_id=ticket_id,
            direction="user_to_admin",
            telegram_message_id=None,
            content_type="subscription_payment_done",
            text="Клиент нажал кнопку «Платёж выполнен» по оформлению подписки",
            user_id=user.id,
        )
        for admin_id in get_admin_ids():
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        f"✅ Клиент подтвердил оплату по заявке на оформление <b>#{ticket_id}</b>.\n"
                        "Проверьте поступление денег. Если оплата пришла, нажмите <b>✅ Создать клиента</b>."
                    ),
                    parse_mode=ParseMode.HTML,
                    reply_markup=subscription_ticket_keyboard(ticket_id, user.id),
                )
            except TelegramError:
                pass
        await query.message.reply_text(
            f"Спасибо. Техподдержка получила подтверждение оплаты по обращению #{ticket_id}.",
            reply_markup=client_main_keyboard(),
        )
        await query.edit_message_reply_markup(reply_markup=None)
        return

    if not is_admin(user.id):
        await query.edit_message_reply_markup(reply_markup=None)
        return

    if action == "tickets":
        try:
            page = int(value)
        except ValueError:
            await query.message.reply_text("Некорректный номер страницы.")
            return
        rows, page, pages = list_open_tickets(page)
        total = count_open_tickets()
        text, keyboard = format_tickets_list_page(rows, page, pages, total)
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    elif action == "history":
        try:
            page = int(value)
        except ValueError:
            await query.message.reply_text("Некорректный номер страницы.")
            return
        rows, page, pages, total = list_all_tickets(page)
        text, keyboard = format_ticket_history_page(rows, page, pages, total)
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    elif action == "historyticket":
        try:
            ticket_id = int(value)
        except ValueError:
            await query.message.reply_text("Некорректный ID обращения.")
            return
        chunks = build_ticket_full_chat_chunks(ticket_id)
        if not chunks:
            await query.message.reply_text("Обращение не найдено.")
            return
        await query.edit_message_text(chunks[0], parse_mode=ParseMode.HTML)
        for chunk in chunks[1:]:
            await query.message.reply_text(chunk, parse_mode=ParseMode.HTML)

    elif action == "ticket":
        try:
            ticket_id = int(value)
        except ValueError:
            await query.message.reply_text("Некорректный ID обращения.")
            return
        detail = build_ticket_detail(ticket_id)
        if not detail:
            await query.message.reply_text("Обращение не найдено.")
            return
        text, keyboard = detail
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    elif action == "renewals":
        try:
            page = int(value)
        except ValueError:
            await query.message.reply_text("Некорректный номер страницы.")
            return
        rows, page, pages, total = list_renewal_requests(page)
        text, keyboard = format_renewal_requests_page(rows, page, pages, total)
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    elif action == "subscriptions":
        try:
            page = int(value)
        except ValueError:
            await query.message.reply_text("Некорректный номер страницы.")
            return
        rows, page, pages, total = list_subscription_requests(page)
        text, keyboard = format_subscription_requests_page(rows, page, pages, total)
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    elif action == "submsgrecord":
        clear_subscription_welcome_messages()
        context.user_data["admin_recording_subscription_messages"] = True
        await query.message.reply_text(
            "Режим записи сообщений для новых клиентов включён.\n\n"
            "Отправьте в бот нужные сообщения по порядку. После завершения напишите /done. Для отмены — /cancel.",
            reply_markup=admin_main_keyboard(),
        )

    elif action == "submsgpreview":
        await preview_subscription_messages(query.message, context)

    elif action == "submsgclear":
        deleted = clear_subscription_welcome_messages()
        context.user_data.pop("admin_recording_subscription_messages", None)
        await query.message.reply_text(f"Удалено сообщений: {deleted}.", reply_markup=admin_main_keyboard())

    elif action == "renewsel":
        try:
            ticket_id = int(value)
        except ValueError:
            await query.message.reply_text("Некорректный ID заявки.")
            return
        request = get_renewal_request(ticket_id)
        if request and not request["payment_details_sent_at"]:
            await query.message.reply_text("Сначала отправьте клиенту реквизиты или выставьте счёт Telegram Stars.")
            return
        if request and str(request["payment_method"] or "manual") == "stars" and not request["payment_confirmed_at"]:
            await query.message.reply_text("Сначала дождитесь успешной оплаты Telegram Stars.")
            return
        await ask_admin_for_renewal_input(query.message, context, ticket_id, bind_email_to_user=True)

    elif action == "renewnolink":
        try:
            ticket_id = int(value)
        except ValueError:
            await query.message.reply_text("Некорректный ID заявки.")
            return
        request = get_renewal_request(ticket_id)
        if request and not request["payment_details_sent_at"]:
            await query.message.reply_text("Сначала отправьте клиенту реквизиты или выставьте счёт Telegram Stars.")
            return
        if request and str(request["payment_method"] or "manual") == "stars" and not request["payment_confirmed_at"]:
            await query.message.reply_text("Сначала дождитесь успешной оплаты Telegram Stars.")
            return
        await ask_admin_for_renewal_input(query.message, context, ticket_id, bind_email_to_user=False)

    elif action == "paydetails":
        try:
            ticket_id = int(value)
        except ValueError:
            await query.message.reply_text("Некорректный ID заявки.")
            return
        request = get_renewal_request(ticket_id)
        if not request:
            await query.message.reply_text("Заявка на продление не найдена.")
            return
        if str(request["status"]) == "renewed":
            await query.message.reply_text("Эта заявка уже продлена.")
            return
        if request["payment_details_sent_at"]:
            await query.message.reply_text(
                "Реквизиты по этой заявке уже отправлены. Теперь можно продлить подписку после проверки оплаты.",
                reply_markup=renewal_ticket_keyboard(ticket_id, int(request["telegram_user_id"])),
            )
            return
        context.user_data["reply_to_ticket_id"] = ticket_id
        context.user_data["admin_sending_payment_details_ticket_id"] = ticket_id
        await query.message.reply_text(
            f"Отправьте реквизиты и сумму для обращения #{ticket_id}.\n"
            "Это сообщение уйдёт клиенту с кнопкой «✅ Платёж выполнен».\n"
            "Для отмены используйте /cancel."
        )

    elif action == "starsinvoice":
        try:
            ticket_id = int(value)
        except ValueError:
            await query.message.reply_text("Некорректный ID заявки.")
            return
        if not STARS_PAYMENTS_ENABLED:
            await query.message.reply_text("Оплата Telegram Stars отключена в .env.")
            return
        request = get_renewal_request(ticket_id)
        if not request:
            await query.message.reply_text("Заявка на продление не найдена.")
            return
        if str(request["status"]) == "renewed":
            await query.message.reply_text("Эта заявка уже продлена.")
            return
        if request["payment_details_sent_at"]:
            await query.message.reply_text("По этой заявке уже отправлен способ оплаты.")
            return
        context.user_data["admin_waiting_stars_invoice_ticket_id"] = ticket_id
        await query.message.reply_text(
            f"Введите сумму счёта в Telegram Stars для обращения #{ticket_id}.\n"
            f"Срок заявки: {request['days']} дн. ({month_word(int(request['months']))}).\n"
            "Например: 250\n\nДля отмены используйте /cancel.",
            reply_markup=admin_main_keyboard(),
        )

    elif action == "rejectrenew":
        try:
            ticket_id = int(value)
        except ValueError:
            await query.message.reply_text("Некорректный ID заявки.")
            return
        await reject_renewal_ticket(context, ticket_id, user.id, query.message)

    elif action == "subpaydetails":
        try:
            ticket_id = int(value)
        except ValueError:
            await query.message.reply_text("Некорректный ID заявки.")
            return
        request = get_subscription_request(ticket_id)
        if not request:
            await query.message.reply_text("Заявка на оформление не найдена.")
            return
        if str(request["status"]) == "created":
            await query.message.reply_text("Клиент по этой заявке уже создан.")
            return
        if request["payment_details_sent_at"]:
            await query.message.reply_text(
                "Реквизиты по этой заявке уже отправлены. Теперь можно создать клиента после проверки оплаты.",
                reply_markup=subscription_ticket_keyboard(ticket_id, int(request["telegram_user_id"])),
            )
            return
        context.user_data["reply_to_ticket_id"] = ticket_id
        context.user_data["admin_sending_subscription_payment_details_ticket_id"] = ticket_id
        await query.message.reply_text(
            f"Отправьте реквизиты и сумму для оформления подписки по обращению #{ticket_id}.\n"
            "Это сообщение уйдёт клиенту с кнопкой «✅ Платёж выполнен».\n"
            "Для отмены используйте /cancel."
        )

    elif action == "substarsinvoice":
        try:
            ticket_id = int(value)
        except ValueError:
            await query.message.reply_text("Некорректный ID заявки.")
            return
        request = get_subscription_request(ticket_id)
        if not request:
            await query.message.reply_text("Заявка на оформление не найдена.")
            return
        if str(request["status"]) == "created":
            await query.message.reply_text("Клиент по этой заявке уже создан.")
            return
        if request["payment_details_sent_at"]:
            await query.message.reply_text("По этой заявке уже отправлен способ оплаты.")
            return
        amount = renewal_stars_amount_for_months(int(request["months"]))
        ok = await send_subscription_stars_invoice_to_client(context, ticket_id, int(request["telegram_user_id"]), amount, sent_by_admin_id=user.id)
        await query.message.reply_text(
            f"Счёт Telegram Stars на {amount} ⭐ отправлен клиенту." if ok else "Не удалось отправить счёт Telegram Stars.",
            reply_markup=subscription_ticket_keyboard(ticket_id, int(request["telegram_user_id"])),
        )

    elif action == "subcreate":
        try:
            ticket_id = int(value)
        except ValueError:
            await query.message.reply_text("Некорректный ID заявки.")
            return
        await finalize_subscription_ticket(update, context, ticket_id)

    elif action == "subcreatesel":
        try:
            ticket_id = int(value)
        except ValueError:
            await query.message.reply_text("Некорректный ID заявки.")
            return
        request = get_subscription_request(ticket_id)
        if request and not request["payment_details_sent_at"]:
            await query.message.reply_text("Сначала отправьте клиенту реквизиты или выставьте счёт Telegram Stars.")
            return
        if request and str(request["payment_method"] or "manual") == "stars" and not request["payment_confirmed_at"]:
            await query.message.reply_text("Сначала дождитесь успешной оплаты Telegram Stars.")
            return
        await ask_admin_for_subscription_input(query.message, context, ticket_id)

    elif action == "rejectsub":
        try:
            ticket_id = int(value)
        except ValueError:
            await query.message.reply_text("Некорректный ID заявки.")
            return
        request = get_subscription_request(ticket_id)
        ticket = get_ticket(ticket_id)
        if not request or not ticket:
            await query.message.reply_text("Заявка на оформление не найдена.")
            return
        mark_subscription_request_rejected(ticket_id, user.id)
        close_ticket(ticket_id)
        log_message(
            ticket_id=ticket_id,
            direction="admin_to_user",
            telegram_message_id=None,
            content_type="subscription_rejected",
            text="Заявка на оформление подписки отклонена",
            admin_id=user.id,
        )
        try:
            await context.bot.send_message(
                chat_id=int(request["telegram_user_id"]),
                text=(
                    f"❌ Заявка на оформление подписки по обращению #{ticket_id} отклонена.\n\n"
                    "Если вопрос остался, создайте новое обращение через кнопку «🆘 Обращение»."
                ),
                reply_markup=client_main_keyboard(),
            )
        except TelegramError:
            pass
        await query.message.reply_text("Заявка на оформление отклонена и обращение закрыто.", reply_markup=admin_main_keyboard())

    elif action == "clients":
        try:
            page = int(value)
        except ValueError:
            await query.message.reply_text("Некорректный номер страницы.")
            return
        try:
            async with XuiClient() as api:
                clients = await api.list_clients()
        except XuiApiError as exc:
            await query.message.reply_text(f"Ошибка 3x-ui: {exc}")
            return
        if not clients:
            await query.message.reply_text("В 3x-ui не найдено клиентов.")
            return
        text, keyboard = format_xui_clients_page(clients, page)
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    elif action == "reply":
        try:
            ticket_id = int(value)
        except ValueError:
            await query.message.reply_text("Некорректный ID обращения.")
            return
        if not get_ticket(ticket_id):
            await query.message.reply_text("Обращение не найдено.")
            return
        context.user_data["reply_to_ticket_id"] = ticket_id
        context.user_data.pop("admin_sending_payment_details_ticket_id", None)
        await query.message.reply_text(
            f"Введите ответ для обращения #{ticket_id}. Можно отправить текст, фото, документ или другое сообщение.\n"
            "Для отмены используйте /cancel."
        )

    elif action == "close":
        try:
            ticket_id = int(value)
        except ValueError:
            await query.message.reply_text("Некорректный ID обращения.")
            return
        ticket = get_ticket(ticket_id)
        if not ticket:
            await query.message.reply_text("Обращение не найдено.")
            return
        changed = close_ticket(ticket_id)
        if changed:
            try:
                await context.bot.send_message(
                    chat_id=int(ticket["user_id"]),
                    text=f"Обращение #{ticket_id} закрыто. Если проблема осталась, напишите новое сообщение.",
                )
            except TelegramError:
                pass
        await query.message.reply_text("Обращение закрыто." if changed else "Обращение уже было закрыто.")

    elif action == "ban":
        try:
            user_id = int(value)
        except ValueError:
            await query.message.reply_text("Некорректный USER_ID.")
            return
        set_ban(user_id, True)
        await query.message.reply_text(f"Пользователь {user_id} заблокирован.")

    elif action == "renewticket":
        try:
            ticket_id = int(value)
        except ValueError:
            await query.message.reply_text("Некорректный ID обращения.")
            return
        # Для кнопки используем сохранённый срок из заявки.
        fake_update = update
        await finalize_renewal_ticket(fake_update, context, ticket_id)


async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.pre_checkout_query
    if not query:
        return

    # Пополнение внутреннего баланса Stars не связано с обращением.
    if query.invoice_payload.startswith("balance:"):
        try:
            _, topup_id_raw, amount_raw = query.invoice_payload.split(":", 2)
            topup_id, amount = int(topup_id_raw), int(amount_raw)
        except ValueError:
            await query.answer(ok=False, error_message="Некорректный счёт пополнения.")
            return
        topup = get_balance_topup(topup_id)
        if not topup or int(topup["user_id"]) != int(query.from_user.id) or str(topup["method"]) != "stars" or str(topup["status"]) != "pending" or int(topup["amount"]) != amount or query.currency != "XTR" or int(query.total_amount) != amount:
            await query.answer(ok=False, error_message="Счёт недействителен или уже обработан.")
            return
        await query.answer(ok=True)
        return

    kind, ticket_id, stars_amount = parse_stars_payload_kind(query.invoice_payload)
    if not ticket_id or not stars_amount or kind not in {"renewal", "subscription"}:
        await query.answer(ok=False, error_message="Некорректный платёжный счёт.")
        return

    request = get_renewal_request(ticket_id) if kind == "renewal" else get_subscription_request(ticket_id)
    if not request or str(request["status"]) != "pending":
        await query.answer(ok=False, error_message="Заявка не найдена или уже закрыта.")
        return

    if int(request["telegram_user_id"]) != int(query.from_user.id):
        await query.answer(ok=False, error_message="Этот счёт относится к другому пользователю.")
        return

    if str(request["payment_method"] or "manual") != "stars":
        await query.answer(ok=False, error_message="Для этой заявки не выбран способ оплаты Telegram Stars.")
        return

    if query.currency != "XTR":
        await query.answer(ok=False, error_message="Ожидалась оплата Telegram Stars.")
        return

    if int(query.total_amount) != int(stars_amount):
        await query.answer(ok=False, error_message="Сумма счёта не совпадает с заявкой.")
        return

    await query.answer(ok=True)


async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user or not message.successful_payment:
        return

    payment = message.successful_payment
    if payment.invoice_payload.startswith("balance:"):
        try:
            _, topup_id_raw, amount_raw = payment.invoice_payload.split(":", 2)
            topup_id, amount = int(topup_id_raw), int(amount_raw)
        except ValueError:
            return
        if payment.currency != "XTR" or int(payment.total_amount) != amount:
            return
        topup = confirm_balance_topup(topup_id, 0, payment.telegram_payment_charge_id)
        if not topup or int(topup["user_id"]) != user.id:
            await message.reply_text("Платёж получен, но пополнение уже обработано или не найдено. Напишите в поддержку.")
            return
        await message.reply_text(
            f"✅ На баланс зачислено {amount} ⭐. Текущий баланс: {balance_of(user.id)} ⭐.",
            reply_markup=client_main_keyboard(),
        )
        return

    kind, ticket_id, stars_amount = parse_stars_payload_kind(payment.invoice_payload)
    if not ticket_id or not stars_amount:
        return

    if kind == "subscription":
        request = get_subscription_request(ticket_id)
        if not request or int(request["telegram_user_id"]) != user.id:
            await message.reply_text("Платёж получен, но заявка не найдена. Напишите в поддержку.", reply_markup=client_main_keyboard())
            return
        mark_subscription_stars_paid(
            ticket_id=ticket_id,
            user_id=user.id,
            charge_id=payment.telegram_payment_charge_id,
            stars_amount=int(payment.total_amount),
        )
        log_message(
            ticket_id=ticket_id,
            direction="user_to_admin",
            telegram_message_id=message.message_id,
            content_type="subscription_stars_payment",
            text=f"Клиент оплатил счёт Telegram Stars за оформление: {payment.total_amount} ⭐",
            user_id=user.id,
        )
        await try_auto_create_subscription_after_stars_payment(context, message, ticket_id, user.id)
        return

    request = get_renewal_request(ticket_id)
    if not request or int(request["telegram_user_id"]) != user.id:
        await message.reply_text("Платёж получен, но заявка не найдена. Напишите в поддержку.", reply_markup=client_main_keyboard())
        return

    mark_renewal_stars_paid(
        ticket_id=ticket_id,
        user_id=user.id,
        charge_id=payment.telegram_payment_charge_id,
        stars_amount=int(payment.total_amount),
    )
    log_message(
        ticket_id=ticket_id,
        direction="user_to_admin",
        telegram_message_id=message.message_id,
        content_type="stars_payment",
        text=f"Клиент оплатил счёт Telegram Stars: {payment.total_amount} ⭐",
        user_id=user.id,
    )

    await try_auto_renew_after_stars_payment(context, message, ticket_id, user.id)


async def post_init(application: Application) -> None:
    # У клиентов не будет подсказок по slash-командам — только две кнопки меню.
    await application.bot.set_my_commands([], scope=BotCommandScopeDefault())

    admin_commands = [
        ("start", "открыть админ-меню"),
        ("help", "помощь"),
        ("id", "узнать свой Telegram ID"),
        ("tickets", "открытые обращения"),
        ("history", "история обращений"),
        ("renewals", "заявки на продление"),
        ("subscriptions", "заявки на оформление"),
        ("submessages", "сообщения новым клиентам"),
        ("stats", "статистика поддержки"),
        ("updatemenus", "обновить кнопки меню всем"),
        ("renewticket", "продлить по заявке"),
        ("renew", "продлить подписку 3x-ui"),
        ("clients", "список клиентов 3x-ui"),
        ("inbounds", "список inbound 3x-ui"),
        ("subinfo", "информация о клиенте 3x-ui"),
    ]
    for admin_id in get_admin_ids():
        try:
            await application.bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=admin_id))
        except TelegramError as exc:
            logger.warning("Не удалось установить команды для админа %s: %s", admin_id, exc)

def build_app() -> Application:
    if not BOT_TOKEN:
        raise RuntimeError("Не указан BOT_TOKEN. Создайте .env по примеру .env.example")
    if not get_admin_ids():
        raise RuntimeError("Не указан ни один администратор. Заполните ADMIN_IDS в .env")

    application = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("id", id_command))
    application.add_handler(CommandHandler("tickets", tickets_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("renewals", renewal_requests_command))
    application.add_handler(CommandHandler("subscriptions", subscription_requests_command))
    application.add_handler(CommandHandler("submessages", subscription_messages_command))
    application.add_handler(CommandHandler("setsubmessages", set_subscription_messages_command))
    application.add_handler(CommandHandler("clearsubmessages", clear_subscription_messages_command))
    application.add_handler(CommandHandler("done", done_command))
    application.add_handler(CommandHandler("ticket", ticket_command))
    application.add_handler(CommandHandler("reply", reply_command))
    application.add_handler(CommandHandler("close", close_command))
    application.add_handler(CommandHandler("ban", ban_command))
    application.add_handler(CommandHandler("unban", unban_command))
    application.add_handler(CommandHandler("users", users_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("xui_status", xui_status_command))
    application.add_handler(CommandHandler("clients", clients_command))
    application.add_handler(CommandHandler("inbounds", inbounds_command))
    application.add_handler(CommandHandler("subinfo", subinfo_command))
    application.add_handler(CommandHandler("renewticket", renewticket_command))
    application.add_handler(CommandHandler("renew", renew_command))
    application.add_handler(CommandHandler("linksub", linksub_command))
    application.add_handler(CommandHandler("renewuser", renewuser_command))
    application.add_handler(CommandHandler("unlinksub", unlinksub_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("updatemenus", update_menus_command))
    application.add_handler(CommandHandler("admins", admins_command))
    application.add_handler(CommandHandler("addadmin", add_admin_command))
    application.add_handler(CommandHandler("deladmin", del_admin_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, route_message))

    return application


def main() -> None:
    init_db()
    app = build_app()
    logger.info("Бот техподдержки запущен")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
