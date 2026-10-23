from __future__ import annotations

import json
from typing import Any, Protocol

import httpx

ALLOWED_UPDATES = ["message", "callback_query"]  # callback_query: taps on inline buttons


class TelegramError(Exception):
    pass


class TelegramService(Protocol):
    async def send_message(
        self, chat_id: int | str, text: str, *, reply_markup: dict | None = None, parse_mode: str | None = None
    ) -> int: ...

    async def send_chat_action(self, chat_id: int | str, action: str = "typing") -> None: ...

    async def send_location(self, chat_id: int | str, latitude: float, longitude: float) -> int: ...

    async def send_photo(self, chat_id: int | str, photo_url: str, caption: str, *, reply_markup: dict | None = None) -> int: ...

    async def send_photo_bytes(self, chat_id: int | str, content: bytes, caption: str, *, reply_markup: dict | None = None) -> int: ...

    async def send_document(self, chat_id: int | str, filename: str, content: bytes, caption: str = "") -> int: ...

    async def answer_callback(self, callback_id: str, text: str = "") -> None: ...

    async def get_me(self) -> dict: ...

    async def download_file(self, file_id: str) -> bytes: ...


class BotApiTelegramService:
    def __init__(self, token: str, transport: httpx.AsyncBaseTransport | None = None):
        self._token = token
        self._transport = transport

    async def _request(self, method: str, _timeout: float = 30, **params: Any) -> Any:
        url = f"https://api.telegram.org/bot{self._token}/{method}"
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=_timeout) as client:
                res = await client.post(url, json=params)
            body = res.json()
        except (httpx.HTTPError, ValueError) as exc:
            # Never chain the original error: its message contains the URL, and the URL contains the bot token.
            raise TelegramError(f"{method} failed: {type(exc).__name__}") from None
        if not body.get("ok"):
            raise TelegramError(f"{method} rejected: {body.get('description', 'unknown error')}")
        return body["result"]

    async def send_message(
        self, chat_id: int | str, text: str, *, reply_markup: dict | None = None, parse_mode: str | None = None
    ) -> int:
        params: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup:
            params["reply_markup"] = reply_markup
        if parse_mode:
            params["parse_mode"] = parse_mode
        return (await self._request("sendMessage", **params))["message_id"]

    async def send_photo_bytes(self, chat_id: int | str, content: bytes, caption: str, *, reply_markup: dict | None = None) -> int:
        """Uploads the picture itself. Used when Telegram cannot fetch the address (a site that blocks hotlinking, or a private address)."""
        url = f"https://api.telegram.org/bot{self._token}/sendPhoto"
        data: dict[str, str] = {"chat_id": str(chat_id), "caption": caption[:1024]}
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup)
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=60) as client:
                res = await client.post(url, data=data, files={"photo": ("photo.jpg", content)})
            body = res.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise TelegramError(f"sendPhoto failed: {type(exc).__name__}") from None
        if not body.get("ok"):
            raise TelegramError(f"sendPhoto rejected: {body.get('description', 'unknown error')}")
        return body["result"]["message_id"]

    async def send_document(self, chat_id: int | str, filename: str, content: bytes, caption: str = "") -> int:
        url = f"https://api.telegram.org/bot{self._token}/sendDocument"
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=60) as client:
                res = await client.post(url, data={"chat_id": str(chat_id), "caption": caption[:1024]}, files={"document": (filename, content)})
            body = res.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise TelegramError(f"sendDocument failed: {type(exc).__name__}") from None
        if not body.get("ok"):
            raise TelegramError(f"sendDocument rejected: {body.get('description', 'unknown error')}")
        return body["result"]["message_id"]

    async def answer_callback(self, callback_id: str, text: str = "") -> None:
        """Stops the button's loading spinner; without it Telegram shows one for about a minute."""
        await self._request("answerCallbackQuery", callback_query_id=callback_id, text=text[:200])

    async def send_chat_action(self, chat_id: int | str, action: str = "typing") -> None:
        await self._request("sendChatAction", chat_id=chat_id, action=action)

    async def send_location(self, chat_id: int | str, latitude: float, longitude: float) -> int:
        return (await self._request("sendLocation", chat_id=chat_id, latitude=latitude, longitude=longitude))["message_id"]

    async def send_photo(self, chat_id: int | str, photo_url: str, caption: str, *, reply_markup: dict | None = None) -> int:
        params: dict[str, Any] = {"chat_id": chat_id, "photo": photo_url, "caption": caption[:1024]}
        if reply_markup:
            params["reply_markup"] = reply_markup
        return (await self._request("sendPhoto", **params))["message_id"]

    async def get_updates(self, offset: int | None, timeout: int = 25) -> list[dict]:
        params: dict[str, Any] = {"timeout": timeout, "allowed_updates": ALLOWED_UPDATES}
        if offset is not None:
            params["offset"] = offset
        return await self._request("getUpdates", _timeout=timeout + 10, **params)

    async def delete_webhook(self, drop_pending_updates: bool = False) -> None:
        await self._request("deleteWebhook", drop_pending_updates=drop_pending_updates)

    async def set_webhook(self, url: str, secret_token: str) -> None:
        await self._request(
            "setWebhook", url=url, secret_token=secret_token, allowed_updates=ALLOWED_UPDATES, drop_pending_updates=False
        )

    async def get_webhook_info(self) -> dict:
        return await self._request("getWebhookInfo")

    async def get_me(self) -> dict:
        return await self._request("getMe")

    async def download_file(self, file_id: str) -> bytes:
        file_path = (await self._request("getFile", file_id=file_id))["file_path"]
        url = f"https://api.telegram.org/file/bot{self._token}/{file_path}"
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=60) as client:
                res = await client.get(url)
        except httpx.HTTPError as exc:
            raise TelegramError(f"file download failed: {type(exc).__name__}") from None
        if res.status_code != 200:
            raise TelegramError(f"file download returned {res.status_code}")
        return res.content


