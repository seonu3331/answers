"""py2app 빌드 설정.

사용법:  ./build_app.sh            (권장: 아이콘 생성 + 빌드 + ad-hoc 서명까지 수행)
직접:    python setup.py py2app     → dist/ScreenAnswer.app
개발용:  python setup.py py2app -A  → 소스를 참조하는 alias 번들(빠른 확인용)
"""

from pathlib import Path

from setuptools import setup

APP_NAME = "ScreenAnswer"
VERSION = "1.2.0"
BUNDLE_ID = "com.rubric.screenanswer"
ICON = Path("assets/ScreenAnswer.icns")

PLIST = {
    "CFBundleName": APP_NAME,
    "CFBundleDisplayName": APP_NAME,
    "CFBundleIdentifier": BUNDLE_ID,
    "CFBundleShortVersionString": VERSION,
    "CFBundleVersion": VERSION,
    # AlDente처럼 Dock 아이콘 + 메뉴바를 함께 쓰는 일반 앱.
    # False(기본값)여야 Dock/앱 전환기에 보이고, 개인정보 보호 설정 목록에서도 찾기 쉽다.
    "LSUIElement": False,
    "LSApplicationCategoryType": "public.app-category.productivity",
    "LSMinimumSystemVersion": "14.0",  # ScreenCaptureKit 스크린샷 API
    "NSHighResolutionCapable": True,
    "NSHumanReadableCopyright": "ScreenAnswer",
}

OPTIONS = {
    "argv_emulation": False,
    "plist": PLIST,
    # 동적 import(백엔드 자동 선택, 플러그인 로딩)를 쓰는 패키지는 통째로 복사해
    # modulegraph가 놓치는 모듈이 없도록 한다.
    # 'google'은 __init__.py 없는 네임스페이스 패키지라 packages 에 넣으면 py2app이
    # 찾지 못한다(ImportError: No module named 'google'). 그래서 includes 로 추적시킨다.
    "packages": [
        "pydantic",
        "pydantic_core",
        "httpx",
        "httpcore",
        "anyio",
        "certifi",
        "websockets",
        "PIL",
        "mss",
        "pynput",
        "rumps",
        "ScreenCaptureKit",
        "dotenv",
    ],
    "includes": [
        "google.genai",
        "google.genai.types",
        "google.genai.errors",
        "google.auth",
        "google.auth.transport.requests",
        "google.oauth2.service_account",
        "typing_extensions",
        "config",
        "analyzer",
        "capture",
        "hotkey",
    ],
    "excludes": ["tkinter", "unittest", "pytest", "numpy", "matplotlib"],
}
if ICON.exists():
    OPTIONS["iconfile"] = str(ICON)

setup(
    name=APP_NAME,
    version=VERSION,
    app=["main.py"],
    options={"py2app": OPTIONS},
)
