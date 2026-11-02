# Plan: the next three multi-tenant features

Status: planned 2026-11-02. Features 1 (the job queue), 2 (per-agency prices) and 3 (master-key rotation) are built. Everything stays on the local `saas` branch: no merge to `main`, no push, no hosted database.

Order and why:

1. **Fair job queue**: the other two need background work that runs per agency, retries and never runs twice.
2. **Per-agency prices**: closes a product hole and a bug found while planning (below). Its notifications are queue jobs.
3. **Master-key rotation**: independent. Its re-encryption is a resumable queue job too.

Migrations continue from 027. Each feature ends with: tests, a doc section, the threat model updated, a full suite run, one commit.

---

## 1. A job queue that is fair between agencies

### Why

The timed jobs (proactive run, reverse-RAG alerts, tour and milestone reminders, promotion ads) are HTTP cron calls that loop over agencies **one after another, inside the request** (`run_for_orgs` in `app/access.py`). Consequences:

- One slow agency (a calendar timing out, many buyers) delays every agency after it, and a long run can outlive the HTTP timeout (threat model T9).
- A crash halfway leaves no record of which agencies were done.
- Nothing runs in parallel, and nothing stops two triggers from running the same job at once except the per-message dedupe keys.

### Design

**Tables (migration 027), platform-owned, not readable by `app_user`:**

```
jobs(id, org_id, kind, payload jsonb, dedupe_key, status [queued|running|done|failed|dead],
     priority, run_after, attempts, max_attempts, locked_by, locked_until, last_error,
     created_at, started_at, finished_at)
unique (org_id, kind, dedupe_key)        -- the same job for the same agency is queued once
index on (status, run_after)             -- what the workers look for
```

**Claiming fairly.** A worker claims with one statement that picks, among agencies that have work due, the one that has been served least recently, then its oldest job:

```sql
with ready as (
  select distinct on (org_id) id, org_id from jobs
  where status = 'queued' and run_after <= now()
    and org_id not in (select org_id from jobs where status = 'running' group by org_id having count(*) >= $per_org_limit)
  order by org_id, priority desc, id
)
update jobs set status = 'running', locked_by = $worker, locked_until = now() + $lease, attempts = attempts + 1, started_at = now()
where id = (select r.id from ready r left join org_last_served s using (org_id)
            order by s.served_at nulls first limit 1
            for update of jobs skip locked)
returning *;
```

`for update skip locked` lets several workers claim without blocking each other. A small `org_last_served` table gives round-robin between agencies, and the per-agency running limit keeps one agency from taking every worker.

**Leases, not trust.** A running job holds a lease (`locked_until`) and the worker extends it while it works. A job whose lease expired (the worker died) goes back to `queued`, so a crash is retried, not lost.

**Retries.** A failure sets `run_after = now() + backoff(attempts)` (exponential with jitter, capped). After `max_attempts` it becomes `dead`, is shown in the console, and the operator can retry it by hand. Errors are stored without secrets.

**Exactly-once effects.** The queue gives at-least-once delivery. The existing per-message dedupe keys (`claim_event`) already make each send happen once, so a retried job does not message a buyer twice. The plan keeps that rule: **every job must be safe to run twice**, and the tests run each one twice.

**Running a job.** A worker loop inside the app (`app/jobs.py`) runs N workers as asyncio tasks. Each claimed job runs inside `runtime.scope(org)`, so row-level security, settings and channels are the agency's, exactly as the HTTP path does today.

**What changes for callers.** The cron endpoints stop doing the work: they enqueue one job per active agency (dedupe key = job kind plus the date or hour) and return at once with the job ids. `dry_run` keeps running inline, so the console's preview stays immediate. The console gets a Jobs panel: queued, running, failed and dead per agency, with a retry button for operators.

**Tenant isolation.** `jobs` holds `org_id` but is not readable by agencies. A job can only act inside the agency it was queued for, because the worker sets the scope from the row, never from the payload.

### Steps

