# QuotaDeck v0.2.3 변경 노트

작성일: 2026-09-11
수정일: 2026-09-12

## 이번 변경

- Codex/Claude 로컬 로그와 선택적 `token-stats`/`ccusage` 경계를 사용하는 누적 Usage collector를 추가했습니다.
- 기간별·모델별 토큰 집계와 평균 대비 표시를 추가하고, 근거가 없는 Cursor 토큰 추정은 `N/A`로 제한했습니다.
- Display 비용 환산 행의 좌측 시작점에 7x7 코인 픽셀 아이콘을 추가했습니다.
- 누적 Display 미리보기와 collector 문서를 README 및 `docs/`에 연결했습니다.
- 입력 검증, refresh 안전성, 진단/HTTP 보안 경로와 회귀 테스트를 보강했습니다.

## 2026-09-12 정규화 엔진·LCD 연동

- 리뷰에서 `collect_normalized_usage` Codex 계정 합계가 패치 전 `UsageService` analytics 합계와 일치함을 확인했습니다. Codex 로컬 스캐너의 기존 delta 재조정은 유지했고, Codex 이벤트 롤업은 바꾸지 않았습니다.
- Grok `grok usage` 세션 총계는 계정/제공자/전체 합계에서 증분 롤업 대상이 아닙니다. resume/fork 겹침이 있어 비용은 미지(`None`)로 두고, 계정 전체·일별 원장으로 주장하지 않습니다.
- `CumulativeUsageService` → `QuotaDeckRuntime` → renderer → F108 LCD 누적 경로가 정규화 엔진을 소비합니다. Cursor attributable export의 가상 API 정가(`LIST`)가 키보드에 도달하고, Grok 세션 총계는 일별 THIS/AVG로 올라가지 않습니다.
- 커스텀 엔드포인트 가격은 계속 `N/A`입니다. unknown을 0으로 만들지 않으며 자격 증명은 노출하지 않습니다.

## 2026-09-12 Win32 GET_FEATURE 64바이트 ABI

- 연결된 AULA F108 Pro(VID `0C45` PID `800A`)에서 기본 auto/Win32 업로드가 페이지 쓰기 전 `GET_FEATURE short read (64/65 bytes)`로 실패했습니다. 동일 8프레임은 hidapi로 업로드되었습니다.
- 재측정에서 기본 Win32는 count 64여도 raw 접두사가 `00 04 18 00 01 00 00 00`이었습니다. 64바이트가 페이로드만이고 `buffer[0]`부터 데이터라는 가정은 틀렸고, 원래처럼 리포트 ID를 제거해야 ACK가 `byte[3]`에 남습니다.
- Windows `IOCTL_HID_GET_FEATURE`의 65바이트(`00` + 64 페이로드)와 64바이트(`00` + 63 페이로드) 모두 리포트 ID를 제거해 접두사 `04 18 00 01`을 만들고, 64는 부족한 마지막 바이트만 패딩하며, 64 미만 읽기는 거절합니다.
- 수정 후 기본 auto가 선택한 `Win32Transport`로 실기기 재검증을 완료했습니다. 8프레임·520,192바이트의 127개 페이지 ACK와 최종 apply ACK를 확인했으며, 전송·적용에는 약 17.6초가 걸렸습니다. 합성 Cursor export를 사용한 검증이며 실제 계정 청구값 검증은 아닙니다. [상세 기록](docs/WIN32_LCD_VERIFICATION.md)

## Display 이미지

![누적 Usage 비용 환산 행의 코인 아이콘](docs/cumulative-coin-preview.png)

실행 시 사용되는 원본 아이콘은 `src/quotadeck/bundled/icons/coin-pixel.png`이며, 240x135 LCD 미리보기는 `docs/cumulative-coin-preview.png`입니다.

## 검증

- pytest: **428 passed, 1 skipped** (2026-09-12 Win32 GET_FEATURE 64/65 리포트 ID 정규화 후)
- Win32 transport unittest: **19 passed** (관측 count 64 접두사 `00 04 18 00 01`, ACK `byte[3]`, 짧은 읽기 거절 포함)
- `compileall`, 문서/예산/스프라이트/릴리스 검사 통과
- 2026-09-12 재검증: **428 passed, 1 skipped, 8 subtests passed**. 건너뛴 1건은 Windows 디렉터리 심볼릭 링크 생성 권한이 필요한 테스트입니다.

하드웨어 LCD 전송과 실제 로그인된 외부 CLI 계정의 live Usage 값은 CI에서 검증하지 않습니다.
