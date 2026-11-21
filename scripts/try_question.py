"""Run the number path on one question with the rules alone (for the attacker). No model is called.

    python scripts/try_question.py "2023년 부산 출생아 수 알려줘" 101_DT_1B8000H
    python scripts/try_question.py "<질문>" <표 키> <표 키>          # two or three tables
    python scripts/try_question.py "<질문>" <표 키> --all            # every computed fact, not only the answer's

It calls kosis_chat.calc.calculate(question, tables) of the Phase 1 clone with no model and no key, which is how
the loop measures, and prints what came back:

    status: ok | no_plan | refused | error
    reason / detail when there is no plan, the calculation was refused, or the code raised
    on "ok": the facts the answer uses (id = value unit, period, the cell's coordinate or the formula)

Use it to see whether a question is already solved before writing it as an attack. A value printed here is the
code's output, never an answer to write down: an attack spec holds coordinates and formulas, no numbers.

The tables are read from <workspace>/data (--workspace, else the KOSIS_WORKSPACE environment variable). The code is
--phase1, else PHASE1_DIR, else kosis-ai-nlp-summary-T1-30 next to this folder. This script writes nothing; Phase 1
itself saves its scan of a big table under <workspace>/data/index the first time it reads one.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_TABLES = 3


def fact_line(fact) -> str:
    value = (fact.raw or "(no value)") if fact.value is None else f"{fact.value:,}"
    parts = [f"{fact.id} = {value}{' ' + fact.unit if fact.unit else ''}"]
    if fact.period:
        parts.append(fact.period)
    parts.append(fact.source_ref or fact.derivation or fact.note or "")
    return "  " + "   ".join(p for p in parts if p)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="run the number path on one question, rules only")
    p.add_argument("question")
    p.add_argument("table_keys", nargs="+", metavar="KEY", help="one to three tables.csv keys")
    p.add_argument("--all", action="store_true", help="list every computed fact")
    p.add_argument("--workspace", type=Path, default=None, help="folder that holds data/ (else KOSIS_WORKSPACE)")
    p.add_argument("--phase1", type=Path, default=None, help="the Phase 1 clone (else PHASE1_DIR)")
    args = p.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if len(args.table_keys) > MAX_TABLES:
        print(f"give one to {MAX_TABLES} table keys", file=sys.stderr)
        return 2
    if args.workspace is not None:
        os.environ["KOSIS_WORKSPACE"] = str(args.workspace.resolve())
    workspace = os.environ.get("KOSIS_WORKSPACE", "").strip()
    if not workspace or not (Path(workspace) / "data" / "tables.csv").is_file():
        print("give --workspace (or set KOSIS_WORKSPACE): the folder whose data/ holds tables.csv", file=sys.stderr)
        return 2
    phase1 = args.phase1 or ROOT / (os.environ.get("PHASE1_DIR", "").strip() or "kosis-ai-nlp-summary-T1-30")
    phase1 = phase1.resolve()
    if not (phase1 / "kosis_chat" / "calc.py").is_file():
        print(f"Phase 1 clone with kosis_chat/calc.py not found: {phase1}", file=sys.stderr)
        return 2

    # The clone must be the code that runs, not another Phase 1 installed in the environment.
    sys.path[:0] = [str(phase1), str(phase1 / "src")]
    import kosis_ai
    import kosis_chat
    from kosis_chat import calc

    for module in (kosis_chat, kosis_ai):
        if phase1 not in Path(module.__file__).resolve().parents:
            print(f"{module.__name__} was imported from {module.__file__}, not from {phase1}", file=sys.stderr)
            return 2

    try:
        tables = calc.load_tables(args.table_keys)
    except LookupError as exc:
        print(f"{exc} (find a key with scripts/show_table.py --find)", file=sys.stderr)
        return 2
    try:
        result = calc.calculate(args.question, tables)  # no llm=, no client=: the rules alone
    except Exception as exc:  # a crash is a finding too: the loop's measurement counts it as an error
        print("status: error")
        print(f"detail: {type(exc).__name__}: {str(exc)[:500]}")
        return 0

    print(f"status: {result.status}")
    if result.status != "ok":
        print(f"reason: {result.reason}")
        for line in result.detail:
            print(f"detail: {line}")
        return 0
    facts = {f.id: f for f in result.facts}
    print("answer (the facts the code chose for the answer):")
    for fact_id in result.answer:
        print(fact_line(facts[fact_id]) if fact_id in facts else f"  {fact_id}")
    others = [f for f in result.facts if f.id not in set(result.answer)]
    if args.all and others:
        print("other computed facts:")
        for fact in others:
            print(fact_line(fact))
    elif others:
        print(f"other computed facts: {len(others)} (--all lists them)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