1. Migration 027: `jobs`, `org_last_served`, indexes, grants (platform only).
2. `app/jobs.py`: `enqueue`, `claim`, `heartbeat`, `complete`, `fail`, `reap_expired`, worker loop, job registry (`kind` to function).
3. Register the existing jobs: proactive run, reverse-RAG, tour reminders, milestone reminders, promotion ads.
4. Cron endpoints enqueue; `dry_run` stays inline; worker started in the app lifespan (off in tests unless asked).
5. `GET /api/jobs` (operator: all; agency admin: its own), `POST /api/jobs/{id}/retry` (operator).
6. Console: a Jobs card on the Agency page and an operator view.

### How it is proven

- Unit tests: claim order (round-robin across agencies), per-agency limit, backoff schedule, dead after max attempts, a dedupe key queued twice gives one job.
- Concurrency test: several workers against **separate** database connections (the suite normally shares one; this test opens its own) claim 500 jobs from 20 agencies; every job runs exactly once, none twice, none lost.
- Crash test: a worker is cancelled mid-job; its lease expires; another worker finishes it.
- Idempotency test: each registered job runs twice for the same agency; buyers receive one message.
- Fairness benchmark (`scripts/bench_jobs.py`): one agency with 1,000 slow jobs and nine with 10 fast jobs each; the quiet agencies finish within a bound regardless of the noisy one. Before/after numbers go into `docs/BENCHMARKS.md`.

### Risks

- The test suite's single shared connection: the queue tests need their own connections and must clean up what they commit.
- Jobs that were synchronous for the console's buttons now finish later; the console must show "queued" and update from the event stream.

---

## 2. Per-agency prices on top of the shared catalogue

### Why

- **Product hole:** since market edits are operator-only, an agency cannot give its own buyers a price for a project that has none, or a better price it negotiated.
- **Bug found while planning:** `ProjectService.set_price` notifies the buyers waiting for that project's price by reading `price_requests`, which is private per agency. The operator sets the price inside the operator's agency, so **only the operator agency's waiting buyers are told**. Buyers who asked through any other agency are never answered. The price-drop path (`_price_changed`) has the same limit.

### Design

**Shared price stays the default; an agency may override it.** Migration 028:

```
agency_prices(org_id, target_kind [project|property], target_id, price_etb, note, valid_until,
              created_by, created_at, updated_at, primary key (org_id, target_kind, target_id))
```

Private table: row-level security like every other private table, readable and writable by the agency's own role (members with `agent` or above through the API).

**One place decides "the price this agency shows":** a SQL view or function, `effective_price(kind, id)`, which returns the agency's override if present and valid, else the shared price. Everything that shows or compares a price goes through it:

- `match_properties_hybrid` (budget filter and the returned price), `rank_properties`, `match_buyers_for_property` (reverse match), project search and project cards.
- Brochures, dossiers and payment schedules (`app/finance.py`) read the effective price, so the agent never quotes one number and the PDF another.
- The groundedness check, which refuses figures not in the facts, gets the effective price as a fact.

The functions run as the caller (`security invoker`), so the override they see is the calling agency's own, enforced by row-level security, not by a parameter.

**Notifications fan out per agency (uses feature 1).** When the shared price of a project changes, or an agency sets or changes its override:

- Shared change: one job per agency that has open or answered price requests for that project. Each job runs in its agency's scope and tells that agency's buyers the **effective** price, so an agency with an override keeps its own price and is not contradicted.
- Override change: a job for that agency only.
- Dedupe keys include the agency and the effective price, so the same price is never announced twice.

**Who may do what.** The shared price stays operator-only (`market_agent`). The override is the agency's own (`console_access`, agent and above), and each change is audited (`price.override_set`, `price.override_clear`).

**Console.** On the Projects page an agency sees the shared price and its own price side by side, and can set, change or clear its price. The operator's buttons stay as they are.

### Steps

1. Migration 028: `agency_prices`, policies, `effective_price`.
2. Repository: override CRUD, and every price read switched to the effective price (grep every `price_etb` and `price_from_etb` read in repository and SQL functions; a test lists them).
3. Rewrite `set_price` and `_price_changed` as fan-out jobs; add the override endpoints and audit actions.
4. Finance, brochure, dossier and groundedness read the effective price.
5. Console: override editor on the Projects page.

### How it is proven

