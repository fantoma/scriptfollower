"""Il copione: un PDF letto con PyMuPDF, parola per parola, con la posizione di ogni parola sulla pagina.

Ogni parola sa se viene detta in scena («parlata») oppure no: didascalie tra parentesi, testo in corsivo o
grassetto (titoli, luci), righe tipo «MUSICA: …» e l'indice sono esclusi dal riconoscimento ma restano
visibili. In preparazione si può includere o escludere a mano qualunque parola.
"""
import hashlib
import os
import re

from .align import normalize
from .vocab import default_alias
from .i18n import _

try:
    import pymupdf
except Exception:  # pragma: no cover - versioni vecchie
    import fitz as pymupdf  # type: ignore

_LABEL = re.compile(r"^[A-ZÀ-Ý][A-ZÀ-Ý0-9 ._\-–]{2,}:$")   # «MUSICA:», «LUCI:», «AUDIO:»
_LEADER = re.compile(r"\.{5,}")                            # puntini dell'indice
_CLOSERS = "»”\"’')]"


def ends_sentence(text):
    """La parola chiude una frase: «.», «!», «?» finali. I puntini di sospensione («…», «...») no: nei copioni
    stanno quasi sempre a metà frase («No, il cane no … ma perché»)."""
    t = text.rstrip(_CLOSERS)
    if not t or t.endswith("…") or t.endswith(".."):
        return False
    return t[-1] in ".!?"
ITALIC, BOLD = 2, 16


class Word:
    __slots__ = ("i", "page", "x0", "y0", "x1", "y1", "text", "line", "auto_spoken", "spoken", "tokens", "t0")

    def __init__(self, i, page, x0, y0, x1, y1, text, line):
        self.i = i
        self.page = page
        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1
        self.text = text
        self.line = line                 # indice globale della riga
        self.auto_spoken = True
        self.spoken = True
        self.tokens = []
        self.t0 = -1                     # primo token parlato di questa parola (-1 = nessuno)


class Line:
    __slots__ = ("i", "page", "x0", "y0", "x1", "y1", "words")

    def __init__(self, i, page):
        self.i = i
        self.page = page
        self.x0 = self.y0 = 1e9
        self.x1 = self.y1 = -1e9
        self.words = []


