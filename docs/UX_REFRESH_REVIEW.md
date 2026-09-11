# F108 Pro LCD UX Refresh 구현·리뷰 문서

이 문서는 240×135 픽셀 AULA F108 Pro LCD에서 QuotaDeck의 캐릭터와 쿼터를 실제로 식별할 수 있게 만든 UX refresh의 설계 의도, 현재 코드 계약, 검증 기준을 정리한다. 단순한 시안이 아니라 현재 저장소 구현을 기준으로 한 인수 문서다.

소프트웨어 렌더링과 정적 검증 경로는 준비되어 있다. 다만 실제 키보드의 밝기, 시야각, RGB565 색 손실과 USB 업로드는 Windows 실기기에서 마지막으로 확인해야 한다.

## 결과 요약

| 요구 사항 | 현재 결과 | 구현 근거 |
|---|---|---|
| 프로바이더별 고품질 픽셀 캐릭터 | Codex, Claude, Cursor, Grok 각각에 이미지 생성 기반 기본/누적 4×4 원본 시트와 13개 상태 × 2프레임의 런타임 스프라이트를 제공한다. 캐릭터는 좌측 88×108 슬롯을 사용하고 2프레임으로 살아 있는 느낌을 준다. | `artwork/sprite_sources/`, `artwork/usage_sprite_sources/`, `tools/gen_sprites.py`, `themes/quotadeck-crew/` |
| 계정별 충분하고 동일한 회전 시간 | 기본값은 계정당 정확히 5,000ms다. 선택된 계정은 한 번씩만 나오며 요약·전환 프레임이나 위험 계정 중복으로 시간을 빼앗기지 않는다. UI 저장값은 2~20초이고 CLI 미리보기에서도 변경할 수 있다. | `config.py`, `budget.py`, `scenes.py`, `scheduler.py`, `cli.py` |
| Quota 막대 시인성 | 우측 영역 높이를 최대한 사용한 1개 또는 2개의 큰 막대로 바꿨다. 막대 안에는 `5H`, `WEEKLY`, `AUTO`, `OTHER`와 큰 퍼센트만 표시한다. | `layout.py`, `canvas.py` |
| 채움 경계 반응형 글자 | 동일한 글자 마스크를 채운 영역과 빈 영역으로 나누어 채운 쪽은 흰색, 빈 쪽은 검은색 계열로 그린다. 경계가 글자를 가로질러도 한 글자처럼 이어진다. | `draw_split_quota_bar()` |
| 마지막 summary 제거 | 계정 playlist에는 계정 전체 화면만 포함한다. summary/overview/grouped summary와 화면 전환 프레임을 넣지 않는다. | `render_playlist()` |
| 전체 UI 개선 | 헤더에는 14px provider/alias, `현재/전체`, 색상과 형태가 함께 바뀌는 상태 아이콘만 남기고, 좌측 캐릭터·우측 쿼터라는 한눈에 읽히는 구조로 고정했다. 작은 OS 폰트 대신 결정적인 픽셀 폰트를 사용한다. | `layout.py`, `canvas.py` |
| 누적 사용량 모드 | 기존 잔여 리밋과 독립적으로 일별/월별 `THIS`·`AVG` 토큰과 조건부 API 정가 환산치를 큰 글자로 비교한다. 평균 대비 토큰 비율은 큰 막대로 표시하고, 모델 정보는 LCD가 아니라 GUI에서만 제공한다. 데이터가 없거나 지원하지 않는 경우 0이 아니라 `N/A`다. | `usage/`, `MetricMode`, `UsagePeriod`, `paint_cumulative_account()` |
| 누적 반응 캐릭터 | 평균 미달, 유사, 1.5배, 2배, 3배 이상을 같은 캐릭터의 놀람 강도로 표현하며 3배 이상은 코믹하게 쓰러진다. | `usage_sprite_sources/`, `CUMULATIVE_SPRITE_STATES` |
| Flash 계산·상태 보존 | 하루 16시간 주기 상한은 첫 기회를 포함한 올림 계산이며 polling과 분리된다. 업로드 직전 예약과 하루 횟수는 원자적으로 보존되고 프로세스 간 잠금으로 직렬화된다. | `flashbudget.py`, `scheduler.py`, `flash-state.json` |

## 개선 전 문제의 root cause

