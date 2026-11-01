from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, RemoveMessage

from app import finance
from app.agent import emails, scoring
from app.agent.deps import AgentDeps
from app.agent.prompts import (
    EXTRACTION_SYSTEM,
    TRANSCRIBE,
    UNINTELLIGIBLE,
    extraction_prompt,
    persona_for,
    plain_text,
)
from app.agent.schemas import Extraction, Recommendation
from app.agent.state import EMPTY_PREFS, BrokerAgentState
from app.brochure import brochure_data
from app.brochure import render_html as brochure_html
from app.brochure import render_pdf as brochure_pdf
from app.dossier import deliver_dossier
from app.finance import fmt_etb, payment_schedule
from app.geo import expand_with_adjacent, normalize_sub_cities
from app.guardrails import looks_like_injection, reply_is_grounded, wants_a_human
from app.ingest.portal import is_portal_unit
from app.language import detect_language, msg
from app.model3d import Layout, render_png, to_glb
from app.projects import suggest_projects
from app.repository import LedgerEntry, PropertyMatch
from app.services.calendar import CalendarError, SlotUnavailable
from app.services.email import Attachment
from app.services.routing import Stop

log = logging.getLogger(__name__)

UNSUPPORTED = "I can read text and voice messages. What kind of home are you looking for (budget, bedrooms, area)?"
TROUBLE = "I'm having trouble right now. Please send that again in a moment."
CALENDAR_DOWN = "I can't reach the broker's calendar right now, so I haven't booked anything. Please try again in a few minutes."
EARLY_STAGES = {"NEW", "QUALIFIED", "INVENTORY_PENDING"}


# ---------------- small helpers ----------------

def num(value: Any) -> float | None:
    return None if value is None else float(value)


def history_text(messages: list[AnyMessage], limit: int = 10) -> str:
    lines = [f"{'Buyer' if isinstance(m, HumanMessage) else 'Agent'}: {m.content}" for m in messages[-limit:]]
    return "\n".join(lines) or "(start of conversation)"


def options_text(active: list[dict]) -> str:
    return "\n".join(
        f"{i}. {m['title']} - {m['sub_city']} - {fmt_etb(m['price_etb'])}" for i, m in enumerate(active, start=1)
    )


def slim(m: PropertyMatch) -> dict:
    return {
        "id": str(m.id), "developer_id": str(m.developer_id), "title": m.title, "sub_city": m.sub_city,
        "price_etb": m.price_etb, "bedrooms": m.bedrooms, "similarity": round(m.similarity, 3),
    }


def can_relax(prefs: dict) -> bool:
    return prefs.get("max_budget_etb") is not None or bool(prefs.get("preferred_sub_cities"))


def relaxed_scope(prefs: dict, next_step: int, step_pct: float, max_steps: int) -> dict:
    """Deterministic widening. Step 1: more budget plus neighbouring areas. Step 2: even more budget, any area."""
    budget = prefs.get("max_budget_etb")
    cities = list(prefs.get("preferred_sub_cities") or [])
    return {
        "effective_max_budget_etb": None if budget is None else budget * (1 + next_step * step_pct / 100),
        "effective_sub_cities": expand_with_adjacent(cities) if next_step == 1 else [],
        "relaxation_step": next_step,
        "max_relaxation_steps": max_steps,
    }


def should_relax_constraints(state: BrokerAgentState) -> str:
    if state.get("intent") == "error":
        return "end"
    if state.get("active_matches"):
        return "recommend"
    scope = state["search_scope"]
    if scope["relaxation_step"] >= scope["max_relaxation_steps"] or not can_relax(state["buyer_preferences"]):
        return "waitlist"
    return "relax"


def property_facts(p: dict) -> dict:
    price, sqm = float(p["price_etb"]), float(p["sqm"])
    down_pct = num(p.get("down_payment_pct"))
    return {
        "title": p["title"], "developer": p["developer_name"], "area": p["sub_city"], "bedrooms": p["bedrooms"],
        "sqm": sqm, "price_etb": price, "price_per_sqm_etb": round(price / sqm),
        "down_payment_pct": down_pct, "down_payment_etb": None if down_pct is None else round(price * down_pct / 100),
        "construction_stage_pct": num(p.get("construction_stage_pct")),
        "delivery_date": None if p.get("delivery_date") is None else p["delivery_date"].isoformat(),
        "est_rental_yield_pct": num(p.get("est_rental_yield_pct")),
        "features": (p.get("vision_amenities") or {}).get("tags", []),
        "reliability_score": num(p.get("reliability_score")),
    }


def option_block(number: int, p: dict, blurb: str, lang: str = "en") -> str:
    """One numbered option, built from database values so the numbers shown always match the stored order."""
    price, sqm = float(p["price_etb"]), float(p["sqm"])
    bits = [f"{fmt_etb(price)} ({price / sqm:,.0f} ETB/sqm)"]
    if p.get("down_payment_pct") is not None:
        pct = float(p["down_payment_pct"])
        bits.append(msg("down", lang, pct=f"{pct:g}", amount=fmt_etb(price * pct / 100)))
    if p.get("delivery_date") is not None:
        bits.append(msg("delivery", lang, when=p["delivery_date"].strftime("%b %Y")))
    lines = [f"{number}. {p['title']} - {p['developer_name']}, {p['sub_city']}", "   " + " | ".join(bits)]
    if blurb:
        lines.append("   " + blurb)
    return "\n".join(lines)


DETAILS_RE = re.compile(r"^tell me more about option (\d+)\.?$", re.I)
BOOK_OPTION_RE = re.compile(r"^(?:book a site visit for|i'd like to book a site visit for) option (\d+)\.?$", re.I)
COMPARE_RE = re.compile(r"^compare these options( for me)?\.?$", re.I)
AFFORD_RE = re.compile(r"^what can i afford\??$", re.I)
CONSULT_RE = re.compile(r"^i'd like a phone consultation\.?$", re.I)
SLOT_RE = re.compile(r"^book (call )?(?:[A-Za-z]{3} )(\d{1,2}) ([A-Za-z]{3}),? (\d{1,2}):(\d{2})$", re.I)


