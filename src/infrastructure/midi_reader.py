from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mido import MidiFile, merge_tracks, tick2second


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
