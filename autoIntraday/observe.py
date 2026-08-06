"""Skill Lab runner — ask every selected skill what it would do, record it, place nothing.

The safety property is structural, not a flag: this module never imports a broker client and has
no code path that can place, modify or cancel an order. It takes a symbol source and an indicator
function, calls skills, and writes rows.

Fairness matters as much as safety. Indicators are fetched ONCE per symbol and the identical
payload is handed to every skill, so a difference in the answers is a difference in the skills
rather than a difference in the data or the minute they ran.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable, Optional, Sequence

from observe_store import ObserveStore, today_ist

log = logging.getLogger("autointraday.observe")

SKILLS_DIR = os.path.expanduser("~/.claude/skills")


def available_skills(skills_dir: str = SKILLS_DIR) -> list[str]:
    """Every installed skill that has a SKILL.md, newest-agnostic and sorted.

    Discovered rather than hard-coded so a skill added tomorrow shows up in the UI without a code
    change — the whole point is to compare skills we have not written yet.
    """
    try:
        return sorted(d for d in os.listdir(skills_dir)
                      if os.path.isfile(os.path.join(skills_dir, d, "SKILL.md")))
    except OSError:
        return []


def skill_path(skill_id: str, skills_dir: str = SKILLS_DIR) -> str:
    return os.path.join(skills_dir, skill_id, "SKILL.md")


def _engine_for(skill_id: str, model: Optional[str] = None, runner=None):
    from decision_engine import MODEL
    from skill_decision_engine import SkillDecisionEngine
    kw = {} if runner is None else {"runner": runner}
    # WebSearch off: a shadow run is for comparing methodology on identical data, and a search
    # makes two skills' inputs differ in ways nothing here can account for.
    return SkillDecisionEngine(use_web_search=False, model=model or MODEL,
                               skill_path=skill_path(skill_id), **kw)


def resolve_symbols(cfg: dict, store: ObserveStore,
                    get_candidates: Optional[Callable[..., list]] = None,
                    get_open_symbols: Optional[Callable[[], list]] = None) -> list[str]:
    """The names to ask about this run, per the configured universe mode."""
    mode = cfg.get("universe_mode") or "screener"
    limit = max(1, int(cfg.get("max_symbols") or 5))
    if mode == "watchlist":
        return store.watchlist()[:limit]
    if mode == "book":
        return list(get_open_symbols() if get_open_symbols else [])[:limit]
    if not get_candidates:
        return []
    out = []
    for c in get_candidates() or []:
        sym = c.get("symbol") if isinstance(c, dict) else getattr(c, "symbol", c)
        if sym:
            out.append(str(sym).upper())
    return out[:limit]


def run_once(store: ObserveStore, get_indicators: Callable[[str], dict],
             get_candidates: Optional[Callable[..., list]] = None,
             get_open_symbols: Optional[Callable[[], list]] = None,
             engine_factory: Callable[[str], Any] = _engine_for,
             trade_date: Optional[str] = None) -> dict:
    """One shadow pass: every selected skill against every selected symbol.

    Returns a summary dict. Never raises for a single failed skill call — one skill erroring must
    not cost the comparison for the others, so the error is recorded as the row's outcome.
    """
    cfg = store.get_config()
    date = trade_date or today_ist()
    if not int(cfg.get("observe_enabled") or 0):
        return {"status": "SKIPPED", "reason": "observer disabled",
                "run_id": store.record_skipped("observer disabled", date)}

    skills = store.skills()
    if not skills:
        return {"status": "SKIPPED", "reason": "no skills selected",
                "run_id": store.record_skipped("no skills selected", date)}

    symbols = resolve_symbols(cfg, store, get_candidates, get_open_symbols)
    if not symbols:
        return {"status": "SKIPPED", "reason": f"no symbols for mode {cfg.get('universe_mode')}",
                "run_id": store.record_skipped(
                    f"no symbols for mode {cfg.get('universe_mode')}", date)}

    # The live trading cycle shares this Claude usage window. If the day's budget cannot cover a
    # full pass, skip rather than half-run: a partial pass compares skills on different symbol
    # sets, which is worse than no data.
    need = len(skills) * len(symbols)
    left = store.budget_left(date)
    if left < need:
        reason = (f"daily call budget: need {need}, {left} left of "
                  f"{cfg['daily_call_budget']} — skipping to protect the live cycle")
        return {"status": "SKIPPED", "reason": reason,
                "run_id": store.record_skipped(reason, date)}

    run_id = store.start_run(skills, symbols, date)
    calls = errors = 0
    try:
        for symbol in symbols:
            # ONE indicator fetch per symbol, shared by every skill — the comparison is only
            # meaningful if the inputs are byte-identical.
            try:
                indicators = get_indicators(symbol)
            except Exception as e:                                  # noqa: BLE001
                for sk in skills:
                    store.record(run_id, sk, symbol, error=f"indicators: {e}", trade_date=date)
                    errors += 1
                continue
            for sk in skills:
                t0 = time.time()
                try:
                    decision = engine_factory(sk).decide(symbol, indicators, position=None,
                                                         book=None)
                    store.record(run_id, sk, symbol, decision=decision,
                                 latency_ms=int((time.time() - t0) * 1000), trade_date=date)
                except Exception as e:                              # noqa: BLE001
                    log.warning("observe: %s / %s failed: %s", sk, symbol, e)
                    store.record(run_id, sk, symbol, error=str(e)[:400],
                                 latency_ms=int((time.time() - t0) * 1000), trade_date=date)
                    errors += 1
                calls += 1
        store.finish_run(run_id, "SUCCESS", calls=calls, errors=errors)
        return {"status": "SUCCESS", "run_id": run_id, "calls": calls, "errors": errors,
                "skills": skills, "symbols": symbols}
    except Exception as e:                                          # noqa: BLE001
        store.finish_run(run_id, "FAILED", calls=calls, errors=errors, error=str(e)[:400])
        raise
