# F108 Pro LCD UX Refresh 구현·리뷰 문서

이 문서는 240×135 픽셀 AULA F108 Pro LCD에서 QuotaDeck의 캐릭터와 쿼터를 실제로 식별할 수 있게 만든 UX refresh의 설계 의도, 현재 코드 계약, 검증 기준을 정리한다. 단순한 시안이 아니라 현재 저장소 구현을 기준으로 한 인수 문서다.

소프트웨어 렌더링과 정적 검증 경로는 준비되어 있다. 다만 실제 키보드의 밝기, 시야각, RGB565 색 손실과 USB 업로드는 Windows 실기기에서 마지막으로 확인해야 한다.

## 결과 요약

| 요구 사항 | 현재 결과 | 구현 근거 |
|---|---|---|
| 프로바이더별 고품질 픽셀 캐릭터 | Codex, Claude, Cursor, Grok 각각에 이미지 생성 기반 4×4 원본 시트와 8개 상태 × 2프레임의 런타임 스프라이트를 제공한다. 캐릭터는 좌측 88×108 슬롯을 사용하고 2프레임으로 살아 있는 느낌을 준다. | `artwork/sprite_sources/`, `tools/gen_sprites.py`, `themes/quotadeck-crew/` |
| 계정별 충분하고 동일한 회전 시간 | 기본값은 계정당 정확히 5,000ms다. 선택된 계정은 한 번씩만 나오며 요약·전환 프레임이나 위험 계정 중복으로 시간을 빼앗기지 않는다. UI 저장값은 2~20초이고 CLI 미리보기에서도 변경할 수 있다. | `config.py`, `budget.py`, `scenes.py`, `scheduler.py`, `cli.py` |
| Quota 막대 시인성 | 우측 영역 높이를 최대한 사용한 1개 또는 2개의 큰 막대로 바꿨다. 막대 안에는 `5H`, `WEEKLY`, `AUTO`, `OTHER`와 큰 퍼센트만 표시한다. | `layout.py`, `canvas.py` |
| 채움 경계 반응형 글자 | 동일한 글자 마스크를 채운 영역과 빈 영역으로 나누어 채운 쪽은 흰색, 빈 쪽은 검은색 계열로 그린다. 경계가 글자를 가로질러도 한 글자처럼 이어진다. | `draw_split_quota_bar()` |
| 마지막 summary 제거 | 계정 playlist에는 계정 전체 화면만 포함한다. summary/overview/grouped summary와 화면 전환 프레임을 넣지 않는다. | `render_playlist()` |
| 전체 UI 개선 | 헤더에는 14px provider/alias, `현재/전체`, 색상과 형태가 함께 바뀌는 상태 아이콘만 남기고, 좌측 캐릭터·우측 쿼터라는 한눈에 읽히는 구조로 고정했다. 작은 OS 폰트 대신 결정적인 픽셀 폰트를 사용한다. | `layout.py`, `canvas.py` |

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

설정 파일은 `config_version = 2`다. 새 설정 또는 필드가 없는 설정에는 5초를 적용한다. 구버전 JSON에 이미 `scene_hold_seconds`가 있으면 과거 기본값과 사용자의 명시적 선택을 구분할 수 없으므로 2~20초 범위의 4초·10초 같은 명시값은 보존하고 범위 밖 값은 clamp한다. 저장할 때는 항상 현재 config version을 기록한다. GUI는 2~20초 범위이며, `quotadeck render --hold-seconds`도 유한한 2~20초 값과 정확한 20ms 단위만 허용한다.

## Split-text Quota bar

막대는 “남은 quota”를 기준으로 왼쪽에서 오른쪽으로 채운다. 채운 부분은 severity별 어두운 색, 소진된 부분은 밝은 중립 배경이다.

`draw_split_quota_bar()`는 텍스트를 두 번 다른 위치에 그리지 않는다. 먼저 `5H`/`WEEKLY` 같은 scale 2 레이블과 높이 25px의 scale 5 퍼센트를 하나의 흑백 마스크로 만든 뒤, 그 마스크를 fill 영역과 empty 영역 마스크로 각각 자른다. fill 쪽 텍스트에는 거의 흰색인 `BAR_TEXT_ON_FILL`, empty 쪽에는 거의 검은색인 `BAR_TEXT_ON_EMPTY`를 적용한다.

이 방식의 중요한 성질은 다음과 같다.

