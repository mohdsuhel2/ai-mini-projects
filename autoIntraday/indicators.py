"""Indicator provider — runs the sibling StockAnalayze intraday tool and returns its JSON.

Does not recompute indicators; shells out to stock_analyze_intraday.py exactly as the
intraday-analyst skill does. See docs/superpowers/specs/2026-07-09-decision-engine-design.md.
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Callable

from decision_engine import DecisionEngineError


class IndicatorError(DecisionEngineError):
    """Indicator fetch failed: non-zero exit, empty output, or unparseable JSON."""


_STOCKANALYZE = "/Users/mohdsuhel/ai-mini-projects/StockAnalayze"
DEFAULT_PYTHON = os.environ.get("INTRADAY_PYTHON", f"{_STOCKANALYZE}/.venv/bin/python")
DEFAULT_SCRIPT = os.environ.get("INTRADAY_SCRIPT", f"{_STOCKANALYZE}/stock_analyze_intraday.py")


def _default_runner(argv: list[str], cwd: str) -> tuple[int, str, str]:
    proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=120)
    return proc.returncode, proc.stdout, proc.stderr


def get_indicators(symbol: str,
                   runner: Callable[[list[str], str], tuple[int, str, str]] = _default_runner
                   ) -> dict[str, Any]:
    argv = [DEFAULT_PYTHON, DEFAULT_SCRIPT, "-s", symbol, "--source", "yahoo"]
    cwd = os.path.dirname(DEFAULT_SCRIPT)
    returncode, stdout, stderr = runner(argv, cwd)
    if returncode != 0:
        raise IndicatorError(f"indicator tool exit {returncode} for {symbol}: {stderr.strip()}")
    if not stdout or not stdout.strip():
        raise IndicatorError(f"indicator tool returned empty output for {symbol}")
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as e:
        raise IndicatorError(f"could not parse indicator JSON for {symbol}: {e}") from e
