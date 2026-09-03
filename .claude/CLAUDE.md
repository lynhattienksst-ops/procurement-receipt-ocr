# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Procurement Receipt OCR & Automation Hub — a "Zero-Touch Procurement" system (Vietnamese business context). It pulls receipt images from a Google Drive inbox folder, extracts structured data via a hybrid OCR pipeline (Google Gemini cloud, falling back to local Ollama vision model, falling back to Tesseract), classifies each receipt into one of 4 business-object categories, runs two-tier duplicate detection, checks Vietnamese spelling, and writes normalized rows into Google Sheets.

**Before working on business rules, sheet schema, or duplicate-detection logic, read `Project_report`** (in repo root, no extension) — it is the authoritative, continuously updated spec covering object-code numbering, the 4-category classification rules, the Google Sheets relational schema (Header V2 / Lines V2), duplicate guard mechanics, and the versioned history of architectural changes.

## Detailed rules — read the relevant file before you start

The full rule set is split into `.claude/rules/`:

- **`.claude/rules/workflow.md`** — how to approach any task here: what to read first, the "no structural changes without an explicit user request" rule, test-before-done, file hygiene, where reusable ops scripts go (`ops/`).
- **`.claude/rules/design.md`** — the V2 relational sheet data model, DT1–DT4 category rules, object-code numbering, and the Critical Invariants that keep live spreadsheet data from being corrupted.
- **`.claude/rules/tech-defaults.md`** — Docker Compose dev workflow, the exact commands to run/test, code style, and runtime configuration (`.env`, `service_account.json`).
- **`.claude/rules/regressions.md`** — condensed ledger of bugs already fixed and the guard each one left behind (from `Project_report` §3.5–§3.22). Check it before editing sheet-write, reconciliation, object-code, or the verify.html viewer.

## Architecture (quick map)

**Two containers**: `procurement-server` (FastAPI, `server.py`, ~1400 lines, single monolithic app) and `frontend` (Nginx serving static HTML/JS from `frontend/`, proxying `/api/` to the backend — see `nginx.conf`).

**Request flow**: Nginx → FastAPI (`server.py`) → `services/*` modules → Google Drive/Sheets APIs.

### `services/` modules

- `ocr_engine.py` — unified OCR client. Tries Google Gemini (native httpx client, OpenAI-compatible endpoint) first, falls back to Ollama (`qwen2.5-vl` local vision model) on network/quota failure per `AI_ENGINE_MODE` (`auto`/`cloud`/`ollama`), with Tesseract (`pytesseract`, Vietnamese trained data) as a last resort for raw text extraction. Extracts into `PROCUREMENT_RECEIPT_SCHEMA` (merchant, customer, line items, tax fields, etc.).
- `business_rules.py` — classification (`detect_business_category`) into DT1–DT4 via keyword matching (`CAT1_KEYWORDS`/`CAT2_KEYWORDS`/`CAT3_KEYWORDS`), Vietnamese number parsing (`parse_vietnamese_number`), VAT calculation, and row formatting (`format_receipt_to_sheet_rows`) that turns one extracted receipt into 1..N sheet rows depending on category. Also `classify_line_item(name, unit)` — line-item cost grouping into 9 codes written to `Data_Lines_V2` cols M/N (`LINE_GROUP_KEYWORDS` is the single keyword source; see `docs/decisions/*-line-item-grouping.md`). This is the core business logic — see `Project_report` §3.1–3.4 and `.claude/rules/design.md` before modifying.
- `duplicate_checker.py` — `DuplicateChecker` class; normalizes and tracks seen order/tracking/invoice/receipt IDs (`clean_dt_code`) to flag duplicates against sheet history (tier 1) and within the same scan batch (tier 2).
- `spell_checker.py` — Vietnamese spelling/typo detection (Telex mis-typing, repeated final consonants, OCR font errors) and field validation.
- `google_service.py` — `GoogleSyncService`: wraps Google Drive (list/download/move files) and Google Sheets (`gspread` + `googleapiclient`) I/O. Defines the canonical sheet column headers (`STANDARD_HEADER_V2_HEADERS`).
- `reconciliation_engine.py` — ledger reconciliation between Header and Lines sheets: match logic with tolerance thresholds (`RECON_TOLERANCE_VND`/`RECON_TOLERANCE_PCT`), break-flag classification (`BreakFlag` enum). Imports `sheet_write_lock` from `server.py` (falls back to a local lock when run standalone).
- `business_rules.clean_num` — coerce any Sheet cell (may carry `%`/`đ`/`VND`) to a plain float; used by `server.py`'s `/api/v1/sheets/reconcile-totals`.

### `ops/`

