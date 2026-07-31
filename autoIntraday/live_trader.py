"""Live Intraday trader — the loop and the safety envelope.

Long-only, one position at a time, one symbol per session. Runs as a long-lived launchd agent
(com.autointraday.livetrader), unlike every other job here which launchd fires and which exits in
minutes. Streamlit cannot host this: the dashboard re-runs its script on every interaction and
holds no background thread.

The page and this loop share nothing but the database. The page writes control flags; `run_once`
re-reads them at the top of every iteration, so DISARM lands within one poll and works even with
the dashboard closed.

Every collaborator is injected, so the whole loop is testable without a broker, a clock or a feed.

See docs/superpowers/specs/2026-07-31-live-intraday-design.md.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

from live_engine import Candle, LiveConfig, LivePosition, decide, to_min

log = logging.getLogger("autointraday.live_trader")


# ---------------------------------------------------------------------------------------------
# Symbol selection — pure, so the ranking can be tested without touching the network
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Pick:
    symbol: str
    ltp: float
    change_pct: float
    vol_ratio: float


def select_symbol(picks: Sequence[Pick], capital: float, rvol_floor: float,
                  min_price: float = 50.0) -> Optional[tuple[str, str]]:
    """Best long candidate, or None. Long-only, so anything red is out.

    Requires the price to allow a sane position (at least 5 shares of `capital`), because a
    2-share position cannot be scaled out of and rounds badly against the stop.
    """
    max_price = capital / 5.0
    eligible = [p for p in picks
                if p.change_pct > 0 and p.vol_ratio >= rvol_floor
                and min_price <= p.ltp <= max_price]
    if not eligible:
        return None
    best = max(eligible, key=lambda p: (p.vol_ratio, p.change_pct))
    return best.symbol, (f"{best.change_pct:+.2f}% on {best.vol_ratio:.1f}x volume, "
                         f"LTP {best.ltp:.2f}")


def parse_picks(payload: dict) -> list[Pick]:
    """Map groww_intraday_screener.py's JSON into Picks, skipping malformed rows."""
    out = []
    for row in (payload or {}).get("picks", []):
        try:
            out.append(Pick(symbol=str(row["symbol"]).upper(), ltp=float(row["ltp"]),
                            change_pct=float(row.get("change_pct") or 0.0),
                            vol_ratio=float(row.get("vol_ratio") or 0.0)))
        except (KeyError, TypeError, ValueError):
            continue
    return out