- 채움 경계가 숫자나 글자를 통과해도 위치가 흔들리거나 글자가 이중으로 보이지 않는다.
- 0%와 100%에서도 track 경계와 글자가 유지된다.
- 값이 없으면 큰 `--%`를 표시한다.
- 문자열은 `5H`, `WEEKLY`, `AUTO`, `OTHER`처럼 짧게 정규화한다.
- reset 시각, plan 등 작은 보조 정보는 LCD에 다시 넣지 않는다.

## 이미지 생성 원본에서 88×108 런타임 asset까지

향후 Codex가 캐릭터를 다시 만들거나 포즈를 고칠 수 있도록 이미지 생성 결과와 펌웨어용 결과물을 분리했다.

```text
GPT image-generation output
                    │
                    ├─ tools/extract_checker_alpha.py  (checker가 그려진 RGB만)
                    ▼  tools/normalize_sprite_sheet.py
artwork/sprite_sources/<provider>.png  (투명 배경 4×4 원본 시트)
                    │
                    ▼  tools/gen_sprites.py
themes/quotadeck-crew/provider/<provider>/<state>_00.png
themes/quotadeck-crew/provider/<provider>/<state>_01.png
                    │
                    ├─ theme.json
                    └─ artwork/sprite_previews/runtime_contact_sheet.png
```

4×4 원본 시트는 `1280×1280` static RGBA PNG이며 각 cell은
`320×320`이다. cell 계약은 고정되어 있다.

| 행 | 열 0~1 | 열 2~3 |
|---:|---|---|
| 0 | `idle` 2프레임 | `busy` 2프레임 |
| 1 | `caution` 2프레임 | `critical` 2프레임 |
| 2 | `exhausted` 2프레임 | `offline` 2프레임 |
| 3 | `stale` 2프레임 | `reset` 2프레임 |

원본 cell은 모든 변에 최소 16px 투명 gutter, cell 폭·높이의 25% 이상인 visible footprint와 primary connected component를 가져야 한다. 상태별 두 포즈의 body baseline 차이는 cell 높이의 3% 이하여야 한다. `tools/normalize_sprite_sheet.py`가 두 포즈에 같은 배율을 적용하고 가장 큰 연결 실루엣을 바닥선에 맞춰 이 계약을 만든다. 생성기가 실제 alpha 대신 회색 checker를 그린 경우에만 `tools/extract_checker_alpha.py`를 먼저 사용하며, 이 휴리스틱 결과는 반드시 눈으로 확인한다.

컴파일러는 각 상태의 두 포즈에서 공통 alpha bounding box를 구하고 3px 여백을 더해 포즈 축을 맞춘다. 그 결과를 LANCZOS로 84×101 content box 안에 맞춘 뒤 장식 파티클이 아니라 가장 큰 8-connected character component를 88×108 canvas의 동일한 하단 앵커에 고정한다. 이어서 alpha threshold 72로 완전 투명/완전 불투명만 남기고, dithering 없이 visible opaque 색을 최대 48색으로 줄인 다음 RGB 채널을 RGB565로 정확히 표현 가능한 값에 맞춘다.

큰 포즈 차이가 빠른 전신 깜빡임으로 보이지 않도록 모든 2-frame 상태는 account slot에 표시 프레임이 2개 이상일 때 보조 포즈 `01`을 slot당 정확히 한 번만 보여 준다. 나머지는 `00`이며 1-frame slot은 정지 화면이다. 기본 8-frame slot은 `00, 00, 00, 01, 00, 00, 00, 00`이다. 개발용 contact sheet에는 provider, state, `00/01`과 cell 경계를 함께 출력해 다음 Codex 작업이 행·열 의미를 추측하지 않게 했다.

현재 산출물은 4 providers × 8 states × 2 frames = 64개의 RGBA PNG다. 각 PNG는 정확히 88×108이며 투명 여백과 불투명 캐릭터 픽셀을 모두 가져야 한다. 모든 상태는 정확히 2개의 서로 다른 프레임을 가져야 한다. `theme.json`은 canvas 240×135, `hud = full-bars-v2`, sprite size 88×108, 4×4 source/compiler와 Pillow 버전을 기록한다. 개발 의존성은 palette/resampling 재현성을 위해 Pillow 12.3.0을 고정한다.

원본을 갱신할 때는 셀 안에 UI 글자나 배경을 넣지 말고, 캐릭터의 발 위치·실루엣·쿼터뷰 방향을 두 프레임 사이에서 유지한다. 생성 프롬프트와 편집 규칙은 `artwork/sprite_sources/PROMPTS.md` 및 `README.md`에 남겨 두었다.

