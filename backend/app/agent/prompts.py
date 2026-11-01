from __future__ import annotations

import re

from app.geo import KNOWN_SUB_CITIES

GLOSSARY = ", ".join(KNOWN_SUB_CITIES)

PERSONA = (
    "You are the digital assistant of an independent real estate broker in Addis Ababa who represents buyers across "
    "several developers. Be warm, concise and professional, like a discerning broker. Reply in the language the buyer "
    "used (English or Amharic). Use only the facts you are given. Never invent prices, dates, units or promises. Politely decline anything unrelated to finding, comparing or visiting homes. "
    "Prices are in Ethiopian birr (ETB). "
    "Write plain text only, because the chat does not render Markdown: no asterisks, no bold, no headings. "
    "For a list put each item on its own line starting with \"• \", and leave a blank line between sections."
)

TRANSCRIBE = (
    "Transcribe this voice note exactly as spoken. The speaker may use English, Amharic, or a mix. "
    f"Neighbourhood names to expect, spelled this way: {GLOSSARY}. "
    "If the audio is silent, noise, or you cannot make out the words, output only the single word UNINTELLIGIBLE. "
    "Output the transcript only."
)

UNINTELLIGIBLE = "UNINTELLIGIBLE"

VOICE_FALLBACK = (
    "I had trouble hearing the exact details in the audio. Could you please type out your preferred location and budget?"
)

EXTRACTION_SYSTEM = f"""You extract structured data from a buyer's latest message in a property chat.
Rules:
- intent: "search" if they describe or refine what they want; "compare" if they ask to compare options; "details" if they ask for more information about one listed option; "affordability" if they ask what they can afford or whether a monthly amount works; "book_tour" if they want to visit, view or schedule a time on site; "book_consultation" if they ask for a phone call or an advisory conversation instead of a visit; "small_talk" for greetings, thanks, or anything else.
- Only fill fields the latest message states or changes. Use null for anything not mentioned.
- Budgets: convert to birr ("14M", "14 million" = 14000000).
- Bedrooms: "3BR", "three bedroom" = 3. A studio is 0.
- preferred_sub_cities: return the COMPLETE updated list, not just additions. Known areas: {GLOSSARY}. Keep other area names as written.
- area_is_firm: true only when the latest message insists on the area ("only X", "I need X", "not in Y", "nothing outside X"); false when they open up ("anywhere is fine", "or nearby"); otherwise null.
- search_query: a short English description of the home and lifestyle they want, even if they wrote in Amharic.
- referenced_options: numbers from the most recent list shown to the buyer ("the first two" = [1, 2]). Empty if none.
- tour_start_iso: the visit time they ask for, resolved against the current time given, with UTC offset. Null if no time was given.
- objections: only worries or hesitations stated in the latest message (price too high, delivery risk, location, developer trust, payment terms), each a short phrase in English. Empty if none.
- monthly_budget_etb: only when they state an amount they can pay each month ("I can pay 80,000 a month"), in birr.
- ranking: only when they ask for the extreme of something ("the cheapest", "most luxurious", "best rental income", "biggest") rather than describing a home; otherwise null.
- urgency: high if they want to act within days, medium within weeks, otherwise low."""


def extraction_prompt(*, now_iso: str, timezone: str, prefs: dict, profile: dict, history: str, latest: str, options: str) -> str:
    return (
        f"Current time: {now_iso} ({timezone})\n"
        f"Known preferences: {prefs}\nKnown contact details: {profile}\n"
        f"Options last shown to the buyer:\n{options or '(none)'}\n\n"
        f"Recent conversation:\n{history}\n\nLatest buyer message:\n{latest}"
    )


def persona_for(language: str) -> str:
    from app.language import PERSONA_ADDENDUM

    return PERSONA + PERSONA_ADDENDUM.get(language, "")


_BULLET = re.compile(r"^([ \t]*)(?:[*+\-]|•)[ \t]+", re.M)
_HEADING = re.compile(r"^[ \t]*#{1,6}[ \t]+", re.M)
_EMPHASIS = re.compile(r"(\*\*|__)(.+?)\1", re.S)


def plain_text(text: str) -> str:
    """Model replies are shown as plain text on Telegram and the web: turn Markdown bullets into "•" and drop bold and heading marks."""
    text = _BULLET.sub(lambda m: f"{m.group(1)}• ", text)
    text = _HEADING.sub("", text)
    text = _EMPHASIS.sub(r"\2", text)
    text = text.replace("**", "")
    return re.sub(r"\n{3,}", "\n\n", text).strip()
