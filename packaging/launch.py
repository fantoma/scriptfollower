"""Avvio di Script Follower impacchettato (AppImage su Linux, PyInstaller su Windows e macOS).

    ScriptFollower [copione.pdf | progetto.sfollow]   avvia il programma
    ScriptFollower --version                          versione
    ScriptFollower --self-test [file]                 verifica che tutte le parti incluse funzionino
                                                      (su Windows il risultato va anche in «file»)
"""
import ctypes.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FROZEN = getattr(sys, "frozen", False)                 # PyInstaller
if not FROZEN:      # AppImage: scriptfollower/ accanto a questo file; sorgenti: nella cartella superiore
    sys.path.insert(0, HERE if os.path.isdir(os.path.join(HERE, "scriptfollower")) else os.path.dirname(HERE))

# AppImage: sounddevice cerca PortAudio con ctypes.util.find_library, gli indichiamo quella inclusa
_TOP = os.environ.get("SCRIPTFOLLOWER_APPIMAGE_DIR") or os.path.dirname(os.path.dirname(HERE))
PORTAUDIO = os.path.join(_TOP, "usr", "lib", "libportaudio.so.2")
if not FROZEN and os.path.exists(PORTAUDIO):
    _find = ctypes.util.find_library

    def _find_library(name):
        return PORTAUDIO if name == "portaudio" else _find(name)

    ctypes.util.find_library = _find_library


def _log_without_console():
    """App a finestra su Windows/macOS: senza console stdout/stderr sono None e un print farebbe cadere
    il programma. Li mandiamo in un file di log nella cartella dei dati."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    from scriptfollower.system import data_dir
    try:
        os.makedirs(data_dir(), exist_ok=True)
        f = open(data_dir("scriptfollower.log"), "a", encoding="utf-8", buffering=1)
    except OSError:
        f = open(os.devnull, "w")
    sys.stdout = sys.stdout or f
    sys.stderr = sys.stderr or f


def self_test(report=None):
    import time
    from scriptfollower.system import IS_WIN, OS_NAME
    lines = []
    ok = True

    def out(s):
        lines.append(s)
        try:
            print(s, flush=True)
        except Exception:
            pass

    def step(name, fn, optional=False):
        nonlocal ok
        try:
            out(f"  ✔ {name}: {fn()}")
        except Exception as e:  # noqa: BLE001
            ok = ok and optional
            out(f"  {'!' if optional else '✘'} {name}: {type(e).__name__}: {e}")

    import scriptfollower
    out(f"Script Follower {scriptfollower.__version__} — {OS_NAME} — Python {sys.version.split()[0]}")
    step("PySide6", lambda: __import__("PySide6").__version__)
    step("numpy", lambda: __import__("numpy").__version__)

    def pa():
        import sounddevice as sd
        apis = ", ".join(a["name"] for a in sd.query_hostapis())
        return f"{sd.get_portaudio_version()[1]}, {len(sd.query_devices())} dispositivi ({apis})"
    step("PortAudio", pa)
    step("python-osc", lambda: (__import__("pythonosc.udp_client", fromlist=["x"]), "ok")[1])

    def mupdf():
        from scriptfollower.script_doc import pymupdf
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Prova del copione: ciao ciao")
        n = len(page.get_text("words"))
        if n != 5:
            raise RuntimeError(f"{n} parole invece di 5")
        return f"PyMuPDF {pymupdf.VersionBind}"
    step("PDF", mupdf)

    def vosk():
        import vosk as v
        v.SetLogLevel(-1)
        return "libvosk caricata"
    step("Vosk", vosk)

    def whisper():
        import ctranslate2
        import tokenizers
        from scriptfollower.whisper_engine import log_mel
        m = log_mel([0.0] * 16000)
        if m.shape != (80, 3000):
            raise RuntimeError(f"log-mel {m.shape}")
        return f"CTranslate2 {ctranslate2.__version__}, tokenizers {tokenizers.__version__}"
    step("Whisper (sloveno)", whisper)

    def audiofile():
        import tempfile
        import numpy as np
        import soundfile as sf
        from scriptfollower.audio_file import AudioFile
        d = tempfile.mkdtemp(prefix="sf-selftest-")
        out = []
        for ext in ("wav", "flac", "mp3"):
            p = os.path.join(d, "prova." + ext)
            sf.write(p, (0.3 * np.sin(np.arange(16000) / 5)).astype(np.float32), 16000)
            f = AudioFile(p)
            n = len(f.read(8000))
            f.close()
            if n != 8000:
                raise RuntimeError(f"{ext}: {n} campioni")
            out.append(ext)
        return f"libsndfile {sf.__libsndfile_version__}: " + ", ".join(out)
    step("file audio", audiofile)

    def languages():
        from scriptfollower.i18n import set_language, _
        for code in ("en", "sl"):
            set_language(code)
            if _("Impostazioni") == "Impostazioni":
                raise RuntimeError(f"catalogo {code} non caricato")
        set_language("it")
        return "it, en, sl"
    step("lingue dell'interfaccia", languages)

    def model():
        from scriptfollower.recognizer import find_model, model_dirs
        m = find_model()
        if not m:
            raise FileNotFoundError("nessun modello in " + " · ".join(model_dirs()))
        import vosk as v
        v.SetLogLevel(-1)
        mod = v.Model(m)
        rec = v.KaldiRecognizer(mod, 16000, '["ciao", "mamma", "[unk]"]')
        rec.AcceptWaveform(b"\x00\x00" * 16000)
        return f"{os.path.basename(m)} caricato"
    step("modello vocale", model, optional=not os.environ.get("SCRIPTFOLLOWER_REQUIRE_MODEL"))

    def plugins():
        from scriptfollower.osc import plugins as pl
        p = pl(reload=True)
        need = {"cuestage", "qlab", "scs", "chamsys_magicq", "generico"}
        if not need <= set(p):
            raise RuntimeError(f"mancano {need - set(p)}")
        return ", ".join(sorted(p))
    step("plugin OSC", plugins)

    def window():
        from PySide6.QtWidgets import QApplication
        from scriptfollower.main_window import MainWindow
        app = QApplication.instance() or QApplication([])
        w = MainWindow()
        w.show()
        t0 = time.monotonic()
        while time.monotonic() - t0 < 0.5:
            app.processEvents()
        w.project.dirty = False
        w.close()
        return "finestra principale creata"
    step("interfaccia", window)
    out("tutto bene" if ok else "ci sono problemi")
    if report or (IS_WIN and FROZEN):
        path = report or os.path.join(os.getcwd(), "scriptfollower-self-test.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    return 0 if ok else 1


def main():
    args = sys.argv[1:]
    if args[:1] == ["--version"]:
        import scriptfollower
        print("Script Follower", scriptfollower.__version__)
        return 0
    if args[:1] == ["--self-test"]:
        if sys.stdout is None:
            sys.stdout = sys.stderr = open(os.devnull, "w")
        return self_test(args[1] if len(args) > 1 else None)
    _log_without_console()
    args = [a for a in args if not a.startswith("-psn_")]      # macOS: argomento aggiunto dal Finder
    sys.argv = ["scriptfollower"] + args
    from scriptfollower.__main__ import main as run
    return run()


if __name__ == "__main__":
    sys.exit(main())
