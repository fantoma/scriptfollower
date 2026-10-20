"""Finestra Impostazioni: ingresso audio con gate e meter, i due profili OSC, il riconoscimento vocale."""
import copy
import threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                               QProgressBar, QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from .audio_input import audio_settings, list_input_devices, list_output_devices, parse_channels
from .osc import N_TARGETS, clean_targets, plugin_order, plugins
from .recognizer import MODEL_NAME, MODEL_SIZE_MB, download_model, is_model, list_models, model_dirs
from .system import data_dir_label
from .ui_meter import GateMeter

REC_DEFAULTS = {"model": "", "grammar": True, "ahead": 260, "min_score": 5.0, "anchor": 35}


PANEL_DEFAULTS = {"unit": "sentences", "count": 2}
PANEL_UNITS = [("sentences", "frasi"), ("lines", "righe"), ("words", "parole")]
PANEL_MAX = {"sentences": 10, "lines": 20, "words": 200}


def panel_settings(d):
    s = dict(PANEL_DEFAULTS)
    s.update({k: v for k, v in (d or {}).items() if k in PANEL_DEFAULTS})
    if s["unit"] not in PANEL_MAX:
        s["unit"] = "sentences"
    try:
        s["count"] = max(1, min(PANEL_MAX[s["unit"]], int(s["count"])))
    except (TypeError, ValueError):
        s["count"] = 2
    return s


def rec_settings(d):
    s = dict(REC_DEFAULTS)
    s.update({k: v for k, v in (d or {}).items() if k in REC_DEFAULTS})
    s["grammar"] = s["grammar"] not in (False, "false", "0", 0)
    s["ahead"] = max(60, min(2000, int(s["ahead"])))
    s["min_score"] = max(2.0, min(20.0, float(s["min_score"])))
    s["anchor"] = max(10, min(70, int(s["anchor"])))
    return s


class _Relay(QObject):
    progress = Signal(int, int)
    done = Signal(str, str)