def file_sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class ScriptDoc:
    def __init__(self, path):
        self.path = os.path.abspath(path)
        self.pdf = pymupdf.open(self.path)
        if self.pdf.needs_pass:
            raise ValueError(_("il PDF è protetto da password"))
        self.sha1 = file_sha1(self.path)
        self.page_sizes = [(p.rect.width, p.rect.height) for p in self.pdf]
        self.words = []
        self.lines = []
        self.page_words = []             # per pagina: (primo indice, ultimo+1)
        self._read()
        self.overrides = {}              # indice parola -> True/False (parlata o no, scelto a mano)
        self.improv = []                 # parti improvvisate: [(prima parola, ultima parola)], non seguite
        self.aliases = {}                # pronunce: parola del copione -> parole note al modello ("118" -> …)
        self.lang = "it"                 # lingua del copione (numeri in lettere, motore di riconoscimento)
        self.rebuild()

    @property
    def n_pages(self):
        return len(self.page_sizes)

    # ------------------------------------------------------------ lettura
    def _read(self):
        for pn, page in enumerate(self.pdf):
            first = len(self.words)
            styles = []                  # (rect, flags) degli span
            for b in page.get_text("dict").get("blocks", []):
                for ln in b.get("lines", []):
                    for s in ln.get("spans", []):
                        if s.get("text", "").strip():
                            styles.append((s["bbox"], int(s.get("flags", 0)), s.get("font", "")))
            raw = page.get_text("words", sort=True)
            cur_key = None
            line = None
            for (x0, y0, x1, y1, text, bno, lno, _wno) in raw:
                key = (bno, lno)
                if key != cur_key or line is None:
                    line = Line(len(self.lines), pn)
                    self.lines.append(line)
                    cur_key = key
                w = Word(len(self.words), pn, x0, y0, x1, y1, text, line.i)
                w.auto_spoken = not self._styled(styles, x0, y0, x1, y1)
                self.words.append(w)
                line.words.append(w.i)
                line.x0, line.y0 = min(line.x0, x0), min(line.y0, y0)
                line.x1, line.y1 = max(line.x1, x1), max(line.y1, y1)
            self.page_words.append((first, len(self.words)))
        self._auto_rules()

    @staticmethod
    def _styled(styles, x0, y0, x1, y1):
        """La parola è in corsivo o in grassetto (didascalie, titoli, cue di luce)."""
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        for (bx0, by0, bx1, by1), flags, font in styles:
            if bx0 - 0.5 <= cx <= bx1 + 0.5 and by0 - 0.5 <= cy <= by1 + 0.5:
                f = font.lower()
                return bool(flags & (ITALIC | BOLD)) or "italic" in f or "bold" in f or "oblique" in f
        return False

    def _auto_rules(self):
        depth = 0
        for ln in self.lines:
            texts = [self.words[i].text for i in ln.words]
            whole = " ".join(texts)
            label_line = bool(texts) and bool(_LABEL.match(texts[0]))
            leader = bool(_LEADER.search(whole))
            for i in ln.words:
                w = self.words[i]
                opens = w.text.count("(") + w.text.count("[")
                closes = w.text.count(")") + w.text.count("]")
                inside = depth > 0 or opens > 0
                depth = max(0, depth + opens - closes)
                if inside or label_line or leader:
                    w.auto_spoken = False
            # una didascalia non chiusa non si porta dietro tutta la pagina successiva
            if depth and ln.i + 1 < len(self.lines) and self.lines[ln.i + 1].page != ln.page:
                depth = 0

    # ------------------------------------------------------------ parti dette e token
    def set_overrides(self, ov):
        self.overrides = {int(k): bool(v) for k, v in (ov or {}).items() if 0 <= int(k) < len(self.words)}
        self.rebuild()

    def toggle_spoken(self, indices, value=None):
        """Include/esclude parole a mano. value None = inverte la prima."""
        idx = [i for i in indices if 0 <= i < len(self.words)]
        if not idx:
            return
        if value is None:
            value = not self.words[idx[0]].spoken
        for i in idx:
            if value == self.words[i].auto_spoken:
                self.overrides.pop(i, None)
            else:
                self.overrides[i] = value
        self.rebuild()

    # ------------------------------------------------------------ parti improvvisate
    def set_improv(self, ranges):
        """Parti in cui non si segue l'ascolto (pezzi improvvisati): unite se si sovrappongono."""
        rs = sorted((max(0, int(a)), min(len(self.words) - 1, int(b))) for a, b in (ranges or []) if int(a) <= int(b))
        out = []
        for a, b in rs:
            if out and a <= out[-1][1] + 1:
                out[-1] = (out[-1][0], max(out[-1][1], b))
            else:
                out.append((a, b))
        self.improv = out
        self.rebuild()

    def improv_at(self, i):
        for a, b in self.improv:
            if a <= i <= b:
                return (a, b)
        return None

    def hold_tokens(self):
        """Token dopo i quali inizia una parte improvvisata (-1 = all'inizio del copione): lì il riconoscimento
        «aspetta» il testo che segue, con una soglia più alta e senza tornare indietro."""
        holds = set()
        for a, _b in self.improv:
            holds.add(self.last_token_at_or_before(a - 1) if a > 0 else -1)
        return holds

    def set_aliases(self, aliases):
        """Pronunce delle parole che il modello non conosce: {parola: "parole note"} ("" = nessuna)."""
        self.aliases = {}
        for k, v in (aliases or {}).items():
            kk = normalize(k)
            if len(kk) == 1:
                self.aliases[kk[0]] = " ".join(normalize(v))
        self.rebuild()

    def word_counts(self):
        """{parola del copione (normalizzata, prima delle pronunce): quante volte è detta}."""
        out = {}
        for w in self.words:
            if w.spoken:
                for t in normalize(w.text):
                    out[t] = out.get(t, 0) + 1
        return out

    def set_language(self, lang):
        if lang != self.lang:
            self.lang = lang
            self.rebuild()

    def pronounce(self, t):
        """Token che il riconoscitore deve sentire per la parola t del copione."""
        a = self.aliases.get(t)
        if a is None:
            a = default_alias(t, self.lang)
        return normalize(a) if a else [t]

    def excluded_runs(self):
        """Tratti consecutivi di parole escluse dal riconoscimento (le zone azzurre), senza le parti
        improvvisate: [(prima, ultima)]. Un tratto non attraversa le pagine."""
        if self._runs is None:
            zone = set()
            for a, b in self.improv:
                zone.update(range(a, b + 1))
            runs, start = [], None
            for w in self.words:
                ex = not w.spoken and w.i not in zone
                if ex and start is not None and self.words[start].page != w.page:
                    runs.append((start, w.i - 1))
                    start = None
                if ex and start is None:
                    start = w.i
                elif not ex and start is not None:
                    runs.append((start, w.i - 1))
                    start = None
            if start is not None:
                runs.append((start, len(self.words) - 1))
            self._runs = runs
        return self._runs

    def rebuild(self):
        self._runs = None
        self.tokens = []                 # token normalizzati delle parti dette
        self.token_word = []             # token -> indice della parola
        zone = set()
        for a, b in self.improv:
            zone.update(range(a, b + 1))
        for w in self.words:
            w.spoken = self.overrides.get(w.i, w.auto_spoken) and w.i not in zone
            w.tokens = [p for t in normalize(w.text) for p in self.pronounce(t)] if w.spoken else []
            w.t0 = len(self.tokens) if w.tokens else -1
            for t in w.tokens:
                self.tokens.append(t)
                self.token_word.append(w.i)

    def vocabulary(self, from_token=0):
        """Parole da dare al riconoscitore (grammatica): i token e le forme con l'apostrofo, dal token
        from_token in poi (con lo spettacolo che avanza basta la parte rimanente)."""
        from_token = max(0, int(from_token))
        voc = set(self.tokens[from_token:])
        for w in self.words:
            if w.spoken and w.t0 >= from_token:
                t = re.sub(r"[^\w’']+", "", w.text.lower()).replace("’", "'")
                if "'" in t:
                    voc.add(t)
        return sorted(voc)

    # ------------------------------------------------------------ aiuti
    def word_at(self, page, x, y, slack=2.0):
        """Parola alle coordinate PDF indicate (o la più vicina sulla stessa riga)."""
        a, b = self.page_words[page]
        best, bd = None, 1e9
        for i in range(a, b):
            w = self.words[i]
            if w.y0 - slack <= y <= w.y1 + slack:
                if w.x0 - slack <= x <= w.x1 + slack:
                    return i
                d = min(abs(x - w.x0), abs(x - w.x1))
                if d < bd:
                    best, bd = i, d
        return best if bd < 40 else None

    def word_of_token(self, t):
        if 0 <= t < len(self.token_word):
            return self.token_word[t]
        return -1

    def last_token_at_or_before(self, word_i):
        """Ultimo token parlato che sta nella parola word_i o prima (-1 se nessuno)."""
        for i in range(min(word_i, len(self.words) - 1), -1, -1):
            w = self.words[i]
            if w.t0 >= 0:
                return w.t0 + len(w.tokens) - 1
        return -1

    def sentences_before(self, word_i, n=2, max_words=45, min_words=18):
        """Le ultime n frasi dette fino alla parola word_i compresa: [indici di parola].

        Se il testo è poco (frasi brevi, «Sì.» «No.»), si aggiungono frasi intere precedenti fino ad avere
        almeno min_words parole: così si vede sempre anche la frase prima."""
        out = []
        count = 0
        i = min(word_i, len(self.words) - 1)
        # si parte dalla parola d'ancora (anche se esclusa) e si va indietro tra le parti dette
        while i >= 0 and len(out) < max_words:
            w = self.words[i]
            if w.spoken or i == word_i:
                if out and ends_sentence(w.text):
                    count += 1
                    if count >= n and len(out) >= min_words:
                        break
                out.append(i)
            i -= 1
        return list(reversed(out))

    def context_before(self, word_i, unit="sentences", count=2):
        """Parole da mostrare prima dell'ancora (compresa): «sentences» = ultime frasi dette, «lines» = ultime
        righe del copione così come sono (didascalie comprese), «words» = ultime parole dette."""
        count = max(1, int(count))
        if unit == "lines":
            li = self.words[word_i].line
            first = max(0, li - count + 1)
            return [i for ln in self.lines[first:li + 1] for i in ln.words if i <= word_i]
        if unit == "words":
            out = []
            i = word_i
            while i >= 0 and len(out) < count:
                if self.words[i].spoken or i == word_i:
                    out.append(i)
                i -= 1
            return list(reversed(out))
        return self.sentences_before(word_i, n=count, max_words=max(45, 60 * count))

    def render(self, page, zoom):
        """Immagine della pagina (bytes RGB, larghezza, altezza, stride)."""
        pg = self.pdf[page]
        pm = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        return pm.samples, pm.width, pm.height, pm.stride

    def close(self):
        try:
            self.pdf.close()
        except Exception:
            pass
