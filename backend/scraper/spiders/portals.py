"""One spider for every portal in scraper/sites.py.

    scrapy crawl portals -a site=guzohomes -a limit=50          # one site, at most 50 listing pages
    scrapy crawl portals -a site=all -a since=2026-10-01        # every site, only listings changed since that date (sitemap lastmod)

Listing addresses come from the site's sitemap when it has one (no index pages to walk), otherwise from its paginated index. Each listing
page is read locally (`app.ingest.portal.extract_listing`); only when no strict price is found is the page text attached to the item, so
the backend can ask the language model about that one page. A bot challenge, a 403 or a 429 stops that site for the rest of the run:
the crawler never tries to get around a site that is pushing back.
"""
from __future__ import annotations

import gzip
import os
import re
from collections.abc import AsyncIterator, Iterator
from datetime import date
from typing import Any

import scrapy
from scrapy.http import Response
from scrapy.utils.sitemap import Sitemap

from app.ingest.parse import page_text
from app.ingest.portal import extract_listing, is_challenge_page, skip_reason
from scraper.sites import SITES, Site

BACKOFF_STATUSES = [403, 429, 503]


class PortalSpider(scrapy.Spider):
    name = "portals"
    custom_settings = {"HTTPERROR_ALLOWED_CODES": BACKOFF_STATUSES}

    def __init__(self, site: str = "all", limit: str = "150", since: str | None = None, max_index_pages: str = "20", **kwargs: Any):
        super().__init__(**kwargs)
        chosen = list(SITES) if site == "all" else [s.strip() for s in site.split(",")]
        unknown = [s for s in chosen if s not in SITES]
        if unknown:
            raise ValueError(f"unknown site(s) {unknown}; known: {', '.join(SITES)}")
        self.sites = [SITES[s] for s in chosen]
        if site == "all" and not self._playwright_on():
            self.sites = [s for s in self.sites if not s.render]  # the rest still run; EthioHome waits for PORTAL_PLAYWRIGHT=1
        elif any(s.render for s in self.sites) and not self._playwright_on():
            raise ValueError("this site builds its pages with JavaScript: set PORTAL_PLAYWRIGHT=1 and run `playwright install chromium`")
        self.limit, self.max_index_pages = int(limit), int(max_index_pages)
        self.since = date.fromisoformat(since) if since else None
        self.taken: dict[str, int] = {s.slug: 0 for s in self.sites}
        self.index_pages: dict[str, int] = {s.slug: 0 for s in self.sites}
        self.stopped: dict[str, str] = {}
        self.seen_urls: set[str] = set()

    @staticmethod
    def _playwright_on() -> bool:
        return os.environ.get("PORTAL_PLAYWRIGHT") == "1"

    # ---------------- where the listing addresses come from ----------------

    async def start(self) -> AsyncIterator[scrapy.Request]:
        for request in self.start_requests():
            yield request

    def start_requests(self) -> Iterator[scrapy.Request]:
        for site in self.sites:
            for url in site.sitemaps:
                yield scrapy.Request(url, callback=self.parse_sitemap, cb_kwargs={"site": site}, dont_filter=True)
            for url in site.index_pages:
                yield self._page(url, site, self.parse_index)

    def parse_sitemap(self, response: Response, site: Site) -> Iterator[scrapy.Request]:
        if self._pushed_back(response, site):
            return
        body = response.body
        if body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
        if response.url.endswith(".txt") or b"<" not in body[:200]:  # a plain-text sitemap: one address per line
            entries = [{"loc": line.strip()} for line in body.decode("utf-8", "replace").splitlines() if line.strip().startswith("http")]
            kind = "urlset"
        else:
            sitemap = Sitemap(body)
            entries, kind = list(sitemap), sitemap.type
        if kind == "sitemapindex":
            for entry in entries:
                if site.sitemap_follow is None or site.sitemap_follow.search(entry["loc"]):
                    yield scrapy.Request(entry["loc"], callback=self.parse_sitemap, cb_kwargs={"site": site})
            return
        for entry in entries:
            if not self._changed_since(entry.get("lastmod")):
                continue
            if request := self._listing_request(entry["loc"], site):
                yield request

    def parse_index(self, response: Response, site: Site) -> Iterator[scrapy.Request]:
        if self._pushed_back(response, site):
            return
        self.index_pages[site.slug] += 1
        for href in response.css("a::attr(href)").getall():
            url = response.urljoin(href)
            if request := self._listing_request(url, site):
                yield request
            elif site.next_page and site.next_page.search(url) and self.index_pages[site.slug] < self.max_index_pages:
                yield self._page(url, site, self.parse_index)

    # ---------------- one listing ----------------

    def parse_listing(self, response: Response, site: Site) -> Iterator[dict]:
        if self._pushed_back(response, site):
            return
        listing = extract_listing(response.text, response.url, site.hints)
        if skip_reason(listing):  # a rental or land: not worth a byte of the backend's time
            self.crawler.stats.inc_value(f"portals/{site.slug}/not_for_sale")
            return
        item = {
            "portal": site.slug, "portal_name": site.name, "url": response.url, "ref": listing.ref, "title": listing.title,
            "price_etb": listing.price_etb, "sqm": listing.sqm, "bedrooms": listing.bedrooms, "sub_city": listing.sub_city,
            "property_type": listing.property_type, "deal": listing.deal, "description": (listing.description or "")[:4000] or None,
            "lat": listing.lat, "lng": listing.lng, "found_by": listing.found_by, "text": None,
        }
        if listing.price_etb is None:
            self.crawler.stats.inc_value(f"portals/{site.slug}/no_local_price")
            if site.allow_llm:
                item["text"] = page_text(response.text, 12_000)
        else:
            self.crawler.stats.inc_value(f"portals/{site.slug}/local_price")
        yield item

    # ---------------- helpers ----------------

    def _listing_request(self, url: str, site: Site) -> scrapy.Request | None:
        url = url.split("#")[0]
        if not site.listing.search(url) or url in self.seen_urls or site.slug in self.stopped:
            return None
        if self.taken[site.slug] >= self.limit:
            return None
        self.seen_urls.add(url)
        self.taken[site.slug] += 1
        return self._page(url, site, self.parse_listing)

    def _page(self, url: str, site: Site, callback: Any) -> scrapy.Request:
        meta: dict[str, Any] = {}
        if site.render:
            from scrapy_playwright.page import PageMethod

            meta = {"playwright": True, "playwright_page_methods": [PageMethod("wait_for_load_state", "networkidle")]}
        return scrapy.Request(url, callback=callback, cb_kwargs={"site": site}, meta=meta)

    def _changed_since(self, lastmod: str | None) -> bool:
        if self.since is None or not lastmod:
            return True
        try:
            return date.fromisoformat(lastmod[:10]) >= self.since
        except ValueError:
            return True

    def _pushed_back(self, response: Response, site: Site) -> bool:
        """True (and the site is stopped for this run) when the site answers with a challenge, a refusal or 'slow down'."""
        if site.slug in self.stopped:
            return True
        reason = None
        if response.status in BACKOFF_STATUSES:
            reason = f"the site answered {response.status}"
        elif re.search(rb"<html", response.body[:2000], re.I) and is_challenge_page(response.text):
            reason = "the site answered with a bot challenge"
        if reason is None:
            return False
        self.stopped[site.slug] = reason
        self.logger.warning("stopping %s for this run: %s (%s)", site.slug, reason, response.url)
        self.crawler.stats.set_value(f"portals/{site.slug}/stopped", reason)
        return True
