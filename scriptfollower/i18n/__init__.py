"""Traduzioni dell'interfaccia: italiano (lingua del codice), inglese, sloveno.

_("testo italiano") restituisce la traduzione nella lingua scelta (Lingua › Lingua dell'interfaccia), oppure il
testo italiano se manca. I cataloghi sono file JSON {italiano: traduzione} in questa cartella (en.json, sl.json).
Le stringhe con valori variabili usano i segnaposto di str.format: _("Pagina {0}").format(n).
"""
import json
import os

UI_LANGS = [("it", "Italiano"), ("en", "English"), ("sl", "Slovenščina")]
HERE = os.path.dirname(os.path.abspath(__file__))
_lang = "it"
_cat = {}


def set_language(code):
    """Carica il catalogo della lingua (da chiamare all'avvio, prima di costruire le finestre)."""
    global _lang, _cat
    _lang = code if code in dict(UI_LANGS) else "it"
    _cat = {}
    if _lang != "it":
        try:
            with open(os.path.join(HERE, f"{_lang}.json"), encoding="utf-8") as f:
                _cat = {k: v for k, v in json.load(f).items() if v}
        except (OSError, ValueError):
            _cat = {}
    return _lang


def language():
    return _lang


def _(text):
    return _cat.get(text, text)


# Testi dei pulsanti standard di Qt per le lingue senza qtbase_<lingua>.qm (Qt 6 non ha lo sloveno).
_QT_STD = {
    "sl": {"OK": "V redu", "&OK": "&V redu", "Cancel": "Prekliči", "&Cancel": "&Prekliči",
           "Yes": "Da", "&Yes": "&Da", "No": "Ne", "&No": "&Ne", "Yes to &All": "Da za &vse",
           "N&o to All": "N&e za vse", "Close": "Zapri", "&Close": "&Zapri", "Save": "Shrani", "&Save": "&Shrani",
           "Discard": "Zavrzi", "Don't Save": "Ne shrani", "Close without Saving": "Zapri brez shranjevanja",
           "Apply": "Uporabi", "Reset": "Ponastavi", "Help": "Pomoč", "Open": "Odpri", "&Open": "&Odpri",
           "Abort": "Prekini", "Retry": "Poskusi znova", "Ignore": "Prezri", "Restore Defaults": "Obnovi privzeto",
           "Save All": "Shrani vse", "Show Details...": "Pokaži podrobnosti...",
           "Hide Details...": "Skrij podrobnosti...", "Look in:": "Išči v:", "File &name:": "&Ime datoteke:",
           "Files of type:": "Vrsta datotek:", "&Choose": "&Izberi", "Choose": "Izberi"},
}


def _qt_translator_base():
    from PySide6.QtCore import QTranslator
    return QTranslator


def QtFallback(code, parent=None):
    """QTranslator con i testi dei pulsanti standard (contesti QPlatformTheme, QDialogButtonBox, QMessageBox…)."""
    base = _qt_translator_base()

    class _Fallback(base):
        def __init__(self, table, parent):
            super().__init__(parent)
            self.table = table

        def translate(self, context, source, disambiguation=None, n=-1):
            # None (non "") = nessuna traduzione: Qt usa il testo originale; "" lo cancellerebbe
            return self.table.get(source or "") or None

        def isEmpty(self):
            return not self.table

    return _Fallback(_QT_STD.get(code, {}), parent)
