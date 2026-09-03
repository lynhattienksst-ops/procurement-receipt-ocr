# `dashboard/` — Cloud Run dashboard micro-service

A standalone, **read-only** web service that reads the live Google Sheet
(`Data_Header_V2` / `Data_Lines_V2` / recon tabs) and serves the executive +
ops dashboards. Independent of the OCR server (`server.py`) — its own image,
its own minimal deps, no writes to any source tab.

Phased build — see `docs/data-contract.md` and the BI review:

| Phase | State | What it adds |
|---|---|---|
| **P0** | this commit | data contract · Cloud Run skeleton · keyless deploy pipeline · token-gated landing + `/api/health` Sheets probe |
| P1 | planned | `services/data_quality.py` — completeness / validity / integrity metrics, R/A/G thresholds |
| P2 | planned | `services/analytics.py` — MoM/YoY, moving average, supplier Pareto + HHI, anomaly flags, money-weighted recon exposure |
| P3 | planned | `dashboard/index.html` real charts (ECharts) — Executive view (exception-first) + Ops view, interactive filters |
| P4 | planned | docs, rollback, release tag |

## Routes

| Route | Auth | Purpose |
|---|---|---|
| `GET /healthz` | none | Cloud Run liveness |
| `GET /` | none | hint only |
| `GET /d/<token>` | token | the dashboard page (`token` = `DASHBOARD_TOKEN`) |
| `GET /d/<token>/api/health` | token | build info + live Sheets auth/reachability probe |

Bad token → `404` (does not confirm the path exists).

## One-time setup

```bash
# In Google Cloud Shell (console.cloud.google.com -> Cloud Shell):
export PROJECT_ID=<your-gcp-project-id>       # from service_account.json "project_id"
bash dashboard/bootstrap_gcp.sh
# then add the 6 printed values as GitHub repo secrets
```

`bootstrap_gcp.sh` provisions Artifact Registry, a `dashboard-deployer` service
account, and Workload Identity Federation so GitHub Actions deploys **without a
stored key**. It touches nothing in the OCR system, the Sheet, or Drive.

## Deploy

Push any change under `dashboard/**` (or `services/**`) to `main`, or run the
**Deploy Dashboard to Cloud Run** workflow manually. The run summary prints the
service URL; open `<URL>/d/<DASHBOARD_TOKEN>`.

## Run locally

```bash
cd dashboard
pip install -r requirements.txt
DASHBOARD_TOKEN=dev \
GOOGLE_SHEET_ID=<id> \
GOOGLE_SERVICE_ACCOUNT_FILE=../service_account.json \
uvicorn app:app --reload --port 8090
# http://localhost:8090/d/dev
```
