"""Piccoli aiuti per le finestre: dimensioni che stanno nello schermo e contenuti scorrevoli."""
from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QLabel, QScrollArea, QSizePolicy


def screen_rect(w):
    """Area utile dello schermo della finestra (o del suo genitore, o quello principale)."""
    s = None
    for x in (w, w.parentWidget() if w is not None else None):
        if x is not None and x.screen() is not None:
            s = x.screen()
            break
    s = s or QGuiApplication.primaryScreen()
    return s.availableGeometry()


def fit_to_screen(w, width, height, margin=0.94):
    """Ridimensiona w a (width, height), ma mai oltre lo schermo (portatili, schermi piccoli, proiettori)."""
    a = screen_rect(w)
    w.resize(max(200, min(width, int(a.width() * margin))), max(150, min(height, int(a.height() * margin))))


def clamp_to_screen(w):
    """Dopo restoreGeometry: se la finestra è più grande dello schermo o fuori dallo schermo, la si riporta dentro."""
    a = screen_rect(w)
    g = w.frameGeometry()
    width, height = min(g.width(), a.width()), min(g.height(), a.height())
    if (width, height) != (g.width(), g.height()):
        w.resize(w.width() - (g.width() - width), w.height() - (g.height() - height))
    g = w.frameGeometry()
    x = min(max(g.x(), a.left()), a.right() - g.width() + 1)
    y = min(max(g.y(), a.top()), a.bottom() - g.height() + 1)
    if (x, y) != (g.x(), g.y()):
        w.move(x, y)


def scroll_wrap(widget):
    """Contenuto lungo in un'area scorrevole senza cornice: la finestra può diventare più bassa del contenuto."""
    sa = QScrollArea()
    sa.setWidget(widget)
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    return sa


class ElidedLabel(QLabel):
    """Etichetta di una riga che si accorcia con «…» invece di allargare la finestra (testo intero nel tooltip)."""

    def __init__(self, text="", parent=None, min_width=60):
        super().__init__(parent)
        self._full = ""
        self._min = min_width
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.setText(text)

    def setText(self, text):
        self._full = str(text or "")
        self._elide()

    def text(self):
        return self._full

    def _elide(self):
        shown = self.fontMetrics().elidedText(self._full, Qt.ElideRight, max(10, self.width()))
        super().setText(shown)
        self.setToolTip(self._full if shown != self._full else "")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.FontChange:
            self._elide()

    def sizeHint(self):
        s = super().sizeHint()
        return QSize(self.fontMetrics().horizontalAdvance(self._full) + 4, s.height())

    def minimumSizeHint(self):
        return QSize(self._min, super().minimumSizeHint().height())