| 관찰된 문제 | 근본 원인 | 이번 변경의 대응 |
|---|---|---|
| 계정마다 보이는 시간이 달랐다. | 화면 단위가 아니라 개별 애니메이션 프레임 수와 delay에 의존했고, 위험 계정 재삽입·전환·요약 장면이 동일한 frame budget을 나눠 썼다. | 계정 하나를 하나의 연속 slot으로 정의하고 모든 slot의 delay 합을 같게 만든다. SMART 모드는 순서만 바꾸며 계정을 복제하지 않는다. |
| 마지막 summary를 읽을 수 없었다. | 240×135 안에 여러 계정과 수치를 동시에 축소해 넣어 글자 높이와 정보 밀도가 LCD 물리 한계를 넘었다. | summary 계열을 playlist에서 완전히 제거하고 계정별 화면에 전체 면적을 배정한다. |
| Quota 정보가 멀리서 구분되지 않았다. | 얇은 막대와 막대 밖의 작은 레이블·숫자가 서로 다른 시선 위치에 있었고, 배경 대비도 일정하지 않았다. | 막대 자체를 정보 카드로 만들고 레이블과 퍼센트를 내부에 넣는다. 채움 경계를 기준으로 글자색을 분할한다. |
| 캐릭터가 작거나 뭉개졌다. | 원본 아트, 런타임 크기, 투명 여백, 상태 프레임, RGB565 변환에 대한 재현 가능한 계약이 없었다. | 이미지 생성 원본과 런타임 PNG를 분리하고, 88×108·binary alpha·48색·RGB565-safe 변환을 자동화한다. |
| 설정한 초와 실재 재생 시간이 어긋날 수 있었다. | F108 펌웨어는 delay를 20ms 단위 1바이트로 저장한다. 일반 밀리초를 프레임별로 반올림하면 누적 오차가 생긴다. | 먼저 전체 시간을 20ms tick으로 양자화한 다음 정수 tick을 프레임에 나눠 합계를 정확히 보존한다. |

## 240×135 화면 레이아웃 계약

좌표는 `(x0, y0, x1, y1)`의 양 끝 픽셀을 모두 포함한다. 렌더러는 항상 정확히 240×135 이미지를 만들며, 캐릭터는 `SPRITE_SLOT` 안으로 축소해 자르지 않고 하단 중앙에 배치한다.

| 영역 | 좌표 | 실제 크기 | 표시 내용 |
|---|---:|---:|---|
| LCD 전체 | `(0, 0, 239, 134)` | 240×135 | 1px 외곽선과 전체 계정 화면 |
| 헤더 `HEADER` | `(3, 3, 236, 18)` | 234×16 | 5×7 glyph의 2배 provider/alias, `n/N`, severity 형태 아이콘 |
| 캐릭터 패널 `CHAR_BAY` | `(3, 21, 92, 132)` | 90×112 | 캐릭터와 accent 모서리 표시 |
| 런타임 스프라이트 슬롯 `SPRITE_SLOT` | `(4, 23, 91, 130)` | 88×108 | RGBA 캐릭터, 하단 중앙 정렬 |
| 쿼터 패널 `RATE_BAY` | `(97, 21, 236, 132)` | 140×112 | usage window가 하나일 때 전체 막대 |
| 위 막대 `RATE_TOP` | `(97, 21, 236, 74)` | 140×54 | usage window 1 |
| 아래 막대 `RATE_BOTTOM` | `(97, 79, 236, 132)` | 140×54 | usage window 2 |

두 막대 사이에는 4px 간격이 있다. 각 패널의 2px inset을 제외한 실제 채움 track은 두 막대일 때 136×50, 한 막대일 때 136×108이다. usage window가 3개 이상이면 첫 primary window와 나머지 중 잔량이 가장 낮은 window를 표시한다. 따라서 작은 막대를 늘리지 않으면서도 전체 severity를 만든 위험 quota가 숨지 않는다.

누적 모드는 같은 좌표를 다른 의미로 재사용한다. `RATE_TOP`은 선택 기간의
`THIS 토큰 ÷ AVG 토큰` 막대이고, `RATE_BOTTOM`은 좌우 `THIS`/`AVG` 열이다.
두 열에는 공통 단위를 사용한 토큰과 비용을 각각 큰 2배 픽셀 글자로 표시한다.
비용 행 좌측 시작(THIS 열)에는 7×7 투명 픽셀 코인을 둔다.
모델명, `TOTAL`, `365D`, `SINCE`는 이 작은 LCD에 넣지 않는다.

## 정확한 계정별 5초 / 20ms 시간 계약

표시 시간과 provider polling, 플래시 업로드 제한은 서로 다른 개념이다.

| 항목 | 기본값 | 역할 |
|---|---:|---|
| `poll_seconds` | 60초 | provider quota를 다시 조회하는 주기 |
| `scene_hold_seconds` | 5초 | 선택된 계정 하나가 LCD 전체를 점유하는 시간 |
| `min_upload_minutes` | 10분 | 키보드 플래시 쓰기 간 최소 간격 |

기본 5초 계약은 다음 순서로 만들어진다.

1. 요청 시간 `requested_ms`를 가장 가까운 20ms tick으로 양자화한다.
   `total_ticks = max(1, round(requested_ms / 20))`
2. 실제 계정 시간은 `account_hold_ms = total_ticks × 20`이다.
3. 유효 frame budget은 요청값, soft cap 48, 펌웨어 hard limit 141 중 가장 작은 값이다.
4. 계정 수가 `N`이면 계정당 수용량은 `floor(budget / N)`이다.
5. 한 프레임의 최대 delay가 5,100ms이므로 계정당 최소 프레임 수는 `ceil(account_hold_ms / 5100)`이다. 모든 계정을 안전하게 담을 수 없으면 계정을 조용히 누락하지 않고 `SceneBudgetError`를 낸다.
6. 가능한 범위에서 계정당 최대 8프레임을 사용한다. `divmod(total_ticks, frames_per_account)`로 tick을 나누고 나머지 tick은 마지막 프레임부터 1개씩 더한다.
7. payload는 각 프레임 delay를 정확히 `delay_ms / 20`인 1바이트 값으로 저장한다. 허용 범위는 1~255 tick, 즉 20~5,100ms다.

