"""A read-only progress screen for the improvement loop. Run it in another terminal while the loop runs.

    python tools/watch_loop.py                the newest loop/orchestrator/run-*.log, read again every 2 seconds
    python tools/watch_loop.py --log FILE     another console log of scripts/loop.sh
    python tools/watch_loop.py --once         one frame, then exit            Stop: Ctrl+C
    python tools/watch_loop.py --still        colours without motion          --no-color: plain text

It reads three files and writes none: the loop's console log (the lines scripts/loop_main.py prints), loop/results.tsv
and loop/faults.tsv. It opens nothing under evals/, loop/attack/ or loop/runs/: the loop moves those folders away while
the defender runs, and a file held open there would make the move fail on Windows. The files it reads hold step names,
counts, scores and reasons, no item contents.

The console log has a line when an agent starts and ends and a line with each result of the script (a score, a count),
but none when the script itself starts measuring. So "the script is measuring" is told from what came last, and is
shown as a guess (추정).

The files are read every 2 seconds. Between two readings the screen is only drawn again, several times a second, so
that the clocks tick and the running step moves: a block for each step in the colour of who does it, a light that
passes over the running step, a glow on a step that has just ended. Only the lines that changed are written.
"""
from __future__ import annotations

import argparse
import math
import os
import re
import shutil
import sys
import time
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINE = re.compile(r"^\[(\d\d):(\d\d):(\d\d)\] loop (\d+) (.*)$")
AGENT = re.compile(r"^(\d+-[A-Za-z0-9_-]+?)(?: (시작|끝))?(?::.*)?$")
RETRY = re.compile(r"(\d+)초 뒤 다시 실행")

# The steps of one loop in order: key, who does it, what it is. "search" only happens when the attacker found too few
# weaknesses, so it is shown only when the log has it.
PHASES = [("dev", "스크립트", "기준 측정"), ("hint", "평가자", "약점 힌트"), ("attack", "공격자", "문항 작성"),
          ("judge1", "평가자", "1차 판정"), ("search", "평가자", "탐색 보고 심사"), ("build", "스크립트", "값 채우기"),
          ("judge2", "평가자", "2차 판정"), ("before", "스크립트", "수정 전 측정"), ("brief", "평가자", "약점 설명"),
          ("defend", "방어자", "코드 수정"), ("check", "스크립트", "검사와 수정 후 측정"),
          ("review", "평가자", "수정 판정과 기록")]
ORDER = [key for key, _, _ in PHASES]
AGENT_STEPS = [("1-hint", "hint"), ("2-attack", "attack"), ("3-judge1", "judge1"), ("3-search", "search"),
               ("5-judge2", "judge2"), ("7-brief", "brief"), ("8-defender", "defend"), ("10-", "review")]
# The lines of the script: how a line starts, the step it belongs to, whether the step is over with it.
NOTES = [("기준 점수", "dev", True), ("공격 문항이 목표 건수보다 적음", "attack", False), ("1차 판정", "judge1", True),
         ("탐색 보고 심사", "search", False), ("약점 부족으로 끝남", "search", False), ("값 채우기", "build", True),
         ("2차 판정", "judge2", True), ("수정 전 점수", "before", True), ("약점 설명 검사", "brief", True),
         ("방어자 변경", "defend", True), ("접근 흔적 검사", "check", False), ("유지 조건", "check", True),
         ("수정 판정을 쓸 수 없어", "review", False), ("기록 초안 검사", "review", False),
         ("다시 쓴", "review", False), ("평가자가 loop/log.md", "review", False)]


# ---------------------------------------------------------------- reading

def read_text(path: Path) -> str:
    data = path.read_bytes()
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):  # a log written by a PowerShell redirect
        return data.decode("utf-16", errors="replace")
    return data.decode("utf-8-sig", errors="replace")


def newest_log() -> Path | None:
    logs = [p for p in (ROOT / "loop" / "orchestrator").glob("run-*.log") if p.is_file()]
    return max(logs, key=lambda p: p.stat().st_mtime) if logs else None


def started_at(path: Path) -> datetime | None:
    """When the log file was made (the run's start). Only where the system keeps that time."""
    stat = path.stat()
    born = getattr(stat, "st_birthtime", None) or (stat.st_ctime if os.name == "nt" else None)
    return datetime.fromtimestamp(born) if born else None


