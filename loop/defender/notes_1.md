# 루프 1 방어자 기록

## 고친 약점

### A. 표 2~3개 질문의 규칙 계획 (규칙 계획) — `kosis_chat/analyze/multi_rules.py`
- `step_plan_multi_rules`가 항상 "계획을 만들지 못함"이던 것을 규칙 계획으로 바꿨다. 표마다 칸 하나(값 나열은 이름 붙은 대상·시점의 칸 전부)를 같은 지역·시점에서 고른다.
  - 표마다 한 표 규칙(`analyze.rules.rule_plan`)으로 분류 코드와 시점을 읽는다. 지역 코드가 표마다 달라도(같은 부산이 21/26) 표마다 따로 찾는다.
  - 항목: 표의 유일한 항목, 아니면 질문이 이름 부른 항목. 다른 표의 더 긴 항목 이름 안에서만 발견된 이름(사망자수 ⊂ 영아사망자수)은 그 표의 것이 아니다 (두 지표가 한 표로 몰리는 문제).
  - 항목 이름을 짧게 부른 경우: 앞의 "총"을 뗀 이름(총인구수 → 인구수), 개수 접미어를 뗀 줄기(혼인건수 → 혼인, 사망자수 → 사망). 표에서 이름이 하나도 안 잡힐 때만 쓴다.
  - 시점: 질문의 시점을 표마다 읽는다(연도·월). 질문이 시점을 말했는데 어떤 표에 없으면 다른 시점으로 바꾸지 않고 계획하지 않는다. 시점이 없으면 모든 표에 숫자가 있는 가장 최근 시점.
  - 계산: "~당"(앞에 놓인 표가 분모, 숫자가 배수: 1건당 → 비율, 1천 명당 → per 1000) → 비중(비중·차지·몇 퍼센트) → 배율(몇 배·나누다·비율) → 합(SUM_WORDS) → 차이(차이·많/적·빼다) → 나란히 값.
  - 차이의 방향: "A가 B보다 많아" → A−B, "A가 B보다 적어" → B−A, "A에서 B를 빼면" → A−B, 방향이 없으면 큰 값−작은 값. 비중·배율의 분모는 "중/가운데/대비/에서/의 몇" 앞의 표, 없으면 나중에 이름 부른 표.
  - 답에 쓸 값: 계획의 show가 계산 값 하나와 그 계산에 쓴 칸들(나열은 칸만).
- 두 표 값의 합을 위해 연산 `sum`(a + b)을 추가했다: `src/kosis_ai/schemas.py`의 `CombineFn`에 값 추가, `compute/plan_engine.py` combine에서 계산. 두 표의 단위가 접두어만 다르면(천명/명) 다른 연산처럼 맞춘 뒤 더하고, 단위가 다르면 거절한다.

### B-1. 항목 이름 속 조사 (분류 매핑) — `kosis_chat/judge/question.py` `label_forms`
- 이름 안에서 두 낱말을 잇는 조사(의·와·과·및·에서·에)를 뺀 이름도 정확한 이름으로 본다. 토큰 대조도 토큰 끝의 이 조사를 떼고 대조한다.
- 사람 수 항목('X자수', 'X아수', 'X인수')을 사람 이름으로 부른 경우('사망자', '출생아')를 정확한 이름으로 본다. 지금 있던 'X건수' → 'X아/X자' 규칙 옆이 아니라 같은 자리에서 넓혔다.

### B-5 + C-1. 합계 (질문 해석, 규칙 계획) — `judge/question.py` `SUM_WORDS`, `analyze/rules.py` `_operation`, `build_plan`, `_targets_sum`
- `SUM_WORDS`를 더하다·합하다·합치다의 활용형 전부(더해, 합쳐, 합쳤 …)와 합계를 묻는 명사·부사(합계는, 합은, 도합, 통틀어)로 넓혔다.
- 합계를 물었는데 시점 범위가 없을 때:
  - 낱개 시점이 둘 이상이면 그 시점들의 합 (기존 `sum` 패턴의 시점 묶음으로).
  - 아니면 한 분류에서 이름 붙은 대상 여럿(지역 둘·셋, 항목 둘)의 한 시점 합 (`_targets_sum`, `select` + `aggregate sum`).
