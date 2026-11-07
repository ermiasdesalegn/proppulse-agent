# Working locally (fast, nothing live is touched)

The hosted system has limits that slow testing down: Supabase sends 2 confirmation emails an hour, Gemini's free tier is shared, and Render sleeps. A local run has none of them.

```bash
make dev-backend                      # backend on :8123, database `proppulse_dev` on the embedded Postgres, everything mocked
make dev-backend ARGS=--fresh         # throw the database away and rebuild it (migrations 001 to the newest, plus the demo catalogue)
make dev-backend ARGS=--live-model    # answer chats with the real Gemini (uses backend/.env keys and their daily quota)
make dev-frontend                     # the website on http://localhost:3100 against it
```

Without `make`: `cd backend; .venv/Scripts/python.exe -m scripts.dev_local [--fresh] [--live-model] [--port N]`.

What is real and what is not:

| Thing | Locally |
|---|---|
| Database | the embedded Postgres in `backend/.pgdata`; `scripts.dev_local` refuses any other URL, so `backend/.env`'s hosted `DATABASE_URL` is never used |
| Telegram, email, calendar, routing | mocked: nothing is sent to anyone |
| Gemini | replayed from recordings by default (only questions that were recorded get answers); `--live-model` uses the real one |
| Console login | the operator password `dev-admin-key` (frontend: `dev-pass` on the "Operator password" form) |
| Buyer and broker sign-up | off (no Supabase). To try sign-up, invitations and `/start`, use the hosted project: Authentication > Users > Add user, tick Auto Confirm User (no email is sent) |

Other things worth running:

- `make test` : the whole offline suite (six `test_tenancy_*` files prove isolation between agencies; about 4 minutes).
- `python -m scripts.rehearse_hosted_migration` : the migration rehearsal on a scratch database (about a minute).
- The console's **Chat Simulator** tries the agent without Telegram.

Gotcha: after the machine restarts, the embedded Postgres can take a minute to recover, and the first start may fail with a timeout. Wait a minute and run it again.
