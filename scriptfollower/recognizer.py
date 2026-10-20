"""Riconoscimento vocale offline con Vosk (Kaldi), in un thread separato.

Il vocabolario può essere limitato alle parole del copione (grammatica): con i modelli «small» il
riconoscimento diventa molto più affidabile, perché può produrre solo parole che stanno nel testo.
Il modello si cerca, nell'ordine, in: $SCRIPTFOLLOWER_MODEL, «models/» dentro il pacchetto, «models/»
accanto al programma (uso portable, anche su chiavetta) e «models/» nella cartella dei dati.
"""
import json
import os
import queue
import shutil
import threading
import time
import urllib.request
import zipfile
from collections import deque

from PySide6.QtCore import QObject, Signal

from .align import normalize
from .system import app_dir, bundle_dir, data_dir

try:
    import vosk
    vosk.SetLogLevel(-1)
except Exception:  # pragma: no cover
    vosk = None

MODEL_NAME = "vosk-model-small-it-0.22"
MODEL_URL = f"https://alphacephei.com/vosk/models/{MODEL_NAME}.zip"
MODEL_SIZE_MB = 48


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


def is_model(path):
    return os.path.isdir(path) and (os.path.isdir(os.path.join(path, "am"))
                                    or os.path.isfile(os.path.join(path, "conf", "model.conf")))


def list_models():
    """[(nome, percorso)] dei modelli trovati, senza doppioni."""
    out, seen = [], set()
    for d in model_dirs():
        cands = [d] if is_model(d) else []
        if os.path.isdir(d) and not cands:
            cands = [os.path.join(d, n) for n in sorted(os.listdir(d))]
        for c in cands:
            if is_model(c):
                rp = os.path.realpath(c)
                if rp not in seen:
                    seen.add(rp)
                    out.append((os.path.basename(c.rstrip("/\\")), c))
    return out


def find_model(preferred=""):
    if preferred and is_model(preferred):
        return preferred
    ms = list_models()
    for name, path in ms:                # prima i modelli italiani
        if "-it" in name:
            return path
    return ms[0][1] if ms else ""


