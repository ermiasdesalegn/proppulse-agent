from __future__ import annotations

from app.geo import KNOWN_SUB_CITIES

GLOSSARY = ", ".join(KNOWN_SUB_CITIES)

PERSONA = (
    "You are the digital assistant of an independent real estate broker in Addis Ababa who represents buyers across "
    "several developers. Be warm, concise and professional, like a discerning broker. Reply in the language the buyer "
    "used (English or Amharic). Use only the facts you are given. Never invent prices, dates, units or promises. "
    "Prices are in Ethiopian birr (ETB)."
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
- intent: "search" if they describe or refine what they want; "compare" if they ask to compare options; "book_tour" if they want to visit, view or schedule a time; "small_talk" for greetings, thanks, or anything else.
- Only fill fields the latest message states or changes. Use null for anything not mentioned.
- Budgets: convert to birr ("14M", "14 million" = 14000000).
- Bedrooms: "3BR", "three bedroom" = 3. A studio is 0.
- preferred_sub_cities: return the COMPLETE updated list, not just additions. Known areas: {GLOSSARY}. Keep other area names as written.
- search_query: a short English description of the home and lifestyle they want, even if they wrote in Amharic.
- referenced_options: numbers from the most recent list shown to the buyer ("the first two" = [1, 2]). Empty if none.
- tour_start_iso: the visit time they ask for, resolved against the current time given, with UTC offset. Null if no time was given.
- urgency: high if they want to act within days, medium within weeks, otherwise low."""


def extraction_prompt(*, now_iso: str, timezone: str, prefs: dict, profile: dict, history: str, latest: str, options: str) -> str:
    return (
        f"Current time: {now_iso} ({timezone})\n"
        f"Known preferences: {prefs}\nKnown contact details: {profile}\n"
        f"Options last shown to the buyer:\n{options or '(none)'}\n\n"
        f"Recent conversation:\n{history}\n\nLatest buyer message:\n{latest}"
    )
