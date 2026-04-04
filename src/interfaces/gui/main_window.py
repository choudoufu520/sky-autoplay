from __future__ import annotations

import os
import webbrowser

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QMenuBar,
    QMessageBox,
    QProgressDialog,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src import __version__
from src.application.updater import UpdateInfo, apply_update, is_frozen
from src.interfaces.gui.i18n import on_language_changed, set_language, tr
from src.interfaces.gui.paths import base_path
from src.interfaces.gui.style import set_theme
from src.interfaces.gui.tabs.convert_tab import ConvertTab
from src.interfaces.gui.tabs.mapping_tab import MappingTab
from src.interfaces.gui.tabs.play_tab import PlayTab
from src.interfaces.gui.tabs.preview_tab import PreviewTab
from src.interfaces.gui.tabs.tracks_tab import TracksTab
from src.interfaces.gui.workers.update_worker import CheckUpdateWorker, DownloadUpdateWorker

GITHUB_RELEASES_URL = "https://github.com/choudoufu520/sky-autoplay/releases/latest"


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(820, 600)

        icon_path = os.path.join(base_path(), "assets", "icon.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self._check_worker: CheckUpdateWorker | None = None
        self._download_worker: DownloadUpdateWorker | None = None
        self._progress_dialog: QProgressDialog | None = None
        self._manual_check = False

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

        QTimer.singleShot(2000, self._auto_check_update)

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

        self.help_menu = menu_bar.addMenu("")
        self.act_check_update = QAction("", self)
        self.act_check_update.triggered.connect(self._manual_check_update)
        self.help_menu.addAction(self.act_check_update)
        self.act_about = QAction("", self)
        self.act_about.triggered.connect(self._show_about)
        self.help_menu.addAction(self.act_about)

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
        self.help_menu.setTitle(tr("menu.help"))
        self.act_check_update.setText(tr("update.check"))
        self.act_about.setText(tr("update.about"))

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

    # ── Update ──────────────────────────────────────────────

    def _auto_check_update(self) -> None:
        self._manual_check = False
        self._start_check()

    def _manual_check_update(self) -> None:
        self._manual_check = True
        self.status_label.setText(tr("update.checking"))
        self._start_check()

    def _start_check(self) -> None:
        self._check_worker = CheckUpdateWorker()
        self._check_worker.finished.connect(self._on_check_finished)
        self._check_worker.error.connect(self._on_check_error)
        self._check_worker.start()

    def _on_check_finished(self, info: object) -> None:
        if not isinstance(info, UpdateInfo):
            return
        if not info.has_update:
            if self._manual_check:
                QMessageBox.information(
                    self,
                    tr("update.check"),
                    tr("update.no_update").format(version=info.current_version),
                )
                self.status_label.setText(tr("status.ready"))
            return

        self._pending_update = info
        msg = QMessageBox(self)
        msg.setWindowTitle(tr("update.available"))
        msg.setIcon(QMessageBox.Icon.Information)

        text = tr("update.new_version").format(version=info.version, current=info.current_version)
        if info.body:
            text += f"\n\n{tr('update.release_notes')}\n{info.body[:500]}"
        msg.setText(text)

        if is_frozen() and info.download_url:
            btn_download = msg.addButton(tr("update.download"), QMessageBox.ButtonRole.AcceptRole)
        else:
            btn_download = None
        btn_open = msg.addButton(tr("update.open_release"), QMessageBox.ButtonRole.ActionRole)
        msg.addButton(tr("update.skip"), QMessageBox.ButtonRole.RejectRole)

        msg.exec()
        clicked = msg.clickedButton()

        if clicked == btn_download and info.download_url:
            self._start_download(info.download_url)
        elif clicked == btn_open:
            webbrowser.open(GITHUB_RELEASES_URL)

    def _on_check_error(self, err: str) -> None:
        if self._manual_check:
            QMessageBox.warning(
                self,
                tr("update.check"),
                tr("update.check_error").format(err=err),
            )
            self.status_label.setText(tr("status.ready"))

    def _start_download(self, url: str) -> None:
        self._progress_dialog = QProgressDialog(
            tr("update.downloading").format(percent=0),
            tr("update.skip"),
            0,
            100,
            self,
        )
        self._progress_dialog.setWindowTitle(tr("update.available"))
        self._progress_dialog.setMinimumDuration(0)
        self._progress_dialog.setValue(0)

        self._download_worker = DownloadUpdateWorker(url)
        self._download_worker.progress.connect(self._on_download_progress)
        self._download_worker.finished.connect(self._on_download_finished)
        self._download_worker.error.connect(self._on_download_error)
        self._progress_dialog.canceled.connect(self._on_download_cancel)
        self._download_worker.start()

    def _on_download_progress(self, downloaded: int, total: int) -> None:
        if self._progress_dialog is None:
            return
        if total > 0:
            pct = min(int(downloaded * 100 / total), 100)
        else:
            pct = 0
        self._progress_dialog.setValue(pct)
        self._progress_dialog.setLabelText(tr("update.downloading").format(percent=pct))

    def _on_download_finished(self, zip_path: str) -> None:
        if self._progress_dialog:
            self._progress_dialog.close()
            self._progress_dialog = None

        self.status_label.setText(tr("update.download_done"))

        from pathlib import Path

        try:
            apply_update(Path(zip_path))
        except RuntimeError:
            QMessageBox.information(self, tr("update.check"), tr("update.source_hint"))

    def _on_download_error(self, err: str) -> None:
        if self._progress_dialog:
            self._progress_dialog.close()
            self._progress_dialog = None
        QMessageBox.warning(
            self,
            tr("update.check"),
            tr("update.download_error").format(err=err),
        )

    def _on_download_cancel(self) -> None:
        if self._download_worker and self._download_worker.isRunning():
            self._download_worker.terminate()
        self._progress_dialog = None

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            tr("update.about"),
            tr("update.about_text").format(version=__version__),
        )
