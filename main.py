"""macOS 메뉴바 앱: 단축키 → 커서 주변 캡처 → Gemini 분석 → 메뉴바 타이틀 갱신.

실행: python main.py  (또는 build_app.sh 로 만든 ScreenAnswer.app 더블 클릭)
단축키 (수정키를 함께 눌렀다 떼면 실행):
  * ⌃ Control + ⌥ Option + ⌘ Command      → 커서 주변 1회 분석
  * ⌃ Control + ⌥ Option + ⌘ Command + A  → 5초마다 자동 분석 켜기/끄기
    (메뉴의 '5초 자동 분석'으로도 켜고 끌 수 있다)

메뉴바 표시: '·' 대기 → '..' 분석 중 → '3' 결과 (1회 분석은 5초 후 '·' 복귀,
자동 분석 중에는 결과가 계속 갱신되며 시작 직후에는 '↻' 표시)

Dock 아이콘이 보이는 일반 앱으로 동작하므로(LSUIElement=False) 시스템 설정의
'손쉬운 사용'/'화면 기록' 목록에서 앱을 쉽게 찾아 권한을 줄 수 있다.
"""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import rumps
from dotenv import load_dotenv

import config
from analyzer import Analyzer, AnalyzerError
from capture import (
    CaptureError,
    capture_around_cursor,
    has_screen_capture_access,
    request_screen_capture_access,
)
from hotkey import (
    ACTION_SINGLE,
    ACTION_TOGGLE_AUTO,
    HotkeyListener,
    get_cursor_position,
    has_input_monitoring_access,
    request_input_monitoring_access,
)

APP_NAME = "ScreenAnswer"
IDLE_TITLE = "·"
BUSY_TITLE = ".."
AUTO_IDLE_TITLE = "↻"
RESULT_DISPLAY_SECONDS = 5.0
AUTO_INTERVAL_SECONDS = 5.0
AUTO_RATE_LIMIT_BACKOFF_SECONDS = 30.0
HOTKEY_WATCHDOG_SECONDS = 3.0
AUTO_ON_LABEL = "5초 자동 분석 중지  (⌃⌥⌘A)"
AUTO_OFF_LABEL = "5초 자동 분석 시작  (⌃⌥⌘A)"
MAX_TITLE_CHARS = 24
MAX_MENU_CHARS = 90
UI_POLL_INTERVAL = 0.1

# py2app 번들 안에서 실행 중이면 sys.frozen == "macosx_app"
IS_APP_BUNDLE = getattr(sys, "frozen", None) == "macosx_app"
LOG_FILE = Path.home() / "Library" / "Logs" / f"{APP_NAME}.log"

DEFAULT_BUNDLE_ID = "com.rubric.screenanswer"
# 권한 초기화 후 재실행했음을 알리는 실행 인자 (같은 안내가 반복되지 않도록)
AFTER_RESET_ARG = "--after-permission-reset"
TCC_SERVICES = ("ScreenCapture", "ListenEvent", "Accessibility")

PRIVACY_PANES = {
    "화면 기록": "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
    "손쉬운 사용": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    "입력 모니터링": "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent",
}


def _setup_logging() -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if IS_APP_BUNDLE:
        # .app 으로 실행하면 터미널 출력이 보이지 않으므로 파일에도 남긴다.
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(LOG_FILE, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )


_setup_logging()
logger = logging.getLogger("screen-answer")

# 개발용 대체 경로: 소스 폴더의 .env (Keychain/config.json에 키가 없을 때만 사용됨)
load_dotenv(Path(__file__).resolve().with_name(".env"))


def _truncate(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _copy_to_clipboard(text: str) -> None:
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=False)


def _command_output(args: list[str]) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=5)
        return (result.stdout + result.stderr).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return f"({type(exc).__name__})"


def _bring_app_to_front() -> None:
    """모달 창이 다른 앱 뒤에 숨지 않도록 앱을 앞으로 가져온다."""
    try:
        from AppKit import NSApplication

        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    except Exception:
        logger.debug("앱 활성화 실패", exc_info=True)


