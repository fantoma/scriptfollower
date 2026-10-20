"""Allineamento del parlato riconosciuto al copione.

Il riconoscitore produce parole (spesso con errori, parole saltate o in più). Si confrontano le ultime parole
sentite con il copione in una finestra intorno alla posizione corrente (allineamento locale tipo
Smith-Waterman, tollerante a errori) e ci si sposta solo quando la corrispondenza è abbastanza sicura.
Se si perde il filo, ogni tanto si cerca in tutto il copione (con una soglia più alta).
"""
import re
import unicodedata

import numpy as np

_APOS = re.compile(r"[’'`´‘]")
_NONWORD = re.compile(r"[^0-9a-zàèéìíòóùúäëïöüçñ]+")


def normalize(text):
    """'Cos’è,' -> ['cos', 'è'] · '«Sì!»' -> ['sì'] · 'dell’ospedale' -> ['dell', 'ospedale']"""
    t = unicodedata.normalize("NFC", (text or "").lower())
    t = _APOS.sub(" ", t)
    t = t.replace("-", " ").replace("–", " ").replace("—", " ")
    out = []
    for part in t.split():
        w = _NONWORD.sub("", part)
        if w:
            out.append(w)
    return out


def strip_accents(w):
    return "".join(ch for ch in unicodedata.normalize("NFD", w) if unicodedata.category(ch) != "Mn")


def _similar(a, b):
    """0 = diverse, 1 = simili (errore di riconoscimento plausibile), 2 = uguali."""
    if a == b:
        return 2
    sa, sb = strip_accents(a), strip_accents(b)
    if sa == sb:
        return 2
    if len(sa) >= 4 and len(sb) >= 4:
        if sa[:4] == sb[:4] and abs(len(sa) - len(sb)) <= 3:
            return 1
        # una sola lettera diversa / in più / in meno
        if abs(len(sa) - len(sb)) <= 1 and _edit1(sa, sb):
            return 1
    return 0


def _edit1(a, b):
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) <= 1
    if len(a) > len(b):
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1:]


NEAR_DIST = 60                 # spostamenti in avanti «normali» (oltre serve più sicurezza)
MATCH, NEAR, MISMATCH, GAP_HYP, GAP_SCRIPT = 2.0, 1.0, -1.0, -1.0, -1.0


