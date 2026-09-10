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

이미 이 PC에 로그인된 AI 코딩 계정의 Rate Limit을 AULA F108 Pro 키보드 LCD에 보여 주는 데스크톱 앱입니다.
토큰은 복사하지 않습니다. 공식 CLI나 앱이 들고 있는 로그인만 읽고, 체크한 계정만 240×135 RGB565로 올립니다.

> 처음 쓰신다면 아래 **시작하기**만 따라가면 됩니다. 프로토콜·테마·보안은 [docs/](docs/)를 보세요.

## 아키텍처

```
Discovery (CLI / App) → 계정 선택 → Providers → UsageSnapshot
    → Severity → Scheduler → SceneEngine → Frames
    → RGB565 payload → F108 driver
                 ↘ PNG 미리보기 (설정 앱)
```

- **렌더러는 하드웨어와 말하지 않습니다.** `quotadeck render`는 키보드 없이 동작합니다.
- **설정 앱**은 계정 체크·별명·시간·한/영·미리보기·지금 올리기를 담당합니다. 트레이로 숨긴 뒤에도 조회가 이어집니다.
- 업로드는 키보드 LCD용 SPI 플래시를 다시 씁니다. 기본은 **10분마다**만 기록합니다. 아래 **LCD 플래시 수명**을 보세요.

## 지원 계정

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

한 계정이 LCD 전체를 씁니다. 왼쪽은 캐릭터, 오른쪽은 남은 % 카드입니다. Cursor는 설정 화면과 같은 **AUTO**(Cursor Models)·**OTHER**(Other Models) 두 줄을 보여 줍니다. `used/limit` 센트 값이 아닙니다.

- 50–100 idle · 20–49 busy · 10–19 caution · 1–9 critical · 0 exhausted
- plus offline / stale / reset

테마는 `themes/quotadeck-crew`입니다. 공식 벤더 마스코트는 넣지 않습니다.

## 시작하기 (Windows 11)

### 실행 파일 (권장)

Python을 설치하지 않아도 됩니다. 저장소의 [`dist/QuotaDeck.exe`](dist/QuotaDeck.exe)를 받아 더블클릭하세요.

1. F108 Pro를 **USB-C 유선**으로 연결하고 `Fn+4`를 누릅니다.
2. 공식 AULA 프로그램이 켜져 있으면 종료합니다.
3. 보고 싶은 제공자에 이 PC에서 한 번 로그인합니다 (CLI 또는 앱).
4. `QuotaDeck.exe`를 실행한 뒤 계정을 고르고 **지금 키보드에 올리기**를 누릅니다.
5. 언어는 창 오른쪽 위 **EN** / **한**으로 바꿉니다.