class ScreenAnswerApp(rumps.App):
    def __init__(self) -> None:
        super().__init__(APP_NAME, title=IDLE_TITLE, quit_button=None)

        self._result_text = ""
        self._summary_text = ""

        self.result_item = rumps.MenuItem("결과: (없음)", callback=self.on_copy_result)
        self.summary_item = rumps.MenuItem("요약: (없음)", callback=self.on_copy_summary)
        self.auto_item = rumps.MenuItem(AUTO_OFF_LABEL, callback=self.on_toggle_auto)
        self.key_status_item = rumps.MenuItem("API Key: (미설정)")
        self.hotkey_status_item = rumps.MenuItem(
            "단축키: 확인 중…", callback=self.on_hotkey_status
        )
        self.permissions_menu = rumps.MenuItem("권한 설정 열기")
        for label in PRIVACY_PANES:
            self.permissions_menu.add(rumps.MenuItem(label, callback=self.on_open_privacy))
        self.permissions_menu.add(None)
        self.permissions_menu.add(
            rumps.MenuItem("권한 초기화 후 재시작…", callback=self.on_reset_permissions)
        )
        self.permissions_menu.add(rumps.MenuItem("진단 정보 복사", callback=self.on_copy_diagnostics))

        self.menu = [
            self.result_item,
            self.summary_item,
            self.auto_item,
            None,
            rumps.MenuItem("API Key 설정…", callback=self.on_set_api_key),
            self.key_status_item,
            self.permissions_menu,
            None,
            rumps.MenuItem("1회 분석: ⌃⌥⌘ 눌렀다 떼기  ·  자동 분석 켜기/끄기: ⌃⌥⌘A"),
            self.hotkey_status_item,
            rumps.MenuItem("초기화(Clear)", callback=self.on_clear),
            rumps.MenuItem("종료(Quit)", callback=self.on_quit),
        ]

        # 백그라운드 스레드는 UI를 직접 건드리지 않고 큐에만 넣는다.
        # 메인 스레드의 rumps.Timer가 큐를 비우며 AppKit UI를 갱신한다.
        self._ui_events: queue.Queue[tuple[str, object]] = queue.Queue()
        # 결과/오류 표시 후 대기 상태('·')로 되돌릴 시각 (time.monotonic 기준)
        self._revert_at: float | None = None
        self._work_lock = threading.Lock()
        self._generation = 0
        self._generation_lock = threading.Lock()
        self._prompt_open = False
        # 자동 분석 스레드 중지 신호. 켜져 있는 동안에는 set 되지 않은 Event.
        self._auto_stop: threading.Event | None = None

        self._analyzer: Analyzer | None = None
        stored = config.load_api_key()
        if stored:
            self._apply_api_key(stored, verify=False)
        else:
            self._set_missing_key_state()
        # 메인 런루프가 돌기 시작한 직후에 키 입력 창/권한 안내를 띄운다.
        self._startup_timer = rumps.Timer(self._on_startup, 0.8)
        self._startup_timer.start()

        self._ui_timer = rumps.Timer(self._drain_ui_events, UI_POLL_INTERVAL)
        self._ui_timer.start()

        self._hotkey = HotkeyListener(self._on_hotkey)
        # 권한이 없으면 시스템 요청 창을 띄우고 설정 목록에 앱을 등록한다.
        if not has_input_monitoring_access():
            request_input_monitoring_access()
        if not has_screen_capture_access():
            request_screen_capture_access()
        logger.info("진단 정보:\n%s", self._diagnostics())
        self._hotkey.start()
        self._update_hotkey_status()
        # 권한이 나중에 허용되거나 macOS가 탭을 꺼 버린 경우를 주기적으로 복구한다.
        self._hotkey_timer = rumps.Timer(self._hotkey_watchdog, HOTKEY_WATCHDOG_SECONDS)
        self._hotkey_timer.start()
        logger.info("준비 완료. ⌃⌥⌘: 1회 분석, ⌃⌥⌘A: 5초 자동 분석 토글")

    # ---------- 단축키 상태 ----------

    def _hotkey_watchdog(self, _timer: rumps.Timer) -> None:
        if self._hotkey.start():
            self._hotkey.ensure_enabled()
        self._update_hotkey_status()

    def _update_hotkey_status(self) -> None:
        if self._hotkey.active:
            title = "단축키 상태: 정상"
        elif not has_input_monitoring_access():
            title = "단축키 상태: ⚠ 입력 모니터링 권한 필요 (클릭)"
        else:
            title = "단축키 상태: ⚠ 비활성 — 앱을 재시작하세요 (클릭)"
        if self.hotkey_status_item.title != title:
            self.hotkey_status_item.title = title

    # ---------- API 키 ----------

    def _on_startup(self, timer: rumps.Timer) -> None:
        timer.stop()
        if self._analyzer is None:
            self._prompt_api_key(first_run=True)
        self._check_permissions_on_startup()

    # ---------- macOS 권한 (TCC) ----------

    def _missing_permissions(self) -> list[str]:
        missing = []
        if not has_screen_capture_access():
            missing.append("화면 기록")
        if not has_input_monitoring_access():
            missing.append("입력 모니터링")
        return missing

    def _check_permissions_on_startup(self) -> None:
        missing = self._missing_permissions()
        if not missing:
            return
        _bring_app_to_front()
        if AFTER_RESET_ARG in sys.argv:
            # 초기화 직후: 이제 새로 허용만 하면 된다.
            clicked = rumps.alert(
                title="권한을 새로 허용해 주세요",
                message=(
                    f"필요한 권한: {', '.join(missing)}\n\n"
                    "시스템 설정에서 ScreenAnswer를 켠 뒤, 메뉴의 종료(Quit)로 끄고 "
                    "앱을 다시 실행하세요. (macOS가 '종료 후 다시 열기'를 물으면 눌러도 됩니다)"
                ),
                ok="설정 열기",
                cancel="나중에",
            )
            if clicked == 1:
                self._open_privacy_pane(missing[0])
            return

        clicked = rumps.alert(
            title="macOS 권한이 적용되지 않았습니다",
            message=(
                f"macOS가 이 앱에 다음 권한을 허용하지 않고 있습니다: {', '.join(missing)}\n\n"
                "• 처음 설치했다면: '설정 열기'에서 ScreenAnswer를 켠 뒤 앱을 다시 실행하세요.\n"
                "• 설정에서 이미 켜져 있는데도 이 창이 뜬다면: 이전 버전 앱 기준으로 등록된 "
                "권한이라 새 버전에 적용되지 않는 상태입니다. '권한 초기화 후 재시작'을 누른 뒤 "
                "다시 허용해 주세요. (앱을 업데이트할 때마다 한 번씩 필요합니다)"
            ),
            ok="권한 초기화 후 재시작",
            cancel="나중에",
            other="설정 열기",
        )
        if clicked == 1:
            self._reset_permissions_and_relaunch()
        elif clicked == -1:
            self._open_privacy_pane(missing[0])

    def _open_privacy_pane(self, label: str) -> None:
        subprocess.run(["open", PRIVACY_PANES[label]], check=False)

    @staticmethod
    def _bundle_info() -> tuple[str, str | None]:
        """(번들 ID, .app 경로) — 소스 실행 시 경로는 None."""
        try:
            from AppKit import NSBundle

            bundle = NSBundle.mainBundle()
            bundle_id = str(bundle.bundleIdentifier() or DEFAULT_BUNDLE_ID)
            path = str(bundle.bundlePath()) if IS_APP_BUNDLE else None
            return bundle_id, path
        except Exception:
            return DEFAULT_BUNDLE_ID, None

    def _reset_permissions_and_relaunch(self) -> None:
        bundle_id, app_path = self._bundle_info()
        if app_path is None:
            rumps.alert(
                title="소스 실행 중",
                message="터미널에서 실행 중일 때는 권한이 터미널 앱에 적용됩니다. "
                "터미널(또는 VS Code)에 권한을 허용하세요.",
            )
            return

        self._set_auto(False)
        for service in TCC_SERVICES:
            # 이 앱(번들 ID)의 항목만 지운다. 다른 앱 권한에는 영향 없음, sudo 불필요.
            result = subprocess.run(
                ["tccutil", "reset", service, bundle_id], capture_output=True, text=True
            )
            logger.info(
                "tccutil reset %s %s → %s %s",
                service,
                bundle_id,
                result.returncode,
                (result.stdout + result.stderr).strip(),
            )

        # 앱이 완전히 종료된 뒤 새 인스턴스로 다시 연다(TCC가 새 프로세스를 기준으로 판단).
        subprocess.Popen(
            ["/bin/sh", "-c", f'sleep 1.5; open -n "$0" --args {AFTER_RESET_ARG}', app_path],
            start_new_session=True,
        )
        self.on_quit(None)

    def _diagnostics(self) -> str:
        bundle_id, app_path = self._bundle_info()
        lines = [
            f"앱: {APP_NAME} (frozen={IS_APP_BUNDLE})",
            f"번들 ID: {bundle_id}",
            f"경로: {app_path or sys.executable}",
            f"macOS: {_command_output(['sw_vers', '-productVersion'])}",
            f"화면 기록 권한: {has_screen_capture_access()}",
            f"입력 모니터링 권한: {has_input_monitoring_access()}",
        ]
        if app_path:
            designated = [
                line
                for line in _command_output(["codesign", "-d", "-r-", app_path]).splitlines()
                if "designated" in line
            ]
            lines.append(f"서명 요구사항: {designated[0].strip() if designated else '(확인 불가)'}")
        return "\n".join(lines)

    def _prompt_api_key(self, first_run: bool = False) -> None:
        if self._prompt_open:
            return
        self._prompt_open = True
        try:
            message = (
                "Google AI Studio(aistudio.google.com)에서 발급한 Gemini API 키를 붙여넣으세요.\n"
                "(AIza… 또는 AQ.… 형식 모두 가능, 키는 macOS Keychain에 저장됩니다)"
            )
            if first_run:
                message = "처음 실행하셨네요. " + message
            stored = config.load_api_key()
            if stored:
                message += f"\n\n현재 키: {config.mask_key(stored.key)} ({stored.source})"

            while True:
                _bring_app_to_front()
                window = rumps.Window(
                    message=message,
                    title="Gemini API Key 설정",
                    default_text="",
                    ok="저장",
                    cancel="취소",
                    dimensions=(360, 24),
                    secure=True,
                )
                response = window.run()
                if response.clicked != 1 or not response.text.strip():
                    return

                try:
                    saved = config.save_api_key(response.text)
                except ValueError as exc:
                    rumps.alert(title="API 키 형식 오류", message=str(exc), ok="다시 입력")
                    continue
                except OSError as exc:
                    rumps.alert(title="저장 실패", message=f"키를 저장하지 못했습니다: {exc}")
                    return

                logger.info("API 키 저장 완료: %s", saved.source)
                self._apply_api_key(saved, verify=True)
                return
        finally:
            self._prompt_open = False

    def _apply_api_key(self, stored: config.StoredKey, verify: bool) -> None:
        try:
            analyzer = Analyzer(api_key=stored.key)
        except Exception as exc:
            logger.error("분석기 초기화 실패: %s", exc)
            self._analyzer = None
            self.key_status_item.title = f"API Key: 초기화 실패 ({_truncate(str(exc), 40)})"
            self._show_error("키 오류")
            return

        self._analyzer = analyzer
        self.key_status_item.title = f"API Key: {config.mask_key(stored.key)} ({stored.source})"
        logger.info("Gemini 모델: %s, 키 위치: %s", analyzer.model, stored.source)
        if self.title.startswith("⚠"):
            self.title = IDLE_TITLE

        if verify:
            self.key_status_item.title += " · 확인 중"
            threading.Thread(
                target=self._verify_key, args=(analyzer, stored), name="verify-key", daemon=True
            ).start()

    def _verify_key(self, analyzer: Analyzer, stored: config.StoredKey) -> None:
        try:
            analyzer.verify()
            self._ui_events.put(("key_ok", (analyzer, stored)))
        except AnalyzerError as exc:
            logger.error("API 키 확인 실패: %s (원인: %r)", exc, exc.__cause__)
            self._ui_events.put(("key_bad", (analyzer, stored, str(exc))))
        except Exception as exc:  # 네트워크 오류 등
            self._ui_events.put(("key_bad", (analyzer, stored, type(exc).__name__)))

    def _alert_key_problem(self, stored: config.StoredKey, message: str) -> None:
        """키 확인 실패 시 원인과 해결 방법을 알려 준다."""
        hints = {
            "API 키 오류": "키가 잘못되었거나 삭제되었습니다. AI Studio에서 키를 다시 복사해 주세요.",
            "키 유형 거부(AQ)": (
                "Google 서버가 이 키를 API 키가 아닌 인증 토큰으로 처리해 거부했습니다.\n"
                "AI Studio의 'Get API key'에서 새 키를 다시 만들거나, Google Cloud 콘솔 → "
                "API 및 서비스 → 사용자 인증 정보에서 'Generative Language API'로 제한한 "
                "API 키(AIza…)를 만들어 입력해 보세요."
            ),
            "키에 Gemini 미허용": (
                "이 키는 Gemini API(Generative Language API) 사용이 제한되어 있습니다. "
                "Google Cloud 콘솔에서 키의 API 제한에 Generative Language API를 추가하세요."
            ),
            "Gemini API 비활성": "해당 프로젝트에서 Generative Language API를 사용 설정하세요.",
        }
        _bring_app_to_front()
        rumps.alert(
            title=f"API 키 확인 실패: {message}",
            message=(
                f"키: {config.mask_key(stored.key)} ({len(stored.key)}자, {stored.source})\n\n"
                + hints.get(message, "네트워크 상태를 확인하고 'API Key 설정…'에서 다시 시도하세요.")
                + f"\n\n자세한 로그: {LOG_FILE}"
            ),
            ok="확인",
        )

    def _set_missing_key_state(self) -> None:
        self._analyzer = None
        self.key_status_item.title = "API Key: (미설정)"
        self.title = "⚠ 키 없음"
        self._revert_at = None

    # ---------- 단축키 → 작업 스레드 ----------

    def _on_hotkey(self, action: str, x: int, y: int) -> None:
        """메인 스레드(이벤트 탭 콜백)에서 호출된다. 무거운 작업은 스레드로 넘긴다."""
        logger.info("단축키 감지: %s (커서 %d, %d)", action, x, y)
        if action == ACTION_TOGGLE_AUTO:
            self._set_auto(not self.auto_running)
        elif action == ACTION_SINGLE:
            self._start_single(x, y)

    def _next_generation(self) -> int:
        with self._generation_lock:
            self._generation += 1
            return self._generation

    def _start_single(self, x: int, y: int) -> None:
        analyzer = self._analyzer
        if analyzer is None:
            self._prompt_api_key()
            return
        if not self._work_lock.acquire(blocking=False):
            logger.info("이전 분석이 진행 중이라 이번 요청은 무시합니다.")
            return
        generation = self._next_generation()

        def run() -> None:
            try:
                self._analyze_once(analyzer, x, y, generation, auto=False)
            finally:
                self._work_lock.release()

        threading.Thread(target=run, name="analyze", daemon=True).start()

    # ---------- 5초 자동 분석 ----------

    @property
    def auto_running(self) -> bool:
        return self._auto_stop is not None and not self._auto_stop.is_set()

    def _set_auto(self, enabled: bool) -> None:
        """메인 스레드에서만 호출한다."""
        if enabled == self.auto_running:
            return
        if enabled:
            if self._analyzer is None:
                self._prompt_api_key()
                return
            stop = threading.Event()
            self._auto_stop = stop
            threading.Thread(
                target=self._auto_loop, args=(stop,), name="auto-analyze", daemon=True
            ).start()
            self.auto_item.title = AUTO_ON_LABEL
            self.auto_item.state = 1
            self._revert_at = None
            self.title = AUTO_IDLE_TITLE
            logger.info("자동 분석 시작 (%.0f초 간격)", AUTO_INTERVAL_SECONDS)
        else:
            if self._auto_stop is not None:
                self._auto_stop.set()
            self._auto_stop = None
            # 진행 중이던 분석 결과가 나중에 도착해도 표시하지 않는다.
            self._next_generation()
            self.auto_item.title = AUTO_OFF_LABEL
            self.auto_item.state = 0
            self._revert_at = None
            self.title = IDLE_TITLE if self._analyzer is not None else "⚠ 키 없음"
            logger.info("자동 분석 중지")

    def _auto_loop(self, stop: threading.Event) -> None:
        """AUTO_INTERVAL_SECONDS 마다 현재 커서 주변을 캡처해 분석한다(작업 스레드)."""
        while not stop.is_set():
            started = time.monotonic()
            analyzer = self._analyzer
            if analyzer is None:
                self._ui_events.put(("auto_off", None))
                return

            # 1회 분석이 진행 중이면 끝날 때까지 기다린 뒤 실행한다.
            with self._work_lock:
                if stop.is_set():
                    return
                x, y = get_cursor_position()
                error = self._analyze_once(analyzer, x, y, self._next_generation(), auto=True)

            wait = AUTO_INTERVAL_SECONDS - (time.monotonic() - started)
            if error and "429" in error:
                logger.warning("사용량 한도 초과: %.0f초 쉬고 재시도", AUTO_RATE_LIMIT_BACKOFF_SECONDS)
                wait = AUTO_RATE_LIMIT_BACKOFF_SECONDS
            stop.wait(max(0.5, wait))

    def _analyze_once(
        self, analyzer: Analyzer, x: int, y: int, generation: int, auto: bool
    ) -> str | None:
        """캡처 → 분석 → UI 이벤트 전송. 실패 시 오류 메시지를 반환한다."""
        try:
            self._ui_events.put(("busy", (generation, auto)))

            capture = capture_around_cursor(x, y)
            logger.info(
                "캡처 완료(%s): 영역=%s, 배율=%.2f, 커서=%s, %d bytes",
                capture.backend,
                capture.region,
                capture.scale_factor,
                capture.cursor_in_image,
                len(capture.png_bytes),
            )

            analysis = analyzer.analyze(capture.png_bytes)
            logger.info(
                "분석 결과(%s): %s | %s", analysis.answer_type, analysis.result, analysis.summary
            )
            self._ui_events.put(
                ("result", (generation, analysis.result, analysis.summary, auto))
            )
            return None
        except (AnalyzerError, CaptureError) as exc:
            logger.error("분석 실패: %s", exc)
            message = str(exc)
        except Exception as exc:
            logger.exception("파이프라인 오류")
            message = type(exc).__name__
        self._ui_events.put(("error", (generation, message, auto)))
        return message

    # ---------- 메인 스레드 UI 갱신 ----------

    def _is_current(self, generation: int) -> bool:
        with self._generation_lock:
            return generation == self._generation

    def _drain_ui_events(self, _timer: rumps.Timer) -> None:
        # 5초 자동 복귀도 같은 메인 스레드 타이머에서 처리해 UI 접근을 한 곳으로 모은다.
        if self._revert_at is not None and time.monotonic() >= self._revert_at:
            self._revert_at = None
            self.title = AUTO_IDLE_TITLE if self.auto_running else IDLE_TITLE

        while True:
            try:
                kind, payload = self._ui_events.get_nowait()
            except queue.Empty:
                return

            if kind == "busy":
                generation, auto = payload  # type: ignore[misc]
                # 자동 분석 중에는 직전 결과를 그대로 두어 깜빡임을 막는다(첫 회만 '..').
                if self._is_current(generation) and (
                    not auto or self.title == AUTO_IDLE_TITLE
                ):
                    self._revert_at = None
                    self.title = BUSY_TITLE
            elif kind == "result":
                generation, result, summary, auto = payload  # type: ignore[misc]
                if self._is_current(generation):
                    self._show_result(result, summary, auto=auto)
            elif kind == "auto_off":
                self._set_auto(False)
                self._prompt_api_key()
            elif kind == "error":
                generation, message, auto = payload  # type: ignore[misc]
                if self._is_current(generation):
                    self._show_error(message, auto=auto)
            elif kind == "need_key":
                self._prompt_api_key()
            elif kind == "key_ok":
                analyzer, stored = payload  # type: ignore[misc]
                if analyzer is self._analyzer:
                    self.key_status_item.title = (
                        f"API Key: {config.mask_key(stored.key)} ({stored.source}) · 확인됨"
                    )
            elif kind == "key_bad":
                analyzer, stored, message = payload  # type: ignore[misc]
                if analyzer is self._analyzer:
                    self.key_status_item.title = (
                        f"API Key: {config.mask_key(stored.key)} ({stored.source}) · {message}"
                    )
                    self._show_error(message)
                    self._alert_key_problem(stored, message)

    def _show_result(self, result: str, summary: str, auto: bool = False) -> None:
        self._result_text = result
        self._summary_text = summary
        self.title = _truncate(result, MAX_TITLE_CHARS)
        self.result_item.title = f"결과: {_truncate(result, MAX_MENU_CHARS)}"
        self.summary_item.title = f"요약: {_truncate(summary or '(없음)', MAX_MENU_CHARS)}"
        # 자동 분석 중에는 다음 결과가 곧 덮어쓰므로 '·' 로 되돌리지 않는다.
        if auto:
            self._revert_at = None
        else:
            self._schedule_revert()

    def _show_error(self, message: str, auto: bool = False) -> None:
        self.title = f"⚠ {_truncate(message, MAX_TITLE_CHARS - 2)}"
        self.summary_item.title = f"오류: {_truncate(message, MAX_MENU_CHARS)}"
        if auto:
            self._revert_at = None
        else:
            self._schedule_revert()

    def _schedule_revert(self) -> None:
        """타이틀만 대기 상태로 되돌린다. 드롭다운의 결과/요약은 남겨 다시 확인할 수 있다."""
        self._revert_at = time.monotonic() + RESULT_DISPLAY_SECONDS

    # ---------- 메뉴 콜백 ----------

    def on_toggle_auto(self, _sender: rumps.MenuItem) -> None:
        self._set_auto(not self.auto_running)

    def on_hotkey_status(self, _sender: rumps.MenuItem) -> None:
        if self._hotkey.active:
            return
        request_input_monitoring_access()
        self._check_permissions_on_startup()

    def on_reset_permissions(self, _sender: rumps.MenuItem) -> None:
        _bring_app_to_front()
        clicked = rumps.alert(
            title="권한 초기화 후 재시작",
            message=(
                "ScreenAnswer의 화면 기록·입력 모니터링·손쉬운 사용 권한 항목을 지우고 앱을 "
                "다시 시작합니다. 다시 시작되면 권한을 새로 허용해 주세요.\n"
                "(다른 앱의 권한에는 영향이 없습니다)"
            ),
            ok="초기화 후 재시작",
            cancel="취소",
        )
        if clicked == 1:
            self._reset_permissions_and_relaunch()

    def on_copy_diagnostics(self, _sender: rumps.MenuItem) -> None:
        info = self._diagnostics()
        _copy_to_clipboard(info)
        rumps.alert(title="진단 정보가 복사되었습니다", message=info)

    def on_set_api_key(self, _sender: rumps.MenuItem) -> None:
        self._prompt_api_key()

    def on_open_privacy(self, sender: rumps.MenuItem) -> None:
        self._open_privacy_pane(sender.title)

    def on_copy_result(self, _sender: rumps.MenuItem) -> None:
        if self._result_text:
            _copy_to_clipboard(self._result_text)

    def on_copy_summary(self, _sender: rumps.MenuItem) -> None:
        if self._summary_text:
            _copy_to_clipboard(self._summary_text)

    def on_clear(self, _sender: rumps.MenuItem) -> None:
        # 진행 중인 분석 결과가 나중에 도착해도 화면을 덮어쓰지 않도록 세대를 올린다.
        with self._generation_lock:
            self._generation += 1
        self._result_text = ""
        self._summary_text = ""
        self._revert_at = None
        self.result_item.title = "결과: (없음)"
        self.summary_item.title = "요약: (없음)"
        if self._analyzer is None:
            self._set_missing_key_state()
        else:
            self.title = AUTO_IDLE_TITLE if self.auto_running else IDLE_TITLE

    def on_quit(self, _sender: rumps.MenuItem | None) -> None:
        self._set_auto(False)
        self._hotkey_timer.stop()
        self._hotkey.stop()
        self._ui_timer.stop()
        rumps.quit_application()


