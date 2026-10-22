"""Single entry point for every Gemini call.

All requests pass through one leaky-bucket queue so the free-tier rate limit is never exceeded.
Retries (429 / 5xx / network) go back through the same queue, so they respect the cap too.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Awaitable, Callable, Protocol, Sequence, TypeVar

import httpx
from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from app.config import get_settings

log = logging.getLogger(__name__)

M = TypeVar("M", bound=BaseModel)


def is_daily_quota(exc: BaseException) -> bool:
    """A 429 that says the per-day quota is spent: retrying cannot help until the quota resets."""
    return isinstance(exc, errors.ClientError) and getattr(exc, "code", None) == 429 and "PerDay" in str(exc)


def is_retryable(exc: BaseException) -> bool:
    if is_daily_quota(exc):
        return False
    if isinstance(exc, errors.ServerError):
        return True
    if isinstance(exc, errors.ClientError):
        return getattr(exc, "code", None) == 429
    return isinstance(exc, (asyncio.TimeoutError, httpx.TransportError))


def _cooldown_seconds(exc: BaseException) -> float:
    if is_daily_quota(exc):
        match = re.search(r"retryDelay'?\"?: ?'?\"?(\d+)", str(exc))
        return min(float(match.group(1)), 6 * 3600) if match else 3600.0
    if isinstance(exc, errors.ClientError):
        return 15.0
    return 30.0


class GeminiBackend(Protocol):
    async def generate_text(self, prompt: str, system: str | None) -> str: ...
    async def generate_structured(self, prompt: str, schema: type[M], system: str | None) -> M: ...
    async def transcribe_audio(self, audio: bytes, mime_type: str, prompt: str) -> str: ...
    async def embed(self, texts: list[str], task_type: str) -> list[list[float]]: ...


class GenAIBackend:
    """Real calls through the google-genai SDK."""

    def __init__(
        self,
        api_key: str,
        model: str,
        embed_model: str,
        embed_dim: int = 768,
        *,
        fallback_models: Sequence[str] = (),
        primary_timeout: float = 20.0,
        client: Any = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._client = client or genai.Client(api_key=api_key)
        self._models = [model, *[m for m in fallback_models if m and m != model]]
        self._embed_model, self._dim = embed_model, embed_dim
        self._timeout = primary_timeout
        self._clock = clock
        self._skip_until: dict[str, float] = {}

    async def _generate(self, **kwargs: Any):
        """Use the first model in the chain that is not cooling down; move down the chain on overload, slowness or quota."""
        last: BaseException | None = None
        for index, model in enumerate(self._models):
            if self._skip_until.get(model, 0) > self._clock():
                continue
            is_last = index == len(self._models) - 1
            try:
                # The last model gets no extra timeout of its own; the gateway's request timeout covers it.
                call = self._client.aio.models.generate_content(model=model, **kwargs)
                return await (call if is_last else asyncio.wait_for(call, self._timeout))
            except (errors.ServerError, errors.ClientError, asyncio.TimeoutError) as exc:
                if isinstance(exc, errors.ClientError) and not is_daily_quota(exc) and getattr(exc, "code", None) != 429:
                    raise  # a bad request would fail on every model
                last = exc
                self._skip_until[model] = self._clock() + _cooldown_seconds(exc)
                log.warning("%s unavailable (%s); trying the next model", model, type(exc).__name__)
        if last is not None:
            raise last
        raise errors.ServerError(503, {"error": {"message": "every configured Gemini model is cooling down", "status": "UNAVAILABLE"}})

    async def generate_text(self, prompt: str, system: str | None) -> str:
        res = await self._generate(contents=prompt, config=types.GenerateContentConfig(system_instruction=system))
        return res.text or ""

    async def generate_structured(self, prompt: str, schema: type[M], system: str | None) -> M:
        res = await self._generate(
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system, response_mime_type="application/json", response_schema=schema
            ),
        )
        if res.parsed is None:
            raise ValueError("Gemini returned output that does not match the requested schema")
        return res.parsed

    async def transcribe_audio(self, audio: bytes, mime_type: str, prompt: str) -> str:
        res = await self._generate(contents=[types.Part.from_bytes(data=audio, mime_type=mime_type), prompt])
        return res.text or ""

    async def embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        res = await self._client.aio.models.embed_content(
            model=self._embed_model,
            contents=texts,
            config=types.EmbedContentConfig(task_type=task_type, output_dimensionality=self._dim),
        )
        return [e.values for e in res.embeddings]


@dataclass
class _Job:
    factory: Callable[[], Awaitable[Any]]
    future: asyncio.Future
    attempt: int = 0


class GeminiGateway:
    def __init__(
        self,
        backend: GeminiBackend,
        rpm: int = 14,
        max_attempts: int = 4,
        backoff: tuple[float, float] = (2.0, 5.0),
        clock: Callable[[], float] = time.monotonic,
        request_timeout: float = 45.0,
    ):
        if rpm <= 0:
            raise ValueError("rpm must be positive")
        self._backend = backend
        self._timeout = request_timeout
        self._interval = 60.0 / rpm
        self._max_attempts = max_attempts
        self._backoff = backoff
        self._clock = clock
        self._queue: asyncio.Queue[_Job] | None = None
        self._worker: asyncio.Task | None = None
        self._inflight: set[asyncio.Task] = set()
        self._retrying = 0
        self._next_slot = 0.0

    @property
    def pending(self) -> int:
        """Requests waiting for a slot (including ones sleeping before a retry)."""
        return (self._queue.qsize() if self._queue else 0) + self._retrying

    def _ensure_started(self) -> None:
        if self._worker is None or self._worker.done():
            self._queue = asyncio.Queue()
            self._next_slot = 0.0
            self._worker = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        assert self._queue is not None
        while True:
            job = await self._queue.get()
            if job.future.cancelled():
                continue
            wait = self._next_slot - self._clock()
            if wait > 0:
                await asyncio.sleep(wait)
            self._next_slot = max(self._clock(), self._next_slot) + self._interval
            task = asyncio.create_task(self._run(job))
            self._inflight.add(task)
            task.add_done_callback(self._inflight.discard)

    async def _run(self, job: _Job) -> None:
        try:
            # A hung request is treated like any other transient failure: it is retried through the queue.
            result = await asyncio.wait_for(job.factory(), self._timeout)
        except asyncio.CancelledError:
            job.future.cancel()
            raise
        except Exception as exc:
            if is_retryable(exc) and job.attempt + 1 < self._max_attempts:
                delay = min(random.uniform(*self._backoff) * (2**job.attempt), 30.0)
                self._retrying += 1
                try:
                    await asyncio.sleep(delay)
                finally:
                    self._retrying -= 1
                job.attempt += 1
                assert self._queue is not None
                await self._queue.put(job)
            elif not job.future.done():
                job.future.set_exception(exc)
        else:
            if not job.future.done():
                job.future.set_result(result)

    async def _submit(self, factory: Callable[[], Awaitable[Any]]) -> Any:
        self._ensure_started()
        assert self._queue is not None
        future = asyncio.get_running_loop().create_future()
        await self._queue.put(_Job(factory, future))
        return await future

    async def aclose(self) -> None:
        tasks = [t for t in [self._worker, *self._inflight] if t is not None]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        while self._queue is not None and not self._queue.empty():
            job = self._queue.get_nowait()
            if not job.future.done():
                job.future.cancel()

    async def generate_text(self, prompt: str, *, system: str | None = None) -> str:
        return await self._submit(lambda: self._backend.generate_text(prompt, system))

    async def generate_structured(self, prompt: str, schema: type[M], *, system: str | None = None) -> M:
        return await self._submit(lambda: self._backend.generate_structured(prompt, schema, system))

    async def transcribe_audio(self, audio: bytes, *, mime_type: str = "audio/ogg", prompt: str) -> str:
        return await self._submit(lambda: self._backend.transcribe_audio(audio, mime_type, prompt))

    async def embed_query(self, text: str) -> list[float]:
        return (await self._submit(lambda: self._backend.embed([text], "RETRIEVAL_QUERY")))[0]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._submit(lambda: self._backend.embed(texts, "RETRIEVAL_DOCUMENT"))


@lru_cache
def get_gateway() -> GeminiGateway:
    s = get_settings()
    backend = GenAIBackend(
        s.gemini_api_key, s.gemini_model, s.gemini_embed_model,
        fallback_models=s.fallback_models, primary_timeout=s.gemini_primary_timeout_seconds,
    )
    return GeminiGateway(backend, rpm=s.gemini_rpm, request_timeout=s.gemini_timeout_seconds)
