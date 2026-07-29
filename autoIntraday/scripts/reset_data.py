#!/usr/bin/env python3
"""Reset the trading/analysis DATA while keeping settings (the config row).

Clears positions, orders, decisions, job_runs, holdings, and swing history — and resets their
auto-increment counters so new ids start at 1 — for a clean slate. The `config` table (pool,
sizing, strategies, profit-taking, exit mode, execution margins) is left UNTOUCHED.

Always backs up the DB first. READ-ONLY (prints counts) unless --confirm is given.

Usage:
    .venv/bin/python scripts/reset_data.py            # dry-run: show what would be cleared
    .venv/bin/python scripts/reset_data.py --confirm  # back up, then clear the data
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, ".")

# Deleted child-first (harmless even with FKs off). config is deliberately NOT here.
DATA_TABLES = ["decisions", "orders", "positions", "job_runs", "holdings",
               "swing_verdicts", "swing_runs"]


def main() -> int:
    confirm = "--confirm" in sys.argv
    from settings import load_settings
    db = load_settings().db_path
    if not os.path.exists(db):
        print(f"no DB at {db}")
        return 1
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    existing = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    tables = [t for t in DATA_TABLES if t in existing]

    print(f"DB: {db}")
    print("Rows to clear (config is KEPT):")
    for t in tables:
        n = conn.execute(f"SELECT COUNT(*) n FROM {t}").fetchone()["n"]
        print(f"  {t:16} {n}")
    keep = conn.execute("SELECT COUNT(*) n FROM config").fetchone()["n"]
    print(f"  {'config (KEPT)':16} {keep}")

    if not confirm:
        print("\nDRY-RUN. Re-run with --confirm to back up + clear.")
        return 0

    backup_dir = os.path.join(os.path.dirname(db), "backups")
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = os.path.join(backup_dir, f"autointraday-{stamp}.db")
    shutil.copy2(db, backup)
    print(f"\nbacked up -> {backup}")

    for t in tables:
        conn.execute(f"DELETE FROM {t}")
    # reset AUTOINCREMENT so fresh ids start at 1 (table may be absent if no autoincrement used yet)
    if "sqlite_sequence" in existing:
        conn.executemany("DELETE FROM sqlite_sequence WHERE name = ?", [(t,) for t in tables])
    conn.commit()
    conn.execute("VACUUM")
    conn.commit()
    conn.close()
    print("cleared:", ", ".join(tables))
    print("config kept. Fresh slate ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