## 주요 변경 파일

| 경로 | 책임 |
|---|---|
| `src/quotadeck/config.py` | 새 설정의 5초 기본값, 기존 명시값 보존, config v2 기록, 2~20초 저장 범위 |
| `src/quotadeck/app/i18n.py` | 설정을 “계정당 표시 시간”으로 명확히 설명하고 미리보기 상태에 hold 표시 |
| `src/quotadeck/cli.py` | `render --hold-seconds`, `--mode`를 저장 설정과 연결하고 범위·tick 검증 |
| `src/quotadeck/renderer/budget.py` | 20ms tick 기반의 균등·정확한 account slot 및 frame budget 오류 처리 |
| `src/quotadeck/renderer/scenes.py` | account-only playlist, summary/transition/중복 제거, 상태 애니메이션 |
| `src/quotadeck/renderer/layout.py` | 240×135 고정 좌표, 88×108 캐릭터 슬롯, 1/2개 full-height quota bar |
| `src/quotadeck/renderer/canvas.py` | 결정적 픽셀 글꼴, 큰 퍼센트, split-colour text mask |
| `src/quotadeck/renderer/sprites.py` | schema/HUD, 88×108 static RGBA, binary alpha, visible palette, symlink, 상태/프레임 무결성의 strict runtime 검증 |
| `src/quotadeck/devices/aula_f108/payload.py`, `protocol.py` | 전송 입력을 불변 snapshot으로 고정한 뒤 20ms delay, 5,100ms/frame, 141-frame header, 정확한 payload 길이를 HID 쓰기 전에 재검증 |
| `tools/extract_checker_alpha.py`, `tools/normalize_sprite_sheet.py` | 생성 결과의 제한적 checker 복구, 1280×1280 grid·cell gutter·body baseline 정규화 |
| `tools/gen_sprites.py` | 4×4 원본을 64개 런타임 asset과 manifest로 재현하고 staging 교체·semantic freshness/contact-sheet 검증 |
| `tools/export_preview.py` | README/문서용 GIF와 RGB565 round-trip hardware-palette preview 출력 |
| `tools/verify_refresh.py` | hardware 없이 실행하는 UX refresh 핵심 acceptance 검사 |
| `artwork/sprite_sources/` | 향후 이미지 생성·수정에 사용하는 provider별 원본 시트와 프롬프트 |
| `themes/quotadeck-crew/` | 배포되는 `theme.json`과 64개 runtime PNG |
| `artwork/sprite_previews/`, `docs/hero-preview.gif`, `docs/hud-preview.png`, `docs/assets/` | sprite 및 최종 LCD 결과 시각 검토물 |
| `tests/test_renderer.py`, `tests/test_quota_bar.py`, `tests/test_theme_assets.py` | 균등 5초, 화면 분리, split text, asset 계약 회귀 테스트 |
| `tests/test_accounts.py`, `tests/test_payload.py`, `tests/test_ui_smoke.py`, `tests/test_cli_help.py` | 기존 설정 보존, 펌웨어 delay/raw payload, GUI/CLI 회귀 테스트 |
| `.github/workflows/build.yml` | Windows에서 asset 재현성, theme, pytest, preview, PyInstaller 검증 |
| `README.md`, `README.en.md`, `docs/THEMES.md`, `docs/ARCHITECTURE.md` | 사용자 사용법과 새 렌더/asset 계약 문서화 |

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
- provider마다 8개 필수 상태, 상태마다 2개 프레임, 총 64개의 88×108 runtime PNG가 존재한다.
- `preview.gif`에서 summary 화면 없이 계정별 전체 화면만 순환하고, provider/alias/두 quota를 축소하지 않고 읽을 수 있다. `docs/hud-preview.png`는 첫 원본 frame을 RGB565 encode/decode한 hardware-palette simulation이다.

자동 검사가 통과해도 실기기 완료 조건은 별도다. Windows에서 공식 AULA 앱을 완전히 종료하고, 키보드를 USB 케이블로 연결한 뒤 사용자 LCD slot에 업로드한다. 실제 LCD에서 각 계정을 스톱워치로 5초씩 확인하고, 0%·50%·100% bar의 경계 글자, 두 줄 quota, 네 provider의 상태 애니메이션, 밝기와 시야각을 확인한다. 이 실기기 검증 전에는 “hardware verified”로 표시하지 않는다.

### 이 source 전달본의 검증 기록 — 2026-09-10

