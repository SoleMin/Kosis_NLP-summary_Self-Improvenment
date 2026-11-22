#!/usr/bin/env bash
# The improvement loop: critic -> attacker -> defender, one loop at a time. The work is scripts/loop_main.py.
#   bash scripts/loop.sh            run until a stop condition      Stop early: touch loop/STOP
#   bash scripts/loop.sh --once     run one loop
#   bash scripts/loop.sh --check    check the settings, run nothing
#   bash scripts/loop.sh --restore  put back what a stopped run left in the vault
set -uo pipefail
cd "$(dirname "$0")/.."
set -a
source <(tr -d '\r' < loop/config.env)   # CRLF line endings would end up in the values
set +a
# The bash that runs this script, for the commands loop_main.py starts (on Windows a bare "bash" can be WSL's).
if command -v cygpath >/dev/null 2>&1; then LOOP_BASH="$(cygpath -w "$BASH")"; else LOOP_BASH="$BASH"; fi
export LOOP_BASH PYTHONUTF8=1
# The Python of the conda environment (CONDA_ENV, default kosis), first on PATH for the agents too.
source scripts/use_python.sh
use_python || { echo "시작할 수 없음" >&2; exit 1; }
echo "파이썬: $PYTHON" >&2
exec "$PYTHON" scripts/loop_main.py "$@"
