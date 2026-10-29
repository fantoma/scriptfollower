"""Finestra «Lingue del riconoscimento»: modelli vocali installati e da scaricare."""
import os
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel, QProgressBar,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from . import languages as L
from .i18n import _
from .ui_util import fit_to_screen
from .system import bundle_dir


class _Relay(QObject):
    progress = Signal(int, int)
    done = Signal(str, str, str)                 # lingua, percorso, errore


def engine_ok(lang):
    if L.LANGS[lang]["engine"] == "whisper":
        from .whisper_engine import available
        return available()
    from .recognizer import vosk
    return vosk is not None


class LanguagesDialog(QDialog):
    def __init__(self, parent=None, highlight=None):
        super().__init__(parent)
        self.setWindowTitle(_("Lingue del riconoscimento"))
        self.busy = None
        v = QVBoxLayout(self)
        intro = QLabel(_("Il programma comprende il parlato nella lingua del copione. L'italiano è incluso; le altre "
                         "lingue si scaricano una volta sola (servono Internet e un po' di spazio). Italiano e "
                         "inglese usano Vosk; lo sloveno usa Whisper, più pesante per il computer e con circa "
                         "1–2 secondi di ritardo in più."))
        intro.setWordWrap(True)
        v.addWidget(intro)
        self.table = QTableWidget(len(L.ORDER), 5)
        self.table.setHorizontalHeaderLabels([_("Lingua"), _("Motore"), _("Dimensione"), _("Stato"), ""])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        hh = self.table.horizontalHeader()
        for c in range(4):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        v.addWidget(self.table, 1)
        self.bar = QProgressBar()
        self.bar.setVisible(False)
        v.addWidget(self.bar)
        self.msg = QLabel()
        self.msg.setWordWrap(True)
        v.addWidget(self.msg)
        where = QLabel(_("I modelli scaricati vanno in: {path}").format(path=L.download_dir()))
        where.setStyleSheet("color:#888; font-size:11px;")
        where.setWordWrap(True)
        where.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(where)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.relay = _Relay()
        self.relay.progress.connect(self._progress)
        self.relay.done.connect(self._done)
        self.refresh()
        if highlight in L.LANGS:
            self.table.selectRow(L.ORDER.index(highlight))
        fit_to_screen(self, 720, 360)

    def refresh(self):
        b = bundle_dir()
        for r, code in enumerate(L.ORDER):
            info = L.LANGS[code]
            path = L.find_model(code)
            bundled = bool(path and b and os.path.realpath(path).startswith(os.path.realpath(b)))
            cells = [info["name"], "Vosk" if info["engine"] == "vosk" else "Whisper", f"{info['mb']} MB"]
            if not engine_ok(code):
                state = _("motore non incluso in questa versione")
            elif bundled:
                state = _("incluso")
            elif path:
                state = _("installata")
            else:
                state = _("da scaricare")
            cells.append(state)
            for c, t in enumerate(cells):
                self.table.setItem(r, c, QTableWidgetItem(t))
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(4, 0, 4, 0)
            if path and not bundled:
                btn = QPushButton(_("Rimuovi"))
                btn.clicked.connect(lambda _c=False, k=code: self.remove(k))
            else:
                btn = QPushButton(_("Scarica"))
                btn.setEnabled(not path and engine_ok(code) and self.busy is None)
                btn.clicked.connect(lambda _c=False, k=code: self.download(k))
            h.addWidget(btn)
            self.table.setCellWidget(r, 4, w)

    def download(self, code):
        self.busy = code
        self.bar.setVisible(True)
        self.bar.setRange(0, 0)
        self.msg.setText(_("Scarico {name}…").format(name=L.LANGS[code]["name"]))
        self.msg.setStyleSheet("color:#999;")
        self.refresh()

        def run():
            try:
                path = L.download(code, lambda a, b: self.relay.progress.emit(a // 1024, b // 1024))
                self.relay.done.emit(code, path, "")
            except Exception as e:  # noqa: BLE001
                self.relay.done.emit(code, "", str(e))
        threading.Thread(target=run, daemon=True).start()

    def _progress(self, done, total):
        if total:
            self.bar.setRange(0, total)
            self.bar.setValue(min(done, total))

    def _done(self, code, path, err):
        self.busy = None
        self.bar.setVisible(False)
        name = L.LANGS[code]["name"]
        if err:
            self.msg.setText(_("Download di {name} non riuscito: {err}").format(name=name, err=err))
            self.msg.setStyleSheet("color:#e66;")
        else:
            self.msg.setText(_("{name} pronta.").format(name=name))
            self.msg.setStyleSheet("color:#6c6;")
        self.refresh()

    def remove(self, code):
        L.remove(code)
        self.msg.setText(_("{name} rimossa.").format(name=L.LANGS[code]["name"]))
        self.refresh()

    def reject(self):
        if self.busy is not None:
            self.msg.setText(_("Attendi la fine del download."))
            return
        super().reject()
