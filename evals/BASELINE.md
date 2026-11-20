# 기준 데이터와 채점기

루프를 시작하기 전 Phase 1의 기준 점수를 재기 위한 문항 600건과 채점 코드임. 2026-11-19 작성, 2026-11-20 채점 기준 변경(숫자 계산 경로만 채점).

**이 폴더는 방어자에게 보이면 안 됨.** `baseline/items.jsonl`과 `runs/` 아래 파일에는 질문과 정답이 들어 있음.

## 파일

| 파일 | 하는 일 |
|---|---|
| `baseline/items.jsonl` | 기준 문항 600건 (질문, 표, 정답 fact, 차트 가능 여부) |
| `grade.py` | 채점. 표준 라이브러리만 사용하고 Phase 1을 불러오지 않음 |
| `run_calc.py` | 문항마다 Phase 1의 숫자 계산 경로(`kosis_chat/calc.py`의 `calculate`)를 실행해 결과를 `runs/<이름>/outputs.jsonl`에 저장 |
| `run_eval.py` | 실행과 채점을 한 번에 함. stdout 마지막 줄에 0~1 숫자 하나 |
| `check_items.py` | 문항의 숫자를 원본 표와 다시 대조 (표가 바뀌었는지도 확인) |
| `selfcheck.py` | 채점기 자체 점검 38개 |
| `run_phase1.py` | 전체 파이프라인(판정, 모델이 쓴 계획, 문장, 차트 포함) 실행. 11/19 방식이며 `--legacy`로만 씀 |
| `cache/fill.jsonl` | 분류 매핑 모델의 응답 캐시 (실행하면 생김) |
| `build_items.py` | 공격자의 명세(질문, 칸 좌표, 계산식)에 표의 값을 채워 문항 파일(`golden_{LOOP_NUM}.jsonl`)을 만들고 `check_items.py`로 검증. 탈락한 명세와 이유는 `<출력>.report.json` |
| `split_items.py`, `baseline/split.json` | 600건을 판정용(dev) 400건과 보관용(holdout) 200건으로 나눈 id 목록과 그것을 만든 스크립트 |

Phase 1 클론에는 파일 두 개를 더했음. `kosis_chat/calc.py`는 숫자 계산만 하는 고정 함수이고 방어자가 고치지 않음. `kosis_chat/analyze/multi_rules.py`는 다중 표 규칙 계획을 넣을 자리이고 처음에는 "계획 못 만듦"만 돌려줌.

## 실행

`kosis_loop` 폴더에서 실행함. `<작업폴더>`는 `data/`가 들어 있는 폴더(KOSIS-ai-multi-agent)임.

```
python evals/selfcheck.py
python evals/check_items.py --workspace "<작업폴더>"
python evals/run_eval.py --limit 5 --workspace "<작업폴더>"
python evals/run_eval.py --out evals/runs/base-rules --workspace "<작업폴더>"
python evals/run_eval.py --backend hcx --interval 1 --out evals/runs/base-hcx --workspace "<작업폴더>"
```

- `--backend rules`(기본값)는 모델을 부르지 않음. 키가 필요 없고 결과가 항상 같음. 규칙이 계획을 못 만든 문항은 "계획 실패"로 끝남.
- `--backend hcx`는 규칙이 정하지 못한 분류를 모델이 고르게 함(문항당 최대 2번 호출). 형식 검사를 통과한 응답은 `evals/cache/fill.jsonl`에 저장하고 같은 프롬프트에는 다시 묻지 않음. 그래서 코드와 프롬프트가 같으면 점수가 같고, 프롬프트가 바뀐 문항만 새로 부름. 캐시에는 프롬프트의 해시와 응답만 들어 있음. `--no-cache`는 매번 부름.
- 같은 `--out`으로 다시 실행하면 끝난 문항은 건너뛰고 이어서 함. 오류로 끝난 문항만 다시 돌리려면 `--retry-errors`를 붙임.
- HCX 키는 Phase 3와 같은 방식으로 넘김: 실행 스크립트가 키를 읽어 설정 객체(`LLMConfig`)로 Phase 1에 넘기고, Phase 1은 `.env`를 읽지 않음. 키는 환경 변수 `HCX_API_KEY`, `--env-file`, `<작업폴더>/.env` 순서로 찾고 파일에서는 그 변수 하나만 읽음. `--no-env-file`을 주면 파일을 읽지 않음. 키는 출력하거나 저장하지 않음.
- 결과는 `--workspace`가 아니라 `--out` 폴더(기본값 `evals/runs/<날짜-시간>-calc-<backend>`)에 저장함. `--workspace`는 표(`data/`)를 읽는 곳임. `--out`을 상대 경로로 주면 명령을 실행한 폴더 기준임.
- 실행하는 Phase 1은 `--phase1`(기본값 `../kosis-ai-nlp-summary-T1-30`)의 코드임. 환경에 설치된 다른 Phase 1이 잡히면 멈춤.
- 이미 실행한 결과만 다시 채점할 때는 `python evals/grade.py --run evals/runs/base-rules`. 배점을 바꿔 보려면 `--weight-codes 0.7 --weight-type 0.4`처럼 줌.
- 루프의 골든셋(`golden_{LOOP_NUM}.jsonl`)도 같은 형식이면 `--items evals/golden_3.jsonl`처럼 넘겨 그대로 채점할 수 있음.
- `EVAL_CMD`에는 `--quiet`를 붙이면 숫자 한 줄만 출력함.
- `--split dev`는 판정용 400건만, `--split holdout`은 보관용 200건만 돌림. 루프의 유지/되돌림은 판정용으로만 정하고, 보관용은 사람이 볼 때만 잼(판정용만 오르고 보관용이 안 오르면 판정용에 맞춰진 것임). 두 부분은 같은 표를 쓰지 않음(판정용 표 156개, 보관용 77개).
- 전체 파이프라인을 11/19 방식으로 재려면 `python evals/run_eval.py --legacy --backend hcx ...` (`--backend`는 mock 또는 hcx).

