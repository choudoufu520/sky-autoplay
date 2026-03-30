from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.domain.chart import ChartDocument, ChartEvent, ChartMetadata
from src.domain.mapping import MappingConfig
from src.infrastructure.midi_reader import RawMidiEvent, read_midi_events


class MappingError(ValueError):
    """Raised when no valid note mapping exists for an event."""


@dataclass(slots=True)
class ConvertOptions:
    profile: str | None = None
    transpose: int = 0
    octave: int = 0
    strict: bool = False
    note_mode: str = "tap"  # tap|hold
    single_track: int | None = None


def _resolve_profile_for_event(
    mapping: MappingConfig,
    requested_profile: str | None,
    midi_program: int | None,
) -> str:
    if requested_profile:
        return requested_profile
    if midi_program is not None and midi_program in mapping.program_to_profile:
        return mapping.program_to_profile[midi_program]
    return mapping.default_profile


def _lookup_key(
    mapping: MappingConfig,
    profile_id: str,
    note: int,
    transpose: int,
    octave: int,
) -> str | None:
    profile = mapping.profiles.get(profile_id)
    if profile is None:
        return None

    final_note = note + profile.transpose_semitones + transpose + (profile.octave_shift + octave) * 12
    lookup_candidates = (str(final_note), _midi_number_to_note_name(final_note))

    for candidate in lookup_candidates:
        mapped = profile.note_to_key.get(candidate)
        if mapped:
            return mapped
    return None


def _midi_number_to_note_name(note_number: int) -> str:
    pitch_classes = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    octave = (note_number // 12) - 1
    return f"{pitch_classes[note_number % 12]}{octave}"


def convert_midi_to_chart(
    midi_path: Path,
    mapping: MappingConfig,
    options: ConvertOptions,
) -> tuple[ChartDocument, list[str]]:
    raw_events, ppq, tempo_count = read_midi_events(midi_path, single_track=options.single_track)
    warnings: list[str] = []
    chart_events: list[ChartEvent] = []

    for event in raw_events:
        profile_id = _resolve_profile_for_event(mapping, options.profile, event.program)
        mapped_key = _lookup_key(mapping, profile_id, event.note, options.transpose, options.octave)

        if mapped_key is None:
            message = (
                f"Unmapped note: note={event.note}, time_ms={event.time_ms}, profile={profile_id}"
            )
            if options.strict:
                raise MappingError(message)
            warnings.append(message)
            continue

        if options.note_mode == "hold":
            _append_hold_event(chart_events, event, mapped_key, profile_id)
        else:
            chart_events.append(
                ChartEvent(
                    time_ms=event.time_ms,
                    key=mapped_key,
                    action="tap",
                    duration_ms=max(event.duration_ms, 1),
                    mapping_profile=profile_id,
                )
            )

    chart_events.sort(key=lambda x: x.time_ms)
    chart = ChartDocument(
        events=chart_events,
        metadata=ChartMetadata(source_midi=str(midi_path), ppq=ppq, tempo_event_count=tempo_count),
    )
    return chart, warnings


def _append_hold_event(
    target: list[ChartEvent],
    event: RawMidiEvent,
    mapped_key: str,
    profile_id: str,
) -> None:
    target.append(
        ChartEvent(time_ms=event.time_ms, key=mapped_key, action="down", mapping_profile=profile_id)
    )
    target.append(
        ChartEvent(
            time_ms=event.time_ms + max(event.duration_ms, 1),
            key=mapped_key,
            action="up",
            mapping_profile=profile_id,
        )
    )
