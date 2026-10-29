"""Finestre di modifica dei punti: pulsante OSC (con più comandi) e annotazione."""
import copy

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QGroupBox, QMessageBox,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
                               QPushButton, QSpinBox, QVBoxLayout, QWidget)

from .osc import CUSTOM, clean_targets, describe, find_command, find_target, plugins, render, send_commands
from .project import NOTE_COLORS
from .i18n import _
from .ui_util import fit_to_screen, scroll_wrap


def _rows_height(w, rows, row=None):
    """Altezza in pixel per mostrare «rows» righe di testo nel widget w."""
    fm = w.fontMetrics()
    return int((row or fm.lineSpacing()) * rows + 2 * w.frameWidth() + 12)


class CommandEditor(QWidget):
    """Un comando OSC: profilo di destinazione, comando del plugin con i suoi parametri o indirizzo libero."""

    def __init__(self, targets, on_change, parent=None):
        super().__init__(parent)
        self.targets = clean_targets(targets)
        self.on_change = on_change
        self.cmd = None
        self._loading = False
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        f = QFormLayout()
        self.target = QComboBox()
        for i, t in enumerate(self.targets):
            pl = _(plugins().get(t["plugin"], {}).get("name", t["plugin"]))
            self.target.addItem(f"{i + 1}. {t['name']}  ({pl} · {t['host']}:{t['port']})", t["id"])
        self.target.currentIndexChanged.connect(self._target_changed)
        f.addRow(_('Profilo OSC'), self.target)
        self.command = QComboBox()
        self.command.currentIndexChanged.connect(self._command_changed)
        f.addRow(_('Comando'), self.command)
        v.addLayout(f)
        self.params_box = QWidget()
        self.params = QFormLayout(self.params_box)
        self.params.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self.params_box)
        self.custom_box = QWidget()
        fc = QFormLayout(self.custom_box)
        fc.setContentsMargins(0, 0, 0, 0)
        self.address = QLineEdit()
        self.address.setPlaceholderText("/indirizzo/osc")
        self.address.textEdited.connect(self._changed)
        fc.addRow(_('Indirizzo'), self.address)
        self.args = QLineEdit()
        self.args.setPlaceholderText(_('argomenti separati da spazi: 1  2.5  "testo"  true'))
        self.args.textEdited.connect(self._changed)
        fc.addRow(_('Argomenti'), self.args)
        v.addWidget(self.custom_box)
        self.preview = QLabel()
        self.preview.setStyleSheet("color:#6cc070;font-family:monospace;")
        self.preview.setWordWrap(True)
        v.addWidget(self.preview)
        self.help = QLabel()
        self.help.setWordWrap(True)
        self.help.setStyleSheet("color:#999;")
        v.addWidget(self.help)
        self.inputs = {}
        self.setEnabled(False)

    def load(self, cmd):
        self.cmd = cmd
        self.setEnabled(cmd is not None)
        if cmd is None:
            self.preview.setText("")
            return
        self._loading = True
        try:
            tid = find_target(cmd, self.targets)["id"]
        except KeyError:
            tid = self.targets[0]["id"]
        self.target.setCurrentIndex(max(0, self.target.findData(tid)))
        self._fill_commands()
        i = self.command.findData(cmd.get("cmd") or CUSTOM)
        self.command.setCurrentIndex(max(0, i))
        self.address.setText(cmd.get("address") or "")
        self.args.setText(cmd.get("args") or "")
        self._build_params()
        self._loading = False
        self._update_preview()

    def _plugin(self):
        tid = self.target.currentData()
        t = next((x for x in self.targets if x["id"] == tid), self.targets[0])
        return t["plugin"]

    def _fill_commands(self):
        self.command.blockSignals(True)
        self.command.clear()
        p = plugins().get(self._plugin())
        for c in (p or {}).get("commands") or []:
            if c["id"] != "custom" or self._plugin() != "generico":
                self.command.addItem(_(c["label"]), c["id"])
        self.command.addItem(_('✎ Indirizzo e argomenti personalizzati'), CUSTOM)
        self.command.blockSignals(False)

    def _target_changed(self, *_a):
        if self._loading or self.cmd is None:
            return
        old = self.command.currentData()
        self.cmd["target_id"] = self.target.currentData()
        self.cmd["target"] = max(0, self.target.currentIndex())
        self._fill_commands()
        i = self.command.findData(old)
        self.command.setCurrentIndex(i if i >= 0 else 0)
        self._command_changed()

    def _command_changed(self, *_a):
        if self._loading or self.cmd is None:
            return
        self.cmd["cmd"] = self.command.currentData() or CUSTOM
        self.cmd.setdefault("values", {})
        self._build_params()
        self._changed()

    def _build_params(self):
        while self.params.rowCount():
            self.params.removeRow(0)
        self.inputs = {}
        cid = self.command.currentData() or CUSTOM
        self.custom_box.setVisible(cid == CUSTOM)
        c = find_command(self._plugin(), cid) if cid != CUSTOM else None
        self.params_box.setVisible(bool(c and c.get("params")))
        pl = plugins().get(self._plugin(), {})
        self.help.setText(_((c or {}).get("help") or (pl.get("notes") if cid == CUSTOM else "") or ""))
        if not c:
            return
        vals = self.cmd.setdefault("values", {})
        for prm in c.get("params") or []:
            t = prm.get("type")
            cur = vals.get(prm["id"], prm.get("default", ""))
            if t == "int":
                w = QSpinBox()
                w.setRange(int(prm.get("min", -999999)), int(prm.get("max", 999999)))
                try:
                    w.setValue(int(float(cur)))
                except (TypeError, ValueError):
                    pass
                w.valueChanged.connect(lambda val, k=prm["id"]: self._set_val(k, val))
            elif t == "float":
                w = QDoubleSpinBox()
                w.setDecimals(int(prm.get("decimals", 2)))
                w.setRange(float(prm.get("min", -999999)), float(prm.get("max", 999999)))
                try:
                    w.setValue(float(cur))
                except (TypeError, ValueError):
                    pass
                w.valueChanged.connect(lambda val, k=prm["id"]: self._set_val(k, val))
            elif t == "enum":
                w = QComboBox()
                for a, b in prm.get("items") or []:
                    w.addItem(_(b), str(a))
                w.setCurrentIndex(max(0, w.findData(str(cur))))
                w.currentIndexChanged.connect(lambda _i, k=prm["id"], ww=w: self._set_val(k, ww.currentData()))
            else:
                w = QLineEdit(str(cur))
                w.textEdited.connect(lambda val, k=prm["id"]: self._set_val(k, val))
            vals[prm["id"]] = cur
            self.inputs[prm["id"]] = w
            self.params.addRow(_(prm["label"]), w)

    def _set_val(self, k, v):
        if self.cmd is not None:
            self.cmd.setdefault("values", {})[k] = v
            self._changed()

    def _changed(self, *_a):
        if self._loading or self.cmd is None:
            return
        if (self.command.currentData() or CUSTOM) == CUSTOM:
            self.cmd["address"] = self.address.text().strip()
            self.cmd["args"] = self.args.text().strip()
        self._update_preview()
        self.on_change()

    def _update_preview(self):
        if self.cmd is None:
            return
        try:
            host, port, address, args = render(self.cmd, self.targets)
            self.preview.setText(f"→ {host}:{port}   {address} {args}")
            self.preview.setStyleSheet("color:#6cc070;font-family:monospace;")
        except Exception as e:  # noqa: BLE001
            self.preview.setText(f"⚠ {e}")
            self.preview.setStyleSheet("color:#e0b040;")


