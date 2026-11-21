"""Turn the attacker's specs (question, cell coordinates, formulas: no numbers) into graded items. pandas, pyarrow.

    python evals/build_items.py --spec loop/attack/spec_3.jsonl --out evals/golden_3.jsonl --workspace <folder that holds data/>

A spec line:
    {"id": "L3-001", "question": "...", "table_keys": ["101_DT_1B8000H"], "type": "trend", "subtype": "trend_rate",
     "cells": {"a": "DT_1B8000H|T10|31|2019", "b": "DT_1B8000H|T10|31|2023"},
     "steps": [{"id": "d", "formula": "b - a", "round": 2}],
     "answers": ["d", {"id": "r", "formula": "(b / a - 1) * 100", "round": 2}],
     "weakness": "what the question tries"}
cells: name -> a cell's coordinate (scripts/show_table.py prints them). steps (optional): values computed on the way,
not the answer. answers: what the question asks: a name from cells or steps, or a new {"id", "formula", "round"}.
A formula uses names, numbers, + - * / ** and parentheses.

The numbers are read from the tables here, never taken from the spec: a cell's value is the table's, a formula is
computed exactly from value_raw and rounded. A spec is rejected (and reported with its reason) when:
a coordinate is not exactly one row of the item's tables or is not a number ('ok') cell; a formula cannot be
computed or its result lies on a rounding tie; a table of table_keys has no cell in the spec (the question does not
need it); the question is already in --known files (the baseline and earlier golden files); a field is missing.

Output: --out, items in the baseline format (evals/BASELINE.md) and, next to it, <out>.report.json: per spec id
"built" or the reason it was rejected. The built file is then checked with check_items.py. Nothing of the items is
printed except ids and reasons. Exit code 0 when at least one item was built and the check passed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import check_items  # noqa: E402
import grade  # noqa: E402

TYPES = ("value", "trend", "compare", "share", "extreme")


class Rejected(Exception):
    pass


def _formula(entry, where: str) -> dict:
    if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not isinstance(entry.get("formula"), str):
        raise Rejected(f"{where}: needs id and formula")
    digits = entry.get("round", 2)
    if not isinstance(digits, int) or isinstance(digits, bool) or not 0 <= digits <= 6:
        raise Rejected(f"{where} {entry['id']}: round is a whole number from 0 to 6")
    return {"id": entry["id"], "formula": entry["formula"], "round": digits}


def build(spec: dict, tables: dict[str, dict], shas: dict[str, str], known: set[str]) -> dict:
    for name in ("id", "question", "table_keys", "cells", "answers"):
        if not spec.get(name):
            raise Rejected(f"missing field: {name}")
    question = " ".join(str(spec["question"]).split())
    if question in known:
        raise Rejected("the question is already used")
    keys = list(spec["table_keys"])
    if not 1 <= len(keys) <= 3 or len(set(keys)) != len(keys) or any(k not in tables for k in keys):
        raise Rejected("table_keys: one to three different tables of the workspace")
    if spec.get("type") not in TYPES:
        raise Rejected(f"type is one of {', '.join(TYPES)}")
    cells = spec["cells"]
    if not isinstance(cells, dict) or not all(isinstance(k, str) and k.isidentifier() and isinstance(v, str) for k, v in cells.items()):
        raise Rejected("cells: name -> coordinate, names like a, b, seoul_2020")

    values: dict[str, Decimal] = {}
    facts: dict[str, dict] = {}
    used: set[str] = set()
    for name, ref in cells.items():
        rows = [(key, row) for key in keys for row in tables[key].get(ref, [])]
        if len(rows) != 1:
            raise Rejected(f"cell {name}: the coordinate matches {len(rows)} rows of the tables")
        key, (value, raw, kind) = rows[0]
        if kind != "ok":
            raise Rejected(f"cell {name}: not a number cell")
        try:
            values[name] = Decimal(str(raw).strip().replace(",", ""))
        except InvalidOperation:
            raise Rejected(f"cell {name}: the table's value cannot be read") from None
        if float(values[name]) != value:
            raise Rejected(f"cell {name}: value_raw and value disagree in the table")
        number = float(value)
        facts[name] = {"id": name, "value": int(number) if number.is_integer() else number, "source_ref": ref}
        used.add(key)
    if used != set(keys):
        raise Rejected("a table of table_keys has no cell here: the question does not need it")
    if len(set(cells.values())) != len(cells):
        raise Rejected("two cell names have the same coordinate")

    steps = [_formula(e, "steps") for e in spec.get("steps") or []]
    answers: list[str] = []
    for entry in spec["answers"]:
        if isinstance(entry, str):
            answers.append(entry)
        else:
            steps.append(_formula(entry, "answers"))
            answers.append(entry["id"])
    for step in steps:
        name = step["id"]
        if not name.isidentifier() or name in facts:
            raise Rejected(f"formula {name}: the name is taken or not a plain name")
        refs = grade.names(step["formula"])
        if not refs or any(r not in values for r in refs):
            raise Rejected(f"formula {name}: uses a name that is not defined before it")
        try:
            x = check_items.exact(step["formula"], values)
        except (ValueError, ZeroDivisionError, InvalidOperation, SyntaxError, ArithmeticError) as exc:
            raise Rejected(f"formula {name}: cannot be computed ({type(exc).__name__})") from None
        digit = Decimal(1).scaleb(-step["round"])
        scaled = x / digit
        if abs(abs(scaled - scaled.to_integral_value(rounding=ROUND_HALF_EVEN)) - Decimal("0.5")) < check_items.TIE_MARGIN:
            raise Rejected(f"formula {name}: the result is on a rounding tie")
        values[name] = x
        facts[name] = {"id": name, "value": float(x.quantize(digit, rounding=ROUND_HALF_EVEN)),
                       "derivation": step["formula"], "round": step["round"]}
    if len(set(answers)) != len(answers) or any(a not in facts for a in answers):
        raise Rejected("answers: each is a different name from cells, steps or a new formula")
    needed = set(answers)
    for name in reversed(list(facts)):  # everything defined must lead to an answer
        if name in needed and facts[name].get("derivation"):
            needed |= set(grade.names(facts[name]["derivation"]))
    if needed != set(facts):
        raise Rejected(f"not used by any answer: {', '.join(sorted(set(facts) - needed))}")

    return {
        "id": str(spec["id"]), "kind": "answerable", "type": spec["type"], "subtype": spec.get("subtype") or spec["type"],
        "question": question, "table": f"data/values/{keys[0]}.parquet", "table_sha256": shas[keys[0]],
        "table_keys": keys, "tables_sha256": [shas[k] for k in keys],
        "required_facts": [facts[a] for a in answers],
        "aux_facts": [f for name, f in facts.items() if name not in answers],
        "chart_points": None, "chart_expected": None, "notes": str(spec.get("weakness") or ""),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="fill the attacker's specs with the tables' numbers")
    p.add_argument("--spec", type=Path, required=True, help="the attacker's spec file (jsonl)")
    p.add_argument("--out", type=Path, required=True, help="the item file to write, e.g. evals/golden_3.jsonl")
    p.add_argument("--workspace", type=Path, default=None, help="folder that holds data/ (else KOSIS_WORKSPACE)")
    p.add_argument("--known", type=Path, nargs="*", default=None,
                   help="item files whose questions may not be repeated (default: the baseline and evals/golden_*.jsonl)")
    args = p.parse_args(argv)
    workspace = args.workspace or Path(os.environ.get("KOSIS_WORKSPACE", ""))
    values_dir = workspace / "data" / "values"
    if not values_dir.is_dir():
        print("give --workspace (or set KOSIS_WORKSPACE): the folder whose data/ holds values/", file=sys.stderr)
        return 2
    known_files = args.known if args.known is not None else [HERE / "baseline" / "items.jsonl", *sorted(HERE.glob("golden_*.jsonl"))]
    known = {" ".join(i["question"].split()) for f in known_files if f.is_file() and f.resolve() != args.out.resolve()
             for i in grade.load_jsonl(f)}

    report: dict[str, str] = {}
    specs = []
    for n, line in enumerate(args.spec.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            spec = json.loads(line)
            if not isinstance(spec, dict):
                raise ValueError
            specs.append(spec)
        except ValueError:
            report[f"line {n}"] = "not a JSON object"
    wanted = defaultdict(set)  # table key -> every coordinate a spec of that table names
    for spec in specs:
        cells = spec.get("cells")
        refs = {v for v in cells.values() if isinstance(v, str)} if isinstance(cells, dict) else set()
        for key in spec.get("table_keys") or []:
            if isinstance(key, str):
                wanted[key] |= refs
    tables: dict[str, dict] = {}
    shas: dict[str, str] = {}
    for spec in specs:
        for key in spec.get("table_keys") or []:
            path = values_dir / f"{key}.parquet" if isinstance(key, str) else None
            if key not in tables and path is not None and path.is_file():
                tables[key] = check_items.load_table(path, wanted[key])  # only the rows the specs name
                shas[key] = check_items.sha256(path)

    items = []
    seen_ids: set[str] = set()
    for spec in specs:
        sid = str(spec.get("id") or f"(no id {len(report)})")
        try:
            if sid in seen_ids:
                raise Rejected("the id is used twice")
            item = build(spec, tables, shas, known)
        except Rejected as exc:
            report[sid] = f"rejected: {exc}"
            continue
        seen_ids.add(sid)
        known.add(item["question"])
        items.append(item)
        report[sid] = "built"

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    report_path = args.out.with_suffix(".report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(items)} built, {len(report) - len(items)} rejected -> {args.out} (reasons: {report_path})")
    for sid, why in report.items():
        if why != "built":
            print(f"  {sid}: {why}")
    if not items:
        return 1
    return check_items.main(["--items", str(args.out), "--workspace", str(workspace)])


if __name__ == "__main__":
    raise SystemExit(main())
