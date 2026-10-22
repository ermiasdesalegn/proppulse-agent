"""Single entry point for every Gemini call.

All requests pass through one leaky-bucket queue so the free-tier rate limit is never exceeded.
Retries (429 / 5xx / network) go back through the same queue, so they respect the cap too.
"""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Awaitable, Callable, Protocol, TypeVar

import httpx
from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from app.config import get_settings

M = TypeVar("M", bound=BaseModel)


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, errors.ServerError):
        return True
    if isinstance(exc, errors.ClientError):
        return getattr(exc, "code", None) == 429
    return isinstance(exc, (asyncio.TimeoutError, httpx.TransportError))


class GeminiBackend(Protocol):
    async def generate_text(self, prompt: str, system: str | None) -> str: ...
    async def generate_structured(self, prompt: str, schema: type[M], system: str | None) -> M: ...
    async def transcribe_audio(self, audio: bytes, mime_type: str, prompt: str) -> str: ...
    async def embed(self, texts: list[str], task_type: str) -> list[list[float]]: ...


class GenAIBackend:
    """Real calls through the google-genai SDK."""

    def __init__(self, api_key: str, model: str, embed_model: str, embed_dim: int = 768):
        self._client = genai.Client(api_key=api_key)
        self._model, self._embed_model, self._dim = model, embed_model, embed_dim

    async def generate_text(self, prompt: str, system: str | None) -> str:
        res = await self._client.aio.models.generate_content(
            model=self._model, contents=prompt, config=types.GenerateContentConfig(system_instruction=system)
        )
        return res.text or ""

    async def generate_structured(self, prompt: str, schema: type[M], system: str | None) -> M:
        res = await self._client.aio.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system, response_mime_type="application/json", response_schema=schema
            ),
        )
        if res.parsed is None:
            raise ValueError("Gemini returned output that does not match the requested schema")
        return res.parsed

    async def transcribe_audio(self, audio: bytes, mime_type: str, prompt: str) -> str:
        res = await self._client.aio.models.generate_content(
            model=self._model,
            contents=[types.Part.from_bytes(data=audio, mime_type=mime_type), prompt],
        )
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
    ):
        if rpm <= 0:
            raise ValueError("rpm must be positive")
        self._backend = backend
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
            result = await job.factory()
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
    backend = GenAIBackend(s.gemini_api_key, s.gemini_model, s.gemini_embed_model)
    return GeminiGateway(backend, rpm=s.gemini_rpm)
