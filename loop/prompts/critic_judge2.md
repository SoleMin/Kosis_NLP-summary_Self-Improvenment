너는 KOSIS 개선 루프 {{LOOP_NUM}}번의 평가자다. 이전 대화는 없다. 지금 할 일은 "공격 문항 2차 판정" 하나다. 스크립트가 표에서 값을 채우고 검증을 마쳤다.

1. `AGENTS.md`, `docs/CRITIC.md`("공격 데이터 판정")를 읽는다.
2. 값이 채워진 문항 `evals/golden_{{LOOP_NUM}}.jsonl`을 한 건씩 판정한다(형식은 `evals/BASELINE.md`의 "문항 형식"). 채워진 값이 질문의 답으로 맞는지, 단위와 방향(증가·감소, 큰 쪽·작은 쪽)이 질문과 같은지 본다. 스크립트가 탈락시킨 문항과 이유는 `evals/golden_{{LOOP_NUM}}.report.json`에 있다.
3. 판정을 `loop/attack/judge2_{{LOOP_NUM}}.jsonl`에 쓴다. 문항마다 한 줄이고 형식은 1차 판정과 같다. 모든 문항에 판정을 쓴다. 판정이 빠진 문항은 부적절로 세지 않고 그 문항만 다시 요청받는다. 문항 파일에 없는 id는 쓰지 않는다.

```
{"id": "L{{LOOP_NUM}}-001", "ok": true, "reason": ""}
```

`loop/attack/judge2_{{LOOP_NUM}}.jsonl` 하나만 쓴다. 문항 파일, 코드, `evals/`는 고치지 않는다. git 명령은 쓰지 않는다.
