"""Ingresso audio: scheda e canale di ascolto, gate (soglia + hold), livelli per il meter.

Il segnale del canale scelto passa per il gate: sotto la soglia (dopo il tempo di hold) al riconoscitore
arriva silenzio, così rumori di fondo, musica bassa e respiri non diventano parole. Il riconoscitore riceve
audio mono a 16 kHz (int16).
"""
import math
import re
import threading

import numpy as np

from .system import IS_WIN
from .i18n import _

try:
    import sounddevice as sd
except Exception:  # PortAudio mancante
    sd = None

ASR_RATE = 16000
MIN_METER_DB = -70.0
WIN_APIS = ("Windows WASAPI", "ASIO")
AUDIO_DEFAULTS = {
    "device": "",            # scheda in ingresso ("" = predefinita di sistema)
    "channel": 1,            # canale della scheda (da 1)
    "gate_on": True,         # usa il gate
    "threshold_db": -45.0,   # soglia del gate (dBFS)
    "hold_ms": 400,          # resta aperto dopo che il segnale è sceso sotto la soglia
    "gain_db": 0.0,          # guadagno digitale prima del riconoscitore
    # preascolto (cuffia) dell'ingresso su una scheda dedicata, come il preascolto di CueStage
    "mon_on": False,
    "mon_device": "",        # scheda in uscita ("" = predefinita di sistema)
    "mon_channels": "1-2",   # «1», «2», «1-2», «3-4»…: canali dell'uscita (lo stesso segnale mono su tutti)
    "mon_db": 0.0,           # volume del preascolto
    "mon_post": True,        # dopo il gate (si sente ciò che arriva al riconoscitore) o prima
}


def audio_settings(d):
    s = dict(AUDIO_DEFAULTS)
    s.update({k: v for k, v in (d or {}).items() if k in AUDIO_DEFAULTS})
    s["device"] = str(s["device"] or "")
    s["channel"] = max(1, int(s["channel"] or 1))
    s["gate_on"] = bool(s["gate_on"]) and s["gate_on"] not in ("false", "0")
    s["threshold_db"] = max(-90.0, min(0.0, float(s["threshold_db"])))
    s["hold_ms"] = max(0, min(3000, int(s["hold_ms"])))
    s["gain_db"] = max(-12.0, min(30.0, float(s["gain_db"])))
    s["mon_on"] = s["mon_on"] not in (False, "false", "0", 0, None)
    s["mon_post"] = s["mon_post"] not in (False, "false", "0", 0, None)
    s["mon_device"] = str(s["mon_device"] or "")
    s["mon_db"] = max(-60.0, min(12.0, float(s["mon_db"])))
    s["mon_channels"] = "-".join(str(c) for c in parse_channels(s["mon_channels"]))
    return s


def parse_channels(text):
    """'1-2' -> [1, 2] · '3' -> [3] · valori strani -> [1, 2]."""
    out = []
    for part in str(text or "").replace("+", "-").split("-"):
        try:
            c = int(part)
        except ValueError:
            continue
        if 1 <= c <= 64 and c not in out:
            out.append(c)
    return out[:2] or [1, 2]


def list_output_devices():
    """Schede con uscite: [{id, name, hostapi, index, max_ch}]."""
    if sd is None:
        return []
    try:
        apis = sd.query_hostapis()
        out = []
        for i, d in enumerate(sd.query_devices()):
            if d.get("max_output_channels", 0) <= 0:
                continue
            api = apis[d["hostapi"]]["name"]
            out.append({"id": f"{api}|{_base_name(d['name'])}", "name": d["name"], "hostapi": api, "index": i,
                        "max_ch": int(d["max_output_channels"])})
        if IS_WIN:
            good = [d for d in out if d["hostapi"] in WIN_APIS]
            out = good or out
        return out
    except Exception:
        return []


def lin_to_db(x):
    return 20.0 * math.log10(x) if x > 1e-6 else -120.0


def _base_name(name):
    return re.sub(r"\s*\((hw|plughw):[^)]*\)\s*$", "", name).strip()


