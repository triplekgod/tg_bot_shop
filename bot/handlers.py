from .config import *
from .database import *
from .xui import *
from .tickets import *

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
            "/subscriptionevents — оформления и продления\n"
            "/renewals, /subscriptions — старые раздельные списки\n/submessages — сообщения, которые отправляются новым клиентам\n"
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
            "/subscriptionevents — оформления и продления с кнопками\n"
            "/renewals, /subscriptions — старые раздельные списки\n/submessages — настроить сообщения новым клиентам\n"
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
            "/setbalance USER_ID СУММА — установить баланс в рублях\n"
            "/setreferrer USER_ID REFERRER_ID — изменить реферала, 0 — удалить\n"
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

    async def deliver(admin_id: int) -> None:
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
    await asyncio.gather(*(deliver(admin_id) for admin_id in get_admin_ids_for_notification("tickets")))


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

    async def deliver(admin_id: int) -> None:
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
    await asyncio.gather(*(deliver(admin_id) for admin_id in get_admin_ids_for_notification("requests")))


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

    async def deliver(admin_id: int) -> None:
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
    await asyncio.gather(*(deliver(admin_id) for admin_id in get_admin_ids_for_notification("payments")))


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
        cost = months * user_monthly_price(user.id)
        if not xui_email:
            await message.reply_text(
                "Подписка не привязана к вашему Telegram ID. Администратор должен привязать её командой /linksub, после этого продление с баланса станет доступно.",
                reply_markup=client_main_keyboard(),
            )
            return
        if balance_of(user.id) < cost:
            await message.reply_text(f"Недостаточно средств: нужно {cost} ₽, доступно {balance_of(user.id)} ₽. Пополните баланс в разделе «💰 Баланс».", reply_markup=client_main_keyboard())
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
        await message.reply_text(f"✅ Подписка продлена на {renew_days} дн. Списано {cost} ₽. Остаток: {balance_of(user.id)} ₽.", reply_markup=client_main_keyboard())
        return

    method = "stars" if method == "stars" else "manual"
    ticket_id = create_ticket(user.id)
    create_renewal_request(
        ticket_id, user.id, xui_email, months, renew_days,
        payment_method=method, price_rub=months * user_monthly_price(user.id),
    )
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
    async def deliver(admin_id: int) -> None:
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
    await asyncio.gather(*(deliver(admin_id) for admin_id in get_admin_ids_for_notification("payments")))


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
    for admin_id in get_admin_ids_for_notification("payments"):
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
    async def deliver(admin_id: int) -> None:
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
    await asyncio.gather(*(deliver(admin_id) for admin_id in get_admin_ids_for_notification("requests")))


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
        cost = months * user_monthly_price(user.id)
        if balance_of(user.id) < cost:
            await message.reply_text(f"Недостаточно средств: нужно {cost} ₽, доступно {balance_of(user.id)} ₽. Пополните баланс в разделе «💰 Баланс».", reply_markup=client_main_keyboard())
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
        await message.reply_text(f"✅ Подписка создана на {days} дн. Списано {cost} ₽. Остаток: {balance_of(user.id)} ₽.{link_text}", reply_markup=client_main_keyboard())
        return

    method = "stars" if method == "stars" else "manual"
    ticket_id = create_ticket(user.id)
    create_subscription_request(
        ticket_id, user.id, email, months, days,
        payment_method=method, price_rub=months * user_monthly_price(user.id),
    )
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


def trial_profile_url(user) -> str:
    username = (getattr(user, "username", None) or "").strip().lstrip("@")
    return f"https://t.me/{quote(username, safe='')}" if username else f"tg://user?id={user.id}"


async def notify_admins_about_trial_subscription(context: ContextTypes.DEFAULT_TYPE, user, email: str, link: Optional[str]) -> None:
    username = f"@{user.username}" if user.username else "без username"
    profile_url = trial_profile_url(user)
    link_line = f"\nСсылка подписки: <code>{html.escape(link)}</code>" if link else "\nСсылка подписки: ⚠️ не получена из панели"
    text = (
        "🎁 Выдана тестовая подписка на 1 день\n\n"
        f"Пользователь: <a href=\"{html.escape(profile_url, quote=True)}\">{html.escape(username)}</a>\n"
        f"Telegram ID: <code>{user.id}</code>\n"
        f"Email 3x-ui: <code>{html.escape(email)}</code>"
        f"{link_line}"
    )

    async def deliver(admin_id: int) -> None:
        try:
            await context.bot.send_message(chat_id=admin_id, text=text, parse_mode=ParseMode.HTML)
        except TelegramError as exc:
            logger.warning("Не удалось уведомить админа %s о тестовой подписке: %s", admin_id, exc)

    await asyncio.gather(*(deliver(admin_id) for admin_id in get_admin_ids_for_notification("requests")))


