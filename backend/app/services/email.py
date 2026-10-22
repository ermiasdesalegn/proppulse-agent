from __future__ import annotations

from typing import Protocol

import httpx


class EmailError(Exception):
    pass


class EmailService(Protocol):
    async def send(self, *, to: str, subject: str, html: str, idempotency_key: str | None = None) -> str:
        """Send one email and return the provider message id."""


class ResendEmailService:
    URL = "https://api.resend.com/emails"

    def __init__(
        self,
        api_key: str,
        from_addr: str,
        override_to: str = "",
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._api_key, self._from = api_key, from_addr
        self._override_to = override_to
        self._transport = transport

    async def send(self, *, to: str, subject: str, html: str, idempotency_key: str | None = None) -> str:
        if self._override_to:
            # Resend's sandbox sender only delivers to the account owner's address.
            subject, to = f"[to: {to}] {subject}", self._override_to
        headers = {"Authorization": f"Bearer {self._api_key}"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        async with httpx.AsyncClient(transport=self._transport, timeout=20) as client:
            res = await client.post(
                self.URL, headers=headers, json={"from": self._from, "to": [to], "subject": subject, "html": html}
            )
        if res.status_code >= 300:
            raise EmailError(f"Resend returned {res.status_code}: {res.text[:300]}")
        return res.json()["id"]


class MockEmailService:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, *, to: str, subject: str, html: str, idempotency_key: str | None = None) -> str:
        message_id = f"mock-email-{len(self.sent) + 1}"
        self.sent.append(dict(id=message_id, to=to, subject=subject, html=html, idempotency_key=idempotency_key))
        return message_id
