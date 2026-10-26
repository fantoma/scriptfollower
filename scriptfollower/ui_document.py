"""Vista del copione: le pagine del PDF una sotto l'altra, con a destra la colonna dei punti.

In preparazione un clic su una parola (o un trascinamento su più parole) apre il menu per aggiungere un
pulsante OSC o un'annotazione, o per includere/escludere parole dal riconoscimento. Durante lo spettacolo
la riga corrente è evidenziata e la vista la segue; doppio clic su una parola = riparti da lì.
"""
import time

from PySide6.QtCore import QEasingCurve, QPoint, QPointF, QPropertyAnimation, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

MARGIN = 18          # bordo intorno alle pagine
GAP = 18             # spazio tra le pagine
GUTTER = 250         # colonna dei punti a destra della pagina
PAGE_BG = QColor(255, 255, 255)
CANVAS_BG = QColor(28, 29, 34)

GO_GREEN = "#1f8f45"
STYLE_BTN = {
    "future": f"QPushButton{{background:{GO_GREEN};color:white;font-weight:bold;font-size:14px;border-radius:6px;"
              f"padding:6px 8px;border:2px solid {GO_GREEN};text-align:left;}}"
              "QPushButton:pressed{background:#2bc060;}",
    "next": "QPushButton{background:#2bc060;color:white;font-weight:bold;font-size:14px;border-radius:6px;"
            "padding:6px 8px;border:2px solid #ffffff;text-align:left;}QPushButton:pressed{background:#36d872;}",
    "next_dim": f"QPushButton{{background:{GO_GREEN};color:white;font-weight:bold;font-size:14px;border-radius:6px;"
                "padding:6px 8px;border:2px solid #1a5c30;text-align:left;}QPushButton:pressed{background:#2bc060;}",
    "armed": "QPushButton{background:#8a6d1a;color:white;font-weight:bold;font-size:14px;border-radius:6px;"
             "padding:6px 8px;border:2px solid #e0b040;text-align:left;}QPushButton:pressed{background:#b08a20;}",
    "sent": "QPushButton{background:#1d4a2c;color:#d8f0dd;font-weight:bold;font-size:14px;border-radius:6px;"
            "padding:6px 8px;border:2px solid #2e7d46;text-align:left;}QPushButton:pressed{background:#2bc060;}",
    "passed": "QPushButton{background:#34363d;color:#80828a;font-size:13px;border-radius:6px;padding:6px 8px;"
              "border:2px solid #3e4048;text-align:left;}",
}


