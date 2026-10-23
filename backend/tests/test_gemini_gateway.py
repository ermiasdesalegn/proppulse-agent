import asyncio
import time

import pytest
from google.genai import errors
from pydantic import BaseModel

from app.gemini import GeminiGateway, is_retryable


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


async def test_embeddings_do_not_wait_behind_text_calls():
    backend = FakeBackend()
    gw = GeminiGateway(backend, rpm=6, embed_rpm=6000)  # text calls 10 s apart, embeddings effectively unlimited
    t0 = time.monotonic()
    await gw.generate_text("first")  # takes the first (immediate) text slot
    await asyncio.gather(*(gw.embed_query(f"q{i}") for i in range(5)))
    assert time.monotonic() - t0 < 1.0, "five embeddings must not queue behind the 10 s text spacing"
    await gw.aclose()


async def test_text_and_embedding_lanes_each_keep_their_own_rate_cap():
    backend = FakeBackend()
    gw = GeminiGateway(backend, rpm=600, embed_rpm=600)  # 0.1 s apart in each lane
    await asyncio.gather(*(gw.embed_query(f"q{i}") for i in range(4)))
    starts = [t for n, t in backend.calls if n == "embed"]
    assert all(b - a >= 0.08 for a, b in zip(starts, starts[1:])), "the embedding lane still spaces its own requests"
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
        self.behaviour, self.used, self.configs = behaviour, [], []

    async def generate_content(self, *, model, **kw):
        self.used.append(model)
        self.configs.append(kw.get("config"))
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


async def test_reasoning_is_switched_off_for_every_text_call():
    async def ok(model):
        return Reply("fine")

    backend, models = backend_with(ok, thinking_budget=0)
    await backend.generate_text("hi", "be brief")
    await backend.transcribe_audio(b"x", "audio/ogg", "transcribe")
    assert [c.thinking_config.thinking_budget for c in models.configs] == [0, 0]
    assert models.configs[0].system_instruction == "be brief", "the caller's own settings are kept"


async def test_a_negative_budget_leaves_the_model_default_alone():
    async def ok(model):
        return Reply("fine")

    backend, models = backend_with(ok, thinking_budget=-1)
    await backend.generate_text("hi", None)
    assert models.configs[0].thinking_config is None


async def test_a_model_that_rejects_the_thinking_setting_is_retried_without_it():
    attempts = []

    async def picky(model):
        attempts.append(model)
        if len(attempts) == 1:
            raise errors.ClientError(400, {"error": {"code": 400, "message": "This model only works in thinking mode.", "status": "INVALID_ARGUMENT"}})
        return Reply("fine")

    backend, models = backend_with(picky, thinking_budget=0)
    assert await backend.generate_text("hi", None) == "fine"
    assert attempts == ["primary", "primary"]
    assert models.configs[0].thinking_config.thinking_budget == 0 and models.configs[1].thinking_config is None


async def test_a_generic_400_on_the_thinking_setting_is_retried_and_remembered():
    """gemini-3.5-flash-lite answers this with 'Request contains an invalid argument' and never mentions thinking."""
    seen = []

    async def picky(model):
        return Reply("fine")

    backend, models = backend_with(picky, thinking_budget=0)
    original = models.generate_content

    async def reject_thinking(*, model, **kw):
        cfg = kw.get("config")
        seen.append(cfg is not None and cfg.thinking_config is not None)
        if seen[-1]:
            raise errors.ClientError(400, {"error": {"code": 400, "message": "Request contains an invalid argument.", "status": "INVALID_ARGUMENT"}})
        return Reply("fine")

    models.generate_content = reject_thinking
    assert await backend.generate_text("a", None) == "fine"
    assert await backend.generate_text("b", None) == "fine"
    assert seen == [True, False, False], "after one rejection the model is called without the setting straight away"


async def test_a_genuinely_bad_request_still_fails_and_does_not_poison_the_model():
    async def always_bad(model):
        raise bad_request()

    backend, models = backend_with(always_bad, thinking_budget=0)
    with pytest.raises(errors.ClientError):
        await backend.generate_text("hi", None)
    assert "primary" not in backend._rejects_thinking, "a failed retry must not mark the model as rejecting the setting"


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


# ---------------- several API keys: the next key takes over when one is out of quota ----------------

