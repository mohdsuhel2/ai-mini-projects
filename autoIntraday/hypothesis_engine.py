"""Competing Hypothesis Engine — reversal detection without trend-following bias.

Replaces the majority-vote committee, which was structurally incapable of finding reversals.

The failure it fixes, from the 2026-08-01 backtest: on INDUSINDBK the Trend (88%), Price Action
(60%) and Volume (62%) experts all voted BULLISH because they read lagging evidence — MAs aligned
up, a fresh breakout, volume expanding. Smart Money alone flagged premium pricing at 95% of the
20-day range. The stock fell 5.97% the next day. Three lagging experts outvoted the one specialist
equipped to see a turn, and a majority vote will do that every time by construction.

So voting is gone. Two research teams instead argue OPPOSITE cases over the same facts and never
share reasoning:

  * Hypothesis A — the current move CONTINUES tomorrow
  * Hypothesis B — the current move is ENDING

The decision compares their probabilities. A reversal thesis no longer has to win a popularity
contest against experts whose evidence is lagging by nature.

Three further rules, all learned from the same backtest:

  * Specialists produce domain EVIDENCE, not direction verdicts. Each owns a disjoint domain and
    may not reason outside it.
  * The Risk Manager never judges direction. It vetoed INDUSINDBK for being "countertrend into a
    volume-confirmed breakout" — a Trend opinion wearing a risk hat. It may now reject only for
    objective execution reasons.
  * UNKNOWN is not negative. Every veto in that backtest ended with "event risk cannot be
    cleared", because absence of news was treated as evidence of danger. Unknown now trims
    confidence and nothing more.
"""
from __future__ import annotations

import concurrent.futures as futures
import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger("autointraday.hypothesis_engine")

# Three-state evidence. UNKNOWN must never act as KNOWN_NEGATIVE.
KNOWN_POSITIVE, KNOWN_NEGATIVE, UNKNOWN = "known_positive", "known_negative", "unknown"
EVIDENCE_STATES = (KNOWN_POSITIVE, KNOWN_NEGATIVE, UNKNOWN)

RISK_LEVELS = ("low", "medium", "high")

# --- Specialists: disjoint domains, evidence only, no directional verdict ---------------------
SPECIALISTS: dict[str, str] = {
    "trend": "TREND specialist. Report ONLY: moving-average structure and alignment, trend "
             "quality, trend maturity and extension. Nothing else — not volume, not candles, not "
             "options, not macro. Do not say whether to short.",
    "price_action": "PRICE ACTION specialist. Report ONLY: candlestick structure, breakouts and "
                    "their quality, support and resistance levels and how price behaved at them. "
                    "Nothing else — not volume, not trend quality, not macro.",
    "volume": "VOLUME specialist. Report ONLY: relative volume, distribution vs accumulation, "
              "climactic volume, and volume/price divergence. Nothing else.",
    "smart_money": "SMART MONEY specialist. Report ONLY: institutional distribution, liquidity "
                   "grabs, premium vs discount pricing, failed breakouts, exhaustion signatures "
                   "and where late retail is positioned. Nothing else.",
    "options": "OPTIONS specialist. Report ONLY: option chain, PCR, max pain, call walls, put "
               "walls. Nothing else. If no chain is available, mark every field UNKNOWN — never "
               "guess positioning, and never treat missing data as bearish.",
    "macro": "MACRO specialist. Report ONLY: NIFTY trend, India VIX, sector behaviour and global "
             "cues. Nothing else. If unavailable, mark UNKNOWN.",
}

_SPECIALIST_SCHEMA = (
    'Return ONLY JSON: {"findings": [{"item": "<what you measured>", '
    '"state": "known_positive|known_negative|unknown", "detail": "<evidence>"}], '
    '"summary": "<one sentence>"}. '
    'state describes DATA AVAILABILITY AND DIRECTION for your domain: known_positive = you '
    'measured it and it supports weakness/downside; known_negative = you measured it and it '
    'argues against downside; unknown = you could not measure it. '
    'You are one of several specialists with disjoint domains. Do NOT reason outside your domain '
    'and do NOT recommend a trade — you supply evidence, not decisions.'
)