기본값에서 계정당 8프레임이면 delay는 `620ms × 6 + 640ms × 2 = 5,000ms`다. payload에는 `31 × 6 + 32 × 2 = 250 tick`으로 기록되므로 하드웨어 재생 합도 정확히 5초다. 계정이 8개이고 frame budget이 32라면 계정당 4프레임을 사용하며 `1,240ms × 2 + 1,260ms × 2 = 5,000ms`가 된다.

따라서 빈 계정 예외 화면을 제외하면 한 playlist의 총 재생 시간은 `선택 계정 수 × 계정별 hold`다. FIXED는 사용자 순서를 유지하고 SMART는 severity 순으로 안정 정렬하지만, 두 모드 모두 각 계정을 한 번만 표시한다.

설정 파일은 `config_version = 5`다. v4에서 도입한 잔여 리밋/누적 사용량
선택 `metric_mode`에 더해, v5는 `cumulative_period`, `cost_currency`,
`usd_to_krw_rate`를 기록한다. 이 필드가 없는 구버전 설정은 월별, KRW,
1 USD당 1,400원으로 마이그레이션하며, `metric_mode`가 없는 더 오래된 설정은
계속 잔여 리밋을 사용한다. 환율은 사용자가 직접 바꾸는 표시용 환율이며 자동 또는
실시간으로 조회되지 않는다. 새 설정 또는 표시 시간 필드가 없는 설정에는 5초를
적용한다. 구버전 JSON에 이미 `scene_hold_seconds`가 있으면 과거 기본값과 사용자의
명시적 선택을 구분할 수 없으므로 2~20초 범위의 4초·10초 같은 명시값은 보존하고
범위 밖 값은 clamp한다. 저장할 때는 항상 현재 config version을 기록한다. GUI는
2~20초 범위이며, `quotadeck render --hold-seconds`도 유한한 2~20초 값과 정확한
20ms 단위만 허용한다.

## Flash 하루 횟수 계산과 영속 안전 한도

설정 화면의 “주기 기준 하루 상한”은 하루 16시간 활성 사용을 가정해 다음처럼
계산한다.

`uncapped_daily_writes = ceil(16 × 60분 / min_upload_minutes)`

첫 업로드 기회를 포함하므로 정수 나눗셈의 버림을 쓰지 않는다. 10분은 96회,
7분은 `ceil(960 / 7) = 138회`, 40분은 24회, 90분은 11회다. 이 계산에는
`poll_seconds`를 넣지 않는다. provider 조회는 플래시 쓰기가 아니며, 실제 쓰기는
렌더 내용 변경/노후화 조건과 최소 기록 간격을 모두 통과해야 하기 때문이다.

UI에 표시하는 수명은 10만 program/erase cycle을 이 **안전 한도 적용 전** 주기
상한으로 나눈 보수적 추정이다. 실제 쓰기는 기본 `daily_flash_limit = 100`의
추가 제한을 받는다. 따라서 7분 설정의 주기 상한은 138회여도 실제 앱 기록은
하루 최대 100회다. 수동 업로드는 최소 간격을 우회할 수 있지만 하루 안전 한도는
우회하지 않는다.

`FlashBudget`은 업로드 승인 직전 예약 시각, 날짜, 그날의 보수적 기록 횟수를
`flash-state.json`에 원자적으로 저장한다. scheduler 재시작이나 GUI 설정 변경으로
runtime을 다시 만들어도 이 상태를 복원한다. 프로세스 간 잠금은 최신 상태 복원부터
장치 전송까지 직렬화한다. 상태 예약을 저장하지 못하면 장치를 열지 않으며, 전송 중
실패·충돌은 일부 플래시 쓰기 가능성 때문에 예약한 안전 횟수를 유지할 수 있다.

## Split-text Quota bar

막대는 “남은 quota”를 기준으로 왼쪽에서 오른쪽으로 채운다. 채운 부분은 severity별 어두운 색, 소진된 부분은 밝은 중립 배경이다.

`draw_split_quota_bar()`는 텍스트를 두 번 다른 위치에 그리지 않는다. 먼저 `5H`/`WEEKLY` 같은 scale 2 레이블과 높이 25px의 scale 5 퍼센트를 하나의 흑백 마스크로 만든 뒤, 그 마스크를 fill 영역과 empty 영역 마스크로 각각 자른다. fill 쪽 텍스트에는 거의 흰색인 `BAR_TEXT_ON_FILL`, empty 쪽에는 거의 검은색인 `BAR_TEXT_ON_EMPTY`를 적용한다.

이 방식의 중요한 성질은 다음과 같다.

- 채움 경계가 숫자나 글자를 통과해도 위치가 흔들리거나 글자가 이중으로 보이지 않는다.
- 0%와 100%에서도 track 경계와 글자가 유지된다.
- 값이 없으면 큰 `--%`를 표시한다.
- 문자열은 `5H`, `WEEKLY`, `AUTO`, `OTHER`처럼 짧게 정규화한다.
- reset 시각, plan 등 작은 보조 정보는 LCD에 다시 넣지 않는다.

