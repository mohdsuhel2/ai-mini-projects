"""Committee of independent expert analysts for the overnight short scan.

Replaces a single reasoning chain with seven specialists who never see each other's conclusions,
then aggregates their votes deterministically.

**Why independence needs separate calls.** Seven "experts" prompted inside one context are not
independent — each reads the previous ones' reasoning and anchors on it, which is precisely the
correlated failure the committee exists to break. So `run_committee` spawns one subprocess per
expert, each receiving ONLY the market data and its own brief. They are run in parallel to keep
wall-clock reasonable.

**Why aggregation is code.** Vote counting, the Risk Manager veto and the consensus score must be
applied identically every night and be auditable afterwards. A model asked to weigh its own
committee's votes rationalises whichever answer it reached first.

The Risk Manager holds an absolute veto: poor risk-reward, event risk, illiquidity or excessive
uncertainty kills the trade regardless of how bearish the other six are. A committee that can be
outvoted on risk is not a risk committee.
"""
from __future__ import annotations

import concurrent.futures as futures
import json
import math
import logging
import os
import subprocess
from dataclasses import dataclass
from typing import Any, Optional, Sequence

log = logging.getLogger("autointraday.committee")

# "abstain" is distinct from "neutral": neutral means the expert looked and is undecided;
# abstain means it had no data to judge its mandate at all. Conflating them let data-starved
# experts silently vote against every trade — in the 2026-08-01 backtest the Options and Macro
# seats never had data, so a fixed 4-of-6 threshold demanded unanimity from the other four and
# rejected 5/5 candidates.
VERDICTS = ("bearish", "bullish", "neutral", "reject", "abstain")
RISK_MANAGER = "risk_manager"

# Each expert sees the same data and only its own mandate. Kept deliberately narrow: a specialist
# that reasons about everything is just the single chain again, wearing a hat.
EXPERTS: dict[str, str] = {
    "trend": "You are a TREND specialist. Judge ONLY trend: direction, moving-average posture and "
             "alignment, slope, maturity, extension, and whether the larger timeframe supports a "
             "short. Ignore volume, options and news — other analysts cover those.",
    "price_action": "You are a PRICE ACTION specialist. Judge ONLY candles and structure: "
                    "reversal patterns, wicks, closes, lower highs, break of structure, failed "
                    "breakouts, and whether the pattern sits AT resistance or mid-range.",
    "volume": "You are a VOLUME specialist. Judge ONLY participation: relative volume, "
              "distribution vs drift, up-candles on falling volume, down-candles on rising "
              "volume, climax and divergence. No volume evidence means no institutional "
              "distribution.",
    "smart_money": "You are a SMART MONEY specialist. Judge ONLY institutional behaviour: "
                   "distribution, liquidity sweeps, supply zones, order-block rejection, "
                   "premium pricing, traps and where late retail is likely positioned.",
    "options": "You are an OPTIONS specialist. Judge ONLY positioning: PCR, max pain versus spot, "
               "call and put OI walls, and what writers are defending. If no option chain is "
               "available, ABSTAIN — never guess positioning, and do not vote neutral, which "
               "would count against the trade rather than removing you from the quorum.",
    "macro": "You are a MACRO specialist. Judge ONLY context: index trend and breadth, India VIX, "
             "sector rotation, relative strength versus the market, and global cues. Shorting a "
             "single name into a strongly advancing tape is a macro objection. If you have no "
             "index, VIX or sector data at all, ABSTAIN rather than voting neutral.",
    RISK_MANAGER: "You are the RISK MANAGER and you hold a VETO. Judge ONLY tradeability and "
                  "risk: is the risk-reward acceptable, is the stop structurally sound and not "
                  "hair-thin, is the name liquid enough to exit, is there event risk "
                  "(results/ex-dividend/F&O ban), and is the setup too uncertain to size? "
                  "Return 'reject' if ANY of these fail. You are not asked whether the stock will "
                  "fall — only whether this trade may responsibly be taken.",
}

