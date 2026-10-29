"""Il progetto: punti, parole incluse/escluse, profili OSC, tempi, pronunce e impostazioni dello show.

Si salva dentro il PDF del copione (pdf_store.py); si può anche esportare/importare come JSON (con le immagini
incorporate in base64). I vecchi file «.sfollow» accanto al PDF si importano allo stesso modo.

Punto: {id, word, kind "button"|"note", label, text, color, commands: [comando OSC, …]}
  word = indice della parola d'ancora nel PDF: il punto «passa» quando il parlato arriva a quella parola.
"""
import base64
import json
import os
import uuid

from .osc import clean_targets
from .timing import START, clean_timing
from .i18n import _

EXT = ".sfollow"                       # vecchio formato (versione 1): si importa
JSON_EXT = ".json"
FILE_FILTER = "Dati di Script Follower (*.json *.sfollow);;Tutti i file (*)"
FORMAT = 2
# impostazioni che dipendono dallo show (teatro, mixer, copione) e viaggiano con il PDF; le schede audio e i
# canali restano invece sul computer
SHOW_AUDIO = ("gate_on", "threshold_db", "hold_ms", "gain_db")
SHOW_REC = ("grammar", "ahead", "min_score", "anchor")
NOTE_COLORS = ["#e0b040", "#5aa0e0", "#e06a5a", "#6cc070", "#b080e0"]


def new_point(word, kind):
    return {"id": uuid.uuid4().hex[:8], "word": int(word), "kind": kind,
            "label": "" if kind == "button" else "", "text": "", "color": NOTE_COLORS[0] if kind == "note" else "",
            "commands": [], "images": []}


class Project:
    def __init__(self):
        self.pdf = None                  # percorso assoluto del PDF (i dati si salvano lì dentro)
        self.saved = False               # i dati sono già nel PDF (salvati o letti da lì)
        self.fingerprint = ""            # impronta del contenuto delle pagine (vedi ScriptDoc.fingerprint)
        self.pdf_sha1 = ""               # solo nei vecchi .sfollow: impronta del file PDF intero
        self.points = []
        self.overrides = {}
        self.improv = []                       # parti improvvisate [[prima parola, ultima parola]]
        self.aliases = {}                      # pronunce delle parole che il modello non conosce
        self.language = "it"                   # lingua del copione (riconoscimento)
        self.targets = clean_targets(None)
        self.timing = clean_timing(None)       # tempi medi tra gli eventi (vedi timing.py)
        self.settings = {}                     # {"audio": {...}, "recognition": {...}, "panel": {...}}
        self.dirty = False

    # ------------------------------------------------------------ punti
    def sorted_points(self):
        return sorted(self.points, key=lambda p: (p["word"], p["kind"] != "note"))

    def point(self, pid):
        return next((p for p in self.points if p["id"] == pid), None)

    def add_point(self, p):
        self.points.append(p)
        self.dirty = True

    def remove_point(self, pid):
        self.points = [p for p in self.points if p["id"] != pid]
        self.dirty = True

    # ------------------------------------------------------------ dati
    def to_dict(self):
        ids = {p["id"] for p in self.points} | {START}
        timing = clean_timing(self.timing)
        timing["intervals"] = {k: v for k, v in timing["intervals"].items()
                               if all(x in ids for x in k.split(">", 1))}
        return {"app": "scriptfollower", "version": FORMAT,
                "pdf": os.path.basename(self.pdf) if self.pdf else "",
                "pdf_fingerprint": self.fingerprint,
                "targets": self.targets, "points": self.sorted_points(),
                "overrides": {str(k): v for k, v in sorted(self.overrides.items())},
                "improv": [list(r) for r in sorted(self.improv)],
                "aliases": dict(sorted(self.aliases.items())),
                "language": self.language,
                "timing": timing,
                "settings": self.settings}

    @classmethod
    def from_dict(cls, d):
        if not isinstance(d, dict) or d.get("app") != "scriptfollower":
            raise ValueError(_("non è un progetto di Script Follower"))
        p = cls()
        p.fingerprint = str(d.get("pdf_fingerprint") or "")
        p.pdf_sha1 = str(d.get("pdf_sha1") or "")
        p.targets = clean_targets(d.get("targets"))
        for pt in d.get("points") or []:
            if isinstance(pt, dict) and "word" in pt and pt.get("kind") in ("button", "note"):
                q = new_point(pt["word"], pt["kind"])
                q.update({k: pt[k] for k in ("id", "label", "text", "color", "commands", "images") if k in pt})
                p.points.append(q)
        p.overrides = {int(k): bool(v) for k, v in (d.get("overrides") or {}).items()}
        p.timing = clean_timing(d.get("timing"))
        p.language = d.get("language") if d.get("language") in ("it", "en", "sl") else "it"
        p.aliases = {str(k): str(v) for k, v in (d.get("aliases") or {}).items() if str(v).strip()}
        p.improv = [[int(r[0]), int(r[1])] for r in d.get("improv") or []
                    if isinstance(r, (list, tuple)) and len(r) == 2]
        st = d.get("settings") if isinstance(d.get("settings"), dict) else {}
        p.settings = {k: dict(v) for k, v in st.items() if k in ("audio", "recognition", "panel") and isinstance(v, dict)}
        return p

    # ------------------------------------------------------------ esporta / importa JSON
    def export_json(self, path, images=None):
        """JSON con tutto, immagini comprese (base64): per archiviare o passare lo show a un collega."""
        d = self.to_dict()
        d["images"] = {n: base64.b64encode(b).decode("ascii") for n, b in sorted((images or {}).items())}
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)


