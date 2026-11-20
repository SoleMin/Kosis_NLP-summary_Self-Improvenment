"""Check an item file against the tables it was made from. Phase 1 is not imported; pandas and pyarrow are needed.

    python evals/check_items.py --workspace <folder that holds data/>

For every item:
- each table file exists and has the sha256 the item recorded (a different file is reported, the cells are still checked);
- each cell fact (source_ref) is exactly one row of its table, with value_kind 'ok' and the same value;
- each derived fact is its derivation computed from value_raw with 50-digit decimals and rounded to its digit,
  and the exact result is not on a rounding tie;
- the grader gives the item's own facts a full score and an empty answer zero.

Nothing of the items is printed except ids. Exit code 0 when every check passes.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from decimal import ROUND_HALF_EVEN, Decimal, getcontext
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import grade  # noqa: E402

getcontext().prec = 50
TIE_MARGIN = Decimal("1e-6")  # of the last rounded digit


def exact(expr: str, values: dict[str, Decimal]) -> Decimal:
    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            return Decimal(str(node.value))
        if isinstance(node, ast.Name):
            return values[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return -ev(node.operand) if isinstance(node.op, ast.USub) else ev(node.operand)
        if isinstance(node, ast.BinOp):
            a, b = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Add):
                return a + b
            if isinstance(node.op, ast.Sub):
                return a - b
            if isinstance(node.op, ast.Mult):
                return a * b
            if isinstance(node.op, ast.Div):
                return a / b
            if isinstance(node.op, ast.Pow):
                return a ** b
        raise ValueError(f"not plain arithmetic: {expr!r}")
    return ev(ast.parse(expr, mode="eval"))


def load_table(path: Path):
    """source_ref -> list of (value, value_raw, value_kind) for one Parquet file."""
    import pyarrow.parquet as pq

    df = pq.read_table(path).to_pandas()
    ref = df["tbl_id"].astype(str) + "|" + df["item_id"].astype(str)
    for i in range(1, 9):
        col = f"dim{i}_code"
        if col in df:
            ref = ref + df[col].map(lambda c: "" if c is None or c != c else "|" + str(c))
    ref = ref + "|" + df["period"].astype(str)
    rows = defaultdict(list)
    for r, v, raw, kind in zip(ref, df["value"], df["value_raw"], df["value_kind"]):
        rows[r].append((v, raw, kind))
    return rows


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="check an item file against its tables")
    p.add_argument("--items", type=Path, default=HERE / "baseline" / "items.jsonl")
    p.add_argument("--workspace", type=Path, default=None, help="folder that holds data/ (else KOSIS_WORKSPACE)")
    args = p.parse_args(argv)
    workspace = args.workspace or Path(os.environ.get("KOSIS_WORKSPACE", ""))
    if not (workspace / "data" / "values").is_dir():
        print("give --workspace (or set KOSIS_WORKSPACE): the folder whose data/ holds values/", file=sys.stderr)
        return 2

    items = grade.load_jsonl(args.items)
    problems: list[str] = []
    changed_tables: set[str] = set()
    tables: dict[str, dict] = {}
    cells = derived = 0

    for name, n in Counter(i["id"] for i in items).items():
        if n > 1:
            problems.append(f"{name}: id used {n} times")
    for _, n in Counter(i["question"] for i in items).items():
        if n > 1:
            problems.append(f"a question is used {n} times")

    for item in items:
        iid = item["id"]
        for key, digest in zip(item["table_keys"], item["tables_sha256"]):
            if key in tables:
                continue
            path = workspace / "data" / "values" / f"{key}.parquet"
            if not path.exists():
                problems.append(f"{iid}: table file missing: {key}")
                tables[key] = {}
                continue
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                changed_tables.add(key)
            tables[key] = load_table(path)
        pool = defaultdict(list)
        for key in item["table_keys"]:
            for ref, rows in tables[key].items():
                pool[ref].extend(rows)

        facts = item["required_facts"] + item["aux_facts"]
        values: dict[str, Decimal] = {}
        for f in facts:
            if not f.get("source_ref"):
                continue
            cells += 1
            rows = pool.get(f["source_ref"], [])
            if len(rows) != 1:
                problems.append(f"{iid}/{f['id']}: source_ref matches {len(rows)} rows")
                continue
            value, raw, kind = rows[0]
            if kind != "ok" or value != f["value"]:
                problems.append(f"{iid}/{f['id']}: the table has another value (or it is not an 'ok' cell)")
                continue
            values[f["id"]] = Decimal(str(raw).strip().replace(",", ""))
            if float(values[f["id"]]) != f["value"]:
                problems.append(f"{iid}/{f['id']}: value_raw and value disagree")
        pending = [f for f in facts if not f.get("source_ref")]
        while pending:
            ready = [f for f in pending if all(n in values for n in grade.names(f.get("derivation") or ""))]
            if not ready:
                problems.extend(f"{iid}/{f['id']}: derivation cannot be computed" for f in pending)
                break
            for f in ready:
                pending.remove(f)
                derived += 1
                x = exact(f["derivation"], values)
                values[f["id"]] = x
                digit = Decimal(1).scaleb(-int(f["round"]))
                if float(x.quantize(digit, rounding=ROUND_HALF_EVEN)) != f["value"]:
                    problems.append(f"{iid}/{f['id']}: derived value differs from the exact result")
                elif abs(abs(x / digit - (x / digit).to_integral_value(rounding=ROUND_HALF_EVEN)) - Decimal("0.5")) < TIE_MARGIN:
                    problems.append(f"{iid}/{f['id']}: exact result is on a rounding tie")
                elif not grade.close(grade.evaluate(f["derivation"], {k: float(v) for k, v in values.items()}),
                                     f["value"], grade.tolerance(f)):
                    problems.append(f"{iid}/{f['id']}: float arithmetic leaves the tolerance")

        own = {"id": iid, "status": "ok", "origin": "rules", "facts": facts, "main": [f["id"] for f in item["required_facts"]],
               "chart": bool(item.get("chart_expected"))}
        if grade.grade_item(item, own)["score"] != 1.0 or grade.grade_item(item, own)["chart_ok"] is False:
            problems.append(f"{iid}: the grader does not accept the item's own facts")
        if grade.grade_item(item, {"id": iid, "status": "ok", "origin": "rules", "facts": [], "main": []})["score"] != 0.0:
            problems.append(f"{iid}: the grader scores an empty answer")

    print(f"{len(items)} items, {len(tables)} tables, {cells} cells, {derived} derived values checked")
    if changed_tables:
        print(f"{len(changed_tables)} table files differ from the ones the items were made from: {sorted(changed_tables)}")
    for line in problems[:50]:
        print("PROBLEM", line)
    if len(problems) > 50:
        print(f"... and {len(problems) - 50} more")
    print("OK" if not problems and not changed_tables else "NOT OK")
    return 0 if not problems and not changed_tables else 1


if __name__ == "__main__":
    raise SystemExit(main())
