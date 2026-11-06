# Deploying PropPulse AI

Three pieces: **Supabase** (database), **Cloud Run** (backend), **Vercel** (dashboard). Telegram talks to the backend through a webhook, and **Cloud Scheduler** runs the three timed jobs.

```
Telegram ──webhook──▶ Cloud Run (FastAPI + LangGraph) ──▶ Supabase Postgres + pgvector
Browser ─▶ Vercel (Next.js) ──/api/proxy (adds admin key)──▶ Cloud Run
Cloud Scheduler ──X-Cron-Secret──▶ Cloud Run  (proactive 08:00, reminders hourly, scrape 06:00)
```

## Cost, honestly
- Cloud Run, Cloud Build, Secret Manager and Scheduler (3 jobs) have free tiers that a demo stays inside, **but Google requires a billing account on the project** before it will enable them. Set a budget alert (Billing > Budgets) at a few dollars.
- Supabase and Vercel hobby plans are free. A free Supabase project pauses after a week without traffic; open the dashboard before a review.
- `ALWAYS_ON=false` (default) lets Cloud Run scale to zero: the first message after a quiet spell takes a few seconds longer, and it costs nothing when idle. `ALWAYS_ON=true` keeps one instance warm (a few dollars a month).

## 1. Supabase (production project)
1. Create a project. Settings > API: copy the URL and the **service_role** key.
2. Connect > **Session pooler**: copy the connection string (port 5432). Use this, not the direct connection: Cloud Run has no IPv6.
3. From `backend/`, with `DATABASE_URL` pointing at it:  `python -m scripts.migrate` then `python -m scripts.seed`.

## 2. Backend on Cloud Run
```bash
gcloud auth login
cp deploy/env.prod.example deploy/.env.prod   # fill in; the file is gitignored
bash deploy/gcp.sh                            # secrets -> Secret Manager, build, deploy, set Telegram webhook
bash deploy/scheduler.sh                      # the three scheduler jobs
cd backend && python -m scripts.post_deploy_check https://<service-url> --admin-key <ADMIN_API_KEY> --cron-secret <CRON_SECRET>
```
Secrets never appear in the image or the service definition: Cloud Run reads them from Secret Manager at start. Logs are JSON with secrets stripped, so Cloud Logging is safe to share.

## 3. Dashboard on Vercel
Import the repository, set **Root Directory** to `frontend`, and add these environment variables:

| Variable | Value |
|---|---|
| `BACKEND_URL` | the Cloud Run URL |
| `ADMIN_API_KEY` | same as the backend's |
| `DASHBOARD_PASSWORD` | the password for the sign-in page |
| `SESSION_SECRET` | a long random string |
| `NEXT_PUBLIC_MAPBOX_TOKEN` | your public `pk.` token (optional; the map falls back to a drawn one) |
| `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase > Project Settings > API (the public key, safe for browsers). Needed for buyer accounts |
| *(none)* | who gets the console is decided by membership, not by an address: sign up, then run `python -m scripts.grant_role --email you@example.com --org proppulse --role owner` once, or start an agency at `/start` |

In Supabase > Authentication: enable the Email provider, keep **Confirm email** on, and add your Vercel address and its `/auth/callback` to the redirect URLs. On the backend, `SUPABASE_URL` is enough for current projects; only an older project that signs tokens with the shared secret also needs `SUPABASE_JWT_SECRET`.

Then put the Vercel address into `FRONTEND_ORIGIN` in `deploy/.env.prod` and run `bash deploy/gcp.sh` again.

## 4. Demo data
`DEMO_RESET_ENABLED` stays `false` on anything holding real conversations. For a demo-only environment set it to `true` to get the dashboard's Reset demo button.

## Rotating the master key

Set `SECRETS_MASTER_KEYS` to the new key followed by the old one on the host, redeploy, run `python -m scripts.rotate_master_key start`
against the production database, wait for `--status` to report every agency on the new key, then remove the old key and redeploy.
The full runbook, and why each step is safe to repeat, is in [SECURITY.md](../SECURITY.md#rotating-the-master-key).

## Updating
`bash deploy/gcp.sh` again: it builds the current commit, adds new secret versions only for values that changed, and redeploys.
