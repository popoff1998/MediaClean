"""
Qt resources and style definitions for MediaClean.
"""

STYLE_SHEET = """
QMainWindow {
    background-color: #1e1e2e;
}
QWidget {
    background-color: #1e1e2e;
    color: #cdd6f4;
    font-family: "Segoe UI", "Roboto", sans-serif;
    font-size: 13px;
}
QGroupBox {
    border: 1px solid #45475a;
    border-radius: 8px;
    margin-top: 12px;
    padding-top: 18px;
    font-weight: bold;
    color: #89b4fa;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
}
QPushButton {
    background-color: #89b4fa;
    color: #1e1e2e;
    border: none;
    border-radius: 6px;
    padding: 8px 18px;
    font-weight: bold;
    min-height: 28px;
}
QPushButton:hover {
    background-color: #b4d0fb;
}
QPushButton:pressed {
    background-color: #74a8f7;
}
QPushButton:disabled {
    background-color: #45475a;
    color: #6c7086;
}
QPushButton#btnDanger {
    background-color: #f38ba8;
    color: #1e1e2e;
}
QPushButton#btnDanger:hover {
    background-color: #f5a0b8;
}
QPushButton#btnSuccess {
    background-color: #a6e3a1;
    color: #1e1e2e;
}
QPushButton#btnSuccess:hover {
    background-color: #b8ebb4;
}
QLineEdit, QComboBox {
    background-color: #313244;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 6px 10px;
    color: #cdd6f4;
    min-height: 24px;
}
QLineEdit:focus, QComboBox:focus {
    border: 1px solid #89b4fa;
}
QTableWidget {
    background-color: #181825;
    alternate-background-color: #1e1e2e;
    border: 1px solid #45475a;
    border-radius: 6px;
    gridline-color: #313244;
    selection-background-color: #45475a;
}
QTableWidget::item {
    padding: 4px 8px;
}
QHeaderView::section {
    background-color: #313244;
    color: #89b4fa;
    border: none;
    padding: 6px 8px;
    font-weight: bold;
}
QProgressBar {
    background-color: #313244;
    border: none;
    border-radius: 6px;
    text-align: center;
    color: #cdd6f4;
    min-height: 22px;
}
QProgressBar::chunk {
    background-color: #89b4fa;
    border-radius: 6px;
}
QTextEdit {
    background-color: #181825;
    border: 1px solid #45475a;
    border-radius: 6px;
    color: #a6adc8;
    font-family: "Cascadia Code", "Consolas", monospace;
    font-size: 12px;
    padding: 6px;
}
QLabel#title {
    font-size: 20px;
    font-weight: bold;
    color: #89b4fa;
}
QLabel#subtitle {
    font-size: 12px;
    color: #6c7086;
}
QCheckBox {
    spacing: 6px;
    color: #cdd6f4;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 4px;
    border: 1px solid #45475a;
    background-color: #313244;
}
QCheckBox::indicator:checked {
    background-color: #89b4fa;
    border: 1px solid #89b4fa;
}
QScrollBar:vertical {
    background-color: #1e1e2e;
    width: 10px;
    border-radius: 5px;
}
QScrollBar::handle:vertical {
    background-color: #45475a;
    border-radius: 5px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background-color: #585b70;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QListWidget {
    background-color: #181825;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 4px;
}
QListWidget::item {
    padding: 6px 8px;
    border-radius: 4px;
}
QListWidget::item:selected {
    background-color: #45475a;
}
QListWidget::item:hover {
    background-color: #313244;
}
"""
