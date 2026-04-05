"""Simulate tab — visual 3x5 keyboard playback with audio feedback."""

from __future__ import annotations

import logging
from pathlib import Path

import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.application.audio_engine import (
    BaseAudioBackend,
    NullAudioBackend,
    create_audio_backend,
)
from src.domain.chart import ChartDocument
from src.infrastructure.repository import load_chart, load_mapping
from src.interfaces.gui.i18n import on_language_changed, tr
from src.interfaces.gui.widgets.note_timeline import NoteTimelineWidget
from src.interfaces.gui.widgets.sky_keyboard import SkyKeyboardWidget
from src.interfaces.gui.widgets.timeline_dialog import TimelineDialog
from src.interfaces.gui.workers.sim_worker import SimulationEngine

_log = logging.getLogger(__name__)

_SPEEDS: list[tuple[str, float]] = [
    ("0.25x", 0.25),
    ("0.5x", 0.5),
    ("0.75x", 0.75),
    ("1.0x", 1.0),
    ("1.25x", 1.25),
    ("1.5x", 1.5),
    ("2.0x", 2.0),
]


class SimulateTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._audio: BaseAudioBackend = NullAudioBackend()
        self._mapping_loaded = False
        self._midi_notes: list[int] = []
        self._engine = SimulationEngine(self)
        self._cached_chart: ChartDocument | None = None

        layout = QVBoxLayout(self)

        # ── mapping row ─────────────────────────────────────
        map_row = QHBoxLayout()
        self.map_label = QLabel()
        map_row.addWidget(self.map_label)
        self.map_edit = QLineEdit()
        map_row.addWidget(self.map_edit, 1)
        self.map_browse = QPushButton()
        self.map_browse.clicked.connect(self._browse_mapping)
        map_row.addWidget(self.map_browse)
        self.map_load_btn = QPushButton()
        self.map_load_btn.clicked.connect(self._load_mapping)
        map_row.addWidget(self.map_load_btn)
        layout.addLayout(map_row)

        # ── chart row ───────────────────────────────────────
        chart_row = QHBoxLayout()
        self.chart_label = QLabel()
        chart_row.addWidget(self.chart_label)
        self.chart_edit = QLineEdit()
        chart_row.addWidget(self.chart_edit, 1)
        self.chart_browse = QPushButton()
        self.chart_browse.clicked.connect(self._browse_chart)
        chart_row.addWidget(self.chart_browse)
        layout.addLayout(chart_row)

        start_row = QHBoxLayout()
        self.start_from_label = QLabel()
        start_row.addWidget(self.start_from_label)
        self.position_edit = QLineEdit("00:00")
        self.position_edit.setFixedWidth(60)
        self.position_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.position_edit.editingFinished.connect(self._on_position_edited)
        start_row.addWidget(self.position_edit)
        self.reset_btn = QPushButton()
        self.reset_btn.clicked.connect(self._reset_position)
        start_row.addWidget(self.reset_btn)
        self.expand_btn = QToolButton()
        self.expand_btn.clicked.connect(self._open_timeline_dialog)
        start_row.addWidget(self.expand_btn)
        start_row.addStretch()
        layout.addLayout(start_row)
        self.timeline = NoteTimelineWidget()
        self.timeline.position_changed.connect(self._sync_position_edit)
        layout.addWidget(self.timeline)

        # ── controls ────────────────────────────────────────
        ctrl = QHBoxLayout()

        self.mode_label = QLabel()
        ctrl.addWidget(self.mode_label)
        self.mode_combo = QComboBox()
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        ctrl.addWidget(self.mode_combo)

        ctrl.addSpacing(16)

        self.speed_label = QLabel()
        ctrl.addWidget(self.speed_label)
        self.speed_combo = QComboBox()
        for label, _ in _SPEEDS:
            self.speed_combo.addItem(label)
        self.speed_combo.setCurrentIndex(3)  # 1.0x
        ctrl.addWidget(self.speed_combo)

        ctrl.addSpacing(16)

        self.vol_label = QLabel()
        ctrl.addWidget(self.vol_label)
        self.vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(70)
        self.vol_slider.setFixedWidth(100)
        self.vol_slider.valueChanged.connect(self._on_volume_changed)
        ctrl.addWidget(self.vol_slider)

        ctrl.addSpacing(16)

        self.transpose_label = QLabel()
        ctrl.addWidget(self.transpose_label)
        self.transpose_spin = QSpinBox()
        self.transpose_spin.setRange(-12, 12)
        self.transpose_spin.setValue(0)
        self.transpose_spin.setSuffix(tr("sim.transpose_suffix"))
        self.transpose_spin.valueChanged.connect(self._on_transpose_changed)
        ctrl.addWidget(self.transpose_spin)

        ctrl.addStretch()

        self.play_btn = QPushButton()
        self.play_btn.setObjectName("primaryBtn")
        self.play_btn.clicked.connect(self._on_play)
        ctrl.addWidget(self.play_btn)

        self.stop_btn = QPushButton()
        self.stop_btn.setObjectName("dangerBtn")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop)
        ctrl.addWidget(self.stop_btn)

        layout.addLayout(ctrl)

        # ── keyboard ────────────────────────────────────────
        self.keyboard = SkyKeyboardWidget()
        layout.addWidget(self.keyboard, 1)

        # ── status ──────────────────────────────────────────
        self.status_label = QLabel()
        self.status_label.setObjectName("statusHint")
        layout.addWidget(self.status_label)

        # ── log ─────────────────────────────────────────────
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(100)
        layout.addWidget(self.log_text)

        # ── connections ─────────────────────────────────────
        self.keyboard.key_pressed.connect(self._on_manual_press)
        self.keyboard.key_released.connect(self._on_manual_release)

        self._engine.key_pressed.connect(self._on_sim_press)
        self._engine.key_released.connect(self._on_sim_release)
        self._engine.progress.connect(self._on_progress)
        self._engine.finished.connect(self._on_finished)

        on_language_changed(self.retranslate)
        self.retranslate()

    # ── public helpers ──────────────────────────────────────

    def set_chart_path(self, path: str) -> None:
        self.chart_edit.setText(path)
        self._load_timeline(path)

    def set_mapping_path(self, path: str, auto_load: bool = False) -> None:
        self.map_edit.setText(path)
        if auto_load and path.strip():
            self._load_mapping()

    # ── i18n ────────────────────────────────────────────────

    def retranslate(self) -> None:
        self.map_label.setText(tr("sim.mapping"))
        self.map_edit.setPlaceholderText(tr("sim.mapping_placeholder"))
        self.map_browse.setText(tr("browse"))
        self.map_load_btn.setText(tr("sim.load"))

        self.chart_label.setText(tr("sim.chart"))
        self.chart_edit.setPlaceholderText(tr("sim.chart_placeholder"))
        self.chart_browse.setText(tr("browse"))

        self.start_from_label.setText(tr("play.start_from"))
        self.reset_btn.setText(tr("play.reset_position"))
        self.expand_btn.setText(tr("play.open_timeline"))
        self.timeline.setToolTip(tr("play.tip_start_from"))

        self.mode_label.setText(tr("sim.mode"))
        self.speed_label.setText(tr("sim.speed"))
        self.vol_label.setText(tr("sim.volume"))
        self.transpose_label.setText(tr("sim.transpose"))
        self.transpose_spin.setSuffix(tr("sim.transpose_suffix"))
        self.transpose_spin.setToolTip(tr("sim.tip_transpose"))
        self.play_btn.setText(tr("sim.play"))
        self.stop_btn.setText(tr("sim.stop"))

        idx = max(self.mode_combo.currentIndex(), 0)
        self.mode_combo.blockSignals(True)
        self.mode_combo.clear()
        self.mode_combo.addItem(tr("sim.mode_auto"))
        self.mode_combo.addItem(tr("sim.mode_manual"))
        self.mode_combo.setCurrentIndex(idx)
        self.mode_combo.blockSignals(False)

        self._refresh_status()

    # ── private: browsing & loading ─────────────────────────

    def _browse_mapping(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("sim.dialog_mapping"), "", "YAML (*.yaml *.yml)"
        )
        if path:
            self.map_edit.setText(path)

    def _browse_chart(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("sim.dialog_chart"), "", "JSON (*.json)"
        )
        if path:
            self.chart_edit.setText(path)
            self._load_timeline(path)

    def _load_timeline(self, path: str) -> None:
        self._cached_chart = None
        if not path.strip():
            self.timeline.clear()
            return
        try:
            chart = load_chart(Path(path))
            self._cached_chart = chart
            total_ms = max(e.time_ms for e in chart.events) if chart.events else 0
            self.timeline.set_events(chart.events, total_ms)
        except Exception:
            self.timeline.clear()

    def _load_mapping(self) -> None:
        path = self.map_edit.text().strip()
        if not path:
            self.log_text.appendPlainText(tr("sim.err_no_mapping"))
            return

        try:
            mapping = load_mapping(Path(path))
        except Exception as exc:
            self.log_text.appendPlainText(tr("sim.err_load_mapping").format(err=exc))
            return

        profile = mapping.profiles.get(mapping.default_profile)
        if profile is None:
            self.log_text.appendPlainText(tr("sim.err_load_mapping").format(err="profile not found"))
            return

        self.keyboard.set_mapping(profile.note_to_key)

        self._midi_notes = []
        for note_str in profile.note_to_key:
            try:
                self._midi_notes.append(int(note_str))
            except ValueError:
                continue

        self._rebuild_audio()
        self._mapping_loaded = True
        self.log_text.appendPlainText(
            tr("sim.mapping_loaded").format(path=path, count=len(self._midi_notes))
        )
        self._refresh_status()

    # ── mode / volume / transpose ──────────────────────────

    def _on_mode_changed(self, index: int) -> None:
        is_auto = index == 0
        self.chart_edit.setEnabled(is_auto)
        self.chart_browse.setEnabled(is_auto)
        self.speed_combo.setEnabled(is_auto)
        if not is_auto:
            self.keyboard.setFocus()
        self._refresh_status()

    def _on_volume_changed(self, value: int) -> None:
        self._audio.set_volume(value / 100.0)

    def _on_transpose_changed(self, _value: int) -> None:
        if self._midi_notes:
            self._rebuild_audio()

    def _rebuild_audio(self) -> None:
        self._audio.cleanup()
        self._audio, backend_name = create_audio_backend(
            self._midi_notes,
            self.vol_slider.value() / 100.0,
            transpose=self.transpose_spin.value(),
        )
        self.log_text.appendPlainText(
            tr("sim.audio_backend").format(name=backend_name)
        )

    # ── play / stop ─────────────────────────────────────────

    def _on_play(self) -> None:
        if self._engine.is_running:
            return

        if not self._mapping_loaded:
            self.log_text.appendPlainText(tr("sim.err_no_mapping"))
            return

        if self.mode_combo.currentIndex() == 0:
            self._start_auto()
        else:
            self.keyboard.setFocus()
            self._refresh_status()

    def _start_auto(self) -> None:
        chart_path = self.chart_edit.text().strip()
        if not chart_path:
            self.log_text.appendPlainText(tr("sim.err_no_chart"))
            return

        chart = self._cached_chart
        if chart is None:
            try:
                chart = load_chart(Path(chart_path))
            except Exception as exc:
                self.log_text.appendPlainText(tr("sim.err_load_chart").format(err=exc))
                return

        speed = _SPEEDS[self.speed_combo.currentIndex()][1]

        self.play_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

        if chart.events and not self.timeline._bins:
            total_ms = max(e.time_ms for e in chart.events)
            self.timeline.set_events(chart.events, total_ms)

        self.log_text.appendPlainText(tr("sim.playback_started"))
        self._engine.start(chart, speed, start_ms=self.timeline.position_ms)

    def _on_stop(self) -> None:
        if self._engine.is_running:
            self._engine.stop()
        self.keyboard.release_all()
        self.play_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._refresh_status()

    # ── simulation callbacks ────────────────────────────────

    def _on_sim_press(self, key: str) -> None:
        self.keyboard.press_key(key)
        note = self.keyboard.get_midi_note(key)
        if note is not None:
            self._audio.play_note(note)

    def _on_sim_release(self, key: str) -> None:
        self.keyboard.release_key(key)

    # ── manual-play callbacks ───────────────────────────────

    def _on_manual_press(self, key: str) -> None:
        note = self.keyboard.get_midi_note(key)
        if note is not None:
            self._audio.play_note(note)

    def _on_manual_release(self, key: str) -> None:
        note = self.keyboard.get_midi_note(key)
        if note is not None:
            self._audio.stop_note(note)

    # ── progress / finish ───────────────────────────────────

    def _on_progress(self, current: int, total: int, elapsed_ms: int, total_ms: int) -> None:
        self.status_label.setText(
            tr("sim.status_playing").format(
                current=current,
                total=total,
                elapsed=_fmt_time(elapsed_ms),
                duration=_fmt_time(total_ms),
            )
        )

    def _on_finished(self) -> None:
        self.keyboard.release_all()
        self.play_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.log_text.appendPlainText(tr("sim.playback_done"))
        self._refresh_status()

    # ── helpers ─────────────────────────────────────────────

    def _refresh_status(self) -> None:
        if self._engine.is_running:
            return
        if self.mode_combo.currentIndex() == 1 and self._mapping_loaded:
            self.status_label.setText(tr("sim.status_manual"))
        else:
            self.status_label.setText(tr("sim.status_ready"))

    # ── start position helpers ─────────────────────────────

    def _sync_position_edit(self, ms: int) -> None:
        s = max(0, ms // 1000)
        m, s = divmod(s, 60)
        self.position_edit.setText(f"{m:02d}:{s:02d}")

    def _on_position_edited(self) -> None:
        text = self.position_edit.text().strip()
        m = re.match(r"^(\d+):(\d{1,2})$", text)
        if m:
            ms = (int(m.group(1)) * 60 + int(m.group(2))) * 1000
            self.timeline.set_position(ms)

    def _reset_position(self) -> None:
        self.timeline.reset()

    def _open_timeline_dialog(self) -> None:
        if not self.timeline.total_ms:
            return
        dlg = TimelineDialog(
            self.timeline.events,
            self.timeline.total_ms,
            self.timeline.position_ms,
            parent=self,
        )
        if dlg.exec():
            self.timeline.set_position(dlg.position_ms)


def _fmt_time(ms: int) -> str:
    s = max(0, ms // 1000)
    m, s = divmod(s, 60)
    return f"{m:02d}:{s:02d}"
