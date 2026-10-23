"""Real Gemini (text, structured output, embeddings) driving the real graph. Database writes are rolled back.

These are the only tests that prove the prompts and the extraction schema work with the actual model.
"""
import pytest

pytestmark = pytest.mark.live
import pytest_asyncio

from app.config import get_settings
from app.gemini import GeminiGateway, GenAIBackend
from tests.agent_helpers import make_agent, new_buyer, say_to


@pytest_asyncio.fixture
async def gateway():
    s = get_settings()
    backend = GenAIBackend(
        s.gemini_api_key, s.gemini_model, s.gemini_embed_model,
        fallback_models=s.fallback_models, primary_timeout=s.gemini_primary_timeout_seconds,
        thinking_budget=s.gemini_thinking_budget,
    )
    gw = GeminiGateway(backend, rpm=s.gemini_rpm, request_timeout=s.gemini_timeout_seconds)
    yield gw
    await gw.aclose()


async def test_english_request_is_understood_searched_and_answered(repo, gateway):
    agent, buyer = make_agent(repo, gateway), await new_buyer(repo, "live-en")
    res = await say_to(agent, buyer, "Hi! I have 14M ETB and I'm looking for a 3 bedroom home in Bole or CMC with a garden for my family.")
    prefs = res.state["buyer_preferences"]
    assert prefs["max_budget_etb"] == 14_000_000 and prefs["min_bedrooms"] == 3
    assert {"Bole", "CMC"} <= set(prefs["preferred_sub_cities"])
    assert res.state["intent"] == "search" and res.state["active_matches"]
    assert len(res.reply) > 80 and any(m["title"].split()[0] in res.reply for m in res.state["active_matches"])


async def test_amharic_request_is_understood(repo, gateway):
    agent, buyer = make_agent(repo, gateway), await new_buyer(repo, "live-am")
    res = await say_to(agent, buyer, "ቦሌ ውስጥ ባለ 3 መኝታ ቤት አፓርታማ እፈልጋለሁ። በጀቴ 20 ሚሊዮን ብር ነው።")
    prefs = res.state["buyer_preferences"]
    assert prefs["max_budget_etb"] == 20_000_000 and prefs["min_bedrooms"] == 3 and "Bole" in prefs["preferred_sub_cities"]
    assert res.reply


async def test_buyer_can_pick_an_option_and_book_with_a_natural_time(repo, gateway):
    agent, buyer = make_agent(repo, gateway), await new_buyer(repo, "live-book")  # clock is fixed at Mon 2 Nov 2026, 08:00
    await say_to(agent, buyer, "I'm looking for a 3 bedroom apartment in Bole, budget 25 million birr")
    res = await say_to(agent, buyer, "Let's visit the first one tomorrow at 10am. I'm Abel Tesfaye, my number is 0911 000 000.")
    assert res.state["booked"], res.reply
    cur = await repo._fetch("select scheduled_start from tours_and_appointments where buyer_id=%s", (buyer["id"],))
    start = cur[0]["scheduled_start"].astimezone(get_settings().timezone and __import__("zoneinfo").ZoneInfo(get_settings().timezone))
    assert (start.month, start.day, start.hour) == (11, 3, 10)
    assert res.state["buyer_profile"]["full_name"] == "Abel Tesfaye"