# --- The two competing hypotheses. They never see each other's reasoning. ---------------------
HYPOTHESES: dict[str, str] = {
    "continuation": (
        "You lead the CONTINUATION research team. Your ONLY job is to build the strongest honest "
        "case that today's move CONTINUES tomorrow, and to estimate its probability.\n\n"
        "Use only evidence relevant to continuation: trend alignment, SMA structure, breakout "
        "quality, momentum, relative strength, volume confirmation, market regime.\n\n"
        "You are NOT arguing about whether to short. You are estimating P(move continues). Be "
        "honest about what weakens your own case — a team that hides its weaknesses is useless."),
    "reversal": (
        "You lead the REVERSAL research team. Your ONLY job is to build the strongest honest case "
        "that the current move is ENDING, and to estimate its probability.\n\n"
        "Use only evidence relevant to reversals: distribution, exhaustion, parabolic extension, "
        "large upper wicks, bearish divergence, premium pricing, resistance rejection, liquidity "
        "sweeps, failed breakouts, smart-money distribution, profit booking, sector exhaustion.\n\n"
        "Institutional distribution BEGINS BEFORE obvious trend deterioration — a trend that still "
        "looks healthy on moving averages is exactly where reversals start, so do not discount "
        "your case merely because the trend is intact. Be honest about what weakens it."),
}

_HYPOTHESIS_SCHEMA = (
    'Return ONLY JSON: {"probability": <0-100>, "supporting_evidence": ["..."], '
    '"weaknesses": ["..."], "summary": "<one or two sentences>"}. '
    'probability is your estimate for YOUR hypothesis alone — it need not complement the other '
    'team, whose reasoning you will never see. Do not hedge toward 50 to appear balanced.'
)

# --- Risk Manager: execution only, never direction -------------------------------------------
RISK_MANAGER_BRIEF = (
    "You are the RISK MANAGER. You NEVER judge market direction.\n\n"
    "You may NOT reject a trade because the breakout looks strong, the trend is bullish, the move "
    "is countertrend, or the pattern might continue. Those are Trend and Price Action's domain "
    "and are not your business.\n\n"
    "Judge ONLY: tradeability, execution quality, liquidity, risk-reward, stop placement, whether "
    "the position can be sized safely, and data quality.\n\n"
    "You may VETO only for OBJECTIVE execution or market-access reasons: insufficient liquidity, "
    "impossible execution, a corporate action, confirmed earnings tomorrow, an invalid stop "
    "(inverted, or inside normal noise), unacceptable risk-reward, or a position that cannot be "
    "sized safely.\n\n"
    "MISSING DATA IS NOT A VETO. If you cannot verify event risk, say so as a caveat and mark it "
    "unknown — do not reject for it. Otherwise return a risk level and a position size."
)

_RISK_SCHEMA = (
    'Return ONLY JSON: {"risk_level": "low|medium|high", "veto": <true|false>, '
    '"veto_reason": "<objective execution reason, or null>", '
    '"position_size_pct": <0-100, share of normal size>, "caveats": ["..."], '
    '"summary": "<one sentence>"}. '
    'Set veto=true ONLY for an objective execution or market-access failure. Never veto for a '
    'directional opinion, and never veto merely because data is missing.'
)

# Decision thresholds. The reversal case must genuinely beat continuation, not merely tie.
MIN_EDGE = 10.0                 # reversal probability must exceed continuation by this
MIN_REVERSAL_PROB = 55.0        # ...and stand on its own
UNKNOWN_CONFIDENCE_COST = 2.0   # per unknown finding — a trim, never a rejection
MAX_UNKNOWN_COST = 20.0
RISK_SIZE = {"low": 1.0, "medium": 0.6, "high": 0.3}


class HypothesisError(Exception):
    """A malformed input — refuse rather than guess."""


@dataclass
class Hypothesis:
    name: str
    probability: float
    supporting: list = field(default_factory=list)
    weaknesses: list = field(default_factory=list)
    summary: str = ""


