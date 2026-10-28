"""Finestra «Cerca in rete»: trova i dispositivi OSC e configura i profili."""
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout,
                               QHeaderView, QLabel, QProgressBar, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout)

from .netscan import scan
from .osc import plugins
from .i18n import _

C_SURE, C_NAME, C_ADDR, C_PLUGIN, C_HOW = range(5)


class _Relay(QObject):
    progress = Signal(str, float)
    done = Signal(object)


class NetScanDialog(QDialog):
    def __init__(self, settings):
        super().__init__(settings)
        self.st = settings
        self.setWindowTitle(_('Cerca dispositivi OSC in rete'))
        self.results = []
        self._cancel = False
        v = QVBoxLayout(self)
        intro = QLabel(
            _("Cerca nella rete locale: <b>CueStage</b> (ricerca delle istanze e interfaccia web), <b>QLab</b> (OSC su TCP 53000, riconosciuto dalla versione), console <b>ChamSys</b> e altri nodi luci (Art-Net), e le <b>porte OSC</b> più usate su ogni computer acceso. L'OSC viaggia su UDP e una porta UDP aperta di solito non risponde: i risultati con ✔ sono certi, quelli con ? sono indizi da verificare (per esempio con «Prova: invia adesso» in un pulsante)."))
        intro.setWordWrap(True)
        v.addWidget(intro)
        row = QHBoxLayout()
        self.go_btn = QPushButton(_('🔍 Cerca'))
        self.go_btn.clicked.connect(self.start)
        row.addWidget(self.go_btn)
        self.sweep = QCheckBox(_('Controlla tutti gli indirizzi della rete (/24)'))
        self.sweep.setChecked(True)
        self.sweep.setToolTip(_('Spento: solo questo computer, CueStage e le console Art-Net (più veloce)'))
        row.addWidget(self.sweep)
        row.addStretch(1)
        v.addLayout(row)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        v.addWidget(self.bar)
        self.msg = QLabel(_('Premi «Cerca» (10–30 secondi).'))
        self.msg.setStyleSheet("color:#999;")
        self.msg.setWordWrap(True)
        v.addWidget(self.msg)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["", _('Dispositivo'), _('Indirizzo'), _('Plugin'), _('Come è stato trovato')])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.table.horizontalHeader()
        for c in (C_SURE, C_NAME, C_ADDR, C_PLUGIN):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(C_HOW, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._sel)
        v.addWidget(self.table, 1)
        use = QHBoxLayout()
        use.addWidget(QLabel(_('Usa il dispositivo selezionato per:')))
        self.dest = QComboBox()
        use.addWidget(self.dest, 1)
        self.use_btn = QPushButton(_('Applica'))
        self.use_btn.clicked.connect(self.use_selected)
        use.addWidget(self.use_btn)
        v.addLayout(use)
        self.auto_btn = QPushButton(_('Configura automaticamente (risultati certi)'))
        self.auto_btn.setToolTip(_('Per ogni dispositivo certo: aggiorna il profilo dello stesso tipo ancora da configurare, oppure ne aggiunge uno nuovo'))
        self.auto_btn.clicked.connect(self.auto)
        v.addWidget(self.auto_btn, 0, Qt.AlignLeft)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.relay = _Relay()
        self.relay.progress.connect(self._progress)
        self.relay.done.connect(self._done)
        self._fill_dest()
        self._sel()
        self.auto_btn.setEnabled(False)
        self.resize(820, 520)

    # ------------------------------------------------------------ ricerca
    def start(self):
        self.go_btn.setEnabled(False)
        self.auto_btn.setEnabled(False)
        self.table.setRowCount(0)
        self._cancel = False
        sweep = self.sweep.isChecked()

        def run():
            try:
                res = scan(progress=lambda t, f: self.relay.progress.emit(t, f), cancel=lambda: self._cancel,
                           sweep=sweep)
            except Exception as e:  # noqa: BLE001
                self.relay.progress.emit(_('Ricerca non riuscita: {0}').format(e), 1.0)
                res = []
            self.relay.done.emit(res)
        threading.Thread(target=run, daemon=True).start()

    def _progress(self, text, frac):
        self.msg.setText(text)
        self.bar.setValue(int(frac * 1000))

    def _done(self, res):
        self.results = res
        self.go_btn.setEnabled(True)
        self.bar.setValue(1000)
        self.table.setRowCount(len(res))
        for r, d in enumerate(res):
            items = ["✔" if d["sure"] else "?", d["name"], f"{d['host']}:{d['port']}",
                     _(plugins().get(d["plugin"], {}).get("name", d["plugin"])), d["how"]]
            for c, t in enumerate(items):
                it = QTableWidgetItem(t)
                if c == C_SURE:
                    it.setForeground(QColor(108, 192, 112) if d["sure"] else QColor(224, 176, 64))
                    it.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(r, c, it)
        sure = sum(1 for d in res if d["sure"])
        if res:
            self.msg.setText(_('Trovati {0} dispositivi o porte ({1} certi). Seleziona una riga e scegli il profilo, oppure «Configura automaticamente».').format(len(res), sure))
            self.msg.setStyleSheet("color:#6c6;")
            self.table.selectRow(0)
        else:
            self.msg.setText(_("Nessun dispositivo trovato. Controlla che i programmi siano aperti e in rete, che il firewall non blocchi e, per la MagicQ, che l'uscita Art-Net sia attiva; altrimenti scrivi l'indirizzo IP a mano."))
            self.msg.setStyleSheet("color:#e0b040;")
        self.auto_btn.setEnabled(sure > 0)

    def reject(self):
        self._cancel = True
        super().reject()

    # ------------------------------------------------------------ uso dei risultati
    def _fill_dest(self):
        cur = self.dest.currentData()
        self.dest.clear()
        self.dest.addItem(_('un profilo nuovo'), None)
        for i, r in enumerate(self.st.t_widgets):
            self.dest.addItem(_('Profilo {0}: {1}  ({2})').format(i + 1, r['name'].text(), r['host'].text() or _('senza indirizzo')),
                              r["id"])
        self.dest.setCurrentIndex(max(0, self.dest.findData(cur)))

    def _sel(self):
        self.use_btn.setEnabled(bool(self.table.selectedItems()))

    def _row_by_id(self, rid):
        return next((r for r in self.st.t_widgets if r["id"] == rid), None)

    def use_selected(self):
        rows = {i.row() for i in self.table.selectedItems()}
        if not rows:
            return
        d = self.results[min(rows)]
        row = self.st.use_found(d, self._row_by_id(self.dest.currentData()))
        self._fill_dest()
        self.msg.setText(_('«{0}» ({1}:{2}) impostato nel profilo «{3}».').format(d['name'], d['host'], d['port'], row['name'].text()))
        self.msg.setStyleSheet("color:#6c6;")

    def auto(self):
        """Ogni risultato certo va nel profilo dello stesso plugin ancora da configurare, o in uno nuovo."""
        done = []
        for d in (x for x in self.results if x["sure"]):
            rows = self.st.t_widgets
            if any(r["host"].text().strip() == d["host"] and r["port"].value() == d["port"] for r in rows):
                continue                                   # c'è già
            same = [r for r in rows if r["plugin"].currentData() == d["plugin"]]
            # «da configurare»: senza indirizzo o con l'indirizzo di esempio dei profili nuovi
            free = [r for r in same if r["host"].text().strip() in ("", "192.168.1.50")]
            row = self.st.use_found(d, free[0] if free else None)
            done.append(f"{d['name']} → {row['name'].text()}")
        self._fill_dest()
        self.msg.setText((_("Configurati: ") + " · ".join(done)) if done else
                         _("Niente da configurare: i dispositivi certi sono già nei profili."))
        self.msg.setStyleSheet("color:#6c6;")
