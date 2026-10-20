"""Finestre di modifica dei punti: pulsante OSC (con più comandi) e annotazione."""
import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
                               QPushButton, QSpinBox, QVBoxLayout, QWidget)

from .osc import CUSTOM, N_TARGETS, clean_targets, describe, find_command, plugins, render, send_commands
from .project import NOTE_COLORS


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
            pl = plugins().get(t["plugin"], {}).get("name", t["plugin"])
            self.target.addItem(f"{i + 1}. {t['name']}  ({pl} · {t['host']}:{t['port']})", i)
        self.target.currentIndexChanged.connect(self._target_changed)
        f.addRow("Profilo OSC", self.target)
        self.command = QComboBox()
        self.command.currentIndexChanged.connect(self._command_changed)
        f.addRow("Comando", self.command)
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
        fc.addRow("Indirizzo", self.address)
        self.args = QLineEdit()
        self.args.setPlaceholderText('argomenti separati da spazi: 1  2.5  "testo"  true')
        self.args.textEdited.connect(self._changed)
        fc.addRow("Argomenti", self.args)
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
        self.target.setCurrentIndex(max(0, min(N_TARGETS - 1, int(cmd.get("target") or 0))))
        self._fill_commands()
        i = self.command.findData(cmd.get("cmd") or CUSTOM)
        self.command.setCurrentIndex(max(0, i))
        self.address.setText(cmd.get("address") or "")
        self.args.setText(cmd.get("args") or "")
        self._build_params()
        self._loading = False
        self._update_preview()

    def _plugin(self):
        t = self.targets[self.target.currentData() or 0]
        return t["plugin"]

    def _fill_commands(self):
        self.command.blockSignals(True)
        self.command.clear()
        p = plugins().get(self._plugin())
        for c in (p or {}).get("commands") or []:
            if c["id"] != "custom" or self._plugin() != "generico":
                self.command.addItem(c["label"], c["id"])
        self.command.addItem("✎ Indirizzo e argomenti personalizzati", CUSTOM)
        self.command.blockSignals(False)

    def _target_changed(self, *_a):
        if self._loading or self.cmd is None:
            return
        old = self.command.currentData()
        self.cmd["target"] = self.target.currentData() or 0
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
        self.help.setText((c or {}).get("help") or (pl.get("notes") if cid == CUSTOM else "") or "")
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
                    w.addItem(b, str(a))
                w.setCurrentIndex(max(0, w.findData(str(cur))))
                w.currentIndexChanged.connect(lambda _i, k=prm["id"], ww=w: self._set_val(k, ww.currentData()))
            else:
                w = QLineEdit(str(cur))
                w.textEdited.connect(lambda val, k=prm["id"]: self._set_val(k, val))
            vals[prm["id"]] = cur
            self.inputs[prm["id"]] = w
            self.params.addRow(prm["label"], w)

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
    def __init__(self, point, targets, context_text="", parent=None):
        super().__init__(parent)
        self.p = copy.deepcopy(point)
        self.targets = clean_targets(targets)
        is_btn = self.p["kind"] == "button"
        self.setWindowTitle("Pulsante OSC" if is_btn else "Annotazione")
        v = QVBoxLayout(self)
        if context_text:
            ctx = QLabel(f"Al punto: «… {context_text}»")
            ctx.setWordWrap(True)
            ctx.setStyleSheet("color:#aaa;")
            v.addWidget(ctx)
        f = QFormLayout()
        if is_btn:
            self.label = QLineEdit(self.p.get("label") or "")
            self.label.setPlaceholderText("es. LUCI 4 · BLU")
            f.addRow("Nome del pulsante", self.label)
            self.text = QLineEdit(self.p.get("text") or "")
            self.text.setPlaceholderText("promemoria per l'operatore: compare sotto «Prossimo evento» (facoltativo)")
            f.addRow("Nota", self.text)
        else:
            self.text = QPlainTextEdit(self.p.get("text") or "")
            self.text.setPlaceholderText("es. Entra Camilla da destra")
            self.text.setFixedHeight(90)
            f.addRow("Testo", self.text)
            self.color = QComboBox()
            names = ["Giallo", "Azzurro", "Rosso", "Verde", "Viola"]
            for c, n in zip(NOTE_COLORS, names):
                self.color.addItem(f"● {n}", c)
            self.color.setCurrentIndex(max(0, self.color.findData(self.p.get("color"))))
            f.addRow("Colore", self.color)
        v.addLayout(f)
        if is_btn:
            g = QGroupBox("Comandi OSC (inviati nell'ordine, tutti alla pressione)")
            gv = QVBoxLayout(g)
            row = QHBoxLayout()
            self.list = QListWidget()
            self.list.setMinimumHeight(110)
            self.list.currentRowChanged.connect(self._select)
            row.addWidget(self.list, 1)
            col = QVBoxLayout()
            for text, fn in (("＋ Aggiungi", self._add), ("Duplica", self._dup), ("▲", lambda: self._move(-1)),
                             ("▼", lambda: self._move(1)), ("Elimina", self._del)):
                b = QPushButton(text)
                b.clicked.connect(fn)
                col.addWidget(b)
            col.addStretch(1)
            row.addLayout(col)
            gv.addLayout(row)
            self.editor = CommandEditor(self.targets, self._refresh_row)
            gv.addWidget(self.editor)
            test = QPushButton("Prova: invia adesso")
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
        v.addWidget(bb)
        self.resize(620, 640 if is_btn else 260)

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
        t = int(last.get("target") or 0) if last else 0
        pl = self.targets[t]["plugin"]
        first = (plugins().get(pl) or {}).get("commands") or []
        cid = first[0]["id"] if first and not (pl == "generico") else CUSTOM
        c = {"target": t, "cmd": cid, "values": {}, "address": "/", "args": ""}
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
            self.msg.setText("Inviato: " + " · ".join(sent) if sent else "Nessun comando da inviare")
            self.msg.setStyleSheet("color:#6c6;")

    # ------------------------------------------------------------ risultato
    def result_point(self):
        if self.p["kind"] == "button":
            self.p["label"] = self.label.text().strip() or "Pulsante"
            self.p["text"] = self.text.text().strip()
        else:
            self.p["text"] = self.text.toPlainText().strip()
            self.p["color"] = self.color.currentData()
        return self.p
