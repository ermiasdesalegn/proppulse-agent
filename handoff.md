# PropPulse AI: handoff

Written 2026-10-24 for a fresh chat. Read this first, then `README.md`, `docs/FREE_DEPLOYMENT.md` and `docs/PropPulse_Project_Guide.docx`.
No secrets are in this file. Real values live in `backend/.env` (git-ignored) and in the Render and Vercel dashboards.

## 1. What the project is
An AI sales assistant for an independent real-estate broker in Addis Ababa (built from `PropPulse_AI_Complete_Solution_Design_SRS.docx`). Buyers chat on Telegram or the web in English or Amharic. The agent finds homes, answers worries from stored facts, books site visits and calls, protects the broker's commission with a lead registration to the developer, and follows up on its own. The broker runs everything from a live dashboard (the Command Center).

## 2. Architecture (all built)
| Part | Tech | Where |
|---|---|---|
| Database | Supabase Postgres + pgvector (HNSW), migrations 001-013 applied to the hosted DB | `backend/migrations` |
| Backend | FastAPI, Python 3.12, `python -m app` (not plain uvicorn on Windows) | `backend/app` |
| Agent | LangGraph: intake, router, discovery, relaxer, recommend, details, affordability, analyst, scheduler, human gate, hand-off, small talk. State checkpointed in Postgres | `backend/app/agent` |
| Models | Gemini via `google-genai`; model names come from `.env` (`GEMINI_MODEL`, fallbacks) | `backend/app/gemini.py` |
| Dashboard | Next.js 14 (App Router), Tailwind, password login, server-side proxy that adds the admin key | `frontend` |
| Outside services | Telegram Bot API, Google Calendar (service account), Mapbox, Resend (email) | `backend/app/services` |

Key design rules: numbers (prices, plans, mortgages) are computed in code and never by the model; a groundedness checker verifies every figure in a reply; button taps and project names are parsed in code with no model call; every outside service has a mock so tests and demos run offline.

Search is hybrid: SQL filters (budget, bedrooms, area) then cosine ranking with a 0.65 floor (`match_properties_hybrid`). "Cheapest / largest / best yield" are deterministic SQL rankings with no embedding. Zero hits triggers a bounded relaxation loop (budget +12 percent and neighbouring areas, then +24 percent and any area). Reverse RAG (`match_buyers_for_property`) finds buyers for a price drop.

