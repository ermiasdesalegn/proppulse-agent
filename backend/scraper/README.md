# Portal crawler (Scrapy)

Two ingestion paths feed the same `IngestionService` and the same `properties` table:

| Path | Sites | How a page is read | Gemini use |
|---|---|---|---|
| **LLM pipeline** (existing, `app/ingest/`) | small developer sites: jenbororealestate.com, phisonrealestate.com, temerproperties.com | `httpx` + BeautifulSoup text, then Gemini structured output | one call per page |
| **Portal crawler** (this folder) | ethiopiapropertycentre, addislist, livingethio, guzohomes, ethiobetoch, ethiohome | Scrapy: sitemap → listing URL → local extraction | only for a page with no strict price, max 3 per batch |

Add the three small sites as developers (Dashboard → Add a developer, with the website) or via `MARKET_SOURCES`; the daily sweep keeps handling them.
Portals are stored as developers with no website and an undeliverable `.invalid` email, so the LLM sweep never visits them and no lead registration is mailed to a portal.

## How a listing is read (`app/ingest/portal.py`)
First source with a value wins: JSON-LD → JSON embedded for the page's own scripts → per-site CSS hints (`scraper/sites.py`) → labelled rows and visible text → OpenGraph.
A **strict price** comes only from those structured sources and must be a plausible birr sale price (300,000 – 20 billion). Loose page text is never used for price, because filters and adverts live there.
Rentals and land are dropped before they reach the backend. The Gemini fallback fires only when no strict price was found; `allow_llm=False` keeps a site away from the model entirely (EthioHome's robots.txt says `ai-train=no`).

## Politeness
`ROBOTSTXT_OBEY`, honest user agent, `DOWNLOAD_DELAY=4` (randomised), `CONCURRENT_REQUESTS_PER_DOMAIN=1`, AutoThrottle with target concurrency 0.5 and a 60 s ceiling, a hard time limit per run.
A 403, 429, 503 or a Cloudflare challenge **stops that site for the run**; the crawler does not try to get around it (addislist.com sits behind Cloudflare and may do this).

## Run
```bash
pip install -r requirements-scraper.txt
cd backend
scrapy crawl portals -a site=guzohomes -a limit=20 -O out.jsonl     # try a site: writes a file, touches nothing
export PROPPULSE_BACKEND_URL=https://proppulse-agent.onrender.com PROPPULSE_ADMIN_KEY=...
PORTAL_DRY_RUN=1 scrapy crawl portals -a site=all -a limit=25       # preview what the backend would change
scrapy crawl portals -a site=all -a since=2026-10-01                # real run, only sitemap entries changed since that date
```
EthioHome renders in the browser: `playwright install chromium` and `PORTAL_PLAYWRIGHT=1`. Without it `site=all` skips EthioHome and `site=ethiohome` refuses to start.

Results: per-site counters in the Scrapy stats (`portals/<site>/local_price`, `no_local_price`, `backend_created`, `model_reads`) and the run in Studio → ingest history (source `portal`).
Run migration `014_portal_ingest.sql` first on the hosted database.

## Adding a portal
Add a `Site` in `sites.py` (sitemap or index page, listing-URL regex). Test with `scrapy crawl portals -a site=<slug> -a limit=3 -O x.jsonl`; add CSS hints only for fields the generic readers miss.
