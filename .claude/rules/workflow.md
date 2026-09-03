# Workflow rules

How to approach any task in this repo. Read this before starting work.

## 1. Read the spec first

- **`Project_report`** (repo root, no extension) is the authoritative, continuously updated spec. Before touching business rules, the sheet schema, object-code numbering, or duplicate-detection logic, read it — especially §3.1–§3.4 (categories & row formatting) and §3.5–§3.20 (version history of invariants).
- Relevant architecture decisions live in `docs/decisions/` (ADR-001 pure-numeric V2 schema; 0001 reconcile without dates; 0002 auto-detect line discounts; 0003 line auto-compute). Check these when working on reconciliation or line math.
- `GEMINI.md` is the short brief for the Gemini CLI and mirrors these rules.

## 2. Do not change load-bearing structure without an explicit user request

Per `GEMINI.md` and `CLAUDE.md`: **never** change on your own initiative:

- Google Sheet column structure or order (Header V2 / Lines V2).
- Object-code format (`DTnXXXX`).
- The 4-category (DT1–DT4) classification rules.
- Number/date formatting rules written to the sheet.

These are load-bearing for existing live spreadsheet data. If a change seems needed, surface it and wait for the user to confirm.

## 3. Respect the Critical Invariants

See `.claude/rules/design.md` for the full list (append-only writes, monotonic object codes, Exact Math Row Guard, `sheet_write_lock`). Violating them silently corrupts or loses live spreadsheet data.

Also read **`.claude/rules/regressions.md`** before editing any sheet-write path, the reconciliation engine, object-code allocation, or the `verify.html` viewer — it lists bugs already fixed and the guard each one left behind, so you don't reintroduce them.

## 4. Test before you call it done

- Run the whole suite with `docker exec procurement-server python -m pytest -q` (everything lives in `tests/`).
- `tests/test_business_rules.py` asserts exact column counts/positions per category — always run after editing `services/business_rules.py`. `tests/test_reconciliation.py` after touching reconciliation logic.
- Report actual results. If a test fails, say so with the output.

## 5. File hygiene

- Never delete or move: `server.py`, `services/*`, `frontend/*`, `Dockerfile`, `docker-compose.yml`, `nginx.conf`, `.env*`, `service_account.json`.
- Reusable operational tools go in `ops/scripts/`; one-time data migrations in `ops/migrations/`. Both must stay importable — `server.py` pulls `format_data_lines_v2_columns_f_to_k` from `ops/scripts/`. (`ops/migrations/migrate_to_v2.py` is a completed migration kept only as a historical record — no longer imported by `server.py`.)
- Genuinely one-off audit/fix scripts do **not** belong in the repo at all — use a scratch dir. Never leave `audit_*` / `fix_*` / `scratch_*` `.py` files at the repo root (a hook warns about this).
- Sheet-state snapshots are written to `ops/backups/` (git-ignored, auto-rotated to the last 3).
- Never commit real API keys or `service_account.json` (see `.gitignore`).

## 6. Language

The project domain, spec, and UI are Vietnamese. Keep domain terms (nhóm đối tượng, hóa đơn, người mua/nhận hàng, ghi chú) intact in code, comments, and sheet output. Match the language of the surrounding text when editing docs.
