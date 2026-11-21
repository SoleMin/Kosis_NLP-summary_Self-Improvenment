# 루프 4 방어자 기록

테스트: `kosis-ai-nlp-summary-T1-30/tests/loop/test_loop4_one_table.py` (가상 표 셋: 항목 이름이 서수인 표 `FAKE_ORD`, 분류 이름이 서수인 표 `FAKE_SEQ`, 분류 이름에 "순위"가 든 표 `FAKE_JOB`). 모든 테스트는 답의 값과 함께 그 값을 계산한 칸(source_ref)을 확인한다.

## 고친 약점

### A-1. 표의 이름 속 서수·순위 낱말을 순위로 읽음 (질문 해석)

- 원인: 순위 낱말(`EXTREME_WORDS`, `RANK_WORDS`)과 등수(`rank_places`)를 표의 이름을 지우지 않은 질문 전체에서 읽었다. 판정 단계의 `RANKING`도 같았다.
- 고친 곳: `analyze/rules.py` `_question_type`(새 인자 `rest`, 없으면 예전처럼 질문), `rule_plan`이 `parsed.rest`를 넘김, `_extreme_plan`의 `rank_places(rest)`, `judge/rules.py` `defaults`의 `RANKING`을 `p.rest`에서 읽음.
- 제외 낱말은 넣지 않았다. 이름 대조에 걸린 자리는 `rest`에서 이미 지워지므로 그 자리의 서수는 순위 표현이 아니다.
- 함께: `EXTREME_WORDS`에 명사 꼴 `최댓`·`최솟`을 더함(판정 단계 `RANKING`과 같은 부류). "최댓값과 최솟값"이 순위 유형으로 읽힌다.
- 테스트 `test_ordinal_in_a_label_is_no_ranking`(항목 이름 서수·분류 이름 서수·"1순위" 이름, 단순 수치와 값 둘 나열), `test_ordinal_label_change_is_a_trend`(두 표, 범위와 두 시점의 변화), `test_a_ranking_on_an_ordinal_label_still_ranks`(이름에 서수가 있어도 질문의 "가장", "두 번째로"는 순위).
- 조건: 새 조건 없음(같은 낱말을 읽는 자리만 `rest`로 옮김).

### B-1. 범위 안의 시점마다 값을 달라는 질문 (질문 해석, 규칙 계획)

- 원인: 한 표 규칙은 범위가 있으면 값 나열(`_values_only`)을 하지 않고 추이로 보냈다. 다중 표가 읽는 `EACH_PERIOD`를 한 표 규칙이 쓰지 않았다.
- 고친 곳: `shared/patterns.py`에 `EVERY_N_YEARS`("N년 간격으로", "N년마다", "N년 주기로", "N년 단위로", "격년")와 `years_step`을 두고 `EACH_PERIOD`에 넣음(다중 표도 같은 사전을 읽음). `analyze/rules.py` `_values_only`가 범위 + `EACH_PERIOD`이면 값 나열로 봄, `_periods`가 간격이 있으면 범위의 첫 해부터 N년마다의 시점만 고름(`_every`), `_operation`의 "모두/전부"가 시점마다 묻는 범위에서는 합이 되지 않음.
- 답: 범위 안 시점의 칸 전부(간격이면 그 시점들), 계산 값 없음. 연간·월 칸이 함께 있는 표는 기존 `_range_periods`가 물은 단위의 칸만 고른다.
- 테스트 `test_range_period_by_period_lists_the_cells`(연도별, 해마다, 2년 간격, 4년마다, 격년 / 세 표), `test_range_trend_stays_a_trend`(변화를 물으면 그대로 추이).
- 조건 범위:
  - `EVERY_N_YEARS`: 숫자 + 년 + 간격/마다/주기/단위로, 또는 격년을 쓴 질문(범위가 있을 때만 시점을 거름).
  - `_values_only`의 범위 분기: 연도 범위가 있고 시점마다를 뜻하는 말(`EACH_PERIOD`)이 있으며 계산·변화·추이 낱말이 없는 질문.
  - `_operation`의 "모두/전부": 범위 + 시점마다를 뜻하는 말이 있는 질문은 합으로 보지 않음.

### C-1. 가장 큰 쪽과 가장 작은 쪽을 함께 값으로 물음 (규칙 계획)

- 원인: 양 끝을 함께 읽는 `_extremes_plan`이 차이·배율을 물을 때만 탔다.
- 고친 곳: `analyze/rules.py` `_extreme_plan`에서 양 끝 판정을 하나로 두고(큰 쪽 낱말 + 작은 쪽 낱말, 등수 없음, 변화 없음), 계산을 물었거나(기존) 순위 낱말이 두 번 이상 나오면(양 끝을 각각 순위 낱말로 부름) `_extremes_plan`으로 보냄. 계산을 묻지 않았으면 `blocks.extremes_combined(cells, None)`이 두 칸만 낸다(present "value"). 순위를 매길 분류가 없고 시점이 둘 이상이면 시점을 후보로 삼음(`rule_plan`의 `over`).
- 테스트 `test_largest_and_smallest_values_only`(분류 후보 두 표현, 시점 후보의 명사 꼴 최댓값·최솟값, 시점 낱말로 부른 해), `test_largest_and_smallest_with_a_difference_stays`.
- 조건 범위:
  - 양 끝 값만: 큰 쪽과 작은 쪽 낱말이 함께 있고 순위 낱말(가장·제일·최고·최저·최대·최소·최댓·최솟 등)이 두 번 이상 있으며 차이·배율을 묻지 않은 순위 질문.
  - `over`를 시점으로: 순위 질문인데 고정되지 않은 분류가 없고 물은 시점이 둘 이상인 질문.

