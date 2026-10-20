# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller: Script Follower per Windows (cartella portable con ScriptFollower.exe) e macOS (.app).

    pyinstaller packaging/scriptfollower.spec --noconfirm        (dalla cartella del progetto)

Variabili d'ambiente:
    SCRIPTFOLLOWER_MODEL_DIR   cartella di un modello Vosk da includere (finisce in «models/»)
    SCRIPTFOLLOWER_MAC_ARCH    arm64 o x86_64 (solo macOS; predefinita: quella del Python usato)
"""
import glob
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
PKG = os.path.join(ROOT, "scriptfollower")
sys.path.insert(0, ROOT)
from scriptfollower import __version__ as VERSION  # noqa: E402

IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"

# ---- file non Python del pacchetto (icona, plugin OSC in JSON)
datas = []
for path in glob.glob(os.path.join(PKG, "**", "*"), recursive=True):
    if os.path.isdir(path) or "__pycache__" in path or path.endswith((".py", ".pyc")):
        continue
    datas.append((path, os.path.dirname(os.path.relpath(path, ROOT))))

# ---- Vosk: libvosk (.so/.dll/.dyld) e le sue dipendenze stanno nella cartella del pacchetto
datas += collect_data_files("vosk")
binaries = collect_dynamic_libs("vosk")

# ---- modello vocale incluso (facoltativo)
model = os.environ.get("SCRIPTFOLLOWER_MODEL_DIR")
if model and os.path.isdir(model):
    name = os.path.basename(model.rstrip("/\\"))
    for path in glob.glob(os.path.join(model, "**", "*"), recursive=True):
        if os.path.isfile(path):
            datas.append((path, os.path.join("models", name, os.path.dirname(os.path.relpath(path, model)))))

hidden = set(collect_submodules("scriptfollower")) | set(collect_submodules("pymupdf")) | {"vosk", "_cffi_backend"}

a = Analysis(
    [os.path.join(SPECPATH, "launch.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(hidden),
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQuick3D",
              "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtPdf",
              "PySide6.QtDesigner", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtMultimedia",
              "matplotlib", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ScriptFollower",
    console=False,                 # app a finestra (il log va in scriptfollower.log nella cartella dei dati)
    icon=os.path.join(SPECPATH, "scriptfollower.ico" if IS_WIN else "scriptfollower.icns"),
    target_arch=os.environ.get("SCRIPTFOLLOWER_MAC_ARCH") or None,
    codesign_identity=os.environ.get("SCRIPTFOLLOWER_CODESIGN_ID") or None,
    entitlements_file=os.path.join(SPECPATH, "macos", "entitlements.plist") if IS_MAC else None,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="ScriptFollower", upx=False)

if IS_MAC:
    app = BUNDLE(
        coll,
        name="Script Follower.app",
        icon=os.path.join(SPECPATH, "scriptfollower.icns"),
        bundle_identifier="org.cuestage.ScriptFollower",
        version=VERSION,
        info_plist={
            "CFBundleName": "Script Follower",
            "CFBundleDisplayName": "Script Follower",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "12.0",
            "NSMicrophoneUsageDescription": "Script Follower ascolta le voci degli attori per seguire il copione.",
            "NSLocalNetworkUsageDescription": "Script Follower invia comandi OSC a CueStage, QLab, console luci e "
                                              "altri dispositivi sulla rete locale.",
            "CFBundleDocumentTypes": [{
                "CFBundleTypeName": "Progetto Script Follower",
                "CFBundleTypeRole": "Editor",
                "LSHandlerRank": "Owner",
                "CFBundleTypeExtensions": ["sfollow"],
            }, {
                "CFBundleTypeName": "Copione PDF",
                "CFBundleTypeRole": "Viewer",
                "LSHandlerRank": "Alternate",
                "CFBundleTypeExtensions": ["pdf"],
            }],
        },
    )