def table(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    lines = [line.split("\t") for line in read_text(path).splitlines() if line.strip()]
    if len(lines) < 2 or lines[0][0] not in ("loop", "time"):  # (an older table of another shape: not this loop's)
        return []
    return [dict(zip(lines[0], row)) for row in lines[1:]]


def parse(text: str, anchor: datetime) -> dict:
    """The console log as events. A line carries a time of day only: the days are counted back from the file's
    time, which is when the last line was written."""
    run = {"python": "", "events": [], "ended": {}, "final": "", "other": []}
    for raw in text.replace("\r", "").split("\n"):
        line = raw.strip()
        if not line:
            continue
        found = LINE.match(line)
        if found:
            clock = (int(found[1]), int(found[2]), int(found[3]))
            loop, rest = int(found[4]), found[5]
            if rest.startswith("끝:"):
                kept = re.match(r"끝: kept=(\S+) \((.*)\)$", rest)
                run["events"].append({"clock": clock, "loop": loop, "step": "끝", "fields": {}, "raw": line})
                run["ended"][loop] = (kept[1], kept[2]) if kept else ("?", rest[2:].strip())
                continue
            parts = rest.lstrip("· ").split(" · ")
            fields: dict[str, str] = {}
            last = None
            for part in parts[1:]:
                name, eq, value = part.partition("=")
                if eq and re.fullmatch(r"[A-Za-z_]+", name):
                    fields[name], last = value, name
                elif last:  # a value that had the separator in it
                    fields[last] += " · " + part
                else:
                    parts[0] += " · " + part
            run["events"].append({"clock": clock, "loop": loop, "step": parts[0], "fields": fields, "raw": line})
        elif line.startswith("파이썬:"):
            run["python"] = line[4:].strip()
        elif line.startswith("종료:"):
            run["final"] = line[3:].strip()
        else:
            run["other"].append(line)
    day, later = anchor.date(), None
    for event in reversed(run["events"]):
        clock = event["clock"]
        if later is None:
            if datetime(day.year, day.month, day.day, *clock) > anchor + timedelta(minutes=2):
                day -= timedelta(days=1)
        elif clock > later:
            day -= timedelta(days=1)
        event["time"], later = datetime(day.year, day.month, day.day, *clock), clock
    return run


# ---------------------------------------------------------------- what the events mean

def place(step: str, current: str | None) -> tuple[str | None, bool, str]:
    """(the step of the loop a line belongs to, whether that step is over with it, start | end | "")."""
    found = AGENT.match(step)
    if found:
        for prefix, key in AGENT_STEPS:
            if found[1].startswith(prefix):
                return key, False, {"시작": "start", "끝": "end", None: ""}[found[2]]
    for prefix, key, over in NOTES:
        if step.startswith(prefix):
            return key, over, ""
    return current, False, ""


def following(key: str) -> str | None:
    rest = [k for k in ORDER[ORDER.index(key) + 1:] if k != "search"]
    return rest[0] if rest else None


def view(run: dict, loop: int, begun: datetime | None) -> dict:
    """One loop's steps: state (done | running | stopped | pending | skipped), times and what its lines told."""
    steps = {key: {"state": "pending", "start": None, "end": None, "seen": False, "info": {}, "agent": None,
                   "runs": 0, "retries": 0, "wait": None, "last": ""} for key in ORDER}
    active, last_time = "dev", begun
    steps["dev"]["start"] = begun
    events = [e for e in run["events"] if e["loop"] == loop]
    for event in events:
        if event["step"] == "끝":
            last_time = event["time"]
            continue
        key, over, edge = place(event["step"], active)
        if key is None:
            continue
        if key != active:
            if active is not None:
                left = steps[active]
                if left["seen"]:
                    left["state"], left["end"] = "done", left["end"] or event["time"]
                else:  # it was only expected to come next
                    left["state"], left["start"] = "pending", None
            active = key
        step = steps[key]
        step["start"] = step["start"] or last_time or (None if key == "dev" else event["time"])
        step["seen"], step["state"], step["end"], step["last"] = True, "running", None, event["step"]
        step["info"] |= event["fields"]
        if edge == "start":
            step["agent"], step["runs"], step["wait"] = (event["step"][:-3], event["time"]), step["runs"] + 1, None
        elif edge == "end":
            step["agent"] = None
            over = over or (key in ("hint", "attack") and event["fields"].get("exit") == "0")
        retry = RETRY.search(event["step"])
        if retry:
            step["retries"] += 1
            step["wait"] = (event["time"] + timedelta(seconds=int(retry[1])), event["fields"].get("attempt", ""))
        last_time = event["time"]
        if over:
            step["state"], step["end"] = "done", event["time"]
            active = following(key)
            if active:
                steps[active]["start"] = steps[active]["start"] or event["time"]
    ended = run["ended"].get(loop)
    newest = max((e["loop"] for e in run["events"]), default=loop)
    if ended or loop < newest or run["final"]:
        # A run that ends by a rule of the loop (done, stop, handed over) ends after a step's result; only a fault
        # ends inside a step, which may be one of the script's that has no line of its own yet.
        finished = bool(ended) or loop < newest or not run["final"].startswith("운영 오류")
        for key in ORDER:
            step = steps[key]
            if step["state"] == "running":
                step["state"], step["end"] = "done" if finished else "stopped", step["end"] or last_time
            elif step["state"] == "pending":
                step["state"] = "stopped" if key == active and not finished else "skipped"
        active = None
    elif active:
        steps[active]["state"] = "running"
    return {"loop": loop, "steps": steps, "active": active, "ended": ended, "last": last_time,
            "first": events[0]["time"] if events else None}


def told(key: str, step: dict) -> str:
    """What a step's lines said, in a few words."""
    info, out = step["info"], []
    if key == "dev" and "score" in info:
        out = [f"점수 {info['score']}", f"칸 일치 {info.get('strict', '-')}", f"오류 {info.get('errors', '-')}건"]
        if info.get("reused") == "True":
            out.append("저장된 점수를 씀")
    elif key == "judge1" and "specs" in info:
        out = [f"문항 {info['specs']}건", f"부적절 {info.get('rejected', '-')}건"]
    elif key == "search" and "verdict" in info:
        out = [f"심사 결과 {info['verdict']}"]
    elif key == "build" and "built" in info:
        out = [f"값을 채운 문항 {info['built']}/{info.get('specs', '-')}건"]
    elif key == "judge2" and "kept" in info:
        out = [f"통과 {info['kept']}/{info.get('built', '-')}건"]
    elif key == "before" and "attack" in info:
        out = [f"이번 공격 {info['attack']}", f"칸 일치 {info.get('attack_strict', '-')}", f"지난 공격 {info.get('past', '-')}"]
    elif key == "brief" and info.get("leaks", "0") != "0":
        out = ["문항 내용이 섞여 다시 씀"]
    elif key == "defend" and "files" in info:
        out = [f"바꾼 파일 {info['files']}개"]
    elif key == "check" and "problem" in info:
        out = ["유지 조건을 만족함" if info["problem"] == "없음" else f"유지 못함: {info['problem']}"]
    if key in ("attack", "judge1", "brief", "review") and step["runs"] > 1:
        out.append(f"에이전트 실행 {step['runs']}번")
    if step["retries"]:
        out.append(f"실패 뒤 다시 실행 {step['retries']}번")
    return " · ".join(out)


# ---------------------------------------------------------------- drawing

ROLE = {"평가자": (57, 135, 229), "공격자": (235, 104, 52), "방어자": (27, 175, 122), "스크립트": (150, 148, 142)}
GOOD, BAD, WARN, LIVE = (22, 178, 22), (224, 72, 72), (250, 178, 25), (64, 190, 214)
GRAY, DARK, WHITE = (128, 128, 128), (60, 60, 60), (255, 255, 255)


def mix(a: tuple, b: tuple, share: float) -> tuple:
    share = min(1.0, max(0.0, share))
    return tuple(round(x + (y - x) * share) for x, y in zip(a, b))


class Paint:
    """Colour codes around a text, or the text as it is when colours are off."""

    def __init__(self, on: bool, full: bool = True) -> None:
        self.on, self.full = on, full  # full: 24-bit colours. Otherwise the nearest of the 256-colour table.

    def __call__(self, text: str, code: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m" if self.on and text else text

    def code(self, rgb: tuple, ground: int = 38) -> str:
        r, g, b = rgb
        if self.full:
            return f"{ground};2;{r};{g};{b}"
        return f"{ground};5;{16 + 36 * round(r / 51) + 6 * round(g / 51) + round(b / 51)}"

    def ink(self, text: str, rgb: tuple, bold: bool = False) -> str:
        return self(text, self.code(rgb) + (";1" if bold else ""))

    def dim(self, text: str) -> str:  # (a gray of its own: the "faint" code is not drawn by every Windows console)
        return self(text, self.code(GRAY))

    def block(self, columns: int, rgb: tuple) -> str:
        """A coloured block: spaces on a coloured ground, so every console agrees on how wide it is."""
        return self(" " * columns, self.code(rgb, 48)) if self.on else ""


class Motion:
    """What moves on the screen: one clock for the pulse and the passing light, and the steps that have just ended."""

    def __init__(self, on: bool) -> None:
        self.on, self.t = on, 0.0
        self.known: dict[tuple[int, str], str] = {}
        self.fresh: dict[tuple[int, str], float] = {}

    def tick(self) -> None:
        self.t = time.monotonic()

    def pulse(self, period: float = 1.6) -> float:
        """Between 0 and 1, up and down once in a period."""
        return 0.5 - 0.5 * math.cos(2 * math.pi * self.t / period) if self.on else 1.0

    def note(self, data: dict) -> None:
        """Called after each reading: a step that was not done at the reading before and is done now glows for a while."""
        state = data.get("state")
        for key, step in (state["steps"] if state else {}).items():
            was = self.known.get((state["loop"], key))
            if was not in (None, "done") and step["state"] == "done":
                self.fresh[(state["loop"], key)] = self.t
            self.known[(state["loop"], key)] = step["state"]

    def glow(self, loop: int, key: str, seconds: float = 6.0) -> float:
        """1 when the step has just ended, down to 0 some seconds later."""
        since = self.fresh.get((loop, key))
        return max(0.0, 1 - (self.t - since) / seconds) if self.on and since is not None else 0.0

    def sweep(self, text: str, rgb: tuple, paint: Paint, period: float = 2.4) -> str:
        """The text in one colour, with a lighter spot that passes over it from left to right."""
        if not (self.on and paint.on):
            return paint.ink(text, rgb, True)
        head = (self.t % period) / period * (wide(text) + 14) - 7
        out, piece, colour, at = [], "", None, 0
        for ch in text:
            now = mix(rgb, WHITE, 0.7 * max(0.0, 1 - abs(at - head) / 7))
            if now != colour and piece:
                out.append(paint.ink(piece, colour, True))
                piece = ""
            piece, colour, at = piece + ch, now, at + wide(ch)
        return "".join(out) + paint.ink(piece, colour, True)


def wide(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def fit(text: str, width: int, fill: bool = True) -> str:
    """The text cut or filled to a number of screen columns (Korean letters take two)."""
    out, used = [], 0
    for ch in text:
        size = 2 if unicodedata.east_asian_width(ch) in "WF" else 1
        if used + size > width:  # cut: the last letter gives way to the mark when there is no column left for it
            while out and used + 1 > width:
                used -= 2 if unicodedata.east_asian_width(out.pop()) in "WF" else 1
            out.append("…")
            used += 1
            break
        out.append(ch)
        used += size
    return "".join(out) + (" " * (width - used) if fill else "")


def wrap(text: str, width: int, most: int) -> list[str]:
    """The text on at most `most` lines of `width` screen columns."""
    lines, line, used = [], "", 0
    for ch in text:
        size = 2 if unicodedata.east_asian_width(ch) in "WF" else 1
        if used + size > width:
            lines.append(line)
            line, used = "", 0
        line, used = line + ch, used + size
    lines.append(line)
    return lines[:most - 1] + [fit("".join(lines[most - 1:]), width, False)] if len(lines) > most else lines


def span(seconds: float | None) -> str:
    if seconds is None:
        return ""
    seconds = max(0, int(seconds))
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def load(log_arg: Path | None) -> dict:
    """Everything the screen shows, read from the three files once. The screen is drawn from it several times."""
    log = log_arg.resolve() if log_arg else newest_log()
    data: dict = {"log": log, "run": None, "state": None, "results": table(ROOT / "loop" / "results.tsv"),
                  "faults": table(ROOT / "loop" / "faults.tsv")}
    if log is None or not log.is_file():
        return data
    written = datetime.fromtimestamp(log.stat().st_mtime)
    run = parse(read_text(log), written)
    loops = sorted({e["loop"] for e in run["events"]})
    if not loops:  # nothing but the first line yet: the loop number is the one after the last recorded
        numbers = [int(r["loop"]) for r in data["results"] if r.get("loop", "").isdigit()]
        loops = [max(numbers, default=0) + 1] if not run["final"] else []
    begun = None
    if loops:
        mine = [e["time"] for e in run["events"] if e["loop"] == loops[-1]]
        before = [e["time"] for e in run["events"] if e["loop"] < loops[-1]]
        if before:  # a later loop of the same run starts where the one before it ended
            begun = before[-1]
        elif not mine:  # only the first line so far: the file was last written when the run started
            begun = None if run["final"] else written
        else:  # the file's own start, unless the file is an older one written over
            begun = started_at(log)
            if begun and not timedelta(0) <= mine[0] - begun <= timedelta(hours=2):
                begun = None
        data["state"] = view(run, loops[-1], begun)
    return data | {"run": run, "written": written, "loops": loops, "begun": begun}


def frame(data: dict, paint: Paint, motion: Motion, width: int, now: datetime, watching: float | None) -> list[str]:
    lines: list[str] = []
    run, log = data["run"], data["log"]
    if run is None:
        lamp = GRAY
    elif not run["final"]:
        lamp = mix(mix(GOOD, (0, 0, 0), 0.65), mix(GOOD, WHITE, 0.25), motion.pulse())
    else:
        lamp = (BAD if run["final"].startswith("운영 오류") else WARN if run["final"].startswith(("사람에게 넘김", "중단"))
                else GOOD)
    light = paint.block(2, lamp)
    head, used = "KOSIS 개선 루프 · 진행 화면", 3 if light else 0
    clocks = [f"{now:%H:%M:%S}" + more for more in ((f" · {watching:g}초마다 다시 읽음 · 끝내기 Ctrl+C", " · 끝내기 Ctrl+C")
                                                    if watching else ()) + ("",)]
    room = min(width, 100) - used - wide(head) - 2  # (a narrow window gets the short clock: no line may wrap)
    clock = next((text for text in clocks if wide(text) <= room), clocks[-1])
    lines += [(light + " " if light else "") + paint(head, "1")
              + " " * max(2, min(width, 100) - used - wide(head) - wide(clock)) + paint.dim(clock),
              paint.dim("─" * min(width, 100))]
    if run is None:
        lines += ["진행 로그가 아직 없음: loop/orchestrator/run-N.log",
                  paint.dim("운영 세션이 루프를 실행하면 생김. 직접 실행할 때는 출력을 그 파일로 보낼 것:"),
                  paint.dim("  bash scripts/loop.sh --once > loop/orchestrator/run-1.log 2>&1"), ""]
    else:
        shown = log.relative_to(ROOT).as_posix() if log.is_relative_to(ROOT) else str(log)
        lines.append(paint.dim(fit(f"로그 {shown}" + (f" · 파이썬 {run['python']}" if run["python"] else ""), width, False)))
        for loop in data["loops"][:-1]:
            ended = run["ended"].get(loop)
            lines.append(paint.dim(fit(f"루프 {loop} · 끝" + (f" · kept={ended[0]} ({ended[1]})" if ended else ""), width, False)))
        if data["state"]:
            lines += [""] + loop_lines(run, data["state"], data["begun"], paint, motion, width, now)
        quiet = (now - data["written"]).total_seconds()
        if run["final"]:
            bad = run["final"].startswith("운영 오류")
            held = run["final"].startswith(("사람에게 넘김", "중단"))
            lines += [""] + [paint.ink(line, BAD if bad else WARN if held else GOOD, True)
                             for line in wrap(f"실행이 끝남: {run['final']}", width, 4)]
        else:
            lines += ["", paint.dim(f"로그의 마지막 줄 이후 {span(quiet)}" + (" (에이전트 한 번은 길면 한 시간이 넘음)" if quiet > 600 else ""))]
        tail = [e["raw"] for e in run["events"][-4:]] + run["other"][-2:]
        if tail:
            lines += ["", paint("최근 줄", "1")] + [paint.dim("  " + fit(line, width - 2, False)) for line in tail]
    lines += [""] + history(data["results"], data["faults"], paint, width)
    return lines


def loop_lines(run: dict, state: dict, begun: datetime | None, paint: Paint, motion: Motion, width: int,
               now: datetime) -> list[str]:
    loop, steps, active, ended = state["loop"], state["steps"], state["active"], state["ended"]
    start = begun or state["first"]
    title, colour = f"루프 {loop}", None
    if ended:
        title += f" · 끝 · kept={ended[0]} ({ended[1]})"
        colour = GOOD if ended[0] == "1" else WARN
    elif run["final"]:
        how = [(("목표 완료",), "목표 완료로 끝남", GOOD), (("중단",), "중단됨", WARN), (("사람에게 넘김",), "사람에게 넘김", WARN),
               (("운영 오류",), "운영 오류로 멈춤 (결과로 기록되지 않음)", BAD)]
        text, colour = next(((text, c) for starts, text, c in how if run["final"].startswith(starts)), ("멈춤", WARN))
        title += " · " + text
    else:
        title += " · 진행 중" + (f" · 지난 시간 {span((now - start).total_seconds())}" if start else "")
    title = fit(title, width, False)
    lines = [paint.ink(title, colour, True) if colour else paint(title, "1")]
    shown = [(key, who, what) for key, who, what in PHASES if key != "search" or steps[key]["seen"]]

    # One block for each step, in the colour of who does it: lit when done, breathing while it runs.
    blocks, done = [], 0
    for key, who, _ in shown:
        kind = steps[key]["state"]
        done += kind == "done"
        if kind == "done":
            tone = mix(ROLE[who], WHITE, 0.75 * motion.glow(loop, key))
        elif kind == "running":
            tone = mix(mix(ROLE[who], (0, 0, 0), 0.7), mix(ROLE[who], WHITE, 0.3), motion.pulse())
        else:
            tone = BAD if kind == "stopped" else DARK
        blocks.append(paint.block(3 if width >= 72 else 2, tone))
    if paint.on:
        lines.append("  " + " ".join(blocks) + paint.dim(f"  {done}/{len(shown)} 단계"))

    names = {"done": "완료", "running": "진행", "stopped": "멈춤", "pending": "대기", "skipped": " -- "}
    for key, who, what in shown:
        step = steps[key]
        kind, role = step["state"], ROLE[who]
        mark = f"[{names[kind]}]"
        if kind == "running":
            took = span((now - step["start"]).total_seconds()) if step["start"] else ""
        else:
            took = span((step["end"] - step["start"]).total_seconds()) if step["start"] and step["end"] else ""
        plain = f"  {mark} {fit(who, 8)} {fit(what, 20)} {took:>8}  "
        said = fit(told(key, step), max(10, width - wide(plain)), False)
        if kind == "running":
            beat = mix(mix(LIVE, (0, 0, 0), 0.55), mix(LIVE, WHITE, 0.35), motion.pulse())
            row = (f"  {paint.ink(mark, beat, True)} {paint.ink(fit(who, 8), role, True)} "
                   f"{motion.sweep(fit(what, 20), LIVE, paint)} {paint.ink(f'{took:>8}', LIVE, True)}  {said}")
        elif kind == "done":
            glow = motion.glow(loop, key)
            sign = paint(mark, paint.code(mix(GOOD, WHITE, 0.5 * glow)) + ";1;7") if glow else paint.ink(mark, GOOD)
            row = f"  {sign} {paint.ink(fit(who, 8), role)} {fit(what, 20)} {paint.dim(f'{took:>8}')}  {said}"
        elif kind == "stopped":
            row = (f"  {paint.ink(mark, BAD, True)} {paint.ink(fit(who, 8), role)} {paint.ink(fit(what, 20), BAD, True)} "
                   f"{paint.dim(f'{took:>8}')}  {said}")
        else:
            row = paint.dim(plain) + said
        lines.append(row)
    if active and not run["final"]:
        step, (_, who, what) = steps[active], PHASES[ORDER.index(active)]
        if step["wait"]:
            due, attempt = step["wait"]
            text = f"{who} · {what} · 에이전트가 실패해 기다리는 중, {due:%H:%M}쯤 다시 실행" + (f" (다시 실행 {attempt})" if attempt else "")
            shade = paint.ink(fit("지금: " + text, width, False), mix(mix(WARN, (0, 0, 0), 0.45), WARN, motion.pulse(2.4)), True)
        else:
            if step["agent"]:
                name, since = step["agent"]
                text = f"{who} · {what} · 에이전트 실행 중 {span((now - since).total_seconds())}째 (단계 {name})"
            elif who == "스크립트":
                text = f"스크립트 · {what} 중 (추정. 이 단계는 시작할 때 줄이 찍히지 않음)"
            else:
                text = f"{who} · {what} · 에이전트가 끝나 스크립트가 결과를 확인하는 중"
            shade = motion.sweep(fit("지금: " + text, width, False), LIVE, paint, 3.2)
        lines += ["", shade]
    return lines


def history(results: list[dict], faults: list[dict], paint: Paint, width: int) -> list[str]:
    lines = [paint("끝난 루프 (loop/results.tsv)", "1")]
    if not results:
        lines.append(paint.dim("  아직 없음"))
    else:
        head = f"  {fit('루프', 5)} {fit('kept', 7)} {fit('기준 전/후', 18)} {fit('이번 공격 전/후', 21)} {fit('칸 일치 전/후', 19)} 이유"
        lines.append(paint.dim(fit(head, width, False)))
        for row in results[-8:]:
            pair = lambda a, b: f"{row.get(a, '-')}/{row.get(b, '-')}"
            kept = row.get("kept", "-")
            text = fit(f"  {fit(row.get('loop', '-'), 5)} {fit(kept, 7)} {fit(pair('dev_before', 'dev_after'), 18)} "
                       f"{fit(pair('attack_before', 'attack_after'), 21)} "
                       f"{fit(pair('attack_strict_before', 'attack_strict_after'), 19)} {row.get('reason', '')}", width, False)
            colour = {"1": GOOD, "done": GOOD, "blocked": WARN, "stop": WARN}.get(kept)
            lines.append(paint.ink(text, colour, kept == "done") if colour else text)
    if faults:
        last = faults[-1]
        lines += ["", paint(f"운영 오류 {len(faults)}건 (loop/faults.tsv). 마지막:", "1"),
                  paint.ink("  " + fit(f"{last.get('time', '')} · 루프 {last.get('loop', '-')} · {last.get('kind', '-')} · "
                                       f"{last.get('reason', '')}", width - 2, False), BAD)]
    return lines



# ---------------------------------------------------------------- the board: steps as boxes on a flow

ANSI = re.compile(r"\x1b\[[0-9;]*m")
WHO = {key: who for key, who, _ in PHASES}
WHAT = {key: what for key, _, what in PHASES}


def seen_width(text: str) -> int:
    return wide(ANSI.sub("", text))


def pad(text: str, width: int) -> str:
    """A painted text filled with spaces to a number of screen columns."""
    return text + " " * max(0, width - seen_width(text))


def box(paint: Paint, width: int, rows: list[str], edge: tuple | None, title: str = "") -> list[str]:
    """A rounded box: the rows are painted already and at most width - 4 columns wide."""
    line = lambda text: paint.ink(text, edge) if edge else paint.dim(text)
    top = "╭" + (f"─ {title} " if title else "") + "─" * (width - 2 - (wide(title) + 3 if title else 0)) + "╮"
    if title:
        top = line("╭─ ") + paint(title, "1") + line(" " + "─" * (width - 5 - wide(title)) + "╮")
    else:
        top = line(top)
    return [top] + [line("│") + " " + pad(row, width - 4) + " " + line("│") for row in rows] + [line("╰" + "─" * (width - 2) + "╯")]


def step_box(paint: Paint, motion: Motion, state: dict, key: str, width: int, now: datetime) -> list[str]:
    step, who, what, loop = state["steps"][key], WHO[key], WHAT[key], state["loop"]
    kind, role, inner = step["state"], ROLE[who], width - 4
    took = ""
    if kind == "running" and step["start"]:
        took = span((now - step["start"]).total_seconds())
    elif step["start"] and step["end"]:
        took = span((step["end"] - step["start"]).total_seconds())
    took = took[2:] if took.startswith("0:") else took  # (in a box a time under an hour is m:ss)
    took = took[1:] if took[:1] == "0" and len(took) == 5 else took
    glow = motion.glow(loop, key) if kind == "done" else 0.0
    edge = {"done": mix(mix(role, (0, 0, 0), 0.25), WHITE, 0.8 * glow),
            "running": mix(mix(role, (0, 0, 0), 0.5), mix(role, WHITE, 0.35), motion.pulse()),
            "stopped": BAD}.get(kind, DARK)
    name = fit(what, inner, False)
    title = motion.sweep(name, mix(role, WHITE, 0.3), paint) if kind == "running" else (
        paint(name, "1") if kind in ("done", "stopped") else paint.dim(name))
    sign = {"done": ("✓ " + (took or "완료"), GOOD), "running": ("● 진행 중", LIVE), "stopped": ("✗ 멈춤 " + took, BAD),
            "pending": ("대기", GRAY), "skipped": ("건너뜀", GRAY)}[kind]
    shown = fit(sign[0] if paint.on else f"[{sign[0]}]", inner - wide(who) - 1, False)
    right = paint.ink(shown, sign[1], kind == "running") if paint.on else shown
    who_text = paint.ink(who, role, kind == "running") if kind != "pending" else paint.dim(who)
    middle = who_text + " " * max(1, inner - wide(who) - wide(shown)) + right
    if kind == "running" and step["wait"]:
        due, attempt = step["wait"]
        last = paint.ink(fit(f"실패, {due:%H:%M}쯤 다시 실행" + (f" ({attempt})" if attempt else ""), inner, False),
                         mix(mix(WARN, (0, 0, 0), 0.4), WARN, motion.pulse(2.4)), True)
    elif kind == "running":
        clock = took or ""
        room = max(4, inner - wide(clock) - 1)
        if paint.on and motion.on:  # a light that runs along the bar: how long is not known, only that it goes on
            size = max(3, room // 4)
            at = int((motion.t / 2.2 % 1) * (room + size)) - size
            cells = [mix(role, WHITE, 0.45) if at <= i < at + size else mix(role, (0, 0, 0), 0.72) for i in range(room)]
            bar = "".join(paint.block(1, c) for c in cells)
        elif paint.on:
            bar = paint.block(room, mix(role, (0, 0, 0), 0.5))
        else:
            bar = fit("에이전트 실행 중" if step["agent"] else "확인 중", room)
        last = bar + " " + paint.ink(clock, LIVE, True)
    else:
        said = told(key, step)
        last = paint.dim(fit(said, inner, False)) if kind != "done" or not said else fit(said, inner, False)
    return box(paint, width, [title, middle, last], edge)



STAGES = [("공격 준비", ["dev", "hint", "attack"]), ("문항 검증", ["judge1", "search", "build", "judge2"]),
          ("방어", ["before", "brief", "defend"]), ("판정", ["check", "review"])]


def columns(paint: Paint, motion: Motion, state: dict, run: dict, width: int, now: datetime) -> list[str]:
    """The four stages from left to right; in each, its steps from top to bottom."""
    gap = 3
    size = (width - gap * 3) // 4
    cols = []
    for n, (name, keys) in enumerate(STAGES, 1):
        keys = [k for k in keys if k != "search" or state["steps"][k]["seen"]]
        kinds = [state["steps"][k]["state"] for k in keys]
        done = sum(k in ("done", "skipped") for k in kinds)
        if "stopped" in kinds:
            tone = BAD
        elif done == len(keys):
            tone = GOOD
        elif "running" in kinds or done:
            tone = LIVE
        else:
            tone = None
        head = f"{n}. {name}"
        count = f"{done}/{len(keys)}"
        text = head + " " * max(1, size - 1 - wide(head) - wide(count)) + count
        rows = [paint.ink(text, tone, True) if tone else paint.dim(text)]
        rule = "━" * size
        rows.append(paint.ink(rule, mix(tone, (0, 0, 0), 0.35)) if tone else paint.dim(rule))
        for i, key in enumerate(keys):
            if i:
                above, below = state["steps"][keys[i - 1]]["state"], state["steps"][key]["state"]
                mark = " " * (size // 2) + "▼"
                if below == "running" and not run["final"]:
                    rows.append(paint.ink(mark, mix(mix(LIVE, (0, 0, 0), 0.5), mix(LIVE, WHITE, 0.3), motion.pulse())))
                else:
                    rows.append(paint.ink(mark, GOOD) if above == "done" else paint.dim(mark))
            rows += step_box(paint, motion, state, key, size, now)
        cols.append(rows)
    tall = max(len(c) for c in cols)
    out = []
    for i in range(tall):
        line = ""
        for j, col in enumerate(cols):
            cell = col[i] if i < len(col) else ""
            line += pad(cell, size)
            if j < len(cols) - 1:
                if i == 0:  # the stages are joined on the line of their names
                    last = STAGES[j][1][-1]
                    arrow = "─▶ "
                    line += paint.ink(arrow, GOOD) if state["steps"][last]["state"] == "done" else paint.dim(arrow)
                else:
                    line += " " * gap
        out.append(line)
    return out


def side_panel(data: dict, paint: Paint, state: dict | None, width: int, height: int) -> list[str]:
    inner, rows = width - 4, []
    info = {key: state["steps"][key]["info"] for key in ORDER} if state else {}
    pair = lambda name, value: paint.dim(fit(name, 10)) + (paint(value, "1") if value != "-" else paint.dim(value))
    rows.append(paint("이번 루프", "1"))
    rows += [pair("기준 점수", info.get("dev", {}).get("score", "-")),
             pair("칸 일치", info.get("dev", {}).get("strict", "-")),
             pair("공격 문항", info.get("judge2", {}).get("kept", "-") + ("건" if info.get("judge2", {}).get("kept") else "")),
             pair("이번 공격", info.get("before", {}).get("attack", "-")),
             pair("지난 공격", info.get("before", {}).get("past", "-")),
             pair("바꾼 파일", info.get("defend", {}).get("files", "-") + ("개" if info.get("defend", {}).get("files") else ""))]
    rows += ["", paint("끝난 루프", "1") + paint.dim(" 기준")]
    results = data["results"]
    if not results:
        rows.append(paint.dim("아직 없음"))
    for row in results[-5:]:
        kept = row.get("kept", "-")
        mark, tone = {"1": ("✓", GOOD), "0": ("✗", WARN), "done": ("★", GOOD)}.get(kept, ("·", GRAY))
        rows.append(paint.ink(mark, tone) + f" {fit(row.get('loop', '-'), 3)}"
                    + f"{row.get('dev_before') or '-'}" + paint.dim(" → ") + f"{row.get('dev_after') or '-'}")
    faults = data["faults"]
    rows += ["", paint("운영 오류", "1") + (paint.ink(f"  {len(faults)}건", BAD) if faults else paint.dim("  없음"))]
    if faults:
        last = faults[-1]
        rows += [paint.dim(line) for line in wrap(f"마지막 {last.get('time', '')[-5:]} · {last.get('kind', '-')} · "
                                                    f"{last.get('reason', '')}", inner, 3)]
    rows = [fit(ANSI.sub("", r), inner) if seen_width(r) > inner else r for r in rows]
    rows = rows[:height - 2] + [""] * max(0, height - 2 - len(rows))
    return box(paint, width, rows, None, "점수 · 기록")


def role_bar(paint: Paint, state: dict | None, run: dict, now: datetime) -> str:
    parts = []
    for who in ("평가자", "공격자", "방어자", "스크립트"):
        keys = [k for k, w, _ in PHASES if w == who and (k != "search" or (state and state["steps"][k]["seen"]))]
        kinds = [state["steps"][k]["state"] for k in keys] if state else []
        running = next((k for k in keys if state and state["steps"][k]["state"] == "running"), None)
        if running and not run["final"]:
            step = state["steps"][running]
            text, tone = ("다시 실행 대기", WARN) if step["wait"] else ("실행 중", LIVE)
        elif "stopped" in kinds:
            text, tone = "멈춤", BAD
        elif kinds and all(k in ("done", "skipped") for k in kinds):
            text, tone = "끝", GOOD
        else:
            text, tone = "대기", GRAY
        parts.append(paint.ink(who, ROLE[who], True) + " " + paint.ink(f"[{text}]", tone, tone is LIVE))
    return "   ".join(parts)


def board(data: dict, paint: Paint, motion: Motion, width: int, now: datetime, watching: float | None) -> list[str]:
    if width < 92:  # too narrow for four columns of boxes: the list
        return frame(data, paint, motion, width, now, watching)
    run, state = data["run"], data["state"]
    width = min(width, 140)
    lines: list[str] = []
    # the head: what this is, which loop, the clock; the colours of the four roles
    if run is None:
        title, tone = "진행 로그가 아직 없음", GRAY
    elif state is None:
        title, tone = "실행이 끝남", GOOD
    else:
        loop, ended = state["loop"], state["ended"]
        start = data["begun"] or state["first"]
        if ended:
            title, tone = f"루프 {loop} · 끝 · kept={ended[0]}", GOOD if ended[0] == "1" else WARN
        elif run["final"]:
            title = f"루프 {loop} · " + ("운영 오류로 멈춤" if run["final"].startswith("운영 오류") else "멈춤")
            tone = BAD if run["final"].startswith("운영 오류") else WARN
        else:
            title, tone = f"루프 {loop} · 진행 중 " + (span((now - start).total_seconds()) if start else ""), LIVE
    lamp = paint.block(2, mix(mix(GOOD, (0, 0, 0), 0.65), mix(GOOD, WHITE, 0.25), motion.pulse())
                       if run and not run["final"] else tone)
    left = (lamp + " " if lamp else "") + paint("KOSIS 개선 루프", "1") + paint.dim("  ·  ") + paint.ink(title, tone, True)
    clock = paint.dim(f"{now:%H:%M:%S}")
    lines.append(left + " " * max(2, width - seen_width(left) - 8) + clock)
    legend = "   ".join(paint.ink("■", ROLE[w]) + " " + paint.dim(w) for w in ROLE)
    if run is not None and data["log"] is not None:
        log = data["log"]
        shown = log.relative_to(ROOT).as_posix() if log.is_relative_to(ROOT) else str(log)
        tail = paint.dim(fit(shown, max(10, width - seen_width(legend) - 4), False))
        legend += " " * max(2, width - seen_width(legend) - seen_width(tail)) + tail
    lines += [legend, paint.dim("─" * width)]
    if run is None:
        lines += ["", "loop/orchestrator/run-N.log 가 생기면 여기에 루프가 그려짐.",
                  paint.dim("운영 세션이 루프를 실행하면 생김. 직접 실행할 때: bash scripts/loop.sh --once > loop/orchestrator/run-1.log 2>&1")]
        return lines
    # the body: the panel on the left when there is room, the four stages side by side, each a column of boxes
    panel = 24 if width >= 118 else 0
    flow = width - panel - (2 if panel else 0)
    body = columns(paint, motion, state, run, flow, now) if state else []
    if panel:
        side = side_panel(data, paint, state, panel, max(len(body), 16))
        body += [""] * (len(side) - len(body))
        body = [pad(s, panel) + "  " + b for s, b in zip(side, body)]
    else:
        body += [""] + side_panel(data, paint, state, min(width, 44), 14)
    lines += body
    # what is happening now, or how the run ended
    lines.append("")
    if run["final"]:
        bad, held = run["final"].startswith("운영 오류"), run["final"].startswith(("사람에게 넘김", "중단"))
        lines += [paint.ink(line, BAD if bad else WARN if held else GOOD, True) for line in wrap(f"실행이 끝남: {run['final']}", width, 2)]
    elif state and state["active"]:
        step, key = state["steps"][state["active"]], state["active"]
        who, what = WHO[key], WHAT[key]
        if step["wait"]:
            text = f"{who} · {what} · 에이전트가 실패해 기다리는 중, {step['wait'][0]:%H:%M}쯤 다시 실행"
        elif step["agent"]:
            text = f"{who} · {what} · 에이전트 실행 중 {span((now - step['agent'][1]).total_seconds())}째 ({step['agent'][0]})"
        elif who == "스크립트":
            text = f"스크립트 · {what} 중 (추정: 이 단계는 시작할 때 줄이 찍히지 않음)"
        else:
            text = f"{who} · {what} · 에이전트가 끝나 스크립트가 결과를 확인하는 중"
        quiet = span((now - data["written"]).total_seconds())
        after = f"   마지막 줄 {quiet} 전"
        lines.append(motion.sweep(fit("지금  " + text, width - wide(after), False), LIVE, paint, 3.2) + paint.dim(after))
    # the last lines of the log, in a box
    events = run["events"][-4:]
    rows = []
    for e in events:
        step = e["step"]
        key = place(step, None)[0]
        who = WHO.get(key) if key else None
        body_text = fit(e["raw"][11:].replace(f"loop {e['loop']} · ", ""), width - 16, False)
        rows.append(paint.dim(e["raw"][1:9]) + "  " + (paint.ink(body_text, ROLE[who]) if who else body_text))
    lines += box(paint, width, rows or [paint.dim("아직 없음")], None, "진행 로그")
    # the roles at the bottom, like a status line
    bar = role_bar(paint, state, run, now)
    note = paint.dim(f"{watching:g}초마다 읽음 · Ctrl+C" if watching and width - seen_width(bar) >= 24 else "")
    lines.append(bar + (" " * max(2, width - seen_width(bar) - seen_width(note)) + note if note else ""))
    return lines


def ansi_ready() -> bool:
    if not sys.stdout.isatty():
        return False
    if os.name != "nt":
        return True
    try:  # Windows: the console takes the colour codes only when told to
        import ctypes
        kernel, mode = ctypes.windll.kernel32, ctypes.c_uint()
        handle = kernel.GetStdHandle(-11)
        return bool(kernel.GetConsoleMode(handle, ctypes.byref(mode)) and kernel.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="개선 루프의 진행 화면 (읽기 전용)")
    parser.add_argument("--log", type=Path, help="scripts/loop.sh 의 출력을 받은 파일. 없으면 loop/orchestrator 의 가장 새 run-*.log")
    parser.add_argument("--once", action="store_true", help="한 번만 그리고 끝냄")
    parser.add_argument("--interval", type=float, default=2.0, help="파일을 다시 읽는 간격(초)")
    parser.add_argument("--still", action="store_true", help="색은 쓰되 움직임은 끔")
    parser.add_argument("--no-color", action="store_true", help="색과 움직임을 모두 끔")
    parser.add_argument("--list", action="store_true", help="상자 그림 대신 전의 목록 화면")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    ansi = ansi_ready()
    full = os.name == "nt" or os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit")
    paint = Paint(ansi and not args.no_color, full)
    motion = Motion(paint.on and not args.still and not args.once)
    interval = max(0.5, args.interval)
    draw = frame if args.list else board

    def size_now() -> tuple[int, int]:
        size = shutil.get_terminal_size((110, 40))
        return max(60, size.columns - 1), size.lines

    if args.once:
        try:
            print("\n".join(draw(load(args.log), paint, motion, size_now()[0], datetime.now(), None)))
        except OSError as problem:
            print(f"읽는 중 오류: {problem}")
        return 0

    data, problem, read_at, shown, size_was = None, "", 0.0, [], None
    try:
        if ansi:
            sys.stdout.write("\x1b[?25l")  # (the cursor is hidden while the screen is up)
        while True:
            motion.tick()
            if data is None or motion.t - read_at >= interval:
                try:
                    data, problem = load(args.log), ""
                    motion.note(data)
                except OSError as error:  # the file was being replaced just then: the next round reads it
                    problem = f"읽는 중 오류 (다음에 다시 읽음): {error}"
                read_at = motion.t
            width, height = size_now()
            lines = draw(data, paint, motion, width, datetime.now(), args.interval) if data else []
            if problem:
                lines = lines[:2] + [paint.ink(fit(problem, width, False), WARN)] + lines[2:]
            if len(lines) > height - 1:  # a frame taller than the window would scroll: the top part is kept
                lines = lines[:max(5, height - 2)] + [paint.dim("(창이 낮아 아래가 잘림. 창을 키우면 다 보임)")]
            if not ansi:
                os.system("cls" if os.name == "nt" else "clear")
                sys.stdout.write("\n".join(lines) + "\n")
                sys.stdout.flush()
                time.sleep(interval)
                continue
            out = []
            if (width, height) != size_was:  # the first frame, or the window changed: everything is drawn again
                out, shown, size_was = ["\x1b[2J"], [], (width, height)
            # Only the lines that changed are written again, each at its own row: nothing flickers.
            out += [f"\x1b[{row};1H{line}\x1b[K" for row, line in enumerate(lines, 1) if row > len(shown) or shown[row - 1] != line]
            if len(lines) < len(shown):
                out.append(f"\x1b[{len(lines) + 1};1H\x1b[J")
            if out:
                sys.stdout.write("".join(out))
                sys.stdout.flush()
            shown = lines
            time.sleep(0.15 if motion.on else 0.5)
    except KeyboardInterrupt:
        return 0
    finally:
        if ansi:  # the last frame stays on the screen, the prompt comes back under it
            sys.stdout.write(f"\x1b[0m\x1b[{len(shown) + 1};1H\x1b[?25h")
            sys.stdout.flush()


if __name__ == "__main__":
    sys.exit(main())
