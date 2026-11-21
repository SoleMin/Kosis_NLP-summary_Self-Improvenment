# 루프 3 방어자 기록

## 고친 약점

### A-1·A-2: 1위가 아닌 등수, 상위·하위 여럿 (질문 해석, 규칙 계획, 계산)

- 원인: 순위 계획이 1위 하나만 냈다. 순위 낱말(`EXTREME_WORDS`)이 없는 "N번째", "N위", "상위 N" 질문은 단순 수치로 떨어져 합계 칸을 냈다.
- 고친 것
  - `src/kosis_ai/schemas.py` `RankStep`에 기본값이 있는 필드 `report`(`first` 기본 / `places` / `place`)를 더했다. `places`는 앞의 `keep`개 후보의 fact를 순위대로, `place`는 `keep`번째 후보의 fact만 답에 낸다. 기존 계획은 `first`라 그대로다.
  - `compute/plan_engine.py`: `rank`, `_rank_mapped`가 `report`대로 답의 fact를 고른다(`_reported`). `keep`번째 후보가 없으면 계산 거절(missing_value). map이 코드마다 만든 fact를 `Mapped.facts`에 남긴다.
  - `shared/patterns.py`: `rank_places`(등수 하나인지 앞의 N개인지와 N), `RANK_PLACE`, `RANK_TOP`, `RANK_WORDS`. 숫자와 고유어 수(한·두·세…열), 서수(첫째·둘째…)를 같은 규칙으로 읽는다.
  - `analyze/rules.py` `_question_type`: `RANK_WORDS`가 있으면 순위 유형. `_extreme_plan`이 `rank_places`를 읽어 `blocks.extreme(places=...)`에 넘긴다. 후보 고르기는 `_pool`로 뽑아 순위 계획 셋이 함께 쓴다.
  - `analyze/roles.py` `_MIN_WORDS`: 적게·작게·최솟·하위·아래(밑·뒤)에서를 더했다(작은 쪽부터 센 순위).

### A-3: 두 시점 사이의 변화로 매기는 순위 (규칙 계획)

- 원인: 변화를 묻는 순위도 뒤 시점의 값으로 줄 세웠다.
- 고친 것: 순위 질문에 변화를 묻는 말(`CHANGE_ASKED`, 증감 폭 낱말)이 있고 시점이 둘 이상이면(분류의 순위일 때), 후보마다 처음·끝 칸과 증감(변화율을 물으면 증감률)을 계산하는 map 단계를 만들고 그 값으로 순위를 매긴다(`analyze/rules.py` `_change_rank_plan`, `analyze/blocks.py` `ranked_change`). 줄어드는 쪽을 물으면(`shared/patterns.py` `CHANGE_DOWN`) 가장 음수인 쪽이 1위다. 답은 고른 후보의 처음·끝 칸과 변화 값이다. 두 끝 시점에 숫자가 없는 후보는 미리 뺀다(칸 요청 한 번). 등수·상위 N개 표현과 함께 쓸 수 있다.

### A-4: 최대와 최소를 함께 부른 계산 (질문 해석, 규칙 계획)

- 원인: 최소 낱말이 있다고 최솟값 하나만 냈다. 시점을 후보로 삼는 판정(`ASKS_PERIOD`)이 "해는/해가"처럼 특정 조사가 붙은 꼴만 읽었고, 판정 단계(`judge/rules.py`)는 그런 질문에 최근 시점을 기본값으로 넣어 후보가 하나가 됐다.
- 고친 것
  - `shared/patterns.py` `ASKS_PERIOD`: 꾸미는 말 뒤의 시점 낱말(해·달·때·분기·시기·시점)을 뒤 조사와 관계없이, 그리고 언제·몇 년·어느 해·연도를 읽는다. `analyze/rules.py`가 이것을 쓴다. "2023년도"처럼 숫자 뒤의 년도, "올해"처럼 꾸미는 말이 없는 해는 읽지 않는다.
  - `judge/rules.py` `defaults`: 순위 낱말(`RANKING`, "가장 최근"은 뺌)과 `ASKS_PERIOD`가 함께 있으면 시점 기본값을 넣지 않는다(시점이 답인 질문).
  - `analyze/rules.py` `_extremes_plan`, `analyze/blocks.py` `extremes_combined`: 큰 쪽 낱말(`EXTREME_MAX`)과 작은 쪽 낱말이 함께 있고 차이·배율을 물으면, 후보(한 계열의 시점들 또는 한 시점의 분류 코드들)에서 최댓값과 최솟값을 고르고 차이(배율을 물으면 배율)를 낸다. 답은 그 값과 두 칸이다.

### E-3: 연평균 증가율 (질문 해석, 규칙 계획)

- 원인: 평균 낱말에 걸려 범위 값들의 산술 평균을 냈다.
- 고친 것: `analyze/rules.py` `_operation`에서 평균 낱말과 변화율 낱말(`RATE_OF_CHANGE`)이 함께 있으면(순위 질문 제외) `cagr` 계획(`_cagr_plan`, `blocks.average_growth`). 처음·끝 연도 칸 두 개와 `CombineStep(fn="cagr", years=끝-처음)`. "최근 N년"이면 표의 숫자 있는 마지막 N개 연도. `shared/patterns.py` `RATE_OF_CHANGE`에 "몇 퍼센트씩 + 변화 동사"와 성장률을 더했다(`MULTI_SHARE`의 부정 전방 탐색도 같이 맞춤). `analyze/schemas.py` `Pattern`에 `cagr`를 더했지만 Built는 "plan"으로 낸다(문장·차트 코드는 손대지 않음).

