"""Pannello «Cue attive» di Cuevivo: le cue che stanno andando sull'istanza (o sulle istanze) Cuevivo collegate.

Gli stati arrivano da CuevivoLink (/cuestage/active); tra un messaggio e l'altro i tempi delle cue in corso
avanzano qui, e ogni messaggio li corregge. Il pannello sta in un QDockWidget: agganciato alla finestra
principale oppure staccato (anche su un altro monitor).
"""
import time

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea, QSizePolicy,
                               QToolButton, QVBoxLayout, QWidget)

from .i18n import _

SIZES = {"compact": 26, "normal": 36, "large": 52, "xlarge": 72}
SIZE_LABELS = (("compact", "Compatta"), ("normal", "Normale"), ("large", "Grande"), ("xlarge", "Molto grande"))
FREQS = (1.0, 2.0, 4.0, 8.0, 15.0)
ICONS = {"audio": "🔊", "video": "🎬", "image": "🖼", "pdf": "📄", "text": "💬", "group": "🗂", "wait": "⏱",
         "osc": "📡", "midi": "🎹", "stop": "⏹", "fade": "↘", "mixer": "🎚", "light": "💡", "projector": "📽"}
C_RUN, C_WAIT, C_PAUSE, C_FADE = QColor(70, 190, 100), QColor(230, 200, 60), QColor(230, 150, 50), QColor(110, 170, 255)
ACTIVE_DEFAULTS_UI = {"size": "normal"}


