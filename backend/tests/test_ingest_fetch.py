import httpx
import pytest

from app.ingest.fetch import FetchError, HttpFetcher, check_url, is_public_address

PUBLIC = "93.184.216.34"


def resolver(mapping):
    async def resolve(host, port):
        if host not in mapping:
            raise OSError("name not known")
        return mapping[host]
    return resolve


def fetcher(handler, mapping=None, **kw):
    return HttpFetcher(transport=httpx.MockTransport(handler), resolver=resolver(mapping or {"dev.example": [PUBLIC], "cdn.example": [PUBLIC]}), **kw)


@pytest.mark.parametrize("address,public", [
    (PUBLIC, True), ("8.8.8.8", True), ("2606:4700:4700::1111", True),
    ("127.0.0.1", False), ("10.0.0.5", False), ("192.168.1.10", False), ("172.16.0.1", False), ("169.254.169.254", False),
    ("0.0.0.0", False), ("::1", False), ("fe80::1", False), ("fc00::1", False), ("::ffff:10.0.0.1", False), ("224.0.0.1", False),
])
def test_which_addresses_count_as_public(address, public):
    assert is_public_address(address) is public


@pytest.mark.parametrize("url,message", [
    ("file:///etc/passwd", "only http"), ("ftp://dev.example/x", "only http"), ("gopher://dev.example", "only http"),
    ("https://user:pass@dev.example/x", "credentials"), ("https:///nohost", "no host"),
])
async def test_dangerous_or_malformed_addresses_are_refused(url, message):
    with pytest.raises(FetchError, match=message):
        await check_url(url, resolver=resolver({"dev.example": [PUBLIC]}))


async def test_internal_hosts_are_blocked_unless_explicitly_allowed():
    mapping = {"intranet.example": ["10.1.2.3"], "localhost": ["127.0.0.1"], "mixed.example": [PUBLIC, "192.168.0.9"]}
    for host in mapping:
        with pytest.raises(FetchError, match="private or internal"):
            await check_url(f"http://{host}/", resolver=resolver(mapping))
    await check_url("http://localhost:8000/", allow_private=True, resolver=resolver(mapping))  # development opt-in


async def test_an_unresolvable_host_is_a_clear_error():
    with pytest.raises(FetchError, match="could not resolve"):
        await check_url("https://nowhere.example/", resolver=resolver({}))


async def test_a_normal_page_is_fetched_with_an_honest_user_agent():
    seen = {}

    def handler(req):
        seen["ua"] = req.headers["user-agent"]
        return httpx.Response(200, text="<html>units</html>", headers={"content-type": "text/html; charset=utf-8"})

    assert await fetcher(handler).get_text("https://dev.example/units") == "<html>units</html>"
    assert "PropPulseBot" in seen["ua"]


async def test_a_redirect_to_an_internal_address_is_stopped_at_the_second_hop():
    hit = []

    def handler(req):
        hit.append(str(req.url))
        if req.url.host == "dev.example":
            return httpx.Response(302, headers={"location": "http://internal.example/secret"})
        return httpx.Response(200, text="secret")

    with pytest.raises(FetchError, match="private or internal"):
        await fetcher(handler, {"dev.example": [PUBLIC], "internal.example": ["10.0.0.7"]}).get_text("https://dev.example/go")
    assert hit == ["https://dev.example/go"], "the internal address was never requested"


async def test_redirects_are_followed_a_few_times_and_then_given_up_on():
    def handler(req):
        if req.url.path == "/a":
            return httpx.Response(301, headers={"location": "/b"})
        if req.url.path == "/b":
            return httpx.Response(200, text="done", headers={"content-type": "text/html"})
        return httpx.Response(302, headers={"location": req.url.path + "x"})

    assert await fetcher(handler).get_text("https://dev.example/a") == "done"
    with pytest.raises(FetchError, match="too many redirects"):
        await fetcher(handler).get_text("https://dev.example/loop")


async def test_error_statuses_oversize_bodies_and_wrong_content_types_are_refused():
    with pytest.raises(FetchError, match="404"):
        await fetcher(lambda r: httpx.Response(404)).get_text("https://dev.example/x")
    with pytest.raises(FetchError, match="too large"):
        await fetcher(lambda r: httpx.Response(200, content=b"x" * 3_100_000, headers={"content-type": "text/html"})).get_text("https://dev.example/x")
    with pytest.raises(FetchError, match="too large"):
        await fetcher(lambda r: httpx.Response(200, content=b"x", headers={"content-type": "text/html", "content-length": "99999999"})).get_text("https://dev.example/x")
    with pytest.raises(FetchError, match="expected a web page"):
        await fetcher(lambda r: httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})).get_text("https://dev.example/x")


async def test_images_must_really_be_images_within_the_size_limit():
    png = httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"})
    assert await fetcher(lambda r: png).get_bytes("https://cdn.example/p.png") == (b"\x89PNG", "image/png")
    with pytest.raises(FetchError, match="expected an image"):
        await fetcher(lambda r: httpx.Response(200, text="<html>", headers={"content-type": "text/html"})).get_bytes("https://cdn.example/p.png")
    with pytest.raises(FetchError, match="too large"):
        await fetcher(lambda r: httpx.Response(200, content=b"x" * 2000, headers={"content-type": "image/png"})).get_bytes("https://cdn.example/p.png", max_bytes=1000)


async def test_network_failures_do_not_leak_internals():
    def down(req):
        raise httpx.ConnectError("boom", request=req)

    with pytest.raises(FetchError, match="could not fetch the page: ConnectError"):
        await fetcher(down).get_text("https://dev.example/x")
