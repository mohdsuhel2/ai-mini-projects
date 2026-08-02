#!/bin/zsh
# zsh does not word-split unquoted parameters, so pass sym/day as separate args.
PY=/Users/mohdsuhel/ai-mini-projects/autoIntraday/.venv/bin/python
run() {
  echo "########## $1 $2 ##########"
  BT_SYM=$1 BT_DAY=$2 $PY dayreplay.py
}
run HINDUNILVR 2026-07-28
run KALYANKJIL 2026-07-31
run INFY       2026-07-30
