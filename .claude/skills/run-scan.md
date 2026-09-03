---
name: run-scan
description: >
  Bring up the Docker stack and run a Google Drive inbox scan end-to-end (OCR →
  classify → duplicate-check → write to Sheets), then tail the logs. Use when the
  user asks to "run a scan", "process the inbox", "test the pipeline against real
  receipts", or verify a change works against the live Drive folder.
---

# Skill: run a Drive inbox scan

## 1. Bring up the stack

```bash
docker compose up -d --build
docker compose ps          # confirm procurement-server + frontend are Up
```

If `requirements.txt` / `Dockerfile` changed, add `--force-recreate`.

## 2. Health check

```bash
curl -s http://localhost:8080/health
curl -s http://localhost:8080/api/v1/status
```

Both should return OK before scanning. If not, check `docker logs procurement-server`.

## 3. Trigger the scan

```bash
curl -s -X POST http://localhost:8080/api/v1/drive/scan | tee /tmp/scan_result.json
```

This scans the Drive inbox folder (`GOOGLE_DRIVE_FOLDER_ID`), runs the hybrid OCR
pipeline, classifies into DT1–DT4, runs two-tier duplicate detection, and appends
rows to `Data_Header_V2` / `Data_Lines_V2`.

Alternatively use the background scanner controls:
`POST /api/v1/auto-scan/trigger` (one-off), `/toggle`, `/stop`.

## 4. Watch it work

```bash
docker logs -f procurement-server
```

Look for: per-file OCR engine used (Gemini / Ollama / Tesseract), assigned
`DTnXXXX` codes, duplicate flags, and the Exact Math Row Guard passing before
each Lines write.

## 5. Verify results

- Dashboard: http://localhost:8080  (scan summary)
- Review UI: http://localhost:8080/verify.html  (per-document, filter by DT group / status)
- Or inspect the sheet directly.

## Guardrails

- This writes to the **live** Google Sheet. Confirm with the user before running
  against production Drive/Sheet IDs.
- Never clears or rewrites existing rows — append-only. If a run reports a row-
  guard mismatch, stop and investigate; do not re-run blindly.
