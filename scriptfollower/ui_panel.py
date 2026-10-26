"""Riquadro laterale «Prossimo evento»: le frasi che precedono il pulsante, il pulsante, la nota successiva."""
import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from .timing import fmt
from .ui_images import ImageViewer, ThumbStrip
from .ui_document import STYLE_BTN


def _big(style, size):
    return style.replace("font-size:14px", f"font-size:{size}px").replace("padding:6px 8px", "padding:12px 10px")


class NextPanel(QWidget):
    pressed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(320)
        self.setMaximumWidth(460)
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 4, 4, 4)
        v.setSpacing(6)
        title = QLabel("Prossimo evento:")
        f = QFont(self.font())
        f.setPointSize(15)
        f.setBold(True)
        title.setFont(f)
        head = QHBoxLayout()
        head.addWidget(title)
        head.addStretch(1)
        self.eta = QLabel()
        self.eta.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.eta.setTextFormat(Qt.RichText)
        self.eta.setToolTip("Tempo stimato: durata media, nelle repliche registrate, dall'ultimo evento a questo.\n"
                            "Sotto: quanto manca secondo la stima (in arancione se si è oltre).")
        head.addWidget(self.eta)
        v.addLayout(head)
        # nota del pulsante (promemoria per l'operatore), subito sotto il titolo
        self.btn_note = QLabel()
        self.btn_note.setWordWrap(True)
        self.btn_note.setTextFormat(Qt.PlainText)
        self.btn_note.setStyleSheet("QLabel{background:#2b3a2f;color:#ffffff;border-left:6px solid #2bc060;"
                                    "border-radius:4px;padding:6px 8px;font-size:15px;font-weight:bold;}")
        self.btn_note.setVisible(False)
        v.addWidget(self.btn_note)
        # immagini del pulsante: miniature, un clic le apre in una finestra
        self.btn_imgs = ThumbStrip(64)
        self.btn_imgs.opened.connect(lambda i: self._open(self.btn_imgs, i, self._btn_title))
        v.addWidget(self.btn_imgs)
        self._btn_title = ""
        self.resolve = lambda rel: rel           # percorso relativo -> assoluto (lo imposta la finestra)
        self.context = QLabel()
        self.context.setWordWrap(True)
        self.context.setTextFormat(Qt.RichText)
        self.context.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.context.setMinimumHeight(120)
        self.context.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self.context.setStyleSheet("QLabel{background:#1c1d22;border-radius:6px;padding:10px;font-size:16px;"
                                   "color:#9a9ca4;}")
        v.addWidget(self.context)
        self.btn = QPushButton("—")
        self.btn.setFocusPolicy(Qt.NoFocus)
        self.btn.setMinimumHeight(74)
        self.btn.clicked.connect(lambda: self.pid and self.pressed.emit(self.pid))
        v.addWidget(self.btn)
        self.cmds = QLabel()
        self.cmds.setWordWrap(True)
        self.cmds.setStyleSheet("color:#8a8c94;font-size:11px;")
        v.addWidget(self.cmds)
        self.hint = QLabel("Spazio = invia il prossimo evento")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color:#6c6e76;font-size:11px;")
        v.addWidget(self.hint)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color:#3a3c44;")
        v.addWidget(line)
        self.prev_title = QLabel("Precedente (ancora attivo):")
        self.prev_title.setStyleSheet("font-weight:bold;color:#c8c8cc;")
        v.addWidget(self.prev_title)
        self.prev_btn = QPushButton()
        self.prev_btn.setFocusPolicy(Qt.NoFocus)
        self.prev_btn.setMinimumHeight(40)
        self.prev_btn.clicked.connect(lambda: self.prev_pid and self.pressed.emit(self.prev_pid))
        v.addWidget(self.prev_btn)
        self.prev_note = QLabel()
        self.prev_note.setStyleSheet("color:#e0b040;font-size:11px;")
        v.addWidget(self.prev_note)

        line2 = QFrame()
        line2.setFrameShape(QFrame.HLine)
        line2.setStyleSheet("color:#3a3c44;")
        v.addWidget(line2)
        nt = QLabel("Prossima nota:")
        nt.setStyleSheet("font-weight:bold;color:#c8c8cc;")
        v.addWidget(nt)
        self.note_imgs = ThumbStrip(52)
        self.note_imgs.opened.connect(lambda i: self._open(self.note_imgs, i, "Nota"))
        self.note = QLabel("—")
        self.note.setWordWrap(True)
        self.note.setTextFormat(Qt.PlainText)
        v.addWidget(self.note)
        v.addWidget(self.note_imgs)
        v.addStretch(1)
        self.heard_title = QLabel("Sentito:")
        self.heard_title.setStyleSheet("color:#8a8c94;font-size:11px;")
        v.addWidget(self.heard_title)
        self.heard = QLabel("")
        self.heard.setWordWrap(True)
        self.heard.setTextFormat(Qt.PlainText)
        self.heard.setStyleSheet("color:#8a8c94;font-size:12px;")
        self.heard.setMinimumHeight(40)
        v.addWidget(self.heard)
        self.pid = None
        self.prev_pid = None
        self._key = None
        self.set_next(None, [], 0, -1, "", [], True)
        self.set_prev(None, False)
        self.set_note(None)

    def set_next(self, point, words, anchor, cur, label, commands, blink_on, doc=None, by_line=False):
        """words = indici delle parole di contesto (fino all'ancora), cur = parola corrente.
        by_line = va a capo come nel copione (anteprima per righe)."""
        self.pid = point["id"] if point else None
        key = (self.pid, tuple(words), cur, label, tuple(commands), blink_on, by_line,
               (point or {}).get("text"))
        if key == self._key:
            return
        self._key = key
        note = (point or {}).get("text") or ""
        self.btn_note.setText(note)
        self.btn_note.setVisible(bool(note))
        self.btn_imgs.set_paths([self.resolve(r) for r in (point or {}).get("images") or []])
        self._btn_title = (point or {}).get("label") or ""
        if point is None:
            self.context.setText("<span style='color:#6c6e76'>Nessun altro pulsante nel copione.</span>")
            self.btn.setText("—")
            self.btn.setEnabled(False)
            self.btn.setStyleSheet(_big(STYLE_BTN["passed"], 18))
            self.cmds.setText("")
            return
        parts = []
        prev_line = None
        for i in words:
            w = doc.words[i]
            if by_line and prev_line is not None and w.line != prev_line:
                parts.append("<br>")
            prev_line = w.line
            t = html.escape(w.text)
            if i == anchor:
                parts.append(f"<span style='color:#ffffff;background:#1f8f45;font-weight:bold;"
                             f"padding:0 2px'>&nbsp;{t}&nbsp;</span>")
            elif i <= cur:
                parts.append(f"<span style='color:#ffd24a'>{t}</span>")
            elif not w.spoken:
                parts.append(f"<span style='color:#5f6f8f;font-style:italic'>{t}</span>")
            else:
                parts.append(f"<span style='color:#e6e6ea'>{t}</span>")
        lead = "" if by_line or not words else "… "
        self.context.setText(lead + " ".join(parts).replace(" <br> ", "<br>"))
        self.btn.setText(f"▶  {label}")
        self.btn.setEnabled(True)
        self.btn.setStyleSheet(_big(STYLE_BTN["next" if blink_on else "next_dim"], 20))
        self.cmds.setText("\n".join(commands) if commands else "nessun comando OSC (pulsante vuoto)")

    def set_eta(self, estimate, left, samples):
        """estimate = durata media stimata (s) · left = secondi che mancano (None se non calcolabile)."""
        if estimate is None:
            self.eta.setText("<span style='color:#6c6e76;font-size:12px'>tempo stimato —</span>")
            return
        rep = f"{samples} replic{'a' if samples == 1 else 'he'}"
        txt = (f"<span style='color:#9a9ca4;font-size:12px'>tempo stimato</span> "
               f"<span style='color:#e6e6ea;font-size:18px;font-weight:bold'>{fmt(estimate)}</span>")
        if left is not None:
            if left >= 0:
                txt += f"<br><span style='color:#6cc070;font-size:12px'>tra {fmt(left)}</span>"
            else:
                txt += f"<br><span style='color:#e0a030;font-size:12px'>oltre di {fmt(-left)}</span>"
        else:
            txt += f"<br><span style='color:#6c6e76;font-size:11px'>{rep}</span>"
        self.eta.setText(txt)
        self.eta.setToolTip(f"Media di {rep}. Durata media dall'ultimo evento a questo; «tra» = quanto manca.")

    def set_prev(self, point, sent):
        self.prev_pid = point["id"] if point else None
        vis = point is not None
        for w in (self.prev_title, self.prev_btn, self.prev_note):
            w.setVisible(vis)
        if point:
            self.prev_btn.setText(f"▶  {point.get('label') or 'Pulsante'}")
            self.prev_btn.setStyleSheet(STYLE_BTN["future" if sent else "armed"])
            self.prev_note.setText("" if sent else "⚠ non ancora inviato")

    def _open(self, strip, i, title):
        if strip.paths:
            ImageViewer(strip.paths, i, title, self.window()).show()

    def set_note(self, point):
        self.note_imgs.set_paths([self.resolve(r) for r in (point or {}).get("images") or []])
        if point is None:
            self.note.setText("—")
            self.note.setStyleSheet("color:#6c6e76;")
        else:
            self.note.setText(point.get("text") or "")
            self.note.setStyleSheet(f"background:#33302a;color:#eeeeee;border-left:6px solid "
                                    f"{point.get('color') or '#e0b040'};border-radius:4px;padding:6px;font-size:14px;")

    def set_heard(self, text):
        self.heard.setText(text)
