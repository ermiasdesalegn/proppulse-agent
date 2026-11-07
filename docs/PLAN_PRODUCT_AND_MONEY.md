# Plan: make the platform productive for buyers and brokers, and pay for itself

Status: **phase 2 built locally as a demo (mock payments, migration 038, see section 3), not deployed;** **all of phase 1 built locally on 2026-11-07 (items 2 to 5, migrations 035 to 037), not deployed;** the **first slice** was also built locally the same day: per-agency commission rates (phase 1, item 1, migration 034, `/console/terms`), operator plan and status controls (phase 2, item 1, `/console/operator`), and the audit export scheduled in the cron workflow (phase 4). Everything else is still a proposal. Written 2026-11-07 after the multi-agency platform went live (migrations 001 to 033). Sizes are relative (S about a day, M a few days, L a week or more of focused work). Every database change is a new numbered migration, added in the expand-then-switch way described in [MIGRATION_HOSTED.md](MIGRATION_HOSTED.md), and every phase ends with the same checks (ruff, the backend suite, the frontend suite, a rehearsal on a scratch database, then one deploy).

## 0. What is true today (so the plan starts from facts)

- **Buyers** can browse and filter 581 shared listings, use the map, save homes, chat in English and Amharic, book visits and calls, and get price-drop alerts (Telegram and web).
- **Brokers** (agency members) get a CRM, deal board, tours, analytics, their own Telegram bot, own unit prices, a team with roles, and a **commission ledger** with deal stages up to COMMISSION_PAID and a lead-registration email to the developer.
- **Money for the platform:** none. Plans (free, pro, business) are only caps; nothing charges for them and the console has no way to change a plan.
- **Gaps found:** the commission rate sits on the shared developer record, so an agency cannot set its own; there is no proof of a sale, no claim to a developer, no reminders for old pending commissions, no deal assignment or split, no export; buyers have no "my visits" page or alert settings.

## 1. Decisions only the owner can make (needed before Phase 2)

| # | Decision | Options | Why it matters |
|---|---|---|---|
| D1 | How the platform earns | (a) monthly subscription per plan, (b) a small share of each closed deal, (c) both, (d) developer-paid promotion later | Changes what is built first; (b) needs Phase 1 to be trustworthy |
| D2 | Prices | amounts per plan in ETB; whether a plan is monthly or yearly | Needed for the pricing page and invoices |
| D3 | Payment provider | a local provider that can take ETB (for example Chapa or Telebirr; their API terms have not been checked) | Drives Phase 2 design and any registration paperwork |
| D4 | Deal share | percentage of the agency's commission that the platform takes, and whether it applies on the free plan | Needs a legal and tax view (VAT, invoices) outside this plan |
| D5 | Who may start an agency | anyone (today), or approval by the owner, or invite code | Controls abuse and quota cost |

## 2. Phase 1: commission integrity (Size L). Why first: it is what brokers need to get paid, and what makes a deal share verifiable.

1. **Per-agency commission terms (M).**
   - Table `agency_developer_terms (org_id, developer_id, rate_pct, valid_until, note)` with row-level security like `agency_prices`; a view or function `effective_commission_rate(developer)` that prefers the agency's own term and falls back to the developer's shared rate.
   - Ledger creation and analytics read the effective rate. Console page "Developers" to set terms.
   - Tests: two agencies with different rates see their own expected commission; a third falls back to the shared rate.
2. **Deal documents (M).** Attach a contract, receipt or the developer's acknowledgement to a ledger entry.
   - Private storage (a private bucket, never public URLs), size and type limits, who may read (agency members only), listed in the audit trail.
   - Risk: documents hold personal data, so retention and deletion rules are needed. Start with PDF and images, 10 MB each.
   - **Storage decision (2026-11-07): a private Supabase Storage bucket, reached only by the backend.**
     - The bucket is private. The backend uploads, reads and deletes with the service role key through the authenticated object endpoints (never `/public/`), and the console downloads a file through `GET /api/ledger/{id}/documents/{doc}` after the caller's membership is checked. There are no signed or public URLs to leak or expire.
     - Object key `<org_id>/<deal id>/<document id>.<ext>`, built by the server from the agency in scope; the browser never names a path. The database holds only an index row (`deal_documents`, migration 037, row-level security), so one agency's rows cannot list another's.
     - PDF, PNG, JPEG and WebP only, judged by the file's first bytes (not its name or label); 10 MB each; 20 per deal. The download answers `attachment`, `nosniff`, `no-store`.
     - Agent role or higher uploads and deletes; every role of the agency may read. Every upload and delete is in the audit trail (reads are not, as for every other record). Deleting removes the object first, then the row; a failed storage delete keeps the row so nothing is orphaned silently.
     - Retention: documents stay until a member deletes them or the agency is deleted (the rows go with the agency; the objects under its folder must be removed by the operator, which is not automated yet). A buyer's erasure request (phase 4) must include their deals' documents.
     - Setup: create a private bucket in Supabase (for example `deal-documents`), set `DEAL_DOCUMENTS_BUCKET` on the backend (it already has `SUPABASE_URL` and the service role key). Until then the console says storage is not set up and refuses uploads. `DEAL_DOCUMENTS_DIR` is a local-folder stand-in for development only; it must not be set on Render (its disk is lost on restart).
   - **Built locally 2026-11-07** (migration 037; `GET/POST /api/ledger/{id}/documents`, `GET/DELETE .../{doc}`; Documents on each Deal Board card). Tested against the local folder and a fake HTTP transport for the Supabase calls. **Only a real bucket confirms:** that the bucket really is private, that the service key may write to it, the object endpoint paths and the real size limits, and that uploads of several MB pass through the Vercel proxy (a Vercel function may cap request bodies near 4.5 MB, which would refuse larger files before the backend sees them).
