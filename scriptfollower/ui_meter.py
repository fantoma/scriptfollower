"""Meter dell'ingresso con la soglia del gate regolabile trascinando il cursore sul meter stesso.

Due barre: il livello dell'ingresso e quello che passa il gate (cioè quello che arriva al riconoscitore);
il LED mostra quando il gate è aperto.
"""
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from .audio_input import MIN_METER_DB, lin_to_db
from .i18n import _


class GateMeter(QWidget):
    thresholdChanged = Signal(float)

    def __init__(self, audio, compact=False, parent=None):
        super().__init__(parent)
        self.audio = audio
        self.compact = compact
        self.threshold = -45.0
        self.gate_on = True
        self.interactive = True
        self._drag = False
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus if not compact else Qt.NoFocus)
        if compact:
            self.setFixedSize(230, 56)
        else:
            self.setMinimumSize(380, 86)
        self.setToolTip(_("Livello dell'ingresso (sopra) e segnale che passa il gate verso il riconoscitore (sotto).\nTrascina il cursore bianco per regolare la soglia del gate."))

    def sizeHint(self):
        return QSize(230, 56) if self.compact else QSize(460, 86)

    # ------------------------------------------------------------ geometria
    def _bars(self):
        led_w = 0 if self.compact else 92
        top = 4
        h = 12 if self.compact else 20
        bar = QRectF(6, top, self.width() - 12 - led_w - (6 if led_w else 0), h)
        post = QRectF(bar.left(), bar.bottom() + 4, bar.width(), 6 if self.compact else 9)
        led = QRectF(bar.right() + 6, top, led_w, h + 4 + post.height()) if led_w else None
        return bar, post, led

    @staticmethod
    def _x(db, r):
        f = (max(MIN_METER_DB, min(0.0, db)) - MIN_METER_DB) / -MIN_METER_DB
        return r.left() + f * r.width()

    def _db_at(self, x):
        bar = self._bars()[0]
        f = (x - bar.left()) / max(1.0, bar.width())
        return round(max(MIN_METER_DB, min(0.0, MIN_METER_DB + f * -MIN_METER_DB)), 1)

    # ------------------------------------------------------------ disegno
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        a = self.audio
        bar, post, led = self._bars()
        f = QFont(self.font())
        f.setPointSizeF(max(6.5, f.pointSizeF() * (0.72 if self.compact else 0.85)))
        p.setFont(f)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(20, 20, 24) if self.compact else QColor(30, 32, 38))
        p.drawRoundedRect(bar, 3, 3)
        p.drawRoundedRect(post, 2, 2)
        listening = a is not None and a.listening
        db = lin_to_db(a.meter) if listening else -120.0
        thr = self.threshold
        if db > MIN_METER_DB:
            x = self._x(db, bar)
            closed = self.gate_on and not a.gate
            col = QColor(110, 115, 125) if closed else (
                QColor(70, 200, 100) if db < -12 else QColor(230, 200, 50) if db < -3 else QColor(220, 60, 50))
            p.setBrush(col)
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), x - bar.left(), bar.height()), 3, 3)
        dbp = lin_to_db(a.meter_post) if listening else -120.0
        if dbp > MIN_METER_DB:
            p.setBrush(QColor(52, 130, 210))
            p.drawRoundedRect(QRectF(post.left(), post.top(), self._x(dbp, post) - post.left(), post.height()), 2, 2)
        # zona sotto soglia (gate chiuso) velata
        tx = self._x(thr, bar)
        if self.gate_on:
            p.setBrush(QColor(0, 0, 0, 90))
            p.drawRect(QRectF(bar.left(), bar.top(), tx - bar.left(), post.bottom() - bar.top()))
            # cursore della soglia
            p.setPen(QPen(QColor(255, 255, 255), 2))
            p.drawLine(QPointF(tx, bar.top() - 2), QPointF(tx, post.bottom() + 1))
            tri = QPainterPath()
            y = post.bottom() + 1
            s = 5 if self.compact else 7
            tri.moveTo(tx, y - 1)
            tri.lineTo(tx - s, y + s)
            tri.lineTo(tx + s, y + s)
            tri.closeSubpath()
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255) if self.interactive else QColor(170, 170, 175))
            p.drawPath(tri)
        txt = self.palette().windowText().color()
        p.setPen(txt)
        ty = post.bottom() + (8 if self.compact else 10)
        for mark in ((-60, -40, -20, 0) if self.compact else (-60, -48, -36, -24, -12, 0)):
            x = self._x(mark, bar)
            p.drawText(QRectF(x - 14, ty, 28, 12), Qt.AlignCenter, str(mark))
        if self.compact:
            p.setPen(QColor(150, 150, 158))
            label = _("gate {0:.0f} dB").format(thr) if self.gate_on else _("gate spento")
            if not listening:
                label = _("ingresso chiuso")
            p.drawText(QRectF(bar.left(), ty + 10, bar.width(), 12), Qt.AlignLeft | Qt.AlignVCenter, label)
            if listening and a.gate and self.gate_on:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(60, 200, 110))
                p.drawEllipse(QRectF(bar.right() - 8, ty + 12, 8, 8))
        if led is not None:
            p.setPen(Qt.NoPen)
            opened = listening and a.gate
            p.setBrush(QColor(40, 150, 80) if opened else QColor(60, 62, 70))
            p.drawRoundedRect(led, 4, 4)
            p.setPen(QColor(255, 255, 255))
            p.drawText(led, Qt.AlignCenter, (_("GATE\nAPERTO") if opened else _("gate\nchiuso")) if self.gate_on
                       else (_("gate\nspento") if listening else _("non in\nascolto")))
        p.end()

    # ------------------------------------------------------------ cursore della soglia
    def _near_handle(self, x):
        bar = self._bars()[0]
        return abs(x - self._x(self.threshold, bar)) <= 10

    def mousePressEvent(self, e):
        if not (self.interactive and self.gate_on) or e.button() != Qt.LeftButton:
            return super().mousePressEvent(e)
        self._drag = True
        self.set_threshold(self._db_at(e.position().x()), emit=True)

    def mouseMoveEvent(self, e):
        if self._drag:
            self.set_threshold(self._db_at(e.position().x()), emit=True)
        else:
            on = self.interactive and self.gate_on and self._near_handle(e.position().x())
            self.setCursor(Qt.SizeHorCursor if on else Qt.ArrowCursor)

    def mouseReleaseEvent(self, e):
        self._drag = False

    def wheelEvent(self, e):
        if not (self.interactive and self.gate_on):
            return super().wheelEvent(e)
        step = 1.0 if e.angleDelta().y() > 0 else -1.0
        self.set_threshold(self.threshold + step, emit=True)

    def keyPressEvent(self, e):
        if self.interactive and self.gate_on and e.key() in (Qt.Key_Left, Qt.Key_Right):
            self.set_threshold(self.threshold + (1 if e.key() == Qt.Key_Right else -1), emit=True)
        else:
            super().keyPressEvent(e)

    def set_threshold(self, db, emit=False):
        db = round(max(MIN_METER_DB, min(0.0, float(db))), 1)
        if db != self.threshold:
            self.threshold = db
            self.update()
            if emit:
                self.thresholdChanged.emit(db)
