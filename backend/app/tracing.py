"""Shows the inspector what the agent's tools were asked and what they answered.

`Traced` wraps a service (calendar, routing, email, Telegram, the search methods of the repository) and publishes a `tool` event for each
call it makes: which tool, its arguments, a summary of the result, how long it took, and whether it failed. Arguments are summarised, not
copied: message bodies become a length, email addresses are masked, vectors become their size, long lists become a count. The event
carries the node and buyer the call belongs to, taken from the same context the model-call log uses.
"""
from __future__ import annotations

import dataclasses
import re
import time
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID
from typing import Any, Iterable

from app.gemini import CALL_CONTEXT

EMAIL = re.compile(r"^([^@\s])[^@\s]*(@[^@\s]+)$")
BODY_KEYS = {"html", "text", "prompt", "caption", "description", "content"}

# Which calls of each service are shown. Everything else passes straight through untouched.
CALENDAR = ("is_available", "find_slots", "book")
ROUTING = ("plan_route",)
EMAIL_CALLS = ("send",)
TELEGRAM = ("send_message", "send_document", "send_location", "send_photo")
SEARCH = ("search_properties", "rank_properties")


def plain(value: Any, depth: int = 0) -> Any:
    """A small, JSON-safe, private-by-default version of any value."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray)):
        return f"{len(value)} bytes"
    if isinstance(value, str):
        if m := EMAIL.match(value):
            return f"{m.group(1)}***{m.group(2)}"
        return value if len(value) <= 80 else f"{value[:77]}... ({len(value)} chars)"
    if depth >= 3:
        return f"<{type(value).__name__}>"
    if isinstance(value, dict):
        return {str(k): (f"{len(v)} chars" if k in BODY_KEYS and isinstance(v, str) else plain(v, depth + 1)) for k, v in list(value.items())[:12]}
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        if len(items) > 40 and all(isinstance(x, float) for x in items[:5]):
            return {"vector": len(items)}  # an embedding: its size is all that matters
        return [plain(x, depth + 1) for x in items[:6]] if len(items) <= 6 else {"count": len(items), "first": plain(items[0], depth + 1)}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return plain(dataclasses.asdict(value), depth + 1)
    if hasattr(value, "model_dump"):
        return plain(value.model_dump(), depth + 1)
    if hasattr(value, "__dict__"):
        return plain({k: v for k, v in vars(value).items() if not k.startswith("_")}, depth + 1)
    return repr(value)[:80]


def summarize_args(args: Iterable[Any], kwargs: dict) -> dict:
    out: dict[str, Any] = {f"arg{i + 1}": a for i, a in enumerate(args)}
    out.update(kwargs)
    return {k: (f"{len(v)} chars" if k in BODY_KEYS and isinstance(v, str) else plain(v)) for k, v in out.items()}


def summarize_result(result: Any) -> Any:
    if hasattr(result, "distance_meters"):  # a route plan
        return {"stops": len(getattr(result, "order", [])), "km": round(result.distance_meters / 1000, 1), "minutes": round(result.duration_seconds / 60)}
    if isinstance(result, (list, tuple)):
        titles = [getattr(r, "title", None) or (r.get("title") if isinstance(r, dict) else None) for r in result[:3]]
        if result and all(titles):  # search results: what was found matters, not the rows' ids
            return {"count": len(result), "titles": titles}
        first = result[0] if result else None
        return {"count": len(result), **({"top": plain(first)} if first is not None else {})}
    return plain(result)


class Traced:
    """Transparent wrapper: everything behaves as the wrapped object, and the named async methods also report themselves."""

    def __init__(self, target: Any, prefix: str, bus: Any, methods: Iterable[str]):
        self._target, self._prefix, self._bus, self._methods = target, prefix, bus, frozenset(methods)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._target, name)
        if name not in self._methods or not callable(attr):
            return attr

        async def call(*args: Any, **kwargs: Any) -> Any:
            started, ok, error, result = time.monotonic(), True, None, None
            try:
                result = await attr(*args, **kwargs)
                return result
            except Exception as exc:
                ok, error = False, type(exc).__name__
                raise
            finally:
                try:
                    ctx = CALL_CONTEXT.get()
                    self._bus.publish(
                        "tool", tool=f"{self._prefix}.{name}", args=summarize_args(args, kwargs), result=summarize_result(result) if ok else None,
                        ms=round((time.monotonic() - started) * 1000), ok=ok, error=error, node=ctx.get("node"), buyer_id=ctx.get("buyer_id"),
                    )
                except Exception:
                    pass  # reporting a call must never change its outcome

        return call


def trace_services(services: Any, bus: Any) -> Any:
    """The same Services, with every shown call reporting itself. Without a bus nothing is wrapped."""
    if bus is None:
        return services
    return dataclasses.replace(
        services,
        calendar=Traced(services.calendar, "calendar", bus, CALENDAR),
        routing=Traced(services.routing, "routing", bus, ROUTING),
        email=Traced(services.email, "email", bus, EMAIL_CALLS),
        telegram=Traced(services.telegram, "telegram", bus, TELEGRAM),
    )


def trace_repo(repo: Any, bus: Any) -> Any:
    return repo if bus is None else Traced(repo, "search", bus, SEARCH)
