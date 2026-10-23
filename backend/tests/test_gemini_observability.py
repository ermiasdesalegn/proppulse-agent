import asyncio

import pytest
from pydantic import BaseModel

from app.calllog import ModelCallLog, to_row
from app.gemini import CALL_CONTEXT, CALL_INFO, GeminiGateway
from app.gemini_replay import RecordingBackend, RecordingStore, ReplayBackend, ReplayMiss, request_key
from tests.test_gemini_gateway import FakeBackend, server_error


class Answer(BaseModel):
    name: str


class InfoBackend(FakeBackend):
    """Reports a model and token counts the way GenAIBackend does."""

    async def _go(self, name, value):
        value = await super()._go(name, value)
        if info := CALL_INFO.get():
            info.model, info.input_tokens, info.output_tokens = "model-a", 12, 5
        return value


async def test_every_attempt_is_recorded_with_its_node_model_tokens_and_outcome():
    seen = []
    gw = GeminiGateway(InfoBackend(failures=[server_error()]), rpm=6000, backoff=(0.0, 0.0), recorder=seen.append)
    token = CALL_CONTEXT.set({"node": "router", "buyer_id": "b-1"})
    try:
        assert (await gw.generate_structured("hi", Answer)).name == "hi"
    finally:
        CALL_CONTEXT.reset(token)
    await gw.aclose()
    assert [(r["outcome"], r["attempt"]) for r in seen] == [("error:ServerError", 1), ("ok", 2)]
    ok = seen[1]
    assert (ok["operation"], ok["schema"], ok["lane"], ok["node"], ok["buyer_id"]) == ("generate_structured", "Answer", "generate", "router", "b-1")
    assert (ok["model"], ok["input_tokens"], ok["output_tokens"]) == ("model-a", 12, 5) and ok["latency_ms"] >= 0


async def test_a_recorder_that_raises_never_breaks_the_call():
    def broken(_):
        raise RuntimeError("disk full")

    gw = GeminiGateway(FakeBackend(), rpm=6000, recorder=broken)
    assert await gw.generate_text("x") == "echo:x"
    await gw.aclose()


async def test_repeated_search_texts_are_embedded_once():
    backend = FakeBackend()
    gw = GeminiGateway(backend, rpm=6000, embed_rpm=6000)
    one, two, other = await gw.embed_query("2BR in Bole"), await gw.embed_query("2BR in Bole"), await gw.embed_query("studio")
    assert one == two and other != one
    assert [c[0] for c in backend.calls].count("embed") == 2 and gw.embed_cache_hits == 1
    one.append(99.0)
    assert len(await gw.embed_query("2BR in Bole")) == 3, "a caller changing its copy cannot damage the cache"
    await gw.aclose()


async def test_the_embedding_cache_is_bounded_and_forgets_the_oldest_text():
    backend = FakeBackend()
    gw = GeminiGateway(backend, rpm=6000, embed_rpm=6000, embed_cache_size=2)
    for text in ("a", "bb", "ccc"):
        await gw.embed_query(text)
    await gw.embed_query("a")  # evicted, so it is embedded again
    assert [c[0] for c in backend.calls].count("embed") == 4
    await gw.aclose()


async def test_document_embeddings_are_not_cached():
    backend = FakeBackend()
    gw = GeminiGateway(backend, rpm=6000, embed_rpm=6000)
    await gw.embed_documents(["x"])
    await gw.embed_documents(["x"])
    assert [c[0] for c in backend.calls].count("embed") == 2
    await gw.aclose()


# ---------------- record and replay ----------------

class Scripted:
    def __init__(self):
        self.calls = 0

    async def generate_text(self, prompt, system):
        self.calls += 1
        return f"text for {prompt}"

    async def generate_structured(self, prompt, schema, system):
        self.calls += 1
        return schema(name=prompt.upper())

    async def transcribe_audio(self, audio, mime_type, prompt):
        self.calls += 1
        return "voice words"

    async def describe_image(self, image, mime_type, prompt, schema, system):
        self.calls += 1
        return schema(name="plan")

    async def embed(self, texts, task_type):
        self.calls += 1
        return [[0.123456789, -1.0] for _ in texts]


async def test_what_was_recorded_is_replayed_without_the_real_backend(tmp_path):
    path = tmp_path / "rec.jsonl"
    real = Scripted()
    rec = RecordingBackend(real, RecordingStore(path))
    assert await rec.generate_text("p", "sys") == "text for p"
    assert (await rec.generate_structured("q", Answer, None)).name == "Q"
    assert await rec.transcribe_audio(b"audio", "audio/ogg", "t") == "voice words"
    assert (await rec.describe_image(b"img", "image/png", "d", Answer, None)).name == "plan"
    assert await rec.embed(["a", "b"], "RETRIEVAL_QUERY") == [[0.123456789, -1.0]] * 2

    replay = ReplayBackend(RecordingStore(path))  # a fresh store, read from disk
    assert await replay.generate_text("p", "sys") == "text for p"
    assert (await replay.generate_structured("q", Answer, None)) == Answer(name="Q")
    assert await replay.transcribe_audio(b"audio", "audio/ogg", "t") == "voice words"
    assert (await replay.describe_image(b"img", "image/png", "d", Answer, None)) == Answer(name="plan")
    assert await replay.embed(["a", "b"], "RETRIEVAL_QUERY") == [[0.123457, -1.0]] * 2, "vectors are stored to six decimals"
    assert real.calls == 5


