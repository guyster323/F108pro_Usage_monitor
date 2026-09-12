# F108 Pro Win32 LCD 실기기 검증

검증 시각: 2026-09-12 15:21 KST

## 결과

연결된 AULA F108 Pro(VID `0C45`, PID `800A`)에서 기본 `auto`가 선택한
`Win32Transport`로 8프레임·520,192바이트의 전송과 적용을 완료했습니다.
`begin`, `lcd-header`, 127개 페이지 각각의 ACK, 마지막 `apply` ACK를
프로토콜 검증 코드가 모두 확인했습니다. 전송·적용 시간은 약 17.6초입니다.

검증한 수정 커밋은 `cbae0c7`입니다. 이전 `c2a1a09`는 64바이트 응답을
리포트 ID가 없는 페이로드로 해석해 ACK 위치가 밀렸습니다. 후속 수정은
64/65바이트 응답에서 모두 첫 리포트 ID를 제거하고, 64바이트일 때만
없는 마지막 페이로드 바이트를 0으로 채웁니다. 64바이트 미만은 계속 거절합니다.

## 입력과 검증 경로

- 데이터: 이전 리뷰에서 만든 합성 Cursor export. 실제 청구 데이터가 아닙니다.
- 모델·일별 토큰: `grok-4.6`, 입력 1,000 + 출력 500 = 1,500토큰.
- 표시: 일별 누적 모드, `CURSOR EXPORT`, `LIST <$0.01`, 프레임 예산 8.
- 경로: `CumulativeUsageService` → `QuotaDeckRuntime.poll/build_frames` →
  `F108Device()` → `Win32Transport` → 실제 키보드.
- 페이로드 SHA-256: `257fe71edb99180420f576fc792c45e2d949a754b8a7a80ebf577f70f3772ddc`.
- 검증 동안 기존 QuotaDeck 프로세스를 중지해 중복 전송을 방지했고,
  검증 직후 다시 실행했습니다. 계정 선택과 사용자 설정은 변경하지 않았습니다.

전송 성공은 장치 ACK와 최종 적용 명령으로 확인했습니다. 키보드 화면을
카메라로 촬영하거나 육안으로 확인한 결과는 아닙니다. 실제 Cursor 계정의
export 수집이나 청구 금액의 정확성까지 이 검증으로 주장하지 않습니다.

## 자동 검사

- 전체 pytest: 428 passed, 1 skipped, 8 subtests passed (57.23초).
- 건너뜀: Windows 디렉터리 심볼릭 링크 권한이 필요한
  `tests/test_grok_account_discovery.py`의 테스트 1건.
- 기존 Pillow `getdata` 폐기 예정 경고가 발생했으며 테스트 실패는 없었습니다.
- `compileall`, 문서 검사, 릴리스 버전·패키지 자산 검사 및
  `git diff --check` 통과.

실기기 결과 JSON과 실행 스크립트는 로컬 `build/`에 보관합니다.
계정 데이터나 실행 로그 전체는 저장소에 추가하지 않습니다.
