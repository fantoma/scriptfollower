"""Riconoscimento vocale offline con Vosk (Kaldi), in un thread separato.

Il vocabolario può essere limitato alle parole del copione (grammatica): con i modelli «small» il
riconoscimento diventa molto più affidabile, perché può produrre solo parole che stanno nel testo.
Il modello si cerca, nell'ordine, in: $SCRIPTFOLLOWER_MODEL, «models/» dentro il pacchetto, «models/»
accanto al programma (uso portable, anche su chiavetta) e «models/» nella cartella dei dati.
"""
import json
import os
import queue
import threading
import time
from collections import deque

import numpy as np

from PySide6.QtCore import QObject, Signal

from .align import normalize

try:
    import vosk
    vosk.SetLogLevel(-1)
except Exception:  # pragma: no cover
    vosk = None

from .languages import download as _download  # noqa: E402
from .languages import find_model as _find_model  # noqa: E402
from .languages import is_any_model as is_model  # noqa: E402
from .languages import list_models, model_dirs  # noqa: E402
from .i18n import _

__all__ = ["Recognizer", "find_model", "download_model", "is_model", "list_models", "model_dirs",
           "MODEL_NAME", "MODEL_SIZE_MB"]

MODEL_NAME = "vosk-model-small-it-0.22"
MODEL_SIZE_MB = 48


def find_model(preferred="", lang="it"):
    return _find_model(lang, preferred)


def download_model(progress=None, dest=None, lang="it"):
    return _download(lang, progress, dest)


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


WHISPER_WINDOW = 6.0                             # secondi di audio trascritti ogni volta
WHISPER_STEP = 0.8                               # ogni quanto si trascrive


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
        self._prompt = ""                        # Whisper: testo detto prima
        self._model_lock = threading.Lock()

    def available(self):
        return vosk is not None

    def load_model(self, model_path):
        """Carica il modello (una volta sola, anche da un altro thread) e lo restituisce."""
        with self._model_lock:
            if self.model is None or self.model_path != model_path:
                self.status.emit(_('Carico il modello vocale «{0}»…').format(os.path.basename(model_path)))
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

    def start(self, model_path, vocabulary=None, engine="vosk", language="it"):
        """Carica il modello (se serve) e inizia ad ascoltare. vocabulary = parole ammesse o None."""
        self.stop()
        if engine == "vosk" and vosk is None:
            self.status.emit(_('Vosk non installato: riconoscimento vocale non disponibile'))
            self.ready.emit(False)
            return
        self.running = True
        target = self._run_whisper if engine == "whisper" else self._run
        args = (model_path, language) if engine == "whisper" else (model_path, vocabulary)
        self.thread = threading.Thread(target=target, args=args, daemon=True)
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
            self.status.emit(_('Modello vocale non caricato: {0}').format(e))
            self.ready.emit(False)
            return
        self.ready.emit(True)
        self.status.emit(_("In ascolto (vocabolario del copione)") if vocabulary else _("In ascolto"))
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
                    self.status.emit(_('Vocabolario non aggiornato: {0}').format(e))
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
                self.status.emit(_('Errore del riconoscitore: {0}').format(e))
                time.sleep(0.2)
        self.running = False

    # ------------------------------------------------------------ Whisper (finestra mobile)
    def set_prompt(self, text):
        """Testo del copione detto fino a qui: aiuta Whisper con lo stile e i nomi propri."""
        self._prompt = text or ""

    def _run_whisper(self, model_path, language):
        from .whisper_engine import WhisperEngine
        try:
            with self._model_lock:
                if not isinstance(self.model, WhisperEngine) or self.model_path != model_path:
                    self.status.emit(_('Carico il modello vocale «{0}»…').format(os.path.basename(model_path)))
                    self.model = WhisperEngine(model_path)
                    self.model_path = model_path
            eng = self.model
        except Exception as e:  # noqa: BLE001
            self.running = False
            self.status.emit(_('Modello vocale non caricato: {0}').format(e))
            self.ready.emit(False)
            return
        self.ready.emit(True)
        self.status.emit(_('In ascolto (Whisper)'))
        win = int(WHISPER_WINDOW * 16000)
        buf = np.zeros(0, np.float32)
        new = 0                                  # campioni arrivati dall'ultima trascrizione
        last = time.monotonic()
        while self.running:
            try:
                data = self.q.get(timeout=0.1)
            except queue.Empty:
                data = b""
            if data is None:
                break
            if self._reset:
                self._reset = False
                buf = np.zeros(0, np.float32)
                new = 0
            if data:
                x = np.frombuffer(data, np.int16).astype(np.float32) / 32768.0
                buf = np.concatenate((buf, x))[-win:]
                new += len(x)
            now = time.monotonic()
            if new < 16000 * WHISPER_STEP or now - last < WHISPER_STEP:
                continue
            last = now
            new = 0
            voiced = np.abs(buf) > 1e-3                          # dopo il gate: zeri = silenzio
            if voiced.mean() < 0.12:
                continue
            first = int(np.argmax(voiced))                       # niente silenzio iniziale
            try:
                text = eng.transcribe(buf[max(0, first - 1600):], language, self._prompt)
            except Exception as e:  # noqa: BLE001
                self.status.emit(_('Errore del riconoscitore: {0}').format(e))
                time.sleep(0.3)
                continue
            words = normalize(text)
            if words:
                self.raw.append(text)
                self.hypothesis.emit(words[-24:], text)
            # svuota la coda accumulata durante la trascrizione (si resta in tempo reale)
            while self.q.qsize() > 40:
                try:
                    self.q.get_nowait()
                except queue.Empty:
                    break
        self.running = False