async def test_a_request_that_was_never_recorded_fails_loudly(tmp_path):
    replay = ReplayBackend(RecordingStore(tmp_path / "empty.jsonl"))
    with pytest.raises(ReplayMiss, match="generate_text"):
        await replay.generate_text("never asked", None)


async def test_the_answer_depends_on_everything_that_was_asked(tmp_path):
    rec = RecordingBackend(Scripted(), RecordingStore(tmp_path / "r.jsonl"))
    await rec.generate_text("p", "system one")
    replay = ReplayBackend(RecordingStore(tmp_path / "r.jsonl"))
    with pytest.raises(ReplayMiss):
        await replay.generate_text("p", "system two")
    with pytest.raises(ReplayMiss):
        await replay.generate_text("p ", "system one")
    assert request_key("embed", texts=["a"]) != request_key("embed", texts=["b"])


async def test_recording_the_same_request_twice_stores_it_once(tmp_path):
    path = tmp_path / "r.jsonl"
    rec = RecordingBackend(Scripted(), RecordingStore(path))
    await rec.generate_text("p", None)
    await rec.generate_text("p", None)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


# ---------------- the call log writer ----------------

class FakePool:
    def __init__(self, fail=False):
        self.rows, self.fail = [], fail

    def connection(self):
        pool = self

        class Conn:
            async def __aenter__(self_):
                return self_

            async def __aexit__(self_, *a):
                return False

            def cursor(self_):
                return Cursor()

        class Cursor:
            async def __aenter__(self_):
                return self_

            async def __aexit__(self_, *a):
                return False

            async def executemany(self_, sql, rows):
                if pool.fail:
                    raise RuntimeError("db down")
                pool.rows.extend(rows)

        return Conn()


class Bus:
    def __init__(self):
        self.events = []

    def publish(self, kind, /, **data):
        self.events.append((kind, data))


def a_record(**over):
    return {"operation": "generate_text", "schema": None, "lane": "generate", "model": "m", "outcome": "ok", "latency_ms": 5, "attempt": 1,
            "fallbacks": ["x"], "input_tokens": 1, "output_tokens": 2, "node": "router", "buyer_id": "b", **over}


def test_a_record_becomes_a_table_row():
    row = to_row(a_record(schema="Extraction"))
    assert row["schema_name"] == "Extraction" and row["fallbacks"] == ["x"] and row["thread_id"] is None and "schema" not in row


async def test_logged_calls_are_written_in_batches_and_announced_to_the_dashboard():
    pool, bus = FakePool(), Bus()
    log = ModelCallLog(pool, bus)
    await log.start()
    for i in range(3):
        log.record(a_record(latency_ms=i))
    await asyncio.sleep(0.5)
    await log.stop()
    assert [r["latency_ms"] for r in pool.rows] == [0, 1, 2]
    assert [k for k, _ in bus.events] == ["model_call"] * 3 and bus.events[0][1]["node"] == "router"


async def test_a_full_queue_drops_rows_instead_of_blocking():
    log = ModelCallLog(FakePool(), capacity=2)
    for _ in range(5):
        log.record(a_record())
    assert log.dropped == 3


async def test_a_database_failure_is_survived_and_counted():
    log = ModelCallLog(FakePool(fail=True))
    log.record(a_record())
    await log.flush()
    assert log.dropped == 1


# ---------------- webhook registration ----------------

class RecordingTelegram:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    async def set_webhook(self, url, secret):
        if self.fail:
            raise RuntimeError("telegram unreachable")
        self.calls.append((url, secret))


class Cfg:
    def __init__(self, base="https://api.example.app/", secret="s3cret"):
        self.public_base_url, self.telegram_webhook_secret = base, secret


async def test_webhook_mode_registers_the_public_address_with_the_secret():
    from app.main import register_webhook

    tg = RecordingTelegram()
    assert await register_webhook(Cfg(), tg) == "webhook"
    assert tg.calls == [("https://api.example.app/api/telegram/webhook", "s3cret")]


@pytest.mark.parametrize("cfg", [Cfg(base=""), Cfg(base="http://insecure.example"), Cfg(secret="")])
async def test_webhook_mode_refuses_to_start_without_https_and_a_secret(cfg):
    from app.main import register_webhook

    tg = RecordingTelegram()
    assert (await register_webhook(cfg, tg)).startswith("unavailable") and tg.calls == []


async def test_a_telegram_outage_at_start_up_is_reported_not_fatal():
    from app.main import register_webhook

    assert "telegram unreachable" in await register_webhook(Cfg(), RecordingTelegram(fail=True))
