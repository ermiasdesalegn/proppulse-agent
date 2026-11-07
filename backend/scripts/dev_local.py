"""A local backend for development and manual testing. Nothing outside this machine is touched.

    python -m scripts.dev_local                 reuse the local database `proppulse_dev` (created and seeded the first time)
    python -m scripts.dev_local --fresh         drop it and start again from migration 001 plus the demo catalogue
    python -m scripts.dev_local --live-model    answer chats with the real Gemini (uses the keys in backend/.env and their daily quota);
                                                without it the model is replayed from recordings, so only questions that were recorded work
    python -m scripts.dev_local --port 8123

It runs the real app on the embedded Postgres under backend/.pgdata with Telegram, email, calendar and routing all mocked, the hosted
Supabase database never contacted (DATABASE_URL is forced to the local one and the check below refuses anything else), and these logins:

    console password (operator) : dev-admin-key        cron secret : dev-cron
    buyer sign-in (Supabase)    : off here. To try sign-up and invitations use the hosted project's Authentication > Users > Add user.

Start the website against it from the repo root:   make dev-frontend     (http://localhost:3100)
"""
import argparse
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
os.chdir(BACKEND)
sys.path.insert(0, str(BACKEND))

ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
ap.add_argument("--fresh", action="store_true", help="drop the local database and rebuild it")
ap.add_argument("--live-model", action="store_true", help="use the real Gemini instead of replaying recordings")
ap.add_argument("--port", type=int, default=8123)
args = ap.parse_args()

import pgserver
import psycopg

NAME = "proppulse_dev"
server = pgserver.get_server(BACKEND / ".pgdata", cleanup_mode=None)
admin = server.get_uri()
url = admin.rsplit("/", 1)[0] + f"/{NAME}"
if not (url.startswith("postgresql://") and ("127.0.0.1" in url or "localhost" in url)) or "supabase" in url:
    sys.exit(f"refusing to start: {url!r} is not the embedded local server")

with psycopg.connect(admin, autocommit=True) as c:
    exists = c.execute("select 1 from pg_database where datname = %s", (NAME,)).fetchone()
    if args.fresh and exists:
        c.execute(f'drop database "{NAME}" with (force)')
        exists = None
    if not exists:
        c.execute(f'create database "{NAME}"')
with psycopg.connect(url, autocommit=True) as c:
    c.execute("create extension if not exists vector")

from scripts.migrate import migrate
from scripts.seed import main as seed
from tests.localdb import EMBEDDINGS

migrate(url)
seed(url=url, embeddings_file=str(EMBEDDINGS))

os.environ.update({
    "DATABASE_URL": url, "GEMINI_MODE": "live" if args.live_model else "replay",
    "TELEGRAM_MODE": "off", "TELEGRAM_BACKEND": "mock", "CALENDAR_BACKEND": "mock", "ROUTING_BACKEND": "mock", "EMAIL_BACKEND": "mock",
    "ADMIN_API_KEY": "dev-admin-key", "CRON_SECRET": "dev-cron", "INGEST_WEBHOOK_SECRET": "dev-ingest",
    "SUPABASE_URL": "", "SUPABASE_SERVICE_ROLE_KEY": "", "SUPABASE_JWT_SECRET": "dev-jwt-secret-that-is-long-enough-for-hs256",
    "SECRETS_MASTER_KEY": "A" * 43, "SECRETS_MASTER_KEYS": "", "GITHUB_TOKEN": "", "GITHUB_REPO": "", "PUBLIC_BASE_URL": "",
    "FRONTEND_ORIGIN": "http://localhost:3100", "TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHANNEL_ID": "", "TELEGRAM_WEBHOOK_SECRET": "",
    "GOOGLE_SERVICE_ACCOUNT_JSON": "", "RESEND_API_KEY": "", "EMAIL_FROM": "", "BROKER_TELEGRAM_CHAT_ID": "",
    "DEMO_FALLBACK_TELEGRAM_CHAT_ID": "", "DEMO_FALLBACK_EMAIL": "", "JOB_WORKERS": "2", "AUDIT_EXPORT_DIR": "", "AUDIT_EXPORT_BUCKET": "",
})
if not args.live_model:
    os.environ.update({"GEMINI_API_KEY": "", "GEMINI_API_KEYS": ""})

print(f"\nlocal backend on http://127.0.0.1:{args.port}  database {NAME}  model {'LIVE Gemini' if args.live_model else 'replayed'}  (Ctrl+C stops it)\n")

import uvicorn

import app  # noqa: F401  (picks the Windows-compatible event loop)

uvicorn.run("app.main:app", host="127.0.0.1", port=args.port, loop="none", log_level="info")
