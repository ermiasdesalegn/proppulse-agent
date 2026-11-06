#!/usr/bin/env bash
# Creates the three Cloud Scheduler jobs (the free tier allows three). Run after deploy/gcp.sh:  bash deploy/scheduler.sh
set -euo pipefail

ENV_FILE="${ENV_FILE:-deploy/.env.prod}"
set -a; . "$ENV_FILE"; set +a
: "${GCP_PROJECT:?}" "${GCP_REGION:=europe-west1}" "${SERVICE:=proppulse-backend}" "${CRON_SECRET:?}" "${TIMEZONE:=Africa/Addis_Ababa}"
URL="$(gcloud run services describe "$SERVICE" --project "$GCP_PROJECT" --region "$GCP_REGION" --format='value(status.url)')"

job() {  # name, schedule, path
  local args=(--project "$GCP_PROJECT" --location "$GCP_REGION" --schedule "$2" --time-zone "$TIMEZONE" --uri "$URL$3" --http-method POST
              --headers "X-Cron-Secret=$CRON_SECRET" --attempt-deadline 300s)
  gcloud scheduler jobs update http "$1" "${args[@]}" >/dev/null 2>&1 || gcloud scheduler jobs create http "$1" "${args[@]}" >/dev/null
  echo "job $1: $2 -> $3"
}

job proppulse-proactive  "0 8 * * *"  /api/cron/daily-proactive-run   # follow-ups, nudges and new-listing alerts, every morning
job proppulse-reminders  "0 * * * *"  /api/cron/reminders             # 24 h tour and milestone reminders, checked hourly
job proppulse-audit      "30 1 * * *" /api/cron/audit-export         # copy each agency's new audit entries to the configured sink (does nothing when none is set)
job proppulse-scrape     "0 6 * * *"  /api/cron/scrape-market         # market scrape with robots.txt and pacing, before the morning run
