#!/usr/bin/env bash
# Build standalone installer ZIPs (no environment.yml, no full source tree).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$ROOT/dist}"
mkdir -p "$OUT"
STAGE="$(mktemp -d)"
cleanup() { rm -rf "$STAGE"; }
trap cleanup EXIT

# Windows
W="$STAGE/autoSAXS-installer-windows"
mkdir -p "$W/installer/assets"
cp -a "$ROOT/scripts/Install autoSAXS.bat" "$W/"
cp -a "$ROOT/scripts/installer-windows/Install-autoSAXS.ps1" "$W/installer/"
cp -a "$ROOT/scripts/installer-windows/Install-autoSAXS-lib.ps1" "$W/installer/"
cp -a "$ROOT/scripts/installer-windows/Install-autoSAXS-worker.ps1" "$W/installer/"
cp -a "$ROOT/scripts/assets/autosaxs_icon.ico" "$W/installer/assets/"
cp -a "$ROOT/scripts/assets/autosaxs_icon.png" "$W/installer/assets/" 2>/dev/null || true
rm -f "$OUT/autoSAXS-installer-windows.zip"
( cd "$STAGE" && zip -r -q "$OUT/autoSAXS-installer-windows.zip" autoSAXS-installer-windows )

# Linux
L="$STAGE/autoSAXS-installer-linux"
mkdir -p "$L/assets"
cp -a "$ROOT/scripts/Install-autoSAXS.sh" "$L/"
cp -a "$ROOT/scripts/assets/autosaxs_icon.png" "$L/assets/"
chmod +x "$L/Install-autoSAXS.sh"
rm -f "$OUT/autoSAXS-installer-linux.zip"
( cd "$STAGE" && zip -r -q "$OUT/autoSAXS-installer-linux.zip" autoSAXS-installer-linux )

echo "Wrote:"
ls -la "$OUT/autoSAXS-installer-windows.zip" "$OUT/autoSAXS-installer-linux.zip"
