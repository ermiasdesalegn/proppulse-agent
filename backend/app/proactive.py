"""Jobs the system runs on its own: reverse-RAG alerts, 24-hour tour reminders, payment-milestone reminders.

Every message is reserved in `proactive_events` before it is sent (see Repository.claim_event), so running a job
twice, or two jobs at once, can never message anyone twice.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage

from app import proactive_messages as msg
from app.agent.graph import BrokerAgent
from app.config import Settings
from app.events import excerpt
from app.repository import Repository
from app.services import Services

log = logging.getLogger(__name__)


def _result(job: str, dry_run: bool) -> dict:
    return {"job": job, "dry_run": dry_run, "sent": 0, "skipped": 0, "failed": 0, "details": []}


class ProactiveService:
    def __init__(self, repo: Repository, agent: BrokerAgent, services: Services, settings: Settings,
                 now: Callable[[], datetime] | None = None, events: Any = None):
        self.repo, self.agent, self.services, self.settings, self.events = repo, agent, services, settings, events
        tz = ZoneInfo(settings.timezone)
        self.now = now or (lambda: datetime.now(tz))

    @property
    def _broker_chat(self) -> str:
        return self.settings.broker_telegram_chat_id or self.settings.demo_fallback_telegram_chat_id

    async def run_all(self, dry_run: bool = False) -> dict:
        jobs = [await self.reverse_rag_alerts(dry_run), await self.tour_reminders(dry_run), await self.milestone_reminders(dry_run)]
        return {"dry_run": dry_run, "jobs": jobs, **{k: sum(j[k] for j in jobs) for k in ("sent", "skipped", "failed")}}

    # ---------------- delivery ----------------

    async def _finish(self, claim: dict, message: str, channel: str) -> None:
        await self.repo.finish_event(claim["id"], message, channel)
        if self.events:
            self.events.publish("proactive", what=claim["kind"], audience=claim["audience"], channel=channel, text=excerpt(message),
                                buyer_id=None if claim["buyer_id"] is None else str(claim["buyer_id"]))

    async def _deliver_to_buyer(self, buyer: dict, text: str, *, email: tuple[str, str] | None = None) -> str | None:
        """Send on every channel the buyer has. Returns the channels used, or None if nothing got through."""
        channels: list[str] = []
        if buyer.get("telegram_id") is not None:
            try:
                for part in [text[i : i + 4000] for i in range(0, len(text), 4000)]:
                    await self.services.telegram.send_message(buyer["telegram_id"], part)
                channels.append("telegram")
            except Exception:
                log.exception("Telegram delivery to buyer %s failed", buyer["id"])
        else:
            channels.append("web")  # web-chat buyers read their transcript
        if email and buyer.get("email"):
            try:
                await self.services.email.send(to=buyer["email"], subject=email[0], html=email[1])
                channels.append("email")
            except Exception:
                log.exception("email to buyer %s failed", buyer["id"])
        return "+".join(channels) or None

    async def _remember(self, buyer: dict, text: str, active_matches: list[dict] | None = None) -> None:
        """Put the message in the transcript and in the agent's memory, so a reply like 'book the first one' works."""
        await self.repo.add_message(buyer["id"], "agent", text)
        try:
            extra = {"active_matches": active_matches} if active_matches else {}
            await self.agent.add_message(buyer["thread_id"], AIMessage(content=text), **extra)
        except Exception:
            log.exception("could not add the proactive message to the agent's memory")

    async def _notify_broker(self, kind: str, key: str, text: str, *, buyer_id: Any = None, tour_id: Any = None,
                             ledger_id: Any = None, property_id: Any = None) -> bool:
        event = await self.repo.claim_event(kind=kind, audience="broker", dedupe_key=key, buyer_id=buyer_id,
                                            tour_id=tour_id, ledger_id=ledger_id, property_id=property_id)
        if event is None:
            return False
        channel = "feed"
        if self._broker_chat:
            try:
                await self.services.telegram.send_message(self._broker_chat, text)
                channel = "telegram"
            except Exception:
                log.exception("broker notice could not be sent to Telegram; kept in the notice feed")
        await self._finish(event, text, channel)
        return True

    # ---------------- A: reverse-RAG alerts ----------------

    async def reverse_rag_alerts(self, dry_run: bool = False) -> dict:
        s, now, out = self.settings, self.now(), _result("reverse_rag_alerts", dry_run)
        since = now - timedelta(hours=s.proactive_lookback_hours)

        for r in await self.repo.recent_price_increases(since):  # buyers hear about drops; the broker hears about increases
            if dry_run:
                out["details"].append({"would": "notify broker", "price_increase": r["title"]})
            elif await self._notify_broker("PRICE_INCREASE", f"up:ph{r['price_history_id']}", msg.price_increase_notice(r), property_id=r["property_id"]):
                out["sent"] += 1
            else:
                out["skipped"] += 1

        news: list[dict] = []
        for r in await self.repo.recent_price_drops(since):
            news.append({**r, "kind": "PRICE_DROP", "event_ref": f"ph{r['price_history_id']}"})
        for r in await self.repo.recent_new_listings(since):
            news.append({**r, "kind": "NEW_LISTING", "event_ref": f"new{r['property_id']}"})

        per_buyer: dict[str, list[tuple[dict, float, bool]]] = defaultdict(list)
        for item in news:
            for m in await self.repo.match_buyers_for_property(item["property_id"], threshold=s.similarity_threshold):
                per_buyer[str(m["buyer_id"])].append((item, m["similarity"], m["human_takeover"]))

        for buyer_id, matches in per_buyer.items():
            buyer = await self.repo.get_buyer(buyer_id)
            if buyer is None:
                continue
            matches.sort(key=lambda t: t[1], reverse=True)

            if buyer["human_takeover"]:  # the broker owns this chat, so the broker hears about it instead
                for item, _, _ in matches:
                    if dry_run:
                        out["details"].append({"buyer": buyer["full_name"], "would": "notify broker", "property": item["title"]})
                    elif await self._notify_broker("TAKEOVER_MATCH", f"takeovermatch:{item['event_ref']}:{buyer_id}",
                                                   msg.takeover_match_notice(buyer, item), buyer_id=buyer["id"], property_id=item["property_id"]):
                        out["sent"] += 1
                    else:
                        out["skipped"] += 1
                continue

            cap_since = now - timedelta(hours=24)
            if await self.repo.recent_buyer_alert_count(buyer_id, cap_since) >= s.max_alerts_per_buyer_per_day:
                out["skipped"] += 1
                out["details"].append({"buyer": buyer["full_name"], "skipped": "already alerted today"})
                continue

            chosen = [m[0] for m in matches[: s.max_properties_per_alert]]
            if dry_run:
                out["details"].append({"buyer": buyer["full_name"] or buyer["thread_id"], "would_send": [c["title"] for c in chosen]})
                continue

            claims = []
            for item in chosen:
                claim = await self.repo.claim_event(
                    kind=item["kind"], audience="buyer", dedupe_key=f"{item['kind'].lower()}:{item['event_ref']}:{buyer_id}",
                    buyer_id=buyer["id"], property_id=item["property_id"])
                if claim is not None:
                    claims.append((item, claim))
            if not claims:
                out["skipped"] += 1  # everything here was sent before
                continue

            items = [c[0] for c in claims]
            text = msg.alert_text(buyer, items)
            channel = await self._deliver_to_buyer(buyer, text, email=msg.alert_email(buyer, items))
            if channel is None:
                for _, claim in claims:
                    await self.repo.release_event(claim["id"])  # try again on the next run
                out["failed"] += 1
                continue
            for _, claim in claims:
                await self._finish(claim, text, channel)
            await self._remember(buyer, text, [self._slim(i) for i in items])
            out["sent"] += 1
            out["details"].append({"buyer": buyer["full_name"] or buyer["thread_id"], "channel": channel, "properties": [i["title"] for i in items]})
        return out

    @staticmethod
    def _slim(item: dict) -> dict:
        """Same shape the search node stores, so the buyer can answer an alert with 'book the first one'."""
        return {"id": str(item["property_id"]), "developer_id": str(item["developer_id"]), "title": item["title"],
                "sub_city": item["sub_city"], "price_etb": float(item["price_etb"]), "bedrooms": item["bedrooms"], "similarity": 1.0}

    # ---------------- B: 24-hour tour reminders ----------------

    async def tour_reminders(self, dry_run: bool = False) -> dict:
        s, now, out = self.settings, self.now(), _result("tour_reminders", dry_run)
        for tour in await self.repo.tours_for_reminder(now, now + timedelta(hours=s.tour_reminder_window_hours)):
            if dry_run:
                out["details"].append({"tour": str(tour["id"]), "would_remind": tour["full_name"] or tour["thread_id"]})
                continue
            claim = await self.repo.claim_event(kind="TOUR_REMINDER", audience="buyer", dedupe_key=f"tour24:{tour['id']}",
                                                buyer_id=tour["buyer_id"], tour_id=tour["id"])
            if claim is None:
                out["skipped"] += 1
                continue
            buyer = await self.repo.get_buyer(tour["buyer_id"])
            stops = await self.repo.get_tour_stops(tour["id"])
            project = await self.repo.get_project(tour["project_id"]) if tour.get("project_id") else None
            if project is not None:
                text = msg.project_visit_reminder_text(buyer=buyer, start=tour["scheduled_start"], now=now, project=project, broker=s.broker_name, tz=s.timezone)
            else:
                text = msg.tour_reminder_text(buyer=buyer, start=tour["scheduled_start"], now=now, stops=stops, broker=s.broker_name, tz=s.timezone)
            email = ("Your site visit is coming up", "".join(f"<p>{line}</p>" for line in text.split("\n") if line.strip()))
            channel = await self._deliver_to_buyer(buyer, text, email=email)
            if channel is None:
                await self.repo.release_event(claim["id"])
                out["failed"] += 1
                continue
            first = next((x for x in stops if x.get("lat") is not None), None)
            if first and buyer.get("telegram_id") is not None:
                try:
                    await self.services.telegram.send_location(buyer["telegram_id"], first["lat"], first["lng"])
                except Exception:
                    log.exception("location pin could not be sent")
            await self._finish(claim, text, channel)
            await self.repo.mark_reminder_sent(tour["id"])
            await self._remember(buyer, text)
            out["sent"] += 1
            out["details"].append({"tour": str(tour["id"]), "channel": channel})
        return out

    # ---------------- C: payment milestones and commission ----------------

    async def milestone_reminders(self, dry_run: bool = False) -> dict:
        s, out = self.settings, _result("milestone_reminders", dry_run)
        today = self.now().date()

        for row in await self.repo.ledger_for_milestones():
            days = (row["next_buyer_milestone_date"] - today).days
            if days < 0:  # the buyer is late: tell the broker, do not nag the buyer
                text = msg.overdue_notice(row)
                if dry_run:
                    out["details"].append({"would_notify_broker": text})
                elif await self._notify_broker("MILESTONE_OVERDUE", f"overdue:{row['id']}:{row['next_buyer_milestone_date']}", text,
                                               buyer_id=row["buyer_id"], ledger_id=row["id"]):
                    out["sent"] += 1
                else:
                    out["skipped"] += 1
                continue
            if days > s.milestone_notice_days:
                continue
            stage = "due" if days == 0 else "soon"
            if dry_run:
                out["details"].append({"would_remind": row["full_name"] or row["thread_id"], "days": days})
                continue
            claim = await self.repo.claim_event(kind="MILESTONE_REMINDER", audience="buyer",
                                                dedupe_key=f"milestone:{row['id']}:{row['next_buyer_milestone_date']}:{stage}",
                                                buyer_id=row["buyer_id"], ledger_id=row["id"])
            if claim is None:
                out["skipped"] += 1
                continue
            buyer = await self.repo.get_buyer(row["buyer_id"])
            text = msg.milestone_text(buyer=buyer, row=row, days=days)
            channel = await self._deliver_to_buyer(buyer, text, email=("Payment milestone reminder", f"<p>{text}</p>"))
            if channel is None:
                await self.repo.release_event(claim["id"])
                out["failed"] += 1
                continue
            await self._finish(claim, text, channel)
            await self._remember(buyer, text)
            out["sent"] += 1
            out["details"].append({"buyer": buyer["full_name"] or buyer["thread_id"], "channel": channel, "days": days})

        for row in await self.repo.ledger_commission_pending():
            text = msg.commission_ready_notice(row)
            if dry_run:
                out["details"].append({"would_notify_broker": text})
            elif await self._notify_broker("COMMISSION_READY", f"commission:{row['id']}", text, buyer_id=row["buyer_id"], ledger_id=row["id"]):
                out["sent"] += 1
            else:
                out["skipped"] += 1
        return out
