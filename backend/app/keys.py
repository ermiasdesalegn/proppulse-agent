"""Master-key rotation: which master key each agency's data key is sealed with, re-sealing it with the newest, and refusing to start
when a stored key names a master key the server was not given.

The runbook is in docs/SECURITY.md. In short: add the new key in front (SECRETS_MASTER_KEYS="new,old"), restart, run
`python -m scripts.rotate_master_key start`, wait for `--status` to show no agency on the old key, then remove the old key.

Every step is safe to repeat and to interrupt. A re-wrap job locks the agency's key row, opens it with whichever key it names, seals it
with the newest, checks that a stored secret still opens, and commits; a crash leaves that agency on its old key, which still works
because both keys are loaded. Only `org_keys.wrapped_key` changes: the secrets themselves are sealed with the data key, which stays.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.jobs import Enqueued, JobContext, JobQueue, job
from app.vault import UnknownKey, Vault, VaultError, stored_key_id

log = logging.getLogger(__name__)

REWRAP = "keys.rewrap"
UNKNOWN = "unknown"


async def _rows(pool: Any, query: str, params: dict | None = None) -> list[dict]:
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(query, params or {})
            return list(await cur.fetchall())


def which_key(vault: Vault, org_id: UUID | str, wrapped: str) -> str:
    """The id of the master key this data key is sealed with; UNKNOWN when this server has no key that opens it."""
    try:
        kid = stored_key_id(wrapped)
    except VaultError:
        return UNKNOWN
    if kid is not None:
        return kid if kid in vault.key_ids else f"{UNKNOWN}:{kid}"
    try:
        return vault.opening_key_id(org_id, wrapped)  # a v1 value: the loaded key that opens it
    except VaultError:
        return UNKNOWN


async def key_status(pool: Any, vault: Vault | None) -> dict:
    """How many agencies' data keys each master key seals. Ids only: nothing here can open anything."""
    rows = await _rows(pool, "select org_id, wrapped_key from org_keys order by org_id")
    if vault is None:
        return {"configured": False, "agencies": len(rows), "current": None, "loaded": [], "by_key": {}, "unknown": len(rows), "done": not rows}
    by_key: dict[str, int] = {}
    for r in rows:
        k = which_key(vault, r["org_id"], r["wrapped_key"])
        by_key[k] = by_key.get(k, 0) + 1
    unknown = sum(n for k, n in by_key.items() if k.startswith(UNKNOWN))
    return {
        "configured": True, "agencies": len(rows), "current": vault.current_key_id, "loaded": vault.key_ids, "by_key": by_key,
        "unknown": unknown, "done": by_key.get(vault.current_key_id, 0) == len(rows),
    }


async def check_keys(pool: Any, vault: Vault | None) -> dict:
    """At start-up: refuse to run when some agency's data key cannot be opened with the keys this server has, and say which."""
    status = await key_status(pool, vault)
    if vault is not None and status["unknown"]:
        named = ", ".join(f"{k.removeprefix(UNKNOWN + ':') or 'a v1 key'} ({n} agencies)" for k, n in status["by_key"].items() if k.startswith(UNKNOWN))
        raise RuntimeError(
            f"{status['unknown']} agency data keys cannot be opened with the master keys in SECRETS_MASTER_KEYS / SECRETS_MASTER_KEY. "
            f"They need: {named}. Put that key back (after the new one) and finish the rotation before removing it.")
    return status


async def rewrap_org(pool: Any, vault: Vault, org_id: UUID | str) -> dict:
    """Seal one agency's data key with the newest master key, in one transaction. Safe to run twice: a key already on the newest
    master key is left alone. The row lock serialises this with a secret being stored for the same agency at the same moment."""
    org = str(org_id)
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("select wrapped_key from org_keys where org_id = %(org)s for update", {"org": org})
                row = await cur.fetchone()
                if row is None:
                    return {"org_id": org, "status": "no key"}
                before = which_key(vault, org, row["wrapped_key"])
                if before == vault.current_key_id:
                    return {"org_id": org, "status": "already current", "key": before}
                fresh = vault.rewrap(org, row["wrapped_key"])
                await cur.execute("select name, ciphertext from org_secrets where org_id = %(org)s order by name limit 1", {"org": org})
                sample = await cur.fetchone()
                if sample is not None:  # prove it before keeping it: a secret must still open through the re-sealed key
                    vault.open(vault.open_data_key(org, fresh), org, sample["name"], sample["ciphertext"])
                await cur.execute("update org_keys set wrapped_key = %(w)s where org_id = %(org)s", {"w": fresh, "org": org})
                await cur.execute(
                    "insert into audit_log (org_id, actor_kind, action, target, detail) values (%(org)s, 'system', 'key.rewrap', 'data key', %(d)s)",
                    {"org": org, "d": Jsonb({"from": before, "to": vault.current_key_id})})
    log.info("data key of organization %s re-sealed: %s -> %s", org, before, vault.current_key_id)
    return {"org_id": org, "status": "rewrapped", "from": before, "to": vault.current_key_id}


async def start_rotation(pool: Any, vault: Vault, queue: JobQueue) -> list[Enqueued]:
    """Queue a re-wrap for every agency whose data key is not on the newest master key. Running it again queues only the rest."""
    rows = await _rows(pool, "select org_id, wrapped_key from org_keys order by org_id")
    behind = [r["org_id"] for r in rows if which_key(vault, r["org_id"], r["wrapped_key"]) != vault.current_key_id]
    unknown = [r["org_id"] for r in rows if which_key(vault, r["org_id"], r["wrapped_key"]).startswith(UNKNOWN)]
    if unknown:
        raise UnknownKey(f"needed by {len(unknown)} agencies")
    return [await queue.enqueue(org, REWRAP, dedupe_key=f"to:{vault.current_key_id}") for org in behind]


@job(REWRAP)
async def _rewrap_job(ctx: JobContext) -> dict:
    vault = ctx.state.runtime.vault
    if vault is None:
        raise VaultError("this server has no master key")
    return await rewrap_org(ctx.state.repo.pool, vault, ctx.org_id)
