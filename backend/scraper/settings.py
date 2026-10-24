"""Scrapy settings for the portal crawler. Politeness first: one request at a time per site, a few seconds apart, slower still when
the site slows down, robots.txt obeyed. A full run is slow on purpose; it runs unattended once a day or week."""
from __future__ import annotations

import importlib.util
import os

from app.ingest.fetch import USER_AGENT

BOT_NAME = "proppulse_portals"
SPIDER_MODULES = ["scraper.spiders"]
NEWSPIDER_MODULE = "scraper.spiders"

USER_AGENT = USER_AGENT  # the same honest name the backend's own fetcher uses
ROBOTSTXT_OBEY = True
COOKIES_ENABLED = False
TELNETCONSOLE_ENABLED = False

# ---- polite crawling ----
CONCURRENT_REQUESTS = 4                 # across all sites
CONCURRENT_REQUESTS_PER_DOMAIN = 1      # never two requests to one site at once
DOWNLOAD_DELAY = float(os.environ.get("PORTAL_DOWNLOAD_DELAY", "4"))  # seconds between requests to one site
RANDOMIZE_DOWNLOAD_DELAY = True         # 0.5x to 1.5x the delay, so requests do not arrive like a metronome
AUTOTHROTTLE_ENABLED = True             # back off further when the site answers slowly
AUTOTHROTTLE_START_DELAY = DOWNLOAD_DELAY
AUTOTHROTTLE_MAX_DELAY = 60
AUTOTHROTTLE_TARGET_CONCURRENCY = 0.5   # on average less than one request in flight per site
DOWNLOAD_TIMEOUT = 30
RETRY_TIMES = 2
RETRY_HTTP_CODES = [500, 502, 504, 522, 524, 408]  # 403, 429 and 503 are not retried: the spider stops that site instead
CLOSESPIDER_TIMEOUT = int(os.environ.get("PORTAL_MAX_SECONDS", "5400"))  # a hard stop for an unattended run
HTTPCACHE_ENABLED = os.environ.get("PORTAL_HTTP_CACHE") == "1"  # while writing hints for a new site: fetch each page once
HTTPCACHE_DIR = "httpcache"
HTTPCACHE_EXPIRATION_SECS = 86_400

# ---- JavaScript pages (only requests marked {"playwright": True}; see Site.render) ----
PLAYWRIGHT_ENABLED = os.environ.get("PORTAL_PLAYWRIGHT") == "1" and importlib.util.find_spec("scrapy_playwright") is not None  # off by default: only EthioHome needs it
if PLAYWRIGHT_ENABLED:
    DOWNLOAD_HANDLERS = {"http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
                         "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler"}
    PLAYWRIGHT_BROWSER_TYPE = "chromium"
    PLAYWRIGHT_LAUNCH_OPTIONS = {"headless": True}
    PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT = 30_000
    PLAYWRIGHT_MAX_PAGES_PER_CONTEXT = 2
    PLAYWRIGHT_ABORT_REQUEST = lambda req: req.resource_type in ("image", "media", "font")  # noqa: E731  # text is all we read
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"

# ---- handing items to the backend ----
ITEM_PIPELINES = {"scraper.pipelines.BackendPipeline": 300}
PORTAL_BATCH_SIZE = 25
PORTAL_LLM_BUDGET_PER_BATCH = int(os.environ.get("PORTAL_LLM_BUDGET_PER_BATCH", "3"))
PORTAL_DRY_RUN = os.environ.get("PORTAL_DRY_RUN") == "1"

FEED_EXPORT_ENCODING = "utf-8"
LOG_LEVEL = os.environ.get("PORTAL_LOG_LEVEL", "INFO")
