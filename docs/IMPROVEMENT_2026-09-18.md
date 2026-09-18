# 2026-09-18 개선 결과

## 변경 내용

- Cursor Admin 정규화가 설정된 최대 레코드 수를 넘는 추가 유효 이벤트를
  감지하고 `CollectorAttempt`, `UsageCoverage`, 캐시에 partial 상태와 사유를
  전달한다. 중복·다른 사용자·비토큰 행은 상한 초과로 세지 않는다.
- Admin 캐시를 v2로 올려 completeness 메타데이터를 저장하고, v1 또는 메타데이터가
  없는 캐시는 새로고침 전까지 보수적으로 partial로 표시한다.
- HTTP 200 빈 응답은 연결 검증 성공으로 유지하되 데이터 갱신 성공 시각은
  갱신하지 않는다. 이전 데이터는 stale 상태와 사유를 게이트 파일에 남기며,
  같은 서비스와 새 서비스의 재조회에서도 유지한다. 새 데이터가 캐시에
  저장된 뒤에만 stale을 해제한다. 캐시 저장 실패는 성공으로 기록하지 않는다.
- `usage-engine --json`이 환경 변수 수집기 위에 `AppConfig.cursor_bindings`를
  반영하도록 바꾸고, 저장된 Cursor CSV 연결을 검증하는 CLI 회귀 테스트를
  추가했다.
- `docs/COLLECTORS.md`와 `docs/CUMULATIVE_USAGE.md`에 부분 수집, 캐시 상태,
  빈 응답, CLI 설정 동작을 기록했다.

## 독립 리뷰 후속 반영

- CLI normalized usage의 completeness(`confidence`)와 freshness(`stale issue`)를
  분리해 JSON·텍스트에 Admin stale 사유를 노출했다.
- HTTP 200 후속 페이지의 `usageEvents`, `pagination`, `hasNextPage` 형식을
  검증하고, 비정상 응답이 last-known-good 캐시를 덮어쓰지 않게 했다.
- Admin 게이트 기록 실패도 API를 호출하지 않고 기존 캐시와 구조화된 사유를
  반환하도록 했다.
- boolean이 아닌 v2 completeness 메타데이터와 잘못된 캐시 관측 행을 partial로
  표시했다.
- 한·영 README 및 변경 노트의 2026-09-18 changelog를 갱신했다.

## 검증 결과

- 기존 개선 회귀군: `38 passed`.
- 독립 리뷰 후속 회귀군: `33 passed`.
- 관련 회귀군: `157 passed`, 기존 Pillow `Image.getdata` deprecation warning
  416건.
- 전체 테스트: `534 passed, 1 skipped, 8 subtests passed`.
  기준선 524 passed에서 이번 개선과 리뷰 후속 회귀 10건이 추가됐다.
- 전체 테스트에서 Pillow `Image.getdata` deprecation warning 8,316건이
  발생했으며, 변경 파일의 `git diff --check`와 `python -m compileall -q src tests`는
  통과했다.

## 남은 미검증 범위

- 실제 Cursor Team Admin API, 실제 자격 증명 관리자, 계정별 실시간 데이터는
  모의 응답과 격리된 캐시로 검증했다.
- 실제 GUI 프로세스와 F108 Pro 키보드 업로드·장치 동작은 이번 자동 검증에
  포함하지 않았다.