## 0.2 누적 사용량 화면 계약

`metric_mode`는 계정 순서를 정하는 `display_mode`와 독립적이다. 누적 화면은 좌측
캐릭터 슬롯을 그대로 사용하고, 우측 위에는 평균 대비 큰 사용량 막대, 우측 아래에는
`THIS`/`AVG` 두 열을 배치한다. 각 열의 첫 줄은 공통 단위의 토큰, 둘째 줄은 선택한
KRW 또는 USD 정가 환산치다. 예를 들어 토큰은 `0.6B / 1.2B`, GUI에서
`KRW (만원)`을 선택한 원화는 `1K / 2K`, 달러는 `0.4K / 0.8K`처럼 두 값을
같은 단위로 보여 준다. 원화 환산 범례는 GUI에
`1K = 천만원 · 1M = 백억 · 1B = 10조`로 표시한다. LCD에는 통화명, 한글,
모델명이나 작은 `TOTAL`/관측 범위 문구를 넣지 않는다. 선택 기간의 상위 모델 2개와
토큰은 설정 GUI의 계정 행에 표시하고, 전체 모델 내역은 그 행의 tooltip에서 확인한다.

기간 계산은 시스템 시간대의 달력 경계를 따른다.

- 일별 `THIS`는 오늘 사용량이고, `AVG`는 이전 완료일 평균이다. 최소 7개의 완료일이
  있어야 평균, 막대와 반응 단계를 확정한다.
- 월별 `THIS`는 현월 1일부터 현재까지의 MTD 사용량이고, `AVG`는 이전 완료 월의
  월평균이다. 최소 1개의 완료 월이 필요하다. 최초 관측일이 월 중간이면 그 부분월은
  평균에서 제외하고, 그 뒤의 사용 기록 없는 완료 월은 0으로 포함한다. MTD를 월말
  예상치로 환산하거나 일할 보정하지 않는다.
- 막대 숫자는 `THIS 토큰 ÷ AVG 토큰 × 100`이다. track의 채움은 한 달 또는 하루
  평균에 도달하는 100%에서 포화하지만, 숫자와 캐릭터 단계는 초과량을 보존한다.
  150%와 200%는 그대로 표시하고 300% 이상은 `300%+`다.

정상 막대 caption은 통화와 무관하게 월별 `M AVG`, 일별 `D AVG`다. 비교 이력
부족은 `M BUILD`/`D BUILD`, 일부 기록만 읽힌 상태는
`M PARTIAL`/`D PARTIAL`, 사용할 수 없는 원장은 `M N/A`/`D N/A`로 구분한다.
`BUILD`와 `PARTIAL`은 정상 퍼센트를 만들지 않으며, partial subtotal에는 비용과
놀람 단계를 붙이지 않는다. 범위 안에 유효 관측이 없으면 측정된 0으로 꾸미지 않고
`N/A`다.

현재 LCD의 계정 누적 카드는 Codex `sessions`/`archived_sessions`와 Claude Code
`projects`의 로컬 JSONL 메타데이터를 지원한다. 이는 이 기기의 보존 기록이지 계정
전체 원장이 아니다. Cursor는 신뢰할 수 있는 로컬 원장이 없어 `ADMIN API REQUIRED`,
Grok은 개별 `grok usage` 세션을 안전하게 계정 합계로 만들 수 없어
`OTEL V1 REQUIRED`로 표시한다. 0.2는 provider 관리자 키/API나 external OTel
collector를 자체 구성하지 않는다. Grok OTel 분석은 사용자가 opt-in 이후 별도
보존한 timestamped v1 이벤트를 공급할 때만 가능하며 과거를 소급 복원하지 않는다.

로컬 분석 범위는 최대 365일이다. 이 범위와 모델별 합계는 GUI 진단 정보와 CLI
`quotadeck cumulative --models`에서 계속 확인할 수 있지만 LCD에는 표시하지 않는다.

| 선택 기간 `THIS` ÷ `AVG` | 상태 | 연출 |
|---:|---|---|
| `< 1.0×` | `usage_below` | 차분함 |
| `1.0× – < 1.5×` | `usage_similar` | 살짝 놀람 |
| `1.5× – < 2.0×` | `usage_150` | 명확히 깜짝 놀람 |
| `2.0× – < 3.0×` | `usage_200` | 크게 충격받음 |
| `≥ 3.0×` | `usage_300` | 코믹하게 쓰러짐 |

가격은 2026-09-11 공식 API 정가의 정적 catalog snapshot
`2026-09-11.2`다. 현재 인증이 명시적 API-key 과금이고 모든 모델·토큰 범주에
단가가 있으며 scan이 완전할 때만 선택 기간의 `THIS`와 `AVG` 정가 환산치를 함께
표시한다. 두 금액은 기간별 all-or-nothing 한 쌍이므로 구독/혼합/불명확 인증,
partial scan, unknown model/category, 비교 이력 부족 중 하나라도 있으면 둘 다
표시하지 않는다. KRW는 USD 결과에 GUI의 수동 `usd_to_krw_rate`를 적용한 표시값이며
기본값은 1,400원/USD이고 실시간 환율이 아니다. 정가 snapshot 역시 실시간 web 가격,
long-context/fast/batch/지역/도구 별도 요금 또는 청구서가 아니다. 이 주의 문구와
가격표 날짜, 수동 환율은 GUI에서 확인한다. 세부 지원표와 가격 출처는
[CUMULATIVE_USAGE.md](CUMULATIVE_USAGE.md)에 있다.

