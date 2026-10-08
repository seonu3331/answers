"""pynput 기반 전역 단축키(Ctrl + Option + Cmd) 감지 및 커서 좌표 조회.

수정키만으로 이루어진 조합이라 pynput.keyboard.GlobalHotKeys(일반 키 필요)를
쓰지 않고, 눌린 수정키 집합을 직접 추적한다.

트리거 규칙:
  * Ctrl, Option, Cmd 세 키가 모두 눌린 상태가 되면 "조합 완성"으로 본다.
  * 조합이 완성된 뒤 첫 번째 키를 뗄 때 콜백을 호출한다.
  * 조합을 누르고 있는 동안 다른 키(예: Ctrl+Option+Cmd+T)가 눌리면
    다른 앱의 단축키로 간주하고 트리거하지 않는다.
  * 모든 키를 뗀 뒤에야 다시 트리거할 수 있다(누르고 있는 동안 반복 방지).
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

from pynput import keyboard, mouse

logger = logging.getLogger(__name__)

HotkeyCallback = Callable[[int, int], None]

_MODIFIER_GROUPS: dict[keyboard.Key, str] = {
    keyboard.Key.ctrl: "ctrl",
    keyboard.Key.ctrl_l: "ctrl",
    keyboard.Key.ctrl_r: "ctrl",
    keyboard.Key.alt: "alt",
    keyboard.Key.alt_l: "alt",
    keyboard.Key.alt_r: "alt",
    keyboard.Key.alt_gr: "alt",
    keyboard.Key.cmd: "cmd",
    keyboard.Key.cmd_l: "cmd",
    keyboard.Key.cmd_r: "cmd",
}

REQUIRED_MODIFIERS: frozenset[str] = frozenset({"ctrl", "alt", "cmd"})


def get_cursor_position() -> tuple[int, int]:
    """현재 마우스 커서의 전역 좌표(macOS 논리 좌표, 포인트 단위)를 반환한다.

    macOS에서 pynput은 Quartz CGEventGetLocation을 사용하므로 좌표 원점은
    주 모니터의 좌상단이며, mss의 모니터 좌표계와 동일하다.
    """
    x, y = mouse.Controller().position
    return int(round(x)), int(round(y))


class HotkeyListener:
    """Ctrl + Option + Cmd 조합을 감지해 커서 좌표와 함께 콜백을 호출한다.

    콜백은 pynput 리스너 스레드에서 호출되므로, 콜백 안에서는 무거운 작업을
    하지 말고 별도 스레드로 넘겨야 한다.
    """

    def __init__(self, callback: HotkeyCallback) -> None:
        self._callback = callback
        self._lock = threading.Lock()
        self._pressed_modifiers: dict[keyboard.Key, str] = {}
        self._chord_complete = False
        self._chord_dirty = False
        self._listener: keyboard.Listener | None = None

    def start(self) -> None:
        if self._listener is not None:
            return
        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.daemon = True
        self._listener.start()
        self._listener.wait()
        if getattr(self._listener, "IS_TRUSTED", True) is False:
            logger.warning(
                "키보드 이벤트 접근 권한이 없습니다. 시스템 설정 > 개인정보 보호 및 보안 > "
                "'손쉬운 사용'과 '입력 모니터링'에서 이 앱(터미널/Python)을 허용하세요."
            )

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    def _held_groups(self) -> set[str]:
        return set(self._pressed_modifiers.values())

    def _on_press(self, key: keyboard.Key | keyboard.KeyCode | None, *_args) -> None:
        with self._lock:
            group = _MODIFIER_GROUPS.get(key) if isinstance(key, keyboard.Key) else None
            if group is None:
                # 조합 키 외의 키가 섞이면 다른 단축키로 보고 이번 트리거를 취소한다.
                if self._pressed_modifiers:
                    self._chord_dirty = True
                return

            self._pressed_modifiers[key] = group
            if self._held_groups() == REQUIRED_MODIFIERS and not self._chord_dirty:
                self._chord_complete = True

    def _on_release(self, key: keyboard.Key | keyboard.KeyCode | None, *_args) -> None:
        fire = False
        with self._lock:
            if isinstance(key, keyboard.Key) and key in _MODIFIER_GROUPS:
                if self._chord_complete and not self._chord_dirty:
                    fire = True
                    # 같은 누름 동안 남은 키를 뗄 때 다시 발화하지 않도록 막는다.
                    self._chord_dirty = True
                if key in self._pressed_modifiers:
                    del self._pressed_modifiers[key]
                else:
                    # 눌림은 ctrl_l, 뗌은 ctrl처럼 좌/우 구분이 다르게 보고된 경우
                    # 같은 그룹을 통째로 정리해 키가 "눌린 채" 남지 않게 한다.
                    group = _MODIFIER_GROUPS[key]
                    self._pressed_modifiers = {
                        k: g for k, g in self._pressed_modifiers.items() if g != group
                    }

            if not self._pressed_modifiers:
                self._chord_complete = False
                self._chord_dirty = False

        if fire:
            try:
                x, y = get_cursor_position()
                self._callback(x, y)
            except Exception:  # 리스너 스레드가 죽지 않도록 모든 예외를 기록만 한다.
                logger.exception("단축키 콜백 처리 중 오류가 발생했습니다.")