async def issue_trial_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return
    if not trial_subscriptions_enabled():
        await message.reply_text("Пробные подписки сейчас отключены.", reply_markup=client_main_keyboard())
        return

    existing = get_trial_subscription(user.id)
    if existing:
        expires_ms = safe_int(existing["expires_at_ms"])
        link = str(existing["subscription_link"] or "").strip()
        if str(existing["status"]) == "active" and expires_ms > int(time.time() * 1000):
            link_line = f"\n\n🔗 Ваша тестовая ссылка:\n{link}" if link else "\n\n⚠️ Ссылка не была получена из панели. Обратитесь в поддержку."
            await message.reply_text(
                f"🎁 Тестовая подписка уже была выдана и действует до <b>{html.escape(format_xui_datetime(expires_ms))}</b>."
                f"{link_line}",
                parse_mode=ParseMode.HTML,
                reply_markup=client_main_keyboard(),
            )
        elif str(existing["status"]) == "creating":
            await message.reply_text("Тестовая подписка уже создаётся. Подождите немного и откройте раздел «📄 Подписка» снова.", reply_markup=client_main_keyboard())
        else:
            await message.reply_text("Вы уже использовали тестовую подписку. Вы можете купить новую подписку.", reply_markup=subscription_purchase_keyboard(False))
        return

    linked_email, _ = await resolve_xui_email_for_user(user.id)
    if linked_email:
        await message.reply_text("У вас уже есть привязанная подписка. Тестовая подписка доступна только до первой подписки.", reply_markup=client_main_keyboard())
        return

    email = generate_subscription_email(user.id, f"trial_{user.username or user.id}")
    if not claim_trial_subscription(user.id, email):
        await message.reply_text("Тестовая подписка уже была запрошена. Откройте раздел «📄 Подписка» снова.", reply_markup=client_main_keyboard())
        return

    try:
        async with XuiClient() as api:
            result = await api.add_client(email, 1, user.id)
            created_email = str(result.get("email") or email)
            try:
                summary = await api.get_client_summary(created_email)
            except XuiApiError:
                summary = None
    except XuiApiError as exc:
        release_trial_subscription_claim(user.id)
        await message.reply_text(f"Не удалось создать тестовую подписку: {exc}. Попробуйте позже.", reply_markup=client_main_keyboard())
        return

    link = build_subscription_link(summary or result)
    expiry_ms = safe_int((summary or result).get("expiry_ms") or result.get("new_expiry_ms"))
    if expiry_ms <= 0:
        expiry_ms = int(time.time() * 1000) + 24 * 60 * 60 * 1000
    activate_trial_subscription(user.id, link, expiry_ms)
    set_xui_link(user.id, created_email)
    link_line = f"\n\n🔗 Ваша тестовая ссылка:\n{link}" if link else "\n\n⚠️ Подписка создана, но панель не вернула ссылку. Напишите в поддержку."
    await message.reply_text(
        f"🎁 Тестовая подписка на 1 день активирована до <b>{html.escape(format_xui_datetime(expiry_ms))}</b>."
        f"{link_line}",
        parse_mode=ParseMode.HTML,
        reply_markup=client_main_keyboard(),
    )
    await notify_admins_about_trial_subscription(context, user, created_email, link)


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

    for admin_id in get_admin_ids_for_notification("requests"):
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
        for admin_id in get_admin_ids_for_notification("requests"):
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
        topup_note = "Пополнение P2P проверяет администратор."
        if CRYPTO_TOPUP_ENABLED:
            topup_note = "Пополнение P2P и криптовалютой проверяет администратор."
        await message.reply_text(
            f"💰 Ваш баланс: <b>{balance_of(user.id)} ₽</b>\n\n"
            f"{topup_note} После зачисления выберите срок подписки и оплатите её с баланса.",
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
            f"Рефералов: <b>{count}</b>\nЗаработано: <b>{earned} ₽</b>\n\n"
            f"Вы получаете {REFERRAL_PERCENT}% с каждого подтверждённого пополнения реферала. Вознаграждение автоматически поступает на баланс.",
            parse_mode=ParseMode.HTML,
            reply_markup=client_main_keyboard(),
        )
        return True

    if text == CLIENT_BUTTON_TICKET:
        context.user_data.pop("client_waiting_renew_months", None)
        context.user_data.pop("client_waiting_subscribe_months", None)
        with db() as conn:
            rows = conn.execute("SELECT id, status, updated_at FROM tickets WHERE user_id = ? ORDER BY updated_at DESC LIMIT 10", (user.id,)).fetchall()
        history = "\n".join(f"#{row['id']} — {'открыто' if row['status'] == 'open' else 'закрыто'} ({row['updated_at']})" for row in rows) or "Обращений пока нет."
        buttons = [[InlineKeyboardButton(f"#{row['id']} — {'🟢' if row['status'] == 'open' else '⚪'}", callback_data=f"clientticket:view:{row['id']}")] for row in rows]
        buttons.append([InlineKeyboardButton("➕ Новое обращение", callback_data="clientticket:new")])
        await message.reply_text(
            f"🆘 Ваши обращения:\n{history}",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return True

    if text == CLIENT_BUTTON_CONNECTION_GUIDE:
        count = count_subscription_welcome_messages()
        if not count:
            await message.reply_text("Инструкция по подключению пока не настроена. Обратитесь в поддержку.", reply_markup=client_main_keyboard())
            return True
        sent = await send_subscription_welcome_messages(context, user.id)
        if sent:
            await message.reply_text("📘 Инструкции по подключению отправлены выше.", reply_markup=client_main_keyboard())
        else:
            await message.reply_text("Не удалось отправить инструкции. Обратитесь в поддержку.", reply_markup=client_main_keyboard())
        return True

    if text == CLIENT_BUTTON_SUBSCRIPTION:
        xui_email, source = await resolve_xui_email_for_user(user.id)
        if not xui_email:
            if source.startswith("error:"):
                await message.reply_text(
                    "Не удалось проверить подписку в панели 3x-ui. Повторите позже или обратитесь в поддержку: подробности ошибки есть в журнале сервера.",
                    reply_markup=client_main_keyboard(),
                )
                return True
            await message.reply_text(
                f"📄 Активная подписка не найдена.\n\nВы можете купить новую подписку.\nЦена: <b>{user_monthly_price(user.id)} ₽/мес.</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=subscription_purchase_keyboard(trial_subscriptions_enabled() and not get_trial_subscription(user.id)),
            )
            return True
        try:
            async with XuiClient() as api:
                summary = await api.get_client_summary(xui_email)
        except XuiApiError as exc:
            await message.reply_text(f"Не удалось получить данные подписки: {exc}", reply_markup=client_main_keyboard())
            return True
        if not summary:
            await message.reply_text("Подписка в панели не найдена. Напишите в поддержку.", reply_markup=client_main_keyboard())
            return True
        link = build_subscription_link(summary)
        expiry = format_xui_datetime(safe_int(summary.get("expiry_ms")))
        link_line = f"\n\n🔗 Ваша ссылка подписки:\n{link}" if link else "\n\n⚠️ Ссылка пока недоступна — обратитесь в поддержку."
        await message.reply_text(
            f"📄 Ваша подписка\nEmail: <code>{html.escape(xui_email)}</code>\nДействует до: <b>{html.escape(expiry)}</b>{link_line}",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Продлить подписку", callback_data="subscription:renew")]]),
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
        monthly_price = user_monthly_price(user.id)
        total_price = months * monthly_price
        await message.reply_text(
            f"Вы выбрали продление на {month_word(months)}.\n"
            f"Цена: <b>{monthly_price} ₽/мес.</b> · Итого: <b>{total_price} ₽</b>.\n\n"
            "Выберите способ оплаты:",
            parse_mode=ParseMode.HTML,
            reply_markup=renewal_payment_choice_keyboard(months),
        )
        return True

    if context.user_data.get("balance_topup_method"):
        try:
            amount = int(text)
        except ValueError:
            amount = 0
        if amount < BALANCE_MIN_TOPUP_RUB or amount > BALANCE_MAX_TOPUP_RUB:
            await message.reply_text(f"Введите целую сумму от {BALANCE_MIN_TOPUP_RUB} до {BALANCE_MAX_TOPUP_RUB} ₽.")
            return True
        method = str(context.user_data.pop("balance_topup_method"))
        topup_id = create_balance_topup(user.id, amount, method)
        method_label = "криптовалютой" if method == "crypto" else "P2P"
        if method in {"p2p", "crypto"}:
            for admin_id in get_admin_ids_for_notification("payments"):
                try:
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=f"💳 Новая заявка на пополнение {method_label} #{topup_id}: пользователь {user.id}, сумма {amount} ₽. Сначала отправьте реквизиты кнопкой ниже; после поступления перевода подтвердите зачисление.",
                        reply_markup=balance_topup_confirm_keyboard(topup_id),
                    )
                except TelegramError:
                    pass
        await message.reply_text(
            f"Заявка на пополнение #{topup_id}: <b>{amount} ₽</b>.\n\n"
            "Дождитесь реквизитов от администратора. После перевода появится кнопка подтверждения.",
            parse_mode=ParseMode.HTML,
            reply_markup=None,
        )
        return True

    if context.user_data.get("client_waiting_stars_months"):
        months = parse_months_from_text(text)
        if months is None:
            await message.reply_text(f"Укажите число от 1 до {XUI_MAX_RENEW_MONTHS}.")
            return True
        context.user_data.pop("client_waiting_stars_months", None)
        xui_email, _ = await resolve_xui_email_for_user(user.id)
        if xui_email:
            await process_client_renewal_payment_choice(update, context, months, "stars")
        else:
            await process_client_subscription_payment_choice(update, context, months, "stars")
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
        monthly_price = user_monthly_price(user.id)
        total_price = months * monthly_price
        await message.reply_text(
            f"Вы выбрали оформление подписки на {month_word(months)}.\n"
            f"Цена: <b>{monthly_price} ₽/мес.</b> · Итого: <b>{total_price} ₽</b>.\n\n"
            "Выберите способ оплаты:",
            parse_mode=ParseMode.HTML,
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

    create_new_ticket = bool(context.user_data.pop("client_waiting_ticket_text", None))
    ticket_id = create_ticket(user.id) if create_new_ticket else get_latest_open_ticket(user.id)
    if not ticket_id:
        await message.reply_text(
            "Чтобы создать обращение, откройте «💬 Обращения» и нажмите «➕ Новое обращение».",
            reply_markup=client_main_keyboard(),
        )
        return
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


async def handle_admin_direct_renewal_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Продление из карточки клиента без обращения и без изменения заявок."""
    message = update.effective_message
    user_id = context.user_data.get("admin_waiting_direct_renew_user_id")
    if not message or not user_id:
        return False
    try:
        days = int((message.text or "").strip())
    except ValueError:
        days = 0
    if days < 1:
        await message.reply_text("Введите количество дней положительным числом. Для отмены используйте /cancel.", reply_markup=admin_main_keyboard())
        return True

    context.user_data.pop("admin_waiting_direct_renew_user_id", None)
    email = get_xui_link(int(user_id))
    try:
        async with XuiClient() as api:
            if not email:
                email = await api.find_client_email_by_tg_id(int(user_id))
                if email:
                    set_xui_link(int(user_id), email)
            if not email:
                await message.reply_text("Подписка не привязана. Сначала укажите email через кнопку «Привязать email».", reply_markup=admin_main_keyboard())
                return True
            result = await api.renew_client(email, days)
    except XuiApiError as exc:
        await message.reply_text(f"Ошибка продления: {exc}", reply_markup=admin_main_keyboard())
        return True

    await message.reply_text(
        format_renew_success_admin_text(result, days) + f"\nTelegram ID: <code>{user_id}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_main_keyboard(),
    )
    try:
        await context.bot.send_message(
            chat_id=int(user_id),
            text=f"✅ Администратор продлил вашу подписку на {days} дн. Новая дата окончания: {format_xui_datetime(safe_int(result.get('new_expiry_ms')))}.",
            reply_markup=client_main_keyboard(),
        )
    except TelegramError:
        logger.warning("Не удалось уведомить пользователя %s о ручном продлении", user_id)
    return True


def admin_management_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for admin_id in get_admin_ids():
        admin = get_user(admin_id)
        name = " ".join(filter(None, [admin["first_name"], admin["last_name"]])) if admin else ""
        label = f"{'👑 ' if is_super_admin(admin_id) else ''}{admin_id}"
        if name:
            label += f" · {name[:20]}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"adminsettings:{admin_id}")])
    buttons.append([InlineKeyboardButton("➕ Добавить администратора", callback_data="adminsettings:add")])
    return InlineKeyboardMarkup(buttons)


def admin_notification_keyboard(admin_id: int) -> InlineKeyboardMarkup:
    settings = get_admin_notification_settings(admin_id)
    buttons = []
    for kind, label in ADMIN_NOTIFICATION_TYPES.items():
        mark = "✅" if settings[kind] else "❌"
        buttons.append([InlineKeyboardButton(f"{mark} {label}", callback_data=f"admintoggle:{admin_id}:{kind}")])
    if not is_super_admin(admin_id):
        buttons.append([InlineKeyboardButton("🗑 Удалить администратора", callback_data=f"adminremove:{admin_id}")])
    buttons.append([InlineKeyboardButton("⬅️ К списку администраторов", callback_data="adminsettings:list")])
    return InlineKeyboardMarkup(buttons)


def trial_settings_keyboard() -> InlineKeyboardMarkup:
    enabled = trial_subscriptions_enabled()
    label = "❌ Отключить пробные подписки" if enabled else "✅ Включить пробные подписки"
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data="trialsettings:toggle")]])


async def send_admin_notification_settings(message: Message, admin_id: int) -> None:
    admin = get_user(admin_id)
    name = " ".join(filter(None, [admin["first_name"], admin["last_name"]])) if admin else "не запускал бота"
    settings = get_admin_notification_settings(admin_id)
    lines = [
        f"👤 Администратор <code>{admin_id}</code>{' 👑' if is_super_admin(admin_id) else ''}",
        f"Имя: {html.escape(name)}",
        "",
        "Типы уведомлений:",
    ]
    lines.extend(f"{'✅' if enabled else '❌'} {label}" for kind, label in ADMIN_NOTIFICATION_TYPES.items() for enabled in [settings[kind]])
    await message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=admin_notification_keyboard(admin_id))


async def handle_admin_menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    message = update.effective_message
    if not message or not message.text:
        return False

    text = message.text.strip()

    sections = {
        ADMIN_BUTTON_REQUESTS_GROUP: ("📥 Заявки", "requests"),
        ADMIN_BUTTON_TICKETS_GROUP: ("💬 Обращения", "tickets"),
        ADMIN_BUTTON_XUI_GROUP: ("🖥 3x-ui", "xui"),
        ADMIN_BUTTON_CLIENTS_GROUP: ("👥 Клиенты", "clients"),
        ADMIN_BUTTON_SERVICE_GROUP: ("⚙️ Сервис", "service"),
    }
    if text in sections:
        title, section = sections[text]
        admin_id = update.effective_user.id if update.effective_user else None
        await message.reply_text(title, reply_markup=admin_section_keyboard(section, admin_id))
        return True

    if text == ADMIN_BUTTON_BACK:
        await message.reply_text("Главное меню администратора.", reply_markup=admin_main_keyboard())
        return True

    if text == ADMIN_BUTTON_ADMIN_SETTINGS:
        if not is_super_admin(update.effective_user.id if update.effective_user else None):
            await message.reply_text("Настройка администраторов доступна только главному администратору.")
            return True
        await message.reply_text("👑 Администраторы и уведомления", reply_markup=admin_management_keyboard())
        return True

    if text == ADMIN_BUTTON_TRIAL_SETTINGS:
        if not is_super_admin(update.effective_user.id if update.effective_user else None):
            await message.reply_text("Настройка пробных подписок доступна только главному администратору.")
            return True
        state = "✅ включены" if trial_subscriptions_enabled() else "❌ отключены"
        await message.reply_text(f"🎁 Пробные подписки сейчас {state}.", reply_markup=trial_settings_keyboard())
        return True

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

    if text == ADMIN_BUTTON_SUBSCRIPTION_EVENTS:
        await subscription_events_command(update, context)
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

    if text == ADMIN_BUTTON_TOPUPS:
        await topups_command(update, context)
        return True

    if text in {ADMIN_BUTTON_BROADCAST_ALL, ADMIN_BUTTON_BROADCAST_ACTIVE}:
        context.user_data["admin_broadcast_mode"] = "active" if text == ADMIN_BUTTON_BROADCAST_ACTIVE else "all"
        audience = "только клиентам с действующей подпиской" if context.user_data["admin_broadcast_mode"] == "active" else "всем незаблокированным пользователям"
        await message.reply_text(
            f"Отправьте текст рассылки одним сообщением. Он будет отправлен {audience}.",
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

    if context.user_data.get("admin_waiting_new_admin"):
        context.user_data.pop("admin_waiting_new_admin", None)
        try:
            new_admin_id = int((message.text or "").strip())
        except ValueError:
            new_admin_id = 0
        if new_admin_id <= 0:
            await message.reply_text("Отправьте корректный Telegram ID администратора.", reply_markup=admin_main_keyboard())
            return
        with db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO admins(user_id, added_by, created_at) VALUES (?, ?, ?)",
                (new_admin_id, admin.id, now_iso()),
            )
        await message.reply_text(f"✅ Администратор {new_admin_id} добавлен.", reply_markup=admin_management_keyboard())
        return

    broadcast_mode = context.user_data.get("admin_broadcast_mode")
    if broadcast_mode:
        text = (message.text or "").strip()
        if not text:
            await message.reply_text("Для рассылки отправьте текстовым сообщением.", reply_markup=admin_main_keyboard())
            return
        if broadcast_mode == "active":
            try:
                recipient_ids = await active_client_broadcast_recipient_ids()
            except XuiApiError as exc:
                await message.reply_text(f"Не удалось проверить активные подписки в 3x-ui: {exc}. Повторите отправку сообщения.", reply_markup=admin_main_keyboard())
                return
            audience = "активным клиентам"
        else:
            with db() as conn:
                rows = conn.execute("SELECT user_id FROM users WHERE is_banned = 0").fetchall()
            recipient_ids = [int(row["user_id"]) for row in rows]
            audience = "всем пользователям"
        context.user_data.pop("admin_broadcast_mode", None)
        ok, failed = await broadcast_text_to_users(context, text, recipient_ids)
        await message.reply_text(f"Рассылка {audience} завершена. Отправлено: {ok}, ошибок: {failed}.", reply_markup=admin_main_keyboard())
        return

    if await handle_admin_stars_invoice_input(update, context):
        return

    deleting_user_id = context.user_data.get("admin_waiting_delete_user_id")
    if deleting_user_id:
        if (message.text or "").strip().lower() != "yes":
            await message.reply_text("Для удаления отправьте строго <code>yes</code> или нажмите «Отмена».", parse_mode=ParseMode.HTML)
            return
        context.user_data.pop("admin_waiting_delete_user_id", None)
        if delete_user_completely(int(deleting_user_id)):
            await message.reply_text(f"Пользователь {deleting_user_id} и его локальные данные удалены. Подписка в 3x-ui не удалялась.", reply_markup=admin_main_keyboard())
        else:
            await message.reply_text("Пользователь уже удалён или не найден.", reply_markup=admin_main_keyboard())
        return

    if await handle_admin_direct_renewal_input(update, context):
        return

    if await handle_admin_renewal_input(update, context):
        return

    if await handle_admin_subscription_input(update, context):
        return

    pending_topup_id = context.user_data.get("admin_sending_balance_details_topup_id")
    if pending_topup_id:
        topup = get_balance_topup(int(pending_topup_id))
        context.user_data.pop("admin_sending_balance_details_topup_id", None)
        if not topup or str(topup["status"]) != "pending":
            await message.reply_text("Заявка на пополнение уже обработана или не найдена.", reply_markup=admin_main_keyboard())
            return
        try:
            await context.bot.copy_message(
                chat_id=int(topup["user_id"]),
                from_chat_id=message.chat_id,
                message_id=message.message_id,
            )
            await context.bot.send_message(
                chat_id=int(topup["user_id"]),
                text=f"Реквизиты для пополнения #{pending_topup_id} получены. После перевода нажмите «✅ Я перевёл».",
                reply_markup=client_balance_topup_keyboard(int(pending_topup_id)),
            )
            await message.reply_text("Реквизиты отправлены клиенту.", reply_markup=balance_topup_confirm_keyboard(int(pending_topup_id)))
        except TelegramError as exc:
            logger.warning("Не удалось отправить реквизиты по пополнению #%s: %s", pending_topup_id, exc)
            await message.reply_text("Не удалось отправить реквизиты клиенту.", reply_markup=admin_main_keyboard())
        return

    linking_user_id = context.user_data.get("admin_linking_xui_email_user_id")
    if linking_user_id:
        context.user_data.pop("admin_linking_xui_email_user_id", None)
        email = (message.text or "").strip()
        if not email:
            await message.reply_text("Отправьте email клиента текстом.", reply_markup=admin_main_keyboard())
            return
        try:
            async with XuiClient() as api:
                found = await api.find_client(email)
                if not found:
                    await message.reply_text("Клиент с таким email не найден в 3x-ui.", reply_markup=admin_main_keyboard())
                    return
                if XUI_SYNC_TGID_TO_PANEL_ON_LINK:
                    await api.set_client_tg_id(email, int(linking_user_id))
            set_xui_link(int(linking_user_id), email)
            await message.reply_text(f"✅ Email {email} привязан к пользователю {linking_user_id}.", reply_markup=admin_main_keyboard())
        except XuiApiError as exc:
            await message.reply_text(f"Ошибка 3x-ui: {exc}", reply_markup=admin_main_keyboard())
        return

    edit_balance_user_id = context.user_data.get("admin_edit_user_balance")
    if edit_balance_user_id:
        context.user_data.pop("admin_edit_user_balance", None)
        try:
            amount = int((message.text or "").strip())
        except ValueError:
            amount = -1
        if set_balance(int(edit_balance_user_id), amount, admin.id):
            await message.reply_text(f"Баланс установлен: {amount} ₽.", reply_markup=admin_main_keyboard())
        else:
            await message.reply_text("Сумма должна быть неотрицательным целым числом.", reply_markup=admin_main_keyboard())
        return

    edit_price_user_id = context.user_data.get("admin_edit_user_price")
    if edit_price_user_id:
        context.user_data.pop("admin_edit_user_price", None)
        try:
            price = int((message.text or "").strip())
        except ValueError:
            price = -1
        if set_user_monthly_price(int(edit_price_user_id), None if price == 0 else price):
            actual = user_monthly_price(int(edit_price_user_id))
            await message.reply_text(f"Цена пользователя: {actual} ₽/мес.", reply_markup=admin_main_keyboard())
        else:
            await message.reply_text("Цена должна быть положительным числом, либо 0 для сброса.", reply_markup=admin_main_keyboard())
        return

    edit_ref_user_id = context.user_data.get("admin_edit_user_referrer")
    if edit_ref_user_id:
        context.user_data.pop("admin_edit_user_referrer", None)
        try:
            referrer_id = int((message.text or "").strip())
        except ValueError:
            referrer_id = -1
        if replace_referrer(int(edit_ref_user_id), None if referrer_id == 0 else referrer_id):
            await message.reply_text("Реферальная привязка обновлена.", reply_markup=admin_main_keyboard())
        else:
            await message.reply_text("Проверьте ID: пригласивший должен существовать и не совпадать с клиентом.", reply_markup=admin_main_keyboard())
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
async def subscription_events_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    page = 1
    if context.args:
        try:
            page = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Страница должна быть числом. Например: /subscriptionevents 2")
            return
    rows, page, pages, total = list_subscription_events(page)
    text, keyboard = format_subscription_events_page(rows, page, pages, total)
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


_XRAY_REVERSE_DNS_CACHE: dict[str, str] = {}
_XRAY_SOURCE_RE = re.compile(r"\bfrom\s+(?:tcp:|udp:)?(?:\[)?(?P<host>[0-9a-fA-F:.]+)(?:\])?:(?P<port>\d+)", re.IGNORECASE)
_XRAY_DESTINATION_RE = re.compile(r"\baccepted\s+(?P<protocol>[a-z0-9_-]+):(?:\[)?(?P<host>[^\s\]:]+|[0-9a-fA-F:.]+)(?:\])?:(?P<port>\d+)", re.IGNORECASE)
_XRAY_TIMESTAMP_RE = re.compile(r"^(?P<time>\d{4}[/-]\d{2}[/-]\d{2}[ T]\d{2}:\d{2}:\d{2})")


def _xray_is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


async def _xray_reverse_dns(ip: str) -> str:
    if ip in _XRAY_REVERSE_DNS_CACHE:
        return _XRAY_REVERSE_DNS_CACHE[ip]
    try:
        name, _, _ = await asyncio.wait_for(asyncio.to_thread(socket.gethostbyaddr, ip), timeout=2.0)
    except (OSError, TimeoutError, asyncio.TimeoutError):
        name = ""
    _XRAY_REVERSE_DNS_CACHE[ip] = name.rstrip(".")
    return _XRAY_REVERSE_DNS_CACHE[ip]


async def format_client_xray_logs(lines: list[str]) -> str:
    """Свести типичные Xray access-log строки к читаемой форме."""
    parsed: list[tuple[str, str, str, str, str, str]] = []
    for line in lines[-20:]:
        source = _XRAY_SOURCE_RE.search(line)
        destination = _XRAY_DESTINATION_RE.search(line)
        timestamp = _XRAY_TIMESTAMP_RE.search(line)
        if source and destination:
            parsed.append((
                timestamp.group("time") if timestamp else "Время не указано",
                source.group("host"), source.group("port"),
                destination.group("protocol").upper(), destination.group("host"), destination.group("port"),
            ))

    reverse_ips = list(dict.fromkeys(destination for _, _, _, _, destination, _ in parsed if _xray_is_ip(destination)))[:10]
    reverse_names = await asyncio.gather(*(_xray_reverse_dns(ip) for ip in reverse_ips)) if reverse_ips else []
    host_names = dict(zip(reverse_ips, reverse_names))

    readable: list[str] = []
    for timestamp, source_ip, source_port, protocol, destination, destination_port in parsed:
        service = host_names.get(destination, "") if _xray_is_ip(destination) else destination
        service_line = f"\n  Сервис: {service}" if service else ""
        readable.append(
            f"• {timestamp}\n"
            f"  Клиент: {source_ip}:{source_port}\n"
            f"  Назначение: {destination}:{destination_port} · {protocol}{service_line}"
        )

    if readable:
        return "\n\n".join(readable)
    return "\n".join(f"• {line}" for line in lines[-8:])


@require_admin
async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT u.user_id, u.username, u.first_name, u.last_name, u.is_banned, u.updated_at,
                   COALESCE(b.balance, 0) AS balance,
                   COALESCE(rc.referrals_count, 0) AS referrals_count,
                   r.referrer_user_id
            FROM users u
            LEFT JOIN (
                SELECT user_id, SUM(amount) AS balance FROM balance_transactions GROUP BY user_id
            ) b ON b.user_id = u.user_id
            LEFT JOIN (
                SELECT referrer_user_id, COUNT(*) AS referrals_count FROM referrals GROUP BY referrer_user_id
            ) rc ON rc.referrer_user_id = u.user_id
            LEFT JOIN referrals r ON r.referral_user_id = u.user_id
            ORDER BY u.updated_at DESC
            LIMIT 20
            """
        ).fetchall()
    if not rows:
        await update.message.reply_text("Пользователей пока нет.")
        return

    lines = ["Выберите пользователя:"]
    for row in rows:
        name = " ".join(filter(None, [row["first_name"], row["last_name"]])).strip() or "Без имени"
        username = f"@{row['username']}" if row["username"] else "без username"
        banned = " 🚫" if row["is_banned"] else ""
        referrer = f"; пришёл от {row['referrer_user_id']}" if row["referrer_user_id"] else ""
        lines.append(f"{row['user_id']} — {name} ({username}){banned}")
    keyboard = [[InlineKeyboardButton(f"{'🚫 ' if row['is_banned'] else '👤 '}{row['first_name'] or row['user_id']} · {row['user_id']}", callback_data=f"userdetail:{row['user_id']}")] for row in rows]
    await update.message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard))


