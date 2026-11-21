"""Grade Phase 1 outputs against an item file (baseline or golden). Standard library only; Phase 1 is not imported.

    python evals/grade.py --run evals/runs/<name>                 # reads <run>/outputs.jsonl and <run>/meta.json
    python evals/grade.py --items evals/baseline/items.jsonl --outputs some/outputs.jsonl
    python evals/grade.py --run evals/runs/<name> --quiet         # prints the final number only

Rule (decided 2026-11-19, changed 2026-11-20):
- What is graded is the number path alone: defaults -> the rules' plan -> (the model fills categories the rules left
  open) -> compute. The judge's refusals and the sentence are not part of it.
- The module's answer is the facts it chose for the answer (the output's "answer": the ids
  compute.answer_facts.step_answer_facts returned; an output written before that step has "main", the facts of
  plan.show, and that is read instead). Other facts it computed are not graded.
- An item's answer is its required_facts. Right = matched required facts / required facts.
  All matched = PASS, some = PARTIAL, none = FAIL.
- A chosen fact has to belong to the answer (decided 2026-11-20: "the answer and the cells it is computed from").
  It belongs when it matched a required fact, when it is one of the item's aux_facts (the cells the answer is
  computed from), or when a chosen fact that matched a required fact was computed from it (the module's own
  derivations, followed down). Any other chosen fact is extra. A wrong answer is already paid for as a required
  fact that was not matched, so extras are counted beyond the number of unmatched required facts:
  kept = 1 - max(0, extra - unmatched required) / chosen facts.
- Item score = right x kept x the weight of how the plan was made (the output's "origin"):
  rules 1.0 (rules alone), model_codes 0.8 (the model filled category codes only; --weight-codes),
  model_type 0.5 (the model also set the question type, the ranking axis or max/min; --weight-type),
  anything else 0 (the model wrote the plan or the numbers, or the origin is not recorded).
- No plan (status no_plan), a refused calculation (refused), a run error or a missing output scores 0.
- An item whose tables could not be read (status load_error, written by run_calc.py) scores 0 too, but it is not a
  result of the code under test. It is counted on its own (category "load_error"); a summary that has any is not
  a measurement of the code, and the loop does not use it.
- A cell fact (source_ref) matches a module fact with the same source_ref and exactly the same value.
- A derived fact (derivation + round) matches a module fact whose value is within half of the last rounded digit
  (round 2 -> 0.005). On top of that only what a float cannot hold is allowed: four steps of the float grid at the
  size of the two numbers (math.ulp), 1e-9 at least. It does not grow with the answer beyond that: for an answer
  near 1e12 it is 0.0005, not 1000. One module fact can satisfy only one required fact.
- aux_facts (the cells a derivation reads) earn nothing; choosing them costs nothing; their coverage is reported.
- The same-inputs score (score_same_inputs) is the item score with one more condition on a derived fact: the module
  fact must be computed from the same source cells as the item's derivation, both followed down to the cells. How
  the module combined them is not compared (a rate from the two cells, or from their difference and one of them,
  reads the same cells). A number that only happens to equal the answer, made from other cells, earns nothing
  here. The loop keeps a fix only when this score rises too (GOAL.md); the plain score alone can be raised by such
  a number.
- Chart: when an item has chart_expected (true/false) and the output says whether a chart came back ("chart"),
  the two are compared. Reported separately; it enters the final number only with --chart-weight.
- --legacy grades outputs of the whole pipeline the 2026-11-19 way: every fact counts, no weight, nothing is extra.

The last stdout line is one number in 0..1 (evals/README.md). Per-item details go to <run>/graded.jsonl and are
never printed: they contain the questions and answers.
"""

from __future__ import annotations

import argparse
import ast
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

ABS_EPS = 1e-9  # float noise allowed on top of the half-digit tolerance: this, or ULPS steps of the float grid
ULPS = 4        # at the size of the numbers compared when that is more (a number near 1e12: 0.0005)
WEIGHTS = {"rules": 1.0, "model_codes": 0.8, "model_type": 0.5}  # by the output's "origin"; any other origin: 0
ZERO_STATUS = ("no_plan", "refused")  # the number path ended without numbers
_BIN = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b,
        ast.Div: lambda a, b: a / b, ast.Pow: lambda a, b: a ** b}


# ---------------------------------------------------------------- small helpers

def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def is_num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x and abs(x) != float("inf")


