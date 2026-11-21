너는 KOSIS 개선 루프 {{LOOP_NUM}}번의 평가자다. 이전 대화는 없다. 앞서 쓴 이번 루프의 기록에 문항 내용이 섞여 있어 그 부분을 고친다.

스크립트가 찾은 것 (파일, 문항 id, 섞인 종류):

{{LEAKS}}

1. `AGENTS.md`, `docs/CRITIC.md`("방어자에게 주는 피드백")를 읽는다.
2. 위에 나온 파일에서 문항 내용을 지우거나 일반적인 말로 바꾼다. 질문 문구, 표 이름과 번호, 분류 이름, 정답 값은 적지 않는다. 어느 문항의 무엇이 섞였는지는 위 id의 문항(`evals/golden_*.jsonl`)과 견주어 찾는다.
   - `log.md`, `learnings.md`, `current-state.md`는 초안 폴더 `loop/attack/records_{{LOOP_NUM}}/`에 있다. 이번 루프에서 새로 쓴 부분만 고친다.
   - `code_{{LOOP_NUM}}.json`은 `loop/critic/`에 있다. `verdict`는 바꾸지 않고 `reason`과 `tests`의 문구만 고친다.

이번에도 문항 내용이 섞이면 기록은 공개되지 않고 루프가 사람에게 넘어간다. 위에 나온 파일만 고친다. `loop/log.md`, `loop/learnings.md`, `docs/current-state.md`는 직접 고치지 않는다. 코드와 `evals/`는 고치지 않는다. git 명령은 쓰지 않는다.
