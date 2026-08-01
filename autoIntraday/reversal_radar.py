"""Early Reversal Prediction Engine — how close is this trend to exhaustion?

Runs BEFORE the decision engine and NEVER issues a trading decision. It answers one question:
what is the probability that the current move is running out, measured from signals that appear
BEFORE price confirms.

**Why it exists.** The decision skill detects reversals late because it waits for confirmation —
VWAP breakdown, lower highs, EMA failure. Those are lagging by construction. On 2026-07-28 it
scored KALYANKJIL quality 70, returned NO_TRADE, and the stock ran +5.54%.

**What it must NOT be used for.** Shorting on this signal alone. Unconfirmed bearish patterns fail
>60% of the time versus ~30% with follow-through, and this engine deliberately looks at the
unconfirmed end. Its output has two legitimate uses:

  1. SUPPRESS new longs into an exhausting trend — risk reduction, correct without confirmation.
  2. PRIME the confirmation trigger, so when price does confirm the system acts immediately.

Deterministic and pure: the same bars always give the same reading, so it can be unit-tested and
backtested — unlike an LLM pass, and it runs per candidate per cycle so it must be fast.

VWAP note: the binary "above or below VWAP" is deliberately ignored, since that is the lagging
directional gate this engine exists to pre-empt. DISTANCE from VWAP is used, as extension evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

# Stages, weakest to strongest. Thresholds are on the 0-100 probability.
STAGES = ("healthy_trend", "late_trend", "early_exhaustion", "distribution",
          "high_probability_reversal")
STAGE_CUTS = ((80, "high_probability_reversal"), (65, "distribution"),
              (45, "early_exhaustion"), (25, "late_trend"), (0, "healthy_trend"))

# Each signal contributes its weight when present. Weights are a starting calibration, not
# evidence-derived — tune them from the replay, not from intuition.
WEIGHTS: dict[str, float] = {
    "momentum_slowing": 8.0,
    "rsi_divergence": 12.0,
    "macd_divergence": 10.0,
    "new_high_weak_momentum": 12.0,
    "consecutive_extension": 7.0,
    "far_from_vwap": 6.0,
    "far_from_ema20": 6.0,
    "far_from_ema50": 5.0,
    "atr_overextended": 8.0,
    "bollinger_exhaustion": 7.0,
    "large_upper_shadows": 8.0,
    "buying_climax": 9.0,
    "volume_climax": 8.0,
    "churn_high_volume_no_progress": 9.0,
    "failed_new_high": 10.0,
    "multiple_rejections": 9.0,
    "premium_in_day_range": 6.0,
    "resistance_rejection": 8.0,
    "sector_weakening": 7.0,
    "relative_weakness": 8.0,
}


@dataclass(frozen=True)
class Candle:
    t: str
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0


@dataclass
class Reading:
    probability: float
    stage: str
    signals: list = field(default_factory=list)      # fired, strongest first
    unknown: list = field(default_factory=list)      # could not evaluate
    evidence: dict = field(default_factory=dict)     # raw numbers behind the calls

    def as_dict(self) -> dict[str, Any]:
        return {"reversal_probability": round(self.probability, 1), "reversal_stage": self.stage,
                "signals": self.signals, "unknown_signals": self.unknown,
                "evidence": self.evidence,
                "note": "ESTIMATE ONLY — never a trading decision. Use to suppress new longs and "
                        "to prime a confirmation trigger, never to short unconfirmed."}


def _upper_shadow(c: Candle) -> float:
    rng = c.h - c.l
    return ((c.h - max(c.o, c.c)) / rng) if rng > 0 else 0.0


def _body_frac(c: Candle) -> float:
    rng = c.h - c.l
    return (abs(c.c - c.o) / rng) if rng > 0 else 0.0


def _slope(vals: Sequence[float]) -> Optional[float]:
    """Simple first-vs-last slope over the window; None when too short."""
    v = [x for x in vals if x is not None]
    return (v[-1] - v[0]) / len(v) if len(v) >= 3 else None


def analyse(candles: Sequence[Candle], *, rsi_series: Optional[Sequence[float]] = None,
            macd_hist: Optional[Sequence[float]] = None, vwap: Optional[float] = None,
            ema20: Optional[float] = None, ema50: Optional[float] = None,
            atr: Optional[float] = None, bb_percent_b: Optional[float] = None,
            rvol: Optional[float] = None, sector_change_pct: Optional[float] = None,
            index_change_pct: Optional[float] = None,
            stock_change_pct: Optional[float] = None) -> Reading:
    """Probability that the current move is exhausting. Never returns a trade decision.

    Anything that cannot be evaluated is reported in `unknown` and contributes NOTHING — absent
    evidence is not bearish evidence, the rule the committee got wrong on 2026-08-01.
    """
    if len(candles) < 6:
        return Reading(0.0, "healthy_trend", [], ["insufficient_bars"],
                       {"bars": len(candles)})

    fired: list[tuple[float, str]] = []
    unknown: list[str] = []
    ev: dict[str, Any] = {}
    last = candles[-1]
    recent = candles[-6:]
    highs = [c.h for c in candles]

    def fire(name: str) -> None:
        fired.append((WEIGHTS[name], name))

    # --- momentum ------------------------------------------------------------------------
    if rsi_series and len([r for r in rsi_series if r is not None]) >= 4:
        rs = [r for r in rsi_series if r is not None]
        ev["rsi_last"], ev["rsi_slope"] = round(rs[-1], 1), round(_slope(rs[-4:]) or 0, 3)
        if (_slope(rs[-4:]) or 0) < 0:
            fire("momentum_slowing")
        # divergence: price makes a higher high while RSI makes a lower high
        if len(candles) >= 8 and len(rs) >= 8:
            if highs[-1] >= max(highs[-8:]) and rs[-1] < max(rs[-8:-1]):
                fire("rsi_divergence")
                fire("new_high_weak_momentum")
    else:
        unknown += ["momentum_slowing", "rsi_divergence", "new_high_weak_momentum"]

    if macd_hist and len([m for m in macd_hist if m is not None]) >= 4:
        mh = [m for m in macd_hist if m is not None]
        ev["macd_hist_last"] = round(mh[-1], 4)
        if highs[-1] >= max(highs[-8:]) and mh[-1] < max(mh[-8:-1] or [mh[-1]]):
            fire("macd_divergence")
    else:
        unknown.append("macd_divergence")

    # --- extension -----------------------------------------------------------------------
    px = last.c
    for name, ref, thresh in (("far_from_vwap", vwap, 1.5), ("far_from_ema20", ema20, 2.0),
                              ("far_from_ema50", ema50, 3.5)):
        if ref:
            dist = (px - ref) / ref * 100
            ev[name.replace("far_from_", "dist_") + "_pct"] = round(dist, 2)
            if dist >= thresh:              # DISTANCE only — the above/below gate is ignored
                fire(name)
        else:
            unknown.append(name)

    if atr and ema20:
        stretch = (px - ema20) / atr
        ev["atr_stretch"] = round(stretch, 2)
        if stretch >= 2.0:
            fire("atr_overextended")
    else:
        unknown.append("atr_overextended")

    if bb_percent_b is not None:
        ev["bb_percent_b"] = round(bb_percent_b, 3)
        if bb_percent_b >= 0.95:
            fire("bollinger_exhaustion")
    else:
        unknown.append("bollinger_exhaustion")

    # --- candle behaviour ----------------------------------------------------------------
    ups = sum(1 for c in recent[-3:] if c.c > c.o)
    ev["consecutive_up_candles"] = ups
    if ups == 3:
        fire("consecutive_extension")

    shadows = [_upper_shadow(c) for c in recent[-3:]]
    ev["max_upper_shadow"] = round(max(shadows), 2)
    if max(shadows) >= 0.45:
        fire("large_upper_shadows")

    rejections = sum(1 for c in recent if _upper_shadow(c) >= 0.4 and _body_frac(c) <= 0.4)
    ev["rejection_candles"] = rejections
    if rejections >= 2:
        fire("multiple_rejections")

    # failed new high: probed above the prior swing high but closed back under it
    prior_high = max(highs[-8:-1]) if len(highs) >= 8 else max(highs[:-1])
    ev["prior_high"] = round(prior_high, 2)
    if last.h > prior_high and last.c < prior_high:
        fire("failed_new_high")
        fire("resistance_rejection")

    # --- volume --------------------------------------------------------------------------
    if rvol is not None:
        ev["rvol"] = round(rvol, 2)
        if rvol >= 2.0:
            fire("volume_climax")
            if last.c > last.o and _upper_shadow(last) >= 0.35:
                fire("buying_climax")
    else:
        unknown += ["volume_climax", "buying_climax"]

    vols = [c.v for c in recent if c.v]
    if len(vols) >= 4:
        rng_recent = max(c.h for c in recent) - min(c.l for c in recent)
        progress = abs(recent[-1].c - recent[0].c)
        ev["progress_vs_range"] = round(progress / rng_recent, 2) if rng_recent else None
        if rng_recent and progress / rng_recent < 0.25 and (vols[-1] > sum(vols[:-1]) / len(vols[:-1])):
            fire("churn_high_volume_no_progress")
    else:
        unknown.append("churn_high_volume_no_progress")

    # --- position in today's range --------------------------------------------------------
    day_hi, day_lo = max(c.h for c in candles), min(c.l for c in candles)
    if day_hi > day_lo:
        pos = (px - day_lo) / (day_hi - day_lo) * 100
        ev["position_in_day_range_pct"] = round(pos, 1)
        if pos >= 85:
            fire("premium_in_day_range")

    # --- relative / sector ----------------------------------------------------------------
    if sector_change_pct is not None and stock_change_pct is not None:
        ev["sector_change_pct"] = sector_change_pct
        if sector_change_pct < 0 <= stock_change_pct:
            fire("sector_weakening")
    else:
        unknown.append("sector_weakening")

    if index_change_pct is not None and stock_change_pct is not None:
        ev["index_change_pct"] = index_change_pct
        if stock_change_pct < index_change_pct:
            fire("relative_weakness")
    else:
        unknown.append("relative_weakness")

    # --- score -----------------------------------------------------------------------------
    raw = sum(w for w, _ in fired)
    # Normalise against the weight that was actually EVALUABLE, so a thin feed does not read as a
    # healthy trend simply because signals could not be checked.
    checkable = sum(w for n, w in WEIGHTS.items() if n not in unknown)
    prob = min(100.0, (raw / checkable * 100.0)) if checkable else 0.0
    stage = next(s for cut, s in STAGE_CUTS if prob >= cut)

    return Reading(probability=prob, stage=stage,
                   signals=[n for _, n in sorted(fired, reverse=True)],
                   unknown=unknown, evidence=ev)


def from_indicator_json(payload: dict) -> Reading:
    """Read a v2 indicator payload (stock_analyze_intraday_2.py) directly.

    Field names verified against real output on 2026-08-01 — the v2 blocks are `indicators`,
    `institutional` and `intraday_structure`, NOT the `momentum`/`moving_averages`/`volatility`
    names the shortswing tool uses. Guessing them would have silently produced an all-unknown
    reading that scored 0 and looked like a healthy trend.
    """
    p = payload or {}
    ind = p.get("indicators") or {}
    inst = p.get("institutional") or {}
    bars = [Candle(t=str(b.get("t", "")), o=float(b["o"]), h=float(b["h"]), l=float(b["l"]),
                   c=float(b["c"]), v=float(b.get("v") or 0))
            for b in (p.get("recent_bars") or [])
            if all(b.get(k) is not None for k in ("o", "h", "l", "c"))]
    return analyse(
        bars,
        rsi_series=ind.get("rsi_series"),
        macd_hist=ind.get("macd_hist_series"),
        vwap=(p.get("vwap") or {}).get("vwap"),
        ema20=inst.get("ema20"),
        ema50=inst.get("ema50"),
        atr=ind.get("atr14_intraday"),
        bb_percent_b=(inst.get("bollinger") or {}).get("percent_b"),
        rvol=(p.get("volume") or {}).get("rvol_vs_prior_days"),
        stock_change_pct=(p.get("price") or {}).get("day_change_pct"),
        index_change_pct=((p.get("market_context") or {}).get("nifty") or {}).get("day_change_pct"),
    )