@dataclass
class RiskAssessment:
    risk_level: str = "medium"
    veto: bool = False
    veto_reason: Optional[str] = None
    position_size_pct: float = 100.0
    caveats: list = field(default_factory=list)
    summary: str = ""


def count_unknowns(findings: list[dict]) -> tuple[int, int]:
    """(unknown, total) across specialist findings."""
    total = len(findings or [])
    return sum(1 for f in (findings or []) if f.get("state") == UNKNOWN), total


def decide(continuation: Hypothesis, reversal: Hypothesis, risk: RiskAssessment,
           findings: Optional[list[dict]] = None, rr: float = 2.0) -> dict[str, Any]:
    """Compare the two hypotheses and produce the trade decision. Pure and deterministic."""
    for h in (continuation, reversal):
        if not 0 <= h.probability <= 100:
            raise HypothesisError(f"{h.name} probability must be 0-100, got {h.probability}")
    if risk.risk_level not in RISK_LEVELS:
        raise HypothesisError(f"risk_level must be one of {RISK_LEVELS}, got {risk.risk_level!r}")

    edge = reversal.probability - continuation.probability
    unknown, total = count_unknowns(findings or [])
    unknown_cost = min(MAX_UNKNOWN_COST, unknown * UNKNOWN_CONFIDENCE_COST)

    # Expected value in R multiples: win rr R with probability p, lose 1R otherwise.
    p = reversal.probability / 100.0
    ev_r = round(p * rr - (1 - p) * 1.0, 3)

    confidence = max(0.0, min(100.0, round(reversal.probability - unknown_cost, 1)))
    size = round(RISK_SIZE[risk.risk_level] * 100.0, 1) if not risk.veto else 0.0
    if risk.position_size_pct is not None:
        size = min(size, float(risk.position_size_pct))

    if risk.veto:
        decision, why = "REJECTED", (
            f"Risk Manager veto (objective): {risk.veto_reason or 'no reason given'}. "
            f"Direction was not the issue — reversal {reversal.probability:.0f}% vs "
            f"continuation {continuation.probability:.0f}%.")
    elif reversal.probability < MIN_REVERSAL_PROB:
        decision, why = "NO_TRADE", (
            f"Reversal case too weak on its own ({reversal.probability:.0f}% < "
            f"{MIN_REVERSAL_PROB:.0f}%), regardless of the {edge:+.0f} point edge.")
    elif edge < MIN_EDGE:
        decision, why = "NO_TRADE", (
            f"Reversal {reversal.probability:.0f}% does not beat continuation "
            f"{continuation.probability:.0f}% by the required {MIN_EDGE:.0f} points "
            f"(edge {edge:+.0f}). Competing hypotheses too close to separate.")
    elif ev_r <= 0:
        decision, why = "NO_TRADE", (
            f"Negative expected value ({ev_r:+.2f}R at {rr:.1f}:1) despite a "
            f"{edge:+.0f} point reversal edge.")
    else:
        decision, why = "SHORT", (
            f"Reversal {reversal.probability:.0f}% beats continuation "
            f"{continuation.probability:.0f}% by {edge:+.0f} points; EV {ev_r:+.2f}R at "
            f"{rr:.1f}:1; {risk.risk_level} risk -> {size:.0f}% size.")
    if unknown and decision != "REJECTED":
        why += (f" {unknown}/{total} findings UNKNOWN — confidence trimmed {unknown_cost:.0f} "
                f"points (unknown is not negative evidence).")

    return {
        "decision": decision,
        "continuation_probability": round(continuation.probability, 1),
        "reversal_probability": round(reversal.probability, 1),
        "edge": round(edge, 1),
        "risk_score": risk.risk_level,
        "expected_value_r": ev_r,
        "confidence": confidence,
        "position_size_pct": size,
        "unknown_findings": unknown,
        "total_findings": total,
        "rationale": why,
        "continuation_case": {"probability": continuation.probability,
                              "supporting": continuation.supporting,
                              "weaknesses": continuation.weaknesses,
                              "summary": continuation.summary},
        "reversal_case": {"probability": reversal.probability,
                          "supporting": reversal.supporting,
                          "weaknesses": reversal.weaknesses,
                          "summary": reversal.summary},
        "risk": {"level": risk.risk_level, "veto": risk.veto, "veto_reason": risk.veto_reason,
                 "caveats": risk.caveats, "summary": risk.summary},
    }


