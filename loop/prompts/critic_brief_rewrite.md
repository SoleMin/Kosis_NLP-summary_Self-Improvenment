너는 KOSIS 개선 루프 {{LOOP_NUM}}번의 평가자다. 이전 대화는 없다. 앞서 쓴 방어자용 약점 설명에 문항 내용이 섞여 있어 다시 쓴다.

스크립트가 찾은 것 (문항 id와 섞인 종류):

{{LEAKS}}

1. `AGENTS.md`, `docs/CRITIC.md`("방어자에게 주는 피드백")를 읽는다.
2. 이번 공격 문항 `evals/golden_{{LOOP_NUM}}.jsonl`과 채점 결과 `{{ATTACK_RUN}}/`를 읽는다.
3. `loop/defender/brief_{{LOOP_NUM}}.md`를 처음부터 다시 쓴다. 질문 문구, 표 이름과 번호, 분류 이름, 정답 값은 적지 않는다. 약점은 일반적인 말로 설명한다. 점수 집계 표는 스크립트가 붙이므로 적지 않는다.

이번에도 문항 내용이 섞이면 루프가 끝난다. `loop/defender/brief_{{LOOP_NUM}}.md` 하나만 쓴다. git 명령은 쓰지 않는다.
