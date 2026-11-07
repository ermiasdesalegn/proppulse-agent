# Next slices, kept small on purpose

Written 2026-11-07. The product plan ([PLAN_PRODUCT_AND_MONEY.md](PLAN_PRODUCT_AND_MONEY.md)) is too large to finish in one go, so each remaining phase is cut into slices that each show one complete, working flow and can be built, tested and deployed alone. Anything left out of a slice goes on its "later" list; nothing is half-built.

Rules for every slice:

- One flow the owner can click through in under two minutes, with no setting or outside service needed to see it work.
- Anything that needs real money, a real email domain, a storage bucket or a legal decision runs in demo or mock mode, and says so on screen.
- Expand-only migration at most one; tests for the happy path and the guards that matter (another agency's data stays invisible, the wrong role is refused).
- Built in its own branch, checked (ruff, backend suite, frontend suite, rehearsal if there is a migration), then deployed migration first, code second.

## Slice A: buyer account basics (Size S to M)

Flow: a buyer signs in, opens **My visits**, sees the visits they booked, cancels one; opens **Alerts**, switches price-drop alerts off and on.

Build:
1. `/portal/visits`: list of the buyer's bookings (date, home, status) from the existing tours table, and a Cancel button that marks the booking cancelled, frees the calendar slot through the existing booking service, and tells the agency's broker.
2. `/portal/alerts`: one switch for price-drop and new-listing alerts per buyer (a boolean on the buyer's record, checked where alerts are sent), with the current state shown.
3. The portal header gets the two links.

Later (not now): reschedule, alert rules per area or budget, unsubscribe links in messages, listing freshness and verified badges, agency profile card, buyer data export and deletion.

Done when: a buyer cancels a booking and the broker sees it cancelled; a buyer who switches alerts off receives none and one who switches them on receives them; another buyer's visits are never shown.

## Slice B: a new agency's first five minutes (Size S)

Flow: a broker creates an agency, lands in the console, sees a short checklist, renames the agency, ticks things off as they happen.

Build:
1. A **Get started** card at the top of the console Overview, shown to owners and admins until all items are done or it is dismissed: connect a Telegram bot, set an own price for one unit, set one commission rate, invite a teammate. Each item is ticked from facts already in the database; each links to the page that does it.
2. A rename field on the Agency page (display name; the address stays the same), audited.

Later: a guided tour, sample data for an empty agency, email reminders to finish setup, changing the address.

Done when: a fresh agency sees four unticked items, each tick appears after the matching action, the card can be dismissed, and a rename shows in the sidebar and the audit trail.

## Slice C: operations, owner settings only (no code except notes)

These are settings only the owner can change; the app already supports them. Each is a one-line instruction in `docs/FREE_DEPLOYMENT.md` once done.

1. Verify a sending domain in Resend; set `EMAIL_FROM` on Render and Supabase's custom SMTP (removes the two-emails-an-hour limit; real buyer and claim emails start working).
2. Create a private Supabase Storage bucket and set `DEAL_DOCUMENTS_BUCKET` on Render (turns deal documents on).
3. Set `AUDIT_EXPORT_DIR` (or an object-lock bucket and `boto3`) to turn the audit export on.
4. Re-record the replay eval with live Gemini (`make eval-record`), which needs free-tier quota.

## Slice D: real payments (Size L, blocked)

The demo billing flow stays until these exist; each is a separate piece of work after a decision.

1. A payment provider that can take ETB, and the paperwork to use it.
2. A signed webhook, idempotent payments, refunds, a grace period before a lapsed plan returns to the free caps, monthly invoices and a fee statement.
3. Invoice numbering and tax rules agreed with an accountant.
4. A real deal-share policy and the contract wording behind it.

## Order

1. Slice B (smallest, helps every new agency) and Slice A (helps every buyer) can be built at the same time in separate branches; they touch different files.
2. Slice C whenever the owner has time; it unlocks emails, documents and the audit export.
3. Slice D only after the owner's decisions and legal review.
