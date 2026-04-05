from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class NoteRole(str, Enum):
    melody = "melody"
    accompaniment = "accompaniment"
    bass = "bass"


ActionType = Literal["down", "up", "tap"]


class ChartEvent(BaseModel):
    time_ms: int = Field(ge=0)
    key: str = Field(min_length=1, max_length=32)
    action: ActionType
    duration_ms: int | None = Field(default=None, ge=0)
    mapping_profile: str | None = None


class ChartMetadata(BaseModel):
    source_midi: str | None = None
    ppq: int | None = Field(default=None, ge=1)
    tempo_event_count: int | None = Field(default=None, ge=0)


class ChartDocument(BaseModel):
    format_version: int = 1
    events: list[ChartEvent]
    metadata: ChartMetadata = Field(default_factory=ChartMetadata)
