from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.application.player import DEFAULT_TAP_PRESS_MS, PlayOptions
from src.infrastructure.input_backends import DryRunInputBackend, PynputInputBackend
from src.infrastructure.repository import load_chart
from src.interfaces.gui.i18n import on_language_changed, tr
from src.interfaces.gui.play_overlay import PlayOverlay
from src.interfaces.gui.workers.play_worker import PlayWorker

_log = logging.getLogger(__name__)


class PlayTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._worker: PlayWorker | None = None
        self._overlay: PlayOverlay | None = None
        self._hotkey_listener = None
        layout = QVBoxLayout(self)

        chart_row = QHBoxLayout()
        self.chart_label = QLabel()
        chart_row.addWidget(self.chart_label)
        self.chart_edit = QLineEdit()
        chart_row.addWidget(self.chart_edit, 1)
        self.browse_btn = QPushButton()
        self.browse_btn.clicked.connect(self._browse)
        chart_row.addWidget(self.browse_btn)
        layout.addLayout(chart_row)

        self.form = QFormLayout()
        self.latency_label = QLabel()
        self.latency_spin = QSpinBox()
        self.latency_spin.setRange(-2000, 2000)
        self.latency_spin.setSuffix(" ms")
        self.form.addRow(self.latency_label, self.latency_spin)

        self.countdown_label = QLabel()
        self.countdown_spin = QSpinBox()
        self.countdown_spin.setRange(0, 30)
        self.countdown_spin.setValue(3)
        self.countdown_spin.setSuffix(" sec")
        self.form.addRow(self.countdown_label, self.countdown_spin)

        self.stagger_label = QLabel()
        self.stagger_spin = QSpinBox()
        self.stagger_spin.setRange(0, 50)
        self.stagger_spin.setSuffix(" ms")
        self.form.addRow(self.stagger_label, self.stagger_spin)

        self.tap_press_label = QLabel()
        self.tap_press_spin = QSpinBox()
        self.tap_press_spin.setRange(10, 200)
        self.tap_press_spin.setValue(DEFAULT_TAP_PRESS_MS)
        self.tap_press_spin.setSuffix(" ms")
        self.form.addRow(self.tap_press_label, self.tap_press_spin)

        self.dry_run_check = QCheckBox()
        self.form.addRow(self.dry_run_check)

        self.debug_check = QCheckBox()
        self.form.addRow(self.debug_check)
        layout.addLayout(self.form)

        btn_row = QHBoxLayout()
        self.play_btn = QPushButton()
        self.play_btn.setObjectName("primaryBtn")
        self.play_btn.clicked.connect(self._start_play)
        btn_row.addWidget(self.play_btn)
        self.stop_btn = QPushButton()
        self.stop_btn.setObjectName("dangerBtn")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_play)
        btn_row.addWidget(self.stop_btn)
        layout.addLayout(btn_row)

        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text, 1)

        on_language_changed(self.retranslate)
        self.retranslate()

    def retranslate(self) -> None:
        self.chart_label.setText(tr("play.chart"))
        self.chart_edit.setPlaceholderText(tr("play.chart_placeholder"))
        self.browse_btn.setText(tr("browse"))

        self.latency_label.setText(tr("play.latency"))
        self.countdown_label.setText(tr("play.countdown"))
        self.stagger_label.setText(tr("play.stagger"))
        self.tap_press_label.setText(tr("play.tap_press"))

        self.dry_run_check.setText(tr("play.dry_run"))
        self.debug_check.setText(tr("play.debug"))
        self.play_btn.setText(tr("play.start"))
        self.stop_btn.setText(tr("play.stop"))

        self.latency_spin.setToolTip(tr("play.tip_latency"))
        self.countdown_spin.setToolTip(tr("play.tip_countdown"))
        self.stagger_spin.setToolTip(tr("play.tip_stagger"))
        self.tap_press_spin.setToolTip(tr("play.tip_tap_press"))
        self.dry_run_check.setToolTip(tr("play.tip_dry_run"))

    def set_chart_path(self, path: str) -> None:
        self.chart_edit.setText(path)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("play.dialog_chart"), "", "JSON Files (*.json)")
        if path:
            self.chart_edit.setText(path)

    def _start_play(self) -> None:
        chart_path = self.chart_edit.text().strip()
        if not chart_path:
            self.log_text.appendPlainText(tr("play.err_no_chart"))
            return

        try:
            chart = load_chart(Path(chart_path))
        except Exception as exc:
            self.log_text.appendPlainText(tr("play.err_load").format(err=exc))
            return

        options = PlayOptions(
            latency_offset_ms=self.latency_spin.value(),
            countdown_sec=self.countdown_spin.value(),
            chord_stagger_ms=self.stagger_spin.value(),
            tap_press_ms=self.tap_press_spin.value(),
            dry_run=self.dry_run_check.isChecked(),
            debug=self.debug_check.isChecked(),
        )

        if options.dry_run:
            backend = DryRunInputBackend()
        else:
            try:
                backend = PynputInputBackend()
            except RuntimeError as exc:
                self.log_text.appendPlainText(f"{tr('error')}: {exc}")
                return

        self.log_text.clear()
        self.log_text.appendPlainText(tr("play.starting"))
        self.play_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

        self._show_overlay()
        self._start_hotkey_listener()

        self._worker = PlayWorker(chart, backend, options)
        self._worker.log_message.connect(self._on_log)
        self._worker.finished_signal.connect(self._on_finished)
        self._worker.progress_signal.connect(self._on_progress)
        self._worker.countdown_signal.connect(self._on_countdown)
        self._worker.key_display_signal.connect(self._on_key_display)
        self._worker.start()

    def _stop_play(self) -> None:
        if self._worker:
            self._worker.request_stop()

    def _on_log(self, msg: str) -> None:
        self.log_text.appendPlainText(msg)

    def _on_progress(self, current: int, total: int, elapsed_ms: int, total_ms: int) -> None:
        if self._overlay:
            self._overlay.update_progress(current, total, elapsed_ms, total_ms)

    def _on_countdown(self, remain: int) -> None:
        if self._overlay:
            self._overlay.set_countdown(remain)

    def _on_key_display(self, data: object) -> None:
        if self._overlay and isinstance(data, tuple) and len(data) == 2:
            current_keys, upcoming = data
            self._overlay.update_keys(current_keys, upcoming)

    def _on_finished(self, success: bool, msg: str) -> None:
        self.log_text.appendPlainText(f"--- {msg} ---")
        self.play_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._hide_overlay()
        self._stop_hotkey_listener()
        self._worker = None

    # ── overlay ─────────────────────────────────────────────

    def _show_overlay(self) -> None:
        if self._overlay is None:
            self._overlay = PlayOverlay()
        self._overlay.set_countdown(0)
        self._overlay.show()

    def _hide_overlay(self) -> None:
        if self._overlay is not None:
            self._overlay.hide()

    # ── global hotkey ───────────────────────────────────────

    def _start_hotkey_listener(self) -> None:
        try:
            from pynput import keyboard
        except ImportError:
            _log.warning("pynput not available, global stop hotkey disabled")
            return

        def on_press(key: keyboard.Key | keyboard.KeyCode | None) -> None:
            try:
                if key == keyboard.Key.f9 and self._worker:
                    self._worker.request_stop()
            except Exception:
                pass

        self._hotkey_listener = keyboard.Listener(on_press=on_press)
        self._hotkey_listener.daemon = True
        self._hotkey_listener.start()

    def _stop_hotkey_listener(self) -> None:
        if self._hotkey_listener is not None:
            self._hotkey_listener.stop()
            self._hotkey_listener = None
