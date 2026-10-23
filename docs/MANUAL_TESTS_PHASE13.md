# Phase 13: manual checks

Everything below needs a real browser engine or a real website, so it is left for you. The automated suite already covers the logic with a
fake browser (robots.txt, pauses between requests, blocking of internal addresses, the plain-then-browser fallback, health, failure
alerts, selector suggestions).

## 0. Setup (once)

```bash
cd backend
.venv/Scripts/python -m scripts.migrate                       # adds fetch_mode and the run-health columns
.venv/Scripts/pip install -r requirements-browser.txt
.venv/Scripts/playwright install chromium                     # about 150 MB
```

Add `SCRAPE_ALLOW_PRIVATE_HOSTS=true` to `backend/.env` for the local demo page below, then restart the backend (`python -m app`).
Turn it off again afterwards: it lets the scraper reach addresses on this machine.

## 1. A page that only exists after JavaScript runs

1. Open http://127.0.0.1:8000/demo/js-listing. You should see four Ayat units. View the page source: there is no `<article>` in it, the cards
   are built by a script.
2. Dashboard > Studio > "Add a developer page": developer **Ayat**, address `http://127.0.0.1:8000/demo/js-listing`,
   click **Suggest selectors**.
   - Expected: the plain page has no cards, so the first proposal finds nothing and the system retries from the browser-rendered page. The preview then shows the units and the response says it was rendered in a browser.
3. Save the selectors (or use the Studio sample selectors), then in "How it is read" choose **Automatic**.
4. Top bar: tick **Preview only**, click **Run Market Scraper**.
   - Expected: Ayat shows units found, and the run notes say "The page builds its listings with JavaScript, so it was read in a browser".
5. Change the mode to **Plain page** and run again: expected 0 units found and the health card turns to "Layout may have changed".
   Change it back to **Automatic**.

## 2. Politeness

1. Pick a real developer site that has a `robots.txt` disallowing some path. Save that path as the page address.
   Expected: the scraper reports "the site's robots.txt does not allow this page to be read automatically" and does not fetch it.
2. Save two pages on the same site and run the scraper: the backend log shows about two seconds between requests to that host.

## 3. Health and alerts

1. Save a page address that does not exist (for example `https://example.com/nope`) for Noah. Run **Run Market Scraper** (not preview) three times.
   - Expected: the card shows "Failing" after the first run and "Failing repeatedly" after the third.
   - Expected: on the third run you get one Telegram message from the bot: "Scraper alert: Noah Real Estate's page could not be read 3 times in a row". A fourth run sends no second alert.
2. Save a valid page again and run: the status returns to Healthy.

## 4. A real developer page

1. Find a real developer listing page (static HTML). In Studio, use **Suggest selectors** with its address.
2. Check the preview against the page: titles, areas, sizes and prices should match what you see. If the model got a field wrong, the preview shows it.
3. Save, run the scraper in preview mode, and compare the proposed changes with the site.

## What to report back

Anything that differs from "Expected" above, with the Studio screenshot or the backend log lines (`backend/server.err.log`).