3. **Commission claims and reminders (M).**
   - Ledger statuses gain: claim sent, acknowledged, disputed. A "Send claim" action emails the developer's sales contact from the agency's name with the deal facts and attachments.
   - A daily job (the existing queue) flags pending commissions older than N days and notifies the owner.
   - **Built locally 2026-11-07** (migration 035; `POST/PUT/GET /api/ledger/{id}/claim`; the daily job `commission.overdue`, N = `COMMISSION_OVERDUE_DAYS`, default 14, cron at 03:45 UTC; Send claim and Claim details on the Deal Board). The claim is a second state on the ledger row, so the deal stages are unchanged. A claim emails the developer's sales contact from the agency's name with the member's address as reply-to; without a configured email provider the console says so and records nothing. Attachments in the claim email wait for item 2 (documents).
4. **Deal assignment and split (M).** `assigned_to` (a member) on a buyer and ledger entry; an optional split table (member, percent, sums to 100). The deal board filters "mine".
   - **Built locally 2026-11-07** (migration 036; `PUT /api/buyers/{id}/assignee`, `PUT /api/ledger/{id}/assignee`, `PUT /api/ledger/{id}/split`; `GET /api/commission/earnings.csv` is the who-earned-what export, and the main export gains Assigned to and Split columns; Show mine, Assign to and Split on the Deal Board). Assigning a buyer moves its open deals that were with the previous owner; new deals inherit the buyer's owner. The 100 percent rule is enforced by the database at commit as well as by the API. Members are shown as You or a short id because the platform stores no member names or emails yet.
5. **Export (S).** CSV of the ledger and deals for accounting. **Built locally 2026-11-07** (`GET /api/commission/export.csv`, agent role or higher, Export CSV button on the Deal Board; no migration).

Acceptance: a broker can show, for any closed deal, the agreed rate, the documents, the claim and its status, and who earned what.

## 3. Phase 2: platform revenue (Size L). Starts after D1 to D4.

1. **Operator plan controls (S).** Console page for the operator: list agencies, plan, status, usage; change plan and suspend, each written to the audit trail. This is also the manual way to bill before automation exists.
2. **Subscriptions (L).**
   - Tables `subscriptions` and `payments` (platform-owned, no grant to the agency role); a "Billing" page for the agency owner: current plan, upgrade, receipts.
   - Provider checkout and a signed webhook that confirms payment, with idempotency keys and a reconciliation job. A plan changes only on a confirmed payment; failed or expired payments return the agency to the free caps after a grace period.
   - Tests with a provider stand-in: success, duplicate webhook, forged signature, refund, expiry.
3. **Deal share (M, only if D1 includes it).**
   - When a ledger entry reaches COMMISSION_PAID, write a platform fee row (fee percent from D4) and include it on the next invoice. Because Phase 1 stores documents and claims, the fee can be checked, and an agency cannot hide a paid deal without a missing-document flag.
   - Risk: agencies can mark a deal lost to avoid the fee. Mitigation: documents, audit trail, and a contractual clause (outside this plan).
4. **Usage overage (S, optional).** Charge or block when the monthly model budget or message cap is reached, as the quotas already measure.
5. **Invoices (M).** Numbered invoice per period, sent by email (needs the Resend domain, Phase 4).

### Phase 2 built as a DEMO (2026-11-07, migration 038, not deployed)

The owner cannot decide D1 to D5 yet and legal and payment integration are not possible, so a small demo flow was built. **Nothing moves real money, and every billing screen says "DEMO MODE: no real money moves".**

Demo defaults, **to be confirmed by the owner** (all in one table, `plan_prices`, migration 038; change a row, no code):

