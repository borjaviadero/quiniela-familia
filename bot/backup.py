"""Database backup and restore.

  python -m bot.backup export backup.json     # downloads every table to a JSON file
  python -m bot.backup restore backup.json    # loads it back into Supabase

The workflow .github/workflows/backup.yml runs the export weekly, encrypts the JSON
with the BACKUP_KEY secret and commits it as backups/quiniela.json.enc. The
repository is public: without the password the backup cannot be read.

To recover a backup (in a terminal with openssl):
  openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in backups/quiniela.json.enc -out backup.json
  python -m bot.backup restore backup.json
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from .db import DB

# In dependency order, so referenced rows exist before the rows that point to them
TABLES = ["players", "matchdays", "matches", "picks", "daily_reports", "payments"]
KEYS = {"players": "id", "matchdays": "id", "matches": "matchday_id,position",
        "picks": "matchday_id,player_id", "daily_reports": "matchday_id,day",
        "payments": "matchday_id,player_id"}
PAGE = 1000   # Supabase returns at most 1000 rows per request


def export(db: DB) -> dict:
    backup = {"created_at": datetime.now(timezone.utc).isoformat(), "tables": {}}
    for table in TABLES:
        rows, offset = [], 0
        order = ",".join(f"{k}.asc" for k in KEYS[table].split(","))
        while True:
            page = db.request("GET", table, {"select": "*", "order": order, "limit": PAGE, "offset": offset})
            rows += page
            if len(page) < PAGE:
                break
            offset += PAGE
        backup["tables"][table] = rows
    return backup


def restore(db: DB, backup: dict):
    for table in TABLES:
        rows = backup["tables"].get(table, [])
        for i in range(0, len(rows), 500):
            db.request("POST", table, {"on_conflict": KEYS[table]}, rows[i:i + 500],
                       prefer="resolution=merge-duplicates,return=minimal")
        print(f"{table}: {len(rows)} rows")
    print("Done. Run this in Supabase > SQL Editor so new matchdays keep numbering correctly:\n"
          "  select setval('matchdays_id_seq', (select coalesce(max(id), 1) from matchdays));")


def main(argv=None):
    argv = argv or sys.argv[1:]
    if len(argv) != 2 or argv[0] not in ("export", "restore"):
        print(__doc__)
        return 2
    db = DB()
    if argv[0] == "export":
        backup = export(db)
        with open(argv[1], "w", encoding="utf-8") as f:
            json.dump(backup, f, ensure_ascii=False, indent=1, sort_keys=True)
        print("Backup: " + ", ".join(f"{t} {len(rows)}" for t, rows in backup["tables"].items()))
    else:
        with open(argv[1], encoding="utf-8") as f:
            restore(db, json.load(f))
    return 0


if __name__ == "__main__":
    sys.exit(main())
