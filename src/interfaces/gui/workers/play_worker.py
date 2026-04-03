from __future__ import annotations

import threading

from PySide6.QtCore import QThread, Signal

from src.application.player import PlayOptions, play_chart
from src.domain.chart import ChartDocument
from src.infrastructure.input_backends import BaseInputBackend


class PlayWorker(QThread):
    log_message = Signal(str)
    finished_signal = Signal(bool, str)

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
            )
            if self.stop_event.is_set():
                self.finished_signal.emit(False, "Playback stopped by user")
            else:
                self.finished_signal.emit(True, "Playback complete")
        except Exception as exc:
            self.finished_signal.emit(False, f"Error: {exc}")

    def _emit_log(self, msg: str) -> None:
        self.log_message.emit(msg)

    def request_stop(self) -> None:
        self.stop_event.set()