| Decision | Demo default |
|---|---|
| D1 how the platform earns | both: a monthly plan and a share of paid commission |
| D2 prices (ETB per month, placeholders) | free 0, pro 2,500, business 9,000 |
| D3 provider | `MockPaymentProvider` in `backend/app/billing.py`: accepts every charge it is asked for; the buyer's choice is the checkout's Pay / Cancel buttons |
| D4 deal share (placeholders) | percentage of the agency's expected commission when the deal reaches COMMISSION_PAID: free 10%, pro 5%, business 2% |
| D5 who may start an agency | anyone, as today |

What exists:
- `/console/billing` (owner buys, admin looks): current plan, plan cards with their limits and an Upgrade button, usage against the caps, "Platform fee on your paid deals", and a list of demo invoices.
- Upgrade opens `/console/billing/checkout/<plan>` (plan, price in ETB, **Pay (demo)** and **Cancel**). Pay changes the plan at once (so the quotas change with it), writes one receipt (`invoices`, numbered DEMO-00001...) and an audit entry `billing.upgrade`. Cancel only goes back: nothing is written. Only a higher plan can be bought; a second click makes no second receipt.
- `platform_fees`: a database trigger on `commission_ledger` writes one row, once per deal (unique on the deal), when it first reaches COMMISSION_PAID, with the percentage of the agency's plan at that moment. It works for every path that moves a deal.
- The operator's All agencies page shows each agency's demo plan revenue and demo fees and the totals (read-only).
- Tests: `backend/tests/test_demo_billing.py`, `frontend/src/lib/billing.test.ts`.

**Later (deliberately not built; a real provider needs all of it):**
- a real provider behind a `PaymentProvider` interface (create checkout, confirm, refund, verify webhook) such as Chapa or Telebirr, with its registration, terms and keys;
- the provider's signed webhook, with idempotency keys on payments and forged-signature refusal; a plan should change only on a confirmed payment;
- refunds, a grace period and a daily job that returns a lapsed agency to the free caps;
- a monthly invoice job with numbered invoices per period and line items (the plan plus the period's fees), sent by email; the demo invoice is only a receipt for one plan purchase and fees are shown, not billed;
- an operator billing API and page (revenue per agency over time, fees outstanding, collections);
- legal and tax: VAT, proper invoices, the contract clause for the deal share (D4), and the decision to let agencies cancel or downgrade (the demo has no downgrade).

Acceptance: an agency can upgrade from the console, pays, and gets the higher caps without anyone touching the database; the owner sees revenue, per agency, in one page.

## 4. Phase 3: buyer experience (Size M)

1. **My visits (S).** A portal page listing a buyer's bookings with reschedule and cancel (reusing the booking service and its calendar checks).
2. **Alert settings (S).** Turn price-drop and new-listing alerts on or off per area and budget; unsubscribe link.
3. **Trust signals (M).** Listing freshness date, source, and the agency's profile card (name, logo, contact) on its public page.
4. **Coverage message (S).** The bot and website state that stock is in Addis Ababa and which areas have the most choice (the bot already says this; add it to the portal).
5. **Buyer email (S, depends on Phase 4).** Booking confirmations and reminders reach the buyer.

## 5. Phase 4: operations that unblock the rest (Size S to M)

- Verify a sending domain in Resend; set Supabase's custom SMTP (removes the 2 emails an hour limit) and `EMAIL_FROM`.
- Schedule `/api/cron/audit-export` in the cron workflow, install `boto3`, pick an object-lock bucket.
- Re-record the replay eval and fix the three date-boundary tests.
- A welcome checklist for a new agency's console (connect a bot, set prices, invite a team).
- Per-agency GDPR-style export and delete of a buyer on request.

## 6. Order and dependencies

```
Phase 4 (domain, SMTP, export job)   -> can start now, unblocks emails
Phase 1 (commission integrity)       -> can start now, needs no decision
D1-D5 (owner decisions)              -> before Phase 2
Phase 2 (revenue)                    -> after Phase 1 and D1-D4
Phase 3 (buyer experience)           -> any time; item 5 after Phase 4
```

Suggested first slice (one deploy, about a week): Phase 4 operations, Phase 1 item 1 (per-agency commission terms), Phase 2 item 1 (operator plan controls). That gives agencies their own rates, gives you a way to manage plans, and removes the email blocker, without taking money yet.

## 7. Risks and what this plan does not cover

- **Legal and tax:** charging in Ethiopia (provider registration, VAT, invoice rules) and the terms between you, agencies and developers are outside the code and need a professional view.
- **Developer data:** listings are collected from developers' sites and portals; their terms and permissions should be checked before charging agencies for access to them.
- **Shared Gemini quota:** more paying agencies need paid model keys, or a per-agency key option.
- **Fraud and abuse:** open agency signup (D5) plus free caps means someone can create many agencies; rate limits or approval will be needed before public launch.
- **Scale:** the benchmarks cover tens of agencies on one database; hundreds need the checks in [BENCHMARKS.md](BENCHMARKS.md) repeated.
