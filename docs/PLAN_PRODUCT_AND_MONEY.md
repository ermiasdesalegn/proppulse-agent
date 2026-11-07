# Plan: make the platform productive for buyers and brokers, and pay for itself

Status: **proposal, nothing built.** Written 2026-11-07 after the multi-agency platform went live (migrations 001 to 033). Sizes are relative (S about a day, M a few days, L a week or more of focused work). Every database change is a new numbered migration, added in the expand-then-switch way described in [MIGRATION_HOSTED.md](MIGRATION_HOSTED.md), and every phase ends with the same checks (ruff, the backend suite, the frontend suite, a rehearsal on a scratch database, then one deploy).

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
3. **Commission claims and reminders (M).**
   - Ledger statuses gain: claim sent, acknowledged, disputed. A "Send claim" action emails the developer's sales contact from the agency's name with the deal facts and attachments.
   - A daily job (the existing queue) flags pending commissions older than N days and notifies the owner.
4. **Deal assignment and split (M).** `assigned_to` (a member) on a buyer and ledger entry; an optional split table (member, percent, sums to 100). The deal board filters "mine".
5. **Export (S).** CSV of the ledger and deals for accounting.

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
