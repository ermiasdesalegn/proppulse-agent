"""A tamper-evident audit trail: checking each agency's hash chain, and copying the trail somewhere the database cannot reach.

Migration 030 chains every `audit_log` entry to the one before it. A chain shows that an entry was removed, edited or reordered, and
where; it cannot bring the entry back, and an owner who rewrites every later hash too leaves a chain that looks whole. The export is
what closes that: entries are copied, in batches that are never changed afterwards, to a sink (a local directory for development, an
S3-compatible bucket with object lock in production), and `python -m scripts.verify_audit` compares the database with what was copied.
Without a sink only the first half exists, and the threat model says so.

Every step is safe to repeat and to interrupt. A batch is named by the first and last `seq` it holds, so a run that died after writing a
batch but before remembering it writes the same bytes to the same name next time.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from psycopg.rows import dict_row

from app.config import get_settings
from app.tenancy import resolve_org_id

log = logging.getLogger(__name__)

EXPORT = "audit.export"
BATCH = 500

_ENTRIES = (
    "select id, org_id, seq, prev_hash, hash, actor_kind, actor_id, action, target, detail, created_at, "
    "audit_entry_hash(prev_hash, seq, org_id, actor_kind, actor_id, action, target, detail, created_at) as computed "
    "from audit_log"
)


class AuditChainBroken(Exception):
    def __init__(self, problem: dict):
        super().__init__(f"audit trail of {problem['org_id']} is broken at entry {problem['seq']}: {problem['problem']}")
        self.problem = problem


class SinkConflict(Exception):
    """A batch with this name already exists in the sink with different content: something changed after it was written."""


# ---------------- the chain ----------------


def first_break(org_id: Any, rows: list[dict], *, after_seq: int = 0, after_hash: str = "") -> dict | None:
    """The first entry of one agency's rows (ordered by seq) that does not follow from the one before, or None if the chain holds."""
    expected, prev = after_seq + 1, after_hash
    for r in rows:
        where = {"org_id": str(org_id), "seq": r["seq"], "id": r["id"]}
        if r["seq"] != expected:
            return {**where, "problem": f"entries {expected} to {r['seq'] - 1} are missing" if r["seq"] > expected else "entries are out of order"}
        if r["prev_hash"] != prev:
            return {**where, "problem": "it does not follow the entry before it (that entry was changed or removed)"}
        if r["computed"] != r["hash"]:
            return {**where, "problem": "its contents do not match its hash (the entry was edited)"}
        expected, prev = expected + 1, r["hash"]
    return None


async def _fetch(pool: Any, query: str, params: dict | None = None) -> list[dict]:
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(query, params or {})
            return list(await cur.fetchall())


async def verify_chains(pool: Any, org_id: UUID | str | None = None) -> list[dict]:
    """One problem per agency whose chain is broken: the agency, the first broken entry and what is wrong with it."""
    where, params = ("where org_id = %(org)s", {"org": str(org_id)}) if org_id else ("", {})
    rows = await _fetch(pool, f"{_ENTRIES} {where} order by org_id, seq", params)
    by_org: dict[Any, list[dict]] = {}
    for r in rows:
        by_org.setdefault(r["org_id"], []).append(r)
    return [p for org, entries in by_org.items() if (p := first_break(org, entries))]


# ---------------- sinks ----------------


