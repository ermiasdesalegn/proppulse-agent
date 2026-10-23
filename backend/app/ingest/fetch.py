"""Fetching listing pages and floor-plan images. The address can come from an admin form, so it is treated as untrusted:
only http(s), no embedded credentials, no private or internal addresses (checked again on every redirect), capped size."""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Awaitable, Callable, Protocol
from urllib.parse import urljoin, urlsplit

import httpx

MAX_PAGE_BYTES = 3_000_000
MAX_IMAGE_BYTES = 5_000_000
USER_AGENT = "PropPulseBot/1.0 (listing aggregator for a licensed broker)"
IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


class FetchError(Exception):
    pass


Resolver = Callable[[str, int], Awaitable[list[str]]]


async def system_resolver(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


def is_public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


async def check_url(url: str, *, allow_private: bool = False, resolver: Resolver = system_resolver) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise FetchError("only http and https addresses can be fetched")
    if not parts.hostname:
        raise FetchError("the address has no host")
    if parts.username or parts.password:
        raise FetchError("addresses with embedded credentials are not allowed")
    try:
        addresses = await resolver(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    except OSError:
        raise FetchError(f"could not resolve {parts.hostname}") from None
    if not addresses:
        raise FetchError(f"could not resolve {parts.hostname}")
    if not allow_private and not all(is_public_address(a) for a in addresses):
        raise FetchError("that address points to a private or internal network and is blocked")


class Fetcher(Protocol):
    async def get_text(self, url: str) -> str: ...

    async def get_bytes(self, url: str, *, max_bytes: int = MAX_IMAGE_BYTES) -> tuple[bytes, str]: ...


class HttpFetcher:
    def __init__(self, *, allow_private: bool = False, transport: httpx.AsyncBaseTransport | None = None,
                 resolver: Resolver = system_resolver, max_redirects: int = 3, timeout: float = 20.0):
        self._allow_private, self._transport, self._resolver = allow_private, transport, resolver
        self._max_redirects, self._timeout = max_redirects, timeout

    async def _get(self, url: str, max_bytes: int) -> tuple[bytes, str]:
        async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout, follow_redirects=False,
                                     headers={"User-Agent": USER_AGENT}) as client:
            for _ in range(self._max_redirects + 1):
                await check_url(url, allow_private=self._allow_private, resolver=self._resolver)
                try:
                    async with client.stream("GET", url) as res:
                        if res.is_redirect:
                            url = urljoin(url, res.headers.get("location", ""))
                            continue  # the next loop checks the new address before requesting it
                        if res.status_code != 200:
                            raise FetchError(f"the server answered {res.status_code}")
                        if int(res.headers.get("content-length") or 0) > max_bytes:
                            raise FetchError("the response is too large")
                        body = bytearray()
                        async for chunk in res.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > max_bytes:
                                raise FetchError("the response is too large")
                        return bytes(body), res.headers.get("content-type", "").split(";")[0].strip().lower()
                except httpx.HTTPError as exc:
                    raise FetchError(f"could not fetch the page: {type(exc).__name__}") from None
        raise FetchError("too many redirects")

    async def get_text(self, url: str) -> str:
        body, kind = await self._get(url, MAX_PAGE_BYTES)
        if kind and not (kind.startswith("text/") or "html" in kind or "xml" in kind):
            raise FetchError(f"expected a web page but got {kind}")
        return body.decode("utf-8", errors="replace")

    async def get_bytes(self, url: str, *, max_bytes: int = MAX_IMAGE_BYTES) -> tuple[bytes, str]:
        body, kind = await self._get(url, max_bytes)
        if kind not in IMAGE_TYPES:
            raise FetchError(f"expected an image but got {kind or 'an unknown type'}")
        return body, kind