### D-3. 한 대상을 뺀 나머지 (규칙 계획)

- 원인: 제외 표현을 읽는 규칙이 없어 빼는 동사로 물을 때만(비교의 차이) 맞았다.
- 고친 곳: `shared/patterns.py` `EXCLUDE`(제외, 빼고/빼면/뺀, 말고, 나머지, 그 밖, 낱말로 선 외·이외), `analyze/rules.py` `_excluded`, `_excluded_plan`, `blocks.remainder`, 계획 패턴 `excl`(`analyze/schemas.py`).
- 계획: 합계가 있는 분류에서 합계 아닌 코드 하나를 제외 낱말 앞에 불렀으면(합계를 함께 불렀다면 합계가 그 앞) 합계 칸 − 그 코드 칸. 합계를 부르지 않아도 그 분류의 합계를 쓴다. 합계가 없는 분류, 합계 아닌 코드가 둘 이상, 시점이 둘 이상, 제외 낱말 뒤에 부른 코드("전국 말고 서울")는 계획하지 않는다. 답은 차이 하나와 두 칸.
- 테스트 `test_total_minus_the_part_left_out`(제외한 나머지, 말고 나머지, 외 지역, 제외한, 빼고 / 지역 분류와 서수 분류, 두 표).
- 조건 범위: 제외 낱말이 있고 값·비교 유형이며 합계가 있는 한 분류에서 합계 아닌 코드 하나만 제외 낱말 앞에 부른, 시점 하나의 질문.

### B-2. 한 해에 평균 얼마씩 변했는지 (양) (규칙 계획, 새 연산)

- 원인: 평균 낱말 + 범위가 있으면 값들의 산술 평균을 냈다. 루프 3의 `cagr`는 변화율일 때만 탔다.
- 고친 곳: 새 연산 `avg_change` = (a − b) ÷ years (`src/kosis_ai/schemas.py` `CombineFn`에 더하고 `CombineStep` 검사 함수에서 `years`를 cagr·avg_change에 허용, `compute/plan_engine.py` `combine`에 계산 추가, 소수 둘째 자리). `analyze/rules.py` `_operation`에서 루프 3의 변화율 분기 바로 아래에 같은 방식(평균 낱말이 변화를 꾸밈)으로 양을 읽어 패턴 `apc`, `_cagr_plan(amount=True)` → `blocks.average_growth(amount=True)`. 답은 처음·끝 칸과 그 값 하나. 가운데 칸은 쓰지 않는다.
- 테스트 `test_average_yearly_change_from_the_two_ends`(범위, 두 시점, 최근 N년 / 한 해 평균 몇 명씩, 연평균 얼마나, 해마다 평균 / 늘어난 쪽·줄어든 쪽 / 세 표), `test_an_average_of_values_stays_a_mean`.
- 조건 범위: 평균 낱말과 변화를 묻는 말(`CHANGE_ASKED`)이 함께 있고, 변화율 낱말은 없으며, 시점이 범위 하나(다른 시점 없음)·낱개 연도 둘·최근 N년 가운데 하나인 질문. 범위 평균과 다른 한 시점을 견주는 질문(vsa)은 타지 않는다.

## 고치지 못한 약점

- A-2(표 제목 속 계산 낱말), G-1~G-6(다중 표), C-2·C-3·C-4·C-5(후보 한정, 다음 등수, 두 등수 사이 계산, 표 사이 계산 순위), D-1(두 분류에 걸친 짝), D-2(합친 묶음을 한 항으로), D-4(전체 항목이 대상으로 세어짐), E(구간 범위·구성비), F(단위 환산), B-3(수준 변화 동사), B-4(시점 후보의 앞 시점 대비 변화 순위): 이번 루프에서 손대지 않았다. 원인은 `loop/defender/brief_4.md`의 각 절 그대로다.
- F는 이번에 넣은 `avg_change`로는 풀리지 않는다. 값에 상수를 곱하는 일반 단계(또는 CombineFn)가 따로 필요하다.
- B-3의 수준 변화 동사(올랐다, 높아졌다 등)는 `CHANGE_ASKED`에 없어서 B-2의 새 분기도 "연평균 몇 세씩 높아졌어" 꼴은 타지 않는다.

## 깨진 것을 알게 된 기존 테스트

관련 파일만 돌려 봤다(`test_analyze.py`, `test_analyze_plan.py`, `test_judge.py`, `test_compute.py`, `test_plan_v3.py`, `test_answerability.py`, `test_multi_compute.py`, `test_plan_properties.py`). 실패 8건은 모두 계산 값을 소수 첫째 자리로 기대하는 것이다(루프 1에서 둘째 자리로 바꾼 결과). 이번 수정과는 관계없다.

- `tests/test_analyze.py::test_months_as_a_part_of_their_year`
- `tests/test_compute.py::test_trend_keeps_every_point_and_derives_difference_and_growth`, `test_share_uses_the_total_cell_and_sums_parts`, `test_avg_and_sum_over_periods`, `test_two_period_groups_compared`, `test_value_against_an_average`, `test_summing_a_stock_or_a_rate_over_periods_is_refused`
- `tests/test_plan_v3.py::test_growth_race_and_average_annual_growth`

`tests/` 전체 실행은 시간 제한(40분) 안에 끝나지 않아 결과를 얻지 못했다.

## 허용 범위 밖이라 고치지 못한 것

- 없음.
