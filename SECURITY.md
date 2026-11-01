# Security notes

What the system protects, how, and what it does not do. Report problems to the repository owner privately.

## Who can do what

| Surface | Protection |
|---|---|
| Console (`/console`, Next.js) | One password, checked on the server; a signed, expiring session cookie; login attempts are rate limited; or a Supabase account that is a member of an organization (the backend's `memberships` table decides, per request, and a role limits what the member may change). Every page and API route sits behind middleware that denies by default |
| Buyer area (`/portal`, `/api/me/*`) | A Supabase sign-in. The backend verifies the token's signature, audience, issuer and expiry itself and keys every query by the user id inside it; responses use a field allow-list (no developer contacts or commission data); anonymous sign-ins are refused |
| Public site (`/`, `/api/public/*`) | Open and read-only; six homes with limited fields; rate limited per client |
| Backend admin API (`/api/*`) | `X-Admin-Key` header compared in constant time; the key never leaves the dashboard's server (the browser talks to a proxy that adds it) |
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

## Known limits

- Rate limits are kept in each instance's memory. With several instances a client gets the limit per instance; a shared store (Redis) would make it exact.
- One admin password and key for the console, no audit trail of who clicked what. Buyer accounts exist, but there is a single broker.
- Console access no longer depends on an email address: nobody becomes a member by signing up with a particular address. Membership is created by an owner's invitation (the link works once, for the invited address, and only its hash is stored) or by an operator with `python -m scripts.grant_role`. Keep "Confirm email" on anyway, so an invitation's address belongs to whoever uses it.
- Buyer sign-up has no CAPTCHA; Supabase's own rate limits apply.
- The prompt-injection check is a list of patterns. It stops the common attempts and costs nothing, but a determined attacker can phrase around it; the structural protection is that the agent's tools only act on the buyer's own conversation.
- Telegram messages are not end-to-end encrypted; buyers' phone numbers and emails are stored in the database.
