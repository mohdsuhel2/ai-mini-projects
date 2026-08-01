"""Groww instrument master — per-symbol tick size.

Why this exists: we hard-coded a ₹0.05 tick and Groww rejected orders with "choose price in
multiples of the tick size". The tick is NOT uniform — verified against Groww's own master on
2026-07-31:

    63MOONS 0.05 | BAJFINANCE 0.10 | MANKIND 0.10 | NETWEB 0.10 | TDPOWERSYS 0.10 | RELIANCE 0.10

So 4399.95 (NETWEB) and 1126.85 (BAJFINANCE) were valid on a 0.05 grid and invalid on the 0.10
grid the exchange actually applies to them.

The CSV is a PUBLIC asset (no auth, no IP whitelist), so this works from anywhere — unlike the
trade API, which only accepts whitelisted IPs. Cached on disk and refreshed daily.
"""
from __future__ import annotations

import csv
import logging
import os
import time
import urllib.request

log = logging.getLogger("autointraday.instruments")

INSTRUMENT_CSV_URL = "https://growwapi-assets.groww.in/instruments/instrument.csv"
CACHE_PATH = os.path.expanduser("~/.autointraday/instruments.csv")
CACHE_MAX_AGE_SECONDS = 24 * 3600

# Fallback when a symbol is not in the master. 0.10, NOT 0.05, deliberately: a 0.10-aligned price
# is also a valid 0.05 price, so rounding to the COARSER grid is safe under uncertainty while
# rounding to the finer one is exactly the bug this module fixes.
DEFAULT_TICK = 0.10

_cache: dict[str, float] | None = None


def _download(path: str = CACHE_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with urllib.request.urlopen(INSTRUMENT_CSV_URL, timeout=120) as resp, open(tmp, "wb") as f:
        f.write(resp.read())
    os.replace(tmp, path)                       # atomic — never leave a half-written master


def _fresh(path: str) -> bool:
    return os.path.exists(path) and (time.time() - os.path.getmtime(path)) < CACHE_MAX_AGE_SECONDS


def load_tick_sizes(path: str = CACHE_PATH, force: bool = False) -> dict[str, float]:
    """symbol -> tick size for NSE CASH. Cached in memory and on disk; refreshed daily.

    Never raises: a download failure falls back to whatever is cached, and failing that to an
    empty map (so every lookup uses DEFAULT_TICK). A missing master must not stop trading.
    """
    global _cache
    if _cache is not None and not force:
        return _cache
    if force or not _fresh(path):
        try:
            _download(path)
        except Exception:
            log.warning("instrument master download failed — using cached/default ticks",
                        exc_info=True)
    out: dict[str, float] = {}
    try:
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                if row.get("exchange") != "NSE" or row.get("segment") != "CASH":
                    continue
                sym, raw = row.get("trading_symbol"), row.get("tick_size")
                if not sym or not raw:
                    continue
                try:
                    tick = float(raw)
                except ValueError:
                    continue
                if tick > 0:
                    out[sym.upper()] = tick
    except FileNotFoundError:
        log.warning("no instrument master at %s — every symbol falls back to %.2f",
                    path, DEFAULT_TICK)
    except Exception:
        log.warning("could not parse instrument master — falling back to %.2f", DEFAULT_TICK,
                    exc_info=True)
    _cache = out
    return out


def _rows() -> list[dict]:
    """Raw master rows, cached. Used for lookups that need more than tick size."""
    global _rows_cache
    try:
        return _rows_cache
    except NameError:
        pass
    load_tick_sizes()                       # ensures the file is present/fresh
    out = []
    try:
        with open(CACHE_PATH, newline="") as f:
            out = list(csv.DictReader(f))
    except Exception:
        log.warning("could not read instrument master rows", exc_info=True)
    globals()["_rows_cache"] = out
    return out


def option_expiries(underlying: str) -> list[str]:
    """Listed option expiries for an underlying, ascending 'YYYY-MM-DD'. Empty if not an F&O name.

    Read from the master rather than computed. The old last-Thursday rule is WRONG for 2026 —
    RELIANCE's August expiry is Tuesday 2026-08-25, not Thursday the 27th, because NSE moved the
    F&O expiry day. Deriving it from the master also handles holiday shifts and weekly expiries
    for free, and a guessed date silently returns an empty chain rather than an error.
    """
    if not underlying:
        return []
    want = underlying.upper()
    seen = {r.get("expiry_date") for r in _rows()
            if r.get("underlying_symbol", "").upper() == want
            and r.get("instrument_type") in ("CE", "PE")
            and r.get("expiry_date")}
    return sorted(seen)


def next_option_expiry(underlying: str, on_or_after: str | None = None) -> str | None:
    """The nearest listed expiry not before `on_or_after` (default today), or None."""
    from datetime import date as _date
    ref = on_or_after or _date.today().isoformat()
    return next((e for e in option_expiries(underlying) if e >= ref), None)


def tick_size_for(symbol: str | None) -> float:
    """The exchange tick for `symbol`, or DEFAULT_TICK when unknown."""
    if not symbol:
        return DEFAULT_TICK
    return load_tick_sizes().get(symbol.upper(), DEFAULT_TICK)


def round_to_tick(px: float | None, symbol: str | None = None) -> float | None:
    """Snap a price to the symbol's own tick grid.

    The trailing round(_, 2) is load-bearing: round(x/tick)*tick alone yields values like
    4399.900000000001, which the exchange reads as off-tick.
    """
    if px is None:
        return None
    tick = tick_size_for(symbol)
    return round(round(float(px) / tick) * tick, 2)
