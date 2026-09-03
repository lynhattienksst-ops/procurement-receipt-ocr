---
name: backup-sheet
description: >
  Take a JSON snapshot of the live Google Sheet state (Data_Header_V2,
  Data_Lines_V2) into ops/backups/ before any risky operation — a migration, a
  bulk edit, a schema/format change, or a large scan.
  Use whenever the user is about to do something that could touch existing rows.
---

# Skill: snapshot the Google Sheet

Always do this **before** running a migration, a bulk row edit, a formatting
change, or anything that carries a risk to existing spreadsheet data.

## Run it

```bash
# Inside the running container (has services/* + .env on the path)
docker exec procurement-server python ops/scripts/backup_sheet_state.py
```

This reads each V2 tab (`Data_Header_V2`, `Data_Lines_V2`) and writes into
`ops/backups/` (git-ignored):

```text
ops/backups/<YYYYMMDD_HHMMSS>_<SheetName>.json
ops/backups/<YYYYMMDD_HHMMSS>_manifest.json   # row counts + file paths
```

The script keeps only the **3 most recent** snapshot sets and prunes older ones.

## After a risky change

- Diff the new snapshot against the pre-change one to confirm only intended rows
  moved (row counts in the manifest are the quick check).
- If something is wrong, the JSON files are a full record to restore from — but
  restoration is manual and append-aware; re-read `Project_report` §3 first.

## Notes

- Snapshots live only in `ops/backups/` (git-ignored) — never commit them, never
  put them at the repo root.
- The script needs `GOOGLE_SHEET_ID` and `service_account.json` — run it in the
  container, not bare on the host.
