# Plan: moving the hosted database and live app to multi-tenancy

Status: **rehearsed on a local copy (section 7), not yet carried out.** Nothing here has been run against the hosted Supabase database. The local and test databases already run migrations 020 to 024; the hosted one does not. This page says what changes, in what order, how to check each step, and how to go back.

Everything below is done from a branch that is merged on purpose. Until then `main` and the hosted system stay exactly as they are.

## 1. What is different about this migration

| Migration | What it does | Safe while the old code still runs? |
|---|---|---|
| 020 | Creates `organizations`, `memberships`, `invitations`, `org_settings`; seeds agency #1 (`proppulse`) | Yes, only adds tables |
| 021 | Adds `org_id` to 15 tables, fills it with agency #1, default = agency #1, indexes | Yes: old inserts omit `org_id` and get the default. Briefly locks each table while rows are updated |
| 022 | Creates role `app_user`, turns on row-level security (RLS) with policies | Yes, **if the old code connects as the table owner** (Supabase's `postgres`): owners are not forced through RLS, so nothing changes for them |
| 023 | Replaces global unique keys with per-agency ones; composite foreign keys | **No.** The live code runs `on conflict` on `thread_id`, `dedupe_key`, `message_id`, `property_id` (layouts) and `(property_id, kind, price_etb)` (channel posts). After 023 those constraints no longer exist and the insert fails with "no unique or exclusion constraint matching the ON CONFLICT specification". Buyers could not start a chat |
| 024 | `org_keys`, `org_secrets` (encrypted credentials), more `org_settings` columns | Yes, only adds |

So the migration is **expand, then switch**: 020 to 022 can go first, with the old code running. 023 and the new code must change together, in one short window, and 024 can go with them.

Related facts to keep in mind:

- The hosted database is believed to be at 019 (the project notes say so; `docs/FREE_DEPLOYMENT.md` still says 013). Check, do not assume: `select name from schema_migrations order by name;`.
- `scripts/migrate.py` runs each file in its own transaction and records it in `schema_migrations`, so a failed file leaves nothing half-applied and a re-run continues from there.
- Use the **direct or session-pooler** connection string for migrations, not the transaction pooler.

## 2. Before touching anything

1. **Back up.** Supabase's free plan has no point-in-time recovery. Take a full dump and keep it off the machine that runs the migration:

   ```bash
   pg_dump "$HOSTED_DIRECT_URL" --format=custom --no-owner --file=proppulse-before-020.dump
   ```

2. **Rehearse on a copy.** Restore that dump into the embedded Postgres (a scratch database, never the test one), then run the real migrator against it:

   ```bash
   createdb proppulse_rehearsal   # on the embedded server, port 8787
   pg_restore --no-owner -d proppulse_rehearsal proppulse-before-020.dump
   DATABASE_URL=postgresql://postgres:@127.0.0.1:8787/proppulse_rehearsal python -m scripts.migrate
   ```

   Record how long each file took and whether 021's backfill or 023's key swap complains about existing data (duplicate `(org_id, thread_id)` cannot happen after 021, but the rehearsal is where surprises show). Then run the application's smoke test against the rehearsal database (`PORT=8123 python -m app`, every channel forced to mock).

3. **Check what Supabase allows.** Migration 022 runs `create role app_user` and `grant app_user to <current user>`. Supabase's `postgres` role has `CREATEROLE`, so this should work; confirm on the real project with a one-line test (`select rolcreaterole from pg_roles where rolname = current_user;`). If it does not, 022 fails cleanly (its own transaction) and nothing else is lost.

4. **Generate secrets** and keep a copy outside the repository and the database:

   ```bash
   python -m scripts.make_master_key
   ```

   Put the result in Render as `SECRETS_MASTER_KEY`. Losing or changing it later makes every stored bot token unreadable.

5. **Pick a quiet time.** The window is a few minutes. The Telegram bot is the only thing buyers see during it.

## 3. The steps

### Step A, expand (old code still running, no outage)

```bash
cd backend
python -m scripts.migrate   # with only 020, 021, 022 present, see note
```

The migrator applies every file it finds, so to stop after 022 run the three files from a temporary copy of `migrations/` (or `psql -f` each and insert its name into `schema_migrations`). Do **not** let it reach 023 yet.

Verify (all of these should hold, and the live bot should keep answering):

```sql
select count(*) from organizations;                              -- 1, slug 'proppulse'
select count(*) from buyers_crm where org_id is null;            -- 0
select relrowsecurity from pg_class where relname = 'buyers_crm'; -- true
select pg_has_role(current_user, 'app_user', 'member');           -- true
```

Then send the live bot a message and open the Command Center. If either breaks, go to section 5.

### Step B, switch (short window)

Order matters because the new code requires 023 and 024, and the old code cannot survive 023.

1. Set on Render: `SECRETS_MASTER_KEY`. Leave `ADMIN_API_KEY` as it is (it is now the platform secret) and keep `CRON_SECRET`. Remove `BROKER_EMAIL` from Vercel; it no longer does anything.
2. Run the remaining migrations (023, 024).
3. Deploy the new backend to Render and the new frontend to Vercel straight away. Render's cold start is the longest part. In the gap, the bot's updates fail; Telegram retries them, so buyers' messages are delayed, not lost.
4. Make the first owner (membership replaces the old email check):

   ```bash
   python -m scripts.grant_role --email <your Supabase account email> --org proppulse --role owner
   ```

5. Webhook: the original route `/api/telegram/webhook` is kept for the first agency and is re-registered by the app on start from `PUBLIC_BASE_URL`; check with `python -m scripts.telegram_webhook info`. New agencies connect their own bot and get `/api/telegram/webhook/<slug>`.
6. Encrypted channels: the first agency still uses the environment's `TELEGRAM_BOT_TOKEN` and calendar settings; nothing has to be re-entered. Moving them into `org_secrets` is optional and can happen later from the console.

### Step C, verify

- `python -m scripts.post_deploy_check` against the live backend.
- A real message to the bot, a booking, and a page load in the Command Center as the owner.
- The cron jobs (GitHub Actions) run once by hand and report one agency.
- A second account created at `/start` sees an empty agency and none of agency #1's buyers. This is the isolation check on the live system, using a throw-away agency you delete afterwards.
- The Supabase Auth settings are unchanged: "Confirm email" stays on, because invitation links rely on the address being the owner's own.

## 4. What this plan does not cover

- **Zero-downtime for 023.** The window is minutes, accepted for a demo-scale deployment. A longer-lived system would first deploy code that tolerates both keys (add the per-agency keys, change `on conflict` targets, then drop the old ones), which is three releases instead of one. Not worth it here.
- **Supabase's data API.** The tables are in `public` and RLS is on everywhere, with no policy for `anon` or `authenticated`, so the public API sees nothing. That was already true; the new tables follow the same rule. Re-check in the dashboard after Step B.
- **Connection pooler behaviour.** The tenant label is set with `set_config(..., true)` inside each transaction, which is safe through a transaction-mode pooler, but this has only been exercised against the embedded Postgres. Step C's second-account check is the real test.
- **pgvector version.** The benchmark ([BENCHMARKS.md](BENCHMARKS.md)) used 0.6.2. Check `select extversion from pg_extension where extname = 'vector';` on Supabase; nothing here depends on it, but the vector-index advice in the ADR does.

## 5. Going back

| Where it stopped | How to go back |
|---|---|
| During Step A, a migration failed | Nothing to undo: each file is its own transaction. Fix and re-run, or stop; the old code is still compatible with whatever was applied |
| After Step A, something looks wrong | Old code is still running and compatible. Restore from the dump if the data is damaged; otherwise leave 020 to 022 in place (they are additive) |
| During or after Step B | There is no down-migration for 023. Restore the dump (`pg_restore --clean`), redeploy the previous Render and Vercel releases, and re-register the webhook. Any chats that happened after the dump are lost, which is why the window should be quiet and short |

Restoring the dump is the only supported way back from 023, so the rehearsal in section 2 is not optional.

## 6. Checklist

- [ ] Hosted migration state checked (`schema_migrations`)
- [ ] Dump taken and stored off this machine
- [ ] Rehearsal on a restored copy passed, timings noted
- [ ] `CREATEROLE` confirmed on Supabase
- [ ] `SECRETS_MASTER_KEY` generated, stored in two places, set on Render
- [ ] Step A applied and verified with the old code running
- [ ] Step B: 023, 024, new backend, new frontend, owner granted, webhook checked
- [ ] Step C verification done, throw-away agency deleted
- [ ] `docs/FREE_DEPLOYMENT.md` updated to the new migration range and variables

## 7. Rehearsal result (2026-11-02, local)

`python -m scripts.rehearse_hosted_migration` (from `backend/`) builds a scratch database at migration 019 with the demo seed plus 300 buyers and 1,500 chat messages, then applies Step A and Step B and runs, after each stage, the exact write statements the live (`main`) code uses. It can be re-run any time.

| Stage | Old-code writes (6 probes) | Notes |
|---|---|---|
| On 019 (control) | all 6 succeed | |
| After 020 to 022 | **all 6 succeed** | 1 organization; 0 buyers without `org_id`; all 1,500 messages in agency #1; RLS on; applying took about 1 s |
| After 023 and 024 | **5 of 6 fail**: `there is no unique or exclusion constraint matching the ON CONFLICT specification` (buyer upsert, announcement claim, layout upsert, channel-post claim, proactive reservation). Only a plain insert still works | applying took about 0.5 s; all 300 buyers and 1,500 messages intact |

So the plan's central claim holds: 020 to 022 are safe with the old code running, and 023 must be switched together with the new code.

What this rehearsal does **not** show:

- It ran as a superuser (the embedded server's `postgres`), so it cannot prove that Supabase's `postgres` may `create role` (section 2, step 3) or that the old code is unaffected by RLS there. Tables are not `FORCE`d, so an owner is exempt; confirm on the real project.
- The data is tiny (hundreds of rows). Timings say nothing about the hosted database; 021 rewrites every row of 15 tables, so measure it on the restored dump.
- It did not use a dump of the real data, the transaction pooler, or Supabase's extensions schema.

