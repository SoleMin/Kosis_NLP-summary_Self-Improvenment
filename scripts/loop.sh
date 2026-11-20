#!/usr/bin/env bash
# Outer loop: one agent iteration at a time -> verify -> score -> keep (commit) or revert.
# Usage: bash scripts/loop.sh        Stop early: touch loop/STOP
set -uo pipefail
cd "$(dirname "$0")/.."
source loop/config.env

git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || { echo "Not a git repo. Run: git init && git add -A && git commit -m init"; exit 1; }
[ -z "$(git status --porcelain)" ] \
  || { echo "Working tree is not clean. Commit or stash first."; exit 1; }
mkdir -p loop/runs

# Resume from the last recorded iteration and best score.
start=$(awk -F'\t' 'NR>1 {n=$1} END {print n+0}' loop/results.tsv)
best=$(awk -F'\t' 'NR>1 && $6!="-" {b=$6} END {print (b=="" ? "-" : b)}' loop/results.tsv)
no_improve=0
verify_fails=0
stop_reason=""

run_agent() {
  local prompt
  prompt="$(cat loop/prompt.md)"
  case "$HARNESS" in
    claude) claude -p "$prompt" --permission-mode acceptEdits --max-turns "$MAX_TURNS" ;;
    codex)  codex exec --full-auto "$prompt" ;;
    *)      echo "Unknown HARNESS=$HARNESS" >&2; return 1 ;;
  esac
}

gt() { awk -v a="$1" -v b="$2" 'BEGIN {exit !(a > b)}'; }
eq() { awk -v a="$1" -v b="$2" 'BEGIN {exit !(a == b)}'; }

commit_all() {  # prints the new short hash, or "-" when nothing changed
  git add -A
  if git diff --cached --quiet; then echo "-"; return; fi
  git commit -qm "$1" && git rev-parse --short HEAD
}

record() {  # iter task verify score kept commit
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$1" "$(date '+%Y-%m-%d %H:%M')" "$2" "$3" "$4" "$best" "$5" "$6" >> loop/results.tsv
  commit_all "iter $1: record" > /dev/null
}

for ((n = 1; n <= MAX_ITER; n++)); do
  iter=$((start + n))
  [ -f loop/STOP ] && { stop_reason="loop/STOP exists"; break; }
  [ -s loop/blocked.md ] && { stop_reason="blocked (see loop/blocked.md)"; break; }
  head -n1 docs/current-state.md | grep -q ALL_DONE && { stop_reason="ALL_DONE"; break; }

  log="loop/runs/iter-$iter.log"
  echo "=== iter $iter ($(date '+%H:%M')) -> $log"
  rm -f loop/.current_task
  run_agent > "$log" 2>&1
  rc=$?
  if [ "$rc" -eq 127 ]; then
    stop_reason="harness '$HARNESS' not found on PATH (see $log)"
    git reset -q --hard HEAD && git clean -qfd
    break
  fi
  [ "$rc" -ne 0 ] && echo "(agent exited with $rc)" >> "$log"
  task=$(head -n1 loop/.current_task 2>/dev/null | tr -d '\r')
  task=${task:--}

  # Agent asked for a human: keep its notes and stop.
  if [ -s loop/blocked.md ]; then
    record "$iter" "$task" "-" "-" "blocked" "$(commit_all "iter $iter: $task blocked")"
    stop_reason="blocked (see loop/blocked.md)"
    break
  fi

  if bash scripts/verify.sh >> "$log" 2>&1; then v=pass; verify_fails=0
  else v=fail; verify_fails=$((verify_fails + 1)); fi

  score="-"; kept=0
  if [ "$v" = pass ]; then
    if [ -z "$EVAL_CMD" ]; then
      kept=1
    else
      score=$(bash scripts/eval.sh 2>>"$log" | tail -n1 | tr -d '\r')
      if ! [[ "$score" =~ ^-?[0-9]+([.][0-9]+)?$ ]]; then
        score="err"; no_improve=$((no_improve + 1))
      elif [ "$best" = "-" ] || gt "$score" "$best"; then
        kept=1; best=$score; no_improve=0
      elif eq "$score" "$best"; then
        kept=1; no_improve=$((no_improve + 1))   # same score: keep the work, but no progress on the metric
      else
        no_improve=$((no_improve + 1))
      fi
    fi
  fi

  if [ "$kept" = 1 ] || [ "$ROLLBACK" != 1 ]; then
    commit=$(commit_all "iter $iter: $task (verify=$v score=$score)")
  else
    git reset -q --hard HEAD && git clean -qfd
    commit="reverted"
    printf '| %s | %s | verify=%s, score=%s (best %s) | 루프가 되돌림. %s 참고 |\n' \
      "$iter" "$task" "$v" "$score" "$best" "$log" >> loop/learnings.md
  fi
  record "$iter" "$task" "$v" "$score" "$kept" "$commit"
  echo "    task=$task verify=$v score=$score best=$best kept=$kept"

  [ "$verify_fails" -ge "$MAX_VERIFY_FAILS" ] && { stop_reason="verify failed $verify_fails times in a row"; break; }
  [ -n "$EVAL_CMD" ] && [ "$no_improve" -ge "$PATIENCE" ] && { stop_reason="no better score for $PATIENCE iterations"; break; }
done

echo "Stopped: ${stop_reason:-MAX_ITER ($MAX_ITER) reached}"