## 채점 규칙

실행 경로

- 숫자 계산 경로만 돌림: 기본값 적용 → 규칙 계획 → (규칙이 못 정했을 때만) 모델의 분류 매핑 → 계산.
- 판정 규칙(거절, 되묻기), 모델이 쓰는 계획, fallback, 문장 작성, 차트는 돌리지 않음.
- 표 2~3개 문항은 다중 표 규칙 계획 → 계산. 모델을 부르지 않음. 규칙이 생기기 전에는 모두 "계획 실패"임.

정답 판정

- 모듈의 답은 모듈이 답으로 표시한 fact(`Computed.main`, 계획의 `show`에 해당)임. 계산 과정에서 나온 다른 fact는 채점하지 않음.
- 문항의 정답은 `required_facts`임. 맞은 비율 = 맞은 정답 fact 수 ÷ 정답 fact 수. 전부 맞으면 PASS, 일부면 PARTIAL, 없으면 FAIL.
- 원본 값(표의 칸): `source_ref`가 같고 값이 정확히 같아야 인정함.
- 계산 값: 소수 둘째 자리로 반올림한 값이며 ±0.005 안이면 인정함.
- 모듈 fact 하나는 정답 fact 하나에만 대응시킴.
- `aux_facts`(계산에 쓰인 칸)는 점수에 넣지 않고 찾은 비율만 보여 줌.

배점

문항 점수 = 맞은 비율 × 계획을 만든 방식의 배점.

| 계획을 만든 방식 | 출력의 `origin` | 배점 |
|---|---|---|
| 규칙만 | `rules` | 1.0 |
| 모델이 분류 코드만 채움 | `model_codes` | 0.8 (`--weight-codes`) |
| 모델이 유형, 순위 축, 최대·최소 방향까지 정함 | `model_type` | 0.5 (`--weight-type`) |
| 그 밖 (모델이 계획이나 숫자를 씀, 기록 없음) | | 0 |

계획 실패(`no_plan`), 계산 거절(`refused`), 실행 오류, 결과 없음은 0점임.

요약에 나오는 것

- 점수(배점 반영)와 배점을 곱하기 전 값, PASS/PARTIAL/FAIL 수.
- 결과 분류: 정답, 숫자 틀림, 모델이 계획·숫자를 정함, 계산 거절, 계획 실패, 오류.
- 계획을 만든 방식별 문항 수와 맞은 비율, 계획 실패와 계산 거절의 이유 코드.
- 모델 호출 수, 답으로 표시한 fact 수의 중앙값과 최대(답 표시를 부풀리는지 보는 값).
- 참고 값: 원본 값만 맞으면 인정했을 때의 점수, 계산에 쓴 칸까지 같아야 인정했을 때의 점수, 아깝게 틀린 경우(부호 반대, 자릿수, 100배).
- 차트: 숫자 계산 경로는 차트를 만들지 않으므로 비교하지 않음. `--legacy` 실행에서만 `chart_expected`와 비교함.

## 문항 형식

Phase 1의 `GoldenItem`에 필드를 더한 형식임. `GoldenItem`은 표가 하나이고 추가 필드를 받지 않아서 그대로는 쓸 수 없음.

| 필드 | 내용 |
|---|---|
| `id`, `kind`, `type`, `question`, `table`, `table_sha256`, `required_facts`, `aux_facts`, `notes` | `GoldenItem`과 같음. `kind`는 모두 `answerable` |
| `subtype` | 세부 유형 (아래 표) |
| `table_keys`, `tables_sha256` | 표 1~3개의 키와 파일 해시. `table`은 첫 번째 표 |
| `chart_points` | 질문이 보는 같은 단위의 원본 값 개수 |
| `chart_expected` | 차트 가능 여부. 지금은 `chart_points >= 2` |

