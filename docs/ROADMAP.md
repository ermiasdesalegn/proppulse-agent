# Roadmap: what is left

Phases 9–17 are done and committed locally (foundation, SRS completion, evaluation, dashboard, scraping, agent depth, engineering quality, deployment assets, README). The dashboard was then restyled three times; the target look is now fixed by the reference below.

## Phase 18 — Dashboard in the "enterprise console" look (done)

**Reference:** near-black base, flat dark panels with hairline borders, a single vivid orange accent, orange-tinted icon tiles, small delta badges, tag chips, orange checkboxes, a quiet sidebar with an orange active item. Take the *look and layout*, not the subscription features.

| Area | Target |
|---|---|
| Tokens | Base `#0a0a0a`, sidebar and panels `#111`, raised `#181818`, borders `#222`, text white and grey `#9ca3af`, accent orange `#ff6a13`. Green and amber stay only as status colours. Remove the navy tint and the indigo accent. |
| Sidebar | Flat, icon + label, section labels (e.g. Workspace / Intelligence / Tools), active item in orange text with a faint orange tint and left bar, user card at the bottom. |
| Page header | Title and one-line help on the left, the page's primary action (solid orange, optionally a split button with a menu) on the right. No section eyebrow. |
| KPI cards | Orange-tinted rounded icon tile on the left; small grey label; large white number with an orange delta badge beside it; grey "vs last month" line. |
| Panels | `#111`, 1px `#222` border, 12px radius, no glow. Panel title with a grey subtitle, and an orange "View all →" link top right. |
| Tables and lists | Grey small header, hairline row dividers, orange checkboxes, status dot + word (green Active, orange Past Due), outlined orange page button for the current page. Bulk-select bar with a "Bulk actions" menu on CRM. |
| Activity | Circular orange-tinted icon, bold line, grey timestamp, tag chip on the right ("New match", "Booking", "System"). |
| Buttons | Primary solid orange with dark text; secondary outlined `#2a2a2a`; outlined-orange for panel actions like "+ New role". |
| Real deltas | Add previous-period comparison to the stats endpoint (this 30 days vs the 30 before, counted from the database), so every "vs last month" figure is real. |
| Pages | Apply to Overview, CRM, Deal Board, Agent Brain, Map, Analytics, Studio, Chat, dossier and sign-in; check phone layouts again. |

Done when: lint, types, unit tests, build and e2e selectors pass, and every page has been compared against the reference at desktop and phone width.

## Phase 19 — Close the SRS gaps and finish (done)

1. Buyer preference vector built from cumulative chat history (SRS 3.3), then re-record the evals.
2. Label price increases as `PRICE_INCREASE` and tell the broker (SRS 3.2).
3. README screenshots of the new look.
4. Final full run: backend tests, evals, ruff, mypy, frontend lint/types/tests/build.

## Phase 20 — Portal crawler and the Portals page (done)

1. Scrapy crawler for six large portals: sitemap or index discovery, local extraction first, Gemini only as a fallback, polite settings, full crawls with guarded retirement of vanished listings (`backend/scraper`).
2. Small developer sites in the LLM sweep, seeded from their own sitemaps; bot checks reported, not bypassed.
3. Portals page: start a crawl from the dashboard (GitHub Actions dispatch from the backend), run history, status, alerts to the broker on Telegram.
4. Portal units: original listing link kept, no lead email or assumed commission, the broker arranges the viewing.
5. Architecture document with LangGraph and RAG diagrams, README and decision log brought up to date.

Not built: a per-portal on/off switch, a review queue for skipped listings, de-duplicating the same flat across portals, and an opt-in channel post for portal listings (decided against for now).

## Yours to do
Free space on C:, rotate the pasted secrets, `git push -u origin main`, deploy per `docs/DEPLOYMENT.md`, run `docs/MANUAL_TESTS_PHASE13–16.md`, native Amharic review of `backend/app/language.py`.
