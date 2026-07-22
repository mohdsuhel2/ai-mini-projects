#!/usr/bin/env python3
"""Manual, not-CI: the 1-share LIVE broker verification — exercises every real order path the
bot uses, with minimal money at risk, and prints every RAW status string so the assumed
_FILLED_STATES/_REJECTED_STATES and smart-order semantics can be confirmed or corrected.

Stage A (no fill risk): LIMIT BUY 1 share ~8%% below LTP -> poll status -> cancel -> status.
Stage B (1 share of a cheap stock): MARKET BUY 1 -> poll to fill -> OCO (legs far away, won't
fire) -> smart-order status -> modify legs -> cancel OCO -> MARKET SELL 1 -> poll to fill ->
confirm flat at broker. The position is ALWAYS flattened in a finally block.

Usage: export $(cat .env | xargs) && .venv/bin/python scripts/verify_live_1share.py [SYMBOL]
Default symbol: IDEA (ultra-liquid, ~single-digit rupees -> 1 share risks almost nothing).
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, ".")

from settings import load_settings
load_settings().apply_to_environ()

from groww_client import GrowwClient
from indicators import get_indicators

SYMBOL = (sys.argv[1] if len(sys.argv) > 1 else "IDEA").upper()
MAX_PRICE = 500.0          # refuse to run the fill stage on an expensive symbol
POLL_S, POLL_TRIES = 2, 15


def tick(px: float) -> float:
    return round(round(px / 0.05) * 0.05, 2)


def poll(label, fn, done_states):
    for i in range(POLL_TRIES):
        st = fn()
        print(f"  [{label}] poll {i+1}: RAW status = {st['status']!r}")
        if str(st["status"]).upper() in done_states:
            return st
        time.sleep(POLL_S)
    print(f"  [{label}] WARNING: never reached {done_states} in {POLL_TRIES} polls")
    return st


def main() -> None:
    client = GrowwClient(mode="live")
    client.authenticate()
    print("auth: OK")

    ltp = float(get_indicators(SYMBOL)["price"]["last"])
    print(f"{SYMBOL} indicator LTP = {ltp}")
    if ltp > MAX_PRICE:
        print(f"ABORT: {SYMBOL} @ {ltp} > {MAX_PRICE} — pick a cheaper symbol")
        sys.exit(1)

    # ---------- Stage A: place/status/cancel with NO fill risk ----------
    print("\nSTAGE A — resting LIMIT far below market (will not fill)")
    low = tick(ltp * 0.92)
    o = client.place_order(symbol=SYMBOL, exchange="NSE", transaction_type="BUY", quantity=1,
                           order_type="LIMIT", price=low, product="MIS")
    print(f"  placed LIMIT BUY 1 @ {low}: id={o['order_id']} RAW status = {o['status']!r}")
    st = client.get_order_status(o["order_id"])
    print(f"  open-order RAW status = {st['status']!r}")
    c = client.cancel_order(o["order_id"])
    print(f"  cancel RAW status = {c['status']!r}")
    poll("A-cancelled", lambda: client.get_order_status(o["order_id"]),
         ("CANCELLED", "CANCELED"))

    # ---------- Stage B: real 1-share round trip + OCO lifecycle ----------
    print("\nSTAGE B — 1-share MARKET round trip + OCO create/modify/cancel")
    held = 0
    oco_id = None
    try:
        b = client.place_order(symbol=SYMBOL, exchange="NSE", transaction_type="BUY",
                               quantity=1, order_type="MARKET", product="MIS")
        print(f"  MARKET BUY 1: id={b['order_id']} RAW status = {b['status']!r}")
        fill = poll("B-buy", lambda: client.get_order_status(b["order_id"]),
                    ("EXECUTED", "COMPLETE", "COMPLETED", "FILLED"))
        if str(fill["status"]).upper() not in ("EXECUTED", "COMPLETE", "COMPLETED", "FILLED"):
            print("ABORT stage B: buy never confirmed filled")
            return
        held = 1

        target, stop = tick(ltp * 1.20), tick(ltp * 0.80)   # far away — must not fire
        try:
            oco = client.place_oco_order(
                symbol=SYMBOL,
                entry={"transaction_type": "SELL", "quantity": 1, "order_type": "MARKET"},
                target={"trigger_price": target, "order_type": "LIMIT", "price": target},
                stop_loss={"trigger_price": stop, "order_type": "LIMIT", "price": stop})
            oco_id = oco["order_id"]
            print(f"  OCO placed: id={oco_id} RAW status = {oco['status']!r}")
            s = client.get_smart_order_status(oco_id)
            print(f"  OCO RAW status = {s['status']!r}")
            m = client.modify_oco_order(oco_id, target=tick(ltp * 1.15),
                                        stop_loss=tick(ltp * 0.85))
            print(f"  OCO modify RAW status = {m['status']!r}")
        except Exception as e:
            print(f"  OCO path FAILED (position still held, will flatten): {e}")
    finally:
        if oco_id:
            try:
                x = client.cancel_oco_order(oco_id)
                print(f"  OCO cancel RAW status = {x['status']!r}")
            except Exception as e:
                print(f"  OCO cancel FAILED: {e} — CHECK THE ORDER IN THE GROWW APP")
        if held:
            s = client.place_order(symbol=SYMBOL, exchange="NSE", transaction_type="SELL",
                                   quantity=1, order_type="MARKET", product="MIS")
            print(f"  MARKET SELL 1: id={s['order_id']} RAW status = {s['status']!r}")
            poll("B-sell", lambda: client.get_order_status(s["order_id"]),
                 ("EXECUTED", "COMPLETE", "COMPLETED", "FILLED"))

    net = sum(int(p["quantity"]) for p in client.get_positions()
              if p["symbol"] == SYMBOL)
    print(f"\nbroker net quantity for {SYMBOL} = {net} (expected 0)")
    print("verification complete — compare RAW statuses above with orchestrator's "
          "_FILLED_STATES/_REJECTED_STATES.")


if __name__ == "__main__":
    main()
