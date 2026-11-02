# Plan: what is left after the three multi-tenant features

Status: planned 2026-11-02. Items 1 (the event bus losing events) 2 (alerts for an agency's own unit price, migration 029) and 3 (replacing one agency's own data key) 4 (a tamper-evident audit trail) and 5 (money and job quotas) are built; the rest are not. Follows [PLAN_NEXT_SAAS.md](PLAN_NEXT_SAAS.md), whose three features (fair job queue,
per-agency prices, master-key rotation) are built and on the local `saas` branch. Everything here stays local on `saas`: no merge to
`main`, no push, no hosted database. Migrations continue from 029.

Each item ends like the earlier features: tests, the doc or threat-model entry it changes, a full suite run, one commit.

Order, and why:

| # | Item | Size | Why this place |
|---|---|---|---|
| 1 | Fix the flaky event-bus test (a real bug) | small | Makes the suite reliable before more is added; it is a real lost-event bug, not only a test problem |
| 2 | Tell buyers about an agency's own lower unit price | small | Closes the gap left by feature 2 |
| 3 | Rotate one agency's own data key | small | Reuses feature 3; closes the last T8 gap |
| 4 | Audit trail the owner cannot quietly edit | medium | T15's remaining gap |
| 5 | Quotas on model cost and on background jobs | medium | T12's remaining gap; needs 1 so usage events are not lost |
| 6 | Invitations sent by email | small | A team cannot be built today without copying a link by hand |
| 7 | The public website shows the agency the visitor chose | medium | The backend supports it; the website never asks for it |
| 8 | Telegram long polling for every agency (development only) | small | Local testing of a second agency's bot |
| 9 | Connect a real Google Calendar once | manual | Needs your Google account; a checklist, not code |
| 10 | Hosted migration | large | Changes the live system: only when you say so (see the end) |

---

## 1. The event bus loses events when two instances write at once

### Why

`test_events_durable::test_two_instances_see_each_others_events_in_the_same_order_with_the_same_ids` fails about 2 runs in 5. The cause
in `DurableEventBus` (app/events.py): each instance remembers only the highest event id it has delivered (`_last_seen`) and then reads
`id > _last_seen`. Ids are taken when a row is inserted but become visible when it commits. If instance B's insert takes id 7 and commits a
moment after instance A's id 8, a reader that already saw 8 never reads 7. In production that is a dashboard event (a reply, an alert)
that one browser tab never shows, so this is a real bug, not only a flaky test.

### Design

- Keep the set of ids delivered recently and a low-water mark: the highest id below which every id has been seen or given up on.
- Read `id > low_water` each poll and deliver only ids not yet delivered.
- A gap (an id not visible yet) is waited for up to a few seconds, then given up: a rolled-back insert leaves a gap that never fills.
- Same for a tab that reconnects with `Last-Event-ID`: the backlog read already uses `id >`, so it gets the same treatment.

### Proof

- First, reproduce it deterministically: hold one instance's insert open (a transaction that has taken an id but not committed) while the
  other commits a higher id, then commit the first. Today the event is lost; after the fix it arrives, once.
- A rolled-back insert does not stall delivery for longer than the grace period.
- The flaky test passes 50 runs in a row.

### Risks

- A grace period that is too short loses a slow insert again; too long delays nothing (later ids are still delivered at once, only the
  low-water mark waits). Start at 10 seconds.

---

## 2. Tell buyers when an agency lowers its own price for a unit

### Why

Feature 2 announces project prices per agency, but an agency that lowers its own price for a single unit tells nobody. The price-drop
alerts (reverse RAG) read `price_history`, which records only shared prices.

### Design

- Migration 029: `agency_price_history (org_id, property_id, old_price_etb, new_price_etb, change_pct, detected_at)`, private per agency
  like `agency_prices`, written by a trigger on `agency_prices` that compares the effective price before and after (an override set,
  changed, cleared or expired by date is a change of what the agency shows).
- `recent_price_drops` reads the shared history (already filtered to drops still in effect for this agency) **and** this agency's own
  history, so the existing reverse-RAG job, the per-buyer daily cap and the dedupe keys do the rest unchanged.
- An increase goes to the agency's broker only, as for shared prices.

### Proof

- Agency A lowers a unit to below a buyer's budget: A's matching buyer is alerted once, B's buyer is not; running the job twice sends once.
- A raises it: only A's broker is told. A clears it back to a higher shared price: treated as an increase.
- The guard tests from feature 2 still pass (the new statements read through the views).

---

## 3. Rotate one agency's own data key

### Why

The last open part of T8: if one agency's data key may have leaked (or the agency asks), only its own secrets need re-sealing, under a
new data key.

### Design

- A `keys.rotate_data_key` job, reusing feature 3's pattern: in one transaction, lock the agency's `org_keys` row, open every secret with
  the old data key, seal each with a new one, write the new wrapped key and the secrets, check one opens, commit; an audit entry.
- `POST /api/keys/agency/{org_id}/rotate` for the operator, and `POST /api/org/keys/rotate` for the agency's owner (its own agency only).

### Proof

