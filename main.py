"""macOS 메뉴바 앱: 단축키 → 커서 주변 캡처 → Gemini 분석 → 메뉴바 타이틀 갱신.

실행: python main.py
단축키: Ctrl + Option + Cmd (세 키를 함께 눌렀다 떼면 실행)
"""

from __future__ import annotations

import logging
import queue
import subprocess
import threading
from pathlib import Path

import rumps
from dotenv import load_dotenv

from analyzer import Analyzer, AnalyzerError
from capture import capture_around_cursor
from hotkey import HotkeyListener

load_dotenv(Path(__file__).resolve().with_name(".env"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("screen-answer")

APP_NAME = "ScreenAnswer"
IDLE_TITLE = "[ · ]"
BUSY_TITLE = "[ … ]"
MAX_TITLE_CHARS = 24
MAX_MENU_CHARS = 90
UI_POLL_INTERVAL = 0.1


def _truncate(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _hide_dock_icon() -> None:
    """소스에서 직접 실행할 때도 Dock 아이콘 없이 메뉴바에만 표시되도록 한다."""
    try:
        from AppKit import NSBundle

        info = NSBundle.mainBundle().infoDictionary()
        info["LSUIElement"] = "1"
    except Exception:
        logger.debug("Dock 아이콘 숨김 설정을 적용하지 못했습니다.", exc_info=True)


def _copy_to_clipboard(text: str) -> None:
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=False)


class ScreenAnswerApp(rumps.App):
    def __init__(self) -> None:
        super().__init__(APP_NAME, title=IDLE_TITLE, quit_button=None)

        self._result_text = ""
        self._summary_text = ""

        self.result_item = rumps.MenuItem("결과: (없음)", callback=self.on_copy_result)
        self.summary_item = rumps.MenuItem("요약: (없음)", callback=self.on_copy_summary)
        self.hint_item = rumps.MenuItem("단축키: ⌃ ⌥ ⌘ (눌렀다 떼기)")
        self.menu = [
            self.result_item,
            self.summary_item,
            None,
            self.hint_item,
            None,
            rumps.MenuItem("초기화(Clear)", callback=self.on_clear),
            rumps.MenuItem("종료(Quit)", callback=self.on_quit),
        ]

        # 백그라운드 스레드는 UI를 직접 건드리지 않고 큐에만 넣는다.
        # 메인 스레드의 rumps.Timer가 큐를 비우며 AppKit UI를 갱신한다.
        self._ui_events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._work_lock = threading.Lock()
        self._generation = 0
        self._generation_lock = threading.Lock()

        self._analyzer: Analyzer | None = None
        self._analyzer_error: str | None = None
        try:
            self._analyzer = Analyzer()
            logger.info("Gemini 모델: %s", self._analyzer.model)
        except AnalyzerError as exc:
            self._analyzer_error = str(exc)
        except Exception as exc:
            self._analyzer_error = f"초기화 실패: {exc}"
        if self._analyzer_error:
            logger.error("분석기 초기화 실패: %s", self._analyzer_error)
            self._show_error(self._analyzer_error)

        self._ui_timer = rumps.Timer(self._drain_ui_events, UI_POLL_INTERVAL)
        self._ui_timer.start()

        self._hotkey = HotkeyListener(self._on_hotkey)
        self._hotkey.start()
        logger.info("준비 완료. Ctrl + Option + Cmd 를 눌렀다 떼면 분석합니다.")

    # ---------- 단축키 → 작업 스레드 ----------

    def _on_hotkey(self, x: int, y: int) -> None:
        """pynput 리스너 스레드에서 호출된다. 즉시 작업 스레드로 넘긴다."""
        if not self._work_lock.acquire(blocking=False):
            logger.info("이전 분석이 진행 중이라 이번 요청은 무시합니다.")
            return
        with self._generation_lock:
            self._generation += 1
            generation = self._generation
        threading.Thread(
            target=self._run_pipeline, args=(x, y, generation), name="analyze", daemon=True
        ).start()

    def _run_pipeline(self, x: int, y: int, generation: int) -> None:
        try:
            if self._analyzer is None:
                self._ui_events.put(("error", (generation, self._analyzer_error or "분석기 없음")))
                return
            self._ui_events.put(("busy", generation))

            capture = capture_around_cursor(x, y)
            logger.info(
                "캡처 완료: 영역=%s, 배율=%.2f, 커서=%s, %d bytes",
                capture.region,
                capture.scale_factor,
                capture.cursor_in_image,
                len(capture.png_bytes),
            )

            analysis = self._analyzer.analyze(capture.png_bytes)
            logger.info("분석 결과: %s | %s", analysis.result, analysis.summary)
            self._ui_events.put(("result", (generation, analysis.result, analysis.summary)))
        except AnalyzerError as exc:
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
        while True:
            try:
                kind, payload = self._ui_events.get_nowait()
            except queue.Empty:
                return

            if kind == "busy":
                if self._is_current(payload):  # type: ignore[arg-type]
                    self.title = BUSY_TITLE
            elif kind == "result":
                generation, result, summary = payload  # type: ignore[misc]
                if self._is_current(generation):
                    self._show_result(result, summary)
            elif kind == "error":
                generation, message = payload  # type: ignore[misc]
                if self._is_current(generation):
                    self._show_error(message)

    def _show_result(self, result: str, summary: str) -> None:
        self._result_text = result
        self._summary_text = summary
        self.title = f"[{_truncate(result, MAX_TITLE_CHARS)}]"
        self.result_item.title = f"결과: {_truncate(result, MAX_MENU_CHARS)}"
        self.summary_item.title = f"요약: {_truncate(summary or '(없음)', MAX_MENU_CHARS)}"

    def _show_error(self, message: str) -> None:
        self.title = f"[⚠ {_truncate(message, MAX_TITLE_CHARS - 2)}]"
        self.summary_item.title = f"오류: {_truncate(message, MAX_MENU_CHARS)}"

    # ---------- 메뉴 콜백 ----------

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
        self.title = IDLE_TITLE
        self.result_item.title = "결과: (없음)"
        self.summary_item.title = "요약: (없음)"

    def on_quit(self, _sender: rumps.MenuItem) -> None:
        self._hotkey.stop()
        self._ui_timer.stop()
        rumps.quit_application()


def main() -> None:
    _hide_dock_icon()
    ScreenAnswerApp().run()


if __name__ == "__main__":
    main()
