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
    snap: bool = False
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


_MAX_SNAP_SEMITONES = 2


def _lookup_key(
    mapping: MappingConfig,
    profile_id: str,
    note: int,
    transpose: int,
    octave: int,
    snap: bool = False,
) -> tuple[str | None, str | None]:
    """Return (mapped_key, snap_info). snap_info is None when exact match.

    Fallback order (when snap=True):
      1. Snap within max distance (handles sharps/flats already in range)
      2. Octave fold → exact (handles out-of-range natural notes)
      3. Octave fold → snap within max distance (handles out-of-range sharps/flats)
      4. Give up → skip
    """
    profile = mapping.profiles.get(profile_id)
    if profile is None:
        return None, None

    final_note = _apply_shifts(note, profile.transpose_semitones, transpose, profile.octave_shift, octave)

    exact = _exact_lookup(profile.note_to_key, final_note)
    if exact is not None:
        return exact, None

    if not snap:
        return None, None

    mapped_notes = _mapped_note_numbers(profile.note_to_key)
    if not mapped_notes:
        return None, None

    snapped = _snap_to_nearest(final_note, mapped_notes, _MAX_SNAP_SEMITONES)
    if snapped is not None:
        key = _exact_lookup(profile.note_to_key, snapped)
        if key is not None:
            return key, f"snap: {final_note}->{snapped}"

    folded = _octave_fold(final_note, mapped_notes)
    if folded != final_note:
        exact = _exact_lookup(profile.note_to_key, folded)
        if exact is not None:
            return exact, f"fold: {final_note}->{folded}"

        folded_snapped = _snap_to_nearest(folded, mapped_notes, _MAX_SNAP_SEMITONES)
        if folded_snapped is not None:
            key = _exact_lookup(profile.note_to_key, folded_snapped)
            if key is not None:
                return key, f"fold+snap: {final_note}->{folded}->{folded_snapped}"

    return None, None


def _apply_shifts(note: int, profile_transpose: int, transpose: int, profile_octave: int, octave: int) -> int:
    return note + profile_transpose + transpose + (profile_octave + octave) * 12


def _exact_lookup(note_to_key: dict[str, str], note: int) -> str | None:
    candidates = (str(note), _midi_number_to_note_name(note))
    for c in candidates:
        mapped = note_to_key.get(c)
        if mapped:
            return mapped
    return None


def _mapped_note_numbers(note_to_key: dict[str, str]) -> list[int]:
    result: list[int] = []
    for key_str in note_to_key:
        try:
            result.append(int(key_str))
        except ValueError:
            parsed = _note_name_to_midi_number(key_str)
            if parsed is not None:
                result.append(parsed)
    result.sort()
    return result


def _snap_to_nearest(note: int, mapped_notes: list[int], max_distance: int) -> int | None:
    """Find the closest mapped note within *max_distance* semitones.

    When two candidates are equidistant, the lower pitch wins (sounds more
    natural as a flat-direction resolution).
    """
    if not mapped_notes:
        return None
    best: int | None = None
    best_dist = max_distance + 1
    for mn in mapped_notes:
        dist = abs(note - mn)
        if dist > max_distance:
            if best is not None and mn > note:
                break
            continue
        if dist < best_dist or (dist == best_dist and best is not None and mn < best):
            best = mn
            best_dist = dist
    return best


def _octave_fold(note: int, mapped_notes: list[int]) -> int:
    low = mapped_notes[0]
    high = mapped_notes[-1]
    if low <= note <= high:
        return note
    pitch_class = note % 12
    best = note
    best_dist = abs(note - note)
    for shift in range(-10, 11):
        candidate = pitch_class + (shift + 5) * 12
        if low <= candidate <= high:
            dist = abs(note - candidate)
            if best == note or dist < best_dist:
                best = candidate
                best_dist = dist
    return best


_PITCH_CLASSES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _midi_number_to_note_name(note_number: int) -> str:
    octave = (note_number // 12) - 1
    return f"{_PITCH_CLASSES[note_number % 12]}{octave}"


def _note_name_to_midi_number(name: str) -> int | None:
    name = name.strip()
    for i, pc in enumerate(_PITCH_CLASSES):
        if name.upper().startswith(pc):
            rest = name[len(pc):]
            try:
                oct_val = int(rest)
                return (oct_val + 1) * 12 + i
            except ValueError:
                continue
    return None


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
        mapped_key, snap_info = _lookup_key(
            mapping, profile_id, event.note, options.transpose, options.octave, snap=options.snap
        )

        if mapped_key is None:
            message = (
                f"Unmapped note: note={event.note}, time_ms={event.time_ms}, profile={profile_id}"
            )
            if options.strict:
                raise MappingError(message)
            warnings.append(message)
            continue

        if snap_info:
            warnings.append(
                f"Snapped: note={event.note}, {snap_info}, key={mapped_key}, t={event.time_ms}ms"
            )

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
