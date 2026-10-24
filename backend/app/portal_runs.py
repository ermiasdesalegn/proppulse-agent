"""Portal crawls as the broker sees them: start one from the dashboard, watch it, and hear about trouble.

The crawl itself runs on GitHub Actions (backend/scraper, .github/workflows/portal-crawl.yml): it is long, and the free Render service is small and
sleeps. This module is the thin layer around it:
  - `start` queues one row per portal in `crawl_runs` and asks GitHub to run the workflow (a fine-grained token with `actions:write` on this repo),
  - the runner reports when each portal starts and finishes (`report_start` / `report_finish`) with its counters,
  - `report_finish` turns the counters into a status and, when something needs a human, a Telegram notice to the broker.
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.config import Settings
from app.repository import Repository

log = logging.getLogger(__name__)

# Slug -> name of every portal the crawler reads. The crawler's own configuration is backend/scraper/sites.py; a test keeps the two in step.
PORTALS: dict[str, str] = {
    "ethiopiapropertycentre": "Ethiopia Property Centre",
    "livingethio": "Live Ethio",
    "guzohomes": "GuzoHomes",
    "addislist": "Addis List",
    "ethiobetoch": "Ethio Betoch",
    "ethiohome": "EthioHome",
}
STALE_AFTER = timedelta(minutes=20)  # a queued run nobody picked up: the workflow is disabled, the token is wrong, or GitHub is down


class PortalRunError(Exception):
    """Safe to show to the broker."""


class GitHubDispatcher:
    """Starts the crawl workflow. The token never leaves the backend."""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.s, self._transport = settings, transport

    @property
    def enabled(self) -> bool:
        return bool(self.s.github_token and self.s.github_repo)

    async def dispatch(self, inputs: dict[str, str]) -> None:
        if not self.enabled:
            raise PortalRunError("Starting a crawl from here needs GITHUB_TOKEN and GITHUB_REPO on the backend (see scraper/README.md).")
        url = f"https://api.github.com/repos/{self.s.github_repo}/actions/workflows/{self.s.github_workflow}/dispatches"
        headers = {"Authorization": f"Bearer {self.s.github_token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=20.0) as client:
                res = await client.post(url, headers=headers, json={"ref": self.s.github_ref, "inputs": inputs})
        except httpx.HTTPError as exc:
            raise PortalRunError(f"GitHub could not be reached ({type(exc).__name__}).") from None
        if res.status_code == 204:
            return
        reason = {401: "the GitHub token is wrong or expired", 403: "the GitHub token may not start workflows (it needs Actions: read and write)",
                  404: "the repository or the workflow file was not found, or the token cannot see it", 422: "the workflow does not accept these inputs on that branch"}
        raise PortalRunError(f"GitHub refused to start the crawl: {reason.get(res.status_code, f'it answered {res.status_code}')}.")


def _rate(stats: dict) -> float | None:
    priced, unpriced = int(stats.get("local_price", 0)), int(stats.get("no_local_price", 0))
    return priced / (priced + unpriced) if priced + unpriced else None


def _label(row: dict) -> str:
    return PORTALS.get(row["portal"], row["portal"])


class PortalRunService:
    def __init__(self, repo: Repository, settings: Settings, *, proactive: Any = None, dispatcher: GitHubDispatcher | None = None, now: Any = None):
        self.repo, self.settings, self.proactive = repo, settings, proactive
        self.dispatcher = dispatcher or GitHubDispatcher(settings)
        self.now = now or (lambda: datetime.now(UTC))

    def config(self) -> dict:
        return {"portals": [{"slug": slug, "name": name} for slug, name in PORTALS.items()], "can_start": self.dispatcher.enabled,
                "min_success_rate": self.settings.portal_min_success_rate}

    async def runs(self, limit: int = 40) -> list[dict]:
        rows = await self.repo.list_crawl_runs(limit)
        now = self.now()
        for row in rows:
            row["waiting_too_long"] = row["status"] == "QUEUED" and now - row["requested_at"] > STALE_AFTER
            row["portal_name"] = _label(row)
            row["success_rate"] = _rate(row.get("stats") or {})
        return rows

    # ---------------- from the dashboard ----------------

    async def start(self, *, site: str, limit: int, dry_run: bool) -> dict:
        if site != "all" and site not in PORTALS:
            raise PortalRunError(f"Unknown portal {site!r}.")
        portals = list(PORTALS) if site == "all" else [site]
        request_id = uuid.uuid4().hex[:12]
        mode = "full" if limit == 0 else "limited"
        rows = await self.repo.queue_crawl_runs(portals=portals, request_id=request_id, dry_run=dry_run, mode=mode, page_limit=limit or None)
        try:
            await self.dispatcher.dispatch({"site": site, "limit": str(limit), "dry_run": "true" if dry_run else "false", "request_id": request_id})
        except PortalRunError as exc:
            for row in rows:
                await self.repo.finish_crawl_run(row["id"], status="FAILED", stats={}, blocked_reason=None, error=str(exc))
            raise
        return {"request_id": request_id, "portals": portals, "dry_run": dry_run, "mode": mode}

    # ---------------- from the runner ----------------

    async def report_start(self, *, portal: str, request_id: str | None, trigger: str, run_url: str | None, dry_run: bool, mode: str, page_limit: int | None) -> dict:
        if portal not in PORTALS:
            raise PortalRunError(f"Unknown portal {portal!r}.")
        row = await self.repo.start_crawl_run(portal=portal, request_id=request_id, trigger=trigger, run_url=run_url, dry_run=dry_run, mode=mode, page_limit=page_limit)
        return {"run_id": row["id"]}

    async def report_finish(self, *, run_id: int, stats: dict, blocked_reason: str | None, error: str | None) -> dict:
        """Settle the run's status from what the runner counted, and tell the broker if a person needs to look."""
        current = next((r for r in await self.repo.list_crawl_runs(200) if r["id"] == run_id), None)
        if current is None:
            raise PortalRunError("Unknown run.")
        pages = int(stats.get("pages", 0))
        if blocked_reason:
            status = "BLOCKED"
        elif error:
            status = "FAILED"
        elif pages == 0 and current["mode"] in ("full", "limited"):
            status, error = "FAILED", "The crawl read no listing pages. The site's sitemap or address may have changed."
        else:
            status = "FINISHED"
        row = await self.repo.finish_crawl_run(run_id, status=status, stats=stats, blocked_reason=blocked_reason, error=error)
        assert row is not None
        await self._alert(row, stats)
        return {"status": status, "success_rate": _rate(stats)}

    async def _alert(self, run: dict, stats: dict) -> None:
        if self.proactive is None:
            return
        s, name = self.settings, _label(run)
        rate, priced = _rate(stats), int(stats.get("local_price", 0)) + int(stats.get("no_local_price", 0))
        notes: list[tuple[str, str]] = []
        if run["status"] == "BLOCKED":
            notes.append(("blocked", f"{name}: the site blocked the crawler ({run['blocked_reason']}). It is not worked around; nothing was lost, and the next run will try again."))
        elif run["status"] == "FAILED":
            notes.append(("failed", f"{name}: the crawl failed. {run['error']}"))
        elif rate is not None and priced >= s.portal_alert_min_pages and rate < s.portal_min_success_rate:
            before = await self.repo.previous_crawl_run(run["portal"], run["id"])
            was = _rate(before["stats"]) if before else None
            history = f" (it was {was:.0%} last time)" if was is not None else ""
            notes.append(("rate", f"{name}: only {rate:.0%} of {priced} pages had a price read locally{history}. The site's layout has probably changed, so the crawler's readers need a look."))
        if run["status"] == "FINISHED":
            changed = int(stats.get("created", 0)) + int(stats.get("price_changed", 0)) + int(stats.get("retired", 0))
            if run["trigger"] != "schedule" or changed:
                tag = " (preview, nothing saved)" if run["dry_run"] else ""
                notes.append(("done", f"{name} crawl finished{tag}: {int(stats.get('created', 0))} new, {int(stats.get('price_changed', 0))} price changes, "
                                      f"{int(stats.get('retired', 0))} retired, {int(stats.get('skipped', 0))} skipped, {int(stats.get('model_reads', 0))} Gemini calls."))
        for kind, text in notes:
            try:
                await self.proactive.notify_broker("PORTAL_ALERT", f"portalrun:{run['id']}:{kind}", text)
            except Exception:
                log.exception("portal run notice could not be sent")
