#!/usr/bin/env python3
"""Manual, not-CI smoke test: run ONE real paper trading cycle end to end against a temp DB,
the real GrowwClient in PAPER mode (no real orders), the real decision engine, and the real
screener/indicators. Prints the run summary.

Usage: .venv/bin/python scripts/smoke_test_cycle.py
Requires ANTHROPIC_API_KEY (or `ant` login), Groww creds for read-only auth, and the sibling
StockAnalayze venv. PLACES NO REAL ORDERS (paper mode).
"""
from __future__ import annotations

import sys
import tempfile

sys.path.insert(0, ".")

from settings import load_settings
load_settings().apply_to_environ()

from decision_engine import DecisionEngine
from groww_client import GrowwClient
from indicators import get_indicators
from orchestrator import Orchestrator
from screener import get_candidates
from store import Store


def main() -> None:
    db_path = tempfile.mktemp(suffix=".db")
    store = Store(db_path)
    store.update_config(mode="paper", total_pool=100000.0, max_open_positions=3,
                        capital_per_position=20000.0, is_paused=False)
    orch = Orchestrator(
        store=store, client=GrowwClient(mode="paper"), engine=DecisionEngine(use_web_search=True),
        get_indicators=get_indicators, get_candidates=get_candidates)
    print(f"running one paper cycle (temp db {db_path}) ...")
    summary = orch.run_cycle()
    print(f"cycle: {summary['status']} - {summary['entries']} entries, "
          f"{summary['exits']} exits, {summary['candidates']} candidates screened")
    for p in store.get_open_positions():
        print(f"  OPEN {p.side} {p.quantity} {p.symbol} @ {p.entry_price} "
              f"target {p.target_price} stop {p.stop_loss}")


if __name__ == "__main__":
    main()
