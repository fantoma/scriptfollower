"""Finestra «Vocabolario del copione»: parole che il modello non conosce e loro pronuncia con parole note."""
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from .align import normalize
from .languages import LANGS, find_model
from .vocab import default_alias, suggest
from .i18n import _
from .ui_util import fit_to_screen

C_WORD, C_COUNT, C_SAY, C_STATE = range(4)


class _Relay(QObject):
    done = Signal(object, str)


class VocabDialog(QDialog):
    def __init__(self, mw):
        super().__init__(mw)
        self.mw = mw
        self.doc = mw.doc
        self.setWindowTitle(_('Vocabolario del copione'))
        self.known = None                          # funzione parola -> bool (dopo il controllo col modello)
        self.counts = self.doc.word_counts()
        v = QVBoxLayout(self)
        intro = QLabel(
            _("Il modello vocale riconosce solo le parole del suo vocabolario: una parola del copione che non conosce (nomi propri, neologismi, numeri in cifre…) non verrà mai «sentita». Aggiungere parole nuove al modello italiano non si può fare da qui: richiede di ricompilarne il grafo, con strumenti che Alphacephei fornisce solo su richiesta. Si può però dare a ogni parola una <b>pronuncia</b> fatta di parole che il modello conosce (es. «Gesualdi» → «gesù aldi»): verrà riconosciuta e agganciata alla parola del copione. Le parole sconosciute senza pronuncia vengono saltate dall'inseguimento senza penalità. I numeri in cifre si leggono già in lettere (118 → centodiciotto)."))
        intro.setWordWrap(True)
        v.addWidget(intro)
        row = QHBoxLayout()
        self.check_btn = QPushButton(_('Controlla le parole con il modello'))
        self.check_btn.clicked.connect(self.check)
        row.addWidget(self.check_btn)
        self.auto_btn = QPushButton(_('Proposte automatiche'))
        self.auto_btn.setToolTip(_('Per le parole sconosciute senza pronuncia: accento finale, parola divisa in parti note («aspirapolvere» → «aspira polvere»), numeri'))
        self.auto_btn.clicked.connect(self.auto)
        self.auto_btn.setEnabled(False)
        row.addWidget(self.auto_btn)
        row.addStretch(1)
        self.add_word = QLineEdit()
        self.add_word.setPlaceholderText(_('aggiungi una parola a mano…'))
        self.add_word.returnPressed.connect(self._add_manual)
        row.addWidget(self.add_word)
        v.addLayout(row)
        io = QHBoxLayout()
        imp = QPushButton(_("Importa vocabolario…"))
        imp.setToolTip(_("Aggiunge le pronunce di un file (JSON o testo «parola = pronuncia»); quelle del file "
                         "sostituiscono le pronunce delle stesse parole"))
        imp.clicked.connect(self.import_file)
        exp = QPushButton(_("Esporta vocabolario…"))
        exp.setToolTip(_("Salva le pronunce in un file, per usarle in un altro copione"))
        exp.clicked.connect(self.export_file)
        io.addWidget(imp)
        io.addWidget(exp)
        io.addStretch(1)
        v.addLayout(io)
        self.msg = QLabel()
        self.msg.setWordWrap(True)
        v.addWidget(self.msg)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([_('Parola del copione'), _('Volte'), _('Pronuncia (parole note al modello)'),
                                              _('Stato')])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(C_WORD, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(C_COUNT, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(C_SAY, QHeaderView.Stretch)
        hh.setSectionResizeMode(C_STATE, QHeaderView.ResizeToContents)
        self.table.itemChanged.connect(self._edited)
        v.addWidget(self.table, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.relay = _Relay()
        self.relay.done.connect(self._checked)
        self._filling = False
        self._fill({w: a for w, a in self.doc.aliases.items()})
        if self.mw.rec.model is not None and hasattr(self.mw.rec.model, "vosk_model_find_word") \
                and LANGS[self.mw.project.language]["engine"] == "vosk":  # modello già caricato
            self._checked(self.mw.rec.known_fn(self.mw.rec.model), "")
        else:
            self.msg.setText(_('Premi «Controlla le parole con il modello» per sapere quali parole non conosce (serve qualche secondo per caricare il modello).'))
            self.msg.setStyleSheet("color:#999;")
        fit_to_screen(self, 760, 620)

    # ------------------------------------------------------------ tabella
    def _fill(self, aliases, unknown=()):
        self._filling = True
        words = sorted(set(aliases) | set(unknown), key=lambda w: (-self.counts.get(w, 0), w))
        self.table.setRowCount(len(words))
        for r, w in enumerate(words):
            it = QTableWidgetItem(w)
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(r, C_WORD, it)
            c = QTableWidgetItem(str(self.counts.get(w, 0)))
            c.setFlags(c.flags() & ~Qt.ItemIsEditable)
            c.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(r, C_COUNT, c)
            self.table.setItem(r, C_SAY, QTableWidgetItem(aliases.get(w, "")))
            s = QTableWidgetItem()
            s.setFlags(s.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(r, C_STATE, s)
            self._state(r)
        self._filling = False

    def _rows(self):
        for r in range(self.table.rowCount()):
            yield r, self.table.item(r, C_WORD).text(), self.table.item(r, C_SAY).text().strip()

    def _state(self, r):
        w = self.table.item(r, C_WORD).text()
        say = self.table.item(r, C_SAY).text().strip()
        it = self.table.item(r, C_STATE)
        toks = normalize(say) if say else normalize(default_alias(w, self.doc.lang) or w)
        if self.known is None:
            it.setText("—")
            it.setForeground(QColor(150, 150, 158))
            return
        missing = [t for t in toks if not self.known(t)]
        if not missing:
            it.setText(_('✔ riconoscibile'))
            it.setForeground(QColor(108, 192, 112))
        elif say:
            it.setText("⚠ " + _("non note: {0}").format(", ".join(missing)))
            it.setForeground(QColor(224, 160, 48))
        else:
            it.setText(_('✘ sconosciuta (saltata)'))
            it.setForeground(QColor(220, 90, 80))

    def _edited(self, item):
        if not self._filling and item.column() == C_SAY:
            self._state(item.row())

    # ------------------------------------------------------------ controllo col modello
    def check(self):
        lang = self.mw.project.language
        if LANGS[lang]["engine"] != "vosk":
            self.msg.setText(_("Con Whisper (sloveno) il vocabolario è aperto: tutte le parole possono essere "
                               "riconosciute, il controllo non serve."))
            self.msg.setStyleSheet("color:#6c6;")
            return
        path = find_model(lang)
        if not path:
            self.msg.setText(_('Nessun modello vocale: scaricalo da Lingua › Lingue del riconoscimento.'))
            self.msg.setStyleSheet("color:#e66;")
            return
        if not self.mw.rec.available():
            self.msg.setText(_('Vosk non è installato.'))
            self.msg.setStyleSheet("color:#e66;")
            return
        self.check_btn.setEnabled(False)
        self.msg.setText(_('Carico il modello e controllo le parole…'))
        self.msg.setStyleSheet("color:#999;")

        def run():
            try:
                model = self.mw.rec.load_model(path)
                self.relay.done.emit(self.mw.rec.known_fn(model), "")
            except Exception as e:  # noqa: BLE001
                self.relay.done.emit(None, str(e))
        threading.Thread(target=run, daemon=True).start()

    def _checked(self, known, err):
        self.check_btn.setEnabled(True)
        if known is None:
            self.msg.setText(_('Controllo non riuscito: {0}').format(err))
            self.msg.setStyleSheet("color:#e66;")
            return
        self.known = known
        aliases = {w: s for _r, w, s in self._rows()}
        unknown = [w for w in self.counts
                   if not all(known(t) for t in normalize(default_alias(w, self.doc.lang) or w))]
        self._fill(aliases, unknown)
        n_occ = sum(self.counts[w] for w in unknown)
        tot = sum(self.counts.values())
        self.msg.setText(_('{0} parole su {1} non sono nel vocabolario del modello ({2} occorrenze su {3}, {4:.1f}% del parlato). Le più frequenti sono in cima.').format(len(unknown), len(self.counts), n_occ, tot, 100.0 * n_occ / max(1, tot)))
        self.msg.setStyleSheet("color:#6c6;" if not unknown else "color:#e0b040;")
        self.auto_btn.setEnabled(True)

    def auto(self):
        if self.known is None:
            return
        n = 0
        for r, w, say in list(self._rows()):
            if not say:
                s = suggest(w, self.known, self.doc.lang)
                if s:
                    self.table.item(r, C_SAY).setText(s)
                    n += 1
        if n:
            self.msg.setText((_("1 proposta inserita") if n == 1 else _("{0} proposte inserite").format(n))
                             + ": " + _("controllale (puoi modificarle o cancellarle) e premi OK."))
        else:
            self.msg.setText(_("Nessuna proposta automatica: scrivi tu la pronuncia con parole che il modello "
                               "conosce."))
        self.msg.setStyleSheet("color:#6c6;")

    def _add_manual(self):
        ws = normalize(self.add_word.text())
        self.add_word.clear()
        if len(ws) != 1:
            return
        aliases = {w: s for _r, w, s in self._rows()}
        if ws[0] not in aliases:
            aliases[ws[0]] = ""
            self._fill(aliases, [])
        for r, w, _s in self._rows():
            if w == ws[0]:
                self.table.setCurrentCell(r, C_SAY)
                self.table.editItem(self.table.item(r, C_SAY))

    def aliases(self):
        return {w: s for _r, w, s in self._rows() if s}

    # ------------------------------------------------------------ vocabolari su file
    def import_file(self):
        al = import_vocab_dialog(self, self.mw)
        if al is None:
            return
        cur = self.aliases()
        unknown = [w for _r, w, s in self._rows() if not s]
        cur.update(al)
        self._fill(cur, unknown)
        self.msg.setText(_("Importate {0} pronunce").format(len(al)))
        self.msg.setStyleSheet("color:#6c6;")

    def export_file(self):
        n = export_vocab_dialog(self, self.mw, self.aliases())
        if n is not None:
            self.msg.setText(_("Esportate {0} pronunce").format(n))
            self.msg.setStyleSheet("color:#6c6;")


def import_vocab_dialog(parent, mw):
    """Chiede il file e ritorna {parola: pronuncia} (None se annullato o illeggibile)."""
    import os
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    from .vocab import VOCAB_FILTER, vocab_import
    start = os.path.dirname(mw.project.pdf) if mw.project.pdf else os.path.expanduser("~")
    path, _f = QFileDialog.getOpenFileName(parent, _("Importa vocabolario"), start, _(VOCAB_FILTER))
    if not path:
        return None
    try:
        al, lang = vocab_import(path)
    except (OSError, ValueError, AttributeError) as e:
        QMessageBox.warning(parent, _("Importa vocabolario"), _("File non letto: {0}").format(e))
        return None
    if lang and lang != mw.project.language:
        r = QMessageBox.question(parent, _("Importa vocabolario"), _(
            "Il vocabolario è per la lingua «{0}», il copione è in «{1}». Importarlo lo stesso?").format(
            LANGS.get(lang, {}).get("name", lang), LANGS[mw.project.language]["name"]),
            QMessageBox.Yes | QMessageBox.No)
        if r != QMessageBox.Yes:
            return None
    return al


def export_vocab_dialog(parent, mw, aliases):
    import os
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    from .vocab import VOCAB_FILTER, vocab_export
    if not aliases:
        QMessageBox.information(parent, _("Esporta vocabolario"), _("Non ci sono pronunce da esportare."))
        return None
    stem = os.path.splitext(mw.project.pdf)[0] if mw.project.pdf else os.path.expanduser("~/vocabolario")
    path, _f = QFileDialog.getSaveFileName(parent, _("Esporta vocabolario"), stem + ".vocabolario.json", _(VOCAB_FILTER))
    if not path:
        return None
    if not os.path.splitext(path)[1]:
        path += ".json"
    try:
        return vocab_export(path, aliases, mw.project.language)
    except OSError as e:
        QMessageBox.warning(parent, _("Esporta vocabolario"), _('Salvataggio non riuscito:\n{0}').format(e))
        return None
