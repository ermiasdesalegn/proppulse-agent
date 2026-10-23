"""Does a reply only state numbers it was given?

The agent must never invent a price, size, percentage or date. This finds the figures in a piece of text and checks each one against
the facts the agent had (the unit rows, the comparison, the buyer's own words). It is deliberately about numbers: they are what a buyer
acts on, and they can be checked without a second model.

What counts as a claim: a number with a unit (M, million, k, %, sqm, ETB, birr) or any number of 100 or more (prices per sqm, years,
phone numbers). Small bare numbers (option numbers, bedroom counts, days) are ordinary language and are ignored.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(?:\s*(million|m|k|%|sqm|etb|birr)(?![a-z]))?", re.I)
SCALE = {"m": 1e6, "million": 1e6, "k": 1e3}
MONEYISH = {"m", "million", "k", "etb", "birr"}


@dataclass(frozen=True)
class Claim:
    text: str
    value: float          # scaled: "13.2M" is 13_200_000
    tolerance: float      # half a unit of the last digit the writer gave, in the same scale
    unit: str | None


def extract_claims(text: str) -> list[Claim]:
    claims = []
    for m in NUMBER.finditer(text or ""):
        whole, frac, unit = m.group(1), m.group(2), (m.group(3) or "").lower() or None
        scale = SCALE.get(unit or "", 1.0)
        value = float(whole.replace(",", "") + ("." + frac if frac else ""))
        if unit is None and value < 100:
            continue  # "option 2", "3 bedrooms", "10:00": ordinary language, not a figure to verify
        decimals = len(frac) if frac else 0
        claims.append(Claim(m.group(0).strip(), value * scale, 0.5 * (10 ** -decimals) * scale, unit))
    return claims


def _flatten(source: Any) -> Iterable[str]:
    if source is None:
        return
    if isinstance(source, dict):
        for value in source.values():
            yield from _flatten(value)
    elif isinstance(source, (list, tuple, set)):
        for value in source:
            yield from _flatten(value)
    elif isinstance(source, bool):
        return
    elif isinstance(source, (int, float)):
        yield repr(float(source)) if isinstance(source, float) else str(source)
    else:
        yield str(source)


def allowed_values(*sources: Any) -> list[float]:
    """Every number that appears anywhere in the given facts, in both its raw and its scaled-by-unit reading."""
    values: set[float] = set()
    for blob in _flatten(list(sources)):
        for m in NUMBER.finditer(blob):
            whole, frac, unit = m.group(1), m.group(2), (m.group(3) or "").lower() or None
            value = float(whole.replace(",", "") + ("." + frac if frac else ""))
            values.add(value)
            values.add(value * SCALE.get(unit or "", 1.0))
    return sorted(values)


def _supported(claim: Claim, allowed: list[float]) -> bool:
    for v in allowed:
        if abs(claim.value - v) <= max(claim.tolerance, abs(v) * 0.005) + 1e-9:
            return True
        if claim.unit in MONEYISH and abs(claim.value - v) <= abs(v) * 0.005:
            return True
    return False


def ungrounded(reply: str, *sources: Any) -> list[Claim]:
    """The figures in `reply` that appear nowhere in `sources`."""
    allowed = allowed_values(*sources)
    return [c for c in extract_claims(reply) if not _supported(c, allowed)]
