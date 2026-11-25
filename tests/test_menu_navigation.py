import sqlite3
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram import CallbackQuery, Chat, Message, Update, User
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
        self.notify = stack.enter_context(patch.object(handlers, "notify_admins_about_user_message", new_callable=AsyncMock))
        self.broadcast = stack.enter_context(patch.object(handlers, "broadcast_text_to_users", new_callable=AsyncMock))
        self.broadcast.return_value = (1, 0)
        stack.enter_context(patch.object(Message, "delete", new_callable=AsyncMock))
        stack.enter_context(patch.object(CallbackQuery, "answer", new_callable=AsyncMock))
        stack.enter_context(patch.object(CallbackQuery, "edit_message_reply_markup", new_callable=AsyncMock))
        self.next_id = 100
        self.last_reply_markup = None
        self.reply_text = stack.enter_context(patch.object(Message, "reply_text", side_effect=self.send_reply))
        self.context = SimpleNamespace(
            args=[],
            user_data={},
            chat_data={},
            bot=SimpleNamespace(
                delete_message=AsyncMock(),
                edit_message_reply_markup=AsyncMock(),
                edit_message_text=AsyncMock(side_effect=self.edit_reply),
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
        data = self.last_reply_markup.inline_keyboard[-1][0].callback_data
        self.assertTrue(data.startswith("input:cancel:"))
        return data

    def mock_subscription(self, stack):
        stack.enter_context(patch.object(handlers, "resolve_xui_email_for_user", return_value=("vpn-client", "linked")))
        panel = stack.enter_context(patch.object(handlers, "XuiClient"))
        panel.return_value.__aenter__.return_value.get_client_summary = AsyncMock(return_value={"expiry_ms": 0})

    async def test_admin_menu_discards_broadcast_and_replaces_prompt(self):
        await self.press(1, handlers.ADMIN_BUTTON_BROADCAST_ALL)
        prompt = self.context.chat_data["menu_screen_ids"][0]
        await self.press(1, handlers.ADMIN_BUTTON_HELP)
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
        self.context.bot.delete_message.assert_any_await(2, prompt)
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
        self.context.bot.delete_message.assert_any_await(2, prompt)
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
        self.context.bot.delete_message.assert_any_await(2, keyboard_id)
        self.context.bot.delete_message.assert_any_await(2, inline_screen_id)

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