### 소스에서 실행

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\pip install -e ".[dev]"
python tools\gen_sprites.py
quotadeck ui
```

설정 앱에서:

- **EN / 한** — 앱 표시 언어를 바꿉니다. 선택한 언어는 설정에 저장됩니다.
- **계정 다시 찾기** — 이 PC의 CLI/앱 로그인을 다시 스캔합니다.
- 체크박스 — 키보드에 올릴 계정만 켭니다. 별명은 LCD에 표시됩니다.
- **미리보기** — 업로드 없이 남은 %와 HUD를 확인합니다. 미리보기는 화면 유지 간격으로 돌아갑니다.
- **지금 키보드에 올리기** — 선택한 계정을 F108 Pro **사용자 GIF 슬롯**에 기록합니다. 통신 중에는 버튼이 잠깁니다.

시간 기본값은 **60초 / 10초 / 10분**입니다. 세 값은 서로 다릅니다.

| 설정 | 기본값 | 하는 일 |
| --- | --- | --- |
| **사용량 조회** | 60초 | PC가 API에서 숫자를 다시 읽습니다. 키보드 플래시에는 쓰지 않습니다. |
| **화면 유지** | 10초 | LCD에서 계정 카드가 다음으로 넘어가기 전에 머무는 시간입니다. |
| **키보드 기록** | 10분 | 키보드 내부 저장공간에 다시 쓰는 최소 간격입니다. |

트레이로 숨긴 뒤에도 60초 주기 조회가 이어지고, 트레이 메뉴의 **지금 사용량 조회**로 즉시 갱신할 수 있습니다(플래시 기록 없음). 기록 예산(마지막 기록 시각·당일 횟수)은 재시작 후에도 유지됩니다. `Windows 시작 시 실행`을 켜면 부팅 후 자동으로 뜹니다.

### CLI

```powershell
quotadeck probe
quotadeck clock
quotadeck detect
quotadeck detect --apply
quotadeck usage
quotadeck render --fixture tests\fixtures\usage.json --out preview.gif
quotadeck run --once
quotadeck ui
```

`detect` + 저장 이후 `quotadeck run`은 양자화된 스냅샷이 바뀔 때만 플래시에 씁니다.

## LCD 플래시 수명

키보드 LCD 화면은 내부 SPI 플래시에 저장됩니다. 업로드할 때마다 그 슬롯을 지우고 다시 씁니다. 소비자 SPI NOR는 보통 **약 10만 회** 소거/기록이 한계입니다. F108 Pro 칩의 공칭값은 공개되어 있지 않아, 아래는 그 일반적인 수명을 기준으로 한 추정치입니다.

기본 **최소 업로드 간격은 10분**입니다. 하루 16시간 사용이면 시간당 6회, **하루 약 96회**입니다(앱 한도 100회).

| 최소 업로드 간격 | 하루 기록 (16시간) | 10만 회 기준 기대 수명 |
| --- | --- | --- |
| **10분 (기본)** | 약 96회 | **약 2.9년** |
| 30분 | 약 32회 | 약 8.6년 |
| 60분 | 약 16회 | 약 17년 |
| 1분 | 약 960회 | 약 3.4개월 |

앱은 하루 100회로 한 번 더 막습니다. 그래도 간격을 줄이거나 **지금 키보드에 올리기**를 남발하면 같은 슬롯을 더 자주 지웁니다. 한도를 올리거나 끄면 수명은 위 표처럼 줄어듭니다.

사용량 숫자가 거의 안 바뀌면 업로드를 건너뛰므로 실제 기록은 이보다 적을 수 있습니다. 표의 숫자는 매 주기마다 썼을 때의 상한입니다.

> **주의: 너무 빠르게 갱신하면 키보드 내부 저장공간의 수명이 감소합니다.** 기본값 10분을 유지하세요. **지금 키보드에 올리기**를 연속으로 눌러도 같은 슬롯을 반복해서 지웁니다.

## 지켜야 할 원칙

- **토큰을 저장소나 config에 넣지 않습니다.** `config.json`에는 별명과 계정 id만 둡니다.
- **141프레임을 넘기지 않습니다.** F108 펌웨어는 슬롯을 강제하지 않습니다. 넘기면 온보드 메뉴 그래픽이 영구적으로 깨질 수 있습니다. QuotaDeck은 잘못된 크기·장수 업로드를 거부하며 우회 플래그가 없습니다.
- **공장 GIF 슬롯(0)에 쓰지 않습니다.** 사용자 화면은 슬롯 1입니다.
- **USB-C 유선(`Fn+4`)만 지원합니다.** 업로드 전에 공식 AULA 소프트웨어를 끄세요.
- **플래시를 너무 자주 쓰지 않습니다.** 기본 10분 간격입니다. 너무 빠르게 하면 수명이 감소합니다.

## 더 읽기

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — 파이프라인
- [docs/PROVIDERS.md](docs/PROVIDERS.md) — CLI/앱 자격 증명과 usage API
- [docs/SECURITY.md](docs/SECURITY.md) — 토큰·로그 마스킹
- [docs/F108_PROTOCOL.md](docs/F108_PROTOCOL.md) — HID 업로드
- [docs/THEMES.md](docs/THEMES.md) — 스프라이트 팩

MIT. 프로토콜 메모는 [parsiya/f108-pro](https://github.com/parsiya/f108-pro) (MIT)에서 왔습니다.
Usage 패턴은 CodexBar, openusage, pane을 참고했습니다. 라이선스 없는 소스는 복사하지 않습니다.
