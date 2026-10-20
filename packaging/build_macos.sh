#!/usr/bin/env bash
# Costruisce «Script Follower.app» e il .dmg per macOS (12 Monterey o successivo). L'app è portable:
# si può tenere in qualunque cartella o su una chiavetta.
#
#   packaging/build_macos.sh                 (dalla cartella del progetto, su un Mac)
#
# Serve: Python 3.11–3.13 e la connessione a Internet. MODEL_URL vuota = senza modello vocale incluso.
# Firma e notarizzazione (facoltative): SCRIPTFOLLOWER_CODESIGN_ID, APPLE_ID, APPLE_TEAM_ID, APPLE_APP_PASSWORD
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
BUILD="$ROOT/build/macos"
CACHE="$ROOT/build/cache"
VERSION="$(sed -n 's/^__version__ *= *"\(.*\)"/\1/p' scriptfollower/__init__.py)"
ARCH="$(uname -m)"                                   # arm64 o x86_64
PY="${PYTHON:-python3}"
MODEL_URL="${MODEL_URL-https://alphacephei.com/vosk/models/vosk-model-small-it-0.22.zip}"
say() { printf '\033[1;36m==> %s\033[0m\n' "$*"; }
mkdir -p "$BUILD" "$CACHE" dist

say "ambiente Python ($("$PY" --version))"
"$PY" -m venv "$BUILD/venv"
"$BUILD/venv/bin/pip" install -q --upgrade pip
"$BUILD/venv/bin/pip" install -q -r requirements.txt pyinstaller

if [ -n "$MODEL_URL" ]; then
    say "modello vocale"
    [ -s "$CACHE/model.zip" ] || curl -fL --retry 3 -o "$CACHE/model.zip" "$MODEL_URL"
    rm -rf "$BUILD/model" && mkdir -p "$BUILD/model"
    unzip -q "$CACHE/model.zip" -d "$BUILD/model"
    SCRIPTFOLLOWER_MODEL_DIR="$(find "$BUILD/model" -mindepth 1 -maxdepth 1 -type d | head -1)"
    export SCRIPTFOLLOWER_MODEL_DIR
    export SCRIPTFOLLOWER_REQUIRE_MODEL=1
fi

say "PyInstaller"
rm -rf dist/ScriptFollower "dist/Script Follower.app"
SCRIPTFOLLOWER_MAC_ARCH="$ARCH" \
    "$BUILD/venv/bin/pyinstaller" packaging/scriptfollower.spec --noconfirm --distpath dist --workpath "$BUILD/pyi"
rm -rf dist/ScriptFollower                            # resta solo l'app
APP="dist/Script Follower.app"

SIGN="${SCRIPTFOLLOWER_CODESIGN_ID:--}"               # «-» = firma ad hoc (gratuita, solo locale)
say "firma ($([ "$SIGN" = "-" ] && echo "ad hoc" || echo "$SIGN"))"
codesign --force --deep --timestamp=none --options runtime \
    --entitlements packaging/macos/entitlements.plist -s "$SIGN" "$APP" 2>/dev/null \
  || codesign --force --deep -s "$SIGN" "$APP"

say "prova di avvio"
QT_QPA_PLATFORM=offscreen "$APP/Contents/MacOS/ScriptFollower" --self-test "$BUILD/self-test.txt" || {
    cat "$BUILD/self-test.txt" 2>/dev/null; exit 1; }

say "immagine disco"
DMG="dist/ScriptFollower-$VERSION-macos-$ARCH.dmg"
rm -rf "$BUILD/dmg" "$DMG" && mkdir -p "$BUILD/dmg"
ditto "$APP" "$BUILD/dmg/Script Follower.app"
ln -s /Applications "$BUILD/dmg/Applicazioni"
DMG_OK=0
for n in 1 2 3 4 5; do              # hdiutil a volte fallisce con «Resource busy» sui Mac di GitHub
    if hdiutil create -volname "Script Follower $VERSION" -srcfolder "$BUILD/dmg" -ov -fs HFS+ -format UDZO "$DMG"; then
        DMG_OK=1
        break
    fi
    echo "hdiutil non riuscito (tentativo $n di 5): riprovo tra $((n * 10)) s"
    rm -f "$DMG"
    sleep $((n * 10))
done
if [ "$DMG_OK" != 1 ]; then
    ZIP="dist/ScriptFollower-$VERSION-macos-$ARCH.zip"
    echo "::warning::immagine disco non creata: pubblico l'app in $ZIP"
    ditto -c -k --keepParent "$APP" "$ZIP"
    DMG="$ZIP"
fi

if [ "$SIGN" != "-" ] && [ -n "${APPLE_ID:-}" ] && [ -n "${APPLE_APP_PASSWORD:-}" ] && [ -n "${APPLE_TEAM_ID:-}" ]; then
    say "notarizzazione Apple (qualche minuto)"
    if [ "${DMG##*.}" = dmg ]; then codesign -s "$SIGN" --timestamp "$DMG"; fi
    xcrun notarytool submit "$DMG" --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" \
        --password "$APPLE_APP_PASSWORD" --wait
    if [ "${DMG##*.}" = dmg ]; then xcrun stapler staple "$DMG"; fi
fi
say "fatto: $DMG ($(du -h "$DMG" | cut -f1))"
