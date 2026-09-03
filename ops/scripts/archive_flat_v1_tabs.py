"""
Archive the legacy flat V1 sheet tabs (Stage 1 of the flat-table removal).

Renames `Bang_Ke_Hoa_Don` -> `_ARCHIVE_Bang_Ke_Hoa_Don_<date>` and
`Links_Hoa_Don` -> `_ARCHIVE_Links_Hoa_Don_<date>`, hides them, and greys the
tab colour. Data is NOT deleted -- this is the reversible freeze step before the
V1 flat tables are dropped for good (see Project_report flat-table removal plan).

Run a full snapshot first: `python ops/scripts/backup_sheet_state.py`.

Usage:
    python ops/scripts/archive_flat_v1_tabs.py            # apply
    python ops/scripts/archive_flat_v1_tabs.py --dry-run  # show plan only
    python ops/scripts/archive_flat_v1_tabs.py --revert   # undo (rename back, unhide)
"""
import os
import sys
import argparse
from datetime import datetime

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from services.google_service import GoogleSyncService

ARCHIVE_DATE = "20260903"

# original title -> archived title
RENAME_MAP = {
    "Bang_Ke_Hoa_Don": f"_ARCHIVE_Bang_Ke_Hoa_Don_{ARCHIVE_DATE}",
    "Links_Hoa_Don": f"_ARCHIVE_Links_Hoa_Don_{ARCHIVE_DATE}",
}
GREY = {"red": 0.6, "green": 0.6, "blue": 0.6}


def build_requests(worksheets, mapping, hidden):
    reqs = []
    for ws in worksheets:
        if ws.title in mapping:
            props = {"sheetId": ws.id, "title": mapping[ws.title], "hidden": hidden}
            fields = "title,hidden"
            if hidden:
                props["tabColor"] = GREY
                fields += ",tabColor"
            reqs.append({"updateSheetProperties": {"properties": props, "fields": fields}})
    return reqs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print plan, do not write")
    ap.add_argument("--revert", action="store_true", help="rename archived tabs back and unhide")
    args = ap.parse_args()

    svc = GoogleSyncService()
    sid = os.getenv("GOOGLE_SHEET_ID")
    sh = svc.gspread_client.open_by_key(sid)
    worksheets = sh.worksheets()

    if args.revert:
        mapping = {v: k for k, v in RENAME_MAP.items()}
        hidden = False
        label = "REVERT"
    else:
        mapping = dict(RENAME_MAP)
        hidden = True
        label = "ARCHIVE"

    targets = [ws for ws in worksheets if ws.title in mapping]
    if not targets:
        print(f"[{label}] No matching tabs found. Nothing to do.")
        print("  present tabs:", [ws.title for ws in worksheets])
        return

    for ws in targets:
        print(f"[{label}] {ws.title!r} -> {mapping[ws.title]!r}  (hidden={hidden})")

    if args.dry_run:
        print("[dry-run] no changes written.")
        return

    reqs = build_requests(worksheets, mapping, hidden)
    sh.batch_update({"requests": reqs})
    print(f"[{label}] applied {len(reqs)} tab update(s).")

    print("--- worksheets now ---")
    for ws in sh.worksheets():
        print(f"  {ws.title!r:45} hidden={ws._properties.get('hidden', False)}")


if __name__ == "__main__":
    main()
