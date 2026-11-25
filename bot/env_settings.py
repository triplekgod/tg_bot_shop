import math
import os
import re
import shutil
import stat
from collections.abc import Iterable
from hashlib import blake2s
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

from dotenv import dotenv_values, set_key
from dotenv.parser import parse_stream

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

ENV_SETTING_GROUPS = {
    "bot": ("Бот, администраторы и база", (
        "BOT_TOKEN", "ADMIN_IDS", "SUPER_ADMIN_ID", "DB_PATH",
    )),
    "panel": ("Подключение к 3x-ui", (
        "XUI_BASE_URL", "XUI_API_TOKEN", "XUI_USERNAME", "XUI_PASSWORD", "XUI_VERIFY_SSL", "XUI_TIMEOUT",
    )),
    "clients": ("Клиенты 3x-ui", (
        "XUI_CLIENTS_PER_PAGE", "XUI_GROUP_CLIENTS_BY_EMAIL", "XUI_SYNC_TGID_TO_PANEL_ON_LINK",
        "XUI_NEW_CLIENT_INBOUND_IDS", "XUI_NEW_CLIENT_EMAIL_PREFIX", "XUI_NEW_CLIENT_FLOW",
        "XUI_NEW_CLIENT_GROUP", "XUI_NEW_CLIENT_COMMENT", "XUI_NEW_CLIENT_LIMIT_IP", "XUI_NEW_CLIENT_TOTAL_GB",
    )),
    "subscriptions": ("Подписки и продление", (
        "XUI_RENEW_MODE", "XUI_CLIENTS_RENEW_METHOD", "XUI_RENEW_EXPIRED_FROM_NOW", "XUI_MAIN_INBOUND_ID",
        "XUI_DEFAULT_RENEW_DAYS", "XUI_DAYS_PER_MONTH", "XUI_MAX_RENEW_MONTHS", "XUI_RESTART_XRAY_AFTER_RENEW",
        "XUI_PUBLIC_SUBSCRIPTION_BASE_URL", "XUI_SUBSCRIPTION_LINK_TEMPLATE",
    )),
    "stars": ("Telegram Stars", (
        "STARS_PAYMENTS_ENABLED", "STARS_PRICE_PER_MONTH", "STARS_MIN_AMOUNT", "STARS_MAX_AMOUNT",
        "STARS_INVOICE_TITLE", "STARS_INVOICE_DESCRIPTION",
    )),
    "balance": ("Баланс и рефералы", (
        "BALANCE_PRICE_PER_MONTH", "BALANCE_MIN_TOPUP_RUB", "BALANCE_MAX_TOPUP_RUB",
        "CRYPTO_TOPUP_ENABLED", "REFERRAL_PERCENT",
    )),
    "other": ("Дополнительные переменные", ()),
}
ENV_SETTING_GROUP_BY_KEY = {
    key: group for group, (_, keys) in ENV_SETTING_GROUPS.items() for key in keys
}

