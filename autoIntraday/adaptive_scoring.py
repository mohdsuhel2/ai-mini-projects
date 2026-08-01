"""Adaptive scoring engine for the overnight short scan.

Replaces the single fixed weight vector. The weight of every factor now depends on the detected
market regime: trend matters most in a strong downtrend, resistance rejection matters most in a
range, options positioning matters most in expiry week.

**Why this is code and not prose in the skill.** The weights must be applied identically every
night and be auditable afterwards — an LLM doing weighted arithmetic in its head produces a
different answer each run, and this feeds real orders. So the skill's job is to (a) name the
regime and (b) score each factor 0-100; this module owns the arithmetic and writes the explanation.

Two properties that matter more than the numbers:

  * **Nothing is scored zero for being unavailable.** A null factor is dropped and the remaining
    weights are renormalised. Zero is a bearish signal; absent is not, and conflating them is how
    a missing option chain silently becomes a short thesis.
  * **Regimes blend.** Expiry week during high volatility is both, not whichever the skill named
    first. Weight vectors are averaged across every detected regime.

CLI so the skill can call it from bash:

    python adaptive_scoring.py --regimes expiry_week,high_volatility \\
        --scores '{"price_action": 82, "trend": 70, "options": null, ...}'
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Iterable, Optional

# Every factor the engine knows. A regime uses a subset; unused factors simply carry no weight.
FACTORS = (
    "price_action", "trend", "smart_money", "options", "volume", "relative_weakness",
    "market_context", "momentum", "volatility", "news", "resistance_rejection", "mean_reversion",
)

# Regime -> weight vector. Each vector MUST sum to 1.0 (enforced by a test). The rationale is not
# decoration: it is emitted in the explanation so a past scan can be re-read and argued with.
REGIMES: dict[str, dict[str, Any]] = {
    "strong_bear_trend": {
        "why": "trend and price action dominate — in a real downtrend, continuation beats cleverness",
        "weights": {"trend": 0.25, "price_action": 0.20, "volume": 0.15, "relative_weakness": 0.15,
                    "options": 0.10, "momentum": 0.05, "volatility": 0.05, "news": 0.05},
    },
    "strong_bull_trend": {
        "why": "shorting into strength is mostly wrong, so only exhaustion and rejection count; "
               "market context is weighted heavily to discourage fighting the tape",
        "weights": {"resistance_rejection": 0.25, "price_action": 0.20, "market_context": 0.15,
                    "smart_money": 0.15, "volume": 0.10, "options": 0.10, "news": 0.05},
    },
    "sideways_range": {
        "why": "levels decide everything in a range — rejection at the top and mean reversion "
               "matter more than trend, which barely exists",
        "weights": {"resistance_rejection": 0.25, "price_action": 0.20, "mean_reversion": 0.15,
                    "volume": 0.15, "options": 0.10, "momentum": 0.10, "news": 0.05},
    },
    "high_volatility": {
        "why": "wide ranges make levels unreliable, so volatility sizing and market context carry "
               "more weight and single-candle price action carries less",
        "weights": {"volatility": 0.20, "market_context": 0.18, "price_action": 0.15,
                    "trend": 0.12, "volume": 0.12, "options": 0.10, "smart_money": 0.08,
                    "news": 0.05},
    },
    "low_volatility": {
        "why": "quiet tape means moves need a real trigger — price action and volume dominate, "
               "and volatility itself says little",
        "weights": {"price_action": 0.24, "volume": 0.18, "trend": 0.16, "smart_money": 0.14,
                    "options": 0.12, "relative_weakness": 0.08, "momentum": 0.05,
                    "volatility": 0.03},
    },
    "expiry_week": {
        "why": "positioning drives price into expiry — max pain and OI walls outweigh the chart",
        "weights": {"options": 0.28, "price_action": 0.18, "smart_money": 0.14, "volume": 0.12,
                    "trend": 0.10, "market_context": 0.08, "volatility": 0.05, "news": 0.05},
    },
    "event_driven": {
        "why": "news overrides the chart; technicals are demoted because an event can invalidate "
               "any level instantly",
        "weights": {"news": 0.30, "market_context": 0.18, "price_action": 0.14, "volume": 0.12,
                    "options": 0.10, "trend": 0.08, "smart_money": 0.08},
    },
    "earnings_heavy": {
        "why": "results dominate direction — heavy news weight, and relative weakness matters "
               "because sector reactions cluster",
        "weights": {"news": 0.26, "relative_weakness": 0.16, "price_action": 0.14,
                    "market_context": 0.12, "volume": 0.12, "options": 0.10, "trend": 0.10},
    },
    "panic_selling": {
        "why": "everything falls together, so relative weakness and market context separate a real "
               "short from beta; mean reversion is weighted as a WARNING against chasing",
        "weights": {"market_context": 0.22, "relative_weakness": 0.20, "volatility": 0.15,
                    "price_action": 0.13, "volume": 0.12, "mean_reversion": 0.10, "trend": 0.08},
    },
    "momentum_rally": {
        "why": "the only shorts worth taking are exhaustion reversals — rejection, smart-money "
               "distribution and volume climax carry the score",
        "weights": {"resistance_rejection": 0.24, "smart_money": 0.20, "volume": 0.16,
                    "price_action": 0.14, "options": 0.10, "market_context": 0.08,
                    "momentum": 0.08},
    },
}


class ScoringError(Exception):
    """A refusal — an unknown regime or an unusable score set."""


def weights_for(regimes: Iterable[str]) -> tuple[dict[str, float], list[str]]:
    """Blended weight vector across every detected regime, plus each regime's rationale.

    Regimes co-occur — expiry week during panic selling is both. Averaging the vectors is more
    honest than forcing a single label, and keeps the result normalised.
    """
    names = [r.strip().lower() for r in regimes if r and r.strip()]
    if not names:
        raise ScoringError("at least one regime is required — the engine has no default vector")
    unknown = [n for n in names if n not in REGIMES]
    if unknown:
        raise ScoringError(f"unknown regime(s): {unknown}; known: {sorted(REGIMES)}")

    blended: dict[str, float] = {}
    for n in names:
        for factor, w in REGIMES[n]["weights"].items():
            blended[factor] = blended.get(factor, 0.0) + w / len(names)
    why = [f"{n}: {REGIMES[n]['why']}" for n in names]
    return {k: round(v, 4) for k, v in blended.items()}, why


def score(scores: dict[str, Optional[float]], regimes: Iterable[str]) -> dict[str, Any]:
    """Final 0-100 confidence from per-factor scores under the regime's adaptive weights.

    `scores` maps factor -> 0-100, or None when the data was unavailable. A None factor is DROPPED
    and the surviving weights are renormalised — never scored zero.
    """
    weights, why = weights_for(regimes)
    unknown = [k for k in scores if k not in FACTORS]
    if unknown:
        raise ScoringError(f"unknown factor(s): {unknown}; known: {list(FACTORS)}")

    used, dropped, missing = {}, [], []
    for factor, w in weights.items():
        val = scores.get(factor, "__absent__")
        if val == "__absent__":
            missing.append(factor)          # the regime wants it but the skill never scored it
            continue
        if val is None:
            dropped.append(factor)
            continue
        used[factor] = (float(val), w)
    if not used:
        raise ScoringError("no scored factor carried weight under this regime")

    total_w = sum(w for _, w in used.values())
    final = sum(v * (w / total_w) for v, w in used.values())

    contributions = sorted(
        ({"factor": f, "score": v, "weight_raw": round(w, 4),
          "weight_effective": round(w / total_w, 4),
          "contribution": round(v * (w / total_w), 2)} for f, (v, w) in used.items()),
        key=lambda c: c["contribution"], reverse=True)

    return {
        "final_score": round(final, 1),
        "regimes": [r.strip().lower() for r in regimes if r and r.strip()],
        "why_these_weights": why,
        "weights_applied": weights,
        "renormalised": bool(dropped or missing),
        "dropped_unavailable": dropped,
        "unscored_but_weighted": missing,
        "contributions": contributions,
        "explanation": _explain(why, contributions, dropped, missing),
    }


def _explain(why: list[str], contributions: list[dict], dropped: list[str],
             missing: list[str]) -> str:
    parts = ["Weights adapted to regime — " + "; ".join(why) + "."]
    top = contributions[:3]
    if top:
        parts.append("Largest contributors: " + ", ".join(
            f"{c['factor']} ({c['score']:.0f} x {c['weight_effective']:.0%})" for c in top) + ".")
    if dropped:
        parts.append(f"Unavailable and renormalised away (NOT scored zero): {', '.join(dropped)}.")
    if missing:
        parts.append(f"Carried weight in this regime but was never scored: {', '.join(missing)}.")
    return " ".join(parts)


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Adaptive, regime-weighted short score")
    p.add_argument("--regimes", required=True,
                   help="comma-separated, e.g. expiry_week,high_volatility")
    p.add_argument("--scores", required=True,
                   help='JSON factor->0-100 or null, e.g. \'{"price_action":82,"options":null}\'')
    p.add_argument("--list-regimes", action="store_true", help="print known regimes and exit")
    args = p.parse_args(argv)
    if args.list_regimes:
        print(json.dumps({k: v["weights"] for k, v in REGIMES.items()}, indent=2))
        return 0
    try:
        print(json.dumps(score(json.loads(args.scores), args.regimes.split(",")), indent=2))
    except (ScoringError, json.JSONDecodeError) as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
