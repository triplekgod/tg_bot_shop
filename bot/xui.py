from .config import *
from .database import *

class XuiApiError(RuntimeError):
    pass


class XuiClient:
    def __init__(self) -> None:
        if not XUI_BASE_URL:
            raise XuiApiError("Не указан XUI_BASE_URL в .env")
        if not XUI_API_TOKEN and (not XUI_USERNAME or not XUI_PASSWORD):
            raise XuiApiError("Укажите XUI_API_TOKEN или XUI_USERNAME + XUI_PASSWORD в .env")

        headers = {"Accept": "application/json"}
        if XUI_API_TOKEN:
            headers["Authorization"] = f"Bearer {XUI_API_TOKEN}"

        self.base_url = XUI_BASE_URL
        self._client = httpx.AsyncClient(
            headers=headers,
            timeout=XUI_TIMEOUT,
            verify=XUI_VERIFY_SSL,
            follow_redirects=True,
        )
        self._logged_in = bool(XUI_API_TOKEN)

    def url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    async def __aenter__(self) -> "XuiClient":
        if not self._logged_in:
            await self.login()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self._client.aclose()

    async def login(self) -> None:
        last_error = ""
        for kwargs in (
            {"data": {"username": XUI_USERNAME, "password": XUI_PASSWORD}},
            {"json": {"username": XUI_USERNAME, "password": XUI_PASSWORD}},
        ):
            try:
                response = await self._client.post(self.url("/login"), **kwargs)
                last_error = response.text[:300]
                if response.status_code >= 400:
                    continue

                if response.text.strip():
                    try:
                        data = response.json()
                    except ValueError:
                        data = None
                    if isinstance(data, dict) and data.get("success") is False:
                        last_error = str(data.get("msg") or last_error)
                        continue

                self._logged_in = True
                return
            except httpx.HTTPError as exc:
                last_error = str(exc)

        raise XuiApiError(f"Не удалось войти в 3x-ui: {last_error or 'пустой ответ'}")

    async def request(self, method: str, path: str, **kwargs) -> Any:
        try:
            response = await self._client.request(method, self.url(path), **kwargs)
        except httpx.HTTPError as exc:
            raise XuiApiError(f"Ошибка подключения к 3x-ui: {exc}") from exc

        if response.status_code in {401, 403} and not XUI_API_TOKEN:
            self._logged_in = False
            await self.login()
            try:
                response = await self._client.request(method, self.url(path), **kwargs)
            except httpx.HTTPError as exc:
                raise XuiApiError(f"Ошибка подключения к 3x-ui: {exc}") from exc

        if response.status_code >= 400:
            raise XuiApiError(f"3x-ui вернул HTTP {response.status_code}: {response.text[:300]}")

        text = response.text.strip()
        if not text:
            return {}
        try:
            data = response.json()
        except ValueError:
            return {"raw": text}

        if isinstance(data, dict) and data.get("success") is False:
            raise XuiApiError(str(data.get("msg") or "3x-ui вернул success=false"))
        return data

    async def list_inbounds(self) -> list[dict[str, Any]]:
        data = await self.request("GET", "/panel/api/inbounds/list")
        obj = data.get("obj", data) if isinstance(data, dict) else data
        if obj is None:
            return []
        if not isinstance(obj, list):
            raise XuiApiError("Неожиданный ответ /panel/api/inbounds/list")
        return [item for item in obj if isinstance(item, dict)]

    @staticmethod
    def _settings(inbound: dict[str, Any]) -> dict[str, Any]:
        raw = inbound.get("settings") or {}
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                data = json.loads(raw)
                return data if isinstance(data, dict) else {}
            except ValueError:
                return {}
        return {}

    @staticmethod
    def _client_identifier(inbound: dict[str, Any], client: dict[str, Any]) -> str:
        protocol = str(inbound.get("protocol") or "").lower()
        if protocol == "trojan" and client.get("password"):
            return str(client["password"])
        if protocol in {"shadowsocks", "ss"} and client.get("email"):
            return str(client["email"])
        return str(client.get("id") or client.get("email") or "")

    async def find_clients(self, email: str) -> list[dict[str, Any]]:
        """Найти все копии одного клиента во всех inbounds.

        Для 3x-ui Node один и тот же клиент часто присутствует сразу в нескольких
        inbounds/подписках. Список клиентов группируется; продление в режиме
        clients_api выполняется через глобальный Clients API.
        """
        wanted = email.strip().casefold()
        matches: list[dict[str, Any]] = []
        if not wanted:
            return matches

        for inbound in await self.list_inbounds():
            settings = self._settings(inbound)
            clients = settings.get("clients") or []
            if not isinstance(clients, list):
                continue
            for client in clients:
                if not isinstance(client, dict):
                    continue
                values = [client.get("email"), client.get("id"), client.get("subId"), client.get("tgId")]
                if any(str(value).strip().casefold() == wanted for value in values if value is not None):
                    matches.append({"inbound": inbound, "client": client})
        return matches

    async def find_client(self, email: str) -> Optional[dict[str, Any]]:
        matches = await self.find_clients(email)
        return matches[0] if matches else None

    async def list_clients_raw(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for inbound in await self.list_inbounds():
            inbound_id = inbound.get("id")
            inbound_remark = inbound.get("remark") or "без названия"
            protocol = inbound.get("protocol") or "unknown"

            stats_by_email: dict[str, dict[str, Any]] = {}
            client_stats = inbound.get("clientStats") or inbound.get("client_stats") or []
            if isinstance(client_stats, list):
                for stat in client_stats:
                    if not isinstance(stat, dict):
                        continue
                    email = str(stat.get("email") or "").strip().casefold()
                    if email:
                        stats_by_email[email] = stat

            settings = self._settings(inbound)
            clients = settings.get("clients") or []
            if not isinstance(clients, list):
                continue

            for client in clients:
                if not isinstance(client, dict):
                    continue

                email = str(client.get("email") or "").strip()
                stat = stats_by_email.get(email.casefold(), {}) if email else {}
                up = safe_int(stat.get("up"))
                down = safe_int(stat.get("down"))
                total_limit = safe_int(client.get("totalGB") or stat.get("total"))
                expiry_ms = safe_int(client.get("expiryTime") or stat.get("expiryTime"))
                enabled = client.get("enable", stat.get("enable", True))

                result.append(
                    {
                        "email": email or "без email",
                        "key": (email or str(client.get("id") or client.get("subId") or client.get("tgId") or "без email")).strip().casefold(),
                        "inbound_id": inbound_id,
                        "inbound_remark": inbound_remark,
                        "protocol": protocol,
                        "enabled": bool(enabled),
                        "expiry_ms": expiry_ms,
                        "total_limit": total_limit,
                        "up": up,
                        "down": down,
                        "used": up + down,
                        "tgId": self._client_tg_id(client),
                        "subId": client.get("subId") or "",
                        "client_id": client.get("id") or "",
                    }
                )

        result.sort(key=lambda item: (str(item["email"]).casefold(), safe_int(item.get("inbound_id"))))
        return result

    @staticmethod
    def _merge_client_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
        enabled = any(bool(row.get("enabled")) for row in rows)
        expiry_values = [safe_int(row.get("expiry_ms")) for row in rows]
        positive_expiry = [value for value in expiry_values if value > 0]
        expiry_min = min(positive_expiry) if positive_expiry else 0
        expiry_max = max(positive_expiry) if positive_expiry else 0
        expiry_different = len(set(positive_expiry)) > 1

        total_limits = [safe_int(row.get("total_limit")) for row in rows]
        positive_limits = [value for value in total_limits if value > 0]
        total_limit = max(positive_limits) if positive_limits else 0

        protocols = sorted({str(row.get("protocol") or "unknown") for row in rows})
        tg_ids = sorted({str(row.get("tgId") or "").strip() for row in rows if str(row.get("tgId") or "").strip()})
        sub_ids = sorted({str(row.get("subId") or "").strip() for row in rows if str(row.get("subId") or "").strip()})
        inbounds = [
            {
                "id": row.get("inbound_id"),
                "remark": row.get("inbound_remark"),
                "protocol": row.get("protocol"),
            }
            for row in rows
        ]

        return {
            "email": rows[0].get("email") or "без email",
            "enabled": enabled,
            "expiry_ms": expiry_min,
            "expiry_ms_min": expiry_min,
            "expiry_ms_max": expiry_max,
            "expiry_different": expiry_different,
            "total_limit": total_limit,
            "up": sum(safe_int(row.get("up")) for row in rows),
            "down": sum(safe_int(row.get("down")) for row in rows),
            "used": sum(safe_int(row.get("used")) for row in rows),
            "tgId": ", ".join(tg_ids),
            "subId": ", ".join(sub_ids),
            "protocol": ", ".join(protocols),
            "inbound_count": len(rows),
            "inbounds": inbounds,
            "inbound_id": "-",
            "inbound_remark": f"{len(rows)} inbound(ов)",
        }

    async def list_clients(self) -> list[dict[str, Any]]:
        # Для 3x-ui Node сначала используем новый Clients API, чтобы не показывать
        # одного клиента по каждой подписке/inbound. Если API недоступен на старой
        # версии панели, возвращаемся к старому чтению inbounds.
        if XUI_RENEW_MODE == "clients_api":
            try:
                clients_api_rows = await self.list_clients_from_clients_api()
                if clients_api_rows:
                    return clients_api_rows
            except XuiApiError as exc:
                logger.warning("Clients API list недоступен, fallback на inbounds/list: %s", exc)

        raw_clients = await self.list_clients_raw()
        if not XUI_GROUP_CLIENTS_BY_EMAIL:
            return raw_clients

        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in raw_clients:
            key = str(item.get("key") or item.get("email") or "").casefold()
            grouped.setdefault(key, []).append(item)

        result = [self._merge_client_rows(rows) for rows in grouped.values()]
        result.sort(key=lambda item: str(item["email"]).casefold())
        return result

    @staticmethod
    def _client_tg_id(client: dict[str, Any]) -> Any:
        """Поддержать названия поля из разных сборок 3x-ui/Clients API."""
        for key in ("tgId", "tg_id", "telegramId", "telegram_id", "telegram", "telegramID", "telegramUserId"):
            value = client.get(key)
            if value not in (None, ""):
                return value
        return ""

    @staticmethod
    def _tg_id_matches(value: Any, telegram_user_id: int) -> bool:
        target = str(telegram_user_id).strip()
        # В разных версиях/форках поле хранится числом, строкой или списком.
        if isinstance(value, (list, tuple, set)):
            return any(XuiClient._tg_id_matches(item, telegram_user_id) for item in value)
        actual = str(value or "").strip()
        if not actual:
            return False
        if actual == target:
            return True
        if re.search(rf"(?<!\d){re.escape(target)}(?!\d)", actual):
            return True
        # Некоторые API сериализуют числовое tgId как "123.0".
        try:
            return int(float(actual)) == int(telegram_user_id) and actual.replace(".", "", 1).isdigit()
        except ValueError:
            return False

    async def find_client_email_by_tg_id(self, telegram_user_id: int) -> Optional[str]:
        """Найти email клиента в 3x-ui по полю tgId.

        Это нужно для 3x-ui Node, когда Telegram аккаунты клиентов уже
        привязаны в самой панели. Тогда боту не требуется отдельная локальная
        команда /linksub — он сам сопоставляет пользователя Telegram с клиентом.
        """
        # Сначала пробуем новый Clients API.
        if XUI_RENEW_MODE == "clients_api":
            try:
                for row in await self.list_clients_from_clients_api():
                    if self._tg_id_matches(row.get("tgId"), telegram_user_id):
                        email = str(row.get("email") or "").strip()
                        if email and email != "без email":
                            return email
            except XuiApiError as exc:
                logger.warning("Не удалось найти клиента по tgId через Clients API: %s", exc)

        # Fallback: читаем клиентов из inbounds/settings.clients.
        for row in await self.list_clients_raw():
            if self._tg_id_matches(row.get("tgId"), telegram_user_id):
                email = str(row.get("email") or "").strip()
                if email and email != "без email":
                    return email
        return None

    async def get_client_summary(self, email: str) -> Optional[dict[str, Any]]:
        if XUI_RENEW_MODE == "clients_api":
            try:
                record = await self.get_client_record(email)
                row = self._client_api_row_from_record(record)
                client = dict(record.get("client") or {})
                for key in ("subscriptionLink", "subscriptionUrl", "subLink", "link", "url"):
                    if client.get(key):
                        row[key] = client[key]
                inbound_ids = record.get("inboundIds") or []
                row["copies"] = [
                    {
                        "email": row.get("email") or email,
                        "inbound_id": inbound_id,
                        "inbound_remark": "attached inbound",
                        "protocol": "client",
                        "enabled": row.get("enabled", True),
                        "expiry_ms": row.get("expiry_ms", 0),
                        "total_limit": row.get("total_limit", 0),
                        "up": row.get("up", 0),
                        "down": row.get("down", 0),
                        "used": row.get("used", 0),
                        "tgId": row.get("tgId") or "",
                        "subId": row.get("subId") or "",
                        "limitIp": client.get("limitIp", 0),
                        "client_id": client.get("id") or client.get("uuid") or "",
                    }
                    for inbound_id in (inbound_ids if isinstance(inbound_ids, list) else [])
                ]
                if not row["copies"]:
                    row["copies"] = [{
                        "email": row.get("email") or email,
                        "inbound_id": "-",
                        "inbound_remark": "Clients API",
                        "protocol": "client",
                        "enabled": row.get("enabled", True),
                        "expiry_ms": row.get("expiry_ms", 0),
                        "total_limit": row.get("total_limit", 0),
                        "up": row.get("up", 0),
                        "down": row.get("down", 0),
                        "used": row.get("used", 0),
                        "tgId": row.get("tgId") or "",
                        "subId": row.get("subId") or "",
                        "limitIp": client.get("limitIp", 0),
                        "client_id": client.get("id") or client.get("uuid") or "",
                    }]
                return row
            except XuiApiError as exc:
                logger.warning("Clients API get недоступен, fallback на inbounds/list: %s", exc)

                # В отдельных сборках доступен список Clients API, но endpoint
                # /clients/get/{email} отключён. Локальная xui_links всё равно
                # должна позволять показать подписку по email.
                try:
                    for row in await self.list_clients_from_clients_api():
                        if str(row.get("email") or "").strip().casefold() == email.strip().casefold():
                            return row
                except XuiApiError as list_exc:
                    logger.warning("Не удалось найти клиента по email через Clients API list: %s", list_exc)

        matches = await self.find_clients(email)
        if not matches:
            return None

        raw_rows: list[dict[str, Any]] = []
        for match in matches:
            inbound = match["inbound"]
            client = match["client"]
            inbound_id = inbound.get("id")
            inbound_remark = inbound.get("remark") or "без названия"
            protocol = inbound.get("protocol") or "unknown"
            raw_rows.append(
                {
                    "email": str(client.get("email") or email).strip(),
                    "key": str(client.get("email") or email).strip().casefold(),
                    "inbound_id": inbound_id,
                    "inbound_remark": inbound_remark,
                    "protocol": protocol,
                    "enabled": bool(client.get("enable", True)),
                    "expiry_ms": safe_int(client.get("expiryTime")),
                    "total_limit": safe_int(client.get("totalGB")),
                    "up": 0,
                    "down": 0,
                    "used": 0,
                    "tgId": self._client_tg_id(client),
                    "subId": client.get("subId") or "",
                    "limitIp": client.get("limitIp", 0),
                    "client_id": client.get("id") or "",
                }
            )

        summary = self._merge_client_rows(raw_rows)
        summary["copies"] = raw_rows
        return summary

    @staticmethod
    def _format_found_inbound_ids(matches: list[dict[str, Any]]) -> str:
        ids = []
        for match in matches:
            inbound = match.get("inbound") or {}
            inbound_id = safe_int(inbound.get("id"))
            remark = str(inbound.get("remark") or "без названия")
            ids.append(f"{inbound_id} ({remark})")
        return ", ".join(ids)

    def _select_main_inbound_matches(self, matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not matches:
            return []

        if XUI_MAIN_INBOUND_ID_ERROR:
            raise XuiApiError(XUI_MAIN_INBOUND_ID_ERROR)

        if XUI_MAIN_INBOUND_ID is None:
            if len(matches) == 1:
                return matches
            found = self._format_found_inbound_ids(matches)
            raise XuiApiError(
                "Клиент найден в нескольких inbounds. Чтобы не продлить не тот inbound, "
                "укажите основной inbound в .env: XUI_MAIN_INBOUND_ID=ID. "
                f"Найдено: {found}. Список inbound можно посмотреть командой /inbounds"
            )

        selected = [
            match
            for match in matches
            if safe_int((match.get("inbound") or {}).get("id")) == XUI_MAIN_INBOUND_ID
        ]
        if not selected:
            found = self._format_found_inbound_ids(matches)
            raise XuiApiError(
                f"Клиент найден, но не в основном inbound #{XUI_MAIN_INBOUND_ID}. "
                f"Найдено в: {found}. Проверьте XUI_MAIN_INBOUND_ID или перенесите клиента в основной inbound."
            )
        return selected

    async def get_server_status(self) -> dict[str, Any]:
        data = await self.request("GET", "/panel/api/server/status")
        if isinstance(data, dict):
            obj = data.get("obj")
            return obj if isinstance(obj, dict) else data
        return {}

    async def list_nodes(self) -> list[dict[str, Any]]:
        """Получить ноды, зарегистрированные в основной панели 3x-ui Node."""
        data = await self.request("GET", "/panel/api/nodes/list")
        obj = data.get("obj", data) if isinstance(data, dict) else data
        if isinstance(obj, dict):
            obj = next((obj[key] for key in ("nodes", "items", "list", "data") if isinstance(obj.get(key), list)), [])
        return [dict(item) for item in obj if isinstance(item, dict)] if isinstance(obj, list) else []

    async def restart_xray(self) -> None:
        await self.request("POST", "/panel/api/server/restartXrayService")

    async def get_client_record(self, email: str) -> dict[str, Any]:
        """Получить глобальную запись клиента из нового Clients API 3x-ui Node."""
        email_clean = email.strip()
        if not email_clean:
            raise XuiApiError("Email клиента не указан")

        encoded_email = quote(email_clean, safe="")
        data = await self.request("GET", f"/panel/api/clients/get/{encoded_email}")
        obj = data.get("obj", data) if isinstance(data, dict) else data

        if isinstance(obj, dict) and isinstance(obj.get("client"), dict):
            client = dict(obj["client"])
            inbound_ids = obj.get("inboundIds") or obj.get("inbound_ids") or client.get("inboundIds") or []
        elif isinstance(obj, dict) and (obj.get("email") or obj.get("id") or obj.get("uuid")):
            client = dict(obj)
            inbound_ids = client.get("inboundIds") or client.get("inbound_ids") or []
        else:
            raise XuiApiError(f"Клиент '{email_clean}' не найден через Clients API")

        if not client.get("email"):
            client["email"] = email_clean

        if isinstance(obj, dict):
            for key in ("subscriptionLink", "subscriptionUrl", "subLink", "link", "url"):
                if not client.get(key) and obj.get(key):
                    client[key] = obj[key]
            if not client.get("subscriptionLink") and isinstance(obj.get("subscription"), dict):
                nested = obj["subscription"]
                client["subscriptionLink"] = nested.get("url") or nested.get("link") or nested.get("subscriptionUrl") or ""

        if not isinstance(inbound_ids, list):
            inbound_ids = []

        return {"client": client, "inboundIds": inbound_ids}

    @staticmethod
    def _client_api_row_from_record(record: dict[str, Any]) -> dict[str, Any]:
        client = dict(record.get("client") or {})
        inbound_ids = record.get("inboundIds") or client.get("inboundIds") or []
        if not isinstance(inbound_ids, list):
            inbound_ids = []

        up = safe_int(client.get("up") or client.get("usedUp"))
        down = safe_int(client.get("down") or client.get("usedDown"))
        used_gb = safe_int(client.get("usedGB"))
        used = up + down if up or down else used_gb

        total_limit = safe_int(client.get("totalGB") or client.get("total"))
        expiry_ms = safe_int(client.get("expiryTime"))
        enabled = bool(client.get("enable", True))
        email = str(client.get("email") or "без email").strip()

        return {
            "email": email,
            "key": email.casefold(),
            "inbound_id": "-",
            "inbound_remark": f"{len(inbound_ids)} inbound(ов)",
            "protocol": str(client.get("protocol") or client.get("security") or "client"),
            "enabled": enabled,
            "expiry_ms": expiry_ms,
            "expiry_ms_min": expiry_ms,
            "expiry_ms_max": expiry_ms,
            "expiry_different": False,
            "total_limit": total_limit,
            "up": up,
            "down": down,
            "used": used,
            "tgId": XuiClient._client_tg_id(client),
            "subId": client.get("subId") or "",
            "client_id": client.get("id") or client.get("uuid") or "",
            "inbound_count": len(inbound_ids),
            "inbounds": [{"id": item, "remark": "", "protocol": ""} for item in inbound_ids],
        }

    async def list_clients_from_clients_api(self) -> list[dict[str, Any]]:
        data = await self.request("GET", "/panel/api/clients/list")
        obj = data.get("obj", data) if isinstance(data, dict) else data

        if isinstance(obj, dict):
            items = next((obj[key] for key in ("clients", "records", "items", "list", "data") if isinstance(obj.get(key), list)), [])
            # В части версий ответ имеет вид {data: {items: [...]}}.
            if not items and isinstance(obj.get("data"), dict):
                nested = obj["data"]
                items = next((nested[key] for key in ("clients", "records", "items", "list") if isinstance(nested.get(key), list)), [])
        elif isinstance(obj, list):
            items = obj
        else:
            items = []

        result: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("client"), dict):
                record = {"client": item["client"], "inboundIds": item.get("inboundIds") or item.get("inbound_ids") or []}
            else:
                record = {"client": item, "inboundIds": item.get("inboundIds") or item.get("inbound_ids") or []}
            row = self._client_api_row_from_record(record)
            if row["email"]:
                result.append(row)

        result.sort(key=lambda item: str(item["email"]).casefold())
        return result

    @staticmethod
    def _sanitize_client_update_payload(client: dict[str, Any], email: str, inbound_ids: list[Any]) -> dict[str, Any]:
        """
        Запасной режим update/{email}.
        В некоторых версиях 3x-ui поле Client.id в Go-структуре имеет тип string,
        а Clients API может вернуть внутренний числовой id БД. Поэтому для update
        приводим id-поля к строкам и не отправляем None.
        """
        payload = {key: value for key, value in dict(client).items() if value is not None}
        payload["email"] = str(payload.get("email") or email).strip()

        for key in ("id", "uuid", "subId", "comment", "group", "security", "flow"):
            if key in payload and payload[key] is not None:
                payload[key] = str(payload[key])

        # В текущих версиях 3x-ui tgId в Go-структуре Client имеет тип int64.
        # Поэтому его нельзя отправлять строкой, иначе панель вернёт ошибку unmarshal.
        if "tgId" in payload and payload["tgId"] not in (None, ""):
            try:
                payload["tgId"] = int(payload["tgId"])
            except (TypeError, ValueError):
                payload["tgId"] = 0

        # Числовые поля оставляем числами, но аккуратно нормализуем типы.
        for key in ("expiryTime", "totalGB", "limitIp", "reset", "up", "down"):
            if key in payload and payload[key] not in (None, ""):
                try:
                    payload[key] = int(payload[key])
                except (TypeError, ValueError):
                    pass

        if "enable" in payload:
            payload["enable"] = bool(payload["enable"])

        # 3x-ui ожидает []string, но некоторые версии Clients API возвращают
        # allowedIPs одной строкой (например, "1.2.3.4, 5.6.7.8").
        if "allowedIPs" in payload and not isinstance(payload["allowedIPs"], list):
            raw_ips = payload["allowedIPs"]
            if isinstance(raw_ips, str):
                try:
                    decoded = json.loads(raw_ips)
                    payload["allowedIPs"] = decoded if isinstance(decoded, list) else [item.strip() for item in raw_ips.replace("\n", ",").split(",") if item.strip()]
                except json.JSONDecodeError:
                    payload["allowedIPs"] = [item.strip() for item in raw_ips.replace("\n", ",").split(",") if item.strip()]
            else:
                payload["allowedIPs"] = []

        if inbound_ids:
            clean_inbound_ids: list[int] = []
            for item in inbound_ids:
                try:
                    clean_inbound_ids.append(int(item))
                except (TypeError, ValueError):
                    continue
            if clean_inbound_ids:
                payload["inboundIds"] = clean_inbound_ids

        return payload

    async def set_client_tg_id(self, email: str, telegram_user_id: int) -> dict[str, Any]:
        """Записать tgId клиента в саму панель 3x-ui через Clients API.

        Используется, когда админ вручную указал email клиента в заявке:
        бот сохраняет привязку не только локально, но и в панели.
        """
        if XUI_RENEW_MODE != "clients_api":
            raise XuiApiError("Запись tgId в панель поддерживается только в режиме XUI_RENEW_MODE=clients_api")

        email_clean = email.strip()
        if not email_clean:
            raise XuiApiError("Email клиента не указан")

        record = await self.get_client_record(email_clean)
        client = dict(record["client"])
        inbound_ids = record.get("inboundIds") or []

        payload = self._sanitize_client_update_payload(client, email_clean, inbound_ids)
        payload["tgId"] = int(telegram_user_id)
        update_email = str(payload.get("email") or email_clean).strip() or email_clean
        encoded_email = quote(update_email, safe="")

        await self.request("POST", f"/panel/api/clients/update/{encoded_email}", json=payload)

        checked = await self.get_client_record(update_email)
        checked_client = checked.get("client") or {}
        if not self._tg_id_matches(checked_client.get("tgId"), telegram_user_id):
            raise XuiApiError(
                "3x-ui ответил без ошибки, но tgId у клиента не изменился. "
                "Проверьте API Docs панели для /panel/api/clients/update/{email}."
            )

        return {
            "email": update_email,
            "tgId": int(telegram_user_id),
            "old_tgId": str(client.get("tgId") or ""),
        }

    async def get_new_client_inbound_ids(self) -> list[int]:
        """Вернуть inboundIds для нового клиента.

        Лучше указать XUI_NEW_CLIENT_INBOUND_IDS в .env. Если настройка пустая,
        бот попробует взять все multi-client inbounds через /inbounds/options
        или через /inbounds/list.
        """
        if XUI_NEW_CLIENT_INBOUND_IDS:
            return list(XUI_NEW_CLIENT_INBOUND_IDS)

        ids: list[int] = []
        try:
            data = await self.request("GET", "/panel/api/inbounds/options")
            obj = data.get("obj", data) if isinstance(data, dict) else data
            if isinstance(obj, dict):
                items = obj.get("inbounds") or obj.get("options") or obj.get("data") or []
            else:
                items = obj
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    protocol = str(item.get("protocol") or "").lower()
                    inbound_id = safe_int(item.get("id"))
                    if inbound_id and protocol in {"vless", "vmess", "trojan", "shadowsocks", "ss", "hysteria", "hysteria2", "portfallback"}:
                        ids.append(inbound_id)
        except XuiApiError as exc:
            logger.warning("/panel/api/inbounds/options недоступен, fallback на inbounds/list: %s", exc)

        if not ids:
            for inbound in await self.list_inbounds():
                protocol = str(inbound.get("protocol") or "").lower()
                inbound_id = safe_int(inbound.get("id"))
                if inbound_id and protocol in {"vless", "vmess", "trojan", "shadowsocks", "ss", "hysteria", "hysteria2", "portfallback"}:
                    ids.append(inbound_id)

        if not ids:
            raise XuiApiError(
                "Не удалось определить inboundIds для нового клиента. "
                "Укажите их в .env: XUI_NEW_CLIENT_INBOUND_IDS=1,2,3"
            )
        return sorted(set(ids))

    async def add_client_by_clients_api(self, email: str, days: int, telegram_user_id: Optional[int] = None) -> dict[str, Any]:
        """Создать нового глобального клиента через Clients API 3x-ui Node."""
        if days <= 0:
            raise XuiApiError("Количество дней должно быть больше 0")
        email_clean = email.strip()
        if not email_clean:
            raise XuiApiError("Email нового клиента не указан")

        # Если такой email уже есть, не создаём дубль.
        try:
            await self.get_client_record(email_clean)
            raise XuiApiError(f"Клиент с email '{email_clean}' уже существует в 3x-ui")
        except XuiApiError as exc:
            if "уже существует" in str(exc):
                raise
            # Ошибка поиска — ожидаемый сценарий для нового клиента.

        inbound_ids = await self.get_new_client_inbound_ids()
        now_ms = int(time.time() * 1000)
        expiry_ms = now_ms + int(days) * 24 * 60 * 60 * 1000
        client_uuid = str(uuid.uuid4())
        sub_id = uuid.uuid4().hex[:16]

        client_payload: dict[str, Any] = {
            "id": client_uuid,
            "email": email_clean,
            "enable": True,
            "expiryTime": expiry_ms,
            "totalGB": int(XUI_NEW_CLIENT_TOTAL_GB),
            "limitIp": int(XUI_NEW_CLIENT_LIMIT_IP),
            "tgId": int(telegram_user_id or 0),
            "subId": sub_id,
        }
        if XUI_NEW_CLIENT_FLOW:
            client_payload["flow"] = XUI_NEW_CLIENT_FLOW
        if XUI_NEW_CLIENT_GROUP:
            client_payload["group"] = XUI_NEW_CLIENT_GROUP
        if XUI_NEW_CLIENT_COMMENT:
            client_payload["comment"] = XUI_NEW_CLIENT_COMMENT

        # В актуальном 3x-ui /panel/api/clients/add принимает SaveCreatePayload:
        # {"client": {...}, "inboundIds": [...]}. Если отправить поля клиента
        # плоским объектом, Go-сервер видит пустой client и возвращает
        # ошибку "client email is required".
        payload: dict[str, Any] = {
            "client": client_payload,
            "inboundIds": inbound_ids,
        }

        try:
            await self.request("POST", "/panel/api/clients/add", json=payload)
        except XuiApiError as exc:
            if "HTTP 404" in str(exc):
                raise XuiApiError(
                    "В этой версии 3x-ui не найден endpoint /panel/api/clients/add. "
                    "Откройте API Docs в панели и проверьте endpoint создания клиента."
                ) from exc
            raise

        if XUI_RESTART_XRAY_AFTER_RENEW:
            await self.restart_xray()

        checked = await self.get_client_record(email_clean)
        checked_client = checked.get("client") or {}
        return {
            "email": str(checked_client.get("email") or email_clean),
            "new_expiry_ms": safe_int(checked_client.get("expiryTime") or expiry_ms),
            "days": int(days),
            "tgId": int(telegram_user_id or 0),
            "inbound_ids": checked.get("inboundIds") or inbound_ids,
            "client_id": str(checked_client.get("id") or client_uuid),
            "subId": str(checked_client.get("subId") or sub_id),
            "created_mode": "clients_api/add",
        }

    async def add_client_legacy_inbound(self, email: str, days: int, telegram_user_id: Optional[int] = None) -> dict[str, Any]:
        """Запасной режим для старого API: добавить клиента в основной inbound."""
        if XUI_MAIN_INBOUND_ID_ERROR:
            raise XuiApiError(XUI_MAIN_INBOUND_ID_ERROR)
        if XUI_MAIN_INBOUND_ID is None:
            raise XuiApiError("Для старого режима добавления укажите XUI_MAIN_INBOUND_ID в .env")
        if days <= 0:
            raise XuiApiError("Количество дней должно быть больше 0")

        email_clean = email.strip()
        matches = await self.find_clients(email_clean)
        if matches:
            raise XuiApiError(f"Клиент с email '{email_clean}' уже существует в 3x-ui")

        now_ms = int(time.time() * 1000)
        expiry_ms = now_ms + int(days) * 24 * 60 * 60 * 1000
        client = {
            "id": str(uuid.uuid4()),
            "email": email_clean,
            "enable": True,
            "expiryTime": expiry_ms,
            "totalGB": int(XUI_NEW_CLIENT_TOTAL_GB),
            "limitIp": int(XUI_NEW_CLIENT_LIMIT_IP),
            "tgId": int(telegram_user_id or 0),
            "subId": uuid.uuid4().hex[:16],
        }
        if XUI_NEW_CLIENT_FLOW:
            client["flow"] = XUI_NEW_CLIENT_FLOW

        payload = {"id": int(XUI_MAIN_INBOUND_ID), "settings": json.dumps({"clients": [client]}, ensure_ascii=False)}
        await self.request("POST", "/panel/api/inbounds/addClient", json=payload)

        if XUI_RESTART_XRAY_AFTER_RENEW:
            await self.restart_xray()

        return {
            "email": email_clean,
            "new_expiry_ms": expiry_ms,
            "days": int(days),
            "tgId": int(telegram_user_id or 0),
            "inbound_ids": [int(XUI_MAIN_INBOUND_ID)],
            "client_id": client["id"],
            "subId": client["subId"],
            "created_mode": "legacy_inbound/addClient",
        }

    async def add_client(self, email: str, days: int, telegram_user_id: Optional[int] = None) -> dict[str, Any]:
        if XUI_RENEW_MODE == "legacy_inbound":
            return await self.add_client_legacy_inbound(email, days, telegram_user_id)
        return await self.add_client_by_clients_api(email, days, telegram_user_id)

    async def renew_client_by_clients_api(self, email: str, days: int) -> dict[str, Any]:
        """Продлить глобальную подписку клиента через новый Clients API."""
        if XUI_CLIENTS_RENEW_METHOD == "update":
            return await self.renew_client_by_clients_api_update(email, days)
        return await self.renew_client_by_clients_api_bulk_adjust(email, days)

    async def renew_client_by_clients_api_bulk_adjust(self, email: str, days: int) -> dict[str, Any]:
        """
        Правильное продление для 3x-ui Node: не перезаписывает клиента,
        а добавляет дни подписки через /panel/api/clients/bulkAdjust.
        """
        if days <= 0:
            raise XuiApiError("Количество дней должно быть больше 0")

        email_clean = email.strip()
        record = await self.get_client_record(email_clean)
        client = dict(record["client"])
        inbound_ids = record.get("inboundIds") or []

        old_expiry_ms = safe_int(client.get("expiryTime"))
        now_ms = int(time.time() * 1000)

        # Важная правка для просроченных подписок.
        # bulkAdjust добавляет дни к старому expiryTime. Если клиент истёк неделю назад,
        # обычный addDays=30 даст фактически только ~23 дня от текущего момента.
        # Поэтому для уже истёкших подписок выставляем точный expiryTime через update/{email}:
        # новый срок = сейчас + DAYS. Для активных подписок оставляем bulkAdjust.
        if XUI_RENEW_EXPIRED_FROM_NOW and old_expiry_ms > 0 and old_expiry_ms < now_ms:
            result = await self.renew_client_by_clients_api_update(email_clean, days)
            result["renew_mode"] = "clients_api/update_expired_from_now"
            result["expired_before_renew"] = True
            return result

        payload = {
            "emails": [str(client.get("email") or email_clean).strip() or email_clean],
            "addDays": int(days),
            "addBytes": 0,
        }

        try:
            result_data = await self.request("POST", "/panel/api/clients/bulkAdjust", json=payload)
        except XuiApiError as exc:
            message = str(exc)
            if "HTTP 404" in message:
                raise XuiApiError(
                    "В этой версии 3x-ui не найден endpoint /panel/api/clients/bulkAdjust. "
                    "Откройте API Docs в панели и проверьте название endpoint для Bulk Adjust. "
                    "Как временный вариант можно поставить XUI_CLIENTS_RENEW_METHOD=update, "
                    "но основной режим продления должен быть bulk_adjust."
                ) from exc
            raise

        if XUI_RESTART_XRAY_AFTER_RENEW:
            await self.restart_xray()

        checked = await self.get_client_record(email_clean)
        checked_client = checked.get("client") or {}
        checked_expiry = safe_int(checked_client.get("expiryTime"))
        checked_inbound_ids = checked.get("inboundIds") or inbound_ids

        skipped = []
        updated_count = 1
        if isinstance(result_data, dict):
            obj = result_data.get("obj", result_data)
            if isinstance(obj, dict):
                raw_skipped = obj.get("skipped") or obj.get("failed") or []
                if isinstance(raw_skipped, list):
                    skipped = raw_skipped
                updated_count = safe_int(obj.get("updated") or obj.get("success") or obj.get("count") or 1) or 1

        if skipped:
            raise XuiApiError(f"3x-ui не продлил клиента: {skipped}")

        return {
            "email": str(checked_client.get("email") or client.get("email") or email_clean),
            "old_expiry_ms": old_expiry_ms,
            "old_expiry_ms_min": old_expiry_ms,
            "old_expiry_ms_max": old_expiry_ms,
            "old_expiry_different": False,
            "new_expiry_ms": checked_expiry,
            "days": days,
            "enabled": bool(checked_client.get("enable", True)),
            "updated_count": updated_count,
            "matched_count": 1,
            "found_count": 1,
            "skipped_count": 0,
            "main_inbound_id": "подписка клиента",
            "renew_mode": "clients_api/bulk_adjust",
            "updated_items": [{"inbound_id": "подписка", "inbound_remark": "Clients API bulkAdjust", "protocol": "client"}],
            "failures": [],
            "inbound_id": "подписка",
            "inbound_remark": f"Clients API bulkAdjust, attached inbounds: {len(checked_inbound_ids) if isinstance(checked_inbound_ids, list) else 0}",
            "protocol": "client",
        }

    async def renew_client_by_clients_api_update(self, email: str, days: int) -> dict[str, Any]:
        """Запасной режим: выставить expiryTime через /panel/api/clients/update/{email}."""
        if days <= 0:
            raise XuiApiError("Количество дней должно быть больше 0")

        email_clean = email.strip()
        record = await self.get_client_record(email_clean)
        client = dict(record["client"])
        inbound_ids = record.get("inboundIds") or []

        old_expiry_ms = safe_int(client.get("expiryTime"))
        now_ms = int(time.time() * 1000)
        base_ms = old_expiry_ms if old_expiry_ms > now_ms else now_ms
        new_expiry_ms = base_ms + days * 24 * 60 * 60 * 1000

        payload = self._sanitize_client_update_payload(client, email_clean, inbound_ids)
        payload["expiryTime"] = new_expiry_ms
        payload["enable"] = True

        update_email = str(payload.get("email") or email_clean).strip() or email_clean
        encoded_email = quote(update_email, safe="")
        await self.request("POST", f"/panel/api/clients/update/{encoded_email}", json=payload)

        if XUI_RESTART_XRAY_AFTER_RENEW:
            await self.restart_xray()

        checked = await self.get_client_record(email_clean)
        checked_expiry = safe_int((checked.get("client") or {}).get("expiryTime"))
        if checked_expiry < new_expiry_ms:
            raise XuiApiError(
                "3x-ui ответил без ошибки, но срок подписки не изменился. "
                "Проверьте API Docs панели для /panel/api/clients/update/{email}."
            )

        checked_inbound_ids = checked.get("inboundIds") or inbound_ids
        return {
            "email": payload["email"],
            "old_expiry_ms": old_expiry_ms,
            "old_expiry_ms_min": old_expiry_ms,
            "old_expiry_ms_max": old_expiry_ms,
            "old_expiry_different": False,
            "new_expiry_ms": checked_expiry,
            "days": days,
            "enabled": True,
            "updated_count": 1,
            "matched_count": 1,
            "found_count": 1,
            "skipped_count": 0,
            "main_inbound_id": "подписка клиента",
            "renew_mode": "clients_api/update",
            "updated_items": [{"inbound_id": "подписка", "inbound_remark": "Clients API update", "protocol": "client"}],
            "failures": [],
            "inbound_id": "подписка",
            "inbound_remark": f"Clients API update, attached inbounds: {len(checked_inbound_ids) if isinstance(checked_inbound_ids, list) else 0}",
            "protocol": "client",
        }

    async def renew_client(self, email: str, days: int) -> dict[str, Any]:
        if XUI_RENEW_MODE == "legacy_inbound":
            return await self.renew_client_legacy_inbound(email, days)
        return await self.renew_client_by_clients_api(email, days)

    async def renew_client_legacy_inbound(self, email: str, days: int) -> dict[str, Any]:
        if days <= 0:
            raise XuiApiError("Количество дней должно быть больше 0")

        all_matches = await self.find_clients(email)
        if not all_matches:
            raise XuiApiError(f"Клиент '{email}' не найден в 3x-ui")

        matches = self._select_main_inbound_matches(all_matches)

        now_ms = int(time.time() * 1000)
        old_expiry_values = [safe_int(match["client"].get("expiryTime")) for match in matches]
        positive_old_expiry = [value for value in old_expiry_values if value > 0]
        old_expiry_min = min(positive_old_expiry) if positive_old_expiry else 0
        old_expiry_max = max(positive_old_expiry) if positive_old_expiry else 0
        base_ms = old_expiry_max if old_expiry_max > now_ms else now_ms
        new_expiry_ms = base_ms + days * 24 * 60 * 60 * 1000

        updated_items: list[dict[str, Any]] = []
        failures: list[str] = []

        for match in matches:
            inbound = match["inbound"]
            client = dict(match["client"])
            inbound_id = int(inbound.get("id"))
            client_id = self._client_identifier(inbound, client)
            if not client_id:
                failures.append(f"inbound {inbound_id}: не удалось определить clientId")
                continue

            client["expiryTime"] = new_expiry_ms
            client["enable"] = True

            payload = {
                "id": inbound_id,
                "settings": json.dumps({"clients": [client]}, ensure_ascii=False),
            }
            try:
                await self.request("POST", f"/panel/api/inbounds/updateClient/{quote(client_id, safe='')}", json=payload)
                updated_items.append(
                    {
                        "inbound_id": inbound_id,
                        "inbound_remark": inbound.get("remark") or "без названия",
                        "protocol": inbound.get("protocol") or "unknown",
                    }
                )
            except XuiApiError as exc:
                failures.append(f"inbound {inbound_id}: {exc}")

        if not updated_items:
            raise XuiApiError("Не удалось обновить клиента ни в одном inbound: " + "; ".join(failures))

        if XUI_RESTART_XRAY_AFTER_RENEW:
            await self.restart_xray()

        # Контрольная проверка: перечитать клиента и убедиться, что legacy inbound изменился.
        checked_matches = await self.find_clients(email)
        checked_by_inbound = {
            safe_int(match["inbound"].get("id")): safe_int(match["client"].get("expiryTime"))
            for match in checked_matches
        }
        changed_bad = [
            str(item["inbound_id"])
            for item in updated_items
            if checked_by_inbound.get(safe_int(item["inbound_id"]), 0) < new_expiry_ms
        ]
        if changed_bad:
            raise XuiApiError(
                "3x-ui ответил без ошибки, но expiryTime не изменился в inbound: "
                + ", ".join(changed_bad)
                + ". Проверьте версию панели и права API-токена."
            )

        return {
            "email": matches[0]["client"].get("email") or email,
            "old_expiry_ms": old_expiry_max,
            "old_expiry_ms_min": old_expiry_min,
            "old_expiry_ms_max": old_expiry_max,
            "old_expiry_different": len(set(positive_old_expiry)) > 1,
            "new_expiry_ms": new_expiry_ms,
            "days": days,
            "enabled": True,
            "updated_count": len(updated_items),
            "matched_count": len(matches),
            "found_count": len(all_matches),
            "skipped_count": max(0, len(all_matches) - len(matches)),
            "main_inbound_id": updated_items[0]["inbound_id"],
            "renew_mode": "main_inbound",
            "updated_items": updated_items,
            "failures": failures,
            # Поля оставлены для совместимости со старым текстом ответа.
            "inbound_id": updated_items[0]["inbound_id"],
            "inbound_remark": updated_items[0]["inbound_remark"],
            "protocol": updated_items[0]["protocol"],
        }


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def format_xui_datetime(ms: int) -> str:
    if not ms:
        return "без ограничения"
    if ms < 0:
        return f"относительное значение 3x-ui: {ms}"
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return dt.strftime("%d.%m.%Y %H:%M UTC")


def format_bytes(value: Any) -> str:
    try:
        num = int(value or 0)
    except (TypeError, ValueError):
        return "0 Б"
    if num <= 0:
        return "без ограничения"
    units = ["Б", "КБ", "МБ", "ГБ", "ТБ"]
    size = float(num)
    unit_index = 0
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    return f"{size:.2f} {units[unit_index]}"


def format_used_bytes(value: Any) -> str:
    try:
        num = int(value or 0)
    except (TypeError, ValueError):
        num = 0
    if num <= 0:
        return "0 Б"
    units = ["Б", "КБ", "МБ", "ГБ", "ТБ"]
    size = float(num)
    unit_index = 0
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    return f"{size:.2f} {units[unit_index]}"


def format_xui_expiry_range(client: dict[str, Any]) -> str:
    if client.get("expiry_different"):
        return (
            f"с {format_xui_datetime(safe_int(client.get('expiry_ms_min')))} "
            f"до {format_xui_datetime(safe_int(client.get('expiry_ms_max')))}"
        )
    return format_xui_datetime(safe_int(client.get("expiry_ms")))


def format_xui_clients_page(clients: list[dict[str, Any]], page: int) -> tuple[str, InlineKeyboardMarkup | None]:
    total = len(clients)
    pages = max(1, (total + XUI_CLIENTS_PER_PAGE - 1) // XUI_CLIENTS_PER_PAGE)
    page = min(max(page, 1), pages)
    start = (page - 1) * XUI_CLIENTS_PER_PAGE
    end = start + XUI_CLIENTS_PER_PAGE

    mode_text = "уникальные клиенты" if XUI_GROUP_CLIENTS_BY_EMAIL else "клиенты по inbounds"
    lines = [f"Клиенты 3x-ui: {total} ({mode_text})", f"Страница {page}/{pages}", ""]
    for index, client in enumerate(clients[start:end], start=start + 1):
        status = "✅ включён" if client["enabled"] else "⛔ выключен"
        used = safe_int(client.get("used"))
        limit = safe_int(client.get("total_limit"))
        traffic = format_used_bytes(used)
        if limit > 0:
            traffic += f" / {format_bytes(limit)}"
        else:
            traffic += " / без ограничения"

        tg_id = str(client.get("tgId") or "").strip()
        tg_line = f"\n   tgId: <code>{html.escape(tg_id)}</code>" if tg_id else ""

        inbound_count = safe_int(client.get("inbound_count"), 1)
        if inbound_count > 1:
            inbound_line = f"   Inbounds: <code>{inbound_count}</code> шт."
        else:
            inbound_line = (
                f"   Inbound: <code>{html.escape(str(client.get('inbound_id')))}</code> — "
                f"{html.escape(str(client.get('inbound_remark')))}"
            )

        lines.append(
            f"<b>{index}. {html.escape(str(client['email']))}</b>\n"
            f"{inbound_line}\n"
            f"   Протоколы: <code>{html.escape(str(client.get('protocol') or 'unknown'))}</code> | {status}\n"
            f"   Истекает: <code>{format_xui_expiry_range(client)}</code>\n"
            f"   Трафик: <code>{traffic}</code>"
            f"{tg_line}"
        )

    keyboard = None
    if pages > 1:
        buttons: list[InlineKeyboardButton] = []
        if page > 1:
            buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"clients:{page - 1}"))
        if page < pages:
            buttons.append(InlineKeyboardButton("Вперёд ➡️", callback_data=f"clients:{page + 1}"))
        if buttons:
            keyboard = InlineKeyboardMarkup([buttons])

    return "\n".join(lines), keyboard


