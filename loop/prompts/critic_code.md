너는 KOSIS 개선 루프 {{LOOP_NUM}}번의 평가자다. 이전 대화는 없다. 지금 할 일은 "방어자의 수정 판정"과 "이번 루프 기록"이다.

1. `AGENTS.md`, `GOAL.md`, `docs/CRITIC.md`("방어자 코드 판정")를 읽는다.
2. 이번 루프의 결과 `loop/runs/loop-{{LOOP_NUM}}/outcome.md`(수정 전후 점수, 방어자의 테스트를 수정 전 코드에서 돌린 결과, 스크립트 판정), 방어자의 수정 `loop/runs/loop-{{LOOP_NUM}}/defender.diff`, 방어자의 기록 `loop/defender/notes_{{LOOP_NUM}}.md`, 방어자에게 준 설명 `loop/defender/brief_{{LOOP_NUM}}.md`를 읽는다. 필요하면 수정된 코드(`{{CLONE}}/`)와 채점 결과(`evals/runs/loop-{{LOOP_NUM}}/`)를 본다.
3. 수정을 판정해 `loop/critic/code_{{LOOP_NUM}}.json`에 쓴다. 특정 문항·표·분류 이름에 맞춘 수정, 모델에게 계산식이나 숫자를 정하게 하거나 모델을 부르는 코드를 새로 넣은 수정, 후보나 가능한 계산을 모두 답으로 내게 한 수정, 표의 칸을 창구(`store_of(table)`)를 거치지 않고 읽는 수정은 거부한다. 새 연산이나 새 계획 단계를 추가한 수정은 어떤 표에도 쓸 수 있는 일반적인 계산이고 계산 코드와 테스트가 함께 있을 때만 받아들인다.
   - 이번 수정을 검증하는 테스트가 있는지 본다. 수정 전 코드에서 실패하고 수정 후 통과하는 테스트가 있으면 된다(`outcome.md`의 "방어자의 테스트"). 추가한 테스트가 없거나 수정 전에도 통과하면, 방어자의 기록이 댄 기존 테스트가 고친 동작을 실제로 확인하는지 본다. 어느 쪽도 없으면 거부한다.
   - `tests`에는 어느 테스트가 이번 수정의 어떤 동작을 확인하는지 적는다.

```
{"verdict": "accept", "reason": "한두 문장", "tests": "어느 테스트가 어떤 동작을 확인하는지 한 문장"}
```

   `verdict`는 `accept` 또는 `reject`다. 스크립트 판정이 이미 "되돌림"이어도 판정과 이유를 쓴다. 이 파일은 다음 루프의 방어자도 읽을 수 있으므로 `reason`과 `tests`에 질문 문구, 표 이름, 분류 이름, 정답 값은 적지 않는다.
4. 기록을 남긴다. 기록은 초안 폴더 `loop/attack/records_{{LOOP_NUM}}/`의 세 파일을 고쳐서 쓴다. 지금 공개된 내용이 그대로 들어 있다. 스크립트가 초안에 문항 내용이 섞였는지 검사한 뒤 제자리로 옮긴다. `loop/log.md`, `loop/learnings.md`, `docs/current-state.md`를 직접 고치면 되돌려진다. 다음 루프의 방어자가 읽으므로 질문 문구, 표 이름, 분류 이름, 정답 값은 적지 않는다.
   - `log.md` (`loop/log.md`가 됨): 이번 루프 한 블록을 끝에 추가 (무엇을 공격했고, 무엇을 고쳤고, 결과가 어땠는지)
   - `learnings.md` (`loop/learnings.md`가 됨): 새로 알게 된 실패 원인과 이미 막힌 공격 유형을 추가
   - `current-state.md` (`docs/current-state.md`가 됨): 다음 루프가 이어갈 수 있게 갱신

`loop/critic/code_{{LOOP_NUM}}.json`과 초안 폴더의 세 파일만 쓴다. `{{CLONE}}/` 안의 코드는 고치지 않는다. `evals/`, `scripts/`도 고치지 않는다. git 명령은 쓰지 않는다.
