"""Strategy-comparison analytics — pure aggregations over the store's per-strategy ledgers.

No Streamlit here so it stays unit-testable; the comparison dashboard renders these results. Every
function is strategy-scoped (positions/decisions filtered by strategy_id), so strategies never
bleed into each other's numbers. See docs/superpowers/specs/2026-07-24-multi-strategy-compare-design.md."""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone

_IST = timedelta(hours=5, minutes=30)


def _ist_day(iso: str | None) -> str:
    """The IST calendar date (YYYY-MM-DD) of a stored UTC-ISO timestamp — the trading day."""
    try:
        dt = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return (iso or "")[:10]
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt.astimezone(timezone.utc) + _IST).date().isoformat()


def _streaks(pnls: list[float]) -> tuple[int, int]:
    """(max consecutive wins, max consecutive losses) over the closed-trade sequence."""
    best_w = best_l = cur_w = cur_l = 0
    for p in pnls:
        if p > 0:
            cur_w, cur_l = cur_w + 1, 0
        else:
            cur_l, cur_w = cur_l + 1, 0
        best_w, best_l = max(best_w, cur_w), max(best_l, cur_l)
    return best_w, best_l


def _max_drawdown(equity: list[float]) -> float:
    """Largest peak-to-trough drop (a positive rupee number) over the cumulative equity curve."""
    peak = mdd = 0.0
    for e in equity:
        peak = max(peak, e)
        mdd = max(mdd, peak - e)
    return mdd


def equity_curve(pnls: list[float]) -> list[float]:
    """Cumulative realized P&L after each closed trade (oldest first)."""
    out, run = [], 0.0
    for p in pnls:
        run += p
        out.append(run)
    return out


def drawdown_series(pnls: list[float]) -> list[float]:
    """Underwater curve: equity minus its running peak after each trade (<= 0). The trough is the
    max drawdown."""
    peak, out = 0.0, []
    for e in equity_curve(pnls):
        peak = max(peak, e)
        out.append(e - peak)
    return out


def daily_pnl(store, strategy_ids: list[str]) -> list[dict]:
    """Realized P&L per IST trading day per strategy: [{date, <sid>: pnl, ...}], oldest first.
    Feeds the daily-P&L bar chart."""
    by_day: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for sid in strategy_ids:
        for p in store.positions_for_strategy(sid, status="CLOSED"):
            by_day[_ist_day(p.closed_at)][sid] += (p.realized_pnl or 0.0)
    rows = []
    for day in sorted(by_day):
        rows.append(dict({"date": day}, **{sid: by_day[day].get(sid, 0.0) for sid in strategy_ids}))
    return rows


def strategy_performance(store, strategy_id: str, total_pool: float, today_iso: str) -> dict:
    """The full performance summary for one strategy from its CLOSED positions."""
    closed = store.positions_for_strategy(strategy_id, status="CLOSED")
    pnls = [p.realized_pnl or 0.0 for p in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win, gross_loss = sum(wins), abs(sum(losses))
    n = len(pnls)
    eq = equity_curve(pnls)
    best_w, best_l = _streaks(pnls)
    avg_profit = gross_win / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    net = sum(pnls)
    return {
        "strategy_id": strategy_id,
        "net_profit": net,
        "today_profit": store.realized_pnl_since(today_iso, strategy_id),
        "total_trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_pct": (len(wins) / n * 100.0) if n else 0.0,
        "avg_profit": avg_profit,
        "avg_loss": avg_loss,
        "profit_factor": (gross_win / gross_loss) if gross_loss else (math.inf if gross_win else 0.0),
        "risk_reward": (avg_profit / abs(avg_loss)) if (wins and losses) else 0.0,
        "roi_pct": (net / total_pool * 100.0) if total_pool else 0.0,
        "max_drawdown": _max_drawdown(eq),
        "consecutive_wins": best_w,
        "consecutive_losses": best_l,
        "equity": eq,
    }


def all_performance(store, strategy_ids: list[str], total_pool: float, today_iso: str) -> list[dict]:
    return [strategy_performance(store, sid, total_pool, today_iso) for sid in strategy_ids]


def leaderboard(perfs: list[dict]) -> dict[str, str | None]:
    """Best strategy per headline metric -> {label: strategy_id}. Ties break on first seen."""
    if not perfs:
        return {}

    def best(key, maximize=True):
        pairs = [(p[key], p["strategy_id"]) for p in perfs]
        chosen = (max if maximize else min)(pairs, key=lambda t: t[0])
        return chosen[1]

    return {
        "Highest net profit": best("net_profit"),
        "Highest win rate": best("win_pct"),
        "Lowest drawdown": best("max_drawdown", maximize=False),
        "Best profit factor": best("profit_factor"),
        "Best risk:reward": best("risk_reward"),
    }


def portfolio_comparison(store, strategy_id: str, leverage: float) -> dict:
    """Current portfolio snapshot for one strategy. Unrealized P&L needs a live price feed the
    dashboard doesn't have, so it is reported as None (shown as '—')."""
    open_p = store.get_open_positions(strategy_id)
    notional = sum(p.quantity * p.entry_price for p in open_p)
    realized = sum((p.realized_pnl or 0.0)
                   for p in store.positions_for_strategy(strategy_id, status="CLOSED"))
    return {
        "strategy_id": strategy_id,
        "open_positions": len(open_p),
        "deployed_notional": notional,
        "used_margin": notional / leverage if leverage else notional,
        "unrealized_pnl": None,           # no live price feed in the dashboard
        "realized_pnl": realized,
    }


def decision_comparison(store, strategy_ids: list[str], limit: int = 300) -> list[dict]:
    """Align decisions across strategies by (IST day, symbol) so each row shows what EVERY strategy
    decided for the same name — using each strategy's LATEST decision that day. This is robust to
    the strategies running sequentially in a compare cycle (their decision timestamps differ by
    minutes, so a naive minute-bucket would split the same name into separate rows and make it look
    like only one strategy decided). Newest day first."""
    grid: dict[tuple, dict] = {}      # (day, symbol) -> {sid: (created_at, action)}
    for sid in strategy_ids:
        for d in store.decisions_for_strategy(sid, limit=limit):   # newest first
            key = (_ist_day(d.created_at), d.symbol)
            per = grid.setdefault(key, {})
            if sid not in per:                                     # first seen = latest that day
                per[sid] = (d.created_at, d.action)
    rows = []
    for (day, symbol), bysid in sorted(
            grid.items(), key=lambda kv: max(v[0] for v in kv[1].values()), reverse=True):
        row = {"time": max(v[0] for v in bysid.values()), "symbol": symbol}
        for sid in strategy_ids:
            row[sid] = bysid[sid][1] if sid in bysid else "—"
        rows.append(row)
    return rows


def trade_comparison(store, strategy_ids: list[str], limit: int = 50) -> list[dict]:
    """Align CLOSED trades across strategies by (minute-opened, symbol). Each row carries the
    per-strategy Position (or None) so the UI can show entry/exit/pnl side by side. Newest first."""
    grid: dict[tuple, dict] = {}
    for sid in strategy_ids:
        for p in store.positions_for_strategy(sid, status="CLOSED"):
            key = ((p.opened_at or "")[:16], p.symbol)
            grid.setdefault(key, {})[sid] = p
    rows = []
    for (ts, symbol), bysid in sorted(grid.items(), reverse=True)[:limit]:
        rows.append({"time": ts, "symbol": symbol,
                     "positions": {sid: bysid.get(sid) for sid in strategy_ids}})
    return rows
