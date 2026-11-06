from __future__ import annotations

import base64
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import httpx


class EmailError(Exception):
    pass


@dataclass(frozen=True)
class Attachment:
    filename: str
    content: bytes
    content_type: str = "application/pdf"


class EmailService(Protocol):
    async def send(self, *, to: str, subject: str, html: str, idempotency_key: str | None = None, attachments: Sequence[Attachment] = (),
                   from_name: str | None = None) -> str:
        """Send one email and return the provider message id."""


def display_name(name: str) -> str:
    """A name safe to put in front of the sender's address: no angle brackets, quotes, commas or line breaks to end the name early or add a header."""
    return " ".join(re.sub(r"[<>\"',;@\r\n\\]", " ", name).split())[:60]


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

    async def send(self, *, to: str, subject: str, html: str, idempotency_key: str | None = None, attachments: Sequence[Attachment] = (),
                   from_name: str | None = None) -> str:
        if self._override_to:
            # Resend's sandbox sender only delivers to the account owner's address.
            subject, to = f"[to: {to}] {subject}", self._override_to
        headers = {"Authorization": f"Bearer {self._api_key}"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        async with httpx.AsyncClient(transport=self._transport, timeout=20) as client:
            sender = self._from
            if from_name and (shown := display_name(from_name)):  # "Bole Homes via PropPulse <address>": the agency's name, the platform's address
                address = sender.rsplit("<", 1)[-1].rstrip("> ")
                sender = f"{shown} via PropPulse <{address}>"
            payload: dict = {"from": sender, "to": [to], "subject": subject, "html": html}
            if attachments:
                payload["attachments"] = [{"filename": a.filename, "content": base64.b64encode(a.content).decode("ascii")} for a in attachments]
            res = await client.post(self.URL, headers=headers, json=payload)
        if res.status_code >= 300:
            raise EmailError(f"Resend returned {res.status_code}: {res.text[:300]}")
        return res.json()["id"]


class MockEmailService:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, *, to: str, subject: str, html: str, idempotency_key: str | None = None, attachments: Sequence[Attachment] = (),
                   from_name: str | None = None) -> str:
        message_id = f"mock-email-{len(self.sent) + 1}"
        self.sent.append(dict(id=message_id, to=to, subject=subject, html=html, idempotency_key=idempotency_key, attachments=list(attachments), from_name=from_name))
        return message_id
