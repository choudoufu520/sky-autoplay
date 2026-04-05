"""QTimer-driven simulation engine for visual + audio chart playback."""

from __future__ import annotations

import time
from bisect import bisect_left

from PySide6.QtCore import QObject, QTimer, Signal

from src.domain.chart import ChartDocument, ChartEvent


class SimulationEngine(QObject):
    """Plays back a chart on the main-thread event loop via QTimer,
    emitting per-key signals that drive the keyboard widget and audio."""

    key_pressed = Signal(str)
    key_released = Signal(str)
    progress = Signal(int, int, int, int)  # current, total, elapsed_ms, total_ms
    finished = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(8)
        self._timer.timeout.connect(self._tick)

        self._groups: list[tuple[int, list[ChartEvent]]] = []
        self._index = 0
        self._speed = 1.0
        self._start = 0.0
        self._start_ms = 0
        self._total_ms = 0
        self._pending: list[tuple[float, str]] = []
        self._pressed: set[str] = set()
        self._running = False
        self._first_tick = False

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self, chart: ChartDocument, speed: float = 1.0, start_ms: int = 0) -> None:
        groups = _group_by_time(chart.events)
        self._start_ms = max(start_ms, 0)
        start_idx = 0
        if self._start_ms > 0:
            start_idx = bisect_left(groups, (self._start_ms,))
            self._inject_held_notes(groups, start_idx)
        self._groups = groups
        self._index = start_idx
        self._speed = max(speed, 0.1)
        self._total_ms = (self._groups[-1][0] - self._start_ms) if self._groups else 0
        self._pending.clear()
        self._pressed.clear()
        self._running = True
        self._first_tick = True
        self._timer.start()

    def _inject_held_notes(self, groups: list[tuple[int, list[ChartEvent]]], start_idx: int) -> None:
        """Find down events before start_ms whose matching up is after; inject
        down at start_ms so held notes are not lost."""
        held: dict[str, ChartEvent] = {}
        for i in range(start_idx):
            for ev in groups[i][1]:
                if ev.action == "down":
                    held[ev.key] = ev
                elif ev.action in ("up", "tap") and ev.key in held:
                    del held[ev.key]
        if not held:
            return
        injected = [
            ChartEvent(time_ms=self._start_ms, key=ev.key, action="down",
                       duration_ms=ev.duration_ms, mapping_profile=ev.mapping_profile)
            for ev in held.values()
        ]
        if start_idx < len(groups) and groups[start_idx][0] == self._start_ms:
            groups[start_idx] = (self._start_ms, injected + list(groups[start_idx][1]))
        else:
            groups.insert(start_idx, (self._start_ms, injected))

    def stop(self) -> None:
        self._running = False
        self._timer.stop()
        for key in list(self._pressed):
            self.key_released.emit(key)
        self._pressed.clear()
        for _, key in self._pending:
            self.key_released.emit(key)
        self._pending.clear()
        self.finished.emit()

    # ── internal ────────────────────────────────────────────

    def _tick(self) -> None:
        if not self._running:
            return

        if self._first_tick:
            self._start = time.perf_counter()
            self._first_tick = False
        else:
            pass  # _start already set on first tick

        elapsed_real = time.perf_counter() - self._start
        elapsed_ms = elapsed_real * 1000.0 * self._speed + self._start_ms

        still_pending: list[tuple[float, str]] = []
        for rel_ms, key in self._pending:
            if elapsed_ms >= rel_ms:
                self.key_released.emit(key)
                self._pressed.discard(key)
            else:
                still_pending.append((rel_ms, key))
        self._pending = still_pending

        total = len(self._groups)
        while self._index < total:
            time_ms, events = self._groups[self._index]
            if elapsed_ms < time_ms:
                break

            for ev in events:
                if ev.action in ("tap", "down"):
                    self.key_pressed.emit(ev.key)
                    self._pressed.add(ev.key)
                    if ev.action == "tap":
                        self._pending.append((time_ms + 80, ev.key))
                elif ev.action == "up":
                    self.key_released.emit(ev.key)
                    self._pressed.discard(ev.key)

            self._index += 1

        self.progress.emit(
            min(self._index, total),
            total,
            int(elapsed_ms - self._start_ms),
            self._total_ms,
        )

        if self._index >= total and not self._pending:
            self._running = False
            self._timer.stop()
            self.finished.emit()


def _group_by_time(events: list[ChartEvent]) -> list[tuple[int, list[ChartEvent]]]:
    ordered = sorted(events, key=lambda e: e.time_ms)
    groups: list[tuple[int, list[ChartEvent]]] = []
    for ev in ordered:
        if groups and groups[-1][0] == ev.time_ms:
            groups[-1][1].append(ev)
        else:
            groups.append((ev.time_ms, [ev]))
    return groups
