"""Immagini e foto allegate ai pulsanti e alle note: miniature sotto le note e finestra di visualizzazione.

Le immagini viaggiano dentro il PDF (e nel JSON esportato). Sul computer stanno in un deposito nella cartella
dei dati, con un nome che comincia con l'impronta del contenuto («3f2a9c41b0d7_foto.jpg»): due immagini diverse
non si sovrascrivono mai e la stessa immagine non si copia due volte. Nei punti si salva «media:<nome>».
I vecchi progetti avevano la cartella «<copione>_immagini» accanto al PDF: quei percorsi si leggono ancora e
al primo salvataggio passano nel deposito.
"""
import hashlib
import os

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QImageReader, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget
from .i18n import _
from .system import data_dir
from .ui_util import fit_to_screen

IMAGE_FILTER = "Immagini (*.png *.jpg *.jpeg *.gif *.bmp *.webp *.tif *.tiff);;Tutti i file (*)"
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff")
_thumbs = {}


MEDIA = "media:"


def store_dir():
    return data_dir("immagini")


def base_dir(project):
    return os.path.dirname(project.pdf) if project.pdf else ""


def _safe(name):
    name = os.path.basename(str(name).replace("\\", "/"))
    return "".join(c for c in name if c not in '<>:"/\\|?*\0').strip(" .") or "immagine"


def store_bytes(data, name):
    """Mette i byte nel deposito e ritorna il riferimento «media:<impronta>_<nome>»."""
    base = _safe(name)
    h = hashlib.sha1(data).hexdigest()[:12]
    fn = base if base[:13] == h + "_" else f"{h}_{base}"
    path = os.path.join(store_dir(), fn)
    if not os.path.isfile(path) or os.path.getsize(path) != len(data):
        os.makedirs(store_dir(), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    return MEDIA + fn


def abs_path(project, ref):
    if ref.startswith(MEDIA):
        return os.path.join(store_dir(), _safe(ref[len(MEDIA):]))
    if os.path.isabs(ref):
        return ref
    return os.path.normpath(os.path.join(base_dir(project), ref.replace("/", os.sep)))


def import_images(project, paths):
    """Copia le immagini nel deposito. Ritorna i riferimenti «media:…»."""
    out = []
    for src in paths:
        if os.path.isfile(src):
            with open(src, "rb") as f:
                out.append(store_bytes(f.read(), os.path.basename(src)))
    return out


def gather(project):
    """Immagini da incorporare: ({nome: bytes}, [riferimenti mancanti]). I vecchi percorsi accanto al PDF
    passano nel deposito (il riferimento nel punto diventa «media:…»)."""
    images, missing = {}, []
    for p in project.points:
        refs = []
        for ref in p.get("images") or []:
            path = abs_path(project, ref)
            try:
                with open(path, "rb") as f:
                    data = f.read()
            except OSError:
                missing.append(ref)
                refs.append(ref)
                continue
            new = ref if ref.startswith(MEDIA) else store_bytes(data, os.path.basename(path))
            images[new[len(MEDIA):]] = data
            refs.append(new)
        if refs != (p.get("images") or []):
            p["images"] = refs
    return images, missing


def restore(images):
    """Immagini lette dal PDF o dal JSON: nel deposito (quelle che mancano)."""
    for name, data in (images or {}).items():
        try:
            store_bytes(data, name)
        except OSError:
            pass


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
                t.setToolTip(_('Immagine non trovata: {0}').format(p))
                t.setStyleSheet("color:#e0a030;")
                self.lay.addWidget(t)
                continue
            t = _Thumb(i, pm, os.path.basename(p) + " — " + _("clic per aprirla"))
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
        self.full = QPushButton(_('⛶ Schermo intero'))
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
        fit_to_screen(self, 900, 650)
        self._load()

    def _load(self):
        p = self.paths[self.i] if self.paths else ""
        self._pm = load_pixmap(p) if p else QPixmap()
        n = len(self.paths)
        self.prev.setVisible(n > 1)
        self.next.setVisible(n > 1)
        name = os.path.basename(p)
        self.cap.setText(f"{name}" + (f"   ({self.i + 1}/{n})" if n > 1 else "")
                         + ("" if not self._pm.isNull() else "   — " + _("immagine non trovata")))
        self.setWindowTitle(f"{self.title} — {name}" if self.title else name)
        self._fit()

    def _fit(self):
        if self._pm.isNull():
            self.view.setText(_('Immagine non trovata'))
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