class PointWidget(QFrame):
    """Un punto nella colonna: pulsante OSC o annotazione."""
    pressed = Signal(str)
    context = Signal(str, QPoint)

    def __init__(self, point, parent=None):
        super().__init__(parent)
        self.pid = point["id"]
        self.kind = point["kind"]
        self.setFixedWidth(GUTTER - 22)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.btn = None
        self.label = None
        if self.kind == "button":
            self.btn = QPushButton()
            self.btn.setFocusPolicy(Qt.NoFocus)
            self.btn.setMinimumHeight(38)
            self.btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.btn.clicked.connect(lambda: self.pressed.emit(self.pid))
            self.btn.setContextMenuPolicy(Qt.CustomContextMenu)
            self.btn.customContextMenuRequested.connect(
                lambda pos: self.context.emit(self.pid, self.btn.mapToGlobal(pos)))
            v.addWidget(self.btn)
        else:
            self.label = QLabel()
            self.label.setWordWrap(True)
            self.label.setTextFormat(Qt.PlainText)
            self.label.setContextMenuPolicy(Qt.CustomContextMenu)
            self.label.customContextMenuRequested.connect(
                lambda pos: self.context.emit(self.pid, self.label.mapToGlobal(pos)))
            v.addWidget(self.label)
        self.state = None
        self.set_point(point)

    def set_point(self, p):
        self.point = p
        if self.btn is not None:
            self._btn_text(False)
        else:
            self.label.setText((p.get("text") or "(annotazione vuota)") + ("  🖼" if p.get("images") else ""))
        self.state = None

    def _btn_text(self, sent):
        p = self.point
        n = len(p.get("commands") or [])
        ni = len(p.get("images") or [])
        self.btn.setText(("✔ " if sent else "▶ ") + (p.get("label") or "Pulsante") + ("" if n == 1 else f"  ×{n}")
                         + ("  🖼" if ni else ""))
        self.btn.setToolTip("\n".join(filter(None, [p.get("text"), f"{n} comandi OSC" if n != 1 else "",
                                                     f"{ni} immagini" if ni else ""])))

    def apply(self, state, enabled, blink_on, prep):
        key = (state, enabled, blink_on, prep)
        if key == self.state:
            return
        self.state = key
        if self.btn is not None:
            if prep:
                style = STYLE_BTN["future"]
            elif state == "next":
                style = STYLE_BTN["next" if blink_on else "next_dim"]
            elif state in ("armed", "sent"):
                style = STYLE_BTN[state]
            elif state in ("passed", "closed"):
                style = STYLE_BTN["passed"]
            else:
                style = STYLE_BTN["future"]
            self.btn.setStyleSheet(style)
            self._btn_text(not prep and state == "sent")
            # in preparazione il clic apre la modifica: il pulsante resta sempre attivo
            # resta sempre cliccabile (anche il tasto destro, per «Riattiva»): lo stato lo dice il colore
            self.btn.setEnabled(True)
            self.btn.setToolTip(self.btn.toolTip().split("\n—")[0] + ("" if prep or enabled else
                                "\n— già passato: tasto destro › Riattiva"))
        else:
            col = self.point.get("color") or "#e0b040"
            if not prep and state == "passed":
                css = ("background:#2c2e34;color:#77797f;border-left:4px solid #4a4c54;"
                       "border-radius:4px;padding:5px 7px;font-size:12px;")
            elif not prep and state == "next":
                bg = "#4a3f1c" if blink_on else "#33302a"
                css = (f"background:{bg};color:#ffffff;border-left:6px solid {col};border-radius:4px;"
                       "padding:5px 7px;font-size:13px;font-weight:bold;")
            else:
                css = (f"background:#33302a;color:#eeeeee;border-left:6px solid {col};border-radius:4px;"
                       "padding:5px 7px;font-size:13px;")
            self.label.setStyleSheet(css)

    def mousePressEvent(self, e):
        if self.label is not None and e.button() == Qt.LeftButton:
            self.pressed.emit(self.pid)
        super().mousePressEvent(e)


