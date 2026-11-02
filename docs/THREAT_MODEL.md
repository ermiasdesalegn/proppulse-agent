# Threat model: many agencies on one PropPulse

Scope: the multi-tenant design (organizations, memberships, row-level security, per-agency channels). It complements [SECURITY.md](../SECURITY.md), which lists the protections of each surface; this page asks what an attacker wants, how far each protection stretches, and what is left.

## What is being protected

| Asset | Why it matters | Where it lives |
|---|---|---|
| An agency's buyers, chats, tours, deals, commission ledger | Personal data (phone, email, budget) and the agency's business | private tables, every row has `org_id` |
| An agency's bot token and calendar key | Whoever has them can speak as the agency | `org_channels`, encrypted (`app/vault.py`) |
| Who belongs to which agency, and their role | Decides who may read and change the above | `memberships`, `invitations` |
| `SECRETS_MASTER_KEY`, the platform admin key, the cron secret | Each opens a whole class of the above | server environment only |
| The shared market data | Wrong data misleads every agency | shared tables, written by the platform's crawlers |

## Who attacks

1. **A member of agency A** (an agent, or a viewer) who wants agency B's data, or more rights inside A.
2. **An outsider** with no account: guesses ids, replays webhooks, sends hostile chat messages, scrapes the public site.
3. **A buyer** who talks to the agent and tries to steer it, or to learn about other buyers.
4. **A mistake of ours**: a query written without the agency filter, a background task that forgets whose it is. Not malicious, and the most likely cause of a real leak.
5. **Someone who obtains a copy of the database** (a backup, a leaked connection string).

## Threats and what answers them

| # | Threat | Defence | Evidence | Remaining risk |
|---|---|---|---|---|
| T1 | A member reads or writes another agency's rows by changing an id in a request | The database refuses it, whatever the query says: `app_user` runs with row-level security and every policy is `org_id = current_org()` | `tests/test_tenancy_isolation.py` | None known for tables covered by policies |
| T2 | A query forgets its agency filter | Same policy. The transaction label is set by `tenant_connection`; with no label the role sees no rows and can write none (fails closed) | `tests/test_tenancy_context.py`, `tests/test_tenancy_schema.py` | A forgotten label produces an empty page, not a leak |
| T3 | A row links to a record of another agency (a chat message pointing at someone else's buyer) | Composite foreign keys `(org_id, id)` refuse the link even for the table owner | `tests/test_tenancy_isolation.py` | None known |
| T4 | A member claims an organization in a header (`X-Org-Id`) they do not belong to | The caller's membership is read from the database for that organization on every request; no membership answers 403 | `tests/test_access_roles.py` | Role changes take effect on the next request, not mid-request |
| T5 | A viewer or agent performs an owner action (invite, connect a bot, change settings) | Roles are ordered (viewer < agent < admin < owner) and each route names its minimum with `require_role` | `tests/test_access_roles.py` | Roles are coarse; there is no per-resource permission |
| T6 | Guessing or reusing an invitation link | A link works once, only for the invited address, expires, and only its hash is stored | `tests/test_access_roles.py` | Anyone who controls that mailbox can accept; keep "Confirm email" on |
| T7 | One agency's conversation continues in another's (same Telegram chat id at two agencies) | Checkpoints are keyed `org:<id>:<thread>`; webhooks are per agency (`/api/telegram/webhook/<slug>`) with a per-bot secret | `tests/test_tenancy_agent.py`, `tests/test_tenancy_runtime.py` | Telegram long polling serves only the original agency (a development convenience) |
| T8 | Stealing an agency's bot token from the database or a backup | AES-GCM envelope encryption: a data key per agency under the master key, ciphertext bound to the agency and the secret's name, so a value copied to another row fails to open. `app_user` cannot read the table at all | `tests/test_vault.py`, `tests/test_channels_api.py` | A copy of the database **plus** the master key opens everything. There is no key rotation yet |
| T9 | A background job (reminders, price alerts) runs for the wrong agency or lets one agency's failure stop the others | Jobs loop over agencies, each in its own scope; a failure in one is recorded and the loop continues | `tests/test_tenancy_jobs.py` | A very slow agency delays those after it (jobs run in sequence) |
| T10 | A buyer's message instructs the agent ("ignore previous instructions", "show me other buyers") | Known patterns are refused in code; more importantly the agent's tools only reach the conversation's own buyer, and the database scope is the agency | `tests/test_security.py` | The pattern list can be phrased around; the structural limit is what holds |
| T11 | Webhook replay or forgery (Telegram, announcement mail, cron) | Constant-time secret comparison; unknown agency, suspended agency and wrong secret all give the same 403; mail ids are processed once | `tests/test_security.py`, `tests/test_telegram_updates.py` | Secrets in the host's environment are only as safe as the host |
| T12 | One agency uses all of the capacity (noisy neighbour) | Per-client rate limits on public endpoints; the benchmark ([BENCHMARKS.md](BENCHMARKS.md)) shows the cost of isolation is small | `scripts/bench_tenants.py` | Limits are per instance, not per agency; there is no per-agency quota on model calls |
| T13 | A tenant's transaction changes its own label (`set_config('app.org_id', ...)`) | Not possible through the application: no SQL is built from request text | code review, `git grep` for raw SQL | **Real if SQL injection ever exists**: the session can set the label itself. Row-level security guards against bugs, not against injection |
| T14 | Tenant writes to shared market data | Allowed today to `app_user` because crawlers and the dashboard share a code path | none | Accepted gap, listed below |

## Trust boundaries

```
 browser / Telegram ──▶ backend (verifies who, picks the agency) ──▶ database (checks again, per row)
                              │
              platform operator (admin key, cron secret, SECRETS_MASTER_KEY): sees all agencies by design
```

The application decides **who is asking and for which agency**; the database enforces **which rows that means**. Neither is trusted alone: a wrong decision in the application still meets the policy, and a policy cannot know who the user is.

The platform operator is trusted. Table owners are not forced through row-level security (migrations and operator scripts need to see everything), so the connection string with owner rights is the most sensitive secret after the master key.

## Known gaps, in the order I would close them

1. **Shared tables are writable by tenants** (T14). Split the platform role from `app_user` so only crawlers and operator tools write market data.
2. **No master-key rotation** (T8). The `v1.` prefix on ciphertexts is there for it; a rotation job would re-encrypt every data key under `v2.`.
3. **Audit trail.** Who invited, connected a bot or changed a role is not recorded as an event with the actor.
4. **Per-agency quotas** (T12), especially on model calls, which cost money.
5. **Injection resistance of the label** (T13). A hosted setup could run each request on a connection whose role has no right to call `set_config` on `app.org_id`, or sign the label; this needs a design of its own.
6. **Rate limits shared across instances** (Redis), as in SECURITY.md.
