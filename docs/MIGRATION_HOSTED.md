# Plan: moving the hosted database and live app to multi-tenancy

Status: **rehearsed on a local copy through migration 033 (section 8), not yet carried out.** Nothing here has been run against the hosted Supabase database. The local and test databases already run migrations 020 to 033; the hosted one does not. This page says what changes, in what order, how to check each step, what could break after going live, and how to go back.

Everything below is done from a branch that is merged on purpose. Until then `main` and the hosted system stay exactly as they are.

## 1. What is different about this migration

| Migration | What it does | Safe while the old code still runs? |
|---|---|---|
| 020 | Creates `organizations`, `memberships`, `invitations`, `org_settings`; seeds agency #1 (`proppulse`, plan **business**) | Yes, only adds tables |
| 021 | Adds `org_id` to 15 tables, fills it with agency #1, default = agency #1, indexes | Yes: old inserts omit `org_id` and get the default. **Rewrites every row of 15 tables** (locks each table while it runs): time it on real data (section 8) |
| 022 | Creates role `app_user`, turns on row-level security (RLS) with policies | Yes, **if the old code connects as the table owner** (Supabase's `postgres`): owners are not forced through RLS. Needs `CREATEROLE` (section 6) |
| 023 | Replaces global unique keys with per-agency ones; composite foreign keys | **No.** The live code runs `on conflict` on `thread_id`, `dedupe_key`, `message_id`, `property_id` (layouts) and `(property_id, kind, price_etb)` (channel posts). After 023 those constraints no longer exist and the insert fails with "no unique or exclusion constraint matching the ON CONFLICT specification". Buyers could not start a chat |
| 024 | `org_keys`, `org_secrets` (encrypted credentials), more `org_settings` columns | Yes, only adds. The new code needs `SECRETS_MASTER_KEY` to use it |
| 025 | Takes write rights on the shared market tables away from `app_user` | Yes: the old code never uses `app_user`. The new code needs it, which is why it goes with the switch |
| 026 | `audit_log` (append-only; no updates) | Yes, only adds |
| 027 | `jobs`, `org_last_served` (the fair job queue), platform-only: no grant to `app_user` | Yes, only adds. Nothing runs the queue until the **new** code does (workers live inside the app, `JOB_WORKERS`, default 2) |
| 028 | `agency_prices`, views `agency_properties` / `agency_projects`, `effective_price`, and **replaces** `match_properties_hybrid` and `match_buyers_for_property` so they read the views | Yes (rehearsed). The functions keep their signature and columns; without a tenant label `current_org()` is null, so the old code (no label) gets the shared price, exactly as before |
| 029 | `agency_price_history` and a trigger on `agency_prices` | Yes, only adds; the trigger fires only for agency price rows, which the old code never writes |
| 030 | Adds `seq`, `prev_hash`, `hash` to `audit_log`, **back-fills every existing entry**, a `before insert` trigger that chains new entries under a per-agency advisory lock, `audit_export_state` | Needs 026. Harmless to the old code (it never writes `audit_log`), but it changes the table the new code writes, so it goes with the switch. Back-fill is row by row: time it if `audit_log` is large (on a database that goes 019 to 033 in one go it is empty) |
| 031 | Two indexes: `model_calls (org_id, created_at desc)`, `jobs (org_id, created_at desc)` | Yes. The `model_calls` index is built with a normal (locking) `create index`; it is brief on a table this size, but note it |
| 032 | `org_settings.listed boolean not null default false` | Yes (metadata-only add column with a constant default) |
| 033 | Replaces the 029 trigger function so deleting an agency that has its own prices works | Yes. Must be applied if 029 is: without it, deleting an agency with unit prices fails |
| 034 | `agency_developer_terms` and `effective_commission_rate()` (an agency's own commission rate with a developer). Added after go-live | Yes, only adds. **Apply it before deploying the code that reads it** (the property queries call the function), never after |
| 035 | Commission claims: claim columns and `pending_since` on `commission_ledger`, a trigger that stamps `pending_since`, the `commission_claim_events` table (row-level security), and the `COMMISSION_OVERDUE` notice kind. Added after go-live | Yes, only adds (the notice-kind check is widened). **Apply it before deploying the code that reads it** (the deal board query reads the new columns) |
| 036 | Deal assignment and split: `assigned_to` on `buyers_crm` and `commission_ledger`, the `commission_splits` table (row-level security) and a deferred check that a deal's split adds up to 100. Added after go-live | Yes, only adds. **Apply it before deploying the code that reads it** (creating a deal and the board read the new columns) |
| 037 | `deal_documents` (the index of files attached to a deal; row-level security). The files are in a private storage bucket, created separately (see the plan, phase 1 item 2). Added after go-live | Yes, only adds. **Apply it before deploying the code that reads it.** Create the private bucket and set `DEAL_DOCUMENTS_BUCKET` before anyone attaches a file; until then the console says storage is not set up |

So the migration is **expand, then switch**: 020 to 022 go first, with the old code running. 023 and the new code must change together, in one short window; 024 to 033 go with them. The rehearsal also applied 027 to 029 and 031 to 033 on top of Step A with the old code probes running and nothing broke, so they are additive, but there is no benefit in applying them early, and `scripts.migrate` is simpler when Step B is one command.

What each migration needs from the environment (the new code, after the switch):

| Migration / feature | Setting | If it is missing |
|---|---|---|
| 024 (stored bot tokens, calendar keys) | `SECRETS_MASTER_KEY` (`python -m scripts.make_master_key`); keep two copies outside the repo and database | Connecting a bot or calendar from the console fails; the first agency keeps working from `TELEGRAM_BOT_TOKEN` etc. Losing or changing the key makes every stored secret unreadable (use `SECRETS_MASTER_KEYS` for a rotation) |
| 027 (queue) | `JOB_WORKERS` (default 2; 0 turns workers off), `JOB_PER_ORG_LIMIT`, `JOB_LEASE_SECONDS`. Workers run **inside the web process** | With 0 workers, or a sleeping Render service, cron calls queue jobs and nothing runs them. `/health` wakes the service, and the workers then pick up what is waiting |
| quotas (code, with 027/031) | `MODEL_PRICES`, `MODEL_PRICES_AS_OF` (defaults are estimates), `ENFORCE_QUOTAS` (default true) | Spend is only as accurate as the price line; check against the provider's list |
| 030 (audit export) | `AUDIT_EXPORT_DIR` (dev) or `AUDIT_EXPORT_BUCKET` (+ `_PREFIX`, `_ENDPOINT_URL`) with an object-lock bucket, and `boto3` installed on the host (**not** in `requirements.txt`) | The chain still proves an edit or a middle removal, but not that the newest entries were cut off. No sink configured means the export job does nothing |
| webhooks, invitations | `PUBLIC_BASE_URL` (https; webhook re-registered at start from it), `FRONTEND_ORIGIN` (CORS and the first origin is the base of invitation links) | Telegram updates stop arriving / invitation links point at localhost |
| email (invitations) | `RESEND_API_KEY`, `EMAIL_FROM` | The link is returned in the API response instead of being emailed |
| public site (032) | Vercel: `BACKEND_URL`, `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `ADMIN_API_KEY`, `SESSION_SECRET`, `DASHBOARD_PASSWORD` | `/<slug>` pages cannot check the agency (the middleware asks the backend), buyer sign-in fails |

Related facts to keep in mind:

- The hosted database is believed to be at 019 (the project notes say so; `docs/FREE_DEPLOYMENT.md` said 013). **Check, do not assume**: `select name from schema_migrations order by name;`. If it is at 013, 014 to 019 are applied first by the same migrator (they are older and additive, the rehearsal applies them from scratch) and the old-code check in Step A must be done with the code that matches the database.
- `scripts/migrate.py` runs each file in its own transaction and records it in `schema_migrations`, so a failed file leaves nothing half-applied and a re-run continues from there.
- Use the **direct or session-pooler** connection string for migrations, not the transaction pooler.

## 2. Before touching anything

1. **Back up.** Supabase's free plan has no point-in-time recovery. Take a full dump and keep it off the machine that runs the migration:

   ```bash
   pg_dump "$HOSTED_DIRECT_URL" --format=custom --no-owner --file=proppulse-before-020.dump
   ```

2. **Rehearse on a copy.** Two ways, do both if time allows:

   - `python -m scripts.rehearse_hosted_migration` (from `backend/`) builds a scratch database at 019 with synthetic data and runs the whole sequence (section 8). It can be re-run any time and takes under a minute (`--buyers`, `--msgs`, `--audit-rows` scale it).
   - Restore the real dump into the embedded Postgres (a scratch database, never `proppulse_test`) and run the real migrator on it. This is the only way to learn the true time of 021 on the real data:

     ```bash
     createdb proppulse_rehearsal   # on the embedded server (its port is in backend/.pgdata/postmaster.pid)
     pg_restore --no-owner -d proppulse_rehearsal proppulse-before-020.dump
     DATABASE_URL=postgresql://postgres:@127.0.0.1:<port>/proppulse_rehearsal python -m scripts.migrate
     ```

   Then run the application's smoke test against the rehearsal database (`PORT=8123 python -m app`, every channel forced to mock).

3. **Check what Supabase allows.** Migration 022 runs `create role app_user` and `grant app_user to <current user>`. Confirm on the real project with a read-only query (`select rolcreaterole from pg_roles where rolname = current_user;`). If it does not allow it, 022 fails cleanly (its own transaction) and nothing else is lost. The embedded server runs as a superuser, so the rehearsal cannot prove this.

4. **Generate secrets** and keep a copy outside the repository and the database:

   ```bash
   python -m scripts.make_master_key
   ```

   Put the result in Render as `SECRETS_MASTER_KEY`.

5. **Check the plan of agency #1** after 020: it is seeded as `business` (10,000 messages a day, $250 a month of model spend, 30,000 jobs a day). Every other agency starts on `free` (100, $2, 300). If the hosted row already existed with another plan, `on conflict do nothing` leaves it alone: `select slug, plan from organizations;`.

6. **Pick a quiet time.** The window is a few minutes. The Telegram bot is the only thing buyers see during it.

## 3. The steps

### Step A, expand (old code still running, no outage)

```bash
cd backend
python -m scripts.migrate   # with only 020, 021, 022 present, see note
```

The migrator applies every file it finds, so to stop after 022 run the three files from a temporary copy of `migrations/` (as the rehearsal script does: `m.MIGRATIONS = <temp dir>`), or `psql -f` each and insert its name into `schema_migrations`. Do **not** let it reach 023 yet.

Verify (all of these should hold, and the live bot should keep answering):

```sql
select count(*) from organizations;                              -- 1, slug 'proppulse'
select count(*) from buyers_crm where org_id is null;            -- 0
select relrowsecurity from pg_class where relname = 'buyers_crm'; -- true
select pg_has_role(current_user, 'app_user', 'member');           -- true
```

Then send the live bot a message and open the Command Center. If either breaks, go to section 5.

### Step B, switch (short window)

Order matters because the new code requires 023 to 033, and the old code cannot survive 023.

1. Set on Render: `SECRETS_MASTER_KEY`, `PUBLIC_BASE_URL`, `FRONTEND_ORIGIN` (check both), optionally `AUDIT_EXPORT_*` and `MODEL_PRICES`. Leave `ADMIN_API_KEY` as it is (it is now the platform secret) and keep `CRON_SECRET`. Remove `BROKER_EMAIL` from Vercel; it no longer does anything. Set the Vercel variables listed in section 1.
2. Run the remaining migrations (023 to 033) with `python -m scripts.migrate`.
3. Deploy the new backend to Render and the new frontend to Vercel straight away. Render's cold start is the longest part. In the gap, the bot's updates fail; Telegram retries them, so buyers' messages are delayed, not lost.
4. Make the first owner (membership replaces the old email check; `BROKER_EMAIL` is no longer read):

   ```bash
   python -m scripts.grant_role --email <your Supabase account email> --org proppulse --role owner
   ```

5. Webhook: the original route `/api/telegram/webhook` is kept for the first agency and is re-registered by the app on start from `PUBLIC_BASE_URL`; check with `python -m scripts.telegram_webhook info`. New agencies connect their own bot and get `/api/telegram/webhook/<slug>`.
6. Encrypted channels: the first agency still uses the environment's `TELEGRAM_BOT_TOKEN` and calendar settings; nothing has to be re-entered. Moving them into `org_secrets` is optional and can happen later from the console.
7. Run one cron job by hand and check the queue drains (go-live list, section 6).

### Step C, verify

- `python -m scripts.post_deploy_check` against the live backend.
- A real message to the bot, a booking, and a page load in the Command Center as the owner.
- The cron jobs (GitHub Actions) run once by hand; the console's Jobs list shows them queued then done, for one agency.
- `DATABASE_URL=<hosted> python -m scripts.verify_audit` (read-only; run it from a shell where the hosted URL is deliberately set) prints "audit trails intact".
- A second account created at `/start` sees an empty agency and none of agency #1's buyers. This is the isolation check on the live system, using a throw-away agency you delete afterwards (deleting works because of 033, and also removes its prices, jobs and audit rows).
- The Supabase Auth settings are unchanged: "Confirm email" stays on, because invitation links rely on the address being the owner's own.

## 4. What this plan does not cover

- **Zero-downtime for 023.** The window is minutes, accepted for a demo-scale deployment. A longer-lived system would first deploy code that tolerates both keys (add the per-agency keys, change `on conflict` targets, then drop the old ones), which is three releases instead of one. Not worth it here.
- **Supabase's data API.** The tables are in `public` and RLS is on everywhere, with no policy for `anon` or `authenticated`, so the public API sees nothing. That was already true; the new tables (including `jobs` and `audit_export_state`, which have RLS and no policy at all) follow the same rule. Re-check in the dashboard after Step B.
- **Connection pooler behaviour.** The tenant label is set with `set_config(..., true)` inside each transaction, which is safe through a transaction-mode pooler, but this has only been exercised against the embedded Postgres. Step C's second-account check is the real test.
- **pgvector version.** The benchmark ([BENCHMARKS.md](BENCHMARKS.md)) used 0.6.2. Check `select extversion from pg_extension where extname = 'vector';` on Supabase; the views in 028 and the `match_*` functions use the `<=>` operator through the `vector` type, which 022 makes reachable via the `extensions` schema grant.

## 5. Going back

| Where it stopped | How to go back |
|---|---|
| During Step A, a migration failed | Nothing to undo: each file is its own transaction. Fix and re-run, or stop; the old code is still compatible with whatever was applied |
| After Step A, something looks wrong | Old code is still running and compatible. Restore from the dump if the data is damaged; otherwise leave 020 to 022 in place (they are additive) |
| During or after Step B | There is no down-migration for 023. Restore the dump (`pg_restore --clean`), redeploy the previous Render and Vercel releases, and re-register the webhook. Any chats that happened after the dump are lost, which is why the window should be quiet and short |
| Only a later file failed (027 to 033) | Its own transaction rolled back, so the database is at the previous file. Fix and re-run; do not restore. The new code needs all of them, so keep the old release until they are all in |
| After go-live, the new code must be rolled back but the data kept | Not supported once 023 is in: the old code cannot write. Roll forward (fix the new code) instead; restoring the dump is the only way to run the old release again, and it loses what happened after the dump |
| After go-live, a trigger misbehaves | 029/033 (`agency_prices_history`): `alter table agency_prices disable trigger agency_prices_history;` stops history rows without losing prices (alerts for agency price drops stop). 030 (`audit_log_chain`): do **not** disable it: the new code's inserts would then fail the `not null` hash columns. Fix the function with a new migration instead. Both are metadata changes with no data loss |

Restoring the dump is the only supported way back from 023, so the rehearsal in section 2 is not optional.

## 6. Go-live checklist: what could break after the switch

**Access and identity**
- [ ] The first owner exists: `python -m scripts.grant_role --email <account> --org proppulse --role owner`. `BROKER_EMAIL` is not read any more; without a membership nobody gets into the Command Center.
- [ ] `select rolcreaterole from pg_roles where rolname = current_user;` is true on Supabase before running 022.
- [ ] The tenant label survives the pooler. Prove it with the second account (Step C), not by assumption. If `DATABASE_URL` is the transaction pooler, `set_config(..., true)` is still inside one transaction, but use the session pooler for the app anyway, as `docs/FREE_DEPLOYMENT.md` says.

**Plans and quotas**
- [ ] Agency #1 is on `business` (section 2, item 5). A new agency is on `free`: **100 buyer messages a day, $2 a month of model spend, 300 queued jobs a day.** Over the message or spend cap the assistant tells the buyer the broker will reply; over the job cap the cron call skips that agency and names it (agency #1 and platform upkeep jobs are exempt from the job cap).
- [ ] `MODEL_PRICES` are estimates, dated by `MODEL_PRICES_AS_OF`. Check them against the provider's list; real spend can differ.
- [ ] The Gemini free tier is a single quota shared by **all** agencies (about 20 requests a day per model on `gemini-3.8-flash` on the free tier, more with `GEMINI_API_KEYS`). A plan cap is a ceiling per agency, not a promise that the shared quota will cover it. A second agency can use up the first one's day.

**Background work**
- [ ] A job worker runs: the web process starts `JOB_WORKERS` (default 2) tasks. Cron (GitHub Actions) now only **queues** jobs; if the process is asleep the jobs wait until the `/health` call in the workflow wakes it. After a manual cron run, check the Jobs list drains to `done`. Jobs left `running` after a restart are taken over once their lease (`JOB_LEASE_SECONDS`, 120) expires.
- [ ] The workflow in `.github/workflows/cron.yml` does **not** call `/api/cron/audit-export`. Nothing exports the audit trail until it is added (or the operator calls it by hand), even with `AUDIT_EXPORT_*` set.
- [ ] If `AUDIT_EXPORT_BUCKET` is used, `boto3` is installed on the host (it is not in `requirements.txt`) and the bucket has object lock with a default retention.

**Channels and email**
- [ ] The Telegram webhook is registered from `PUBLIC_BASE_URL` at start (it must be the https Render URL, and `TELEGRAM_WEBHOOK_SECRET` set). Check with `python -m scripts.telegram_webhook info`, and re-check after any URL change.
- [ ] `FRONTEND_ORIGIN` is the Vercel URL (it is also the base of the invitation links).
- [ ] Resend is on its sandbox sender: mail reaches **only the Resend account owner's address**. Invitations to anyone else are not delivered (the API then returns the link in the response). Verify a domain in Resend and set `EMAIL_FROM` before inviting real people.

**Data**
- [ ] Hosted `schema_migrations` state read before starting (it may be 013 or 019).
- [ ] 030's back-fill: empty `audit_log` on a 019 to 033 run, so instant. If the trail was already written by a partial rollout, rehearsal measured about 6 s per 100,000 entries.
- [ ] 021's rewrite timed on the restored real dump (rehearsal: about 11 s for 30,000 buyers, 300,000 messages, 60,000 model calls).
- [ ] The throw-away second agency was created, saw none of agency #1's data, and was deleted.
- [ ] `verify_audit` is clean after go-live and again after the first real owner action (an invitation or a settings change writes an entry).

## 7. Checklist

- [ ] Hosted migration state checked (`schema_migrations`)
- [ ] Dump taken and stored off this machine
- [ ] Rehearsal on a restored copy passed, timings noted
- [ ] `CREATEROLE` confirmed on Supabase
- [ ] `SECRETS_MASTER_KEY` generated, stored in two places, set on Render
- [ ] Step A applied and verified with the old code running
- [ ] Step B: 023 to 033, new backend, new frontend, owner granted, webhook checked, a cron job drained by a worker
- [ ] Step C verification done, throw-away agency deleted
- [ ] Section 6 go-live checklist ticked
- [ ] `docs/FREE_DEPLOYMENT.md` and `deploy/env.prod.example` list the new migration range and variables (done in this branch)

## 8. Rehearsal result (2026-11-06, local, migrations 001 to 033)

`python -m scripts.rehearse_hosted_migration` (from `backend/`) builds a scratch database `proppulse_rehearsal` on the embedded Postgres 16 (never the test database, never the hosted URL: the script refuses other names and does not read `.env`) at migration 019 with the demo seed plus synthetic buyers, messages and model calls, then runs the real migrator one file at a time and checks after each stage. It finished with **31 checks passed, 0 failed** on both runs below.

| Run | Data at 019 | 021 | Step A (020-022) | 023-026 | 027-033 | 030 alone | `verify_audit` |
|---|---|---|---|---|---|---|---|
| Small (default) | 300 buyers, 1,500 messages, 600 model calls | 0.9 s | 1.4 s | 0.8 s | 1.0 s | 0.4 s for 3,000 entries | 0.6 s |
| Large | 30,000 buyers, 300,000 messages, 60,000 model calls | **10.6 s** | 11.2 s | 1.1 s | 6.9 s | **6.1 s for 100,000 entries** | 2.0 s |

(Embedded Postgres on a laptop SSD, run as superuser, no other load. Supabase's free tier is shared CPU: expect it to be slower, not faster. 021 scales roughly linearly with rows; 030's back-fill is a row-by-row loop, about 60 microseconds an entry.)

What was checked:

| Check | Result |
|---|---|
| Old-code probes (9 statements the `main` code runs: the five `on conflict` writes, a chat insert, the two `match_*` functions, a catalogue update) on 019, after 020-022 | 9 of 9 succeed both times |
| Same probes after 020-022 with **027-029 and 031-033 also applied** (side copy) | 9 of 9 succeed: those files are additive, 028's replaced functions keep working for the old code |
| Same probes after 023-026 | **5 of 9 fail** (`no unique or exclusion constraint matching the ON CONFLICT specification`: buyer upsert, announcement claim, layout upsert, channel-post claim, proactive reservation); the plain insert, both searches and the catalogue update still work. 023 is the switch |
| Row counts of every 019 table | unchanged after Step A, after 026 and after 033 |
| Agency #1 | 1 organization, plan `business`, no row without `org_id`, all messages in it, RLS on, table owner not forced through RLS |
| RLS from `app_user` | agency #1 sees all its buyers; a second agency sees 0 buyers, 0 messages, none of agency #1's audit entries; an insert naming another agency is refused; no access to `jobs`; cannot write the shared catalogue (025) |
| Price views (028/029) | agency #1 sees its own price in `agency_properties`, `effective_price` and `match_properties_hybrid`; another agency sees the shared price; a history row was written for agency #1 only |
| 033 | deleting an agency that has its own prices works |
| 030 back-fill | every audit entry has `seq`/`hash`, `seq` runs 1..n per agency, a new entry continues the chain, `python -m scripts.verify_audit` exits 0 ("chain only": no sink configured) |
| Tampering | editing one entry (trigger disabled by the owner) makes `verify_audit` exit 1 |

What this rehearsal does **not** show:

- It ran as a superuser (the embedded server's `postgres`), so it cannot prove that Supabase's `postgres` may `create role` (section 2, step 3) or that the old code is unaffected by RLS there. Tables are not `FORCE`d, so an owner is exempt; confirm on the real project.
- The data is synthetic. The timings say nothing precise about the hosted database; measure 021 on the restored real dump.
- It did not use the transaction pooler, Supabase's `extensions` schema for pgvector, `auth.users`, or the hosted pgvector version. It ran no application server, workers, webhook, email or Gemini calls (the job worker, the quota paths and the export job are covered by the backend test suite, not by this script).
- The audit export to a bucket (object lock) was not rehearsed; only the chain check was.
