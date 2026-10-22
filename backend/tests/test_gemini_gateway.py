import asyncio
import time

import pytest
from google.genai import errors

from app.gemini import GeminiGateway, is_retryable
from pydantic import BaseModel


class FakeBackend:
    def __init__(self, failures=None):
        self.calls = []
        self.failures = list(failures or [])

    async def _go(self, name, value):
        self.calls.append((name, time.monotonic()))
        if self.failures:
            raise self.failures.pop(0)
        return value

    async def generate_text(self, prompt, system):
        return await self._go("text", f"echo:{prompt}")

    async def generate_structured(self, prompt, schema, system):
        return await self._go("structured", schema(name=prompt))

    async def transcribe_audio(self, audio, mime_type, prompt):
        return await self._go("audio", f"{len(audio)}:{mime_type}")

    async def embed(self, texts, task_type):
        return await self._go("embed", [[float(len(t))] * 3 for t in texts])


def _response(code: int, status: str, message: str) -> dict:
    return {"error": {"code": code, "message": message, "status": status}}


def server_error():
    return errors.ServerError(503, _response(503, "UNAVAILABLE", "overloaded"))


def rate_error():
    return errors.ClientError(429, _response(429, "RESOURCE_EXHAUSTED", "quota"))


def bad_request():
    return errors.ClientError(400, _response(400, "INVALID_ARGUMENT", "bad"))


def test_default_rate_is_14_per_minute():
    assert GeminiGateway(FakeBackend(), rpm=14)._interval == pytest.approx(60 / 14)


def test_only_transient_errors_are_retryable():
    assert is_retryable(server_error()) and is_retryable(rate_error())
    assert not is_retryable(bad_request()) and not is_retryable(ValueError("x"))


async def test_calls_are_spaced_by_the_rate_interval():
    backend = FakeBackend()
    gw = GeminiGateway(backend, rpm=600)  # 0.1 s apart
    await asyncio.gather(*(gw.generate_text(str(i)) for i in range(5)))
    starts = [t for _, t in backend.calls]
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert all(g >= 0.08 for g in gaps), gaps
    assert sum(gaps) < 1.5
    await gw.aclose()


async def test_each_caller_gets_its_own_result():
    gw = GeminiGateway(FakeBackend(), rpm=6000)
    results = await asyncio.gather(*(gw.generate_text(str(i)) for i in range(6)))
    assert results == [f"echo:{i}" for i in range(6)]
    await gw.aclose()


async def test_all_call_types_go_through_the_queue():
    class Out(BaseModel):
        name: str

    backend = FakeBackend()
    gw = GeminiGateway(backend, rpm=6000)
    assert (await gw.generate_structured("a", Out)).name == "a"
    assert await gw.transcribe_audio(b"1234", prompt="p") == "4:audio/ogg"
    assert await gw.embed_query("abc") == [3.0, 3.0, 3.0]
    assert await gw.embed_documents(["a", "bb"]) == [[1.0] * 3, [2.0] * 3]
    assert [n for n, _ in backend.calls] == ["structured", "audio", "embed", "embed"]
    await gw.aclose()


@pytest.mark.parametrize("make_error", [server_error, rate_error])
async def test_transient_errors_are_retried_and_still_rate_limited(make_error):
    backend = FakeBackend(failures=[make_error(), make_error()])
    gw = GeminiGateway(backend, rpm=600, max_attempts=4, backoff=(0.01, 0.02))
    assert await gw.generate_text("x") == "echo:x"
    starts = [t for _, t in backend.calls]
    assert len(starts) == 3
    assert all(b - a >= 0.08 for a, b in zip(starts, starts[1:])), "retries must respect the rate cap"
    await gw.aclose()


async def test_gives_up_after_max_attempts():
    backend = FakeBackend(failures=[server_error() for _ in range(10)])
    gw = GeminiGateway(backend, rpm=6000, max_attempts=3, backoff=(0.01, 0.02))
    with pytest.raises(errors.ServerError):
        await gw.generate_text("x")
    assert len(backend.calls) == 3
    await gw.aclose()


async def test_non_retryable_error_fails_immediately():
    backend = FakeBackend(failures=[bad_request()])
    gw = GeminiGateway(backend, rpm=6000, backoff=(0.01, 0.02))
    with pytest.raises(errors.ClientError):
        await gw.generate_text("x")
    assert len(backend.calls) == 1
    await gw.aclose()


async def test_a_hung_request_times_out_and_is_retried():
    class Hangs(FakeBackend):
        async def generate_text(self, prompt, system):
            self.calls.append(("text", time.monotonic()))
            if len(self.calls) == 1:
                await asyncio.sleep(10)
            return "recovered"

    backend = Hangs()
    gw = GeminiGateway(backend, rpm=6000, request_timeout=0.1, backoff=(0.01, 0.02))
    assert await gw.generate_text("x") == "recovered" and len(backend.calls) == 2
    await gw.aclose()


