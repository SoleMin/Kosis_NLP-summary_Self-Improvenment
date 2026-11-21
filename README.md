# kosis_loop

Phase 1의 숫자 계산을 개선하는 루프. 평가자, 공격자, 방어자 세 에이전트를 `scripts/loop.sh`가 차례로 실행한다.
한 루프에서 공격자가 약점을 찌르는 문항을 만들고, 방어자가 코드를 고치고, 점수가 조건을 만족하면 커밋하고 아니면 되돌린다.

## 시작하기

1. `loop/config.env`에 설정을 넣는다. 넣을 항목은 `loop/config.example.env`에 있다 (에이전트 명령, 표가 있는 폴더, 루프 수).
2. Phase 1 클론(`kosis-ai-nlp-summary-T1-30/`)이 루프 전용 브랜치에 있고 커밋하지 않은 변경이 없는지 확인한다.
3. 설정을 점검한다.
   ```bash
   bash scripts/loop.sh --check
   ```
4. 한 루프만 돌려 본다. 문제가 없으면 옵션 없이 실행한다. 중간에 멈추려면 `loop/STOP` 파일을 만든다.
   ```bash
   bash scripts/loop.sh --once
   bash scripts/loop.sh
   ```

실행이 중간에 끊겨 `evals/` 폴더가 보이지 않으면 `bash scripts/loop.sh --restore`로 되돌린다.

## 한 루프의 순서

| 순서 | 누가 | 하는 일 | 결과 파일 |
|---|---|---|---|
| 0 | 스크립트 | 기준 데이터(판정용) 점수 측정. 측정은 모두 규칙만으로 함(모델 호출 없음, `EVAL_BACKEND=rules`) | `evals/runs/loop-N/dev-before/` |
| 1 | 평가자 | 공격 힌트 | `loop/attack/hint_N.md` |
| 2 | 공격자 | 공격 문항 (질문, 칸 좌표, 계산식). 목표 건수를 못 채우면 탐색 보고 | `loop/attack/spec_N.jsonl`, `spec_N.search.md` |
| 3 | 평가자 | 1차 판정. 판정이 빠진 문항은 다시 요청. 절반 이상 부적절이면 한 번 다시 작성 | `loop/attack/judge1_N.jsonl` |
| 4 | 스크립트 | 표에서 값 채우기와 검증 | `evals/golden_N.jsonl` |
| 5 | 평가자 | 2차 판정. 탈락 문항 제외. 5건 미만이 남으면 공격 세트로 등록하지 않고 끝냄 | `loop/attack/judge2_N.jsonl` |
| 6 | 스크립트 | 수정 전 점수 (이번 공격, 지난 공격) | `evals/runs/loop-N/` |
| 7 | 평가자 | 방어자용 약점 설명. 스크립트가 문항 유출을 검사하고 집계를 붙임 | `loop/defender/brief_N.md` |
| 8 | 방어자 | 코드 수정. 그동안 `evals/`, `loop/attack/`, `loop/runs/`는 보관 폴더로 옮김 | 클론, `loop/defender/notes_N.md` |
| 9 | 스크립트 | 허용 범위, 공용 형식 파일 검사, 접근 흔적, verify, 방어자의 테스트를 수정 전 코드에서 돌려 보기, 수정 후 점수, 유지 조건 | `loop/runs/loop-N/outcome.md` |
| 10 | 평가자 | 수정 판정과 기록. 기록은 초안에 쓰고 스크립트가 문항 유출을 검사한 뒤 공개. 수정이 없는 루프도 기록 | `loop/critic/code_N.json`, 초안 `loop/attack/records_N/` → `loop/log.md`, `loop/learnings.md`, `docs/current-state.md` |
| 11 | 스크립트 | 클론에서 커밋 또는 되돌림, 점수표 한 줄 | `loop/results.tsv` |

유지 조건: 기준 데이터 점수가 떨어지지 않음, 이번 공격 문항 점수가 오름, 지난 공격 문항 점수가 떨어지지 않음, 평가자가 수정을 받아들임.

## 파일 지도