def list_input_devices():
    """Schede con ingressi: [{id, name, hostapi, index, max_ch}] (id stabile: «API|nome»)."""
    if sd is None:
        return []
    try:
        apis = sd.query_hostapis()
        out = []
        for i, d in enumerate(sd.query_devices()):
            if d.get("max_input_channels", 0) <= 0:
                continue
            api = apis[d["hostapi"]]["name"]
            out.append({"id": f"{api}|{_base_name(d['name'])}", "name": d["name"], "hostapi": api, "index": i,
                        "max_ch": int(d["max_input_channels"])})
        if IS_WIN:
            good = [d for d in out if d["hostapi"] in WIN_APIS]
            out = good or out
        return out
    except Exception:
        return []


def find_input(dev_id):
    if not dev_id:
        return None
    for d in list_input_devices():
        if d["id"] == dev_id:
            return d
    return None


class Resampler:
    """Da una frequenza qualsiasi a un'altra (16 kHz per il riconoscitore): media mobile (anti-alias) e
    interpolazione lineare continua."""

    def __init__(self, sr, out=ASR_RATE):
        self.sr = float(sr)
        self.ratio = self.sr / float(out)
        self.k = max(1, int(round(self.ratio)))
        self.tail = np.zeros(self.k - 1, np.float32)
        self.phase = 0.0                 # posizione (in campioni d'ingresso) del prossimo campione d'uscita
        self.prev = np.zeros(1, np.float32)

    def __call__(self, x):
        x = np.asarray(x, np.float32)
        if self.k > 1:
            xx = np.concatenate((self.tail, x))
            c = np.cumsum(np.concatenate(([0.0], xx)))
            x = ((c[self.k:] - c[:-self.k]) / self.k).astype(np.float32)
            self.tail = xx[-(self.k - 1):]
        if self.ratio == 1.0:
            return x
        if self.k == self.ratio:         # 48 kHz, 32 kHz…: basta prendere un campione ogni k
            start = int(self.phase)
            y = x[start::self.k]
            self.phase = (start + len(y) * self.k) - len(x)
            return y
        buf = np.concatenate((self.prev, x))       # buf[0] = ultimo campione del blocco precedente
        pos = np.arange(self.phase, len(x) - 1 + 1e-9, self.ratio) + 1.0
        y = np.interp(pos, np.arange(len(buf)), buf).astype(np.float32)
        self.phase = (pos[-1] - 1.0 + self.ratio - len(x)) if len(pos) else self.phase - len(x)
        self.prev = x[-1:]
        return y


class Monitor:
    """Preascolto in cuffia: il segnale dell'ingresso (prima o dopo il gate) su una scheda in uscita."""

    def __init__(self):
        self.stream = None
        self.error = ""
        self.lock = threading.Lock()
        self.buf = np.zeros(0, np.float32)
        self.primed = False
        self.gain = 1.0
        self.cols = [0, 1]
        self.rs = None
        self.key = None

    @property
    def active(self):
        return self.stream is not None

    def configure(self, cfg, in_rate):
        """Apre, riapre o chiude l'uscita del preascolto secondo le impostazioni."""
        self.gain = 10.0 ** (float(cfg.get("mon_db") or 0.0) / 20.0)
        want = bool(cfg.get("mon_on")) and in_rate > 0
        key = (cfg.get("mon_device"), cfg.get("mon_channels"), int(in_rate)) if want else None
        if key == self.key and (self.stream is not None or not want):
            return
        self.close()
        self.key = key
        if want:
            self.open(cfg.get("mon_device") or "", parse_channels(cfg.get("mon_channels")), int(in_rate))

    def open(self, dev_id, chans, in_rate):
        self.error = ""
        if sd is None:
            self.error = _("PortAudio non disponibile")
            return False
        dev = None
        if dev_id:
            dev = next((d for d in list_output_devices() if d["id"] == dev_id), None)
            if dev is None:
                self.error = _("Scheda del preascolto «{0}» non trovata").format(dev_id.split('|', 1)[-1])
                return False
        try:
            max_ch = dev["max_ch"] if dev else int(sd.query_devices(kind="output").get("max_output_channels") or 2)
        except Exception:  # noqa: BLE001
            max_ch = 2
        if max(chans) > max_ch:
            self.error = _("La scheda del preascolto ha {0} canali: il canale {1} non esiste").format(max_ch, max(chans))
            return False
        self.cols = [c - 1 for c in chans]
        n = max(chans)
        err = None
        for rate in (in_rate, None, 48000, 44100):
            try:
                st = sd.OutputStream(samplerate=rate, channels=n, dtype="float32", blocksize=0, latency="low",
                                     device=dev["index"] if dev else None, callback=self._cb)
                out_rate = int(st.samplerate)
                self.rs = None if out_rate == in_rate else Resampler(in_rate, out_rate)
                with self.lock:
                    self.buf = np.zeros(0, np.float32)
                    self.primed = False
                self.stream = st
                st.start()
                return True
            except Exception as e:  # noqa: BLE001
                self.stream = None
                err = e
        self.error = _("Impossibile aprire il preascolto: {0}").format(err)
        return False

    def close(self):
        s, self.stream = self.stream, None
        self.key = None
        if s is not None:
            try:
                s.stop()
                s.close()
            except Exception:
                pass

    def push(self, x):
        """Dal thread dell'ingresso: campioni mono alla frequenza dell'ingresso."""
        if self.stream is None:
            return
        y = x * self.gain
        if self.rs is not None:
            y = self.rs(y)
        with self.lock:
            buf = np.concatenate((self.buf, y.astype(np.float32)))
            if len(buf) > 9600:                        # mai più di ~200 ms di ritardo
                buf = buf[-2400:]
            self.buf = buf

    def read(self, frames):
        with self.lock:
            buf = self.buf
            if not self.primed:
                if len(buf) < frames + 256:            # un piccolo margine contro i buchi
                    return np.zeros(frames, np.float32)
                self.primed = True
            if len(buf) < frames:                      # l'ingresso è in ritardo: si riparte con il margine
                self.primed = False
                out = np.zeros(frames, np.float32)
                out[:len(buf)] = buf
                self.buf = np.zeros(0, np.float32)
                return out
            out, self.buf = buf[:frames], buf[frames:]
            return out

    def _cb(self, outdata, frames, t, st):
        outdata.fill(0)
        y = np.clip(self.read(frames), -1.0, 1.0)
        for c in self.cols:
            if c < outdata.shape[1]:
                outdata[:, c] = y


