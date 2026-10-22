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
        thinking_budget: int = -1,
    ):
        self._client = client or genai.Client(api_key=api_key)
        self._models = [model, *[m for m in fallback_models if m and m != model]]
        self._embed_model, self._dim = embed_model, embed_dim
        self._timeout = primary_timeout
        self._clock = clock
        self._thinking_budget = thinking_budget  # 0 turns reasoning off (about 7x faster here); negative leaves the model default
        self._skip_until: dict[str, float] = {}
        self._rejects_thinking: set[str] = set()

    async def _call(self, model: str, kwargs: dict[str, Any]):
        """One request to one model, with reasoning switched off where configured and the model allows it."""
        if self._thinking_budget < 0 or model in self._rejects_thinking:
            return await self._client.aio.models.generate_content(model=model, **kwargs)
        config = (kwargs.get("config") or types.GenerateContentConfig()).model_copy(
            update={"thinking_config": types.ThinkingConfig(thinking_budget=self._thinking_budget)}
        )
        try:
            return await self._client.aio.models.generate_content(model=model, **{**kwargs, "config": config})
        except errors.ClientError as exc:
            if getattr(exc, "code", None) != 400:
                raise
            # Some models refuse the setting with a generic 400 that never mentions it, so retry on any 400.
            result = await self._client.aio.models.generate_content(model=model, **kwargs)
            self._rejects_thinking.add(model)  # only remembered once the plain request has worked
            log.warning("%s does not accept thinking_budget=%s; using its default from now on", model, self._thinking_budget)
            return result

    async def _generate(self, **kwargs: Any):
        """Use the first model in the chain that is not cooling down; move down the chain on overload, slowness or quota."""
        last: BaseException | None = None
        for index, model in enumerate(self._models):
            if self._skip_until.get(model, 0) > self._clock():
                continue
            is_last = index == len(self._models) - 1
            try:
                # The last model gets no extra timeout of its own; the gateway's request timeout covers it.
                call = self._call(model, kwargs)
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
    lane: "_Lane"
    attempt: int = 0


class _Lane:
    """An independent rate-limited queue. Generation and embeddings have separate quotas, so they get separate lanes."""

    def __init__(self, name: str, rpm: int):
        if rpm <= 0:
            raise ValueError(f"{name} rpm must be positive")
        self.name, self.interval = name, 60.0 / rpm
        self.queue: asyncio.Queue[_Job] | None = None
        self.worker: asyncio.Task | None = None
        self.next_slot = 0.0


class GeminiGateway:
    def __init__(
        self,
        backend: GeminiBackend,
        rpm: int = 14,
        max_attempts: int = 4,
        backoff: tuple[float, float] = (2.0, 5.0),
        clock: Callable[[], float] = time.monotonic,
        request_timeout: float = 45.0,
        embed_rpm: int = 60,
    ):
        self._backend = backend
        self._timeout = request_timeout
        self._generate = _Lane("generate", rpm)
        self._embed = _Lane("embed", embed_rpm)
        self._max_attempts = max_attempts
        self._backoff = backoff
        self._clock = clock
        self._inflight: set[asyncio.Task] = set()
        self._retrying = 0

    @property
    def _interval(self) -> float:
        return self._generate.interval

    @property
    def pending(self) -> int:
        """Requests waiting for a slot (including ones sleeping before a retry)."""
        queued = sum(lane.queue.qsize() for lane in (self._generate, self._embed) if lane.queue)
        return queued + self._retrying

    def _ensure_started(self, lane: _Lane) -> None:
        if lane.worker is None or lane.worker.done():
            lane.queue = asyncio.Queue()
            lane.next_slot = 0.0
            lane.worker = asyncio.create_task(self._drain(lane))

    async def _drain(self, lane: _Lane) -> None:
        assert lane.queue is not None
        while True:
            job = await lane.queue.get()
            if job.future.cancelled():
                continue
            wait = lane.next_slot - self._clock()
            if wait > 0:
                await asyncio.sleep(wait)
            lane.next_slot = max(self._clock(), lane.next_slot) + lane.interval
            task = asyncio.create_task(self._run(job))
            self._inflight.add(task)
            task.add_done_callback(self._inflight.discard)

    async def _run(self, job: _Job) -> None:
        started = self._clock()
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
                assert job.lane.queue is not None
                await job.lane.queue.put(job)  # retries re-enter the same lane, so they respect its rate cap
            elif not job.future.done():
                job.future.set_exception(exc)
        else:
            log.info("gemini %s call finished in %.1fs (attempt %d)", job.lane.name, self._clock() - started, job.attempt + 1)
            if not job.future.done():
                job.future.set_result(result)

    async def _submit(self, factory: Callable[[], Awaitable[Any]], lane: _Lane | None = None) -> Any:
        lane = lane or self._generate
        self._ensure_started(lane)
        assert lane.queue is not None
        future = asyncio.get_running_loop().create_future()
        await lane.queue.put(_Job(factory, future, lane))
        return await future

    async def aclose(self) -> None:
        lanes = (self._generate, self._embed)
        tasks = [t for t in [*(lane.worker for lane in lanes), *self._inflight] if t is not None]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for lane in lanes:
            while lane.queue is not None and not lane.queue.empty():
                job = lane.queue.get_nowait()
                if not job.future.done():
                    job.future.cancel()

    async def generate_text(self, prompt: str, *, system: str | None = None) -> str:
        return await self._submit(lambda: self._backend.generate_text(prompt, system))

    async def generate_structured(self, prompt: str, schema: type[M], *, system: str | None = None) -> M:
        return await self._submit(lambda: self._backend.generate_structured(prompt, schema, system))

    async def transcribe_audio(self, audio: bytes, *, mime_type: str = "audio/ogg", prompt: str) -> str:
        return await self._submit(lambda: self._backend.transcribe_audio(audio, mime_type, prompt))

    async def embed_query(self, text: str) -> list[float]:
        return (await self._submit(lambda: self._backend.embed([text], "RETRIEVAL_QUERY"), self._embed))[0]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._submit(lambda: self._backend.embed(texts, "RETRIEVAL_DOCUMENT"), self._embed)


@lru_cache
def get_gateway() -> GeminiGateway:
    s = get_settings()
    backend = GenAIBackend(
        s.gemini_api_key, s.gemini_model, s.gemini_embed_model,
        fallback_models=s.fallback_models, primary_timeout=s.gemini_primary_timeout_seconds,
        thinking_budget=s.gemini_thinking_budget,
    )
    return GeminiGateway(backend, rpm=s.gemini_rpm, request_timeout=s.gemini_timeout_seconds, embed_rpm=s.gemini_embed_rpm)
