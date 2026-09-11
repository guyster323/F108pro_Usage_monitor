# QuotaDeck v0.2.3 변경 노트

작성일: 2026-09-11

## 이번 변경

- Codex/Claude 로컬 로그와 선택적 `token-stats`/`ccusage` 경계를 사용하는 누적 Usage collector를 추가했습니다.
- 기간별·모델별 토큰 집계와 평균 대비 표시를 추가하고, 근거가 없는 Cursor 토큰 추정은 `N/A`로 제한했습니다.
- Display 비용 환산 행의 좌측 시작점에 7x7 코인 픽셀 아이콘을 추가했습니다.
- 누적 Display 미리보기와 collector 문서를 README 및 `docs/`에 연결했습니다.
- 입력 검증, refresh 안전성, 진단/HTTP 보안 경로와 회귀 테스트를 보강했습니다.

## Display 이미지

![누적 Usage 비용 환산 행의 코인 아이콘](docs/cumulative-coin-preview.png)

실행 시 사용되는 원본 아이콘은 `src/quotadeck/bundled/icons/coin-pixel.png`이며, 240x135 LCD 미리보기는 `docs/cumulative-coin-preview.png`입니다.

## 검증

- pytest: **377 passed, 1 skipped**
- Win32 transport unittest: **12 passed**
- `compileall`, 문서/예산/스프라이트/릴리스 검사 통과

하드웨어 LCD 전송과 실제 로그인된 외부 CLI 계정의 live Usage 값은 CI에서 검증하지 않습니다.
