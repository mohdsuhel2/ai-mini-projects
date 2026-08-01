"""activeShort logic — select tonight, arm tomorrow, protect what fills.

Pure decision functions here; the jobs in active_short_job.py do the I/O. Everything that decides
whether real money moves is testable without a broker.

Short side throughout. Two facts drive the rules:

  * Retail shorts in India are intraday-only — naked short delivery is prohibited and MIS is
    auto-squared ~15:20. Every position closes the same session.
  * An unconfirmed bearish pattern fails >60% of the time; with follow-through, ~30%. So an entry
    is a stop-ENTRY armed BELOW the confirmation level, never a market order at the open.

See docs/superpowers/specs/2026-07-31-active-short-design.md.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional, Sequence

log = logging.getLogger("autointraday.active_short")


class ActiveShortError(Exception):
    """A refusal that must stop the order, not be logged and ignored."""


@dataclass(frozen=True)
class Candidate:
    """One row of the scanner skill's output."""
    symbol: str
    confidence: float
    confirmation_level: float      # short valid only BELOW this
    stop: float                    # above the entry (a short's stop is up)
    target: float                  # below the entry
    rvol: Optional[float] = None
    reason: Optional[str] = None
    prior_close: Optional[float] = None


def parse_candidates(payload: Any) -> list[Candidate]:
    """Map the skill's JSON into Candidates, skipping malformed rows rather than failing the scan.

    A single bad row must not cost the whole night's list.
    """
    rows = (payload or {}).get("candidates") if isinstance(payload, dict) else payload
    out: list[Candidate] = []
    for row in (rows or []):
        try:
            out.append(Candidate(
                symbol=str(row["symbol"]).upper(),
                confidence=float(row["confidence"]),
                confirmation_level=float(row["confirmation_level"]),
                stop=float(row["stop"]),
                target=float(row["target"]),
                rvol=float(row["rvol"]) if row.get("rvol") is not None else None,
                reason=(str(row["reason"]) if row.get("reason") else None),
                prior_close=(float(row["prior_close"]) if row.get("prior_close") is not None
                             else None)))
        except (KeyError, TypeError, ValueError):
            log.warning("skipping malformed scanner row: %r", row)
    return out


def _geometry_ok(c: Candidate) -> bool:
    """A short's levels must be ordered: target < confirmation_level < stop.

    A row that fails this is not a short setup, whatever the skill labelled it — the same class of
    error that wrote a short's stop onto a long on 2026-07-30.
    """
    return c.target < c.confirmation_level < c.stop


def select(candidates: Sequence[Candidate], cfg: dict) -> list[Candidate]:
    """Rank and cut the night's list. Highest confidence first, capped at max_shorts."""
    eligible = []
    for c in candidates:
        if c.confidence < cfg["min_confidence"]:
            continue
        if cfg["min_rvol"] and (c.rvol is None or c.rvol < cfg["min_rvol"]):
            # No RVOL means no evidence of institutional distribution behind the pattern.
            continue
        if not _geometry_ok(c):
            log.warning("%s: refusing incoherent short geometry target=%s level=%s stop=%s",
                        c.symbol, c.target, c.confirmation_level, c.stop)
            continue
        eligible.append(c)
    eligible.sort(key=lambda c: c.confidence, reverse=True)
    return eligible[:int(cfg["max_shorts"])]


def gap_too_far(confirmation_level: float, open_price: float, max_gap_pct: float) -> bool:
    """True when the stock opened so far below the trigger that an SL_M would fill far from plan.

    SL_M becomes a MARKET order once triggered. A name that opens 4% below the confirmation level
    fills there, not at the level — the move the scan predicted has already happened without us.
    """
    if not confirmation_level or open_price is None or max_gap_pct <= 0:
        return False
    below_pct = (confirmation_level - open_price) / confirmation_level * 100.0
    return below_pct > max_gap_pct


# A reversal short of a gapped-up winner must trigger off TODAY'S action, not yesterday's low —
# the stock will never trade back there, so the entry never fires. Measured: only 53% of signals
# ever triggered, and those that did skewed to the weaker breakdown setups.
REVERSAL_OPEN_TRIGGER_PCT = 0.4


def open_anchored_trigger(planned_level: float, open_price: float, last_price: float,
                          setup_type: Optional[str],
                          pct: float = REVERSAL_OPEN_TRIGGER_PCT) -> float:
    """Trigger for a short entry, tightened toward the open for reversal setups.

    For `reversal_short`, take the trigger `pct` below the open (or the planned level, whichever
    is HIGHER — i.e. the one that actually gets reached) so a gap-up winner can still fade into
    the entry. Breakdown setups keep the level the scan set, which is structural.

    A level that ends up AT OR ABOVE the tape is deliberately left alone, NOT clamped down: that
    means price has already fallen through the planned level, so the move happened without us.
    validate_short_entry then refuses it and the pick is skipped — chasing it lower is precisely
    what the gap guard exists to prevent.
    """
    level = float(planned_level)
    if setup_type == "reversal_short" and open_price:
        open_level = float(open_price) * (1 - pct / 100.0)
        level = max(level, open_level)          # the nearer level is the one that can be hit
    return round(level, 2)


def validate_short_entry(trigger: float, last_price: float) -> None:
    """A SELL stop-ENTRY must sit strictly BELOW the market, or it fires instantly at the open.

    The mirror of the long-side stop clamp. Raises rather than returning a flag: an entry on the
    wrong side of the tape is a defect, and silently skipping it would hide a broken scan.
    """
    if trigger is None or last_price is None:
        raise ActiveShortError("cannot validate a short entry without both trigger and price")
    if trigger >= last_price:
        raise ActiveShortError(
            f"short stop-entry trigger {trigger:g} is at or above the market {last_price:g} — "
            "it would fire immediately instead of waiting for confirmation")


def validate_short_stop(stop: float, last_price: float) -> None:
    """A SHORT's protective stop must sit strictly ABOVE the market.

    Below it, the BUY SL_M triggers immediately and covers the short at a loss the instant it is
    placed. This is the mirror of the long-side clamp added on 2026-07-31 after a short's stop was
    written onto a long — and the smoke test proved it was needed here too: a bad fill price
    produced a stop of 99.98 for a stock trading at 243, which would have been placed without
    complaint.

    Raises rather than returning a flag: a stop on the wrong side of the tape is a defect, and the
    caller must record the position as UNPROTECTED rather than pretend it is covered.
    """
    if stop is None or last_price is None:
        raise ActiveShortError("cannot validate a short stop without both stop and price")
    if stop <= last_price:
        raise ActiveShortError(
            f"short stop {stop:g} is at or below the market {last_price:g} — it would cover the "
            "position immediately instead of protecting it")


def position_size(capital: float, price: float) -> int:
    return int(capital // price) if price and price > 0 else 0


def protective_levels(fill_price: float, cfg: dict) -> tuple[float, float]:
    """(stop, target) for a filled SHORT: stop ABOVE the fill, target BELOW it.

    Derived from the ACTUAL fill, not the planned entry — an SL_M can fill well below its trigger
    on a gap, and a stop computed from the plan would then sit far too wide.
    """
    stop = fill_price * (1 + cfg["stop_pct"] / 100.0)
    target = fill_price * (1 - cfg["target_pct"] / 100.0)
    return round(stop, 2), round(target, 2)


def short_pnl(entry: float, exit_price: float, quantity: int) -> float:
    """Short P&L: profit when the exit is BELOW the entry."""
    return (entry - exit_price) * quantity