# ---------------------------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------------------------
class LiveTrader:
    """One iteration = `run_once`. The daemon just calls it on a timer."""

    def __init__(self, store, client, feed_factory: Callable[[str], Any],
                 now_fn: Callable[[], str], today_fn: Callable[[], str],
                 fetch_picks: Callable[[], list[Pick]]):
        self.store = store
        self.client = client
        self.feed_factory = feed_factory       # symbol -> PriceFeed
        self.now_fn = now_fn                   # () -> "HH:MM" IST
        self.today_fn = today_fn               # () -> "YYYY-MM-DD"
        self.fetch_picks = fetch_picks
        self.feed = None
        self.symbol: Optional[str] = None
        self._no_setup_since: Optional[int] = None

    # -- config ------------------------------------------------------------------------------
    def _engine_config(self, cfg: dict) -> LiveConfig:
        return LiveConfig(min_rr=cfg["min_rr"], atr_mult=cfg["atr_mult"],
                          rr_target=cfg["rr_target"], min_stop_pct=cfg["min_stop_pct"],
                          rvol_floor=cfg["rvol_floor"],
                          vwap_exit_candles=cfg["vwap_exit_candles"],
                          max_hold_minutes=cfg["max_hold_minutes"],
                          no_new_entry_after=cfg["no_new_entry_after"],
                          squareoff_at=cfg["squareoff_at"])

    # -- one iteration -----------------------------------------------------------------------
    def run_once(self) -> str:
        """Returns a short status string describing what this tick did."""
        cfg = self.store.get_config()
        now, today = self.now_fn(), self.today_fn()
        self.store.heartbeat()

        open_trade = self.store.get_open_trade()

        # 1. Square-off overrides everything, including DISARM — an open position must be closed
        #    even after the kill switch, or the switch would strand live exposure into the close.
        if open_trade is not None and to_min(now) >= to_min(cfg["squareoff_at"]):
            return self._exit(open_trade, f"square-off {cfg['squareoff_at']}", cfg)

        # 2. Kill switch: blocks NEW entries; an open position keeps being managed below.
        if not cfg["armed"] and open_trade is None:
            self.store.update_state(signal_action="NONE", signal_reason="disarmed")
            return "disarmed"

        # 3. Symbol: chosen once, held for the session.
        if self.symbol is None:
            if to_min(now) < to_min(cfg["select_at"]):
                return f"waiting for select_at {cfg['select_at']}"
            if not self._select(cfg, today):
                return "no eligible symbol"

        # 4. Data.
        price = self.feed.poll()
        candles = self.feed.candles()
        if price is None or not candles:
            return "no data this tick"

        # 5. Decide.
        position = None
        if open_trade is not None:
            position = LivePosition(entry_price=open_trade.entry_price, stop=open_trade.stop,
                                    target=open_trade.target, quantity=open_trade.quantity,
                                    opened_at=self._opened_hhmm(open_trade))
        signal = decide(candles, position, self._engine_config(cfg))
        self.store.update_state(signal_action=signal.action, signal_reason=signal.reason,
                                indicators_json=json.dumps(signal.indicators, default=str))

        if open_trade is not None:
            if signal.action == "EXIT":
                return self._exit(open_trade, signal.reason, cfg)
            return f"holding: {signal.reason}"

        # 6. No position — loss cap, then entry.
        if self._loss_cap_breached(cfg, today):
            return "disarmed: daily loss cap"
        if signal.action == "ENTER":
            self._no_setup_since = None
            return self._enter(signal, cfg, today)

        self._track_idle(now, cfg)
        return f"flat: {signal.reason}"

    # -- steps -------------------------------------------------------------------------------
    def _select(self, cfg: dict, today: str) -> bool:
        chosen = select_symbol(self.fetch_picks(), cfg["capital_per_trade"], cfg["rvol_floor"])
        if chosen is None:
            self.store.update_state(trading_date=today, symbol=None,
                                    selected_reason="no eligible candidate")
            return False
        self.symbol, reason = chosen
        self.feed = self.feed_factory(self.symbol)
        self.store.update_state(trading_date=today, symbol=self.symbol, selected_reason=reason)
        log.info("selected %s — %s", self.symbol, reason)
        return True

    def _loss_cap_breached(self, cfg: dict, today: str) -> bool:
        if self.store.realized_pnl(today) <= -abs(cfg["daily_loss_cap"]):
            self.store.disarm(f"daily loss cap {cfg['daily_loss_cap']:g} breached")
            log.warning("daily loss cap breached — disarmed for the day")
            return True
        return False

    def _enter(self, signal, cfg: dict, today: str) -> str:
        qty = int(cfg["capital_per_trade"] // signal.entry)
        if qty < 1:
            return "entry skipped: capital buys < 1 share"
        order_id = None
        if cfg["mode"] == "live":
            try:
                resp = self.client.place_order(
                    symbol=self.symbol, exchange="NSE", transaction_type="BUY",
                    quantity=qty, order_type="MARKET", price=signal.entry, product="MIS")
                order_id = (resp or {}).get("order_id")
            except Exception as e:
                log.exception("entry order failed for %s", self.symbol)
                return f"entry order FAILED: {e}"
        self.store.open_trade(today, self.symbol, qty, signal.entry, signal.stop, signal.target,
                              cfg["mode"], entry_order_id=order_id)
        log.info("ENTER %s x%d @ %.2f stop %.2f target %.2f (%s)", self.symbol, qty, signal.entry,
                 signal.stop, signal.target, cfg["mode"])
        return f"entered {self.symbol} x{qty} @ {signal.entry:g}"

    def _exit(self, trade, reason: str, cfg: dict) -> str:
        price = None
        try:
            price = self.feed.poll() if self.feed else None
        except Exception:
            pass
        if price is None:
            price = trade.entry_price          # last resort: book flat rather than crash
            log.warning("%s: no price for exit — booking at entry", trade.symbol)
        order_id = None
        if trade.mode == "live":
            try:
                resp = self.client.place_order(
                    symbol=trade.symbol, exchange="NSE", transaction_type="SELL",
                    quantity=trade.quantity, order_type="MARKET", price=price, product="MIS")
                order_id = (resp or {}).get("order_id")
            except Exception as e:
                log.exception("EXIT ORDER FAILED for %s — position still live", trade.symbol)
                return f"EXIT ORDER FAILED: {e}"
        pnl = self.store.close_trade(trade.id, price, reason, exit_order_id=order_id)
        log.info("EXIT %s @ %.2f (%s) pnl %.0f", trade.symbol, price, reason, pnl)
        return f"exited {trade.symbol} @ {price:g} ({reason}) pnl {pnl:.0f}"

    def _track_idle(self, now: str, cfg: dict) -> None:
        """Drop a symbol that has offered nothing for abandon_after_minutes, so a dead name does
        not consume the whole session. Never while a position is open (checked by the caller)."""
        if self._no_setup_since is None:
            self._no_setup_since = to_min(now)
            return
        if to_min(now) - self._no_setup_since >= cfg["abandon_after_minutes"]:
            if to_min(now) >= to_min(cfg["no_new_entry_after"]):
                return                                  # too late to start a new name
            log.info("%s idle for %d min — re-selecting", self.symbol,
                     cfg["abandon_after_minutes"])
            self.symbol, self.feed, self._no_setup_since = None, None, None

    @staticmethod
    def _opened_hhmm(trade) -> str:
        """UTC ISO -> IST HH:MM, matching the engine's clock convention."""
        from datetime import datetime, timedelta, timezone
        ist = timezone(timedelta(hours=5, minutes=30))
        try:
            return datetime.fromisoformat(trade.opened_at).astimezone(ist).strftime("%H:%M")
        except Exception:
            return "09:15"


# ---------------------------------------------------------------------------------------------
# Daemon entry point — launchd starts this once at 09:20; it exits after square-off
# ---------------------------------------------------------------------------------------------
def main() -> int:
    import fcntl
    import json as _json
    import os
    import subprocess
    import sys
    import time
    from datetime import datetime

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from groww_client import GrowwClient
    from live_feed import GatewayPollFeed
    from live_store import LiveStore
    from settings import load_settings
    from trading_calendar import IST, is_trading_time, load_holidays

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    # Single-flight, same pattern as run_cycle_job.py: two live traders would double-order.
    lock_path = os.path.expanduser("~/.autointraday/livetrader.lock")
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    handle = open(lock_path, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log.warning("another live trader holds the lock — exiting")
        return 0

    if not is_trading_time(datetime.now(IST), load_holidays()):
        log.info("market closed — exiting")
        return 0

    settings = load_settings()
    store = LiveStore(settings.db_path)
    client = GrowwClient(mode=store.get_config()["mode"])
    client.ensure_ready()

    def fetch_picks() -> list[Pick]:
        """Run the screener (public page, no auth, no IP whitelist needed)."""
        try:
            out = subprocess.run(
                [settings.screener_python, settings.screener_script, "--direction", "up",
                 "--top", "12", "--min-mcap-cr", "1000", "--min-price", "50"],
                capture_output=True, text=True, timeout=120)
            return parse_picks(_json.loads(out.stdout))
        except Exception:
            log.exception("screener failed — no candidates this pass")
            return []

    trader = LiveTrader(
        store=store, client=client,
        feed_factory=lambda sym: GatewayPollFeed(
            client, sym, now_fn=lambda: datetime.now(IST).strftime("%H:%M"),
            interval_minutes=store.get_config()["candle_minutes"]),
        now_fn=lambda: datetime.now(IST).strftime("%H:%M"),
        today_fn=lambda: datetime.now(IST).date().isoformat(),
        fetch_picks=fetch_picks)

    log.info("live trader started")
    while True:
        cfg = store.get_config()
        now = datetime.now(IST).strftime("%H:%M")
        # Exit only once flat — never leave a position unmanaged after square-off time.
        if to_min(now) > to_min(cfg["squareoff_at"]) and store.get_open_trade() is None:
            log.info("past square-off and flat — exiting")
            return 0
        try:
            log.info("tick %s: %s", now, trader.run_once())
        except Exception:
            log.exception("tick failed — continuing")   # one bad tick must not end the session
        time.sleep(max(1, int(cfg["poll_seconds"])))


if __name__ == "__main__":
    raise SystemExit(main())
