import asyncio
import json
import time

import pytest
import requests
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


def _response(code: int, status: str, message: str) -> requests.Response:
    r = requests.Response()
    r.status_code = code
    r.reason = status
    r._content = json.dumps({"error": {"code": code, "message": message, "status": status}}).encode()
    return r


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


async def test_pending_counts_waiting_requests_and_close_is_clean():
    gw = GeminiGateway(FakeBackend(), rpm=60)  # 1 s apart
    tasks = [asyncio.create_task(gw.generate_text(str(i))) for i in range(4)]
    await asyncio.sleep(0.2)
    assert gw.pending >= 2
    await gw.aclose()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