class PointDialog(QDialog):
    def __init__(self, point, targets, context_text="", parent=None, project=None):
        super().__init__(parent)
        self.project = project
        self.p = copy.deepcopy(point)
        self.p.setdefault("images", [])
        self.targets = clean_targets(targets)
        is_btn = self.p["kind"] == "button"
        self.setWindowTitle(_("Pulsante OSC") if is_btn else _("Annotazione"))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 8)
        body = QWidget()
        v = QVBoxLayout(body)                    # contenuto scorrevole: la finestra sta anche su schermi bassi
        outer.addWidget(scroll_wrap(body), 1)
        if context_text:
            ctx = QLabel(_('Al punto: «… {0}»').format(context_text))
            ctx.setWordWrap(True)
            ctx.setStyleSheet("color:#aaa;")
            v.addWidget(ctx)
        f = QFormLayout()
        if is_btn:
            self.label = QLineEdit(self.p.get("label") or "")
            self.label.setPlaceholderText(_('es. LUCI 4 · BLU'))
            f.addRow(_('Nome del pulsante'), self.label)
            self.text = QPlainTextEdit(self.p.get("text") or "")
            self.text.setPlaceholderText(_("promemoria per l'operatore: compare sotto «Prossimo evento» (facoltativo)"))
            self.text.setTabChangesFocus(True)
            self.text.setMinimumHeight(_rows_height(self.text, 5))
            f.addRow(_('Nota'), self.text)
        else:
            self.text = QPlainTextEdit(self.p.get("text") or "")
            self.text.setPlaceholderText(_('es. Entra Camilla da destra'))
            self.text.setTabChangesFocus(True)
            self.text.setMinimumHeight(_rows_height(self.text, 5))
            f.addRow(_('Testo'), self.text)
            self.color = QComboBox()
            names = [_("Giallo"), _("Azzurro"), _("Rosso"), _("Verde"), _("Viola")]
            for c, n in zip(NOTE_COLORS, names):
                self.color.addItem(f"● {n}", c)
            self.color.setCurrentIndex(max(0, self.color.findData(self.p.get("color"))))
            f.addRow(_('Colore'), self.color)
        v.addLayout(f)
        self._build_images(v)
        if is_btn:
            g = QGroupBox(_("Comandi OSC (inviati nell'ordine, tutti alla pressione)"))
            gv = QVBoxLayout(g)
            row = QHBoxLayout()
            self.list = QListWidget()
            h = _rows_height(self.list, 5, row=self.list.fontMetrics().height() + 6)
            self.list.setMinimumHeight(h)
            self.list.setMaximumHeight(h)
            self.list.currentRowChanged.connect(self._select)
            row.addWidget(self.list, 1)
            col = QVBoxLayout()
            for text, fn in ((_("＋ Aggiungi"), self._add), (_("Duplica"), self._dup), ("▲", lambda: self._move(-1)),
                             ("▼", lambda: self._move(1)), (_("Elimina"), self._del)):
                b = QPushButton(text)
                b.clicked.connect(fn)
                col.addWidget(b)
            col.addStretch(1)
            row.addLayout(col)
            gv.addLayout(row)
            self.editor = CommandEditor(self.targets, self._refresh_row)
            gv.addWidget(self.editor)
            test = QPushButton(_('Prova: invia adesso'))
            test.clicked.connect(self._test)
            gv.addWidget(test, 0, Qt.AlignLeft)
            self.msg = QLabel()
            self.msg.setWordWrap(True)
            gv.addWidget(self.msg)
            v.addWidget(g)
            for c in self.p.get("commands") or []:
                self.list.addItem(QListWidgetItem(describe(c, self.targets)))
            if not self.p.get("commands"):
                self._add()
            else:
                self.list.setCurrentRow(0)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        bb.setContentsMargins(9, 0, 9, 0)
        outer.addWidget(bb)
        fit_to_screen(self, 660, 880 if is_btn else 470)

    # ------------------------------------------------------------ immagini e foto
    def _build_images(self, v):
        g = QGroupBox(_('Immagini e foto (miniature sotto la nota; clic per aprirle in una finestra)'))
        h = QHBoxLayout(g)
        self.imgs = QListWidget()
        self.imgs.setViewMode(QListWidget.IconMode)
        self.imgs.setIconSize(QSize(96, 72))
        self.imgs.setFlow(QListWidget.LeftToRight)
        self.imgs.setWrapping(False)
        self.imgs.setResizeMode(QListWidget.Adjust)
        self.imgs.setMovement(QListWidget.Static)
        self.imgs.setSpacing(4)
        self.imgs.setFixedHeight(118)
        self.imgs.setAcceptDrops(True)
        self.imgs.viewport().setAcceptDrops(True)
        self.imgs.setDragDropMode(QListWidget.DropOnly)
        self.imgs.itemDoubleClicked.connect(lambda it: self._open_image(self.imgs.row(it)))
        self.imgs.dragEnterEvent = self._drag_enter
        self.imgs.dragMoveEvent = self._drag_enter
        self.imgs.dropEvent = self._drop
        h.addWidget(self.imgs, 1)
        col = QVBoxLayout()
        for text, fn in ((_("＋ Aggiungi…"), self._add_images),
                         (_("Apri"), lambda: self._open_image(self.imgs.currentRow())),
                         ("◀", lambda: self._move_image(-1)), ("▶", lambda: self._move_image(1)),
                         (_("Rimuovi"), self._remove_image)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            col.addWidget(b)
        col.addStretch(1)
        h.addLayout(col)
        v.addWidget(g)
        self._fill_images()

    def _abs(self, rel):
        from .ui_images import abs_path
        return abs_path(self.project, rel) if self.project is not None else rel

    def _fill_images(self):
        from PySide6.QtGui import QIcon
        from .ui_images import thumbnail
        self.imgs.clear()
        for rel in self.p["images"]:
            pm = thumbnail(self._abs(rel), 72)
            it = QListWidgetItem(QIcon(pm), "" if not pm.isNull() else "⚠ " + rel.split("/")[-1])
            it.setToolTip(rel + ("" if not pm.isNull() else "\n" + _("(immagine non trovata)")))
            self.imgs.addItem(it)

    def _import(self, paths):
        from .ui_images import IMAGE_EXT, import_images
        paths = [p for p in paths if p.lower().endswith(IMAGE_EXT)]
        if not paths:
            return
        if self.project is None:
            return
        try:
            rels = import_images(self.project, paths)
        except OSError as e:
            QMessageBox.warning(self, _('Immagini'), _('Copia non riuscita: {0}').format(e))
            return
        for r in rels:
            if r not in self.p["images"]:
                self.p["images"].append(r)
        self._fill_images()
        self.imgs.setCurrentRow(self.imgs.count() - 1)

    def _add_images(self):
        from .ui_images import IMAGE_FILTER
        paths, _f = QFileDialog.getOpenFileNames(self, _("Aggiungi immagini o foto"), "", _(IMAGE_FILTER))
        self._import(paths)

    def _drag_enter(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def _drop(self, e):
        self._import([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])
        e.acceptProposedAction()

    def _open_image(self, row):
        from .ui_images import ImageViewer
        if 0 <= row < len(self.p["images"]):
            ImageViewer([self._abs(r) for r in self.p["images"]], row, self.windowTitle(), self).show()

    def _move_image(self, d):
        r = self.imgs.currentRow()
        n = r + d
        im = self.p["images"]
        if 0 <= r < len(im) and 0 <= n < len(im):
            im[r], im[n] = im[n], im[r]
            self._fill_images()
            self.imgs.setCurrentRow(n)

    def _remove_image(self):
        r = self.imgs.currentRow()
        if 0 <= r < len(self.p["images"]):
            del self.p["images"][r]           # il file resta nella cartella delle immagini
            self._fill_images()

    # ------------------------------------------------------------ comandi
    def _cmds(self):
        return self.p.setdefault("commands", [])

    def _select(self, row):
        cmds = self._cmds()
        self.editor.load(cmds[row] if 0 <= row < len(cmds) else None)

    def _refresh_row(self):
        r = self.list.currentRow()
        if 0 <= r < len(self._cmds()):
            self.list.item(r).setText(describe(self._cmds()[r], self.targets))

    def _add(self):
        last = self._cmds()[-1] if self._cmds() else None
        try:
            tt = find_target(last, self.targets) if last else self.targets[0]
        except KeyError:
            tt = self.targets[0]
        t = self.targets.index(tt)
        pl = tt["plugin"]
        first = (plugins().get(pl) or {}).get("commands") or []
        cid = first[0]["id"] if first and not (pl == "generico") else CUSTOM
        c = {"target": t, "target_id": tt["id"], "cmd": cid, "values": {}, "address": "/", "args": ""}
        self._cmds().append(c)
        self.list.addItem(QListWidgetItem(describe(c, self.targets)))
        self.list.setCurrentRow(self.list.count() - 1)

    def _dup(self):
        r = self.list.currentRow()
        if 0 <= r < len(self._cmds()):
            c = copy.deepcopy(self._cmds()[r])
            self._cmds().insert(r + 1, c)
            self.list.insertItem(r + 1, QListWidgetItem(describe(c, self.targets)))
            self.list.setCurrentRow(r + 1)

    def _move(self, d):
        r = self.list.currentRow()
        n = r + d
        cmds = self._cmds()
        if 0 <= r < len(cmds) and 0 <= n < len(cmds):
            cmds[r], cmds[n] = cmds[n], cmds[r]
            it = self.list.takeItem(r)
            self.list.insertItem(n, it)
            self.list.setCurrentRow(n)

    def _del(self):
        r = self.list.currentRow()
        if 0 <= r < len(self._cmds()):
            del self._cmds()[r]
            self.list.takeItem(r)
            self._select(self.list.currentRow())

    def _test(self):
        sent, errors = send_commands(self._cmds(), self.targets)
        if errors:
            self.msg.setText("⚠ " + "\n⚠ ".join(errors))
            self.msg.setStyleSheet("color:#e66;")
        else:
            self.msg.setText(_("Inviato: ") + " · ".join(sent) if sent else _("Nessun comando da inviare"))
            self.msg.setStyleSheet("color:#6c6;")

    # ------------------------------------------------------------ risultato
    def result_point(self):
        if self.p["kind"] == "button":
            self.p["label"] = self.label.text().strip() or "Pulsante"
            self.p["text"] = self.text.toPlainText().strip()
        else:
            self.p["text"] = self.text.toPlainText().strip()
            self.p["color"] = self.color.currentData()
        return self.p