def keyed_backend(behaviours, **kw):
    """One fake client per key; `behaviours` maps the key's position to an async function of the model name."""
    from app.gemini import GenAIBackend

    all_models, clients = [], []
    for position in range(len(behaviours)):
        models = FakeModels(behaviours[position])
        all_models.append(models)
        clients.append(type("C", (), {"aio": type("A", (), {"models": models})()})())
    return GenAIBackend("unused", "primary", "embed", clients=clients, **kw), all_models


async def test_when_every_model_on_a_key_is_out_of_quota_the_next_key_is_used_and_stays_in_use():
    async def spent(model):
        raise daily_quota_error()

    async def fine(model):
        return Reply(f"key two answered with {model}")

    backend, (first, second) = keyed_backend([spent, fine], fallback_models=["lite"])
    assert await backend.generate_text("a", None) == "key two answered with primary"
    assert first.used == ["primary", "lite"], "the whole chain on key one was tried first"
    assert await backend.generate_text("b", None) == "key two answered with primary"
    assert first.used == ["primary", "lite"], "key one is not asked again while it is spent"
    assert second.used == ["primary", "primary"]


async def test_a_rate_limit_also_moves_to_the_next_key_and_the_whole_pool_can_run_dry():
    async def limited(model):
        raise rate_error()

    async def fine(model):
        return Reply("third key")

    backend, models = keyed_backend([limited, limited, fine])
    assert await backend.generate_text("a", None) == "third key" and [len(m.used) for m in models] == [1, 1, 1]

    dry, _ = keyed_backend([limited, limited])
    with pytest.raises(errors.ClientError):
        await dry.generate_text("a", None)


async def test_a_bad_request_does_not_burn_through_the_keys():
    async def bad(model):
        raise bad_request()

    async def fine(model):
        return Reply("never reached")

    backend, (first, second) = keyed_backend([bad, fine])
    with pytest.raises(errors.ClientError):
        await backend.generate_text("a", None)
    assert second.used == [], "a request that is wrong would be wrong on every key"


async def test_the_first_key_is_tried_again_after_six_hours():
    now = [0.0]
    calls = {"first_ok": False}

    async def first_key(model):
        if not calls["first_ok"]:
            raise daily_quota_error()
        return Reply("first key again")

    async def second_key(model):
        return Reply("second key")

    backend, _ = keyed_backend([first_key, second_key], clock=lambda: now[0])
    assert await backend.generate_text("a", None) == "second key"
    calls["first_ok"] = True
    now[0] = 6 * 3600 + 10
    assert await backend.generate_text("b", None) == "first key again"


async def test_embeddings_rotate_keys_too():
    from app.gemini import GenAIBackend

    class Embed:
        def __init__(self, fail):
            self.fail, self.calls = fail, 0

        @property
        def aio(self):
            return self

        @property
        def models(self):
            return self

        async def embed_content(self, **kw):
            self.calls += 1
            if self.fail:
                raise rate_error()
            return type("R", (), {"embeddings": [type("E", (), {"values": [0.5] * 3})()]})()

    one, two = Embed(True), Embed(False)
    clients = [type("C", (), {"aio": one})(), type("C", (), {"aio": two})()]
    backend = GenAIBackend("k", "primary", "embed", clients=clients, embed_dim=3)
    assert await backend.embed(["x"], "RETRIEVAL_QUERY") == [[0.5] * 3] and (one.calls, two.calls) == (1, 1)
    await backend.embed(["y"], "RETRIEVAL_QUERY")
    assert (one.calls, two.calls) == (1, 2), "the second key stays in use"


def test_the_key_list_is_read_from_the_environment_and_every_key_is_redacted():
    from app.config import Settings
    from app.observability import redact, secrets_from

    s = Settings(gemini_api_key="AQ.first-key-0000000000000000000000000000000", gemini_api_keys="AQ.second-key-000000000000000000000000000000, \"AQ.third-key-0000000000000000000000000000000\",AQ.first-key-0000000000000000000000000000000")
    assert s.gemini_key_list == ["AQ.first-key-0000000000000000000000000000000", "AQ.second-key-000000000000000000000000000000", "AQ.third-key-0000000000000000000000000000000"]
    text = redact("keys: " + " ".join(s.gemini_key_list), secrets_from(s))
    assert "AQ." not in text and text.count("[redacted]") == 3
    assert redact("a stray AQ.Ab8RN6JWddG9GfzHt186BsDKyvOQ8naigqWLQm9gOb6cQmCfAA in a log") == "a stray [redacted] in a log", "the newer key format is recognised even when it is not configured"
    assert Settings(gemini_api_key="", gemini_api_keys="").gemini_key_list == []