@require_admin
async def setbalance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or len(context.args) != 2:
        await update.message.reply_text("Использование: /setbalance USER_ID СУММА_В_РУБЛЯХ")
        return
    try:
        target_user_id, amount = int(context.args[0]), int(context.args[1])
    except ValueError:
        await update.message.reply_text("USER_ID и сумма должны быть целыми числами.")
        return
    if not set_balance(target_user_id, amount, update.effective_user.id):
        await update.message.reply_text("Не удалось изменить баланс: пользователь не найден или указана отрицательная сумма.")
        return
    await update.message.reply_text(f"Баланс пользователя {target_user_id}: {balance_of(target_user_id)} ₽.")


@require_admin
async def setreferrer_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or len(context.args) != 2:
        await update.message.reply_text("Использование: /setreferrer USER_ID REFERRER_ID\nДля удаления: /setreferrer USER_ID 0")
        return
    try:
        user_id, referrer_id = int(context.args[0]), int(context.args[1])
    except ValueError:
        await update.message.reply_text("USER_ID и REFERRER_ID должны быть числами.")
        return
    if not get_user(user_id) or not replace_referrer(user_id, None if referrer_id == 0 else referrer_id):
        await update.message.reply_text("Не удалось изменить реферала: проверьте оба USER_ID, они не должны совпадать.")
        return
    await update.message.reply_text("Реферальная привязка обновлена.")


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
async def topups_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with db() as conn:
        pending_count = int(conn.execute("SELECT COUNT(*) FROM balance_topups WHERE status = 'pending'").fetchone()[0])
        history_count = int(conn.execute("SELECT COUNT(*) FROM balance_topups WHERE status != 'pending'").fetchone()[0])
    await update.message.reply_text(
        "💳 Пополнения\nВыберите список:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(f"🕐 Текущие ({pending_count})", callback_data="topups:pending:1")],
            [InlineKeyboardButton(f"🗂 История ({history_count})", callback_data="topups:history:1")],
        ]),
    )


