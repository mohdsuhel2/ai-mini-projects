"""Live Intraday price transport — candle aggregation behind one swappable interface.

The engine decides on candles it never fetches itself. This module supplies them, so the transport
can change (gateway polling now, VPS WebSocket later) without the rules changing at all.

Volume note: Groww's /v1/ltp returns a bare price, but the engine's VWAP and RVOL gates need
volume. So the poll feed reads /v1/quote, whose `volume` is the CUMULATIVE day total, and takes
the per-candle volume as the delta between successive reads. A cumulative counter that goes
backwards (session rollover, a bad tick) yields 0 rather than a negative bar.

See docs/superpowers/specs/2026-07-31-live-intraday-design.md.
"""
from __future__ import annotations

import logging
from typing import Optional, Protocol, Sequence

from live_engine import Candle, to_min

log = logging.getLogger("autointraday.live_feed")


class PriceFeed(Protocol):
    """What the trader needs from any transport."""

    def poll(self) -> Optional[float]: ...
    def candles(self) -> list[Candle]: ...


def _bucket(hhmm: str, minutes: int) -> str:
    """Floor a HH:MM stamp to its candle bucket start."""
    m = to_min(hhmm)
    m -= m % minutes
    return f"{m // 60:02d}:{m % 60:02d}"


class CandleBuilder:
    """Aggregates (time, price, cumulative_volume) samples into fixed-interval candles.

    Pure and clock-free — the caller supplies the timestamp, so the same builder serves the live
    loop and a replay. Seeded candles (from historical REST) can be loaded first; a sample landing
    in a seeded bucket updates it rather than duplicating it.
    """

    def __init__(self, interval_minutes: int = 1, seed: Optional[Sequence[Candle]] = None):
        if interval_minutes <= 0:
            raise ValueError("interval_minutes must be positive")
        self.interval = interval_minutes
        self._candles: list[Candle] = list(seed or [])
        self._last_cum_volume: Optional[float] = None

    def add(self, hhmm: str, price: float, cum_volume: Optional[float] = None) -> None:
        """Fold one sample in. Ignores non-positive prices and out-of-order stamps."""
        if price is None or price <= 0:
            return
        bucket = _bucket(hhmm, self.interval)
        if self._candles and to_min(bucket) < to_min(self._candles[-1].t):
            log.warning("dropping out-of-order sample %s (last bucket %s)", hhmm,
                        self._candles[-1].t)
            return

        vol_delta = 0.0
        if cum_volume is not None:
            if self._last_cum_volume is not None and cum_volume >= self._last_cum_volume:
                vol_delta = cum_volume - self._last_cum_volume
            self._last_cum_volume = cum_volume

        if self._candles and self._candles[-1].t == bucket:
            c = self._candles[-1]
            self._candles[-1] = Candle(t=bucket, o=c.o, h=max(c.h, price), l=min(c.l, price),
                                       c=price, v=c.v + vol_delta)
        else:
            self._candles.append(Candle(t=bucket, o=price, h=price, l=price, c=price, v=vol_delta))

    def candles(self) -> list[Candle]:
        return list(self._candles)

    def closed_candles(self) -> list[Candle]:
        """Every candle except the one still forming.

        The engine must decide on COMPLETED candles only — the same rule the 15m skill engine
        learned on 2026-07-27 (the RKFORGE whipsaw), where ingesting a partial bar made the label
        flip mid-candle.
        """
        return list(self._candles[:-1])


class GatewayPollFeed:
    """Phase 1 transport: poll /v1/quote through the existing (IP-whitelisted) gateway.

    Groww's trade API only accepts whitelisted static IPs, so a WebSocket cannot be opened from
    this machine at all — every call detours through the VPS gateway. Polling reuses that proven
    path. For a 1-minute-candle strategy a 2-second poll is not materially worse than tick
    streaming; the WebSocket's edge is tick-precise stops, which phase 2 adds.
    """

    def __init__(self, client, symbol: str, now_fn, interval_minutes: int = 1,
                 seed: Optional[Sequence[Candle]] = None):
        self.client = client
        self.symbol = symbol
        self.now_fn = now_fn                      # () -> "HH:MM" in IST; injected for testability
        self.builder = CandleBuilder(interval_minutes, seed)

    def poll(self) -> Optional[float]:
        """One sample. Returns the price, or None if the read failed — a transient gateway error
        must never kill the session, so the caller simply gets no new data this tick."""
        try:
            q = self.client.get_quote(self.symbol)
        except Exception as e:
            log.warning("%s: quote poll failed (%s) — no sample this tick", self.symbol, e)
            return None
        price = q.get("ltp")
        if price is None:
            return None
        self.builder.add(self.now_fn(), float(price), q.get("volume"))
        return float(price)

    def candles(self) -> list[Candle]:
        return self.builder.closed_candles()
