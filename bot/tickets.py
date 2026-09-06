from .config import *
from .database import *
from .xui import *

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


CLIENT_BUTTON_SUBSCRIPTION = "📄 Подписка"
CLIENT_BUTTON_BALANCE = "💰 Баланс"
CLIENT_BUTTON_REFERRALS = "👥 Рефералы"
CLIENT_BUTTON_TICKET = "🆘 Обращения"

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
ADMIN_BUTTON_TOPUPS = "💳 Пополнения"
ADMIN_BUTTON_BROADCAST = "📣 Рассылка"
ADMIN_BUTTON_REFRESH_MENUS = "🔄 Обновить меню"
ADMIN_BUTTON_HELP = "ℹ️ Помощь"
ADMIN_BUTTON_REQUESTS_GROUP = "📥 Заявки"
ADMIN_BUTTON_TICKETS_GROUP = "💬 Обращения"
ADMIN_BUTTON_XUI_GROUP = "🖥 3x-ui"
ADMIN_BUTTON_CLIENTS_GROUP = "👥 Клиенты"
ADMIN_BUTTON_SERVICE_GROUP = "⚙️ Сервис"
ADMIN_BUTTON_BACK = "⬅️ Главное меню"


def client_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[CLIENT_BUTTON_SUBSCRIPTION], [CLIENT_BUTTON_BALANCE, CLIENT_BUTTON_REFERRALS], [CLIENT_BUTTON_TICKET]],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )


def balance_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Пополнить P2P", callback_data="balance:p2p")],
        [InlineKeyboardButton("₿ Пополнить криптовалютой", callback_data="balance:crypto")],
        [InlineKeyboardButton("⭐ Купить подписку за Stars", callback_data="starsbuy:start")],
    ])


def balance_topup_confirm_keyboard(topup_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Отправить реквизиты", callback_data=f"topupdetails:{topup_id}")],
        [
            InlineKeyboardButton("✅ Подтвердить и зачислить", callback_data=f"topupconfirm:{topup_id}"),
            InlineKeyboardButton("❌ Отменить", callback_data=f"topupcancel:{topup_id}"),
        ],
    ])


def client_balance_topup_keyboard(topup_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Я перевёл", callback_data=f"topuppaid:{topup_id}"),
        InlineKeyboardButton("❌ Отменить", callback_data=f"topupcancel:{topup_id}"),
    ]])


def admin_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [ADMIN_BUTTON_REQUESTS_GROUP, ADMIN_BUTTON_TICKETS_GROUP],
            [ADMIN_BUTTON_XUI_GROUP, ADMIN_BUTTON_CLIENTS_GROUP],
            [ADMIN_BUTTON_SERVICE_GROUP],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите раздел",
    )


def admin_section_keyboard(section: str) -> ReplyKeyboardMarkup:
    sections = {
        "requests": [[ADMIN_BUTTON_RENEW_REQUESTS, ADMIN_BUTTON_SUBSCRIPTION_REQUESTS], [ADMIN_BUTTON_TOPUPS]],
        "tickets": [[ADMIN_BUTTON_TICKETS, ADMIN_BUTTON_HISTORY]],
        "xui": [[ADMIN_BUTTON_XUI_STATUS, ADMIN_BUTTON_INBOUNDS], [ADMIN_BUTTON_RENEW]],
        "clients": [[ADMIN_BUTTON_USERS, ADMIN_BUTTON_CLIENTS]],
        "service": [[ADMIN_BUTTON_BROADCAST, ADMIN_BUTTON_REFRESH_MENUS], [ADMIN_BUTTON_STATS, ADMIN_BUTTON_HELP]],
    }
    return ReplyKeyboardMarkup(sections.get(section, []) + [[ADMIN_BUTTON_BACK]], resize_keyboard=True)


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


