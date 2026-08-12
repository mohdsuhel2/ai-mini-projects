#!/usr/bin/env python3
"""Manual-analysis job — launched detached by the dashboard's Analyze page. The run row and,
in symbols mode, its PENDING rows already exist (the dashboard creates them so the UI has
something to show the instant you click), so this job needs only the run id.

Fully defensive: a single symbol failing marks just that row ERROR and the run continues —
losing four good analyses because the fifth stock's data tool timed out would be absurd.
Never places orders; never authenticates to Groww.
See docs/superpowers/specs/2026-08-12-manual-analysis-page-design.md."""
from __future__ import annotations

import logging
import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("autointraday.analysis")


def _install_quiet_sigterm() -> None:
    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))


def run_analysis(store, engine, run_id: int) -> int:
    """Analyze every still-PENDING symbol of `run_id`, one at a time so the UI can show
    progress. Held symbols get their position as context. Never raises."""
    _install_quiet_sigterm()
    store.set_analysis_pid(run_id, os.getpid())
    by_symbol = {p["symbol"]: p for p in store.get_broker_positions()}
    pending = [r["symbol"] for r in store.get_analysis_results(run_id)
               if r["status"] == "PENDING"]
    for symbol in pending:
        store.update_analysis_result(run_id, symbol, "ANALYZING")
        try:
            item = engine.run_symbol(symbol, position=by_symbol.get(symbol))
            store.update_analysis_result(run_id, symbol, "DONE", item=item,
                                         raw=item.get("raw"))
        except Exception as e:                                       # noqa: BLE001
            log.warning("analysis failed for %s: %s", symbol, e)
            store.update_analysis_result(run_id, symbol, "ERROR", error=str(e)[:500])
    total = store.analysis_progress(run_id)["total"]
    store.finish_analysis_run(run_id, "SUCCESS", num_symbols=total)
    log.info("analysis run %d done: %d symbol(s)", run_id, total)
    return run_id


def run_top5(store, engine, run_id: int) -> int:
    """One call in which the skill screens and names its own picks. A failure here has no
    per-symbol granularity to fall back on, so the whole run is marked FAILED."""
    _install_quiet_sigterm()
    store.set_analysis_pid(run_id, os.getpid())
    try:
        picks = engine.run_top5()
    except Exception as e:                                           # noqa: BLE001
        log.exception("top-5 run failed")
        store.finish_analysis_run(run_id, "FAILED", error=str(e)[:500])
        return run_id
    for item in picks:
        store.add_analysis_result(run_id, item, item.get("raw"))
    store.finish_analysis_run(run_id, "SUCCESS", num_symbols=len(picks))
    log.info("top-5 run %d done: %d pick(s)", run_id, len(picks))
    return run_id


def main() -> int:
    from settings import load_settings
    settings = load_settings()
    settings.apply_to_environ()

    from store import Store
    os.makedirs(os.path.dirname(settings.db_path), exist_ok=True)
    store = Store(settings.db_path)

    run_id = int(sys.argv[sys.argv.index("--run") + 1])
    run = next((r for r in store.get_analysis_runs(limit=200) if r["id"] == run_id), None)
    if run is None:
        log.error("no analysis run %d", run_id)
        return 1

    from manual_engine import ManualSkillEngine
    engine = ManualSkillEngine(run["skill_id"])
    if run["mode"] == "top5":
        run_top5(store, engine, run_id)
    else:
        run_analysis(store, engine, run_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
