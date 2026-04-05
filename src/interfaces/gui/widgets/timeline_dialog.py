"""Standalone dialog with an enlarged NoteTimelineWidget for precise position selection."""

from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.domain.chart import ChartEvent
from src.interfaces.gui.i18n import tr
from src.interfaces.gui.widgets.note_timeline import NoteTimelineWidget


class TimelineDialog(QDialog):
    """Modal dialog that shows a large timeline and returns the chosen position."""

    def __init__(
        self,
        events: list[ChartEvent],
        total_ms: int,
        initial_ms: int = 0,
        track_events: dict[int, list[ChartEvent]] | None = None,
        track_names: dict[int, str] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("play.timeline_dialog_title"))
        self.setMinimumSize(800, 220)
        self.resize(950, 260)

        self._all_events = events
        self._total_ms = total_ms
        self._track_events = track_events
        self._track_checks: list[tuple[int, QCheckBox]] = []

        outer = QHBoxLayout(self)

        if track_events and len(track_events) > 1:
            filter_panel = QWidget()
            filter_layout = QVBoxLayout(filter_panel)
            filter_layout.setContentsMargins(0, 0, 4, 0)
            filter_layout.addWidget(QLabel(tr("timeline.track_filter")))

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFixedWidth(180)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            inner = QWidget()
            inner_layout = QVBoxLayout(inner)
            inner_layout.setContentsMargins(2, 2, 2, 2)

            names = track_names or {}
            for idx in sorted(track_events.keys()):
                label = names.get(idx, f"Track {idx}")
                count = sum(1 for e in track_events[idx] if e.action in ("tap", "down"))
                cb = QCheckBox(f"[{idx}] {label} ({count})")
                cb.setChecked(True)
                cb.stateChanged.connect(self._on_filter_changed)
                inner_layout.addWidget(cb)
                self._track_checks.append((idx, cb))

            inner_layout.addStretch()
            scroll.setWidget(inner)
            filter_layout.addWidget(scroll, 1)
            outer.addWidget(filter_panel)

        right = QVBoxLayout()

        self._timeline = NoteTimelineWidget(bar_height=80)
        self._timeline.setFixedHeight(100)
        right.addWidget(self._timeline)

        row = QHBoxLayout()
        row.addWidget(QLabel(tr("play.manual_position")))
        self._time_edit = QLineEdit()
        self._time_edit.setFixedWidth(80)
        self._time_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self._time_edit)

        self._reset_btn = QPushButton(tr("play.reset_position"))
        self._reset_btn.clicked.connect(self._on_reset)
        row.addWidget(self._reset_btn)

        row.addStretch()

        ok_btn = QPushButton(tr("play.confirm"))
        ok_btn.setObjectName("primaryBtn")
        ok_btn.clicked.connect(self.accept)
        row.addWidget(ok_btn)

        cancel_btn = QPushButton(tr("play.cancel"))
        cancel_btn.clicked.connect(self.reject)
        row.addWidget(cancel_btn)

        right.addLayout(row)
        outer.addLayout(right, 1)

        self._timeline.set_events(events, total_ms)
        if initial_ms > 0:
            self._timeline.set_position(initial_ms)
        self._sync_edit(initial_ms)

        self._timeline.position_changed.connect(self._sync_edit)
        self._time_edit.editingFinished.connect(self._on_edit_finished)

    @property
    def position_ms(self) -> int:
        return self._timeline.position_ms

    def _on_filter_changed(self) -> None:
        if not self._track_events:
            return
        filtered: list[ChartEvent] = []
        for idx, cb in self._track_checks:
            if cb.isChecked():
                filtered.extend(self._track_events[idx])
        pos = self._timeline.position_ms
        self._timeline.set_events(filtered, self._total_ms)
        self._timeline.set_position(pos)

    def _sync_edit(self, ms: int) -> None:
        s = max(0, ms // 1000)
        m, s = divmod(s, 60)
        self._time_edit.setText(f"{m:02d}:{s:02d}")

    def _on_edit_finished(self) -> None:
        text = self._time_edit.text().strip()
        m = re.match(r"^(\d+):(\d{1,2})$", text)
        if m:
            ms = (int(m.group(1)) * 60 + int(m.group(2))) * 1000
            self._timeline.set_position(ms)

    def _on_reset(self) -> None:
        self._timeline.reset()
