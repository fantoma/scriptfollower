"""Finestra principale di Script Follower (stessa veste grafica di CueStage)."""
import json
import os
import random
import time

from PySide6.QtCore import QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QFont, QFontDatabase, QKeySequence, QShortcut
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout,
                               QHBoxLayout, QLabel, QLayout, QLineEdit, QMainWindow, QMenu, QMessageBox, QPushButton,
                               QSpinBox, QSplitter, QTextBrowser, QVBoxLayout, QWidget)

from . import __version__
from .align import Aligner, normalize
from .audio_file import is_audio
from .audio_input import AudioInput, audio_settings
from .osc import clean_targets, describe, migrate_commands, send_commands
from .project import EXT, FILE_FILTER, Cueing, Project, new_point
from .languages import LANGS, ORDER, find_model, installed, model_lang
from .recognizer import Recognizer
from .script_doc import ScriptDoc
from .system import APP_NAME
from .timing import Timing
from .ui_document import DocView
from .ui_meter import GateMeter
from .ui_panel import NextPanel
from .ui_point import PointDialog
from .ui_settings import SettingsDialog, panel_settings, rec_settings
from .i18n import _
from .ui_util import ElidedLabel, clamp_to_screen, fit_to_screen

PDF_FILTER = "Copione PDF (*.pdf);;Tutti i file (*)"
CHECK_STYLE = ("QPushButton{padding:0 12px;font-weight:bold;font-size:13px;border-radius:6px;}"
               "QPushButton:checked{background:#3a4a6a;color:white;}")


