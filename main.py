"""macOS 메뉴바 앱: 단축키 → 커서 주변 캡처 → Gemini 분석 → 메뉴바 타이틀 갱신.

실행: python main.py  (또는 build_app.sh 로 만든 ScreenAnswer.app 더블 클릭)
단축키: Ctrl + Option + Cmd (세 키를 함께 눌렀다 떼면 실행)

메뉴바 표시: '·' 대기 → '..' 분석 중 → '3' 결과 (5초 후 자동으로 '·' 복귀)

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
from capture import CaptureError, capture_around_cursor
from hotkey import HotkeyListener

APP_NAME = "ScreenAnswer"
IDLE_TITLE = "·"
BUSY_TITLE = ".."
RESULT_DISPLAY_SECONDS = 5.0
MAX_TITLE_CHARS = 24
MAX_MENU_CHARS = 90
UI_POLL_INTERVAL = 0.1

# py2app 번들 안에서 실행 중이면 sys.frozen == "macosx_app"
IS_APP_BUNDLE = getattr(sys, "frozen", None) == "macosx_app"
LOG_FILE = Path.home() / "Library" / "Logs" / f"{APP_NAME}.log"

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
        self.key_status_item = rumps.MenuItem("API Key: (미설정)")
        self.permissions_menu = rumps.MenuItem("권한 설정 열기")
        for label in PRIVACY_PANES:
            self.permissions_menu.add(rumps.MenuItem(label, callback=self.on_open_privacy))

        self.menu = [
            self.result_item,
            self.summary_item,
            None,
            rumps.MenuItem("API Key 설정…", callback=self.on_set_api_key),
            self.key_status_item,
            self.permissions_menu,
            None,
            rumps.MenuItem("단축키: ⌃ ⌥ ⌘ (눌렀다 떼기)"),
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

        self._analyzer: Analyzer | None = None
        stored = config.load_api_key()
        if stored:
            self._apply_api_key(stored, verify=False)
        else:
            self._set_missing_key_state()
            # 메인 런루프가 돌기 시작한 직후에 최초 실행 키 입력 창을 띄운다.
            self._first_run_timer = rumps.Timer(self._on_first_run_prompt, 0.8)
            self._first_run_timer.start()

        self._ui_timer = rumps.Timer(self._drain_ui_events, UI_POLL_INTERVAL)
        self._ui_timer.start()

        self._hotkey = HotkeyListener(self._on_hotkey)
        self._hotkey.start()
        logger.info("준비 완료. Ctrl + Option + Cmd 를 눌렀다 떼면 분석합니다.")

    # ---------- API 키 ----------

    def _on_first_run_prompt(self, timer: rumps.Timer) -> None:
        timer.stop()
        if self._analyzer is None:
            self._prompt_api_key(first_run=True)

    def _prompt_api_key(self, first_run: bool = False) -> None:
        if self._prompt_open:
            return
        self._prompt_open = True
        try:
            message = (
                "Google AI Studio(aistudio.google.com)에서 발급한 Gemini API 키를 붙여넣으세요.\n"
                "키는 macOS Keychain에 저장됩니다."
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
            self._ui_events.put(("key_bad", (analyzer, stored, str(exc))))
        except Exception as exc:  # 네트워크 오류 등
            self._ui_events.put(("key_bad", (analyzer, stored, type(exc).__name__)))

    def _set_missing_key_state(self) -> None:
        self._analyzer = None
        self.key_status_item.title = "API Key: (미설정)"
        self.title = "⚠ 키 없음"
        self._revert_at = None

    # ---------- 단축키 → 작업 스레드 ----------

    def _on_hotkey(self, x: int, y: int) -> None:
        """pynput 리스너 스레드에서 호출된다. 즉시 작업 스레드로 넘긴다."""
        analyzer = self._analyzer
        if analyzer is None:
            self._ui_events.put(("need_key", None))
            return
        if not self._work_lock.acquire(blocking=False):
            logger.info("이전 분석이 진행 중이라 이번 요청은 무시합니다.")
            return
        with self._generation_lock:
            self._generation += 1
            generation = self._generation
        threading.Thread(
            target=self._run_pipeline,
            args=(analyzer, x, y, generation),
            name="analyze",
            daemon=True,
        ).start()

    def _run_pipeline(self, analyzer: Analyzer, x: int, y: int, generation: int) -> None:
        try:
            self._ui_events.put(("busy", generation))

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
            self._ui_events.put(("result", (generation, analysis.result, analysis.summary)))
        except (AnalyzerError, CaptureError) as exc:
            logger.error("분석 실패: %s", exc)
            self._ui_events.put(("error", (generation, str(exc))))
        except Exception as exc:
            logger.exception("파이프라인 오류")
            self._ui_events.put(("error", (generation, type(exc).__name__)))
        finally:
            self._work_lock.release()

    # ---------- 메인 스레드 UI 갱신 ----------

    def _is_current(self, generation: int) -> bool:
        with self._generation_lock:
            return generation == self._generation

    def _drain_ui_events(self, _timer: rumps.Timer) -> None:
        # 5초 자동 복귀도 같은 메인 스레드 타이머에서 처리해 UI 접근을 한 곳으로 모은다.
        if self._revert_at is not None and time.monotonic() >= self._revert_at:
            self._revert_at = None
            self.title = IDLE_TITLE

        while True:
            try:
                kind, payload = self._ui_events.get_nowait()
            except queue.Empty:
                return

            if kind == "busy":
                if self._is_current(payload):  # type: ignore[arg-type]
                    self._revert_at = None
                    self.title = BUSY_TITLE
            elif kind == "result":
                generation, result, summary = payload  # type: ignore[misc]
                if self._is_current(generation):
                    self._show_result(result, summary)
            elif kind == "error":
                generation, message = payload  # type: ignore[misc]
                if self._is_current(generation):
                    self._show_error(message)
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

    def _show_result(self, result: str, summary: str) -> None:
        self._result_text = result
        self._summary_text = summary
        self.title = _truncate(result, MAX_TITLE_CHARS)
        self.result_item.title = f"결과: {_truncate(result, MAX_MENU_CHARS)}"
        self.summary_item.title = f"요약: {_truncate(summary or '(없음)', MAX_MENU_CHARS)}"
        self._schedule_revert()

    def _show_error(self, message: str) -> None:
        self.title = f"⚠ {_truncate(message, MAX_TITLE_CHARS - 2)}"
        self.summary_item.title = f"오류: {_truncate(message, MAX_MENU_CHARS)}"
        self._schedule_revert()

    def _schedule_revert(self) -> None:
        """타이틀만 대기 상태로 되돌린다. 드롭다운의 결과/요약은 남겨 다시 확인할 수 있다."""
        self._revert_at = time.monotonic() + RESULT_DISPLAY_SECONDS

    # ---------- 메뉴 콜백 ----------

    def on_set_api_key(self, _sender: rumps.MenuItem) -> None:
        self._prompt_api_key()

    def on_open_privacy(self, sender: rumps.MenuItem) -> None:
        subprocess.run(["open", PRIVACY_PANES[sender.title]], check=False)

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
            self.title = IDLE_TITLE

    def on_quit(self, _sender: rumps.MenuItem) -> None:
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
    from pynput import keyboard, mouse  # noqa: F401

    from analyzer import normalize_result
    from capture import screencapturekit_available

    # 가짜 키로 실제 HTTPS 요청을 보내 SSL/네트워크 모듈이 번들에 다 들어갔는지 확인한다.
    try:
        Analyzer(api_key="AIzaSySELFTEST_000000000000000000").verify()
    except AnalyzerError as exc:
        logger.info("가짜 키 확인 요청 → 예상된 거부: %s", exc)
    assert normalize_result("정답: 3번", "choice") == "3"
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
