"""Quartz CGEventTap 기반 전역 단축키 감지 및 커서 좌표 조회.

단축키:
  * ⌃ Control + ⌥ Option + ⌘ Command      → 1회 분석 (수정키만, 떼는 순간 실행)
  * ⌃ Control + ⌥ Option + ⌘ Command + A  → 5초 자동 분석 켜기/끄기 (A를 누르는 순간 실행)
    ⌃⌥⌘ + 글자 조합은 macOS 기본 단축키에 없다(⌃⌥⌘8, ⌃⌥⌘, ⌃⌥⌘. 등 손쉬운 사용 단축키는 숫자/기호).

pynput 대신 Quartz를 직접 쓰는 이유:
  * pynput 키보드 리스너는 백그라운드 스레드에서 키보드 레이아웃(TIS) API를 호출하는데,
    macOS 14 이후에는 이 API가 메인 스레드 전용이라 리스너가 조용히 죽을 수 있다.
  * 권한이 없어 이벤트 탭 생성에 실패해도 pynput은 오류 없이 끝나 원인을 알 수 없다.
  * 콜백이 늦어 macOS가 탭을 비활성화(kCGEventTapDisabledByTimeout)해도 복구하지 않는다.
여기서는 메인 런루프에 "수신 전용" 탭을 붙이고, 수정키 상태(flags)만 읽으며,
권한 확인/요청과 탭 자동 재활성화를 직접 처리한다.

수신 전용 탭(kCGEventTapOptionListenOnly)은 '입력 모니터링' 권한이 필요하다.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)

ACTION_SINGLE = "single"  # ⌃⌥⌘
ACTION_TOGGLE_AUTO = "toggle_auto"  # ⌃⌥⌘A

HotkeyCallback = Callable[[str, int, int], None]

# CGEventFlags 비트 (Quartz 상수와 동일한 값, 순수 로직 테스트용으로 직접 둔다)
FLAG_SHIFT = 1 << 17
FLAG_CONTROL = 1 << 18
FLAG_ALTERNATE = 1 << 19
FLAG_COMMAND = 1 << 20
_MODIFIER_MASK = FLAG_SHIFT | FLAG_CONTROL | FLAG_ALTERNATE | FLAG_COMMAND
_BASE_CHORD = FLAG_CONTROL | FLAG_ALTERNATE | FLAG_COMMAND

# 자동 분석 토글 키: 물리 키 위치 기준 가상 키코드라 한글 입력 상태에서도 같은 키(A/ㅁ)다.
KEYCODE_A = 0
AUTO_TOGGLE_KEYCODE = KEYCODE_A


@dataclass
class ChordDetector:
    """수정키 상태 변화를 받아 단축키 동작을 판정하는 순수 상태 기계.

    규칙:
      * 수정키가 하나라도 눌려 있는 동안을 한 번의 "조합"으로 본다.
      * 조합 중 가장 많이 눌렸던 수정키 집합(peak)으로 동작을 정한다.
      * 첫 번째 키를 떼는 순간 발화하고, 모든 키를 뗄 때까지 다시 발화하지 않는다.
      * 조합 중 일반 키(예: T)가 눌리면 다른 앱 단축키로 보고 1회 분석을 취소한다.
      * 단, ⌃⌥⌘(정확히 이 세 키)와 A가 함께 눌리면 자동 분석 토글로 처리한다(키 반복은 무시).
    """

    _peak: int = 0
    _held: int = 0
    _dirty: bool = False
    _fired: bool = field(default=False)

    def on_flags(self, flags: int) -> str | None:
        held = flags & _MODIFIER_MASK
        action: str | None = None

        if (held & ~self._held) == 0 and held != self._held:
            # 키를 뗀 순간(눌린 키가 줄어듦): peak 기준으로 판정
            if not self._dirty and not self._fired:
                if self._peak == _BASE_CHORD:
                    action = ACTION_SINGLE
                    self._fired = True
        self._peak |= held
        self._held = held

        if held == 0:
            self._peak = 0
            self._dirty = False
            self._fired = False
        return action

    def on_key_down(self, keycode: int, flags: int, autorepeat: bool = False) -> str | None:
        held = flags & _MODIFIER_MASK
        if held:
            self._dirty = True
        if held == _BASE_CHORD and keycode == AUTO_TOGGLE_KEYCODE and not autorepeat:
            return ACTION_TOGGLE_AUTO
        return None


def get_cursor_position() -> tuple[int, int]:
    """현재 마우스 커서의 전역 좌표(포인트, 주 모니터 좌상단 원점)를 반환한다."""
    import Quartz

    event = Quartz.CGEventCreate(None)
    point = Quartz.CGEventGetLocation(event)
    return int(round(point.x)), int(round(point.y))


def has_input_monitoring_access() -> bool:
    if sys.platform != "darwin":
        return False
    import Quartz

    try:
        return bool(Quartz.CGPreflightListenEventAccess())
    except AttributeError:
        return True


def request_input_monitoring_access() -> None:
    """권한 요청 창을 띄우고, 시스템 설정 '입력 모니터링' 목록에 앱을 등록한다."""
    import Quartz

    try:
        Quartz.CGRequestListenEventAccess()
    except AttributeError:
        pass


class HotkeyListener:
    """메인 런루프에 CGEventTap을 붙여 단축키를 감지한다.

    start()/콜백 모두 메인 스레드에서 실행된다. 콜백에서는 무거운 작업을 하지 말고
    별도 스레드로 넘겨야 한다(늦으면 macOS가 탭을 비활성화한다).
    """

    def __init__(self, callback: HotkeyCallback) -> None:
        self._callback = callback
        self._detector = ChordDetector()
        self._tap = None
        self._source = None
        # PyObjC가 콜백을 GC 하지 않도록 참조를 유지한다.
        self._tap_callback = self._on_event

    @property
    def active(self) -> bool:
        if self._tap is None:
            return False
        import Quartz

        return bool(Quartz.CGEventTapIsEnabled(self._tap))

    def start(self) -> bool:
        """탭을 만든다. 권한이 없어 실패하면 False (나중에 다시 호출해 재시도 가능)."""
        if self._tap is not None:
            return True
        import Quartz

        mask = Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged) | Quartz.CGEventMaskBit(
            Quartz.kCGEventKeyDown
        )
        tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            mask,
            self._tap_callback,
            None,
        )
        if tap is None:
            logger.warning(
                "단축키 이벤트 탭 생성 실패: '입력 모니터링' 권한이 없거나 아직 적용되지 않았습니다."
            )
            return False

        source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        # 메뉴가 열려 있는 동안(이벤트 추적 모드)에도 동작하도록 common modes 에 붙인다.
        Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetMain(), source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(tap, True)
        self._tap, self._source = tap, source
        logger.info("단축키 감지 시작 (⌃⌥⌘: 1회, ⌃⌥⌘A: 자동 분석 토글)")
        return True

    def stop(self) -> None:
        if self._tap is None:
            return
        import Quartz

        Quartz.CGEventTapEnable(self._tap, False)
        Quartz.CFRunLoopRemoveSource(
            Quartz.CFRunLoopGetMain(), self._source, Quartz.kCFRunLoopCommonModes
        )
        self._tap = self._source = None

    def ensure_enabled(self) -> None:
        """macOS가 탭을 꺼 버린 경우 다시 켠다(주기적으로 호출)."""
        if self._tap is not None and not self.active:
            import Quartz

            logger.warning("단축키 이벤트 탭이 꺼져 있어 다시 켭니다.")
            Quartz.CGEventTapEnable(self._tap, True)

    def _on_event(self, _proxy, event_type, event, _refcon):
        import Quartz

        try:
            if event_type in (
                Quartz.kCGEventTapDisabledByTimeout,
                Quartz.kCGEventTapDisabledByUserInput,
            ):
                if self._tap is not None:
                    Quartz.CGEventTapEnable(self._tap, True)
                return event

            action = None
            if event_type == Quartz.kCGEventKeyDown:
                action = self._detector.on_key_down(
                    int(Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)),
                    int(Quartz.CGEventGetFlags(event)),
                    bool(
                        Quartz.CGEventGetIntegerValueField(
                            event, Quartz.kCGKeyboardEventAutorepeat
                        )
                    ),
                )
            elif event_type == Quartz.kCGEventFlagsChanged:
                action = self._detector.on_flags(int(Quartz.CGEventGetFlags(event)))
            if action:
                point = Quartz.CGEventGetLocation(event)
                self._callback(action, int(round(point.x)), int(round(point.y)))
        except Exception:  # 콜백 예외로 탭이 멈추지 않도록 기록만 한다.
            logger.exception("단축키 처리 중 오류")
        return event
