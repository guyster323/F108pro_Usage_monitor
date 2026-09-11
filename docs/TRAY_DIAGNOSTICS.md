# 시스템 트레이 종료 진단

QuotaDeck GUI에는 자동 종료 timer가 없다. 창의 X 버튼은 tray가 실제로 사용 가능한
경우에만 창을 숨기며, 정상 프로세스 종료는 tray의 **종료** 메뉴에서 시작된다.

## 로그 가져오기

1. tray가 보이면 우클릭 후 **진단 로그 폴더 열기**를 선택한다.
2. tray가 사라졌다면 `Win+R`에서 `%APPDATA%\QuotaDeck\logs`를 연다.
3. 폴더가 없으면 `%TEMP%\QuotaDeck\logs`를 확인한다.
4. 문제가 발생한 시각의 `quotadeck-ui-*.log`와 같은 이름의
   `*-crash.log`를 함께 보존한다.

일반 로그는 세션마다 분리되고 파일당 2 MiB, rollover 3개로 제한된다. 14일이 지난
파일은 자동 정리되며 전체 진단 파일도 최근 80개까지만 유지한다. `*-crash.log`는
네이티브 fault 수집을 위해 매 실행마다 만들어지므로 header만 있는 것은 crash를
뜻하지 않는다.

## 빠른 판독표

| 마지막 주요 event | 의미 |
|---|---|
| `process_end clean=true reason='tray_quit'` | 사용자가 tray 메뉴로 정상 종료 |
| `previous_ui_session_unclean` | 직전 GUI 로그에 정상 종료 marker가 없음 |
| `worker_exception kind=preview` | Provider 조회 또는 렌더 작업 예외; 바로 뒤 traceback 확인 |
| `worker_exception kind=upload` | HID 탐색·렌더·키보드 전송 예외 |
| `worker_start` 뒤 `worker_finished` 없음 | 해당 background 작업 중 native 종료 또는 강제 종료 |
| `provider_fetch_start` 뒤 완료 없음 | 표시된 provider 조회 중 멈춤/종료 |
| `render_start` 뒤 완료 없음 | sprite/theme 렌더링 중 멈춤/종료 |
| `transport_open_start` 뒤 완료 없음 | 해당 HID backend를 여는 중 실패 또는 native 종료 |
| `hid_feature_start` 뒤 완료 없음 | 표시된 feature command의 장치 응답 대기 중 종료 |
| `hid_page_ack_wait` 뒤 완료 없음 | LCD page ACK 대기 구간에서 멈춤/종료 |
| `win32_io_timeout` | Win32 LCD 읽기/쓰기가 제한 시간 안에 끝나지 않아 안전하게 취소됨 |
| `win32_cancel_unconfirmed` | 500ms 안에 취소 완료가 확인되지 않아 I/O 저장소와 handle을 격리·백그라운드 회수함 |
| `python_unhandled_exception` / `python_unraisable` | 처리되지 않은 Python 예외 |
| `qt_message ... QtFatalMsg` | Qt가 fatal 상태를 보고함 |
| `tray_destroyed quit_requested=false` | 정상 종료 요청 없이 tray 객체가 파괴됨 |
| `tray_heartbeat available=false` | Windows Explorer/system tray가 일시적으로 없음 |
| `tray_heartbeat visible=false` 뒤 `tray_recovery_attempt` | 프로세스는 살아 있고 icon 재등록을 시도함 |
| 마지막 heartbeat 뒤 `process_end` 없음 | 외부 강제 종료, native abort, 전원 종료 가능성 |
| `unexpected_event_loop_return` | tray 종료 요청 없이 Qt event loop가 끝남 |
| `qt_exit_with_active_workers` | 외부 종료 시점에 작업 thread가 아직 남아 있었음 |

`*-crash.log`의 session header 뒤에 Python fatal error와 thread stack이 있다면 일반
Python 예외가 아니라 native/Qt 수준 종료 증거다. Windows 이벤트 뷰어의
`Windows 로그 > 응용 프로그램`에서 같은 시각의 `QuotaDeck.exe` Application Error도
함께 확인한다.

## 이번 보강에서 제거한 종료 위험

- GUI/PySide import 전부터 파일 logging을 시작한다.
- `sys`, Python thread, unraisable exception과 Qt message를 모두 기록한다.
- 60초마다 tray/window/worker 상태를 검사하고 5분마다 heartbeat를 남긴다.
- 결과 signal이 아니라 `QThread.finished`가 GUI thread에서 처리된 뒤에만 다음 작업을 허용한다.
- 작업 중 tray 종료 요청은 thread가 끝날 때까지 보류한다.
- tray context menu를 window가 소유·유지해 garbage collection으로 사라지지 않게 한다.
- Codex app-server stdout은 실제 timeout이 있는 reader queue로 읽는다.
- 한 Provider의 계정 검색 또는 HID 열거 실패가 전체 tray 앱을 종료시키지 않는다.
- 모든 Win32 API의 pointer/HANDLE 반환형을 명시해 64-bit handle 잘림에 따른 native
  process 종료를 막는다.
- LCD `ReadFile`/`WriteFile`은 overlapped I/O로 실행한다. timeout 후 취소 완료가
  500ms 안에 오지 않으면 해당 handle/event/buffer를 격리하고 daemon reaper에 넘겨
  worker는 안전하게 반환한다. pending native I/O가 쓰는 메모리를 먼저 해제하지 않는다.

코드 리뷰에서 확인된 가장 유력한 두 종료 원인은 (1) 결과 signal 직후 아직 실행 중인
`QThread`의 마지막 참조를 교체하던 수명주기 경쟁과 (2) 64-bit Win32 HANDLE을 ctypes
기본 `int` 반환형으로 받던 ABI 오류였다. 둘 다 이번 보강에서 제거했으며, 새 로그는
남은 장치·Qt·provider별 원인을 구분하기 위한 것이다.

Win32 feature `DeviceIoControl`은 HID driver가 제공하는 동기 호출이므로 별도 hard
timeout이 없다. 마지막 기록이 `hid_feature_start`인 채 heartbeat가 계속
`busy=true`라면 이 경로를 의심하고 Windows 실기기에서 재현해야 한다.

## 공유 전 확인

일반 로그는 Authorization, token/password/API key/cookie, 이메일 앞부분과 사용자 홈
경로를 자동 마스킹하며 provider 원문 응답을 기록하지 않는다. 다만 `*-crash.log`의
native stack에는 로컬 파일 경로가 보일 수 있으므로 외부 공유 전 직접 확인한다.
`config.json`, `auth.json`, `state.vscdb`, `.credentials.json`은 진단 자료에 포함하지 않는다.
