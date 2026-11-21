"""Look at a table's metadata and find a cell's coordinate (for the attacker). pyarrow and pandas are needed.

    python scripts/show_table.py --find 출생 시도          # tables whose name or survey holds every word
    python scripts/show_table.py 101_DT_1B8000H            # the table's items, categories (codes) and periods
    python scripts/show_table.py 101_DT_1B8000H --cell item=T10 dim1=31 period=2023
                                                           # the cells that match: coordinate (source_ref), value

The tables are read from <workspace>/data (--workspace, else the KOSIS_WORKSPACE environment variable). A cell's
coordinate is tbl_id|item_id|<the code of every category the table has, in order>|period: what an attack spec
writes under "cells". Nothing is written anywhere. A missing value (empty cell) matches no condition, not even
dim2=None or dim2=nan, and is never part of a coordinate (a row without tbl_id, item_id or period shows "nan").

A table is never read whole (some have 100M rows): the listing streams the needed columns a batch at a time, and
--cell reads only the matching rows (scripts/tablescan.py).
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

DIMS = range(1, 9)
SHOWN = 40  # codes shown per category


def catalog(data: Path) -> list[dict]:
    with (data / "tables.csv").open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def open_table(data: Path, key: str):
    from tablescan import TableFile

    row = next((r for r in catalog(data) if r["key"] == key), None)
    if row is None:
        raise SystemExit(f"{key} is not in tables.csv (find a key with --find)")
    return row, TableFile(data / row["parquet"])


def present(df, col: str):
    """The column's values as text where it has one; NaN where it is missing (pandas 2 made the text "None"/"nan")."""
    return df[col].astype(str).where(df[col].notna())


def ref_of(df):
    """A missing tbl_id, item_id or period makes the whole coordinate NaN (printed "nan") on every pandas version."""
    ref = present(df, "tbl_id") + "|" + present(df, "item_id")
    for i in DIMS:
        col = f"dim{i}_code"
        if col in df:
            ref = ref + df[col].map(lambda c: "" if c is None or c != c else "|" + str(c))
    return ref + "|" + present(df, "period")


def distinct_rows(table):
    """The distinct rows of a slice in first-seen order, worked out on dictionary indices instead of texts (fast on
    a batch read as dictionaries). Two entries of one dictionary with the same text would count as different, so
    the caller runs this again on plain text. (Table.group_by does not keep first-seen order.)"""
    import numpy as np
    import pyarrow as pa
    import pyarrow.compute as pc

    cols = []
    key = np.zeros(table.num_rows, np.int64)
    for col in table.columns:
        col = col.combine_chunks()
        if not pa.types.is_dictionary(col.type):
            col = col.dictionary_encode()
        size = len(col.dictionary) + 1  # null is one more value
        key = key * size + pc.fill_null(col.indices, size - 1).to_numpy().astype(np.int64)
        cols.append((col.dictionary, size))
    key = pc.unique(pa.array(key)).to_numpy()  # first-seen order
    out = []
    for dictionary, size in reversed(cols):
        key, index = np.divmod(key, size)
        out.append(dictionary.take(pa.array(index, mask=index == size - 1)))
    return pa.table(out[::-1], names=table.column_names)


