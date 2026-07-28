#!/usr/bin/env python3
"""Manual smoke test: verify Groww read access (no orders placed).

Gateway mode (recommended): set GROWW_GATEWAY_URL + GROWW_GATEWAY_TOKEN in .env — uses live
mode via the VPS relay.

Legacy direct mode: set GROWW_API_KEY + GROWW_TOTP_SECRET on a whitelisted static IP.

Usage: .venv/bin/python scripts/smoke_test_groww_auth.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, ".")

from settings import load_settings

load_settings().apply_to_environ()

from groww_client import GrowwClient, GrowwClientError


def main() -> None:
    use_gateway = bool(os.environ.get("GROWW_GATEWAY_URL", "").strip())
    mode = "live" if use_gateway else "paper"
    client = GrowwClient(mode=mode)
    try:
        if use_gateway:
            client.ensure_ready()
            print(f"gateway: OK ({os.environ['GROWW_GATEWAY_URL']})")
        else:
            client.authenticate()
            print("auth: OK (direct SDK)")
        holdings = client.get_holdings()
        print(f"get_holdings: OK ({len(holdings)} holdings)")
        print("credentials + read access verified.")
    except GrowwClientError as e:
        print(f"FAILED: {e}")
        sys.exit(1)

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
