"""Is each developer's page still being read well? Judged from the record of recent runs."""
from __future__ import annotations

from typing import Any


def judge(runs: list[dict], *, failures_before_alert: int = 3) -> dict:
    """Status of one developer from its recent runs (newest first): ok, layout_changed, failing, or never_run."""
    if not runs:
        return {"status": "never_run", "consecutive_failures": 0, "message": "Not read yet."}
    failures = 0
    for run in runs:
        if run["error"] is None:
            break
        failures += 1
    last = runs[0]
    if failures:
        status = "failing" if failures >= failures_before_alert else "warning"
        return {"status": status, "consecutive_failures": failures, "message": f"The last {failures} read{'s' if failures > 1 else ''} failed: {last['error']}", "last_error": last["error"]}
    found, earlier = last["items_found"], next((r["items_found"] for r in runs[1:] if r["error"] is None and r["items_found"]), None)
    if found == 0:
        return {"status": "layout_changed", "consecutive_failures": 0, "message": "The page was read but no unit cards were found. Its layout may have changed."}
    if earlier and found is not None and found < earlier * 0.5:
        return {"status": "layout_changed", "consecutive_failures": 0, "message": f"Found {found} units where it used to find {earlier}. Part of the page may have changed."}
    return {"status": "ok", "consecutive_failures": 0, "message": f"Found {found} units." if found is not None else "Read successfully."}


async def scrape_health(repo: Any, *, failures_before_alert: int = 3) -> list[dict]:
    out = []
    for dev in await repo.list_developers():
        if not dev["scrape_url"]:
            continue
        runs = await repo.recent_page_runs(dev["id"])
        verdict = judge(runs, failures_before_alert=failures_before_alert)
        out.append({"slug": dev["slug"], "name": dev["name"], "url": dev["scrape_url"], "fetch_mode": dev["fetch_mode"], "last_run_at": runs[0]["created_at"] if runs else None,
                    "items_found": runs[0]["items_found"] if runs else None, **verdict})
    return out
