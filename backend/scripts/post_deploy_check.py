"""Smoke-checks a running backend.  Usage:  python -m scripts.post_deploy_check https://your-service.run.app [--admin-key KEY] [--cron-secret SECRET]

Without keys it checks what an outsider can see: the service is healthy, protected routes refuse anonymous calls and a webhook without
its secret is turned away. With the keys it also checks that the keys work and that a dry-run of each scheduled job succeeds.
Nothing is sent to buyers: the cron calls use dry_run=true.
"""
import argparse
import sys

import httpx

CRON_JOBS = ("/api/cron/daily-proactive-run", "/api/cron/reminders", "/api/cron/scrape-market")


def run(base: str, admin_key: str = "", cron_secret: str = "", client: httpx.Client | None = None) -> list[tuple[str, bool, str]]:
    http = client or httpx.Client(base_url=base, timeout=60)
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))

    r = http.get("/health")
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    check("health answers 200 with status ok", r.status_code == 200 and body.get("status") == "ok", f"{r.status_code} {body.get('status')}")
    check("the database is configured", bool(body.get("database_configured")), str(body.get("database_configured")))
    check("every response carries a request id", bool(r.headers.get("x-request-id")), r.headers.get("x-request-id", "missing"))

    r = http.get("/api/samples/listing")
    check("an admin route refuses an anonymous call", r.status_code in (401, 403), str(r.status_code))
    r = http.post("/api/cron/reminders?dry_run=true")
    check("a cron route refuses a call without the secret", r.status_code in (401, 403), str(r.status_code))
    r = http.post("/api/telegram/webhook", json={"update_id": 1})
    check("the Telegram webhook refuses a call without its secret", r.status_code in (401, 403), str(r.status_code))

    if admin_key:
        r = http.get("/api/samples/listing", headers={"X-Admin-Key": admin_key})
        check("the admin key is accepted", r.status_code == 200, str(r.status_code))
    if cron_secret:
        for path in CRON_JOBS:
            r = http.post(f"{path}?dry_run=true", headers={"X-Cron-Secret": cron_secret})
            check(f"dry run of {path}", r.status_code == 200, str(r.status_code))
    return results


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("url")
    p.add_argument("--admin-key", default="")
    p.add_argument("--cron-secret", default="")
    args = p.parse_args()
    results = run(args.url.rstrip("/"), args.admin_key, args.cron_secret)
    for name, ok, detail in results:
        print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f"  ({detail})" if not ok else ""))
    failed = [r for r in results if not r[1]]
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
