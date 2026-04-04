"""Application theming — dark / light modern stylesheets."""
from __future__ import annotations

from typing import Callable

_listeners: list[Callable[[], None]] = []
_current_theme = "dark"

# ── colour palettes ──

_DARK = {
    "bg":           "#1b1b2f",
    "surface":      "#232340",
    "surface2":     "#2c2c4a",
    "input_bg":     "#2c2c4a",
    "border":       "#3d3d5c",
    "border_focus": "#7c6bf5",
    "text":         "#dcdce5",
    "text2":        "#9999b3",
    "accent":       "#7c6bf5",
    "accent_hover": "#9180ff",
    "accent_text":  "#ffffff",
    "danger":       "#e05555",
    "danger_hover": "#f06666",
    "success":      "#4ec9b0",
    "warn":         "#e0a030",
    "header_bg":    "#282846",
    "row_alt":      "#262642",
    "selection":    "#3d3d70",
    "scrollbar":    "#3d3d5c",
    "scrollbar_h":  "#555580",
    "tab_bg":       "#1b1b2f",
    "tab_sel":      "#232340",
    "tab_hover":    "#2c2c4a",
    "menu_bg":      "#232340",
    "menu_hover":   "#3d3d5c",
    "group_border": "#3d3d5c",
    "placeholder":  "#666680",
    "disabled":     "#555570",
}

_LIGHT = {
    "bg":           "#f5f5f8",
    "surface":      "#ffffff",
    "surface2":     "#eeeef2",
    "input_bg":     "#ffffff",
    "border":       "#d0d0da",
    "border_focus": "#6c5ce7",
    "text":         "#2d2d3a",
    "text2":        "#7a7a8c",
    "accent":       "#6c5ce7",
    "accent_hover": "#7f70f0",
    "accent_text":  "#ffffff",
    "danger":       "#e05555",
    "danger_hover": "#f06666",
    "success":      "#2dbb8a",
    "warn":         "#d49520",
    "header_bg":    "#eaeaf0",
    "row_alt":      "#f9f9fc",
    "selection":    "#ddddf5",
    "scrollbar":    "#ccccdd",
    "scrollbar_h":  "#aaaacc",
    "tab_bg":       "#eaeaf0",
    "tab_sel":      "#ffffff",
    "tab_hover":    "#dddde8",
    "menu_bg":      "#ffffff",
    "menu_hover":   "#eaeaf0",
    "group_border": "#d0d0da",
    "placeholder":  "#999aaa",
    "disabled":     "#aaaabc",
}


