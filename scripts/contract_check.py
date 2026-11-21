"""What the defender may change in src/kosis_ai/schemas.py: the calculation plan, and only by adding.

The file holds two things. The calculation plan (the operations and the steps of a plan) is what a new kind of
calculation needs. The rest is the format of facts, results and answers, which the grader, Phase 2 and Phase 3 read:
it stays as committed. This check compares the working file with the committed one (git HEAD) by syntax tree, so it
imports nothing from Phase 1 and comments and blank lines do not count.

Rules
- Everything outside the plan part is unchanged, in the same order (imports and the module docstring too).
- A name of the plan part stays: an operation list or a step-shape list keeps every old value, the Step union keeps
  every old step, a plan class keeps its bases and every old field (same default; the type the same or wider).
  A new field has a default, so a plan written before still loads. Validators and methods of a plan class and the two
  wiring functions (_check_order, _shape) may change; the functions keep their arguments.
- New top-level names may be added as a class, a function or a plain assignment. Nothing else is added at the top level,
  and no name is bound twice.

  python scripts/contract_check.py <clone>      exit 0: fine (or the file is unchanged) · 1: problems, one per line
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

SCHEMA = "src/kosis_ai/schemas.py"
VALUE_LISTS = ("AggFn", "CombineFn", "Compare", "PeriodKind", "SpanPart", "STEP_SHAPES")
PLAN_CLASSES = ("PeriodSpec", "TargetSpec", "_Step", "SelectStep", "AggregateStep", "CombineStep", "RankStep",
                "FindStep", "MapStep", "PlanV3")
PLAN_FUNCS = ("_check_order", "_shape")
PLAN = {*VALUE_LISTS, *PLAN_CLASSES, *PLAN_FUNCS, "Step"}


def names_of(node: ast.stmt) -> list[str]:
    """Top-level names a statement binds ([] for an import, an expression or anything else)."""
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        return [node.name]
    if isinstance(node, ast.Assign) and all(isinstance(t, ast.Name) for t in node.targets):
        return [t.id for t in node.targets]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    return []


def dump(node: ast.AST | None) -> str:
    return "" if node is None else ast.dump(node)


def members(node: ast.AST) -> list[ast.AST]:
    """a | b | c -> [a, b, c]"""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return members(node.left) + members(node.right)
    return [node]


def parts(node: ast.AST) -> list[ast.AST]:
    return list(node.elts) if isinstance(node, ast.Tuple) else [node]


def wider(old: ast.AST | None, new: ast.AST | None) -> bool:
    """new accepts everything old accepted: the same, a union or a Literal with more members, or a container of
    such (list[A | B] -> list[A | B | C])."""
    if dump(old) == dump(new):
        return True
    if old is None or new is None:
        return False
    olds, news = members(old), members(new)
    if len(olds) > 1 or len(news) > 1:
        return all(any(wider(o, n) for n in news) for o in olds)
    if isinstance(old, ast.Subscript) and isinstance(new, ast.Subscript) and dump(old.value) == dump(new.value):
        a, b = parts(old.slice), parts(new.slice)
        if isinstance(old.value, ast.Name) and old.value.id == "Literal":
            return {dump(x) for x in a} <= {dump(x) for x in b}
        return len(a) == len(b) and all(wider(x, y) for x, y in zip(a, b))
    return False


def values(node: ast.AST | None) -> set | None:
    """The members of Literal[...] or of a tuple of constants; None when it is neither."""
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "Literal":
        elts = parts(node.slice)
    elif isinstance(node, ast.Tuple):
        elts = node.elts
    else:
        return None
    return {e.value for e in elts} if all(isinstance(e, ast.Constant) for e in elts) else None


def fields(cls: ast.ClassDef) -> dict[str, ast.AnnAssign]:
    return {n.target.id: n for n in cls.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}


def has_default(field: ast.AnnAssign) -> bool:
    value = field.value
    if value is None:
        return False
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "Field":
        if value.args:  # Field(...) is required, Field(0) has a default
            return not (isinstance(value.args[0], ast.Constant) and value.args[0].value is ...)
        return bool({k.arg for k in value.keywords} & {"default", "default_factory"})
    return True


def step_union(node: ast.AST | None) -> tuple[set[str], str] | None:
    """Step = Annotated[A | B | ..., Field(discriminator="op")] -> ({A, B, ...}, the rest as text)"""
    if not (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "Annotated"):
        return None
    inner = parts(node.slice)
    steps = members(inner[0])
    if not all(isinstance(s, ast.Name) for s in steps):
        return None
    return {s.id for s in steps}, "|".join(dump(x) for x in inner[1:])


def check_class(name: str, old: ast.ClassDef, new: ast.AST) -> list[str]:
    if not isinstance(new, ast.ClassDef):
        return [f"{name}: 클래스가 아니게 됨"]
    out = []
    head = lambda c: [dump(x) for x in (*c.bases, *c.keywords, *c.decorator_list)]  # noqa: E731
    if head(old) != head(new):
        out.append(f"{name}: 상속이나 데코레이터가 바뀜")
    was, now = fields(old), fields(new)
    for key, field in was.items():
        if key not in now:
            out.append(f"{name}.{key}: 있던 필드가 없어짐")
        elif not wider(field.annotation, now[key].annotation):
            out.append(f"{name}.{key}: 타입이 좁아지거나 달라짐 (값을 더하는 것만 가능)")
        elif dump(field.value) != dump(now[key].value):
            out.append(f"{name}.{key}: 기본값이나 제약이 바뀜")
    for key, field in now.items():
        if key not in was and not has_default(field):
            out.append(f"{name}.{key}: 새 필드에 기본값이 없음 (전에 만든 계획을 읽지 못함)")
    return out


def problems(old_src: str, new_src: str) -> list[str]:
    """Why the new file breaks the rules above ([] when it does not)."""
    try:
        old, new = ast.parse(old_src), ast.parse(new_src)
    except SyntaxError as e:
        return [f"문법 오류: {e.msg} (line {e.lineno})"]
    out: list[str] = []
    bound = [n for node in new.body for n in names_of(node)]
    twice = sorted({n for n in bound if bound.count(n) > 1})
    if twice:
        out.append(f"최상위 이름을 두 번 정함: {', '.join(twice)}")
    old_names = {n for node in old.body for n in names_of(node)}

    def plan(node: ast.stmt) -> bool:
        return bool(names_of(node)) and all(n in PLAN for n in names_of(node))

    def added(node: ast.stmt) -> bool:
        return bool(names_of(node)) and not any(n in old_names for n in names_of(node))

    label = lambda node: ", ".join(names_of(node)) or f"{type(node).__name__} (line {node.lineno})"  # noqa: E731
    fixed_old = [n for n in old.body if not plan(n)]
    fixed_new = [n for n in new.body if not plan(n) and not added(n)]
    if [dump(n) for n in fixed_old] != [dump(n) for n in fixed_new]:
        was, now = {dump(n): n for n in fixed_old}, {dump(n): n for n in fixed_new}
        changed = sorted({label(n) for d, n in was.items() if d not in now}
                         | {label(n) for d, n in now.items() if d not in was})
        out.append("연산 계획 밖의 내용이 바뀜: " + (", ".join(changed[:8]) if changed else "순서가 바뀜"))

    was = {n: node for node in old.body if plan(node) for n in names_of(node)}
    now = {n: node for node in new.body for n in names_of(node)}
    for name, node in was.items():
        if name not in now:
            out.append(f"{name}: 없어짐")
            continue
        other = now[name]
        if name in VALUE_LISTS:
            a = values(node.value)
            b = values(other.value) if isinstance(other, (ast.Assign, ast.AnnAssign)) else None
            if b is None:
                out.append(f"{name}: 값 목록 형태가 아니게 됨")
            elif not a <= b:
                out.append(f"{name}: 있던 값이 빠짐 ({', '.join(sorted(map(repr, a - b)))})")
        elif name == "Step":
            a = step_union(node.value)
            b = step_union(other.value) if isinstance(other, (ast.Assign, ast.AnnAssign)) else None
            if b is None or a[1] != b[1]:
                out.append("Step: 형태가 바뀜 (op로 구분하는 단계 묶음이어야 함)")
            elif not a[0] <= b[0]:
                out.append(f"Step: 있던 단계가 빠짐 ({', '.join(sorted(a[0] - b[0]))})")
        elif name in PLAN_FUNCS:
            if not isinstance(other, ast.FunctionDef) or dump(node.args) != dump(other.args):
                out.append(f"{name}: 함수의 인자가 바뀜")
        else:
            out.extend(check_class(name, node, other))
    return out


def committed(clone: Path) -> str | None:
    """The file at HEAD of the clone; None when git cannot give it."""
    done = subprocess.run(["git", "-C", str(clone), "show", f"HEAD:{SCHEMA}"], capture_output=True)
    return done.stdout.decode("utf-8") if done.returncode == 0 else None


def check(clone: Path) -> list[str]:
    file = clone / SCHEMA
    old = committed(clone)
    if old is None:
        return [f"{SCHEMA}: 커밋된 내용을 읽지 못함 (git)"]
    if not file.exists():
        return [f"{SCHEMA}: 파일이 없어짐"]
    new = file.read_text(encoding="utf-8")
    if old.replace("\r\n", "\n") == new.replace("\r\n", "\n"):
        return []
    return problems(old, new)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(__doc__)
        return 2
    found = check(Path(args[0]))
    for line in found:
        print(f"- {line}")
    print("schemas 검사: " + (f"실패 {len(found)}건" if found else "통과"))
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
