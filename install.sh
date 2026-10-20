#!/usr/bin/env bash
# Installazione di Script Follower dai sorgenti su Ubuntu (22.04 e successive).
# Per l'uso portable non serve: basta l'AppImage della pagina Releases.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.local/share/scriptfollower"
MODEL="vosk-model-small-it-0.22"

echo "==> Pacchetti di sistema (richiede la password di amministratore)"
# un repository difettoso nel sistema non deve bloccare l'installazione
if ! sudo apt update; then
  echo
  echo "!! 'apt update' ha segnalato errori (di solito una sorgente software non valida nel sistema)."
  echo "!! Proseguo con gli elenchi di pacchetti già presenti."
  echo
fi
sudo apt install -y python3 python3-venv libportaudio2 libxcb-cursor0 libgl1 libegl1 unzip curl || true

echo "==> Copia dei file in $DEST"
mkdir -p "$DEST"
cp -r "$DIR/scriptfollower" "$DIR/requirements.txt" "$DIR/run.sh" "$DEST/"
[ -f "$DIR/README.md" ] && cp "$DIR/README.md" "$DEST/"
chmod +x "$DEST/run.sh"

echo "==> Ambiente Python isolato"
python3 -m venv "$DEST/.venv"
"$DEST/.venv/bin/pip" install --upgrade pip wheel
"$DEST/.venv/bin/pip" install -r "$DEST/requirements.txt"

echo "==> Modello vocale italiano ($MODEL, circa 50 MB)"
if [ -d "$DEST/models/$MODEL" ]; then
  echo "   già presente"
elif curl -fL --retry 3 -o "/tmp/$MODEL.zip" "https://alphacephei.com/vosk/models/$MODEL.zip"; then
  mkdir -p "$DEST/models"
  unzip -q -o "/tmp/$MODEL.zip" -d "$DEST/models" && rm -f "/tmp/$MODEL.zip"
else
  echo "!! Download non riuscito: lo puoi scaricare dopo da Impostazioni › Riconoscimento."
fi

echo "==> Avviatore e voce di menu"
mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications" "$HOME/.local/share/icons"
ln -sf "$DEST/run.sh" "$HOME/.local/bin/scriptfollower"
cp "$DIR/packaging/scriptfollower.png" "$HOME/.local/share/icons/scriptfollower.png"
cat > "$HOME/.local/share/applications/scriptfollower.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Script Follower
Comment=Segue il parlato e fa scorrere il copione teatrale, con pulsanti OSC
Exec=$DEST/run.sh %f
Icon=$HOME/.local/share/icons/scriptfollower.png
Terminal=false
Categories=AudioVideo;Audio;
DESK
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
echo
echo "Fatto! Avvia «Script Follower» dal menu applicazioni oppure con:  scriptfollower"
