# Design decisions

Short records of choices that are not obvious from the code, with the reason and what was given up.

## Embeddings: `gemini-embedding-001` at 768 dimensions, not `text-embedding-004`

The specification names `text-embedding-004`. That model is being retired, so a project started now would break on a fixed date.
`gemini-embedding-001` accepts `output_dimensionality=768`, which keeps the schema (`vector(768)`) and the HNSW index exactly as
specified. The similarity threshold (0.64) was tuned on this model, so changing models later means re-running `scripts/tune_threshold.py`.

## Dashboard live updates: our own event stream, not Supabase Realtime

Supabase Realtime publishes row changes. The dashboard needs more than rows: which graph node is running, how long it took, which
model answered, when a proactive message went out. Those are events, not table changes, and they must reach the browser through the
server (the browser never holds a database key). So the backend writes events to `event_log` and streams them over Server-Sent
Events. The Realtime publication on `chat_messages` stays in the migrations as an option; nothing depends on it.

**The stream reads the table by polling, not LISTEN/NOTIFY.** The first version used LISTEN/NOTIFY. psycopg's `notifies()` does not
honour task cancellation here, which made shutdown hang about one run in three, and a dedicated listening connection is also not
something a pooled database promises to keep alive. Polling `event_log` by id every 0.5 s (and immediately after the instance's own
writes) costs one indexed query per instance and needs no special connection. The trade-off is up to 0.5 s of delay for events written
by another instance.

**Events are delivered strictly in id order, waiting out gaps (found and fixed 2026-11-02).** Reading "every id after the last one
seen" lost events: ids are taken at insert but rows appear at commit, so when two instances wrote at once, id 8 could be read before
id 7 committed, and 7 was never read. The test with two instances failed about 2 runs in 5 because of it, and in use a dashboard tab
would silently miss an event. The stream to the browser, a tab resuming with Last-Event-ID and the dashboard's own "newer than" check
all assume ids arrive in order, so the fix is in the bus: a missing id holds back the later events until it appears, or for at most
3 seconds (`gap_grace`), after which it is taken to be an insert that was rolled back. A tab opening during a gap gets a backlog that
stops before it. Each instance starts after the last id handed out, so ids used before it started are not waited for. Tests reproduce
the loss with an insert held open, and the two-instance test passed 50 runs in a row after the fix.

## Tests run on an embedded Postgres, not Docker and not the hosted database

The suite used to run against the hosted Supabase database: 23 minutes, flaky when the network was slow, and sharing data with real
use. Docker Desktop needs several GB of disk and a running engine; neither could be assumed. `pgserver` ships PostgreSQL 16 with
pgvector as a pip package, so `pytest` works on a clean checkout with no services. The seed uses stored embeddings
(`tests/fixtures/seed_embeddings.json`), so building the database needs no model quota. CI can point `TEST_DATABASE_URL` at a
pgvector service container instead.

## Reading button taps in code

Buttons send fixed sentences ("Tell me more about option 2", "Book Tue 03 Nov, 10:00"). Those are recognised with exact patterns, so
a tap costs no model call and cannot be misread. Anything that does not match goes to the model as before. The button data is the
sentence itself (at most 64 bytes), so a tap needs no stored state and shows in the transcript as what the buyer chose. A time offered
for booking remembers which options it was for (`pending_booking`), so tapping a time books the unit chosen earlier.

## Developer reliability: a capped reward, not a re-sort

Search results are ordered by similarity plus `0.05 x reliability`. The reward can settle a near-tie in favour of the developer who
delivers, but a match that is more than 0.05 better always stays ahead. An earlier version bucketed scores into bands; two matches
0.006 apart could land in different bands and be ordered by the wrong rule, so it was replaced.

## Payment plans

A plan is `{installments, interval_months, handover_pct}`, stored on the unit if it differs from its developer's usual terms and
falling back field by field to the developer's, then to a generic plan. Schedules are computed in code and always sum to the price
exactly. They are the developer's advertised terms, not offers: the brochure says so, and payment dates assume signing a week after
the visit.

## Announcement emails

The mail service posts to `/api/webhooks/announcement` with a shared secret. Each message id is processed once (a redelivery gets
`duplicate`). The sender is matched to a developer by exact sales address, then by company domain if exactly one developer uses it; a
shared domain such as gmail.com matches nobody, and unknown senders are recorded and ignored. An unusable attachment never blocks the
announcement itself.

## Brochure fonts

