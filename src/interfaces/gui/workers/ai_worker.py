from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from src.application.ai_arranger import AiArrangeResult, ai_arrange
from src.domain.mapping import MappingConfig


class AiArrangeWorker(QThread):
    finished = Signal(object)
    error = Signal(str)
    chunk_received = Signal(str)

    def __init__(
        self,
        midi_path: Path,
        mapping: MappingConfig,
        profile_id: str,
        api_key: str,
        transpose: int = 0,
        octave: int = 0,
        single_track: int | None = None,
        base_url: str | None = None,
        model: str = "gpt-4o-mini",
        mode: str = "remap",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.midi_path = midi_path
        self.mapping = mapping
        self.profile_id = profile_id
        self.api_key = api_key
        self.transpose = transpose
        self.octave = octave
        self.single_track = single_track
        self.base_url = base_url
        self.model = model
        self.mode = mode

    def run(self) -> None:
        try:
            result = ai_arrange(
                self.midi_path,
                self.mapping,
                self.profile_id,
                self.api_key,
                transpose=self.transpose,
                octave=self.octave,
                single_track=self.single_track,
                base_url=self.base_url,
                model=self.model,
                mode=self.mode,
                on_chunk=self._on_chunk,
            )
            self.finished.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))

    def _on_chunk(self, accumulated: str) -> None:
        self.chunk_received.emit(accumulated)