# A short thesis needs a real majority of the analysts who could actually VOTE. Expressed as a
# fraction so an abstaining expert shrinks the quorum instead of counting against the trade:
# 4-of-6 and 3-of-4 are the same bar, but a fixed 4 is an impossible bar when two seats abstain.
MIN_BEARISH_FRACTION = 2 / 3
# Fewer voters than this is not a committee. Refuse rather than let two experts decide.
MIN_VOTING_ANALYSTS = 3
# Spread of confidence among the bearish camp. Wide disagreement means the thesis rests on one
# loud expert, so the consensus score is discounted.
DISAGREEMENT_PENALTY = 0.85


class CommitteeError(Exception):
    """A malformed verdict set — refuse rather than guess."""


@dataclass(frozen=True)
class Verdict:
    expert: str
    verdict: str            # bearish | bullish | neutral | reject
    confidence: float       # 0-100
    reasoning: str = ""


def parse_verdict(expert: str, payload: Any) -> Verdict:
    """One expert's reply -> Verdict. Raises on anything unusable."""
    if not isinstance(payload, dict):
        raise CommitteeError(f"{expert}: expected an object, got {type(payload).__name__}")
    v = str(payload.get("verdict", "")).strip().lower()
    if v not in VERDICTS:
        raise CommitteeError(f"{expert}: verdict must be one of {VERDICTS}, got {v!r}")
    try:
        c = float(payload.get("confidence"))
    except (TypeError, ValueError):
        raise CommitteeError(f"{expert}: confidence must be a number")
    if not 0 <= c <= 100:
        raise CommitteeError(f"{expert}: confidence must be 0-100, got {c}")
    return Verdict(expert=expert, verdict=v, confidence=c,
                   reasoning=str(payload.get("reasoning", "")).strip())


def aggregate(verdicts: Sequence[Verdict]) -> dict[str, Any]:
    """Committee decision. Deterministic: same votes always give the same recommendation."""
    if not verdicts:
        raise CommitteeError("no verdicts to aggregate")
    seen = [v.expert for v in verdicts]
    if len(set(seen)) != len(seen):
        raise CommitteeError(f"duplicate expert verdicts: {seen}")

    by_expert = {v.expert: v for v in verdicts}
    rm = by_expert.get(RISK_MANAGER)
    analysts = [v for v in verdicts if v.expert != RISK_MANAGER]

    tally = {k: [v.expert for v in analysts if v.verdict == k] for k in VERDICTS}
    bearish = tally["bearish"]
    voting = [v for v in analysts if v.verdict != "abstain"]
    required = max(1, math.ceil(len(voting) * MIN_BEARISH_FRACTION))

    # --- the veto, before anything else -------------------------------------------------------
    if rm is None:
        return _result("REJECTED", 0.0, tally, verdicts,
                       "No Risk Manager verdict — the veto seat cannot be empty, so the trade is "
                       "refused rather than assumed safe.")
    if rm.verdict == "reject":
        return _result("REJECTED", 0.0, tally, verdicts,
                       f"Risk Manager VETO ({rm.confidence:.0f}% confidence): "
                       f"{rm.reasoning or 'no reason given'}. The veto is absolute — "
                       f"{len(bearish)} bearish analyst vote(s) cannot override it.")

    # --- consensus ----------------------------------------------------------------------------
    if len(voting) < MIN_VOTING_ANALYSTS:
        return _result("NO_TRADE", 0.0, tally, verdicts,
                       f"Only {len(voting)}/{len(analysts)} analysts had data to judge "
                       f"({', '.join(tally['abstain'])} abstained). Fewer than "
                       f"{MIN_VOTING_ANALYSTS} voters is not a committee.")
    if len(bearish) < required:
        return _result("NO_TRADE", 0.0, tally, verdicts,
                       f"Only {len(bearish)}/{len(voting)} VOTING analysts bearish "
                       f"({required} required; {len(tally['abstain'])} abstained for want of "
                       f"data). A split committee is not an edge.")

    confs = [by_expert[e].confidence for e in bearish]
    score = sum(confs) / len(confs)
    spread = max(confs) - min(confs)
    discounted = spread > 30
    if discounted:
        score *= DISAGREEMENT_PENALTY

    # A bullish dissenter is a real objection, not noise: each costs the consensus.
    score -= 5.0 * len(tally["bullish"])
    score = max(0.0, min(100.0, round(score, 1)))

    note = (f"{len(bearish)}/{len(voting)} voting analysts bearish (needed {required}"
            + (f"; {len(tally['abstain'])} abstained: {', '.join(tally['abstain'])}"
               if tally["abstain"] else "")
            + f"); Risk Manager cleared ({rm.verdict}, {rm.confidence:.0f}%). "
              f"Consensus {score:.0f}")
    if discounted:
        note += f" (discounted — confidence spread {spread:.0f} points across the bearish camp)"
    if tally["bullish"]:
        note += f"; {len(tally['bullish'])} bullish dissenter(s): {', '.join(tally['bullish'])}"
    return _result("SHORT", score, tally, verdicts, note + ".")


