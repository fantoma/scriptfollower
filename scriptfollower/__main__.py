import os
import sys


def main():
    from .system import IS_WIN
    if IS_WIN:
        os.environ.setdefault("SD_ENABLE_ASIO", "1")    # PortAudio con ASIO (incluso in sounddevice per Windows)
        try:                                    # icona e raggruppamento corretti nella barra delle applicazioni
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ScriptFollower.ScriptFollower")
        except Exception:
            pass
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QColor, QIcon, QPalette
    from PySide6.QtWidgets import QApplication

    class _App(QApplication):
        """Su macOS il doppio clic su un file nel Finder arriva come evento, non come argomento."""
        window = None
        pending = []

        def event(self, e):
            if e.type() == QEvent.FileOpen and e.file():
                if self.window is not None:
                    if self.window._confirm_discard():
                        self.window.open_any(e.file())
                else:
                    self.pending.append(e.file())
                return True
            return super().event(e)

    app = _App(sys.argv)
    # lingua dell'interfaccia (Lingua › Lingua dell'interfaccia), prima di costruire le finestre
    from PySide6.QtCore import QLibraryInfo, QSettings, QTranslator
    from .i18n import set_language
    code = set_language(str(QSettings("ScriptFollower", "ScriptFollower").value("ui_lang", "it") or "it"))
    qt_tr = QTranslator(app)                 # pulsanti standard di Qt (OK, Annulla, Sì, No…)
    if qt_tr.load(f"qtbase_{code}", QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
        app.installTranslator(qt_tr)
    else:                                    # Qt 6 non ha qtbase_sl: i pochi pulsanti standard a mano
        from .i18n import QtFallback
        fb = QtFallback(code, app)
        if fb.table:
            app.installTranslator(fb)
            app._qt_fallback = fb

    from .main_window import MainWindow

    app.setApplicationName("Script Follower")
    app.setWindowIcon(QIcon(os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")))
    app.setDesktopFileName("scriptfollower")
    app.setStyle("Fusion")
    pal = QPalette()                         # stessa tavolozza scura di CueStage
    base = {
        QPalette.Window: (37, 38, 44), QPalette.WindowText: (225, 225, 230),
        QPalette.Base: (28, 29, 34), QPalette.AlternateBase: (34, 35, 41),
        QPalette.ToolTipBase: (50, 50, 58), QPalette.ToolTipText: (235, 235, 240),
        QPalette.Text: (225, 225, 230), QPalette.Button: (50, 52, 60),
        QPalette.ButtonText: (230, 230, 235), QPalette.Highlight: (52, 100, 170),
        QPalette.HighlightedText: (255, 255, 255), QPalette.PlaceholderText: (120, 120, 130),
    }
    for role, rgb in base.items():
        pal.setColor(role, QColor(*rgb))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(110, 110, 118))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(110, 110, 118))
    app.setPalette(pal)

    path = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else None
    w = MainWindow(path)
    app.window = w
    for p in app.pending:
        w.open_any(p)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