async def show_topups_page(message, kind: str, page: int) -> None:
    """Список текущих или завершённых заявок на пополнение для администратора."""
    kind = "pending" if kind == "pending" else "history"
    page = max(1, page)
    where = "status = 'pending'" if kind == "pending" else "status != 'pending'"
    per_page = 12
    with db() as conn:
        total = int(conn.execute(f"SELECT COUNT(*) FROM balance_topups WHERE {where}").fetchone()[0])
        rows = conn.execute(
            f"SELECT id, user_id, amount, method, status, created_at FROM balance_topups WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            (per_page, (page - 1) * per_page),
        ).fetchall()
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, pages)
    title = "🕐 Текущие пополнения" if kind == "pending" else "🗂 История пополнений"
    if not rows:
        await message.reply_text(f"{title}\n\nЗаписей нет.")
        return
    status_icon = {"pending": "🕐", "confirmed": "✅", "cancelled": "❌"}
    buttons = [
        [InlineKeyboardButton(
            f"{status_icon.get(str(row['status']), '•')} #{row['id']} · {row['amount']} ₽ · {row['method']} · {row['user_id']}",
            callback_data=f"topupshow:{row['id']}",
        )]
        for row in rows
    ]
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"topups:{kind}:{page - 1}"))
    if page < pages:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"topups:{kind}:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("↩️ К спискам", callback_data="topupsmenu:show")])
    await message.reply_text(f"{title}\nСтраница {page}/{pages} · всего: {total}", reply_markup=InlineKeyboardMarkup(buttons))


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