def fmt_s(sec):
    """12.4 → «0:12» · 3725 → «1:02:05»."""
    s = int(max(0.0, sec))
    h, r = divmod(s, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_dec(sec):
    """3.2 → «3,2 s» (virgola italiana anche nelle altre lingue: è un tempo da leggere a colpo d'occhio)."""
    return f"{max(0.0, sec):.1f}".replace(".", ",") + " s"


class CueRow(QWidget):
    """Una cue attiva: etichetta della lista, icona, numero e nome, barra con i tempi, ❚❚/▶ e ■."""
    pause = Signal(str)
    stop = Signal(str)

    def __init__(self, height, parent=None):
        super().__init__(parent)
        self.cue = {}
        self.t0 = time.monotonic()        # istante dello stato (per far avanzare i tempi)
        self.frozen = False               # pausa generale: i tempi non avanzano
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 4, 0)
        h.setSpacing(4)
        h.addStretch(1)
        self.pause_btn = QPushButton("❚❚")
        self.stop_btn = QPushButton("■")
        for b in (self.pause_btn, self.stop_btn):
            b.setFocusPolicy(Qt.NoFocus)
            b.setCursor(Qt.PointingHandCursor)
            h.addWidget(b)
        self.pause_btn.setToolTip(_("Pausa / ripresa"))
        self.stop_btn.setToolTip(_("Ferma la cue"))
        self.pause_btn.clicked.connect(lambda: self.pause.emit(self.cue.get("id", "")))
        self.stop_btn.clicked.connect(lambda: self.stop.emit(self.cue.get("id", "")))
        self.set_height(height)

    def set_height(self, hgt):
        self.setFixedHeight(hgt)
        side = max(22, hgt - 8)
        for b in (self.pause_btn, self.stop_btn):
            b.setFixedSize(QSize(side, side))
            b.setStyleSheet(f"QPushButton{{font-size:{max(10, int(hgt * 0.38))}px;padding:0;border-radius:4px;}}")
        self.update()

    def set_cue(self, c, t0, frozen):
        self.cue, self.t0, self.frozen = c, t0, frozen
        self.pause_btn.setText("▶" if c.get("state") == "paused" else "❚❚")
        self.update()

    # ------------------------------------------------------------ tempi stimati adesso
    def now_values(self, now=None):
        """(trascorso, rimanente o None, avanzamento 0..1 o None, attesa rimanente)."""
        c = self.cue
        dt = 0.0 if self.frozen else max(0.0, (now or time.monotonic()) - self.t0)
        st = c.get("state")
        el = c.get("elapsed") or 0.0
        wait = c.get("wait") or 0.0
        if st == "run":
            el += dt
        elif st == "wait":
            wait = max(0.0, wait - dt)
        total = c.get("total")
        if total:
            el = min(el, total)
            return el, max(0.0, total - el), el / total if total > 0 else 0.0, wait
        return el, None, None, wait

    def state_text(self):
        c = self.cue
        el, rem, _p, wait = self.now_values()
        if c.get("state") == "wait":
            return _("in attesa ⧗ {0}").format(fmt_dec(wait))
        if c.get("state") == "paused":
            return _("in pausa")
        return _("sfuma") if c.get("fading") else ""

    def paintEvent(self, _e):
        c = self.cue
        if not c:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        right = self.pause_btn.geometry().left() - 6
        st = c.get("state")
        col = C_WAIT if st == "wait" else C_PAUSE if st == "paused" else C_RUN
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(34, 35, 41))
        p.drawRoundedRect(QRectF(0, 1, w, h - 2), 4, 4)
        p.setBrush(QColor(c.get("color") or "#5a5c64"))                  # colore della lista
        p.drawRoundedRect(QRectF(0, 1, 6, h - 2), 2, 2)
        el, rem, prog, _w = self.now_values()
        bar = QRectF(12, h - max(4, h * 0.16) - 3, max(10, right - 12), max(4, h * 0.16))
        p.setBrush(QColor(52, 54, 62))
        p.drawRoundedRect(bar, 2, 2)
        if st == "wait":
            pass
        elif prog is not None:
            p.setBrush(C_FADE if c.get("fading") else col)
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), bar.width() * prog, bar.height()), 2, 2)
        else:                                                           # senza fine: una barra a strisce
            p.setBrush(QColor(col.red(), col.green(), col.blue(), 90))
            p.drawRoundedRect(bar, 2, 2)
        f = QFont(self.font())
        f.setPixelSize(max(10, int(h * 0.36)))
        p.setFont(f)
        top = QRectF(12, 2, right - 12, bar.top() - 2)
        times = ""
        if st != "wait":
            times = fmt_s(el) + "  " + (("−" + fmt_s(rem)) if rem is not None else "∞")
        tw = p.fontMetrics().horizontalAdvance(times) + 8 if times else 0
        extra = self.state_text()
        icon = ICONS.get(c.get("type") or "", "•")
        if c.get("group"):
            icon = ICONS["group"]
        p.setPen(QColor(150, 152, 160) if c.get("in_group") else QColor(230, 230, 235))
        label = f"{icon} {c.get('name') or c.get('number') or '?'}"
        if c.get("status"):
            label += f"  ·  {c['status']}"
        room = int(top.width() - tw - (p.fontMetrics().horizontalAdvance(extra) + 10 if extra else 0))
        p.drawText(top.adjusted(0, 0, -tw, 0), Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(label, Qt.ElideRight, max(20, room)))
        if extra:
            p.setPen(C_FADE if (c.get("fading") and st == "run") else col)
            p.drawText(top.adjusted(0, 0, -tw, 0), Qt.AlignRight | Qt.AlignVCenter, extra)
        if times:
            p.setPen(QColor(200, 202, 210))
            p.drawText(top, Qt.AlignRight | Qt.AlignVCenter, times)


