"""The table tools read only what they need; their output must be what the old whole-table code printed and returned.

The old functions are copied below as the reference. Small Parquet files with tiny row groups stand in for big tables.
The tools treat a missing value the same on every pandas version: it matches no text (pandas 3 did that in the old
code). On pandas 2 the old code made the text "None" or "nan" of it; the reference adapts exactly those places when
PANDAS2 (marked "pandas 2:").

A coordinate is written as Phase 1 writes a cell's source_ref (2026-11-21: the old code left a missing code out and
wrote nothing for a table without categories, so such a cell could never be graded as right). The reference below
(old_ref_of) writes it row by row from that rule; test_refs_as_phase1 compares the tools with Phase 1's own
function when the clone is next to this folder.
"""

from __future__ import annotations

import contextlib
import io
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

import hashlib
import os
import pickle
import subprocess
import threading

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

TOP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOP / "scripts"))
sys.path.insert(0, str(TOP / "evals"))
import build_items  # noqa: E402
import check_items  # noqa: E402
import show_table  # noqa: E402
from tablescan import TableFile, ref_column  # noqa: E402

DIMS = range(1, 9)
NAMES = ["strings", "numbers", "floats", "pipes", "many", "one_group", "empty", "eight", "nostats", "two_labels",
         "nodim", "unnamed"]
PANDAS2 = int(pd.__version__.split(".")[0]) < 3


def no_null_parts(df, ref):
    """pandas 2: a coordinate with a missing tbl_id, item_id or period was text ("None|..", "..|nan"); now NaN."""
    if PANDAS2:
        ref = ref.where(~df[["tbl_id", "item_id", "period"]].isna().any(axis=1))
    return ref


# ---- the old code (reference): reads the whole table into pandas ----

def old_ref_of(df):
    """tbl_id|item_id|<codes>|period as Phase 1's cells_of writes it: the codes of the categories named on the row,
    "" for a named one without a code, joined by "|" (so one "" when no category is named)."""
    def codes(r) -> str:
        out = []
        for i in DIMS:
            name, code = r.get(f"dim{i}_name"), r.get(f"dim{i}_code")
            if name is None or name != name or name == "":
                continue
            out.append("" if code is None or code != code else str(code))
        return "|".join(out)

    middle = pd.Series([codes(r) for r in df.to_dict("records")], index=df.index, dtype=str)
    ref = df["tbl_id"].astype(str) + "|" + df["item_id"].astype(str) + "|" + middle + "|" + df["period"].astype(str)
    return no_null_parts(df, ref)


def old_show(row: dict, df, limit: int) -> None:
    print(f"{row['key']}  {row['table_name']}  ({row['survey_name']})  rows {len(df)}")
    items = df[["item_id", "item_name", "unit"]].drop_duplicates()
    print("[item]")
    for r in items.itertuples(index=False):
        print(f"  {r.item_id} = {r.item_name} ({r.unit})")
    for i in DIMS:
        name = f"dim{i}_name"
        if name not in df or df[name].dropna().empty:
            continue
        pairs = df[[f"dim{i}_code", f"dim{i}_label"]].dropna().drop_duplicates()
        print(f"[dim{i}] {df[name].dropna().iloc[0]}  ({len(pairs)} codes)")
        for r in pairs.head(limit).itertuples(index=False):
            print(f"  {r[0]} = {r[1]}")
        if len(pairs) > limit:
            print(f"  ... {len(pairs) - limit} more (--limit {len(pairs)})")
    period = df["period"].astype(str)
    if PANDAS2:  # a missing period was the text "None" / "nan"; now NaN as in pandas 3, which sorted() cannot order
        period = period.where(df["period"].notna())
    periods = sorted(period.unique())
    print(f"[period] {len(periods)}: {', '.join(periods[:6])}{' ... ' + ', '.join(periods[-6:]) if len(periods) > 12 else ', ' + ', '.join(periods[6:]) if len(periods) > 6 else ''}")
    kinds = df["value_kind"].value_counts().to_dict()
    print(f"[value_kind] {kinds}  (only 'ok' cells hold a number)")


