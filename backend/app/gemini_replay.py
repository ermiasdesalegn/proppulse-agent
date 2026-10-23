"""Record real Gemini answers once, replay them forever.

Tests and evaluations need the model's behaviour without its daily quota. `RecordingBackend` wraps the real backend and appends
every answer to a JSONL file; `ReplayBackend` serves those answers back, keyed by exactly what was asked. A request that was never
recorded fails loudly (`ReplayMiss`) instead of silently calling the network, so a changed prompt shows up as a missing recording.
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

M = TypeVar("M", bound=BaseModel)


class ReplayMiss(RuntimeError):
    """A request that has no recording. Re-run the evaluation in record mode to add it."""


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def schema_id(schema: type[BaseModel]) -> str:
    """The schema's name and a fingerprint of its fields: change a field or its description and old recordings stop matching."""
    return f"{schema.__name__}:{_digest(json.dumps(schema.model_json_schema(), sort_keys=True).encode('utf-8'))[:8]}"


def request_key(operation: str, **parts: Any) -> str:
    return _digest(json.dumps({"op": operation, **parts}, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8"))


class RecordingStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._entries: dict[str, Any] = {}
        self._lock = threading.Lock()
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    self._entries[row["key"]] = row["value"]

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, key: str) -> Any:
        return self._entries.get(key)

    def has(self, key: str) -> bool:
        return key in self._entries

    def put(self, key: str, operation: str, value: Any, hint: str = "") -> None:
        with self._lock:
            if key in self._entries:
                return
            self._entries[key] = value
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"key": key, "op": operation, "hint": hint[:80], "value": value}, ensure_ascii=False) + "\n")


def _keys(operation: str, prompt: str = "", system: str | None = None, schema: str | None = None, blob: bytes | None = None,
          mime: str | None = None, texts: list[str] | None = None, task: str | None = None) -> str:
    return request_key(operation, prompt=prompt, system=system, schema=schema, blob=_digest(blob) if blob is not None else None,
                       mime=mime, texts=texts, task=task)


class RecordingBackend:
    """Fills the recording: a request already recorded is answered from it, anything new goes to the real backend and is kept.

    Because known requests cost nothing, a recording run that stops (the free tier's daily limit) is simply run again to continue."""

    def __init__(self, inner: Any, store: RecordingStore):
        self._inner, self._store = inner, store
        self._replay = ReplayBackend(store)
        self.live_calls = 0

    async def generate_text(self, prompt: str, system: str | None) -> str:
        try:
            return await self._replay.generate_text(prompt, system)
        except ReplayMiss:
            self.live_calls += 1
            out = await self._inner.generate_text(prompt, system)
            self._store.put(_keys("generate_text", prompt, system), "generate_text", out, prompt)
            return out

    async def generate_structured(self, prompt: str, schema: type[M], system: str | None) -> M:
        try:
            return await self._replay.generate_structured(prompt, schema, system)
        except ReplayMiss:
            self.live_calls += 1
            out = await self._inner.generate_structured(prompt, schema, system)
            self._store.put(_keys("generate_structured", prompt, system, schema_id(schema)), "generate_structured", out.model_dump(mode="json"), prompt)
            return out

    async def transcribe_audio(self, audio: bytes, mime_type: str, prompt: str) -> str:
        try:
            return await self._replay.transcribe_audio(audio, mime_type, prompt)
        except ReplayMiss:
            self.live_calls += 1
            out = await self._inner.transcribe_audio(audio, mime_type, prompt)
            self._store.put(_keys("transcribe_audio", prompt, blob=audio, mime=mime_type), "transcribe_audio", out, prompt)
            return out

    async def describe_image(self, image: bytes, mime_type: str, prompt: str, schema: type[M], system: str | None) -> M:
        try:
            return await self._replay.describe_image(image, mime_type, prompt, schema, system)
        except ReplayMiss:
            self.live_calls += 1
            out = await self._inner.describe_image(image, mime_type, prompt, schema, system)
            self._store.put(_keys("describe_image", prompt, system, schema_id(schema), blob=image, mime=mime_type), "describe_image", out.model_dump(mode="json"), prompt)
            return out

    async def embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        try:
            return await self._replay.embed(texts, task_type)
        except ReplayMiss:
            self.live_calls += 1
            out = await self._inner.embed(texts, task_type)
            self._store.put(_keys("embed", texts=texts, task=task_type), "embed", [[round(float(x), 6) for x in v] for v in out], " | ".join(texts))
            return out


class ReplayBackend:
    """Answers only from the recording."""

    def __init__(self, store: RecordingStore):
        self._store = store

    def _find(self, key: str, operation: str, hint: str) -> Any:
        if not self._store.has(key):
            raise ReplayMiss(f"no recording for {operation}: {' '.join(hint.split())[:120]!r}")
        return self._store.get(key)

    async def generate_text(self, prompt: str, system: str | None) -> str:
        return self._find(_keys("generate_text", prompt, system), "generate_text", prompt)

    async def generate_structured(self, prompt: str, schema: type[M], system: str | None) -> M:
        return schema.model_validate(self._find(_keys("generate_structured", prompt, system, schema_id(schema)), "generate_structured", prompt))

    async def transcribe_audio(self, audio: bytes, mime_type: str, prompt: str) -> str:
        return self._find(_keys("transcribe_audio", prompt, blob=audio, mime=mime_type), "transcribe_audio", prompt)

    async def describe_image(self, image: bytes, mime_type: str, prompt: str, schema: type[M], system: str | None) -> M:
        return schema.model_validate(self._find(_keys("describe_image", prompt, system, schema_id(schema), blob=image, mime=mime_type), "describe_image", prompt))

    async def embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        return self._find(_keys("embed", texts=texts, task=task_type), "embed", " | ".join(texts))