def _self_test() -> int:
    """빌드된 .app 안에 필요한 모듈이 모두 들어갔는지 확인한다 (CI에서 사용).

    SCREENANSWER_SELFTEST=1 로 실행하면 UI를 띄우지 않고 확인 후 종료한다.
    """
    import AppKit  # noqa: F401  (rumps/pyobjc)
    import mss  # noqa: F401
    from google.genai import types  # noqa: F401
    from PIL import Image  # noqa: F401
    import Quartz  # noqa: F401

    from hotkey import FLAG_ALTERNATE, FLAG_COMMAND, FLAG_CONTROL, FLAG_SHIFT, KEYCODE_A, ChordDetector

    from analyzer import normalize_result
    from capture import screencapturekit_available

    # 가짜 키로 실제 HTTPS 요청을 보내 SSL/네트워크 모듈이 번들에 다 들어갔는지 확인한다.
    try:
        Analyzer(api_key="AIzaSySELFTEST_000000000000000000").verify()
    except AnalyzerError as exc:
        logger.info("가짜 키 확인 요청 → 예상된 거부: %s", exc)
    assert normalize_result("정답: 3번", "choice") == "3"
    detector = ChordDetector()
    chord = FLAG_CONTROL | FLAG_ALTERNATE | FLAG_COMMAND
    assert [detector.on_flags(f) for f in (chord, FLAG_CONTROL, 0)][1] == ACTION_SINGLE
    # ⌃⌥⌘A → 토글 1회, 키 반복은 무시, 이후 수정키를 떼도 1회 분석은 발화하지 않음
    detector.on_flags(chord)
    assert detector.on_key_down(KEYCODE_A, chord) == ACTION_TOGGLE_AUTO
    assert detector.on_key_down(KEYCODE_A, chord, autorepeat=True) is None
    assert [detector.on_flags(f) for f in (FLAG_CONTROL, 0)] == [None, None]
    assert detector.on_key_down(KEYCODE_A, chord | FLAG_SHIFT) is None
    assert normalize_result("B", "choice") == "2" and normalize_result("(d)", "text") == "4"
    listener = HotkeyListener(lambda *_args: None)
    logger.info(
        "커서 위치: %s, 입력 모니터링 권한: %s, 이벤트 탭 생성: %s",
        get_cursor_position(),
        has_input_monitoring_access(),
        listener.start(),
    )
    listener.stop()
    assert config.CONFIG_DIR.name == ".rubric_gemini"
    assert screencapturekit_available(), "ScreenCaptureKit 모듈이 번들에 없습니다"
    logger.info("SELFTEST OK (python %s, frozen=%s)", sys.version.split()[0], IS_APP_BUNDLE)
    return 0


def main() -> None:
    if os.environ.get("SCREENANSWER_SELFTEST") == "1":
        sys.exit(_self_test())
    ScreenAnswerApp().run()


if __name__ == "__main__":
    main()