class Section(QFrame):
    """Le cue attive di un'istanza di Cuevivo."""

    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.key = None
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(3)
        self.head = QLabel()
        self.head.setStyleSheet("font-weight:bold;color:#c8c8cc;")
        self.head.setWordWrap(True)
        v.addWidget(self.head)
        self.standby = QLabel()
        self.standby.setStyleSheet("color:#9a9ca4;font-size:12px;")
        self.standby.setWordWrap(True)
        v.addWidget(self.standby)
        self.paused = QLabel(_("❚❚ PAUSA GENERALE"))
        self.paused.setStyleSheet("background:#8a5a14;color:white;font-weight:bold;padding:2px 6px;border-radius:3px;")
        v.addWidget(self.paused)
        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(3)
        v.addLayout(self.rows_box)
        self.msg = QLabel()
        self.msg.setWordWrap(True)
        v.addWidget(self.msg)
        self.rows = {}                    # id cue -> CueRow

    def show_endpoint(self, ep, height, now):
        self.key = ep.key
        who = ep.instance or ", ".join(ep.names) or ep.host
        where = f"{ep.host}:{ep.port}"
        if ep.fallback_for is not None:
            head = _("{0} — ridondanza: il master non risponde").format(who)
            color = "#e0a030"
        elif ep.note == "master":
            head = _("{0} — nuovo master").format(who)
            color = "#6cc070"
        else:
            head, color = who, "#c8c8cc"
        self.head.setText(head)
        self.head.setToolTip(where + ((" · " + ", ".join(ep.names)) if ep.names else ""))
        self.head.setStyleSheet(f"font-weight:bold;color:{color};")
        st = ep.state if ep.fresh(now) else None
        if st is None:
            self._set_rows([], height, now, False)
            self.standby.setVisible(False)
            self.paused.setVisible(False)
            self.msg.setText(_("Cuevivo non collegato"))
            self.msg.setStyleSheet("color:#e07060;")
            self.msg.setVisible(True)
            return
        sb = st.get("standby")
        self.standby.setText(_("Prossima: {0}").format(sb["name"] + (f"  ({sb['list']})" if sb.get("list") else ""))
                             if sb and sb.get("name") else "")
        self.standby.setVisible(bool(sb and sb.get("name")))
        self.paused.setVisible(st.get("paused_all"))
        self._set_rows(st["cues"], height, ep.t, st.get("paused_all"))
        if not st["cues"]:
            self.msg.setText(_("Nessuna cue attiva"))
            self.msg.setStyleSheet("color:#8a8c94;")
        elif st.get("more"):
            self.msg.setText(_("… e altre {0} cue").format(st["more"]))
            self.msg.setStyleSheet("color:#8a8c94;")
        self.msg.setVisible(not st["cues"] or bool(st.get("more")))

    def _set_rows(self, cues, height, t0, frozen):
        ids = [c["id"] for c in cues]
        for cid in [k for k in self.rows if k not in ids]:
            r = self.rows.pop(cid)
            self.rows_box.removeWidget(r)
            r.deleteLater()
        for i, c in enumerate(cues):
            r = self.rows.get(c["id"])
            if r is None:
                r = self.rows[c["id"]] = CueRow(height)
                r.pause.connect(lambda cid, s=self: self.panel.link.send_pause(s.key, cid))
                r.stop.connect(lambda cid, s=self: self.panel.link.send_stop(s.key, cid))
            if self.rows_box.indexOf(r) != i:
                self.rows_box.insertWidget(i, r)
            if r.height() != height:
                r.set_height(height)
            r.set_cue(c, t0, frozen)

    def tick(self):
        for r in self.rows.values():
            r.update()


