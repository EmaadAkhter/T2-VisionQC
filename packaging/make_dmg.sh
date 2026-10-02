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

# hdiutil occasionally fails with "Resource busy" on CI runners (Spotlight /
# leftover mounts). Retry, and use a unique volume name if the plain one is
# still busy.
create_dmg() {
  hdiutil create \
    -volname "$1" \
    -srcfolder "$STAGE" \
    -ov -format UDZO \
    "$OUT" >/dev/null 2>&1
}

if ! create_dmg "$VOLUME"; then
  echo "note: first hdiutil attempt failed; retrying with a unique volume" >&2
  hdiutil detach "/Volumes/$VOLUME" -force >/dev/null 2>&1 || true
  rm -f "$OUT"
  sleep 2
  UNIQUE_VOLUME="$VOLUME $(date +%s)"
  if ! create_dmg "$UNIQUE_VOLUME"; then
    sleep 3
    if ! create_dmg "$UNIQUE_VOLUME-2"; then
      echo "error: hdiutil create failed after 3 attempts" >&2
      exit 1
    fi
  fi
fi

echo "wrote $OUT ($(du -h "$OUT" | cut -f1))"