class MainWindow(QMainWindow):
    def __init__(self, path=None):
        super().__init__()
        self.setAcceptDrops(True)
        self.qs = QSettings("ScriptFollower", "ScriptFollower")
        self.audio_cfg = audio_settings(self._qs_json("audio"))
        self.rec_cfg = rec_settings(self._qs_json("recognition"))
        self.panel_cfg = panel_settings(self._qs_json("panel"))
        self.project = Project()
        self.project.targets = clean_targets(self._qs_json("osc_targets") or None)
        self.doc = None
        self.aligner = None
        self.cueing = Cueing(self.project)
        self.timing = Timing(self.project)
        self.timing.recording = self.qs.value("record_timing", "false") in ("true", True)
        self.prep = True
        self.listening = False
        self.suspended = False
        self.audio = AudioInput()
        self.rec = Recognizer()
        self.rec.hypothesis.connect(self.on_hypothesis)
        self.rec.status.connect(self.status)
        self.rec.ready.connect(self._rec_ready)
        self.rec.unknown.connect(self._model_unknown)
        self.unknown_words = set()           # parole del copione che il modello non conosce
        self.sim = None
        self.audiotest = None                  # finestra «Prova con un file audio»
        self.test_speed = 1.0
        self._blink = True

        self._build_ui()
        self._build_menus()
        self._build_shortcuts()
        self.set_mode(True)
        self.set_timing_recording(self.timing.recording)

        self.audio.configure(self.audio_cfg)
        if self.audio.error:
            QTimer.singleShot(300, lambda: self.status(self.audio.error))
        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self._ui_tick)
        self.ui_timer.start(50)
        self.blink_timer = QTimer(self)
        self.blink_timer.timeout.connect(self._blink_tick)
        self.blink_timer.start(450)
        self._update_title()
        g = self.qs.value("geometry")
        if g is not None:
            self.restoreGeometry(g)
            clamp_to_screen(self)
        else:
            fit_to_screen(self, 1400, 900)
        QTimer.singleShot(0, lambda: self._startup_open(path))

    def _qs_json(self, key):
        try:
            v = self.qs.value(key)
            return json.loads(v) if v else None
        except (TypeError, ValueError):
            return None

    # =========================================================== finestra ridimensionabile
    def _relayout_bar(self):
        """Barra in alto: una riga se ci sta, altrimenti due; l'orologio sparisce per ultimo."""
        if not hasattr(self, "top_w"):
            return
        width = self.top_w.width()
        clock_w = self.clock.sizeHint().width() + self.bar_b.layout().spacing()
        b_min = self.bar_b.minimumSizeHint().width() - (clock_w if not self.clock.isHidden() else 0)
        a_min = self.bar_a.minimumSizeHint().width()
        a_min += 170                         # perché si legga la posizione (pagina e parole)
        gap = self.top.horizontalSpacing()
        if width >= a_min + b_min + clock_w + gap:
            wrap, clock = False, True
        elif width >= a_min + b_min + gap:
            wrap, clock = False, False       # prima si toglie l'orologio, poi si va su due righe
        else:
            wrap, clock = True, width >= b_min + clock_w
        if self.clock.isHidden() == clock:
            self.clock.setVisible(clock)
        if wrap != self.bar_wrapped:
            self.bar_wrapped = wrap
            self.top.removeWidget(self.bar_b)
            if wrap:
                self.top.addWidget(self.bar_b, 1, 0, Qt.AlignRight)
            else:
                self.top.addWidget(self.bar_b, 0, 1)
            self.top_w.updateGeometry()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._relayout_bar()
        QTimer.singleShot(0, self._update_nav)       # testi dei tasti comando alla nuova larghezza
        # finestra stretta: il riquadro di destra non si prende più del 45% (il copione resta leggibile)
        sw, pw = self.split.width(), self.panel.width()
        if sw > 0 and pw > max(self.panel.minimumWidth(), int(sw * 0.45)):
            keep = max(self.panel.minimumWidth(), int(sw * 0.45))
            self.split.setSizes([sw - keep - self.split.handleWidth(), keep])

    def top_min_width(self):
        """Larghezza minima della barra in alto: quella su due righe, senza orologio."""
        clock_w = self.clock.sizeHint().width() + self.bar_b.layout().spacing()
        b_min = self.bar_b.minimumSizeHint().width() - (clock_w if not self.clock.isHidden() else 0)
        return max(self.bar_a.minimumSizeHint().width(), b_min)

    def showEvent(self, e):
        super().showEvent(e)
        self._relayout_bar()

    def _qs_save(self, key, value):
        self.qs.setValue(key, json.dumps(value, ensure_ascii=False))

    # =========================================================== costruzione UI
    def _build_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)

        # barra in alto in due gruppi: affiancati se c'è spazio, uno sotto l'altro se la finestra è stretta
        self.bar_a, self.bar_b = QWidget(), QWidget()
        bar = QHBoxLayout(self.bar_a)
        bar.setContentsMargins(0, 0, 0, 0)
        self.listen_btn = QPushButton(_('ASCOLTA'))
        self.listen_btn.setFixedSize(150, 70)
        self.listen_btn.setFocusPolicy(Qt.NoFocus)
        self.listen_btn.setToolTip(_("Avvia / ferma l'ascolto del parlato (F5)"))
        self.listen_btn.clicked.connect(self.toggle_listen)
        bar.addWidget(self.listen_btn)
        # sospensione: il modello resta caricato, ma la posizione si muove solo a mano
        self.suspend_btn = QPushButton()
        self.suspend_btn.setFixedHeight(70)
        self.suspend_btn.setMinimumWidth(116)
        self.suspend_btn.setFocusPolicy(Qt.NoFocus)
        self.suspend_btn.setToolTip(_("Sospendi / riprendi l'ascolto (F8): da sospeso la posizione si sposta solo a mano (doppio clic su una parola, Ctrl+↑/↓) e i pulsanti si usano normalmente"))
        self.suspend_btn.clicked.connect(self.toggle_suspend)
        bar.addWidget(self.suspend_btn)
        mv = QVBoxLayout()
        mv.setSpacing(4)
        self.prep_btn = QPushButton(_('✎ Preparazione'))
        self.show_btn = QPushButton(_('▶ Spettacolo'))
        for b, on in ((self.prep_btn, True), (self.show_btn, False)):
            b.setCheckable(True)
            b.setFocusPolicy(Qt.NoFocus)
            b.setFixedHeight(32)
            b.setMinimumWidth(130)
            b.setStyleSheet(CHECK_STYLE)
            b.clicked.connect(lambda _c=False, on=on: self.set_mode(on))
            mv.addWidget(b)
        self.prep_btn.setToolTip(_('Preparazione: aggiungi pulsanti e annotazioni (Ctrl+E)'))
        self.show_btn.setToolTip(_('Spettacolo: i pulsanti inviano OSC, la vista segue il parlato (Ctrl+E)'))
        bar.addLayout(mv)
        bar.addSpacing(6)
        sb = QVBoxLayout()
        sb.addWidget(QLabel(_('Posizione:')))
        self.pos_label = ElidedLabel("—", min_width=90)
        f = QFont()
        f.setPointSize(16)
        f.setBold(True)
        self.pos_label.setFont(f)
        sb.addWidget(self.pos_label)
        self.pos_notes = ElidedLabel("", min_width=90)
        self.pos_notes.setStyleSheet("color:#aaa;")
        sb.addWidget(self.pos_notes)
        bar.addLayout(sb, 1)
        bar = QHBoxLayout(self.bar_b)
        bar.setContentsMargins(0, 0, 0, 0)
        nav = QVBoxLayout()
        nav.setSpacing(4)
        r1 = QHBoxLayout()
        for text, fn, tip in ((_("◀ Riga"), lambda: self.step_line(-1), _("Riga precedente (Ctrl+↑)")),
                              (_("Riga ▶"), lambda: self.step_line(1), _("Riga successiva (Ctrl+↓)"))):
            b = QPushButton(text)
            b.setFixedHeight(32)
            b.setFocusPolicy(Qt.NoFocus)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            r1.addWidget(b)
        nav.addLayout(r1)
        r2 = QHBoxLayout()
        b = QPushButton(_("⟲ Dall'inizio"))
        b.setFixedHeight(32)
        b.setFocusPolicy(Qt.NoFocus)
        b.setToolTip(_("Riporta la posizione all'inizio del copione e riattiva tutti i punti"))
        b.clicked.connect(self.restart)
        r2.addWidget(b)
        self.follow_btn = QPushButton(_('Segui'))
        self.follow_btn.setCheckable(True)
        self.follow_btn.setChecked(True)
        self.follow_btn.setFixedHeight(32)
        self.follow_btn.setFocusPolicy(Qt.NoFocus)
        self.follow_btn.setStyleSheet(CHECK_STYLE)
        self.follow_btn.setToolTip(_('La vista scorre seguendo il parlato (F6). Se scorri a mano, si ferma per 5 s.'))
        self.follow_btn.toggled.connect(self._follow_toggled)
        r2.addWidget(self.follow_btn)
        self.timing_btn = QPushButton(_('⏺ Tempi'))
        self.timing_btn.setCheckable(True)
        self.timing_btn.setFixedHeight(32)
        self.timing_btn.setFocusPolicy(Qt.NoFocus)
        self.timing_btn.setStyleSheet("QPushButton{padding:0 10px;font-weight:bold;font-size:13px;border-radius:6px;}"
                                      "QPushButton:checked{background:#b3261e;color:white;}")
        self.timing_btn.setToolTip(_("Registra i tempi tra un evento e l'altro durante la replica: la media delle repliche dà il «tempo stimato» del prossimo evento"))
        self.timing_btn.toggled.connect(self.set_timing_recording)
        r1.addWidget(self.timing_btn)
        nav.addLayout(r2)
        bar.addLayout(nav)
        # Comando precedente / successivo: si vedono solo se c'è un comando a cui andare
        cv = QVBoxLayout()
        cv.setSpacing(4)
        self.nav_prev_btn = QPushButton()
        self.nav_next_btn = QPushButton()
        for b, d, tip in ((self.nav_prev_btn, -1, _("Mostra il comando precedente nel copione (Alt+↑)")),
                          (self.nav_next_btn, 1, _("Mostra il comando successivo nel copione (Alt+↓)"))):
            b.setFixedHeight(32)
            b.setMinimumWidth(120)
            b.setMaximumWidth(190)
            b.setFocusPolicy(Qt.NoFocus)
            b.setProperty("tip", tip + ". " + _("Solo la vista si sposta: la posizione dello spettacolo non cambia."))
            b.setToolTip(b.property("tip"))
            b.setStyleSheet("QPushButton{text-align:left;padding:0 8px;font-size:12px;}")
            b.clicked.connect(lambda _c=False, d=d: self.nav_command(d))
            sp = b.sizePolicy()
            sp.setRetainSizeWhenHidden(True)        # la barra non cambia forma quando un tasto sparisce
            b.setSizePolicy(sp)
            cv.addWidget(b)
        bar.addLayout(cv)
        self.nav_word = None                         # comando mostrato con i tasti (None = posizione attuale)
        bar.addSpacing(6)
        self.meter = GateMeter(self.audio, compact=True)
        self.meter.set_threshold(self.audio_cfg["threshold_db"])
        self.meter.gate_on = self.audio_cfg["gate_on"]
        self.meter.thresholdChanged.connect(self._threshold_from_bar)
        self.meter.setContextMenuPolicy(Qt.CustomContextMenu)
        self.meter.customContextMenuRequested.connect(lambda *_: self.settings_dialog(0))
        mvb = QVBoxLayout()
        mvb.addStretch(1)
        mh = QHBoxLayout()
        ml = self.meter_label = QLabel(_('Ingresso · gate'))
        ml.setStyleSheet("font-weight:bold;")
        mh.addWidget(ml)
        mh.addStretch(1)
        self.mon_btn = QPushButton("🎧")
        self.mon_btn.setCheckable(True)
        self.mon_btn.setFixedSize(34, 20)
        self.mon_btn.setFocusPolicy(Qt.NoFocus)
        self.mon_btn.setStyleSheet("QPushButton{border-radius:4px;font-size:12px;padding:0;}"
                                   "QPushButton:checked{background:#2d5f8a;color:white;}")
        self.mon_btn.setToolTip(_("Preascolto in cuffia dell'ingresso (scheda e canali in Impostazioni › Audio)"))
        self.mon_btn.setChecked(self.audio_cfg["mon_on"])
        self.mon_btn.toggled.connect(self._mon_toggled)
        mh.addWidget(self.mon_btn)
        mvb.addLayout(mh)
        mvb.addWidget(self.meter)
        mvb.addStretch(1)
        bar.addLayout(mvb)
        self.clock = QLabel("00:00:00")
        cf = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        cf.setPointSize(18)
        self.clock.setFont(cf)
        bar.addWidget(self.clock)
        self.top_w = _TopBar(self)
        self.top = QGridLayout(self.top_w)
        self.top.setSizeConstraint(QLayout.SetNoConstraint)   # può stringersi: poi la barra va su due righe
        self.top.setContentsMargins(0, 0, 0, 0)
        self.top.setHorizontalSpacing(6)
        self.top.setVerticalSpacing(4)
        self.top.addWidget(self.bar_a, 0, 0)
        self.top.addWidget(self.bar_b, 0, 1)
        self.bar_wrapped = False
        root.addWidget(self.top_w)

        self.split = QSplitter(Qt.Horizontal)
        self.view = DocView()
        c = self.view.canvas
        c.wordMenu.connect(self._word_menu)
        c.wordDouble.connect(self.jump_to)
        c.pointPressed.connect(self.press_point)
        c.pointContext.connect(self._point_menu)
        c.zoneResized.connect(self._zone_resized)
        self.split.addWidget(self.view)
        self.panel = NextPanel()
        from .ui_images import abs_path
        self.panel.resolve = lambda rel: abs_path(self.project, rel)
        self.panel.pressed.connect(self.press_point)
        self.split.addWidget(self.panel)
        self.split.setStretchFactor(0, 1)
        self.split.setStretchFactor(1, 0)
        self.split.setSizes([1000, 360])
        root.addWidget(self.split, 1)
        self.setCentralWidget(central)
        self._listen_look()

    def _build_menus(self):
        mb = self.menuBar()

        def add(menu, text, fn, shortcut=None):
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.triggered.connect(fn)
            menu.addAction(a)
            return a

        m = mb.addMenu(_('&File'))
        add(m, _('Apri copione PDF…'), self.open_pdf_dialog, "Ctrl+O")
        add(m, _('Apri progetto…'), self.open_project_dialog, "Ctrl+Shift+O")
        add(m, _('Salva'), self.save, "Ctrl+S")
        add(m, _('Salva con nome…'), self.save_as, "Ctrl+Shift+S")
        m.addSeparator()
        add(m, _('Esci'), self.close, "Ctrl+Q")

        m = mb.addMenu(_('&Modifica'))
        add(m, _('Preparazione / Spettacolo'), lambda: self.set_mode(not self.prep), "Ctrl+E")
        m.addSeparator()
        add(m, _('Vocabolario del copione…'), self.vocab_dialog, "Ctrl+Shift+V")
        add(m, _('Ripristina le parole escluse automaticamente'), self.reset_overrides)
        add(m, _('Elimina tutti i punti…'), self.delete_all_points)

        m = mb.addMenu(_('&Spettacolo'))
        add(m, _('Ascolta / ferma'), self.toggle_listen, "F5")
        add(m, _("Sospendi / riprendi l'ascolto (modo manuale)"), self.toggle_suspend, "F8")
        add(m, _('Segui il parlato'), lambda: self.follow_btn.toggle(), "F6")
        self.timing_act = add(m, _('Registra i tempi degli eventi'), lambda on: self.set_timing_recording(on), "F7")
        self.timing_act.setCheckable(True)
        add(m, _('Cancella i tempi registrati…'), self.clear_timing)
        m.addSeparator()
        a = add(m, _('Invia il prossimo evento'), self.fire_next)
        a.setShortcut(QKeySequence(Qt.Key_Space))
        a.setShortcutContext(Qt.WindowShortcut)
        add(m, _('Mostra il comando precedente'), lambda: self.nav_command(-1), "Alt+Up")
        add(m, _('Mostra il comando successivo'), lambda: self.nav_command(1), "Alt+Down")
        add(m, _('Riga precedente'), lambda: self.step_line(-1), "Ctrl+Up")
        add(m, _('Riga successiva'), lambda: self.step_line(1), "Ctrl+Down")
        add(m, _("Torna all'inizio"), self.restart, "Ctrl+Home")
        add(m, _('Vai alla posizione corrente'), self.go_current, "Ctrl+L")
        m.addSeparator()
        add(m, _('Simula il parlato…'), self.simulate_dialog, "F4")
        add(m, _('Prova con un file audio…'), self.audio_test_dialog, "F9")
        m.addSeparator()
        add(m, _('Ingrandisci'), lambda: self.view.set_zoom(self.view.canvas.zoom * 1.1), "Ctrl++")
        add(m, _('Riduci'), lambda: self.view.set_zoom(self.view.canvas.zoom / 1.1), "Ctrl+-")
        add(m, _('Adatta alla larghezza'), lambda: self.view.set_zoom(1.0), "Ctrl+0")

        m = mb.addMenu(_('&Impostazioni'))
        add(m, _('Impostazioni…'), lambda: self.settings_dialog(0), "Ctrl+,")
        m.addSeparator()
        add(m, _('Audio e gate…'), lambda: self.settings_dialog(0))
        add(m, _('Profili OSC…'), lambda: self.settings_dialog(1))
        add(m, _('Riconoscimento vocale…'), lambda: self.settings_dialog(2))
        add(m, _('Riquadro «Prossimo evento»…'), lambda: self.settings_dialog(3))

        m = mb.addMenu(_("&Lingua"))
        self.lang_menu = m.addMenu(_("Lingua del copione (riconoscimento)"))
        self.lang_menu.aboutToShow.connect(self._fill_lang_menu)
        add(m, _("Lingue del riconoscimento…"), lambda: self.languages_dialog())
        m.addSeparator()
        ui = m.addMenu(_("Lingua dell'interfaccia"))
        from PySide6.QtGui import QActionGroup
        from .i18n import UI_LANGS, language
        grp = QActionGroup(self)
        for code, name in UI_LANGS:
            a = ui.addAction(name)
            a.setCheckable(True)
            a.setChecked(code == language())
            grp.addAction(a)
            a.triggered.connect(lambda _c=False, c=code: self.set_ui_language(c))

        m = mb.addMenu(_('&Aiuto'))
        add(m, _('Guida rapida'), self.help_dialog, "F1")
        add(m, _('Informazioni'), self.about)

    def _build_shortcuts(self):
        sc = QShortcut(QKeySequence(Qt.Key_Escape), self)
        sc.activated.connect(self.view.canvas.clear_selection)

    # =========================================================== stato e aspetto
    def status(self, text):
        self.statusBar().showMessage(text, 8000)

    def _update_title(self):
        name = os.path.basename(self.project.path) if self.project.path else (
            os.path.basename(self.project.pdf) if self.project.pdf else _("nessun copione"))
        self.setWindowTitle(f"{APP_NAME} — {name}{' *' if self.project.dirty else ''}")

    def _listen_look(self, loading=False):
        if loading:
            self.listen_btn.setText("…")
            self.listen_btn.setStyleSheet(
                "QPushButton{background:#8a6d1a;color:white;font-size:22px;font-weight:bold;border-radius:8px;}")
        elif self.listening and self.suspended:
            self.listen_btn.setText(_('MANUALE'))
            self.listen_btn.setStyleSheet(
                "QPushButton{background:#8a6d1a;color:white;font-size:22px;font-weight:bold;border-radius:8px;}"
                "QPushButton:pressed{background:#b08a20;}")
        elif self.listening:
            self.listen_btn.setText(_('● IN ASCOLTO'))
            self.listen_btn.setStyleSheet(
                "QPushButton{background:#b3261e;color:white;font-size:17px;font-weight:bold;border-radius:8px;}"
                "QPushButton:pressed{background:#e0453a;}")
        else:
            self.listen_btn.setText(_('ASCOLTA'))
            self.listen_btn.setStyleSheet(
                "QPushButton{background:#1f8f45;color:white;font-size:24px;font-weight:bold;border-radius:8px;}"
                "QPushButton:pressed{background:#2bc060;}")
        base = "QPushButton{{background:{};color:white;font-weight:bold;font-size:14px;border-radius:8px;padding:0 10px;}}" \
               "QPushButton:disabled{{background:#3a3c44;color:#777;}}"
        if self.suspended:
            self.suspend_btn.setText(_('▶ Riprendi\nascolto'))
            self.suspend_btn.setStyleSheet(base.format("#2d5f8a"))
        else:
            self.suspend_btn.setText(_('❚❚ Sospendi\nascolto'))
            self.suspend_btn.setStyleSheet(base.format("#8a6d1a"))
        self.suspend_btn.setEnabled(self.listening)

    def set_mode(self, prep):
        self.prep = bool(prep)
        self.prep_btn.setChecked(self.prep)
        self.show_btn.setChecked(not self.prep)
        self.view.canvas.prep = self.prep
        self.view.canvas.clear_selection()
        self.panel.hint.setText(_("Spazio = invia il prossimo evento") if not self.prep else
                                _("In preparazione i pulsanti si modificano con un clic; "
                                  "passa a «Spettacolo» per inviarli"))
        self.refresh_points()
        if not self.prep and self.cueing.pos >= 0:
            self.view.ensure_word(self.cueing.pos, force=True)

    def _follow_toggled(self, on):
        self.view.follow = on
        if on:
            self.view.manual_until = 0
            self.view.ensure_word(self.cueing.pos, force=True)

    def _ui_tick(self):
        self.meter.update()
        lab = _("File audio · gate") if self.audio.file_mode else _('Ingresso · gate')
        if self.meter_label.text() != lab:
            self.meter_label.setText(lab)
            self.meter_label.setStyleSheet("font-weight:bold;" + ("color:#e0b040;" if self.audio.file_mode else ""))
            self.meter_label.setToolTip(self.audio.name if self.audio.file_mode else "")
        self.clock.setText(time.strftime("%H:%M:%S"))

    def _blink_tick(self):
        self._blink = not self._blink
        self.view.canvas.set_blink(self._blink)
        self._update_panel()
        self._update_nav()

    # =========================================================== file
    def _startup_open(self, path):
        if path:
            self.open_any(path)
            return
        last = self.qs.value("last_project")
        if last and os.path.isfile(last):
            self.open_project(last)

    def open_any(self, path):
        if is_audio(path):
            self.audio_test_dialog(path)
        elif path.lower().endswith(EXT):
            self.open_project(path)
        else:
            self.open_pdf(path)

    def _confirm_discard(self):
        if not self.project.dirty:
            return True
        r = QMessageBox.question(self, APP_NAME, _('Il progetto ha modifiche non salvate. Salvarle?'),
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save()
        return r == QMessageBox.Discard

    def open_pdf_dialog(self):
        if not self._confirm_discard():
            return
        start = os.path.dirname(self.project.pdf) if self.project.pdf else os.path.expanduser("~")
        p, _f = QFileDialog.getOpenFileName(self, _('Apri copione PDF'), start, _(PDF_FILTER))
        if p:
            self.open_pdf(p)

    def open_project_dialog(self):
        if not self._confirm_discard():
            return
        start = os.path.dirname(self.project.path or self.project.pdf or os.path.expanduser("~"))
        p, _f = QFileDialog.getOpenFileName(self, _('Apri progetto'), start, _(FILE_FILTER))
        if p:
            self.open_project(p)

    def open_pdf(self, path):
        side = os.path.splitext(path)[0] + EXT
        if os.path.isfile(side):
            r = QMessageBox.question(self, APP_NAME, _("Accanto al PDF c'è il progetto «{0}» con i suoi pulsanti e note. Aprirlo?").format(os.path.basename(side)), QMessageBox.Yes | QMessageBox.No)
            if r == QMessageBox.Yes:
                self.open_project(side)
                return
        try:
            doc = ScriptDoc(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, APP_NAME, _('Impossibile aprire il PDF:\n{0}').format(e))
            return
        p = Project()
        p.pdf = doc.path
        p.pdf_sha1 = doc.sha1
        p.targets = clean_targets(self.project.targets)
        p.language = self.project.language           # un copione nuovo: la stessa lingua dell'ultimo
        p.dirty = True
        self._set_project(p, doc)
        if not doc.tokens:
            QMessageBox.warning(self, APP_NAME, _("In questo PDF non c'è testo selezionabile (forse è una scansione): serve un PDF esportato da Word / LibreOffice, oppure passato da un OCR."))

    def open_project(self, path):
        try:
            p = Project.load(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, APP_NAME, _('Impossibile aprire il progetto:\n{0}').format(e))
            return
        pdf = p.pdf
        if not pdf or not os.path.isfile(pdf):
            QMessageBox.information(self, APP_NAME, _("Il PDF del progetto non si trova:\n{0}\n\nIndica dov'è.").format(pdf))
            pdf, _f = QFileDialog.getOpenFileName(self, _("Dov'è il copione PDF?"), os.path.dirname(path), _(PDF_FILTER))
            if not pdf:
                return
            p.pdf = pdf
            p.dirty = True
        try:
            doc = ScriptDoc(pdf)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, APP_NAME, _('Impossibile aprire il PDF:\n{0}').format(e))
            return
        if p.pdf_sha1 and p.pdf_sha1 != doc.sha1:
            QMessageBox.warning(self, APP_NAME, _('Il PDF è cambiato da quando il progetto è stato salvato: pulsanti e note sono agganciati alle parole per posizione e potrebbero risultare spostati. Controllali in Preparazione.'))
            p.pdf_sha1 = doc.sha1
            p.dirty = True
        if any(pt["word"] >= len(doc.words) for pt in p.points):
            for pt in p.points:
                pt["word"] = min(pt["word"], len(doc.words) - 1)
        self._set_project(p, doc)
        self.qs.setValue("last_project", p.path)

    def _set_project(self, p, doc):
        if self.listening:
            self.stop_listen()
        if self.doc is not None:
            self.doc.close()
        self.project = p
        p.targets = clean_targets(p.targets)
        migrate_commands(p.points, p.targets)        # i vecchi progetti: comandi legati al profilo per id
        self.doc = doc
        doc.set_overrides(p.overrides)
        doc.set_improv(p.improv)
        doc.set_aliases(p.aliases)
        doc.set_language(p.language)
        self.unknown_words = set()
        self.cueing = Cueing(p)
        rec_on = self.timing.recording
        self.timing = Timing(p)
        self.timing.set_speed(self.test_speed)
        self.timing.recording = rec_on
        self._rebuild_aligner()
        self.view.canvas.set_doc(doc)
        self.view.relayout()
        self.view.verticalScrollBar().setValue(0)
        self.refresh_points()
        self._update_position()
        self._update_title()
        n_b = sum(1 for x in p.points if x["kind"] == "button")
        self.status(_('{0}: {1} pagine, {2} parole da seguire, {3} pulsanti, {4} note').format(os.path.basename(doc.path), doc.n_pages, len(doc.tokens), n_b, len(p.points) - n_b))

    def save(self):
        if not self.project.pdf:
            return False
        if not self.project.path:
            return self.save_as()
        try:
            self.project.overrides = dict(self.doc.overrides) if self.doc else {}
            self.project.save()
        except OSError as e:
            QMessageBox.warning(self, APP_NAME, _('Salvataggio non riuscito:\n{0}').format(e))
            return False
        self.qs.setValue("last_project", self.project.path)
        self._qs_save("osc_targets", self.project.targets)
        self._update_title()
        self.status(_('Salvato {0}').format(self.project.path))
        return True

    def save_as(self):
        if not self.project.pdf:
            return False
        start = self.project.path or os.path.splitext(self.project.pdf)[0] + EXT
        p, _f = QFileDialog.getSaveFileName(self, _('Salva progetto'), start, _(FILE_FILTER))
        if not p:
            return False
        self.project.path = p if p.endswith(EXT) else p + EXT
        return self.save()

    def closeEvent(self, e):
        if not self._confirm_discard():
            e.ignore()
            return
        self.qs.setValue("geometry", self.saveGeometry())
        self.stop_listen()
        if self.audiotest is not None:
            self.audiotest.close()
        self.audio.close()
        if self.sim is not None:
            self.sim.close()
        super().closeEvent(e)

    def _mark_dirty(self):
        self.project.dirty = True
        self._update_title()

    # =========================================================== riconoscimento
    def _rebuild_aligner(self):
        d = self.doc
        if d is None:
            self.aligner = None
            return
        floor = self.aligner.floor if self.aligner is not None else -1
        skip = [t in self.unknown_words for t in d.tokens] if self.unknown_words else None
        self.aligner = Aligner(d.tokens, skippable=skip, holds=d.hold_tokens())
        if self.cueing.pos >= 0:
            self.aligner.set_floor(min(floor, d.last_token_at_or_before(self.cueing.pos)))
        self.aligner.ahead = self.rec_cfg["ahead"]
        self.aligner.min_score = self.rec_cfg["min_score"]
        self.aligner.reset(d.last_token_at_or_before(self.cueing.pos) if self.cueing.pos >= 0 else -1)
        self.view.anchor = self.rec_cfg["anchor"] / 100.0

    def toggle_listen(self):
        if self.listening or self.rec.running:
            self.stop_listen()
        else:
            self.start_listen()

    def start_listen(self):
        if self.doc is None:
            self.status(_('Apri prima un copione'))
            return
        lang = self.project.language
        info = LANGS[lang]
        pref = self.rec_cfg["model"]
        if pref and model_lang(os.path.basename(pref.rstrip("/\\"))) not in (lang, None):
            pref = ""                            # il modello scelto a mano è di un'altra lingua
        model = find_model(lang, pref)
        if not model:
            r = QMessageBox.question(
                self, APP_NAME, _("Il modello vocale per «{0}» non c'è ancora. Scaricarlo adesso (circa {1} MB, una "
                                  "volta sola)?\n\nIn alternativa: Spettacolo › Simula il parlato, per provare senza "
                                  "microfono.").format(info["name"], info["mb"]), QMessageBox.Yes | QMessageBox.No)
            if r == QMessageBox.Yes:
                self.languages_dialog(lang)
            return
        if info["engine"] == "vosk" and not self.rec.available():
            QMessageBox.warning(self, APP_NAME, _('Il modulo di riconoscimento vocale (vosk) non è installato.'))
            return
        if info["engine"] == "whisper":
            from .whisper_engine import available
            if not available():
                QMessageBox.warning(self, APP_NAME, _("Il motore Whisper non è incluso in questa versione."))
                return
        if not self.audio.listening:
            self.audio.configure(self.audio_cfg)
            if not self.audio.listening:
                self.status(self.audio.error or _("Ingresso audio non disponibile"))
                return
        vocab = self.doc.vocabulary(self._vocab_from()) if self.rec_cfg["grammar"] and info["engine"] == "vosk" \
            else None
        self._listen_look(loading=True)
        self.rec.clear_history()
        self._update_prompt()
        self.rec.start(model, vocab, engine=info["engine"], language=lang)

    def _update_prompt(self):
        """Whisper: il testo detto fino alla posizione attuale (ultime ~40 parole) come contesto."""
        d = self.doc
        if d is None:
            return
        pos = self.cueing.pos
        words = [d.words[i].text for i in range(max(0, pos - 120), pos + 1) if d.words[i].spoken][-40:]
        self.rec.set_prompt(" ".join(words))

    def toggle_suspend(self):
        if not self.listening:
            self.status(_("L'ascolto non è attivo: premi ASCOLTA (F5)"))
            return
        self.set_suspended(not self.suspended)

    def set_suspended(self, on):
        """Modo manuale: il riconoscitore resta pronto ma la posizione non si muove da sola."""
        self.suspended = bool(on)
        self.rec.flush()
        if self.suspended:
            self.audio.consumer = None
            self.status(_('Ascolto sospeso: modo manuale (doppio clic su una parola o Ctrl+↑/↓ per spostarti)'))
        else:
            if self.aligner is not None and self.doc is not None:      # si riparte dalla posizione attuale
                pos = self.cueing.pos
                self.aligner.reset(self.doc.last_token_at_or_before(pos) if pos >= 0 else -1)
            self.timing.jumped()                 # il tempo passato in manuale non entra nelle medie
            self.audio.consumer = self.rec.feed if self.listening else None
            self.status(_('Ascolto ripreso dalla posizione attuale'))
        self._listen_look()
        self._update_position()

    # =========================================================== ricerca solo nella parte rimanente
    def _vocab_from(self):
        """Primo token del vocabolario: un po' prima del limite di ricerca (le frasi a cavallo)."""
        a = self.aligner
        return max(0, (a.floor if a is not None else 0) - 20)

    def _raise_floor(self, p):
        """Premuto il pulsante p: la parte di copione già fatta esce dalla ricerca (e dal vocabolario)."""
        a = self.aligner
        if a is None or self.doc is None:
            return
        tok = self.doc.last_token_at_or_before(p["word"])
        if a.pos >= 0:
            tok = min(tok, a.pos)                # premuto in anticipo: il limite è dove si è arrivati
        if tok > a.floor:
            a.set_floor(tok)
            self._update_vocab()

    def _lower_floor(self, tok):
        a = self.aligner
        if a is not None and tok < a.floor:
            a.set_floor(tok)
            self._update_vocab()

    def _update_vocab(self):
        if self.rec.running and self.rec_cfg["grammar"] and self.doc is not None:
            self.rec.set_vocabulary(self.doc.vocabulary(self._vocab_from()))

    def _rec_ready(self, ok):
        self.listening = bool(ok)
        self.suspended = False
        self.audio.consumer = self.rec.feed if ok else None
        self._listen_look()

    def stop_listen(self):
        self.suspended = False
        self.audio.consumer = None
        self.rec.stop()
        self.listening = False
        self._listen_look()

    def on_hypothesis(self, words, text):
        if self.aligner is None or self.suspended:
            return
        if text:
            self.panel.set_heard(text)
        t = self.aligner.update(words)
        if t is not None:
            self.set_position(self.doc.word_of_token(t))

    # =========================================================== posizione
    def set_position(self, word, scroll=True, force=False, manual=False):
        if self.doc is None:
            return
        self._timing_events(self.cueing.pos, word, manual)
        self.cueing.set_pos(word)
        if self.listening and LANGS[self.project.language]["engine"] == "whisper":
            self._update_prompt()
        self.view.canvas.cur_word = word
        self.view.canvas.update()
        if scroll and word >= 0:
            self.view.ensure_word(word, force=force)
        self.refresh_states()
        self._update_position()

    def jump_to(self, word):
        """Riposizionamento a mano: il riconoscimento riparte da qui."""
        if self.doc is None:
            return
        if self.aligner is not None:
            tok = self.doc.last_token_at_or_before(word)
            self._lower_floor(tok)
            self.aligner.reset(tok)
        self.rec.clear_history()
        self.set_position(word, force=True, manual=True)
        self.status(_('Riparto da «{0}» (pagina {1})').format(self.doc.words[word].text, self.doc.words[word].page + 1))

    def restart(self):
        if self.doc is None:
            return
        self.cueing.reset()
        self.timing.restart()
        if self.aligner is not None:
            self._lower_floor(-1)
            self.aligner.reset(-1)
        self.rec.clear_history()
        self.view.canvas.cur_word = -1
        self.view.verticalScrollBar().setValue(0)
        self.refresh_states()
        self._update_position()
        self.status(_("Posizione all'inizio del copione: tutti i punti sono di nuovo attivi"))

    def step_line(self, d):
        if self.doc is None:
            return
        cur = self.cueing.pos
        lines = self.doc.lines
        li = self.doc.words[cur].line if cur >= 0 else -1
        j = li + d
        while 0 <= j < len(lines):
            ws = [i for i in lines[j].words if self.doc.words[i].spoken]
            if ws:
                self.jump_to(ws[-1])
                return
            j += d
        if d < 0:
            self.restart()

    def _update_position(self):
        d = self.doc
        pos = self.cueing.pos
        if d is None or pos < 0:
            self.pos_label.setText("—" if d is None else _("Inizio del copione"))
            self.pos_notes.setText("" if d is None else _("{0} pagine · {1} parole da seguire").format(
                d.n_pages, len(d.tokens)))
            return
        w = d.words[pos]
        ln = d.lines[w.line]
        tail = [d.words[i].text for i in ln.words if i <= pos][-7:]
        self.pos_label.setText(_('Pag. {0} · «… {1}»').format(w.page + 1, ' '.join(tail)))
        pct = 100.0 * (max(0, w.t0) + 1) / max(1, len(d.tokens))
        a = self.aligner
        fl = ""
        if a is not None and a.floor > 0:
            fw = d.words[d.word_of_token(a.floor)]
            fl = " · " + _("ricerca da pag. {0}").format(fw.page + 1)
        self.pos_notes.setText(_("{0:.0f}% del copione").format(pct) + fl
                               + (" · " + _("ascolto sospeso: MODO MANUALE") if self.suspended else "")
                                + (" · 🎭 " + _("parte improvvisata: si riprende al testo che segue")
                                   if self.aligner is not None and self.aligner.pos in self.aligner.holds
                                   and self.aligner.pos >= 0 else ""))

    # =========================================================== punti
    def refresh_points(self):
        pts = self.project.sorted_points()
        self.view.canvas.set_points(pts)
        self.refresh_states()

    def point_state(self, p):
        cu = self.cueing
        if p["kind"] == "note":
            if cu.passed(p):
                return "passed", False
            nn = cu.next_note()
            return ("next" if nn and nn["id"] == p["id"] else "future"), True
        if p["id"] in cu.forced:
            return "armed", True                 # riattivato a mano
        if p["id"] in cu.closed:
            return "closed", False
        nb = cu.next_button()
        if nb and nb["id"] == p["id"]:
            return "next", True
        sent = p["id"] in cu.sent
        if not cu.passed(p):
            return ("sent" if sent else "future"), True
        if cu.enabled(p):
            return ("sent" if sent else "armed"), True
        return "passed", False

    def refresh_states(self):
        self.view.canvas.set_states({p["id"]: self.point_state(p) for p in self.project.points})
        self._update_panel()
        self._update_nav()

    # =========================================================== Comando precedente / successivo
    def go_current(self):
        self.nav_word = None
        self.view.manual_until = 0.0
        self.view.ensure_word(self.cueing.pos, force=True)
        self._update_nav()

    def _nav_ref(self):
        """Da dove si cerca: il comando mostrato con i tasti, finché la vista non torna a seguire il parlato."""
        if self.nav_word is not None and time.monotonic() < self.view.manual_until:
            return self.nav_word, True
        self.nav_word = None
        return self.cueing.pos, False

    def _nav_target(self, d):
        ref, from_nav = self._nav_ref()
        btns = [p for p in self.project.sorted_points() if p["kind"] == "button"]
        if d > 0:
            return next((b for b in btns if b["word"] > ref), None)
        prev = [b for b in btns if b["word"] < ref or (not from_nav and b["word"] == ref)]
        return prev[-1] if prev else None

    def _update_nav(self):
        if not hasattr(self, "nav_prev_btn"):
            return
        for b, d, arrow in ((self.nav_prev_btn, -1, "▲ " + _("Prec.:")), (self.nav_next_btn, 1, "▼ " + _("Succ.:"))):
            t = self._nav_target(d) if self.doc is not None else None
            if t is not None:
                full = f"{arrow} {t.get('label') or _('Pulsante')}"
                # larghezza vera solo se il tasto è già disposto; appena mostrato vale ancora quella di prima
                laid = b.isVisible() and b.minimumWidth() <= b.width() <= b.maximumWidth()
                room = (b.width() if laid else b.maximumWidth()) - 18
                b.setText(b.fontMetrics().elidedText(full, Qt.ElideRight, room))
                b.setToolTip(full if b.text() != full else b.property("tip") or "")
            b.setVisible(t is not None)

    def nav_command(self, d):
        t = self._nav_target(d)
        if t is None:
            return
        self.nav_word = t["word"]
        self.view.ensure_word(t["word"], force=True)
        self.view.manual_until = time.monotonic() + 8.0      # la vista resta lì per qualche secondo
        c = self.view.canvas
        c.flash_pid, c.flash_until = t["id"], time.monotonic() + 2.5
        c.update()
        QTimer.singleShot(2600, c.update)
        self._update_nav()
        w = self.doc.words[t["word"]]
        self.status(_('Comando «{0}» · pagina {1} (la vista torna a seguire il parlato dopo qualche secondo; Ctrl+L per tornare subito)').format(t.get('label'), w.page + 1))

    # =========================================================== zone trascinate in preparazione
    def _zone_resized(self, kind, a, b, na, nb):
        if self.doc is None:
            return
        old, new = set(range(a, b + 1)), set(range(na, nb + 1))
        if kind == "improv":
            self.doc.set_improv([z for z in self.doc.improv if z != (a, b)] + [(na, nb)])
            self._improv_changed()
        else:
            if old - new:
                self.doc.toggle_spoken(sorted(old - new), True)
            if new - old:
                self.doc.toggle_spoken(sorted(new - old), False)
            self.project.overrides = dict(self.doc.overrides)
            self._mark_dirty()
            self._rebuild_aligner()
            self.view.canvas.update()
            if self.listening:
                self.stop_listen()
                self.start_listen()
        self.status(_('Zona {0}: «{1}» … «{2}»').format('improvvisata' if kind == 'improv' else 'esclusa', self.doc.words[na].text, self.doc.words[nb].text))

    # =========================================================== tempi (euristica)
    def _timing_events(self, old, new, manual):
        """Eventi dei punti raggiunti passando da old a new."""
        t = self.timing
        if manual:
            t.jumped(back_to_word=new if new < old else None)
            t.reached.update(p["id"] for p in self.project.points if p["word"] <= new)
            if new >= 0:
                t.run_started = True
            return
        if new < old:                            # piccolo passo indietro del riconoscimento
            for p in self.project.points:
                if p["word"] > new:
                    t.reached.discard(p["id"])
            if t.chain is not None and self.project.point(t.chain[0]) and \
                    self.project.point(t.chain[0])["word"] > new:
                t.chain = None
            return
        if old < 0 <= new:
            t.begin()
        d = self.doc
        if d is not None and old >= 0:
            jump = abs(max(0, d.words[new].t0) - max(0, d.words[old].t0))
            if jump > 150:                       # salto lungo del riconoscimento: niente misure
                t.jumped()
        for p in self.project.sorted_points():
            if old < p["word"] <= new:
                self._timing_event(p["id"])

    def _timing_event(self, pid):
        if self.timing.event(pid) is not None:
            if self.project.path and not self.project.dirty:
                try:                             # i tempi si salvano da soli se non ci sono altre modifiche
                    self.project.save()
                except OSError:
                    self._mark_dirty()
            else:
                self._mark_dirty()

    def set_timing_recording(self, on):
        on = bool(on)
        self.timing.recording = on
        self.qs.setValue("record_timing", "true" if on else "false")
        for w in (self.timing_btn, self.timing_act):
            if w.isChecked() != on:
                w.blockSignals(True)
                w.setChecked(on)
                w.blockSignals(False)
        runs = int(self.project.timing.get("runs") or 0)
        self.status((_("Registrazione dei tempi ATTIVA") if on else _("Registrazione dei tempi spenta"))
                    + " · " + _("repliche registrate: {0}").format(runs))

    def clear_timing(self):
        if QMessageBox.question(self, APP_NAME, _('Cancellare tutti i tempi registrati per questo copione?')) \
                == QMessageBox.Yes:
            self.timing.clear()
            self._mark_dirty()
            self._update_panel()

    def _update_panel(self):
        cu = self.cueing
        nb = cu.next_button() if self.doc else None
        if nb is not None:
            pc = self.panel_cfg
            words = self.doc.context_before(nb["word"], pc["unit"], pc["count"])
            cmds = [describe(c, self.project.targets) for c in nb.get("commands") or []]
            self.panel.set_next(nb, words, nb["word"], cu.pos, nb.get("label") or "Pulsante", cmds,
                                self._blink, self.doc, by_line=pc["unit"] == "lines")
            est, left = self.timing.eta(nb["id"])
            self.panel.set_eta(est, left, self.timing.samples(nb["id"]))
        else:
            self.panel.set_next(None, [], -1, cu.pos, "", [], self._blink)
            self.panel.set_eta(None, None, 0)
        lp = cu.last_passed_button() if self.doc else None
        self.panel.set_prev(lp, bool(lp and lp["id"] in cu.sent))
        self.panel.set_note(cu.next_note() if self.doc else None)

    def fire_next(self):
        if self.prep or self.doc is None:
            return
        nb = self.cueing.next_button()
        if nb is None:
            self.status(_('Nessun altro pulsante'))
            return
        self.press_point(nb["id"])

    def press_point(self, pid):
        p = self.project.point(pid)
        if p is None:
            return
        if self.prep:
            self.edit_point(pid)
            return
        if p["kind"] != "button":
            return
        st, en = self.point_state(p)
        if not en:
            self.status(_('«{0}» è già passato: disattivato (tasto destro › Riattiva)').format(p.get('label')))
            return
        sent, errors = send_commands(p.get("commands"), self.project.targets)
        if not self.cueing.passed(p):            # premuto prima che il parlato ci arrivi: l'evento è adesso
            self._timing_event(pid)
        self.cueing.press(p)
        self._raise_floor(p)
        self.refresh_states()
        if errors:
            self.status("⚠ " + " · ".join(errors))
        else:
            self.status(f"▶ {p.get('label')}: " + (" · ".join(sent) if sent else _("nessun comando")))

    def add_point(self, word, kind):
        p = new_point(word, kind)
        if kind == "button":
            p["label"] = _("Pulsante {0}").format(sum(1 for x in self.project.points if x['kind'] == 'button') + 1)
        ctx = " ".join(self.doc.words[i].text for i in self.doc.sentences_before(word, n=1, max_words=12))
        dlg = PointDialog(p, self.project.targets, ctx, self, project=self.project)
        if dlg.exec() == QDialog.Accepted:
            self.project.add_point(dlg.result_point())
            self._mark_dirty()
            self.refresh_points()

    def edit_point(self, pid):
        p = self.project.point(pid)
        if p is None:
            return
        ctx = " ".join(self.doc.words[i].text for i in self.doc.sentences_before(p["word"], n=1, max_words=12))
        dlg = PointDialog(p, self.project.targets, ctx, self, project=self.project)
        if dlg.exec() == QDialog.Accepted:
            p.clear()
            p.update(dlg.result_point())
            self._mark_dirty()
            self.refresh_points()

    def delete_point(self, pid):
        self.project.remove_point(pid)
        self._mark_dirty()
        self.refresh_points()

    def delete_all_points(self):
        if not self.project.points:
            return
        if QMessageBox.question(self, APP_NAME, _('Eliminare tutti i {0} punti?').format(len(self.project.points))) \
                == QMessageBox.Yes:
            self.project.points = []
            self._mark_dirty()
            self.refresh_points()

    def reactivate(self, pid):
        p = self.project.point(pid)
        if p is None or p["kind"] != "button":
            return
        self.cueing.reactivate(p)
        self.refresh_states()
        self.status(_('«{0}» riattivato: si può premere di nuovo').format(p.get('label')))

    def _point_menu(self, pid, gpos):
        p = self.project.point(pid)
        if p is None:
            return
        m = QMenu(self)
        m.addAction(_('Modifica…'), lambda: self.edit_point(pid))
        if p["kind"] == "button":
            def test():
                sent, errors = send_commands(p.get("commands"), self.project.targets)
                self.status(("⚠ " + " · ".join(errors)) if errors else _("Prova inviata: ") + " · ".join(sent))
            m.addAction(_('Prova invio (senza cambiare lo stato)'), test)
            st, en = self.point_state(p)
            if not en or st in ("sent", "passed", "closed"):
                m.addAction(_('↺ Riattiva questo pulsante'), lambda: self.reactivate(pid))
        m.addAction(_('Vai qui'), lambda: self.jump_to(p["word"]))
        m.addSeparator()
        m.addAction(_('Elimina'), lambda: self.delete_point(pid))
        m.exec(gpos)

    def _word_menu(self, a, b, gpos):
        if self.doc is None:
            return
        words = list(range(a, b + 1))
        m = QMenu(self)
        last = self.doc.words[b].text
        m.addAction(_('＋ Pulsante OSC su «{0}»').format(last), lambda: self.add_point(b, "button"))
        m.addAction(_('＋ Annotazione su «{0}»').format(last), lambda: self.add_point(b, "note"))
        m.addSeparator()
        spoken = self.doc.words[a].spoken
        what = _("la selezione") if a != b else f"«{self.doc.words[a].text}»"
        m.addAction((_("Escludi {0} dal riconoscimento") if spoken else _("Includi {0} nel riconoscimento")).format(what),
                    lambda: self._toggle_spoken(words, not spoken))
        if a == b:
            ln = self.doc.lines[self.doc.words[a].line].words
            m.addAction(_("Escludi tutta la riga") if spoken else _("Includi tutta la riga"),
                        lambda: self._toggle_spoken(ln, not spoken))
        m.addSeparator()
        zone = self.doc.improv_at(a) or self.doc.improv_at(b)
        if zone:
            m.addAction(_('🎭 Togli la parte improvvisata'), lambda: self._remove_improv(zone))
        if a != b or not zone:
            m.addAction(_("🎭 Segna come parte improvvisata (non seguire l'ascolto)"), lambda: self._add_improv(a, b))
        m.addSeparator()
        m.addAction(_('Riparti da qui'), lambda: self.jump_to(b))
        m.exec(gpos)
        self.view.canvas.clear_selection()

    def _toggle_spoken(self, words, value):
        self.doc.toggle_spoken(words, value)
        self.project.overrides = dict(self.doc.overrides)
        self._mark_dirty()
        self._rebuild_aligner()
        self.view.canvas.update()
        if self.listening:
            self.stop_listen()
            self.start_listen()

    def _add_improv(self, a, b):
        self.doc.set_improv(self.doc.improv + [(a, b)])
        self._improv_changed()
        self.status(_("Parte improvvisata segnata: lì l'ascolto non segue il parlato e riprende al testo che segue"))

    def _remove_improv(self, zone):
        self.doc.set_improv([z for z in self.doc.improv if z != zone])
        self._improv_changed()

    def _improv_changed(self):
        self.project.improv = [list(z) for z in self.doc.improv]
        self._mark_dirty()
        self._rebuild_aligner()
        self.view.canvas.update()
        if self.listening:                       # il vocabolario cambia
            self.stop_listen()
            self.start_listen()

    def _model_unknown(self, words):
        """Il modello non conosce queste parole: l'inseguimento le salta senza penalità."""
        words = set(words)
        if words == self.unknown_words:
            return
        self.unknown_words = words
        pos = self.aligner.pos if self.aligner is not None else -1
        floor = self.aligner.floor if self.aligner is not None else -1
        self._rebuild_aligner()
        if self.aligner is not None:
            self.aligner.set_floor(floor)
            self.aligner.reset(pos)
        if words and self.doc is not None:
            counts = self.doc.word_counts()
            occ = sum(c for w, c in counts.items() if w in words)
            self.status(_('{0} parole del copione ({1} occorrenze) non sono nel vocabolario del modello: vengono saltate. Modifica › Vocabolario del copione… per dar loro una pronuncia.').format(len(words), occ))

    # =========================================================== lingue
    def _fill_lang_menu(self):
        m = self.lang_menu
        m.clear()
        for code in ORDER:
            info = LANGS[code]
            a = m.addAction(info["name"] + ("" if installed(code) else "  " + _("(da scaricare)")))
            a.setCheckable(True)
            a.setChecked(code == self.project.language)
            a.triggered.connect(lambda _c=False, c=code: self.set_script_language(c))

    def set_script_language(self, code):
        """Lingua del copione (salvata nel progetto): sceglie il modello e il motore di riconoscimento."""
        if code not in LANGS:
            return
        if not installed(code):
            r = QMessageBox.question(self, APP_NAME, _("Il modello vocale per «{0}» non è ancora installato "
                                                       "(circa {1} MB). Scaricarlo adesso?").format(
                LANGS[code]["name"], LANGS[code]["mb"]), QMessageBox.Yes | QMessageBox.No)
            if r == QMessageBox.Yes:
                self.languages_dialog(code)
            if not installed(code):
                return
        if code == self.project.language:
            return
        was = self.listening
        if was:
            self.stop_listen()
        self.project.language = code
        if self.doc is not None:
            self.doc.set_language(code)
        self.unknown_words = set()
        self._mark_dirty()
        self._rebuild_aligner()
        self.status(_("Lingua del copione: {0}").format(LANGS[code]["name"]))
        if was:
            self.start_listen()

    def languages_dialog(self, highlight=None):
        from .ui_languages import LanguagesDialog
        LanguagesDialog(self, highlight).exec()

    def set_ui_language(self, code):
        from .i18n import language
        self.qs.setValue("ui_lang", code)
        if code == language():
            return
        r = QMessageBox.question(self, APP_NAME, _("La nuova lingua dell'interfaccia vale dal prossimo avvio. "
                                                   "Riavviare adesso?"), QMessageBox.Yes | QMessageBox.No)
        if r == QMessageBox.Yes and self._confirm_discard():
            import subprocess
            import sys
            self.project.dirty = False
            path = self.project.path
            args = [sys.executable] + ([] if getattr(sys, "frozen", False) else ["-m", "scriptfollower"])
            if os.environ.get("APPIMAGE"):
                args = [os.environ["APPIMAGE"]]
            subprocess.Popen(args + ([path] if path else []))
            self.close()

    def vocab_dialog(self):
        if self.doc is None:
            self.status(_('Apri prima un copione'))
            return
        from .ui_vocab import VocabDialog
        dlg = VocabDialog(self)
        if dlg.exec() == QDialog.Accepted:
            al = dlg.aliases()
            if al != self.project.aliases:
                self.project.aliases = al
                self.doc.set_aliases(al)
                self._mark_dirty()
                self._rebuild_aligner()
                if self.listening:               # il vocabolario cambia
                    self.stop_listen()
                    self.start_listen()
                self.status(_('Pronunce aggiornate: {0} parole').format(len(al)))

    def reset_overrides(self):
        if self.doc is None:
            return
        self.doc.set_overrides({})
        self.project.overrides = {}
        self._mark_dirty()
        self._rebuild_aligner()
        self.view.canvas.update()

    # =========================================================== impostazioni
    def settings_dialog(self, page=0):
        SettingsDialog(self, page).exec()

    def set_audio(self, cfg):
        self.audio_cfg = audio_settings(cfg)
        self.audio.configure(self.audio_cfg)
        self.meter.set_threshold(self.audio_cfg["threshold_db"])
        self.meter.gate_on = self.audio_cfg["gate_on"]
        if self.mon_btn.isChecked() != self.audio_cfg["mon_on"]:
            self.mon_btn.blockSignals(True)
            self.mon_btn.setChecked(self.audio_cfg["mon_on"])
            self.mon_btn.blockSignals(False)
        self._qs_save("audio", self.audio_cfg)

    def _mon_toggled(self, on):
        cfg = dict(self.audio_cfg)
        cfg["mon_on"] = bool(on)
        self.set_audio(cfg)
        err = self.audio.monitor.error if on else ""
        self.status(err or (_("Preascolto acceso") if on else _("Preascolto spento")))

    def _threshold_from_bar(self, db):
        cfg = dict(self.audio_cfg)
        cfg["threshold_db"] = db
        self.set_audio(cfg)
        self.status(_('Soglia del gate: {0:.1f} dBFS').format(db))

    def apply_settings(self, targets, rec, panel=None):
        if panel is not None and panel != self.panel_cfg:
            self.panel_cfg = panel
            self._qs_save("panel", panel)
            self._update_panel()
        if targets != self.project.targets:
            self.project.targets = targets
            self._mark_dirty()
            self._qs_save("osc_targets", targets)
            self.refresh_points()
        changed_vocab = rec["grammar"] != self.rec_cfg["grammar"] or rec["model"] != self.rec_cfg["model"]
        self.rec_cfg = rec
        self._qs_save("recognition", rec)
        self._rebuild_aligner()
        if self.listening and changed_vocab:
            self.stop_listen()
            self.start_listen()

    # =========================================================== simulazione
    def simulate_dialog(self):
        if self.doc is None:
            self.status(_('Apri prima un copione'))
            return
        if self.sim is None:
            self.sim = SimulateDialog(self)
        self.sim.show()
        self.sim.raise_()

    # =========================================================== prova con un file audio
    def audio_test_dialog(self, path=None):
        if self.doc is None:
            self.status(_('Apri prima un copione'))
            return
        if self.audiotest is None:
            from .ui_audiofile import AudioFileDialog
            self.audiotest = AudioFileDialog(self)
            self.audiotest.finished.connect(self._audio_test_closed)
            path = path or self.audiotest.suggested()
        if path:
            self.audiotest.load(path)
        self.audiotest.show()
        self.audiotest.raise_()

    def _audio_test_closed(self, _r=0):
        d, self.audiotest = self.audiotest, None
        if d is not None:
            d.deleteLater()

    def set_test_speed(self, s):
        self.test_speed = float(s)
        self.timing.set_speed(self.test_speed)

    # ------------------------------------------------------------ trascinamento di file sulla finestra
    def dragEnterEvent(self, e):
        urls = e.mimeData().urls() if e.mimeData().hasUrls() else []
        if urls and urls[0].isLocalFile():
            p = urls[0].toLocalFile().lower()
            if p.endswith((".pdf", EXT)) or is_audio(p):
                e.acceptProposedAction()

    def dropEvent(self, e):
        p = e.mimeData().urls()[0].toLocalFile()
        if is_audio(p):
            self.audio_test_dialog(p)
        elif self._confirm_discard():
            self.open_any(p)
        e.acceptProposedAction()

    # =========================================================== aiuto
    def help_dialog(self):
        d = QDialog(self)
        d.setWindowTitle(_('Guida rapida'))
        v = QVBoxLayout(d)
        t = QTextBrowser()
        t.setHtml(_(HELP))
        v.addWidget(t)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(d.reject)
        v.addWidget(bb)
        fit_to_screen(d, 680, 560)
        d.exec()

    def about(self):
        QMessageBox.about(self, APP_NAME, _('<b>{0}</b> {1}<br>Segue il parlato e fa scorrere il copione, con pulsanti OSC per CueStage, QLab, SCS, ChamSys e altri.<br><br>Riconoscimento vocale offline: Vosk (Apache 2.0), Whisper/CTranslate2 (MIT). PDF: PyMuPDF (AGPL). Audio: libsndfile (LGPL).<br>Licenza GPL v3.').format(APP_NAME, __version__))


