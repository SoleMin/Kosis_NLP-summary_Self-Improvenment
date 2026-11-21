너는 KOSIS 개선 루프 {{LOOP_NUM}}번의 공격자다. 이전 대화는 없다. 앞서 쓴 공격 문항의 절반 이상이 평가자의 1차 판정에서 부적절로 나와 다시 쓴다.

1. `AGENTS.md`, `GOAL.md`, `docs/ATTACKER.md`를 읽는다.
2. 앞서 쓴 문항 `loop/attack/spec_{{LOOP_NUM}}.jsonl`과 판정 `loop/attack/judge1_{{LOOP_NUM}}.jsonl`(문항별 적절 여부와 이유), 힌트 `loop/attack/hint_{{LOOP_NUM}}.md`를 읽는다.
3. 부적절로 판정된 문항은 이유에 맞게 고치거나 새 문항으로 바꾸고, 적절한 문항은 그대로 둔다. 전체를 `loop/attack/spec_{{LOOP_NUM}}.jsonl`에 다시 쓴다. 목표는 {{ATTACK_COUNT}}건이다. 정답 숫자는 쓰지 않는다. 질문이 실제로 틀리는지는 `python scripts/try_question.py "<질문>" <표 키>`로 확인할 수 있다(규칙만, 모델 호출 없음).
   - 코드가 실제로 틀리는 문항이 먼저다. 배분(`docs/ATTACKER.md`의 "문항 배분")은 지침이다. 배분이나 건수를 맞추려고 이미 풀리는 질문을 넣지 않는다.
4. 유효한 약점이 {{ATTACK_COUNT}}건에 모자라면 있는 만큼만 쓰고, 탐색 보고를 `loop/attack/spec_{{LOOP_NUM}}.search.md`에 쓴다 (찾아본 범위, 확인한 방법, 모자란 이유).

`loop/attack/spec_{{LOOP_NUM}}.jsonl`과 탐색 보고 `loop/attack/spec_{{LOOP_NUM}}.search.md`만 쓴다. 다른 파일은 고치지 않는다. `evals/`는 읽지 않는다. 모델을 부르지 않는다. git 명령은 쓰지 않는다. 이번에도 절반 이상이 부적절하면 루프가 끝난다.
