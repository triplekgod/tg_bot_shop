from .config import *

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

            CREATE TABLE IF NOT EXISTS admin_notification_settings (
                admin_id INTEGER NOT NULL,
                notification_type TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(admin_id, notification_type),
                FOREIGN KEY(admin_id) REFERENCES admins(user_id)
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
                price_rub INTEGER,
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
                price_rub INTEGER,
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

            CREATE TABLE IF NOT EXISTS user_prices (
                user_id INTEGER PRIMARY KEY,
                monthly_price_rub INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS trial_subscriptions (
                user_id INTEGER PRIMARY KEY,
                xui_email TEXT NOT NULL,
                subscription_link TEXT,
                expires_at_ms INTEGER,
                status TEXT NOT NULL DEFAULT 'creating',
                created_at TEXT NOT NULL,
                activated_at TEXT,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS bot_settings (
                setting_key TEXT PRIMARY KEY,
                setting_value TEXT NOT NULL,
                updated_at TEXT NOT NULL
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
        if "price_rub" not in columns:
            conn.execute("ALTER TABLE renewal_requests ADD COLUMN price_rub INTEGER")
        if "stars_amount" not in columns:
            conn.execute("ALTER TABLE renewal_requests ADD COLUMN stars_amount INTEGER")
        if "stars_invoice_sent_at" not in columns:
            conn.execute("ALTER TABLE renewal_requests ADD COLUMN stars_invoice_sent_at TEXT")
        if "stars_invoice_sent_by" not in columns:
            conn.execute("ALTER TABLE renewal_requests ADD COLUMN stars_invoice_sent_by INTEGER")
        if "stars_charge_id" not in columns:
            conn.execute("ALTER TABLE renewal_requests ADD COLUMN stars_charge_id TEXT")

        subscription_columns = {row[1] for row in conn.execute("PRAGMA table_info(subscription_requests)").fetchall()}
        if "price_rub" not in subscription_columns:
            conn.execute("ALTER TABLE subscription_requests ADD COLUMN price_rub INTEGER")

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


# Настройки отсутствуют у новых и старых администраторов до первого изменения.
# Это означает «включено», поэтому после обновления никто не пропустит уведомления.
ADMIN_NOTIFICATION_TYPES = {
    "tickets": "Обращения",
    "requests": "Заявки",
    "payments": "Оплаты",
}


def get_admin_notification_settings(admin_id: int) -> dict[str, bool]:
    settings = {kind: True for kind in ADMIN_NOTIFICATION_TYPES}
    with db() as conn:
        rows = conn.execute(
            "SELECT notification_type, enabled FROM admin_notification_settings WHERE admin_id = ?",
            (admin_id,),
        ).fetchall()
    for row in rows:
        kind = str(row["notification_type"])
        if kind in settings:
            settings[kind] = bool(row["enabled"])
    return settings


def set_admin_notification_setting(admin_id: int, notification_type: str, enabled: bool) -> bool:
    if notification_type not in ADMIN_NOTIFICATION_TYPES or not is_admin(admin_id):
        return False
    with db() as conn:
        conn.execute(
            """
            INSERT INTO admin_notification_settings(admin_id, notification_type, enabled, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(admin_id, notification_type) DO UPDATE SET
                enabled = excluded.enabled, updated_at = excluded.updated_at
            """,
            (admin_id, notification_type, int(enabled), now_iso()),
        )
    return True


def get_admin_ids_for_notification(notification_type: str) -> list[int]:
    if notification_type not in ADMIN_NOTIFICATION_TYPES:
        return get_admin_ids()
    with db() as conn:
        rows = conn.execute(
            """
            SELECT a.user_id
            FROM admins a
            LEFT JOIN admin_notification_settings s
                ON s.admin_id = a.user_id AND s.notification_type = ?
            WHERE COALESCE(s.enabled, 1) = 1
            ORDER BY a.user_id
            """,
            (notification_type,),
        ).fetchall()
    return [int(row["user_id"]) for row in rows]


def remove_admin(admin_id: int) -> bool:
    if admin_id == SUPER_ADMIN_ID:
        return False
    with db() as conn:
        conn.execute("DELETE FROM admin_notification_settings WHERE admin_id = ?", (admin_id,))
        cur = conn.execute("DELETE FROM admins WHERE user_id = ?", (admin_id,))
    return cur.rowcount > 0


def get_trial_subscription(user_id: int) -> Optional[sqlite3.Row]:
    with db() as conn:
        return conn.execute(
            "SELECT * FROM trial_subscriptions WHERE user_id = ?",
            (user_id,),
        ).fetchone()


def claim_trial_subscription(user_id: int, xui_email: str) -> bool:
    """Зарезервировать единственную пробную подписку до вызова 3x-ui."""
    with db() as conn:
        try:
            conn.execute(
                """
                INSERT INTO trial_subscriptions(user_id, xui_email, status, created_at)
                VALUES (?, ?, 'creating', ?)
                """,
                (user_id, xui_email, now_iso()),
            )
        except sqlite3.IntegrityError:
            return False
    return True


def activate_trial_subscription(user_id: int, subscription_link: Optional[str], expires_at_ms: int) -> bool:
    with db() as conn:
        cur = conn.execute(
            """
            UPDATE trial_subscriptions
            SET subscription_link = ?, expires_at_ms = ?, status = 'active', activated_at = ?
            WHERE user_id = ? AND status = 'creating'
            """,
            (subscription_link, expires_at_ms or None, now_iso(), user_id),
        )
    return cur.rowcount > 0


def release_trial_subscription_claim(user_id: int) -> None:
    """Разрешить повтор после ошибки до создания клиента в панели."""
    with db() as conn:
        conn.execute("DELETE FROM trial_subscriptions WHERE user_id = ? AND status = 'creating'", (user_id,))


def trial_subscriptions_enabled() -> bool:
    with db() as conn:
        row = conn.execute(
            "SELECT setting_value FROM bot_settings WHERE setting_key = 'trial_subscriptions_enabled'"
        ).fetchone()
    # Для обратной совместимости до первого изменения пробные подписки включены.
    return not row or str(row["setting_value"]).strip().lower() in {"1", "true", "yes", "да"}


def set_trial_subscriptions_enabled(enabled: bool) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO bot_settings(setting_key, setting_value, updated_at)
            VALUES ('trial_subscriptions_enabled', ?, ?)
            ON CONFLICT(setting_key) DO UPDATE SET
                setting_value = excluded.setting_value, updated_at = excluded.updated_at
            """,
            ("true" if enabled else "false", now_iso()),
        )


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


def delete_user_completely(user_id: int) -> bool:
    """Удалить локальный профиль и связанные данные бота, не трогая 3x-ui."""
    with db() as conn:
        if not conn.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone():
            return False
        ticket_rows = conn.execute("SELECT id FROM tickets WHERE user_id = ?", (user_id,)).fetchall()
        ticket_ids = [int(row["id"]) for row in ticket_rows]
        if ticket_ids:
            placeholders = ", ".join("?" for _ in ticket_ids)
            conn.execute(f"DELETE FROM admin_message_map WHERE ticket_id IN ({placeholders})", ticket_ids)
            conn.execute(f"DELETE FROM messages WHERE ticket_id IN ({placeholders})", ticket_ids)
            conn.execute(f"DELETE FROM renewal_requests WHERE ticket_id IN ({placeholders})", ticket_ids)
            conn.execute(f"DELETE FROM subscription_requests WHERE ticket_id IN ({placeholders})", ticket_ids)
        conn.execute("DELETE FROM tickets WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM referrals WHERE referral_user_id = ? OR referrer_user_id = ?", (user_id, user_id))
        conn.execute("DELETE FROM balance_transactions WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM balance_topups WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM user_prices WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM trial_subscriptions WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM xui_links WHERE telegram_user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
    return True


def replace_referrer(referral_user_id: int, referrer_user_id: Optional[int]) -> bool:
    """Админская правка привязки реферала; None полностью снимает её."""
    if referrer_user_id is not None and referral_user_id == referrer_user_id:
        return False
    with db() as conn:
        if referrer_user_id is None:
            conn.execute("DELETE FROM referrals WHERE referral_user_id = ?", (referral_user_id,))
            return True
        if not conn.execute("SELECT 1 FROM users WHERE user_id = ?", (referrer_user_id,)).fetchone():
            return False
        conn.execute(
            "INSERT INTO referrals(referral_user_id, referrer_user_id, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(referral_user_id) DO UPDATE SET referrer_user_id = excluded.referrer_user_id, created_at = excluded.created_at",
            (referral_user_id, referrer_user_id, now_iso()),
        )
    return True


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


def set_balance(user_id: int, target_amount: int, admin_id: int) -> bool:
    """Выставляет итоговый баланс через прозрачную корректирующую операцию."""
    if target_amount < 0 or not get_user(user_id):
        return False
    current = balance_of(user_id)
    delta = target_amount - current
    if delta:
        add_balance_transaction(user_id, delta, "admin_adjustment", f"Корректировка администратором {admin_id}")
    return True


def user_monthly_price(user_id: int) -> int:
    with db() as conn:
        row = conn.execute("SELECT monthly_price_rub FROM user_prices WHERE user_id = ?", (user_id,)).fetchone()
    return int(row["monthly_price_rub"]) if row else BALANCE_PRICE_PER_MONTH


def set_user_monthly_price(user_id: int, price: Optional[int]) -> bool:
    if not get_user(user_id):
        return False
    with db() as conn:
        if price is None:
            conn.execute("DELETE FROM user_prices WHERE user_id = ?", (user_id,))
            return True
        if price < 1:
            return False
        conn.execute(
            "INSERT INTO user_prices(user_id, monthly_price_rub, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET monthly_price_rub = excluded.monthly_price_rub, updated_at = excluded.updated_at",
            (user_id, price, now_iso()),
        )
    return True


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


def cancel_balance_topup(topup_id: int) -> Optional[sqlite3.Row]:
    """Отменяет только ещё не подтверждённое пополнение без движения средств."""
    with db() as conn:
        topup = conn.execute("SELECT * FROM balance_topups WHERE id = ?", (topup_id,)).fetchone()
        if not topup or str(topup["status"]) != "pending":
            return None
        conn.execute("UPDATE balance_topups SET status = 'cancelled' WHERE id = ?", (topup_id,))
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
        or result.get("url")
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
    # Импорт здесь предотвращает цикл database → xui → database при старте.
    from .xui import XuiApiError, XuiClient

    local_email = get_xui_link(telegram_user_id)
    if local_email:
        return local_email, "local"

    try:
        async with XuiClient() as api:
            panel_email = await api.find_client_email_by_tg_id(telegram_user_id)
    except XuiApiError as exc:
        logger.warning("Не удалось автоматически найти 3x-ui клиента по Telegram ID %s: %s", telegram_user_id, exc)
        return None, f"error:{exc}"

    if panel_email:
        set_xui_link(telegram_user_id, panel_email)
        return panel_email, "panel_tgId"
    return None, "empty"


