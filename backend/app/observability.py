"""Structured logs, request ids, secret redaction and abuse limits.

Logs can be written as one JSON object per line (what Cloud Run and most log tools want) and always pass through a filter that removes
secrets, so a stray `log.exception(...)` can never put a token or a password into a log file. Every request gets an id that appears in its
log lines and in the `X-Request-ID` response header, which makes one buyer's problem findable in a sea of lines.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable, Sequence
from contextvars import ContextVar
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

REDACTED = "[redacted]"
PATTERNS = [
    re.compile(r"\d{6,}:[A-Za-z0-9_-]{30,}"),                      # a Telegram bot token (also right after "/bot" in an API URL)
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}"),        # an Authorization header value
    re.compile(r"(?i)(postgres(?:ql)?://[^:/\s]+:)[^@\s]+(@)"),   # the password inside a database URL
    re.compile(r"\b(?:sk|pk)\.[A-Za-z0-9._-]{20,}"),              # Mapbox tokens
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),                      # Google API keys
    re.compile(r"\bAQ\.[0-9A-Za-z_-]{30,}"),                       # Google API keys in the newer format
    re.compile(r"\bre_[A-Za-z0-9_]{20,}"),                        # Resend keys
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
]
SECRET_FIELDS = ("gemini_api_key", "gemini_api_keys", "supabase_service_role_key", "telegram_bot_token", "telegram_webhook_secret", "resend_api_key", "admin_api_key",
                 "cron_secret", "ingest_webhook_secret", "mapbox_token", "database_url", "google_service_account_json")
REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def _replacement(match: re.Match[str]) -> str:
    # The database-URL pattern keeps the user name and the host and hides only the password between them.
    return match.group(1) + REDACTED + match.group(2) if match.re.groups == 2 else REDACTED


def redact(text: str, secrets: Sequence[str] = ()) -> str:
    for value in secrets:
        if value and len(value) >= 8:
            text = text.replace(value, REDACTED)
    for pattern in PATTERNS:
        text = pattern.sub(_replacement, text)
    return text


def secrets_from(settings: Any) -> list[str]:
    found: list[str] = []
    for f in SECRET_FIELDS:
        value = str(getattr(settings, f, "") or "")
        found += [part.strip() for part in value.split(",")] if f == "gemini_api_keys" else [value]  # a list of keys: each key is a secret on its own
    return [v for v in found if v]


class RedactingFilter(logging.Filter):
    """Removes secrets from the message, its arguments and any exception text before a handler sees them."""

    def __init__(self, secrets: list[str] | None = None):
        super().__init__()
        self._secrets = secrets or []

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(str(record.getMessage()), self._secrets)
        record.args = ()
        if record.exc_info and record.exc_info[1] is not None:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info), self._secrets)
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redact(record.exc_text, self._secrets)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"), "level": record.levelname, "logger": record.name, "message": record.getMessage(), "request_id": request_id_var.get()}
        for key in ("method", "path", "status", "ms", "client"):
            if hasattr(record, key):
                entry[key] = getattr(record, key)
        if record.exc_text:
            entry["exception"] = record.exc_text
        return json.dumps(entry, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        rid = request_id_var.get()
        return base if rid == "-" else f"{base} [{rid}]"


def setup_logging(settings: Any) -> None:
    """INFO logs in the chosen format, always redacted. httpx stays quiet because its INFO lines contain request URLs (and so, bot tokens)."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if settings.log_format == "json" else TextFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter(secrets_from(settings)))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ---------------- limits ----------------

class SlidingWindow:
    def __init__(self, limit: int, window: float, clock: Callable[[], float] = time.monotonic):
        self.limit, self.window, self._clock = limit, window, clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _trim(self, key: str) -> deque[float]:
        hits, now = self._hits[key], self._clock()
        while hits and now - hits[0] >= self.window:
            hits.popleft()
        return hits

    def blocked(self, key: str) -> float:
        """0 if `key` may proceed, otherwise the seconds until it may."""
        hits = self._trim(key)
        return 0.0 if len(hits) < self.limit else self.window - (self._clock() - hits[0])

    def hit(self, key: str) -> None:
        self._trim(key).append(self._clock())
        if len(self._hits) > 5000:  # many distinct clients: drop the ones with nothing left in their window
            for idle in [k for k in list(self._hits) if not self._trim(k)]:
                del self._hits[idle]


PUBLIC_PREFIXES = ("/api/webhooks/", "/api/telegram/webhook", "/demo/")


class GuardMiddleware(BaseHTTPMiddleware):
    """Gives each request an id and a log line, slows down clients that hammer public endpoints, and blocks one that keeps guessing keys."""

    def __init__(self, app: Any, *, public_per_minute: int, failed_auth_per_minute: int, trust_proxy: bool = False, clock: Callable[[], float] = time.monotonic):
        super().__init__(app)
        self._public = SlidingWindow(public_per_minute, 60.0, clock)
        self._failures = SlidingWindow(failed_auth_per_minute, 60.0, clock)
        self._trust_proxy = trust_proxy
        self._log = logging.getLogger("proppulse.access")

    def _client(self, request: Request) -> str:
        if self._trust_proxy and (forwarded := request.headers.get("x-forwarded-for")):
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request: Request, call_next: Callable[[Request], Any]) -> Response:
        incoming = request.headers.get("x-request-id", "")
        rid = incoming if REQUEST_ID.match(incoming) else uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        started, client, path = time.monotonic(), self._client(request), request.url.path
        try:
            retry = 0.0
            if path.startswith("/api/") and (wait := self._failures.blocked(client)):
                retry = wait  # too many rejected keys: even a correct one waits until the window passes
            elif path.startswith(PUBLIC_PREFIXES):
                if wait := self._public.blocked(client):
                    retry = wait
                else:
                    self._public.hit(client)
            if retry:
                response: Response = JSONResponse({"detail": "Too many requests"}, status_code=429, headers={"Retry-After": str(int(retry) + 1)})
            else:
                response = await call_next(request)
                if response.status_code in (401, 403) and path.startswith("/api/") and not path.startswith(PUBLIC_PREFIXES):
                    self._failures.hit(client)
            response.headers["X-Request-ID"] = rid
            if path != "/health":
                self._log.info("%s %s -> %s", request.method, path, response.status_code,
                               extra={"method": request.method, "path": path, "status": response.status_code, "ms": round((time.monotonic() - started) * 1000), "client": client})
            return response
        finally:
            request_id_var.reset(token)
