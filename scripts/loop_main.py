"""The improvement loop: critic, attacker and defender in turn, one loop at a time (started by scripts/loop.sh).

    bash scripts/loop.sh              run loops until a stop condition
    bash scripts/loop.sh --check      check the settings and the two repositories, run nothing
    bash scripts/loop.sh --restore    put back what a stopped run left in the vault
    bash scripts/verify.sh            the defender's pass/fail gate (this file with --verify)

One loop (N is the loop number; AGENTS.md 4장):
   0  script    the baseline's dev part is scored (reused when the clone has not changed since it was scored)
   1  critic    writes the attack hint                      -> loop/attack/hint_N.md
   2  attacker  writes specs: question, cells, formulas     -> loop/attack/spec_N.jsonl
                and, when it found fewer than asked, what it searched -> loop/attack/spec_N.search.md
                fewer than ATTACK_MIN specs from an attacker that ended normally and wrote that report: the final
                verify runs and the goal is complete (kept=done)
   3  critic    first judgment of the specs                 -> loop/attack/judge1_N.jsonl
                a spec without a verdict is asked for again. BAD_RATIO or more rejected: one rewrite, then stop.
   4  script    fills the numbers and checks them           -> evals/golden_N.jsonl (evals/build_items.py)
   5  critic    second judgment of the filled items         -> loop/attack/judge2_N.jsonl; rejected items are dropped
                fewer than ATTACK_MIN left: stop, and the set is not kept as an attack set of later loops
   6  script    scores before the fix: this attack, the earlier attacks
   7  critic    writes what the defender is told            -> loop/defender/brief_N.md
                the script checks that no item leaked into it and appends the aggregate tables
   8  defender  fixes the code in the Phase 1 clone. While it runs, evals/, loop/attack/ and loop/runs/ are moved
                out of the project (the vault) and put back afterwards.
   9  script    files changed outside loop/defender_allow.txt, the shared schema changed beyond adding to the
                calculation plan (scripts/contract_check.py), traces of reaching for the hidden data, verify,
                scores after the fix, the keep rule:
                dev not lower, this attack higher, earlier attacks not lower
  10  critic    judges the change (the diff) and writes the record -> loop/critic/code_N.json
                the record is written as drafts (loop/attack/records_N/), checked for item content by the script and
                only then copied to loop/log.md, loop/learnings.md and docs/current-state.md. A loop without a change
                (all attack items already right, or the defender changed nothing) gets a record too.
  11  script    commit in the clone, or reset; one line in loop/results.tsv; the records committed in this repository

How a run ends is told apart (the kept column of loop/results.tsv, or loop/faults.tsv):
  done     the attacker searched and found fewer than ATTACK_MIN weaknesses, and the final verify passed (GOAL.md)
  stop     a rule of the loop ended it: the judgments left too few attack items
  blocked  an agent wrote loop/blocked.md; a defender's change of that loop is saved as a diff, never committed
  fault    the loop could not be carried out (an agent failed or timed out without its output, an output file is
           malformed, a measurement failed, a protected file changed). No row in loop/results.tsv: one line in
           loop/faults.tsv, and the next run starts the same loop number again.

Git commands that change anything run inside the clone only (git -C <clone>), on its loop branch. This repository
gets one commit per loop of the record files (RECORD_COMMIT=0 turns that off) and is never pushed from here.

Settings are environment variables, read from loop/config.env by scripts/loop.sh (loop/config.example.env lists
them). The three agents are commands (ATTACKER_CMD, DEFENDER_CMD, CRITIC_CMD) that read their prompt on stdin and
run in this folder with LOOP_NUM and LOOP_ROLE set.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path

import contract_check

ROOT = Path(__file__).resolve().parents[1]
RESULTS_HEADER = ["loop", "time", "items", "dev_before", "dev_after", "attack_before", "attack_after", "past_before",
                  "past_after", "verify", "kept", "reason", "commit"]
HIDDEN = ("evals", "loop/attack", "loop/runs")  # moved out of the project while the defender runs
# what an agent's tool call must not name while it is the defender (the data is gone then; this finds the attempt)
REACH = re.compile(r"(?i)(^|[\s\"'=:(\\/])evals([\\/\s\"')]|$)|golden_[\d*{\[]|items\.jsonl|loop[\\/]+(attack|runs)|"
                   r"config\.env|kosis_loop_vault")
WHERE = ("command", "file_path", "path", "pattern", "glob", "notebook_path", "url")  # tool inputs that name a target
EPS = 1e-9
# Phase 1 tests of the cell store, run by the default verify: big tables are read through it, never held whole
STORE_TESTS = ("tests/test_store.py", "tests/test_store_callers.py", "tests/test_parquet_store.py",
               "tests/test_saved_index.py")


# An agent run that ended without its output and with these words at the end of its log has hit a limit of the
# account it runs on (a subscription's usage window, a rate limit), whatever its exit code was.
LIMIT = re.compile(r"(?i)usage limit|rate limit|limit reached|hit your [a-z ]*limit|quota|too many requests|overloaded")
LOG_TAIL = 4000  # bytes of the log's end searched for LIMIT
TIMED_OUT = -1  # the exit code recorded for a run stopped at AGENT_TIMEOUT
RETRY_QUICK = 600  # seconds: a second or later try that worked longer than this and failed again is not retried


# The critic's record files. The critic writes them as drafts (loop/attack/records_N/<name>, where the defender
# cannot read); the script checks the drafts and copies them here.
RECORDS = {"log.md": "loop/log.md", "learnings.md": "loop/learnings.md", "current-state.md": "docs/current-state.md"}
RECORDS_CHECKED = "loop/runs/records_checked.json"  # hashes of the record files as last checked for item content
REASK = 2  # times the critic is asked again for the verdicts it left out
TESTS_BEFORE_TIMEOUT = 900  # seconds for the defender's new tests on the code before the fix


class Stop(Exception):
    """The run ends here by a rule of the loop; the message is the reason. Recorded as a row of loop/results.tsv
    with `kept` in the kept column."""
    kept = "stop"


class Done(Stop):
    """The goal is complete (GOAL.md): the attacker found too few weaknesses and the final verify passed."""
    kept = "done"


class Handoff(Stop):
    """An agent wrote loop/blocked.md: a person decides. Nothing of this loop is committed."""
    kept = "blocked"


class Fault(Stop):
    """The loop could not be carried out: not a result of the loop. No row in loop/results.tsv (one line in
    loop/faults.tsv); the next run starts the same loop number again. kind: agent | timeout | no_output | format |
    measure | script | tamper | leak | setup | crash."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class AgentFailed(Fault):
    """An agent did not do its step (a usage limit, a dropped connection) and waiting did not help."""

    def __init__(self, message: str) -> None:
        super().__init__("agent", message)


class Short(Exception):
    """The attacker ended normally with fewer than ATTACK_MIN specs and its search report (inside Loop only)."""


# ---------------------------------------------------------------- settings