def old_cells(df, conditions: list[str], limit: int) -> None:
    mask = None
    for cond in conditions:
        name, _, value = cond.partition("=")
        col = {"item": "item_id", "period": "period"}.get(name, f"{name}_code" if name.startswith("dim") else name)
        if col not in df:
            raise SystemExit(f"unknown condition {cond!r}: use item=, dim1= ... dim8=, period=")
        hit = df[col].astype(str) == value
        if PANDAS2:  # a missing value matched "None" / "nan"; now it matches nothing
            hit = hit & df[col].notna()
        mask = hit if mask is None else mask & hit
    found = df if mask is None else df[mask]
    refs = old_ref_of(found)
    print(f"{len(found)} cells match")
    for ref, r in list(zip(refs, found.itertuples(index=False)))[:limit]:
        labels = [str(getattr(r, f"dim{i}_label")) for i in DIMS if getattr(r, f"dim{i}_label", None) == getattr(r, f"dim{i}_label", None)
                  and getattr(r, f"dim{i}_label", None) is not None]
        print(f"  {ref}   {r.item_name} / {' / '.join(labels)} / {r.period} = {r.value_raw} {r.unit} [{r.value_kind}]")
    if len(found) > limit:
        print(f"  ... {len(found) - limit} more: add conditions")


def old_load_table(path: Path):
    df = pq.read_table(path).to_pandas()
    ref = old_ref_of(df)
    rows = defaultdict(list)
    for r, v, raw, kind in zip(ref, df["value"], df["value_raw"], df["value_kind"]):
        rows[r].append((v, raw, kind))
    return rows


# ---- test tables ----

def make_rows(rng: random.Random, period_type: str, n_items=3):
    """Every combination of item x dim1 x dim2 x dim3 x period, with some rows dropped, some repeated (a duplicated
    coordinate), symbols, and dim2 missing on part of the rows (that code is then left out of the coordinate)."""
    periods = [2019, 2020, 2021, 2022, 2023] if period_type != "string" else ["2019", "2020", "202101", "202102", "2022"]
    dim1 = [("00", "전국"), ("11", "서울"), ("21", "부산"), ("31", "경기")]
    dim2 = [("0", "계"), ("1", "남자"), ("2", "여자"), (None, None)]
    dim3 = [("A01", "15세 미만"), ("A02", "15-64세")]
    rows = []
    for p in periods:  # sorted by period, then dim1 code, like the real files
        for c1, l1 in dim1:
            for it in range(n_items):
                for c2, l2 in dim2:
                    for c3, l3 in dim3:
                        if rng.random() < 0.15:
                            continue
                        kind = "ok" if rng.random() < 0.85 else rng.choice(["symbol", "blank"])
                        value = round(rng.uniform(-500, 50000), rng.choice([0, 1, 2])) if kind == "ok" else None
                        raw = (f"{value:,}" if rng.random() < 0.2 else str(value)) if kind == "ok" else rng.choice(["-", "X", None])
                        row = {"tbl_id": "DT_TEST", "prd_se": "Y", "period": p, "item_id": f"T{it + 1}0",
                               "item_name": ["인구", "가구", "주택"][it % 3], "unit": ["명", None, "호"][it % 3],
                               "value_raw": raw, "value": value, "value_kind": kind,
                               "dim1_name": "시도별", "dim1_code": c1, "dim1_label": l1,
                               "dim2_name": "성별" if c2 is not None else None, "dim2_code": c2, "dim2_label": l2,
                               "dim3_name": "연령별", "dim3_code": c3, "dim3_label": l3}
                        rows.append(row)
                        if rng.random() < 0.03:  # the same coordinate twice
                            rows.append({**row, "value": 1.0, "value_raw": "1", "value_kind": "ok"})
    return rows


def write(path: Path, rows: list[dict], types: dict, row_group_size: int, statistics=True):
    schema = pa.schema([(c, types.get(c, pa.string())) for c in
                        ["tbl_id", "prd_se", "period", "item_id", "item_name", "unit", "value_raw", "value", "value_kind",
                         *(f"dim{i}_{p}" for i in DIMS for p in ("name", "code", "label"))]])
    cols = {f.name: [r.get(f.name) for r in rows] for f in schema}
    table = pa.table({name: pa.array(values, schema.field(name).type) for name, values in cols.items()}, schema=schema)
    pq.write_table(table, path, row_group_size=row_group_size, compression="zstd", use_dictionary=True,
                   write_statistics=statistics)


