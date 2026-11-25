import math
import os
import re
import shutil
import stat
from hashlib import blake2s
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

from dotenv import dotenv_values, set_key

from .config import ENV_FILE


BOOLEAN_ENV_KEYS = {
    "XUI_VERIFY_SSL", "XUI_RESTART_XRAY_AFTER_RENEW", "XUI_GROUP_CLIENTS_BY_EMAIL",
    "XUI_RENEW_EXPIRED_FROM_NOW", "XUI_SYNC_TGID_TO_PANEL_ON_LINK",
    "STARS_PAYMENTS_ENABLED", "CRYPTO_TOPUP_ENABLED",
}
POSITIVE_INTEGER_ENV_KEYS = {
    "SUPER_ADMIN_ID", "XUI_DEFAULT_RENEW_DAYS", "XUI_CLIENTS_PER_PAGE",
    "XUI_MAX_RENEW_MONTHS", "XUI_DAYS_PER_MONTH", "STARS_MIN_AMOUNT", "STARS_MAX_AMOUNT",
    "BALANCE_PRICE_PER_MONTH", "BALANCE_MIN_TOPUP_RUB", "BALANCE_MAX_TOPUP_RUB",
}
NONNEGATIVE_INTEGER_ENV_KEYS = {
    "XUI_NEW_CLIENT_TOTAL_GB", "XUI_NEW_CLIENT_LIMIT_IP", "STARS_PRICE_PER_MONTH", "REFERRAL_PERCENT",
}
ENV_OPTIONS = {
    "XUI_RENEW_MODE": {"clients_api", "legacy_inbound"},
    "XUI_CLIENTS_RENEW_METHOD": {"bulk_adjust", "update"},
}
ID_LIST_ENV_KEYS = {"ADMIN_IDS", "XUI_NEW_CLIENT_INBOUND_IDS"}
OPTIONAL_ENV_KEYS = {
    "XUI_BASE_URL", "XUI_PUBLIC_SUBSCRIPTION_BASE_URL", "XUI_API_TOKEN", "XUI_USERNAME", "XUI_PASSWORD",
    "XUI_MAIN_INBOUND_ID", "XUI_NEW_CLIENT_INBOUND_IDS", "XUI_NEW_CLIENT_FLOW", "XUI_NEW_CLIENT_GROUP",
    "XUI_NEW_CLIENT_COMMENT", "XUI_SUBSCRIPTION_LINK_TEMPLATE", "ADMIN_IDS",
}
EDITABLE_ENV_KEYS = tuple(sorted(
    BOOLEAN_ENV_KEYS | POSITIVE_INTEGER_ENV_KEYS | NONNEGATIVE_INTEGER_ENV_KEYS |
    ENV_OPTIONS.keys() | ID_LIST_ENV_KEYS | OPTIONAL_ENV_KEYS | {
        "BOT_TOKEN", "DB_PATH", "XUI_TIMEOUT", "XUI_NEW_CLIENT_EMAIL_PREFIX", "STARS_INVOICE_TITLE", "STARS_INVOICE_DESCRIPTION",
    }
))


def env_settings_values() -> dict[str, str | None]:
    configured = dotenv_values(ENV_FILE, interpolate=False) if ENV_FILE.exists() else {}
    names = set(EDITABLE_ENV_KEYS) | {
        key for key in configured if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)
    }
    return {key: configured.get(key) for key in sorted(names)}


def env_key_reference(key: str) -> str:
    if len(key) <= 45:
        return key
    return "~" + blake2s(key.encode(), digest_size=12).hexdigest()


def env_value_can_be_empty(key: str) -> bool:
    return key in OPTIONAL_ENV_KEYS or key not in EDITABLE_ENV_KEYS


def env_display_value(value: str | None, limit: int = 2000) -> str:
    if value is None:
        return "Не задано"
    if not value:
        return "Пусто"
    return value if len(value) <= limit else value[:limit] + "…"


def validate_env_value(key: str, value: str) -> None:
    if key not in EDITABLE_ENV_KEYS and key not in env_settings_values():
        raise ValueError("Эту переменную нельзя изменить через меню.")
    if len(value) > 2000 or any(ord(char) < 32 for char in value) or "${" in value:
        raise ValueError("Введите значение одной строкой, до 2000 символов, без подстановок переменных.")
    if not value:
        if env_value_can_be_empty(key):
            return
        raise ValueError("Для этой настройки нужно указать значение.")
    if key in BOOLEAN_ENV_KEYS and value.lower() not in {"true", "false"}:
        raise ValueError("Введите true или false.")
    if key in POSITIVE_INTEGER_ENV_KEYS | {"XUI_MAIN_INBOUND_ID"}:
        if not re.fullmatch(r"[0-9]+", value) or int(value) < 1:
            raise ValueError("Введите целое число больше нуля.")
        if int(value) >= 2**63 or (key == "SUPER_ADMIN_ID" and int(value) >= 2**52):
            raise ValueError("Число слишком большое для этой настройки.")
    if key in NONNEGATIVE_INTEGER_ENV_KEYS and not re.fullmatch(r"[0-9]+", value):
        raise ValueError("Введите целое число от нуля.")
    if key == "REFERRAL_PERCENT" and int(value) > 100:
        raise ValueError("Процент должен быть от 0 до 100.")
    if key == "XUI_TIMEOUT":
        try:
            timeout = float(value)
        except ValueError:
            timeout = 0
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Введите положительное число секунд.")
    if key in ENV_OPTIONS and value not in ENV_OPTIONS[key]:
        raise ValueError(f"Допустимые значения — {', '.join(sorted(ENV_OPTIONS[key]))}.")
    if key in ID_LIST_ENV_KEYS and not re.fullmatch(r"[1-9][0-9]*(\s*[,;]\s*[1-9][0-9]*)*", value):
        raise ValueError("Введите целочисленные ID через запятую.")
    if key == "ADMIN_IDS" and any(int(item) >= 2**52 for item in re.split(r"[,;]", value)):
        raise ValueError("Укажите действительные Telegram ID администраторов.")
    if key == "BOT_TOKEN" and not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", value):
        raise ValueError("Введите токен в формате, который выдаёт BotFather.")
    if key in {"XUI_BASE_URL", "XUI_PUBLIC_SUBSCRIPTION_BASE_URL"}:
        try:
            url = urlsplit(value)
        except ValueError:
            raise ValueError("Введите полный адрес с http:// или https://.") from None
        if url.scheme not in {"http", "https"} or not url.hostname or any(char.isspace() for char in value):
            raise ValueError("Введите полный адрес с http:// или https://.")
    if key == "DB_PATH" and (value == ":memory:" or Path(value).is_dir() or not Path(value).parent.is_dir()):
        raise ValueError("Укажите путь к файлу SQLite в существующей папке.")


def save_env_value(key: str, value: str) -> None:
    validate_env_value(key, value)
    original_mode = stat.S_IMODE(ENV_FILE.stat().st_mode) if ENV_FILE.exists() else 0o600
    with NamedTemporaryFile(dir=ENV_FILE.parent, prefix=".env-edit-", delete=False) as stream:
        temporary = Path(stream.name)
    try:
        if ENV_FILE.exists():
            shutil.copyfile(ENV_FILE, temporary)
        set_key(temporary, key, value, quote_mode="always")
        if dotenv_values(temporary, interpolate=False).get(key) != value:
            raise ValueError("Формат .env не позволяет сохранить это значение без изменений.")
        os.chmod(temporary, original_mode)
        os.replace(temporary, ENV_FILE)
    finally:
        temporary.unlink(missing_ok=True)
