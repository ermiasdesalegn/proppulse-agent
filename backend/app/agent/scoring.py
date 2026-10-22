from __future__ import annotations

# Points per signal; the total is capped at 100.
_INTENT_POINTS = {"book_tour": 20, "compare": 8}
_URGENCY_POINTS = {"high": 10, "medium": 5, "low": 0}


def lead_score(profile: dict, prefs: dict, intent: str, urgency: str) -> int:
    score = 0
    score += 10 if profile.get("full_name") else 0
    score += 15 if profile.get("phone") else 0
    score += 5 if profile.get("email") else 0
    score += 20 if prefs.get("max_budget_etb") else 0
    score += 10 if prefs.get("min_bedrooms") is not None else 0
    score += 10 if prefs.get("preferred_sub_cities") else 0
    score += _INTENT_POINTS.get(intent, 0)
    score += _URGENCY_POINTS.get(urgency, 0)
    return min(score, 100)
