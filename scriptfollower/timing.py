"""Euristica dei tempi: a ogni replica registra quanto passa tra un evento e il successivo.

Un «evento» è un punto del copione (pulsante o nota) che viene raggiunto: dal parlato o, per i pulsanti, dalla
pressione se avviene prima. Per ogni punto si tiene la durata dall'evento che lo precede nel copione
(o dall'inizio della replica, per il primo). La chiave è «precedente>punto»: se in preparazione si aggiunge
un punto in mezzo, le misure vecchie non vengono attribuite a una coppia sbagliata.

Una misura vale solo se i due eventi sono consecutivi nella stessa replica, senza salti a mano in mezzo.
Le stime sono la media delle ultime MAX_SAMPLES repliche, scartando i valori molto lontani dalla mediana.
"""
import statistics
import time

START = "__inizio__"
MAX_SAMPLES = 10
MIN_INTERVAL = 0.3               # eventi praticamente simultanei (salto del riconoscimento): non si misurano
MAX_INTERVAL = 3 * 3600


def fmt(sec, sign=False):
    """75 -> '1:15' · 3725 -> '1:02:05' · sign=True -> '+0:12' / '−0:12'."""
    neg = sec < 0
    s = int(round(abs(sec)))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    txt = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
    if sign:
        return ("−" if neg else "+") + txt
    return ("−" if neg else "") + txt


def clean_timing(d):
    out = {"runs": 0, "intervals": {}}
    if isinstance(d, dict):
        try:
            out["runs"] = max(0, int(d.get("runs") or 0))
        except (TypeError, ValueError):
            pass
        for k, v in (d.get("intervals") or {}).items():
            if isinstance(k, str) and ">" in k and isinstance(v, list):
                vals = []
                for x in v:
                    try:
                        x = float(x)
                    except (TypeError, ValueError):
                        continue
                    if MIN_INTERVAL <= x <= MAX_INTERVAL:
                        vals.append(round(x, 2))
                if vals:
                    out["intervals"][k] = vals[-MAX_SAMPLES:]
    return out


class Timing:
    def __init__(self, project, clock=time.monotonic):
        self.project = project
        self._real = clock
        self.speed = 1.0               # prova con un file audio accelerato: il tempo «dello spettacolo» corre di più
        self._r0 = self._v0 = 0.0
        self.recording = False
        self.reached = set()           # punti già raggiunti in questa replica
        self.chain = None              # (id dell'ultimo evento, istante) oppure None dopo un salto
        self.run_started = False

    def clock(self):
        """Tempo dello spettacolo: quello reale, moltiplicato per la velocità della prova con un file."""
        if self.speed == 1.0 and not self._r0:
            return self._real()
        return self._v0 + (self._real() - self._r0) * self.speed

    def set_speed(self, speed):
        v = self.clock()
        self._r0, self._v0 = self._real(), v
        self.speed = float(speed) or 1.0

    @property
    def data(self):
        if not isinstance(getattr(self.project, "timing", None), dict):
            self.project.timing = clean_timing(None)
        return self.project.timing

    # ------------------------------------------------------------ struttura
    def predecessor(self, pid):
        pts = self.project.sorted_points()
        for i, p in enumerate(pts):
            if p["id"] == pid:
                return pts[i - 1]["id"] if i else START
        return None

    def key(self, pid):
        pred = self.predecessor(pid)
        return None if pred is None else f"{pred}>{pid}"

    # ------------------------------------------------------------ replica
    def restart(self):
        """Nuova replica dall'inizio: si misura dal primo parlato."""
        self.reached.clear()
        self.chain = None
        self.run_started = False

    def begin(self, now=None):
        """Prima parola riconosciuta dopo l'inizio: parte il cronometro."""
        if self.run_started:
            return
        self.run_started = True
        self.chain = (START, self.clock() if now is None else now)
        if self.recording:
            self.data["runs"] = int(self.data.get("runs") or 0) + 1

    def jumped(self, back_to_word=None):
        """Salto a mano (o lungo salto del riconoscimento): la catena si interrompe."""
        self.chain = None
        if back_to_word is not None:
            for p in self.project.points:
                if p["word"] > back_to_word:
                    self.reached.discard(p["id"])

    def event(self, pid, now=None):
        """Il punto pid è stato raggiunto. Ritorna la durata misurata (se registrata) oppure None."""
        if pid in self.reached:
            return None
        self.reached.add(pid)
        now = self.clock() if now is None else now
        measured = None
        if self.chain is not None and self.chain[0] == self.predecessor(pid):
            dt = now - self.chain[1]
            if self.recording and MIN_INTERVAL <= dt <= MAX_INTERVAL:
                lst = self.data["intervals"].setdefault(self.key(pid), [])
                lst.append(round(dt, 2))
                del lst[:-MAX_SAMPLES]
                measured = dt
        self.chain = (pid, now)
        return measured

    # ------------------------------------------------------------ stime
    def estimate(self, pid):
        """Durata media dall'evento precedente a pid (secondi) oppure None se non c'è ancora una misura."""
        k = self.key(pid)
        vals = self.data["intervals"].get(k) if k else None
        if not vals:
            return None
        if len(vals) >= 3:                                  # niente valori assurdi (pause, interruzioni)
            med = statistics.median(vals)
            vals = [v for v in vals if med / 3 <= v <= med * 3] or vals
        return sum(vals) / len(vals)

    def samples(self, pid):
        k = self.key(pid)
        return len(self.data["intervals"].get(k) or []) if k else 0

    def eta(self, pid, now=None):
        """(durata stimata dall'ultimo evento a pid, secondi che mancano) — None dove non si può stimare.

        Se tra l'ultimo evento e pid ci sono altri punti, si sommano le loro durate medie."""
        pts = [p["id"] for p in self.project.sorted_points()]
        if pid not in pts:
            return None, None
        if self.chain is None:
            return self.estimate(pid), None
        last, t0 = self.chain
        start = 0 if last == START else (pts.index(last) + 1 if last in pts else None)
        end = pts.index(pid)
        if start is None or start > end:
            return self.estimate(pid), None
        total = 0.0
        for q in pts[start:end + 1]:
            e = self.estimate(q)
            if e is None:
                return self.estimate(pid), None
            total += e
        now = self.clock() if now is None else now
        return total, total - (now - t0)

    def clear(self):
        self.project.timing = clean_timing(None)
