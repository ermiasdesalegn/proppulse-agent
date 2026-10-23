"""The AI lead score (0 to 100) and the reasons behind it.

A score is only useful to a broker if they can see why it is what it is. Every point comes from one named signal, a signal once earned is
kept (a buyer who asked to visit does not become less interested by saying thank you), and the earned signals are stored with the buyer
so the dashboard can show them.
"""
from __future__ import annotations

# key -> (label, points). "intent" and "urgency" depend on what was said, so their label and points come from the tables below.
FIXED = {
    "name": ("Gave their name", 10),
    "phone": ("Shared a phone number", 15),
    "email": ("Shared an email address", 5),
    "budget": ("Stated a budget", 20),
    "bedrooms": ("Said how many bedrooms", 10),
    "areas": ("Named the areas they like", 10),
}
INTENT = {"book_consultation": ("Asked for a phone consultation", 15), "book_tour": ("Asked to visit a property", 20), "compare": ("Compared options", 8)}
URGENCY = {"high": ("Wants to act within days", 10), "medium": ("Wants to act within weeks", 5)}
MAXIMUM = sum(p for _, p in FIXED.values()) + max(p for _, p in INTENT.values()) + max(p for _, p in URGENCY.values())  # exactly 100

# Kept for callers that only want the number.
_INTENT_POINTS = {k: v[1] for k, v in INTENT.items()}
_URGENCY_POINTS = {k: v[1] for k, v in URGENCY.items()}


def signals(profile: dict, prefs: dict, intent: str, urgency: str) -> dict[str, dict]:
    """The signals this message shows: {key: {"points": n, "label": text}}."""
    got: dict[str, dict] = {}

    def earn(key: str, label: str, points: int) -> None:
        got[key] = {"points": points, "label": label}

    checks = {
        "name": bool(profile.get("full_name")), "phone": bool(profile.get("phone")), "email": bool(profile.get("email")),
        "budget": bool(prefs.get("max_budget_etb")), "bedrooms": prefs.get("min_bedrooms") is not None, "areas": bool(prefs.get("preferred_sub_cities")),
    }
    for key, present in checks.items():
        if present:
            earn(key, *FIXED[key])
    if intent in INTENT:
        earn("intent", *INTENT[intent])
    if urgency in URGENCY:
        earn("urgency", *URGENCY[urgency])
    return got


def merge(before: dict[str, dict] | None, now: dict[str, dict]) -> dict[str, dict]:
    """Earned signals only ever grow: for each key the better of what was earned before and what this message shows."""
    merged = {k: dict(v) for k, v in (before or {}).items()}
    for key, value in now.items():
        if key not in merged or value["points"] > merged[key]["points"]:
            merged[key] = dict(value)
    return merged


def total(earned: dict[str, dict]) -> int:
    return min(sum(v["points"] for v in earned.values()), 100)


def components(earned: dict[str, dict]) -> list[dict]:
    """Every possible signal in a fixed order, earned or not, so the dashboard can show what is still missing."""
    out = []
    for key, (label, points) in FIXED.items():
        got = earned.get(key)
        out.append({"key": key, "label": label, "max": points, "points": got["points"] if got else 0, "earned": got is not None})
    got = earned.get("intent")
    out.append({"key": "intent", "label": got["label"] if got else "Asked to visit or compare", "max": 20, "points": got["points"] if got else 0, "earned": got is not None})
    got = earned.get("urgency")
    out.append({"key": "urgency", "label": got["label"] if got else "Wants to act soon", "max": 10, "points": got["points"] if got else 0, "earned": got is not None})
    return out


def gained(before: dict[str, dict] | None, after: dict[str, dict]) -> list[dict]:
    """What this message added, for the score history: [{label, points}]."""
    out = []
    for key, value in after.items():
        was = (before or {}).get(key, {}).get("points", 0)
        if value["points"] > was:
            out.append({"label": value["label"], "points": value["points"] - was})
    return out


def lead_score(profile: dict, prefs: dict, intent: str, urgency: str) -> int:
    return total(signals(profile, prefs, intent, urgency))
