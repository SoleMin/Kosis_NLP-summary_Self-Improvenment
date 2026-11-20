너는 KOSIS 루프의 한 iteration을 맡았다. 이전 대화는 없다.

1. `AGENTS.md`의 규칙을 따른다.
2. `GOAL.md`, `docs/current-state.md`, `TASKS.md`, `loop/learnings.md`를 읽는다.
3. `todo` 작업 하나를 골라 ID를 `loop/.current_task`에 쓰고, 그 작업만 끝낸다.
4. `bash scripts/verify.sh`를 통과시킨다.
5. `TASKS.md`, `docs/current-state.md`, `loop/log.md`(필요하면 `loop/learnings.md`)를 갱신하고 끝낸다.

판단할 수 없으면 `loop/blocked.md`에 이유를 쓰고 끝낸다. git 명령은 쓰지 않는다.
