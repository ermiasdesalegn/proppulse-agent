"""The post-deploy smoke check passes a healthy, protected service and fails one that leaves a route open."""
import httpx

from scripts.post_deploy_check import run


def service(open_cron: bool = False):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/health":
            return httpx.Response(200, json={"status": "ok", "database_configured": True}, headers={"x-request-id": "abc"})
        if path.startswith("/api/cron/") and open_cron:
            return httpx.Response(200, json={})
        keyed = request.headers.get("x-admin-key") == "k" or request.headers.get("x-cron-secret") == "c"
        return httpx.Response(200 if keyed else 401, json={})

    return httpx.Client(base_url="http://svc", transport=httpx.MockTransport(handler))


def test_a_protected_service_passes_every_check():
    results = run("http://svc", "k", "c", client=service())
    assert len(results) == 10 and all(ok for _, ok, _ in results), [r for r in results if not r[1]]


def test_a_cron_route_left_open_is_reported():
    failed = [name for name, ok, _ in run("http://svc", client=service(open_cron=True)) if not ok]
    assert failed == ["a cron route refuses a call without the secret"]
