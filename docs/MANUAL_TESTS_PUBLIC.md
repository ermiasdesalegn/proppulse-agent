# Manual checks: the public site and the Client Portal

What was tested without your accounts: the access rules and role logic (unit tests), the buyer API against the real database (backend tests), and the whole path in a browser with a stand-in for Supabase Auth that issues real signed tokens (routing for every role, an unconfirmed would-be broker, expired sign-ins, listings, saving a home, the map, the Concierge with the real agent). What cannot be tested without a real Supabase project, a real mailbox and your own phone is below.

## Setup (once)

1. Supabase > Authentication > Providers: **Email** on, **Confirm email** on.
2. Supabase > Authentication > URL Configuration: Site URL = your Vercel address; add `http://localhost:3000/auth/callback` and `https://<your-vercel-address>/auth/callback` to the redirect URLs.
3. Create the broker account (Authentication > Users > Add user, tick auto-confirm, or sign up and confirm the email). Make it the owner of the original organization: `python -m scripts.grant_role --email <its email> --org proppulse --role owner`.
4. `frontend/.env.local` (and the same names on Vercel): `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`. Backend: `SUPABASE_URL` is enough; add `SUPABASE_JWT_SECRET` only if the backend answers 401 to valid buyer sign-ins (an older project signing with the shared secret).
5. Migrations 016 and 017 are applied (`python -m scripts.migrate`). Already done on the hosted database.

## Checks

1. **Landing.** Open `/` signed out: the hero, live counts (not "Hundreds"), six featured homes from different developers, areas. Search for "Bole": you land on the sign-in page and, after signing in, on the Bole results.
2. **Sign-up with a real mailbox.** `/signup` with a name, an email you own and an 8+ character password: "Check your email" appears. Open the link **on the same device**: you arrive signed in at `/portal`. Sign-up with an address that already exists shows the same message (no hint that it exists).
3. **Link opened on another device.** Sign up, open the email link on your phone instead: you land on the sign-in page with "Your email is confirmed"; sign in works.
4. **Roles.** Signed in as the buyer: `/console` sends you to `/portal`, `/api/proxy/buyers` answers 403. Sign out, sign in as the broker account (same form): you land in `/console`. The broker can open `/portal` and sees the same homes.
5. **Someone signing up with the owner's email.** In a private window, sign up with the owner's address and do not confirm it: you must not reach `/console`, because that account is not a member of any organization (Supabase refuses a second account with that address, or the account stays unconfirmed).
6. **Password login.** `/login`, "Broker access", the console password: you land in `/console`; Sign out returns to `/login`.
7. **Browse.** `/portal` shows about 560 homes. Pick area Bole, 3+ bedrooms, up to 30M: the count drops, chips appear, each chip's x removes just that filter, the address can be copied into another tab and shows the same result. Paging works.
8. **A home.** Open one: price, facts, payment terms where the listing states them, the original-listing link opens the portal or developer page in a new tab. A home with a 3D layout (build one in the Studio) shows the picture.
9. **Saving.** The heart fills at once; `/portal/saved` lists it; sign in on your phone with the same account and it is there too; the heart on the saved page removes it.
10. **Map.** With `NEXT_PUBLIC_MAPBOX_TOKEN` set: price pins and clusters, clicking a pin previews the home. If the live map does not load (a token restricted to other addresses, for instance) the page falls back to the drawn map by itself. Add `localhost:3000` and your Vercel address to the token's allowed URLs in Mapbox if it does.
11. **Concierge.** `/portal/chat`: type "3 bedroom in Bole, 25 million birr" (replies take a few seconds to half a minute on the Gemini free tier): recommendations, quick-reply buttons, the "Homes we talked about" list, each linking to its home. Say the same in Amharic. Reload: the conversation is still there.
12. **Ask about a home.** On a home, "Ask the assistant about this home", then "Tell me about it", "Compare with similar" and "Book a visit": each opens that exact home. Book a visit through to the end and check the calendar entry and the broker's Telegram message arrive (this is real: use your own details).
13. **Takeover.** In the console CRM, open that web buyer (named from the account), press Take over, send a reply. The buyer's page shows the broker's message within about three seconds, with "Our broker" on it and the takeover notice; the assistant stays quiet. Release it and the assistant answers again.
14. **Phone.** Landing, browse, a home, the map and the chat on a real phone: no sideways scrolling, the filter form is long but usable, the chat input stays reachable above the keyboard.
15. **Look.** The ivory and brass theme, serif headings and prices. The console is still near-black and orange.

## After testing

Delete the test buyers in the console (or in Supabase > Authentication > Users and `buyers_crm` where `thread_id` starts with `web:`) so they do not appear in analytics or the demo.
