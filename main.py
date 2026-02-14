"""
MediaClean — Entry point.
Organiza tus descargas de series para Plex usando TMDB.
"""

import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont

from mediaclean.ui.main_window import MainWindow
from mediaclean.ui.style import STYLE_SHEET


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("MediaClean")
    app.setOrganizationName("MediaClean")

    # Apply stylesheet
    app.setStyleSheet(STYLE_SHEET)

    # Set default font
    font = QFont("Segoe UI", 10)
    app.setFont(font)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