PDFs use the built-in Latin-1 fonts, so characters outside that range (Amharic) are replaced by `?` rather than crashing. The HTML
brochure and the chat show them correctly. A bundled Unicode font for Amharic PDFs is planned with the rest of the Amharic work.

## The lead score is a list of earned signals, not a number

Each point comes from one named signal (gave a name, shared a phone number, stated a budget, asked to visit...). Signals are kept once
earned, so a buyer who asked to visit does not lose interest by saying thank you, and the score only ever rises. The earned signals are
stored with the buyer, and every rise is written to `lead_score_history` with what raised it. That is what lets the CRM answer "why
62?" and draw how the score grew. Buyers scored before this existed keep their old score; the panel says how many points have no
recorded reason instead of guessing.

## What the inspector shows, and what it hides

Every model request is logged (model, latency, tokens, fall-backs) and every tool call (search, calendar, routing, email, Telegram) is
reported with its arguments and a summary of its result. Arguments are summarised on purpose: message bodies become a length, email
addresses are masked, embeddings become their size, long lists become a count. The inspector therefore shows what the agent decided
and how long it took without turning the event log into a second copy of every buyer's messages. Events are kept 30 days.

## Mapbox on the page: a public token, with a drawn fallback

The browser needs a token, so the page accepts only a public one (`pk.`); a secret token is never put in the bundle. Without a token,
or if Mapbox refuses it or is slow, the page falls back to the original drawn map, so the dashboard never shows a blank box. Units that
are close together gather into numbered clusters; zooming or clicking opens them. While a tour is chosen the map shows only that visit's
units, its route and numbered stops.

## Chart colours were validated, and one set failed

The three developer colours were checked with the palette validator in light and dark. The light set passed. The original dark set failed
the lightness band (the amber was too light to sit evenly beside the blue and purple), so the dark colours were re-stepped
(`#4E86EE`, `#C98009`, `#9D63E0` on the `#141921` surface) and now pass for colour-blind separation and contrast. Charts use one hue
for magnitude, thin bars with rounded ends, values at the bar tip, hover details, and a "view as table" for every chart.

## Analytics are counted in the database, in parallel

Every figure on the Analytics page is a query over tables the system already keeps. The queries run side by side, which took the page
from 8.8 s to 3.5 s against the hosted database. The numbers are honest about the free tier: the page reports how often a model request had
to fall back to another model and how long replies really take.

## Reset demo is off unless asked for, and asks twice

Resetting wipes every buyer, conversation, booking, deal and log, restores prices, removes scraped units and cancels the calendar events
the system created. The endpoint refuses unless `DEMO_RESET_ENABLED=true` is set, the dashboard only shows the button then, and the
dialog needs the word RESET typed. It is meant for a demo instance, never for real data.

## Two ingestion strategies, because the pages are two different problems

