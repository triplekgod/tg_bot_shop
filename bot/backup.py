import asyncio
import sqlite3
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

from telegram import Bot, InputFile
from telegram.error import TelegramError

from .config import DB_PATH, SUPER_ADMIN_ID, logger
from .database import db, now_iso


MOSCOW_TIME = timezone(timedelta(hours=3), "MSK")


def backup_date() -> date | None:
    with closing(db()) as connection:
        row = connection.execute(
            "SELECT setting_value FROM bot_settings WHERE setting_key = 'daily_backup_date'"
        ).fetchone()
    return date.fromisoformat(row["setting_value"]) if row else None


def save_backup_date(day: date) -> None:
    with closing(db()) as connection, connection:
        connection.execute(
            """INSERT INTO bot_settings(setting_key, setting_value, updated_at)
            VALUES ('daily_backup_date', ?, ?)
            ON CONFLICT(setting_key) DO UPDATE SET
                setting_value = excluded.setting_value, updated_at = excluded.updated_at""",
            (day.isoformat(), now_iso()),
        )


def make_archive(source_path: Path, archive_path: Path) -> None:
    snapshot = archive_path.with_suffix("")
    source_uri = f"{source_path.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(source_uri, uri=True)) as source:
        with closing(sqlite3.connect(snapshot)) as destination:
            source.backup(destination)
    with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
        archive.write(snapshot, arcname=snapshot.name)


async def send_daily_backup(bot: Bot, day: date) -> None:
    filename = f"support-bot-{day.isoformat()}.sqlite3.zip"
    with TemporaryDirectory(prefix="support-bot-backup-") as directory:
        archive_path = Path(directory) / filename
        snapshot_task = asyncio.create_task(asyncio.to_thread(make_archive, Path(DB_PATH), archive_path))
        try:
            await asyncio.shield(snapshot_task)
        except asyncio.CancelledError:
            await asyncio.gather(snapshot_task, return_exceptions=True)
            raise
        with archive_path.open("rb") as stream:
            await bot.send_document(
                chat_id=SUPER_ADMIN_ID,
                document=InputFile(stream, filename=filename, read_file_handle=False),
                caption=f"Резервная копия базы SQLite · {day:%d.%m.%Y} (МСК)",
                read_timeout=120,
                write_timeout=120,
            )


def next_midnight(now: datetime) -> datetime:
    return datetime.combine(now.date() + timedelta(days=1), time.min, MOSCOW_TIME)


async def run_daily_backups(bot: Bot) -> None:
    if not SUPER_ADMIN_ID:
        logger.warning("Ежедневный бэкап не настроен: укажите SUPER_ADMIN_ID")
        return

    while True:
        now = datetime.now(MOSCOW_TIME)
        try:
            last_day = backup_date()
            if last_day is None:
                # Start the daily schedule at the next Moscow midnight on first launch.
                save_backup_date(now.date())
            elif last_day < now.date():
                await send_daily_backup(bot, now.date())
                save_backup_date(now.date())
        except (OSError, sqlite3.Error, TelegramError, ValueError) as exc:
            logger.warning("Ежедневный бэкап не завершён (%s). Повтор через 5 минут.", type(exc).__name__)
            await asyncio.sleep(300)
            continue
        delay = (next_midnight(now) - datetime.now(MOSCOW_TIME)).total_seconds()
        await asyncio.sleep(max(0, delay))