def read_json(path):
    """(progetto, {nome immagine: bytes}, nome o percorso del PDF indicato nel file). Anche i vecchi .sfollow."""
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    p = Project.from_dict(d)
    images = {}
    for n, b64 in (d.get("images") or {}).items():
        try:
            images[str(n)] = base64.b64decode(b64)
        except (ValueError, TypeError):
            continue
    pdf = str(d.get("pdf") or "")
    if pdf and not os.path.isabs(pdf):
        pdf = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(path)), pdf.replace("/", os.sep)))
    return p, images, pdf


class Cueing:
    """Stato dei punti durante lo spettacolo.

    - passato: la posizione ha raggiunto la parola d'ancora → grigio, pulsante disattivato;
    - eccezione: l'ultimo pulsante passato resta premibile finché non si preme quello corrente
      (il successivo): la pressione di un pulsante disattiva quelli precedenti;
    - prossimo pulsante: il primo dopo la posizione non ancora inviato (lampeggia, tasto Spazio);
    - prossima nota: la prima annotazione dopo la posizione (lampeggia).
    """

    def __init__(self, project):
        self.project = project
        self.pos = -1                    # indice della parola corrente (-1 = prima dell'inizio)
        self.sent = set()                # pulsanti inviati
        self.closed = set()              # pulsanti disattivati dalla pressione di uno successivo
        self.forced = set()              # pulsanti riattivati a mano: premibili finché non si premono

    def reset(self):
        self.pos = -1
        self.sent.clear()
        self.closed.clear()
        self.forced.clear()

    def set_pos(self, word):
        back = word < self.pos
        self.pos = word
        if back:                         # tornando indietro i punti successivi si riattivano
            for p in self.project.points:
                if p["word"] > word:
                    self.sent.discard(p["id"])
                    self.closed.discard(p["id"])
                    self.forced.discard(p["id"])

    def buttons(self):
        return [p for p in self.project.sorted_points() if p["kind"] == "button"]

    def passed(self, p):
        return p["word"] <= self.pos

    def last_passed_button(self):
        """L'ultimo pulsante passato, se è ancora premibile."""
        prev = [b for b in self.buttons() if self.passed(b)]
        if prev and prev[-1]["id"] not in self.closed:
            return prev[-1]
        return None

    def next_button(self):
        for b in self.buttons():
            if not self.passed(b) and b["id"] not in self.sent:
                return b
        return None

    def next_note(self):
        for p in self.project.sorted_points():
            if p["kind"] == "note" and not self.passed(p):
                return p
        return None

    def reactivate(self, p):
        """Riattivazione a mano di un pulsante già passato, disattivato o inviato."""
        self.sent.discard(p["id"])
        self.closed.discard(p["id"])
        self.forced.add(p["id"])

    def enabled(self, p):
        if p["kind"] != "button":
            return not self.passed(p)
        if p["id"] in self.forced:
            return True
        if p["id"] in self.closed:
            return False
        if not self.passed(p):
            return True
        lp = self.last_passed_button()
        return lp is not None and lp["id"] == p["id"]

    def press(self, p):
        """Il pulsante p è stato premuto: i pulsanti precedenti si disattivano."""
        self.sent.add(p["id"])
        self.closed.discard(p["id"])
        self.forced.discard(p["id"])
        for b in self.buttons():
            if b["word"] < p["word"] or (b["word"] == p["word"] and b["id"] != p["id"]
                                          and self.buttons().index(b) < self.buttons().index(p)):
                self.closed.add(b["id"])