- `ops/scripts/` — reusable operational tools: `backup_sheet_state.py` (Sheet snapshot → `ops/backups/`), `reset_processed_pdfs.py` / `audit_and_reset_pdfs.py` (re-queue PDFs), `format_data_lines_v2_numeric.py` (imported by `server.py`), `backfill_line_groups.py` (one-shot backfill of `Data_Lines_V2` cols M/N "Nhóm hàng"; `--dry-run` prints the group distribution), `format_accounting_numbers.py` (coerce Header/Lines number columns to real numbers, `round(x,2)`, + VN accounting number format `#,##0;(#,##0)`; targeted range writes, Exact Row Guard, `--dry-run`), `build_dashboard_tab.py` (dựng tab `Dashboard` — bảng điều khiển phân tích khung ngang: KPI + 6 biểu đồ công thức (1 LINE + 5 BAR ngang, không pie) + bảng nguồn + mục "CÁCH ĐỌC"; đọc live từ Header/Lines V2 + `Reconciliation_Report_ALL` + `Recon_Audit_Log`; idempotent, chỉ đụng tab `Dashboard` + cột ẩn `AA:AK`, `--dry-run`; xem `docs/dashboard.md`).
- `ops/migrations/` — one-time data migrations: `migrate_to_v2.py` (the V1→V2 migration; already run, kept only as a historical record — the `/api/v1/sheets/migrate-v2` endpoint was removed with the flat V1 tables), `migrate_header_datetime_format.py`.
- `ops/backups/` — snapshot output, git-ignored, auto-rotated to the last 3.

`ops/` stays inside the Docker image (only `ops/backups/` is dockerignored) because `server.py` imports from it.

### `server.py` key endpoints

- `GET /health`, `GET /api/v1/status` — health/status
- `POST /api/v1/receipt-ocr` (`/ocr/`) — single-image OCR
- `POST /api/v1/drive/scan` — scan the Drive inbox folder, OCR + classify + write to sheet (background scanner also runs this on a schedule; toggle via `/api/v1/auto-scan/toggle|stop|trigger`)
- `POST /api/v1/sheets/export` — write processed rows to Google Sheets
- `POST /api/v1/sheets/reconcile-totals`, `/api/v1/reconcile/run`, `/api/v1/reconcile/status/{period}`, `/api/v1/reconcile/report/{period}` — reconciliation engine endpoints
- `GET/PUT/DELETE /api/v1/sheets/record*` — human-in-the-loop review/edit/approve/delete of individual records (used by `frontend/verify.html`)
- `GET /api/v1/drive/image/{file_id}` — proxies Drive images for the frontend viewer
- `POST /api/v1/manual-entry` — manual (non-OCR) receipt entry path
- Parallel processing: `MAX_CONCURRENT_DOWNLOADS`/`MAX_CONCURRENT_OCR` env vars throttle concurrent Drive downloads and OCR calls during a scan.

### Frontend (`frontend/`)

Plain HTML/JS (no build step), served directly by Nginx: `index.html` (scan dashboard, AI model selector, auto-scan controls), `verify.html` (document-by-document review/approval UI, filterable by DT group and status), `reconcile.html` (reconciliation report viewer), `manual.html` (manual entry form), `app.js` (shared client logic).

## Repo layout

```text
Scan_Hoa_Don/
├── server.py                 ← monolithic FastAPI app
├── services/                 ← core business modules (+ __init__.py)
├── frontend/                 ← static HTML/JS (Nginx)
├── ops/
│   ├── scripts/              ← reusable tools (some imported by server.py)
│   ├── migrations/           ← one-time data migrations (migrate_to_v2 = historical record; migration done)
│   └── backups/              ← Sheet snapshots (git-ignored, auto-rotated)
├── tests/                    ← pytest suites + fixtures/  (conftest.py + pytest.ini at root)
├── docs/decisions/           ← ADRs
├── Project_report            ← authoritative spec
├── Dockerfile · docker-compose.yml · nginx.conf · requirements.txt
├── .dockerignore · .gitignore
└── .claude/                  ← Claude Code config (below)
```

### `.claude/` layout

```text
.claude/
├── CLAUDE.md            ← this file — project brain + architecture map
├── CLAUDE.local.md      ← private notes, git-ignored
├── settings.json        ← shared permissions + hooks
├── settings.local.json  ← private settings, git-ignored
├── memory.md            ← Claude's running memory for this project
├── rules/               ← detailed rules, split out of CLAUDE.md
│   ├── workflow.md
│   ├── design.md
│   ├── tech-defaults.md
│   └── regressions.md   ← fixed-bug ledger + the guard each one left
├── agents/              ← project sub-agents
│   ├── project-researcher.md          ← digests Project_report + ADRs, sourced answers
│   ├── invariant-reviewer.md          ← audits a pending diff against the sheet invariants
│   ├── reconciliation-auditor.md      ← read-only ledger reconciliation (Header vs Lines)
│   └── business-operations-research.md ← researches a business/ops question (repo + web), scores options on cost/time/flexibility/productivity, writes an inline report
└── skills/              ← reusable task recipes
    ├── scan-context.md          ← load full operating context (V2 schema, DT1–DT4, version history)
    ├── reconcile-ledger.md      ← run/interpret the reconciliation engine
    ├── run-scan.md              ← bring up stack → trigger Drive scan → verify
    └── backup-sheet.md          ← JSON snapshot of the live Sheet before risky ops
```

## Other tools configured in this repo

Not Claude Code, but present — don't be surprised by them:

- **`GEMINI.md`** (repo root) — brief for the Gemini CLI. Its rules are mirrored in `.claude/rules/workflow.md`.
- **`.serena/`** — Serena MCP project config (`project.yml` = Python language server; `memories/`; `cache/` and `project.local.yml` are git-ignored). The Serena MCP server itself is wired up in `../.agents/plugins/serena/mcp_config.json`.
- **`.github/workflows/ci.yml`** — GitHub Actions CI, runs the test suite bare (not Docker). See `.claude/rules/tech-defaults.md`.
- **`../.agents/skills/`** (workspace level) — a library of generic skills plus thin stubs for this project that now point back to `.claude/skills/`.