class Canvas(QWidget):
    wordMenu = Signal(int, int, QPoint)     # prima parola, ultima parola, posizione globale
    zoneResized = Signal(str, int, int, int, int)   # "excluded"|"improv", vecchio inizio/fine, nuovo inizio/fine
    wordDouble = Signal(int)
    pointPressed = Signal(str)
    pointContext = Signal(str, QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.doc = None
        self.points = []
        self.widgets = {}
        self.states = {}
        self.prep = True
        self.blink_on = True
        self.cur_word = -1
        self.scale = 1.0
        self.zoom = 1.0
        self.page_y = []
        self.page_w = 0
        self._cache = {}
        self._sel = None                    # (prima, ultima) parole selezionate col trascinamento
        self._press_word = None
        self.setMouseTracking(True)         # in preparazione: cursore sui bordi delle zone
        self._resize = None                 # trascinamento del bordo di una zona: {kind, a, b, side, na, nb}
        self.flash_pid = None               # punto da evidenziare (Comando precedente / successivo)
        self.flash_until = 0.0
        self.setAttribute(Qt.WA_OpaquePaintEvent)

    # ------------------------------------------------------------ impaginazione
    def set_doc(self, doc):
        self.doc = doc
        self._cache.clear()
        self.cur_word = -1
        self._sel = None
        for w in self.widgets.values():
            w.deleteLater()
        self.widgets = {}
        self.points = []

    def relayout(self, view_w):
        if self.doc is None:
            self.setFixedSize(max(200, view_w), 200)
            return
        maxw = max(w for w, _h in self.doc.page_sizes)
        avail = max(300, view_w - GUTTER - 2 * MARGIN)
        scale = max(0.5, min(3.0, avail / maxw)) * self.zoom
        if abs(scale - self.scale) > 1e-4:
            self.scale = scale
            self._cache.clear()
        y = MARGIN
        self.page_y = []
        for _w, h in self.doc.page_sizes:
            self.page_y.append(y)
            y += h * self.scale + GAP
        self.page_w = maxw * self.scale
        self.setFixedSize(int(max(view_w, MARGIN * 2 + self.page_w + GUTTER)), int(y + MARGIN))
        self.place_points()
        self.update()

    def word_rect(self, i):
        w = self.doc.words[i]
        s = self.scale
        y = self.page_y[w.page]
        return QRectF(MARGIN + w.x0 * s, y + w.y0 * s, (w.x1 - w.x0) * s, (w.y1 - w.y0) * s)

    def line_rect(self, line_i):
        ln = self.doc.lines[line_i]
        s = self.scale
        y = self.page_y[ln.page]
        return QRectF(MARGIN + ln.x0 * s - 4, y + ln.y0 * s - 1, (ln.x1 - ln.x0) * s + 8, (ln.y1 - ln.y0) * s + 2)

    def _page_at(self, y):
        for k, py in enumerate(self.page_y):
            if py <= y <= py + self.doc.page_sizes[k][1] * self.scale:
                return k
        return None

    def word_at(self, pos):
        if self.doc is None:
            return None
        k = self._page_at(pos.y())
        if k is None:
            return None
        x = (pos.x() - MARGIN) / self.scale
        y = (pos.y() - self.page_y[k]) / self.scale
        return self.doc.word_at(k, x, y)

    # ------------------------------------------------------------ punti
    def set_points(self, points):
        self.points = list(points)
        ids = {p["id"] for p in self.points}
        for pid in list(self.widgets):
            if pid not in ids:
                self.widgets.pop(pid).deleteLater()
        for p in self.points:
            w = self.widgets.get(p["id"])
            if w is None or w.kind != p["kind"]:
                if w is not None:
                    w.deleteLater()
                w = PointWidget(p, self)
                w.pressed.connect(self.pointPressed)
                w.context.connect(self.pointContext)
                self.widgets[p["id"]] = w
                w.show()
            else:
                w.set_point(p)
        self.place_points()
        self.apply_states()

    def place_points(self):
        if self.doc is None or not self.page_y:
            return
        x = int(MARGIN + self.page_w + 14)
        bottom = -1e9
        items = []
        for p in self.points:
            if 0 <= p["word"] < len(self.doc.words):
                r = self.word_rect(p["word"])
                items.append((r.center().y(), p))
        items.sort(key=lambda t: (t[0], t[1]["kind"] != "note"))
        for cy, p in items:
            w = self.widgets.get(p["id"])
            if w is None:
                continue
            w.adjustSize()
            h = max(w.sizeHint().height(), 30)
            y = max(cy - h / 2, bottom + 4)
            w.setGeometry(x, int(y), GUTTER - 22, int(h))
            bottom = y + h
        self.update()

    def set_states(self, states):
        """{pid: (stato, attivo)} stato: future | next | passed | closed | armed"""
        self.states = states
        self.apply_states()

    def apply_states(self):
        for pid, w in self.widgets.items():
            st, en = self.states.get(pid, ("future", True))
            w.apply(st, en, self.blink_on, self.prep)
        self.update()

    def set_blink(self, on):
        if on != self.blink_on:
            self.blink_on = on
            self.apply_states()

    # ------------------------------------------------------------ disegno
    def _image(self, k):
        dpr = self.devicePixelRatioF()
        key = (k, round(self.scale * dpr, 4))
        img = self._cache.get(key)
        if img is None:
            data, w, h, stride = self.doc.render(k, self.scale * dpr)
            img = QImage(data, w, h, stride, QImage.Format_RGB888).copy()
            img.setDevicePixelRatio(dpr)
            if len(self._cache) > 24:
                self._cache.clear()
            self._cache[key] = img
        return img

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(e.rect(), CANVAS_BG)
        if self.doc is None:
            p.setPen(QColor(150, 150, 158))
            f = QFont(self.font())
            f.setPointSize(14)
            p.setFont(f)
            p.drawText(self.rect(), Qt.AlignCenter, "Apri un copione in PDF (File › Apri copione PDF…)\n"
                                                    "oppure un progetto (File › Apri progetto…)")
            p.end()
            return
        vis = e.rect()
        s = self.scale
        p.setRenderHint(QPainter.Antialiasing)
        for k, py in enumerate(self.page_y):
            pw, ph = self.doc.page_sizes[k]
            r = QRectF(MARGIN, py, pw * s, ph * s)
            if not r.intersects(QRectF(vis)):
                continue
            p.fillRect(r.translated(3, 3), QColor(0, 0, 0, 120))
            p.fillRect(r, PAGE_BG)
            p.drawImage(r.topLeft(), self._image(k))
            a, b = self.doc.page_words[k]
            zone = set()
            for za, zb in self.doc.improv:
                if za < b and zb >= a:
                    zone.update(range(max(a, za), min(b, zb + 1)))
            if self.prep:
                # parole escluse dal riconoscimento (didascalie, titoli…): velo azzurro
                p.setPen(Qt.NoPen)
                for i in range(a, b):
                    w = self.doc.words[i]
                    if not w.spoken and i not in zone:
                        p.fillRect(self.word_rect(i).adjusted(-1, 0, 1, 0), QColor(70, 120, 200, 55))
            if zone:
                # parti improvvisate: velo viola e barra nel margine sinistro
                p.setPen(Qt.NoPen)
                lines = set()
                for i in zone:
                    p.fillRect(self.word_rect(i).adjusted(-1, 0, 1, 0), QColor(150, 80, 210, 70))
                    lines.add(self.doc.words[i].line)
                for li in lines:
                    lr = self.line_rect(li)
                    p.fillRect(QRectF(MARGIN + 6, lr.top(), 5, lr.height()), QColor(150, 80, 210, 200))
            if self._sel and not (self._sel[1] < a or self._sel[0] >= b):
                for i in range(max(a, self._sel[0]), min(b, self._sel[1] + 1)):
                    p.fillRect(self.word_rect(i).adjusted(-1, -1, 1, 1), QColor(52, 100, 170, 110))
            # numero di pagina
            p.setPen(QColor(120, 120, 128))
            p.drawText(QRectF(MARGIN, py + ph * s + 1, pw * s, GAP - 2), Qt.AlignCenter, f"{k + 1}")
        # bordo di una zona in trascinamento: anteprima del nuovo tratto
        if self._resize:
            r = self._resize
            col = QColor(70, 120, 200) if r["kind"] == "excluded" else QColor(150, 80, 210)
            for i in range(min(r["na"], r["a"]), max(r["nb"], r["b"]) + 1):
                inside = r["na"] <= i <= r["nb"]
                wr = self.word_rect(i).adjusted(-1, -1, 1, 1)
                if inside:
                    p.fillRect(wr, QColor(col.red(), col.green(), col.blue(), 120))
                else:                       # parole che escono dalla zona
                    p.setPen(QPen(QColor(255, 255, 255, 200), 1, Qt.DashLine))
                    p.setBrush(Qt.NoBrush)
                    p.drawRect(wr)
            for i, left in ((r["na"], True), (r["nb"], False)):
                wr = self.word_rect(i)
                x = wr.left() - 2 if left else wr.right() + 2
                p.setPen(QPen(QColor(255, 255, 255), 3))
                p.drawLine(QPointF(x, wr.top() - 3), QPointF(x, wr.bottom() + 3))
        # punto raggiunto con «Comando precedente / successivo»
        if self.flash_pid and time.monotonic() < self.flash_until:
            fp = next((q for q in self.points if q["id"] == self.flash_pid), None)
            if fp is not None:
                lr = self.line_rect(self.doc.words[fp["word"]].line)
                p.setPen(QPen(QColor(255, 150, 0), 3))
                p.setBrush(QColor(255, 150, 0, 40))
                p.drawRoundedRect(lr.adjusted(-6, -4, 6, 4), 4, 4)
        # riga e parola correnti
        if 0 <= self.cur_word < len(self.doc.words):
            w = self.doc.words[self.cur_word]
            p.setPen(Qt.NoPen)
            p.fillRect(self.line_rect(w.line), QColor(255, 196, 0, 60))
            wr = self.word_rect(self.cur_word).adjusted(-2, -1, 2, 1)
            p.setBrush(QColor(255, 180, 0, 120))
            p.drawRoundedRect(wr, 2, 2)
            # freccia nel margine sinistro
            p.setBrush(QColor(255, 180, 0))
            cy = wr.center().y()
            x0 = MARGIN - 14
            p.drawPolygon([QPointF(x0, cy - 6), QPointF(x0 + 10, cy), QPointF(x0, cy + 6)])
        # ancore dei punti: parola sottolineata e collegamento alla colonna
        right = MARGIN + self.page_w
        for pt in self.points:
            if not 0 <= pt["word"] < len(self.doc.words):
                continue
            st, _en = self.states.get(pt["id"], ("future", True))
            col = QColor(GO_GREEN) if pt["kind"] == "button" else QColor(pt.get("color") or "#e0b040")
            if not self.prep and st in ("passed", "closed"):
                col = QColor(110, 112, 120)
            elif not self.prep and st == "armed":
                col = QColor("#e0b040")
            if not self.prep and st == "next" and self.blink_on:
                col = col.lighter(150)
            wr = self.word_rect(pt["word"])
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(col.red(), col.green(), col.blue(), 70))
            p.drawRoundedRect(wr.adjusted(-2, -1, 2, 1), 2, 2)
            p.setPen(QPen(col, 2.5))
            p.drawLine(QPointF(wr.left() - 1, wr.bottom() + 1), QPointF(wr.right() + 1, wr.bottom() + 1))
            wdg = self.widgets.get(pt["id"])
            if wdg is not None:
                # il collegamento parte dalla fine della riga: non copre il testo che segue l'ancora
                lr = self.line_rect(self.doc.words[pt["word"]].line)
                gy = wdg.geometry().center().y()
                cy = wr.center().y()
                p.setPen(QPen(col, 1.4, Qt.DashLine))
                p.drawLine(QPointF(lr.right() + 2, cy), QPointF(right + 4, cy))
                p.drawLine(QPointF(right + 4, cy), QPointF(wdg.x() - 2, gy))
                p.setPen(Qt.NoPen)
                p.setBrush(col)
                p.drawEllipse(QPointF(lr.right() + 2, cy), 3, 3)
        p.end()

    # ------------------------------------------------------------ mouse
    # ------------------------------------------------------------ bordi delle zone (preparazione)
    def _edge_at(self, pos):
        """(tipo, inizio, fine, lato) se il mouse è sul bordo di una zona azzurra o viola."""
        if self.doc is None or not self.prep:
            return None
        i = self.word_at(pos)
        if i is None:
            return None
        wr = self.word_rect(i)
        x = pos.x()
        tol = max(5.0, min(9.0, wr.width() * 0.3))
        zones = [("excluded", a, b) for a, b in self.doc.excluded_runs()]
        zones += [("improv", a, b) for a, b in self.doc.improv]
        for kind, a, b in zones:
            if a <= i <= b:
                if i == a and x <= wr.left() + tol:
                    return kind, a, b, "start"
                if i == b and x >= wr.right() - tol:
                    return kind, a, b, "end"
        return None

    def mousePressEvent(self, e):
        i = self.word_at(e.position())
        edge = self._edge_at(e.position()) if e.button() == Qt.LeftButton else None
        if edge:
            kind, a, b, side = edge
            self._resize = {"kind": kind, "a": a, "b": b, "side": side, "na": a, "nb": b}
            self._sel = None
            self.update()
            return
        if e.button() == Qt.LeftButton and self.prep:
            self._press_word = i
            self._sel = (i, i) if i is not None else None
            self.update()
        elif e.button() == Qt.RightButton and i is not None:
            if self._sel and self._sel[0] <= i <= self._sel[1]:
                self.wordMenu.emit(self._sel[0], self._sel[1], e.globalPosition().toPoint())
            else:
                self._sel = (i, i)
                self.update()
                self.wordMenu.emit(i, i, e.globalPosition().toPoint())

    def mouseMoveEvent(self, e):
        if self._resize:
            i = self.word_at(e.position())
            r = self._resize
            if i is not None and self.doc.words[i].page == self.doc.words[r["a"]].page:
                if r["side"] == "start":
                    r["na"], r["nb"] = min(i, r["b"]), r["b"]
                else:
                    r["na"], r["nb"] = r["a"], max(i, r["a"])
                self.update()
            return
        if not (e.buttons() & Qt.LeftButton):
            self.setCursor(Qt.SizeHorCursor if self._edge_at(e.position()) else Qt.ArrowCursor)
            return
        if self._press_word is not None and self.prep:
            i = self.word_at(e.position())
            if i is not None:
                a, b = sorted((self._press_word, i))
                if (a, b) != self._sel:
                    self._sel = (a, b)
                    self.update()

    def mouseReleaseEvent(self, e):
        if self._resize:
            r, self._resize = self._resize, None
            self.update()
            if (r["na"], r["nb"]) != (r["a"], r["b"]):
                self.zoneResized.emit(r["kind"], r["a"], r["b"], r["na"], r["nb"])
            return
        if e.button() == Qt.LeftButton and self.prep and self._press_word is not None:
            self._press_word = None
            if self._sel:
                self.wordMenu.emit(self._sel[0], self._sel[1], e.globalPosition().toPoint())

    def mouseDoubleClickEvent(self, e):
        if not self.prep:
            i = self.word_at(e.position())
            if i is not None:
                self.wordDouble.emit(i)

    def clear_selection(self):
        if self._sel:
            self._sel = None
            self.update()


