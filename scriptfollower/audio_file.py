"""Prova con una registrazione: un file audio al posto della scheda.

Il file viene letto a blocchi in tempo reale (o più veloce) e passa per la stessa catena dell'ingresso:
guadagno, meter, gate, preascolto e riconoscitore. Così si prova il copione con la registrazione di una prova
o di una replica, senza attori e senza microfono.

Formati: WAV, FLAC, MP3, OGG/Opus, AIFF… con libsndfile (incluso nel pacchetto «soundfile»); se sul computer
c'è ffmpeg, anche M4A/AAC e l'audio dei video (MP4, MOV, MKV…).
"""
import json
import os
import shutil
import subprocess
import threading
import time

import numpy as np
from PySide6.QtCore import QObject, Signal

from .system import IS_WIN
from .i18n import _

try:
    import soundfile as sf
except Exception:  # pragma: no cover - libsndfile mancante
    sf = None

AUDIO_EXT = (".wav", ".wave", ".flac", ".mp3", ".ogg", ".oga", ".opus", ".aif", ".aiff", ".aifc", ".caf", ".w64",
             ".rf64", ".m4a", ".aac", ".mp4", ".m4v", ".mov", ".mkv", ".webm", ".wma")
AUDIO_FILTER = ("File audio (*.wav *.flac *.mp3 *.ogg *.oga *.opus *.aif *.aiff *.caf *.m4a *.aac *.mp4 *.mov "
                "*.mkv *.webm *.wma);;Tutti i file (*)")
SPEEDS = (1.0, 1.5, 2.0, 3.0)
BLOCK_S = 0.02                       # 20 ms per blocco, come una scheda audio


def is_audio(path):
    return str(path).lower().endswith(AUDIO_EXT)


def _ffmpeg():
    return shutil.which("ffmpeg"), shutil.which("ffprobe")


def _popen_kw():
    return {"creationflags": 0x08000000} if IS_WIN else {}      # CREATE_NO_WINDOW


