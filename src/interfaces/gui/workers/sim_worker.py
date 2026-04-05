"""QTimer-driven simulation engine for visual + audio chart playback."""

from __future__ import annotations

import time

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

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self, chart: ChartDocument, speed: float = 1.0, start_ms: int = 0) -> None:
        groups = _group_by_time(chart.events)
        self._start_ms = max(start_ms, 0)
        if self._start_ms > 0:
            groups = [(t, evts) for t, evts in groups if t >= self._start_ms]
        self._groups = groups
        self._index = 0
        self._speed = max(speed, 0.1)
        self._total_ms = (self._groups[-1][0] - self._start_ms) if self._groups else 0
        self._pending.clear()
        self._pressed.clear()
        self._start = time.perf_counter()
        self._running = True
        self._timer.start()

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
