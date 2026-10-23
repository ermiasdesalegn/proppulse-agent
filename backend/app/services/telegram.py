from __future__ import annotations

from typing import Any, Protocol

import httpx


class TelegramError(Exception):
    pass


class TelegramService(Protocol):
    async def send_message(
        self, chat_id: int | str, text: str, *, reply_markup: dict | None = None, parse_mode: str | None = None
    ) -> int: ...

    async def send_chat_action(self, chat_id: int | str, action: str = "typing") -> None: ...

    async def send_location(self, chat_id: int | str, latitude: float, longitude: float) -> int: ...

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

    async def send_chat_action(self, chat_id: int | str, action: str = "typing") -> None:
        await self._request("sendChatAction", chat_id=chat_id, action=action)

    async def send_location(self, chat_id: int | str, latitude: float, longitude: float) -> int:
        return (await self._request("sendLocation", chat_id=chat_id, latitude=latitude, longitude=longitude))["message_id"]

    async def get_updates(self, offset: int | None, timeout: int = 25) -> list[dict]:
        params: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            params["offset"] = offset
        return await self._request("getUpdates", _timeout=timeout + 10, **params)

    async def delete_webhook(self, drop_pending_updates: bool = False) -> None:
        await self._request("deleteWebhook", drop_pending_updates=drop_pending_updates)

    async def set_webhook(self, url: str, secret_token: str) -> None:
        await self._request(
            "setWebhook", url=url, secret_token=secret_token, allowed_updates=["message"], drop_pending_updates=False
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

    async def download_file(self, file_id: str) -> bytes:
        return self._files[file_id]
