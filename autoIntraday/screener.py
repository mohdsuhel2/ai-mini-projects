"""Candidate provider — runs the sibling StockAnalayze groww movers screener and returns its
ranked picks. Same subprocess pattern as indicators.py. See
docs/superpowers/specs/2026-07-09-orchestrator-design.md."""
from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Callable

from decision_engine import DecisionEngineError

_STOCKANALYZE = "/Users/mohdsuhel/ai-mini-projects/StockAnalayze"
DEFAULT_PYTHON = os.environ.get("SCREENER_PYTHON", f"{_STOCKANALYZE}/.venv/bin/python")
DEFAULT_SCRIPT = os.environ.get("SCREENER_SCRIPT", f"{_STOCKANALYZE}/groww_intraday_screener.py")


class ScreenerError(DecisionEngineError):
    """Candidate screen failed: non-zero exit, empty output, bad JSON, or missing picks."""


def _default_runner(argv: list[str], cwd: str) -> tuple[int, str, str]:
    proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=60)
    return proc.returncode, proc.stdout, proc.stderr


def get_candidates(direction: str = "up", top: int = 15, min_price: float = 50.0,
                   min_mcap_cr: float = 1000.0,
                   runner: Callable[[list[str], str], tuple[int, str, str]] = _default_runner
                   ) -> list[dict[str, Any]]:
    argv = [DEFAULT_PYTHON, DEFAULT_SCRIPT, "--direction", direction, "--top", str(top),
            "--min-price", str(min_price), "--min-mcap-cr", str(min_mcap_cr)]
    cwd = os.path.dirname(DEFAULT_SCRIPT)
    rc, out, err = runner(argv, cwd)
    if rc != 0:
        raise ScreenerError(f"screener exit {rc}: {err.strip()}")
    if not out or not out.strip():
        raise ScreenerError("screener returned empty output")
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        raise ScreenerError(f"could not parse screener JSON: {e}") from e
    if "picks" not in data:
        raise ScreenerError(f"screener output missing 'picks': {list(data)[:5]}")
    return data["picks"]
