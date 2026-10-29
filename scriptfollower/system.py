"""Differenze fra sistemi operativi (Linux, Windows, macOS) raccolte in un solo posto (come in CueStage)."""
import os
import sys

IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")
OS_NAME = "Windows" if IS_WIN else "macOS" if IS_MAC else "Linux"
APP_NAME = "Script Follower"


def data_dir(*sub):
    """Cartella dei dati utente (modelli vocali scaricati, profili OSC aggiunti, log).

    Linux:   ~/.local/share/scriptfollower
    Windows: %APPDATA%\\ScriptFollower
    macOS:   ~/Library/Application Support/ScriptFollower
    """
    if os.environ.get("SCRIPTFOLLOWER_DATA"):            # test e usi particolari: un'altra cartella
        base = os.environ["SCRIPTFOLLOWER_DATA"]
    elif IS_WIN:
        base = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~\\AppData\\Roaming"), "ScriptFollower")
    elif IS_MAC:
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "ScriptFollower")
    else:
        base = os.path.join(os.path.expanduser("~"), ".local", "share", "scriptfollower")
    return os.path.join(base, *sub)


def data_dir_label(*sub):
    """Percorso da mostrare all'utente (con ~ al posto della cartella personale)."""
    p = data_dir(*sub)
    home = os.path.expanduser("~")
    return "~" + p[len(home):] if p.startswith(home) else p


def bundle_dir():
    """Cartella del programma impacchettato (PyInstaller/AppImage), oppure None se avviato dai sorgenti."""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.environ.get("SCRIPTFOLLOWER_APPIMAGE_DIR") or None


def app_dir():
    """Cartella accanto al programma: dove sta il file .AppImage, l'exe portable o l'app (per «models/»)."""
    appimage = os.environ.get("APPIMAGE")              # impostata dal runtime dell'AppImage
    if appimage:
        return os.path.dirname(os.path.abspath(appimage))
    if getattr(sys, "frozen", False):
        d = os.path.dirname(os.path.abspath(sys.executable))
        if IS_MAC and d.endswith(os.path.join("Contents", "MacOS")):
            return os.path.dirname(os.path.dirname(os.path.dirname(d)))     # cartella che contiene la .app
        return d
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # cartella del progetto