- `compute/semantics.py` `check_sum`: 시점 여럿을 더할 때만 유량(count_flow)을 요구하고, 한 시점의 여러 대상 합은 저량(인구)도 허용한다. 비율·단위 모를 값은 그대로 거절.

### D. 비교의 답에 쓸 값 — `kosis_chat/compute/answer_facts.py`
- 비교 계획(pattern compare)은 차이와 배율을 함께 계산한다. 답에는 질문이 물은 것만 남긴다: 배율은 몇 배·배수·비율·나누다가 있을 때만, 차이는 비교하는 말(비교·차이·보다·많/적 …)이 있을 때만(배율과 함께면 차이·격차·빼다로 이름 부를 때만). 둘 다 없으면 두 칸만 (값을 나란히 물은 질문). 질문의 낱말은 표 이름을 지운 나머지에서 읽는다.

### 계산 값의 자릿수 — `compute/engine.py` `PERCENT_DIGITS`, `compute/plan_engine.py` 평균
- 증감률·비중·배율·비율과 평균이 소수 첫째 자리로 반올림되어, 소수 둘째 자리까지 맞아야 하는 채점에서 틀릴 수 있었다(약점 설명의 예시 "1.19명"). 둘 다 소수 둘째 자리로 남긴다. 값만 바뀌고 칸·식은 그대로다.

## 새로 넣은 조건과 그 조건을 타는 질문의 범위
- `PER_UNIT` (shared/patterns.py): "숫자·단위어 + 당"으로 한 수량당 값을 묻는 질문 전부(1건당, 1천 명당, 10만 명당, 가구당, 1인당). 해당·상당·담당·정당·적당·부당·배당과 당시·당해는 제외. 표 이름 안의 "당"(천명당 단위가 붙은 항목 이름)은 제외.
- `MULTI_SHARE`: 표 둘의 값 가운데 하나가 다른 것에서 차지하는 비율을 묻는 질문(비중·차지·구성비·몇 퍼센트, 증감률을 묻는 "몇 퍼센트 늘었" 제외).
- `MULTI_RATIO`: 표 둘의 값을 나눈 배율을 묻는 질문(몇 배·배수·나누다·비율).
- `MULTI_WHOLE_AFTER`: 분모가 되는 쪽을 가리키는 말(중·가운데·대비·에서·에 비해·의 몇·기준) 바로 앞에 놓인 지표.
- `MULTI_DIFF`/`MULTI_FEWER`: 표 둘의 값 차이를 묻는 질문 / 그 가운데 "더 적은 쪽"을 묻는 말(적·낮·작·모자라·덜).
- `COUNT_STEM_SUFFIX`, `TOTAL_PREFIX`: 표의 항목 이름에서 짧은 이름을 만드는 규칙(개수 접미어, 앞의 총·전체). 다중 표에서 표의 항목이 질문에 한 번도 정확히 잡히지 않을 때만 쓴다.
- `_INNER_PARTICLE` (judge/question.py): 항목·분류 이름 가운데 두 음절 이상 낱말 뒤의 조사로 두 낱말을 잇는 모든 이름.
- `_PERSON_ENDS` (judge/question.py): '자·아·인'으로 끝나는 사람 + '수' 꼴의 항목 이름 전부(세 글자 이상).
- `SUM_WORDS` 확장: 합계를 묻는 동사 활용형과 명사·부사 전부.
- `rules._operation`의 새 두 분기: 합계 + 범위 없음 + 낱개 시점 둘 이상 / 합계 + 범위 없음(이름 붙은 대상 여럿). 순위 질문은 제외.
- `answer_facts._ASKS_RATIO`/`_ASKS_DIFF`/`_ASKS_GAP`: 비교 계획의 답에서 배율·차이를 남길지 정하는 말의 부류.