## 이미지 생성 원본에서 88×108 런타임 asset까지

향후 Codex가 캐릭터를 다시 만들거나 포즈를 고칠 수 있도록 이미지 생성 결과와 펌웨어용 결과물을 분리했다.

```text
GPT image-generation output
                    │
                    ├─ tools/extract_checker_alpha.py  (checker가 그려진 RGB만)
                    ▼  tools/normalize_sprite_sheet.py
                    ├─ artwork/sprite_sources/<provider>.png
                    │      (quota/status 8쌍, 투명 배경 4×4)
                    └─ artwork/usage_sprite_sources/<provider>.png
                           (누적 반응 5쌍 + reserve, 투명 배경 4×4)
                                      │
                                      ▼  tools/gen_sprites.py
themes/quotadeck-crew/provider/<provider>/<state>_00.png
themes/quotadeck-crew/provider/<provider>/<state>_01.png
                    │
                    ├─ theme.json
                    └─ artwork/sprite_previews/runtime_contact_sheet.png
```

두 종류의 4×4 원본 시트는 모두 `1280×1280` static RGBA PNG이며 각 cell은
`320×320`이다. 기본 quota/status 시트의 cell 계약은 다음과 같다.

| 행 | 열 0~1 | 열 2~3 |
|---:|---|---|
| 0 | `idle` 2프레임 | `busy` 2프레임 |
| 1 | `caution` 2프레임 | `critical` 2프레임 |
| 2 | `exhausted` 2프레임 | `offline` 2프레임 |
| 3 | `stale` 2프레임 | `reset` 2프레임 |

누적 반응 시트는 앞의 10개 cell만 사용한다.

| 행 | 열 0~1 | 열 2~3 |
|---:|---|---|
| 0 | `usage_below` 2프레임 | `usage_similar` 2프레임 |
| 1 | `usage_150` 2프레임 | `usage_200` 2프레임 |
| 2 | `usage_300` 2프레임 | reserve |
| 3 | reserve | reserve |

원본 cell은 모든 변에 최소 16px 투명 gutter, cell 폭·높이의 25% 이상인 visible footprint와 primary connected component를 가져야 한다. 상태별 두 포즈의 body baseline 차이는 cell 높이의 3% 이하여야 한다. `tools/normalize_sprite_sheet.py`가 두 포즈에 같은 배율을 적용하고 가장 큰 연결 실루엣을 바닥선에 맞춰 이 계약을 만든다. 생성기가 실제 alpha 대신 회색 checker를 그린 경우에만 `tools/extract_checker_alpha.py`를 먼저 사용하며, 이 휴리스틱 결과는 반드시 눈으로 확인한다.

컴파일러는 각 상태의 두 포즈에서 공통 alpha bounding box를 구하고 3px 여백을 더해 포즈 축을 맞춘다. 그 결과를 LANCZOS로 84×101 content box 안에 맞춘 뒤 장식 파티클이 아니라 가장 큰 8-connected character component를 88×108 canvas의 동일한 하단 앵커에 고정한다. 이어서 alpha threshold 72로 완전 투명/완전 불투명만 남기고, dithering 없이 visible opaque 색을 최대 48색으로 줄인 다음 RGB 채널을 RGB565로 정확히 표현 가능한 값에 맞춘다.

큰 포즈 차이가 빠른 전신 깜빡임으로 보이지 않도록 모든 2-frame 상태는 account slot에 표시 프레임이 2개 이상일 때 보조 포즈 `01`을 slot당 정확히 한 번만 보여 준다. 나머지는 `00`이며 1-frame slot은 정지 화면이다. 기본 8-frame slot은 `00, 00, 00, 01, 00, 00, 00, 00`이다. 개발용 contact sheet에는 provider, state, `00/01`과 cell 경계를 함께 출력해 다음 Codex 작업이 행·열 의미를 추측하지 않게 했다.

현재 산출물은 4 providers × 13 states × 2 frames = **104개의 RGBA PNG**다. 각 PNG는 정확히 88×108이며 투명 여백과 불투명 캐릭터 픽셀을 모두 가져야 한다. 모든 상태는 정확히 2개의 서로 다른 프레임을 가져야 한다. 수평으로 쓰러진 `usage_300`은 전용 최소 visible box 64×40을 적용하고, 나머지는 최소 48×56을 적용한다. `theme.json`은 canvas 240×135, `hud = full-bars-v2`, sprite size 88×108, 두 4×4 source/compiler와 Pillow 버전을 기록한다. 개발 의존성은 palette/resampling 재현성을 위해 Pillow 12.3.0을 고정한다.