def _result(rec: str, score: float, tally: dict, verdicts: Sequence[Verdict],
            rationale: str) -> dict[str, Any]:
    return {
        "recommendation": rec,                    # SHORT | NO_TRADE | REJECTED
        "consensus_confidence": score,
        "vetoed": rec == "REJECTED",
        "votes": {k: v for k, v in tally.items()},
        "vote_counts": {k: len(v) for k, v in tally.items()},
        "rationale": rationale,
        "expert_verdicts": [{"expert": v.expert, "verdict": v.verdict,
                             "confidence": v.confidence, "reasoning": v.reasoning}
                            for v in verdicts],
    }


# ---------------------------------------------------------------------------------------------
# Running the experts for real — one subprocess each, in parallel, no shared context
# ---------------------------------------------------------------------------------------------
_SCHEMA_NOTE = (
    'Return ONLY a JSON object: {"verdict": "bearish|bullish|neutral|reject|abstain", '
    '"confidence": <0-100>, "reasoning": "<one or two sentences of concrete evidence>"}. '
    "Judge ONLY your own mandate. You are one member of a committee and you will NOT see the "
    "other members' views — do not speculate about them. "
    "Use ABSTAIN when you have no data to judge your mandate at all — that is different from "
    "neutral, which means you looked and are undecided. Abstaining removes you from the quorum "
    "rather than counting as a vote against the trade, so do not vote neutral to be safe."
)


def _ask(expert: str, brief: str, data: str, model: str, timeout: int) -> Verdict:
    p = subprocess.run(
        ["claude", "-p", "--output-format", "json", "--model", model,
         "--append-system-prompt", f"{brief}\n\n{_SCHEMA_NOTE}"],
        input=f"Analyse this short candidate for the NEXT session.\n\n{data}",
        capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise CommitteeError(f"{expert}: claude exit {p.returncode}: {p.stderr[:200]}")
    txt = p.stdout
    try:
        txt = json.loads(p.stdout).get("result", p.stdout)
    except Exception:
        pass
    a, b = txt.find("{"), txt.rfind("}")
    if a == -1 or b <= a:
        raise CommitteeError(f"{expert}: no JSON in reply")
    return parse_verdict(expert, json.loads(txt[a:b + 1]))


def run_committee(data: str, experts: Optional[dict[str, str]] = None,
                  model: Optional[str] = None, timeout: int = 300) -> dict[str, Any]:
    """Run every expert in its OWN process and aggregate. Parallel; failures are reported.

    An expert that errors is not silently dropped — a missing analyst changes the vote count, and
    a missing Risk Manager refuses the trade outright.
    """
    experts = experts or EXPERTS
    model = model or os.environ.get("COMMITTEE_MODEL", "claude-opus-4-8")
    verdicts, failures = [], []
    with futures.ThreadPoolExecutor(max_workers=len(experts)) as pool:
        jobs = {pool.submit(_ask, e, b, data, model, timeout): e for e, b in experts.items()}
        for job in futures.as_completed(jobs):
            expert = jobs[job]
            try:
                verdicts.append(job.result())
            except Exception as exc:
                failures.append({"expert": expert, "error": str(exc)[:200]})
                log.warning("%s failed: %s", expert, exc)
    if not verdicts:
        raise CommitteeError(f"every expert failed: {failures}")
    out = aggregate(verdicts)
    out["failed_experts"] = failures
    if failures:
        out["rationale"] += (f" NOTE: {len(failures)} expert(s) failed to report "
                             f"({', '.join(f['expert'] for f in failures)}) — the vote is "
                             f"incomplete, not unanimous.")
    return out