class AudioInput:
    """Ascolta il canale scelto, applica il gate e passa l'audio al consumatore (il riconoscitore)."""

    def __init__(self):
        self.cfg = audio_settings(None)
        self.stream = None
        self.error = ""
        self.name = ""
        self.n_ch = 0
        self.sr = 0
        self.meter = 0.0                 # picco (lineare) del canale, con caduta
        self.meter_post = 0.0            # picco dopo il gate
        self.gate = False                # gate aperto (segnale sopra soglia o in hold)
        self.hold_left = 0.0
        self.consumer = None             # funzione(bytes int16 16 kHz) oppure None
        self._rs = None
        self._g = 0.0                    # apertura del gate (rampa tra un blocco e l'altro)
        self.lock = threading.Lock()
        self.monitor = Monitor()
        # prova con un file audio (audio_file.FilePlayer): la scheda resta chiusa e i blocchi arrivano da lì
        self.file_mode = False
        self.file_listen = False         # far sentire il file sull'uscita del preascolto
        self.file_speed = 1.0

    @property
    def listening(self):
        return self.stream is not None or self.file_mode

    def configure(self, cfg):
        new = audio_settings(cfg)
        old, self.cfg = self.cfg, new
        if self.file_mode:
            self._monitor_file()
            return
        if self.stream is None or (new["device"], new["channel"]) != (old["device"], old["channel"]):
            self.open()
        self.monitor.configure(new, self.sr if self.stream is not None else 0)

    # ------------------------------------------------------------ file audio al posto della scheda
    def use_file(self, sr, name):
        """Da qui in poi l'audio arriva da un file (FilePlayer) alla frequenza sr."""
        self.close()
        self.error = ""
        self.file_mode = True
        self.name = name
        self.n_ch = 1
        self.sr = int(sr)
        self._rs = Resampler(self.sr)
        self._monitor_file()

    def leave_file(self):
        """Fine della prova con il file: si torna alla scheda in ingresso."""
        if not self.file_mode:
            return
        self.file_mode = False
        self.file_listen = False
        self.file_speed = 1.0
        self.close()
        self.open()
        self.monitor.configure(self.cfg, self.sr if self.stream is not None else 0)

    def set_file_listen(self, on):
        self.file_listen = bool(on)
        self._monitor_file()

    def set_file_speed(self, speed):
        self.file_speed = float(speed)
        self._monitor_file()

    def _monitor_file(self):
        if not self.file_mode:
            return
        cfg = dict(self.cfg)
        cfg["mon_on"] = (cfg["mon_on"] or self.file_listen) and self.file_speed == 1.0   # accelerato non si sente
        self.monitor.configure(cfg, self.sr)

    def open(self):
        self.close()
        self.error = ""
        if sd is None:
            self.error = _("PortAudio non disponibile: nessun ingresso audio")
            return False
        dev_id = self.cfg.get("device") or ""
        dev = find_input(dev_id) if dev_id else None
        if dev_id and dev is None:
            self.error = _("Scheda in ingresso «{0}» non trovata").format(dev_id.split('|', 1)[-1])
            return False
        try:
            if dev is None:
                info = sd.query_devices(kind="input")
                max_ch = int(info.get("max_input_channels") or 0)
                self.name = info.get("name", "predefinita")
            else:
                max_ch = dev["max_ch"]
                self.name = dev["name"]
            ch = int(self.cfg.get("channel") or 1)
            if ch > max_ch:
                self.error = _("«{0}» ha {1} canali in ingresso: il canale {2} non esiste").format(self.name, max_ch, ch)
                return False
            self.n_ch = max_ch
            err = None
            # le schede «hw:» di ALSA accettano a volte solo tutti i canali insieme: si riprova così;
            # la frequenza è quella della scheda (si converte a 16 kHz qui)
            for n in dict.fromkeys((ch, max_ch)):
                for rate in (None, 48000, 44100):
                    try:
                        st = sd.InputStream(samplerate=rate, channels=n, dtype="float32", blocksize=0,
                                            latency="low", device=dev["index"] if dev else None, callback=self._cb)
                        self.sr = int(st.samplerate)
                        self._rs = Resampler(self.sr)
                        self.stream = st
                        st.start()
                        return True
                    except Exception as e:  # noqa: BLE001
                        self.stream = None
                        err = e
            raise err
        except Exception as e:  # noqa: BLE001
            self.stream = None
            self.error = _("Impossibile aprire l'ingresso «{0}»: {1}").format(self.name, e)
            return False

    def close(self):
        s, self.stream = self.stream, None
        if s is not None:
            try:
                s.stop()
                s.close()
            except Exception:
                pass
        if not self.file_mode:
            self.monitor.close()
        self.meter = self.meter_post = 0.0
        self.gate = False
        self.hold_left = 0.0
        self._g = 0.0

    # ------------------------------------------------------------ elaborazione
    def _cb(self, indata, frames, t, st):
        ch = int(self.cfg.get("channel") or 1) - 1
        x = indata[:, ch] if indata.ndim == 2 and indata.shape[1] > ch else indata.reshape(-1)
        self.process(x, self.sr)

    def process(self, x, sr):
        """Un blocco del canale scelto: guadagno, meter, gate e invio al riconoscitore."""
        x = np.asarray(x, np.float32).reshape(-1)
        if not len(x):
            return
        g = float(self.cfg.get("gain_db") or 0.0)
        if g:
            x = x * (10.0 ** (g / 20.0))
        peak = float(np.abs(x).max())
        dt = len(x) / float(sr or 48000)
        decay = 0.90 ** (dt / 0.0107)
        self.meter = max(peak, self.meter * decay)
        if self.cfg.get("gate_on"):
            if lin_to_db(peak) >= float(self.cfg["threshold_db"]):
                self.gate = True
                self.hold_left = float(self.cfg["hold_ms"]) / 1000.0
            else:
                self.hold_left -= dt
                self.gate = self.hold_left > 0
        else:
            self.gate = True
        g1 = 1.0 if self.gate else 0.0
        g0, self._g = self._g, g1
        if g0 == g1:
            y = x if g1 else np.zeros_like(x)
        else:
            y = x * np.linspace(g0, g1, len(x), dtype=np.float32)
        self.meter_post = max(float(np.abs(y).max()) if g1 or g0 else 0.0, self.meter_post * decay)
        if self.monitor.stream is not None:
            self.monitor.push(y if self.cfg.get("mon_post") else x)
        cons = self.consumer
        if cons is not None and self._rs is not None:
            z = self._rs(y)
            if len(z):
                cons((np.clip(z, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes())