def download_model(progress=None, dest=None, url=MODEL_URL):
    """Scarica ed estrae il modello italiano. progress(scaricati, totali). Ritorna il percorso."""
    dest = dest or data_dir("models")
    os.makedirs(dest, exist_ok=True)
    zpath = os.path.join(dest, MODEL_NAME + ".zip.part")
    with urllib.request.urlopen(url, timeout=30) as r, open(zpath, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    tmp = os.path.join(dest, ".estrazione")
    shutil.rmtree(tmp, ignore_errors=True)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(tmp)
    os.remove(zpath)
    inner = [os.path.join(tmp, n) for n in os.listdir(tmp)]
    src = next((p for p in inner if is_model(p)), tmp)
    final = os.path.join(dest, MODEL_NAME)
    shutil.rmtree(final, ignore_errors=True)
    shutil.move(src, final)
    shutil.rmtree(tmp, ignore_errors=True)
    if not is_model(final):
        raise RuntimeError("l'archivio scaricato non contiene un modello Vosk")
    return final


def model_vocabulary(path):
    """Parole conosciute dal modello (se il modello ne ha l'elenco), altrimenti None."""
    for rel in (("graph", "words.txt"), ("words.txt",)):
        p = os.path.join(path, *rel)
        if os.path.isfile(p):
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    return {ln.split()[0] for ln in f if ln.strip()}
            except OSError:
                return None
    return None


class Recognizer(QObject):
    """Riceve l'audio (int16, 16 kHz) e produce le ultime parole sentite.

    hypothesis(parole normalizzate recenti, testo sentito) arriva più volte al secondo."""
    hypothesis = Signal(list, str)
    status = Signal(str)
    ready = Signal(bool)
    unknown = Signal(object)                     # set delle parole del vocabolario che il modello non conosce

    def __init__(self):
        super().__init__()
        self.q = queue.Queue(maxsize=400)
        self.thread = None
        self.running = False
        self.model = None
        self.model_path = ""
        self.history = deque(maxlen=24)          # ultime parole definitive
        self.raw = deque(maxlen=40)              # testo sentito (per la finestra «Sentito»)
        self._reset = False
        self._vocab = None                       # nuovo vocabolario da applicare (dal thread)
        self._model_lock = threading.Lock()

    def available(self):
        return vosk is not None

    def load_model(self, model_path):
        """Carica il modello (una volta sola, anche da un altro thread) e lo restituisce."""
        with self._model_lock:
            if self.model is None or self.model_path != model_path:
                self.status.emit(f"Carico il modello vocale «{os.path.basename(model_path)}»…")
                self.model = vosk.Model(model_path)
                self.model_path = model_path
            return self.model

    @staticmethod
    def known_fn(model):
        """Funzione parola -> True se il modello la conosce."""
        cache = {}

        def known(w):
            v = cache.get(w)
            if v is None:
                try:
                    v = cache[w] = model.vosk_model_find_word(w) >= 0
                except Exception:  # noqa: BLE001
                    v = cache[w] = True                # non si sa: meglio non escluderla
            return v
        return known

    def start(self, model_path, vocabulary=None):
        """Carica il modello (se serve) e inizia ad ascoltare. vocabulary = parole ammesse o None."""
        self.stop()
        if vosk is None:
            self.status.emit("Vosk non installato: riconoscimento vocale non disponibile")
            self.ready.emit(False)
            return
        self.running = True
        self.thread = threading.Thread(target=self._run, args=(model_path, vocabulary), daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        t, self.thread = self.thread, None
        if t is not None:
            try:
                self.q.put_nowait(None)
            except queue.Full:
                pass
            t.join(timeout=2)
        while not self.q.empty():
            try:
                self.q.get_nowait()
            except queue.Empty:
                break

    def feed(self, data):
        """Chiamata dal thread audio."""
        if not self.running:
            return
        try:
            self.q.put_nowait(data)
        except queue.Full:                       # il riconoscitore è in ritardo: si scarta il più vecchio
            try:
                self.q.get_nowait()
                self.q.put_nowait(data)
            except (queue.Empty, queue.Full):
                pass

    def clear_history(self):
        self.history.clear()

    def set_vocabulary(self, vocabulary):
        """Cambia le parole ammesse senza ricaricare il modello (si applica al prossimo blocco audio)."""
        if self.running:
            self._vocab = list(vocabulary)

    def flush(self):
        """Dimentica l'audio in coda e la frase in corso (dopo una sospensione dell'ascolto)."""
        while not self.q.empty():
            try:
                self.q.get_nowait()
            except queue.Empty:
                break
        self.history.clear()
        self._reset = True

    def _run(self, model_path, vocabulary):
        try:
            self.load_model(model_path)
            if vocabulary:
                known = self.known_fn(self.model)
                self.unknown.emit({w for w in vocabulary if "'" not in w and not known(w)})
                rec = vosk.KaldiRecognizer(self.model, 16000, json.dumps(list(vocabulary) + ["[unk]"],
                                                                        ensure_ascii=False))
            else:
                rec = vosk.KaldiRecognizer(self.model, 16000)
        except Exception as e:  # noqa: BLE001
            self.running = False
            self.status.emit(f"Modello vocale non caricato: {e}")
            self.ready.emit(False)
            return
        self.ready.emit(True)
        self.status.emit("In ascolto" + (" (vocabolario del copione)" if vocabulary else ""))
        last_partial = 0.0
        partial_words = []
        while self.running:
            try:
                data = self.q.get(timeout=0.3)
            except queue.Empty:
                continue
            if data is None:
                break
            if self._vocab is not None:
                voc, self._vocab = self._vocab, None
                try:
                    rec = vosk.KaldiRecognizer(self.model, 16000,
                                               json.dumps(voc + ["[unk]"], ensure_ascii=False))
                    partial_words = []
                except Exception as e:  # noqa: BLE001
                    self.status.emit(f"Vocabolario non aggiornato: {e}")
            if self._reset:
                self._reset = False
                partial_words = []
                try:
                    rec.Reset()
                except Exception:  # noqa: BLE001
                    pass
            try:
                if rec.AcceptWaveform(data):
                    text = json.loads(rec.Result()).get("text", "")
                    words = [w for w in normalize(text) if w != "unk"]
                    if words:
                        self.history.extend(words)
                        self.raw.append(text)
                    partial_words = []
                    self.hypothesis.emit(list(self.history), text)
                else:
                    now = time.monotonic()
                    if now - last_partial >= 0.12:
                        last_partial = now
                        text = json.loads(rec.PartialResult()).get("partial", "")
                        words = [w for w in normalize(text) if w != "unk"]
                        if words != partial_words:
                            partial_words = words
                            self.hypothesis.emit(list(self.history) + words, text)
            except Exception as e:  # noqa: BLE001
                self.status.emit(f"Errore del riconoscitore: {e}")
                time.sleep(0.2)
        self.running = False
