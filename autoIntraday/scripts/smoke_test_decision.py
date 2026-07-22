#!/usr/bin/env python3
"""Manual, not-CI smoke test: fetch real indicators for one symbol and make a real
claude-opus-4-8 decision call (with web search). Verifies the whole Phase 3 path end to end.

Usage: .venv/bin/python scripts/smoke_test_decision.py RELIANCE
Requires ANTHROPIC_API_KEY (or an `ant` login) and the sibling StockAnalayze venv.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from settings import load_settings
load_settings().apply_to_environ()

from decision_engine import DecisionEngine, DecisionEngineError
from indicators import get_indicators


def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"
    try:
        print(f"fetching indicators for {symbol} ...")
        indicators = get_indicators(symbol)
        print("indicators: OK")
        engine = DecisionEngine(use_web_search=True)
        decision = engine.decide(symbol, indicators)
        print(f"decide: OK -> {decision.action} "
              f"(confidence {decision.confidence}, quality {decision.trade_quality})")
        print(f"  entry={decision.entry} stop={decision.stop_loss} "
              f"t1={decision.target1} R:R={decision.risk_reward}")
    except DecisionEngineError as e:
        print(f"FAILED: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
