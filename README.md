# PropPulse AI

An AI agent for a single independent real-estate broker in Addis Ababa. Buyers chat on Telegram (or the web simulator); the agent finds matching units, books site visits, and the broker watches and steers everything from a dashboard.

## What it does
- **Chat agent** (LangGraph + Gemini): extracts preferences, runs hybrid + reverse search over Supabase pgvector, relaxes filters when nothing fits, recommends numbered options, compares units, books tours.
- **Tour booking**: Google Calendar availability, Mapbox route ordering, lead emails to developers, commission ledger.
- **Proactive jobs**: price-drop alerts, tour reminders, milestone reminders (idempotent, cron-triggered).
- **Market ingestion**: reads developer listing pages (CSS selectors) or pasted announcements; only new units cost a model call.
- **Channel ads**: composes and posts listings to a Telegram channel with Ask / Compare / Book buttons that deep-link back to the bot.
- **Command Center** (Next.js): CRM, live chat with takeover, board, map, studio (ingest, ads, price simulator).

Anything with no credentials runs on a mock, so the whole system works offline.

## Layout
| Path | Contents |
|---|---|
| `backend/app` | FastAPI app, agent graph, services, ingestion, ads, proactive jobs |
| `backend/migrations` | SQL schema (001–005) |
| `backend/scripts` | `migrate`, `seed`, and live checks |
| `backend/tests` | pytest suite (452 tests) |
| `frontend` | Next.js 14 dashboard |

## Setup
```bash
cd backend
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
cp .env.example .env          # fill in keys
.venv/Scripts/python -m scripts.migrate
.venv/Scripts/python -m scripts.seed
.venv/Scripts/python -m app   # http://localhost:8000
```
Start the server with `python -m app`, not `uvicorn` directly: on Windows the database driver needs the selector event loop that launcher sets.

```bash
cd frontend
cp .env.example .env.local    # backend URL, admin key, dashboard password
npm install && npm run dev    # http://localhost:3000
```

## Checks
```bash
cd backend && .venv/Scripts/python -m pytest              # offline, no credentials
.venv/Scripts/python -m scripts.live_check                 # Telegram/DB
.venv/Scripts/python -m scripts.live_model_check           # Gemini (read-only)
.venv/Scripts/python -m scripts.live_services_check        # Calendar + Mapbox (books and deletes one test event)
cd ../frontend && npm test && npm run typecheck && npm run build
```

## Going live
- **Google Calendar**: enable the Calendar API, create a service account, download its JSON key, share the calendar with the account's email with "Make changes to events". Set `GOOGLE_SERVICE_ACCOUNT_JSON` and `GOOGLE_CALENDAR_ID`.
- **Mapbox**: set `MAPBOX_TOKEN` (optionally `OFFICE_LAT` / `OFFICE_LNG`).
- Both switch on automatically once set; force with `CALENDAR_BACKEND` / `ROUTING_BACKEND` (`mock`, `google`/`mapbox`, `auto`).
- Never commit `.env` or the service-account file (both are git-ignored).

## Demo script (about 5 minutes)
1. **Dashboard → Chat**: as a new buyer say "I have 15 million birr, looking for a 2BR in Bole or CMC." Get numbered options.
2. Reply "compare 1 and 2", then "book option 1 tomorrow afternoon". Show the event in Google Calendar and the lead email.
3. **Studio → price simulator**: cut a unit's price. The matching buyer gets a Telegram price-drop alert.
4. **Studio → Read a listing page**: load the sample "a week later" page, preview the diff (new, price change, removed), apply.
5. **Studio → Ads**: preview, confirm, post. Tap *Ask AI About This Unit* in the channel to land back in the bot.
6. **Chat → Take over**: pause the agent for a buyer, reply as the broker, then hand back.

## Notes
- Recommendation numbering is rendered in code, so "option 2" always means the stored match.
- The scraper refuses private addresses, caps response sizes, and will not wipe the catalogue after a suspiciously small scrape.
- Real channel posts need an explicit second step; previews are the default.
