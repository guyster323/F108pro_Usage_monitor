# Optional usage collectors

QuotaDeck 0.2.3 can read **token-stats** as a preferred optional cumulative
collector and **ccusage** as a validation/fallback boundary. Neither tool is
required. Native Codex/Claude parsers remain the scoped fallback, and Cursor
stays `N/A` until an attributable export exists.

## Priority

```text
token-stats JSON / import   (preferred when dated and scoped)
        ↓ missing / timeout / unknown schema
native Codex / Claude parsers
        ↓ empty native history
ccusage daily JSON          (Codex/Claude only; never Cursor)
```

ccusage never blends with another source. If overlapping daily totals disagree,
QuotaDeck keeps the preferred observations and marks coverage `PARTIAL`.

## Trust boundary

Install the CLIs yourself if you want them. QuotaDeck does not vendor them.

| Source | Used when | Refused when |
| --- | --- | --- |
| token-stats | Dated rows, `--client` matches the card, token categories parse | No executable, timeout, oversized JSON, month-only aggregates, undated `entries` |
| QuotaDeck import | `schema = quotadeck.collector.v1` with timestamps | Prompt/response-only rows, unknown schema |
| ccusage | Documented daily JSON for one Codex or Claude home | Cursor, mixed multi-agent rows, missing dates |
| Cursor export | `usage.<account>.csv` or JSON with account + date + tokens | `state.vscdb`, `auth.json`, generic `usage.csv` without account |

Set an explicit executable or import to enable live collection:

```powershell
$env:QUOTADECK_TOKEN_STATS = "C:\path\to\token-stats.exe"
$env:QUOTADECK_ENABLE_TOKEN_STATS = "1"
$env:QUOTADECK_CCUSAGE = "C:\path\to\ccusage.exe"
$env:QUOTADECK_ENABLE_CCUSAGE = "1"
$env:QUOTADECK_COLLECTOR_IMPORT = "C:\path\to\collector.json"
$env:QUOTADECK_CURSOR_EXPORT = "C:\path\to\usage.work.csv"
```

Without those variables QuotaDeck never spawns a collector. When it does, the
child is started with `shell=False`, a timeout (default 8s), an 8 MiB stdout
cap, and a 20,000-record cap. Stderr is discarded.

## Import document

```json
{
  "schema": "quotadeck.collector.v1",
  "source": "token-stats",
  "observations": [
    {
      "provider": "codex",
      "account": "work",
      "model": "gpt-5.6-sol",
      "observed_at": "2026-09-11T01:00:00Z",
      "session_id": "s1",
      "event_id": "e1",
      "input_tokens": 100,
      "output_tokens": 20,
      "cached_input_tokens": 10,
      "cache_write_tokens": 0,
      "reasoning_output_tokens": 5
    }
  ]
}
```

Reasoning is a subset of output and is never added twice. Prompt and response
bodies are ignored and are not written to the memory-only usage cache.

token-stats `models --json` rows are accepted only when each used row also has
`date`, `day`, `timestamp`, or `observed_at`. The documented undated
`groupBy/entries` object cannot drive calendar THIS/AVG, so QuotaDeck falls
back instead of inventing days.

## Cursor

Cursor cumulative usage is enabled only from exported or server-derived
collector data with account and time attribution. Typical token-stats cache
files live under `%APPDATA%\tokscale\cursor-cache\` as `usage.<account>.csv`.
A file named only `usage.csv` is refused unless every row names an account
or a GUI/config binding selects exactly one account.
`state.vscdb` remains a remaining-limit login source, not a token ledger.

일반 사용자는 환경 변수나 파일명 변경이 필요 없다. 설정에서 Cursor 행의
컴팩트 소스 메뉴로 대시보드 Usage CSV를 고르면 QuotaDeck이 검증한 뒤
`%APPDATA%\QuotaDeck\cursor-usage\`에 원자적으로 복사하고 계정에 연결한다.
계정이 여러 개면 선택창이 뜬다. 같은 메뉴에서 CSV 갱신, Enterprise API 전환,
전체 연결 해제가 가능하다. CSV와 Admin 소스는 동시에 활성이지 않으며,
전환하면 이전 소스를 조용히 남기지 않는다. 데이터가 없는 Cursor는 설정에
남지만 누적 LCD/Preview 순환에서는 빠지고, 잔여 리밋 모드는 그대로다.
지원되지 않는 Cursor만 있으면 누적 playlist는 빈 화면 한 프레임을 보여 준다.

Enterprise/Team 관리자는 같은 메뉴의 Enterprise API로 공식
`filtered-usage-events`를 연결할 수 있다. 연결은 API 검증이 성공할 때만
표시된다. HTTP 200에 현재 사용자 토큰 이벤트가 없어도 자격 증명은 유효하며,
키와 바인딩을 저장하고 시간당 시도를 성공으로 기록한다. 이전 관측이 없으면
측정 0이 아닌 no-usage-yet(N/A) 상태를 보여 주고, 비어 있지 않은
last-known-good 캐시가 있으면 덮어쓰지 않고 stale로 유지한다. 라이브 검증과
캐시/게이트 재사용은 구분되며, 인증/네트워크 실패는 후보 키를 저장하지
않는다. API 키는 `config.json`이 아니라 Windows 자격 증명 관리자에만
저장되며, 현재 로그인 사용자 이벤트만 가져오고 계정별로 최대 1시간에 한 번
동기화한다. 메뉴의 API 다시 동기화는 이 시간당 게이트를 지키며 강제
bypass 하지 않는다. 연결 직후 Preview는 방금 쓴 Admin 캐시를 한 번만 읽고
API를 다시 치지 않는다. 게이트 허용/예약은 계정별 프로세스 간 락으로
원자적이며, 다른 계정은 서로 기다리지 않는다. `pageSize`는 1000이며,
페이지 상한에서도 `hasNextPage`가 남으면 불완전 응답으로 거절하고
last-known-good 캐시를 유지한다. 전체 연결 해제는 해당 계정 바인딩과
Admin 캐시/게이트를 지우고, Admin 계정이 하나도 없으면 자격 증명 키도
삭제한다. CSV만 끊을 때는 키를 건드리지 않는다.

소스 우선순위는 섞지 않는다.

```text
Cursor Admin API          (optional Enterprise key)
        ↓ missing / disconnected
GUI-imported CSV          (config-backed AppData copy)
        ↓ missing
token-stats / env export  (advanced QUOTADECK_CURSOR_* paths)
        ↓ missing
unsupported (hidden on cumulative LCD)
```

고급 환경 변수 절차는 그대로 동작한다.

```powershell
setx QUOTADECK_CURSOR_EXPORT "C:\path\to\usage.<account_id>.csv"
```

일반 이름 `usage.csv`를 유지하려면 한 계정에만 귀속되도록 두 값을 함께 설정한다.

```powershell
setx QUOTADECK_CURSOR_EXPORT "C:\path\to\usage.csv"
setx QUOTADECK_CURSOR_USAGE_CSV_ACCOUNT "<account_id>"
```

`setx` 이후 실행 중인 QuotaDeck을 완전히 종료하고 다시 시작해야 새 환경 변수를
읽는다. CSV에는 최소한 날짜 또는 timestamp, 모델, 서로 겹치지 않는 토큰 범주가
필요하다. 계정 열이나 위의 명시적 파일명/바인딩이 없으면 다른 Cursor 로그인에
잘못 붙는 것을 막기 위해 거부한다.
