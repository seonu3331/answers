"""마우스 커서 중심 화면 캡처 및 Retina 배율 보정.

좌표계 정리 (macOS):
  * pynput 커서 좌표와 mss 모니터 좌표는 모두 "포인트(논리 좌표)" 단위이며,
    주 모니터 좌상단이 원점인 같은 전역 좌표계를 쓴다.
  * 실제로 받아오는 이미지의 픽셀 수는 mss 버전/디스플레이에 따라
    논리 해상도(1x)일 수도, Retina 물리 해상도(2x 등)일 수도 있다.
  * 그래서 "요청한 영역 크기(포인트)" 대비 "받은 이미지 크기(픽셀)"로 배율을
    직접 계산해, 커서 표시 위치와 최종 이미지 크기를 보정한다.

디스크 I/O 없이 결과 PNG를 메모리(BytesIO)에만 담아 반환한다.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import mss
from PIL import Image, ImageDraw

CAPTURE_WIDTH = 800
CAPTURE_HEIGHT = 600


@dataclass(frozen=True)
class CaptureResult:
    png_bytes: bytes
    region: dict[str, int]  # 캡처한 영역(논리 좌표): left, top, width, height
    scale_factor: float  # 원본 캡처의 픽셀/포인트 배율 (Retina면 보통 2.0)
    cursor_in_image: tuple[int, int]  # 최종 이미지 안에서의 커서 위치(픽셀)


def _find_monitor(monitors: list[dict[str, int]], x: int, y: int) -> dict[str, int]:
    """커서가 위치한 모니터를 찾는다. monitors[0]은 전체 가상 화면이므로 제외한다."""
    physical = monitors[1:] or monitors[:1]
    for mon in physical:
        if (
            mon["left"] <= x < mon["left"] + mon["width"]
            and mon["top"] <= y < mon["top"] + mon["height"]
        ):
            return mon

    def distance(mon: dict[str, int]) -> int:
        cx = min(max(x, mon["left"]), mon["left"] + mon["width"] - 1)
        cy = min(max(y, mon["top"]), mon["top"] + mon["height"] - 1)
        return (cx - x) ** 2 + (cy - y) ** 2

    return min(physical, key=distance)


def _region_around(
    mon: dict[str, int], x: int, y: int, width: int, height: int
) -> dict[str, int]:
    """커서 중심의 영역을 만들되, 모니터 경계를 넘지 않도록 안쪽으로 밀어 넣는다."""
    width = min(width, mon["width"])
    height = min(height, mon["height"])

    left = x - width // 2
    top = y - height // 2
    left = max(mon["left"], min(left, mon["left"] + mon["width"] - width))
    top = max(mon["top"], min(top, mon["top"] + mon["height"] - height))

    return {"left": int(left), "top": int(top), "width": int(width), "height": int(height)}


def _draw_cursor_marker(image: Image.Image, cx: int, cy: int, unit: float) -> None:
    """스크린샷에는 커서가 찍히지 않으므로, 모델이 위치를 알 수 있게 표시를 그린다."""
    draw = ImageDraw.Draw(image)
    radius = max(6, int(14 * unit))
    line = max(2, int(3 * unit))
    gap = max(3, int(5 * unit))
    color = (255, 0, 0)

    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=color, width=line)
    draw.line((cx - radius - gap * 2, cy, cx - gap, cy), fill=color, width=line)
    draw.line((cx + gap, cy, cx + radius + gap * 2, cy), fill=color, width=line)
    draw.line((cx, cy - radius - gap * 2, cx, cy - gap), fill=color, width=line)
    draw.line((cx, cy + gap, cx, cy + radius + gap * 2), fill=color, width=line)


def capture_around_cursor(
    x: int,
    y: int,
    width: int = CAPTURE_WIDTH,
    height: int = CAPTURE_HEIGHT,
    max_output_scale: float = 2.0,
) -> CaptureResult:
    """커서 (x, y)를 중심으로 width x height(포인트) 영역을 캡처해 PNG 바이트로 반환한다.

    max_output_scale: 최종 이미지의 최대 배율. Retina(2x) 원본은 작은 글씨 인식에
    유리하므로 기본적으로 2x까지 유지하고, 그보다 큰 배율(3x 등)은 줄여서
    업로드 크기와 지연 시간을 아낀다.
    """
    # mss 인스턴스는 스레드 간 공유가 안전하지 않으므로 호출마다 새로 만든다.
    with mss.mss() as sct:
        mon = _find_monitor(sct.monitors, x, y)
        region = _region_around(mon, x, y, width, height)
        shot = sct.grab(region)
        image = Image.frombytes("RGB", shot.size, shot.rgb)

    # Retina 배율 보정: 요청 영역(포인트) 대비 실제 픽셀 수로 배율을 계산한다.
    scale_x = image.width / region["width"]
    scale_y = image.height / region["height"]
    scale = (scale_x + scale_y) / 2

    if scale > max_output_scale:
        target = (
            round(region["width"] * max_output_scale),
            round(region["height"] * max_output_scale),
        )
        image = image.resize(target, Image.Resampling.LANCZOS)
        scale_x = image.width / region["width"]
        scale_y = image.height / region["height"]

    cursor_px = (
        int(round((x - region["left"]) * scale_x)),
        int(round((y - region["top"]) * scale_y)),
    )
    _draw_cursor_marker(image, cursor_px[0], cursor_px[1], unit=(scale_x + scale_y) / 2)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False)

    return CaptureResult(
        png_bytes=buffer.getvalue(),
        region=region,
        scale_factor=scale,
        cursor_in_image=cursor_px,
    )
