from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from mido import MidiFile, merge_tracks, tick2second

PITCH_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

MAJOR_PROFILE = [1, 0, 1, 0, 1, 1, 0, 1, 0, 1, 0, 1]
MINOR_PROFILE = [1, 0, 1, 1, 0, 1, 0, 1, 1, 0, 1, 0]


@dataclass(slots=True)
class RawMidiEvent:
    time_ms: int
    note: int
    duration_ms: int
    velocity: int
    program: int | None


@dataclass(slots=True)
class MidiTrackInfo:
    index: int
    name: str
    message_count: int
    note_on_count: int
    program_changes: list[int]
    has_tempo: bool


def read_midi_events(midi_path: Path, single_track: int | None = None) -> tuple[list[RawMidiEvent], int, int]:
    midi = MidiFile(str(midi_path))
    ppq = midi.ticks_per_beat

    track_stream = _build_track_stream(midi, single_track)

    current_tempo = 500000  # 120 BPM
    current_ms = 0.0
    active_notes: dict[int, tuple[float, int, int | None]] = {}
    current_program: int | None = None
    events: list[RawMidiEvent] = []
    tempo_count = 0

    for msg in track_stream:
        if msg.time:
            delta_sec = tick2second(msg.time, ppq, current_tempo)
            current_ms += delta_sec * 1000

        if msg.type == "set_tempo":
            current_tempo = msg.tempo
            tempo_count += 1
            continue

        if msg.type == "program_change":
            current_program = msg.program
            continue

        if msg.type == "note_on" and msg.velocity > 0:
            active_notes[msg.note] = (current_ms, msg.velocity, current_program)
            continue

        if msg.type in {"note_off", "note_on"}:
            if msg.type == "note_on" and msg.velocity > 0:
                continue
            opened = active_notes.pop(msg.note, None)
            if opened is None:
                continue
            start_ms, velocity, program = opened
            duration = max(int(current_ms - start_ms), 1)
            events.append(
                RawMidiEvent(
                    time_ms=max(int(start_ms), 0),
                    note=msg.note,
                    duration_ms=duration,
                    velocity=velocity,
                    program=program,
                )
            )

    events.sort(key=lambda x: x.time_ms)
    return events, ppq, tempo_count


def list_midi_tracks(midi_path: Path) -> tuple[int, list[MidiTrackInfo]]:
    midi = MidiFile(str(midi_path))
    infos: list[MidiTrackInfo] = []

    for idx, track in enumerate(midi.tracks):
        name = ""
        note_on_count = 0
        program_changes: list[int] = []
        has_tempo = False

        for msg in track:
            if msg.type == "track_name":
                name = msg.name
            elif msg.type == "note_on" and msg.velocity > 0:
                note_on_count += 1
            elif msg.type == "program_change":
                program_changes.append(msg.program)
            elif msg.type == "set_tempo":
                has_tempo = True

        dedup_programs = sorted(set(program_changes))
        infos.append(
            MidiTrackInfo(
                index=idx,
                name=name or f"Track {idx}",
                message_count=len(track),
                note_on_count=note_on_count,
                program_changes=dedup_programs,
                has_tempo=has_tempo,
            )
        )

    return midi.ticks_per_beat, infos


@dataclass(slots=True)
class MidiKeyAnalysis:
    key_signature: str | None = None
    detected_key: str | None = None
    detected_mode: str | None = None
    suggested_transpose: int = 0
    note_distribution: list[tuple[str, int]] = field(default_factory=list)


def analyze_midi_key(midi_path: Path, single_track: int | None = None) -> MidiKeyAnalysis:
    midi = MidiFile(str(midi_path))
    result = MidiKeyAnalysis()

    scan_tracks = (
        [midi.tracks[single_track]] if single_track is not None and 0 <= single_track < len(midi.tracks)
        else midi.tracks
    )

    for track in scan_tracks:
        for msg in track:
            if msg.type == "key_signature":
                result.key_signature = f"{msg.key} {'major' if msg.key.islower() is False else 'minor'}"
                break
        if result.key_signature:
            break

    pitch_counts: Counter[int] = Counter()
    note_stream = scan_tracks[0] if len(scan_tracks) == 1 else merge_tracks(scan_tracks)
    for msg in note_stream:
        if msg.type == "note_on" and msg.velocity > 0:
            pitch_counts[msg.note % 12] += 1

    if not pitch_counts:
        return result

    result.note_distribution = [
        (PITCH_NAMES[pc], pitch_counts.get(pc, 0)) for pc in range(12)
    ]
    result.note_distribution.sort(key=lambda x: -x[1])

    best_key, best_mode, best_score = "C", "major", -1.0
    for root in range(12):
        for mode_name, profile in [("major", MAJOR_PROFILE), ("minor", MINOR_PROFILE)]:
            score = sum(
                pitch_counts.get((root + i) % 12, 0) * profile[i] for i in range(12)
            )
            if score > best_score:
                best_score = score
                best_key = PITCH_NAMES[root]
                best_mode = mode_name

    result.detected_key = best_key
    result.detected_mode = best_mode

    root_index = PITCH_NAMES.index(best_key)
    if best_mode == "major":
        result.suggested_transpose = -root_index if root_index <= 6 else 12 - root_index
    else:
        relative_major = (root_index + 3) % 12
        result.suggested_transpose = -relative_major if relative_major <= 6 else 12 - relative_major

    return result


def export_single_track_midi(
    midi_path: Path,
    track_index: int,
    output_path: Path,
    include_tempo_track: bool = True,
) -> None:
    midi = MidiFile(str(midi_path))
    if track_index < 0 or track_index >= len(midi.tracks):
        raise IndexError(f"track index out of range: {track_index}, track_count={len(midi.tracks)}")

    target = MidiFile(type=1, ticks_per_beat=midi.ticks_per_beat)

    if include_tempo_track and len(midi.tracks) > 0:
        tempo_track = midi.tracks[0].copy()
        target.tracks.append(tempo_track)

    selected = midi.tracks[track_index].copy()
    target.tracks.append(selected)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    target.save(str(output_path))


def _build_track_stream(midi: MidiFile, single_track: int | None):
    if single_track is not None:
        if single_track < 0 or single_track >= len(midi.tracks):
            raise IndexError(f"track index out of range: {single_track}, track_count={len(midi.tracks)}")
        return midi.tracks[single_track]
    return merge_tracks(midi.tracks)
