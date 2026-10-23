"""The numbers behind the dashboard's Analytics page, computed in the database.

Everything here is a read; each figure comes from a table the system already keeps (buyers, ledger, messages, model calls, events).
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

STAGES = ["NEW", "QUALIFIED", "INVENTORY_PENDING", "SITE_VISIT_BOOKED", "TOUR_COMPLETED", "CONTRACT_SIGNED", "COMMISSION_PENDING", "COMMISSION_PAID", "LOST"]
LEDGER = ["LEAD_REGISTERED", "TOUR_COMPLETED", "CONTRACT_SIGNED", "COMMISSION_PENDING", "COMMISSION_PAID", "CLOSED_LOST"]
EVAL_SUMMARY = Path(__file__).resolve().parents[1] / "evals" / "summary.json"


def _f(value: Any) -> float | None:
    return None if value is None else float(value)


async def build(repo: Any, *, days: int = 30, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    since = now - timedelta(days=days)
    q = repo._fetch
    one = repo._one

    # Each figure is its own query; run them side by side so a page of charts costs one round trip, not sixteen.
    (stage_rows, ledger_rows, messages, tours, turns, failed_row, models, nodes, per_conversation, scores, objections, totals) = await asyncio.gather(
        q("select deal_stage, count(*)::int as n from buyers_crm group by 1"),
        q("select status, count(*)::int as n, coalesce(sum(expected_commission_etb), 0) as commission, coalesce(sum(contract_value_etb), 0) as contracts "
          "from commission_ledger group by 1"),
        q("select date_trunc('day', created_at)::date as day, role, count(*)::int as n from chat_messages where created_at >= %s group by 1, 2 order by 1", (since,)),
        q("select date_trunc('day', created_at)::date as day, kind, count(*)::int as n from tours_and_appointments where created_at >= %s group by 1, 2 order by 1", (since,)),
        one("select count(*)::int as n, percentile_cont(0.5) within group (order by (data->>'ms')::numeric) as p50, "
            "percentile_cont(0.95) within group (order by (data->>'ms')::numeric) as p95 from event_log where kind = 'turn_finished' and created_at >= %s", (since,)),
        one("select count(*)::int as n from event_log where kind = 'turn_failed' and created_at >= %s", (since,)),
        q("select coalesce(model, 'unknown') as model, count(*)::int as calls, round(avg(latency_ms))::int as avg_ms, "
          "coalesce(sum(input_tokens), 0)::int as input_tokens, coalesce(sum(output_tokens), 0)::int as output_tokens, "
          "count(*) filter (where outcome <> 'ok')::int as errors, count(*) filter (where cardinality(fallbacks) > 0)::int as fell_back "
          "from model_calls where created_at >= %s group by 1 order by 2 desc", (since,)),
        q("select coalesce(node, 'outside the graph') as node, count(*)::int as calls, round(avg(latency_ms))::int as avg_ms, "
          "coalesce(sum(input_tokens + output_tokens), 0)::int as tokens from model_calls where created_at >= %s and outcome = 'ok' group by 1 order by 2 desc", (since,)),
        one("select count(distinct buyer_id)::int as buyers, count(*)::int as calls, coalesce(sum(input_tokens + output_tokens), 0)::int as tokens "
            "from model_calls where created_at >= %s and buyer_id is not null", (since,)),
        q("select (least(lead_score, 99) / 20 * 20)::int as bucket, count(*)::int as n from buyers_crm group by 1 order by 1"),
        q("select lower(o->>'text') as text, count(*)::int as n from buyers_crm, jsonb_array_elements(objections) o group by 1 order by 2 desc, 1 limit 8"),
        one("select (select count(*) from buyers_crm)::int as buyers, (select count(*) from chat_messages where created_at >= %s)::int as messages, "
            "(select count(*) from tours_and_appointments where created_at >= %s)::int as tours", (since, since)),
    )
    stages = {r["deal_stage"]: r["n"] for r in stage_rows}
    ledger = {r["status"]: r for r in ledger_rows}
    open_commission = sum(float(ledger[s]["commission"]) for s in ledger if s not in ("COMMISSION_PAID", "CLOSED_LOST"))
    paid_commission = float(ledger["COMMISSION_PAID"]["commission"]) if "COMMISSION_PAID" in ledger else 0.0
    failed_turns = failed_row["n"]
    scores = [dict(r) for r in scores]

    buyers_n = max(per_conversation["buyers"], 1)
    return {
        "generated_at": now.isoformat(), "days": days, "totals": totals,
        "funnel": [{"stage": s, "buyers": stages.get(s, 0)} for s in STAGES],
        "ledger": [{"status": s, "deals": ledger[s]["n"] if s in ledger else 0, "expected_commission_etb": float(ledger[s]["commission"]) if s in ledger else 0.0,
                    "contract_value_etb": float(ledger[s]["contracts"]) if s in ledger else 0.0} for s in LEDGER],
        "commission": {"open_etb": open_commission, "paid_etb": paid_commission},
        "messages_per_day": [{"day": r["day"].isoformat(), "role": r["role"], "n": r["n"]} for r in messages],
        "tours_per_day": [{"day": r["day"].isoformat(), "kind": r["kind"], "n": r["n"]} for r in tours],
        "turns": {"count": turns["n"], "failed": failed_turns, "p50_ms": _f(turns["p50"]), "p95_ms": _f(turns["p95"])},
        "models": models, "nodes": nodes,
        "per_conversation": {"buyers": per_conversation["buyers"], "model_calls": round(per_conversation["calls"] / buyers_n, 1), "tokens": round(per_conversation["tokens"] / buyers_n)},
        "score_distribution": [{"from": r["bucket"], "to": r["bucket"] + 19 if r["bucket"] < 80 else 100, "buyers": r["n"]} for r in scores],
        "objections": objections,
        "evaluation": json.loads(EVAL_SUMMARY.read_text(encoding="utf-8")) if EVAL_SUMMARY.exists() else None,
    }