Small developer sites are unpredictable and publish projects, not prices, so a language model reads their page text and nothing needs maintaining.
Large portals publish thousands of pages in a stable shape, so reading each with the model would use the free quota in a morning. Those go to a
Scrapy crawler that extracts locally first (JSON-LD, the page's embedded JSON, per-site CSS hints, OpenGraph) and asks the model only when no
strict price is found, at most three times per batch of 25. Both paths end in the same `IngestionService.apply`, so unchanged units cost nothing,
price changes are recorded for the alerts, and only new units are embedded.

## A "strict price" is a rule, not a guess

A price counts only if it comes from structured data, is in birr, falls between 300,000 and 20 billion, and works out to at least 3,000 ETB per m².
Loose page text is never used for price (price filters and adverts live there), and a size read from loose text must fit the price. The last two
rules were added after the first real crawl saved a 700 m² villa at 640,000 ETB, almost certainly a rent. Refusing a unit costs a skipped row with
a stated reason; accepting a wrong one costs a buyer's trust.

## The crawler runs on GitHub Actions, and is controlled from the dashboard

The free Render service has 512 MB of memory and 0.1 CPU and sleeps, while a full crawl of one portal takes hours (Property Centre has about 4,800
sale listings at a polite pace). GitHub Actions is free compute with a six-hour limit per job, so each portal is its own job. The broker should not
have to open GitHub: the dashboard asks the backend, the backend (which alone holds the token, limited to Actions on this repository) asks GitHub,
and the runner reports each portal's start and finish back, so the Portals page and the Telegram alerts need no polling of GitHub.

## Politeness is a hard rule, and a bot check is a stop sign

robots.txt, an honest user agent, a randomised delay, one request at a time per site. A 403, 429, 503 or a bot-check page (Cloudflare and similar)
ends the crawl of that site and tells the broker; it is never worked around. EthioHome's robots.txt forbids AI use, so its pages are read locally
and never sent to the model. One small developer site currently serves a bot check; it is reported, and its listings are added by hand.

## A vanished listing is only retired after a complete, clean crawl

Retiring units on a partial crawl would wipe inventory whenever a site hiccups. So retirement happens only after a full crawl with no cap, no
date filter, no push-back, no failed page and every requested page answered, and the backend still refuses if fewer than half the known units
were seen. A page that is gone (404 or 410) counts as learned; a page that errored does not.

## A buyer booking a portal unit is never sent to the portal

The point of the product is the broker's commission, and a link to another agent's listing hands the lead away. A portal unit has no developer
to register a lead with, so no email is sent, no commission is assumed (portal developers carry a 0% rate, which also keeps the commission
pipeline honest), and the broker gets one Telegram message with the buyer's details and the original link; the calendar entry carries the link too.

## Alerts are a state change, not a stream

The broker is told when a run ends in a state a person must look at: blocked, failed, a full crawl that read nothing, or fewer than 70% of at least
ten pages priced locally (the usual sign that a site changed its layout). A run started by hand sends a one-line summary; a scheduled run only
when something changed. Each notice is reserved before it is sent, like every proactive message, so it cannot be sent twice.

## Retrieval is hybrid on purpose

Hard SQL filters (available, budget, bedrooms, area) run before similarity, because a buyer's budget is not a preference. Cosine similarity then
ranks with a floor of 0.64, tuned on the seed data so known non-matches score at most 0.606 and real matches at least 0.666. "The cheapest" and
"the largest" are deterministic rankings with no embedding, since they are rules and not descriptions. When nothing fits, a bounded loop widens
the search by rule and says so; the buyer's stored preferences are never changed by a search.

## Buyers and the broker use two doors into one backend

The broker's console reaches the backend through a proxy that adds the admin key. Buyers never get that route: `/api/me/*` forwards the buyer's own Supabase
token, and the backend checks it itself (the public signing keys at `SUPABASE_URL`, or the shared secret for older projects; the algorithm is only trusted
if it is on the list for that kind of key). Every query is keyed by the user id inside the token, so a buyer can read and change only their own saved homes
and chat. Listing responses are built from an allow-list: no developer email, commission rate, reliability score, content hash or embedding. A test fails if
one of those names ever appears. The admin key is not a buyer's sign-in, and a buyer's token does not open the broker API.

## The broker is the confirmed account with BROKER_EMAIL (superseded by organization membership, below)

A role is worked out from the sign-in: the old password cookie means broker; otherwise a Supabase account whose email equals `BROKER_EMAIL` **and which the
auth server says is confirmed** is the broker; any other account is a buyer. Anyone can type the broker's address into the sign-up form, so the address alone
proves nothing; only that one account is checked against the auth server (once a minute per sign-in), everyone else is verified from the token's signature
alone. "Confirm email" must stay on in Supabase. The password login stays behind a "Broker access" link so the console is never locked out by the auth service.

## One pure function decides who may open what, and it denies by default

`lib/access.ts` maps a path and a role to allow, redirect or deny (401 or 403 for API paths). The middleware only applies the answer. Public: `/`, sign-in and
sign-up pages, `/auth/*`, `/api/auth/*`, `/api/public/*`. Buyer area: `/portal/*` and `/api/me/*` (the broker may look too). Everything else, including any path
added later by mistake, is broker-only. Signed-in people are sent past the sign-in pages; `?next=` only follows paths on this site.

## The console moved to /console so that / can be the front door

The route group became a real folder. Nothing else about the console changed.

## A buyer's web chat is one conversation per account

The thread is `web:<Supabase user id>`, so history follows the buyer across devices, and the broker's takeover, notes and CRM record work as they do on Telegram.
The account's name and email fill the CRM record only where it is empty, so what the agent learned in conversation is never overwritten. "Ask the assistant
about this home" reuses the channel-ad deep links (`ask`, `cmp`, `book`), so the agent opens exactly that unit instead of guessing from words; `shown_as` keeps
the transcript in the website's wording.

## Rejected buyer sign-ins must not lock the broker out

The website's server is one client to the backend. Counting an expired buyer token as a failed admin key would block the console for a minute. `/api/me/*` is
therefore left out of the failed-key counter and the block, and `/api/public/*` has the public rate limit instead.

## The landing page is rendered on request, and shows real but limited data

A prerendered page keeps whatever the build could reach: with the backend down at build time, an empty fallback would be baked in. The page renders per request,
with the featured homes cached for a minute by the fetch. Visitors who are not signed in see six homes spread across developers (price, area, size, cover), the
live counts, and areas; descriptions, links and details wait for an account. No testimonials or claims of verification are invented.

## Homes without photographs get a drawing, and bad coordinates are ignored

A unit's cover is its project's uploaded photo, else the developer's picture, else a line drawing of the kind of home in a quiet colour fixed by its id. About one
listing in a hundred is mis-geocoded (one Lebu home sits in Texas) and many share an identical point, so the map ignores locations outside greater Addis Ababa
and the drawn fallback shows one pin per spot ("16 homes"). The fix at the source belongs in the geocoder.

## Fonts are part of the repository

Fraunces and Inter (SIL OFL, latin only) are kept in `frontend/src/fonts`. The build once failed because Google's font host timed out; self-hosting removes that,
and visitors' browsers never contact Google.

## Who may use the console is a membership, not an email address

Multi-tenancy replaced the single `BROKER_EMAIL`. A person belongs to organizations through the `memberships` table with a role (viewer, agent, admin, owner).
The backend decides on every console request (`app/access.py`): it verifies the person's token, finds their membership for the organization named in
`X-Org-Id` (or their only one), refuses a stranger with the same answer whether the organization exists or not, and checks the role. Reads need viewer, writes
need agent, setting up shared market data needs admin, and wiping the demo needs owner. The request then runs inside `use_tenant(...)`, so row-level security
confines every query to that organization.

Why: an address anyone can type into a sign-up form is a weak identity, and one address cannot describe several agencies with several teams. Membership is created
only by an owner's invitation (a link that works once, for the invited address, with only its hash stored) or by an operator (`scripts/grant_role.py`), so there is
nothing to guess or squat. An organization always keeps at least one owner (a database trigger, not application code). The admin key stays as the platform secret
for the cron jobs and the operator's password session; it acts as owner of the organization it names, or of the original one.

Known limits: invitations are returned to the inviter instead of being emailed; roles are checked by route (read/write by HTTP method, a few routes stricter), not
per field; the organization a buyer talks to on the public site is still the original one until buyers choose an agency by address.

## Each agency brings its own channels, behind the names the code already uses

About thirty places read `settings.broker_name`, `settings.broker_telegram_chat_id`, `services.telegram`, `services.calendar` and so on. Passing an
organization through every one of them would have touched most of the codebase and been easy to get wrong. Instead `app/runtime.py` hands them
`TenantSettings` and a `Services` made of proxies: inside `runtime.scope(org)` every read or call resolves to the organization in scope, outside it to the
original configuration. Scope is a context variable, so a Telegram update processed in a background task keeps the agency it arrived for.

Rules that follow from it: an agency without a bot or calendar gets a silent stand-in, **never the platform's own** (a buyer of one agency must not be messaged
through another's bot); the demo's fallback chat and email are the platform owner's and are switched off for every other agency; the original agency keeps the
platform's configured channels, so nothing changed for it.

Credentials (`org_secrets`) are encrypted with AES-GCM under a per-agency data key, which is itself encrypted under a master key held only in the server's
environment (`SECRETS_MASTER_KEY`). Each value is bound to its agency and name as associated data, so copying a ciphertext to another row makes it fail to open.
`app_user` has no grant on the key or secret tables: they are read only on the platform path, after the caller's role is checked. The API never returns a
token, and a connect request proves the token with Telegram's `getMe` before storing anything.

Conversation memory: the agent's checkpoint tables have no organization column, so each conversation is stored under `org:<id>:<thread>` (the original agency keeps
bare ids, so its saved chats still work). Events and model-call rows carry `org_id`, and a console tab's live stream is filtered to its own agency.

Jobs: the cron secret runs every job once for each active agency, each inside its own scope; a failing agency is reported and never stops the others. A price drop on
a shared listing is matched against each agency's own buyers and sent through that agency's own bot, once per agency. The market scraper writes shared data, so
it runs once. A signed-in agent can start the jobs for their own agency from the console; an admin can start the market scraper.

Known limits: Telegram long polling serves only the original agency (others use the webhook); the public site picks an agency by an `X-Agency` header, and a
page for choosing one is still to be built; a master-key rotation is not automated (values carry a version prefix to allow it); calendar credentials are stored
and used but the Google connection itself is not tested against Google here.

