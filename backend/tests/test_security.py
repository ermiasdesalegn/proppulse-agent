"""Log redaction and format, request ids, abuse limits, and row-level security on every table."""
import json
import logging

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from app.observability import REDACTED, JsonFormatter, RedactingFilter, SlidingWindow, redact, request_id_var, secrets_from, setup_logging
from tests.agent_helpers import FakeGateway, make_chat

BOT = "8123456789:AAEhBP0av28mK3sD9xQwErTyUiOpAsDfGhJkLz"


# ---------------- redaction ----------------

@pytest.mark.parametrize("text", [
    f"calling https://api.telegram.org/bot{BOT}/sendMessage", "Authorization: Bearer abcdefghijklmnop123456", "pk.eyJ1IjoiYWJjZGVmZ2hpamtsbW5vcCJ9",
    "re_AbCdEfGhIjKlMnOpQrStUvWx", "AIzaSyA1234567890abcdefghijklmnopqrstu", "-----BEGIN PRIVATE KEY-----\nMIIEvQ\n-----END PRIVATE KEY-----",
])
def test_known_secret_shapes_are_removed(text):
    out = redact(text)
    assert REDACTED in out and BOT not in out and "abcdefghijklmnop123456" not in out and "MIIEvQ" not in out


def test_a_database_url_keeps_its_host_and_loses_its_password():
    out = redact("connect postgresql://postgres.abc:SuperSecretPw@aws-0.pooler.supabase.com:5432/postgres failed")
    assert "SuperSecretPw" not in out and "postgres.abc:" in out and "@aws-0.pooler.supabase.com" in out


def test_configured_secrets_are_removed_wherever_they_appear_and_short_values_are_left_alone():
    assert redact("key=my-admin-key-12345 end", ["my-admin-key-12345"]) == f"key={REDACTED} end"
    assert redact("a short x", ["x"]) == "a short x", "a one-letter secret would shred every message"
    s = Settings(admin_api_key="admin-key-abcdef", cron_secret="", gemini_api_key="gem-key-abcdef")
    assert set(secrets_from(s)) >= {"admin-key-abcdef", "gem-key-abcdef"}


def logger_with(filter_secrets):
    stream = []

    class Capture(logging.Handler):
        def emit(self, record):
            stream.append(self.format(record))

    handler = Capture()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter(filter_secrets))
    log = logging.getLogger(f"test.{id(stream)}")
    log.handlers[:] = [handler]
    log.propagate = False
    log.setLevel(logging.INFO)
    return log, stream


def test_messages_arguments_and_exceptions_are_all_redacted_in_json_logs():
    log, stream = logger_with(["topsecretvalue123"])
    log.info("token is %s", "topsecretvalue123")
    try:
        raise RuntimeError(f"failed with {BOT}")
    except RuntimeError:
        log.exception("boom")
    entries = [json.loads(line) for line in stream]
    assert all("topsecretvalue123" not in json.dumps(e) and BOT not in json.dumps(e) for e in entries)
    assert entries[0]["message"] == f"token is {REDACTED}" and "RuntimeError" in entries[1]["exception"] and entries[0]["level"] == "INFO"


def test_json_logs_carry_the_request_id_and_extra_fields():
    log, stream = logger_with([])
    token = request_id_var.set("req-12345678")
    try:
        log.info("hello", extra={"method": "GET", "path": "/x", "status": 200, "ms": 5})
    finally:
        request_id_var.reset(token)
    assert json.loads(stream[0]) | {"ts": ""} == {"ts": "", "level": "INFO", "logger": log.name, "message": "hello", "request_id": "req-12345678", "method": "GET", "path": "/x", "status": 200, "ms": 5}


def test_setup_logging_installs_one_redacting_handler_and_quiets_httpx():
    root = logging.getLogger()
    saved, level = list(root.handlers), root.level
    try:
        setup_logging(Settings(log_format="json", telegram_bot_token="123456789:" + "A" * 35))
        assert len(root.handlers) == 1 and any(isinstance(f, RedactingFilter) for f in root.handlers[0].filters)
        assert logging.getLogger("httpx").level == logging.WARNING
    finally:
        root.handlers[:], root.level = saved, level  # other tests (and pgserver's exit handler) keep the handlers they expect


# ---------------- sliding window ----------------