## 추가한 테스트 (`tests/loop/`)
- `test_multi_rules.py` (가상 표 7개): 값 나열(표 2개·3개, 다른 지역·연도), 차이의 방향(보다 많/적, 에서 빼면; 다른 지역·연도·표 순서), 월 시점 두 월별 표, 합(연도·월, 다른 표현 합치면/더하면), "~당" 비율(1건당, 1명당, 1천 명당 + 천명 단위 환산), 겹치는 항목 이름(사망자수/영아사망자수), 시점이 없을 때 공통 최근 시점, 질문한 월이 없는 연간 표는 계획하지 않음. 값과 계산에 쓴 칸(source_ref)을 함께 확인.
- `test_one_table_sum_compare.py` (가상 표 3개): 이름 붙은 대상 합(지역 둘·셋, 항목 둘, 저량·유량), 낱개 시점 합(두 표현), 값만 물은 비교는 칸만, 차이만/배율만 남는 비교(각 두 표현·다른 지역·연도), 조사가 든 항목 이름(두 항목, 다른 표현). 값과 칸을 확인.
- 모두 수정 전 코드에서 실패한다(다중 표는 계획 없음, 합은 차이·배율, 비교는 네 값, 조사 이름은 항목 미정).

## 고치지 못한 약점과 이유
- B-2 (나이 구간 "a~b세", "N세미만", "N0대"), B-3 (한 해 안의 월 범위, 월 기준 상대 시점, 연간·월 칸 혼합), B-4 (낱말 없는 구성비), B-6/C-4 (N번째 순위, 후보 한정), C-2의 시점 둘 배율과 한 표 안의 "~당" 환산 비율, C-3 (구성비의 부분 분류 선택), C-5 (연평균 변화율 cagr): 이번 루프에서는 손대지 않았다. 다음 루프 대상이다.
  - 한 표 안의 "혼인 1건당 출생아 수"는 아직 출생아 칸 하나만 낸다(혼인건수가 "혼인 1건"으로 불려 항목으로 잡히지 않음). 다중 표에서 쓴 줄기 규칙과 `PER_UNIT`을 한 표 비교 계획에 넓히면 된다.
- 다중 표: 추이·순위·연도 범위가 걸린 다중 표 질문(cross_trend, cross_extreme)은 계획하지 않는다. 시점이 월뿐인 표에 연도를 물으면(연간 값 없음) 계획하지 않는다. 단위가 다른 두 표 값의 합·차이는 거절된다(기존 단위 규칙).
- "비율"은 다중 표에서 배율(a/b)로 읽는다. 퍼센트로 묻는 비율은 비중 낱말이 있을 때만 비중이 된다.

## 깨진 것을 알게 된 기존 테스트
고친 모듈과 관련된 기존 테스트(test_analyze, test_analyze_plan, test_compute, test_judge, test_multi_compute, test_plan_v3, test_plan_properties, test_semantics, test_shared_labels, test_resolve, test_schemas, test_multi_pipeline)를 돌렸다. 아래 9개가 실패하며, 확인한 것은 모두 기대값이 소수 첫째 자리 반올림이라서다(예: 8.33 ≠ 8.3, 47.62 ≠ 47.6, 0.06 ≠ 0.1). 칸이나 식이 달라진 실패는 없었다. 기존 테스트는 고치지 않았다.
- tests/test_analyze.py::test_months_as_a_part_of_their_year
- tests/test_compute.py::test_trend_keeps_every_point_and_derives_difference_and_growth
- tests/test_compute.py::test_share_uses_the_total_cell_and_sums_parts
- tests/test_compute.py::test_avg_and_sum_over_periods
- tests/test_compute.py::test_two_period_groups_compared
- tests/test_compute.py::test_value_against_an_average
- tests/test_compute.py::test_summing_a_stock_or_a_rate_over_periods_is_refused (반올림 줄에서 먼저 멈춤; 여러 시점의 저량 합 거절은 `check_sum`에 그대로 남아 있음)
- tests/test_plan_v3.py::test_growth_race_and_average_annual_growth
- tests/test_semantics.py::test_r5_the_denominator_must_hold_the_numerator
전체 `tests/`는 시간 제한(10분) 안에 끝나지 않아 다 돌리지 못했다. 나머지는 루프가 끝난 뒤 사람이 확인해야 한다.

## 허용 범위 밖이라 고치지 못한 것
- 없음. `kosis_chat/calc.py`, fact 형식, 창구는 바꾸지 않았다.
