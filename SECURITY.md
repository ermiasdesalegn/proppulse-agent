# Security notes

What the system protects, how, and what it does not do. Report problems to the repository owner privately. For several agencies on one platform, [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) goes through each threat and its test.

## Who can do what

| Surface | Protection |
|---|---|
| Console (`/console`, Next.js) | One password, checked on the server; a signed, expiring session cookie; login attempts are rate limited; or a Supabase account that is a member of an organization (the backend's `memberships` table decides, per request, and a role limits what the member may change). Every page and API route sits behind middleware that denies by default |
| Buyer area (`/portal`, `/api/me/*`) | A Supabase sign-in. The backend verifies the token's signature, audience, issuer and expiry itself and keys every query by the user id inside it; responses use a field allow-list (no developer contacts or commission data); anonymous sign-ins are refused |
| Public site (`/`, `/api/public/*`) | Open and read-only; six homes with limited fields; rate limited per client |
| An agency's credentials (bot token, calendar key) | Encrypted with a per-agency key under a master key kept only in the server's environment (`SECRETS_MASTER_KEY`); bound to the agency and name; never returned by the API; the restricted database role cannot read them |
| An agency's Telegram webhook (`/api/telegram/webhook/<agency>`) | A secret registered for that bot, compared in constant time; an unknown agency, a suspended one and a wrong secret all answer the same 403 |
| Backend admin API (`/api/*`) | `X-Admin-Key` header compared in constant time; the key never leaves the dashboard's server (the browser talks to a proxy that adds it) |
| Shared market data (crawls, ingestion, developers, prices, project offers and pictures) | Writable only through the platform key or a member of the operator's own agency; every other agency's members get 403 whatever their role, and their database role cannot write those tables at all |
| Cron endpoints | `X-Cron-Secret` (Cloud Scheduler) or the admin key |
| Telegram webhook | Secret token header compared in constant time; refuses everything if no secret is configured |
| Announcement email webhook | Shared secret header; each message id is processed once; unknown senders are recorded and ignored |
| Brute force | A client that sends more than 10 rejected admin keys a minute is blocked for a minute (429 with Retry-After), even if its next key is right |
| Public endpoints | Webhooks and the demo page allow 120 requests a minute per client; `X-Forwarded-For` is trusted only when `TRUST_PROXY_HEADERS=true` (behind Cloud Run), so a client cannot buy itself a fresh allowance by forging it |

## Data

- **Row-level security is on for every table** (the migrations enable it and a test fails if a new table forgets). The backend uses the Supabase service role; the public API key reads nothing.
- Secrets live only in environment variables (Secret Manager in production). `.env`, service-account files and keys are git-ignored.
- The browser bundle contains no server secret: it holds only the optional **public** Mapbox token (`pk.`), which is built for browsers and should be restricted to the site's address in the Mapbox account.
- Event and call logs store summaries, not message bodies; email addresses in tool traces are masked; events are kept 30 days.

## Logs

Logs pass through a redaction filter before any handler sees them: configured secrets, Telegram bot tokens (also inside API URLs), Bearer values, database-URL passwords, Mapbox / Google / Resend key shapes and private keys become `[redacted]`. Each request has an id (`X-Request-ID`) that appears in its log lines. `LOG_FORMAT=json` writes one JSON object per line for a log service.

## Input from outside

- **Scraping:** addresses are checked on every redirect and on every sub-request a rendered page makes; private, loopback and internal addresses are refused; size caps apply; `robots.txt` is obeyed and requests to one site are paced.
- **Listings and announcements** are treated as untrusted text: the model reads them for fields only, values are validated, and a unit that fails validation is dropped.
- **Buyer messages that try to give the agent orders** ("ignore previous instructions", "admin mode") are recognised in code and answered with a fixed message; no booking, search or profile change follows from them.
- **Replies** are checked so that a figure that is not in the facts is never sent; the plain, fact-built layout is used instead.
- **Uploads** (floor plans) are limited to PNG, JPEG and WebP up to 5 MB and the file's first bytes must match its declared type.

## Rotating the master key

Every agency's bot token and calendar key is sealed with that agency's own data key, and only the data key is sealed with the master key.
A rotation therefore re-seals one small row per agency (`org_keys.wrapped_key`) and never touches the secrets. Each sealed data key names
its master key (`v2.<key id>.`; the id is a fingerprint, safe to log). Keys sealed before ids existed (`v1.`) still open.

1. **Make and store the new key first.** `python -m scripts.make_master_key`, and keep the result in two places (a password manager and
   the host's secret store). Losing the new key halfway is as bad as losing the old one.
2. **Load both, newest first.** `SECRETS_MASTER_KEYS="<new>,<old>"` (the old `SECRETS_MASTER_KEY` may stay; it is kept as the older key).
   Restart. From now on new data keys are sealed with the new key, and everything opens with either.
3. **Re-seal.** `python -m scripts.rotate_master_key start` queues one job per agency for the server's workers
   (`run` does them in the command instead, when no server is running). Each job locks the agency's key row, re-seals the data key, checks
   that a stored secret still opens, writes it and adds an audit entry (`key.rewrap`, actor "system"). A crash leaves some agencies on
   the old key; they keep working because both keys are loaded, and running the command again finishes only those.
4. **Check.** `python -m scripts.rotate_master_key --status` (or the Master key card on the operator's Agency page) shows how many
   agencies are on each key. It says when every agency is on the new one.
5. **Remove the old key** from the environment and restart. If any data key still needs it, the server refuses to start and names the
   key and how many agencies need it, so a key cannot be dropped too early without noticing. Put it back and finish step 3.

The old key should be destroyed only after backups made before the rotation have expired: a restored old backup needs it.

## Replacing one agency's own data key

When an agency asks, or its data key may have leaked, only that agency's secrets need re-sealing under a new data key (the master key
stays). The agency's owner calls `POST /api/org/keys/rotate`; the operator calls `POST /api/keys/agency/{org_id}/rotate`. Either queues
the job `keys.rotate_data_key` (shown in the Jobs card) and returns its id. The job locks the agency's key row, opens every secret with
the old data key, seals each with a new random one, checks that each opens again, and commits the new wrapped key, the secrets and an
audit entry (`key.rotate`) together; if anything fails, nothing is kept and the agency stays on its old key. The old data key opens none
of the new ciphertexts. Storing a secret reads the key row under a shared lock in the same transaction as the write, so a bot connected at
the same moment waits and ends up sealed with the new key. The job does not retry by itself: look at the Jobs card and run it again.
A copy of the database taken before the rotation still holds the old key and the old ciphertexts, so they stay readable with the master
key until such backups expire.

## The audit trail

Each agency's entries are chained: an entry stores the hash of the one before it and a hash of its own fields (migration 030). Removing,
editing or reordering an entry breaks the chain at that entry. `python -m scripts.verify_audit` recomputes every chain and prints the
agency and the first broken entry (exit status 1). A chain only proves that something changed. It cannot show that the newest entries
were cut off, or that someone rewrote every hash after an edit. For that, set one sink and the daily job `audit.export`
(`POST /api/cron/audit-export`, scheduled by `deploy/scheduler.sh`) copies each agency's new entries there, in batches that are never
changed afterwards, and `verify_audit` then also compares the database with the copy:

- `AUDIT_EXPORT_DIR`: a directory, for development.
- `AUDIT_EXPORT_BUCKET` (with `AUDIT_EXPORT_PREFIX`, and `AUDIT_EXPORT_ENDPOINT_URL` for a service other than AWS): an S3-compatible bucket.
  Create it with object lock and a default retention (for example 400 days, compliance mode) so a stored batch cannot be replaced or
  deleted, give the server a credential that can only list, read and write new objects, and install `boto3` on the host.

With neither set the export is off and only the chain is checked. Run `verify_audit` on a schedule you control, from a machine that
holds no write access to the bucket. The export remembers how far it got (`audit_export_state`); it refuses to copy past a broken
chain, and the job then fails visibly in the Jobs card.

## Quotas

Each plan caps three things per agency, each counted from that agency's own rows so one agency reaching a cap changes nothing for another:
buyer messages answered per day, a monthly model budget in dollars, and background jobs queued per day (`app/quota.py`). Over the message
cap or the budget the assistant stops answering with the model: the buyer is told the broker will reply, and the message waits in the
broker's inbox. Over the job number `enqueue` refuses, and a cron call skips that agency and names it in its answer. The operator's agency
and the platform's own upkeep jobs (`keys.*`, `audit.export`) are not counted.

Spend is the tokens recorded for each model call times the prices in `MODEL_PRICES` (US dollars per million tokens,
`model=input/output`, `default` for any other model). The defaults are estimates written on 2026-11-02 and not checked against the
provider's list: check them, change the line and `MODEL_PRICES_AS_OF` when the provider changes its prices. A call that reported no
tokens adds nothing and is counted separately on `GET /api/usage`.

## Known limits

- Rate limits are kept in each instance's memory. With several instances a client gets the limit per instance; a shared store (Redis) would make it exact.
- One admin password and key for the console. Changes to roles, invitations, channels and settings are recorded per agency with the actor (`audit_log`, `GET /api/audit`), but reads and other clicks are not. The table owner can still delete or rewrite rows, but not unnoticed: see "The audit trail" below.
- Console access no longer depends on an email address: nobody becomes a member by signing up with a particular address. Membership is created by an owner's invitation (the link is emailed and works once, for the invited address, and only its hash is stored; the response carries it only when no email could be sent) or by an operator with `python -m scripts.grant_role`. Keep "Confirm email" on anyway, so an invitation's address belongs to whoever uses it.
- Buyer sign-up has no CAPTCHA; Supabase's own rate limits apply.
- The prompt-injection check is a list of patterns. It stops the common attempts and costs nothing, but a determined attacker can phrase around it; the structural protection is that the agent's tools only act on the buyer's own conversation.
- Telegram messages are not end-to-end encrypted; buyers' phone numbers and emails are stored in the database.
- Losing `SECRETS_MASTER_KEY` makes every stored credential unreadable (agencies reconnect their channels); exposing it together with a copy of the database would expose them. Keep it in the host's secret store, not in the repository or the database.
- Code that can run arbitrary SQL could change the organization label of its own transaction; the database protects against bugs and wrong queries, not against SQL injection. There is no raw SQL built from user input in the application.

