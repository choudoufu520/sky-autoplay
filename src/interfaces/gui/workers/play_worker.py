from __future__ import annotations

import threading

from PySide6.QtCore import QThread, Signal

from src.application.player import PlayOptions, play_chart
from src.domain.chart import ChartDocument
from src.infrastructure.input_backends import BaseInputBackend


class PlayWorker(QThread):
    log_message = Signal(str)
    finished_signal = Signal(bool, str)
    progress_signal = Signal(int, int, int, int)
    countdown_signal = Signal(int)
    key_display_signal = Signal(object)

    def __init__(
        self,
        chart: ChartDocument,
        backend: BaseInputBackend,
        options: PlayOptions,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.chart = chart
        self.backend = backend
        self.options = options
        self.stop_event = threading.Event()

    def run(self) -> None:
        try:
            play_chart(
                self.chart,
                self.backend,
                self.options,
                stop_event=self.stop_event,
                log=self._emit_log,
                progress=self._emit_progress,
                countdown=self._emit_countdown,
                key_display=self._emit_key_display,
            )
            if self.stop_event.is_set():
                self.finished_signal.emit(False, "Playback stopped by user")
            else:
                self.finished_signal.emit(True, "Playback complete")
        except Exception as exc:
            self.finished_signal.emit(False, f"Error: {exc}")

    def _emit_log(self, msg: str) -> None:
        self.log_message.emit(msg)

    def _emit_progress(self, current: int, total: int, elapsed_ms: int, total_ms: int) -> None:
        self.progress_signal.emit(current, total, elapsed_ms, total_ms)

    def _emit_countdown(self, remain: int) -> None:
        self.countdown_signal.emit(remain)

    def _emit_key_display(
        self,
        current_keys: list[str],
        upcoming: list[tuple[int, str]],
    ) -> None:
        self.key_display_signal.emit((current_keys, upcoming))

    def request_stop(self) -> None:
        self.stop_event.set()
