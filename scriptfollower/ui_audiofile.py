"""Finestra «Prova con un file audio»: la registrazione di una prova al posto della scheda in ingresso."""
import json
import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractSlider, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QMessageBox, QPushButton, QSlider, QVBoxLayout)

from .audio_file import AUDIO_FILTER, SPEEDS, FilePlayer, fmt_time
from .i18n import _

RECENT_KEY = "test_audio_files"              # QSettings: {pdf: file audio} per riproporre l'ultimo


class AudioFileDialog(QDialog):
    def __init__(self, mw):
        super().__init__(mw)
        self.mw = mw
        self.setModal(False)
        self.setWindowTitle(_("Prova con un file audio"))
        self.player = FilePlayer(mw.audio)
        self.player.position.connect(self._pos)
        self.player.state.connect(self._state)
        self.player.error.connect(self._error)
        v = QVBoxLayout(self)
        intro = QLabel(_("Fai ascoltare al programma la registrazione di una prova o di una replica, come se "
                         "arrivasse dalla scheda audio: passa per guadagno, gate e meter e il copione la segue. "
                         "Finché questa finestra è aperta la scheda in ingresso non viene ascoltata."))
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#aaa;")
        v.addWidget(intro)
        row = QHBoxLayout()
        self.open_btn = QPushButton(_("Apri file audio…"))
        self.open_btn.clicked.connect(self.choose)
        row.addWidget(self.open_btn)
        self.name = QLabel(_("nessun file"))
        self.name.setStyleSheet("font-weight:bold;")
        self.name.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addWidget(self.name, 1)
        v.addLayout(row)
        self.info = QLabel("")
        self.info.setStyleSheet("color:#888; font-size:11px;")
        v.addWidget(self.info)
        pos = QHBoxLayout()
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderReleased.connect(lambda: self.player.seek(self.slider.value() / 10.0))
        self.slider.sliderMoved.connect(lambda v: self._show_time(v / 10.0))
        self.slider.actionTriggered.connect(self._slider_action)
        pos.addWidget(self.slider, 1)
        self.time = QLabel("0:00 / 0:00")
        self.time.setMinimumWidth(130)
        self.time.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.time.setStyleSheet("font-family:monospace;")
        pos.addWidget(self.time)
        v.addLayout(pos)
        tr = QHBoxLayout()
        self.back = QPushButton("◀◀ 10 s")
        self.back.clicked.connect(lambda: self.skip(-10))
        self.play_btn = QPushButton(_("▶ Riproduci"))
        self.play_btn.setMinimumWidth(130)
        self.play_btn.clicked.connect(self.toggle)
        self.stop_btn = QPushButton(_("■ Ferma"))
        self.stop_btn.setToolTip(_("Ferma e torna all'inizio del file"))
        self.stop_btn.clicked.connect(self.stop)
        self.fwd = QPushButton("10 s ▶▶")
        self.fwd.clicked.connect(lambda: self.skip(10))
        for b in (self.back, self.play_btn, self.stop_btn, self.fwd):
            tr.addWidget(b)
        tr.addStretch(1)
        v.addLayout(tr)
        f = QFormLayout()
        self.chan = QComboBox()
        self.chan.currentIndexChanged.connect(lambda _i: self.player.set_channel(self.chan.currentData() or 0))
        self.chan.setToolTip(_("Registrazioni multitraccia: scegli il canale con le voci"))
        f.addRow(_("Canale del file"), self.chan)
        self.speed = QComboBox()
        for s in SPEEDS:
            self.speed.addItem(f"{s:g}×", s)
        self.speed.setToolTip(_("Più veloce per provare in fretta un copione lungo (consigliato con Vosk). "
                                "Accelerato il file non si sente; i tempi tra gli eventi restano quelli della "
                                "registrazione."))
        self.speed.currentIndexChanged.connect(self._speed)
        f.addRow(_("Velocità"), self.speed)
        self.listen = QCheckBox(_("Fai sentire il file"))
        self.listen.setToolTip(_("Sull'uscita del preascolto (Impostazioni › Audio), o su quella predefinita del "
                                 "sistema"))
        self.listen.toggled.connect(lambda on: self.mw.audio.set_file_listen(on))
        f.addRow("", self.listen)
        self.auto = QCheckBox(_("Avvia ASCOLTA quando parte la riproduzione"))
        self.auto.setChecked(True)
        f.addRow("", self.auto)
        v.addLayout(f)
        hint = QLabel(_("Spostandoti nel file il copione si riaggancia da solo dopo qualche parola; per partire "
                        "subito dal punto giusto fai doppio clic sulla parola."))
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888; font-size:11px;")
        v.addWidget(hint)
        self._enable(False)
        self.resize(560, 330)

    # ------------------------------------------------------------ file
    def _recent(self):
        try:
            return json.loads(self.mw.qs.value(RECENT_KEY, "{}") or "{}")
        except (TypeError, ValueError):
            return {}

    def suggested(self):
        """L'ultimo file provato con questo copione, se c'è ancora."""
        p = self._recent().get(self.mw.project.pdf or "", "")
        return p if p and os.path.isfile(p) else ""

    def choose(self):
        cur = self.player.file.path if self.player.file else (self.suggested() or self.mw.project.pdf or "")
        start = os.path.dirname(cur) if cur else os.path.expanduser("~")
        p, _f = QFileDialog.getOpenFileName(self, _("Apri file audio"), start, _(AUDIO_FILTER))
        if p:
            self.load(p)

    def load(self, path):
        was_playing = self.player.playing
        try:
            f = self.player.load(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, _("Prova con un file audio"), str(e))
            return False
        self.name.setText(f.name)
        self.name.setToolTip(path)
        dur = f.duration
        self.info.setText(_("{0} Hz · {1} canali · {2} · {3}").format(
            f.sr, f.channels, fmt_time(dur) if dur else "?", f.backend))
        self.slider.setRange(0, int(dur * 10))
        self.slider.setPageStep(max(10, int(dur)))           # un clic sulla barra = ~1/10 del file
        self.chan.blockSignals(True)
        self.chan.clear()
        self.chan.addItem(_("Mix di tutti i canali") if f.channels > 1 else _("Mono"), 0)
        if f.channels > 1:
            for c in range(1, f.channels + 1):
                self.chan.addItem(_("Canale {0}").format(c), c)
        self.chan.blockSignals(False)
        self.player.set_speed(self.speed.currentData() or 1.0)
        self.mw.audio.set_file_listen(self.listen.isChecked())
        self._enable(True)
        self._pos(0.0)
        rec = self._recent()
        if self.mw.project.pdf:
            rec[self.mw.project.pdf] = path
            self.mw.qs.setValue(RECENT_KEY, json.dumps(dict(list(rec.items())[-30:])))
        self.mw.status(_("Prova con il file audio «{0}»: la scheda in ingresso non viene ascoltata").format(f.name))
        if was_playing:
            self.play()
        return True

    # ------------------------------------------------------------ trasporto
    def toggle(self):
        if self.player.playing:
            self.pause()
        else:
            self.play()

    def play(self):
        if self.player.file is None:
            self.choose()
            if self.player.file is None:
                return
        if self.auto.isChecked() and not (self.mw.listening or self.mw.rec.running):
            self.mw.start_listen()
        self.mw.timing.jumped()
        self.player.play()

    def pause(self):
        self.player.pause()
        self.mw.timing.jumped()             # la pausa non entra nei tempi tra gli eventi

    def stop(self):
        self.pause()
        self.player.seek(0)

    def skip(self, sec):
        f = self.player.file
        if f is None:
            return
        self.seek(max(0.0, f.seconds + sec))

    def seek(self, sec):
        self.player.seek(sec)
        self.mw.rec.flush()
        self.mw.timing.jumped()

    def _slider_action(self, action):
        if action in (QAbstractSlider.SliderPageStepAdd, QAbstractSlider.SliderPageStepSub,
                      QAbstractSlider.SliderSingleStepAdd, QAbstractSlider.SliderSingleStepSub):
            QTimer.singleShot(0, lambda: self.seek(self.slider.value() / 10.0))

    def _speed(self, _i):
        s = self.speed.currentData() or 1.0
        self.player.set_speed(s)
        self.mw.timing.jumped()
        self.mw.set_test_speed(s)

    # ------------------------------------------------------------ aggiornamenti dal lettore
    def _show_time(self, sec):
        f = self.player.file
        total = f.duration if f else 0.0
        self.time.setText(f"{fmt_time(sec)} / {fmt_time(total) if total else '?'}")

    def _pos(self, sec):
        if not self.slider.isSliderDown():
            self.slider.blockSignals(True)
            self.slider.setValue(int(sec * 10))
            self.slider.blockSignals(False)
            self._show_time(sec)

    def _state(self, st):
        playing = st == "play"
        self.play_btn.setText(_("❚❚ Pausa") if playing else _("▶ Riproduci"))
        if st == "end":
            self.mw.status(_("Fine del file audio"))
            self.mw.timing.jumped()
        if not playing:
            a = self.mw.audio                  # il meter non resta fermo sull'ultimo livello
            a.meter = a.meter_post = 0.0
            a.gate = False

    def _error(self, msg):
        self.mw.status(msg)

    def _enable(self, on):
        for w in (self.slider, self.back, self.play_btn, self.stop_btn, self.fwd, self.chan):
            w.setEnabled(on)
        self.play_btn.setEnabled(True)

    # ------------------------------------------------------------ chiusura
    def done(self, r):
        if getattr(self, "_closed", False):
            return
        self._closed = True
        self.player.unload()
        self.mw.audio.leave_file()
        self.mw.set_test_speed(1.0)
        self.mw.status(_("Prova con il file audio terminata: si ascolta di nuovo la scheda in ingresso"))
        super().done(r)

    def closeEvent(self, e):
        self.done(0)
        e.accept()
