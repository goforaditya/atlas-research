#!/usr/bin/env bash
# One-command build, push and deploy to Google Cloud Run (Section 13.3).
# Needs: gcloud signed in, GCP_PROJECT_ID set, and Secret Manager secrets GEMINI_API_KEY and SEC_EDGAR_USER_AGENT.
set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:?Set GCP_PROJECT_ID}"
REGION="${GCP_REGION:-asia-south1}"
SERVICE_NAME="atlas-research"
REPO="${GCP_AR_REPO:-atlas}"
GEMINI_MODEL="${GEMINI_MODEL:-gemini-3.8-flash}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${SERVICE_NAME}:$(git rev-parse --short HEAD 2>/dev/null || echo 'latest')"

echo "0. Ensuring Artifact Registry repo exists..."
gcloud artifacts repositories describe "${REPO}" --location "${REGION}" --project "${PROJECT_ID}" >/dev/null 2>&1 || \
  gcloud artifacts repositories create "${REPO}" --repository-format docker --location "${REGION}" --project "${PROJECT_ID}"

echo "1. Verifying golden fixtures & running unit tests (local, with dev deps)..."
uv sync --dev
uv run pytest backend/tests/
uv run python -m scripts.verify_golden_fixtures

echo "2. Building and pushing container image via Cloud Build..."
gcloud builds submit --tag "${IMAGE}" --project "${PROJECT_ID}"

echo "3. Deploying to Google Cloud Run..."
# One instance only: run state and the SQLite file live in the instance (Section 14, decision 9).
gcloud run deploy "${SERVICE_NAME}" \
  --image "${IMAGE}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --platform managed \
  --allow-unauthenticated \
  --port 8080 \
  --cpu 2 \
  --memory 2Gi \
  --min-instances 0 \
  --max-instances 1 \
  --session-affinity \
  --concurrency 40 \
  --timeout 300s \
  --set-env-vars "GEMINI_MODEL=${GEMINI_MODEL},MAX_LIVE_RUNS_PER_HOUR_PER_IP=10" \
  --set-secrets "GEMINI_API_KEY=GEMINI_API_KEY:latest,SEC_EDGAR_USER_AGENT=SEC_EDGAR_USER_AGENT:latest"

SERVICE_URL=$(gcloud run services describe "${SERVICE_NAME}" --region "${REGION}" --project "${PROJECT_ID}" --format 'value(status.url)')
echo "Deployment complete: ${SERVICE_URL}"
curl -fsS "${SERVICE_URL}/api/v1/health"
