#!/usr/bin/env bash
# Deploys the backend to Cloud Run. Run from the repository root:  bash deploy/gcp.sh
# Needs: gcloud (logged in), a project with billing enabled, and deploy/.env.prod (copy deploy/env.prod.example).
set -euo pipefail

ENV_FILE="${ENV_FILE:-deploy/.env.prod}"
[ -f "$ENV_FILE" ] || { echo "Missing $ENV_FILE (copy deploy/env.prod.example and fill it in)"; exit 1; }
set -a; . "$ENV_FILE"; set +a

: "${GCP_PROJECT:?}" "${GCP_REGION:=europe-west1}" "${SERVICE:=proppulse-backend}"
SECRET_VARS=(GEMINI_API_KEY SUPABASE_SERVICE_ROLE_KEY DATABASE_URL TELEGRAM_BOT_TOKEN TELEGRAM_WEBHOOK_SECRET RESEND_API_KEY ADMIN_API_KEY CRON_SECRET INGEST_WEBHOOK_SECRET GOOGLE_SERVICE_ACCOUNT_JSON MAPBOX_TOKEN)
PLAIN_VARS=(SUPABASE_URL GEMINI_MODEL GEMINI_EMBED_MODEL TELEGRAM_CHANNEL_ID EMAIL_FROM FRONTEND_ORIGIN TIMEZONE GOOGLE_CALENDAR_ID OFFICE_LAT OFFICE_LNG)

gcloud config set project "$GCP_PROJECT" >/dev/null
gcloud services enable run.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com cloudscheduler.googleapis.com artifactregistry.googleapis.com

# 1. Secrets: one Secret Manager entry per sensitive value; a new version is added only when the value changed.
for name in "${SECRET_VARS[@]}"; do
  value="${!name:-}"; [ -n "$value" ] || continue
  secret="proppulse-$(echo "$name" | tr 'A-Z_' 'a-z-')"
  gcloud secrets describe "$secret" >/dev/null 2>&1 || gcloud secrets create "$secret" --replication-policy=automatic >/dev/null
  printf '%s' "$value" | gcloud secrets versions add "$secret" --data-file=- >/dev/null
  SECRET_FLAGS+="${SECRET_FLAGS:+,}$name=$secret:latest"
done
PROJECT_NUMBER="$(gcloud projects describe "$GCP_PROJECT" --format='value(projectNumber)')"
for secret in $(gcloud secrets list --filter='name~proppulse-' --format='value(name)'); do
  gcloud secrets add-iam-policy-binding "$secret" --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" --role=roles/secretmanager.secretAccessor >/dev/null
done

# 2. Plain settings.
for name in "${PLAIN_VARS[@]}"; do
  value="${!name:-}"; [ -n "$value" ] || continue
  ENV_FLAGS+="${ENV_FLAGS:+@@}$name=$value"
done

# 3. Build with Cloud Build and deploy. Telegram runs in webhook mode (Cloud Run has no long-lived polling).
IMAGE="$GCP_REGION-docker.pkg.dev/$GCP_PROJECT/proppulse/backend:$(git rev-parse --short HEAD 2>/dev/null || date +%s)"
gcloud artifacts repositories describe proppulse --location "$GCP_REGION" >/dev/null 2>&1 || gcloud artifacts repositories create proppulse --repository-format=docker --location "$GCP_REGION"
gcloud builds submit backend --tag "$IMAGE"

# ALWAYS_ON=true keeps one instance warm with CPU allocated, so background work (the event writer, reminders) never waits for a request.
SCALE=(--min-instances 0 --max-instances 3)
[ "${ALWAYS_ON:-false}" = "true" ] && SCALE=(--min-instances 1 --max-instances 3 --no-cpu-throttling)

gcloud run deploy "$SERVICE" --image "$IMAGE" --region "$GCP_REGION" --allow-unauthenticated "${SCALE[@]}" \
  --memory 1Gi --cpu 1 --concurrency 40 --timeout 3600 \
  --set-env-vars "^@@^TELEGRAM_MODE=webhook@@LOG_FORMAT=json@@TRUST_PROXY_HEADERS=true@@DEMO_RESET_ENABLED=${DEMO_RESET_ENABLED:-false}${ENV_FLAGS:+@@$ENV_FLAGS}" \
  --set-secrets "$SECRET_FLAGS"

URL="$(gcloud run services describe "$SERVICE" --region "$GCP_REGION" --format='value(status.url)')"
echo "Backend: $URL"

# 4. Register the Telegram webhook so Telegram calls the service.
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
  curl -fsS "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
    --data-urlencode "url=${URL}/api/telegram/webhook" --data-urlencode "secret_token=${TELEGRAM_WEBHOOK_SECRET:-}" >/dev/null && echo "Telegram webhook set"
fi

echo "Next: bash deploy/scheduler.sh   then   python -m scripts.post_deploy_check $URL"