def _build_qss(c: dict[str, str]) -> str:
    return f"""
/* ── global ── */
QWidget {{
    font-family: "Segoe UI", "Microsoft YaHei UI", "Noto Sans SC", sans-serif;
    font-size: 13px;
    color: {c["text"]};
    background: transparent;
}}
QMainWindow {{
    background: {c["bg"]};
}}

/* ── menu bar ── */
QMenuBar {{
    background: {c["surface"]};
    border-bottom: 1px solid {c["border"]};
    padding: 2px 6px;
}}
QMenuBar::item {{
    padding: 4px 10px;
    border-radius: 4px;
}}
QMenuBar::item:selected {{
    background: {c["menu_hover"]};
}}
QMenu {{
    background: {c["menu_bg"]};
    border: 1px solid {c["border"]};
    border-radius: 6px;
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 24px 6px 12px;
    border-radius: 4px;
}}
QMenu::item:selected {{
    background: {c["accent"]};
    color: {c["accent_text"]};
}}

/* ── tab bar ── */
QTabWidget::pane {{
    border: 1px solid {c["border"]};
    border-radius: 8px;
    background: {c["surface"]};
    margin-top: -1px;
}}
QTabBar {{
    background: transparent;
}}
QTabBar::tab {{
    background: {c["tab_bg"]};
    color: {c["text2"]};
    border: 1px solid {c["border"]};
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 7px 18px;
    margin-right: 2px;
    min-width: 70px;
}}
QTabBar::tab:selected {{
    background: {c["tab_sel"]};
    color: {c["accent"]};
    font-weight: bold;
    border-bottom: 2px solid {c["accent"]};
}}
QTabBar::tab:hover:!selected {{
    background: {c["tab_hover"]};
    color: {c["text"]};
}}

/* ── buttons ── */
QPushButton {{
    background: {c["surface2"]};
    color: {c["text"]};
    border: 1px solid {c["border"]};
    border-radius: 6px;
    padding: 5px 14px;
    min-height: 20px;
}}
QPushButton:hover {{
    background: {c["border"]};
    border-color: {c["accent"]};
}}
QPushButton:pressed {{
    background: {c["accent"]};
    color: {c["accent_text"]};
}}
QPushButton:disabled {{
    color: {c["disabled"]};
    border-color: {c["border"]};
}}
QPushButton#primaryBtn {{
    background: {c["accent"]};
    color: {c["accent_text"]};
    border: none;
    font-weight: bold;
}}
QPushButton#primaryBtn:hover {{
    background: {c["accent_hover"]};
}}
QPushButton#dangerBtn {{
    color: {c["danger"]};
    border-color: {c["danger"]};
}}
QPushButton#dangerBtn:hover {{
    background: {c["danger"]};
    color: {c["accent_text"]};
}}

/* ── line edit / spin / combo ── */
QLineEdit, QSpinBox, QComboBox {{
    background: {c["input_bg"]};
    color: {c["text"]};
    border: 1px solid {c["border"]};
    border-radius: 6px;
    padding: 5px 8px;
    min-height: 20px;
    selection-background-color: {c["accent"]};
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border-color: {c["accent"]};
}}
QLineEdit::placeholder {{
    color: {c["placeholder"]};
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {c["text2"]};
    margin-right: 6px;
}}
QComboBox QAbstractItemView {{
    background: {c["menu_bg"]};
    border: 1px solid {c["border"]};
    border-radius: 6px;
    selection-background-color: {c["accent"]};
    selection-color: {c["accent_text"]};
    outline: none;
}}
QSpinBox::up-button, QSpinBox::down-button {{
    background: {c["surface2"]};
    border: none;
    width: 18px;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
    background: {c["border"]};
}}

/* ── checkbox ── */
QCheckBox {{
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    border: 2px solid {c["border"]};
    border-radius: 4px;
    background: {c["input_bg"]};
}}
QCheckBox::indicator:checked {{
    background: {c["accent"]};
    border-color: {c["accent"]};
}}
QCheckBox::indicator:hover {{
    border-color: {c["accent"]};
}}

/* ── tables ── */
QTableWidget, QTableView {{
    background: {c["surface"]};
    alternate-background-color: {c["row_alt"]};
    border: 1px solid {c["border"]};
    border-radius: 6px;
    gridline-color: {c["border"]};
    selection-background-color: {c["selection"]};
    selection-color: {c["text"]};
    outline: none;
}}
QTableWidget::item {{
    padding: 4px 6px;
}}
QHeaderView::section {{
    background: {c["header_bg"]};
    color: {c["text2"]};
    font-weight: bold;
    font-size: 12px;
    padding: 6px 8px;
    border: none;
    border-bottom: 2px solid {c["border"]};
    border-right: 1px solid {c["border"]};
}}

/* ── list widget ── */
QListWidget {{
    background: {c["surface"]};
    border: 1px solid {c["border"]};
    border-radius: 6px;
    outline: none;
}}
QListWidget::item {{
    padding: 6px 10px;
    border-radius: 4px;
    margin: 1px 3px;
}}
QListWidget::item:selected {{
    background: {c["accent"]};
    color: {c["accent_text"]};
}}
QListWidget::item:hover:!selected {{
    background: {c["surface2"]};
}}

/* ── group box ── */
QGroupBox {{
    font-weight: bold;
    font-size: 12px;
    color: {c["text2"]};
    border: 1px solid {c["group_border"]};
    border-radius: 8px;
    margin-top: 12px;
    padding-top: 16px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
}}
QGroupBox::indicator {{
    width: 20px;
    height: 20px;
    border: 2px solid {c["border"]};
    border-radius: 5px;
    background: {c["input_bg"]};
}}
QGroupBox::indicator:checked {{
    background: {c["accent"]};
    border-color: {c["accent"]};
}}
QGroupBox::indicator:hover {{
    border-color: {c["accent"]};
}}
QGroupBox#aiGroupBox {{
    border: 1.5px solid {c["group_border"]};
    padding-top: 20px;
}}
QGroupBox#aiGroupBox:checked {{
    border-color: {c["accent"]};
}}
QGroupBox#aiGroupBox::title {{
    font-size: 14px;
    color: {c["text"]};
}}

/* ── plain text edit ── */
QPlainTextEdit {{
    background: {c["surface"]};
    color: {c["text"]};
    border: 1px solid {c["border"]};
    border-radius: 6px;
    padding: 6px;
    font-family: "Cascadia Code", "JetBrains Mono", "Consolas", monospace;
    font-size: 12px;
    selection-background-color: {c["accent"]};
}}

/* ── scrollbar ── */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {c["scrollbar"]};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {c["scrollbar_h"]};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {c["scrollbar"]};
    border-radius: 5px;
    min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {c["scrollbar_h"]};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}

/* ── splitter ── */
QSplitter::handle {{
    background: {c["border"]};
    margin: 1px;
}}
QSplitter::handle:horizontal {{
    width: 3px;
}}
QSplitter::handle:vertical {{
    height: 3px;
}}

/* ── status bar ── */
QStatusBar {{
    background: {c["surface"]};
    border-top: 1px solid {c["border"]};
    color: {c["text2"]};
    font-size: 12px;
    padding: 2px 8px;
}}

/* ── labels ── */
QLabel {{
    background: transparent;
}}
QLabel#infoLabel {{
    color: {c["text2"]};
    font-size: 12px;
    padding: 2px 0;
}}
QLabel#keyLabel {{
    color: {c["accent"]};
    font-weight: bold;
    font-size: 13px;
    padding: 4px 8px;
    background: {c["surface2"]};
    border-radius: 6px;
}}
QLabel#noteDistLabel {{
    color: {c["text2"]};
    font-size: 12px;
    padding: 2px 0;
}}
QLabel#statusHint {{
    color: {c["text2"]};
    font-size: 12px;
}}
QLabel#keyInfoLabel {{
    color: {c["text2"]};
    padding: 2px;
}}
QLabel#keyInfoLabelActive {{
    color: {c["success"]};
    font-weight: bold;
    padding: 2px;
}}

/* ── form layout labels ── */
QFormLayout QLabel {{
    font-size: 13px;
}}

/* ── tooltips ── */
QToolTip {{
    background: {c["surface2"]};
    color: {c["text"]};
    border: 1px solid {c["border"]};
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 12px;
}}
"""


def _palettes() -> dict[str, dict[str, str]]:
    return {"dark": _DARK, "light": _LIGHT}


def current_theme() -> str:
    return _current_theme


def set_theme(name: str) -> None:
    global _current_theme
    if name in _palettes():
        _current_theme = name
        for fn in _listeners:
            fn()


def on_theme_changed(fn: Callable[[], None]) -> None:
    _listeners.append(fn)


def get_qss(theme: str | None = None) -> str:
    t = theme or _current_theme
    palette = _palettes().get(t, _DARK)
    return _build_qss(palette)