class ActivePanel(QWidget):
    """Elenco delle cue attive di ogni istanza Cuevivo collegata, con opzioni (⚙) e stacca/aggancia (⧉)."""
    optionsChanged = Signal(dict)
    detach = Signal()

    def __init__(self, link, opts=None, parent=None):
        super().__init__(parent)
        self.link = link
        self.opts = dict(ACTIVE_DEFAULTS_UI, **link.active_cfg)
        self.opts.update(opts or {})
        if self.opts.get("size") not in SIZES:
            self.opts["size"] = "normal"
        self.setMinimumWidth(240)
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 4, 6, 6)
        v.setSpacing(6)
        head = QHBoxLayout()
        title = QLabel(_("Cue attive"))
        f = QFont(self.font())
        f.setPointSize(13)
        f.setBold(True)
        title.setFont(f)
        head.addWidget(title, 1)
        self.gear = QToolButton()
        self.gear.setText("⚙")
        self.gear.setToolTip(_("Opzioni del pannello"))
        self.gear.setPopupMode(QToolButton.InstantPopup)
        self.gear.setMenu(self._menu())
        head.addWidget(self.gear)
        self.detach_btn = QToolButton()
        self.detach_btn.setText("⧉")
        self.detach_btn.setToolTip(_("Stacca / riaggancia il pannello"))
        self.detach_btn.clicked.connect(self.detach)
        head.addWidget(self.detach_btn)
        v.addLayout(head)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color:#e0a030;")
        v.addWidget(self.note)
        inner = QWidget()
        self.box = QVBoxLayout(inner)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(10)
        self.box.addStretch(1)
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.NoFrame)
        sa.setWidget(inner)
        sa.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        v.addWidget(sa, 1)
        self.sections = []
        link.activeChanged.connect(self.refresh)
        self.timer = QTimer(self)                    # tempi che avanzano e «non collegato» dopo 5 s
        self.timer.timeout.connect(self._tick)
        self.timer.start(100)
        self._was_fresh = {}
        self.refresh()

    # ------------------------------------------------------------ opzioni
    def _menu(self):
        m = QMenu(self)
        sm = m.addMenu(_("Dimensione delle righe"))
        for key, lab in SIZE_LABELS:
            a = sm.addAction(_(lab))
            a.setCheckable(True)
            a.setChecked(self.opts["size"] == key)
            a.triggered.connect(lambda _c=False, k=key: self.set_option("size", k))
        m.addSeparator()
        for key, lab, tip in (("nogroups", "Nascondi i gruppi", "Le cue gruppo non si vedono (le loro cue sì)"),
                              ("media", "Solo media", "Solo audio, video, immagini, PDF e testo")):
            a = m.addAction(_(lab))
            a.setToolTip(_(tip))
            a.setCheckable(True)
            a.setChecked(bool(self.opts[key]))
            a.triggered.connect(lambda c, k=key: self.set_option(k, bool(c)))
        fm = m.addMenu(_("Aggiornamenti al secondo"))
        for f in FREQS:
            a = fm.addAction(f"{f:g}")
            a.setCheckable(True)
            a.setChecked(abs(self.opts["freq"] - f) < 1e-6)
            a.triggered.connect(lambda _c=False, f=f: self.set_option("freq", f))
        return m

    def set_option(self, key, value):
        self.opts[key] = value
        self.gear.setMenu(self._menu())             # segni di spunta aggiornati
        if key != "size":
            self.link.set_active(self.link.active_on, {k: self.opts[k] for k in ("freq", "nogroups", "media")})
        self.refresh()
        self.optionsChanged.emit(dict(self.opts))

    # ------------------------------------------------------------ contenuto
    def refresh(self):
        now = time.monotonic()
        eps = sorted(self.link.endpoints.values(),
                     key=lambda e: (e.fallback_for is not None, e.fallback_for or e.key, e.key))
        # istanza di riserva: si mostra solo mentre il suo master tace
        eps = [e for e in eps if e.fallback_for is None or not (self.link.endpoints.get(e.fallback_for)
                                                                 and self.link.endpoints[e.fallback_for].fresh(now))]
        if not self.link.cfg.get("on") or self.link.server is None:
            self.note.setText(_("Il collegamento con Cuevivo è spento (Impostazioni › OSC)."))
        elif not eps:
            self.note.setText(_("Nessun profilo Cuevivo con un indirizzo (Impostazioni › OSC)."))
        else:
            self.note.setText("")
        self.note.setVisible(bool(self.note.text()))
        while len(self.sections) < len(eps):
            s = Section(self)
            self.box.insertWidget(len(self.sections), s)
            self.sections.append(s)
        while len(self.sections) > len(eps):
            s = self.sections.pop()
            self.box.removeWidget(s)
            s.deleteLater()
        hgt = SIZES[self.opts["size"]]
        for s, ep in zip(self.sections, eps):
            s.show_endpoint(ep, hgt, now)
        self._was_fresh = {e.key: e.fresh(now) for e in eps}

    def _tick(self):
        now = time.monotonic()
        if any(self._was_fresh.get(e.key, False) != e.fresh(now) for e in self.link.endpoints.values()):
            self.refresh()                           # qualcuno ha smesso di rispondere (o ha ripreso)
            return
        for s in self.sections:
            s.tick()

    def row_widgets(self):
        """Per i test: [(chiave istanza, CueRow)]."""
        return [(s.key, r) for s in self.sections for r in s.rows.values()]

    def messages(self):
        return [s.msg.text() for s in self.sections if not s.msg.isHidden()]
