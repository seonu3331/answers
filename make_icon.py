"""앱 아이콘(assets/ScreenAnswer.icns)을 Pillow로 생성한다. build_app.sh에서 자동 호출."""

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 1024
OUT = Path(__file__).resolve().parent / "assets" / "ScreenAnswer.icns"


def build_icon() -> Image.Image:
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # macOS 아이콘 그리드에 맞춘 여백과 둥근 사각형 배경
    margin = 100
    top, bottom = (36, 99, 235), (17, 24, 39)
    for y in range(margin, SIZE - margin):
        t = (y - margin) / (SIZE - 2 * margin)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,)
        draw.line((margin, y, SIZE - margin, y), fill=color)
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (margin, margin, SIZE - margin, SIZE - margin), radius=185, fill=255
    )
    image.putalpha(mask)

    # 커서 마커(빨간 원 + 십자)와 메뉴바 점
    cx, cy, r = SIZE // 2, SIZE // 2 + 30, 190
    red = (248, 72, 72, 255)
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=red, width=44)
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        draw.line(
            (cx + dx * (r - 120), cy + dy * (r - 120), cx + dx * (r + 60), cy + dy * (r + 60)),
            fill=red,
            width=40,
        )
    dot = 46
    draw.ellipse((cx - dot, 200 - dot, cx + dot, 200 + dot), fill=(255, 255, 255, 255))
    return image


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    build_icon().save(OUT)
    print(f"아이콘 생성: {OUT}")


if __name__ == "__main__":
    main()