class _TopBar(QWidget):
    """Contenitore della barra in alto: dichiara come minimo la larghezza su due righe, così la finestra si può
    stringere; quando il contenitore cambia larghezza la barra si ridispone."""

    def __init__(self, mw):
        super().__init__()
        self.mw = mw

    def minimumSizeHint(self):
        lay = self.layout()
        h = lay.minimumSize().height() if lay is not None else 0
        return QSize(self.mw.top_min_width(), h)

    def sizeHint(self):
        s = super().sizeHint()
        return QSize(max(s.width(), self.mw.top_min_width()), s.height())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.mw._relayout_bar()


class SimulateDialog(QDialog):
    """Prova senza microfono: scrivi le parole «dette», oppure lascia che legga il copione da solo con errori."""

    def __init__(self, mw):
        super().__init__(mw)
        self.mw = mw
        self.setWindowTitle(_('Simula il parlato'))
        self.setModal(False)
        self.words = []
        v = QVBoxLayout(self)
        v.addWidget(QLabel(_("Scrivi ciò che «dice l'attore» e premi Invio (anche con errori o parole saltate):")))
        self.edit = QLineEdit()
        self.edit.returnPressed.connect(self._typed)
        v.addWidget(self.edit)
        f = QFormLayout()
        self.speed = QDoubleSpinBox()
        self.speed.setRange(0.5, 6)
        self.speed.setValue(2.5)
        self.speed.setSuffix(_(' parole/s'))
        f.addRow(_('Lettura automatica'), self.speed)
        self.err = QSpinBox()
        self.err.setRange(0, 60)
        self.err.setValue(20)
        self.err.setSuffix(_(' % di errori'))
        f.addRow(_('Errori di riconoscimento'), self.err)
        v.addLayout(f)
        h = QHBoxLayout()
        self.play = QPushButton(_('▶ Leggi dalla posizione corrente'))
        self.play.setCheckable(True)
        self.play.toggled.connect(self._toggle)
        h.addWidget(self.play)
        v.addLayout(h)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._step)
        self.rnd = random.Random(1)
        self.t = 0
        fit_to_screen(self, 520, 200)

    def _send(self, text):
        self.words.extend(normalize(text))
        self.words = self.words[-24:]
        self.mw.on_hypothesis(list(self.words), text)

    def _typed(self):
        t = self.edit.text().strip()
        if t:
            self._send(t)
        self.edit.clear()

    def _toggle(self, on):
        if on:
            d = self.mw.doc
            self.t = d.last_token_at_or_before(self.mw.cueing.pos) + 1 if self.mw.cueing.pos >= 0 else 0
            self.words = []
            self.timer.start(int(1000 / self.speed.value()))
            self.play.setText(_('■ Ferma la lettura'))
        else:
            self.timer.stop()
            self.play.setText(_('▶ Leggi dalla posizione corrente'))

    def _step(self):
        d = self.mw.doc
        if d is None or self.t >= len(d.tokens):
            self.play.setChecked(False)
            return
        w = d.tokens[self.t]
        self.t += 1
        e = self.err.value() / 100.0
        r = self.rnd.random()
        if r < e / 2:
            return                                   # parola persa
        if r < e:
            w = self.rnd.choice(d.tokens)            # parola sbagliata
        self._send(w)
        self.timer.setInterval(int(1000 / self.speed.value()))

    def closeEvent(self, e):
        self.play.setChecked(False)
        super().closeEvent(e)