def variants(tmp: Path):
    rng = random.Random(7)
    out = {}
    rows = make_rows(rng, "string")
    write(tmp / "strings.parquet", rows, {"value": pa.float64()}, 37)
    out["strings"] = tmp / "strings.parquet"

    rows = make_rows(rng, "int")
    for r in rows:  # numeric dim1 codes; "00" becomes 0, and some are missing (pandas: float64, "11.0")
        r["dim1_code"] = None if r["dim1_code"] == "21" else int(r["dim1_code"])
    write(tmp / "numbers.parquet", rows, {"value": pa.float64(), "period": pa.int64(), "dim1_code": pa.int64()}, 23)
    out["numbers"] = tmp / "numbers.parquet"

    rows = make_rows(rng, "int", n_items=2)
    for r in rows:  # a float code and an integer period without nulls
        r["dim3_code"] = {"A01": 1.5, "A02": 2.0}[r["dim3_code"]]
    rows[5]["period"] = None  # a missing period: pandas makes the period float and that coordinate NaN
    write(tmp / "floats.parquet", rows, {"value": pa.float64(), "period": pa.int32(), "dim3_code": pa.float64()}, 50)
    out["floats"] = tmp / "floats.parquet"

    rows = make_rows(rng, "string", n_items=2)
    for r in rows:  # a "|" inside a code and inside tbl_id: the coordinate cannot simply be split on "|"
        r["tbl_id"] = "DT|P"
        r["dim3_code"] = {"A01": "A|01", "A02": "A02|"}[r["dim3_code"]]
    write(tmp / "pipes.parquet", rows, {"value": pa.float64()}, 41)
    out["pipes"] = tmp / "pipes.parquet"

    codes = [f"{rng.randrange(10, 99)}{n:03d}" for n in range(400)]  # many codes, not in sorted order
    rows = [{"tbl_id": "DT_MANY", "period": p, "item_id": it, "item_name": it, "unit": "명", "value": 1.0,
             "value_raw": "1", "value_kind": rng.choice(["ok", "ok", "x"]), "dim1_name": "지역", "dim1_code": c,
             "dim1_label": f"곳 {c}", "dim2_name": "성별", "dim2_code": rng.choice("012"), "dim2_label": "성"}
            for p in ["2023", "2024"] for c in codes for it in rng.sample(["T1", "T2", "T3", "T4"], 2)]
    write(tmp / "many.parquet", rows, {"value": pa.float64()}, 97)
    out["many"] = tmp / "many.parquet"

    write(tmp / "one_group.parquet", make_rows(rng, "string")[:200], {"value": pa.float64()}, 10_000)
    out["one_group"] = tmp / "one_group.parquet"
    write(tmp / "empty.parquet", [], {"value": pa.float64()}, 10)
    out["empty"] = tmp / "empty.parquet"

    rows = []  # eight categories, a "|" inside the period, item_name stored as a dictionary (pandas: categorical)
    for p in ["2020|H1", "2020|H2", "2021|H1"]:
        for n in range(48):
            row = {"tbl_id": "DT_EIGHT", "period": p, "item_id": f"T{n % 2}", "item_name": [None, "인구"][n % 2],
                   "unit": "명", "value": float(n), "value_raw": str(n), "value_kind": "ok"}
            for i in DIMS:
                code = None if i == 5 and n % 3 == 0 else "ab"[(n >> (i % 6)) & 1] + str(i)
                row.update({f"dim{i}_name": f"분류{i}", f"dim{i}_code": code, f"dim{i}_label": None if code is None else f"L{code}"})
            rows.append(row)
    rng.shuffle(rows)
    write(tmp / "eight.parquet", rows, {"value": pa.float64(), "item_name": pa.dictionary(pa.int32(), pa.string())}, 13)
    out["eight"] = tmp / "eight.parquet"

    write(tmp / "nostats.parquet", make_rows(rng, "string"), {"value": pa.float64()}, 29, statistics=False)
    out["nostats"] = tmp / "nostats.parquet"

    # code 11 has two labels, seen first as "B" and then as "A", with code 31 between: first-seen order is not sorted
    pairs = [("21", "부산"), ("11", "B"), ("31", "A"), ("11", "A"), ("21", "부산"), ("41", "B"), ("11", "B")]
    rows = [{"tbl_id": "DT_TWO", "period": "2023", "item_id": "T1", "item_name": "인구", "unit": "명", "value": 1.0,
             "value_raw": "1", "value_kind": "ok", "dim1_name": "시도", "dim1_code": c, "dim1_label": label}
            for c, label in pairs]
    write(tmp / "two_labels.parquet", rows, {"value": pa.float64()}, 3)
    out["two_labels"] = tmp / "two_labels.parquet"

    # a table without any category: Phase 1 writes tbl_id|item_id||period
    rows = [{"tbl_id": "DT_NODIM", "prd_se": "Y", "period": p, "item_id": it, "item_name": "인구", "unit": "명",
             "value": float(n), "value_raw": str(n), "value_kind": "ok"}
            for n, (p, it) in enumerate((p, it) for p in ["2021", "2022", "2023"] for it in ["T1", "T2"])]
    write(tmp / "nodim.parquet", rows, {"value": pa.float64()}, 4)
    out["nodim"] = tmp / "nodim.parquet"

    # a code under a category without a name is not part of the coordinate; a named category without a code is ""
    rows = [{"tbl_id": "DT_UNNAMED", "prd_se": "Y", "period": p, "item_id": "T1", "item_name": "인구", "unit": "명",
             "value": float(n), "value_raw": str(n), "value_kind": "ok", "dim1_name": "시도별", "dim1_code": c1,
             "dim1_label": "곳", "dim2_name": name2, "dim2_code": c2, "dim2_label": "성", "dim3_name": "연령별",
             "dim3_code": c3, "dim3_label": None if c3 is None else "나이"}
            for n, (p, c1, (name2, c2), c3) in enumerate(
                (p, c1, d2, c3) for p in ["2022", "2023"] for c1 in ["00", "11"]
                for d2 in [("성별", "1"), (None, "2"), ("", "3"), (None, None)] for c3 in ["A", None])]
    write(tmp / "unnamed.parquet", rows, {"value": pa.float64()}, 5)
    out["unnamed"] = tmp / "unnamed.parquet"
    return out


