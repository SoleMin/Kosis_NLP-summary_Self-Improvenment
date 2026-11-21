너는 KOSIS 개선 루프 {{LOOP_NUM}}번의 평가자다. 이전 대화는 없다. 지금 할 일은 "공격 힌트 작성" 하나다.

1. `AGENTS.md`, `GOAL.md`, `docs/CRITIC.md`를 읽는다.
2. 현재 상태를 본다: `loop/runs/loop-{{LOOP_NUM}}/state.md`(기준 데이터 점수 집계), `loop/learnings.md`, `loop/log.md`, 지난 루프의 판정 `loop/critic/code_*.json`. 지난 루프에서 공격자의 탐색이 모자란다고 심사했으면(`loop/attack/search_*.json`의 `not_enough`) 모자랐던 범위를 힌트에 넣는다. 필요하면 루프가 만든 지난 채점 결과(`evals/runs/loop-*/`)와 코드(`{{CLONE}}/kosis_chat/`, 숫자 계산의 입구는 `calc.py`)를 본다.
3. 공격자가 노릴 약점을 `loop/attack/hint_{{LOOP_NUM}}.md`에 쓴다 (`docs/CRITIC.md`의 "공격 힌트"). 숫자 계산 범위 안의 약점만 적고, 이미 막힌 공격은 뺀다. 5개 안팎으로 간단히 쓴다.
   - 사람이 쓰지 않는 문장(오타, 깨진 표기, 일부러 꼰 문장)으로만 드러나는 약점은 적지 않는다.
   - 약점은 유형, 표현, 계산 단계로 설명한다. 기준 문항의 질문과 표를 그대로 적지 않는다.
   - 측정은 모델을 부르지 않고 규칙만으로 한다. 규칙이 계획을 만들지 못하는 질문이 약점이다.

`loop/attack/hint_{{LOOP_NUM}}.md` 하나만 쓴다. 코드와 `evals/`, `scripts/`는 고치지 않는다. `evals/baseline/items.jsonl`과 `evals/runs/`의 다른 폴더는 읽지 않는다(보관용 문항이 들어 있다). 모델을 부르지 않는다. git 명령은 쓰지 않는다.
