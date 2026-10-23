"""Reading pages that build their listings with JavaScript, and being polite to the sites we read.

`BrowserFetcher` renders a page in headless Chromium (Playwright) and returns the HTML after the scripts have run. It is optional: if
Playwright is not installed the fetcher says so and the rest of the system carries on with plain HTTP. Everything the page asks for
while rendering goes through the same safety check as a direct fetch, so a page cannot use the browser to reach an internal address.

`Politeness` keeps the scraper a good guest: it obeys robots.txt and never hits one host more often than every few seconds.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from app.ingest.fetch import MAX_PAGE_BYTES, USER_AGENT, FetchError, HttpFetcher, Resolver, check_url, system_resolver

BLOCKED_RESOURCES = {"image", "font", "media"}  # a listing's text is all we need; skipping these makes rendering several times faster


class Politeness:
    def __init__(self, fetch_text: Callable[[str], Any], *, min_interval: float = 2.0, clock: Callable[[], float] = time.monotonic, sleep=asyncio.sleep):
        self._fetch_text, self._min_interval, self._clock, self._sleep = fetch_text, min_interval, clock, sleep
        self._robots: dict[str, RobotFileParser | None] = {}
        self._last: dict[str, float] = {}

    async def _rules(self, url: str) -> RobotFileParser | None:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            parser = RobotFileParser()
            try:
                parser.parse((await self._fetch_text(f"{origin}/robots.txt")).splitlines())
                self._robots[origin] = parser
            except Exception:
                self._robots[origin] = None  # no robots.txt, or it cannot be read: nothing forbids us
        return self._robots[origin]

    async def wait_turn(self, url: str) -> None:
        """Raises FetchError if robots.txt forbids the page; otherwise waits until this host has had a quiet moment."""
        rules = await self._rules(url)
        if rules is not None and not rules.can_fetch(USER_AGENT, url):
            raise FetchError("the site's robots.txt does not allow this page to be read automatically")
        host = urlsplit(url).netloc
        wait = self._last.get(host, float("-inf")) + self._min_interval - self._clock()
        if wait > 0:
            await self._sleep(wait)
        self._last[host] = self._clock()


class BrowserFetcher:
    """Renders pages in headless Chromium. `launcher` is injectable so the logic can be tested without a browser."""

    def __init__(self, *, http: HttpFetcher, allow_private: bool = False, resolver: Resolver = system_resolver, politeness: Politeness | None = None,
                 launcher: Callable[[], Any] | None = None, timeout_ms: int = 20_000):
        self._http, self._allow_private, self._resolver = http, allow_private, resolver
        self._politeness, self._launcher, self._timeout_ms = politeness, launcher or self._playwright_launcher, timeout_ms
        self._browser: Any = None
        self._lock = asyncio.Lock()
        self._safe_hosts: dict[str, bool] = {}

    @staticmethod
    async def _playwright_launcher():
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise FetchError("rendering JavaScript pages needs Playwright: pip install playwright && playwright install chromium") from None
        pw = await async_playwright().start()
        return await pw.chromium.launch(args=["--no-sandbox"])

    async def _browser_instance(self):
        async with self._lock:
            if self._browser is None:
                self._browser = await self._launcher()
            return self._browser

    async def _allowed(self, url: str) -> bool:
        host = urlsplit(url).hostname or ""
        if url.startswith(("data:", "blob:", "about:")):
            return True
        if host not in self._safe_hosts:
            try:
                await check_url(url, allow_private=self._allow_private, resolver=self._resolver)
                self._safe_hosts[host] = True
            except FetchError:
                self._safe_hosts[host] = False
        return self._safe_hosts[host]

    async def get_text(self, url: str) -> str:
        await check_url(url, allow_private=self._allow_private, resolver=self._resolver)
        if self._politeness:
            await self._politeness.wait_turn(url)
        browser = await self._browser_instance()
        context = await browser.new_context(user_agent=USER_AGENT, java_script_enabled=True)
        try:
            async def guard(route):  # every request the page makes, including redirects and scripts, is checked like a direct fetch
                request = route.request
                if request.resource_type in BLOCKED_RESOURCES or not await self._allowed(request.url):
                    await route.abort()
                else:
                    await route.continue_()

            await context.route("**/*", guard)
            page = await context.new_page()
            try:
                await page.goto(url, wait_until="networkidle", timeout=self._timeout_ms)
            except Exception as exc:
                raise FetchError(f"the page could not be rendered: {type(exc).__name__}") from None
            html = await page.content()
        finally:
            await context.close()
        if len(html.encode("utf-8", errors="ignore")) > MAX_PAGE_BYTES:
            raise FetchError("the rendered page is too large")
        return html

    async def get_bytes(self, url: str, *, max_bytes: int = 5_000_000) -> tuple[bytes, str]:
        return await self._http.get_bytes(url, max_bytes=max_bytes)  # images need no JavaScript

    async def aclose(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
