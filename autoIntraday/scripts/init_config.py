#!/usr/bin/env python3
"""One-time DB init: seed the trading config (mode/pool/caps/pause) from config.yaml's
trading_defaults. After this, the DB is the live source of truth — change these in the
dashboard, not the YAML. Safe to re-run (it just re-applies the YAML defaults).

Usage: .venv/bin/python scripts/init_config.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from settings import load_settings
from store import Store

_TRADING_FIELDS = ("mode", "total_pool", "max_open_positions", "capital_per_position",
                   "is_paused")


def seed_trading_config(store, defaults: dict) -> None:
    fields = {k: defaults[k] for k in _TRADING_FIELDS if k in defaults}
    if fields:
        store.update_config(**fields)


def main() -> int:
    settings = load_settings()
    os.makedirs(os.path.dirname(settings.db_path), exist_ok=True)
    store = Store(settings.db_path)
    seed_trading_config(store, settings.trading_defaults)
    cfg = store.get_config()
    print(f"seeded {settings.db_path}: mode={cfg.mode} pool={cfg.total_pool} "
          f"max_positions={cfg.max_open_positions} per_position={cfg.capital_per_position} "
          f"paused={cfg.is_paused}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
