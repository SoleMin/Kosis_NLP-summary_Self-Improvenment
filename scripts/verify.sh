#!/usr/bin/env bash
# Pass/fail gate for one iteration. Exit 0 = pass. Configure VERIFY_CMD in loop/config.env.
set -euo pipefail
cd "$(dirname "$0")/.."
source loop/config.env
[ -n "$VERIFY_CMD" ] || { echo "VERIFY_CMD is empty in loop/config.env" >&2; exit 1; }
bash -c "$VERIFY_CMD"