def _status_number(value: Any) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace("%", "").strip())
        except ValueError:
            return 0.0
    return 0.0


def _status_value(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if data.get(key) not in (None, ""):
            return data[key]
    return None


def _resource_pair(value: Any) -> tuple[int, int]:
    if not isinstance(value, dict):
        return 0, 0
    current = safe_int(_status_value(value, "current", "used", "usage", "active", "freeUsed"))
    total = safe_int(_status_value(value, "total", "limit", "capacity", "all"))
    return current, total


def _percent_bar(percent: float) -> str:
    percent = max(0.0, min(100.0, percent))
    filled = round(percent / 10)
    return "█" * filled + "░" * (10 - filled)


def _node_status_snapshot(node: dict[str, Any]) -> dict[str, Any]:
    """Поддержать разные поля heartbeat в версиях 3x-ui Node."""
    snapshot = dict(node)
    for key in ("heartbeat", "lastHeartbeat", "heartbeatPatch", "statusDetail", "snapshot", "metrics", "serverStatus", "system"):
        value = node.get(key)
        if isinstance(value, dict):
            snapshot.update(value)
    if isinstance(node.get("status"), dict):
        snapshot.update(node["status"])
    # В ответе /nodes/list текущих 3x-ui Node метрики плоские, в отличие от
    # /server/status: cpuPct, memPct, netUp, netDown, xrayState.
    if snapshot.get("cpuPct") not in (None, "") and "cpu" not in snapshot:
        snapshot["cpu"] = {"percent": snapshot["cpuPct"]}
    if snapshot.get("memPct") not in (None, "") and "memPercent" not in snapshot:
        snapshot["memPercent"] = snapshot["memPct"]
    if "netIO" not in snapshot and (snapshot.get("netUp") is not None or snapshot.get("netDown") is not None):
        snapshot["netIO"] = {"up": snapshot.get("netUp", 0), "down": snapshot.get("netDown", 0)}
    if "xray" not in snapshot and (snapshot.get("xrayState") or snapshot.get("xrayVersion")):
        snapshot["xray"] = {"state": snapshot.get("xrayState"), "version": snapshot.get("xrayVersion")}
    return snapshot


def format_xui_status_card(title: str, status: dict[str, Any], node_state: Optional[str] = None) -> str:
    data = _node_status_snapshot(status)
    cpu_raw = data.get("cpu")
    if isinstance(cpu_raw, dict):
        cpu_percent = _status_number(_status_value(cpu_raw, "percent", "usage", "current", "value"))
        cpu_cores = _status_value(cpu_raw, "cores", "coreCount")
    elif isinstance(cpu_raw, (list, tuple)) and cpu_raw:
        cpu_percent = _status_number(cpu_raw[0])
        cpu_cores = cpu_raw[1] if len(cpu_raw) > 1 else None
    else:
        cpu_percent = _status_number(cpu_raw)
        cpu_cores = None

    mem_current, mem_total = _resource_pair(data.get("mem") or data.get("memory"))
    disk_current, disk_total = _resource_pair(data.get("disk") or data.get("storage"))
    mem_percent = mem_current * 100 / mem_total if mem_total else _status_number(data.get("memPercent"))
    disk_percent = disk_current * 100 / disk_total if disk_total else 0
    net = data.get("netIO") or data.get("network") or {}
    traffic = data.get("netTraffic") or data.get("traffic") or {}
    net_up = safe_int(_status_value(net, "up", "sent", "upload")) if isinstance(net, dict) else 0
    net_down = safe_int(_status_value(net, "down", "recv", "received", "download")) if isinstance(net, dict) else 0
    speed_up = safe_int(_status_value(traffic, "up", "sent", "upload")) if isinstance(traffic, dict) else 0
    speed_down = safe_int(_status_value(traffic, "down", "recv", "received", "download")) if isinstance(traffic, dict) else 0
    xray = data.get("xray") or {}
    if isinstance(xray, dict):
        xray_state = str(_status_value(xray, "state", "status") or "неизвестно")
        xray_version = str(_status_value(xray, "version", "ver") or "")
    else:
        xray_state, xray_version = str(xray or "неизвестно"), ""
    tcp = safe_int(_status_value(data, "tcpCount", "tcp", "connections", "connectionCount"))
    udp = safe_int(_status_value(data, "udpCount", "udp"))
    load = data.get("load") or {}
    if isinstance(load, dict):
        load_text = ", ".join(str(_status_value(load, key) or "—") for key in ("load1", "load5", "load15"))
    else:
        load_text = str(load or "—")
    state_line = f"\nСостояние ноды: <b>{html.escape(node_state)}</b>" if node_state else ""
    cpu_suffix = f" · {html.escape(str(cpu_cores))} ядер" if cpu_cores not in (None, "") else ""
    speed_line = f"\n⚡ Сейчас: ↑ {format_used_bytes(speed_up)}/с · ↓ {format_used_bytes(speed_down)}/с" if speed_up or speed_down else ""
    is_node = node_state is not None
    disk_line = (
        f"💽 Диск: <b>{disk_percent:.1f}%</b> · {format_used_bytes(disk_current)} / {format_used_bytes(disk_total) if disk_total else '—'}\n<code>{_percent_bar(disk_percent)}</code>"
        if disk_total
        else "💽 Диск: <i>данные не передаются нодой</i>"
    )
    connections_line = (
        f"🔌 Соединения: TCP {tcp} · UDP {udp}"
        if not is_node or tcp or udp
        else "🔌 Соединения: <i>TCP/UDP-метрики не передаются нодой</i>"
    )
    node_details = ""
    if node_state is not None:
        latency = safe_int(data.get("latencyMs"))
        uptime = safe_int(data.get("uptimeSecs"))
        online = safe_int(data.get("onlineCount"))
        active = safe_int(data.get("activeCount"))
        client_count = safe_int(data.get("clientCount"))
        details = [f"👥 Клиенты: {active}/{client_count} активны · онлайн: {online}"]
        if latency:
            details.append(f"⏱ Задержка: {latency} мс")
        if uptime:
            details.append(f"🕒 Аптайм: {uptime // 86400} дн. {(uptime % 86400) // 3600} ч.")
        if data.get("lastError"):
            details.append(f"⚠️ Ошибка: {html.escape(str(data['lastError']))}")
        node_details = "\n" + "\n".join(details)
    return (
        f"<b>🖥 {html.escape(title)}</b>{state_line}\n"
        f"⚙️ CPU: <b>{cpu_percent:.1f}%</b>{cpu_suffix}\n<code>{_percent_bar(cpu_percent)}</code>\n"
        f"🧠 Память: <b>{mem_percent:.1f}%</b> · {format_used_bytes(mem_current)} / {format_used_bytes(mem_total) if mem_total else '—'}\n<code>{_percent_bar(mem_percent)}</code>\n"
        f"{disk_line}\n"
        f"📡 Трафик: ↑ {format_used_bytes(net_up)} · ↓ {format_used_bytes(net_down)}{speed_line}\n"
        f"{connections_line}\n"
        f"📈 Load: {html.escape(load_text)}\n"
        f"☢️ Xray: <b>{html.escape(xray_state)}</b>{' · ' + html.escape(xray_version) if xray_version else ''}{node_details}"
    )


@require_admin
async def xui_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        async with XuiClient() as api:
            status = await api.get_server_status()
            try:
                nodes = await api.list_nodes()
                nodes_error = None
            except XuiApiError as exc:
                nodes, nodes_error = [], str(exc)
    except XuiApiError as exc:
        await update.message.reply_text(f"Ошибка 3x-ui: {exc}")
        return

    if not status:
        await update.message.reply_text("Подключение к 3x-ui есть, но основная панель не вернула статус сервера.")
        return
    await update.message.reply_text(format_xui_status_card("Основная панель", status), parse_mode=ParseMode.HTML)
    if nodes_error:
        await update.message.reply_text(f"⚠️ Не удалось получить список нод: {nodes_error}")
        return
    if not nodes:
        await update.message.reply_text("Ноды, подключённые к основной панели, не найдены.")
        return
    await update.message.reply_text(f"🌐 Подключённые ноды: {len(nodes)}")
    for index, node in enumerate(nodes, start=1):
        name = str(_status_value(node, "name", "remark", "address") or f"Нода #{index}")
        address = str(node.get("address") or "").strip()
        if address and address not in name:
            name = f"{name} · {address}"
        node_state = str(node.get("status") or ("включена" if node.get("enable", True) else "выключена"))
        await update.message.reply_text(format_xui_status_card(name, node, node_state), parse_mode=ParseMode.HTML)


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


async def broadcast_text_to_users(
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    recipient_ids: Iterable[int],
) -> tuple[int, int]:
    ok = 0
    failed = 0
    for user_id in recipient_ids:
        try:
            await context.bot.send_message(chat_id=int(user_id), text=text)
            ok += 1
        except TelegramError:
            failed += 1
    return ok, failed


async def active_client_broadcast_recipient_ids() -> list[int]:
    """Незаблокированные пользователи с действующей подпиской в 3x-ui."""
    with db() as conn:
        rows = conn.execute(
            """
            SELECT u.user_id, xl.xui_email
            FROM users u
            JOIN xui_links xl ON xl.telegram_user_id = u.user_id
            WHERE u.is_banned = 0
            """
        ).fetchall()
    if not rows:
        return []

    async with XuiClient() as api:
        clients = await api.list_clients()
    now_ms = int(time.time() * 1000)
    active_emails = {
        str(client.get("email") or "").strip().casefold()
        for client in clients
        if bool(client.get("enabled", True))
        and (safe_int(client.get("expiry_ms")) == 0 or safe_int(client.get("expiry_ms")) > now_ms)
    }
    return [
        int(row["user_id"])
        for row in rows
        if str(row["xui_email"] or "").strip().casefold() in active_emails
    ]


@require_admin
async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("Использование: /broadcast текст рассылки")
        return

    with db() as conn:
        rows = conn.execute("SELECT user_id FROM users WHERE is_banned = 0").fetchall()
    ok, failed = await broadcast_text_to_users(context, text, (int(row["user_id"]) for row in rows))

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
    context.user_data.pop("admin_waiting_direct_renew_user_id", None)
    context.user_data.pop("admin_waiting_delete_user_id", None)
    context.user_data.pop("admin_sending_payment_details_ticket_id", None)
    context.user_data.pop("admin_waiting_stars_invoice_ticket_id", None)
    context.user_data.pop("admin_sending_subscription_payment_details_ticket_id", None)
    context.user_data.pop("admin_sending_balance_details_topup_id", None)
    context.user_data.pop("admin_edit_user_price", None)
    context.user_data.pop("admin_waiting_new_admin", None)
    context.user_data.pop("admin_broadcast_mode", None)
    context.user_data.pop("admin_waiting_subscription_ticket_id", None)
    context.user_data.pop("admin_recording_subscription_messages", None)
    context.user_data.pop("client_waiting_subscribe_months", None)
    context.user_data.pop("client_subscribe_months", None)
    context.user_data.pop("client_waiting_ticket_text", None)
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
        if value not in {"p2p", "crypto"}:
            return
        if value == "crypto" and not CRYPTO_TOPUP_ENABLED:
            await query.message.reply_text("Криптопополнение сейчас отключено.")
            return
        context.user_data["balance_topup_method"] = value
        await query.message.reply_text(
            f"Введите сумму пополнения в ₽ (от {BALANCE_MIN_TOPUP_RUB} до {BALANCE_MAX_TOPUP_RUB}).\n"
            "Эта сумма будет зачислена на внутренний баланс после оплаты/подтверждения.",
            reply_markup=client_main_keyboard(),
        )
        return

    if action == "topupsmenu":
        if not is_admin(user.id) or value != "show":
            return
        with db() as conn:
            pending_count = int(conn.execute("SELECT COUNT(*) FROM balance_topups WHERE status = 'pending'").fetchone()[0])
            history_count = int(conn.execute("SELECT COUNT(*) FROM balance_topups WHERE status != 'pending'").fetchone()[0])
        await query.message.reply_text(
            "💳 Пополнения\nВыберите список:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"🕐 Текущие ({pending_count})", callback_data="topups:pending:1")],
                [InlineKeyboardButton(f"🗂 История ({history_count})", callback_data="topups:history:1")],
            ]),
        )
        return

    if action == "topups":
        if not is_admin(user.id):
            await query.message.reply_text("Список пополнений доступен только администраторам.")
            return
        try:
            kind, page_raw = value.split(":", 1)
            page = int(page_raw)
        except ValueError:
            return
        await show_topups_page(query.message, kind, page)
        return

    if action == "topupdetails":
        if not is_admin(user.id):
            await query.message.reply_text("Реквизиты может отправить только администратор.")
            return
        try:
            topup_id = int(value)
        except ValueError:
            return
        topup = get_balance_topup(topup_id)
        if not topup or str(topup["status"]) != "pending":
            await query.message.reply_text("Заявка уже обработана или не найдена.")
            return
        context.user_data["admin_sending_balance_details_topup_id"] = topup_id
        await query.message.reply_text(
            f"Отправьте реквизиты для пополнения #{topup_id}. Это сообщение будет переслано клиенту {topup['user_id']}.\nДля отмены используйте /cancel.",
            reply_markup=admin_main_keyboard(),
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
        for admin_id in get_admin_ids_for_notification("payments"):
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"💰 Пользователь {user.id} подтвердил {topup['method'].upper()}-перевод по пополнению #{topup_id}: {topup['amount']} ₽.",
                    reply_markup=balance_topup_confirm_keyboard(topup_id),
                )
            except TelegramError:
                pass
        await query.message.reply_text("Подтверждение передано администратору. Баланс будет зачислен после проверки.", reply_markup=client_main_keyboard())
        return

    if action == "topupcancel":
        try:
            topup_id = int(value)
        except ValueError:
            return
        topup = get_balance_topup(topup_id)
        if not topup:
            await query.message.reply_text("Заявка на пополнение не найдена.")
            return
        # Клиент может отменить только свою заявку, администратор — любую.
        if not is_admin(user.id) and int(topup["user_id"]) != user.id:
            await query.message.reply_text("Эта заявка вам не принадлежит.")
            return
        cancelled = cancel_balance_topup(topup_id)
        if not cancelled:
            await query.message.reply_text("Заявка уже обработана и не может быть отменена.")
            return
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except TelegramError:
            pass
        if is_admin(user.id):
            try:
                await context.bot.send_message(chat_id=int(cancelled["user_id"]), text=f"❌ Пополнение #{topup_id} отменено администратором. Баланс не изменён.", reply_markup=client_main_keyboard())
            except TelegramError:
                pass
        await query.message.reply_text(f"Пополнение #{topup_id} отменено. Баланс не изменён.", reply_markup=admin_main_keyboard() if is_admin(user.id) else client_main_keyboard())
        return

    if action == "topupconfirm":
        if not is_admin(user.id):
            await query.message.reply_text("Подтверждать пополнения могут только администраторы.")
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
            await context.bot.send_message(chat_id=int(topup["user_id"]), text=f"✅ Баланс пополнен на {topup['amount']} ₽. Текущий баланс: {balance_of(int(topup['user_id']))} ₽.", reply_markup=client_main_keyboard())
        except TelegramError:
            pass
        await query.message.reply_text(f"Пополнение #{topup_id} зачислено. Реферальная награда (если есть) начислена автоматически.")
        return

    if action == "subscription":
        if value == "renew":
            context.user_data["client_waiting_renew_months"] = True
            await query.message.reply_text(
                f"На сколько месяцев хотите продлить подписку?\n"
                f"Цена: <b>{user_monthly_price(user.id)} ₽/мес.</b>\n"
                f"Введите число от 1 до {XUI_MAX_RENEW_MONTHS}.",
                parse_mode=ParseMode.HTML,
                reply_markup=client_main_keyboard(),
            )
        elif value == "buy":
            context.user_data["client_waiting_subscribe_months"] = True
            await query.message.reply_text(f"На сколько месяцев хотите оформить подписку?\nВведите число от 1 до {XUI_MAX_RENEW_MONTHS}.", reply_markup=client_main_keyboard())
        elif value == "trial":
            await issue_trial_subscription(update, context)
        return

    if action == "starsbuy":
        context.user_data["client_waiting_stars_months"] = True
        await query.message.reply_text(f"На сколько месяцев купить/продлить подписку за Stars?\nВведите число от 1 до {XUI_MAX_RENEW_MONTHS}.", reply_markup=client_main_keyboard())
        return

    if action == "clientticket" and value == "new":
        context.user_data["client_waiting_ticket_text"] = True
        await query.message.reply_text("Опишите проблему одним сообщением — я передам его в поддержку.", reply_markup=client_main_keyboard())
        return

    if action == "clientticket" and value.startswith("view:"):
        try:
            ticket_id = int(value.split(":", 1)[1])
        except ValueError:
            return
        ticket = get_ticket(ticket_id)
        if not ticket or int(ticket["user_id"]) != user.id:
            await query.message.reply_text("Обращение не найдено.")
            return
        chunks = build_ticket_full_chat_chunks(ticket_id)
        if chunks:
            for chunk in chunks:
                await query.message.reply_text(chunk, parse_mode=ParseMode.HTML)
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
        for admin_id in get_admin_ids_for_notification("payments"):
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

    if action == "adminsettings":
        if not is_super_admin(user.id):
            await query.message.reply_text("Настройка администраторов доступна только главному администратору.")
            return
        if value == "list":
            await query.message.reply_text("👑 Администраторы и уведомления", reply_markup=admin_management_keyboard())
            return
        if value == "add":
            context.user_data["admin_waiting_new_admin"] = True
            await query.message.reply_text(
                "Отправьте Telegram ID нового администратора одним сообщением.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✖️ Отмена", callback_data="adminsettings:canceladd")]]),
            )
            return
        if value == "canceladd":
            context.user_data.pop("admin_waiting_new_admin", None)
            await query.message.reply_text("Добавление администратора отменено.", reply_markup=admin_management_keyboard())
            return
        try:
            target_admin_id = int(value)
        except ValueError:
            return
        if not is_admin(target_admin_id):
            await query.message.reply_text("Администратор не найден.", reply_markup=admin_management_keyboard())
            return
        await send_admin_notification_settings(query.message, target_admin_id)
        return

    if action == "trialsettings":
        if not is_super_admin(user.id):
            await query.message.reply_text("Настройка пробных подписок доступна только главному администратору.")
            return
        if value != "toggle":
            return
        enabled = not trial_subscriptions_enabled()
        set_trial_subscriptions_enabled(enabled)
        state = "✅ включены" if enabled else "❌ отключены"
        await query.message.reply_text(f"🎁 Пробные подписки {state}.", reply_markup=trial_settings_keyboard())
        return

    if action == "admintoggle":
        if not is_super_admin(user.id):
            await query.message.reply_text("Настройка уведомлений доступна только главному администратору.")
            return
        try:
            target_raw, notification_type = value.split(":", 1)
            target_admin_id = int(target_raw)
        except ValueError:
            return
        settings = get_admin_notification_settings(target_admin_id)
        if notification_type not in settings or not set_admin_notification_setting(target_admin_id, notification_type, not settings[notification_type]):
            await query.message.reply_text("Не удалось изменить настройку уведомлений.")
            return
        await send_admin_notification_settings(query.message, target_admin_id)
        return

    if action == "adminremove":
        if not is_super_admin(user.id):
            await query.message.reply_text("Удалять администраторов может только главный администратор.")
            return
        try:
            target_admin_id = int(value)
        except ValueError:
            return
        if not remove_admin(target_admin_id):
            await query.message.reply_text("Нельзя удалить главного администратора или администратор уже удалён.")
            return
        await query.message.reply_text(f"Администратор {target_admin_id} удалён.", reply_markup=admin_management_keyboard())
        return

    if action in {"userlink", "userdetail", "userban", "userbalance", "userref", "userprice", "userlogs", "userrenew", "userrenewmanual", "userrenewrequest", "userrenewrequestticket", "userdelete", "userdeletecancel"} and not is_admin(user.id):
        await query.message.reply_text("Это действие доступно только администраторам.")
        return

    if action == "userlogs":
        try:
            target_user_id = int(value)
        except ValueError:
            return
        email = get_xui_link(target_user_id)
        if not email:
            await query.message.reply_text("У пользователя нет привязанного email 3x-ui.")
            return
        try:
            async with XuiClient() as api:
                lines = await api.get_xray_log_lines()
        except XuiApiError as exc:
            await query.message.reply_text(f"Не удалось получить Xray-логи: {exc}")
            return

        email_key = email.casefold()
        matched = [line for line in lines if email_key in line.casefold()]
        if not matched:
            await query.message.reply_text(
                f"В последних {len(lines)} строках Xray-лога основной панели нет записей клиента <code>{html.escape(email)}</code>.\n\n"
                "Это возможно, если клиент подключался давно, access-log не содержит email или использовал удалённую ноду.",
                parse_mode=ParseMode.HTML,
            )
            return
        log_text = await format_client_xray_logs(matched)
        if len(log_text) > 3300:
            log_text = log_text[-3300:]
        await query.message.reply_text(
            f"📥 Xray-лог клиента <code>{html.escape(email)}</code> — основная панель\n"
            "Показаны IP клиента, адрес назначения и имя сервиса, если reverse DNS смог его определить.\n\n"
            f"<pre>{html.escape(log_text)}</pre>\n\n"
            "Имя по IP — ориентир: CDN и некоторые сервисы не публикуют PTR-запись. Логи удалённых нод основная панель 3x-ui через API не проксирует; для них нужен отдельный доступ к каждой ноде.",
            parse_mode=ParseMode.HTML,
        )
        return

    if action == "userrenew":
        try:
            target_user_id = int(value)
        except ValueError:
            return
        if not get_user(target_user_id):
            await query.message.reply_text("Пользователь не найден.")
            return
        await query.message.reply_text(
            "Как продлить подписку?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 Продлить по заявке", callback_data=f"userrenewrequest:{target_user_id}")],
                [InlineKeyboardButton("⚡ Продлить без заявки", callback_data=f"userrenewmanual:{target_user_id}")],
            ]),
        )
        return

    if action == "userrenewmanual":
        try:
            target_user_id = int(value)
        except ValueError:
            return
        if not get_user(target_user_id):
            await query.message.reply_text("Пользователь не найден.")
            return
        context.user_data["admin_waiting_direct_renew_user_id"] = target_user_id
        await query.message.reply_text("Введите количество дней для продления. Это продление не будет связано с заявкой.", reply_markup=admin_main_keyboard())
        return

    if action == "userrenewrequest":
        try:
            target_user_id = int(value)
        except ValueError:
            return
        with db() as conn:
            rows = conn.execute(
                """
                SELECT r.ticket_id, r.days, r.months, r.status, t.created_at
                FROM renewal_requests r
                JOIN tickets t ON t.id = r.ticket_id
                WHERE r.telegram_user_id = ? AND r.status NOT IN ('renewed', 'rejected')
                ORDER BY r.ticket_id DESC
                """,
                (target_user_id,),
            ).fetchall()
        if not rows:
            await query.message.reply_text("У пользователя нет незавершённых заявок на продление. Выберите «Продлить без заявки».")
            return
        buttons = [
            [InlineKeyboardButton(f"Заявка #{row['ticket_id']} · {row['days']} дн. · {row['status']}", callback_data=f"userrenewrequestticket:{target_user_id}:{row['ticket_id']}")]
            for row in rows
        ]
        await query.message.reply_text("Выберите заявку для продления:", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if action == "userrenewrequestticket":
        try:
            target_raw, ticket_raw = value.split(":", 1)
            target_user_id, ticket_id = int(target_raw), int(ticket_raw)
        except ValueError:
            return
        request = get_renewal_request(ticket_id)
        if not request or int(request["telegram_user_id"]) != target_user_id:
            await query.message.reply_text("Заявка не найдена или не принадлежит этому пользователю.")
            return
        await ask_admin_for_renewal_input(query.message, context, ticket_id, bind_email_to_user=True)
        return

    if action == "userdelete":
        try:
            target_user_id = int(value)
        except ValueError:
            return
        if not get_user(target_user_id) or is_admin(target_user_id):
            await query.message.reply_text("Нельзя удалить администратора или несуществующего пользователя.")
            return
        context.user_data["admin_waiting_delete_user_id"] = target_user_id
        await query.message.reply_text(
            f"⚠️ Удалить пользователя {target_user_id} и все его локальные данные бота? Подписка в 3x-ui останется.\n\nОтправьте <code>yes</code> для подтверждения.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data=f"userdeletecancel:{target_user_id}")]]),
        )
        return

    if action == "userdeletecancel":
        try:
            target_user_id = int(value)
        except ValueError:
            return
        if context.user_data.get("admin_waiting_delete_user_id") == target_user_id:
            context.user_data.pop("admin_waiting_delete_user_id", None)
        await query.message.reply_text("Удаление отменено.", reply_markup=admin_main_keyboard())
        return

    if action == "userlink":
        try:
            target_user_id = int(value)
        except ValueError:
            return
        if not get_user(target_user_id):
            await query.message.reply_text("Пользователь не найден.")
            return
        context.user_data["admin_linking_xui_email_user_id"] = target_user_id
        await query.message.reply_text(f"Введите email клиента 3x-ui для пользователя {target_user_id}. Бот проверит его и запишет tgId в панель.", reply_markup=admin_main_keyboard())
        return

    if action == "userdetail":
        try:
            target_user_id = int(value)
        except ValueError:
            return
        target = get_user(target_user_id)
        if not target:
            await query.message.reply_text("Пользователь не найден.")
            return
        count, earned = referral_stats(target_user_id)
        with db() as conn:
            ref = conn.execute("SELECT referrer_user_id FROM referrals WHERE referral_user_id = ?", (target_user_id,)).fetchone()
        username = f"@{target['username']}" if target['username'] else "без username"
        email = get_xui_link(target_user_id) or "не привязан"
        price = user_monthly_price(target_user_id)
        subscription_line = "Подписка: не привязана"
        online_line = "Онлайн: —"
        last_online_line = "Последняя активность: —"
        if email != "не привязан":
            try:
                async with XuiClient() as api:
                    summary = await api.get_client_summary(email)
                    online_emails = await api.get_online_client_emails()
                    last_online_by_email = await api.get_client_last_online()
                if summary:
                    expiry_ms = safe_int(summary.get("expiry_ms"))
                    enabled = bool(summary.get("enabled", True))
                    is_current = enabled and (expiry_ms == 0 or expiry_ms > int(time.time() * 1000))
                    subscription_state = "✅ активна" if is_current else "⛔ неактивна"
                    subscription_line = f"Подписка: {subscription_state} · до {format_xui_datetime(expiry_ms)}"
                else:
                    subscription_line = "Подписка: не найдена в 3x-ui"
                email_key = email.strip().casefold()
                online_line = "Онлайн: 🟢 в сети" if email_key in online_emails else "Онлайн: 🔴 не в сети"
                last_online = safe_int(last_online_by_email.get(email_key))
                if 0 < last_online < 10_000_000_000:
                    last_online *= 1000
                if last_online:
                    last_online_line = f"Последняя активность: {format_xui_datetime(last_online)}"
            except XuiApiError as exc:
                logger.warning("Не удалось получить подписку пользователя %s: %s", target_user_id, exc)
                subscription_line = "Подписка: не удалось проверить в 3x-ui"
                online_line = "Онлайн: не удалось проверить"
        text = (f"👤 Пользователь {target_user_id}\n{target['first_name'] or ''} {target['last_name'] or ''}\n{username}\n\n"
                f"Email 3x-ui: {email}\n"
                f"Статус: {'🚫 заблокирован' if target['is_banned'] else '✅ активен'}\n"
                f"{subscription_line}\n"
                f"{online_line}\n{last_online_line}\n"
                f"Баланс: {balance_of(target_user_id)} ₽\nЦена: {price} ₽/мес.\nРефералов: {count}\nЗаработано: {earned} ₽\n"
                f"Пригласил: {ref['referrer_user_id'] if ref else 'нет'}")
        buttons = [
            [InlineKeyboardButton("🔗 Привязать email", callback_data=f"userlink:{target_user_id}")],
            [InlineKeyboardButton("📥 Выгрузка логов", callback_data=f"userlogs:{target_user_id}")],
            [InlineKeyboardButton("🔄 Продлить подписку", callback_data=f"userrenew:{target_user_id}")],
            [InlineKeyboardButton("💰 Изменить баланс", callback_data=f"userbalance:{target_user_id}"), InlineKeyboardButton("🏷 Цена/мес.", callback_data=f"userprice:{target_user_id}")],
            [InlineKeyboardButton("👥 Изменить реферера", callback_data=f"userref:{target_user_id}")],
        ]
        if not is_admin(target_user_id):
            action_label = "✅ Разблокировать" if target["is_banned"] else "🚫 Заблокировать"
            buttons.append([InlineKeyboardButton(action_label, callback_data=f"userban:{target_user_id}")])
            buttons.append([InlineKeyboardButton("🗑 Удалить пользователя", callback_data=f"userdelete:{target_user_id}")])
        keyboard = InlineKeyboardMarkup(buttons)
        await query.message.reply_text(text, reply_markup=keyboard)
        return

    if action == "userban":
        try:
            target_user_id = int(value)
        except ValueError:
            return
        target = get_user(target_user_id)
        if not target:
            await query.message.reply_text("Пользователь не найден.")
            return
        if is_admin(target_user_id):
            await query.message.reply_text("Нельзя блокировать администратора через карточку пользователя.")
            return
        new_ban_state = not bool(target["is_banned"])
        set_ban(target_user_id, new_ban_state)
        await query.message.reply_text(
            f"Пользователь {target_user_id} {'заблокирован' if new_ban_state else 'разблокирован'}.",
            reply_markup=admin_main_keyboard(),
        )
        return

    if action == "topupshow":
        if not is_admin(user.id):
            await query.message.reply_text("Просмотр пополнений доступен только администраторам.")
            return
        try:
            topup_id = int(value)
        except ValueError:
            return
        topup = get_balance_topup(topup_id)
        if not topup:
            await query.message.reply_text("Заявка не найдена.")
            return
        status_text = {"pending": "🕐 ожидает проверки", "confirmed": "✅ зачислено", "cancelled": "❌ отменено"}.get(str(topup["status"]), str(topup["status"]))
        client_profile = f'<a href="tg://user?id={topup["user_id"]}">Открыть профиль клиента</a> · <code>{topup["user_id"]}</code>'
        details = (
            f"💳 Пополнение #{topup_id}\n"
            f"Клиент: {client_profile}\nСумма: {topup['amount']} ₽\n"
            f"Способ: {topup['method']}\nСтатус: {status_text}\n"
            f"Создано: {topup['created_at']}"
        )
        if topup["confirmed_at"]:
            if topup["confirmed_by"]:
                admin_id = int(topup["confirmed_by"])
                admin_profile = f'<a href="tg://user?id={admin_id}">Открыть профиль администратора</a> · <code>{admin_id}</code>'
            else:
                admin_profile = "автоматически"
            details += f"\nОбработано: {topup['confirmed_at']} · администратор: {admin_profile}"
        await query.message.reply_text(
            details,
            parse_mode=ParseMode.HTML,
            reply_markup=balance_topup_confirm_keyboard(topup_id) if str(topup["status"]) == "pending" else None,
        )
        return

    if action in {"userbalance", "userref", "userprice"}:
        try:
            target_user_id = int(value)
        except ValueError:
            return
        context.user_data["admin_edit_user_balance"] = target_user_id if action == "userbalance" else None
        context.user_data["admin_edit_user_referrer"] = target_user_id if action == "userref" else None
        context.user_data["admin_edit_user_price"] = target_user_id if action == "userprice" else None
        prompt = (
            "Введите итоговый баланс в рублях." if action == "userbalance" else
            "Введите цену за месяц в рублях. Отправьте 0, чтобы вернуть общую цену из .env." if action == "userprice" else
            "Введите ID пригласившего или 0, чтобы удалить привязку."
        )
        await query.message.reply_text(prompt, reply_markup=admin_main_keyboard())
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

    elif action == "subevents":
        try:
            page = int(value)
        except ValueError:
            await query.message.reply_text("Некорректный номер страницы.")
            return
        rows, page, pages, total = list_subscription_events(page)
        text, keyboard = format_subscription_events_page(rows, page, pages, total)
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
        ("topups", "заявки на пополнение"),
        ("setbalance", "установить баланс пользователя"),
        ("setreferrer", "изменить реферала пользователя"),
    ]
    for admin_id in get_admin_ids():
        try:
            await application.bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=admin_id))
        except TelegramError as exc:
            logger.warning("Не удалось установить команды для админа %s: %s", admin_id, exc)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Не позволяет ошибкам обработчика исчезать в стандартном сообщении PTB."""
    logger.exception("Необработанная ошибка при обработке update %r", update, exc_info=context.error)

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
    application.add_handler(CommandHandler("subscriptionevents", subscription_events_command))
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
    application.add_handler(CommandHandler("topups", topups_command))
    application.add_handler(CommandHandler("setbalance", setbalance_command))
    application.add_handler(CommandHandler("setreferrer", setreferrer_command))
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
    application.add_error_handler(error_handler)

    return application


def main() -> None:
    init_db()
    app = build_app()
    logger.info("Бот техподдержки запущен")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
