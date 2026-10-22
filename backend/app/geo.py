"""Addis Ababa neighbourhoods used for search relaxation. Edit ADJACENT as the inventory grows."""
from __future__ import annotations

# One direction is enough; adjacent_to() makes it symmetric.
_EDGES = {
    "Bole": ["Kazanchis", "CMC"],
    "CMC": ["Ayat"],
    "Kazanchis": ["Sarbet"],
    "Sarbet": ["Lebu", "Bole"],
}

KNOWN_SUB_CITIES = ["Bole", "CMC", "Kazanchis", "Ayat", "Sarbet", "Lebu"]
_CANONICAL = {name.lower(): name for name in KNOWN_SUB_CITIES}


def normalize_sub_city(name: str) -> str:
    cleaned = name.strip()
    return _CANONICAL.get(cleaned.lower(), cleaned.title())


def normalize_sub_cities(names: list[str] | None) -> list[str]:
    seen: list[str] = []
    for n in names or []:
        if n.strip() and (c := normalize_sub_city(n)) not in seen:
            seen.append(c)
    return seen


def adjacent_to(city: str) -> list[str]:
    out = list(_EDGES.get(city, []))
    out += [a for a, bs in _EDGES.items() if city in bs and a not in out]
    return out


def expand_with_adjacent(cities: list[str]) -> list[str]:
    result = list(cities)
    for c in cities:
        for n in adjacent_to(c):
            if n not in result:
                result.append(n)
    return result