class SettingsDialog(QDialog):
    """Le impostazioni audio valgono subito (si vede il meter e si sente il gate); «Annulla» le rimette."""

    def __init__(self, mw, page=0):
        super().__init__(mw)
        self.mw = mw
        self.setWindowTitle("Impostazioni")
        self.orig_audio = copy.deepcopy(mw.audio_cfg)
        self._loading = True
        v = QVBoxLayout(self)
        self.tabs = QTabWidget()
        v.addWidget(self.tabs, 1)
        self._build_audio()
        self._build_osc()
        self._build_rec()
        self._build_panel()
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.tabs.setCurrentIndex(page)
        self._loading = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(40)
        self.resize(740, 760)

    # ------------------------------------------------------------ audio
    def _build_audio(self):
        w = QWidget()
        v = QVBoxLayout(w)
        a = audio_settings(self.mw.audio_cfg)
        gi = QGroupBox("Ingresso di ascolto")
        fi = QFormLayout(gi)
        self.dev = QComboBox()
        self.dev.addItem("Predefinita di sistema", "")
        for d in list_input_devices():
            self.dev.addItem(f"{d['name']}  [{d['hostapi']}] — {d['max_ch']} canali", d["id"])
        i = self.dev.findData(a["device"])
        if i < 0:
            self.dev.addItem(f"{a['device'].split('|', 1)[-1]} (non collegata)", a["device"])
            i = self.dev.count() - 1
        self.dev.setCurrentIndex(i)
        self.dev.currentIndexChanged.connect(self._dev_changed)
        fi.addRow("Scheda audio", self.dev)
        self.ch = QSpinBox()
        self.ch.setRange(1, 64)
        self._fit_channels()
        self.ch.setValue(a["channel"])
        self.ch.valueChanged.connect(self._audio_changed)
        self.ch.setToolTip("Canale della scheda da ascoltare: meglio una mandata aux del mixer con le sole voci")
        fi.addRow("Canale di ascolto", self.ch)
        self.gain = QDoubleSpinBox()
        self.gain.setRange(-12, 30)
        self.gain.setDecimals(1)
        self.gain.setSuffix(" dB")
        self.gain.setValue(a["gain_db"])
        self.gain.valueChanged.connect(self._audio_changed)
        self.gain.setToolTip("Guadagno digitale prima del meter, del gate e del riconoscitore")
        fi.addRow("Guadagno", self.gain)
        rescan = QPushButton("Rileggi le schede collegate")
        rescan.clicked.connect(self._rescan)
        fi.addRow("", rescan)
        v.addWidget(gi)

        gg = QGroupBox("Gate")
        gv = QVBoxLayout(gg)
        self.gate_on = QCheckBox("Usa il gate: sotto la soglia al riconoscitore arriva silenzio")
        self.gate_on.setChecked(a["gate_on"])
        self.gate_on.toggled.connect(self._audio_changed)
        gv.addWidget(self.gate_on)
        self.meter = GateMeter(self.mw.audio)
        self.meter.set_threshold(a["threshold_db"])
        self.meter.gate_on = a["gate_on"]
        self.meter.thresholdChanged.connect(self._thr_from_meter)
        gv.addWidget(self.meter)
        hint = QLabel("Trascina il cursore bianco sul meter per la soglia. Barra superiore: livello "
                      "dell'ingresso (grigia quando il gate è chiuso). Barra blu: ciò che arriva al riconoscitore.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888; font-size:11px;")
        gv.addWidget(hint)
        fg = QFormLayout()
        self.thr = QDoubleSpinBox()
        self.thr.setRange(-70, 0)
        self.thr.setDecimals(1)
        self.thr.setSuffix(" dBFS")
        self.thr.setValue(a["threshold_db"])
        self.thr.valueChanged.connect(self._thr_from_box)
        fg.addRow("Soglia", self.thr)
        self.hold = QSpinBox()
        self.hold.setRange(0, 3000)
        self.hold.setSingleStep(50)
        self.hold.setSuffix(" ms")
        self.hold.setValue(a["hold_ms"])
        self.hold.setToolTip("Quanto resta aperto dopo che il segnale scende sotto la soglia "
                             "(non taglia le fine parola e le pause brevi)")
        self.hold.valueChanged.connect(self._audio_changed)
        fg.addRow("Hold", self.hold)
        gv.addLayout(fg)
        v.addWidget(gg)

        gp = QGroupBox("Preascolto (cuffia)")
        fp = QFormLayout(gp)
        self.mon_on = QCheckBox("Ascolta in cuffia il canale d'ingresso su una scheda dedicata")
        self.mon_on.setChecked(a["mon_on"])
        self.mon_on.toggled.connect(self._audio_changed)
        fp.addRow(self.mon_on)
        self.mon_dev = QComboBox()
        self._fill_mon_devices(a["mon_device"])
        self.mon_dev.currentIndexChanged.connect(self._mon_dev_changed)
        fp.addRow("Scheda in uscita", self.mon_dev)
        self.mon_ch = QComboBox()
        self._fill_mon_channels(a["mon_channels"])
        self.mon_ch.currentIndexChanged.connect(self._audio_changed)
        fp.addRow("Canali", self.mon_ch)
        self.mon_db = QDoubleSpinBox()
        self.mon_db.setRange(-60, 12)
        self.mon_db.setDecimals(1)
        self.mon_db.setSuffix(" dB")
        self.mon_db.setValue(a["mon_db"])
        self.mon_db.valueChanged.connect(self._audio_changed)
        fp.addRow("Volume", self.mon_db)
        self.mon_post = QCheckBox("Dopo il gate (si sente esattamente ciò che arriva al riconoscitore)")
        self.mon_post.setChecked(a["mon_post"])
        self.mon_post.toggled.connect(self._audio_changed)
        fp.addRow(self.mon_post)
        self.mon_err = QLabel()
        self.mon_err.setWordWrap(True)
        self.mon_err.setStyleSheet("color:#e0b040;")
        fp.addRow(self.mon_err)
        v.addWidget(gp)
        self.err = QLabel()
        self.err.setWordWrap(True)
        self.err.setStyleSheet("color:#e0b040;")
        v.addWidget(self.err)
        v.addStretch(1)
        from PySide6.QtWidgets import QFrame, QScrollArea
        sa = QScrollArea()                     # come in CueStage: la scheda scorre, i controlli non si schiacciano
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.NoFrame)
        sa.setWidget(w)
        self.tabs.addTab(sa, "Audio")

    def _fit_channels(self):
        dev_id = self.dev.currentData() or ""
        n = 64
        for d in list_input_devices():
            if d["id"] == dev_id:
                n = d["max_ch"]
        self.ch.setMaximum(max(1, n))

    def _rescan(self):
        try:
            from .audio_input import sd
            if sd is not None:
                sd._terminate()
                sd._initialize()
        except Exception:
            pass
        cur = self.dev.currentData()
        self._loading = True
        self.dev.clear()
        self.dev.addItem("Predefinita di sistema", "")
        for d in list_input_devices():
            self.dev.addItem(f"{d['name']}  [{d['hostapi']}] — {d['max_ch']} canali", d["id"])
        self.dev.setCurrentIndex(max(0, self.dev.findData(cur)))
        self._fill_mon_devices(self.mon_dev.currentData() or "")
        self._fill_mon_channels(self.mon_ch.currentData() or "1-2")
        self._loading = False
        self.mw.audio.close()
        self._audio_changed()

    def _fill_mon_devices(self, cur):
        self.mon_dev.blockSignals(True)
        self.mon_dev.clear()
        self.mon_dev.addItem("Predefinita di sistema", "")
        for d in list_output_devices():
            self.mon_dev.addItem(f"{d['name']}  [{d['hostapi']}] — {d['max_ch']} canali", d["id"])
        i = self.mon_dev.findData(cur)
        if i < 0:
            self.mon_dev.addItem(f"{cur.split('|', 1)[-1]} (non collegata)", cur)
            i = self.mon_dev.count() - 1
        self.mon_dev.setCurrentIndex(i)
        self.mon_dev.blockSignals(False)

    def _fill_mon_channels(self, cur):
        n = 2
        for d in list_output_devices():
            if d["id"] == (self.mon_dev.currentData() or ""):
                n = d["max_ch"]
        if not self.mon_dev.currentData():
            n = max(n, 2)
        self.mon_ch.blockSignals(True)
        self.mon_ch.clear()
        for c in range(1, n, 2):
            self.mon_ch.addItem(f"{c}-{c + 1} (stereo)", f"{c}-{c + 1}")
        for c in range(1, n + 1):
            self.mon_ch.addItem(f"{c} (mono)", str(c))
        cur = "-".join(str(c) for c in parse_channels(cur))
        if self.mon_ch.findData(cur) < 0:
            self.mon_ch.addItem(cur, cur)
        self.mon_ch.setCurrentIndex(max(0, self.mon_ch.findData(cur)))
        self.mon_ch.blockSignals(False)

    def _mon_dev_changed(self, *_a):
        self._fill_mon_channels(self.mon_ch.currentData() or "1-2")
        self._audio_changed()

    def audio_cfg(self):
        return {"device": self.dev.currentData() or "", "channel": self.ch.value(),
                "gate_on": self.gate_on.isChecked(), "threshold_db": self.thr.value(),
                "hold_ms": self.hold.value(), "gain_db": self.gain.value(),
                "mon_on": self.mon_on.isChecked(), "mon_device": self.mon_dev.currentData() or "",
                "mon_channels": self.mon_ch.currentData() or "1-2", "mon_db": self.mon_db.value(),
                "mon_post": self.mon_post.isChecked()}

    def _dev_changed(self, *_a):
        if self._loading:
            return
        self._fit_channels()
        self._audio_changed()

    def _thr_from_meter(self, db):
        self.thr.blockSignals(True)
        self.thr.setValue(db)
        self.thr.blockSignals(False)
        self._audio_changed()

    def _thr_from_box(self, db):
        self.meter.set_threshold(db)
        self._audio_changed()

    def _audio_changed(self, *_a):
        if self._loading:
            return
        self.meter.gate_on = self.gate_on.isChecked()
        self.mw.set_audio(self.audio_cfg())

    def _tick(self):
        e = self.mw.audio.error
        if self.err.text() != e:
            self.err.setText(e)
        me = self.mw.audio.monitor.error if self.mon_on.isChecked() else ""
        if self.mon_err.text() != me:
            self.mon_err.setText(me)
        self.meter.update()

    # ------------------------------------------------------------ OSC
    def _build_osc(self):
        w = QWidget()
        v = QVBoxLayout(w)
        intro = QLabel("Due profili di destinazione per i pulsanti. Il <b>plugin</b> dice che cosa c'è dall'altra "
                       "parte (CueStage, QLab, SCS, ChamSys…) e offre i suoi comandi già pronti nei pulsanti; "
                       "con «Generico» scrivi indirizzo e argomenti a mano. I profili sono salvati nel progetto.")
        intro.setWordWrap(True)
        v.addWidget(intro)
        self.t_widgets = []
        for i, t in enumerate(clean_targets(self.mw.project.targets)):
            g = QGroupBox(f"Profilo OSC {i + 1}")
            f = QFormLayout(g)
            name = QLineEdit(t["name"])
            f.addRow("Nome", name)
            plug = QComboBox()
            for pid in plugin_order():
                plug.addItem(plugins()[pid]["name"], pid)
            plug.setCurrentIndex(max(0, plug.findData(t["plugin"])))
            f.addRow("Plugin", plug)
            hr = QHBoxLayout()
            host = QLineEdit(t["host"])
            host.setPlaceholderText("indirizzo IP, es. 192.168.1.50 (127.0.0.1 = questo computer)")
            hr.addWidget(host, 1)
            port = QSpinBox()
            port.setRange(1, 65535)
            port.setValue(int(t["port"]))
            hr.addWidget(QLabel("Porta"))
            hr.addWidget(port)
            f.addRow("Indirizzo", hr)
            note = QPlainTextEdit(t["note"])
            note.setPlaceholderText("Appunto del profilo (es. «MagicQ in regia, playback 1 = scena»)")
            note.setFixedHeight(54)
            f.addRow("Appunto", note)
            pnotes = QLabel()
            pnotes.setWordWrap(True)
            pnotes.setStyleSheet("color:#999; font-size:11px;")
            f.addRow(pnotes)
            row = {"name": name, "plugin": plug, "host": host, "port": port, "note": note, "pnotes": pnotes,
                   "last_plugin": t["plugin"]}
            plug.currentIndexChanged.connect(lambda _x, r=row: self._plugin_changed(r))
            self._plugin_changed(row, first=True)
            self.t_widgets.append(row)
            v.addWidget(g)
        v.addStretch(1)
        self.tabs.addTab(w, "OSC")

    def _plugin_changed(self, row, first=False):
        pid = row["plugin"].currentData()
        p = plugins().get(pid) or {}
        row["pnotes"].setText(p.get("notes", ""))
        if not first:
            old = plugins().get(row["last_plugin"]) or {}
            if row["port"].value() == int(old.get("port") or 0):
                row["port"].setValue(int(p.get("port") or 8000))
            if row["name"].text().strip() in ("", old.get("name", "")):
                row["name"].setText(p.get("name", "").split(" (")[0])
        row["last_plugin"] = pid

    def targets(self):
        out = []
        for r in self.t_widgets[:N_TARGETS]:
            out.append({"name": r["name"].text().strip(), "host": r["host"].text().strip(), "port": r["port"].value(),
                        "note": r["note"].toPlainText().strip(), "plugin": r["plugin"].currentData()})
        return clean_targets(out)

    # ------------------------------------------------------------ riconoscimento
    def _build_rec(self):
        w = QWidget()
        v = QVBoxLayout(w)
        r = rec_settings(self.mw.rec_cfg)
        gm = QGroupBox("Modello vocale (Vosk, offline)")
        f = QFormLayout(gm)
        mr = QHBoxLayout()
        self.model = QComboBox()
        self.model.addItem("Automatico (il primo modello italiano trovato)", "")
        for name, path in list_models():
            self.model.addItem(f"{name}  —  {path}", path)
        if r["model"] and self.model.findData(r["model"]) < 0:
            self.model.addItem(r["model"], r["model"])
        self.model.setCurrentIndex(max(0, self.model.findData(r["model"])))
        mr.addWidget(self.model, 1)
        pick = QPushButton("Scegli cartella…")
        pick.clicked.connect(self._pick_model)
        mr.addWidget(pick)
        f.addRow("Modello", mr)
        dl = QHBoxLayout()
        self.dl_btn = QPushButton(f"Scarica il modello italiano ({MODEL_SIZE_MB} MB)")
        self.dl_btn.clicked.connect(self._download)
        dl.addWidget(self.dl_btn)
        self.dl_bar = QProgressBar()
        self.dl_bar.setVisible(False)
        dl.addWidget(self.dl_bar, 1)
        f.addRow("", dl)
        where = QLabel("Cartelle in cui si cercano i modelli (la prima che esiste vince):<br>"
                       + "<br>".join(f"· {d}" for d in model_dirs())
                       + f"<br>Il download va in {data_dir_label('models')}. Per una copia davvero portable "
                         "metti la cartella del modello in «models» accanto al programma.")
        where.setWordWrap(True)
        where.setStyleSheet("color:#888; font-size:11px;")
        where.setTextInteractionFlags(Qt.TextSelectableByMouse)
        f.addRow(where)
        self.dl_msg = QLabel()
        self.dl_msg.setWordWrap(True)
        f.addRow(self.dl_msg)
        v.addWidget(gm)
        ga = QGroupBox("Inseguimento del copione")
        fa = QFormLayout(ga)
        self.grammar = QCheckBox("Limita il vocabolario alle parole del copione (consigliato con i modelli «small»)")
        self.grammar.setChecked(r["grammar"])
        fa.addRow(self.grammar)
        self.ahead = QSpinBox()
        self.ahead.setRange(60, 2000)
        self.ahead.setSingleStep(20)
        self.ahead.setSuffix(" parole")
        self.ahead.setValue(r["ahead"])
        self.ahead.setToolTip("Quanto avanti si cerca normalmente. I salti più lunghi (tagli, scene saltate) si "
                              "riconoscono comunque in tutto il copione, ma servono più parole sicure.")
        fa.addRow("Finestra di ricerca in avanti", self.ahead)
        self.min_score = QDoubleSpinBox()
        self.min_score.setRange(2, 20)
        self.min_score.setDecimals(1)
        self.min_score.setValue(r["min_score"])
        self.min_score.setToolTip("Più alto = si sposta solo con più parole riconosciute di fila (più sicuro, "
                                  "ma più in ritardo). 5 ≈ tre parole giuste.")
        fa.addRow("Sicurezza richiesta", self.min_score)
        self.anchor = QSpinBox()
        self.anchor.setRange(10, 70)
        self.anchor.setSuffix(" %")
        self.anchor.setValue(r["anchor"])
        self.anchor.setToolTip("Altezza nella finestra a cui si tiene la riga corrente durante lo scorrimento")
        fa.addRow("Riga corrente a", self.anchor)
        v.addWidget(ga)
        v.addStretch(1)
        self.tabs.addTab(w, "Riconoscimento")
        self.relay = _Relay()
        self.relay.progress.connect(self._dl_progress)
        self.relay.done.connect(self._dl_done)

    def _pick_model(self):
        d = QFileDialog.getExistingDirectory(self, "Cartella del modello Vosk")
        if not d:
            return
        if not is_model(d):
            self.dl_msg.setText("Questa cartella non sembra un modello Vosk (manca «am» o «conf/model.conf»).")
            self.dl_msg.setStyleSheet("color:#e66;")
            return
        if self.model.findData(d) < 0:
            self.model.addItem(d, d)
        self.model.setCurrentIndex(self.model.findData(d))

    def _download(self):
        self.dl_btn.setEnabled(False)
        self.dl_bar.setVisible(True)
        self.dl_bar.setRange(0, 0)
        self.dl_msg.setText(f"Scarico {MODEL_NAME}…")
        self.dl_msg.setStyleSheet("color:#999;")

        def run():
            try:
                path = download_model(lambda a, b: self.relay.progress.emit(a, b))
                self.relay.done.emit(path, "")
            except Exception as e:  # noqa: BLE001
                self.relay.done.emit("", str(e))
        threading.Thread(target=run, daemon=True).start()

    def _dl_progress(self, done, total):
        if total:
            self.dl_bar.setRange(0, total)
            self.dl_bar.setValue(done)

    def _dl_done(self, path, err):
        self.dl_btn.setEnabled(True)
        self.dl_bar.setVisible(False)
        if err:
            self.dl_msg.setText(f"Download non riuscito: {err}. Puoi scaricarlo a mano da alphacephei.com/vosk/models "
                                "ed estrarlo nella cartella «models».")
            self.dl_msg.setStyleSheet("color:#e66;")
            return
        self.dl_msg.setText(f"Modello pronto: {path}")
        self.dl_msg.setStyleSheet("color:#6c6;")
        if self.model.findData(path) < 0:
            self.model.addItem(path, path)
        self.model.setCurrentIndex(self.model.findData(path))

    # ------------------------------------------------------------ riquadro «Prossimo evento»
    def _build_panel(self):
        w = QWidget()
        v = QVBoxLayout(w)
        ps = panel_settings(self.mw.panel_cfg)
        g = QGroupBox("Anteprima nel riquadro «Prossimo evento»")
        f = QFormLayout(g)
        row = QHBoxLayout()
        self.pv_count = QSpinBox()
        self.pv_unit = QComboBox()
        for k, t in PANEL_UNITS:
            self.pv_unit.addItem(t, k)
        self.pv_unit.setCurrentIndex(max(0, self.pv_unit.findData(ps["unit"])))
        self._pv_unit_changed()
        self.pv_count.setValue(ps["count"])
        self.pv_unit.currentIndexChanged.connect(self._pv_unit_changed)
        row.addWidget(QLabel("Mostra le ultime"))
        row.addWidget(self.pv_count)
        row.addWidget(self.pv_unit)
        row.addWidget(QLabel("prima del pulsante"))
        row.addStretch(1)
        f.addRow(row)
        hint = QLabel("<b>frasi</b>: le frasi dette, fino al punto del pulsante · <b>righe</b>: le righe del copione "
                      "così come sono impaginate, didascalie comprese · <b>parole</b>: solo le ultime parole dette "
                      "(il riquadro più compatto). Le parole già dette diventano gialle, quella del pulsante è verde.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888; font-size:11px;")
        f.addRow(hint)
        v.addWidget(g)
        v.addStretch(1)
        self.tabs.addTab(w, "Riquadro")

    def _pv_unit_changed(self, *_a):
        unit = self.pv_unit.currentData()
        self.pv_count.setRange(1, PANEL_MAX[unit])
        if not self._loading:
            self.pv_count.setValue({"sentences": 2, "lines": 4, "words": 25}[unit])

    def panel_cfg(self):
        return panel_settings({"unit": self.pv_unit.currentData(), "count": self.pv_count.value()})

    def rec_cfg(self):
        return rec_settings({"model": self.model.currentData() or "", "grammar": self.grammar.isChecked(),
                             "ahead": self.ahead.value(), "min_score": self.min_score.value(),
                             "anchor": self.anchor.value()})

    # ------------------------------------------------------------ chiusura
    def accept(self):
        self.timer.stop()
        self.mw.set_audio(self.audio_cfg())
        self.mw.apply_settings(self.targets(), self.rec_cfg(), self.panel_cfg())
        super().accept()

    def reject(self):
        self.timer.stop()
        self.mw.set_audio(self.orig_audio)
        super().reject()