원본을 갱신할 때는 셀 안에 UI 글자나 배경을 넣지 말고, 캐릭터의 발 위치·실루엣·쿼터뷰 방향을 두 프레임 사이에서 유지한다. 생성 프롬프트와 편집 규칙은 `artwork/sprite_sources/`와 `artwork/usage_sprite_sources/`의 `PROMPTS.md` 및 `README.md`에 남겨 두었다. 후자의 `*.alpha.png`는 checker 추출 중간 파일이므로 release archive에는 넣지 않는다.

## 주요 변경 파일

| 경로 | 책임 |
|---|---|
| `src/quotadeck/config.py` | 새 설정의 5초 기본값, 기존 명시값 보존, config v5와 독립 `metric_mode`·일별/월별·KRW/USD·수동 환율 기록, 2~20초 저장 범위 |
| `src/quotadeck/app/i18n.py` | 표시 정보·기간·통화 선택, 수동 환율, 계정당 표시 시간, 누적 local-history/가격 안내와 Flash 상한 표시 |
| `src/quotadeck/cli.py`, `src/quotadeck/__main__.py`, `src/quotadeck/diagnostics.py` | GUI import 전부터 시작하는 회전 로그, 예외/fault/Qt hook, 민감정보 마스킹, 정상·비정상 종료 marker |
| `src/quotadeck/app/main_window.py`, `tray.py` | worker 실제 종료 기준 수명주기, 안전한 tray 종료 대기, tray/menu 소유권, 60초 health watchdog와 로그 폴더 메뉴 |
| `src/quotadeck/core/flashbudget.py`, `scheduler.py` | 올림 기반 하루 횟수, 잠금·선예약 기반 Flash 상태 보존, quota/cumulative poll·render·upload |
| `src/quotadeck/usage/` | Codex/Claude 로컬 scanner, 일별/월별 날짜·모델·평균 분석, 보수적 Grok adapter, TTL cache, 기간별 LCD snapshot |
| `src/quotadeck/usage/pricing.py`, `pricing_catalog_2026_09_11.py` | 날짜가 고정된 공식 API 정가 snapshot과 기간별 THIS/AVG all-or-nothing 비용 추정 |
| `src/quotadeck/renderer/budget.py` | 20ms tick 기반의 균등·정확한 account slot 및 frame budget 오류 처리 |
| `src/quotadeck/renderer/scenes.py` | account-only playlist, summary/transition/중복 제거, 상태 애니메이션 |
| `src/quotadeck/renderer/layout.py` | 240×135 고정 좌표, 88×108 캐릭터 슬롯, 1/2개 full-height quota bar와 큰 THIS/AVG 토큰·비용 표 |
| `src/quotadeck/renderer/canvas.py` | 결정적 픽셀 글꼴, 큰 퍼센트, split-colour text mask, 100% 포화·300%+ 사용량 막대 |
| `src/quotadeck/renderer/sprites.py` | schema/HUD, 88×108 static RGBA, binary alpha, visible palette, symlink, 상태/프레임 무결성의 strict runtime 검증 |
| `src/quotadeck/devices/aula_f108/payload.py`, `protocol.py`, `transport_win32.py` | 전송 입력 검증, 단계별 breadcrumb, 64-bit WinAPI ABI, timeout 가능한 overlapped LCD I/O |
| `tools/extract_checker_alpha.py`, `tools/normalize_sprite_sheet.py` | 생성 결과의 제한적 checker 복구, 1280×1280 grid·cell gutter·body baseline 정규화 |
| `tools/gen_sprites.py` | 기본/누적 4×4 원본을 104개 런타임 asset과 manifest로 재현하고 staging 교체·semantic freshness/contact-sheet 검증 |
| `tools/export_preview.py` | README/문서용 GIF와 RGB565 round-trip hardware-palette preview 출력 |
| `tools/verify_refresh.py` | hardware 없이 실행하는 UX refresh 핵심 acceptance 검사 |
| `artwork/sprite_sources/`, `artwork/usage_sprite_sources/` | 향후 이미지 생성·수정에 사용하는 provider별 기본/누적 원본 시트와 프롬프트 |
| `themes/quotadeck-crew/` | 배포되는 `theme.json`과 104개 runtime PNG |
| `artwork/sprite_previews/`, `docs/hero-preview.gif`, `docs/hud-preview.png`, `docs/assets/` | sprite 및 최종 LCD 결과 시각 검토물 |
| `tests/test_renderer.py`, `tests/test_quota_bar.py`, `tests/test_theme_assets.py` | 균등 5초, 화면 분리, split text, asset 계약 회귀 테스트 |
| `tests/test_accounts.py`, `tests/test_payload.py`, `tests/test_ui_smoke.py`, `tests/test_cli_help.py`, `tests/test_diagnostics.py`, `tests/test_transport_win32.py` | 기존 설정 보존, payload, GUI/CLI, 로그 회전·마스킹·종료 판별, Win32 ABI/timeout 회귀 테스트 |
| `tests/test_usage_*.py`, `tests/test_cumulative_display.py`, `tests/test_pricing.py`, `tests/test_flashbudget.py`, `tests/test_scheduler_safety.py` | local scanner/평균/DST/Grok 한계, 누적 UI·반응 단계, 비용 gate, Flash 올림·restart 안전성 회귀 테스트 |
| `.github/workflows/build.yml` | Windows에서 asset 재현성, theme, pytest, preview, PyInstaller 검증 |
| `README.md`, `README.en.md`, `docs/CUMULATIVE_USAGE.md`, `docs/THEMES.md`, `docs/ARCHITECTURE.md` | 사용자 사용법, 누적 지원/가격 한계와 새 렌더/asset 계약 문서화 |

