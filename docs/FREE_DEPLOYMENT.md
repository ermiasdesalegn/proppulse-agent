# Free deployment (no card): Supabase + Render + Vercel + GitHub Actions

## 1. Database: Supabase (already done)
Use the session pooler string as `DATABASE_URL`. The hosted database is believed to be at migration 019 (check `select name from schema_migrations order by name;`); the multi-agency migrations 020-033 are **not applied yet**, and the new code needs all of them: follow [MIGRATION_HOSTED.md](MIGRATION_HOSTED.md) (expand 020-022, then switch with 023-033 and the new code). For a fresh database: `cd backend; python -m scripts.migrate; python -m scripts.seed`.

## 2. Backend: Render free web service
1. render.com → sign in with GitHub → **New + → Web Service** → pick the repo. Or **New + → Blueprint**, which reads `render.yaml` and pre-fills everything below.
2. Root directory `backend`, runtime Python 3, build `pip install -r requirements.txt`, start `python -m app`, instance type **Free**. (The start command is `python -m app`, not `uvicorn main:app`: the entry point is `app.main:app` and `python -m app` runs it on `HOST`/`PORT`; Render sets `PORT`.)
3. Environment: add the secrets and variables listed in `render.yaml` (`DATABASE_URL`, `GEMINI_API_KEY`, `GEMINI_API_KEYS`, `TELEGRAM_*`, `ADMIN_API_KEY`, `CRON_SECRET`, `SECRETS_MASTER_KEY`, `SUPABASE_*`, ...). Optional, with defaults: `JOB_WORKERS` (background job tasks inside the app, default 2), `MODEL_PRICES`, `AUDIT_EXPORT_DIR` / `AUDIT_EXPORT_BUCKET`. Set `TELEGRAM_MODE=webhook`, `PUBLIC_BASE_URL` = the `https://....onrender.com` URL Render shows, `FRONTEND_ORIGIN` = your Vercel URL.
4. Deploy, then open `<url>/health`: `"status":"ok"`. In webhook mode it registers the Telegram webhook at start-up.

## 3. Dashboard: Vercel (free Hobby plan)
Import the GitHub repo, root directory `frontend`. Environment variables: `BACKEND_URL` = the Space URL, `ADMIN_API_KEY` (same as the Space), `DASHBOARD_PASSWORD`, `SESSION_SECRET`, `NEXT_PUBLIC_MAPBOX_TOKEN`, and for buyer accounts `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` (the Supabase Auth settings are in section 3 of `docs/DEPLOYMENT.md`; keep Confirm email on). The browser only talks to Vercel; Vercel's server calls the Space, so CORS is not even needed (it is set anyway).

## 4. Scheduled jobs: GitHub Actions (replaces Cloud Scheduler)
The cron calls now only queue jobs; the workers inside the running backend do them, so the service must be awake (the workflow calls `/health` first). Repo → Settings → Secrets and variables → Actions → add `BACKEND_URL` (the Space URL) and `CRON_SECRET`. `.github/workflows/cron.yml` then runs hourly (reminders, and it wakes the Space), 06:00 Addis (market sweep) and 08:00 Addis (follow-ups and offer ads). Run it once by hand from the Actions tab to test.

## Free-tier limits to know
- Render's free web service has **512 MB RAM** (this backend uses about 130 MB), shared CPU, and **sleeps after 15 minutes without traffic**. The next request (a Telegram message, the dashboard, a cron call) wakes it in about 30-60 seconds; Telegram retries a webhook that times out.
- For an always-awake demo, add a free UptimeRobot monitor on `<url>/health` every 5 minutes (no card). The hourly GitHub job does not keep it awake on its own.
- Render free web services run about 750 hours a month, enough for one service.
- The disk is not persistent; everything important lives in Supabase.
- A long market sweep (about 6 minutes) runs in the background; keep the service awake while it runs, or it can be cut off by a restart. Chromium (JavaScript pages) is not available on the free plan's memory.
- Supabase pauses a free project after 7 days of no use; the hourly job keeps it active.
- Fallback if the hosted backend is down: run the backend locally and `ngrok http 8000`, then set `BACKEND_URL` (Vercel) and `PUBLIC_BASE_URL` to the ngrok address.
