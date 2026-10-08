"""화면 캡처 진단 도구: ScreenCaptureKit / mss 두 경로로 커서 주변을 찍어 비교한다.

    python scripts/capture_check.py            # 결과 PNG를 ~/Desktop 에 저장
    python scripts/capture_check.py --no-save  # 통계만 출력 (CI용)

이미지가 전부 검거나 바탕화면만 보이면 화면 기록 권한 문제다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageChops, ImageStat  # noqa: E402

import capture  # noqa: E402


def describe(image: Image.Image) -> str:
    stat = ImageStat.Stat(image.convert("L"))
    colors = len(image.resize((200, 150)).getcolors(200 * 150) or [])
    return f"{image.width}x{image.height} mean={stat.mean[0]:.1f} stddev={stat.stddev[0]:.1f} colors={colors}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-save", action="store_true")
    parser.add_argument("--x", type=int)
    parser.add_argument("--y", type=int)
    args = parser.parse_args()

    if args.x is not None and args.y is not None:
        x, y = args.x, args.y
    else:
        from hotkey import get_cursor_position

        x, y = get_cursor_position()

    print(f"macOS: {sys.platform}, 커서=({x}, {y})")
    print(f"화면 기록 권한(CGPreflightScreenCaptureAccess): {capture.has_screen_capture_access()}")
    print(f"ScreenCaptureKit 사용 가능: {capture.screencapturekit_available()}")

    region = capture._find_region(x, y)
    print(f"캡처 영역(pt): {region}")

    results: dict[str, Image.Image] = {}
    for name, grab in (
        ("ScreenCaptureKit", lambda: capture._grab_screencapturekit(region, x, y)),
        ("mss", lambda: capture._grab_mss(region)),
    ):
        try:
            image = grab()
        except Exception as exc:
            print(f"[{name}] 실패: {type(exc).__name__}: {exc}")
            continue
        results[name] = image
        print(f"[{name}] {describe(image)}")

    if len(results) == 2:
        a = results["ScreenCaptureKit"].resize((400, 300))
        b = results["mss"].resize((400, 300))
        diff = ImageStat.Stat(ImageChops.difference(a, b).convert("L")).mean[0]
        print(f"두 백엔드 이미지 평균 차이(0=동일): {diff:.1f}")

    if not args.no_save:
        desktop = Path.home() / "Desktop"
        for name, image in results.items():
            path = desktop / f"capture_check_{name}.png"
            image.save(path)
            print(f"저장: {path}")

    print("최종 경로:", end=" ")
    try:
        result = capture.capture_around_cursor(x, y)
        print(f"{result.backend}, 배율 {result.scale_factor:.2f}, {len(result.png_bytes)} bytes")
    except Exception as exc:
        print(f"실패: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