ENV_DESCRIPTIONS = {
    "ADMIN_IDS": "Telegram ID администраторов, которых бот добавляет при запуске. Укажите ID через запятую.",
    "BALANCE_MAX_TOPUP_RUB": "Максимальная сумма одной заявки на пополнение баланса, в рублях.",
    "BALANCE_MIN_TOPUP_RUB": "Минимальная сумма одной заявки на пополнение баланса, в рублях.",
    "BALANCE_PRICE_PER_MONTH": "Цена месяца подписки в рублях при оплате с баланса. Индивидуальная цена клиента имеет приоритет.",
    "BOT_TOKEN": "Токен Telegram-бота, выданный @BotFather. Определяет, от имени какого бота работает служба.",
    "CRYPTO_TOPUP_ENABLED": "Показывать клиентам кнопку пополнения баланса криптовалютой.",
    "DB_PATH": "Путь к файлу SQLite с данными бота. При изменении пути существующая база не переносится.",
    "REFERRAL_PERCENT": "Процент подтверждённого пополнения реферала, который начисляется пригласившему. От 0 до 100.",
    "STARS_INVOICE_DESCRIPTION": "Описание, которое клиент увидит в счёте Telegram Stars на продление.",
    "STARS_INVOICE_TITLE": "Заголовок счёта Telegram Stars на продление.",
    "STARS_MAX_AMOUNT": "Максимальная допустимая сумма счёта в Telegram Stars.",
    "STARS_MIN_AMOUNT": "Минимальная допустимая сумма счёта в Telegram Stars.",
    "STARS_PAYMENTS_ENABLED": "Разрешить оплату через Telegram Stars.",
    "STARS_PRICE_PER_MONTH": "Цена месяца подписки в Telegram Stars. При 0 бот скрывает этот способ оплаты.",
    "SUPER_ADMIN_ID": "Telegram ID главного администратора. Ему доступны редактор .env и ежедневные бэкапы.",
    "XUI_API_TOKEN": "API-токен 3x-ui. Если он задан, бот авторизуется по токену.",
    "XUI_BASE_URL": "Адрес панели 3x-ui. Включите порт и путь к панели, если они настроены.",
    "XUI_CLIENTS_PER_PAGE": "Количество клиентов 3x-ui на странице команды /clients.",
    "XUI_CLIENTS_RENEW_METHOD": "Способ продления в Clients API. bulk_adjust добавляет дни, update меняет дату окончания.",
    "XUI_DAYS_PER_MONTH": "Сколько дней добавляет бот за один месяц подписки.",
    "XUI_DEFAULT_RENEW_DAYS": "Срок продления по умолчанию, если в команде администратора не указано число дней.",
    "XUI_GROUP_CLIENTS_BY_EMAIL": "Показывать один раз клиентов с одинаковым email, привязанных к нескольким inbounds.",
    "XUI_MAIN_INBOUND_ID": "ID основного inbound для создания и продления клиентов в режиме legacy_inbound.",
    "XUI_MAX_RENEW_MONTHS": "Максимальный срок в месяцах, который клиент может выбрать при оформлении или продлении.",
    "XUI_NEW_CLIENT_COMMENT": "Комментарий, который бот записывает новому клиенту 3x-ui через Clients API.",
    "XUI_NEW_CLIENT_EMAIL_PREFIX": "Префикс email, который бот генерирует для нового клиента 3x-ui.",
    "XUI_NEW_CLIENT_FLOW": "Значение flow для нового клиента, например xtls-rprx-vision. Пустое значение оставляет поле незаданным.",
    "XUI_NEW_CLIENT_GROUP": "Группа, которую бот указывает при создании клиента через Clients API.",
    "XUI_NEW_CLIENT_INBOUND_IDS": "ID inbounds, к которым бот прикрепляет новых клиентов. Укажите ID через запятую.",
    "XUI_NEW_CLIENT_LIMIT_IP": "Лимит IP-адресов нового клиента 3x-ui. При 0 лимит отключён.",
    "XUI_NEW_CLIENT_TOTAL_GB": "Лимит трафика нового клиента в байтах. При 0 трафик не ограничен.",
    "XUI_PASSWORD": "Пароль учётной записи панели 3x-ui для входа без API-токена.",
    "XUI_PUBLIC_SUBSCRIPTION_BASE_URL": "Публичный адрес сервера подписок. Если он пуст, бот использует XUI_BASE_URL.",
    "XUI_RENEW_EXPIRED_FROM_NOW": "В режиме bulk_adjust значение true продлевает истёкшие подписки от текущего времени, false — от прежней даты окончания.",
    "XUI_RENEW_MODE": "Режим API для работы с клиентами. clients_api подходит для 3x-ui Node, legacy_inbound — для старых панелей.",
    "XUI_RESTART_XRAY_AFTER_RENEW": "Перезапускать Xray после создания или продления клиента через 3x-ui.",
    "XUI_SUBSCRIPTION_LINK_TEMPLATE": "Шаблон ссылки подписки. Например, {base}/sub/{subId}, где подставляются адрес и ID подписки.",
    "XUI_SYNC_TGID_TO_PANEL_ON_LINK": "Записывать Telegram ID в поле tgId панели 3x-ui при ручной привязке клиента.",
    "XUI_TIMEOUT": "Тайм-аут одного запроса к 3x-ui, в секундах.",
    "XUI_USERNAME": "Логин учётной записи панели 3x-ui для входа без API-токена.",
    "XUI_VERIFY_SSL": "Проверять TLS-сертификат панели при подключении бота к 3x-ui.",
}


def env_setting_descriptions(keys: Iterable[str]) -> dict[str, str]:
    descriptions = {}
    custom_keys = set()
    for key in keys:
        if key in ENV_DESCRIPTIONS:
            descriptions[key] = ENV_DESCRIPTIONS[key]
        else:
            custom_keys.add(key)
    if not custom_keys:
        return descriptions

    fallback = "Дополнительная переменная из .env. Назначение не указано в комментарии."
    descriptions.update(dict.fromkeys(custom_keys, fallback))
    if not ENV_FILE.exists():
        return descriptions

    comments = []
    with ENV_FILE.open(encoding="utf-8") as source:
        for binding in parse_stream(source):
            original = binding.original.string
            if binding.key is None and not binding.error:
                for line in original.splitlines():
                    line = line.strip()
                    comment = line.removeprefix("#").strip()
                    if line.startswith("#") and comment and not set(comment) <= {"-", "=", "#"}:
                        comments.append(comment)
                    else:
                        comments.clear()
                continue
            if binding.key in custom_keys:
                separated = re.match(r"^[ \t]*[\r\n]", original)
                descriptions[binding.key] = " ".join(comments) if comments and not separated else fallback
            comments.clear()
    return descriptions


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