class Aligner:
    """Posizione nel copione (indice del token parlato) a partire dalle parole riconosciute."""

    def __init__(self, tokens, skippable=None, holds=None):
        self.tokens = list(tokens)                        # token normalizzati del copione (solo parti dette)
        self.holds = set(holds or ())                     # posizioni prima di una parte improvvisata
        self.floor = -1                                   # non si cerca prima di questo token
        self.vocab = sorted(set(self.tokens))
        vid = {w: k for k, w in enumerate(self.vocab)}
        self.ids = np.array([vid[t] for t in self.tokens], dtype=np.int32)
        sk = list(skippable) if skippable else [False] * len(self.tokens)
        gaps = np.where(np.array(sk, dtype=bool), 0.0, GAP_SCRIPT) if self.tokens else np.zeros(0)
        self.gcum = np.concatenate(([0.0], np.cumsum(gaps)))     # costo cumulato dei salti nel copione
        self.pos = -1                                     # ultimo token riconosciuto (-1 = prima dell'inizio)
        self.ahead = 260                                  # token cercati in avanti
        self.behind = 25                                  # token cercati indietro
        self.min_score = 5.0                              # ~3 parole giuste di fila
        self.jump_score = 9.0                             # per i salti lunghi e all'indietro
        self.global_score = 11.0                          # ricerca in tutto il copione
        self.max_hyp = 10
        self.last_penalty = 1.0                           # se l'ultima parola sentita non corrisponde
        self._sim_cache = {}
        self._cand = None                                 # salto lontano in attesa di conferma

    def reset(self, pos=-1):
        self.pos = max(-1, min(len(self.tokens) - 1, pos))
        self._cand = None
        if self.pos < self.floor:                         # riposizionato prima del limite: il limite scende
            self.floor = max(-1, self.pos)

    def set_floor(self, tok):
        """Da qui in poi si cerca solo da tok in avanti (la parte già fatta dello spettacolo è esclusa)."""
        self.floor = max(-1, min(len(self.tokens) - 1, int(tok)))

    # ------------------------------------------------------------ confronto
    def _sim_vec(self, w):
        """Punteggio della parola sentita w contro ogni parola del vocabolario del copione."""
        v = self._sim_cache.get(w)
        if v is None:
            sims = np.fromiter((_similar(w, t) for t in self.vocab), dtype=np.int8, count=len(self.vocab))
            v = (np.where(sims == 2, MATCH, np.where(sims == 1, NEAR, MISMATCH)).astype(np.float64), sims > 0)
            if len(self._sim_cache) > 5000:
                self._sim_cache.clear()
            self._sim_cache[w] = v
        return v

    def _align(self, hyp, lo, hi):
        """Allineamento locale di hyp (Smith-Waterman) che termina con una delle ultime parole sentite.

        Ritorna (punteggi, valido): per ogni token lo..hi-1 il punteggio del miglior allineamento che finisce
        lì, e se lì c'è una parola corrispondente."""
        n = hi - lo
        m = len(hyp)
        if n <= 0 or m == 0:
            return None, None
        ids = self.ids[lo:hi]
        g = self.gcum[lo:hi + 1]                          # g[j] = costo dei salti fino al token lo+j-1
        prev = np.zeros(n + 1)
        rows = []
        for i in range(m):
            sc, ok = self._sim_vec(hyp[i])
            s = sc[ids]
            base = np.zeros(n + 1)
            base[1:] = np.maximum(prev[:-1] + s, prev[1:] + GAP_HYP)
            base = np.maximum(base, 0.0)
            # parole del copione saltate: cur[j] = max_k<=j base[k] + (g[j] - g[k])
            cur = g + np.maximum.accumulate(base - g)
            rows.append((cur, ok[ids]))
            prev = cur
        last, ok_last = rows[-1]
        score = last[1:].copy()
        valid = ok_last.copy()
        if m >= 2:
            pen, ok_pen = rows[-2]
            alt = pen[1:] - self.last_penalty              # l'ultima parola sentita può essere un errore
            use = (alt > score) & ok_pen
            score = np.where(use, alt, score)
            valid = valid | use
        return score, valid

    # ------------------------------------------------------------ aggiornamento
    def update(self, words):
        """Parole sentite più di recente (già normalizzate, in ordine). Ritorna la nuova posizione o None."""
        hyp = [w for w in words if w][-self.max_hyp:]
        if len(hyp) < 2 or not self.tokens:
            return None
        lo = max(0, self.pos - self.behind, self.floor)
        hi = min(len(self.tokens), max(self.pos, self.floor) + 1 + self.ahead)
        score, valid = self._align(hyp, lo, hi)
        best, best_raw = None, 0.0
        holding = any(h - 8 <= self.pos <= h for h in self.holds)
        if score is not None:
            ends = np.arange(lo, hi)
            dist = ends - self.pos
            need = np.where((dist >= 0) & (dist <= NEAR_DIST), self.min_score, self.jump_score)
            good = valid & (score >= need)
            for h in self.holds:
                if h - 8 <= self.pos <= h and h + 1 < hi:
                    # parte improvvisata vicina o in corso: oltre il suo inizio ci si sposta solo se il testo
                    # che segue è riconosciuto da solo, con parole sicure (le frasi improvvisate somigliano
                    # spesso al copione); da ferma sull'inizio non si torna indietro
                    sc2, v2 = self._align(hyp, h + 1, hi)
                    after = ends > h
                    k = ends[after] - (h + 1)
                    score = score.copy()
                    score[after] = sc2[k]
                    good[after] = v2[k] & (sc2[k] >= self.jump_score)
                    if self.pos == h:
                        good &= dist > 0
            if good.any():
                adj = score - 0.012 * np.abs(dist) - np.where(dist < 0, 2.0, 0.0)
                adj = np.where(good, adj, -1e9)
                k = int(np.argmax(adj))
                best, best_raw = int(ends[k]), float(score[k])
        # in tutto il copione (è veloce): un salto lontano solo se nettamente migliore e confermato
        if len(hyp) >= 6:
            g, g_raw = self._global(hyp)
            if g is not None and g_raw >= self.global_score and g_raw >= best_raw + 3.0 \
                    and not (lo <= g < hi and (best is not None or holding)):
                cand = self._cand
                if cand is not None and 0 <= g - cand <= 4:
                    self._cand = None
                    best = g
                else:
                    self._cand = g
                    return None
            else:
                self._cand = None
        if best is None or best == self.pos:
            return None
        self.pos = best
        return best

    def _global(self, hyp):
        base = max(0, self.floor)                         # solo la parte rimanente del copione
        score, valid = self._align(hyp, base, len(self.tokens))
        if score is None:
            return None, 0.0
        score = np.where(valid, score, -1e9)
        k = int(np.argmax(score))
        # se la stessa frase compare più volte con lo stesso punteggio, meglio la più vicina
        ties = np.flatnonzero(score >= score[k] - 0.01)
        k = int(ties[np.argmin(np.abs(ties + base - self.pos))])
        return k + base, float(score[k])
