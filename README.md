<p align="center">
  <strong>🇰🇷 한국어</strong> · <a href="README.en.md">🇺🇸 English</a>
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-%3E%3D3.11-3776AB?logo=python&logoColor=white">
  <img alt="Windows" src="https://img.shields.io/badge/Windows-11-0078D6?logo=windows&logoColor=white">
  <img alt="License" src="https://img.shields.io/badge/License-MIT-green">
  <img alt="LCD" src="https://img.shields.io/badge/AULA%20F108%20Pro-240%C3%97135-1EE2B0">
</p>
<p align="center">
  <img src="docs/assets/crew-lineup.png" alt="QuotaDeck crew: Codex, Claude, Cursor, Grok" width="720">
</p>

<p align="center">
  <img src="docs/hud-preview.png" alt="F108 Pro HUD — 캐릭터 베이와 남은 % 카드" width="480">
</p>

---
# QuotaDeck

## v0.2.3 최신 변경 요약 (2026-09-17)

누적 Usage collector와 기간·모델별 토큰 집계, 부분 월 기반 `AVG EST`,
KRW 자동 환율, 컴팩트 설정 UI를 제공합니다. Cursor는 설정의 소스 메뉴에서
Usage CSV를 파일명 변경 없이 가져오거나, Enterprise Team Admin API를 Windows
자격 증명 관리자에 안전하게 연결할 수 있습니다. 연결 전 팝업에서 공식
[Analytics 대시보드](https://cursor.com/dashboard/analytics)와
[Admin API 안내](https://cursor.com/docs/account/teams/admin-api)를 바로 엽니다.

Codex 최신 `token_usage_record`의 `thread_id`를 독립 스트림으로 처리해 같은
parent session을 공유하는 sibling thread가 손상 기록으로 오인되거나 누락되지
않습니다. 실제 TEAM 프로필 재검증은 36개 파일·12,516개 usage event에서
10,387개 observation, malformed 0건이었습니다. `PARTIAL`이어도 신뢰 가능한
비율이 있으면 5단계 반응을 사용하므로 300% 이상은 쓰러짐 캐릭터로 표시합니다.

F108 Pro payload delay는 이 사용자 장치의 관측을 기준으로 인코딩합니다.
이전 `/20` 패킹은 5초 설정이 약 1초로 재생됐고, 이후 `/4` 패킹은 약 2.5초로
관측됐습니다. 소스는 관측 재생 단위 2ms(`delay_byte = logical_ms / 2`)로
2배 보정합니다. GUI/GIF의 논리 시간은 설정한 밀리초 그대로입니다. 프레임당
최대 논리 delay는 500ms이므로 기본 8계정 × 5초는 계정당 10프레임이 필요하고,
숨은 frame budget 기본값은 80입니다. 81~141프레임이 필요한 유효 조합
(예: 8계정 × 6초=96, 9계정 × 5초=90, 3계정 × 20초=120)은 저장 전에
숨은 budget을 필요량까지 올리고, 141을 넘는 조합은 파일을 쓰기 전에 거절합니다.
프레임이 늘면 HID payload가 커집니다(80≈5.2MB, 96≈6.2MB, 141≈9.1MB).
구버전의 숨은 32와 v7 기본 48은 config v8에서 80으로 올린 뒤, 계정×표시
시간이 더 필요하면 141 이하에서 추가로 맞춥니다. 이번 보정은 사용자 관측
기반이며, 연결된 키보드에서 스톱워치 재측정이 필요합니다.
`dist/QuotaDeck.exe`는 2026-09-17에 2ms 보정·config v8·`NO AVG`를 반영해
다시 빌드했습니다.

Windows 기본 auto/Win32 전송은 `GET_FEATURE`가 65바이트 또는 64바이트를 돌려줘도 둘 다 리포트 ID(`buffer[0]`)를 제거하고 페이로드 접두사 `04 18 00 01`을 만들며, 64바이트 카운트는 부족한 마지막 바이트만 패딩하고, 그보다 짧은 읽기는 거절합니다.

2026-09-12 연결된 F108 Pro에서 기본 Win32로 8프레임·520,192바이트를 전송하고 127개 페이지와 최종 적용 명령의 ACK를 확인했습니다. 검증에는 실제 청구액이 아닌 합성 Cursor export를 사용했습니다. [실기기 검증 기록](docs/WIN32_LCD_VERIFICATION.md)

스프라이트 컴파일러는 Pillow `FASTOCTREE`의 libc `qsort` 타이브레이크를 쓰지 않습니다. occupancy가 같은 색 큐브는 공간 인덱스로 순서를 고정해 Windows CI와 커밋된 104개 런타임 PNG가 같은 팔레트를 만듭니다. 런타임 아트 파일은 바꾸지 않았습니다.

### 통합 검증 상태

스프라이트 컴파일러는 Pillow `QuantOctree.c`를 바탕으로 한 고정 순서 알고리즘을 사용하며, 출처와 라이선스는 [LICENSE](LICENSE)에 기록했습니다. 문서 미리보기 내보내기는 픽셀이 같을 때 기존 PNG 바이트를 유지합니다. `python tools/gen_sprites.py --check`(기본 환경과 `PYTHONHASHSEED=1`), `tools/verify_refresh.py`, 문서 내보내기를 독립적으로 통과했습니다. `pyproject.toml`의 테스트 경로에 `src`와 저장소 루트를 함께 지정해 `pytest`와 `python -m pytest` 모두 `tools` 회귀 테스트를 수집합니다.

2026-09-17 최신 로컬 통합 검증은 **524 passed, 1 skipped, 8 subtests
passed**였고, 문서·스프라이트·frame budget 검사와 PyInstaller 패키지 렌더
스모크 테스트도 통과했습니다. 건너뛴 테스트는 Windows 디렉터리 심볼릭 링크
권한이 필요한 기존 테스트입니다. 이전 Windows CI 기록은
[여기](https://github.com/guyster323/F108pro_Usage_monitor/actions/runs/34682870012)에서 확인할 수 있습니다.

<p align="center">
  <img src="docs/cumulative-coin-preview.png" alt="Display 비용 환산 행의 코인 픽셀 아이콘" width="480">
</p>

자세한 변경 사항은 [v0.2.3 변경 노트](CHANGE_NOTE_v0.2.3_KO.md)를 참고하세요.

## Changelog

### 2026-09-17
- F108 payload delay를 이 장치 관측 기준 2ms 단위(`delay_byte = logical_ms / 2`)로 보정했습니다. 이전 `/20`은 약 1초, `/4`는 약 2.5초로 재생됐습니다.
- 숨은 frame budget 기본값을 48→80으로 올리고 설정 스키마를 **config v8**로 올렸습니다. 81~141프레임이 필요한 조합은 저장 전 자동 상향하고, 141을 넘으면 파일을 쓰기 전에 거절합니다.
- 평균을 만들 수 없을 때 `BUILD` 대신 `NO AVG`를 쓰고, GUI tooltip에 일/월 완료 이력 부족 이유를 표시합니다.
- `dist/QuotaDeck.exe`를 위 변경으로 다시 빌드했습니다. 연결된 키보드에서 스톱워치 재측정이 필요합니다.
- 로컬 통합 검증: **524 passed, 1 skipped, 8 subtests passed**.

### 2026-09-16
- Cursor Usage CSV 가져오기와 Enterprise Team Admin API 안내 연결을 추가했습니다. API 키는 Windows 자격 증명 관리자에만 둡니다.
- Codex `token_usage_record`를 `thread_id` 독립 스트림으로 묶어 sibling thread 누락을 막았습니다.
- 부분 월 `AVG EST`, KRW 자동 환율(미국 재무부 분기 고시 → 캐시 → 1,400원/USD), 컴팩트 설정 UI를 제공합니다.
- GUI 모드 전환 스냅샷을 유지하고, Cursor/FX 조회는 TLS 검증을 끄지 않습니다.

### 2026-09-12
- Win32 `GET_FEATURE` 64/65바이트를 리포트 ID 제거 후 정규화하고, 연결된 F108 Pro에서 8프레임 업로드 ACK를 확인했습니다.
- 스프라이트 컴파일러가 Pillow `FASTOCTREE`의 libc `qsort` 타이브레이크를 쓰지 않아 Windows CI와 104개 런타임 PNG 팔레트가 같아졌습니다.
- 표시 정보 콤보박스가 Qt `QPaintDevice.metric()`을 가리던 기동 오류를 수정했습니다.

이미 이 PC에 로그인된 AI 코딩 계정의 **잔여 Rate Limit** 또는 이 기기에 보존된
**누적 토큰 사용량**을 AULA F108 Pro 키보드 LCD에 보여 주는 데스크톱 앱입니다.
자격 증명과 대화 내용은 복사하지 않습니다. 공식 CLI나 앱이 들고 있는 로그인과
로컬 사용량 메타데이터를 읽고, 체크한 계정만 240×135 RGB565로 올립니다.

> 처음 쓰신다면 아래 **시작하기**만 따라가면 됩니다. 프로토콜·테마·보안은 [docs/](docs/)를 보세요.
## 아키텍처

```
Discovery (CLI / App) → 계정 선택 → Providers / 로컬 기록 → quota / cumulative snapshot
    → Severity → Scheduler → SceneEngine → Frames
    → RGB565 payload → F108 driver
                 ↘ PNG 미리보기 (설정 앱)
```

- **렌더러는 하드웨어와 말하지 않습니다.** `quotadeck render`는 키보드 없이 동작합니다.
- **설정 앱**은 계정 체크·별명·표시 데이터·시간·한/영·미리보기·수동 업로드를 담당합니다. 트레이로 실행 중이면 설정한 주기로 자동 조회하고, 유선 키보드가 준비됐을 때만 안전 한도 안에서 자동 업로드합니다.
- 업로드는 키보드 LCD용 SPI 플래시를 다시 씁니다. 기본은 **10분마다**만 기록합니다. 아래 **LCD 플래시 수명**을 보세요.

## 계정 탐색과 잔여 리밋

| 제공자 | 출처 | 찾는 로그인 | 상태 |
| --- | --- | --- | --- |
| OpenAI Codex | CLI | `~/.codex`, `CODEX_HOME`, extra homes | Live |
| Cursor | App / CLI | `%APPDATA%\Cursor\...\state.vscdb` 또는 `auth.json` | Live |
| Anthropic Claude | CLI | `~/.claude/.credentials.json` | Community |
| xAI Grok | CLI | `~/.grok/auth.json` | Community |

같은 Cursor 사용자가 앱과 CLI에 모두 있으면 앱을 우선해 한 줄로 보여 줍니다. 다른 계정이면 둘 다 고를 수 있습니다.
<p align="center">
  <img src="docs/assets/crew-codex.png" alt="Codex" width="160">
  <img src="docs/assets/crew-claude.png" alt="Claude" width="160">
  <img src="docs/assets/crew-cursor.png" alt="Cursor" width="160">
  <img src="docs/assets/crew-grok.png" alt="Grok" width="160">
</p>

한 계정이 LCD 전체를 정확히 같은 시간 동안 씁니다. 잔여 리밋 모드에서는 왼쪽에
88×108 쿼터뷰 캐릭터, 오른쪽에 화면 높이를 채우는 Quota Bar를 둡니다. Bar 안에는
**5H / WEEKLY / AUTO / OTHER**와 남은 %만 크게 표시됩니다. 잔량 경계를 기준으로
채워진 쪽 글자는 흰색, 비워진 쪽은 검정으로 자동 전환됩니다. Cursor는 설정 화면의
**AUTO**·**OTHER** 값을 우선하고, 해당 필드가 없을 때만 `used/limit` 센트 기반
**PLAN**으로 대체합니다.

- 50–100 idle · 20–49 busy · 10–19 caution · 1–9 critical · 0 exhausted
- plus offline / stale / reset

테마는 `themes/quotadeck-crew`입니다. 공식 벤더 마스코트는 넣지 않습니다.

## 0.2 표시 모드

설정 창의 **표시 정보**에서 두 모드를 독립적으로 고릅니다.

<p align="center">
  <img src="docs/cumulative-preview.gif" alt="누적 사용량 모드 — 평균 대비 5단계 놀람 반응" width="480">
</p>

- **잔여 리밋** — 기존처럼 provider quota API의 남은 비율과 reset 상태를 표시합니다.
- **누적 사용량** — 일별 또는 월별로 선택한 기간의 토큰과 완료 기간 평균,
  평균 대비 Bar, 조건부 API 정가 환산치를 표시합니다.

누적 모드는 계정 전체 청구 원장이 아니라 **현재 프로필/기기에 남아 있는 기록**을
최대 365일까지 관측합니다. LCD에는 선택한 기간의 `THIS`와 `AVG`만 표시하고
전체 누적값이나 모델명은 넣지 않습니다. 기록이 없거나 범위 밖이면 0으로 만들지
않고 `N/A`입니다. 보존 범위의 전체 모델별 합계는
`quotadeck usage --cumulative --models`에서 확인할 수 있습니다.

| 서비스 | 누적 데이터 출처 | 기간별 토큰·GUI 모델·최대 365일 | LCD 비용 |
| --- | --- | --- | --- |
| Codex | 로컬 `sessions`, `archived_sessions` JSONL | 지원 | 가상 API 정가 `LIST` (명시적 API-key 또는 구독; 보고 지출 아님). 커스텀 엔드포인트는 `N/A` |
| Claude Code | 로컬 `projects/**/*.jsonl` 응답 메타데이터 | 지원 | 가상 API 정가 `LIST` (명시적 API-key 또는 구독; 보고 지출 아님). 커스텀 엔드포인트는 `N/A` |
| Cursor | GUI에서 가져온 Usage CSV 또는 Enterprise Team Admin API. 고급 사용자는 token-stats/export 사용 | 조건부 | 귀속 가능한 데이터가 있으면 가상 API 정가 `LIST`. 보고 지출과 섞지 않음 |
| Grok Build | `grok usage`는 개별 세션 총계만 제공 | 기본 계정 카드는 N/A; 별도 보존한 external OTel v1 이벤트를 공급할 때만 일별 분석 가능 | 세션 총계는 일별 THIS/AVG로 표시하지 않음 |

Cursor의 정확한 계정 범위 누적량에는 관리자 API 또는 신뢰할 수 있는
export가 필요합니다. 0.2.3는 `state.vscdb`로 토큰을 추정하지 않습니다.
설정에서 CSV를 한 번 고르면 `%APPDATA%\QuotaDeck\cursor-usage\`로 복사되고,
Enterprise 관리자는 Windows 자격 증명 관리자에 둔 Admin API 키로 현재
사용자 이벤트만 시간당 최대 한 번 가져올 수 있습니다. HTTP 200에 이벤트가
없어도 연결은 유효합니다. 이전 캐시가 없으면 측정 0 대신 no-usage-yet를
보여 주고, 기존 last-known-good은 stale로 유지합니다. 미연결 Cursor는
누적 LCD에서만 숨깁니다. token-stats는 선택적 우선 collector이고,
Codex/Claude native parser가 fallback이며, ccusage는 검증/fallback
경계입니다. 설치는 선택 사항입니다.
Grok 세션은 resume/fork 이력이 겹칠 수 있어 합산하지 않으며, `updatedAt`을
사용 날짜로 간주하지 않습니다. 전체 계약과 가격 제한은
[누적 사용량 모드](docs/CUMULATIVE_USAGE.md)와
[COLLECTORS.md](docs/COLLECTORS.md)를 보세요.

일별은 오늘과 이전 완료일 평균을 비교하며 최소 7일의 완료일이 필요합니다.
월별은 이번 달 1일부터 현재까지(MTD)와 이전 완료 월들의 월평균을 비교하며
최소 1개의 과거 월 표본이 필요합니다. 최초 관측이 월 중간이면 보존된 달력일의
일평균에 해당 월의 전체 일수를 곱해 첫 월을 추정하고 `EST`로 표시합니다.
그 이후 사용이 없던 완료 월은 0으로 포함합니다. 현재 월의 사용량 자체는 월말
예상치로 보정하지 않습니다.

| 선택 기간 `THIS ÷ AVG` | 캐릭터 반응 |
| ---: | --- |
| `< 1.0×` | 차분함 |
| `1.0× – < 1.5×` | 살짝 놀람 |
| `1.5× – < 2.0×` | 명확히 놀람 |
| `2.0× – < 3.0×` | 크게 충격받음 |
| `≥ 3.0×` | 코믹하게 쓰러짐 |

누적 LCD의 Bar는 `THIS 토큰 ÷ AVG 토큰 × 100`입니다. 채움은 100%에서
포화되지만 숫자는 150%, 200%를 유지하고 300% 이상을 `300%+`로 표시합니다.
Bar 아래에는 큰 글씨로 같은 단위의 토큰과 비용을 나란히 표시합니다.
비용 행 좌측 시작에는 작은 투명 픽셀 코인 아이콘이 있습니다.
정상 Bar 제목은 선택 기간에 따라 `M AVG` 또는 `D AVG`, 첫 부분 월을 환산한
평균은 `M EST`로 보입니다. 작은
LCD에는 통화명과 한글을 넣지 않고 비용도 ASCII `K/M/B` 단위만 사용합니다.
읽기 오류가 있는 subtotal은 숫자를 유지하면서 `M/D PARTIAL`로 표시합니다.
사용할 과거 완료 이력이 부족해 평균을 만들지 못할 때는 `M/D NO AVG`, 귀속
가능한 원장이 없을 때만 `M/D N/A`로 구분됩니다. `NO AVG`는 계산 근거 없는
AVG를 합성하지 않으며, GUI tooltip에 일간 완료 이력 n/7 또는 월간 완료 과거
월 n/1과 현재 기간이 시작일부터 완전한지를 표시합니다.

```text
THIS   AVG          THIS   AVG
0.6B   1.2B         0.6B   1.2B
1K     2K           0.4K   0.8K
```

왼쪽 비용 예시는 GUI에서 `KRW (만원)`을 선택한 경우입니다. 즉 `1K = 천만원`,
`1M = 백억`, `1B = 10조`이며, 이 환산 범례는 원화 선택 시 설정 창에 표시됩니다.
오른쪽은 USD 선택 예시입니다. LCD 자체에는 통화 레이블을 반복하지 않습니다.

모델 정보는 LCD에서 제외했습니다. 설정 앱에는 선택 기간의 상위 2개 모델을
표시하고, tooltip에서 전체 모델별 사용량을 확인할 수 있습니다.

비용은 2026-09-11 공식 API 정가의 내장 스냅샷으로 계산한 **정가 환산치**입니다.
선택 기간의 `THIS`와 `AVG`를 모두 완전하게 계산할 수 있을 때만 두 값을 함께
표시합니다. 실제 지출액이나 청구서로 보지 마세요. 구독 이력도 토큰·모델
범주가 완전하면 같은 가상 `LIST` 환산을 표시할 수 있습니다. 커스텀 엔드포인트,
알 수 없는 모델/토큰 범주, partial 기록은 `THIS`와 `AVG` 모두 `--`(미산정)입니다.
`quotadeck prices`는 OpenAI, Anthropic, Cursor, xAI의 snapshot 행을 보여 줍니다.
가격 행이 있다고 해당 서비스의 계정 누적 원장이 구현됐다는 뜻은 아닙니다.
KRW/USD를 선택할 수 있습니다. LCD·GUI 원화 환산의 기본은 **자동 환율**입니다.
미국 재무부 분기 공식 고시환율(실시간 현물가가 아님)을 조회하고, 실패하면
last-known-good 캐시, 그다음 기본 **1,400원/USD** 수동 폴백을 씁니다.
`quotadeck usage-engine`과 같은 체인입니다.

## 시작하기 (Windows 11)

### 실행 파일

저장소의 [dist/QuotaDeck.exe](dist/QuotaDeck.exe)는 Python 설치 없이 실행할 수
있습니다. 2026-09-17에 관측 2ms LCD 인코딩, config v8 frame budget 80,
`NO AVG` 표시를 반영해 다시 빌드했습니다. 연결된 키보드에서 스톱워치로
재생 시간을 한 번 더 확인해야 합니다.
직접 빌드하려면 아래 소스 실행 안내와 `QuotaDeck.spec`을 사용하세요.

1. F108 Pro를 **USB-C 유선**으로 연결하고 `Fn+4`를 누릅니다.
2. 공식 AULA 프로그램이 켜져 있으면 종료합니다.
3. 보고 싶은 제공자에 이 PC에서 한 번 로그인합니다 (CLI 또는 앱).
4. `QuotaDeck.exe`를 실행한 뒤 계정을 고르고 **지금 키보드에 올리기**를 누릅니다.
5. 언어는 창 오른쪽 위 **EN** / **한**으로 바꿉니다.

### 소스에서 실행

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe tools\gen_sprites.py
.\.venv\Scripts\python.exe -m quotadeck ui
```

설정 앱에서:

- **EN / 한** — 앱 표시 언어를 바꿉니다. 선택한 언어는 설정에 저장됩니다.
- **표시 정보** — **잔여 리밋** 또는 **누적 사용량**을 고릅니다. **저장**이나
  **미리보기**를 누르면 런타임에도 적용됩니다.
- **누적 집계 기간** — **일별(오늘/일평균)** 또는 **월별(금월/월평균)**을
  고릅니다. 기본은 월별입니다.
- **비용 통화** — **KRW (만원)** 또는 **USD**를 고릅니다. KRW 기본은 **자동
  환율**이며 미국 재무부 분기 공식 고시(실시간 현물가 아님) → 캐시 → 수동
  1,400원/USD 순입니다. 자세한 출처·기준일은 환율 아이콘 hover에 있습니다.
  원화 K/M/B 범례(`1K = 천만원`)도 hover로 옮겨 두었습니다.
- **Cursor 소스 메뉴** — 개인 사용자는 안내 팝업의 Analytics 링크에서 Usage
  CSV를 받은 뒤 바로 선택합니다. Enterprise 관리자는 같은 메뉴에서 Admin API
  키를 검증·연결합니다. 키는 설정 파일이나 로그에 저장되지 않습니다.
- **계정 다시 찾기** — 이 PC의 CLI/앱 로그인을 다시 스캔합니다.
- 체크박스 — 키보드에 올릴 계정만 켭니다. 별명은 LCD에 표시됩니다.
- **미리보기** — 업로드 없이 선택한 잔여/누적 HUD를 확인합니다. 모든 계정은 설정한 계정당 표시 시간만큼 순환합니다.
- **지금 키보드에 올리기** — 선택한 계정을 F108 Pro **사용자 GIF 슬롯**에 기록합니다. 통신 중에는 버튼이 잠깁니다.

시간 기본값은 **60초 / 5초 / 10분**입니다. 세 값은 서로 다릅니다.

| 설정 | 기본값 | 하는 일 |
| --- | --- | --- |
| **사용량 조회** | 60초 | PC가 provider API 또는 로컬 기록에서 숫자를 다시 읽습니다. 이 횟수는 플래시 기록 횟수가 아닙니다. |
| **계정당 표시 시간** | 5초 | 캐릭터 애니메이션을 포함한 계정 전체 화면의 총 시간입니다. N개면 한 바퀴 약 N×5초이며, 렌더러가 이 값을 다시 곱하지 않습니다. |
| **키보드 기록** | 10분 | 키보드 내부 저장공간에 다시 쓰는 최소 간격입니다. 일일 추정은 활성 16시간 기준(10분≈96회, 7분≈138회)이며 앱 안전 한도와는 별개입니다. |

새 설정이나 시간 필드가 없는 설정은 5초입니다. 업그레이드할 때는 사용자가
정한 값을 덮어쓰지 않기 위해 기존 `scene_hold_seconds`(2~20초)를 보존하므로,
예전 4초/10초가 남아 있으면 설정에서 5초로 한 번 변경하세요.
현재 설정 스키마는 **v8**입니다. 누적 기간·통화·환율과 Cursor CSV/Admin
바인딩을 저장하며, API 키는 제외합니다. 이전 설정은 월별·KRW·자동 환율·
1,400원/USD 폴백과 frame budget 80으로 안전하게 마이그레이션됩니다. v7의
숨은 기본값 48은 새 안전값 80으로 올리고, 사용자가 명시한 다른 커스텀
budget은 보존한 뒤 1..141으로 clamp합니다. 8계정 × 6초처럼 80을 넘지만
141 이하인 조합은 필요량까지 자동 수용합니다.

트레이 앱은 이 주기로 자동 조회합니다. 조회 뒤 렌더 결과가 바뀌었거나 오래된 경우에도
별도의 최소 기록 간격과 영속화된 하루 안전 한도를 통과할 때만 키보드에 씁니다.
유선 키보드가 준비되지 않았으면 장치 기록을 시도하지 않고 화면·트레이 미리보기만
갱신합니다. 트레이는 앱 열기, 수동 업로드, **진단 로그 폴더 열기**를 제공합니다.

### 트레이가 사라질 때

GUI를 실행할 때마다 회전 진단 로그가 자동 생성됩니다. 트레이가 이미 사라져 메뉴를
열 수 없다면 `Win+R`에 아래 경로를 붙여 넣으세요.

```text
%APPDATA%\QuotaDeck\logs
```

가장 최근 `quotadeck-ui-*.log`와 같은 시각의 `*-crash.log`를 확인합니다. 정상 종료는
`event=process_end clean=true reason='tray_quit'`로 끝납니다. 이 줄 없이 끝났거나
`worker_exception`, `python_unhandled_exception`, `qt_message`, `tray_heartbeat` 이후 기록이
끊겼다면 [트레이 종료 진단 가이드](docs/TRAY_DIAGNOSTICS.md)의 판독표를 사용하세요.
기본 경로에 쓸 수 없으면 `%TEMP%\QuotaDeck\logs`로 자동 대체합니다.
이번 보강은 로그 추가와 함께 실행 중인 `QThread` 참조 경쟁 및 64-bit Win32 HID
HANDLE 잘림도 수정하고, LCD ACK 대기에 실제 timeout을 적용합니다.

### CLI

```powershell
.\.venv\Scripts\python.exe -m quotadeck probe
.\.venv\Scripts\python.exe -m quotadeck clock
.\.venv\Scripts\python.exe -m quotadeck detect
.\.venv\Scripts\python.exe -m quotadeck detect --apply
.\.venv\Scripts\python.exe -m quotadeck usage
.\.venv\Scripts\python.exe -m quotadeck usage --cumulative --models
.\.venv\Scripts\python.exe -m quotadeck prices
.\.venv\Scripts\python.exe -m quotadeck prices --provider codex --model gpt-5.6-sol
.\.venv\Scripts\python.exe -m quotadeck render --fixture tests\fixtures\usage.json --out preview.gif --hold-seconds 5 --mode fixed
.\.venv\Scripts\python.exe -m quotadeck run --once
.\.venv\Scripts\python.exe -m quotadeck ui
```

`detect` + 저장 이후 `quotadeck run`은 양자화된 스냅샷이 바뀌거나 최대 age에
도달했을 때만, 최소 간격과 하루 한도를 통과한 뒤 플래시에 씁니다.

## LCD 플래시 수명

키보드 LCD 화면은 내부 SPI 플래시에 저장됩니다. 업로드할 때마다 그 슬롯을 지우고 다시 씁니다. 소비자 SPI NOR는 보통 **약 10만 회** 소거/기록이 한계입니다. F108 Pro 칩의 공칭값은 공개되어 있지 않아, 아래는 그 일반적인 수명을 기준으로 한 추정치입니다.

기본 **최소 업로드 간격은 10분**입니다. 하루 16시간 사용이면 시간당 6회,
**하루 약 96회**입니다. 주기 기준 상한은 첫 기록 기회를 포함해
`ceil(960분 / 최소 업로드 간격)`으로 계산하며, provider 조회 주기와 무관합니다.
따라서 나누어떨어지지 않는 7분도 137회가 아니라 **138회**입니다.

| 최소 업로드 간격 | 주기 기준 하루 상한 (16시간) | 10만 회 기준 기대 수명 |
| --- | --- | --- |
| **10분 (기본)** | 약 96회 | **약 2.9년** |
| 7분 | 약 138회 | 약 2.0년 |
| 30분 | 약 32회 | 약 8.6년 |
| 60분 | 약 16회 | 약 17년 |
| 1분 | 약 960회 | 약 3.4개월 |

기본 하루 100회 안전 한도와 업로드 직전의 **보수적 예약 횟수·시각**은
`flash-state.json`에 원자적으로 저장됩니다. 여러 앱/CLI 인스턴스도 프로세스 간
잠금으로 판단부터 전송까지 직렬화되며, 재시작이나 런타임 재생성 뒤에도 복원됩니다.
전송 실패나 충돌로 플래시가 일부 쓰였을 가능성도 안전 횟수 1회로 남을 수 있습니다.
위 표와 설정 화면의 수명은 안전 한도를 적용하기 전의 보수적인 주기
상한이며, 7분 예시의 실제 앱 기록은 기본 하루 한도 때문에 최대 100회입니다.
이 안전 한도는 위의 주기·수명 추정치와 별도입니다. **지금 키보드에 올리기**는 최소
기록 간격을 건너뛸 수 있지만 하루 안전 한도는 건너뛰지 않습니다.

사용량 숫자가 거의 안 바뀌면 업로드를 건너뛰므로 실제 기록은 이보다 적을 수 있습니다.
표의 숫자는 **안전 한도를 적용하기 전 주기 기준 상한**입니다. 예를 들어 1분 주기는
계산상 960회지만 실제 기록은 기본 하루 안전 한도인 100회를 넘지 않습니다.

> **주의: 너무 빠르게 갱신하면 키보드 내부 저장공간의 수명이 감소합니다.** 기본값 10분을 유지하세요. **지금 키보드에 올리기**를 연속으로 눌러도 같은 슬롯을 반복해서 지웁니다.

## 지켜야 할 원칙

- **토큰을 저장소나 config에 넣지 않습니다.** `config.json`에는 별명과 계정 id만 둡니다.
- **로컬 기록은 계정 청구서가 아닙니다.** 같은 프로필을 다른 로그인/API key와
  함께 사용하면 과거 기록이 섞일 수 있습니다.
- **141프레임을 넘기지 않습니다.** F108 펌웨어는 슬롯을 강제하지 않습니다. 넘기면 온보드 메뉴 그래픽이 영구적으로 깨질 수 있습니다. QuotaDeck은 잘못된 크기·장수 업로드를 거부하며 우회 플래그가 없습니다.
- **공장 GIF 슬롯(0)에 쓰지 않습니다.** 사용자 화면은 슬롯 1입니다.
- **USB-C 유선(`Fn+4`)만 지원합니다.** 업로드 전에 공식 AULA 소프트웨어를 끄세요.
- **플래시를 너무 자주 쓰지 않습니다.** 기본 10분 간격입니다. 너무 빠르게 하면 수명이 감소합니다.
- **TLS 검증을 끄지 않습니다.** 회사 프록시/자체 서명 CA 때문에 Cursor 등 사용량 API가 실패하면 OS 신뢰 저장소에 CA를 설치하거나 `QUOTADECK_CA_BUNDLE`(또는 `SSL_CERT_FILE`)에 PEM 번들을 지정하세요. 로그인과 사용량 조회 실패는 상태/자물쇠 hover에 구분되어 표시됩니다.

## 더 읽기

- [docs/UX_REFRESH_REVIEW.md](docs/UX_REFRESH_REVIEW.md) — UI 개선 방향·코드리뷰·검증 기준
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — 파이프라인
- [docs/PROVIDERS.md](docs/PROVIDERS.md) — CLI/앱 자격 증명과 usage API
- [docs/CUMULATIVE_USAGE.md](docs/CUMULATIVE_USAGE.md) — 누적 토큰·평균·모델·가격 지원 범위
- [docs/SECURITY.md](docs/SECURITY.md) — 토큰·로그 마스킹
- [docs/TRAY_DIAGNOSTICS.md](docs/TRAY_DIAGNOSTICS.md) — 트레이 종료 로그 위치·판독법
- [docs/F108_PROTOCOL.md](docs/F108_PROTOCOL.md) — HID 업로드
- [docs/THEMES.md](docs/THEMES.md) — 스프라이트 팩

MIT. 프로토콜 메모는 [parsiya/f108-pro](https://github.com/parsiya/f108-pro) (MIT)에서 왔습니다.
Usage 패턴은 CodexBar, openusage, pane을 참고했습니다. 라이선스 없는 소스는 복사하지 않습니다.
