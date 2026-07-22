#!/usr/bin/env python3
"""Swing holdings-analysis job — launched as a detached subprocess by the dashboard's
"Analyze my holdings" button. Authenticates to Groww, fetches holdings, runs the SwingEngine
(both horizons), and persists the run + per-holding verdicts. Fully defensive: any failure
marks the run FAILED with the message. Never places orders, never touches trading state.
See docs/superpowers/specs/2026-07-21-swing-page-design.md."""
from __future__ import annotations

import logging
import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("autointraday.swing")


def _install_quiet_sigterm() -> None:
    """Exit promptly and quietly on SIGTERM (the dashboard's Stop). The DB state (mark STOPPED,
    reset the mid-flight stock) is owned by the dashboard, so the job just needs to get out."""
    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))


def run_swing(store, client, engine, holdings=None, resume_run_id=None) -> int:
    """Do one analysis run against the given collaborators, ONE STOCK AT A TIME so the UI can
    show live per-stock progress. Seeds a PENDING row per holding, then fills each in. Returns
    the swing_runs id. A per-stock failure marks just that row ERROR and continues; only a
    setup failure (auth / holdings fetch) marks the whole run FAILED. Never raises.

    `holdings` may be supplied (e.g. the last-loaded snapshot); otherwise fetched from Groww.

    When `resume_run_id` is given, continue that stopped run: reuse its row, skip the Groww
    fetch and the seed, and process only the holdings still marked PENDING (the interrupted
    stock plus any not yet reached). DONE/ERROR rows are left as they are."""
    _install_quiet_sigterm()
    if resume_run_id is not None:
        run_id = resume_run_id
        store.set_swing_pid(run_id, os.getpid())
        holdings = store.resume_swing_run(run_id)
    else:
        run_id = store.start_swing_run()
        store.set_swing_pid(run_id, os.getpid())
        try:
            if holdings is None:
                client.authenticate()             # holdings need real Groww auth
                holdings = client.get_holdings()
                store.replace_holdings(holdings)   # keep the last-loaded snapshot fresh
        except Exception as e:
            log.exception("swing analysis setup failed")
            store.finish_swing_run(run_id, "FAILED", num_holdings=0, error=str(e)[:500])
            return run_id
        store.seed_swing_verdicts(run_id, holdings)

    for h in holdings:
        _analyze_stock(store, engine, run_id, h["symbol"], h.get("quantity"), h.get("avg_price"))
    # Report the full run size (all verdict rows), not just the slice processed this pass — a
    # resumed run only loops over the remaining PENDING holdings.
    total = store.swing_progress(run_id)["total"]
    store.finish_swing_run(run_id, "SUCCESS", num_holdings=total)
    log.info("swing analysis done: %d holdings (%d this pass)", total, len(holdings))
    return run_id


def _analyze_stock(store, engine, run_id, symbol, quantity, avg_price) -> None:
    """Analyze one stock and write its verdict row: ANALYZING → DONE (or ERROR on failure).
    Shared by the batch loop and the single-stock re-analyze path."""
    store.update_swing_verdict(run_id, symbol, "ANALYZING")
    try:
        v = engine.analyze_one(symbol, quantity, avg_price)
        store.update_swing_verdict(run_id, symbol, "DONE", swing=v["swing"],
                                   shortswing=v["shortswing"])
    except Exception as e:
        log.warning("swing analysis failed for %s: %s", symbol, e)
        store.update_swing_verdict(run_id, symbol, "ERROR")


def run_swing_one(store, client, engine, symbol, run_id=None) -> int:
    """Re-analyze a single holding. With `run_id`, update that stock's row IN PLACE in the given
    run (the row flips ANALYZING → DONE/ERROR; the rest of the run is untouched); qty/avg come
    from the existing row. Without `run_id`, run the stock as its own fresh single-stock run,
    with qty/avg looked up from the persisted holdings snapshot. No Groww auth either way."""
    _install_quiet_sigterm()
    if run_id is None:
        match = next((h for h in store.get_holdings() if h["symbol"] == symbol), None)
        holding = match or {"symbol": symbol, "quantity": None, "avg_price": None}
        return run_swing(store, client, engine, holdings=[holding])
    row = next((r for r in store.get_swing_verdicts(run_id) if r["symbol"] == symbol), None)
    if row is None:
        # Stock bought after this run started — not in its verdicts. Seed a row (qty/avg from the
        # holdings snapshot) so it joins the run; otherwise update_swing_verdict would no-op on a
        # non-existent row and the analysis would silently vanish.
        h = next((x for x in store.get_holdings() if x["symbol"] == symbol), None)
        store.seed_swing_verdicts(run_id, [h or {"symbol": symbol}])
        qty, avg = (h.get("quantity"), h.get("avg_price")) if h else (None, None)
    else:
        qty, avg = row["quantity"], row["avg_price"]
    _analyze_stock(store, engine, run_id, symbol, qty, avg)
    return run_id


def main() -> int:
    from settings import load_settings
    settings = load_settings()
    settings.apply_to_environ()

    from store import Store
    os.makedirs(os.path.dirname(settings.db_path), exist_ok=True)
    store = Store(settings.db_path)

    from groww_client import GrowwClient
    from swing_engine import SwingEngine
    client, engine = GrowwClient(mode="live"), SwingEngine(use_web_search=True)

    if "--symbol" in sys.argv:
        run_id = int(sys.argv[sys.argv.index("--run") + 1]) if "--run" in sys.argv else None
        run_swing_one(store, client, engine, sys.argv[sys.argv.index("--symbol") + 1],
                      run_id=run_id)
    elif "--resume" in sys.argv:
        run_swing(store, client, engine,
                  resume_run_id=int(sys.argv[sys.argv.index("--resume") + 1]))
    else:
        run_swing(store, client, engine)
    return 0


if __name__ == "__main__":
    sys.exit(main())
