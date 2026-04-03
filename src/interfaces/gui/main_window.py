from __future__ import annotations

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QLabel, QMainWindow, QMenuBar, QStatusBar, QTabWidget, QVBoxLayout, QWidget

from src.interfaces.gui.i18n import on_language_changed, set_language, tr
from src.interfaces.gui.style import set_theme
from src.interfaces.gui.tabs.convert_tab import ConvertTab
from src.interfaces.gui.tabs.mapping_tab import MappingTab
from src.interfaces.gui.tabs.play_tab import PlayTab
from src.interfaces.gui.tabs.preview_tab import PreviewTab
from src.interfaces.gui.tabs.tracks_tab import TracksTab


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(820, 600)

        self._build_menu()

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(6, 6, 6, 6)

        self.tabs = QTabWidget()
        self.tracks_tab = TracksTab()
        self.convert_tab = ConvertTab()
        self.preview_tab = PreviewTab()
        self.play_tab = PlayTab()
        self.mapping_tab = MappingTab()

        self.tabs.addTab(self.tracks_tab, "")
        self.tabs.addTab(self.convert_tab, "")
        self.tabs.addTab(self.preview_tab, "")
        self.tabs.addTab(self.play_tab, "")
        self.tabs.addTab(self.mapping_tab, "")

        layout.addWidget(self.tabs)
        self.setCentralWidget(central)

        self.status_bar = QStatusBar()
        self.status_label = QLabel()
        self.status_bar.addWidget(self.status_label)
        self.setStatusBar(self.status_bar)

        self._connect_signals()
        on_language_changed(self.retranslate)
        self.retranslate()

    def _build_menu(self) -> None:
        menu_bar = QMenuBar()

        self.lang_menu = menu_bar.addMenu("")
        self.act_zh = QAction("中文", self)
        self.act_en = QAction("English", self)
        self.act_zh.triggered.connect(lambda: set_language("zh"))
        self.act_en.triggered.connect(lambda: set_language("en"))
        self.lang_menu.addAction(self.act_zh)
        self.lang_menu.addAction(self.act_en)

        self.theme_menu = menu_bar.addMenu("")
        self.act_dark = QAction("", self)
        self.act_light = QAction("", self)
        self.act_dark.triggered.connect(lambda: set_theme("dark"))
        self.act_light.triggered.connect(lambda: set_theme("light"))
        self.theme_menu.addAction(self.act_dark)
        self.theme_menu.addAction(self.act_light)

        self.setMenuBar(menu_bar)

    def retranslate(self) -> None:
        self.setWindowTitle(tr("window.title"))
        self.tabs.setTabText(0, tr("tab.tracks"))
        self.tabs.setTabText(1, tr("tab.convert"))
        self.tabs.setTabText(2, tr("tab.preview"))
        self.tabs.setTabText(3, tr("tab.play"))
        self.tabs.setTabText(4, tr("tab.mapping"))
        self.status_label.setText(tr("status.ready"))
        self.lang_menu.setTitle(tr("menu.language"))
        self.theme_menu.setTitle(tr("menu.theme"))
        self.act_dark.setText(tr("theme.dark"))
        self.act_light.setText(tr("theme.light"))

    def _connect_signals(self) -> None:
        self.tracks_tab.midi_loaded.connect(self._on_midi_loaded)
        self.tracks_tab.preview_requested.connect(self._on_preview_requested)
        self.tracks_tab.key_analyzed.connect(self._on_key_analyzed)
        self.convert_tab.chart_saved.connect(self._on_chart_saved)

    def _on_midi_loaded(self, path: str) -> None:
        self.convert_tab.set_midi_path(path)
        self.preview_tab.set_midi_path(path)
        self.status_label.setText(tr("status.midi_loaded").format(path=path))
        self.tabs.setCurrentWidget(self.convert_tab)

    def _on_preview_requested(self, path: str, track_index: int) -> None:
        self.preview_tab.set_midi_path(path)
        self.preview_tab.set_track_index(track_index)
        self.tabs.setCurrentWidget(self.preview_tab)

    def _on_key_analyzed(self, analysis: object) -> None:
        self.convert_tab.set_key_analysis(analysis)

    def _on_chart_saved(self, path: str) -> None:
        self.play_tab.set_chart_path(path)
        self.status_label.setText(tr("status.chart_saved").format(path=path))
        self.tabs.setCurrentWidget(self.play_tab)