class MockTelegramService:
    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.messages: list[dict] = []
        self.actions: list[tuple] = []
        self.locations: list[tuple] = []
        self.photos: list[dict] = []
        self.documents: list[dict] = []
        self.answered: list[tuple] = []
        self.username = "mock_bot"
        self._files = files or {}

    async def send_message(
        self, chat_id: int | str, text: str, *, reply_markup: dict | None = None, parse_mode: str | None = None
    ) -> int:
        self.messages.append(dict(chat_id=chat_id, text=text, reply_markup=reply_markup))
        return len(self.messages)

    async def send_chat_action(self, chat_id: int | str, action: str = "typing") -> None:
        self.actions.append((chat_id, action))

    async def send_location(self, chat_id: int | str, latitude: float, longitude: float) -> int:
        self.locations.append((chat_id, latitude, longitude))
        return len(self.locations)

    async def send_photo(self, chat_id: int | str, photo_url: str, caption: str, *, reply_markup: dict | None = None) -> int:
        self.photos.append(dict(chat_id=chat_id, photo_url=photo_url, caption=caption, reply_markup=reply_markup))
        return 1000 + len(self.photos)

    async def send_photo_bytes(self, chat_id: int | str, content: bytes, caption: str, *, reply_markup: dict | None = None) -> int:
        self.photos.append(dict(chat_id=chat_id, photo_bytes=len(content), caption=caption, reply_markup=reply_markup))
        return 1000 + len(self.photos)

    async def send_document(self, chat_id: int | str, filename: str, content: bytes, caption: str = "") -> int:
        self.documents.append(dict(chat_id=chat_id, filename=filename, content=content, caption=caption))
        return 2000 + len(self.documents)

    async def answer_callback(self, callback_id: str, text: str = "") -> None:
        self.answered.append((callback_id, text))

    async def get_me(self) -> dict:
        return {"id": 1, "username": self.username}

    async def download_file(self, file_id: str) -> bytes:
        return self._files[file_id]
