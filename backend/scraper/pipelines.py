"""Hands the crawler's items to the FastAPI backend in batches (POST /api/ingest/portal), where they go through the same
IngestionService as every other source: unchanged listings are skipped, price changes are recorded, new listings are embedded.

It also reports each portal's run to the backend (POST /api/portal/runs/report): once when the portal starts and once when it ends, with the
counters, so the dashboard shows the run and the broker is told about a block, a failure or a site that stopped parsing.

Set PROPPULSE_BACKEND_URL and PROPPULSE_ADMIN_KEY to send. Without them the pipeline only counts, which is the way to try a site
safely: `scrapy crawl portals -a site=guzohomes -O guzohomes.jsonl` writes the items to a file and touches nothing.
"""
from __future__ import annotations

import os
from collections import defaultdict
from typing import Any

import httpx

ITEM_FIELDS = ("url", "ref", "title", "price_etb", "sqm", "bedrooms", "sub_city", "property_type", "deal", "description", "lat", "lng", "text", "found_by")


def run_trigger(request_id: str | None) -> str:
    """Who started this run: the dashboard (it passes a request id), a person in the GitHub UI or a terminal, or the schedule."""
    if request_id:
        return "dashboard"
    return "schedule" if os.environ.get("PORTAL_TRIGGER") == "schedule" else "manual"


class BackendPipeline:
    def __init__(self, backend_url: str, admin_key: str, batch_size: int, llm_budget: int, dry_run: bool):
        self.backend_url, self.admin_key = backend_url.rstrip("/"), admin_key
        self.batch_size, self.llm_budget, self.dry_run = batch_size, llm_budget, dry_run
        self.buffers: dict[str, list[dict]] = defaultdict(list)
        self.names: dict[str, str] = {}
        self.run_ids: dict[str, int] = {}
        self.client: httpx.Client | None = None

    @classmethod
    def from_crawler(cls, crawler: Any) -> BackendPipeline:
        s = crawler.settings
        pipeline = cls(os.environ.get("PROPPULSE_BACKEND_URL", ""), os.environ.get("PROPPULSE_ADMIN_KEY", ""), s.getint("PORTAL_BATCH_SIZE", 25),
                       s.getint("PORTAL_LLM_BUDGET_PER_BATCH", 3), s.getbool("PORTAL_DRY_RUN", False))
        pipeline.crawler = crawler
        return pipeline

    def open_spider(self, spider: Any) -> None:
        if self.backend_url and self.admin_key:
            self.client = httpx.Client(timeout=180.0, headers={"X-Admin-Key": self.admin_key})  # a batch may wait on a few model calls
            for site in spider.sites:
                self._report_start(site.slug, spider)
        else:
            spider.logger.info("PROPPULSE_BACKEND_URL / PROPPULSE_ADMIN_KEY not set: items are not sent to the backend")

    def process_item(self, item: dict, spider: Any) -> dict:
        if self.client is not None:
            self.names[item["portal"]] = item["portal_name"]
            self.buffers[item["portal"]].append({k: item.get(k) for k in ITEM_FIELDS})
            if len(self.buffers[item["portal"]]) >= self.batch_size:
                self._flush(item["portal"], spider)
        return item

    def close_spider(self, spider: Any) -> None:
        for portal in list(self.buffers):
            if self.buffers[portal]:
                self._flush(portal, spider)
        if self.client is not None:
            self._sync_vanished(spider)
            for site in spider.sites:
                self._report_finish(site.slug, spider)
            self.client.close()

    # ---------------- reporting the run ----------------

    def _post(self, path: str, body: dict, spider: Any) -> dict | None:
        assert self.client is not None
        try:
            res = self.client.post(f"{self.backend_url}{path}", json=body)
            res.raise_for_status()
            return res.json()
        except httpx.HTTPError as exc:
            spider.logger.error("backend call %s failed: %s", path, exc)
            return None

    def _report_start(self, slug: str, spider: Any) -> None:
        request_id = os.environ.get("PORTAL_REQUEST_ID") or None
        mode = "incremental" if spider.since else "full" if spider.limit == 0 else "limited"
        answer = self._post("/api/portal/runs/report", {
            "phase": "start", "portal": slug, "request_id": request_id, "trigger": run_trigger(request_id), "run_url": os.environ.get("PORTAL_RUN_URL") or None,
            "dry_run": self.dry_run, "mode": mode, "page_limit": spider.limit or None}, spider)
        if answer:
            self.run_ids[slug] = answer["run_id"]

    def _report_finish(self, slug: str, spider: Any) -> None:
        if slug not in self.run_ids:
            return
        stats = self.crawler.stats
        count = lambda key: int(stats.get_value(f"portals/{slug}/{key}", 0))  # noqa: E731
        batches_failed = count("batches_failed")
        numbers = {
            "pages": spider.done[slug], "failed_pages": spider.failed[slug], "local_price": count("local_price"), "no_local_price": count("no_local_price"),
            "not_for_sale": count("not_for_sale"), "model_reads": count("model_reads"), "created": count("backend_created"),
            "price_changed": count("backend_price_changed"), "unchanged": count("backend_unchanged"), "skipped": count("backend_skipped"),
            "retired": count("retired"), "batches_failed": batches_failed,
        }
        error = f"The backend rejected {batches_failed} batch(es) of listings. Check the admin key and that the latest migration is applied." if batches_failed else None
        self._post("/api/portal/runs/report", {"phase": "finish", "portal": slug, "run_id": self.run_ids[slug], "stats": numbers,
                                               "blocked_reason": spider.stopped.get(slug), "error": error}, spider)

    # ---------------- items and removals ----------------

    def _sync_vanished(self, spider: Any) -> None:
        """Only after a complete, clean crawl of a site: tell the backend which listings exist, so it retires the rest."""
        for site in spider.sites:
            slug = site.slug
            if slug not in self.names:
                continue  # nothing was sent for this site, so there is nothing to compare
            if not spider.sync_ready(slug):
                spider.logger.info("%s: not a complete crawl, so no listing is retired", slug)
                continue
            report = self._post("/api/ingest/portal/sync", {"portal": slug, "seen_refs": sorted(spider.refs[slug]), "dry_run": self.dry_run}, spider)
            if report is None:
                continue
            self.crawler.stats.set_value(f"portals/{slug}/retired", len(report.get("removed", [])))
            spider.logger.info("%s: %s, %d retired", slug, report.get("sync"), len(report.get("removed", [])))

    def _flush(self, portal: str, spider: Any) -> None:
        items, self.buffers[portal] = self.buffers[portal], []
        body = {"portal": portal, "name": self.names[portal], "items": items, "dry_run": self.dry_run, "llm_budget": self.llm_budget}
        stats = self.crawler.stats
        report = self._post("/api/ingest/portal", body, spider)
        if report is None:
            stats.inc_value(f"portals/{portal}/batches_failed")
            spider.logger.error("backend rejected a batch of %d from %s", len(items), portal)
            return
        for key, value in report.get("counts", {}).items():
            stats.inc_value(f"portals/{portal}/backend_{key}", value)
        stats.inc_value(f"portals/{portal}/model_reads", report.get("model_reads", 0))
        spider.logger.info("%s: %s (model reads: %s)", portal, report.get("counts"), report.get("model_reads"))
