"""Compare two run folders of run_calc.py item by item: did a code change alter any result?

    python evals/compare_runs.py evals/runs/s0-rules evals/runs/s1-rules

Reads <run>/outputs.jsonl of both. An item is the same when every field but the time (`seconds`) is equal.
`asked` (a hash of the question and its tables, written since 2026-11-21) is compared when both runs have it:
a difference there means the two runs did not answer the same question under that id.
Prints counts, the status table of each run and the ids of items that differ with the names of the fields that
differ. It does not print the questions' answers unless --show is given (the output of a run holds them: use --show
only where the defender cannot read).

A model reply that failed the format check is not kept in the cache, so that item asks the model again on every run
and may end differently with the same code. Such items (the earlier run has a model status other than "ok" and the
model's status is not the same in the later run) are listed apart as "model asked again": the model answered
differently, which does not show a code change.

Exit 0: no item differs but those the model was asked again for, and both runs hold the same items · 1: something
else differs · 2: a file is missing.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

IGNORED = ("seconds",)


def load(run: Path) -> dict[str, dict]:
    """id -> record; a run that was continued may hold an id twice, the last line counts."""
    out: dict[str, dict] = {}
    with (run / "outputs.jsonl").open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                record = json.loads(line)
                out[record["id"]] = record
    return out


def differing(a: dict, b: dict) -> list[str]:
    return sorted(k for k in a.keys() | b.keys() if k not in IGNORED and a.get(k) != b.get(k)
                  and not (k == "asked" and (k not in a or k not in b)))  # a run from before the field was written


def asked_again(before: dict, after: dict) -> bool:
    """The earlier reply was not cached (its status is not "ok") and the model's status came out differently."""
    return (any(status != "ok" for status in before.get("llm_status") or [])
            and before.get("llm_status") != after.get("llm_status"))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("before", type=Path)
    p.add_argument("after", type=Path)
    p.add_argument("--show", action="store_true", help="print the differing fields' contents (holds answers)")
    p.add_argument("--max", type=int, default=30, help="how many differing items to list")
    args = p.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    for run in (args.before, args.after):
        if not (run / "outputs.jsonl").is_file():
            print(f"outputs.jsonl not found in {run}", file=sys.stderr)
            return 2
    before, after = load(args.before), load(args.after)
    both = sorted(before.keys() & after.keys())
    only_before, only_after = sorted(before.keys() - after.keys()), sorted(after.keys() - before.keys())
    changed = [(i, differing(before[i], after[i])) for i in both]
    changed = [(i, fields) for i, fields in changed if fields]

    print(f"before: {len(before)} items  {dict(Counter(r.get('status') for r in before.values()))}")
    print(f"after:  {len(after)} items  {dict(Counter(r.get('status') for r in after.values()))}")
    seconds = [sum(r.get("seconds") or 0 for r in run.values()) for run in (before, after)]
    print(f"time:   {seconds[0]:.1f}s -> {seconds[1]:.1f}s")
    again = [(i, fields) for i, fields in changed if asked_again(before[i], after[i])]
    changed = [(i, fields) for i, fields in changed if not asked_again(before[i], after[i])]
    print(f"same: {len(both) - len(changed) - len(again)}  different: {len(changed)}  model asked again: {len(again)}  "
          f"only in before: {len(only_before)}  only in after: {len(only_after)}")
    for i, fields in again[: args.max]:
        print(f"~ {i} (model asked again): {', '.join(fields)}")
    for name, ids in (("only in before", only_before), ("only in after", only_after)):
        if ids:
            print(f"{name}: {', '.join(ids[:args.max])}" + (" ..." if len(ids) > args.max else ""))
    for i, fields in changed[: args.max]:
        print(f"- {i}: {', '.join(fields)}")
        if args.show:
            for k in fields:
                print(f"    {k}: {json.dumps(before[i].get(k), ensure_ascii=False)[:300]}")
                print(f"    {' ' * len(k)}  {json.dumps(after[i].get(k), ensure_ascii=False)[:300]}")
    if len(changed) > args.max:
        print(f"... {len(changed) - args.max} more")
    return 1 if changed or only_before or only_after else 0


if __name__ == "__main__":
    raise SystemExit(main())