| 역할 | 파일 | 누가 쓰나 |
|---|---|---|
| 운영 규칙 | `AGENTS.md` (`CLAUDE.md`가 가져옴) | 사람 |
| 역할 지시문 | `docs/CRITIC.md`, `docs/ATTACKER.md`, `docs/DEFENDER.md` | 사람 |
| 목표 | `GOAL.md` | 사람 |
| 단계별 지시문 | `loop/prompts/*.md` | 사람 |
| 방어자 수정 허용 범위 | `loop/defender_allow.txt` | 사람 |
| 설정 | `loop/config.env` (예시 `loop/config.example.env`) | 사람 |
| 채점 | `evals/` (`evals/BASELINE.md` 참고) | 사람 |
| 실행기 | `scripts/loop.sh`, `scripts/loop_main.py`, `scripts/verify.sh` | 사람 |
| 표와 질문 보기 | `scripts/show_table.py` (표의 항목·분류·칸 좌표), `scripts/try_question.py` (질문 하나를 규칙만으로 돌려 보기) | 사람 |
| 공용 형식 파일 검사 | `scripts/contract_check.py` (방어자가 `src/kosis_ai/schemas.py`에서 연산 계획에 더하기만 했는지) | 사람 |
| 강제 장치 | `.claude/settings.json`, `.claude/hooks/protect.js` (Node 필요) | 사람 |
| 현재 상태 | `docs/current-state.md` | 평가자 |
| 반복 기록 | `loop/log.md`, `loop/learnings.md` (평가자가 초안에 쓰고 스크립트가 옮김), `loop/results.tsv`, `loop/faults.tsv` (스크립트) | |
| 사람에게 넘기기 | `loop/blocked.md` (내용이 있으면 루프 정지) | 에이전트, 스크립트 |

루프별 실행 로그와 지시문 사본은 `loop/runs/loop-N/`에 남는다 (git 제외). 문항이 들어 있는 `evals/baseline/items.jsonl`, `evals/golden_*.jsonl`, `evals/runs/`, `loop/attack/`도 git에 올리지 않는다.

## 루프가 멈추는 조건

멈춘 이유는 마지막 줄 `종료: ...`와 기록에서 구별된다.

| 구분 | 언제 | 기록 | 다시 실행하면 |
|---|---|---|---|
| 목표 완료 | 공격자가 정상적으로 끝내고 탐색 보고와 함께 `ATTACK_MIN`건 미만을 냈고, 최종 verify를 통과함 | `loop/results.tsv`에 `kept=done` | 다음 루프 번호 |
| 심사 탈락 | 다시 쓴 공격 문항도 절반 이상 부적절, 또는 판정·값 채우기를 통과한 문항이 `ATTACK_MIN`건 미만. 약점 부족으로 끝났는데 최종 verify가 실패한 경우도 여기 | `kept=stop` | 다음 루프 번호 |
| 사람에게 넘김 | `loop/blocked.md`에 내용이 생김 (에이전트가 쓰거나, 평가자의 기록에 문항 내용이 섞여 스크립트가 씀) | `kept=blocked` (기록 유출은 그 루프의 결과 줄 그대로) | 파일을 비울 때까지 시작하지 않음 |
| 운영 오류 | 에이전트가 실패하거나 시간 초과로 끝나 결과 파일이 없음, 결과 파일 형식이 깨짐(문항 파일의 깨진 줄, 채워지지 않는 판정), 측정·값 채우기 스크립트 실패, 보호 파일 변경, 방어자용 설명의 문항 유출, 실행기 오류 | `loop/results.tsv`에 줄 없음. `loop/faults.tsv`에 한 줄 (시각, 루프, 종류, 이유). 종료 코드 1 | 같은 루프 번호부터 다시 |
| 한도 | `MAX_ITER` 도달, `PATIENCE`번 연속으로 수정을 유지하지 못함, `loop/STOP` 파일 | | 다음 루프 번호 |

- 규칙만으로 잴 때 코드가 오류로 끝난 문항은 0점으로 세고 계속한다. 운영 오류가 아니다.
- 공격 문항이 `ATTACK_MIN`건 미만이어도 공격자의 실행이 끊겼거나 탐색 보고가 없으면 목표 완료로 보지 않는다. 운영 오류다.
- 운영 오류나 사람에게 넘김으로 끝날 때 클론에 방어자의 수정이 남아 있으면 `loop/runs/loop-N/unfinished.diff`(넘긴 경우 `blocked.diff`)에 저장하고 클론을 되돌린다. 커밋하지 않는다. `git apply`로 다시 적용해 볼 수 있다.
- 시작할 때 기록 파일(`loop/log.md`, `loop/learnings.md`, `docs/current-state.md`)이 마지막 검사 뒤에 바뀌었으면 문항 내용이 섞였는지 다시 검사한다. 섞여 있으면 고칠 때까지 시작하지 않는다.
- 에이전트의 실패(사용량 한도, 연결 끊김)는 먼저 기다렸다가 다시 실행한다. 아래 "에이전트가 실패했을 때"

