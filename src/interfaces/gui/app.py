from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from src.interfaces.gui.main_window import MainWindow
from src.interfaces.gui.style import get_qss, on_theme_changed


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Sky Music Automation")
    app.setStyle("Fusion")
    app.setStyleSheet(get_qss())

    on_theme_changed(lambda: app.setStyleSheet(get_qss()))

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
