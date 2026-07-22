"""Pure view functions for the dashboard — take a Store, return plain Python. No Streamlit,
no broker, no LLM, so they are fully unit-testable. See
docs/superpowers/specs/2026-07-10-dashboard-design.md."""
from __future__ import annotations


def header_view(store) -> dict:
    cfg = store.get_config()
    deployed = store.deployed_capital()
    util = round(deployed / cfg.total_pool * 100, 1) if cfg.total_pool else 0.0
    return {
        "mode": cfg.mode, "is_paused": cfg.is_paused, "total_pool": cfg.total_pool,
        "deployed_capital": deployed, "utilization_pct": util,
        "open_count": store.count_open_positions(),
        "pending_count": len(store.get_pending_positions()),
        "max_open_positions": cfg.max_open_positions,
        "capital_per_position": cfg.capital_per_position,
    }


def pending_view(store) -> list[dict]:
    """Resting orders not yet filled — what WILL happen: each waits for price to reach `rest_at`,
    then fills with the shown target/stop. Cancelled at square-off if never reached."""
    return [
        {"symbol": p.symbol, "side": p.side, "quantity": p.quantity,
         "rest_at": p.entry_price, "target": p.target_price, "stop": p.stop_loss,
         "placed_at": p.opened_at}
        for p in store.get_pending_positions()
    ]


def positions_view(store, limit: int = 50) -> list[dict]:
    return [
        {"symbol": p.symbol, "side": p.side, "quantity": p.quantity,
         "entry_price": p.entry_price, "target_price": p.target_price, "stop_loss": p.stop_loss,
         "status": p.status, "exit_price": p.exit_price, "exit_reason": p.exit_reason,
         "realized_pnl": p.realized_pnl, "opened_at": p.opened_at, "closed_at": p.closed_at}
        for p in store.get_recent_positions(limit)
    ]


def pnl_summary(store, today_iso: str) -> dict:
    return {
        "realized_total": store.realized_pnl_total(),
        "realized_today": store.realized_pnl_since(today_iso),
        "open_count": store.count_open_positions(),
    }


def performance_view(store) -> dict:
    """Strategy performance over closed trades — the decide-to-scale-or-stop numbers."""
    return store.performance_summary()


def exit_reasons_view(store) -> list[dict]:
    return store.exit_reason_breakdown()


def decisions_view(store, limit: int = 50) -> list[dict]:
    return [_decision_row(d) for d in store.get_recent_decisions(limit)]


def runs_view(store, limit: int = 20) -> list[dict]:
    return [_run_row(r) for r in store.get_recent_runs(limit)]


# Actions that represent a real OPERATION (not a screening/management decision), with a
# human label for the activity log. Entry placements are handled separately (see below).
_OP_LABELS = {
    "FILL": "Filled", "EXIT": "Exit", "ADD": "Added more",
    "ADJUSTED": "Adjusted SL/target", "ADOPTED": "Adopted", "CANCEL": "Cancelled",
}
_ENTRY_ACTIONS = ("BUY_NOW", "SHORT_NOW", "BUY_ON_PULLBACK", "BUY_ON_BREAKOUT")


def activity_log(store, start_iso: str, end_iso: str) -> list[dict]:
    """Chronological (newest-first) log of what the bot actually DID in the window — the real
    operations only, filtering out the screening/management chatter (WAIT / below-gate / HOLD).
    Each row: time (rendered HH:MM:SS IST by the dashboard), symbol, event, detail."""
    rows = []
    for d in store.get_decisions_between(start_iso, end_iso):
        if d.action in _OP_LABELS:
            event = _OP_LABELS[d.action]
        elif d.action in _ENTRY_ACTIONS and (d.reason or "").startswith("resting"):
            event = "Order placed"
        else:
            continue
        pnl = ""
        if d.action == "EXIT" and d.position_id is not None:
            # The realized P&L is booked on the position at close — show what this exit made/lost.
            try:
                rp = store.get_position(d.position_id).realized_pnl
                if rp is not None:
                    pnl = f"₹{rp:,.2f}"
            except Exception:
                pass
        rows.append({"time": d.created_at, "symbol": d.symbol, "event": event,
                     "detail": d.reason or "", "P&L": pnl})
    return rows


def _decision_row(d) -> dict:
    # `time` leads and holds the raw UTC created_at; the dashboard renders it as HH:MM:SS IST
    # (the table is day-scoped, so time-of-day is what's useful, not the redundant date).
    return {"time": d.created_at, "symbol": d.symbol, "action": d.action, "score": d.score,
            "reason": d.reason, "entry_price": d.entry_price, "target_price": d.target_price,
            "stop_loss": d.stop_loss}


def _run_row(r) -> dict:
    return {"id": r.id, "started_at": r.started_at, "finished_at": r.finished_at,
            "status": r.status, "mode": r.mode, "num_candidates": r.num_candidates,
            "num_actions": r.num_actions, "summary": r.summary, "error": r.error}


# ---- day-scoped views: the dashboard shows one (IST) day at a time --------------------------

def decisions_for_day(store, start_iso: str, end_iso: str) -> list[dict]:
    return [_decision_row(d) for d in store.get_decisions_between(start_iso, end_iso)]


def runs_for_day(store, start_iso: str, end_iso: str) -> list[dict]:
    return [_run_row(r) for r in store.get_runs_between(start_iso, end_iso)]


def closed_positions_for_day(store, start_iso: str, end_iso: str) -> list[dict]:
    return [
        {"symbol": p.symbol, "side": p.side, "quantity": p.quantity,
         "entry_price": p.entry_price, "exit_price": p.exit_price,
         "exit_reason": p.exit_reason, "realized_pnl": p.realized_pnl,
         "opened_at": p.opened_at, "closed_at": p.closed_at}
        for p in store.get_closed_positions_between(start_iso, end_iso)
    ]


def realized_for_day(store, start_iso: str, end_iso: str) -> float:
    return store.realized_pnl_between(start_iso, end_iso)
