from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from src.application.ai_arranger import (
    AiArrangeResult,
    ai_arrange,
    build_retry_prompt,
    call_openai,
    parse_analysis_and_json,
    parse_context_response,
    parse_remap_response,
)
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
        style: str = "conservative",
        simplify: bool = False,
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
        self.style = style
        self.simplify = simplify

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
                style=self.style,
                simplify=self.simplify,
                on_chunk=self._on_chunk,
            )
            self.finished.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))

    def _on_chunk(self, accumulated: str) -> None:
        self.chunk_received.emit(accumulated)


class AiRetryWorker(QThread):
    """Re-run AI arrangement with user feedback incorporated."""

    finished = Signal(object)
    error = Signal(str)
    chunk_received = Signal(str)

    def __init__(
        self,
        original_prompt: str,
        previous_analysis: str,
        user_feedback: str,
        api_key: str,
        base_url: str | None = None,
        model: str = "gpt-4o-mini",
        mode: str = "remap",
        max_tokens: int = 16384,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.original_prompt = original_prompt
        self.previous_analysis = previous_analysis
        self.user_feedback = user_feedback
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.mode = mode
        self.max_tokens = max_tokens

    def run(self) -> None:
        try:
            prompt = build_retry_prompt(
                self.original_prompt, self.previous_analysis, self.user_feedback,
            )
            raw = call_openai(
                self.api_key, prompt,
                base_url=self.base_url, model=self.model,
                on_chunk=self._on_chunk, max_tokens=self.max_tokens,
            )
            analysis_text, json_text = parse_analysis_and_json(raw)

            if self.mode == "context":
                position_map = parse_context_response(json_text) if json_text else []
                result = AiArrangeResult(
                    position_map=position_map,
                    mode="context",
                    explanation=raw,
                    analysis_text=analysis_text,
                )
            else:
                note_map = parse_remap_response(json_text) if json_text else {}
                result = AiArrangeResult(
                    note_map=note_map,
                    mode="remap",
                    explanation=raw,
                    analysis_text=analysis_text,
                )
            self.finished.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))

    def _on_chunk(self, accumulated: str) -> None:
        self.chunk_received.emit(accumulated)
