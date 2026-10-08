#!/usr/bin/env bash
# ScreenAnswer.app 빌드 스크립트 (macOS 전용)
#
#   ./build_app.sh          독립 실행형 .app 빌드 → dist/ScreenAnswer.app
#   ./build_app.sh --alias  개발용 alias 빌드(소스 수정이 바로 반영, 다른 Mac에 배포 불가)
#   ./build_app.sh --install  빌드 후 /Applications 로 복사
set -euo pipefail

cd "$(dirname "$0")"

if [[ "$(uname)" != "Darwin" ]]; then
  echo "macOS에서만 빌드할 수 있습니다." >&2
  exit 1
fi

MODE="standalone"
INSTALL=0
for arg in "$@"; do
  case "$arg" in
    --alias) MODE="alias" ;;
    --install) INSTALL=1 ;;
    *) echo "알 수 없는 옵션: $arg" >&2; exit 1 ;;
  esac
done

PYTHON="${PYTHON:-python3}"
VENV_DIR=".venv"

if [[ ! -d "$VENV_DIR" ]]; then
  echo "==> 가상환경 생성 ($VENV_DIR)"
  "$PYTHON" -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo "==> 의존성 설치"
python -m pip install --upgrade pip >/dev/null
python -m pip install -r requirements.txt
python -m pip install "py2app>=0.28"

echo "==> 아이콘 생성"
python make_icon.py

echo "==> 이전 빌드 정리"
rm -rf build dist

if [[ "$MODE" == "alias" ]]; then
  echo "==> alias 빌드"
  python setup.py py2app -A
else
  echo "==> 독립 실행형 빌드"
  python setup.py py2app
fi

APP="dist/ScreenAnswer.app"

# ad-hoc 서명: Apple Silicon에서 실행에 필요하고, 권한(TCC) 식별을 안정화한다.
echo "==> ad-hoc 코드 서명"
codesign --force --deep --sign - "$APP"

if [[ "$INSTALL" == "1" ]]; then
  echo "==> /Applications 로 설치"
  rm -rf "/Applications/ScreenAnswer.app"
  cp -R "$APP" /Applications/
  APP="/Applications/ScreenAnswer.app"
fi

echo
echo "완료: $APP"
echo "실행: open \"$APP\""
echo "로그: ~/Library/Logs/ScreenAnswer.log"
