# Portal crawler (Scrapy)

Two ingestion paths feed the same `IngestionService` and the same `properties` table:

| Path | Sites | How a page is read | Gemini use |
|---|---|---|---|
| **LLM pipeline** (existing, `app/ingest/`) | small developer sites: jenbororealestate.com, phisonrealestate.com, temerproperties.com, kavodcommercial.com | `httpx` + BeautifulSoup text, then Gemini structured output | one call per page |
| **Portal crawler** (this folder) | ethiopiapropertycentre, addislist, livingethio, guzohomes, ethiobetoch, ethiohome | Scrapy: sitemap → listing URL → local extraction | only for a page with no strict price, max 3 per batch |

The three small sites are in the built-in sweep (`DEFAULT_MARKET_SOURCES`); their developer rows are created on the first sweep (`SMALL_DEVELOPERS`, skipped when `MARKET_SOURCES` is set). Their crawl is seeded with the site's own sitemap (listing-like paths first, taxonomy sitemaps skipped), reads up to 80 pages and asks Gemini about at most 30 changed project pages per run; unchanged pages cost nothing. `jenbororealestate.com` currently answers with a "One moment, please…" bot check: the crawl stops there and says so, it does not work around it. Set the real sales emails for jenboro and temer (Projects → Add a developer) before booking visits.
Portals are stored as developers with no website and an undeliverable `.invalid` email, so the LLM sweep never visits them and no lead registration is mailed to a portal.

## How a listing is read (`app/ingest/portal.py`)
First source with a value wins: JSON-LD → JSON embedded for the page's own scripts → per-site CSS hints (`scraper/sites.py`) → labelled rows and visible text → OpenGraph.
A **strict price** comes only from those structured sources and must be a plausible birr sale price (300,000 – 20 billion). Loose page text is never used for price, because filters and adverts live there.
Rentals and land are dropped before they reach the backend. The Gemini fallback fires only when no strict price was found; `allow_llm=False` keeps a site away from the model entirely (EthioHome's robots.txt says `ai-train=no`).

## Politeness
`ROBOTSTXT_OBEY`, honest user agent, `DOWNLOAD_DELAY=4` (randomised), `CONCURRENT_REQUESTS_PER_DOMAIN=1`, AutoThrottle with target concurrency 0.5 and a 60 s ceiling, a hard time limit per run.
A 403, 429, 503 or a Cloudflare challenge **stops that site for the run**; the crawler does not try to get around it (addislist.com sits behind Cloudflare and may do this).

## Complete crawls and retiring listings
`-a limit=0` is a full crawl. After a *complete, clean* one (no cap, no date filter, no 403/429/challenge, no failed page, every requested page answered) the pipeline sends the ids it saw to `POST /api/ingest/portal/sync`, and units no longer on the portal become UNAVAILABLE. The backend refuses when fewer than half of the known units were seen. Property Centre has ~4,800 sale listings and no dates in its sitemap, so its sitemap is read newest-first: a capped daily run sees what is new, the weekly run does everything.

## Free scheduling
`.github/workflows/portal-crawl.yml` runs one job per portal on GitHub Actions: Mon–Sat the newest 300 pages and sitemap entries changed since yesterday, Sunday a full crawl. Add repository secrets `BACKEND_URL` and `ADMIN_API_KEY`. Run it by hand from the Actions tab (it defaults to a preview with a limit of 50). EthioHome is rendered with Playwright in that job.

## From the dashboard (Portals page)
The **Portals** page starts the crawl, shows every run (status, counts, the share of pages priced without the model, a link to the GitHub log) and flags a portal that is blocked, failed or never started.
Behind it the backend asks GitHub to run the workflow, so no one needs the GitHub Actions page. One-time setup on the backend (Render):
1. GitHub → Settings → Developer settings → Fine-grained tokens → new token, **only this repository**, permission **Actions: Read and write**, expiry up to a year (note the date: when it expires the page says "GitHub refused...", and you make a new one).
2. Render environment: `GITHUB_TOKEN` = the token, `GITHUB_REPO` = `ermiasdesalegn/proppulse-agent`. (`GITHUB_WORKFLOW` and `GITHUB_REF` default to `portal-crawl.yml` and `main`.)
3. Apply migration `015_portal_runs.sql` (`python -m scripts.migrate`).
The runner reports back with the same `ADMIN_API_KEY` it already uses, so there is nothing else to add.

## Alerts
When a portal's run ends, the backend tells you on Telegram (and in the dashboard feed) if: the site blocked the crawler, the crawl failed or its batches were rejected, a full crawl read no pages, or fewer than `PORTAL_MIN_SUCCESS_RATE` (default 70%) of at least `PORTAL_ALERT_MIN_PAGES` (10) pages had a price read locally, which usually means the site changed its layout. A run started by hand or from the dashboard also sends a one-line summary; a scheduled run only when something changed.

## Units from portals and visit requests
A unit read from a portal keeps the address of the original listing (`properties.source_url`). When a buyer books a visit for one, no lead-registration email is sent (there is no developer), no commission is assumed (portal developers have a 0% rate), the buyer is told the broker is arranging it, and **you** get a Telegram message with the buyer's details and the listing link; the calendar entry carries the link too. The buyer is never sent to the portal.

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
Run migrations `014_portal_ingest.sql` and `015_portal_runs.sql` first on the hosted database (`python -m scripts.migrate`).

## Adding a portal
Add a `Site` in `sites.py` (sitemap or index page, listing-URL regex). Test with `scrapy crawl portals -a site=<slug> -a limit=3 -O x.jsonl`; add CSS hints only for fields the generic readers miss.
