# QuotaDeck v0.2.3 개선 계획 (Sol)

## 목표

v0.2.2의 누적 사용량·비용·Flash 안전 계약을 유지하면서, 외부 오픈소스 collector를 안전하게 활용할 수 있는 수집 계층과 비용 표시용 코인 픽셀 아이콘을 추가한다.

## 구현 범위

1. `token-stats`를 우선 collector로 사용할 수 있는 선택적 adapter를 추가한다.
   - 실행 파일을 발견할 수 없거나 호출/파싱에 실패하면 기존 native parser로 복귀한다.
   - shell을 사용하지 않고 timeout, stdout 크기, 레코드 수 한도를 적용한다.
   - prompt/response 본문은 정규화하거나 저장하지 않는다.
2. `ccusage`는 선택적 검증/fallback adapter 경계로 둔다.
   - 결과가 불일치하거나 기간·계정 귀속이 불명확하면 임의 합산하지 않고 `PARTIAL` 또는 `N/A`로 처리한다.
3. 외부 collector 결과를 기존 `UsageObservation`/기간 분석 계층으로 정규화한다.
   - provider/account/model/timestamp와 상호 배타적 token category를 보존한다.
   - reasoning은 output에 다시 더하지 않는다.
   - 누적 counter와 delta를 혼동하지 않는다.
4. Cursor는 신뢰 가능한 server Usage export/local cache가 collector 결과로 제공될 때만 누적 사용량을 활성화한다.
   - `state.vscdb`나 로그인 정보만으로 token을 추정하지 않는다.
   - 계정/기간 귀속 또는 timestamp가 부족하면 0으로 꾸미지 않는다.
5. 기존 Codex/Claude/Grok native parser는 안전한 fallback과 특수 계정 매핑 경로로 유지한다.
6. 생성된 `artwork/coin-pixel-source.png`를 기반으로 작은 투명 runtime coin asset을 만든다.
   - 누적 LCD 비용 행의 좌측 시작에 배치한다.
   - KRW/USD 공통 아이콘이며 문자·통화기호가 없다.
   - THIS/AVG 두 열, ASCII-only 문자열, 240x135 경계를 침범하지 않는다.
7. 설정/문서/버전을 필요한 최소 범위로 갱신한다.

## 수용 기준

- 기존 전체 pytest 통과.
- 외부 collector 부재/timeout/비정상 JSON/과대 출력/불완전 스키마가 앱을 중단시키지 않고 안전하게 fallback 또는 `PARTIAL`/`N/A` 처리된다.
- token-stats fixture가 Codex/Claude/Cursor/Grok의 모델 및 token category를 정확히 정규화한다.
- reasoning token 이중 합산이 없다.
- Cursor의 신뢰 가능한 export가 없으면 기존처럼 `N/A`다.
- 비용 all-or-nothing gate와 Daily/Monthly 비교 계약을 유지한다.
- 비용 행 좌측에 코인 픽셀 asset이 렌더되고 숫자·분할선·패널 경계를 침범하지 않는다.
- LCD에 한글, `KRW`, `USD`, 모델명이 새로 노출되지 않는다.
- 104개 runtime sprite와 bundled mirror를 수정하지 않는다.
- Flash 안전 계산과 상태 계약을 수정하지 않는다.
- README/관련 docs에 collector 우선순위, 신뢰 경계, 설치가 선택 사항임을 기록한다.
- 최종 소스 ZIP과 SHA-256을 생성한다.

## 검증

```powershell
python -m compileall -q src tests tools
python -m pytest -q
python tools/check_sprite_hashes.py
python tools/check_sprite_runtime.py
python tools/check_golden.py
python tools/check_compressed.py
python tools/check_budget.py
python tools/check_timing.py
python tools/check_docs.py
python tools/check_release.py
```

실제 F108 Pro 하드웨어 검증은 별도 수동 QA로 남긴다.
