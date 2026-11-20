"""Run Phase 1 on an item file and save what the module returned. Grading is grade.py (this file does not grade).

    python evals/run_phase1.py --backend mock --limit 5                     # no model call: the rules' route only (also a wiring check)
    python evals/run_phase1.py --backend hcx --interval 1                    # the real model
    python evals/run_phase1.py --backend hcx --out evals/runs/base-hcx       # same --out again = continue the run

Each item is answered with kosis_chat.api.summarize_stats_run(question, table_keys) of the Phase 1 clone given by
--phase1 (default: ../kosis-ai-nlp-summary-T1-30 next to this folder). The tables are read from <workspace>/data
(--workspace, else the KOSIS_WORKSPACE environment variable).

The HCX key is read from the environment variable named by --key-env (default HCX_API_KEY). When it is not set and
--env-file is given, that one variable is read from the file with python-dotenv. The key is never printed or saved.

Output: <out>/outputs.jsonl (one line per item: outcome, facts, whether a chart came back) and <out>/meta.json.
These files contain the questions' answers: keep the folder where the defender cannot read it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_ITEMS = HERE / "baseline" / "items.jsonl"
DEFAULT_PHASE1 = HERE.parent / "kosis-ai-nlp-summary-T1-30"


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def git_head(repo: Path) -> str | None:
    """The clone's commit, read from .git files (no git command is run)."""
    try:
        head = (repo / ".git" / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref: "):
            ref = repo / ".git" / head[5:]
            return f"{head[5:]} {ref.read_text(encoding='utf-8').strip()[:12]}" if ref.exists() else head[5:]
        return head[:12]
    except OSError:
        return None


def read_key(name: str, env_file: Path | None) -> str:
    key = os.environ.get(name, "").strip()
    if not key and env_file is not None:
        from dotenv import dotenv_values  # a Phase 1 dependency

        key = (dotenv_values(env_file).get(name) or "").strip()
    return key


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="run Phase 1 on an item file")
    p.add_argument("--items", type=Path, default=DEFAULT_ITEMS)
    p.add_argument("--backend", choices=("mock", "hcx"), default="mock")
    p.add_argument("--model", default=None, help="HCX model; default: Phase 1's DEFAULT_MODEL")
    p.add_argument("--phase1", type=Path, default=DEFAULT_PHASE1, help="the Phase 1 clone to run")
    p.add_argument("--workspace", type=Path, default=None, help="folder that holds data/ (else KOSIS_WORKSPACE)")
    p.add_argument("--out", type=Path, default=None, help="run folder; an existing one is continued")
    p.add_argument("--only", default="", help="comma-separated item ids")
    p.add_argument("--limit", type=int, default=0, help="run only the first N items (a smoke test)")
    p.add_argument("--interval", type=float, default=0.0, help="seconds before a second model call (HCX 429)")
    p.add_argument("--sleep", type=float, default=0.0, help="seconds to wait between items")
    p.add_argument("--retry-errors", action="store_true", help="when continuing, run items that ended in an error again")
    p.add_argument("--key-env", default="HCX_API_KEY", help="name of the environment variable holding the HCX key")
    p.add_argument("--env-file", type=Path, default=None, help="read --key-env from this file when it is not set")
    args = p.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    phase1 = args.phase1.resolve()
    if not (phase1 / "kosis_chat").is_dir():
        print(f"Phase 1 clone not found: {phase1}", file=sys.stderr)
        return 2
    if args.workspace is not None:
        os.environ["KOSIS_WORKSPACE"] = str(args.workspace.resolve())
    workspace = os.environ.get("KOSIS_WORKSPACE", "").strip()
    if not workspace or not (Path(workspace) / "data" / "tables.csv").exists():
        print("give --workspace (or set KOSIS_WORKSPACE): the folder whose data/ holds tables.csv", file=sys.stderr)
        return 2

    # The clone must be the code that runs, not another Phase 1 installed in the environment.
    sys.path[:0] = [str(phase1), str(phase1 / "src")]
    import kosis_ai
    import kosis_chat
    from kosis_chat import api
    from kosis_chat.llm.model import MockClient
    from kosis_chat.schemas.resources import DEFAULT_MODEL, LLMConfig

    for module in (kosis_chat, kosis_ai):
        if phase1 not in Path(module.__file__).resolve().parents:
            print(f"{module.__name__} was imported from {module.__file__}, not from {phase1}", file=sys.stderr)
            return 2

    llm = None
    model = args.model or DEFAULT_MODEL
    if args.backend == "hcx":
        key = read_key(args.key_env, args.env_file)
        if not key:
            print(f"{args.key_env} is not set (shell environment, or --env-file)", file=sys.stderr)
            return 2
        llm = LLMConfig(api_key=key, model=model, provider="hcx")

    items = load_jsonl(args.items)
    only = {x.strip() for x in args.only.split(",") if x.strip()}
    if only:
        items = [i for i in items if i["id"] in only]
    items.sort(key=lambda i: (len(i["table_keys"]), i["table_keys"]))  # one-table pipelines are cached per table
    if args.limit:
        items = items[: args.limit]

    out_dir = args.out or HERE / "runs" / f"{datetime.now():%Y%m%d-%H%M%S}-{args.backend}"
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = out_dir / "outputs.jsonl"
    done = {o["id"] for o in load_jsonl(outputs) if o.get("status") == "ok" or not args.retry_errors}
    meta = {"items": str(args.items.resolve()), "items_sha256": hashlib.sha256(args.items.read_bytes()).hexdigest(),
            "backend": args.backend, "model": model if args.backend == "hcx" else None, "phase1": str(phase1),
            "phase1_head": git_head(phase1), "workspace": workspace, "started": datetime.now().isoformat(timespec="seconds")}
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    todo = [i for i in items if i["id"] not in done]
    print(f"{len(todo)} to run ({len(items) - len(todo)} already done) -> {out_dir}", file=sys.stderr)
    errors = 0
    with outputs.open("a", encoding="utf-8") as f:
        for n, item in enumerate(todo, start=1):
            client = MockClient() if args.backend == "mock" else None
            started = time.perf_counter()
            try:
                out = api.summarize_stats_run(item["question"], list(item["table_keys"]), client=client, llm=llm,
                                              retry_interval=args.interval)
                answer = out.answer
                result = getattr(answer, "result", None)
                record = {"id": item["id"], "status": "ok", "outcome": answer.outcome,
                          "numbers_origin": answer.numbers_origin, "numbers_verified": answer.numbers_verified,
                          "refusal": answer.refusal.reason if answer.refusal else None,
                          "facts": [x.model_dump(exclude_none=True) for x in (result.facts if result else [])],
                          "chart": out.chart is not None, "chart_kind": getattr(out.chart, "kind", None),
                          "text": answer.text}
            except Exception as exc:  # a failed model call is a failed item, not a stopped run
                errors += 1
                record = {"id": item["id"], "status": "error", "error": f"{type(exc).__name__}: {exc}"[:500]}
            record["seconds"] = round(time.perf_counter() - started, 2)
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
            print(f"{n}/{len(todo)} {item['id']} {record.get('outcome') or record['status']} {record['seconds']}s",
                  file=sys.stderr)
            if args.sleep:
                time.sleep(args.sleep)
    print(f"done: {len(todo)} run, {errors} errors -> {out_dir}", file=sys.stderr)
    print(str(out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
