"""Il progetto (file .sfollow, JSON accanto al PDF): copione, punti, parole incluse/escluse e profili OSC.

Punto: {id, word, kind "button"|"note", label, text, color, commands: [comando OSC, …]}
  word = indice della parola d'ancora nel PDF: il punto «passa» quando il parlato arriva a quella parola.
"""
import json
import os
import uuid

from .osc import clean_targets
from .timing import START, clean_timing

EXT = ".sfollow"
FILE_FILTER = "Progetto Script Follower (*.sfollow);;Tutti i file (*)"
NOTE_COLORS = ["#e0b040", "#5aa0e0", "#e06a5a", "#6cc070", "#b080e0"]


def new_point(word, kind):
    return {"id": uuid.uuid4().hex[:8], "word": int(word), "kind": kind,
            "label": "" if kind == "button" else "", "text": "", "color": NOTE_COLORS[0] if kind == "note" else "",
            "commands": [], "images": []}


class Project:
    def __init__(self):
        self.path = None                 # file .sfollow (None = non ancora salvato)
        self.pdf = None                  # percorso assoluto del PDF
        self.pdf_sha1 = ""
        self.points = []
        self.overrides = {}
        self.improv = []                       # parti improvvisate [[prima parola, ultima parola]]
        self.aliases = {}                      # pronunce delle parole che il modello non conosce
        self.targets = clean_targets(None)
        self.timing = clean_timing(None)       # tempi medi tra gli eventi (vedi timing.py)
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

    # ------------------------------------------------------------ file
    def to_dict(self):
        pdf = self.pdf
        if pdf and self.path:
            try:
                pdf = os.path.relpath(pdf, os.path.dirname(self.path)).replace("\\", "/")
            except ValueError:           # Windows: unità diverse
                pass
        ids = {p["id"] for p in self.points} | {START}
        timing = clean_timing(self.timing)
        timing["intervals"] = {k: v for k, v in timing["intervals"].items()
                               if all(x in ids for x in k.split(">", 1))}
        return {"app": "scriptfollower", "version": 1, "pdf": pdf, "pdf_sha1": self.pdf_sha1,
                "targets": self.targets, "points": self.sorted_points(),
                "overrides": {str(k): v for k, v in sorted(self.overrides.items())},
                "improv": [list(r) for r in sorted(self.improv)],
                "aliases": dict(sorted(self.aliases.items())),
                "timing": timing}

    def save(self, path=None):
        path = path or self.path
        if not path:
            raise ValueError("nessun file indicato")
        if not path.endswith(EXT):
            path += EXT
        self.path = os.path.abspath(path)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)
        self.dirty = False

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if d.get("app") != "scriptfollower":
            raise ValueError("non è un progetto di Script Follower")
        p = cls()
        p.path = os.path.abspath(path)
        pdf = d.get("pdf") or ""
        if pdf and not os.path.isabs(pdf):
            pdf = os.path.normpath(os.path.join(os.path.dirname(p.path), pdf.replace("/", os.sep)))
        p.pdf = pdf or None
        p.pdf_sha1 = d.get("pdf_sha1") or ""
        p.targets = clean_targets(d.get("targets"))
        for pt in d.get("points") or []:
            if isinstance(pt, dict) and "word" in pt and pt.get("kind") in ("button", "note"):
                q = new_point(pt["word"], pt["kind"])
                q.update({k: pt[k] for k in ("id", "label", "text", "color", "commands", "images") if k in pt})
                p.points.append(q)
        p.overrides = {int(k): bool(v) for k, v in (d.get("overrides") or {}).items()}
        p.timing = clean_timing(d.get("timing"))
        p.aliases = {str(k): str(v) for k, v in (d.get("aliases") or {}).items() if str(v).strip()}
        p.improv = [[int(r[0]), int(r[1])] for r in d.get("improv") or []
                    if isinstance(r, (list, tuple)) and len(r) == 2]
        return p


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