## 새로 넣은 조건과 그 조건을 타는 질문의 범위

- `RANK_PLACE`: 숫자·고유어 수 + 번째, 서수 + 째, 숫자 + 위/등(뒤에 까지·안·이내·권·부터가 없고 등급·등분이 아님)으로 등수 하나를 부른 질문.
- `RANK_TOP`: 상위·하위·위에서·아래에서 + 수, "1~N위"·"1위부터 N위까지", "N위까지/안/이내", 수 + 개·곳·군데(뒤에 개월이나 "시도 중/가운데/에서/의"처럼 후보 범위를 뜻하는 말이 오지 않음)로 앞의 N개를 부른 질문. N이 1이면 지금처럼 1위.
- `RANK_WORDS`: 상위·하위·순위·번째·서수째·숫자+위/등이 있어 "가장" 없이도 순위인 질문.
- `_MIN_WORDS` 추가분: 적게·작게·최솟·하위·아래/밑/뒤에서 — 작은 쪽을 묻는 모든 순위 질문.
- `ASKS_PERIOD`: 순위 질문 가운데 꾸미는 말 + 시점 낱말(해·달·때·분기·시기·시점, 조사 무관), 언제·몇 년·어느 해·연도로 시점을 후보로 부른 질문.
- `judge` 기본값 조건: 순위 낱말과 `ASKS_PERIOD`가 함께 있는 질문(시점이 답).
- 변화 순위(`_change_rank_plan`): 분류의 순위 + 변화를 묻는 말 + 같은 종류 시점 둘 이상 + 비중 낱말 없음.
- `CHANGE_DOWN`: 변화 순위 가운데 줄다·감소·떨어지다·하락·내려가다·축소로 물은 질문(순위 방향을 뒤집음).
- `EXTREME_MAX` + 작은 쪽 낱말 + 차이·배율 낱말(변화 낱말 없음): 최대와 최소를 함께 부르고 둘의 차이나 배율을 묻는 순위 질문.
- cagr 조건: 평균 낱말 + 변화율 낱말이 함께 있는 순위가 아닌 질문.

## 추가한 테스트

- `tests/loop/test_loop3_rankings.py` (가상 표 둘: 시도별 인구·출생아수, 산업별 사업체수)
  - 등수 하나: 세 번째로 많은 / 2위 / 두 번째로 적은 — 다른 표현, 다른 항목·연도, 다른 표(산업별). 답이 그 칸 하나인지 확인.
  - 앞의 N개: 상위 3개 / 가장 적은 두 곳 / 1~3위 — 다른 표현·방향·표. 칸과 순서 확인.
  - 변화 순위: 가장 많이 늘어난(범위) / 가장 많이 줄어든(두 시점 사이) / 증가율이 가장 높은(대비) / 가장 크게 증가한(다른 표) — 고른 후보의 처음·끝 칸과 변화 값 확인.
  - 최대·최소: 가장 많았던 해와 가장 적었던 해의 차이(조사 "와") / 가장 많았던 연도는 가장 적었던 연도의 몇 배 / 한 시점의 산업 최대·최소 차이 — 시점 후보와 분류 후보, 차이와 배율. 칸과 값 확인.
  - 1위 질문이 그대로인지(수정 전에도 통과, 보호용).
- `tests/loop/test_loop3_average_growth.py`: 연평균 증가율(범위), 해마다 평균 몇 퍼센트씩(다른 지역·범위 꼴), 연평균 성장률(두 시점, 다른 항목·지역), 최근 5년 연평균 증가율 — 처음·끝 칸과 cagr 값 확인. 값의 평균은 그대로인지(보호용).

## 고치지 못한 약점

- A-5 (다중 표 순위): 다중 표 계획(`multi_rules.py`)에 순위가 없다. map 단계가 표 하나의 분류를 도는 형식이라 표 사이 계산을 후보마다 하려면 map 본문이 표마다 다른 지역 코드를 써야 한다. 이번에는 손대지 않았다.
- 변화 순위에서 시점이 하나만 있고 "전년보다"로 물은 경우(판정 단계의 previous 기본값이 명시 연도와 함께 들어오지 않음)는 그대로 뒤 시점 값 순위다.
- 시점을 후보로 한 변화 순위("가장 많이 늘어난 해": 해마다의 전년 대비 증감)는 하지 않았다.
- B, C(퍼센트로 묻는 계산 네 가지), D(구성비 부분·구간·전체 항목), E-1, E-2, F, G: 손대지 않았다.

## 깨진 것을 알게 된 기존 테스트 (고치지 않음)

- 루프 2 기록과 같은 목록만 실패한다(이번 수정 전후 같음): 루프 1의 자릿수 변경에서 온 `tests/test_analyze.py::test_months_as_a_part_of_their_year`, `tests/test_compute.py`의 6개, `tests/test_plan_v3.py::test_growth_race_and_average_annual_growth`, `tests/test_pipeline.py::test_average_question_is_answered_with_a_chart_in_the_contract`과 `test_pattern_templates_pass_verification` 5건(문장의 소수 자릿수), 그리고 `tests/test_answer_rules.py::test_rules_fire_only_where_the_criteria_say`의 "지역별로 다 더하면" 경우.
- 돌린 파일: test_analyze, test_judge, test_compute, test_plan_v3, test_answerability, test_plan_properties, test_schemas, test_multi_compute, test_multi_pipeline, test_multi_dev, test_resolve, test_analyze_plan, test_answer_rules, test_pipeline, test_nodes_pure. 전체 `tests/`는 돌리지 않았다.

## 허용 범위 밖이라 고치지 못한 것

- 없음.
