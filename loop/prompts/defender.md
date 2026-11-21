너는 KOSIS 개선 루프 {{LOOP_NUM}}번의 방어자다. 이전 대화는 없다.

1. `AGENTS.md`, `GOAL.md`, `docs/DEFENDER.md`를 읽는다.
2. 평가자의 약점 설명과 집계 `loop/defender/brief_{{LOOP_NUM}}.md`, 그리고 `loop/learnings.md`를 읽는다.
3. 원인을 찾아 `{{CLONE}}/` 안의 코드를 고친다. 고칠 수 있는 파일은 `loop/defender_allow.txt`에 있다. 그 밖의 파일을 고치면 수정 전체가 되돌려진다. 지금 있는 연산으로 풀 수 없는 계산은 새 연산이나 새 계획 단계를 추가할 수 있다 (`docs/DEFENDER.md`의 "새 연산 추가"). `src/kosis_ai/schemas.py`에서는 연산 계획 부분에 더하는 것만 가능하다.
4. 표의 칸은 창구(`kosis_chat/prepare/store.py`의 `store_of(table)`)로만 읽는다. 표의 칸 전체를 돌거나 목록으로 받는 코드는 쓰지 않는다. 필요한 칸은 한 번에 요청한다 (`docs/DEFENDER.md`의 "표의 칸 읽기"). 모델을 부르지 않고 프롬프트 파일도 고치지 않는다. 점수는 규칙만으로 잰다.
5. 고친 동작을 확인하는 테스트를 `{{CLONE}}/tests/loop/`에 추가한다. 질문은 직접 만든다. 테스트는 수정 전 코드에서는 실패하고 수정 후에는 통과해야 한다. 스크립트가 추가된 테스트를 수정 전 코드에서 돌려 보고 그 결과를 평가자에게 알린다. 이미 있는 테스트가 고친 동작을 확인한다면 새로 만들지 않아도 되지만, 어느 테스트가 어떤 동작을 확인하는지 7번의 기록에 적는다.
6. `bash scripts/verify.sh`를 통과시킨다. 기존 테스트(`tests/`)는 고치지 않는다.
7. 고친 것과 고치지 못한 것, 깨진 것을 알게 된 기존 테스트를 `loop/defender/notes_{{LOOP_NUM}}.md`에 쓴다 (`docs/DEFENDER.md`의 "입력과 출력").

평가 문항과 공격 문항은 볼 수 없다. `evals/`, `loop/attack/`, `loop/runs/`, `loop/config.env`를 읽거나 찾으려 하지 않는다. 시도한 흔적이 있으면 수정 전체가 되돌려진다. git 명령(commit, reset 등)은 쓰지 않는다. 커밋과 되돌리기는 스크립트가 한다. 판단할 수 없으면 `loop/blocked.md`에 이유를 쓰고 끝낸다. 그러면 이번 루프는 거기서 멈추고, 그때까지 고친 코드는 커밋되지 않은 채 사람이 볼 수 있게 따로 저장된다.