def quick_extraction(text: str, option_count: int, now: datetime) -> Extraction | None:
    """What a button tap means, worked out in code. The buttons send fixed sentences, so no model call is needed to read them."""
    text = " ".join(text.split())
    if (m := DETAILS_RE.match(text)) and 1 <= int(m.group(1)) <= option_count:
        return Extraction(intent="details", referenced_options=[int(m.group(1))])
    if (m := BOOK_OPTION_RE.match(text)) and 1 <= int(m.group(1)) <= option_count:
        return Extraction(intent="book_tour", referenced_options=[int(m.group(1))])
    if COMPARE_RE.match(text) and option_count > 1:
        return Extraction(intent="compare")
    if CONSULT_RE.match(text):
        return Extraction(intent="book_consultation")
    if AFFORD_RE.match(text):
        return Extraction(intent="affordability")
    if m := SLOT_RE.match(text):
        try:
            when = datetime.strptime(f"{m.group(2)} {m.group(3)} {now.year} {m.group(4)}:{m.group(5)}", "%d %b %Y %H:%M").replace(tzinfo=now.tzinfo)
        except ValueError:
            return None
        if when < now - timedelta(days=1):
            when = when.replace(year=now.year + 1)  # a December offer tapped in January is for next year, never the past
        return Extraction(intent="book_consultation" if m.group(1) else "book_tour", tour_start_iso=when.isoformat())
    return None


def option_buttons(count: int, lang: str = "en") -> list[list[dict]]:
    """Labels follow the buyer's language; the texts are the fixed English sentences the agent recognises in code."""
    rows = [[{"label": msg("btn_details", lang, n=n), "text": f"Tell me more about option {n}"}, {"label": msg("btn_book", lang, n=n), "text": f"Book a site visit for option {n}"}]
            for n in range(1, count + 1)]
    if count > 1:
        rows.append([{"label": msg("btn_compare", lang), "text": "Compare these options"}])
    rows.append([{"label": msg("btn_afford", lang), "text": "What can I afford?"}, {"label": msg("btn_consult", lang), "text": "I'd like a phone consultation"}])
    return rows


PRICE_WORRY = re.compile(r"price|expens|cost|afford|budget|high|ዋጋ|ውድ", re.I)
DELIVERY_WORRY = re.compile(r"deliver|delay|late|complet|finish|handover|ርክክብ|መዘግየት", re.I)
TRUST_WORRY = re.compile(r"trust|reliab|scam|risk|reput|developer|አምነት|አልሚ", re.I)
PAYMENT_WORRY = re.compile(r"payment|installment|down|upfront|ክፍያ", re.I)


def objection_notes(worries: list[str], rows: list[dict], today: date) -> list[str]:
    """A fact-based answer to a worry the buyer voiced, built from the stored data. Nothing here is written by a model."""
    if not worries or not rows:
        return []
    text = " ".join(worries)
    notes = []
    if PRICE_WORRY.search(text):
        per = sorted((float(r["price_etb"]) / float(r["sqm"]), r) for r in rows)
        low = per[0][1]
        notes.append(f"On price: the best value per square metre among these is {low['title']} at {per[0][0]:,.0f} ETB/sqm. Ask me to compare them, or tell me a lower budget and I'll search again.")
    if DELIVERY_WORRY.search(text):
        for r in rows:
            parts = [f"{r['title']} ({r['developer_name']})"]
            if r.get("construction_stage_pct") is not None:
                parts.append(f"{float(r['construction_stage_pct']):g}% built")
            if r.get("delivery_date") is not None:
                parts.append(f"delivery {r['delivery_date'].strftime('%b %Y')}")
            if r.get("reliability_score") is not None:
                parts.append(f"developer reliability {round(float(r['reliability_score']) * 100)}%")
            notes.append("On delivery: " + ", ".join(parts) + ".")
    if TRUST_WORRY.search(text) and not DELIVERY_WORRY.search(text):
        for r in rows:
            if r.get("reliability_score") is not None:
                notes.append(f"On the developer: {r['developer_name']} has a reliability score of {round(float(r['reliability_score']) * 100)}% in our records.")
    if PAYMENT_WORRY.search(text):
        for r in rows[:2]:
            notes.append(f"On payments: {r['title']}: " + finance.describe(finance.summarize(payment_schedule(r, today)), fmt_etb) + ".")
    return notes[:6]


def on_telegram(chat_id: int | str | None) -> bool:
    """A Telegram chat id is a number; the website has none."""
    return chat_id is not None and str(chat_id).lstrip("-").isdigit()


def model_file_name(title: Any) -> str:
    return ("".join(c if c.isalnum() else "-" for c in str(title).lower()).strip("-")[:60] or "unit") + ".glb"


def say(text: str, **extra: Any) -> dict:
    return {"reply": text, "messages": [AIMessage(content=text)], **extra}


