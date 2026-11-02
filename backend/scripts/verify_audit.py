"""Check every agency's audit trail for removed, edited or reordered entries. Operator only; reads DATABASE_URL (and the sink, if one is set).

    python -m scripts.verify_audit              recompute every hash chain, and compare with the export when a sink is configured
    python -m scripts.verify_audit --org <id>   one agency
    python -m scripts.verify_audit --json       the problems as JSON

Exit status 0: nothing wrong. 1: at least one trail is broken (the agency and its first broken entry are printed). 2: could not run.
Without a sink (AUDIT_EXPORT_DIR or AUDIT_EXPORT_BUCKET) only the chain is checked, which shows an edit or a removal in the middle of the
trail but cannot show that the newest entries were cut off or that the whole chain was rewritten.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

import app  # noqa: F401  (picks the Windows-compatible event loop)
from app.auditchain import sink_from_settings, verify_chains, verify_export
from app.config import get_settings


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--org", help="check only this agency (its id)")
    ap.add_argument("--json", action="store_true", help="print the problems as JSON")
    args = ap.parse_args(argv)
    s = get_settings()
    pool = AsyncConnectionPool(s.database_url, min_size=1, max_size=2, open=False, kwargs={"row_factory": dict_row, "prepare_threshold": None})
    await pool.open(wait=True, timeout=30)
    try:
        sink = sink_from_settings(s)
        problems = [{"check": "chain", **p} for p in await verify_chains(pool, args.org)]
        if sink is not None:
            problems += [{"check": "export", **p} for p in await verify_export(pool, sink, args.org)]
    except Exception as exc:  # a sink that cannot be reached is not a clean bill of health
        print(f"could not check: {exc}", file=sys.stderr)
        return 2
    finally:
        await pool.close()
    if args.json:
        print(json.dumps(problems, indent=2, default=str))
    elif not problems:
        print("audit trails intact" + ("" if sink is not None else " (chain only: no export sink is configured)"))
    else:
        for p in problems:
            print(f"BROKEN [{p['check']}] agency {p['org_id']}, entry {p['seq']} (id {p['id']}): {p['problem']}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