def fmt_time(sec):
    sec = max(0, int(sec))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class AudioFile:
    """Lettura a blocchi di un file audio: read(n) → float32 mono (mix dei canali o un canale)."""

    def __init__(self, path):
        self.path = path
        self.name = os.path.basename(path)
        self.channel = 0                 # 0 = mix di tutti i canali, altrimenti il canale (da 1)
        self._sf = None
        self._proc = None
        self.pos = 0                     # frame correnti
        err = None
        if sf is not None:
            try:
                self._sf = sf.SoundFile(path)
                self.sr, self.channels = int(self._sf.samplerate), int(self._sf.channels)
                self.frames = int(self._sf.frames) if self._sf.seekable() else 0
                self.backend = "libsndfile"
                return
            except Exception as e:  # noqa: BLE001
                err = e
        ff, probe = _ffmpeg()
        if ff and probe:
            info = self._probe(probe, path)
            if info:
                self.sr, self.channels, dur = info
                self.frames = int(dur * self.sr)
                self.backend = "ffmpeg"
                self._ff = ff
                return
        if err is not None and not (ff and probe):
            raise RuntimeError(_("Formato non letto ({0}). Converti il file in WAV, FLAC o MP3, oppure installa "
                                 "ffmpeg per leggere M4A, AAC e l'audio dei video.").format(err))
        raise RuntimeError(_("Impossibile leggere «{0}» come file audio.").format(self.name))

    @staticmethod
    def _probe(probe, path):
        try:
            out = subprocess.run([probe, "-v", "error", "-select_streams", "a:0", "-show_entries",
                                  "stream=sample_rate,channels:format=duration", "-of", "json", path],
                                 capture_output=True, timeout=20, **_popen_kw()).stdout
            d = json.loads(out or b"{}")
            st = (d.get("streams") or [{}])[0]
            sr, ch = int(st.get("sample_rate") or 0), int(st.get("channels") or 0)
            dur = float((d.get("format") or {}).get("duration") or 0)
            return (sr, ch, dur) if sr and ch else None
        except Exception:  # noqa: BLE001
            return None

    @property
    def duration(self):
        return self.frames / float(self.sr) if self.frames else 0.0

    @property
    def seconds(self):
        return self.pos / float(self.sr)

    def seek(self, sec):
        f = max(0, int(sec * self.sr))
        if self.frames:
            f = min(f, self.frames)
        self.pos = f
        if self._sf is not None:
            self._sf.seek(f)
        else:
            self._stop_ff()             # ffmpeg ripartirà da qui alla prossima lettura

    def _stop_ff(self):
        p, self._proc = self._proc, None
        if p is not None:
            try:
                p.kill()
                p.wait(timeout=2)
            except Exception:  # noqa: BLE001
                pass

    def _read_raw(self, n):
        if self._sf is not None:
            return self._sf.read(n, dtype="float32", always_2d=True)
        if self._proc is None:
            self._proc = subprocess.Popen(
                [self._ff, "-nostdin", "-v", "error", "-ss", f"{self.seconds:.3f}", "-i", self.path, "-vn",
                 "-f", "f32le", "-ac", str(self.channels), "-ar", str(self.sr), "-"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **_popen_kw())
        want = n * self.channels * 4
        data = b""
        while len(data) < want:
            chunk = self._proc.stdout.read(want - len(data))
            if not chunk:
                break
            data += chunk
        usable = len(data) // (self.channels * 4) * self.channels * 4
        return np.frombuffer(data[:usable], np.float32).reshape(-1, self.channels)

    def read(self, n):
        x = self._read_raw(n)
        self.pos += len(x)
        if not len(x):
            return np.zeros(0, np.float32)
        if self.channel and self.channel <= x.shape[1]:
            return np.ascontiguousarray(x[:, self.channel - 1], np.float32)
        return x.mean(axis=1).astype(np.float32)

    def close(self):
        self._stop_ff()
        if self._sf is not None:
            try:
                self._sf.close()
            except Exception:  # noqa: BLE001
                pass
            self._sf = None


class FilePlayer(QObject):
    """Riproduce un AudioFile dentro AudioInput (al posto della scheda), in tempo reale × velocità."""
    position = Signal(float)                # secondi (circa 10 volte al secondo)
    state = Signal(str)                     # "play", "pause", "end"
    error = Signal(str)

    def __init__(self, audio):
        super().__init__()
        self.audio = audio
        self.file = None
        self.speed = 1.0
        self.playing = False
        self._thread = None
        self._lock = threading.Lock()
        self._seek_to = None

    def load(self, path):
        f = AudioFile(path)                 # solleva un'eccezione se il file non si legge
        self.unload()
        self.file = f
        self.audio.use_file(f.sr, f.name)
        self.position.emit(0.0)
        return f

    def unload(self):
        self.pause()
        f, self.file = self.file, None
        if f is not None:
            f.close()

    def set_speed(self, s):
        self.speed = float(s) if s in SPEEDS else 1.0
        self.audio.set_file_speed(self.speed)

    def set_channel(self, ch):
        if self.file is not None:
            self.file.channel = int(ch)

    def seek(self, sec):
        if self.file is None:
            return
        with self._lock:
            if self.playing:
                self._seek_to = float(sec)
            else:
                self.file.seek(sec)
        self.position.emit(float(sec))

    def play(self):
        if self.file is None or self.playing:
            return
        if self.file.frames and self.file.pos >= self.file.frames - 1:
            self.file.seek(0)
        self.playing = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.state.emit("play")

    def pause(self):
        if not self.playing:
            return
        self.playing = False
        t, self._thread = self._thread, None
        if t is not None and t is not threading.current_thread():
            t.join(timeout=2)
        self.state.emit("pause")

    def _run(self):
        f = self.file
        n = max(1, int(f.sr * BLOCK_S))
        t0 = time.monotonic()
        sent = 0.0                          # secondi di file inviati da t0 (alla velocità scelta)
        last_pos = 0.0
        while self.playing:
            with self._lock:
                if self._seek_to is not None:
                    try:
                        f.seek(self._seek_to)
                    except Exception as e:  # noqa: BLE001
                        self.error.emit(str(e))
                    self._seek_to = None
                    t0, sent = time.monotonic(), 0.0
                try:
                    x = f.read(n)
                except Exception as e:  # noqa: BLE001
                    self.error.emit(_("Lettura del file non riuscita: {0}").format(e))
                    x = np.zeros(0, np.float32)
            if not len(x):
                self.playing = False
                self.position.emit(f.seconds)
                self.state.emit("end")
                return
            self.audio.process(x, f.sr)
            sent += len(x) / float(f.sr) / self.speed
            now = time.monotonic()
            if now - last_pos > 0.1:
                last_pos = now
                self.position.emit(f.seconds)
            ahead = t0 + sent - now
            if ahead > 0:
                time.sleep(ahead)
            elif ahead < -0.5:              # computer in ritardo: non si recupera tutto di colpo
                t0, sent = now, 0.0
