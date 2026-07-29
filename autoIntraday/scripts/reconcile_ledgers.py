#!/usr/bin/env python3
"""Reconcile the DB's live positions against what Groww actually holds.

Why: switching the live strategy mid-session leaves positions ORPHANED in inactive strategy
ledgers, and some DB "open" rows are PHANTOM (already squared at the broker but still OPEN in the
DB). This tool lines the two up so the bot's view matches reality — a prerequisite before enabling
the eager exit modes (which rest real broker orders).

Safety: READ-ONLY by default — it only prints the diff. With --apply it makes DB-ONLY changes
(close phantom OPEN rows, cancel phantom PENDING rows). It NEVER places or cancels broker orders.

Usage:
    .venv/bin/python scripts/reconcile_ledgers.py            # dry-run report
    .venv/bin/python scripts/reconcile_ledgers.py --apply    # also fix the DB (phantoms only)
    .venv/bin/python scripts/reconcile_ledgers.py --offline  # DB ledger view only (no broker call)
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")


def reconcile_view(db_positions, broker_positions, active_strategy):
    """Pure diff. db_positions: objects with .symbol/.side/.quantity/.status/.strategy_id/.id.
    broker_positions: dicts {symbol, quantity}. Returns dict of lists:
      real    — one DB row per symbol Groww actually holds (the active-ledger row preferred),
      phantom — DB rows with no matching broker position (incl. duplicate rows for a held symbol),
      unknown — (symbol, net_qty) Groww holds that the DB has no open row for.
    Duplicates across ledgers collapse to ONE real row per held symbol; the rest are phantom."""
    held: dict[str, int] = {}
    for b in broker_positions:
        sym = (b.get("symbol") or "").upper()
        if sym:
            held[sym] = held.get(sym, 0) + int(b.get("quantity") or 0)
    by_sym: dict[str, list] = {}
    for p in db_positions:
        by_sym.setdefault((p.symbol or "").upper(), []).append(p)
    real, phantom = [], []
    for sym, rows in by_sym.items():
        if held.get(sym, 0) != 0:
            # keep ONE as real — prefer the active ledger, then lowest id; the rest are duplicates
            ordered = sorted(rows, key=lambda p: (p.strategy_id != active_strategy, p.id))
            real.append(ordered[0])
            phantom.extend(ordered[1:])
        else:
            phantom.extend(rows)
    unknown = [(s, q) for s, q in held.items() if q != 0 and s not in by_sym]
    return {"real": real, "phantom": phantom, "unknown": unknown, "held": held}


def _live_db_positions(store):
    rows = store._conn.execute(
        "SELECT * FROM positions WHERE status IN ('OPEN','PENDING') AND mode='live' ORDER BY id"
    ).fetchall()
    return [store._row_to_position(r) for r in rows]


def _p(p):
    return f"#{p.id} {p.strategy_id:12} {p.symbol:10} {p.side:5} {p.status:7} qty={p.quantity}"


def main() -> int:
    apply = "--apply" in sys.argv
    offline = "--offline" in sys.argv
    from settings import load_settings
    settings = load_settings()
    settings.apply_to_environ()
    from store import Store
    store = Store(settings.db_path)
    active = store.get_config().live_strategy
    db_positions = _live_db_positions(store)

    print(f"active live ledger: {active}")
    print(f"DB open/pending live positions: {len(db_positions)}")
    for p in db_positions:
        print("  " + _p(p))

    if offline:
        print("\n--offline: skipping broker read.")
        return 0

    from groww_client import GrowwClient, GrowwClientError
    client = GrowwClient(mode="live")
    try:
        client.authenticate()
        broker = client.get_positions()
    except GrowwClientError as e:
        print(f"\nBROKER READ FAILED: {e}\n(Groww auth may still be rate-limit clamped — retry "
              "after the 6:00 IST token reset.)")
        return 1

    print(f"\nGroww actually holds {len(broker)} position(s):")
    for b in broker:
        print(f"  {b['symbol']:10} qty={b['quantity']} avg={b.get('avg_price')}")

    view = reconcile_view(db_positions, broker, active)
    print(f"\n=== DIFF ===")
    print(f"REAL (backed by a broker position) — {len(view['real'])}:")
    for p in view["real"]:
        print("  keep   " + _p(p))
    print(f"PHANTOM (no broker position — DB thinks open, broker is flat) — {len(view['phantom'])}:")
    for p in view["phantom"]:
        print("  close  " + _p(p))
    print(f"UNKNOWN (Groww holds, DB has no open row) — {len(view['unknown'])}:")
    for sym, qty in view["unknown"]:
        print(f"  adopt? {sym} net_qty={qty}")

    if not apply:
        print("\nDRY-RUN. Re-run with --apply to CLOSE phantom rows / CANCEL phantom pendings "
              "in the DB (no broker orders are touched).")
        return 0

    closed = cancelled = 0
    for p in view["phantom"]:
        if p.status == "PENDING":
            store.cancel_position(p.id, "RECONCILE")
            cancelled += 1
        else:                                   # OPEN phantom: broker already flat, exit unknown
            store.close_position(p.id, exit_price=p.entry_price, exit_reason="RECONCILE",
                                  realized_pnl=p.booked_pnl or 0.0)
            closed += 1
    print(f"\nAPPLIED (DB only): closed {closed} phantom OPEN, cancelled {cancelled} phantom PENDING.")
    print("NOTE: reconciled OPEN rows kept booked_pnl only — their real broker exit P&L was not "
          "captured by the bot (they were closed outside it). REAL rows left untouched; if any sit "
          "in an inactive ledger, decide per-position whether to square off manually at Groww.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
