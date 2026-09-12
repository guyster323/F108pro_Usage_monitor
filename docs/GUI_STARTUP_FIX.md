# GUI 시작 시 QComboBox / QPaintDevice.metric 충돌

검증 시각: 2026-09-12 15:35 KST

## 원인

`MainWindow`가 표시 정보 선택용 `QComboBox`를 `self.metric`에 저장했습니다.
`QWidget`/`QMainWindow`는 `QPaintDevice.metric(PaintDeviceMetric)` 가상
메서드를 상속하므로, 이 속성 이름이 네이티브 `metric()` 콜백을 가립니다.

이벤트 루프가 창을 보이거나 그릴 때 Qt가 `QMainWindow.metric()`을 호출하면
파이썬 쪽에서는 콤보박스를 함수처럼 호출하게 됩니다. 증상은 다음과
같습니다.

```text
TypeError: 'PySide6.QtWidgets.QComboBox' object is not callable
```

프로덕션 로그 `quotadeck-ui-20260912T062648.100504Z-pid34616.log`는
`qt_startup` → `event_loop_started` 이후 `origin=main`의
`python_unhandled_exception`으로 이 TypeError만 남겼습니다. 프로세스는
살아 있고 스케줄러는 하드웨어 업로드를 계속했습니다. 숨김 실행 옵션도
사용했으므로, 당시 창이 보이지 않았던 현상을 이 예외만의 결과로 단정하지 않습니다.

## 재현

실제 사용자 설정·실제 HID·실제 provider를 쓰지 않고, 격리된
`APPDATA`와 mock runtime으로 재현했습니다.

1. `MainWindow(live=False)` 생성 직후 `type(window.metric)` 은 `QComboBox`.
2. `window.logicalDpiX()` / `window.widthMM()` 가 다음을 발생시킴:
   `Error calling Python override of QMainWindow::metric():
   'PySide6.QtWidgets.QComboBox' object is not callable`.
3. `show()` + 이벤트 루프에서도 같은 가상 메서드 호출이 실패함.

## 수정

위젯 이름만 `self.metric_combo`로 바꿨습니다. 표시 정보 콤보의 항목,
저장되는 `metric_mode`, 언어 전환, 누적/잔여 리밋 전환 동작은 그대로입니다.
전송·스케줄러·하드웨어 경로는 변경하지 않았습니다.

## 회귀 테스트

`tests/test_ui_smoke.py`의
`test_event_loop_startup_does_not_shadow_paint_device_metric` 가
다음을 확인합니다.

- 격리 설정 + mock provider/HID
- `window.metric` 가 다시 호출 가능한 `QPaintDevice.metric`
- `window.metric_combo` 가 표시 정보 `QComboBox`
- `show()` 뒤 짧은 `QEventLoop` 동안 `sys.excepthook` /
  `sys.unraisablehook` 에 QComboBox TypeError가 없음
- `collect_config()` 가 기존처럼 `metric_mode`와 계정을 수집함

집중 검사: `tests/test_ui_smoke.py` + `tests/test_cumulative_ui_smoke.py`
12 passed.

전체 pytest(`--basetemp` 고유, `-p no:cacheprovider`): 429 passed, 1 skipped,
8 subtests passed. 건너뜀은 Windows 디렉터리 심볼릭 링크 권한이 필요한
`tests/test_grok_account_discovery.py` 1건입니다. Pillow `getdata` 폐기
예정 경고는 기존과 같고 실패는 없습니다.

위 회귀 테스트는 실행 중인 QuotaDeck.exe와 사용자 `config.json`을 사용하지 않았습니다.

## 재빌드 실행 파일 확인

2026-09-12 15:46 KST에 수정 코드를 포함한 실행 파일을 기동했습니다.
새 로그에는 Qt 기동과 이벤트 루프 시작 후 해당 TypeError가 없었고,
실제 계정 조회와 업로드 쿨다운 판단이 정상적으로 끝났습니다.
Windows 접근성 정보로 설정 창, USB-C 키보드 연결, 선택한 3개 계정의
미리보기 준비 상태를 확인했습니다. 잠금 화면에 가려진 스크린샷은
앱 화면의 픽셀 검증 근거로 사용하지 않았습니다.

배포 파일은 `dist/QuotaDeck.exe`이며, 런타임 소스는 `65c529d`와 같습니다.
PyInstaller 빌드와 실행 파일의 `--version` 확인을 마쳤습니다.
실행 파일 SHA-256:
`14ed24bf94a94d27f001bfeee35d0dd4533d270eb262738dc2f8f05be1268398`.