@pytest.fixture(scope="module")
def tables(tmp_path_factory):
    return variants(tmp_path_factory.mktemp("tables"))


def printed(fn, *args) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            fn(*args)
        except (SystemExit, Exception) as exc:  # the old code fails on some odd tables; the new must fail alike
            print(type(exc).__name__, exc)
    return buf.getvalue()


ROW = {"key": "000_DT_TEST", "table_name": "시험 표", "survey_name": "시험 조사"}


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("limit", [40, 3, 0, -1])
def test_show(tables, name, limit):
    df = pq.read_table(tables[name]).to_pandas()
    want = printed(old_show, ROW, df, limit)
    got = printed(show_table.show, ROW, TableFile(tables[name]), limit)
    assert got == want


CONDITIONS = [
    ["item=T10"], ["item=T10", "dim1=11", "period=2020"], ["item=T20", "dim1=11", "dim2=1", "dim3=A01"],
    ["dim2=None"], ["dim2=nan"], ["dim2="], ["period=2021"], ["period=2021.0"], ["period=202101"], ["dim1=11.0"],
    ["dim1=0"], ["dim1=00"], ["dim1=0.0"], ["dim3=1.5"], ["dim3=2.0"], ["dim3=2"], ["value_kind=symbol"],
    ["item=T10", "value_kind=ok", "dim1=31"], ["item=T99"], ["period=nan"], ["period=None"], ["value=1.0"],
    ["dim9=1"], ["unit=명"], ["period= 2021"], ["dim1=+11"], ["period=2020|H1"], ["period=H1"], ["dim8=b8", "dim5=a5"],
    ["dim4=a4", "item=T1"], ["dim1=11"],
]


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("limit", [40, 2, 0, -1])
def test_cells(tables, name, limit):
    df = pq.read_table(tables[name]).to_pandas()
    tf = TableFile(tables[name])
    for cond in CONDITIONS:
        assert printed(show_table.cells, tf, cond, limit) == printed(old_cells, df, cond, limit), cond


def same(a, b) -> bool:
    """Lists of (value, value_raw, value_kind) equal as values and Python types, NaN equal to NaN."""
    norm = lambda rows: [tuple((type(x).__name__, "nan" if x != x else x) for x in row) for row in rows]  # noqa: E731
    return norm(a) == norm(b)


@pytest.mark.parametrize("name", NAMES)
def test_load_table(tables, name):
    old = old_load_table(tables[name])
    keys = [k for k in old if isinstance(k, str)]
    rng = random.Random(name)
    for _ in range(30):
        wanted = rng.sample(keys, min(len(keys), rng.choice([1, 3, 20]))) + [
            "DT_TEST|T10|99|2020", "DT_TEST|T10", "nonsense", "DT_TEST|T10|00|0|A01|2020|", "", "DT_TEST||2020"]
        new = check_items.load_table(tables[name], wanted)
        assert set(new) <= set(wanted)
        for ref in wanted:
            assert same(new.get(ref, []), old.get(ref, [])), ref
    if keys:  # duplicated coordinates come back as two rows
        dup = [k for k in keys if len(old[k]) > 1]
        assert name in ("empty", "many", "eight", "nodim", "unnamed") or dup
        assert all(same(check_items.load_table(tables[name], [k])[k], old[k]) for k in dup)
    every = check_items.load_table(tables[name])  # old signature: every row with a coordinate
    assert set(every) == set(keys) and all(same(every[k], old[k]) for k in keys)


