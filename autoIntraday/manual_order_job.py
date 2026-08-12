#!/usr/bin/env python3
"""Manual Intraday order runner — launched detached by the Manual Intraday page.

Three steps, in order: place the entry, poll until it fills, then arm a NATIVE Groww OCO
carrying the target and stop. The broker owns one-cancels-other, so both exit legs rest
safely and no orphan leg can survive to open a reverse position.

Every failure is recorded on the order row rather than raised, because the operator's next
action depends entirely on WHERE it failed: a rejected entry means nothing happened, while an
OCO failure after a fill means a live unprotected position that needs attention now.
See docs/superpowers/specs/2026-08-12-manual-intraday-orders-design.md."""
from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from manual_broker import is_filled, is_rejected

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("autointraday.manual_order")

POLL_S = 10
# A LIMIT entry may rest all session; MARKET resolves on the first poll.
DEADLINE_S = 6 * 60 * 60


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_order(store, broker, order_id: int, poll_s: int = POLL_S,
              deadline_s: int = DEADLINE_S, sleep=time.sleep) -> str:
    """Place, wait, arm. Returns the final status string. Never raises."""
    row = store.get_manual_order(order_id)
    if row is None:
        log.error("no manual order %s", order_id)
        return "ERROR"

    try:
        res = broker.place_entry(row["symbol"], row["side"], row["quantity"],
                                 row["entry_type"], row["entry_price"])
    except Exception as e:                                           # noqa: BLE001
        log.warning("manual entry failed for %s: %s", row["symbol"], e)
        store.update_manual_order(order_id, status="REJECTED", error=str(e)[:500])
        return "REJECTED"

    entry_order_id = res.get("order_id")
    store.update_manual_order(order_id, status="ENTRY_PENDING",
                              entry_order_id=entry_order_id)

    status, fill_price, waited = res.get("status"), res.get("price"), 0
    reason = None
    while not is_filled(status) and not is_rejected(status) and waited < deadline_s:
        sleep(poll_s)
        waited += poll_s
        try:
            st = broker.order_status(entry_order_id)
        except Exception as e:                                       # noqa: BLE001
            log.warning("status poll failed for %s: %s", entry_order_id, e)
            continue
        status = st.get("status")
        if st.get("price") is not None:
            fill_price = st.get("price")
        reason = st.get("reason")

    if is_rejected(status):
        store.update_manual_order(order_id, status="REJECTED",
                                  error=f"entry {status}: {reason or ''}"[:500])
        return "REJECTED"
    if not is_filled(status):
        log.info("manual order %s still resting at the deadline", order_id)
        return "ENTRY_PENDING"

    store.update_manual_order(order_id, status="FILLED", filled_at=_now(),
                              fill_price=fill_price)
    try:
        oco = broker.arm_oco(row["symbol"], row["side"], row["quantity"],
                             target=row["target"], stop=row["stop"])
    except Exception as e:                                           # noqa: BLE001
        log.error("OCO arm FAILED for %s — position is OPEN and UNPROTECTED: %s",
                  row["symbol"], e)
        store.update_manual_order(order_id, error=f"OCO arm failed: {e}"[:500])
        return "FILLED"
    store.update_manual_order(order_id, status="ARMED", oco_order_id=oco.get("order_id"))
    log.info("manual order %s armed: %s", order_id, oco.get("order_id"))
    return "ARMED"


def main() -> int:
    from settings import load_settings
    settings = load_settings()
    settings.apply_to_environ()

    from store import Store
    os.makedirs(os.path.dirname(settings.db_path), exist_ok=True)
    store = Store(settings.db_path)

    order_id = int(sys.argv[sys.argv.index("--order") + 1])
    row = store.get_manual_order(order_id)
    if row is None:
        log.error("no manual order %s", order_id)
        return 1

    from manual_broker import ManualBroker
    run_order(store, ManualBroker(mode=row["mode"]), order_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
