"""Rotate the master key that seals every agency's data key. Operator only; reads DATABASE_URL and the master keys from the environment.

    python -m scripts.rotate_master_key --status   how many agencies are on each master key (ids only)
    python -m scripts.rotate_master_key start      queue one re-wrap job per agency still on an older key; the server's workers run them
    python -m scripts.rotate_master_key run        do the re-wraps here, one agency after another, when no server is running

Before `start` or `run`: SECRETS_MASTER_KEYS must list the new key first and the old one after it (the server restarted with both).
Every command is safe to repeat and to interrupt; the runbook is in docs/SECURITY.md.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

import app  # noqa: F401  (picks the Windows-compatible event loop)
from app.config import get_settings
from app.jobs import JobQueue
from app.keys import key_status, rewrap_org, start_rotation, which_key
from app.vault import Vault


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", nargs="?", choices=["start", "run"], help="start: queue the re-wraps; run: do them here")
    ap.add_argument("--status", action="store_true", help="show how many agencies are on each master key")
    args = ap.parse_args(argv)
    s = get_settings()
    if not s.master_key_list:
        print("No master key is set (SECRETS_MASTER_KEYS or SECRETS_MASTER_KEY).", file=sys.stderr)
        return 2
    vault = Vault(s.master_key_list)
    pool = AsyncConnectionPool(s.database_url, min_size=1, max_size=2, open=False, kwargs={"row_factory": dict_row, "prepare_threshold": None})
    await pool.open(wait=True, timeout=30)
    try:
        if args.command == "start":
            queued = await start_rotation(pool, vault, JobQueue(pool))
            print(f"queued {sum(q.created for q in queued)} re-wrap jobs ({len(queued)} agencies not yet on {vault.current_key_id})")
        elif args.command == "run":
            async with pool.connection() as conn:
                rows = await (await conn.execute("select org_id, wrapped_key from org_keys order by org_id")).fetchall()
            behind = [r["org_id"] for r in rows if which_key(vault, r["org_id"], r["wrapped_key"]) != vault.current_key_id]
            for org in behind:
                print(json.dumps(await rewrap_org(pool, vault, org), default=str))
        status = await key_status(pool, vault)
        print(json.dumps(status, indent=2))
        if len(vault.key_ids) > 1 and status["done"]:
            print(f"Every agency is on {vault.current_key_id}. The older keys ({', '.join(vault.key_ids[1:])}) can now be removed from the environment.")
        return 1 if status["unknown"] else 0
    finally:
        await pool.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