def listing(tf) -> tuple[dict, dict]:
    """The distinct rows of each column group in first-seen order (what drop_duplicates() kept from the whole table)
    and the value_kind counts, read a batch at a time. Text columns are read as dictionaries, which is much faster
    than reading every text. A category whose name is null in every row is not read."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    names = tf.schema.names
    groups = {"item": ["item_id", "item_name", "unit"], "period": ["period"]}
    for i in DIMS:
        name = f"dim{i}_name"
        if name in names and not tf.all_null(name):
            groups[name] = [name]
            groups[f"dim{i}"] = [f"dim{i}_code", f"dim{i}_label"]
    seen = {g: tf.schema.empty_table().select(cols) for g, cols in groups.items()}
    kinds: dict = {}
    columns = list(dict.fromkeys(c for cols in groups.values() for c in cols)) + ["value_kind"]
    texts = [c for c in columns if pa.types.is_string(tf.schema.field(c).type)]
    for batch in pq.ParquetFile(tf.path, read_dictionary=texts).iter_batches(batch_size=500_000, columns=columns):
        table = pa.Table.from_batches([batch])
        for g, cols in groups.items():
            part = distinct_rows(table.select(cols)).cast(seen[g].schema)
            seen[g] = distinct_rows(pa.concat_tables([seen[g], part]))
        for kc in pc.value_counts(batch.column("value_kind")).to_pylist():
            if kc["values"] is not None and kc["values"] == kc["values"]:  # value_counts() leaves out missing values
                kinds[kc["values"]] = kinds.get(kc["values"], 0) + kc["counts"]
    frames = {g: tf.to_pandas(t) for g, t in seen.items()}
    return frames, dict(sorted(kinds.items(), key=lambda kv: -kv[1]))  # stable: ties stay in first-seen order


def show(row: dict, tf, limit: int) -> None:
    frames, kinds = listing(tf)
    print(f"{row['key']}  {row['table_name']}  ({row['survey_name']})  rows {tf.rows}")
    items = frames["item"][["item_id", "item_name", "unit"]].drop_duplicates()
    print("[item]")
    for r in items.itertuples(index=False):
        print(f"  {r.item_id} = {r.item_name} ({r.unit})")
    for i in DIMS:
        name = f"dim{i}_name"
        if name not in frames or frames[name][name].dropna().empty:
            continue
        pairs = frames[f"dim{i}"][[f"dim{i}_code", f"dim{i}_label"]].dropna().drop_duplicates()
        print(f"[dim{i}] {frames[name][name].dropna().iloc[0]}  ({len(pairs)} codes)")
        for r in pairs.head(limit).itertuples(index=False):
            print(f"  {r[0]} = {r[1]}")
        if len(pairs) > limit:
            print(f"  ... {len(pairs) - limit} more (--limit {len(pairs)})")
    periods = sorted(present(frames["period"], "period").unique())  # a missing period: NaN, which sorted() refuses
    print(f"[period] {len(periods)}: {', '.join(periods[:6])}{' ... ' + ', '.join(periods[-6:]) if len(periods) > 12 else ', ' + ', '.join(periods[6:]) if len(periods) > 6 else ''}")
    print(f"[value_kind] {kinds}  (only 'ok' cells hold a number)")


SHOWN_COLUMNS = {"tbl_id", "item_id", "item_name", "unit", "period", "value_raw", "value_kind",
                 *(f"dim{i}_{part}" for i in DIMS for part in ("code", "label"))}


def cells(tf, conditions: list[str], limit: int) -> None:
    """The conditions are a filter on the file; only the first `limit` matches are read, the rest only counted."""
    import pyarrow as pa

    where = None
    for cond in conditions:
        name, _, value = cond.partition("=")
        col = {"item": "item_id", "period": "period"}.get(name, f"{name}_code" if name.startswith("dim") else name)
        if col not in tf.schema.names:
            raise SystemExit(f"unknown condition {cond!r}: use item=, dim1= ... dim8=, period=")
        hit = tf.equals(col, value)
        where = hit if where is None else where & hit
    columns = [c for c in tf.schema.names if c in SHOWN_COLUMNS]
    keep = limit + 1 if limit >= 0 else None  # one more than shown tells whether to count the rest
    batches, n = [], 0
    for batch in tf.batches(columns, where):
        batches.append(batch)
        n += batch.num_rows
        if keep is not None and n >= keep:
            break
    found = pa.Table.from_batches(batches, schema=tf.schema.empty_table().select(columns).schema)
    total = n if keep is None or n < keep else tf.count(where)
    shown = len(range(n)[:limit])
    found = tf.to_pandas(found.slice(0, max(shown, min(n, 1))))  # only the rows printed (one if none is) go to pandas
    refs = ref_of(found)
    print(f"{total} cells match")
    for ref, r in list(zip(refs, found.itertuples(index=False)))[:shown]:
        labels = [str(getattr(r, f"dim{i}_label")) for i in DIMS if getattr(r, f"dim{i}_label", None) == getattr(r, f"dim{i}_label", None)
                  and getattr(r, f"dim{i}_label", None) is not None]
        print(f"  {ref}   {r.item_name} / {' / '.join(labels)} / {r.period} = {r.value_raw} {r.unit} [{r.value_kind}]")
    if total > limit:
        print(f"  ... {total - limit} more: add conditions")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="look at a table's metadata and find a cell's coordinate")
    p.add_argument("key", nargs="?", help="a tables.csv key, e.g. 101_DT_1B8000H")
    p.add_argument("--find", nargs="+", metavar="WORD", help="list tables whose name or survey holds every word")
    p.add_argument("--cell", nargs="+", metavar="NAME=CODE", help="item=, dim1= ... dim8=, period=")
    p.add_argument("--limit", type=int, default=SHOWN)
    p.add_argument("--workspace", type=Path, default=None, help="folder that holds data/ (else KOSIS_WORKSPACE)")
    args = p.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    workspace = args.workspace or Path(os.environ.get("KOSIS_WORKSPACE", ""))
    data = workspace / "data"
    if not (data / "tables.csv").is_file():
        print("give --workspace (or set KOSIS_WORKSPACE): the folder whose data/ holds tables.csv", file=sys.stderr)
        return 2
    if args.find:
        rows = [r for r in catalog(data) if all(w in (r["table_name"] + " " + r["survey_name"]) for w in args.find)]
        print(f"{len(rows)} tables")
        for r in rows[: args.limit]:
            print(f"  {r['key']}  {r['table_name']}  ({r['survey_name']}; {r['coverage']})")
        return 0
    if not args.key:
        p.error("give a table key, or --find WORD")
    row, tf = open_table(data, args.key)
    if args.cell:
        cells(tf, args.cell, args.limit)
    else:
        show(row, tf, args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