def setting(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


class Settings:
    def __init__(self) -> None:
        self.clone = (ROOT / setting("PHASE1_DIR", "kosis-ai-nlp-summary-T1-30")).resolve()
        self.branch = setting("LOOP_BRANCH", "loop-phase1-calc")
        self.python = setting("PYTHON", sys.executable)
        self.workspace = setting("KOSIS_WORKSPACE")
        self.backend = setting("EVAL_BACKEND", "rules")  # the loop measures without the model (decided 2026-11-21)
        self.eval_args = shlex.split(setting("EVAL_ARGS"))
        self.max_iter = int(setting("MAX_ITER", "10"))
        self.patience = int(setting("PATIENCE", "3"))
        self.attack_count = int(setting("ATTACK_COUNT", "20"))
        self.attack_min = int(setting("ATTACK_MIN", "5"))
        self.bad_ratio = float(setting("BAD_RATIO", "0.5"))
        self.timeout = int(setting("AGENT_TIMEOUT", "5400"))
        self.retry_wait = int(setting("AGENT_RETRY_WAIT", "1800"))  # seconds before a failed agent step runs again
        self.retry_max = int(setting("AGENT_RETRY_MAX", "12"))  # times it runs again (0: stop at the first failure)
        self.record_commit = setting("RECORD_COMMIT", "1") == "1"
        self.verify_cmd = setting("VERIFY_CMD")
        self.bash = setting("LOOP_BASH", "bash")  # scripts/loop.sh sets it: on Windows a bare "bash" may be WSL's
        self.vault = Path(setting("VAULT_DIR", str(Path.home() / ".kosis_loop_vault"))) / hashlib.sha256(
            str(ROOT).encode("utf-8")).hexdigest()[:10]
        self.commands = {role: setting(f"{role.upper()}_CMD") for role in ("attacker", "defender", "critic")}


# ---------------------------------------------------------------- small helpers

def say(text: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {text}", flush=True)


def run(cmd: list[str], cwd: Path = ROOT, env: dict | None = None, log: Path | None = None) -> subprocess.CompletedProcess:
    done = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if log is not None:
        with log.open("a", encoding="utf-8") as f:
            f.write(f"$ {' '.join(cmd)}\n{done.stdout}{done.stderr}\n(exit {done.returncode})\n")
    return done


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
    return rows


def read_specs(path: Path) -> list[dict]:
    """The attacker's specs. A line that is not a JSON object, a spec without an id or an id used twice is a
    malformed output (Fault), never "no specs": too few specs can end the whole run as complete."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise Fault("format", f"출력 형식 오류: {path.name}을 읽지 못함 ({type(exc).__name__})") from None
    specs, seen = [], set()
    for n, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            spec = json.loads(line)
        except ValueError:
            raise Fault("format", f"출력 형식 오류: {path.name}의 {n}번째 줄이 JSON이 아님") from None
        sid = spec.get("id") if isinstance(spec, dict) else None
        if not isinstance(sid, str) or not sid.strip():
            raise Fault("format", f"출력 형식 오류: {path.name}의 {n}번째 줄에 문항 id가 없음")
        if sid in seen:
            raise Fault("format", f"출력 형식 오류: {path.name}에 id {sid}가 두 번 나옴")
        seen.add(sid)
        specs.append(spec)
    return specs


def read_verdicts(path: Path) -> tuple[dict[str, tuple[bool, str]], int]:
    """(verdicts by id, lines that are not a verdict). A verdict is a JSON object with an id and a true or false
    "ok"; a later line of the same id replaces an earlier one."""
    verdicts, bad = {}, 0
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.is_file() else []
    except OSError:
        lines = []
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            row = None
        if isinstance(row, dict) and isinstance(row.get("id"), str) and isinstance(row.get("ok"), bool):
            verdicts[row["id"]] = (row["ok"], str(row.get("reason") or ""))
        else:
            bad += 1
    return verdicts, bad


def blocked_text() -> str:
    """What loop/blocked.md says ('' when there is nothing: the loop goes on)."""
    file = ROOT / "loop" / "blocked.md"
    try:
        return file.read_text(encoding="utf-8", errors="replace").strip() if file.is_file() else ""
    except OSError:
        return ""


def added_lines(new: str, old: str) -> str:
    """The lines of new that old does not have (what a writer added or changed)."""
    have = {line.strip() for line in old.splitlines()}
    return "\n".join(line for line in new.splitlines() if line.strip() and line.strip() not in have)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    except OSError:
        return ""


def attack_sets(below: int | None = None) -> list[Path]:
    """The attack sets of earlier loops (evals/golden_K.jsonl, K < below when given), oldest first."""
    files = [p for p in (ROOT / "evals").glob("golden_*.jsonl") if re.fullmatch(r"golden_(\d+)\.jsonl", p.name)]
    files = [p for p in files if below is None or int(p.stem.split("_")[1]) < below]
    return sorted(files, key=lambda p: int(p.stem.split("_")[1]))


def set_items(files: list[Path]) -> list[dict]:
    items = []
    for file in files:
        try:
            items += load_jsonl(file)
        except (OSError, ValueError):
            pass
    return items


def mark_records() -> None:
    """Remember the record files as they are now: checked for item content."""
    file = ROOT / RECORDS_CHECKED
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps({rel: file_digest(ROOT / rel) for rel in RECORDS.values()}, indent=1), encoding="utf-8")


def records_changed() -> list[str]:
    """Item content in a record file that changed since it was last checked (a person's edit between runs, a file
    put in place by hand). The defender reads these files, so the loop does not start while this finds anything."""
    file = ROOT / RECORDS_CHECKED
    try:
        checked = json.loads(file.read_text(encoding="utf-8")) if file.is_file() else {}
    except (OSError, ValueError):
        checked = {}
    changed = [rel for rel in RECORDS.values() if file_digest(ROOT / rel) != checked.get(rel)]
    if not changed:
        return []
    items = set_items(attack_sets())
    found = [f"{rel} ({', '.join(hits[:3])})" for rel in changed if (hits := leaks(read_text(ROOT / rel), items))]
    if not found:
        mark_records()
    return found


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def fresh(path: Path) -> Path:
    """path, or path-2, path-3 ... when it is taken (a step that runs again keeps the earlier output)."""
    if not path.exists():
        return path
    n = 2
    while path.with_name(f"{path.stem}-{n}{path.suffix}").exists():
        n += 1
    return path.with_name(f"{path.stem}-{n}{path.suffix}")


def set_aside(path: Path, into: Path) -> None:
    """Move a file a stopped run of the same loop left behind out of the way."""
    if path.exists():
        into.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(fresh(into / path.name)))


def file_digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def tail_of(path: Path, size: int = LOG_TAIL) -> str:
    """The end of a log as text ('' when it cannot be read)."""
    try:
        with path.open("rb") as f:
            f.seek(max(0, path.stat().st_size - size))
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def glob_regex(pattern: str) -> re.Pattern:
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out + r"\Z")


def allow_rules(path: Path, loop: int) -> list[tuple[bool, re.Pattern]]:
    """loop/defender_allow.txt: one pattern a line, '!' in front forbids, the last line that matches a path decides.
    {N} is the loop number."""
    rules = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            deny = line.startswith("!")
            rules.append((not deny, glob_regex(line.lstrip("!").strip().replace("{N}", str(loop)))))
    return rules


def allowed(rel: str, rules: list[tuple[bool, re.Pattern]]) -> bool:
    verdict = False
    for allow, pattern in rules:
        if pattern.match(rel):
            verdict = allow
    return verdict


def tool_inputs(log: Path) -> list[str] | None:
    """The targets an agent's tool calls named (commands, paths, patterns), read from a Claude Code stream-json log
    (one JSON object a line, tool calls as "tool_use" blocks of assistant messages). None when the log is not in
    that format."""
    found, seen_json = [], False

    def strings(value) -> None:  # what a call reaches for, not the text it writes
        if isinstance(value, dict):
            found.extend(str(v) for k, v in value.items() if k in WHERE and isinstance(v, (str, int)))

    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        message = event.get("message") if isinstance(event, dict) else None
        if not isinstance(message, dict):
            continue
        seen_json = True
        for block in message.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                strings(block.get("input"))
    return found if seen_json else None


def reached(log: Path) -> list[str] | None:
    """Tool inputs of the defender that name the hidden data. None: the log could not be read as tool calls."""
    inputs = tool_inputs(log)
    if inputs is None:
        return None
    return [text[:200] for text in inputs if REACH.search(text)]


def squash(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def leaks(text: str, items: list[dict], window: int = 12) -> list[str]:
    """What of the items shows in a text meant for the defender: a stretch of a question, a table, an answer."""
    body, found = squash(text), []
    for item in items:
        q = squash(item.get("question", ""))
        if any(q[i:i + window] in body for i in range(max(1, len(q) - window + 1))) and len(q) >= window:
            found.append(f"{item['id']}: 질문 문구")
        for key in item.get("table_keys") or []:
            if any(len(part) >= 6 and part.lower() in body for part in (key, key.split("_", 1)[-1])):
                found.append(f"{item['id']}: 표 {key}")
        for fact in item.get("required_facts") or []:
            v = fact.get("value")
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                continue
            forms = set()
            if abs(v) >= 1000:
                forms.add(f"{int(abs(v)):,}")
            if abs(v) >= 10000:  # shorter plain numbers could be a year
                forms.add(str(int(abs(v))))
            if v != int(v) and abs(v) >= 10:
                forms.add(f"{abs(v):.2f}")
            if any(form in body for form in forms):
                found.append(f"{item['id']}: 정답 값")
    return sorted(set(found))


def aggregate_md(title: str, summary: dict, subtypes: bool) -> str:
    """The numbers of a graded run the defender may see: counts and means, nothing of an item."""
    names = {"correct": "정답", "wrong": "숫자 틀림", "not_accepted": "모델이 계획·숫자를 정함", "refused": "계산 거절",
             "no_plan": "계획 실패", "error": "오류"}
    origin = {"rules": "규칙만", "model_codes": "모델이 분류만 채움", "model_type": "모델이 유형까지 정함"}
    lines = [f"### {title}", "", f"- 문항 {summary['items']}건, 점수 {summary['score']:.4f}",
             "- 결과: " + ", ".join(f"{names.get(k, k)} {v}" for k, v in summary.get("category", {}).items())]
    for name, g in (summary.get("origin") or {}).items():
        lines.append(f"- {origin.get(name, name)}: {g['items']}건, 맞은 비율 {g['right']:.4f}")
    if summary.get("reason"):
        lines.append("- 계획 실패·계산 거절 이유: " + ", ".join(f"{k} {v}" for k, v in summary["reason"].items()))
    extra = summary.get("extra") or {}
    lines.append(f"- 답과 관계없는 값으로 감점된 문항: {extra.get('items', 0)}건")
    lines += ["", "| 유형 | 문항 | 점수 |", "|---|---|---|"]
    for key in ("type", "tables") + (("subtype",) if subtypes else ()):
        label = {"type": "", "tables": "표 개수 ", "subtype": "세부 "}[key]
        lines += [f"| {label}{name} | {g['items']} | {g['score']:.4f} |" for name, g in summary["by"][key].items()]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- the vault

class Vault:
    """evals/, loop/attack/ and loop/runs/ are moved out of the project while the defender runs."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def _slot(self, rel: str) -> Path:
        return self.folder / rel.replace("/", "__")

    @staticmethod
    def _move(src: Path, dst: Path) -> None:
        last = None
        for _ in range(6):  # a sync client may hold a file for a moment
            try:
                shutil.move(str(src), str(dst))
                return
            except OSError as exc:
                last = exc
                time.sleep(2)
        raise Fault("setup", f"could not move {src} -> {dst}: {last}")

    def hide(self) -> None:
        if self.folder.exists() and any(self.folder.iterdir()):
            raise Fault("setup", f"the vault is not empty: {self.folder} (bash scripts/loop.sh --restore)")
        self.folder.mkdir(parents=True, exist_ok=True)
        try:
            for rel in HIDDEN:
                if (ROOT / rel).exists():
                    self._move(ROOT / rel, self._slot(rel))
        except Stop:
            self.restore()
            raise

    def path(self, rel: str) -> Path:
        """Where a hidden path is right now."""
        for hidden in HIDDEN:
            if rel == hidden or rel.startswith(hidden + "/"):
                slot = self._slot(hidden)
                if slot.exists():
                    return slot / rel[len(hidden):].lstrip("/")
        return ROOT / rel

    def restore(self) -> list[str]:
        """Put everything back. Returns the hidden paths something was created at meanwhile (set aside, reported)."""
        intruders = []
        if not self.folder.exists():
            return intruders
        for rel in HIDDEN:
            slot = self._slot(rel)
            if not slot.exists():
                continue
            home = ROOT / rel
            if home.exists():
                aside = fresh(home.with_name(f"{home.name}.made-while-hidden"))
                self._move(home, aside)
                intruders.append(f"{rel} -> {aside.name}")
            home.parent.mkdir(parents=True, exist_ok=True)
            self._move(slot, home)
        return intruders


# ---------------------------------------------------------------- the loop

class Loop:
    def __init__(self, s: Settings, n: int) -> None:
        self.s, self.n = s, n
        self.dir = ROOT / "loop" / "runs" / f"loop-{n}"
        self.log = self.dir / "loop.log"
        self.attack = ROOT / "loop" / "attack"
        self.golden = ROOT / "evals" / f"golden_{n}.jsonl"
        self.vault = Vault(s.vault)
        self.row = {k: "-" for k in RESULTS_HEADER} | {"loop": str(n)}
        self.state: dict = {"loop": n, "steps": []}
        self.final_items = False  # evals/golden_N.jsonl holds the items both judgments passed (ATTACK_MIN or more)
        self.after_stop = ""  # a reason to stop once this loop is recorded
        self.last_exit = 0  # the exit code of the last agent run (TIMED_OUT: stopped at AGENT_TIMEOUT)

    # ---- plumbing

    def note(self, step: str, **fields) -> None:
        self.state["steps"].append({"step": step, "time": datetime.now().isoformat(timespec="seconds"), **fields})
        target = self.vault.path(f"loop/runs/loop-{self.n}/state.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")
        say(f"loop {self.n} · {step}" + ("".join(f" · {k}={v}" for k, v in fields.items()) if fields else ""))

    def git(self, *args: str) -> subprocess.CompletedProcess:
        return run(["git", "-C", str(self.s.clone), *args])

    def clone_changes(self) -> list[str]:
        out = self.git("status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
        paths, parts, i = [], out.split("\0"), 0
        while i < len(parts):
            entry = parts[i]
            if len(entry) > 3:
                paths.append(entry[3:])
                if entry[0] in "RC":  # a rename lists its old path next
                    i += 1
                    if i < len(parts) and parts[i]:
                        paths.append(parts[i])
            i += 1
        return sorted(set(p.replace("\\", "/") for p in paths))

    def revert(self) -> None:
        self.git("reset", "-q", "--hard", "HEAD")
        self.git("clean", "-qfd")

    def prompt(self, name: str, **values) -> str:
        text = (ROOT / "loop" / "prompts" / f"{name}.md").read_text(encoding="utf-8")
        values |= {"LOOP_NUM": self.n, "ATTACK_COUNT": self.s.attack_count, "ATTACK_MIN": self.s.attack_min,
                   "CLONE": self.s.clone.name}
        for key, value in values.items():
            text = text.replace("{{" + key + "}}", str(value))
        return text

    def agent(self, role: str, step: str, prompt: str, hidden: bool = False, output: Path | None = None,
              strict: bool = False, reset=None) -> Path:
        """Run one agent with the prompt on stdin. Returns its log. hidden: the log is written inside the vault.

        A run that failed is run again after AGENT_RETRY_WAIT seconds, at most AGENT_RETRY_MAX times (a subscription's
        usage limit opens again after some hours); then AgentFailed ends the run without recording the loop.
        Failed means: it did not write `output` (the file the step is for) and it ended with a failure exit code or
        with the words of a limit at the end of its log. strict (the defender, whose output is the clone): any
        failure exit code, or those words with the clone unchanged. A run stopped at AGENT_TIMEOUT has not failed in
        this sense (it worked the whole time) and is not run again. reset() is called before each new try."""
        tries = 0
        while True:
            before = file_digest(output) if output is not None else None
            name = step if tries == 0 else f"{step}-retry{tries}"
            log, final, code, seconds = self.agent_once(role, name, prompt, hidden)
            self.last_exit = code
            wrote = output is not None and output.exists() and file_digest(output) != before
            limited = bool(LIMIT.search(tail_of(log)))
            if strict:
                failed = code not in (0, TIMED_OUT) or (limited and not self.clone_changes())
            else:
                failed = not wrote and (code not in (0, TIMED_OUT) or limited)
            if not failed:
                return final
            why = f"{role}가 {name} 단계를 하지 못함 (exit {code}{', 로그에 한도 문구' if limited else ''})"
            if tries >= self.s.retry_max or (tries > 0 and seconds > RETRY_QUICK):
                raise AgentFailed(f"{why}. 사용량 한도일 수 있음. 다시 실행하면 루프 {self.n}부터 다시 함 (로그 {final})")
            tries += 1
            self.note(f"{why}: {self.s.retry_wait}초 뒤 다시 실행", attempt=f"{tries}/{self.s.retry_max}")
            self.pause(self.s.retry_wait)
            if reset is not None:
                reset()

    def pause(self, seconds: int) -> None:
        """Wait before a new try; loop/STOP ends the wait and the run."""
        end = time.time() + seconds
        while time.time() < end:
            if (ROOT / "loop" / "STOP").exists():
                raise AgentFailed(f"loop/STOP 파일이 있음 (루프 {self.n}에서 다시 실행을 기다리던 중)")
            time.sleep(min(15.0, max(0.0, end - time.time())))

    def agent_once(self, role: str, step: str, prompt: str, hidden: bool) -> tuple[Path, Path, int, float]:
        """One run of an agent. Returns (its log now, where the log is once the vault is put back, exit code, seconds)."""
        command = self.s.commands[role]
        rel = f"loop/runs/loop-{self.n}/{step}"
        base = self.vault.path(rel) if hidden else ROOT / rel
        base.parent.mkdir(parents=True, exist_ok=True)
        log = fresh(base.with_suffix(".log"))
        log.with_suffix(".prompt.md").write_text(prompt, encoding="utf-8")
        env = os.environ | {"LOOP_NUM": str(self.n), "LOOP_ROLE": role}
        self.note(f"{step} 시작", role=role)
        started = time.time()
        with log.open("wb") as out:
            try:
                code = subprocess.run([self.s.bash, "-c", command], cwd=ROOT, env=env, input=prompt.encode("utf-8"),
                                      stdout=out, stderr=subprocess.STDOUT, timeout=self.s.timeout).returncode
            except subprocess.TimeoutExpired:
                code = TIMED_OUT
        seconds = time.time() - started
        self.note(f"{step} 끝", exit=code, seconds=int(seconds))
        if code == 127:
            raise Fault("setup", f"{role}: 명령을 찾지 못함 ({log})")
        return log, (ROOT / rel).with_name(log.name), code, seconds

    def need(self, file: Path, role: str, what: str) -> None:
        """A step's output must be there once its agent has run. Going on without it would turn a run that did not
        work (a timeout, an agent that wrote nothing) into a result of the loop."""
        if not file.is_file():
            self.missing(role, what, file.relative_to(ROOT).as_posix())

    def missing(self, role: str, what: str, where: str) -> None:
        if self.last_exit == TIMED_OUT:
            raise Fault("timeout", f"{role}가 시간 초과({self.s.timeout}초)로 끝났고 {what}({where})이 없음")
        raise Fault("no_output", f"{role}가 {what}({where})을 쓰지 않고 끝남 (exit {self.last_exit})")

    @staticmethod
    def records_now() -> dict[str, bytes | None]:
        return {rel: ((ROOT / rel).read_bytes() if (ROOT / rel).is_file() else None) for rel in RECORDS.values()}

    def records_back(self, before: dict[str, bytes | None], who: str) -> None:
        """The record files are written by the script alone, from checked drafts: what an agent changed in them
        directly is put back."""
        for rel, data in before.items():
            path = ROOT / rel
            if (path.read_bytes() if path.is_file() else None) == data:
                continue
            if data is None:
                path.unlink()
            else:
                path.write_bytes(data)
            self.note(f"{who}가 {rel}을 직접 고쳐서 되돌림 (기록은 초안으로만 받음)")

    def shelve(self, name: str) -> str:
        """Save the clone's uncommitted change as loop/runs/loop-N/<name> (a diff a person can read and apply) and
        reset the clone. Returns where it is, '' when the clone had no change."""
        if not self.clone_changes():
            return ""
        self.git("add", "-A")
        file = fresh(self.dir / name)
        file.parent.mkdir(parents=True, exist_ok=True)
        # bytes as git wrote them, so that `git apply` takes the file whatever the line endings are
        file.write_bytes(subprocess.run(["git", "-C", str(self.s.clone), "diff", "--cached", "--binary"],
                                        capture_output=True).stdout)
        self.revert()
        return file.relative_to(ROOT).as_posix()

    def handoff(self, when: str) -> None:
        """loop/blocked.md has text: an agent handed the loop to a person (AGENTS.md 6장). The loop ends here and
        nothing of it is committed; a change in the clone is saved as a diff next to the reason."""
        if not blocked_text():
            return
        saved = self.shelve("blocked.diff")
        raise Handoff(f"{when} loop/blocked.md에 내용이 있음" + (f". 클론의 수정은 커밋하지 않고 {saved}에 남김" if saved else ""))

    def guarded(self, role: str, step: str, prompt: str, may_write: tuple[str, ...], staged: bool = False,
                output: Path | None = None) -> Path:
        """An attacker or critic run: afterwards the clone and the protected files must be as they were.
        staged: the defender's change is staged in the clone (the review); the work tree must still equal it.
        output: the file the step is for (see agent(): a run that failed without writing it is run again)."""
        before, records = self.fingerprint(may_write), self.records_now()
        log = self.agent(role, step, prompt, output=output)
        self.records_back(records, role)
        if staged:
            untracked = self.git("ls-files", "--others", "--exclude-standard").stdout.strip()
            if self.git("diff", "--quiet").returncode != 0 or untracked:
                self.git("checkout", "-q", "--", ".")
                self.git("clean", "-qfd")
                self.note(f"{step}: {role}가 클론을 고쳐서 방어자의 수정 상태로 되돌림")
        elif self.clone_changes():
            self.revert()
            self.note(f"{step}: {role}가 클론을 고쳐서 되돌림")
        after = self.fingerprint(may_write)
        changed = sorted(k for k in before if before[k] != after.get(k))
        changed += sorted(k for k in after if k not in before and k.startswith(("evals/", "scripts/", ".claude/")))
        if changed:
            raise Fault("tamper", f"{role}가 보호 파일을 바꿈: {', '.join(changed[:8])} (git으로 확인 후 되돌릴 것)")
        self.handoff(f"{step}({role}) 뒤")
        return log

    def fingerprint(self, may_write: tuple[str, ...]) -> dict[str, str]:
        """Hashes of the files no attacker or critic may change (loop/config.env: size and time only, never read)."""
        out: dict[str, str] = {}
        roots = ["evals", "scripts", ".claude", "docs/ATTACKER.md", "docs/DEFENDER.md", "docs/CRITIC.md", "AGENTS.md",
                 "GOAL.md", "loop/prompts", "loop/defender_allow.txt", "loop/results.tsv", "loop/faults.tsv", "loop/attack",
                 "loop/defender", "loop/critic"]
        for root in roots:
            path = ROOT / root
            files = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else []
            for f in files:
                rel = f.relative_to(ROOT).as_posix()
                if "__pycache__" in rel or rel.startswith(("evals/runs/", "evals/cache/")) or rel.startswith(may_write):
                    continue
                out[rel] = hashlib.sha256(f.read_bytes()).hexdigest()
        config = ROOT / "loop" / "config.env"
        if config.exists():
            stat = config.stat()
            out["loop/config.env"] = f"{stat.st_size}:{stat.st_mtime_ns}"
        return out

    # ---- measuring

    def measure(self, tag: str, items: Path, split: str | None = None) -> dict:
        out = fresh(ROOT / "evals" / "runs" / f"loop-{self.n}" / tag)
        cmd = [self.s.python, "evals/run_eval.py", "--items", str(items), "--out", str(out), "--backend", self.s.backend,
               "--phase1", str(self.s.clone), "--quiet", *(["--split", split] if split else []), *self.s.eval_args]
        errors, summary, last = 0, None, ""
        for attempt in (1, 2):
            done = run(cmd + (["--retry-errors"] if attempt == 2 else []), log=self.log)
            last = (done.stdout.strip().splitlines() or [""])[-1]
            file = out / "summary.json"
            summary = json.loads(file.read_text(encoding="utf-8")) if done.returncode == 0 and file.exists() else None
            errors = (summary or {}).get("category", {}).get("error", 0) if summary else -1
            if errors == 0 or (summary is not None and self.errors_count):
                break  # with the rules alone an item's error is the code raising: the same again on a second run
        if summary is None:
            return {"score": None, "errors": -1, "items": 0, "summary": None, "run": str(out), "last": last}
        return {"score": float(summary["final"]), "errors": errors, "items": summary["items"], "summary": summary,
                "run": str(out)}

    def measure_dev(self, tag: str) -> dict:
        """The baseline's dev part. A score of the same clone commit under the same settings is reused."""
        head = self.git("rev-parse", "HEAD").stdout.strip()
        clean = not self.clone_changes()
        cache = self.dev_cache()
        if clean and (hit := cache.get(self.dev_key(head))) and Path(hit["run"], "summary.json").exists():
            summary = json.loads(Path(hit["run"], "summary.json").read_text(encoding="utf-8"))
            return hit | {"summary": summary, "reused": True}
        got = self.measure(tag, ROOT / "evals" / "baseline" / "items.jsonl", "dev")
        if clean:
            self.remember_dev(got)
        return got

    def dev_key(self, head: str) -> str:
        return hashlib.sha256(json.dumps([head, self.s.backend, self.s.eval_args]).encode()).hexdigest()[:16]

    @staticmethod
    def dev_cache() -> dict:
        file = ROOT / "evals" / "runs" / "dev_scores.json"
        return json.loads(file.read_text(encoding="utf-8")) if file.exists() else {}

    def remember_dev(self, got: dict) -> None:
        """Keep a dev score under the clone's commit (call when the work tree is that commit)."""
        if self.broken(got):
            return
        cache = self.dev_cache()
        cache[self.dev_key(self.git("rev-parse", "HEAD").stdout.strip())] = {k: got[k] for k in ("score", "errors", "items", "run")}
        (ROOT / "evals" / "runs" / "dev_scores.json").write_text(json.dumps(cache, indent=1), encoding="utf-8")

    def measure_past(self, tag: str) -> dict | None:
        """Every earlier loop's attack items as one score (mean over items). None when there are none."""
        parts = [self.measure(f"{tag}-{f.stem}", f) for f in attack_sets(self.n) if load_jsonl(f)]
        if not parts:
            return None
        if any(p["score"] is None for p in parts):
            return {"score": None, "errors": -1, "items": 0}
        total = sum(p["items"] for p in parts)
        return {"score": sum(p["score"] * p["items"] for p in parts) / total, "errors": sum(p["errors"] for p in parts),
                "items": total}

    @property
    def errors_count(self) -> bool:
        """Whether an item that ended in an error is a graded item (0 points, category "error") and the measurement
        stands. With the rules alone (EVAL_BACKEND=rules) nothing outside the code can fail, so an error is the code
        raising on that question: a weakness to fix, and an attack that makes the code raise is a valid attack
        (decided 2026-11-21). With a model an error can be a failed call, and such a measurement is not used."""
        return self.s.backend == "rules"

    def broken(self, *parts: dict | None) -> bool:
        """A measurement that cannot be used: no score at all, or (not with the rules alone) items in error."""
        return any(p is not None and (p["score"] is None or (p["errors"] != 0 and not self.errors_count)) for p in parts)

    # ---- the steps

    def verdicts(self, step: str, file: Path, ids: list[str], asked: str,
                 may: tuple[str, ...]) -> dict[str, tuple[bool, str]]:
        """The critic's verdict of every id in `file`. An id without a verdict is not counted as rejected (twelve
        unjudged specs out of twenty would send the attacker back to rewrite): the critic is asked again for those,
        REASK times, and the rejected share is computed from complete verdicts only. asked: the file the ids are in."""
        self.need(file, "평가자", "판정 파일")
        for again in range(REASK + 1):
            got, unreadable = read_verdicts(file)
            missing = [i for i in ids if i not in got]
            extra = len(set(got) - set(ids))
            if not missing:
                if extra or unreadable:
                    self.note(f"{step}: 판정 파일에 쓰지 않는 줄이 있음", unknown_ids=extra, unreadable=unreadable)
                return {i: got[i] for i in ids}
            if again == REASK:
                raise Fault("format", f"출력 형식 오류: 평가자의 판정({file.name})에 {len(missing)}건이 빠져 있고 "
                                      f"다시 요청해도 채워지지 않음: {', '.join(missing[:8])}")
            self.note(f"{step}: 판정이 없는 문항을 평가자에게 다시 요청", missing=len(missing), unreadable=unreadable)
            text = self.prompt("critic_judge_rest", JUDGE_FILE=file.relative_to(ROOT).as_posix(), ASKED_FILE=asked,
                               MISSING="\n".join(f"- {i}" for i in missing))
            self.guarded("critic", f"{step}-rest{again + 1}", text, may, output=file)
        raise Stop("unreachable")

    def attack_specs(self, dev: dict) -> list[dict]:
        stale = self.dir / "stale"
        search = self.attack / f"spec_{self.n}.search.md"
        for path in (self.attack / f"hint_{self.n}.md", self.attack / f"spec_{self.n}.jsonl", search,
                     self.attack / f"spec_{self.n}.try1.jsonl", self.attack / f"judge1_{self.n}.try1.jsonl",
                     self.attack / f"judge1_{self.n}.jsonl", self.attack / f"judge2_{self.n}.jsonl",
                     self.attack / f"accepted_{self.n}.jsonl", self.attack / f"built_{self.n}.jsonl",
                     self.attack / f"kept_{self.n}.jsonl", self.attack / f"records_{self.n}", self.golden,
                     self.golden.with_suffix(".report.json"), ROOT / "loop" / "defender" / f"brief_{self.n}.md",
                     ROOT / "loop" / "defender" / f"notes_{self.n}.md", ROOT / "loop" / "critic" / f"code_{self.n}.json"):
            set_aside(path, stale)
        self.attack.mkdir(parents=True, exist_ok=True)
        (self.dir / "state.md").write_text(
            f"# 루프 {self.n} 시작 상태\n\n" + aggregate_md("기준 데이터(판정용)", dev["summary"], True), encoding="utf-8")
        may = ("loop/attack/", "loop/critic/")
        hint = self.attack / f"hint_{self.n}.md"
        self.guarded("critic", "1-hint", self.prompt("critic_hint"), may, output=hint)
        self.need(hint, "평가자", "공격 힌트")

        spec_file, judge_file = self.attack / f"spec_{self.n}.jsonl", self.attack / f"judge1_{self.n}.jsonl"
        low = self.s.attack_min
        for attempt in (1, 2):
            name = "attacker" if attempt == 1 else "attacker_rewrite"
            set_aside(search, stale)
            before = file_digest(spec_file)
            self.guarded("attacker", f"2-attack-{attempt}", self.prompt(name),
                         (f"loop/attack/spec_{self.n}.jsonl", f"loop/attack/spec_{self.n}.search.md"), output=spec_file)
            code, report = self.last_exit, read_text(search).strip()
            wrote = spec_file.is_file() and file_digest(spec_file) != before
            if wrote or (attempt == 2 and code == 0 and spec_file.is_file()):  # (a rewrite may leave it as it was)
                specs = read_specs(spec_file)
            elif attempt == 1 and code == 0 and report:
                specs = []  # it ended normally and says what it searched: it found nothing to attack
            else:
                self.missing("공격자", "새로 쓴 공격 문항", f"loop/attack/{spec_file.name}")
            ids = [s["id"] for s in specs]
            self.row["items"] = str(len(specs))
            if len(specs) < low:
                # Too few weaknesses ends the whole run as complete (GOAL.md), so it must be the attacker's own
                # statement after a normal run, not what a run that broke off left behind.
                if code == 0 and report:
                    raise Short(f"공격자가 유효한 약점을 {len(specs)}건만 찾음 ({low}건 미만, 탐색 보고 "
                                f"loop/attack/{search.name})")
                kind = "timeout" if code == TIMED_OUT else "agent" if code != 0 else "no_output"
                how = "시간 초과로 끝남" if code == TIMED_OUT else f"비정상 종료함 (exit {code})" if code != 0 else \
                    f"탐색 보고({search.name})를 남기지 않음"
                raise Fault(kind, f"공격자가 문항을 {len(specs)}건만 쓰고 {how}: 약점이 더 없는 것인지 실행이 잘못된 "
                                  f"것인지 알 수 없음")
            if len(specs) < self.s.attack_count:
                self.note("공격 문항이 목표 건수보다 적음", specs=len(specs), asked=self.s.attack_count, report=bool(report))
            set_aside(judge_file, stale)
            self.guarded("critic", f"3-judge1-{attempt}", self.prompt("critic_judge1"), may, output=judge_file)
            verdicts = self.verdicts(f"3-judge1-{attempt}", judge_file, ids, f"loop/attack/{spec_file.name}", may)
            bad = sum(1 for ok, _ in verdicts.values() if not ok)
            self.note("1차 판정", specs=len(specs), rejected=bad)
            if bad / len(specs) < self.s.bad_ratio:
                good = [s for s in specs if verdicts[s["id"]][0]]
                if len(good) < low:
                    raise Stop(f"심사 탈락: 1차 판정을 통과한 공격 문항이 {len(good)}건뿐임 ({len(specs)}건 가운데, {low}건 미만)")
                return good
            if attempt == 2:
                raise Stop(f"심사 탈락: 다시 쓴 공격 문항도 절반 이상이 부적절함 (1차 판정에서 {len(specs)}건 가운데 {bad}건)")
            shutil.copy(spec_file, self.attack / f"spec_{self.n}.try1.jsonl")
            shutil.copy(judge_file, self.attack / f"judge1_{self.n}.try1.jsonl")
        raise Stop("unreachable")

    def attack_items(self, specs: list[dict]) -> list[dict]:
        accepted = self.attack / f"accepted_{self.n}.jsonl"
        write_jsonl(accepted, specs)
        done = run([self.s.python, "evals/build_items.py", "--spec", str(accepted), "--out", str(self.golden)], log=self.log)
        try:
            items = load_jsonl(self.golden) if self.golden.exists() else []
        except ValueError:
            raise Fault("script", f"값을 채운 문항 파일을 읽지 못함 ({self.golden.name}, {self.log})") from None
        self.note("값 채우기", built=len(items), specs=len(specs), exit=done.returncode)
        if done.returncode not in (0, 1) or (done.returncode != 0 and items):  # 1 without items: every spec rejected
            raise Fault("script", f"값 채우기·검증 스크립트가 실패함 (exit {done.returncode}, {self.log})")
        low = self.s.attack_min
        if items:
            shutil.copy(self.golden, self.attack / f"built_{self.n}.jsonl")
        if len(items) < low:
            raise Stop(f"심사 탈락: 스크립트의 값 채우기·검증을 통과한 공격 문항이 {len(items)}건뿐임 ({low}건 미만, "
                       f"이유는 evals/{self.golden.stem}.report.json)")
        may = ("loop/attack/", "loop/critic/")
        judge_file = self.attack / f"judge2_{self.n}.jsonl"
        self.guarded("critic", "5-judge2", self.prompt("critic_judge2"), may, output=judge_file)
        verdicts = self.verdicts("5-judge2", judge_file, [i["id"] for i in items], f"evals/{self.golden.name}", may)
        kept = [i for i in items if verdicts[i["id"]][0]]
        self.note("2차 판정", built=len(items), kept=len(kept))
        self.row["items"] = str(len(kept))
        if len(kept) < low:
            # Kept for diagnosis only. evals/golden_N.jsonl is what later loops score as an earlier attack: a set
            # under the minimum is not one (main() moves the built file away, final_items stays False).
            write_jsonl(self.attack / f"kept_{self.n}.jsonl", kept)
            raise Stop(f"심사 탈락: 2차 판정을 통과한 공격 문항이 {len(kept)}건뿐임 ({low}건 미만). 진단용으로 "
                       f"loop/attack/kept_{self.n}.jsonl에 남기고 지난 공격 세트로는 쓰지 않음")
        write_jsonl(self.golden, kept)
        self.final_items = True
        return kept

    def brief(self, items: list[dict], dev: dict, attack: dict) -> None:
        file = ROOT / "loop" / "defender" / f"brief_{self.n}.md"
        file.parent.mkdir(parents=True, exist_ok=True)
        may = ("loop/defender/", "loop/critic/", "loop/attack/")
        found: list[str] = []
        for attempt in (1, 2):
            set_aside(file, self.dir / "stale")
            name = "critic_brief" if attempt == 1 else "critic_brief_rewrite"
            text = self.prompt(name, ATTACK_RUN=Path(attack["run"]).relative_to(ROOT).as_posix(),
                               LEAKS="\n".join(f"- {x}" for x in found))
            self.guarded("critic", f"7-brief-{attempt}", text, may, output=file)
            self.need(file, "평가자", "방어자용 약점 설명")
            found = leaks(file.read_text(encoding="utf-8"), items)
            self.note("약점 설명 검사", leaks=len(found))
            if not found:
                break
        else:
            set_aside(file, self.dir / "held")  # loop/defender/ is what defenders read
            raise Fault("leak", f"방어자용 약점 설명에 다시 써도 문항 내용이 섞임: {', '.join(found[:5])} "
                                f"({self.dir.name}/held/로 옮김)")
        with file.open("a", encoding="utf-8") as f:
            f.write("\n\n## 집계 (스크립트가 붙임)\n\n" + aggregate_md("이번 공격 문항", attack["summary"], False) + "\n"
                    + aggregate_md("기준 데이터(판정용)", dev["summary"], True))

    def defend(self) -> tuple[str | None, Path]:
        """Run the defender with the data hidden. Returns (why the change cannot stay | None, its log)."""
        prompt = self.prompt("defender")
        may = ("loop/defender/notes_", "loop/blocked.md")
        records = self.records_now()
        self.vault.hide()
        try:
            before = self.fingerprint(may)
            log = self.agent("defender", "8-defender", prompt, hidden=True, strict=True, reset=self.revert)
            after = self.fingerprint(may)
        finally:
            intruders = self.vault.restore()
        self.records_back(records, "defender")
        tampered = sorted(k for k in before if before[k] != after.get(k))
        tampered += sorted(k for k in after if k not in before and k.startswith(("scripts/", ".claude/")))
        if tampered:
            raise Fault("tamper", f"방어자가 보호 파일을 바꿈: {', '.join(tampered[:8])} (git으로 확인 후 되돌릴 것)")
        changes = self.clone_changes()
        self.note("방어자 변경", files=len(changes))
        if intruders:
            return f"숨긴 경로에 파일을 만듦: {', '.join(intruders)}", log
        if not changes:
            return "방어자가 코드를 고치지 않음" + (" (시간 초과로 끝남)" if self.last_exit == TIMED_OUT else ""), log
        rules = allow_rules(ROOT / "loop" / "defender_allow.txt", self.n)
        outside = [p for p in changes if not allowed(p, rules)]
        if outside:
            return f"허용 범위 밖 수정: {', '.join(outside[:8])}", log
        if contract_check.SCHEMA in changes:
            broken = contract_check.check(self.s.clone)
            if broken:
                (self.dir / "contract.txt").write_text("\n".join(broken), encoding="utf-8")
                return f"schemas.py에서 연산 추가 밖의 변경 {len(broken)}건: {'; '.join(broken[:3])}", log
        hits = reached(log)
        if hits is None:
            self.note("접근 흔적 검사를 못 함 (DEFENDER_CMD에 --output-format stream-json --verbose 가 필요)")
        elif hits:
            (self.dir / "reach.txt").write_text("\n".join(hits), encoding="utf-8")
            return f"숨긴 데이터에 접근하려 한 흔적 {len(hits)}건 ({self.dir.name}/reach.txt)", log
        return None, log

    def verify(self) -> bool:
        done = run([self.s.bash, "scripts/verify.sh"], log=self.log)
        self.row["verify"] = "pass" if done.returncode == 0 else "fail"
        return done.returncode == 0

    def tests_before(self) -> str:
        """Whether the tests the defender added or changed (tests/loop) fail on the code before the fix: a test that
        passes there too does not show that this loop's change works. One sentence for the critic; never a reason
        of its own to revert. The code before the fix is HEAD, unpacked outside the clone."""
        changes = self.clone_changes()
        tests = [p for p in changes if p.startswith("tests/loop/") and p.endswith(".py")
                 and Path(p).name.startswith("test_") and (self.s.clone / p).is_file()]
        if not tests:
            return "방어자가 이번에 추가하거나 고친 테스트가 없음 (tests/loop)"
        if any(p.startswith("kosis_chat/shared/") or p == "kosis_chat/prepare/index.py" for p in changes):
            return (f"추가·수정된 테스트 {len(tests)}개. 수정 전 코드로는 돌려 보지 않음 (색인 코드가 바뀌어, 돌리면 큰 표의 "
                    f"저장된 색인을 다시 만들게 됨)")
        try:
            with tempfile.TemporaryDirectory(prefix="kosis_loop_head_", ignore_cleanup_errors=True) as tmp:
                pack, head = Path(tmp) / "head.tar", Path(tmp) / "head"
                if self.git("archive", "--format=tar", "-o", str(pack), "HEAD").returncode != 0:
                    return f"추가·수정된 테스트 {len(tests)}개. 수정 전 코드를 꺼내지 못해 확인하지 못함"
                with tarfile.open(pack) as tar:
                    try:
                        tar.extractall(head, filter="data")
                    except TypeError:  # a Python without extraction filters
                        tar.extractall(head)
                for rel in changes:  # the new tests and what they import from tests/loop
                    src = self.s.clone / rel
                    if rel.startswith("tests/loop/") and src.is_file():
                        (head / rel).parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy(src, head / rel)
                env = os.environ | {"PYTHONPATH": os.pathsep.join([str(head), str(head / "src")])}
                done = subprocess.run([self.s.python, "-m", "pytest", *tests, "-q", "-p", "no:cacheprovider"], cwd=head,
                                      env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                      timeout=TESTS_BEFORE_TIMEOUT)
        except subprocess.TimeoutExpired:
            return f"추가·수정된 테스트 {len(tests)}개. 수정 전 코드에서 {TESTS_BEFORE_TIMEOUT}초 안에 끝나지 않아 확인하지 못함"
        except (OSError, tarfile.TarError) as exc:
            return f"추가·수정된 테스트 {len(tests)}개. 수정 전 코드로 돌리지 못함 ({type(exc).__name__})"
        with self.log.open("a", encoding="utf-8") as f:
            f.write(f"$ pytest {' '.join(tests)} (on HEAD, before the fix)\n{done.stdout}{done.stderr}\n(exit {done.returncode})\n")
        head_text = f"추가·수정된 테스트 파일 {len(tests)}개를 수정 전 코드에서 돌린 결과: "
        if done.returncode == 0:
            return head_text + "모두 통과함. 수정 전에도 통과하므로 이번 수정을 검증하는 테스트가 아님"
        if done.returncode == 1:
            failed = sum(1 for line in done.stdout.splitlines() if line.startswith("FAILED "))
            return head_text + (f"{failed}건이 실패함" if failed else "실패가 있음") + ". 수정 후에는 verify에서 모두 통과함"
        if done.returncode == 2:
            return head_text + "테스트를 불러오지 못함. 이번에 추가한 코드를 쓰는 테스트이면 정상이고, 수정 후에는 verify에서 통과함"
        return head_text + f"확인하지 못함 (pytest exit {done.returncode})"

    def records(self, items: list[dict], outcome: str, changed: bool) -> tuple[bool, str]:
        """The critic's judgment of the change (when the clone has one) and its record of the loop, which every loop
        that got this far leaves: also one whose attack was already all right, or whose defender changed nothing.
        Returns (accepted, reason); (False, "") without a change.

        The record files are what the next defender reads. The critic writes drafts of them where the defender
        cannot read (loop/attack/records_N/); the script checks what the drafts add for item content and only then
        copies them over loop/log.md, loop/learnings.md and docs/current-state.md. Drafts that still hold item
        content after one rewrite are not published: the reason goes to loop/blocked.md, which stops this run after
        the loop and every later run until a person has corrected the drafts and emptied that file."""
        if changed:
            self.git("add", "-A")
            (self.dir / "defender.diff").write_text(self.git("diff", "--cached").stdout, encoding="utf-8")
        (self.dir / "outcome.md").write_text(outcome, encoding="utf-8")
        drafts = self.attack / f"records_{self.n}"
        set_aside(drafts, self.dir / "stale")
        drafts.mkdir(parents=True)
        public = self.records_now()
        for name, rel in RECORDS.items():
            (drafts / name).write_bytes(public[rel] or b"")
        file = ROOT / "loop" / "critic" / f"code_{self.n}.json"
        file.parent.mkdir(parents=True, exist_ok=True)
        may = ("loop/critic/", "loop/attack/")
        accepted, why = False, ""
        if changed:
            for attempt in (1, 2):
                self.guarded("critic", f"10-review-{attempt}", self.prompt("critic_code"), may, staged=True, output=file)
                try:
                    verdict = json.loads(file.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    verdict = None
                if isinstance(verdict, dict) and verdict.get("verdict") in ("accept", "reject"):
                    accepted, why = verdict["verdict"] == "accept", str(verdict.get("reason") or "")
                    break
            else:  # not a rejection: nobody judged the change
                self.need(file, "평가자", "수정 판정")
                raise Fault("format", f"출력 형식 오류: 평가자의 수정 판정({file.name})에 verdict가 accept도 reject도 아님")
        else:
            self.guarded("critic", "10-note", self.prompt("critic_note"), may, output=drafts / "log.md")

        known = items + set_items(attack_sets(self.n))

        def spilled() -> list[str]:
            found = []
            for name, rel in RECORDS.items():
                hits = leaks(added_lines(read_text(drafts / name), (public[rel] or b"").decode("utf-8", "replace")), known)
                if hits:
                    found.append(f"{name} ({', '.join(hits[:4])})")
            if changed and (hits := leaks(read_text(file), known)):
                found.append(f"{file.name} ({', '.join(hits[:4])})")
            return found

        found = spilled()
        self.note("기록 초안 검사", leaks=len(found))
        if found:
            text = self.prompt("critic_record_rewrite", LEAKS="\n".join(f"- {x}" for x in found))
            self.guarded("critic", "10-record-rewrite", text, may, staged=changed)
            found = spilled()
            self.note("다시 쓴 기록 초안 검사", leaks=len(found))
        where = drafts.relative_to(ROOT).as_posix()
        if found:
            moved = any(x.startswith(file.name) for x in found)
            if moved:
                set_aside(file, drafts)  # loop/critic/ is read by later defenders
            self.after_stop = "평가자의 기록에 문항 내용이 섞여 공개하지 않음: " + "; ".join(found)
            lines = [f"\n# 루프 {self.n}: 평가자의 기록을 공개하지 않음 (스크립트가 적음, {datetime.now():%Y-%m-%d %H:%M})\n",
                     f"- 기록 초안에 문항 내용이 섞여 있음: {'; '.join(found)}",
                     f"- 초안은 `{where}/`에 있음 (방어자가 볼 수 없는 곳)"
                     + (f". 수정 판정 `{file.name}`도 그곳으로 옮김 (고친 뒤 `loop/critic/`에 되돌림)" if moved else ""),
                     "- 할 일: 초안에서 문항 내용(질문 문구, 표 번호, 정답 값)을 지우고 `loop/log.md`, `loop/learnings.md`, "
                     "`docs/current-state.md`에 옮긴 뒤 이 파일을 비움. 그때까지 루프는 시작하지 않음."]
            with (ROOT / "loop" / "blocked.md").open("a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            return accepted, why
        wrote = False
        for name, rel in RECORDS.items():
            data = (drafts / name).read_bytes() if (drafts / name).is_file() else b""
            if data.strip() and data != (public[rel] or b""):
                (ROOT / rel).parent.mkdir(parents=True, exist_ok=True)
                (ROOT / rel).write_bytes(data)
                wrote = wrote or name == "log.md"
        if not wrote:  # every loop leaves a block the next one can read
            with (ROOT / "loop" / "log.md").open("a", encoding="utf-8") as f:
                f.write(f"\n## 루프 {self.n} (평가자가 기록을 남기지 않아 스크립트가 적음)\n\n"
                        + "\n".join(line for line in outcome.splitlines() if line.startswith("- ")) + "\n")
            self.note("평가자가 loop/log.md 초안을 고치지 않아 스크립트가 결과를 적음")
        mark_records()
        return accepted, why

    def run(self) -> bool:
        """One loop. True: the defender's change was kept."""
        self.dir.mkdir(parents=True, exist_ok=True)
        self.row["time"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        dev = self.measure_dev("dev-before")
        if self.broken(dev):
            raise Fault("measure", f"기준 데이터 측정에 오류가 있음 ({dev.get('run')}, {self.log})")
        self.row["dev_before"] = f"{dev['score']:.4f}"
        self.note("기준 점수", score=self.row["dev_before"], reused=bool(dev.get("reused")), errors=dev["errors"])

        try:
            specs = self.attack_specs(dev)
        except Short as short:  # GOAL.md's completion: too few weaknesses found, and verify passes
            self.note("약점 부족으로 끝남: 최종 verify 실행")
            if self.verify():
                raise Done(f"목표 완료: {short}, 최종 verify 통과") from None
            raise Stop(f"{short}. 그러나 최종 verify가 실패해 목표 완료로 기록하지 않음 ({self.log})") from None
        items = self.attack_items(specs)
        attack, past = self.measure("attack-before", self.golden), self.measure_past("past-before")
        if self.broken(attack, past):
            raise Fault("measure", f"수정 전 측정에 오류가 있음 ({self.log})")
        self.row["attack_before"] = f"{attack['score']:.4f}"
        self.row["past_before"] = "-" if past is None else f"{past['score']:.4f}"
        self.note("수정 전 점수", attack=self.row["attack_before"], past=self.row["past_before"],
                  errors=attack["errors"] + (past["errors"] if past else 0))
        if attack["score"] >= 1 - EPS:
            self.row |= {"kept": "0", "reason": "공격 문항을 이미 모두 맞힘"}
            self.records(items, f"# 루프 {self.n} 결과\n\n- 이번 공격 문항(수정 전): {self.row['attack_before']}\n"
                                f"- 지난 공격 문항(수정 전): {self.row['past_before']}\n"
                                f"- 스크립트 판정: 수정 없음. 수정 전 코드가 이번 공격 문항을 이미 모두 맞혀 방어자를 부르지 않음.\n",
                         changed=False)
            return False

        self.brief(items, dev, attack)
        problem, _ = self.defend()
        self.handoff("방어자 실행 뒤")
        if problem is None and not self.verify():
            problem = "verify 실패"
        metrics, dev2 = "", None
        if problem is None:
            metrics = f"- 방어자의 테스트: {self.tests_before()}\n"
            dev2, attack2, past2 = self.measure_dev("dev-after"), self.measure("attack-after", self.golden), \
                self.measure_past("past-after")
            if self.broken(dev2, attack2, past2):
                problem = "수정 후 측정에 오류가 있음"
            else:
                self.row |= {"dev_after": f"{dev2['score']:.4f}", "attack_after": f"{attack2['score']:.4f}",
                             "past_after": "-" if past2 is None else f"{past2['score']:.4f}"}
                failed = [text for bad, text in (
                    (dev2["score"] < dev["score"] - EPS, "기준 데이터 점수가 떨어짐"),
                    (attack2["score"] <= attack["score"] + EPS, "이번 공격 문항 점수가 오르지 않음"),
                    (past is not None and past2["score"] < past["score"] - EPS, "지난 공격 문항 점수가 떨어짐")) if bad]
                problem = ", ".join(failed) or None
                metrics = (f"- 기준 데이터(판정용): {self.row['dev_before']} → {self.row['dev_after']}\n"
                           f"- 이번 공격 문항: {self.row['attack_before']} → {self.row['attack_after']}\n"
                           f"- 지난 공격 문항: {self.row['past_before']} → {self.row['past_after']}\n") + metrics
        self.note("유지 조건", problem=problem or "없음")

        changed = bool(self.clone_changes())
        outcome = (f"# 루프 {self.n} 결과\n\n{metrics}- 스크립트 판정: "
                   + ("유지 조건을 모두 만족함. 평가자의 판정에 따라 유지 또는 되돌림.\n" if problem is None
                      else f"되돌림 ({problem}). 평가자의 판정과 관계없이 되돌림.\n" if changed
                      else f"수정 없음 ({problem}).\n"))
        accepted, why = self.records(items, outcome, changed)
        if problem is None and not accepted:
            problem = f"평가자가 거부함: {why}"
        if problem is None:
            if not self.after_stop:  # (then loop/blocked.md holds the script's own note about the record)
                self.handoff("커밋 직전")
            self.git("add", "-A")
            done = self.git("commit", "-qm", f"loop {self.n}: 공격 {self.row['attack_before']} -> {self.row['attack_after']}, "
                                              f"기준 {self.row['dev_before']} -> {self.row['dev_after']}")
            if done.returncode != 0:
                raise Fault("setup", f"클론 커밋 실패: {done.stderr.strip()[:300]}")
            self.row |= {"kept": "1", "reason": why[:200] or "유지",
                         "commit": self.git("rev-parse", "--short", "HEAD").stdout.strip()}
            self.remember_dev(dev2)  # the next loop starts from this commit: its dev score is known
            return True
        self.revert()
        self.row |= {"kept": "0", "reason": problem[:300], "commit": "reverted" if changed else "-"}
        return False


# ---------------------------------------------------------------- records, checks, main

def results_rows() -> list[list[str]]:
    file = ROOT / "loop" / "results.tsv"
    if not file.exists():
        return []
    lines = [line.split("\t") for line in file.read_text(encoding="utf-8").splitlines() if line.strip()]
    return lines[1:] if lines and lines[0][0] in ("loop", "iter") else lines


def record(row: dict) -> None:
    file = ROOT / "loop" / "results.tsv"
    rows = results_rows()
    old_header = file.exists() and file.read_text(encoding="utf-8").startswith("iter\t")
    if old_header and rows:
        shutil.move(str(file), str(fresh(file.with_name("results.single-agent.tsv"))))
        rows = []
    rows.append([str(row[k]).replace("\t", " ").replace("\n", " ") for k in RESULTS_HEADER])
    file.write_text("\n".join("\t".join(r) for r in [RESULTS_HEADER, *rows]) + "\n", encoding="utf-8")


def record_fault(n: int, fault: Fault) -> None:
    """One line in loop/faults.tsv: a loop that could not be carried out (it has no row in loop/results.tsv)."""
    file = ROOT / "loop" / "faults.tsv"
    line = [datetime.now().strftime("%Y-%m-%d %H:%M"), str(n), fault.kind, str(fault)[:500]]
    new = not file.is_file() or file.stat().st_size == 0
    with file.open("a", encoding="utf-8") as f:
        if new:
            f.write("time\tloop\tkind\treason\n")
        f.write("\t".join(x.replace("\t", " ").replace("\n", " ") for x in line) + "\n")


def commit_records(n: int, row: dict) -> None:
    paths = [p for p in ("loop/results.tsv", "loop/faults.tsv", "loop/log.md", "loop/learnings.md", "loop/defender",
                         "loop/critic", "docs/current-state.md") if (ROOT / p).exists()]
    run(["git", "-C", str(ROOT), "add", "--", *paths])
    done = run(["git", "-C", str(ROOT), "commit", "-qm", f"loop {n}: kept={row['kept']} ({row['reason'][:60]})", "--", *paths])
    if done.returncode != 0 and "nothing to commit" not in done.stdout + done.stderr:
        say(f"기록 커밋을 하지 못함: {(done.stderr or done.stdout).strip()[:200]}")


def preflight(s: Settings) -> list[str]:
    problems = []
    if not (s.clone / ".git").exists():
        problems.append(f"Phase 1 클론이 git 저장소가 아님: {s.clone}")
    else:
        branch = run(["git", "-C", str(s.clone), "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
        if branch != s.branch:
            problems.append(f"클론의 브랜치가 {branch}임 (LOOP_BRANCH={s.branch})")
        if run(["git", "-C", str(s.clone), "status", "--porcelain"]).stdout.strip():
            problems.append("클론에 커밋하지 않은 변경이 있음 (커밋하거나 되돌린 뒤 시작)")
    for role, command in s.commands.items():
        if not command:
            problems.append(f"{role.upper()}_CMD 가 비어 있음 (loop/config.env)")
    if not s.workspace or not (Path(s.workspace) / "data" / "tables.csv").exists():
        problems.append("KOSIS_WORKSPACE 가 data/tables.csv 가 있는 폴더가 아님")
    for rel in ("evals/baseline/items.jsonl", "evals/baseline/split.json", "loop/defender_allow.txt",
                *(f"loop/prompts/{n}.md" for n in ("critic_hint", "attacker", "attacker_rewrite", "critic_judge1",
                                                   "critic_judge2", "critic_judge_rest", "critic_brief",
                                                   "critic_brief_rewrite", "defender", "critic_code", "critic_note",
                                                   "critic_record_rewrite"))):
        if not (ROOT / rel).exists():
            problems.append(f"파일이 없음: {rel}")
    if s.vault.exists() and any(s.vault.iterdir()):
        problems.append(f"보관 폴더에 남은 것이 있음: {s.vault} (bash scripts/loop.sh --restore)")
    return problems


def verify(s: Settings) -> int:
    """The gate of a defender's change: the shared schema changed by adding to the calculation plan only, then
    VERIFY_CMD when set, else the import check, the import rules, the cell store's tests (STORE_TESTS: a change that
    breaks the reading of big tables does not stay) and the defender's own tests (tests/loop).
    The whole Phase 1 test suite is not run here (GOAL.md)."""
    broken = contract_check.check(s.clone)
    if broken:
        print("verify: 실패 (src/kosis_ai/schemas.py 는 연산 계획에 더하는 것만 가능)")
        for line in broken:
            print(f"- {line}")
        return 1
    if s.verify_cmd:
        return subprocess.run([s.bash, "-c", s.verify_cmd], cwd=ROOT).returncode
    env = os.environ | {"PYTHONPATH": os.pathsep.join([str(s.clone), str(s.clone / "src")])}
    steps = [[s.python, "-c", "import kosis_chat.calc, kosis_chat.compute.answer_facts, kosis_chat.analyze.multi_rules"],
             [s.python, "-m", "pytest", "tests/test_layout.py", "-q", "-p", "no:cacheprovider"]]
    store_tests = [t for t in STORE_TESTS if (s.clone / t).is_file()]
    if store_tests:
        # without the comparison over every real table of the workspace: it takes long, a person runs it
        steps.append([s.python, "-m", "pytest", *store_tests, "-q", "-p", "no:cacheprovider", "-k", "not provided_tables"])
    if any((s.clone / "tests" / "loop").glob("test_*.py")):
        steps.append([s.python, "-m", "pytest", "tests/loop", "-q", "-p", "no:cacheprovider"])
    for step in steps:
        code = subprocess.run(step, cwd=s.clone, env=env).returncode
        if code != 0:
            print(f"verify: 실패 ({' '.join(step[1:4])})")
            return code
    print("verify: 통과")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="the improvement loop")
    p.add_argument("--check", action="store_true", help="check the settings, run nothing")
    p.add_argument("--restore", action="store_true", help="put back what is in the vault")
    p.add_argument("--verify", action="store_true", help="the defender's gate")
    p.add_argument("--once", action="store_true", help="run one loop and stop")
    args = p.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    s = Settings()
    if args.verify:
        return verify(s)
    if args.restore:
        back = Vault(s.vault).restore()
        print("되돌림 완료" + (f" (그사이 생긴 것: {back})" if back else ""))
        return 0
    problems = preflight(s)
    for problem in problems:
        print(f"- {problem}")
    if args.check or problems:
        print("시작할 수 없음" if problems else "설정 확인 통과")
        return 1 if problems else 0

    rows = results_rows()
    start = max((int(r[0]) for r in rows if r and r[0].isdigit()), default=0)
    streak, reason, code = 0, "", 0  # streak: loops in a row that kept nothing, counted within this run
    for n in range(start + 1, start + 1 + (1 if args.once else s.max_iter)):
        if (ROOT / "loop" / "STOP").exists():
            reason = "loop/STOP 파일이 있음"
            break
        if blocked_text():
            reason = "사람에게 넘김: loop/blocked.md 에 내용이 있음 (확인하고 비운 뒤 다시 시작)"
            break
        spilled = records_changed()
        if spilled:  # the defender reads these files
            reason = "기록 파일에 문항 내용이 섞여 있음: " + "; ".join(spilled) + " (고친 뒤 다시 시작)"
            break
        if streak >= s.patience:
            reason = f"{s.patience}번 연속으로 유지하지 못함"
            break
        loop = Loop(s, n)
        try:
            try:
                kept = loop.run()
            except Stop:
                raise
            except Exception as exc:  # a bug, an unreadable file: the run still ends in order and is told apart
                loop.dir.mkdir(parents=True, exist_ok=True)
                (loop.dir / "crash.txt").write_text(traceback.format_exc(), encoding="utf-8")
                raise Fault("crash", f"실행기 오류 {type(exc).__name__}: {str(exc)[:200]} ({loop.dir.name}/crash.txt)") from None
        except Fault as fault:  # not a result of the loop: no row, the same loop number runs again next time
            saved = unwind(loop)
            reason = f"운영 오류({fault.kind}): {fault}" + (f". 클론의 수정은 {saved}에 남기고 되돌림" if saved else "")
            reason += f". 다시 실행하면 루프 {n}부터 다시 함"
            record_fault(n, fault)
            code = 1
            break
        except Stop as stop:  # a rule of the loop ended the run: done, blocked or stop
            saved = unwind(loop)
            reason = str(stop) + (f". 클론의 수정은 커밋하지 않고 {saved}에 남김" if saved else "")
            loop.row |= {"kept": stop.kept, "reason": reason[:300]}
            record(loop.row)
            reason = {"done": "", "blocked": "사람에게 넘김: ", "stop": "중단: "}[stop.kept] + reason
            break
        finally:
            Vault(s.vault).restore()
        record(loop.row)
        say(f"loop {n} 끝: kept={loop.row['kept']} ({loop.row['reason']})")
        if loop.after_stop:
            reason = "사람에게 넘김: " + loop.after_stop + " (loop/blocked.md에 할 일을 적음)"
            break
        if s.record_commit:
            commit_records(n, loop.row)
        streak = 0 if kept else streak + 1
    print(f"종료: {reason or ('한 번 실행함' if args.once else f'최대 루프 수({s.max_iter})에 도달함')}")
    return code


def unwind(loop: Loop) -> str:
    """Leave the clone and the item files as a run that ended early must: a change in the clone saved as a diff and
    reset, items that are not a finished attack set moved away. Returns where the diff is ('' when none)."""
    saved = loop.shelve("unfinished.diff")
    if not loop.final_items:  # no judgment finished, or too few passed: not an attack set for later loops
        set_aside(loop.golden, loop.dir / "stale")
    return saved


if __name__ == "__main__":
    raise SystemExit(main())