- The bug first: a test where agency B's buyer asks for a price and the operator sets it. Today it fails (B's buyer is not told); after the change it passes.
- Isolation: agency A's override never appears in B's search results, cards, brochures or messages (a test per surface).
- Consistency: for one buyer, the price in the reply, the brochure, the payment schedule and the dossier is the same number.
- Budget filtering uses the effective price: a unit above a buyer's budget at the shared price but within it at the agency's price is found for that agency's buyer only.
- Benchmark: search p95 with overrides present for 50 agencies stays within a few percent of today's (`scripts/bench_tenants.py` extended).

### Risks

- Missing one price read leaves two prices in different places. Mitigation: a test that scans the SQL functions and repository for raw `price_etb` and `price_from_etb` reads outside an allow-list.
- The evals compare replies to recordings; replies must not change for the operator agency (no overrides). `python -m evals --check` is part of done.

---

## 3. Master-key rotation without downtime

### Why

All agency credentials are protected by data keys wrapped with one master key (`SECRETS_MASTER_KEY`). Today the key can only be replaced by breaking every stored credential, so in practice it can never be rotated, even after a suspected leak (threat model T8).

### Design

**Keys carry an id.** Ciphertexts become `v2.<key_id>.<nonce+ciphertext>`, where `key_id` is a short fingerprint of the master key. `v1.` values (no id) are still read, as belonging to the original key.

**Several keys during a rotation.** Settings accept `SECRETS_MASTER_KEYS` (several, newest first; the existing single variable keeps working). The vault encrypts with the newest key and decrypts with whichever key the stored value names. During a rotation both keys are present, so nothing breaks.

**Only the data keys are re-wrapped.** Each agency's secrets are sealed with its own data key, and only the data key is sealed with the master key. Rotation therefore rewrites one small row per agency (`org_keys.wrapped_key`), never the secrets themselves.

**The rotation is a job (uses feature 1).** `python -m scripts.rotate_master_key` (operator) enqueues one re-wrap job per agency. Each job, in a transaction: lock the agency's key row, open with the old key, seal with the new, write, verify by opening a stored secret, commit. A crash leaves some agencies done and some not; both kinds keep working because both keys are loaded, and re-running the command only queues the agencies still on the old key.

**Finishing.** `--status` reports how many agencies are on each key. When none use the old key, the operator removes it from the environment. The app refuses to start if a stored value names a key it does not have, and says which, so a key cannot be dropped too early unnoticed.

**Optional, same machinery:** rotating one agency's data key, which re-seals that agency's few secrets, for when an agency asks for it.

### Steps

1. Vault: `v2` format with key id, multi-key decrypt, newest-key encrypt; `v1` read path kept.
2. Settings: `SECRETS_MASTER_KEYS`; start-up check for unknown key ids.
3. Re-wrap job and `scripts/rotate_master_key.py` (`start`, `--status`).
4. Audit entry per agency re-wrapped (`system` actor); an operator view of key status.
5. Docs: a rotation runbook in SECURITY.md and DEPLOYMENT notes.

### How it is proven

- Unit: `v1` values still open; `v2` values name their key; a value from another agency or another secret name still fails to open (the existing binding).
- Rotation test: 30 agencies with secrets; rotate; every secret still opens; no `org_keys` row names the old key; the secrets' own ciphertexts are unchanged (only data keys moved).
- Crash test: cancel the rotation halfway; the app keeps serving every agency with both keys loaded; a second run finishes only the rest.
- Early-removal test: drop the old key while one agency still uses it; start-up fails with a message naming the key and the count.
- Concurrency: an agency connecting a new bot during its re-wrap ends with a working, new-key-sealed secret (the row lock serialises the two).

### Risks

- Losing the new key mid-rotation is as bad as losing the old one today: the runbook says to store it before starting.
- The `v2` format must be read by every code path that opens a secret; a test opens every stored secret after rotation.

---

## Done for the whole plan

- Three commits on `saas`, each with a passing full backend suite, frontend typecheck, lint and tests, and `python -m evals --check`.
- `docs/BENCHMARKS.md` has the queue fairness numbers and the search-with-overrides numbers.
- `docs/THREAT_MODEL.md`: T9 (job fairness) and T8 (rotation) moved from open to covered, with their tests; the price-notification bug recorded as found and fixed.
- The two-agency smoke run (real server, mocked channels) extended with: a queued job per agency, an override seen by one agency only, and a rotation in the middle of traffic.
