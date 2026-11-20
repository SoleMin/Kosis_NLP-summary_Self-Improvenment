"""Self-check of grade.py with made-up answers (no Phase 1, no data needed).

    python evals/selfcheck.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import grade  # noqa: E402


def cell(i, ref, value):
    return {"id": i, "value": value, "source_ref": ref}


def derived(i, value, expr, digits=2):
    return {"id": i, "value": value, "derivation": expr, "round": digits}


ITEM = {  # change from 200 to 250: difference and rate of change
    "id": "T1", "type": "trend", "subtype": "demo", "table_keys": ["000_T"], "chart_expected": True,
    "required_facts": [derived("d", 50, "b - a"), derived("r", 25.0, "(b / a - 1) * 100")],
    "aux_facts": [cell("a", "T|I|00|2020", 200), cell("b", "T|I|00|2021", 250)],
}
CELLS = {"id": "T2", "type": "value", "subtype": "demo", "table_keys": ["000_T", "000_U"], "chart_expected": False,
         "required_facts": [cell("a", "T|I|00|2020", 200), cell("b", "U|I|00|2020", 200)], "aux_facts": []}


def out(facts, chart=False, status="ok", origin="rules", main=None):
    """A number-path output; every fact is marked as the answer unless main says otherwise."""
    return {"id": "x", "status": status, "origin": origin, "facts": facts, "chart": chart,
            "main": [f["id"] for f in facts] if main is None else main}


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name}: {got!r}" + ("" if ok else f" (want {want!r})"))
    return ok


def main() -> int:
    module_cells = [cell("f1", "T|I|00|2020", 200), cell("f2", "T|I|00|2021", 250)]
    g = grade.grade_item
    results = [
        check("all facts present -> PASS",
              g(ITEM, out(module_cells + [derived("f3", 50, "f2 - f1", 0), derived("f4", 25.0, "(f2 / f1 - 1) * 100", 1)], True))["verdict"], "PASS"),
        check("one of two -> 0.5",
              g(ITEM, out(module_cells + [derived("f3", 50, "f2 - f1", 0)]))["score"], 0.5),
        check("none -> FAIL", g(ITEM, out(module_cells))["verdict"], "FAIL"),
        check("within half of the last digit (24.996 vs 25.00)",
              g(ITEM, out([derived("f3", 50, "f2 - f1"), derived("f4", 24.996, "f2 / f1")]))["score"], 1.0),
        check("outside the tolerance (25.006 vs 25.00)",
              g(ITEM, out([derived("f3", 50, "f2 - f1"), derived("f4", 25.006, "f2 / f1")]))["score"], 0.5),
        check("opposite sign is not a match, but flagged",
              g(ITEM, out([derived("f3", -50, "f1 - f2"), derived("f4", 25.0, "f2 / f1")]))["near_miss"], {"d": ["sign"]}),
        check("ratio instead of percent is flagged",
              g(ITEM, out([derived("f3", 50, "f2 - f1"), derived("f4", 0.25, "f2 / f1")]))["near_miss"], {"r": ["scale_100"]}),
        check("same cells in the derivation -> same-inputs score 1.0",
              g(ITEM, out(module_cells + [derived("f3", 50, "f2 - f1"), derived("f4", 25.0, "(f2 / f1 - 1) * 100")]))["score_same_inputs"], 1.0),
        check("right value from other cells -> same-inputs score lower",
              g(ITEM, out([cell("f9", "T|I|99|2021", 50), derived("f4", 25.0, "f9 / 2")]))["score_same_inputs"], 0.0),
        check("one module fact cannot satisfy two answers (50 and 50.004 asked, one 50 given)",
              g({**ITEM, "required_facts": [derived("d", 50, "b - a"), derived("e", 50.0, "b - a + 0")]},
                out([derived("f3", 50, "f2 - f1")]))["score"], 0.5),
        check("cell: same value under another table's ref is not a match (ref mode)",
              g(CELLS, out([cell("f1", "T|I|00|2020", 200)]))["score"], 0.5),
        check("cell: value mode still needs two facts for two answers",
              g(CELLS, out([cell("f1", "T|I|00|2020", 200)]), "value")["score"], 0.5),
        check("cell: value-only reference score counts another ref",
              g(CELLS, out([cell("f1", "T|I|00|2020", 200), cell("f2", "Z|I|00|2020", 200)]))["score_value_only"], 1.0),
        check("cell: value must be exact (200.004 is not 200)",
              g(CELLS, out([cell("f1", "T|I|00|2020", 200.004), cell("f2", "U|I|00|2020", 200)]))["score"], 0.5),
        check("run error -> 0", g(ITEM, {"id": "T1", "status": "error", "error": "boom"})["score"], 0.0),
        check("no output -> 0, status missing", g(ITEM, None)["status"], "missing"),
        check("chart expected and made", g(ITEM, out([], True))["chart_ok"], True),
        check("chart not expected but made", g(CELLS, out([], True))["chart_ok"], False),
        check("derivation recomputed", grade.evaluate("((b / a) ** (1 / 2) - 1) * 100", {"a": 100, "b": 121}) - 10 < 1e-9, True),
    ]
    both = module_cells + [derived("f3", 50, "f2 - f1"), derived("f4", 25.0, "(f2 / f1 - 1) * 100")]
    results += [
        check("only facts marked as the answer count (one of two marked)", g(ITEM, out(both, main=["f3"]))["score"], 0.5),
        check("a fact that is not marked still feeds the same-inputs check",
              g(ITEM, out(both, main=["f3", "f4"]))["score_same_inputs"], 1.0),
        check("no mark at all -> 0", g(ITEM, {"id": "x", "status": "ok", "origin": "rules", "facts": both})["score"], 0.0),
        check("model filled codes -> x0.8", g(ITEM, out(both, origin="model_codes"))["score"], 0.8),
        check("model set the type -> x0.5", g(ITEM, out(both, origin="model_type"))["score"], 0.5),
        check("model wrote the plan -> 0, not accepted", g(ITEM, out(both, origin="model_plan"))["category"], "not_accepted"),
        check("origin not recorded -> 0", g(ITEM, {**out(both), "origin": None})["score"], 0.0),
        check("weights can be changed", g(ITEM, out(both, origin="model_type"), weights={"rules": 1, "model_type": 0.25})["score"], 0.25),
        check("no plan -> 0, its own category", g(ITEM, {"id": "x", "status": "no_plan", "reason": "unresolved"})["category"], "no_plan"),
        check("refused -> 0 even with facts", g(ITEM, {**out(both), "status": "refused"})["score"], 0.0),
        check("right but weighted is still PASS / correct",
              (lambda r: (r["verdict"], r["category"], r["right"]))(g(ITEM, out(both, origin="model_codes"))), ("PASS", "correct", 1.0)),
        check("half right -> wrong", g(ITEM, out(both, main=["f3"]))["category"], "wrong"),
        check("legacy: every fact counts, no weight",
              g(ITEM, {"id": "x", "status": "ok", "outcome": "answered", "facts": both}, legacy=True)["score"], 1.0),
        check("no chart key -> chart is not compared", g(ITEM, {k: v for k, v in out(both).items() if k != "chart"})["chart_ok"], None),
    ]
    rows = [g(ITEM, out(module_cells + [derived("f3", 50, "f2 - f1")], True)), g(CELLS, None),
            g(ITEM, out(both, True, origin="model_codes")), g(ITEM, {"id": "x", "status": "no_plan", "reason": "unresolved"})]
    summary = grade.summarize(rows)
    results.append(check("summary mean", summary["score"], round((0.5 + 0 + 0.8 + 0) / 4, 4)))
    results.append(check("summary before weights", summary["right_unweighted"], 0.375))
    results.append(check("summary categories", summary["category"], {"correct": 1, "wrong": 1, "no_plan": 1, "error": 1}))
    results.append(check("summary origins", {k: v["items"] for k, v in summary["origin"].items()}, {"model_codes": 1, "rules": 1}))
    results.append(check("summary chart accuracy", summary["chart"]["accuracy"], 1.0))
    print(f"{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
