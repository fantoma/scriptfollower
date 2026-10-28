"""Lingue del copione e modelli vocali.

Ogni lingua ha un motore e un modello:
- italiano e inglese: Vosk (modelli «small», vocabolario limitabile alle parole del copione);
- sloveno: Vosk non ha un modello sloveno, quindi Whisper (CTranslate2, modello «small» multilingue).

Nei pacchetti portable è incluso solo il modello italiano; gli altri si scaricano dal menu Lingua › Lingue del
riconoscimento. Il download va in «models/» accanto al programma se si può scrivere lì (uso portable, anche da
chiavetta), altrimenti nella cartella dei dati.
"""
import os
import shutil
import urllib.request
import zipfile

from .system import app_dir, bundle_dir, data_dir
from .i18n import _

LANGS = {
    "it": {"name": "Italiano", "engine": "vosk", "model": "vosk-model-small-it-0.22", "mb": 48,
           "url": "https://alphacephei.com/vosk/models/vosk-model-small-it-0.22.zip"},
    "en": {"name": "English", "engine": "vosk", "model": "vosk-model-small-en-us-0.15", "mb": 40,
           "url": "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"},
    "sl": {"name": "Slovenščina", "engine": "whisper", "model": "faster-whisper-small", "mb": 484,
           "hf": "Systran/faster-whisper-small",
           "files": ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"]},
}
ORDER = ["it", "en", "sl"]


def model_dirs():
    dirs = []
    env = os.environ.get("SCRIPTFOLLOWER_MODEL")
    if env:
        dirs.append(env)
    b = bundle_dir()
    if b:
        dirs.append(os.path.join(b, "models"))
    dirs.append(os.path.join(app_dir(), "models"))
    dirs.append(data_dir("models"))
    return list(dict.fromkeys(dirs))


def is_vosk_model(path):
    return os.path.isdir(path) and (os.path.isdir(os.path.join(path, "am"))
                                    or os.path.isfile(os.path.join(path, "conf", "model.conf")))


def is_any_model(path):
    from .whisper_engine import is_whisper_model
    return is_vosk_model(path) or is_whisper_model(path)


def list_models():
    """[(nome, percorso)] dei modelli trovati (Vosk e Whisper), senza doppioni."""
    out, seen = [], set()
    for d in model_dirs():
        cands = [d] if is_any_model(d) else []
        if os.path.isdir(d) and not cands:
            cands = [os.path.join(d, n) for n in sorted(os.listdir(d))]
        for c in cands:
            if is_any_model(c):
                rp = os.path.realpath(c)
                if rp not in seen:
                    seen.add(rp)
                    out.append((os.path.basename(c.rstrip("/\\")), c))
    return out


def model_lang(name):
    """Lingua di un modello dal nome della cartella (None se non si capisce)."""
    n = name.lower()
    for code, info in LANGS.items():
        if n == info["model"].lower():
            return code
    if "whisper" in n:
        return "sl"
    for code in LANGS:
        if f"-{code}-" in n or n.endswith(f"-{code}"):
            return code
    return None


def find_model(lang="it", preferred=""):
    """Cartella del modello per la lingua (preferred = cartella scelta a mano, se valida)."""
    if preferred and is_any_model(preferred):
        return preferred
    ms = list_models()
    want = LANGS.get(lang, LANGS["it"])
    for name, path in ms:
        if name == want["model"]:
            return path
    for name, path in ms:
        if model_lang(name) == lang and (want["engine"] == "whisper") == ("whisper" in name.lower()):
            return path
    return ""


def installed(lang):
    return bool(find_model(lang))


def download_dir():
    """«models» accanto al programma se scrivibile (portable), altrimenti nella cartella dei dati."""
    d = os.path.join(app_dir(), "models")
    try:
        os.makedirs(d, exist_ok=True)
        test = os.path.join(d, ".scrittura")
        with open(test, "w") as f:
            f.write("ok")
        os.remove(test)
        return d
    except OSError:
        return data_dir("models")


def _fetch(url, dest, progress=None, base=0, total_all=0):
    with urllib.request.urlopen(url, timeout=60) as r, open(dest + ".part", "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(base + done, total_all or total)
    os.replace(dest + ".part", dest)
    return done


def download(lang, progress=None, dest=None):
    """Scarica il modello della lingua. progress(scaricati, totali) in byte. Ritorna la cartella."""
    info = LANGS[lang]
    dest = dest or download_dir()
    os.makedirs(dest, exist_ok=True)
    final = os.path.join(dest, info["model"])
    if info["engine"] == "whisper":
        tmp = final + ".part"
        os.makedirs(tmp, exist_ok=True)
        base = 0
        total = info["mb"] * 1_000_000
        for fn in info["files"]:
            url = f"https://huggingface.co/{info['hf']}/resolve/main/{fn}"
            base += _fetch(url, os.path.join(tmp, fn), progress, base, total)
        shutil.rmtree(final, ignore_errors=True)
        os.replace(tmp, final)
        return final
    zpath = os.path.join(dest, info["model"] + ".zip")
    _fetch(info["url"], zpath, progress)
    tmp = os.path.join(dest, ".estrazione")
    shutil.rmtree(tmp, ignore_errors=True)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(tmp)
    os.remove(zpath)
    inner = [os.path.join(tmp, n) for n in os.listdir(tmp)]
    src = next((p for p in inner if is_vosk_model(p)), tmp)
    shutil.rmtree(final, ignore_errors=True)
    shutil.move(src, final)
    shutil.rmtree(tmp, ignore_errors=True)
    if not is_vosk_model(final):
        raise RuntimeError(_("l'archivio scaricato non contiene un modello Vosk"))
    return final


def remove(lang):
    """Toglie il modello scaricato della lingua (non quello incluso nel pacchetto)."""
    path = find_model(lang)
    b = bundle_dir()
    if not path or (b and os.path.realpath(path).startswith(os.path.realpath(b))):
        return False
    shutil.rmtree(path, ignore_errors=True)
    return True