## 3. Features finished in the latest stretch
- **Gemini key pool:** `GEMINI_API_KEYS` (comma list, plus `GEMINI_API_KEY`) in `backend/.env` and on Render. When every model on a key is rate-limited or out of daily quota the same request is retried on the next key (text, structured output, audio, images, embeddings); the working key stays in use and the first is retried after 6 hours. `/health` shows `gemini_keys`. All 6 keys were verified. Free-tier quota is per Google project, so keys from one project share it.
- **Projects (price on request):** the real developer sites publish projects, not unit prices. A `projects` catalogue (area, sizes, floors, status, offer, photo/video, embedding) with buyer flows: shown beside priced results ("Also worth a visit"), found by area or meaning, Ask the price (broker is told, broker sets the price, waiting buyers are messaged; drops notify askers, increases notify only the broker), Book a visit (calendar, broker notice, developer lead-registration email, dossier), live offers advertised once each. Dashboard page: Projects.
- **LLM market sweep (no CSS selectors):** `httpx` + BeautifulSoup strip a page to text, Gemini returns a structured schema, rows are validated and upserted with embeddings. **Run Market Scraper** and the daily job start a background sweep over `MARKET_SOURCES` (`slug=url;slug=url`) or the built-in Ayat, Noah and Metropolitan list plus any developer with a website. Status at `GET /api/market-sweep`. Code: `backend/app/projects.py`, `backend/app/ingest/projects.py`.
- **Channel posts:** ads and project ads are posted as photo or video (scraped image, broker upload, or generated placeholder for demo projects), with an upload fallback when Telegram cannot fetch the address.
- **Branding and UI:** logo, favicon, tab titles, Telegram bot name/description and channel photo (`python -m scripts.branding`; the bot's own picture is set in @BotFather). Neutral enterprise look with one orange primary action. Instant sidebar navigation (transition, prefetch, loading skeleton, progress bar).
- **Quality:** backend 802 tests passing (3 need a live key and are skipped), evals 28/28 scenarios and 44/44 grounded replies, ruff and mypy clean, frontend 45 unit tests, lint and types clean. Project guide docx exists but does not yet include the Render/Vercel deployment or the key pool (see section 6).

## 4. Deployment: free, no card
| Piece | Service | Status |
|---|---|---|
| Database | Supabase (eu-west-1 pooler) | live |
| Backend | **Render free web service** `https://proppulse-agent-44qv.onrender.com`, root `backend`, start `python -m app`, Frankfurt | live; `/health` shows `status ok`, `telegram webhook` |
| Website and dashboard | **Vercel** project `proppulse-agent`, root `frontend`. Public address: `https://proppulseagent.vercel.app` (public site; dashboard at `/console`). Vercel's per-branch preview addresses are behind Vercel login protection: never share them | live |
| Scheduled jobs | GitHub Actions `.github/workflows/cron.yml` (hourly reminders, 06:00 sweep, 08:00 follow-ups, Addis time) | written; secrets not added yet |
| Fallback if the hosted backend is down | run the backend locally and `ngrok http 8000`; set Vercel `BACKEND_URL` and `PUBLIC_BASE_URL` to the ngrok URL | documented in `docs/FREE_DEPLOYMENT.md` |

Hugging Face Docker Spaces was dropped (needs a paid plan); its files were removed. GCP/Cloud Run scripts remain in `deploy/` but are not used.

Render env (set): `DATABASE_URL`, `GEMINI_API_KEY`, `GEMINI_API_KEYS`, Telegram token/secret/channel, `ADMIN_API_KEY`, `CRON_SECRET`, Supabase, Resend, Mapbox, Google calendar (JSON text with braces), `TELEGRAM_MODE=webhook`, `PUBLIC_BASE_URL`, `HOST=0.0.0.0`, `TRUST_PROXY_HEADERS=true`, `PYTHON_VERSION=3.12.8`, `SCRAPE_ALLOW_PRIVATE_HOSTS=false`, `FRONTEND_ORIGIN`. Free service: 512 MB (backend uses about 130 MB), 0.1 CPU, sleeps after 15 minutes idle (first request takes 30-60 s).
Vercel env: `BACKEND_URL` (no trailing slash), `ADMIN_API_KEY` (identical to Render), `DASHBOARD_PASSWORD`, `SESSION_SECRET`, `NEXT_PUBLIC_MAPBOX_TOKEN`.

### Open issue when this was written
The dashboard on Vercel showed dashes and an error. Cause found and fixed in code: the proxy forwarded the upstream `content-length` of a body Node had already decompressed (Render answers gzip), so larger answers were cut short. Fix is commit `f41b035` (plus `bd5cbc1`, `33ab42b` for clearer errors). **These commits must be pushed** and Vercel must redeploy. If the error remains, send the Network tab status and response of the failing request.

## 5. Gotchas to remember
- Windows: start with `python -m app`; never run a production `npm run build` into `.next` while `npm run dev` is running (use `NEXT_DIST_DIR=.next-build`). `npm run demo` serves a production build.
- Telegram: only one process may receive the bot's updates. Webhook mode on Render means the local backend must be stopped (or polling-mode only locally).
- `python -m scripts.seed_projects` is now non-destructive; only "Reset demo" (needs `DEMO_RESET_ENABLED=true`) removes crawled projects. Earlier a reseed wiped the real Metropolitan projects.
- Never commit `.env`; `.tmp/` is git-ignored (the docx builder lives in `.tmp/docgen`).
- Commit messages: plain, no AI signature; never push unasked (user's memory rule).

## 6. Next steps (in order)
1. Done: pushed and deployed. Open `https://proppulseagent.vercel.app/console`, sign in, confirm Overview shows buyers, deals and live activity.
2. Vercel: Settings → Deployment Protection → disable Vercel Authentication for production so evaluators can open the link. Set Render `FRONTEND_ORIGIN` to the Vercel address. Restrict the Mapbox public token to that address.
3. GitHub: Settings → Secrets → Actions → add `BACKEND_URL=https://proppulse-agent-44qv.onrender.com` and `CRON_SECRET` (same as Render). Run the "scheduled jobs" workflow once by hand.
4. Add a free UptimeRobot monitor on `https://proppulse-agent-44qv.onrender.com/health` every 5 minutes so the service stays awake during the demo.
5. Test on the real stack: message the bot on Telegram, run Chat Simulator, Projects → Ask the price → Set price, a project visit booking (check Google Calendar and the developer email), and **Run Market Scraper** with Preview first.
6. Restore real data: Metropolitan Real Estate exists as a developer (placeholder sales email `sales@metropolitan.example`: set a real one) but its projects were deleted by mistake; re-run a crawl when metropolitanaddis.com is reachable (it timed out from this machine). Noah's site also timed out.
7. Real photos or videos for the demo projects (Projects → Add photo or video); the demo ones only have a generated placeholder.
8. Rotate every secret that was pasted in chat (Telegram token via BotFather `/revoke`, Supabase password and service key, Resend, Gemini keys, Google service account key, admin key) and update Render, Vercel and `backend/.env`.
9. Rebuild `docs/PropPulse_Project_Guide.docx` with sections for the Render/Vercel deployment, the key pool and the sweep wording (builder: `.tmp/docgen/build.js`, `node build.js`).

## 7. Not verified yet
Live post of a real photo or video to the channel; Playwright browser tests (`npm run e2e`) and Docker build were never run on this machine; GitHub CI has not run until the push; the Amharic message catalogue has not been reviewed by a native speaker; a real key-exhaustion rotation was only tested with doubles.

## 8. Addendum (2026-10-24, later): portal crawler, Portals page, docs
Built after sections 1-7 were written; where they disagree, this wins.
- **Portal crawler** (`backend/scraper`, `backend/app/ingest/portal.py`): six portals read by Scrapy on GitHub Actions (`.github/workflows/portal-crawl.yml`), local extraction first, Gemini only as a fallback. Details: `backend/scraper/README.md`, `docs/ARCHITECTURE.md` section 5-6.
- **Portals page** in the dashboard starts a crawl (the backend dispatches the workflow with `GITHUB_TOKEN` + `GITHUB_REPO`), shows run history, and the broker gets Telegram alerts (`backend/app/portal_runs.py`, migration 015). The first real run (GuzoHomes) worked.
- **Small sites** jenboro, phison, temer, kavod are in the LLM sweep; jenboro serves a bot check and is reported, not bypassed.
- **Portal units:** keep `source_url`; booking one tells the broker instead of emailing a developer; no assumed commission.
- **Fixes after the first real crawl:** price-per-m² floor (a 640,000 ETB villa was hidden by hand in Supabase), source link kept on units the model reads.
- Needed on the hosted side: migrations 014 and 015 applied; Render `GITHUB_TOKEN`, `GITHUB_REPO`; GitHub secrets `BACKEND_URL`, `ADMIN_API_KEY`.
- Counts now: backend 861 tests (3 skipped), frontend 48.
- Known gap: the daily job can message matching buyers about newly crawled portal units (it treats any unit created in the last 24 hours as a new listing).
- Docs: `README.md` rewritten, `docs/ARCHITECTURE.md` (LangGraph, RAG, ingestion), `docs/DECISIONS.md` extended.

## 9. Addendum (2026-10-31): public website, live addresses, photos
Built after section 8; where they disagree, this wins.
- **Live addresses.** Backend: `https://proppulse-agent-44qv.onrender.com` (the old `proppulse-agent.onrender.com` no longer exists). Website: `https://proppulseagent.vercel.app`; the broker's Command Center is at `/console`. Anything that still points at the old backend address (GitHub secret `BACKEND_URL`, the scraper's `PROPPULSE_BACKEND_URL`, an uptime monitor) must be updated.
- **Telegram webhook.** In webhook mode the backend registers its own address with Telegram on every start, from Render's `PUBLIC_BASE_URL`. If the bot goes quiet after an address change, fix `PUBLIC_BASE_URL` and redeploy; `python -m scripts.telegram_webhook info` shows where Telegram is sending.
- **Public site and Client Portal** (`frontend/src/app/(site)`, `frontend/src/app/portal`): landing page, Supabase sign-up for buyers, browse with filters, home pages, saved homes, map, and the Concierge web chat (the same agent as Telegram). Details: `docs/ARCHITECTURE.md` section 10, `docs/MANUAL_TESTS_PUBLIC.md`.
- **Web chat parity with Telegram:** voice notes (recorded as WAV, transcribed with the Telegram prompt), the 3D model picture and `.glb` file, the comparison PDF, and a map link to the first stop on visit day. Home pages show an interactive 3D view when the broker saved a layout. A QR code to @prop_plus_bot is on the landing page and the Concierge page.
- **Listing photos.** The portal crawler keeps each listing's cover photo (`properties.photo_url`, migration 018), checking it loads and falling back to the smaller copy a site keeps when the main one was deleted. `python -m scripts.backfill_listing_photos --recheck` fills or re-checks photos for listings already stored. Developer logos are never used as a home's picture.
- **Hero picture.** Vercel `HERO_PROPERTY_ID` names the listing whose photo leads the landing page (unset: the newest featured home).
- **Hosted setup still needed after a fresh database:** migrations 016-019; Vercel `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `BROKER_EMAIL`; Supabase Site URL and redirect URLs (`/auth/callback` for the website and for `http://localhost:3000`).
