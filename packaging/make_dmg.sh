#!/usr/bin/env bash
# Build a distributable DMG from a VisionQC.app bundle.
#
# Usage: packaging/make_dmg.sh dist/VisionQC.app VisionQC-macos-arm64.dmg
set -euo pipefail

APP="${1:?usage: make_dmg.sh <VisionQC.app> <output.dmg>}"
OUT="${2:?usage: make_dmg.sh <VisionQC.app> <output.dmg>}"
VOLUME="VisionQC"

if [ ! -d "$APP" ]; then
  echo "error: $APP not found" >&2
  exit 1
fi

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"

rm -f "$OUT"
hdiutil create \
  -volname "$VOLUME" \
  -srcfolder "$STAGE" \
  -ov -format UDZO \
  "$OUT" >/dev/null

echo "wrote $OUT ($(du -h "$OUT" | cut -f1))"
