# Phase 15: manual checks

The automated side (lint, types, 750+ backend tests, frontend tests, log redaction, rate limits, row-level security, the evaluation guard)
runs on every push in GitHub Actions. These need Docker or a real browser engine, which were not available where this was built.

## 1. The whole stack in Docker

```bash
cp .env.docker.example .env.docker      # put a free Gemini key in GEMINI_API_KEY
docker compose up --build
```

Expected: `db` becomes healthy, `setup` prints the migrations and "inserted 15 properties" and exits 0, then the backend and the dashboard start.
Open http://localhost:3000, sign in with `change-me` (or the DASHBOARD_PASSWORD you exported), open Chat Simulator and say
"I have 15 million birr, looking for a 2 bedroom in Bole". Expected: options appear; Analytics and Agent Brain fill in.
Open http://localhost:8000/health: `"status":"ok"`.

Also check: `docker compose logs backend` shows JSON lines with a `request_id` and no tokens or passwords.

## 2. Browser (end-to-end) tests

```bash
cd frontend
npx playwright install chromium         # once, about 150 MB
# with the backend (python -m app) and the dashboard (npm run dev) already running:
DASHBOARD_PASSWORD=<your dashboard password> npm run e2e
```

Expected: 7 tests pass (closed when signed out, wrong and right password, every section opens, Studio sample preview, map units, analytics, chat survives).
If the chat test fails because Gemini is slow, rerun it; it only needs *some* agent reply within 80 seconds.

## 3. CI on GitHub

Push to GitHub and open the Actions tab. Expected: jobs `backend`, `frontend`, `e2e` and `docker` all green. If `e2e` fails on the first run, open its
uploaded report (Playwright HTML) and the printed log tails; send them to me.

## 4. Secrets in logs (spot check)

Run the backend with `LOG_FORMAT=json`, use the dashboard for a minute (chat, studio), then search the log output for your bot token, Gemini key
and database password. Expected: none appear (only `[redacted]`).
