# kosis_loop

하네스(Claude Code 또는 Codex CLI)로 돌리는 루프 엔지니어링 초안.
`scripts/loop.sh`가 에이전트를 한 번씩 실행하고, 검증하고, 점수가 오르면 커밋하고 떨어지면 되돌린다.

## 시작하기

1. `GOAL.md`에 목표·지표·완료 기준을 쓴다.
2. `TASKS.md`에 작업을 작게 쪼개 넣는다.
3. `loop/config.env`에 `VERIFY_CMD`, `EVAL_CMD`, 멈춤 조건을 채운다.
4. `evals/`에 골든셋과 채점 스크립트를 둔다.
5. git으로 첫 커밋을 만든다.
   ```bash
   git init && git add -A && git commit -m "loop scaffold"
   ```
6. 루프를 실행한다. 중간에 멈추려면 `loop/STOP` 파일을 만든다.
   ```bash
   bash scripts/loop.sh
   ```

## 파일 지도

| 역할 | 파일 | 누가 쓰나 |
|---|---|---|
| 운영 규칙 | `AGENTS.md` (`CLAUDE.md`가 가져옴) | 사람 |
| 역할 지시문 | `docs/CRITIC.md`, `docs/ATTACKER.md`, `docs/DEFENDER.md` | 사람 |
| 목표 | `GOAL.md` | 사람 |
| 작업 목록 | `TASKS.md` | 사람 + 에이전트 |
| 현재 상태 | `docs/current-state.md` | 에이전트 |
| 검증 기준 | `scripts/verify.sh`, `scripts/eval.sh`, `evals/` | 사람 |
| 반복 기록 | `loop/log.md` (에이전트), `loop/results.tsv` (루프) | |
| 교훈 | `loop/learnings.md` | 에이전트 + 루프(되돌릴 때 자동 기록) |
| 멈춤 조건 | `loop/config.env` | 사람 |
| 강제 장치 | `.claude/settings.json`, `.claude/hooks/protect.js` (Node 필요) | 사람 |
| 실행기 | `scripts/loop.sh`, `loop/prompt.md` | 사람 |
| 초안 검토 메모 | `REVIEW.md` (루프 시작 전에 지움) | Claude |
| 사람에게 넘기기 | `loop/blocked.md` (내용이 있으면 루프 정지) | 에이전트 |

iteration별 실행 로그는 `loop/runs/iter-N.log`에 남는다 (git 제외).

## 루프가 멈추는 조건

- `MAX_ITER` 도달
- `loop/blocked.md`에 내용이 생김
- `docs/current-state.md` 첫 줄이 `ALL_DONE`
- verify가 `MAX_VERIFY_FAILS`번 연속 실패
- 점수가 `PATIENCE`번 연속 오르지 않음 (`EVAL_CMD`가 있을 때)
- `loop/STOP` 파일이 생김

## 한계

- `protect.js`는 Claude Code에서만 동작한다. `HARNESS=codex`로 돌릴 때는 `.codex/hooks.json`에 같은 훅을 따로 연결해야 한다.
- Bash 명령 검사는 명령 문자열만 보고 판단해서 우회가 가능하다. 채점 파일은 git 이력으로도 확인한다.
- 비용 한도는 `MAX_ITER` × `MAX_TURNS`로만 제한한다.