fact는 Phase 1의 `Fact` 형식 그대로임. 계산 값은 `derivation`(fact id의 사칙연산과 거듭제곱)과 `round: 2`를 가짐.

## 유형과 분포

표 233개 사용. 표 1개 480건, 2개 85건, 3개 35건. 큰 유형으로는 trend 164, compare 159, extreme 113, value 87, share 77.

| 세부 유형 | 건수 | 묻는 것 |
|---|---|---|
| value_single | 35 | 값 하나 |
| value_multi | 25 | 값 여러 개 (각각) |
| trend_diff | 28 | 두 시점의 증감 |
| trend_rate | 30 | 두 시점의 증감률 |
| trend_diff_rate | 18 | 증감과 증감률 |
| trend_cagr | 14 | 연평균 증가율 |
| trend_series | 18 | 연도별 값 나열 |
| period_avg | 22 | 여러 시점 평균 |
| period_sum | 20 | 여러 시점 합계 |
| compare_diff | 28 | 두 분류의 차이 |
| compare_ratio | 18 | 두 분류의 배율 |
| compare_sum | 18 | 여러 분류의 합 |
| item_ratio | 14 | 두 항목의 비 |
| gap_change | 14 | 격차의 변화 |
| rate_compare | 13 | 두 분류의 증감률 각각 |
| share | 33 | 비중 |
| share_change | 18 | 비중의 변화(%p) |
| share_sum | 9 | 두 범주를 합친 비중 |
| extreme_list | 18 | 나열한 후보 중 최대·최소 |
| extreme_sido | 18 | 시도 중 최대·최소 |
| extreme_period | 22 | 기간 중 최대·최소인 해 |
| rank_n | 11 | n번째로 크거나 작은 시도 |
| top_k | 10 | 상위·하위 k곳 |
| extreme_change | 12 | 가장 많이 늘거나 준 곳의 증감 |
| range | 14 | 최댓값 − 최솟값 |
| cross_value | 27 | 여러 표의 값 각각 |
| cross_diff | 20 | 표 사이의 차이 |
| cross_ratio | 18 | 표 사이의 배율 |
| cross_sum | 16 | 표 사이의 합 |
| cross_share | 17 | 표 사이의 비중 |
| cross_trend | 14 | 표 사이의 증감·격차 변화 |
| cross_extreme | 8 | 여러 표 중 최대·최소 |

## 만든 방법

- 표: 전체 2,364개에서 조사별로 층화해 240개를 뽑음(seed 20261119, 기존 골든셋의 표 10개 제외). 거절·되묻기 문항은 없음.
- 숫자: 표의 `value_raw`를 분수로 읽어 정확히 계산한 뒤 소수 둘째 자리로 반올림함. 반올림 경계에 걸리는 값과 부동소수 계산이 다르게 반올림되는 값은 버림. `source_ref`가 한 행만 가리키는 칸만 사용함.
- 질문: 계산된 조건을 보고 한 건씩 직접 작성함. 모든 조건(시점, 분류, 합계 기준)을 질문에 적었고 말투는 섞음. 뜻이 불분명한 12건은 같은 유형의 예비 문항으로 바꿈.
- 방향 규칙: 시점 증감은 나중 − 먼저, 두 분류의 차이는 큰 쪽 − 작은 쪽, 격차 변화는 질문에 적은 방향.
- 검증: `check_items.py`로 칸 1,381개와 계산 값 439개를 원본 표와 다시 대조해 모두 통과함.

## 정해야 할 것

- 배점 0.8과 0.5는 임시로 정한 값임. 옵션으로 바꿀 수 있음.
- 답 표시 부풀리기: 모듈이 모든 fact를 답으로 표시하면 최대·최소 문항은 고르지 않아도 정답이 됨. 지금은 표시한 수를 요약에 보여 주기만 함. 감점 규칙은 미정.
- 차트 가능 기준: 임시로 "같은 단위의 값이 2개 이상"으로 둠 (가능 554건, 불가 46건). 숫자 계산 경로 채점에는 쓰지 않음.
- 원본 값 인정 기준: 기본은 `source_ref`와 값이 모두 같아야 함. 값만 같아도 인정하려면 `--cell-match value`.
- 다중 표에서도 분류 매핑 모델을 쓰게 할지 미정. 지금은 규칙만.
- 방어자에게 숨기는 방법: 지금 `.claude/hooks/protect.js`는 수정만 막음. 읽기도 막아야 하고, Codex는 Claude hook을 쓰지 않으므로 따로 막아야 함. `kosis_chat/calc.py`도 수정 금지 목록에 넣어야 함.
