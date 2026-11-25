import asyncio
import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram import Chat, Message, PreCheckoutQuery, SuccessfulPayment, Update, User

from bot import database, handlers, tickets, xui


class DatabaseIntegrityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.enter_patch(patch.object(database, "DB_PATH", str(Path(temp.name) / "test.sqlite3")))
        database.init_db()
        self.user = User(10, "Client", False)
        database.upsert_user(self.user)
        database.upsert_user(User(20, "Referrer", False))

    def enter_patch(self, patcher):
        self.addCleanup(patcher.stop)
        return patcher.start()

    async def test_confirm_topup_once_across_concurrent_connections(self):
        database.set_referrer(10, 20)
        topup = database.create_balance_topup(10, 200, "manual")
        with patch.object(database, "REFERRAL_PERCENT", 25):
            outcomes = await asyncio.gather(*(asyncio.to_thread(database.confirm_balance_topup, topup, 123) for _ in range(4)))
        self.assertEqual(sum(row is not None for row in outcomes), 1)
        self.assertEqual(database.balance_of(10), 200)
        self.assertEqual(database.balance_of(20), 50)

    async def test_cannot_spend_the_same_balance_twice(self):
        database.add_balance_transaction(10, 300, "topup")
        outcomes = await asyncio.gather(*(asyncio.to_thread(database.spend_balance, 10, 200, "test") for _ in range(4)))
        self.assertEqual(outcomes.count(True), 1)
        self.assertEqual(database.balance_of(10), 100)

    def test_stars_topup_checks_payer_amount_method_and_charge_before_writing(self):
        first = database.create_balance_topup(10, 100, "stars")
        second = database.create_balance_topup(10, 100, "stars")
        manual = database.create_balance_topup(10, 100, "manual")
        self.assertIsNone(database.confirm_balance_topup(first, 0, "charge", stars_payment=(20, 100)))
        self.assertIsNone(database.confirm_balance_topup(first, 0, "charge", stars_payment=(10, 99)))
        self.assertIsNone(database.confirm_balance_topup(manual, 0, "charge", stars_payment=(10, 100)))
        self.assertIsNotNone(database.confirm_balance_topup(first, 0, "charge", stars_payment=(10, 100)))
        self.assertIsNone(database.confirm_balance_topup(second, 0, "charge", stars_payment=(10, 100)))
        self.assertEqual(database.balance_of(10), 100)

    def test_welcome_message_can_be_recorded_and_connection_closes(self):
        message = Message(1, datetime.now(timezone.utc), Chat(10, "private"), from_user=self.user, text="Инструкция")
        database.add_subscription_welcome_message(10, message)
        self.assertEqual(database.list_subscription_welcome_messages()[0]["content_type"], "text")
        with database.db() as connection:
            self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")

    async def test_stars_receipt_does_not_repeat_3x_ui_action(self):
        operations = (
            ("renewal", tickets.create_renewal_request, "try_auto_renew_after_stars_payment"),
            ("subscription", tickets.create_subscription_request, "try_auto_create_subscription_after_stars_payment"),
        )
        for kind, create_request, action_name in operations:
            with self.subTest(kind=kind):
                ticket_id = tickets.create_ticket(10)
                create_request(ticket_id, 10, "test", 1, 30, payment_method="stars")
                invoice = tickets.make_stars_payload(ticket_id, 50, kind)
                payment = SuccessfulPayment("XTR", 50, invoice, f"charge-{kind}", "provider")
                message = Message(
                    ticket_id, datetime.now(timezone.utc), Chat(10, "private"),
                    from_user=self.user, successful_payment=payment,
                )
                update = Update(ticket_id, message=message)
                with patch.object(handlers, action_name, new_callable=AsyncMock) as apply, patch.object(handlers, "log_message") as log:
                    await handlers.successful_payment_handler(update, SimpleNamespace())
                    await handlers.successful_payment_handler(update, SimpleNamespace())
                apply.assert_awaited_once()
                log.assert_called_once()
                precheckout = PreCheckoutQuery("test", self.user, "XTR", 50, invoice)
                with patch.object(PreCheckoutQuery, "answer", new_callable=AsyncMock) as answer:
                    await handlers.precheckout_callback(Update(ticket_id, pre_checkout_query=precheckout), SimpleNamespace())
                answer.assert_awaited_once_with(ok=False, error_message="Эта заявка уже оплачена.")

    async def test_stars_precheckout_does_not_wait_for_global_action_lock(self):
        processor = handlers.ChatUpdateProcessor()
        precheckout = PreCheckoutQuery("test", self.user, "XTR", 50, "renewal:1:50")
        answer = AsyncMock()
        async with processor.action_lock:
            await asyncio.wait_for(processor.process_update(Update(1, pre_checkout_query=precheckout), answer()), 1)
        answer.assert_awaited_once()

    async def test_xui_connection_is_closed_when_login_fails(self):
        client = xui.XuiClient.__new__(xui.XuiClient)
        client._logged_in = False
        client._client = SimpleNamespace(aclose=AsyncMock())
        with patch.object(client, "login", new_callable=AsyncMock, side_effect=xui.XuiApiError("login failed")):
            with self.assertRaises(xui.XuiApiError):
                async with client:
                    self.fail("The client must not enter the context after login fails")
        client._client.aclose.assert_awaited_once()