class FakeModels:
    """Mimics client.aio.models, recording which model each call used."""

    def __init__(self, behaviour):
        self.behaviour, self.used = behaviour, []

    async def generate_content(self, *, model, **kw):
        self.used.append(model)
        return await self.behaviour(model)

    @property
    def aio(self):
        return self


class Reply:
    def __init__(self, text):
        self.text = text


def backend_with(behaviour, **kw):
    from app.gemini import GenAIBackend

    models = FakeModels(behaviour)
    client = type("C", (), {"aio": type("A", (), {"models": models})()})()
    return GenAIBackend("k", "primary", "embed", client=client, **kw), models


async def test_overloaded_primary_model_falls_back_once():
    async def behaviour(model):
        if model == "primary":
            raise server_error()
        return Reply("from fallback")

    backend, models = backend_with(behaviour, fallback_models=["backup"])
    assert await backend.generate_text("hi", None) == "from fallback" and models.used == ["primary", "backup"]


async def test_slow_primary_model_falls_back_once():
    async def behaviour(model):
        if model == "primary":
            await asyncio.sleep(10)
        return Reply("from fallback")

    backend, models = backend_with(behaviour, fallback_models=["backup"], primary_timeout=0.1)
    assert await backend.generate_text("hi", None) == "from fallback" and models.used == ["primary", "backup"]


async def test_no_fallback_when_disabled_and_none_for_bad_requests():
    async def overloaded(model):
        raise server_error()

    backend, models = backend_with(overloaded, fallback_models=[])
    with pytest.raises(errors.ServerError):
        await backend.generate_text("hi", None)
    assert models.used == ["primary"]

    async def invalid(model):
        raise bad_request()

    backend, models = backend_with(invalid, fallback_models=["backup"])
    with pytest.raises(errors.ClientError):
        await backend.generate_text("hi", None)
    assert models.used == ["primary"], "a bad request would fail on the fallback too"


def daily_quota_error():
    return errors.ClientError(429, {"error": {
        "code": 429, "status": "RESOURCE_EXHAUSTED",
        "message": "Quota exceeded for metric generate_content_free_tier_requests, limit: 20, model: m",
        "details": [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}, {"retryDelay": "29714s"}],
    }})


def test_daily_quota_is_recognised_and_never_retried():
    from app.gemini import _cooldown_seconds, is_daily_quota

    assert is_daily_quota(daily_quota_error()) and not is_daily_quota(rate_error())
    assert not is_retryable(daily_quota_error()) and is_retryable(rate_error())
    assert _cooldown_seconds(daily_quota_error()) == 6 * 3600, "Google said 29714 s; capped at 6 h so a bad parse can't lock a model out for a day"
    assert _cooldown_seconds(rate_error()) == 15 and _cooldown_seconds(server_error()) == 30


async def test_spent_daily_quota_moves_down_the_chain_and_the_model_is_skipped_afterwards():
    async def behaviour(model):
        if model == "primary":
            raise daily_quota_error()
        return Reply(f"from {model}")

    backend, models = backend_with(behaviour, fallback_models=["lite", "lite2"])
    assert await backend.generate_text("a", None) == "from lite"
    assert await backend.generate_text("b", None) == "from lite"
    assert models.used == ["primary", "lite", "lite"], "the exhausted model must not be called again"


async def test_cooldown_expires():
    now = [0.0]

    async def behaviour(model):
        if model == "primary" and now[0] < 100:
            raise server_error()
        return Reply(f"from {model}")

    backend, models = backend_with(behaviour, fallback_models=["lite"], clock=lambda: now[0])
    assert await backend.generate_text("a", None) == "from lite"
    now[0] = 500.0
    assert await backend.generate_text("b", None) == "from primary"


async def test_every_model_exhausted_fails_fast_without_retries():
    async def behaviour(model):
        raise daily_quota_error()

    backend, models = backend_with(behaviour, fallback_models=["lite"])
    gw = GeminiGateway(backend, rpm=6000, backoff=(0.01, 0.02))
    with pytest.raises(errors.ClientError):
        await gw.generate_text("x")
    assert models.used == ["primary", "lite"], "daily quota must not trigger gateway retries"
    with pytest.raises(errors.ServerError, match="cooling down"):
        await gw.generate_text("y")
    assert models.used == ["primary", "lite"], "cooling models are not called at all"
    await gw.aclose()


async def test_pending_counts_waiting_requests_and_close_is_clean():
    gw = GeminiGateway(FakeBackend(), rpm=60)  # 1 s apart
    tasks = [asyncio.create_task(gw.generate_text(str(i))) for i in range(4)]
    await asyncio.sleep(0.2)
    assert gw.pending >= 2
    await gw.aclose()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
