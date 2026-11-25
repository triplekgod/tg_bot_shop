import asyncio
import sqlite3
import unittest
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, Mock, patch
from zipfile import ZipFile

from dotenv import dotenv_values

from bot import backup, env_settings


class BackupTests(unittest.IsolatedAsyncioTestCase):
    async def test_daily_backup_sends_consistent_wal_snapshot_to_super_admin(self):
        with TemporaryDirectory() as directory:
            source_path = Path(directory) / "live.sqlite3"
            received = Path(directory) / "received.sqlite3"
            with closing(sqlite3.connect(source_path)) as source:
                source.execute("PRAGMA journal_mode=WAL")
                source.execute("CREATE TABLE demo(value TEXT)")
                source.execute("INSERT INTO demo VALUES ('committed')")
                source.commit()
                self.assertTrue(source_path.with_suffix(".sqlite3-wal").exists())

                async def receive_document(*, document, **kwargs):
                    self.assertEqual(kwargs["chat_id"], 42)
                    self.assertEqual(document.filename, "support-bot-2026-11-25.sqlite3.zip")
                    with ZipFile(document.input_file_content) as archive:
                        received.write_bytes(archive.read("support-bot-2026-11-25.sqlite3"))

                bot = Mock(send_document=AsyncMock(side_effect=receive_document))
                with patch.multiple(backup, DB_PATH=str(source_path), SUPER_ADMIN_ID=42):
                    await backup.send_daily_backup(bot, date(2026, 11, 25))

            with closing(sqlite3.connect(received)) as snapshot:
                self.assertEqual(snapshot.execute("PRAGMA integrity_check").fetchone(), ("ok",))
                self.assertEqual(snapshot.execute("SELECT value FROM demo").fetchone(), ("committed",))

    def test_midnight_moscow_is_21_utc_on_previous_day(self):
        now = datetime(2026, 11, 25, 23, 59, tzinfo=backup.MOSCOW_TIME)
        midnight = backup.next_midnight(now)
        self.assertEqual(midnight - now, timedelta(minutes=1))
        self.assertEqual(midnight.astimezone(timezone.utc), datetime(2026, 11, 25, 21, tzinfo=timezone.utc))

    async def test_scheduler_delivers_unsent_day_and_skips_delivered_day(self):
        now = datetime(2026, 11, 25, tzinfo=backup.MOSCOW_TIME)
        clock = Mock(wraps=datetime)
        clock.now.return_value = now
        for last_day in (None, date(2026, 11, 24), now.date()):
            with (
                self.subTest(last_day=last_day),
                patch.multiple(backup, datetime=clock, SUPER_ADMIN_ID=42),
                patch.object(backup, "backup_date", return_value=last_day),
                patch.object(backup, "save_backup_date") as record,
                patch.object(backup, "send_daily_backup", new_callable=AsyncMock) as send,
                patch.object(backup.asyncio, "sleep", side_effect=asyncio.CancelledError),
            ):
                bot = Mock()
                with self.assertRaises(asyncio.CancelledError):
                    await backup.run_daily_backups(bot)
                if last_day is not None and last_day < now.date():
                    send.assert_awaited_once_with(bot, now.date())
                else:
                    send.assert_not_awaited()
                if last_day != now.date():
                    record.assert_called_once_with(now.date())
                else:
                    record.assert_not_called()


class EnvSettingsTests(unittest.TestCase):
    def test_all_supported_settings_have_descriptions(self):
        self.assertEqual(set(env_settings.EDITABLE_ENV_KEYS), env_settings.ENV_DESCRIPTIONS.keys())
        self.assertTrue(all(env_settings.ENV_DESCRIPTIONS.values()))

    def test_all_supported_settings_belong_to_one_group(self):
        keys = [key for _, group_keys in env_settings.ENV_SETTING_GROUPS.values() for key in group_keys]
        self.assertCountEqual(keys, env_settings.EDITABLE_ENV_KEYS)

    def test_custom_descriptions_use_adjacent_comments_above_setting(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "MULTILINE='value\n# Часть значения, а не комментарий'\n"
                "# Описание дополнительной настройки.\n# Продолжение.\nCUSTOM_KEY=yes\n"
                "# Заголовок раздела\n\nUNCOMMENTED=yes\n"
                "# Локальный комментарий\nXUI_TIMEOUT=20\n",
                encoding="utf-8",
            )
            with patch.object(env_settings, "ENV_FILE", path):
                descriptions = env_settings.env_setting_descriptions(["CUSTOM_KEY", "UNCOMMENTED", "XUI_TIMEOUT"])
        self.assertEqual(descriptions["CUSTOM_KEY"], "Описание дополнительной настройки. Продолжение.")
        self.assertIn("Назначение не указано", descriptions["UNCOMMENTED"])
        self.assertEqual(descriptions["XUI_TIMEOUT"], env_settings.ENV_DESCRIPTIONS["XUI_TIMEOUT"])

    def test_saving_changes_only_selected_setting_and_retains_credential_value(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("# Данные\nXUI_API_TOKEN=original\nEXTERNAL_KEY=yes\n", encoding="utf-8")
            with patch.object(env_settings, "ENV_FILE", path):
                env_settings.save_env_value("XUI_API_TOKEN", "new 'secret' # value")
                env_settings.save_env_value("BALANCE_PRICE_PER_MONTH", "320")
                values = env_settings.env_settings_values()
                self.assertEqual(env_settings.env_display_value(values["XUI_API_TOKEN"]), "new 'secret' # value")
                self.assertEqual(env_settings.env_display_value(values["BALANCE_PRICE_PER_MONTH"]), "320")
                self.assertEqual(values["EXTERNAL_KEY"], "yes")
                env_settings.save_env_value("EXTERNAL_KEY", "no")
            self.assertEqual(dotenv_values(path)["XUI_API_TOKEN"], "new 'secret' # value")
            self.assertEqual(dotenv_values(path)["EXTERNAL_KEY"], "no")
            self.assertIn("# Данные", path.read_text(encoding="utf-8"))

    def test_invalid_values_do_not_change_file(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            original = "REFERRAL_PERCENT=25\n"
            path.write_text(original, encoding="utf-8")
            with patch.object(env_settings, "ENV_FILE", path):
                for key, value in (
                    ("REFERRAL_PERCENT", "101"), ("XUI_TIMEOUT", "NaN"),
                    ("XUI_PASSWORD", "one\nBOT_TOKEN=two"), ("XUI_PASSWORD", "${BOT_TOKEN}"),
                    ("UNSUPPORTED", "1"),
                ):
                    with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                        env_settings.save_env_value(key, value)
                self.assertEqual(path.read_text(encoding="utf-8"), original)
