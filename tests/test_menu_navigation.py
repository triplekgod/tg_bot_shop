import asyncio
import sqlite3
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram import CallbackQuery, Chat, Message, ReplyKeyboardMarkup, Update, User
from telegram.error import BadRequest

from bot import database, handlers, tickets


class MenuNavigationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        self.addCleanup(connection.close)
        stack = ExitStack()
        self.addCleanup(stack.close)
        for module in (database, handlers, tickets):
            stack.enter_context(patch.object(module, "db", return_value=connection))
        database.init_db()
        stack.enter_context(patch.object(handlers, "is_admin", side_effect=lambda user_id: user_id == 1))
        stack.enter_context(patch.object(tickets, "is_admin", side_effect=lambda user_id: user_id == 1))
        self.notify = stack.enter_context(patch.object(handlers, "notify_admins_about_user_message", new_callable=AsyncMock))
        self.broadcast = stack.enter_context(patch.object(handlers, "broadcast_text_to_users", new_callable=AsyncMock))
        self.broadcast.return_value = (1, 0)
        self.delete_incoming_message = stack.enter_context(patch.object(Message, "delete", new_callable=AsyncMock))
        stack.enter_context(patch.object(CallbackQuery, "answer", new_callable=AsyncMock))
        stack.enter_context(patch.object(CallbackQuery, "edit_message_reply_markup", new_callable=AsyncMock))
        self.next_id = 100
        self.last_reply_markup = None
        self.reply_text = stack.enter_context(patch.object(Message, "reply_text", side_effect=self.send_reply))
        self.context = SimpleNamespace(
            args=[],
            user_data={},
            chat_data={},
            application=SimpleNamespace(create_task=lambda coroutine, **_: asyncio.create_task(coroutine)),
            bot=SimpleNamespace(
                username="shop_test_bot",
                delete_message=AsyncMock(),
                delete_messages=AsyncMock(),
                edit_message_reply_markup=AsyncMock(),
                edit_message_text=AsyncMock(side_effect=self.edit_reply),
                send_message=AsyncMock(return_value=SimpleNamespace(delete=AsyncMock())),
            ),
        )

    async def send_reply(self, *args, **kwargs):
        self.next_id += 1
        self.last_reply_markup = kwargs.get("reply_markup")
        return SimpleNamespace(message_id=self.next_id)

    async def edit_reply(self, **kwargs):
        self.last_reply_markup = kwargs.get("reply_markup")

    def message(self, user_id, *, text=None, message_id=1):
        user = User(user_id, "Test", is_bot=False)
        return Message(
            message_id, datetime.now(timezone.utc), Chat(user_id, "private"), from_user=user, text=text,
        )

    async def press(self, user_id, text):
        self.next_id += 1
        message = self.message(user_id, text=text, message_id=self.next_id)
        await handlers.route_message(Update(self.next_id, message=message), self.context)

    async def click(self, user_id, data, message_id=None):
        self.next_id += 1
        query = CallbackQuery(
            str(self.next_id), User(user_id, "Test", is_bot=False), "chat",
            message=self.message(user_id, message_id=message_id or self.context.chat_data["menu_screen_ids"][0]),
            data=data,
        )
        await handlers.button_callback(Update(self.next_id, callback_query=query), self.context)

    def cancel_button_data(self):
        for row in self.last_reply_markup.inline_keyboard:
            for button in row:
                if button.callback_data.startswith("input:cancel:"):
                    return button.callback_data
        self.fail("Cancel button is missing")

    def mock_subscription(self, stack):
        stack.enter_context(patch.object(handlers, "resolve_xui_email_for_user", return_value=("vpn-client", "linked")))
        panel = stack.enter_context(patch.object(handlers, "XuiClient"))
        panel.return_value.__aenter__.return_value.get_client_summary = AsyncMock(return_value={"expiry_ms": 0})

    async def test_admin_menu_discards_broadcast_and_replaces_prompt(self):
        await self.press(1, handlers.ADMIN_BUTTON_BROADCAST_ALL)
        prompt = self.context.chat_data["menu_screen_ids"][0]
        await self.press(1, handlers.ADMIN_BUTTON_HELP)
        self.assertIn("Команды администратора", self.reply_text.await_args.args[0])
        await self.press(1, "Текст после выхода из рассылки")
        self.assertNotIn("admin_broadcast_mode", self.context.user_data)
        self.context.bot.delete_message.assert_any_await(1, prompt)
        self.broadcast.assert_not_awaited()

    async def test_admin_can_switch_audience_cancel_and_send(self):
        await self.press(1, handlers.ADMIN_BUTTON_BROADCAST_ALL)
        old_cancel = self.cancel_button_data()
        old_prompt = self.context.chat_data["menu_screen_ids"][0]
        await self.press(1, handlers.ADMIN_BUTTON_BROADCAST_ACTIVE)
        await self.click(1, old_cancel, old_prompt)
        self.assertEqual(self.context.user_data["admin_broadcast_mode"], "active")
        await self.click(1, self.cancel_button_data())
        await self.press(1, "Текст после отмены")
        self.broadcast.assert_not_awaited()
        await self.press(1, "📣 Рассылка")
        await self.press(1, "Объявление")
        self.broadcast.assert_awaited_once()
        self.assertEqual(self.broadcast.await_args.args[1], "Объявление")

    async def test_client_menu_including_legacy_button_discards_ticket_input(self):
        await self.press(2, handlers.CLIENT_BUTTON_TICKET)
        await self.click(2, "clientticket:new")
        self.cancel_button_data()
        await self.press(2, "🆘 Обращение")
        await self.press(2, "Текст после выхода из обращения")
        self.notify.assert_not_awaited()
        self.assertIsNone(tickets.get_latest_open_ticket(2))

    async def test_client_can_cancel_or_send_new_ticket(self):
        await self.press(2, handlers.CLIENT_BUTTON_TICKET)
        await self.click(2, "clientticket:new")
        prompt = self.context.chat_data["menu_screen_ids"][0]
        await self.click(2, self.cancel_button_data())
        self.assertEqual(self.context.chat_data["menu_screen_ids"], [prompt])
        self.assertEqual(self.last_reply_markup.inline_keyboard[0][0].callback_data, "clientticket:new")
        await self.press(2, "Текст после отмены")
        self.assertIsNone(tickets.get_latest_open_ticket(2))
        await self.press(2, handlers.CLIENT_BUTTON_TICKET)
        await self.click(2, "clientticket:new")
        await self.press(2, "Описание проблемы")
        self.notify.assert_awaited_once()
        self.assertIsNotNone(tickets.get_latest_open_ticket(2))

    async def test_inline_navigation_discards_ticket_input(self):
        await self.press(2, handlers.CLIENT_BUTTON_TICKET)
        await self.click(2, "clientticket:new")
        prompt = self.context.chat_data["menu_screen_ids"][0]
        await self.click(2, "balance:p2p")
        self.assertNotIn("client_waiting_ticket_text", self.context.user_data)
        self.assertEqual(self.context.user_data["balance_topup_method"], "p2p")
        edit = self.context.bot.edit_message_text.await_args.kwargs
        self.assertEqual(edit["message_id"], prompt)
        self.assertIn("Введите сумму пополнения", edit["text"])
        self.notify.assert_not_awaited()

    async def test_navigation_removes_old_buttons_if_delete_fails(self):
        await self.press(2, handlers.CLIENT_BUTTON_TICKET)
        old_screen = self.context.chat_data["menu_screen_ids"][0]
        self.context.bot.edit_message_text.side_effect = BadRequest("Message to edit not found")
        self.context.bot.delete_message.side_effect = BadRequest("Message can't be deleted")
        await self.press(2, handlers.CLIENT_BUTTON_BALANCE)
        self.context.bot.edit_message_reply_markup.assert_awaited_once_with(
            chat_id=2, message_id=old_screen, reply_markup=None,
        )

    async def test_client_inline_menus_reuse_screen_and_keep_bottom_keyboard(self):
        await handlers.start(Update(1, message=self.message(2, text="/start")), self.context)
        keyboard_id = self.context.chat_data["menu_screen_ids"][0]
        self.assertTrue(self.last_reply_markup.is_persistent)

        with patch.object(handlers, "resolve_xui_email_for_user", return_value=(None, "missing")):
            await self.press(2, handlers.CLIENT_BUTTON_SUBSCRIPTION)
            inline_screen_id = self.context.chat_data["menu_screen_ids"][0]
            self.assertNotEqual(inline_screen_id, keyboard_id)
            self.context.bot.edit_message_text.side_effect = BadRequest("Message is not modified")
            await self.press(2, handlers.CLIENT_BUTTON_SUBSCRIPTION)

        self.context.bot.edit_message_text.side_effect = self.edit_reply
        await self.press(2, handlers.CLIENT_BUTTON_BALANCE)
        await self.press(2, handlers.CLIENT_BUTTON_TICKET)
        self.assertEqual(self.context.chat_data["menu_screen_ids"], [inline_screen_id])
        self.assertEqual(self.context.chat_data["menu_keyboard_id"], keyboard_id)
        self.assertEqual(self.reply_text.await_count, 2)
        self.assertEqual(self.context.bot.edit_message_text.await_count, 3)
        self.context.bot.delete_message.assert_not_awaited()

        await handlers.start(Update(2, message=self.message(2, text="/start")), self.context)
        self.context.bot.delete_messages.assert_awaited_once_with(2, sorted([inline_screen_id, keyboard_id]))

    async def test_edited_and_repeated_updates_do_not_render_twice(self):
        message = self.message(2, text=handlers.CLIENT_BUTTON_BALANCE, message_id=80)
        await handlers.route_message(Update(1, message=message), self.context)
        await handlers.route_message(Update(2, message=message), self.context)
        edited = self.message(2, text=handlers.CLIENT_BUTTON_TICKET, message_id=80)
        await handlers.route_message(Update(3, edited_message=edited), self.context)
        self.reply_text.assert_awaited_once()
        self.context.bot.edit_message_text.assert_not_awaited()

    async def test_cancel_renewal_months_returns_to_subscription_card(self):
        with ExitStack() as stack:
            self.mock_subscription(stack)
            await self.press(2, handlers.CLIENT_BUTTON_SUBSCRIPTION)
            await self.click(2, "subscription:renew")
            await self.press(2, "не число")
            await self.click(2, self.cancel_button_data())

        self.assertNotIn("client_waiting_renew_months", self.context.user_data)
        self.assertIn("Ваша подписка", self.context.bot.edit_message_text.await_args.kwargs["text"])
        self.assertEqual(self.last_reply_markup.inline_keyboard[0][0].callback_data, "subscription:renew")

    async def test_switching_menu_replaces_renewal_prompt_and_keeps_keyboard(self):
        await handlers.start(Update(1, message=self.message(2, text="/start")), self.context)
        keyboard_id = self.context.chat_data["menu_keyboard_id"]
        with ExitStack() as stack:
            self.mock_subscription(stack)
            await self.press(2, handlers.CLIENT_BUTTON_SUBSCRIPTION)
            await self.click(2, "subscription:renew")

        prompt_id = self.context.chat_data["menu_screen_ids"][0]
        await self.press(2, handlers.CLIENT_BUTTON_BALANCE)
        self.assertNotIn("client_waiting_renew_months", self.context.user_data)
        edit = self.context.bot.edit_message_text.await_args.kwargs
        self.assertEqual(edit["message_id"], prompt_id)
        self.assertIn("Ваш баланс", edit["text"])
        self.assertEqual(self.context.chat_data["menu_keyboard_id"], keyboard_id)
        self.context.bot.delete_message.assert_not_awaited()

    async def test_cancel_renewal_payment_returns_to_months_and_invalidates_old_payment(self):
        with ExitStack() as stack:
            self.mock_subscription(stack)
            pay = stack.enter_context(patch.object(handlers, "process_client_renewal_payment_choice", new_callable=AsyncMock))
            await self.press(2, handlers.CLIENT_BUTTON_SUBSCRIPTION)
            await self.click(2, "subscription:renew")
            await self.press(2, "3")
            old_payment = self.last_reply_markup.inline_keyboard[0][0].callback_data

            await self.click(2, self.cancel_button_data())
            self.assertIn("На сколько месяцев", self.context.bot.edit_message_text.await_args.kwargs["text"])
            self.assertNotIn("client_renew_months", self.context.user_data)
            await self.press(2, "3")
            new_payment = self.last_reply_markup.inline_keyboard[0][0].callback_data
            await self.click(2, old_payment)
            pay.assert_not_awaited()
            await self.click(2, new_payment)

        pay.assert_awaited_once()
        self.assertEqual(pay.await_args.args[2:], (3, "balance"))

    async def test_cancel_topup_returns_to_balance_after_invalid_amount(self):
        await self.press(2, handlers.CLIENT_BUTTON_BALANCE)
        await self.click(2, "balance:p2p")
        old_cancel = self.cancel_button_data()
        await self.press(2, "не сумма")
        await self.click(2, old_cancel)
        self.assertEqual(self.context.user_data["balance_topup_method"], "p2p")
        await self.click(2, self.cancel_button_data())
        self.assertNotIn("balance_topup_method", self.context.user_data)
        self.assertIn("Ваш баланс", self.context.bot.edit_message_text.await_args.kwargs["text"])
        self.assertEqual(self.last_reply_markup.inline_keyboard[0][0].callback_data, "balance:p2p")
        with database.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM balance_topups").fetchone()[0], 0)

    async def test_referrals_screen_is_replaced_without_removing_main_keyboard(self):
        await handlers.start(Update(1, message=self.message(2, text="/start")), self.context)
        keyboard_id = self.context.chat_data["menu_keyboard_id"]
        await self.press(2, handlers.CLIENT_BUTTON_REFERRALS)
        referral_id = self.context.chat_data["menu_screen_ids"][0]
        self.assertNotEqual(keyboard_id, referral_id)
        self.assertIsNone(self.last_reply_markup)
        await self.press(2, handlers.CLIENT_BUTTON_BALANCE)
        self.assertEqual(self.context.chat_data["menu_screen_ids"], [referral_id])
        self.assertEqual(self.context.chat_data["menu_keyboard_id"], keyboard_id)
        self.assertIn("Ваш баланс", self.context.bot.edit_message_text.await_args.kwargs["text"])
        self.context.bot.delete_message.assert_not_awaited()

    async def test_topup_confirmation_is_shown_while_admin_notification_is_pending(self):
        await self.press(2, handlers.CLIENT_BUTTON_BALANCE)
        await self.click(2, "balance:p2p")
        notification_started = asyncio.Event()
        finish_notification = asyncio.Event()

        async def notify_admin(**kwargs):
            notification_started.set()
            await finish_notification.wait()

        self.context.bot.send_message = AsyncMock(side_effect=notify_admin)
        with patch.object(handlers, "get_admin_ids_for_notification", return_value=[1]):
            submit = asyncio.create_task(self.press(2, str(handlers.BALANCE_MIN_TOPUP_RUB)))
            try:
                await asyncio.wait_for(notification_started.wait(), 1)
                self.assertIn("Заявка на пополнение #", self.context.bot.edit_message_text.await_args.kwargs["text"])
            finally:
                finish_notification.set()
                await submit

        with database.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM balance_topups").fetchone()[0], 1)

    async def test_slow_action_does_not_block_navigation_in_another_chat(self):
        processor = handlers.ChatUpdateProcessor()
        started = asyncio.Event()
        finish = asyncio.Event()
        same_chat_ran = asyncio.Event()

        async def slow_action():
            started.set()
            await finish.wait()

        async def navigate_same_chat():
            same_chat_ran.set()

        slow_update = Update(1, message=self.message(2, text="подтверждение"))
        same_chat = Update(2, message=self.message(2, text=handlers.CLIENT_BUTTON_BALANCE))
        other_chat = Update(3, message=self.message(3, text=handlers.CLIENT_BUTTON_BALANCE, message_id=3))
        slow = asyncio.create_task(processor.process_update(slow_update, slow_action()))
        await started.wait()
        queued = asyncio.create_task(processor.process_update(same_chat, navigate_same_chat()))
        try:
            await asyncio.wait_for(processor.process_update(other_chat, handlers.route_message(other_chat, self.context)), 1)
            self.assertIn("Ваш баланс", self.reply_text.await_args.args[0])
            self.assertFalse(same_chat_ran.is_set())
        finally:
            finish.set()
            await asyncio.gather(slow, queued)
        self.assertTrue(same_chat_ran.is_set())

    async def test_leaving_connection_guide_removes_all_copies_and_confirmation(self):
        await handlers.start(Update(1, message=self.message(2, text="/start")), self.context)
        keyboard_id = self.context.chat_data["menu_keyboard_id"]
        copied = [200, 201, 202]
        with (
            patch.object(handlers, "count_subscription_welcome_messages", return_value=3),
            patch.object(handlers, "send_subscription_welcome_messages", return_value=copied),
        ):
            await self.press(2, handlers.CLIENT_BUTTON_CONNECTION_GUIDE)
        guide_ids = self.context.chat_data["menu_screen_ids"].copy()
        self.assertEqual(guide_ids[:-1], copied)
        self.assertIsNone(self.last_reply_markup)
        await self.press(2, handlers.CLIENT_BUTTON_BALANCE)
        self.context.bot.delete_messages.assert_awaited_once_with(2, sorted(guide_ids))
        self.assertEqual(self.context.chat_data["menu_keyboard_id"], keyboard_id)

    async def test_client_ticket_history_has_its_own_screen_and_paginates(self):
        await self.press(2, handlers.CLIENT_BUTTON_TICKET)
        buttons = [row[0] for row in self.last_reply_markup.inline_keyboard]
        self.assertEqual([button.text for button in buttons], ["Новое обращение", "История обращений"])
        for _ in range(11):
            tickets.create_ticket(2)
        await self.press(3, handlers.CLIENT_BUTTON_TICKET)
        other_ticket = tickets.create_ticket(3)
        await self.press(2, handlers.CLIENT_BUTTON_TICKET)
        await self.click(2, buttons[1].callback_data)
        first_page = [button.callback_data for row in self.last_reply_markup.inline_keyboard for button in row]
        self.assertEqual(sum(value.startswith("clientticket:view:") for value in first_page), 10)
        self.assertNotIn(f"clientticket:view:{other_ticket}:1", first_page)
        await self.click(2, "clientticket:history:2")
        own_ticket = self.last_reply_markup.inline_keyboard[0][0].callback_data
        self.assertTrue(own_ticket.endswith(":2"))
        await self.click(2, own_ticket)
        self.assertEqual(self.last_reply_markup.inline_keyboard[0][0].callback_data, "clientticket:history:2")

    async def test_admin_navigation_uses_bottom_keyboard_without_echoing_buttons(self):
        await handlers.start(Update(1, message=self.message(1, text="/start")), self.context)
        self.assertIsInstance(self.last_reply_markup, ReplyKeyboardMarkup)
        self.assertTrue(self.last_reply_markup.is_persistent)
        self.assertEqual(self.last_reply_markup.keyboard[0][1].text, handlers.ADMIN_BUTTON_TICKETS_GROUP)
        await self.press(1, handlers.ADMIN_BUTTON_TICKETS_GROUP)
        self.assertIsInstance(self.last_reply_markup, ReplyKeyboardMarkup)
        self.assertEqual(self.last_reply_markup.keyboard[-1][0].text, handlers.ADMIN_BUTTON_BACK)
        self.assertNotEqual(self.reply_text.await_args.args[0], handlers.ADMIN_BUTTON_TICKETS_GROUP)
        section_id = self.context.chat_data["menu_keyboard_id"]

        await self.press(1, handlers.ADMIN_BUTTON_NEW_CLIENT_MESSAGES)
        self.assertIn("Сообщения новым клиентам", self.reply_text.await_args.args[0])
        self.assertEqual(self.context.chat_data["menu_keyboard_id"], section_id)
        self.assertFalse(any(
            button.callback_data.startswith("adminmenu:")
            for row in self.last_reply_markup.inline_keyboard for button in row
        ))

        await self.press(1, handlers.ADMIN_BUTTON_BACK)
        self.assertIsInstance(self.last_reply_markup, ReplyKeyboardMarkup)
        self.assertEqual(self.last_reply_markup.keyboard[0][1].text, handlers.ADMIN_BUTTON_TICKETS_GROUP)
        await asyncio.sleep(0)
        self.assertEqual(self.delete_incoming_message.await_count, 3)

    async def test_client_cannot_use_admin_menu_callback(self):
        await self.press(2, handlers.CLIENT_BUTTON_TICKET)
        await self.click(2, "adminmenu:broadcast-all")
        self.assertNotIn("admin_broadcast_mode", self.context.user_data)
        self.reply_text.assert_awaited_once()
        self.context.bot.edit_message_text.assert_not_awaited()
