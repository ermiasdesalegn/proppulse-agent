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
cd backend && .venv/Scripts/python -m pytest              # offline, no credentials: runs on an embedded Postgres + pgvector (backend/.pgdata)
.venv/Scripts/python -m pytest --live                      # also runs the tests that call the real Gemini API
# TEST_DATABASE_URL=postgresql://... uses an existing pgvector database instead of the embedded one (CI does this)
.venv/Scripts/python -m evals --check                      # quality guard: retrieval, conversations, invented numbers (from a recording, no network)
.venv/Scripts/python -m scripts.live_check                 # Telegram/DB
.venv/Scripts/python -m scripts.live_model_check           # Gemini (read-only)
.venv/Scripts/python -m scripts.live_services_check        # Calendar + Mapbox (books and deletes one test event)
cd ../frontend && npm test && npm run typecheck && npm run build
```

## The Command Center

| Page | What it does |
|---|---|
| Overview | Live numbers, activity and the proactive messages that went out |
| CRM & Takeover | Every buyer, the full chat, voice notes you can play, and one-click takeover. Each buyer has a panel that explains the lead score signal by signal, shows how it grew, lists the worries they raised and links to the booking dossier |
| Deal Board | Drag deals through the commission stages; each card links to its pre-tour dossier |
| Agent Brain | The live graph, a timeline of every step with the model requests and tool calls made in it (which model answered, tokens, fall-backs, search arguments and results) |
| Map & Tours | Live Mapbox map with price pins coloured by developer, clusters, and the driving route, numbered stops, distance and time of any tour. Falls back to a drawn map without a token |
| Analytics | Pipeline, commission, messages per day, lead scores, buyer worries, reply time, model usage and the evaluation results |
| Studio | Read listing pages and announcements, floor-plan upload, price simulator, channel ads |
| Chat Simulator | Talk to the agent as a buyer, with the same buttons Telegram shows |

Optional settings: `NEXT_PUBLIC_MAPBOX_TOKEN` in `frontend/.env.local` (a public `pk.` token) for the live map, and
`DEMO_RESET_ENABLED=true` in `backend/.env` to show the Reset demo button on a demo instance.

## Evaluation

The agent is measured, not just demonstrated. `backend/evals` runs three checks from a recording of the real model's answers (free, 27 seconds):

- **Retrieval** (58 labelled buyer messages in English, Amharic and mixed, 14 of them deliberately hard): hit@3 100%, MRR 0.99, precision@3 83%; requests nothing matches come back empty 5 of 5 times; the widening loop rescues 3 of 3 too-tight requests. A threshold sweep shows why the similarity cut-off is 0.64.
- **Conversations** (23 scripted scenarios through the real graph: search, widening, comparison, booking, conflicts, consultations, takeover, voice, ad links, price-drop alerts, reminders, prompt injection): 23 pass; 1 known gap is listed with the phase that fixes it.
- **Groundedness**: every reply is checked so that each price, size, percentage or year comes from the unit facts or the buyer's own words (37 of 37).

`python -m evals --check` fails if anything gets worse, and the same check runs inside `pytest`. Full tables: [backend/evals/REPORT.md](backend/evals/REPORT.md); method and limits: [backend/evals/README.md](backend/evals/README.md).

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