def test_refs_written_as_phase1(tables):
    """The cases the old code wrote otherwise: no category, a named category without a code, a code without a name."""
    refs = set(check_items.load_table(tables["nodim"]))
    assert "DT_NODIM|T1||2023" in refs and len(refs) == 6 and all(r.count("|") == 3 for r in refs)
    assert check_items.load_table(tables["nodim"], ["DT_NODIM|T1||2023", "DT_NODIM|T1|2023"]).keys() == {"DT_NODIM|T1||2023"}
    refs = set(check_items.load_table(tables["unnamed"]))
    assert {"DT_UNNAMED|T1|00|1|A|2022", "DT_UNNAMED|T1|00|1||2022", "DT_UNNAMED|T1|00|A|2022", "DT_UNNAMED|T1|00||2022"} <= refs
    got = check_items.load_table(tables["unnamed"], ["DT_UNNAMED|T1|00|A|2022", "DT_UNNAMED|T1|00|2|A|2022"])
    assert len(got["DT_UNNAMED|T1|00|A|2022"]) == 3 and "DT_UNNAMED|T1|00|2|A|2022" not in got  # (three rows name no dim2)
    out = printed(show_table.cells, TableFile(tables["nodim"]), ["item=T1", "period=2023"], 5)
    assert "DT_NODIM|T1||2023 " in out, out


PHASE1 = TOP / "kosis-ai-nlp-summary-T1-30"
TEXT_TABLES = ["strings", "pipes", "many", "one_group", "eight", "nostats", "two_labels", "nodim", "unnamed"]


@pytest.mark.skipif(not (PHASE1 / "kosis_chat" / "prepare" / "convert.py").is_file(), reason="the Phase 1 clone is not here")
@pytest.mark.parametrize("name", TEXT_TABLES)
def test_refs_as_phase1(tables, name):
    """The tools' coordinate is the source_ref Phase 1's own cells_of gives the same row (text columns, as the
    provided tables are). Run in another process: Phase 1 is not imported into this one."""
    code = ("import sys, json, types; sys.path[:0] = [sys.argv[1], sys.argv[1] + '/src']\n"
            "import pyarrow.parquet as pq\n"
            "from kosis_chat.prepare.convert import cells_of, rows_of\n"
            "rows = rows_of(pq.read_table(sys.argv[2]))\n"
            "meta = types.SimpleNamespace(tbl_id=rows[0]['tbl_id'])\n"
            "print(json.dumps([c.source_ref for c in cells_of(rows, meta)]))\n")
    done = subprocess.run([sys.executable, "-c", code, PHASE1.as_posix(), str(tables[name])], capture_output=True,
                          text=True, encoding="utf-8", env=os.environ | {"PYTHONUTF8": "1"})
    assert done.returncode == 0, done.stderr[-2000:]
    theirs = json.loads(done.stdout)
    tf = TableFile(tables[name])
    mine = []
    for group in tf.row_groups():
        keys = tf.read(group, ["tbl_id", "item_id", "period", *(f"dim{i}_{p}" for i in DIMS for p in ("name", "code"))])
        mine += ref_column(tf, keys).to_pylist()
    assert mine == theirs


def test_small_batches(tables, monkeypatch):
    """Batches smaller than a row group keep file order too."""
    import tablescan

    monkeypatch.setattr(tablescan, "BATCH", 5)
    test_cells(tables, "strings", 7)
    test_cells(tables, "one_group", 40)