def test_the_window_allows_a_limit_then_blocks_then_frees_up():
    now = [0.0]
    w = SlidingWindow(3, 60.0, lambda: now[0])
    for _ in range(3):
        assert w.blocked("a") == 0
        w.hit("a")
    assert w.blocked("a") == pytest.approx(60.0) and w.blocked("b") == 0, "another client is not affected"
    now[0] = 59.0
    assert 0 < w.blocked("a") <= 1.0
    now[0] = 61.0
    assert w.blocked("a") == 0


# ---------------- middleware through the real app ----------------

def client_for(**overrides):
    chat, *_ = make_chat(None, FakeGateway(), admin_api_key="test-admin-key", cron_secret="c", ingest_webhook_secret="hook", telegram_webhook_secret="tg", **overrides)
    app = create_app(use_lifespan=False, settings=chat.settings)
    app.state.settings, app.state.chat = chat.settings, chat
    return app


async def test_every_response_has_a_request_id_and_a_good_incoming_one_is_kept():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client_for()), base_url="http://t") as c:
        generated = (await c.get("/health")).headers["x-request-id"]
        kept = (await c.get("/health", headers={"X-Request-ID": "trace-abc-12345"})).headers["x-request-id"]
        replaced = (await c.get("/health", headers={"X-Request-ID": "bad id with spaces!"})).headers["x-request-id"]
    assert len(generated) == 16 and kept == "trace-abc-12345" and replaced != "bad id with spaces!" and len(replaced) == 16


async def test_a_client_that_keeps_guessing_the_admin_key_is_blocked_then_a_correct_key_waits_too():
    app = client_for()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("203.0.113.9", 1)), base_url="http://t") as c:
        codes = [(await c.get("/api/samples/listing", headers={"X-Admin-Key": f"guess-{i}"})).status_code for i in range(12)]
        blocked = await c.get("/api/samples/listing", headers={"X-Admin-Key": "test-admin-key"})
        health = await c.get("/health")
    assert codes[:10] == [401] * 10 and codes[10:] == [429, 429]
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) >= 1 and blocked.headers["x-request-id"]
    assert health.status_code == 200, "the health check is never limited"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("198.51.100.4", 1)), base_url="http://t") as other:
        assert (await other.get("/health")).status_code == 200
        assert (await other.get("/api/samples/listing", headers={"X-Admin-Key": "test-admin-key"})).status_code == 200, "another client is unaffected"


async def test_successful_requests_never_count_towards_the_block():
    app = client_for(rate_limit_failed_auth_per_minute=3)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        for _ in range(8):
            assert (await c.get("/api/samples/listing", headers={"X-Admin-Key": "test-admin-key"})).status_code == 200


async def test_public_endpoints_are_limited_per_client_and_the_forwarded_address_counts_only_behind_a_trusted_proxy():
    app = client_for(rate_limit_public_per_minute=3)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        codes = [(await c.get("/demo/js-listing")).status_code for _ in range(5)]
        assert codes[:3] == [200] * 3 and codes[3:] == [429, 429]

    spoof = {"X-Forwarded-For": "10.1.1.1"}
    untrusted = client_for(rate_limit_public_per_minute=1)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=untrusted), base_url="http://t") as c:
        assert (await c.get("/demo/js-listing", headers=spoof)).status_code == 200
        assert (await c.get("/demo/js-listing", headers={"X-Forwarded-For": "10.2.2.2"})).status_code == 429, "a spoofed header does not buy a fresh allowance"

    trusted = client_for(rate_limit_public_per_minute=1, trust_proxy_headers=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=trusted), base_url="http://t") as c:
        assert (await c.get("/demo/js-listing", headers=spoof)).status_code == 200
        assert (await c.get("/demo/js-listing", headers={"X-Forwarded-For": "10.2.2.2"})).status_code == 200
        assert (await c.get("/demo/js-listing", headers=spoof)).status_code == 429


# ---------------- database: every table has row-level security ----------------

async def test_every_table_has_row_level_security_enabled(db_conn):
    cur = await db_conn.execute(
        "select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace "
        "where n.nspname = 'public' and c.relkind = 'r' and not c.relrowsecurity order by 1")
    unprotected = [r["relname"] for r in await cur.fetchall()]
    assert unprotected == ["schema_migrations"], "a new table must enable row-level security in its migration (the backend uses the service role, the public API gets nothing)"
