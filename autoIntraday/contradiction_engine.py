"""Contradiction detection for the overnight short scan.

Runs AFTER the adaptive score and BEFORE the confidence is accepted. Its job is adversarial: go
looking for evidence that the bearish thesis is wrong, and make that evidence cost something.

Confirmation bias is the failure mode this exists to catch. A scan that only counts supporting
evidence will always find some — the discipline is in what argues against the trade. Three or more
MAJOR contradictions rejects the name outright, regardless of how good the score was.

**Why this is code, not prose in the skill.** A model asked to self-critique grades itself
inconsistently and tends to rationalise a thesis it just built. Deterministic rules over the same
facts penalise identically every night and can be audited afterwards.

A rule whose inputs are missing does NOT silently pass — it is reported as unchecked, because "we
could not look" is different from "we looked and it was fine".

CLI:

    python contradiction_engine.py --confidence 84 --context '{"rvol": 1.1, "rsi14": 26, ...}'
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Any, Callable, Optional

MAJOR_PENALTY = 15.0
MINOR_PENALTY = 7.0
REJECT_AT_MAJOR_COUNT = 3


@dataclass(frozen=True)
class Rule:
    id: str
    severity: str                       # "major" | "minor"
    needs: tuple[str, ...]              # context keys required to evaluate it
    describe: str
    test: Callable[[dict], bool]        # True == the contradiction is present


def _f(ctx: dict, key: str) -> Optional[float]:
    v = ctx.get(key)
    return float(v) if isinstance(v, (int, float)) else None


def _b(ctx: dict, key: str) -> Optional[bool]:
    v = ctx.get(key)
    return bool(v) if isinstance(v, bool) else None


RULES: tuple[Rule, ...] = (
    Rule("bearish_candle_low_rvol", "major", ("pattern_strength", "rvol"),
         "strong bearish candle but low relative volume — no institutional distribution behind it",
         lambda c: _f(c, "pattern_strength") >= 70 and _f(c, "rvol") < 1.5),

    Rule("breakdown_vs_sector_highs", "major", ("sector_making_highs",),
         "breaking down while its sector makes new highs — fighting sector flow",
         lambda c: _b(c, "sector_making_highs") is True),

    Rule("heavy_put_writing", "major", ("pcr_oi",),
         "heavy put writing (PCR well above 1) — option writers are positioned for support",
         lambda c: _f(c, "pcr_oi") >= 1.3),

    Rule("vix_collapsing", "minor", ("vix_change_pct",),
         "India VIX collapsing — falling fear rarely accompanies a fresh downside move",
         lambda c: _f(c, "vix_change_pct") <= -5.0),

    Rule("positive_announcement", "major", ("positive_announcement",),
         "positive corporate announcement against weak price action — news overrides the chart",
         lambda c: _b(c, "positive_announcement") is True),

    Rule("momentum_improving", "minor", ("trend_bearish", "rsi_rising"),
         "trend bearish but momentum improving — the down-move is losing force",
         lambda c: _b(c, "trend_bearish") is True and _b(c, "rsi_rising") is True),

    Rule("near_support_not_resistance", "major", ("near_support",),
         "price sits near support rather than resistance — shorting into a bounce zone",
         lambda c: _b(c, "near_support") is True),

    Rule("oversold_no_catalyst", "major", ("rsi14", "has_catalyst"),
         "oversold RSI with no fresh catalyst — the fall has already happened",
         lambda c: _f(c, "rsi14") < 30 and _b(c, "has_catalyst") is False),

    Rule("bullish_divergence", "major", ("bullish_divergence",),
         "bullish divergence against bearish structure — momentum is diverging from price",
         lambda c: _b(c, "bullish_divergence") is True),

    Rule("max_pain_above_spot", "minor", ("max_pain", "spot"),
         "max pain sits ABOVE spot — expiry positioning pulls price up, not down",
         lambda c: _f(c, "max_pain") > _f(c, "spot")),

    Rule("already_extended_down", "minor", ("pct_below_20d_high",),
         "already far below its 20-day high — most of the move is behind it",
         lambda c: _f(c, "pct_below_20d_high") >= 15.0),
)


class ContradictionError(Exception):
    """A refusal — an unusable confidence or context."""


def analyse(confidence: float, context: dict[str, Any]) -> dict[str, Any]:
    """Find contradictions, apply penalties, and decide whether the name survives at all."""
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 100:
        raise ContradictionError(f"confidence must be 0-100, got {confidence!r}")
    context = context or {}

    found, unchecked = [], []
    for rule in RULES:
        if any(context.get(k) is None for k in rule.needs):
            unchecked.append({"id": rule.id, "missing": [k for k in rule.needs
                                                         if context.get(k) is None]})
            continue
        try:
            hit = bool(rule.test(context))
        except (TypeError, ValueError):
            unchecked.append({"id": rule.id, "missing": ["unevaluable: " + ", ".join(rule.needs)]})
            continue
        if hit:
            found.append({"id": rule.id, "severity": rule.severity,
                          "penalty": MAJOR_PENALTY if rule.severity == "major" else MINOR_PENALTY,
                          "detail": rule.describe})

    majors = [c for c in found if c["severity"] == "major"]
    penalty = sum(c["penalty"] for c in found)
    adjusted = max(0.0, round(confidence - penalty, 1))
    rejected = len(majors) >= REJECT_AT_MAJOR_COUNT

    return {
        "original_confidence": round(float(confidence), 1),
        "contradictions_found": found,
        "major_count": len(majors),
        "minor_count": len(found) - len(majors),
        "confidence_penalty": round(penalty, 1),
        "final_adjusted_confidence": 0.0 if rejected else adjusted,
        "rejected": rejected,
        # Not the same as "no contradictions" — a rule we could not evaluate is a blind spot, and
        # reporting it stops "we did not look" reading as "we looked and it was clean".
        "unchecked_rules": unchecked,
        "verdict": _verdict(rejected, majors, found, confidence, adjusted, unchecked),
    }


def _verdict(rejected: bool, majors: list, found: list, original: float, adjusted: float,
             unchecked: list) -> str:
    if rejected:
        return (f"REJECTED — {len(majors)} major contradictions "
                f"({', '.join(c['id'] for c in majors)}). At {REJECT_AT_MAJOR_COUNT}+ majors the "
                f"thesis is refused outright, whatever the score said.")
    if not found:
        base = f"No contradictions found; confidence stands at {original:.0f}."
    else:
        base = (f"{len(found)} contradiction(s) cost {original - adjusted:.0f} points: "
                + "; ".join(f"{c['id']} ({c['severity']})" for c in found)
                + f". Adjusted confidence {adjusted:.0f}.")
    if unchecked:
        base += (f" NOT checked for want of data: {', '.join(u['id'] for u in unchecked)} — "
                 f"treat as blind spots, not as clean.")
    return base


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Adversarial contradiction check for a short thesis")
    p.add_argument("--confidence", type=float, required=True, help="adaptive score, 0-100")
    p.add_argument("--context", required=True, help="JSON of evidence — see --list-rules")
    p.add_argument("--list-rules", action="store_true", help="print rules and their inputs")
    args = p.parse_args(argv)
    if args.list_rules:
        print(json.dumps([{"id": r.id, "severity": r.severity, "needs": list(r.needs),
                           "detail": r.describe} for r in RULES], indent=2))
        return 0
    try:
        print(json.dumps(analyse(args.confidence, json.loads(args.context)), indent=2))
    except (ContradictionError, json.JSONDecodeError) as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