def test_build_rejects_values_that_tell_no_calculation_apart(tables, tmp_path):
    """A spec whose answer stays the same under another calculation of the same cells is not an item: with 2 and 2
    a product passes for a sum (the grader compares numbers and cells, not how they were combined)."""
    values = tmp_path / "data" / "values"
    values.mkdir(parents=True)
    (values / "000_DT_NODIM.parquet").write_bytes(tables["nodim"].read_bytes())
    cell = lambda period, item: f"DT_NODIM|{item}||{period}"  # noqa: E731  values: 2021 T1=0 T2=1, 2022 2 3, 2023 4 5
    spec = lambda i, cells, formula: {"id": i, "question": f"q {i}", "table_keys": ["000_DT_NODIM"], "type": "compare",  # noqa: E731
                                      "cells": cells, "answers": [{"id": "r", "formula": formula, "round": 2}]}
    specs = [spec("sum", {"a": cell("2022", "T1"), "b": cell("2022", "T2")}, "a + b"),            # 2 + 3: only a sum is 5
             spec("zero", {"a": cell("2022", "T1"), "b": cell("2021", "T1")}, "a - b"),           # 2 - 0 = 2 + 0
             spec("one", {"a": cell("2023", "T2"), "b": cell("2021", "T2")}, "a * b"),            # 5 * 1 = 5 / 1
             spec("rate", {"a": cell("2022", "T1"), "b": cell("2023", "T2")}, "(b / a - 1) * 100"),          # 2 -> 5
             spec("rate24", {"a": cell("2022", "T1"), "b": cell("2023", "T1")}, "(b / a - 1) * 100"),      # 4 / 2 = 4 - 2
             {**spec("step", {"a": cell("2022", "T1"), "b": cell("2021", "T1"), "c": cell("2023", "T2")}, "d * c"),
              "steps": [{"id": "d", "formula": "a + b", "round": 2}]}]                             # the step: 2 + 0 = 2 - 0
    path = tmp_path / "spec.jsonl"
    path.write_text("\n".join(json.dumps(s, ensure_ascii=False) for s in specs) + "\n", encoding="utf-8")
    out = tmp_path / "items.jsonl"
    with contextlib.redirect_stdout(io.StringIO()):
        assert build_items.main(["--spec", str(path), "--out", str(out), "--workspace", str(tmp_path), "--known"]) == 0
    report = json.loads(out.with_suffix(".report.json").read_text(encoding="utf-8"))
    assert report["sum"] == "built" and report["rate"] == "built", report
    assert all("the result is the same when" in report[i] for i in ("zero", "one", "step", "rate24")), report
    assert build_items.same_under_another("a + b", {"a": build_items.Decimal(2), "b": build_items.Decimal(2)},
                                          build_items.Decimal(4), build_items.Decimal("0.01")) == "'+' is '*'"


def test_build_and_check(tables, tmp_path, monkeypatch):
    """build_items + check_items give the same items, report and verdicts with the old load_table."""
    values = tmp_path / "data" / "values"
    values.mkdir(parents=True)
    (values / "000_DT_TEST.parquet").write_bytes(tables["strings"].read_bytes())
    (values / "000_DT_NUM.parquet").write_bytes(tables["numbers"].read_bytes())
    old_s = old_load_table(tables["strings"])
    old_n = old_load_table(tables["numbers"])
    one = lambda old, kind: sorted(k for k, v in old.items() if len(v) == 1 and v[0][2] == kind)  # noqa: E731
    two = sorted(k for k, v in old_s.items() if len(v) > 1)
    ok_s, ok_n, sym = one(old_s, "ok"), one(old_n, "ok"), one(old_s, "symbol")
    specs = [
        {"id": "a", "question": "q a", "table_keys": ["000_DT_TEST"], "type": "trend", "cells": {"a": ok_s[0], "b": ok_s[5]},
         "answers": [{"id": "d", "formula": "b - a", "round": 2}]},
        {"id": "b", "question": "q b", "table_keys": ["000_DT_TEST", "000_DT_NUM"], "type": "compare",
         "cells": {"a": ok_s[9], "b": ok_n[3]}, "answers": [{"id": "r", "formula": "a / b * 100", "round": 1}]},
        {"id": "c", "question": "q c", "table_keys": ["000_DT_TEST"], "type": "value", "cells": {"a": two[0]}, "answers": ["a"]},
        {"id": "d", "question": "q d", "table_keys": ["000_DT_TEST"], "type": "value", "cells": {"a": sym[0]}, "answers": ["a"]},
        {"id": "e", "question": "q e", "table_keys": ["000_DT_TEST"], "type": "value", "cells": {"a": "DT_TEST|T10|2020"},
         "answers": ["a"]},
        {"id": "f", "question": "q f", "table_keys": ["000_DT_NUM", "000_DT_MISSING"], "type": "value",
         "cells": {"a": ok_n[7]}, "answers": ["a"]},
        {"id": "g", "question": "q g", "table_keys": ["000_DT_NUM"], "type": "value", "cells": {"a": ok_n[11]}, "answers": ["a"]},
    ]
    spec = tmp_path / "spec.jsonl"
    spec.write_text("\n".join(json.dumps(s, ensure_ascii=False) for s in specs) + "\nnot json\n", encoding="utf-8")

    def run(tag):
        out = tmp_path / f"items_{tag}.jsonl"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = build_items.main(["--spec", str(spec), "--out", str(out), "--workspace", str(tmp_path), "--known"])
        text = buf.getvalue().replace(str(out), "OUT").replace(str(out.with_suffix(".report.json")), "REPORT")
        return code, text, out.read_text(encoding="utf-8"), out.with_suffix(".report.json").read_text(encoding="utf-8")

    new = run("new")
    with monkeypatch.context() as m:
        m.setattr(check_items, "load_table", lambda path, refs=None: old_load_table(path))
        old = run("old")
    assert new == old
    assert "built" in new[3] and "rejected" in new[3]

    # check_items on a hand-made item file: a broken fact, a symbol cell, a duplicated coordinate, a missing table
    items = [json.loads(line) for line in new[2].splitlines()]
    bad = json.loads(json.dumps(items[0]))
    bad["id"] = "x"
    bad["question"] = "q x"
    bad["aux_facts"][0]["value"] += 1
    bad["aux_facts"][1]["source_ref"] = two[1]
    bad["table_keys"] = bad["table_keys"] + ["000_DT_GONE"]
    bad["tables_sha256"] = bad["tables_sha256"] + ["0" * 64]
    path = tmp_path / "check.jsonl"
    path.write_text("\n".join(json.dumps(i, ensure_ascii=False) for i in items + [bad]) + "\n", encoding="utf-8")

    def check():
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = check_items.main(["--items", str(path), "--workspace", str(tmp_path)])
        return code, buf.getvalue()

    got = check()
    with monkeypatch.context() as m:
        m.setattr(check_items, "load_table", lambda p, refs=None: old_load_table(p))
        want = check()
    assert got == want
    assert got[0] == 1 and "PROBLEM x/" in got[1]