def names(expr: str) -> list[str]:
    """Fact ids a derivation refers to (AST names, so '1e3' is not read as an id)."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return []
    return list(dict.fromkeys(n.id for n in ast.walk(tree) if isinstance(n, ast.Name)))


def evaluate(expr: str, values: dict[str, float]) -> float:
    """Recompute a derivation: fact ids, numbers, + - * / ** and parentheses only."""
    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and is_num(node.value):
            return node.value
        if isinstance(node, ast.Name):
            return values[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            v = ev(node.operand)
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            return _BIN[type(node.op)](ev(node.left), ev(node.right))
        raise ValueError(f"not plain arithmetic: {expr!r}")
    return ev(ast.parse(expr, mode="eval"))


def leaf_refs(fact_id: str, by_id: dict[str, dict], _seen: frozenset = frozenset()) -> frozenset | None:
    """The source cells a fact is made of (itself for a cell fact). None when an id is unknown or circular."""
    fact = by_id.get(fact_id)
    if fact is None or fact_id in _seen:
        return None
    if fact.get("source_ref"):
        return frozenset([fact["source_ref"]])
    if not fact.get("derivation"):
        return None
    refs: set[str] = set()
    for name in names(fact["derivation"]):
        sub = leaf_refs(name, by_id, _seen | {fact_id})
        if sub is None:
            return None
        refs |= sub
    return frozenset(refs)


def tolerance(fact: dict) -> float:
    return 0.5 * 10 ** -int(fact.get("round") or 0)


def close(a: float, b: float, tol: float) -> bool:
    """Within tol, give or take what floats of this size cannot tell apart. (Until 2026-11-21 the allowance was
    1e-9 of the answer's size, which let 1e12 + 500 pass for 1e12.)"""
    return abs(a - b) <= tol + max(ABS_EPS, ULPS * math.ulp(max(abs(a), abs(b))))


def max_matching(candidates: list[list[int]]) -> dict[int, int]:
    """Largest one-to-one assignment required index -> module index (augmenting paths; the lists are tiny)."""
    owner: dict[int, int] = {}  # module index -> required index

    def assign(r: int, seen: set[int]) -> bool:
        for m in candidates[r]:
            if m in seen:
                continue
            seen.add(m)
            if m not in owner or assign(owner[m], seen):
                owner[m] = r
                return True
        return False

    for r in sorted(range(len(candidates)), key=lambda i: len(candidates[i])):
        assign(r, set())
    return {r: m for m, r in owner.items()}


# ---------------------------------------------------------------- one item

def _candidates(req: dict, mods: list[dict], cell_mode: str) -> list[int]:
    out = []
    for i, m in enumerate(mods):
        v = m.get("value")
        if not is_num(v):
            continue
        if req.get("source_ref"):
            same_ref = m.get("source_ref") == req["source_ref"]
            if float(v) == float(req["value"]) and (same_ref or cell_mode == "value"):
                out.append(i)
        elif close(float(v), float(req["value"]), tolerance(req)):
            out.append(i)
    return out


def _inputs_of(ids: list[str], by_id: dict[str, dict]) -> set[str]:
    """Ids of every fact the given facts were computed from, followed down their derivations (the given ids too)."""
    seen: set[str] = set()
    todo = list(ids)
    while todo:
        i = todo.pop()
        if i in seen or i not in by_id:
            continue
        seen.add(i)
        todo += names(by_id[i].get("derivation") or "")
    return seen


def _extra(mods: list[dict], matched: list[int], aux: list[dict], by_id: dict[str, dict], cell_mode: str) -> list[int]:
    """Indexes of the chosen facts that do not belong to the answer: not matched to a required fact, not a cell the
    item's answer is computed from (aux_facts), and nothing a matched fact was computed from."""
    used = _inputs_of([mods[m].get("id") for m in matched], by_id)
    aux_refs = {(f["source_ref"], float(f["value"])) for f in aux if f.get("source_ref") and is_num(f.get("value"))}
    aux_values = {v for _, v in aux_refs}
    out = []
    for i, m in enumerate(mods):
        if i in matched or m.get("id") in used:
            continue
        v = m.get("value")
        if is_num(v) and m.get("source_ref") and not m.get("derivation"):
            if (m["source_ref"], float(v)) in aux_refs or (cell_mode == "value" and float(v) in aux_values):
                continue
        out.append(i)
    return out


def _near_miss(req: dict, mods: list[dict]) -> list[str]:
    """Why an unmatched required fact may have been close (diagnosis only, never scored)."""
    g, flags = float(req["value"]), []
    values = [(float(m["value"]), m) for m in mods if is_num(m.get("value"))]
    if req.get("source_ref"):
        if any(v == g for v, _ in values):
            flags.append("value_only")  # the value is there under another source_ref (or none)
        return flags
    tol = tolerance(req)
    if g != 0 and any(close(v, -g, tol) for v, _ in values):
        flags.append("sign")  # same size, opposite sign
    if any(isinstance(m.get("round"), int) and m["round"] < int(req.get("round") or 0)
           and close(v, g, 0.5 * 10 ** -m["round"]) for v, m in values):
        flags.append("coarse_round")  # right when rounded to the module's fewer decimals
    if g != 0 and any(close(v * 100, g, tol) or close(v / 100, g, tol) for v, _ in values):
        flags.append("scale_100")  # ratio vs percent
    return flags


def grade_item(item: dict, out: dict | None, cell_mode: str = "ref", weights: dict[str, float] | None = None,
               legacy: bool = False) -> dict:
    required = item.get("required_facts") or []
    aux = item.get("aux_facts") or []
    gold_by_id = {f["id"]: f for f in required + aux}
    out = out or {}
    status = "missing" if not out else out.get("status", "ok")
    every = (out.get("facts") or []) if status == "ok" else []
    mod_by_id = {m.get("id"): m for m in every}
    chosen = out.get("answer") if isinstance(out.get("answer"), list) else out.get("main")
    marked = isinstance(chosen, list)
    if legacy:
        mods, factor, origin = every, 1.0, out.get("origin")
    else:  # only the facts the module chose for the answer; an output without the list has no answer
        ids = set(chosen) if marked else set()
        mods = [m for m in every if m.get("id") in ids]
        origin = out.get("origin")
        factor = float((WEIGHTS if weights is None else weights).get(origin, 0.0)) if status == "ok" else 0.0

    def score_with(mode: str) -> tuple[dict[int, int], float, list[int]]:
        match = max_matching([_candidates(r, mods, mode) for r in required])
        extra = [] if legacy else _extra(mods, list(match.values()), aux, mod_by_id, mode)
        return match, (len(match) / len(required) if required else 0.0), extra

    def kept_of(match: dict[int, int], extra: list[int]) -> float:
        counted = max(0, len(extra) - (len(required) - len(match)))  # a wrong answer is not charged twice
        return 1.0 - counted / len(mods) if mods else 1.0

    match, right, extra = score_with(cell_mode)
    kept = kept_of(match, extra)
    match_value_only, right_value_only, extra_value_only = score_with("value")
    # strict: a derived fact is matched only by a module fact built from the same source cells as the item's own
    # derivation (followed down to the cells on both sides). Matched again on its own, so that of two module facts
    # with the answer's value the one built from the right cells is the one that counts.
    def same_cells(r: int, m: int) -> bool:
        if required[r].get("source_ref"):
            return True
        gold_refs = leaf_refs(required[r]["id"], gold_by_id)
        return gold_refs is not None and gold_refs == leaf_refs(mods[m].get("id"), mod_by_id)

    strict_match = max_matching([[m for m in _candidates(required[r], mods, cell_mode) if same_cells(r, m)]
                                 for r in range(len(required))])
    strict = len(strict_match) / len(required) if required else 0.0

    missing = [required[r] for r in range(len(required)) if r not in match]
    near = {f["id"]: flags for f in missing if (flags := _near_miss(f, mods))}
    mod_refs_all = {m.get("source_ref") for m in every if m.get("source_ref")}
    aux_cells = [f for f in aux if f.get("source_ref")]
    expected_chart = item.get("chart_expected") if (legacy or "chart" in out) else None
    made_chart = bool(out.get("chart")) if status == "ok" else False
    full = bool(required) and len(match) == len(required)
    if status in ZERO_STATUS or status == "load_error":
        category = status
    elif status != "ok":
        category = "error"
    elif factor == 0:
        category = "not_accepted"  # the plan was not made the accepted way (or the output does not say how)
    else:
        category = "correct" if full else "wrong"

    return {
        "id": item["id"], "type": item.get("type"), "subtype": item.get("subtype"),
        "tables": len(item.get("table_keys") or [1]),
        "status": status, "outcome": out.get("outcome"), "numbers_origin": out.get("numbers_origin"),
        "origin": origin, "weight": factor, "category": category, "reason": out.get("reason"),
        "right": round(right, 6), "kept": round(kept, 6), "score": round(right * kept * factor, 6),
        "score_no_extra_rule": round(right * factor, 6),
        "extra_facts": len(extra), "extra_counted": max(0, len(extra) - (len(required) - len(match))),
        "extra_ids": [mods[i].get("id") for i in extra],
        "verdict": "PASS" if full else "PARTIAL" if match else "FAIL",
        "required": len(required), "matched": sorted(required[r]["id"] for r in match),
        "missing": [f["id"] for f in missing], "near_miss": near,
        "score_value_only": round(right_value_only * kept_of(match_value_only, extra_value_only) * factor, 6),
        "score_same_inputs": round(strict * kept * factor, 6),
        "aux_cells": len(aux_cells), "aux_cells_found": sum(1 for f in aux_cells if f["source_ref"] in mod_refs_all),
        "module_facts": len(every), "main_facts": len(mods), "main_marked": marked or legacy,
        "answer_step": isinstance(out.get("answer"), list),
        "llm_calls": out.get("llm_calls"),
        "chart_expected": expected_chart, "chart_made": made_chart,
        "chart_ok": None if expected_chart is None else (bool(expected_chart) == made_chart),
        "error": out.get("error"),
    }


# ---------------------------------------------------------------- summary

def _mean(xs) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


CATEGORIES = ("correct", "wrong", "not_accepted", "refused", "no_plan", "error", "load_error")
_CATEGORY_KO = {"correct": "정답", "wrong": "숫자 틀림", "not_accepted": "모델이 계획·숫자를 정함(또는 출처 없음)",
                "refused": "계산 거절", "no_plan": "계획 실패", "error": "오류", "load_error": "표 읽기 실패"}
_ORIGIN_KO = {"rules": "규칙만", "model_codes": "모델이 분류만 채움", "model_type": "모델이 유형까지 정함",
              "model_plan": "모델이 계획을 씀", "fallback": "모델이 숫자를 씀", "None": "기록 없음"}


def summarize(rows: list[dict], chart_weight: float = 0.0) -> dict:
    verdicts = Counter(r["verdict"] for r in rows)
    charted = [r for r in rows if r["chart_ok"] is not None]
    groups: dict[str, dict[str, list[dict]]] = {"type": defaultdict(list), "subtype": defaultdict(list),
                                               "tables": defaultdict(list)}
    for r in rows:
        for key in groups:
            groups[key][str(r.get(key))].append(r)
    fact_score = _mean(r["score"] for r in rows)
    chart_acc = _mean(1.0 if r["chart_ok"] else 0.0 for r in charted) if charted else None
    final = fact_score if chart_acc is None else (1 - chart_weight) * fact_score + chart_weight * chart_acc
    ok = [r for r in rows if r["status"] == "ok"]
    counts = [r["module_facts"] for r in ok]
    cut = [r for r in ok if r["extra_counted"]]
    mains = [r["main_facts"] for r in ok]
    by_origin: dict[str, list[dict]] = defaultdict(list)
    for r in ok:
        by_origin[str(r["origin"])].append(r)
    return {
        "items": len(rows), "score": round(fact_score, 4), "final": round(final, 4), "chart_weight": chart_weight,
        "right_unweighted": round(_mean(r["right"] for r in rows), 4),
        "pass": verdicts["PASS"], "partial": verdicts["PARTIAL"], "fail": verdicts["FAIL"],
        "category": {name: n for name in CATEGORIES if (n := sum(1 for r in rows if r["category"] == name))},
        "origin": {name: {"items": len(rs), "weight": rs[0]["weight"], "right": round(_mean(r["right"] for r in rs), 4),
                          "pass": sum(1 for r in rs if r["verdict"] == "PASS")}
                   for name, rs in sorted(by_origin.items())},
        "reason": dict(Counter(str(r["reason"]) for r in rows if r["category"] in ZERO_STATUS).most_common(15)),
        "score_no_extra_rule": round(_mean(r["score_no_extra_rule"] for r in rows), 4),
        "extra": {"items": len(cut), "facts": sum(r["extra_counted"] for r in cut),
                  "kept_mean": round(_mean(r["kept"] for r in ok), 4) if ok else None,
                  "right_but_cut": sum(1 for r in cut if r["verdict"] == "PASS"),
                  "by_subtype": dict(Counter(str(r["subtype"]) for r in cut).most_common())},
        "main_not_marked": sum(1 for r in ok if not r["main_marked"]),
        "without_answer_step": sum(1 for r in ok if r["main_marked"] and not r["answer_step"]),
        "main_facts_median": statistics.median(mains) if mains else None,
        "main_facts_max": max(mains) if mains else None,
        "llm_calls": sum(r["llm_calls"] or 0 for r in rows),
        "items_with_llm_call": sum(1 for r in rows if r["llm_calls"]),
        "status": dict(Counter(r["status"] for r in rows)),
        "outcome": dict(Counter(str(r["outcome"]) for r in ok if r["outcome"] is not None)),
        "numbers_origin": dict(Counter(str(r["numbers_origin"]) for r in ok if r["numbers_origin"] is not None)),
        "scored_but_not_answered": sum(1 for r in rows if r["score"] > 0 and r["outcome"] not in (None, "answered")),
        "score_value_only": round(_mean(r["score_value_only"] for r in rows), 4),
        "score_same_inputs": round(_mean(r["score_same_inputs"] for r in rows), 4),
        "near_miss": dict(Counter(flag for r in rows for flags in r["near_miss"].values() for flag in flags)),
        "aux_cells_found": round(sum(r["aux_cells_found"] for r in rows) / max(1, sum(r["aux_cells"] for r in rows)), 4),
        "module_facts_median": statistics.median(counts) if counts else None,
        "module_facts_max": max(counts) if counts else None,
        "chart": None if chart_acc is None else {
            "items": len(charted), "accuracy": round(chart_acc, 4),
            "expected_yes_made_yes": sum(1 for r in charted if r["chart_expected"] and r["chart_made"]),
            "expected_yes_made_no": sum(1 for r in charted if r["chart_expected"] and not r["chart_made"]),
            "expected_no_made_yes": sum(1 for r in charted if not r["chart_expected"] and r["chart_made"]),
            "expected_no_made_no": sum(1 for r in charted if not r["chart_expected"] and not r["chart_made"]),
        },
        "by": {key: {name: {"items": len(rs), "score": round(_mean(r["score"] for r in rs), 4),
                            "pass": sum(1 for r in rs if r["verdict"] == "PASS")}
                     for name, rs in sorted(by.items())} for key, by in groups.items()},
    }


def print_summary(s: dict) -> None:
    n = s["items"]
    print(f"문항 {n}  점수 {s['score']:.4f}  (배점을 곱하기 전 {s['right_unweighted']:.4f} · "
          f"PASS {s['pass']} / PARTIAL {s['partial']} / FAIL {s['fail']})")
    print("결과: " + " · ".join(f"{_CATEGORY_KO[k]} {v}" for k, v in s["category"].items()))
    if s["category"].get("load_error"):
        print(f"주의: 표를 읽지 못한 문항이 {s['category']['load_error']}건 있음. 코드의 결과가 아니므로 이 점수는 "
              f"측정값으로 쓸 수 없음 (같은 --out으로 다시 실행하면 그 문항만 다시 돎)")
    print("[계획을 만든 방식]")
    for name, g in s["origin"].items():
        print(f"  {_ORIGIN_KO.get(name, name):<22} {g['items']:>4}문항  배점 {g['weight']:.2f}  "
              f"맞은 비율 {g['right']:.4f}  PASS {g['pass']}")
    if s["reason"]:
        print(f"계획 실패·계산 거절 이유 {s['reason']}")
    print(f"모델 호출 {s['llm_calls']}번 ({s['items_with_llm_call']}문항)  ·  답에 쓴 fact 수 중앙값 "
          f"{s['main_facts_median']} 최대 {s['main_facts_max']}")
    e = s["extra"]
    if e["items"]:
        print(f"답과 관계없는 값으로 감점된 문항 {e['items']}건 (값 {e['facts']}개, 그중 정답은 다 맞힌 문항 "
              f"{e['right_but_cut']}건)  ·  감점이 없다면 점수 {s['score_no_extra_rule']:.4f}")
        print(f"  세부 유형별 감점 문항 수 {e['by_subtype']}")
    else:
        print("답과 관계없는 값으로 감점된 문항 없음")
    if s["main_not_marked"]:
        print(f"주의: 답에 쓴 fact 목록(answer)이 없는 출력 {s['main_not_marked']}건")
    if s["without_answer_step"]:
        print(f"주의: 답 추리기 단계 전의 출력(answer 없이 main으로 채점) {s['without_answer_step']}건")
    if s["outcome"] or s["numbers_origin"]:
        print(f"실행 상태 {s['status']}  답변 종류 {s['outcome']}  숫자 출처 {s['numbers_origin']}")
    if s["scored_but_not_answered"]:
        print(f"주의: 답변(answered)이 아닌데 점수를 받은 문항 {s['scored_but_not_answered']}건")
    print(f"참고 점수: 원본 값만 맞으면 인정 {s['score_value_only']:.4f} · 계산에 쓴 칸까지 같아야 인정 {s['score_same_inputs']:.4f}")
    print(f"틀린 정답 가운데 아까운 경우 {s['near_miss'] or '없음'}  (sign 부호만 반대, coarse_round 자릿수만 다름, "
          f"scale_100 비율/퍼센트, value_only 값은 있는데 칸이 다름)")
    print(f"계산에 쓰는 칸을 모듈이 찾은 비율 {s['aux_cells_found']:.4f}  ·  모듈 fact 수 중앙값 {s['module_facts_median']} 최대 {s['module_facts_max']}")
    c = s["chart"]
    if c:
        print(f"차트 가능 여부 정확도 {c['accuracy']:.4f} ({c['items']}문항)  가능→생성 {c['expected_yes_made_yes']} · "
              f"가능→없음 {c['expected_yes_made_no']} · 불가→생성 {c['expected_no_made_yes']} · 불가→없음 {c['expected_no_made_no']}")
    for key, title in (("type", "유형"), ("subtype", "세부 유형"), ("tables", "표 개수")):
        print(f"[{title}]")
        for name, g in s["by"][key].items():
            print(f"  {name:<24} {g['items']:>4}문항  점수 {g['score']:.4f}  PASS {g['pass']}")


def grade_run(items: list[dict], outputs: list[dict], cell_mode: str = "ref", only_present: bool = False,
              weights: dict[str, float] | None = None, legacy: bool = False) -> list[dict]:
    by_id = {o["id"]: o for o in outputs}  # a later line for the same id wins (a re-run)
    if only_present:
        items = [i for i in items if i["id"] in by_id]
    return [grade_item(item, by_id.get(item["id"]), cell_mode, weights, legacy) for item in items]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="grade Phase 1 outputs against an item file")
    p.add_argument("--run", type=Path, help="run folder written by run_phase1.py (outputs.jsonl, meta.json)")
    p.add_argument("--items", type=Path, help="item file; default: the one recorded in <run>/meta.json")
    p.add_argument("--outputs", type=Path, help="outputs.jsonl; default: <run>/outputs.jsonl")
    p.add_argument("--cell-match", choices=("ref", "value"), default="ref",
                   help="ref: same source_ref and value (default); value: the value alone")
    p.add_argument("--chart-weight", type=float, default=0.0, help="share of chart accuracy in the final number (0..1)")
    p.add_argument("--only-present", action="store_true", help="grade only items that have an output (a partial run)")
    p.add_argument("--weight-codes", type=float, default=WEIGHTS["model_codes"],
                   help="weight of an item whose category codes the model filled (0..1)")
    p.add_argument("--weight-type", type=float, default=WEIGHTS["model_type"],
                   help="weight of an item whose type, ranking axis or max/min the model set (0..1)")
    p.add_argument("--legacy", action="store_true", help="whole-pipeline outputs: every fact counts, no weight")
    p.add_argument("--quiet", action="store_true", help="print the final number only")
    p.add_argument("--no-write", action="store_true", help="do not write graded.jsonl / summary.json")
    args = p.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    outputs_path = args.outputs or (args.run / "outputs.jsonl" if args.run else None)
    items_path = args.items
    if items_path is None and args.run and (args.run / "meta.json").exists():
        items_path = Path(json.loads((args.run / "meta.json").read_text(encoding="utf-8"))["items"])
    if not outputs_path or not items_path:
        p.error("give --run, or both --items and --outputs")
    if not (0 <= args.weight_type <= 1 and 0 <= args.weight_codes <= 1):
        p.error("--weight-codes and --weight-type are between 0 and 1")
    weights = {"rules": 1.0, "model_codes": args.weight_codes, "model_type": args.weight_type}
    rows = grade_run(load_jsonl(items_path), load_jsonl(outputs_path), args.cell_match, args.only_present,
                     weights, args.legacy)
    if not rows:
        print("no items to grade", file=sys.stderr)
        print("0.0")
        return 1
    summary = summarize(rows, args.chart_weight)
    summary["weights"] = None if args.legacy else weights
    if not args.no_write:
        out_dir = args.run or outputs_path.parent
        with (out_dir / "graded.jsonl").open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.quiet:
        print_summary(summary)
    print(f"{summary['final']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