`src/quotadeck/app/main_window.py`의 기존 `Stepper(minimum=2, maximum=20)`와 config 수집 경로는 그대로 재사용한다. 변경 목록에는 넣지 않았지만 5초 설정의 GUI 진입점이므로 merge 시 동작을 반드시 보존해야 한다.

## 검증 명령과 완료 기준

저장소 루트의 PowerShell에서 다음 순서로 실행한다.

```powershell
.\.venv\Scripts\python.exe tools\verify_refresh.py
.\.venv\Scripts\python.exe tools\gen_sprites.py --check
.\.venv\Scripts\python.exe -m quotadeck theme validate themes\quotadeck-crew
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m quotadeck render --fixture tests\fixtures\usage.json --out preview.gif --hold-seconds 5 --mode fixed
git diff --check
git status --short
```

완료로 판정하려면 다음 조건을 모두 만족해야 한다.

- `verify_refresh.py`, sprite `--check`, theme validation, 전체 pytest가 모두 exit code 0이다.
- sprite 재생성 후 `themes/quotadeck-crew`에 예상하지 않은 diff가 생기지 않는다.
- 렌더 프레임은 모두 240×135이고 총 frame 수는 soft cap 48 및 hard limit 141을 넘지 않는다.
- 각 계정의 payload delay 합이 기본값에서 정확히 250 tick, 즉 5,000ms다.
- SMART/FIXED 모두 선택 계정의 누락과 중복이 없다.
- raw payload는 불변 snapshot으로 고정되며 HID 명령을 보내기 전에 1~141 frame, non-zero delay, frame 수에 맞는 정확한 padded length를 만족해야 한다.
- 50% bar의 글자 픽셀이 fill 쪽에서는 흰색, empty 쪽에서는 검은색 계열이다.
- provider마다 8개 quota/status 상태와 5개 누적 반응 상태, 상태마다 서로 다른
  2개 프레임, 총 104개의 88×108 runtime PNG가 존재한다.
- 누적 분석은 local calendar day/month와 DST를 보존한다. 일별은 최소 7개 완료일,
  월별은 첫 부분월을 제외한 최소 1개 완료 월 이후에만 평균을 확정하고, 이후 빈
  완료 월은 0으로 포함한다.
- 사용량 막대는 `THIS 토큰 ÷ AVG 토큰`이며 채움은 100%에서 포화하되 150%, 200%,
  `300%+` 숫자와 `<1×`, `1~<1.5×`, `1.5~<2×`, `2~<3×`, `≥3×` 캐릭터 경계를
  정확히 보존한다.
- LCD에는 모델명이 없고 THIS/AVG 토큰·비용을 큰 글자로 표시한다. GUI는 선택 기간의
  상위 모델 2개를 보여 주며 전체 모델 내역은 tooltip에서 제공한다.
- partial/unsupported/no-observation은 0 또는 일부 가격으로 보이지 않으며, 기간별
  비용은 명시적 API-key 신호와 완전한 모델·토큰 범주가 모두 확인될 때만 THIS/AVG를
  함께 표시한다. KRW 환산은 기본 1,400원/USD의 수동 설정이며 실시간 환율이 아니다.
- Flash 주기 상한은 16시간에 대해 올림 계산하고 polling 주기와 독립적이며, 재시작
  뒤에도 cooldown과 하루 안전 한도를 보존한다.
- `preview.gif`에서 summary 화면 없이 계정별 전체 화면만 순환하고, provider/alias/두 quota를 축소하지 않고 읽을 수 있다. `docs/hud-preview.png`는 첫 원본 frame을 RGB565 encode/decode한 hardware-palette simulation이다.

자동 검사가 통과해도 실기기 완료 조건은 별도다. Windows에서 공식 AULA 앱을 완전히 종료하고, 키보드를 USB 케이블로 연결한 뒤 사용자 LCD slot에 업로드한다. 실제 LCD에서 각 계정을 스톱워치로 5초씩 확인하고, 0%·50%·100% bar의 경계 글자, 두 줄 quota, 네 provider의 상태 애니메이션, 밝기와 시야각을 확인한다. 이 실기기 검증 전에는 “hardware verified”로 표시하지 않는다.

### 이 source 전달본의 검증 기록 — 2026-09-11

