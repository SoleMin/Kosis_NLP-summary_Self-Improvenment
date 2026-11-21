"""Run Phase 1's number path on an item file and save what it returned. Grading is grade.py (this file does not grade).

    python evals/run_calc.py --limit 5                                   # rules only: no model call, no key
    python evals/run_calc.py --backend hcx --interval 1                   # the model fills what the rules leave open
    python evals/run_calc.py --backend hcx --out evals/runs/base-calc     # same --out again = continue the run

Each item is answered with kosis_chat.calc.calculate(question, tables, llm=...) of the Phase 1 clone given by --phase1
(default: ../kosis-ai-nlp-summary-T1-30 next to this folder): defaults -> the rules' plan -> (the model's fill) ->
compute. The judge's rules, the model-written plan, the sentence and the chart are not run. The tables are read from
<workspace>/data (--workspace, else the KOSIS_WORKSPACE environment variable).

A table of 500,000 rows or more is not loaded whole: Phase 1 reads the cells it needs from the file (the clone's
adapters.parquet.BIG_TABLE_ROWS). --big-rows N sets that number for this run: --big-rows 1 sends every table that
can be read from its file that way (a check that both ways give the same results; compare the two runs with
compare_runs.py). The first time, a table's file is scanned once and the scan is saved in <workspace>/data/index.

--backend rules: an item the rules cannot place ends as "no_plan". --backend hcx: the fill step may call the model
(at most twice per item; --model names it, --thinking sets its thinking effort, off by default). Replies that
passed the format check are kept in --cache (default evals/cache/fill.jsonl)
under a hash of the model name and the prompt, and reused: the same code and prompt give the same score, and only
prompts that changed are asked again. --no-cache asks every time.

The key is given to Phase 1 from here, as Phase 3 does it: this script reads the key and passes Phase 1 a settings
object (LLMConfig); Phase 1 reads no .env. The key is looked up in this order: the environment variable named by
--key-env (default HCX_API_KEY), then --env-file, then <workspace>/.env (the Phase 3 repository's .env, the file its
own evals read). Only that one variable is read from the file. --no-env-file (or KOSIS_DOTENV=off) reads no file.
The key is never printed or saved.

An item whose tables could not be read (the file is missing or damaged, the memory ran out) ends as "load_error",
apart from "error" (the calculation raised on the question). A load error says nothing about the code under test:
grade.py reports it on its own and a score with such items in it is not a measurement. Items that ended that way
are run again whenever the same --out is given again; --retry-errors also runs the "error" items again.

The same --out continues a run only where it is the same run: an output is kept when it was written for the same
question over the same tables (each output line carries a hash of them, "asked") and the folder's meta.json names
the same conditions (backend, model, thinking, fill prompt, the clone and its commit, --big-rows, the workspace).
An item whose question changed under the same id is run again, and under other conditions every item is.
(Uncommitted changes in the clone are not among the conditions: after changing code, use a new --out.)

Output: <out>/outputs.jsonl (one line per item: status, origin, the facts and which of them are the answer) and
<out>/meta.json. These files contain the questions' answers: keep the folder where the defender cannot read it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_ITEMS = HERE / "baseline" / "items.jsonl"
DEFAULT_PHASE1 = HERE.parent / "kosis-ai-nlp-summary-T1-30"
DEFAULT_CACHE = HERE / "cache" / "fill.jsonl"
TABLES_KEPT = 48  # prepared tables kept in memory (items are run table by table)
CELLS_KEPT = 1_000_000  # ... and at most this many cells in them (about 1.4 GB); the table in use is always kept
FILE_TABLE_CELLS = 250_000  # what a table read from its file counts as (it holds its index, not its cells)


CONDITIONS = ("backend", "model", "thinking", "fill_prompt", "phase1", "phase1_head", "big_rows", "workspace")


def asked_hash(item: dict) -> str:
    """What an output answers: the question and the tables it was asked over."""
    text = json.dumps([item["question"], list(item["table_keys"])], ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class TableUnread(Exception):
    """A table of the item could not be loaded (cause: what was raised)."""

    def __init__(self, cause: BaseException) -> None:
        super().__init__(str(cause))
        self.cause = cause


def reading_failed(exc: BaseException, model_in_use: bool) -> bool:
    """Whether what the calculation raised is a table that could not be read, not the calculation going wrong on
    the question. A big table's cells are read from its file while it calculates: a file that is gone or locked, a
    disk error, memory running out. With a model in use an OSError can also be a failed call (a dropped
    connection), so there only the errors that cannot be one are taken."""
    if isinstance(exc, (MemoryError, FileNotFoundError, PermissionError)):
        return True
    return isinstance(exc, OSError) and not model_in_use


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def cells_held(resources) -> int:
    """How much a prepared table counts toward CELLS_KEPT: its cells, or FILE_TABLE_CELLS when it is read from the file."""
    table = getattr(resources, "table", None)
    if getattr(table, "file_backed", False):
        return FILE_TABLE_CELLS
    try:
        return len(table.cells)
    except Exception:
        return 0


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
    """The key: the shell's value wins, else that one variable of env_file (None or a missing file: no file read)."""
    key = os.environ.get(name, "").strip()
    if not key and env_file is not None and env_file.is_file():
        from dotenv import dotenv_values  # a Phase 1 dependency

        key = (dotenv_values(env_file).get(name) or "").strip()
    return key


