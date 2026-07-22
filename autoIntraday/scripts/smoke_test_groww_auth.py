#!/usr/bin/env python3
"""Manual, not-CI smoke test: authenticate for real and call read-only endpoints only.

Safe to run against a live Groww account — places no orders. Confirms GROWW_API_KEY /
GROWW_TOTP_SECRET are valid and the SDK is reachable end-to-end.

Usage: GROWW_API_KEY=... GROWW_TOTP_SECRET=... .venv/bin/python scripts/smoke_test_groww_auth.py
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from settings import load_settings
load_settings().apply_to_environ()

from groww_client import GrowwClient, GrowwClientError


def main() -> None:
    client = GrowwClient(mode="paper")
    try:
        client.authenticate()
        print("auth: OK")
        holdings = client.get_holdings()
        print(f"get_holdings: OK ({len(holdings)} holdings)")
        print("credentials + read access verified.")
    except GrowwClientError as e:
        print(f"FAILED: {e}")
        sys.exit(1)

    # Optional bonus: live LTP. This is Groww's separate "Live Data" API category and is NOT
    # used by the trading system (the orchestrator prices everything from the indicator tool).
    # So a failure here (e.g. "Access forbidden" when the account's plan lacks Live Data access)
    # is a WARNING, not a failure — it does not block paper or live trading.
    if holdings:
        symbol = holdings[0]["symbol"]
        try:
            ltp = client.get_ltp([symbol])
            print(f"get_ltp (bonus): OK ({symbol} = {ltp[symbol]})")
        except GrowwClientError as e:
            print(f"get_ltp (bonus): SKIPPED — {e}")
            print("  (Live Data API not available on this plan; not needed by the system.)")


if __name__ == "__main__":
    main()
