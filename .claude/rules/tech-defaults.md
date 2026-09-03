# Tech defaults — dev workflow, commands, config

## Everything runs through Docker Compose

There is no local (non-Docker) dev workflow documented.

```bash
# Start the full stack (FastAPI backend + Nginx frontend on :8080)
docker compose up -d --build

# View logs
docker logs -f procurement-server

# Rebuild after dependency changes (requirements.txt / Dockerfile)
docker compose up -d --build --force-recreate
```

The backend runs with `--reload` and `.:/app` is bind-mounted, so editing Python files on the host applies live **without a rebuild** — rebuild only for `requirements.txt` / `Dockerfile` changes.

Dashboard: `http://localhost:8080` (Nginx reverse-proxies to FastAPI on :8000 inside the container).

## Tests

All tests live in `tests/` and run under **pytest** (one entrypoint):

```bash
# Whole suite (business rules + reconciliation + V2 architecture)
docker exec procurement-server python -m pytest -q

# One file / one test
docker exec procurement-server python -m pytest tests/test_business_rules.py -q
docker exec procurement-server python -m pytest tests/test_business_rules.py::test_category_1_ecommerce -q
```

- `tests/test_business_rules.py` — asserts exact column counts/positions per category (14-column Header schema, VAT calc, `(loại bỏ)` filtering for category 3). Keep it green after any `services/business_rules.py` change.
- `tests/test_reconciliation.py` — reconciliation engine (`BreakFlag`, tolerances, line auto-compute).
- `tests/test_v2_architecture.py` — relational V2 (schema, discount, filter, duplicate guard).

`conftest.py` + `pytest.ini` at the repo root put the root on `sys.path`, so pytest works both in Docker and bare.

### CI vs local — they differ on purpose

- **Local / dev**: everything through Docker Compose (commands above). There is no supported bare-Python dev workflow.
- **CI** (`.github/workflows/ci.yml`): runs **bare**, not in Docker — Python 3.12, installs `tesseract-ocr` + `tesseract-ocr-vie` via apt, `pip install -r requirements.txt`, then `python -m pytest -q`. CI also guards against re-introduced waste (tracked files under `cache/`, `ket_qua/`, `ops/backups/`; stray `*.py` at the repo root). If you change how tests are invoked, update the CI workflow too.

## Code style

- PEP 8 for Python.
- `server.py` is intentionally a single monolithic ~1400-line FastAPI app; new logic belongs in a `services/*` module where it fits.

## Runtime configuration

All runtime config is via `.env` (copy from `.env.example`) plus a `service_account.json` GCP service account file (Drive + Sheets API scopes) in the repo root.

Key variables:

- `AI_ENGINE_MODE` — `auto` / `cloud` / `ollama`
- `OPENAI_API_KEY` / `OPENAI_MODEL` — Gemini (OpenAI-compatible endpoint)
- `OLLAMA_BASE_URL` / `OLLAMA_MODEL` — local vision fallback (`qwen2.5-vl`)
- `GOOGLE_DRIVE_FOLDER_ID` / `GOOGLE_DRIVE_PROCESSED_FOLDER_ID` — inbox / processed folders
- `GOOGLE_SHEET_ID` + tab names
- `RECON_TOLERANCE_VND` / `RECON_TOLERANCE_PCT` — reconciliation tolerances
- `MAX_CONCURRENT_DOWNLOADS` / `MAX_CONCURRENT_OCR` — scan throttling

Never commit real API keys or `service_account.json` — both are git-ignored. Templates: `.env.example`, `service_account.example.json`.

### Security (`SECURITY.md`)

- Never modify `.gitignore` to track `.env` or `service_account.json`.
- The GCP service account should hold **least-privilege** scopes only: **Google Drive Viewer** + **Google Sheets Editor**. Do not grant broader roles.
- In production behind a public network, terminate TLS at Nginx or Cloudflare.

## Repo layout note

- `services/` — core business modules. `server.py` — the monolithic FastAPI app.
- `frontend/` — static HTML/JS served by Nginx.
- `tests/` — pytest suites + `fixtures/`.
- `ops/scripts/` — reusable operational tools (backup, PDF reset). `ops/migrations/` — one-time data migrations. `ops/backups/` — snapshot output (git-ignored). `server.py` imports `format_data_lines_v2_columns_f_to_k` from `ops/scripts/` — keep `ops/` importable (it stays in the image; only `ops/backups/` is dockerignored). (`migrate_to_v2.py` is a completed migration, kept as a historical record only.)
- One-off audit/fix scripts do **not** belong in the repo — write them in a scratch dir, or promote genuinely reusable ones into `ops/scripts/`.

## Contributing

`CONTRIBUTING.md` (repo root) is the human-facing version of these rules: fork/clone, `cp .env.example .env`, `docker compose up -d --build`, keep `python -m pytest -q` green, PR against `main` with rationale + test steps + UI screenshots.