| 검사 | 결과 |
|---|---|
| `python -m compileall -q src tools tests` | 통과 |
| `python tools/gen_sprites.py` 후 `--check` | 통과 — 4개 source, 64개 runtime PNG, contact sheet 일치 |
| strict `theme validate` | 통과 |
| `tools/verify_refresh.py` | 통과 — config, split bar, identity, visible-window, 5초 slot, payload/NACK 검사 |
| 파괴적 output-path/미관리 target 수동 검사 | 통과 — source/theme/preview 중첩 거부, 기존 marker 보존 |
| 전체 `pytest` | 이 실행 환경에 `pytest`가 없어 미실행 (`No module named pytest`) |
| GUI/provider/device 통합 | 이 실행 환경에 PySide6/httpx/hid가 없어 미실행 |
| Windows exe 빌드 및 실제 F108 Pro 업로드 | 미실행 |

따라서 dependency-light 코드·렌더·asset 계약은 검증됐지만, 위의 미실행 항목은
Windows 로컬 checkout에서 완료해야 한다. 이 표를 전체 pytest나 hardware 검증을
통과했다는 의미로 해석하지 않는다.

## PR #4 충돌 주의

문서 작성 시점의 열린 [PR #4 — `feat: add persistent tray refresh scheduler`](https://github.com/guyster323/F108pro_Usage_monitor/pull/4)는 장기 실행 tray refresh, 상태 영속화, timeout/logging을 다룬다. 이 UX refresh와 목적은 보완 관계지만 파일 단위로 한쪽을 통째로 선택하면 기능이 사라질 수 있다.

직접 겹치는 주요 경로는 `README.md`, `src/quotadeck/app/i18n.py`, `src/quotadeck/cli.py`, `src/quotadeck/config.py`다. PR #4가 수정하는 `main_window.py`, `core/scheduler.py`, `core/severity.py`도 display hold, render hash, UI 상태와 연결되므로 의미 단위 검토가 필요하다. 특히 PR #4의 `dist/QuotaDeck.exe`는 소스 충돌을 해결하고 전체 검증·빌드를 마친 뒤 새로 생성해야 하며, 어느 쪽 바이너리도 그대로 채택하지 않는다.

권장 통합 순서는 다음과 같다.

1. UX refresh를 독립 branch와 commit으로 먼저 보존한다.
2. 별도의 disposable integration branch에서 PR #4를 merge/rebase 또는 commit `3961a44` cherry-pick으로 가져온다.
3. PR #4의 `RefreshController`, flash 상태 영속화, timeout/logging을 유지한다.
4. UX refresh의 새 설정 5초 기본값·기존 설정 보존, render hash의 hold 반영, account-only playlist, full-bar renderer와 asset pipeline을 유지한다.
5. `i18n.py`와 `main_window.py`에서는 장기 refresh 상태와 “계정당 표시 시간” UI가 함께 동작하도록 수동 통합한다.
6. 충돌 해결 뒤 sprite 재현성, 전체 pytest, preview, Windows 빌드와 실기기 업로드를 처음부터 다시 실행한다.

`git checkout --ours <file>` 또는 `git checkout --theirs <file>`로 위 경로 전체를 일괄 해결하지 않는 것이 핵심이다.

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
실행한다. upstream이나 PR #4와 통합할 때는 원본 저장소를 별도 clone하고 ZIP을
그 옆에 푼 다음, Codex에 두 디렉터리를 비교해 의미 단위로 옮기도록 요청한다.
snapshot을 기존 작업 tree 위에 통째로 덮어쓰지 않는다. 정상 git checkout을
전달받은 경우에만 그 폴더의 `git status --short`로 사용자 변경을 먼저 확인한다.
Codex에는 다음과 같이 요청할 수 있다.

```text
docs/UX_REFRESH_REVIEW.md를 먼저 읽고 현재 구현과 diff를 대조해 줘.
사용자 변경을 덮어쓰지 말고, 계정별 정확한 5초/20ms 계약과
240x135 full-bar UI, 4x4 source -> 88x108 runtime asset 파이프라인을 보존해.
PR #4를 통합해야 하면 scheduler/state 기능과 UX 기능을 의미 단위로 병합하고
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

최종 diff에는 source sheet, 생성 스크립트, manifest, 64개 runtime sprite, 테스트와 문서를 함께 포함한다. 임시 프레임, 로컬 `preview.gif`, `.venv`, 캐시, 검증 전 `dist/QuotaDeck.exe`는 commit하지 않는다.