def test_load_table_many_refs(tables):
    """More than 64 refs: the narrowing works on bit sets 64 refs at a time."""
    for name in ("strings", "eight", "pipes"):
        old = old_load_table(tables[name])
        keys = sorted(k for k in old if isinstance(k, str))
        wanted = random.Random(name).sample(keys, min(len(keys), 160)) + ["DT_TEST|T10|99|2020", "nonsense"]
        assert len(wanted) > 128 or name == "eight"
        new = check_items.load_table(tables[name], wanted)
        assert set(new) <= set(wanted)
        for ref in wanted:
            assert same(new.get(ref, []), old.get(ref, [])), ref


STACK_RUN = r"""
import pickle, sys, threading
sys.path.insert(0, sys.argv[1])
import check_items
refs = pickle.load(open(sys.argv[3], "rb"))
out = {}
threading.stack_size(1 << 20)  # 1 MB, the default stack of a Windows program
t = threading.Thread(target=lambda: out.update(check_items.load_table(__import__("pathlib").Path(sys.argv[2]), refs)))
t.start()
t.join()
pickle.dump(dict(out), open(sys.argv[3], "wb"))
"""


def test_load_table_thousands_of_refs_small_stack(tables, tmp_path):
    """Thousands of refs (tens of thousands of period texts) on a 1 MB stack: one long OR of them used to crash
    arrow (a segmentation fault, no traceback). Run in a child process so that a crash fails only this test."""
    try:
        threading.stack_size(1 << 20)
    except (ValueError, RuntimeError):
        pytest.skip("this platform cannot set a thread's stack size")
    finally:
        threading.stack_size(0)
    old = old_load_table(tables["strings"])
    keys = sorted(k for k in old if isinstance(k, str))
    wanted = keys + [f"DT_TEST|T{n % 7}0|{n % 90:02d}|A0{n % 3}|{1000 + n}" for n in range(4000)]
    refs = tmp_path / "refs.pkl"
    refs.write_bytes(pickle.dumps(wanted))
    script = tmp_path / "run.py"
    script.write_text(STACK_RUN, encoding="utf-8")
    r = subprocess.run([sys.executable, str(script), str(TOP / "evals"), str(tables["strings"]), str(refs)],
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, (r.returncode, r.stderr[-2000:])
    new = pickle.loads(refs.read_bytes())
    assert set(new) == set(keys)
    assert all(same(new[k], old[k]) for k in keys)


def test_ref_not_utf8(tables, tmp_path):
    """A coordinate with a lone surrogate is no row's coordinate (the old code: 0 rows); the other refs still load."""
    old = old_load_table(tables["strings"])
    key = next(k for k in old if isinstance(k, str))
    new = check_items.load_table(tables["strings"], [key, "DT_TEST|T10|\ud800", key + "\udcff"])
    assert set(new) == {key} and same(new[key], old[key])
    # through build_items: only that spec is rejected
    values = tmp_path / "data" / "values"
    values.mkdir(parents=True)
    (values / "000_DT_TEST.parquet").write_bytes(tables["strings"].read_bytes())
    ok = sorted(k for k, v in old.items() if isinstance(k, str) and len(v) == 1 and v[0][2] == "ok")
    specs = [{"id": "a", "question": "q a", "table_keys": ["000_DT_TEST"], "type": "value", "cells": {"a": ok[0]}, "answers": ["a"]},
             {"id": "b", "question": "q b", "table_keys": ["000_DT_TEST"], "type": "value", "cells": {"a": "T|I|\ud800"},
              "answers": ["a"]}]
    spec = tmp_path / "spec.jsonl"
    spec.write_text("\n".join(json.dumps(s) for s in specs) + "\n", encoding="utf-8")
    out = tmp_path / "items.jsonl"
    with contextlib.redirect_stdout(io.StringIO()):
        code = build_items.main(["--spec", str(spec), "--out", str(out), "--workspace", str(tmp_path), "--known"])
    report = json.loads(out.with_suffix(".report.json").read_text(encoding="utf-8"))
    assert code == 0 and report == {"a": "built", "b": "rejected: cell a: the coordinate matches 0 rows of the tables"}


def test_sha256_big_file(tmp_path):
    path = tmp_path / "big.bin"
    path.write_bytes(random.Random(1).randbytes(3 * (1 << 20) + 12345))
    assert check_items.sha256(path) == hashlib.sha256(path.read_bytes()).hexdigest()
    (tmp_path / "empty.bin").write_bytes(b"")
    assert check_items.sha256(tmp_path / "empty.bin") == hashlib.sha256(b"").hexdigest()


def test_big_integers_with_nulls(tmp_path):
    """An integer column with a null and values beyond 2**24 is float64 in pandas; conditions and coordinates on it do
    not fail. Beyond 2**53 load_table works; --cell still fails in arrow (no table has such codes)."""
    rows = [{"tbl_id": "DT_INT", "period": "2023", "item_id": "T1", "item_name": "인구", "unit": "명", "value": 1.0,
             "value_raw": "1", "value_kind": "ok", "dim1_name": "코드", "dim1_code": c, "dim1_label": "x"}
            for c in [9007199254740992, None, 5, 2**40, 2**30 + 1]]
    path = tmp_path / "int.parquet"
    write(path, rows, {"value": pa.float64(), "dim1_code": pa.int64()}, 2)
    df = pq.read_table(path).to_pandas()
    tf = TableFile(path)
    for cond in (["dim1=5.0"], ["dim1=5"], ["dim1=1099511627776.0"], ["dim1=9007199254740992.0"], ["dim1=1.5"],
                 ["dim1=1073741825.0"]):
        assert printed(show_table.cells, tf, cond, 40) == printed(old_cells, df, cond, 40), cond
    # beyond 2**53, with a null (float64 in pandas): --cell on another column, and load_table, where the period
    # (with a null too, so pandas makes it float) prunes row groups by statistics
    big = [{**r, "dim1_code": c, "period": p} for r, c, p in
           zip(rows, [2**53 + 1, None, 5, 2**60, 3], [2**53 + 1, 2**53 + 3, None, 2023, 2**62])]
    path = tmp_path / "big.parquet"
    write(path, big, {"value": pa.float64(), "dim1_code": pa.int64(), "period": pa.int64()}, 2)
    df = pq.read_table(path).to_pandas()
    for cond in (["item=T1"], ["unit=명"]):
        assert printed(show_table.cells, TableFile(path), cond, 40) == printed(old_cells, df, cond, 40), cond
    old = old_load_table(path)
    keys = [k for k in old if isinstance(k, str)]
    assert len(keys) == 4
    new = check_items.load_table(path, keys + ["DT_INT|T1|5.0|2024.0", "DT_INT|T1|9007199254740994.0"])
    assert set(new) == set(keys) and all(same(new[k], old[k]) for k in keys)
