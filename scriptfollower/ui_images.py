"""Immagini e foto allegate ai pulsanti e alle note: miniature sotto le note e finestra di visualizzazione.

Le immagini vengono copiate nella cartella «<nome del copione>_immagini» accanto al PDF, così il progetto si
sposta insieme (PDF + .sfollow + cartella) su un altro computer o una chiavetta. Nel progetto si salva il
percorso relativo alla cartella del PDF.
"""
import os
import shutil

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QImageReader, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

IMAGE_FILTER = "Immagini (*.png *.jpg *.jpeg *.gif *.bmp *.webp *.tif *.tiff);;Tutti i file (*)"
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff")
_thumbs = {}


def base_dir(project):
    return os.path.dirname(project.pdf) if project.pdf else (os.path.dirname(project.path) if project.path else "")


def media_dir(project):
    stem = os.path.splitext(os.path.basename(project.pdf or project.path or "copione"))[0]
    return os.path.join(base_dir(project), f"{stem}_immagini")


def abs_path(project, rel):
    if os.path.isabs(rel):
        return rel
    return os.path.normpath(os.path.join(base_dir(project), rel.replace("/", os.sep)))


def import_images(project, paths):
    """Copia le immagini nella cartella del progetto. Ritorna i percorsi relativi (con «/»)."""
    dest = media_dir(project)
    os.makedirs(dest, exist_ok=True)
    out = []
    for src in paths:
        if not os.path.isfile(src):
            continue
        name = os.path.basename(src)
        target = os.path.join(dest, name)
        if os.path.abspath(src) != os.path.abspath(target):
            stem, ext = os.path.splitext(name)
            k = 2
            while os.path.exists(target):
                if os.path.getsize(target) == os.path.getsize(src):      # stessa immagine già copiata
                    break
                target = os.path.join(dest, f"{stem}-{k}{ext}")
                k += 1
            if not os.path.exists(target):
                shutil.copy2(src, target)
        out.append(os.path.relpath(target, base_dir(project)).replace(os.sep, "/"))
    return out


def load_pixmap(path, size=None):
    """Immagine (orientata secondo l'EXIF delle foto), ridotta a size (QSize) se indicato."""
    r = QImageReader(path)
    r.setAutoTransform(True)
    if size is not None:
        s = r.size()
        if s.isValid() and (s.width() > size.width() or s.height() > size.height()):
            r.setScaledSize(s.scaled(size, Qt.KeepAspectRatio))
    img = r.read()
    return QPixmap.fromImage(img) if not img.isNull() else QPixmap()


def thumbnail(path, h=72):
    try:
        key = (path, h, os.path.getmtime(path))
    except OSError:
        return QPixmap()
    pm = _thumbs.get(key)
    if pm is None:
        pm = _thumbs[key] = load_pixmap(path, QSize(h * 3, h))
        if len(_thumbs) > 300:
            _thumbs.clear()
    return pm


class _Thumb(QLabel):
    clicked = Signal(int)

    def __init__(self, i, pm, tip, parent=None):
        super().__init__(parent)
        self.i = i
        self.setPixmap(pm)
        self.setToolTip(tip)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("QLabel{border:1px solid #4a4c54;border-radius:3px;padding:1px;background:#1c1d22;}"
                           "QLabel:hover{border-color:#e0b040;}")

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.i)


class ThumbStrip(QWidget):
    """Fila di miniature: un clic apre l'immagine in una finestra."""
    opened = Signal(int)

    def __init__(self, h=64, parent=None):
        super().__init__(parent)
        self.h = h
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(6)
        self.paths = []
        self.setVisible(False)

    def set_paths(self, paths):
        paths = list(paths or [])
        if paths == self.paths:
            return
        self.paths = paths
        while self.lay.count():
            it = self.lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for i, p in enumerate(paths):
            pm = thumbnail(p, self.h)
            if pm.isNull():
                t = QLabel("⚠")
                t.setToolTip(f"Immagine non trovata: {p}")
                t.setStyleSheet("color:#e0a030;")
                self.lay.addWidget(t)
                continue
            t = _Thumb(i, pm, os.path.basename(p) + " — clic per aprirla")
            t.clicked.connect(self.opened)
            self.lay.addWidget(t)
        self.lay.addStretch(1)
        self.setVisible(bool(paths))


class ImageViewer(QDialog):
    """Finestra con l'immagine adattata alla finestra; ◀ ▶ tra le immagini dello stesso punto, F schermo intero."""

    def __init__(self, paths, index=0, title="", parent=None):
        super().__init__(parent)
        self.setModal(False)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.paths = list(paths)
        self.i = max(0, min(index, len(self.paths) - 1))
        self.title = title
        self.setStyleSheet("QDialog{background:#111214;}")
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        self.view = QLabel()
        self.view.setAlignment(Qt.AlignCenter)
        self.view.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.view.setMinimumSize(200, 150)
        v.addWidget(self.view, 1)
        bar = QHBoxLayout()
        self.prev = QPushButton("◀")
        self.next = QPushButton("▶")
        self.full = QPushButton("⛶ Schermo intero")
        self.cap = QLabel()
        self.cap.setStyleSheet("color:#aaa;")
        for b in (self.prev, self.next, self.full):
            b.setFocusPolicy(Qt.NoFocus)
        self.prev.clicked.connect(lambda: self.step(-1))
        self.next.clicked.connect(lambda: self.step(1))
        self.full.clicked.connect(self.toggle_full)
        bar.addWidget(self.prev)
        bar.addWidget(self.next)
        bar.addWidget(self.cap, 1)
        bar.addWidget(self.full)
        v.addLayout(bar)
        for key, fn in (("Left", lambda: self.step(-1)), ("Right", lambda: self.step(1)), ("F", self.toggle_full)):
            QShortcut(QKeySequence(key), self, activated=fn)
        self._pm = QPixmap()
        self.resize(900, 650)
        self._load()

    def _load(self):
        p = self.paths[self.i] if self.paths else ""
        self._pm = load_pixmap(p) if p else QPixmap()
        n = len(self.paths)
        self.prev.setVisible(n > 1)
        self.next.setVisible(n > 1)
        name = os.path.basename(p)
        self.cap.setText(f"{name}" + (f"   ({self.i + 1}/{n})" if n > 1 else "")
                         + ("" if not self._pm.isNull() else "   — immagine non trovata"))
        self.setWindowTitle(f"{self.title} — {name}" if self.title else name)
        self._fit()

    def _fit(self):
        if self._pm.isNull():
            self.view.setText("Immagine non trovata")
            return
        self.view.setPixmap(self._pm.scaled(self.view.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def step(self, d):
        if self.paths:
            self.i = (self.i + d) % len(self.paths)
            self._load()

    def toggle_full(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()
