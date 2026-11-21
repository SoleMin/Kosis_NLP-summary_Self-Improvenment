#!/usr/bin/env bash
# Pass/fail gate for a defender's change. Exit 0 = pass.
# First the shared schema check (scripts/contract_check.py), then VERIFY_CMD in loop/config.env when set, else the
# default (import check, import rules, the defender's tests in tests/loop).
set -euo pipefail
cd "$(dirname "$0")/.."
set -a
source <(tr -d '\r' < loop/config.env)   # CRLF line endings would end up in the values
set +a
if command -v cygpath >/dev/null 2>&1; then LOOP_BASH="$(cygpath -w "$BASH")"; else LOOP_BASH="$BASH"; fi
export LOOP_BASH PYTHONUTF8=1
exec "${PYTHON:-python}" scripts/loop_main.py --verify