- Every secret opens afterwards and every ciphertext changed; the old data key opens none of them.
- A crash before commit leaves the agency on its old key, working. A secret stored at the same moment ends up sealed with the new key
  (the row lock serialises them, as in feature 3's race test).

---

## 4. An audit trail the database owner cannot quietly edit

### Why

T15's remaining gap: the table owner can still delete or rewrite audit rows (the trigger only stops `update` from ordinary paths).

### Design

- Migration 030: each entry gets `prev_hash` and `hash` = SHA-256 of the previous entry's hash and this entry's fields, per agency, set by
  a trigger at insert. Deleting or editing a row breaks the chain at that point.
- An export job (daily, through the queue) appends each agency's new entries as JSON lines to a sink the database cannot reach: a local
  directory for development, an S3-compatible bucket with object lock in production (configured by URL; off when unset).
- `python -m scripts.verify_audit` recomputes every chain and, when an export exists, compares it with the database.

### Proof

- Removing, editing or reordering an entry is reported with the agency and the first broken entry.
- The export is append-only and resumable (it records the last exported id per agency); running it twice exports nothing new.

### Risks

- A chain proves an edit happened, not who did it; the export is what makes deletion recoverable. Without a configured sink, only
  detection remains (say so in the threat model).

---

## 5. Quotas on model cost, and on background jobs

### Why

T12: the cap counts answered buyer messages, not what they cost, and background jobs have no cap at all.

### Design

- Model cost: `model_calls` already records input and output tokens per call and agency. A price table per model in settings turns them
  into money; each plan gets a monthly budget beside its daily message cap. Over budget, the assistant behaves as over the message cap
  (the buyer is told the broker will reply; nothing is lost). The usage endpoint and the Agency page show spend against budget.
- Jobs: each plan gets a daily number of queued jobs per agency; `enqueue` refuses beyond it with a clear error (cron calls skip that
  agency and say so in their answer). The operator's own agency is not capped.

### Proof

- An agency at its budget gets the "broker will reply" path; another agency is unaffected. Spend matches the recorded tokens times the
  prices.
- The job cap refuses the next job for that agency only; the fairness test from feature 1 still passes.

### Risks

- Model prices change: keep them in settings, not code, and show the date they were set.

---

## 6. Invitations sent by email

### Why

`POST /api/invitations` returns the link token and leaves sending it to the caller; nobody sends it.

### Design

- Send the link through the existing email service (Resend, or the mock in development), from the agency's name, with the role and the
  expiry. The token still appears only once, in the email (and in the response for a copy-by-hand fallback when email is not configured).
- Resend and revoke stay as they are; a resend issues a new token and invalidates the old one.

### Proof

- The email holds a link that accepts the invitation once, for that address only; no email is sent for a revoked invitation; the token is
  never logged (the log redaction test covers it).

---

## 7. The public website shows the agency the visitor chose

### Why

The backend serves the public site per agency (`X-Agency` header, `agency_scope`), but the website never sends it, so every visitor sees
the original agency's listings, prices and assistant.

### Design

- Agency address: `/{agency-slug}/...` on the website (a subdomain later, when there is a domain). The website reads the slug from the
  path and sends `X-Agency` on every call; an unknown or suspended agency shows the existing "not found" page.
- An optional public directory of agencies that opt in (`org_settings.listed`, migration 031).
- The agency's name, colour and logo (already in `org_settings`) on its pages.

### Proof

- Frontend tests: the slug reaches every API call; an invalid slug never does. Backend: the directory lists opted-in, active agencies only.
- A browser check with two agencies: different prices for the same unit (feature 2), different names.

---

## 8. Telegram long polling for every agency (development only)

Today polling serves only the original agency, so a second agency's bot cannot be tried locally without a public webhook URL. In
`TELEGRAM_MODE=polling`, start one poller per agency with a connected bot, each submitting its updates inside that agency's scope; stop
and start pollers as bots are connected and disconnected. Production keeps webhooks. Proof: two fake bots polled, each update answered
by its own agency.

---

## 9. Connect a real Google Calendar once (manual)

Connecting an agency's calendar has only been tested with fakes. With your Google account: create a service account, share a test
calendar with it, connect it on the Agency page of a local server, book a visit, and check the event appears and the busy times are
respected. A checklist in docs, plus anything it uncovers.

---

## 10. The hosted migration (not part of this plan's work)

The live database is still at migration 019 and none of `saas` is live. `docs/MIGRATION_HOSTED.md` has the plan, rehearsed locally:
020 to 022 are safe with the current live code, while 023 onwards must switch together with the new code. That means merging `saas`
into `main`, migrating Supabase and deploying, which you have ruled out so far. When you decide to, it gets its own plan review first,
updated with migrations 027 onwards.

---

## Done for this plan

- Items 1 to 8 each in one local commit on `saas`, with a passing full backend suite, ruff, mypy, frontend typecheck, lint and tests,
  and `python -m evals --check` where replies could change (items 2, 5).
- `docs/THREAT_MODEL.md`: T8, T12 and T15 updated to what is covered and what is not; the event-loss bug recorded as found and fixed.
- The smoke run (local server, mocked channels) extended with: an agency's unit price drop alerting its buyer, an emailed invitation
  accepted, and the website showing two agencies side by side.
