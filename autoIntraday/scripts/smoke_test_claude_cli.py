#!/usr/bin/env python3
"""Manual, not-CI smoke test: run ONE real intraday decision through the headless `claude -p`
backend (on your Claude subscription) for one symbol. Verifies the CLI backend end to end,
including the real `--output-format json` envelope.

Usage: .venv/bin/python scripts/smoke_test_claude_cli.py RELIANCE
Requires: the `claude` CLI installed and logged in to your Claude subscription, and the sibling
StockAnalayze venv (for indicators). Do NOT set ANTHROPIC_API_KEY (it would force API billing).
"""
from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, ".")

from settings import load_settings
load_settings().apply_to_environ()

from claude_cli_engine import ClaudeCliEngine
from decision_engine import DecisionEngineError
from indicators import get_indicators


def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"
    if shutil.which("claude") is None:
        print("FAILED: `claude` CLI not found on PATH — install/login to Claude Code first.")
        sys.exit(1)
    if os.environ.get("ANTHROPIC_API_KEY"):
        print("WARNING: ANTHROPIC_API_KEY is set — `claude` will bill the API, not your "
              "subscription. Unset it to use the subscription.")
    try:
        print(f"fetching indicators for {symbol} ...")
        indicators = get_indicators(symbol)
        print("indicators: OK")
        engine = ClaudeCliEngine(use_web_search=True)
        decision = engine.decide(symbol, indicators)
        print(f"decide (claude_cli): OK -> {decision.action} "
              f"(confidence {decision.confidence}, quality {decision.trade_quality})")
        print(f"  entry={decision.entry} stop={decision.stop_loss} "
              f"t1={decision.target1} R:R={decision.risk_reward}")
    except DecisionEngineError as e:
        print(f"FAILED: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
