"""Look at a table's metadata and find a cell's coordinate (for the attacker). pandas and pyarrow are needed.

    python scripts/show_table.py --find 출생 시도          # tables whose name or survey holds every word
    python scripts/show_table.py 101_DT_1B8000H            # the table's items, categories (codes) and periods
    python scripts/show_table.py 101_DT_1B8000H --cell item=T10 dim1=31 period=2023
                                                           # the cells that match: coordinate (source_ref), value

The tables are read from <workspace>/data (--workspace, else the KOSIS_WORKSPACE environment variable). A cell's
coordinate is tbl_id|item_id|<the code of every category the table has, in order>|period: what an attack spec
writes under "cells". Nothing is written anywhere.
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


def frame(data: Path, key: str):
    import pyarrow.parquet as pq

    row = next((r for r in catalog(data) if r["key"] == key), None)
    if row is None:
        raise SystemExit(f"{key} is not in tables.csv (find a key with --find)")
    df = pq.read_table(data / row["parquet"]).to_pandas()
    return row, df


def ref_of(df):
    ref = df["tbl_id"].astype(str) + "|" + df["item_id"].astype(str)
    for i in DIMS:
        col = f"dim{i}_code"
        if col in df:
            ref = ref + df[col].map(lambda c: "" if c is None or c != c else "|" + str(c))
    return ref + "|" + df["period"].astype(str)


def show(row: dict, df, limit: int) -> None:
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
    periods = sorted(df["period"].astype(str).unique())
    print(f"[period] {len(periods)}: {', '.join(periods[:6])}{' ... ' + ', '.join(periods[-6:]) if len(periods) > 12 else ', ' + ', '.join(periods[6:]) if len(periods) > 6 else ''}")
    kinds = df["value_kind"].value_counts().to_dict()
    print(f"[value_kind] {kinds}  (only 'ok' cells hold a number)")


def cells(df, conditions: list[str], limit: int) -> None:
    mask = None
    for cond in conditions:
        name, _, value = cond.partition("=")
        col = {"item": "item_id", "period": "period"}.get(name, f"{name}_code" if name.startswith("dim") else name)
        if col not in df:
            raise SystemExit(f"unknown condition {cond!r}: use item=, dim1= ... dim8=, period=")
        hit = df[col].astype(str) == value
        mask = hit if mask is None else mask & hit
    found = df if mask is None else df[mask]
    refs = ref_of(found)
    print(f"{len(found)} cells match")
    for ref, r in list(zip(refs, found.itertuples(index=False)))[:limit]:
        labels = [str(getattr(r, f"dim{i}_label")) for i in DIMS if getattr(r, f"dim{i}_label", None) == getattr(r, f"dim{i}_label", None)
                  and getattr(r, f"dim{i}_label", None) is not None]
        print(f"  {ref}   {r.item_name} / {' / '.join(labels)} / {r.period} = {r.value_raw} {r.unit} [{r.value_kind}]")
    if len(found) > limit:
        print(f"  ... {len(found) - limit} more: add conditions")


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
    row, df = frame(data, args.key)
    if args.cell:
        cells(df, args.cell, args.limit)
    else:
        show(row, df, args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
