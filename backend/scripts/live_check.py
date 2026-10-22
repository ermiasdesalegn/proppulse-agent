"""Drive a RUNNING server end to end over HTTP with the real model, database and Telegram bot.

    terminal 1:  .venv\\Scripts\\python -m app
    terminal 2:  .venv\\Scripts\\python -m scripts.live_check

It creates one throwaway web-chat buyer and removes it (and its checkpoints) at the end.
"""
import asyncio
import sys
import time
import uuid

import httpx
import psycopg

from app.config import get_settings
from app.services.telegram import BotApiTelegramService

BASE = "http://127.0.0.1:8000"


def show(label: str, text: str | None) -> None:
    print(f"{label}: {' '.join((text or '(none)').split())[:230]}")


async def main() -> int:
    s = get_settings()
    headers = {"X-Admin-Key": s.admin_api_key}
    session = f"livecheck-{uuid.uuid4().hex[:10]}"
    thread = f"web:{session}"
    ok = True

    async with httpx.AsyncClient(base_url=BASE, headers=headers, timeout=180) as c:
        for _ in range(60):
            try:
                health = (await c.get("/health")).json()
                if health.get("checkpointer"):
                    break
            except httpx.TransportError:
                pass
            await asyncio.sleep(1)
        else:
            print("server did not become ready")
            return 1
        print("health:", {k: health[k] for k in ("status", "checkpointer", "telegram")})

        try:
            me = await BotApiTelegramService(s.telegram_bot_token).get_me()
            print(f"telegram bot: @{me['username']} (token valid)")
        except Exception as exc:
            print(f"telegram: NOT WORKING ({exc}). Check TELEGRAM_BOT_TOKEN in backend/.env; regenerate it in BotFather if needed.")

        try:
            t0 = time.time()
            first = (await c.post("/api/chat/web", json={"session_id": session, "text": "Hello!"})).json()
            show(f"turn 1 ({time.time() - t0:.0f}s)", first["reply"])
            t0 = time.time()
            second = (await c.post("/api/chat/web", json={"session_id": session, "text": "I need a 3 bedroom apartment in Bole, budget 25 million birr, with a balcony."})).json()
            show(f"turn 2 ({time.time() - t0:.0f}s)", second["reply"])
            print("options shown:", [m["title"] for m in second["active_matches"]])
            ok &= bool(second["active_matches"]) and not second["error"]

            buyer_id = first["buyer_id"]
            buyer = (await c.get(f"/api/buyers/{buyer_id}")).json()
            print("crm:", {k: buyer[k] for k in ("max_budget_etb", "min_bedrooms", "preferred_sub_cities", "deal_stage", "lead_score")})

            await c.post(f"/api/buyers/{buyer_id}/takeover", json={"on": True})
            held = (await c.post("/api/chat/web", json={"session_id": session, "text": "Can you go lower on price?"})).json()
            ok &= held["takeover"] and held["reply"] is None
            await c.post("/api/chat/broker-reply", json={"buyer_id": buyer_id, "text": "Yes, let me ask the developer about a discount."})
            await c.post(f"/api/buyers/{buyer_id}/takeover", json={"on": False})
            print("takeover: buyer message held, broker reply stored, handed back")

            rows = (await c.get(f"/api/chat/web/{session}/messages")).json()["messages"]
            print("transcript roles:", [m["role"] for m in rows])
        finally:
            with psycopg.connect(s.database_url, autocommit=True) as db:
                for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                    db.execute(f"delete from {table} where thread_id = %s", (thread,))
                db.execute("delete from buyers_crm where thread_id = %s", (thread,))
            print("cleaned up the test buyer")

    print("RESULT:", "ok" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
