#!/usr/bin/env bash
# dist/ScreenAnswer.app 을 드래그 설치용 .dmg 로 묶는다. (build_app.sh 다음에 실행)
#
#   ./make_dmg.sh            → dist/ScreenAnswer-<버전>-<arm64|x86_64>.dmg
set -euo pipefail

cd "$(dirname "$0")"

APP="dist/ScreenAnswer.app"
if [[ ! -d "$APP" ]]; then
  echo "$APP 이 없습니다. 먼저 ./build_app.sh 를 실행하세요." >&2
  exit 1
fi

VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist")"
ARCH="$(uname -m)"
DMG="dist/ScreenAnswer-${VERSION}-${ARCH}.dmg"

STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT

# 앱 + /Applications 바로가기 → Finder에서 드래그해서 설치
cp -R "$APP" "$STAGING/"
ln -s /Applications "$STAGING/Applications"

rm -f "$DMG"
hdiutil create \
  -volname "ScreenAnswer" \
  -srcfolder "$STAGING" \
  -fs HFS+ \
  -format UDZO \
  -ov \
  "$DMG"

echo "완료: $DMG"
