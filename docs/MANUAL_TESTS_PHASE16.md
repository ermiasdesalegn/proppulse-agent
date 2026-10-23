# Phase 16 manual checks (deployment)

Nothing below can run without your Google, Supabase and Vercel accounts, so it is a short checklist. The smoke check script was run against the local backend (10/10) and is unit tested.

1. Follow `docs/DEPLOYMENT.md` sections 1-3. Budget alert set in Google Billing.
2. `python -m scripts.post_deploy_check <cloud-run-url> --admin-key ... --cron-secret ...` prints `10/10 checks passed`.
3. Send your bot a message on your phone: it answers (this exercises webhook, Gemini and the database through the pooler).
4. Open the Vercel address, sign in, check Live Brain shows that conversation within a couple of seconds.
5. Cloud Scheduler console: press **Force run** on each of the three jobs; each shows a success status.
6. Cloud Logging: logs are JSON; search for your bot token and the admin key: neither appears.
7. Send 130 quick requests to `/demo/js-listing` (a loop of `curl`): later ones return 429.
8. Leave the service idle ten minutes (with `ALWAYS_ON=false`) and message the bot again: the reply still arrives.
