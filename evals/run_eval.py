"""Run Phase 1's number path on an item file and grade it in one command. The last stdout line is one number in 0..1.

    python evals/run_eval.py --workspace <folder that holds data/>                  # rules only (no model, no key)
    python evals/run_eval.py --backend hcx --interval 1 --workspace <...> --env-file <.env with HCX_API_KEY>
    python evals/run_eval.py --backend hcx --workspace <...> --quiet                # for EVAL_CMD: the number only

Arguments this file does not know are passed on to run_calc.py (--items, --phase1, --out, --interval, --limit,
--cache ...). --quiet, --chart-weight, --cell-match, --only-present, --weight-codes and --weight-type go to grade.py.

--legacy runs the whole pipeline instead (run_phase1.py: judge, model-written plans, sentence, chart) and grades it
the 2026-11-19 way (every fact counts, no weight). Its --backend is mock or hcx.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import grade  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="run Phase 1 and grade", allow_abbrev=False)
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--chart-weight", type=float, default=0.0)
    p.add_argument("--cell-match", choices=("ref", "value"), default="ref")
    p.add_argument("--only-present", action="store_true")
    p.add_argument("--weight-codes", type=float, default=None)
    p.add_argument("--weight-type", type=float, default=None)
    p.add_argument("--legacy", action="store_true")
    args, rest = p.parse_known_args(argv)

    if args.legacy:
        import run_phase1 as runner
    else:
        import run_calc as runner

    captured = io.StringIO()  # the runner prints the run folder on stdout; keep stdout's last line for the number
    with contextlib.redirect_stdout(captured):
        code = runner.main(rest)
    if code != 0:
        print("0.0")
        return code
    run_dir = captured.getvalue().strip().splitlines()[-1]
    grade_args = ["--run", run_dir, "--chart-weight", str(args.chart_weight), "--cell-match", args.cell_match]
    for flag, value in (("--weight-codes", args.weight_codes), ("--weight-type", args.weight_type)):
        if value is not None:
            grade_args += [flag, str(value)]
    if args.legacy:
        grade_args.append("--legacy")
    if args.quiet:
        grade_args.append("--quiet")
    if args.only_present or any(flag in rest for flag in ("--limit", "--only", "--split")):
        grade_args.append("--only-present")
    return grade.main(grade_args)


if __name__ == "__main__":
    raise SystemExit(main())