## 평가자의 기록에 문항 내용이 섞였을 때

평가자는 기록을 초안(`loop/attack/records_N/`)에 쓴다. 스크립트는 초안에서 이번에 더해진 줄을 이번 공격 문항과 지난 공격 문항에 견주어 검사하고, 깨끗할 때만 `loop/log.md`, `loop/learnings.md`, `docs/current-state.md`로 옮긴다. 섞였으면 평가자에게 한 번 다시 쓰게 한다. 그래도 남으면

- 기록을 공개하지 않는다. 초안은 방어자가 볼 수 없는 `loop/attack/records_N/`에 그대로 둔다.
- `loop/blocked.md`에 걸린 파일과 문항 id, 할 일을 적는다. 그 루프의 결과(유지 또는 되돌림)는 그대로 기록하고 루프를 멈춘다.
- 사람이 초안에서 문항 내용을 지우고 세 파일에 옮긴 뒤 `loop/blocked.md`를 비우면 다시 시작한다. 옮긴 파일은 시작할 때 다시 검사된다.

## 에이전트가 실패했을 때

에이전트가 자기 단계의 결과 파일을 쓰지 못한 채 실패 종료 코드로 끝나거나, 로그 끝에 한도 문구(usage limit, rate limit 등)가 있으면 그 단계는 실패로 본다. 방어자는 종료 코드가 실패이면 코드를 일부 고쳤더라도 실패로 보고, 클론을 되돌린 뒤 다시 실행한다.

- 실패한 단계는 `AGENT_RETRY_WAIT`초(30분) 뒤에 다시 실행한다. 최대 `AGENT_RETRY_MAX`번(12번, 6시간)까지 한다.
- 다시 실행한 것이 10분 넘게 일한 뒤 또 실패하면 더 기다리지 않는다.
- 끝내 안 되면 그 루프를 `loop/results.tsv`에 기록하지 않고 멈춘다. 연속 실패 횟수에도 세지 않는다. 다시 실행하면 같은 루프 번호부터 처음부터 한다(기준 점수는 다시 재지 않고 저장된 것을 씀).
- 기다리는 중에 `loop/STOP` 파일을 만들면 바로 멈춘다.
- `AGENT_TIMEOUT`으로 끊긴 실행은 다시 실행하지 않는다. 그 단계의 결과 파일이 있으면 그대로 진행하고, 없으면 운영 오류로 멈춘다.

## 한계

- 보관용 문항은 문서의 금지 문구로만 지킨다. 기준 문항 파일(`evals/baseline/items.jsonl`)에 판정용과 함께 들어 있어, 평가자와 공격자가 읽는 것을 막는 장치는 아직 없다.
- 공격 문항의 배분(다중 표 비율, 한 유형·한 표의 비율)은 지침이며 스크립트가 검사하지 않는다.
- "더 찾을 약점이 없음"은 공격자의 탐색 보고가 있는지만 확인한다. 보고의 내용이 충분한지는 스크립트가 판단하지 않는다. 목표 완료로 끝난 뒤 사람이 `loop/attack/spec_N.search.md`를 읽어 확인한다.
- 방어자의 테스트를 수정 전 코드에서 돌려 보는 검사는 표 색인 코드(`kosis_chat/shared/`, `kosis_chat/prepare/index.py`)가 바뀐 루프에서는 하지 않는다 (큰 표의 저장된 색인을 다시 만들게 되기 때문). 결과는 평가자의 판정 자료이고 유지 조건이 아니다.
- 기록의 문항 유출 검사는 이번에 더해진 줄만 본다. 질문 문구의 일부, 표 번호, 큰 정답 값을 찾는 방식이라 돌려 쓴 표현은 잡지 못한다.
- `protect.js`는 Claude Code에서만 동작한다. Codex로 도는 역할은 실행 뒤 스크립트의 검사(보호 파일 해시, 클론 변경)로만 막는다.
- Bash 명령 검사는 명령 문자열만 보고 판단해서 우회가 가능하다. 방어자의 읽기는 폴더를 옮기는 것으로 막는다.
- 방어자의 접근 흔적 검사는 Claude Code의 stream-json 로그에서만 된다.
- 비용 한도는 `MAX_ITER`와 에이전트 명령의 `--max-turns`, `AGENT_TIMEOUT`으로만 제한한다.
