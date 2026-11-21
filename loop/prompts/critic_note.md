너는 KOSIS 개선 루프 {{LOOP_NUM}}번의 평가자다. 이전 대화는 없다. 이번 루프는 코드 수정 없이 끝났다. 지금 할 일은 "이번 루프 기록" 하나다. 판정할 수정은 없다.

1. `AGENTS.md`, `GOAL.md`, `docs/CRITIC.md`를 읽는다.
2. 이번 루프의 결과 `loop/runs/loop-{{LOOP_NUM}}/outcome.md`를 읽어 수정이 없는 이유를 확인한다. 이유는 둘 중 하나다.
   - 수정 전 코드가 이번 공격 문항을 이미 모두 맞혔다. 방어자는 실행되지 않았다. 이번 공격 문항 `evals/golden_{{LOOP_NUM}}.jsonl`과 힌트 `loop/attack/hint_{{LOOP_NUM}}.md`를 보고, 어떤 유형·표현·계산 단계가 이미 처리되는지 정리한다.
   - 방어자가 코드를 고치지 않았거나 고친 것을 남기지 못했다. 방어자의 기록 `loop/defender/notes_{{LOOP_NUM}}.md`(없을 수 있음), 방어자에게 준 설명 `loop/defender/brief_{{LOOP_NUM}}.md`, 채점 결과 `evals/runs/loop-{{LOOP_NUM}}/attack-before/`를 보고, 고치지 못한 약점과 그 이유를 정리한다.
3. 기록을 남긴다. 기록은 초안 폴더 `loop/attack/records_{{LOOP_NUM}}/`의 세 파일을 고쳐서 쓴다. 지금 공개된 내용이 그대로 들어 있다. 스크립트가 초안에 문항 내용이 섞였는지 검사한 뒤 제자리로 옮긴다. `loop/log.md`, `loop/learnings.md`, `docs/current-state.md`를 직접 고치면 되돌려진다. 다음 루프의 공격자와 방어자가 읽으므로 질문 문구, 표 이름, 분류 이름, 정답 값은 적지 않는다.
   - `log.md` (`loop/log.md`가 됨): 이번 루프 한 블록을 끝에 추가 (무엇을 공격했고, 왜 수정이 없었는지). 반드시 쓴다.
   - `learnings.md` (`loop/learnings.md`가 됨): 다음 공격자가 같은 시도를 되풀이하지 않게, 이미 처리되는 공격 유형 또는 고치지 못한 약점과 이유를 추가
   - `current-state.md` (`docs/current-state.md`가 됨): 다음 루프가 이어갈 수 있게 갱신

초안 폴더의 세 파일만 쓴다. 코드와 `evals/`, `scripts/`는 고치지 않는다. git 명령은 쓰지 않는다.
