"""Rules the agent applies to what buyers write and to what it writes back.

Three kinds, all deterministic so they cost no model call and behave the same every time:
 - text that tries to give the agent instructions (prompt injection) is recognised, and no booking or other side effect follows from it;
 - a buyer who asks for a person, or is plainly fed up, is handed to the broker;
 - a reply that states a figure that is not in the facts is replaced by the plain layout built from the facts.
"""
from __future__ import annotations

import re
from typing import Any

from app.groundedness import ungrounded

INJECTION = re.compile(
    r"(ignore|disregard|forget)\s+(all\s+|any\s+|the\s+|your\s+)*(previous|prior|above|earlier|system)\b|"
    r"\b(system|developer)\s+(prompt|message|instruction)s?\b|\b(admin|developer|debug|dan|jailbreak)\s+mode\b|"
    r"\byou\s+are\s+now\b|\bact\s+as\s+(if\s+you\s+are\s+)?(an?\s+)?(admin|root|system|different)|\breveal\s+(your|the)\s+(prompt|instructions)|"
    r"\boverride\s+(your|the|all)\b|\bnew\s+instructions?\s*:",
    re.I,
)
HUMAN_REQUEST = re.compile(
    r"\b(talk|speak|chat|connect|transfer)\s+(me\s+)?(to|with)\s+(a\s+|the\s+|your\s+)?(real\s+|actual\s+)?(human|person|agent|broker|someone|manager|owner)\b|"
    r"\b(real|actual)\s+(human|person)\b|\bcall\s+me\s+back\b|ሰው\s*ማነጋገር|ደላላውን\s*ማነጋገር",
    re.I,
)
FRUSTRATION = re.compile(r"\b(useless|stupid|idiot|rubbish|waste of time|this is ridiculous|you('re| are) not (helping|listening)|not helpful|terrible)\b", re.I)


def looks_like_injection(text: str) -> bool:
    return bool(INJECTION.search(text or ""))


def wants_a_human(text: str) -> str | None:
    """'asked' when the buyer asks for a person, 'frustrated' when they are plainly fed up, otherwise None."""
    if HUMAN_REQUEST.search(text or ""):
        return "asked"
    if FRUSTRATION.search(text or ""):
        return "frustrated"
    return None


def reply_is_grounded(reply: str, *facts: Any) -> bool:
    """True when every price, size, percentage or year in the reply appears in the facts (or in what the buyer wrote)."""
    return not ungrounded(reply, *facts)