def cached_client(inner, model: str, path: Path | None):
    """The client with its validated replies kept in a file: (model, system, user) -> reply text.

    A reply is stored only after ask_structured reports that it passed the format check (record_event
    "validation_ok"), so a reply that had to be asked again is never replayed. The file holds a hash of the prompt,
    not the prompt."""
    from kosis_chat.llm.model import LLMClient, LLMResult

    class Cached(LLMClient):
        name = inner.name

        def __init__(self) -> None:
            self.hits = self.asked = 0
            self.pending: tuple[str, str] | None = None
            self.store: dict[str, str] = {}
            if path is not None:
                self.store = {row["key"]: row["text"] for row in load_jsonl(path)}

        @staticmethod
        def key(system: str, user: str) -> str:
            return hashlib.sha256("\x00".join((model, system, user)).encode("utf-8")).hexdigest()

        def generate(self, system: str, user: str):
            key = self.key(system, user)
            self.pending = None
            if key in self.store:
                self.hits += 1
                return LLMResult(text=self.store[key], attempts=0)
            self.asked += 1
            result = inner.generate(system, user)
            if result.status == "ok":
                self.pending = (key, result.text)
            return result

        def record_event(self, event: dict) -> None:
            if event.get("type") == "validation_ok" and self.pending is not None and path is not None:
                key, text = self.pending
                self.store[key] = text
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"key": key, "model": model, "text": text}, ensure_ascii=False) + "\n")
            self.pending = None
            inner.record_event(event)

    return Cached()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="run Phase 1's number path on an item file")
    p.add_argument("--items", type=Path, default=DEFAULT_ITEMS)
    p.add_argument("--backend", choices=("rules", "hcx"), default="rules",
                   help="rules: no model call; hcx: the model fills what the rules leave open")
    p.add_argument("--model", default=None, help="HCX model; default: Phase 1's DEFAULT_MODEL")
    p.add_argument("--thinking", choices=("none", "low", "medium", "high"), default="none",
                   help="the model's thinking effort (HCX-007 only); default none")
    p.add_argument("--max-tokens", type=int, default=0,
                   help="output token limit; default 1024, or 5120 with --thinking (thinking tokens count)")
    p.add_argument("--phase1", type=Path, default=DEFAULT_PHASE1, help="the Phase 1 clone to run")
    p.add_argument("--workspace", type=Path, default=None, help="folder that holds data/ (else KOSIS_WORKSPACE)")
    p.add_argument("--out", type=Path, default=None, help="run folder; an existing one is continued")
    p.add_argument("--only", default="", help="comma-separated item ids")
    p.add_argument("--split", choices=("all", "dev", "holdout"), default="all",
                   help="run one part of split.json (next to the item file, made by split_items.py)")
    p.add_argument("--limit", type=int, default=0, help="run only the first N items (a smoke test)")
    p.add_argument("--interval", type=float, default=0.0, help="seconds before a second model call (HCX 429)")
    p.add_argument("--sleep", type=float, default=0.0, help="seconds to wait after an item that called the model")
    p.add_argument("--retry-errors", action="store_true", help="when continuing, run items that ended in an error again")
    p.add_argument("--key-env", default="HCX_API_KEY", help="name of the environment variable holding the HCX key")
    p.add_argument("--env-file", type=Path, default=None,
                   help="read --key-env from this file when the shell does not set it (default: <workspace>/.env)")
    p.add_argument("--no-env-file", action="store_true", help="read no .env file: the shell's variable only")
    p.add_argument("--cache", type=Path, default=DEFAULT_CACHE, help="file of the model's validated replies")
    p.add_argument("--no-cache", action="store_true", help="ask the model every time")
    p.add_argument("--big-rows", type=int, default=None,
                   help="rows from which a table is read from its file, not loaded whole (default: Phase 1's own)")
    args = p.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    phase1 = args.phase1.resolve()
    if not (phase1 / "kosis_chat" / "calc.py").is_file():
        print(f"Phase 1 clone with kosis_chat/calc.py not found: {phase1}", file=sys.stderr)
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
    from kosis_chat import calc
    from kosis_chat.analyze import fill
    from kosis_chat.llm.providers import build_client
    from kosis_chat.schemas.resources import DEFAULT_MODEL, LLMConfig

    for module in (kosis_chat, kosis_ai):
        if phase1 not in Path(module.__file__).resolve().parents:
            print(f"{module.__name__} was imported from {module.__file__}, not from {phase1}", file=sys.stderr)
            return 2
    from kosis_chat.adapters import parquet
    if args.big_rows is not None:
        if args.big_rows < 1 or not hasattr(parquet, "BIG_TABLE_ROWS"):
            print("--big-rows needs a number of 1 or more and a Phase 1 clone that reads big tables from the file",
                  file=sys.stderr)
            return 2
        parquet.BIG_TABLE_ROWS = args.big_rows

    model = args.model or DEFAULT_MODEL
    config = client = None
    if args.backend == "hcx":
        off = args.no_env_file or os.environ.get("KOSIS_DOTENV", "").strip().lower() in ("off", "0", "false", "no")
        env_file = None if off else (args.env_file or Path(workspace) / ".env")
        key = read_key(args.key_env, env_file)
        if not key:
            print(f"{args.key_env} is not set: not in the shell environment" +
                  ("" if env_file is None else f" and not in {env_file}"), file=sys.stderr)
            return 2
        max_tokens = args.max_tokens or (1024 if args.thinking == "none" else 5120)
        config = LLMConfig(api_key=key, model=model, provider="hcx", thinking_effort=args.thinking,
                           max_tokens=max_tokens)  # the settings object Phase 1 is given
        # a reply is the model's under its settings: the cache keeps them apart (plain model name = thinking off)
        label = model if args.thinking == "none" else f"{model}|thinking={args.thinking}"
        client = cached_client(build_client(config), label, None if args.no_cache else args.cache)

    items = load_jsonl(args.items)
    only = {x.strip() for x in args.only.split(",") if x.strip()}
    if only:
        items = [i for i in items if i["id"] in only]
    if args.split != "all":
        split_file = args.items.with_name("split.json")
        if not split_file.is_file():
            print(f"--split {args.split}: {split_file} not found (python evals/split_items.py)", file=sys.stderr)
            return 2
        part = set(json.loads(split_file.read_text(encoding="utf-8"))[args.split])
        items = [i for i in items if i["id"] in part]
    items.sort(key=lambda i: (len(i["table_keys"]), i["table_keys"]))  # the same table's items one after another
    if args.limit:
        items = items[: args.limit]

    part_name = "" if args.split == "all" else f"-{args.split}"
    out_dir = args.out or HERE / "runs" / f"{datetime.now():%Y%m%d-%H%M%S}-calc-{args.backend}{part_name}"
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = out_dir / "outputs.jsonl"
    last = {o["id"]: o for o in load_jsonl(outputs)}  # a later line of an id replaces an earlier one
    asked = {i["id"]: asked_hash(i) for i in items}
    meta = {"path": "calc", "items": str(args.items.resolve()),
            "items_sha256": hashlib.sha256(args.items.read_bytes()).hexdigest(), "backend": args.backend, "split": args.split,
            "model": model if args.backend == "hcx" else None,
            "thinking": args.thinking if args.backend == "hcx" else None, "fill_prompt": fill.VERSION,
            "phase1": str(phase1),
            "phase1_head": git_head(phase1), "workspace": workspace,
            "big_rows": getattr(parquet, "BIG_TABLE_ROWS", None),
            "cache": None if (args.no_cache or args.backend != "hcx") else str(args.cache),
            "started": datetime.now().isoformat(timespec="seconds")}
    try:  # the conditions the outputs already in the folder were made under
        before = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        before = None
    differs = [] if not last else ["meta.json"] if not isinstance(before, dict) else \
        [k for k in CONDITIONS if before.get(k) != meta[k]]
    if differs:
        print(f"the outputs in {out_dir} were made under other conditions ({', '.join(differs)}): every item is run again",
              file=sys.stderr)
    done = {i for i, o in last.items()
            if not differs and o.get("asked") == asked.get(i) and o.get("status") != "load_error"
            and (o.get("status") != "error" or not args.retry_errors)}
    stale = sum(1 for i, o in last.items() if i in asked and o.get("asked") != asked[i])
    if stale and not differs:
        print(f"{stale} outputs in {out_dir} answer another question or tables than the item now has (or do not say): "
              f"run again", file=sys.stderr)
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    tables: OrderedDict[str, object] = OrderedDict()
    cells: dict[str, int] = {}

    def prepared(key: str):
        if key not in tables:
            tables[key] = calc.load_tables([key])[0]
            cells[key] = cells_held(tables[key])
            while len(tables) > 1 and (len(tables) > TABLES_KEPT or sum(cells.values()) > CELLS_KEPT):
                cells.pop(tables.popitem(last=False)[0])
        tables.move_to_end(key)
        return tables[key]

    todo = [i for i in items if i["id"] not in done]
    print(f"{len(todo)} to run ({len(items) - len(todo)} already done) -> {out_dir}", file=sys.stderr)
    errors = unread = 0
    with outputs.open("a", encoding="utf-8") as f:
        for n, item in enumerate(todo, start=1):
            started = time.perf_counter()
            asked_before = client.asked if client is not None else 0
            try:
                try:
                    loaded = [prepared(k) for k in item["table_keys"]]
                except Exception as exc:  # the tables are not there to calculate on: not the calculation's failure
                    raise TableUnread(exc) from None
                result = calc.calculate(item["question"], loaded, llm=config, client=client,
                                        retry_interval=args.interval)
                record = {"id": item["id"], **result.record()}
            except Exception as exc:
                cause = exc.cause if isinstance(exc, TableUnread) else exc
                if isinstance(exc, TableUnread) or reading_failed(exc, client is not None):
                    unread += 1
                    status = "load_error"
                else:  # a failed model call or a crash is a failed item, not a stopped run
                    errors += 1
                    status = "error"
                record = {"id": item["id"], "status": status, "error": f"{type(cause).__name__}: {cause}"[:500]}
            record["asked"] = asked[item["id"]]
            record["seconds"] = round(time.perf_counter() - started, 2)
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
            print(f"{n}/{len(todo)} {item['id']} {record['status']} {record.get('origin') or ''} {record['seconds']}s",
                  file=sys.stderr)
            if args.sleep and client is not None and client.asked > asked_before:
                time.sleep(args.sleep)
    if client is not None:
        print(f"model: {client.asked} asked, {client.hits} from the cache", file=sys.stderr)
    print(f"done: {len(todo)} run, {errors} errors, {unread} with tables that could not be read -> {out_dir}",
          file=sys.stderr)
    print(str(out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
