"""Check an item file against the tables it was made from. Phase 1 is not imported; pyarrow and pandas are needed.

    python evals/check_items.py --workspace <folder that holds data/>

For every item:
- each table file exists and has the sha256 the item recorded (a different file is reported, the cells are still checked);
- each cell fact (source_ref) is exactly one row of its table, with value_kind 'ok' and the same value;
- each derived fact is its derivation computed from value_raw with 50-digit decimals and rounded to its digit,
  and the exact result is not on a rounding tie;
- the grader gives the item's own facts a full score and an empty answer zero.

Nothing of the items is printed except ids. Exit code 0 when every check passes.
Only the rows of the items' cells are read from each table (see load_table), so big tables are fine.
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
sys.path.append(str(HERE.parent / "scripts"))  # tablescan.py, shared with scripts/show_table.py
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


def load_table(path: Path, refs=None):
    """source_ref -> list of (value, value_raw, value_kind) for one Parquet file, as pandas reads them.

    With refs, only those coordinates are looked for, a row group at a time so memory stays small on any table:
    row groups whose periods cannot match are skipped (footer statistics), the coordinate columns of the others are
    read as dictionaries and narrowed to rows whose every part could be in a wanted coordinate, the exact coordinate
    is built for those, and value columns are read only for rows that are wanted. Without refs, every row (slow on
    big tables)."""
    import numpy as np
    import pyarrow as pa
    import pyarrow.compute as pc
    from tablescan import TableFile, ref_column

    tf = TableFile(path)
    codes = [f"dim{i}_code" for i in range(1, 9) if f"dim{i}_code" in tf.schema.names]
    wanted = parts = None
    groups = tf.row_groups()
    if refs is not None:
        # a text that is not valid UTF-8 (a lone surrogate) is no row's coordinate, and arrow cannot hold it
        wanted = pa.array(sorted({r for r in refs if isinstance(r, str) and utf8(r)}), pa.string())
        parts = ref_parts(wanted.to_pylist())
        # an OR of equalities: arrow does not use the statistics for an is_in of more than about 50 values; it is
        # pruned a chunk of terms at a time, since one long OR overflows the C stack (thousands of refs)
        groups = tf.row_groups_any([tf.equals("period", p) for p in sorted(parts["period"])])
    rows = defaultdict(list)
    for group in groups:
        keys = tf.read(group, ["tbl_id", "item_id", *codes, "period"])
        at = pa.array(np.arange(keys.num_rows))
        if parts is not None:
            at = pa.array(may_match(keys, parts, codes, len(wanted)))
            keys = keys.take(at)
        ref = ref_column(tf, keys)
        keep = pc.is_in(ref, value_set=wanted) if wanted is not None else pc.is_valid(ref)
        if not pc.any(keep).as_py():
            continue
        df = tf.to_pandas(tf.read(group, ["value", "value_raw", "value_kind"]).take(at.filter(keep)))
        for r, v, raw, kind in zip(ref.filter(keep).to_pylist(), df["value"], df["value_raw"], df["value_kind"]):
            rows[r].append((v, raw, kind))
    return rows


def ref_parts(refs: list[str]) -> dict[str, dict[str, int]]:
    """For each column, text -> bit set (an int) of the refs whose coordinate could hold that text there. It does not
    assume that no code holds a "|": tbl_id is some text before a "|", period some text after one, item_id and each
    code some text between two."""
    from itertools import combinations

    parts = {"tbl_id": {}, "item_id": {}, "period": {}, "code": {}}
    for n, ref in enumerate(refs):
        cuts = [i for i, ch in enumerate(ref) if ch == "|"]
        if len(cuts) < 2:  # a row's coordinate has at least tbl_id|item_id|period
            continue
        middles = {ref[i + 1:j] for i, j in combinations(cuts, 2)}
        for name, texts in (("tbl_id", {ref[:i] for i in cuts}), ("period", {ref[i + 1:] for i in cuts}),
                            ("item_id", middles), ("code", middles)):
            for text in texts:
                parts[name][text] = parts[name].get(text, 0) | 1 << n
    return parts


def may_match(keys, parts: dict[str, dict[str, int]], codes: list[str], n_refs: int):
    """Positions of the rows that could have one of the refs' coordinates: for some ref, every text column holds a
    text that ref allows (a missing code is allowed: it is left out of the coordinate). Worked out with bit sets
    (64 refs at a time) on each column's dictionary, period first and then only on the rows still left, so it is
    cheap and few rows are left for the exact check. Other column types are left to that check."""
    import numpy as np
    import pyarrow as pa

    columns = []
    for name in sorted(keys.column_names, key=lambda c: c != "period"):
        col = keys[name].combine_chunks()
        if pa.types.is_dictionary(col.type) and pa.types.is_string(col.type.value_type):
            texts = col.dictionary.to_pylist()
            index = col.indices.fill_null(len(texts)).to_numpy()  # the last entry stands for a missing value
            columns.append((parts["code" if name in codes else name], texts, index, name in codes))
    found = [np.arange(keys.num_rows)[:0]]
    for word in range(0, max(n_refs, 1), 64):
        rows, alive = np.arange(keys.num_rows), None
        for allowed, texts, index, may_miss in columns:
            bits = np.array([(allowed.get(t, 0) >> word) & (2**64 - 1) for t in texts] + [2**64 - 1 if may_miss else 0],
                            np.uint64)
            mine = bits[index[rows]]
            alive = mine if alive is None else alive & mine
            left = alive != 0
            rows, alive = rows[left], alive[left]
        found.append(rows)
    return np.unique(np.concatenate(found))  # in file order


def utf8(text: str) -> bool:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:  # in pieces: a table file can be large
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


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

    wanted = defaultdict(set)  # table key -> every source_ref an item of that table looks up
    for item in items:
        refs = {f["source_ref"] for f in item["required_facts"] + item["aux_facts"] if f.get("source_ref")}
        for key in item["table_keys"]:
            wanted[key] |= refs

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
            if sha256(path) != digest:
                changed_tables.add(key)
            tables[key] = load_table(path, wanted[key])
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
