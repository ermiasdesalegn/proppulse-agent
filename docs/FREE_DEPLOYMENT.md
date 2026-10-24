# Free deployment (no card): Supabase + Hugging Face Space + Vercel + GitHub Actions

## 1. Database: Supabase (already done)
Use the session pooler string as `DATABASE_URL`. Migrations are already applied (001-013). For a fresh database: `cd backend; python -m scripts.migrate; python -m scripts.seed`.

## 2. Backend: Hugging Face Space
1. Create the account at huggingface.co. Spaces → Create new Space → name `proppulse-backend`, SDK **Docker**, template **Blank**, hardware **CPU basic (free)**, **Private** is fine.
2. Build the upload folder: `python deploy/huggingface/build_space.py` → `dist/hf-space/` (about 90 files, 0.7 MB, no `.env`, `.venv` or key files).
3. Upload: Space → Files → Add file → Upload files → drag the **contents** of `dist/hf-space/` (not the folder itself) → Commit. (Or `git clone` the Space and copy them in.)
4. Space → Settings → **Variables and secrets**. Secrets (hidden): `DATABASE_URL`, `GEMINI_API_KEY`, `GEMINI_API_KEYS`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `ADMIN_API_KEY`, `CRON_SECRET`, `SUPABASE_SERVICE_ROLE_KEY`, `RESEND_API_KEY`, `MAPBOX_TOKEN`, `GOOGLE_SERVICE_ACCOUNT_JSON` (the file's JSON text). Variables (plain): `SUPABASE_URL`, `TELEGRAM_MODE=webhook`, `TELEGRAM_CHANNEL_ID`, `BROKER_TELEGRAM_CHAT_ID`, `GOOGLE_CALENDAR_ID`, `EMAIL_FROM`, `TIMEZONE=Africa/Addis_Ababa`, `FRONTEND_ORIGIN=https://<your-app>.vercel.app`, `PUBLIC_BASE_URL=https://<your-username>-proppulse-backend.hf.space`.
5. The Space builds (a few minutes). Check `https://<your-username>-proppulse-backend.hf.space/health` → `"status":"ok"`. In webhook mode it registers the Telegram webhook itself at start-up.

## 3. Dashboard: Vercel (free Hobby plan)
Import the GitHub repo, root directory `frontend`. Environment variables: `BACKEND_URL` = the Space URL, `ADMIN_API_KEY` (same as the Space), `DASHBOARD_PASSWORD`, `SESSION_SECRET`, `NEXT_PUBLIC_MAPBOX_TOKEN`. The browser only talks to Vercel; Vercel's server calls the Space, so CORS is not even needed (it is set anyway).

## 4. Scheduled jobs: GitHub Actions (replaces Cloud Scheduler)
Repo → Settings → Secrets and variables → Actions → add `BACKEND_URL` (the Space URL) and `CRON_SECRET`. `.github/workflows/cron.yml` then runs hourly (reminders, and it wakes the Space), 06:00 Addis (market sweep) and 08:00 Addis (follow-ups and offer ads). Run it once by hand from the Actions tab to test.

## Free-tier limits to know
- A free Space **sleeps after 48 hours without traffic** and takes about a minute to wake. The hourly job keeps it awake; Telegram's webhook also wakes it.
- The Space disk is **not persistent**: everything lives in Supabase (it does). Do not rely on local files.
- Free Spaces have 2 vCPU and 16 GB RAM, plenty for this app. JavaScript-page scraping (Chromium) is not in this image.
- GitHub Actions cron can run a few minutes late. Supabase pauses a free project after 7 days of no use; the hourly job keeps it active.