class Sink(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def keys(self, prefix: str) -> list[str]: ...
    def read(self, key: str) -> bytes: ...


class DirectorySink:
    """Development: batches as files in a directory. Nothing stops the machine's owner from editing them; production uses object lock."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def put(self, key: str, data: bytes) -> None:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, "xb") as f:  # never overwrites
                f.write(data)
        except FileExistsError:
            if path.read_bytes() != data:
                raise SinkConflict(key) from None

    def keys(self, prefix: str) -> list[str]:
        base = self.root / prefix
        return sorted(p.relative_to(self.root).as_posix() for p in base.rglob("*.jsonl")) if base.exists() else []

    def read(self, key: str) -> bytes:
        return (self.root / key).read_bytes()


class S3Sink:
    """Production: one object per batch in a bucket with object lock (default retention set on the bucket, so a stored batch cannot be
    replaced or deleted until it expires). `client` is a boto3 S3 client; it is created from the environment's credentials when not given."""

    def __init__(self, bucket: str, prefix: str = "", endpoint_url: str = "", client: Any = None):
        if client is None:
            import boto3  # not a dependency of the app: installed on the host that exports

            client = boto3.client("s3", endpoint_url=endpoint_url or None)
        self.client, self.bucket, self.prefix = client, bucket, prefix.strip("/")

    def _key(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def put(self, key: str, data: bytes) -> None:
        try:
            have = self.client.get_object(Bucket=self.bucket, Key=self._key(key))["Body"].read()
        except Exception as exc:  # no such key: write it; any other failure is raised
            if getattr(exc, "response", {}).get("Error", {}).get("Code") not in ("NoSuchKey", "404"):
                raise
            self.client.put_object(Bucket=self.bucket, Key=self._key(key), Body=data, ContentType="application/x-ndjson")
            return
        if have != data:
            raise SinkConflict(key)

    def keys(self, prefix: str) -> list[str]:
        out: list[str] = []
        token: str | None = None
        while True:
            kw: dict[str, Any] = {"Bucket": self.bucket, "Prefix": self._key(prefix)}
            if token:
                kw["ContinuationToken"] = token
            page = self.client.list_objects_v2(**kw)
            out += [o["Key"].removeprefix(self.prefix + "/") if self.prefix else o["Key"] for o in page.get("Contents", [])]
            token = page.get("NextContinuationToken")
            if not token:
                return sorted(out)

    def read(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=self._key(key))["Body"].read()


def sink_from_settings(settings: Any) -> Sink | None:
    """None when no sink is configured (the export is then off)."""
    if getattr(settings, "audit_export_bucket", ""):
        return S3Sink(settings.audit_export_bucket, settings.audit_export_prefix, settings.audit_export_endpoint_url)
    if getattr(settings, "audit_export_dir", ""):
        return DirectorySink(settings.audit_export_dir)
    return None


# ---------------- the export ----------------


def _line(r: dict) -> str:
    return json.dumps({
        "org_id": str(r["org_id"]), "seq": r["seq"], "id": r["id"], "prev_hash": r["prev_hash"], "hash": r["hash"],
        "actor_kind": r["actor_kind"], "actor_id": None if r["actor_id"] is None else str(r["actor_id"]), "action": r["action"],
        "target": r["target"], "detail": r["detail"], "created_at": r["created_at"].isoformat(),
    }, sort_keys=True, separators=(",", ":"))


def batch_key(org_id: Any, first: int, last: int) -> str:
    return f"{org_id}/{first:012d}-{last:012d}.jsonl"


async def pending(pool: Any, org_id: UUID | str) -> int:
    row = (await _fetch(
        pool, "select count(*)::int as n from audit_log a where a.org_id = %(org)s and a.seq > "
              "coalesce((select last_seq from audit_export_state where org_id = %(org)s), 0)", {"org": str(org_id)}))[0]
    return int(row["n"])


async def export_org(pool: Any, sink: Sink, org_id: UUID | str, *, batch: int = BATCH) -> dict:
    """Copy the agency's entries that have not been copied yet, in order, and remember how far it got. Refuses to copy past an entry that
    does not follow from the one before: a broken chain is reported (AuditChainBroken), not exported over."""
    org, exported, last = str(org_id), 0, 0
    while True:
        async with pool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute("insert into audit_export_state (org_id) values (%(org)s) on conflict do nothing", {"org": org})
                    await cur.execute("select last_seq, last_hash from audit_export_state where org_id = %(org)s for update", {"org": org})
                    state = await cur.fetchone()
                    assert state is not None
                    last = state["last_seq"]
                    await cur.execute(f"{_ENTRIES} where org_id = %(org)s and seq > %(after)s order by seq limit %(n)s",
                                      {"org": org, "after": last, "n": batch})
                    rows = list(await cur.fetchall())
                    if not rows:
                        return {"org_id": org, "exported": exported, "last_seq": last}
                    if problem := first_break(org, rows, after_seq=last, after_hash=state["last_hash"]):
                        raise AuditChainBroken(problem)
                    sink.put(batch_key(org, rows[0]["seq"], rows[-1]["seq"]), ("\n".join(_line(r) for r in rows) + "\n").encode())
                    await cur.execute("update audit_export_state set last_seq = %(s)s, last_hash = %(h)s, exported_at = now() where org_id = %(org)s",
                                      {"s": rows[-1]["seq"], "h": rows[-1]["hash"], "org": org})
                    exported += len(rows)
                    last = rows[-1]["seq"]
        if len(rows) < batch:
            return {"org_id": org, "exported": exported, "last_seq": last}


async def verify_export(pool: Any, sink: Sink, org_id: UUID | str | None = None) -> list[dict]:
    """Compare what the sink holds with the database. One problem per agency: the first exported entry that is gone, differs, or whose
    batches do not follow each other. Entries the database has and the sink does not yet are only not exported yet, not a problem."""
    orgs = [str(org_id)] if org_id else sorted({k.split("/")[0] for k in sink.keys("") if "/" in k})
    problems = []
    for org in orgs:
        db = {r["seq"]: r for r in await _fetch(pool, "select seq, hash, prev_hash from audit_log where org_id = %(org)s", {"org": org})}
        expected, prev = 1, ""
        found: dict | None = None
        for key in sink.keys(f"{org}/"):
            for raw in sink.read(key).decode().splitlines():
                e = json.loads(raw)
                where = {"org_id": org, "seq": e["seq"], "id": e["id"]}
                if e["seq"] != expected or e["prev_hash"] != prev:
                    found = {**where, "problem": "the export itself has a gap or a break here"}
                elif e["seq"] not in db:
                    found = {**where, "problem": "exported earlier, but no longer in the database (removed)"}
                elif db[e["seq"]]["hash"] != e["hash"] or db[e["seq"]]["prev_hash"] != e["prev_hash"]:
                    found = {**where, "problem": "exported earlier, but different in the database now (edited)"}
                if found:
                    break
                expected, prev = expected + 1, e["hash"]
            if found:
                break
        if found:
            problems.append(found)
    return problems


# ---------------- the scheduled job ----------------


async def audit_export(state: Any, dry_run: bool = False) -> dict:
    """The scheduled job: runs in one agency's scope and copies that agency's new entries."""
    org = resolve_org_id()
    sink = sink_from_settings(getattr(state, "settings", None) or get_settings())
    if sink is None:
        return {"job": EXPORT, "status": "off", "sent": 0, "skipped": 0, "failed": 0, "note": "no AUDIT_EXPORT_DIR or AUDIT_EXPORT_BUCKET is set"}
    pool = state.repo.pool
    if dry_run:
        return {"job": EXPORT, "status": "preview", "pending": await pending(pool, org), "sent": 0, "skipped": 0, "failed": 0}
    done = await export_org(pool, sink, org)
    log.info("audit trail of organization %s: %d entries exported, up to %d", org, done["exported"], done["last_seq"])
    return {"job": EXPORT, "status": "exported", "exported": done["exported"], "last_seq": done["last_seq"], "sent": done["exported"], "skipped": 0, "failed": 0}

