"""Split an item file into a decision part (dev) and a kept-aside part (holdout). Standard library only.

    python evals/split_items.py                       # writes evals/baseline/split.json (ids only)

The loop decides keep / revert on dev; holdout is measured only when a person looks (overfitting to dev shows up
as dev rising while holdout does not). The two parts share no table: items are grouped by the tables they read
(items that share a table go together), and whole groups are assigned. Among many random assignments the one
closest to the wanted size and to the same share of every subtype and table count is kept (fixed seed: the same
file gives the same split). Nothing of the items is printed except counts.
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent


def groups_of(items: list[dict]) -> list[list[dict]]:
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for item in items:
        keys = item["table_keys"]
        for key in keys[1:]:
            parent[find(key)] = find(keys[0])
    by_root: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        by_root[find(item["table_keys"][0])].append(item)
    return [by_root[root] for root in sorted(by_root)]


def split(items: list[dict], holdout: int, seed: int, tries: int) -> tuple[list[str], list[str]]:
    groups = groups_of(items)
    share = holdout / len(items)
    want = {key: n * share for key, n in Counter(_strata(i) for i in items).items()}
    best, best_cost = None, None
    for t in range(tries):
        order = groups[:]
        random.Random(seed * 100003 + t).shuffle(order)
        picked, size = [], 0
        for group in order:
            if size + len(group) <= holdout:
                picked.append(group)
                size += len(group)
        have = Counter(_strata(i) for g in picked for i in g)
        cost = sum(abs(have.get(k, 0) - w) for k, w in want.items()) + 3 * abs(holdout - size)
        if best_cost is None or cost < best_cost:
            best, best_cost = picked, cost
    held = {i["id"] for g in best for i in g}
    return [i["id"] for i in items if i["id"] not in held], [i["id"] for i in items if i["id"] in held]


def _strata(item: dict) -> tuple:
    return (item.get("subtype"), len(item["table_keys"]))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="split an item file into dev and holdout")
    p.add_argument("--items", type=Path, default=HERE / "baseline" / "items.jsonl")
    p.add_argument("--out", type=Path, default=None, help="default: split.json next to the item file")
    p.add_argument("--holdout", type=int, default=200)
    p.add_argument("--seed", type=int, default=20261120)
    p.add_argument("--tries", type=int, default=4000)
    args = p.parse_args(argv)
    items = [json.loads(line) for line in args.items.read_text(encoding="utf-8").splitlines() if line.strip()]
    dev, held = split(items, args.holdout, args.seed, args.tries)
    out = args.out or args.items.with_name("split.json")
    out.write_text(json.dumps({"seed": args.seed, "items": len(items), "dev": dev, "holdout": held}, indent=1),
                   encoding="utf-8")
    by_id = {i["id"]: i for i in items}
    tables = lambda ids: {k for i in ids for k in by_id[i]["table_keys"]}  # noqa: E731
    print(f"dev {len(dev)} items / {len(tables(dev))} tables, holdout {len(held)} items / {len(tables(held))} tables, "
          f"shared tables {len(tables(dev) & tables(held))} -> {out}")
    for name, key in (("subtype", lambda i: i.get("subtype")), ("tables", lambda i: len(i["table_keys"]))):
        all_, h = Counter(key(i) for i in items), Counter(key(by_id[i]) for i in held)
        print(f"[{name}] holdout / all: " + ", ".join(f"{k} {h.get(k, 0)}/{n}" for k, n in sorted(all_.items(), key=str)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