class DocView(QScrollArea):
    """Scorrimento del copione: segue la parola corrente, si ferma per qualche secondo se scorri a mano."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.canvas = Canvas()
        self.setWidget(self.canvas)
        self.setWidgetResizable(False)
        self.setAlignment(Qt.AlignHCenter)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet("QScrollArea{background:#1c1d22;}")
        self.follow = True
        self.manual_until = 0.0
        self.anchor = 0.35              # la riga corrente sta a questa altezza della finestra
        self.anim = QPropertyAnimation(self.verticalScrollBar(), b"value", self)
        self.anim.setDuration(380)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.verticalScrollBar().sliderPressed.connect(self._manual)

    def _manual(self):
        self.manual_until = time.monotonic() + 5.0
        self.anim.stop()

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            z = self.canvas.zoom * (1.1 if e.angleDelta().y() > 0 else 1 / 1.1)
            self.set_zoom(z)
            return
        self._manual()
        super().wheelEvent(e)

    def set_zoom(self, z):
        bar = self.verticalScrollBar()
        frac = bar.value() / max(1, self.canvas.height())
        self.canvas.zoom = max(0.5, min(2.5, z))
        self.relayout()
        bar.setValue(int(frac * self.canvas.height()))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.relayout()

    def relayout(self):
        self.canvas.relayout(self.viewport().width())

    def ensure_word(self, i, animate=True, force=False):
        c = self.canvas
        if c.doc is None or not 0 <= i < len(c.doc.words):
            return
        if not force and (not self.follow or time.monotonic() < self.manual_until):
            return
        r = c.line_rect(c.doc.words[i].line)
        target = int(r.center().y() - self.viewport().height() * self.anchor)
        bar = self.verticalScrollBar()
        target = max(bar.minimum(), min(bar.maximum(), target))
        if abs(target - bar.value()) < 3:
            return
        self.anim.stop()
        if animate:
            self.anim.setStartValue(bar.value())
            self.anim.setEndValue(target)
            self.anim.start()
        else:
            bar.setValue(target)