HELP = """
<h3>Preparazione</h3>
<p><b>File › Apri copione PDF…</b> (solo PDF con testo selezionabile, es. esportato da Word o LibreOffice).
In azzurro le parole <b>escluse dal riconoscimento</b>: didascalie tra parentesi, corsivo e grassetto (titoli,
luci), righe tipo «MUSICA:», l'indice. Si cambiano con il menu sulla parola.</p>
<p><b>Clic su una parola</b> (o trascina su più parole): <b>＋ Pulsante OSC</b> o <b>＋ Annotazione</b> su
quella parola. Ogni pulsante può inviare <b>più comandi OSC</b>, verso i <b>profili OSC</b>
(Impostazioni › Profili OSC, anche con la ricerca in rete), e avere note e immagini. I plugin (CueStage, QLab, SCS, ChamSys MagicQ, Generico) offrono i comandi già
pronti. In preparazione un clic sul pulsante lo modifica; tasto destro = modifica, prova, elimina.</p>
<p>Salva il progetto (<b>.sfollow</b>) accanto al PDF: contiene pulsanti, note, parole incluse/escluse e i
profili OSC.</p>
<h3>Spettacolo</h3>
<p><b>ASCOLTA</b> (F5) avvia il riconoscimento dal canale scelto (Impostazioni › Audio e gate). La riga
detta è evidenziata in giallo e la vista la segue. I punti già passati diventano grigi; il prossimo pulsante e la
prossima nota lampeggiano. Il riquadro <b>Prossimo evento</b> mostra le frasi che precedono il pulsante
(in giallo le parole già dette) e il pulsante: <b>Spazio</b> lo invia.</p>
<p>L'ultimo pulsante passato resta premibile (arancione se non è stato inviato) finché non premi il pulsante
successivo: la pressione di un pulsante disattiva i precedenti.</p>
<p><b>❚❚ Sospendi ascolto</b> (F8): modo manuale, la posizione si sposta solo a mano; <b>▶ Riprendi
ascolto</b> riparte dalla posizione attuale (il modello resta caricato).</p>
<p><b>Doppio clic</b> su una parola = riparti da lì (anche Ctrl+↑ / Ctrl+↓ per riga). Tornando indietro i punti
successivi si riattivano. Se scorri a mano, la vista smette di seguire per 5 secondi (o spegni «Segui», F6).</p>
<h3>Tempi</h3>
<p>Con <b>⏺ Tempi</b> acceso (F7) ogni replica registra il tempo tra un evento e il successivo. Accanto a
«Prossimo evento» compare il <b>tempo stimato</b> (media delle repliche) e il conto alla rovescia.</p>
<h3>Gate</h3>
<p>Sotto la soglia al riconoscitore arriva silenzio: il cursore bianco sul meter (anche quello in alto) regola la
soglia. Meglio una mandata del mixer con le sole voci, senza musica né effetti.</p>
<h3>Lingue</h3>
<p><b>Lingua › Lingua del copione</b>: italiano (incluso), inglese, sloveno; i modelli si scaricano da
<b>Lingua › Lingue del riconoscimento</b>. <b>Lingua › Lingua dell'interfaccia</b>: italiano, English, slovenščina.</p>
<h3>Senza attori</h3>
<p><b>Spettacolo › Prova con un file audio</b> (F9, o trascina il file sulla finestra): la registrazione di una
prova o di una replica (WAV, FLAC, MP3, OGG; con ffmpeg anche M4A e video) passa per gate e riconoscitore come se
arrivasse dalla scheda, anche accelerata fino a 3×. <b>Spettacolo › Simula il parlato</b> (F4): scrivi frasi o
lascia leggere il copione con errori casuali.</p>
"""

