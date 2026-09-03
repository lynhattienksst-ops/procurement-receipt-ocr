#!/usr/bin/env bash
# =============================================================================
# One-time GCP bootstrap for the Cloud Run dashboard deploy pipeline.
#
# Run this in Google Cloud Shell (console.cloud.google.com -> Cloud Shell icon).
# It is idempotent — safe to re-run. Nothing here touches the OCR server, the
# Google Sheet, or Drive. It only provisions: Artifact Registry, a deploy
# service account, and keyless GitHub -> GCP auth (Workload Identity Federation).
#
#   bash dashboard/bootstrap_gcp.sh
#
# Requires: you are Owner of the project (or hold resourcemanager + iam +
# run + artifactregistry admin).
# =============================================================================
set -euo pipefail

# ---- Config (override by exporting before running) --------------------------
PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null || true)}"
REGION="${REGION:-asia-southeast1}"
GITHUB_REPO="${GITHUB_REPO:-lynhattienksst-ops/procurement-receipt-ocr}"
AR_REPO="${AR_REPO:-dashboard}"
DEPLOY_SA_NAME="${DEPLOY_SA_NAME:-dashboard-deployer}"
POOL="${POOL:-github-pool}"
PROVIDER="${PROVIDER:-github-provider}"
# Runtime SA the Cloud Run service runs AS. It MUST already have read access to
# the Google Sheet (share the sheet with its email, Viewer is enough). Leave
# empty to auto-pick the first non-deployer service account in the project
# (usually the existing procurement service account).
RUNTIME_SA="${RUNTIME_SA:-}"
# ---------------------------------------------------------------------------

[ -n "$PROJECT_ID" ] || { echo "ERROR: set PROJECT_ID (export PROJECT_ID=...)"; exit 1; }
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
DEPLOY_SA="${DEPLOY_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

echo "Project     : $PROJECT_ID ($PROJECT_NUMBER)"
echo "Region      : $REGION"
echo "GitHub repo : $GITHUB_REPO"
echo "Deploy SA   : $DEPLOY_SA"
echo

echo "==> Enabling required APIs"
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  iamcredentials.googleapis.com \
  sts.googleapis.com \
  --project "$PROJECT_ID"

echo "==> Artifact Registry repo '$AR_REPO' ($REGION)"
gcloud artifacts repositories describe "$AR_REPO" --location "$REGION" --project "$PROJECT_ID" >/dev/null 2>&1 || \
gcloud artifacts repositories create "$AR_REPO" \
  --repository-format docker --location "$REGION" \
  --description "Procurement dashboard images" --project "$PROJECT_ID"

echo "==> Deploy service account"
gcloud iam service-accounts describe "$DEPLOY_SA" --project "$PROJECT_ID" >/dev/null 2>&1 || \
gcloud iam service-accounts create "$DEPLOY_SA_NAME" \
  --display-name "Dashboard Cloud Run deployer" --project "$PROJECT_ID"

echo "==> Project roles for deploy SA"
for ROLE in roles/run.admin roles/artifactregistry.writer roles/iam.serviceAccountUser; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member "serviceAccount:$DEPLOY_SA" --role "$ROLE" --condition=None >/dev/null
done

if [ -z "$RUNTIME_SA" ]; then
  RUNTIME_SA="$(gcloud iam service-accounts list --project "$PROJECT_ID" \
    --format='value(email)' --filter="email!=$DEPLOY_SA" | head -n1 || true)"
fi
[ -n "$RUNTIME_SA" ] || { echo "ERROR: no runtime SA found — export RUNTIME_SA=<sheet-reader SA email>"; exit 1; }
echo "Runtime SA  : $RUNTIME_SA"

echo "==> Let deploy SA act as the runtime SA"
gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" \
  --member "serviceAccount:$DEPLOY_SA" --role roles/iam.serviceAccountUser \
  --project "$PROJECT_ID" >/dev/null

echo "==> Workload Identity Federation pool + provider"
gcloud iam workload-identity-pools describe "$POOL" --location global --project "$PROJECT_ID" >/dev/null 2>&1 || \
gcloud iam workload-identity-pools create "$POOL" \
  --location global --display-name "GitHub Actions" --project "$PROJECT_ID"

gcloud iam workload-identity-pools providers describe "$PROVIDER" \
  --location global --workload-identity-pool "$POOL" --project "$PROJECT_ID" >/dev/null 2>&1 || \
gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" \
  --location global --workload-identity-pool "$POOL" \
  --display-name "GitHub OIDC" \
  --issuer-uri "https://token.actions.githubusercontent.com" \
  --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition "assertion.repository=='${GITHUB_REPO}'" \
  --project "$PROJECT_ID"

WIF_PROVIDER="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}"
PRINCIPAL="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${GITHUB_REPO}"

echo "==> Bind the repo's WIF principal to the deploy SA"
gcloud iam service-accounts add-iam-policy-binding "$DEPLOY_SA" \
  --member "$PRINCIPAL" --role roles/iam.workloadIdentityUser \
  --project "$PROJECT_ID" >/dev/null

TOKEN_SUGGESTION="$(openssl rand -hex 24 2>/dev/null || echo 'run: openssl rand -hex 24')"

cat <<EOF

=============================================================
 DONE. Add these 6 GitHub repository secrets:
   repo -> Settings -> Secrets and variables -> Actions -> New repository secret
=============================================================
 GCP_PROJECT_ID   = ${PROJECT_ID}
 GCP_WIF_PROVIDER = ${WIF_PROVIDER}
 GCP_DEPLOY_SA    = ${DEPLOY_SA}
 GCP_RUNTIME_SA   = ${RUNTIME_SA}
 GOOGLE_SHEET_ID  = <same value as GOOGLE_SHEET_ID in your .env>
 DASHBOARD_TOKEN  = ${TOKEN_SUGGESTION}
=============================================================
 Then: push any change under dashboard/ to main (or run the
 "Deploy Dashboard to Cloud Run" workflow manually). The run
 summary prints the service URL; open  <URL>/d/<DASHBOARD_TOKEN>
=============================================================
EOF

if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  echo
  read -r -p "gh CLI is authenticated. Set the 4 derived secrets now? [y/N] " ans
  if [ "${ans:-N}" = "y" ] || [ "${ans:-N}" = "Y" ]; then
    gh secret set GCP_PROJECT_ID   -R "$GITHUB_REPO" -b "$PROJECT_ID"
    gh secret set GCP_WIF_PROVIDER -R "$GITHUB_REPO" -b "$WIF_PROVIDER"
    gh secret set GCP_DEPLOY_SA    -R "$GITHUB_REPO" -b "$DEPLOY_SA"
    gh secret set GCP_RUNTIME_SA   -R "$GITHUB_REPO" -b "$RUNTIME_SA"
    echo "Set. Still add GOOGLE_SHEET_ID and DASHBOARD_TOKEN manually."
  fi
fi