| 검사 | 결과 |
|---|---|
| `python -m compileall -q src tools tests` | 통과 |
| `python tools/gen_sprites.py` 후 `--check` | 통과 — 기본/누적 8개 source, 104개 runtime PNG, contact sheet 일치 |
| strict `theme validate` | 통과 |
| `tools/verify_refresh.py` | 통과 — config, split bar, identity, visible-window, 5초 slot, payload/NACK 검사 |
| `PYTHONPATH=src python -m unittest -v tests/test_transport_win32.py` | 통과 — 12개 WinAPI ABI, overlapped timeout/cancel/quarantine, handle 수명 경계 |
| dependency-light 비-Qt 회귀 실행 | 통과 — 누적/가격/Grok/Flash 신규 회귀 포함 |
| 파괴적 output-path/미관리 target 수동 검사 | 통과 — source/theme/preview 중첩 거부, 기존 marker 보존 |
| 전체 `pytest` | 이 실행 환경에 `pytest`가 없어 미실행 (`No module named pytest`) |
| GUI/provider/device 통합 | 이 실행 환경에 PySide6/httpx/hid가 없어 미실행 |
| Windows exe 빌드 및 실제 F108 Pro 업로드 | 미실행 |

따라서 dependency-light 코드·렌더·asset 계약은 검증됐지만, 위의 미실행 항목은
Windows 로컬 checkout에서 완료해야 한다. 이 표를 전체 pytest나 hardware 검증을
통과했다는 의미로 해석하지 않는다.

## 장기 트레이 refresh 계약

장기 자동 조회와 Flash 상태 보존은 현재 UX에 통합되어 있다.

- 설정 앱은 `poll_seconds`마다 single-shot timer로 새 작업을 예약하며, 이전 worker가 실제로 끝난 뒤에만 다음 주기를 시작한다.
- 키보드가 유선 모드로 준비되고 AULA 프로그램이 닫혀 있을 때만 자동 업로드 worker를 사용한다. 그 외에는 provider 조회와 화면·트레이 미리보기만 갱신한다.
- 자동 업로드는 `force=False`이므로 렌더 변경 또는 노후화 조건과 최소 기록 간격, 하루 안전 한도를 모두 통과해야 한다. 노후화는 최소 간격을 우회하지 않는다.
- 예약 시각과 하루 안전 횟수는 장치 기록 직전에 `flash-state.json`에 원자적으로 저장하며, 프로세스 간 잠금·앱 재시작·런타임 재생성에도 보존한다.
- 종료 요청은 refresh timer를 먼저 멈추고 실행 중 worker가 끝날 때까지 기다린다. 자동 조회 오류는 매 주기 modal 창을 띄우지 않고 상태·진단 로그에 남긴다.

upstream과 병합할 때 `main_window.py`, `core/scheduler.py`, `core/flashbudget.py`, `config.py`를 파일 단위로 선택하면 이 계약이나 렌더 계약이 사라질 수 있다. 의미 단위로 병합한 뒤 전체 검증과 Windows 실기기 확인을 다시 실행한다.

## Windows 로컬 Codex 작업 절차

Python 3.12, Git, Codex CLI가 설치되고 Codex 로그인이 끝났다는 전제다. PowerShell에서 UX 작업을 별도 branch로 시작한다.

```powershell
git clone https://github.com/guyster323/F108pro_Usage_monitor.git
Set-Location F108pro_Usage_monitor
git fetch origin
git switch -c feat/lcd-ux-refresh origin/main

py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

codex
```

`.git` metadata가 없는 source snapshot ZIP을 전달받았다면 두 가지 방식이 있다.
기능을 바로 확인할 때는 압축을 푼 폴더에서 가상 환경 생성과 검증 명령을
실행한다. upstream과 통합할 때는 원본 저장소를 별도 clone하고 ZIP을
그 옆에 푼 다음, Codex에 두 디렉터리를 비교해 의미 단위로 옮기도록 요청한다.
snapshot을 기존 작업 tree 위에 통째로 덮어쓰지 않는다. 정상 git checkout을
전달받은 경우에만 그 폴더의 `git status --short`로 사용자 변경을 먼저 확인한다.
Codex에는 다음과 같이 요청할 수 있다.

```text
docs/UX_REFRESH_REVIEW.md를 먼저 읽고 현재 구현과 diff를 대조해 줘.
사용자 변경을 덮어쓰지 말고, 계정별 정확한 5초/20ms 계약과
240x135 full-bar UI, 4x4 source -> 88x108 runtime asset 파이프라인을 보존해.
자동 refresh/Flash state 기능과 UX 기능을 의미 단위로 병합하고
tools/verify_refresh.py, sprite --check, theme validate, 전체 pytest, preview를 실행해.
실기기에서 확인하지 않은 항목은 통과했다고 추정하지 말고 수동 QA 목록으로 남겨 줘.
```

원본 캐릭터를 수정한 경우에만 일반 생성 모드로 asset을 다시 만든다.

```powershell
.\.venv\Scripts\python.exe tools\gen_sprites.py
.\.venv\Scripts\python.exe tools\gen_sprites.py --check
```

검증 후 Windows 실행 파일이 필요하면 마지막에만 빌드한다.

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm QuotaDeck.spec
```

최종 diff에는 기본/누적 source sheet, 생성 스크립트, manifest, 104개 runtime
sprite, 테스트와 문서를 함께 포함한다. checker 추출 중간 `*.alpha.png`, 임시
프레임, 로컬 `preview.gif`, `.venv`, 캐시, 검증 전 `dist/QuotaDeck.exe`는
release archive나 commit에 넣지 않는다.