# ---------------------------------------------------------------------------------------------
# Running it for real — every role in its own process
# ---------------------------------------------------------------------------------------------
def _ask(brief: str, schema: str, payload: str, model: str, timeout: int) -> dict:
    p = subprocess.run(
        ["claude", "-p", "--output-format", "json", "--model", model,
         "--append-system-prompt", f"{brief}\n\n{schema}"],
        input=payload, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise HypothesisError(f"claude exit {p.returncode}: {p.stderr[:200]}")
    txt = p.stdout
    try:
        txt = json.loads(p.stdout).get("result", p.stdout)
    except Exception:
        pass
    a, b = txt.find("{"), txt.rfind("}")
    if a == -1 or b <= a:
        raise HypothesisError(f"no JSON in reply: {txt[:200]}")
    return json.loads(txt[a:b + 1])


def run_engine(data: str, model: Optional[str] = None, timeout: int = 300,
               rr: float = 2.0) -> dict[str, Any]:
    """Specialists -> two competing hypotheses -> risk -> decision.

    The hypothesis teams receive the specialists' FACTS but never each other's reasoning: separate
    processes, opposite mandates.
    """
    model = model or os.environ.get("HYPOTHESIS_MODEL", "claude-opus-4-8")
    findings, failures = [], []

    with futures.ThreadPoolExecutor(max_workers=len(SPECIALISTS)) as pool:
        jobs = {pool.submit(_ask, b, _SPECIALIST_SCHEMA, data, model, timeout): name
                for name, b in SPECIALISTS.items()}
        for job in futures.as_completed(jobs):
            name = jobs[job]
            try:
                r = job.result()
                for f in r.get("findings", []):
                    f["specialist"] = name
                    if f.get("state") not in EVIDENCE_STATES:
                        f["state"] = UNKNOWN
                    findings.append(f)
            except Exception as e:
                failures.append({"specialist": name, "error": str(e)[:200]})
                # A specialist that fails contributes an UNKNOWN, never a negative.
                findings.append({"specialist": name, "item": "specialist unavailable",
                                 "state": UNKNOWN, "detail": str(e)[:120]})

    evidence = json.dumps({"market_data_note": "specialist findings below", "findings": findings},
                          default=str)[:12000]
    combined = f"{data}\n\nSPECIALIST FINDINGS:\n{evidence}"

    with futures.ThreadPoolExecutor(max_workers=3) as pool:
        jc = pool.submit(_ask, HYPOTHESES["continuation"], _HYPOTHESIS_SCHEMA, combined, model,
                         timeout)
        jr = pool.submit(_ask, HYPOTHESES["reversal"], _HYPOTHESIS_SCHEMA, combined, model,
                         timeout)
        jk = pool.submit(_ask, RISK_MANAGER_BRIEF, _RISK_SCHEMA, combined, model, timeout)
        cont_raw, rev_raw, risk_raw = jc.result(), jr.result(), jk.result()

    cont = Hypothesis("continuation", float(cont_raw.get("probability", 50)),
                      cont_raw.get("supporting_evidence", []), cont_raw.get("weaknesses", []),
                      cont_raw.get("summary", ""))
    rev = Hypothesis("reversal", float(rev_raw.get("probability", 50)),
                     rev_raw.get("supporting_evidence", []), rev_raw.get("weaknesses", []),
                     rev_raw.get("summary", ""))
    risk = RiskAssessment(
        risk_level=str(risk_raw.get("risk_level", "medium")).lower(),
        veto=bool(risk_raw.get("veto", False)),
        veto_reason=risk_raw.get("veto_reason"),
        position_size_pct=float(risk_raw.get("position_size_pct", 100) or 100),
        caveats=risk_raw.get("caveats", []), summary=risk_raw.get("summary", ""))

    out = decide(cont, rev, risk, findings, rr=rr)
    out["specialist_findings"] = findings
    out["failed_specialists"] = failures
    return out