class AgentNodes:
    def __init__(self, deps: AgentDeps):
        self.d = deps

    async def _write(self, prompt: str, fallback: str, lang: str = "en", facts: Any = None) -> str:
        """Model-written text. If it states a figure that is not in `facts`, the plain fallback built from the facts is used instead."""
        try:
            text = plain_text(await self.d.gateway.generate_text(prompt, system=persona_for(lang)))
        except Exception:
            log.exception("text generation failed; using deterministic fallback")
            return fallback
        if not text:
            return fallback
        if facts is not None and not reply_is_grounded(text, facts):
            log.warning("a generated reply stated a figure that is not in the facts; using the plain layout")
            return fallback
        return text

    @staticmethod
    def _lang(state: BrokerAgentState) -> str:
        return state.get("language") or "en"

    async def _compact(self, state: BrokerAgentState) -> dict:
        """A long conversation keeps its latest messages and a short summary of the rest, so it never grows without limit."""
        s, messages = self.d.settings, state.get("messages") or []
        if len(messages) <= s.history_compact_after:
            return {}
        old = [m for m in messages[: -s.history_keep_recent] if getattr(m, "id", None)]
        if not old:
            return {}
        previous = state.get("conversation_summary") or ""
        transcript = history_text(old, limit=len(old))
        fallback = (previous + " " + " ".join(str(m.content)[:100] for m in old if isinstance(m, HumanMessage))).strip()[-900:]
        summary = await self._write(
            f"Earlier summary: {previous or '(none)'}\n\nMessages to fold in:\n{transcript}\n\nWrite a summary of at most 100 words: what the buyer wants, "
            "what was shown, what they liked or worried about, and anything booked. Facts only.", fallback)
        return {"conversation_summary": summary, "messages": [RemoveMessage(id=m.id) for m in old]}

    def _local(self, dt: datetime) -> str:
        return dt.astimezone(ZoneInfo(self.d.settings.timezone)).strftime("%a %d %b, %H:%M")

    # ---------------- Node 1: intake ----------------

    async def intake(self, state: BrokerAgentState) -> dict:
        reset = {
            "reply": None, "transcript_failed": False, "comparison_matrix": None, "booked": False,
            "lead_reg_failed": [], "selected_property_ids": [], "tour_start_iso": None,
            "query_embedding": None, "human_takeover": False, "intent": "", "quick_replies": [], "attachments": [],
            "injection": False, "handoff_reason": None, "objections_now": [],
        }
        inbound = state.get("inbound") or {}
        lang = self._lang(state)
        if inbound.get("kind") == "voice":
            text = ""
            try:
                audio = await self.d.services.telegram.download_file(inbound["file_id"])
                text = (
                    await self.d.gateway.transcribe_audio(
                        audio, mime_type=inbound.get("mime_type", "audio/ogg"), prompt=TRANSCRIBE
                    )
                ).strip()
            except Exception:
                log.exception("voice note could not be processed")
            if not text or UNINTELLIGIBLE in text.upper():
                fallback = msg("voice_fallback", lang)
                return {**reset, **say(fallback, transcript_failed=True),
                        "messages": [HumanMessage(content="[voice note could not be understood]"), AIMessage(content=fallback)]}
            source = {"source": "voice"}
        else:
            text = (inbound.get("text") or "").strip()
            if not text:
                return {**reset, **say(msg("unsupported", lang), transcript_failed=True)}
            source = {}

        # A tap on a button sends one of the fixed English sentences; it says nothing about the language the buyer speaks.
        language = lang if quick_extraction(text, 99, self.d.now()) is not None else detect_language(text)
        flags = {"language": language, "injection": looks_like_injection(text), "handoff_reason": wants_a_human(text)}
        compacted = await self._compact(state)
        removals = compacted.pop("messages", [])
        return {**reset, **flags, **compacted, "messages": [*removals, HumanMessage(content=text, additional_kwargs=source)]}

    # ---------------- Node 2: supervisor router and CRM extraction ----------------

    async def router(self, state: BrokerAgentState) -> dict:
        d = self.d
        messages = state["messages"]
        buyer = await d.repo.get_buyer(state["buyer_id"])
        if buyer is None:
            raise LookupError(f"buyer {state['buyer_id']} not found")

        # A restarted process may have lost the checkpoint; the CRM row is the durable copy.
        prefs = {**EMPTY_PREFS, **(state.get("buyer_preferences") or {})}
        profile = dict(state.get("buyer_profile") or {})
        if not state.get("buyer_preferences"):
            prefs = {
                "max_budget_etb": num(buyer["max_budget_etb"]), "min_bedrooms": buyer["min_bedrooms"],
                "preferred_sub_cities": list(buyer["preferred_sub_cities"] or []),
            }
            profile = {k: buyer[k] for k in ("full_name", "phone", "email") if buyer[k]}
        active = state.get("active_matches") or []

        earlier = state.get("conversation_summary")
        prompt = extraction_prompt(
            now_iso=d.now().isoformat(), timezone=d.settings.timezone, prefs=prefs, profile=profile,
            history=(f"Summary of the earlier conversation: {earlier}\n\n" if earlier else "") + history_text(messages[:-1]),
            latest=str(messages[-1].content), options=options_text(active),
        )
        ex = quick_extraction(str(messages[-1].content), len(active), d.now())
        if ex is None and state.get("injection"):
            ex = Extraction(intent="small_talk")  # text that gives the agent orders is never read as a request: no search, booking or profile change
        if ex is None:
            try:
                ex = await d.gateway.generate_structured(prompt, Extraction, system=EXTRACTION_SYSTEM)
            except Exception:
                log.exception("extraction failed")
                return say(msg("trouble", self._lang(state)), intent="error")

        if ex.objections:
            try:
                await d.repo.add_objections(state["buyer_id"], ex.objections)
            except Exception:
                log.exception("could not store the buyer's objections")  # the dossier is a nicety; the chat goes on

        if (ex.intent == "search" and ex.objections and active and ex.max_budget_etb is None and ex.min_bedrooms is None
                and ex.preferred_sub_cities is None and ex.ranking is None):
            ex = ex.model_copy(update={"intent": "small_talk"})  # a worry voiced about the options shown is answered, not searched again

        new_prefs = dict(prefs)
        if ex.max_budget_etb is not None:
            new_prefs["max_budget_etb"] = float(ex.max_budget_etb)
        if ex.min_bedrooms is not None:
            new_prefs["min_bedrooms"] = ex.min_bedrooms
        if ex.preferred_sub_cities is not None:
            new_prefs["preferred_sub_cities"] = normalize_sub_cities(ex.preferred_sub_cities)
        new_profile = dict(profile)
        for key in ("full_name", "phone", "email"):
            if value := (getattr(ex, key) or "").strip():
                new_profile[key] = value

        query = ex.search_query or state.get("query_text") or self._query_from_prefs(new_prefs)
        earned = scoring.merge(buyer.get("score_breakdown"), scoring.signals(new_profile, new_prefs, ex.intent, ex.urgency))
        score = max(buyer["lead_score"], scoring.total(earned))  # a score recorded before breakdowns existed is never lowered
        stage = buyer["deal_stage"]
        if stage == "NEW" and new_prefs["max_budget_etb"] and (
            new_prefs["min_bedrooms"] is not None or new_prefs["preferred_sub_cities"] or ex.search_query
        ):
            stage = "QUALIFIED"

        updates: dict[str, Any] = {k: new_profile[k] for k in ("full_name", "phone", "email") if new_profile.get(k) and new_profile[k] != buyer[k]}
        for k in ("max_budget_etb", "min_bedrooms", "preferred_sub_cities"):
            current = list(buyer[k] or []) if k == "preferred_sub_cities" else num(buyer[k]) if k == "max_budget_etb" else buyer[k]
            if new_prefs[k] != current:
                updates[k] = new_prefs[k]
        if score != buyer["lead_score"]:
            updates["lead_score"] = score
        if earned != (buyer.get("score_breakdown") or {}):
            updates["score_breakdown"] = earned
        if stage != buyer["deal_stage"]:
            updates["deal_stage"] = stage
        if updates:
            await d.repo.update_buyer(state["buyer_id"], **updates)
        if score > buyer["lead_score"]:
            try:
                await d.repo.record_score(state["buyer_id"], score, scoring.gained(buyer.get("score_breakdown"), earned))
            except Exception:
                log.exception("could not record the score history")  # the history is a nicety; the chat goes on

        summary = state.get("preference_summary", "")
        has_prefs = bool(new_prefs["max_budget_etb"] or new_prefs["min_bedrooms"] is not None or new_prefs["preferred_sub_cities"])
        if has_prefs and (new_prefs != prefs or not summary):
            summary = await self._refresh_preference_vector(state["buyer_id"], new_prefs, query, summary, state["messages"])

        selected: list[str] = []
        if ex.referenced_options:
            selected = [active[i - 1]["id"] for i in ex.referenced_options if 1 <= i <= len(active)]
        elif ex.intent == "compare":
            selected = [m["id"] for m in active]
        elif ex.intent == "book_tour" and (pending := state.get("pending_booking")):
            selected = list(pending.get("ids") or [])  # the buyer is answering "which time?", not "which unit?"

        return {
            "intent": ex.intent, "urgency": ex.urgency, "objections_now": list(ex.objections),
            "monthly_budget_etb": ex.monthly_budget_etb if ex.monthly_budget_etb else state.get("monthly_budget_etb"), "query_text": query, "query_embedding": None, "ranking": ex.ranking,
            "buyer_preferences": new_prefs, "buyer_profile": new_profile, "preference_summary": summary,
            "deal_stage": stage, "selected_property_ids": selected, "tour_start_iso": ex.tour_start_iso,
            "search_scope": {
                "effective_max_budget_etb": new_prefs["max_budget_etb"],
                "effective_sub_cities": list(new_prefs["preferred_sub_cities"]),
                "relaxation_step": 0,
                "max_relaxation_steps": d.settings.max_relaxation_steps,
            },
        }

    @staticmethod
    def _query_from_prefs(prefs: dict) -> str:
        beds = prefs.get("min_bedrooms")
        where = " or ".join(prefs.get("preferred_sub_cities") or [])
        return " ".join(filter(None, [f"{beds} bedroom" if beds is not None else "", "home", f"in {where}" if where else ""]))

    @staticmethod
    def _preference_text(summary: str, query: str, history: Sequence[AnyMessage]) -> str:
        """What the buyer's preference vector is made from: their limits plus the last few things they actually said, so it follows the whole conversation and not only the latest message."""
        recent: list[str] = []
        for text in [*[str(m.content) for m in history if isinstance(m, HumanMessage)][-8:], query]:
            text = " ".join(text.split())[:200]
            if text and text not in recent:
                recent.append(text)
        return f"{summary}. They said: {' / '.join(recent[-6:])}"[:1200] if recent else summary

    async def _refresh_preference_vector(self, buyer_id: str, prefs: dict, query: str, previous: str, history: Sequence[AnyMessage] = ()) -> str:
        parts = [
            fmt_etb(prefs["max_budget_etb"]) if prefs["max_budget_etb"] else "",
            f"{prefs['min_bedrooms']}BR" if prefs["min_bedrooms"] is not None else "",
            " or ".join(prefs["preferred_sub_cities"]),
            query,
        ]
        summary = ", ".join(p for p in parts if p)
        try:
            vector = await self.d.gateway.embed_query(self._preference_text(summary, query, history))
            await self.d.repo.set_preference_embedding(buyer_id, summary, vector)
            return summary
        except Exception:
            log.exception("could not refresh preference embedding; will retry next turn")
            return previous

    # ---------------- Node 3A: hybrid discovery, relaxer, recommendation, waitlist ----------------

    async def discovery(self, state: BrokerAgentState) -> dict:
        d, scope, prefs = self.d, state["search_scope"], state["buyer_preferences"]
        try:
            if state.get("ranking"):
                # "The cheapest" is a rule, not a description: sort the units that fit the buyer's limits by it. No embedding involved.
                matches = await d.repo.rank_properties(
                    state["ranking"], max_budget_etb=scope["effective_max_budget_etb"], min_bedrooms=prefs["min_bedrooms"],
                    sub_cities=scope["effective_sub_cities"], count=3)
                return {"query_embedding": None, "active_matches": [slim(m) for m in matches]}
            embedding = state.get("query_embedding") or await d.gateway.embed_query(state["query_text"])
            matches = await d.repo.search_properties(
                embedding,
                max_budget_etb=scope["effective_max_budget_etb"],
                min_bedrooms=prefs["min_bedrooms"],
                sub_cities=scope["effective_sub_cities"],
                threshold=d.settings.similarity_threshold,
                count=3,
            )
        except Exception:
            log.exception("search failed")
            return say(msg("trouble", self._lang(state)), intent="error")
        return {"query_embedding": embedding, "active_matches": [slim(m) for m in matches]}

    async def constraint_relaxer(self, state: BrokerAgentState) -> dict:
        s = self.d.settings
        step = state["search_scope"]["relaxation_step"] + 1
        return {"search_scope": relaxed_scope(state["buyer_preferences"], step, s.relax_budget_step_pct, s.max_relaxation_steps)}

    async def recommend(self, state: BrokerAgentState) -> dict:
        d, scope, prefs = self.d, state["search_scope"], state["buyer_preferences"]
        rows = await d.repo.get_properties([m["id"] for m in state["active_matches"]])
        shown = {str(r["id"]) for r in rows}
        matches = [m for m in state["active_matches"] if m["id"] in shown]  # numbering must match what the buyer is shown

        widened = scope["relaxation_step"] > 0
        widening = ""
        if widened:
            widening = (
                f"Nothing matched exactly, so I widened the search (budget up to {fmt_etb(scope['effective_max_budget_etb'])}"
                if scope["effective_max_budget_etb"] else "Nothing matched exactly, so I widened the search ("
            )
            areas = scope["effective_sub_cities"]
            widening += (", " if scope["effective_max_budget_etb"] else "") + (f"areas: {', '.join(areas)}" if areas else "any area") + ")."
        facts = [property_facts(r) for r in rows]
        prompt = (
            f"Buyer wants: {state['query_text']}\nBuyer budget: {prefs['max_budget_etb']}, bedrooms: {prefs['min_bedrooms']}\n"
            + (f"IMPORTANT: the search was widened beyond the buyer's exact criteria. Say so openly in the intro. Details: {widening}\n" if widened else "")
            + f"Number of options: {len(facts)}. Options, in the order they will be numbered:\n{json.dumps(facts, default=str, indent=1)}\n\n"
            "Write: an intro; exactly one short blurb per option, in the same order, about its standout feature (do not repeat "
            "prices or numbers); and a closing question offering to compare options or book a visit."
        )
        lang = self._lang(state)
        try:
            rec = await d.gateway.generate_structured(prompt, Recommendation, system=persona_for(lang))
            if len(rec.blurbs) != len(rows) or not rec.intro.strip():
                raise ValueError("recommendation does not line up with the options")
            intro, blurbs, closing = rec.intro.strip(), [b.strip() for b in rec.blurbs], rec.closing.strip()
            written = " ".join([intro, *blurbs, closing])
            if not reply_is_grounded(written, facts, prefs, state["query_text"], scope, [str(getattr(m, "content", "")) for m in state["messages"]]):
                raise ValueError("the wording states a figure that is not in the facts")
        except Exception:
            log.exception("recommendation wording failed; using the plain layout")
            note = widening if lang == "en" else msg("widened", lang)  # the English note also says how far the budget and areas were widened
            intro = f"{note} {msg('found_intro', lang)}".strip() if widened else msg("found_intro", lang)
            blurbs = [", ".join(f["features"][:2]) for f in facts]
            closing = msg("ask_next", lang)

        text = "\n\n".join([intro, *[option_block(i, r, b, lang) for i, (r, b) in enumerate(zip(rows, blurbs), start=1)], closing])
        buttons = option_buttons(len(rows), lang)
        also, project_buttons = await suggest_projects(self.d.repo, state["buyer_preferences"], lang, limit=1, embedding=state.get("query_embedding"),
                                                       threshold=self.d.settings.similarity_threshold, header="proj_also_header")
        if also:  # a project in the buyer's area, shown next to the priced options: the offer and the location are the hook
            text, buttons = f"{text}\n\n{also}", [*buttons, *project_buttons]
        return {**say(text, quick_replies=buttons), "active_matches": matches}

    async def waitlist(self, state: BrokerAgentState) -> dict:
        scope, prefs = state["search_scope"], state["buyer_preferences"]
        stage = state.get("deal_stage", "NEW")
        out: dict[str, Any] = {}
        if stage in EARLY_STAGES:
            await self.d.repo.update_buyer(state["buyer_id"], deal_stage="INVENTORY_PENDING")
            out["deal_stage"] = "INVENTORY_PENDING"
        lang = self._lang(state)
        if scope["relaxation_step"] > 0 and prefs.get("max_budget_etb"):
            text = msg("waitlist_buffer", lang, pct=round(scope["relaxation_step"] * self.d.settings.relax_budget_step_pct))
        else:
            text = msg("waitlist", lang)
        block, buttons = await suggest_projects(self.d.repo, prefs, lang, embedding=state.get("query_embedding"), threshold=self.d.settings.similarity_threshold)
        if block:  # developers that publish no prices: the buyer can still ask for one
            return {**say(f"{text}\n\n{block}", quick_replies=buttons), **out, "active_matches": []}
        return {**say(text), **out, "active_matches": []}

    async def smalltalk(self, state: BrokerAgentState) -> dict:
        lang = self._lang(state)
        if state.get("injection"):
            return say(msg("off_limits", lang))  # no model is asked to weigh instructions that came from the buyer's text
        if state.get("objections_now") and state.get("active_matches"):
            rows = await self.d.repo.get_properties([m["id"] for m in state["active_matches"]])
            notes = objection_notes(state["objections_now"], rows, self.d.now().date())
            if notes:
                return say("\n".join(notes))
        prompt = (
            f"Conversation so far:\n{history_text(state['messages'][:-1])}\n\nThe buyer just wrote: {state['messages'][-1].content}\n"
            "Reply briefly. If this is a greeting or the buyer has not said what they want, welcome them and ask about "
            "budget, number of bedrooms and preferred area."
        )
        fallback = "Welcome! Tell me your budget, how many bedrooms you need and which area you prefer, and I'll find options across developers."
        return say(await self._write(prompt, msg("off_limits", lang) if lang != "en" else fallback, lang))

    # ---------------- details of one option (no model call: every line is a stored fact) ----------------

    async def details(self, state: BrokerAgentState) -> dict:
        active = state.get("active_matches") or []
        ids = list(state.get("selected_property_ids") or [])
        if not ids and len(active) == 1:
            ids = [active[0]["id"]]
        if not ids:
            return say(msg("which_option_details", self._lang(state)))
        rows = await self.d.repo.get_properties(ids[:1])
        if not rows:
            return say(msg("option_gone", self._lang(state)))
        p = rows[0]
        number = next((i for i, m in enumerate(active, start=1) if m["id"] == str(p["id"])), 1)
        f = property_facts(p)
        summary = finance.summarize(payment_schedule(p, self.d.now().date()))
        lang = self._lang(state)
        lines = [option_block(number, p, "", lang), "", msg("d_size", lang, beds=p["bedrooms"], sqm=f"{f['sqm']:g}", unit=p["unit_type"])]
        if f["construction_stage_pct"] is not None:
            lines.append(msg("d_construction", lang, pct=f"{f['construction_stage_pct']:g}"))
        if f["est_rental_yield_pct"] is not None:
            lines.append(msg("d_yield", lang, pct=f"{f['est_rental_yield_pct']:g}"))
        if f["features"]:
            lines.append(msg("d_features", lang, items=", ".join(f["features"][:6])))
        lines += ["", msg("d_plan", lang, plan=finance.describe(summary, fmt_etb), monthly=fmt_etb(summary["monthly_equivalent_etb"]))]
        if p.get("description"):
            lines += ["", p["description"]]
        lines += objection_notes(state.get("objections_now") or [], rows, self.d.now().date())
        attachments: list[dict] = []
        if await self._send_3d_model(state, p, attachments):
            lines += ["", msg("d_3d", lang)]
        rows_ = [[{"label": msg("btn_book", lang, n=number), "text": f"Book a site visit for option {number}"}]]
        if len(active) > 1:
            rows_.append([{"label": msg("btn_compare", lang), "text": "Compare these options"}])
        return {**say("\n".join(lines), quick_replies=rows_, attachments=attachments), "active_matches": active}

    async def _send_3d_model(self, state: BrokerAgentState, p: dict, attachments: list[dict]) -> bool:
        """If the broker saved a layout for this unit, send the buyer its 3D picture and the model file. Best effort: a failure here never spoils the reply.
        The website has no Telegram chat: there the reply carries an attachment instead, and the files are made when the buyer opens them."""
        chat_id = state.get("chat_id")
        try:
            stored = await self.d.repo.get_layout(p["id"])
            if not stored:
                return False
            layout = Layout.model_validate(stored)
            if not on_telegram(chat_id):
                attachments.append({"kind": "model3d", "property_id": str(p["id"]), "title": str(p["title"])})
                return True
            picture, model = await asyncio.gather(asyncio.to_thread(render_png, layout), asyncio.to_thread(to_glb, layout))
            await self.d.services.telegram.send_photo_bytes(chat_id, picture, f"3D view: {p['title']}")
            await self.d.services.telegram.send_document(chat_id, model_file_name(p["title"]), model, "3D model file (opens on most phones and in any 3D viewer)")
            return True
        except Exception:
            log.exception("3D model could not be sent")
            return False

    # ---------------- affordability: what the options mean for a monthly budget (all arithmetic, no model) ----------------

    async def affordability(self, state: BrokerAgentState) -> dict:
        lang, st = self._lang(state), self.d.settings
        budget = state.get("monthly_budget_etb")
        if not budget:
            return say(msg("afford_need_budget", lang))
        ids = list(state.get("selected_property_ids") or []) or [m["id"] for m in state.get("active_matches") or []]
        if not ids:
            return say(msg("find_first", lang))
        rows = await self.d.repo.get_properties(ids)
        today = self.d.now().date()
        lines, scored = [msg("afford_intro", lang, budget=fmt_etb(budget)), ""], []
        for i, p in enumerate(rows, start=1):
            summary = finance.summarize(payment_schedule(p, today))
            a = finance.affordability(float(p["price_etb"]), summary, float(budget), loan_share_pct=st.mortgage_financed_pct, rate_pct=st.mortgage_rate_pct, years=st.mortgage_years)
            scored.append((a["monthly_during_construction_etb"], p["title"]))
            key = "afford_fits" if a["fits_during_construction"] else "afford_stretch"
            lines.append(f"{i}. {p['title']}: " + msg(key, lang, monthly=fmt_etb(a["monthly_during_construction_etb"])))
            lines.append("   " + msg("afford_loan", lang, share=f"{st.mortgage_financed_pct:g}", years=st.mortgage_years, rate=f"{st.mortgage_rate_pct:g}", loan=fmt_etb(a["loan_monthly_etb"])))
        if len(scored) > 1:
            lines += ["", msg("afford_cheapest", lang, title=min(scored)[1])]
        return say("\n".join(lines), quick_replies=option_buttons(len(rows), lang)[:-1])

    # ---------------- hand-off: a buyer who asks for a person, or is plainly fed up, gets the broker ----------------

    async def handoff(self, state: BrokerAgentState) -> dict:
        d, s = self.d, self.d.settings
        await d.repo.set_human_takeover(state["buyer_id"], True)
        chat = s.broker_telegram_chat_id or s.demo_fallback_telegram_chat_id
        if chat:
            profile = state.get("buyer_profile") or {}
            who = profile.get("full_name") or state.get("buyer_id")
            why = "asked for a person" if state.get("handoff_reason") == "asked" else "sounds frustrated"
            try:
                await d.services.telegram.send_message(chat, f"Hand-off: {who} {why}. Last message: \"{str(state['messages'][-1].content)[:200]}\". The chat is yours now.")
            except Exception:
                log.exception("broker notification failed")
        return {**say(msg("handoff", self._lang(state), broker=s.broker_name)), "human_takeover": True}

    # ---------------- Node 3B: cross-developer financial analyst ----------------

    async def analyst(self, state: BrokerAgentState) -> dict:
        ids = state.get("selected_property_ids") or [m["id"] for m in state.get("active_matches") or []]
        if not ids:
            return say(msg("nothing_to_compare", self._lang(state)))
        rows = await self.d.repo.get_properties(ids)
        today = self.d.now().date()
        matrix = {}
        for row in rows:
            f = property_facts(row)
            summary = finance.summarize(payment_schedule(row, today))
            matrix[f["title"]] = {k: f[k] for k in (
                "developer", "area", "price_etb", "price_per_sqm_etb", "down_payment_pct", "down_payment_etb",
                "construction_stage_pct", "delivery_date", "est_rental_yield_pct", "reliability_score")}
            matrix[f["title"]]["payment_plan"] = finance.describe(summary, fmt_etb)
            matrix[f["title"]]["monthly_equivalent_etb"] = summary["monthly_equivalent_etb"]
        prompt = (
            "Compare these options for the buyer using only these numbers. Cover price per sqm, down payment in birr, "
            "the payment plan and the average monthly amount, construction stage, delivery date, rental yield and the "
            f"developer's reliability score (0 to 1), then say which suits the buyer's stated needs and why.\n"
            f"{json.dumps(matrix, default=str, indent=1)}\nBuyer budget: {state['buyer_preferences'].get('max_budget_etb')}"
        )
        fallback = "\n".join(
            f"{t}: {fmt_etb(v['price_etb'])}, {v['price_per_sqm_etb']:,} ETB/sqm, {v['payment_plan']}"
            for t, v in matrix.items()
        )
        lang = self._lang(state)
        known = [matrix, state.get("buyer_preferences"), [str(getattr(m, "content", "")) for m in state["messages"]]]
        return {**say(await self._write(prompt, fallback, lang, known), quick_replies=[[{"label": msg("btn_afford", lang), "text": "What can I afford?"}]]), "comparison_matrix": matrix}

    # ---------------- Node 3C: tour booking and lead registration ----------------

    async def scheduler(self, state: BrokerAgentState) -> dict:
        d, s = self.d, self.d.settings
        if state.get("intent") == "book_consultation":
            return await self._book_consultation(state)
        active = state.get("active_matches") or []
        ids = list(state.get("selected_property_ids") or [])
        if not ids:
            if len(active) == 1:
                ids = [active[0]["id"]]
            elif not active:
                return say(msg("find_first", self._lang(state)))
            else:
                return say(msg("which_to_visit", self._lang(state)))

        profile = state.get("buyer_profile") or {}
        lang = self._lang(state)
        missing = [msg(label, lang) for key, label in (("full_name", "full_name"), ("phone", "phone_number")) if not profile.get(key)]
        if missing:
            return say(msg("ask_contact", lang, missing=msg("and", lang).join(missing)))

        props = await d.repo.get_properties(ids)
        by_id = {str(p["id"]): p for p in props}
        stops = [Stop(str(p["id"]), p["lat"], p["lng"]) for p in props if p["lat"] is not None and p["lng"] is not None]
        origin = (s.office_lat, s.office_lng) if s.office_lat is not None and s.office_lng is not None else None
        plan = await d.services.routing.plan_route(stops, origin=origin) if len(stops) == len(props) else None
        ordered = [by_id[i] for i in plan.order] if plan else props
        minutes = s.tour_duration_minutes * len(ordered) + (plan.duration_seconds // 60 if plan else 0)
        duration = timedelta(minutes=math.ceil(minutes / 15) * 15)

        now = d.now()
        start = self._parse_start(state.get("tour_start_iso"))
        title = " + ".join(p["title"] for p in ordered)
        try:
            if start is None:
                return await self._offer_slots("When would you like to go?", now + timedelta(hours=2), duration, pending_ids=ids)
            end = start + duration
            if start < now + timedelta(hours=1) or not await d.services.calendar.is_available(start, end):
                return await self._offer_slots("That time isn't free.", max(start, now + timedelta(hours=2)), duration, pending_ids=ids)
            try:
                event_id = await d.services.calendar.book(
                    start=start, end=end, summary=f"Site visit: {profile['full_name']}",
                    description=f"{profile['full_name']} {profile['phone']} - {title}" + "".join(
                        f"\nListed at: {p['source_url']}" for p in ordered if is_portal_unit(p) and p.get("source_url")),
                    location=ordered[0]["sub_city"],
                )
            except SlotUnavailable:
                return await self._offer_slots("That time was just taken.", max(start, now + timedelta(hours=2)), duration, pending_ids=ids)
        except CalendarError:
            # Without a readable calendar a visit cannot be confirmed safely; say so rather than risk a double booking.
            log.exception("calendar unavailable while booking")
            return say(msg("calendar_down", self._lang(state)))

        tour = await d.repo.create_tour(
            buyer_id=state["buyer_id"], scheduled_start=start, stops=[p["id"] for p in ordered],
            route_json=plan.to_json() if plan else None, calendar_event_id=event_id,
        )

        failed: list[str] = []
        entries: list[LedgerEntry] = []
        seen: set[str] = set()
        for p in ordered:
            slug = p["developer_slug"]
            if slug in seen or is_portal_unit(p):  # a portal listing has no developer to register the lead with: the broker is told below
                continue
            seen.add(slug)
            dev_props = [q for q in ordered if q["developer_slug"] == slug]
            subject, html = emails.lead_registration(
                broker_name=s.broker_name, buyer=profile,
                developer={"name": p["developer_name"]}, properties=dev_props, start=start,
            )
            try:
                message_id = await d.services.email.send(
                    to=p["sales_email"], subject=subject, html=html, idempotency_key=f"lead-{tour['id']}-{slug}"
                )
            except Exception:
                log.exception("lead registration email to %s failed", slug)
                failed.append(slug)
                message_id = None
            entries.append(LedgerEntry(p["id"], message_id))
        await d.repo.create_ledger_rows(buyer_id=state["buyer_id"], tour_id=tour["id"], entries=entries)
        portal_units = [p for p in ordered if is_portal_unit(p)]
        if portal_units:
            await self._tell_broker_about_portal_visit(tour["id"], profile, portal_units, start)

        sheet = brochure_data(ordered, today=now.date(), broker_name=s.broker_name, buyer_name=profile.get("full_name"), route=plan.to_json() if plan else None)
        try:
            pdf = brochure_pdf(sheet)
        except Exception:
            log.exception("brochure PDF could not be made")
            pdf = None
        if profile.get("email"):
            subject, html = emails.buyer_itinerary(
                broker_name=s.broker_name, buyer=profile, stops=ordered, start=start,
                distance_km=(plan.distance_meters / 1000) if plan else 0, drive_minutes=(plan.duration_seconds // 60) if plan else 0,
            )
            try:
                await d.services.email.send(
                    to=profile["email"], subject=subject, html=html + brochure_html(sheet),
                    attachments=[Attachment("property-comparison.pdf", pdf)] if pdf else [],
                )
            except Exception:
                log.exception("itinerary email failed")
        attachments: list[dict] = []
        if pdf and state.get("chat_id"):
            try:
                await d.services.telegram.send_document(state["chat_id"], "property-comparison.pdf", pdf, "Your property comparison for the visit")
            except Exception:
                log.exception("brochure could not be sent on Telegram")
        elif pdf:
            attachments.append({"kind": "comparison", "tour_id": str(tour["id"])})  # the website makes the PDF again from the tour when it is opened
        try:
            await deliver_dossier(d.repo, d.services, s, tour["id"], today=now.date())
        except Exception:
            log.exception("pre-tour dossier failed")

        await d.repo.update_buyer(state["buyer_id"], deal_stage="SITE_VISIT_BOOKED")

        lines = [f"Your site visit is booked for {self._local(start)} with {s.broker_name}.", "", "Route:"]
        lines += [f"{i}. {p['title']} ({p['sub_city']})" for i, p in enumerate(ordered, start=1)]
        if plan and plan.distance_meters > 0:  # a lone stop with no starting point has no driving to report
            lines += ["", f"About {plan.distance_meters / 1000:.1f} km of driving in total, roughly {plan.duration_seconds // 60} minutes."]
        devs = ", ".join(sorted({p["developer_name"] for p in ordered if not is_portal_unit(p)}))
        if devs:
            lines += ["", f"I've notified {devs} that you're visiting with us."]
        if portal_units:
            lines += ["", f"{s.broker_name} is arranging the viewing of {', '.join(p['title'] for p in portal_units)} with the listing agent and will confirm."]
        if profile.get("email"):
            lines.append("A confirmation is on its way to your email.")
        high_value = max(float(p["price_etb"]) for p in ordered) >= s.high_value_threshold_etb
        if high_value:
            lines += ["", f"Because of the size of this purchase, {s.broker_name} will personally take over this chat from here."]
        text = "\n".join(lines)
        return {
            **say(text, attachments=attachments), "booked": True, "lead_reg_failed": failed, "deal_stage": "SITE_VISIT_BOOKED",
            "human_takeover": high_value, "active_matches": state.get("active_matches", []), "pending_booking": None,
        }

    async def _tell_broker_about_portal_visit(self, tour_id: Any, profile: dict, units: list[dict], start: datetime) -> None:
        """Units read from portals have no developer to email. The broker, who contacts the listing agent, gets everything needed in one message."""
        d, s = self.d, self.d.settings
        lines = [f"Portal viewing to arrange: {profile.get('full_name')} ({profile.get('phone')}) on {self._local(start)}."]
        for p in units:
            lines.append(f"- {p['title']} ({p['sub_city']}), {fmt_etb(float(p['price_etb']))}" + (f"\n  Listed at: {p['source_url']}" if p.get("source_url") else ""))
        lines.append("Contact the listing agent to confirm the time. The buyer was told you are arranging it.")
        text = "\n".join(lines)
        claim = await d.repo.claim_event(kind="PORTAL_LEAD", audience="broker", dedupe_key=f"portallead:{tour_id}", tour_id=tour_id)
        if claim is None:
            return
        chat, channel = s.broker_telegram_chat_id or s.demo_fallback_telegram_chat_id, "feed"
        if chat:
            try:
                await d.services.telegram.send_message(chat, text)
                channel = "telegram"
            except Exception:
                log.exception("portal viewing notice could not be sent to Telegram; kept in the notice feed")
        await d.repo.finish_event(claim["id"], text, channel)

    async def _book_consultation(self, state: BrokerAgentState) -> dict:
        """A phone call instead of a visit: no stops, no route, no developer registration. The broker rings the buyer."""
        d, s = self.d, self.d.settings
        profile = state.get("buyer_profile") or {}
        lang = self._lang(state)
        missing = [msg(label, lang) for key, label in (("full_name", "full_name"), ("phone", "phone_number")) if not profile.get(key)]
        if missing:
            return say(msg("ask_contact_call", lang, missing=msg("and", lang).join(missing)))
        duration = timedelta(minutes=s.consultation_minutes)
        now, start = d.now(), self._parse_start(state.get("tour_start_iso"))
        try:
            if start is None:
                return await self._offer_slots("When is a good time for the call?", now + timedelta(hours=1), duration, call=True)
            end = start + duration
            if start < now + timedelta(minutes=30) or not await d.services.calendar.is_available(start, end):
                return await self._offer_slots("That time isn't free.", max(start, now + timedelta(hours=1)), duration, call=True)
            try:
                event_id = await d.services.calendar.book(
                    start=start, end=end, summary=f"Consultation call: {profile['full_name']}",
                    description=f"Call {profile['full_name']} on {profile['phone']}", location="Phone",
                )
            except SlotUnavailable:
                return await self._offer_slots("That time was just taken.", max(start, now + timedelta(hours=1)), duration, call=True)
        except CalendarError:
            log.exception("calendar unavailable while booking a consultation")
            return say(msg("calendar_down", self._lang(state)))
        tour = await d.repo.create_tour(buyer_id=state["buyer_id"], scheduled_start=start, stops=[], route_json=None, calendar_event_id=event_id, kind="CONSULTATION")
        if profile.get("email"):
            try:
                await d.services.email.send(
                    to=profile["email"], subject="Your consultation call is confirmed",
                    html=f"<p>Hello {profile.get('full_name', '')},</p><p>{s.broker_name} will call you on <b>{profile['phone']}</b> on <b>{start.strftime('%A, %d %B %Y at %H:%M')}</b>.</p>",
                )
            except Exception:
                log.exception("consultation email failed")
        try:
            await deliver_dossier(d.repo, d.services, s, tour["id"], today=now.date())
        except Exception:
            log.exception("pre-call dossier failed")
        text = f"Your call is booked for {self._local(start)}. {s.broker_name} will phone you on {profile['phone']}."
        return {**say(text), "booked": True, "lead_reg_failed": [], "human_takeover": False, "active_matches": state.get("active_matches", [])}

    def _parse_start(self, iso: str | None) -> datetime | None:
        if not iso:
            return None
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=ZoneInfo(self.d.settings.timezone))

    async def _offer_slots(self, lead_in: str, after: datetime, duration: timedelta, *, call: bool = False, pending_ids: list[str] | None = None) -> dict:
        slots = await self.d.services.calendar.find_slots(after=after, days=7, duration=duration, limit=3)
        if not slots:
            return say(f"{lead_in} I don't have an opening in the next week. Please suggest another date.")
        options = "\n".join(f"- {self._local(sl.start)}" for sl in slots)
        verb = "Book call" if call else "Book"
        buttons = [[{"label": self._local(sl.start), "text": f"{verb} {self._local(sl.start)}"}] for sl in slots]
        extra = {"pending_booking": {"ids": list(pending_ids)}} if pending_ids else {}
        return say(f"{lead_in} I can offer:\n{options}\nWhich suits you?", quick_replies=buttons, **extra)

    # ---------------- Node 4: human-in-the-loop gate ----------------

    async def human_gate(self, state: BrokerAgentState) -> dict:
        if not (state.get("booked") and state.get("human_takeover")):
            return {}
        d = self.d
        await d.repo.set_human_takeover(state["buyer_id"], True)
        chat = d.settings.demo_fallback_telegram_chat_id
        if chat:
            profile = state.get("buyer_profile") or {}
            try:
                await d.services.telegram.send_message(
                    chat, f"High-value lead booked a tour: {profile.get('full_name')} ({profile.get('phone')}). Chat is now yours."
                )
            except Exception:
                log.exception("broker notification failed")
        return {}
