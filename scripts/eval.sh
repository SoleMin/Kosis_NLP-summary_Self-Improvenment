#!/usr/bin/env bash
# Scores the current tree. The last stdout line must be one number (higher is better).
# Configure EVAL_CMD in loop/config.env.
set -euo pipefail
cd "$(dirname "$0")/.."
source loop/config.env
[ -n "$EVAL_CMD" ] || { echo "EVAL_CMD is empty in loop/config.env" >&2; exit 1; }
bash -c "$EVAL_CMD"
