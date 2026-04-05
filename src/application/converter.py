from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field
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
    tracks: list[int] | None = None
    ai_note_map: dict[int, int] | None = None
    ai_position_map: dict[tuple[int, int], int] = field(default_factory=dict)
    denoise: bool = False
    denoise_max_simultaneous: int = 3
    denoise_max_chord_repeats: int = 4


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

_POSITION_TIME_TOLERANCE_MS = 30

PositionIndex = dict[int, list[tuple[int, int]]]


def build_position_index(pos_map: dict[tuple[int, int], int]) -> PositionIndex:
    """Build a note-keyed index for fuzzy time lookup from position_map."""
    idx: PositionIndex = {}
    for (t, note), repl in pos_map.items():
        idx.setdefault(note, []).append((t, repl))
    for entries in idx.values():
        entries.sort()
    return idx


def _fuzzy_position_lookup(
    index: PositionIndex,
    time_ms: int,
    note: int,
    tolerance: int = _POSITION_TIME_TOLERANCE_MS,
) -> int | None:
    """Find the replacement for *note* at the closest time within *tolerance*."""
    entries = index.get(note)
    if not entries:
        return None
    times = [e[0] for e in entries]
    i = bisect_left(times, time_ms)
    best_dist = tolerance + 1
    best_repl: int | None = None
    for candidate_idx in (i - 1, i):
        if 0 <= candidate_idx < len(entries):
            t, repl = entries[candidate_idx]
            dist = abs(t - time_ms)
            if dist < best_dist:
                best_dist = dist
                best_repl = repl
    return best_repl


def _lookup_key(
    mapping: MappingConfig,
    profile_id: str,
    note: int,
    transpose: int,
    octave: int,
    snap: bool = False,
    ai_note_map: dict[int, int] | None = None,
    ai_position_map: dict[tuple[int, int], int] | None = None,
    ai_position_index: PositionIndex | None = None,
    time_ms: int = 0,
) -> tuple[str | None, str | None]:
    """Return (mapped_key, snap_info). snap_info is None when exact match.

    Fallback order:
      0a. AI position map — context-aware per-position replacement (exact then fuzzy)
      0b. AI note map — global 1:1 replacement
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

    if ai_position_map:
        pos_key = (time_ms, final_note)
        if pos_key in ai_position_map:
            replacement = ai_position_map[pos_key]
            if replacement == -1:
                return None, f"ai-drop: {final_note}"
            key = _exact_lookup(profile.note_to_key, replacement)
            if key is not None:
                return key, f"ai-ctx: {final_note}->{replacement}"

        if ai_position_index:
            replacement = _fuzzy_position_lookup(ai_position_index, time_ms, final_note)
            if replacement is not None:
                if replacement == -1:
                    return None, f"ai-drop: {final_note}"
                key = _exact_lookup(profile.note_to_key, replacement)
                if key is not None:
                    return key, f"ai-ctx~: {final_note}->{replacement}"

    if ai_note_map and final_note in ai_note_map:
        replacement = ai_note_map[final_note]
        if replacement == -1:
            return None, f"ai-drop: {final_note}"
        key = _exact_lookup(profile.note_to_key, replacement)
        if key is not None:
            return key, f"ai: {final_note}->{replacement}"

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

_JIANPU_NOTE = {0: "1", 2: "2", 4: "3", 5: "4", 7: "5", 9: "6", 11: "7"}


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
    raw_events, ppq, tempo_count = read_midi_events(midi_path, tracks=options.tracks)
    warnings: list[str] = []
    chart_events: list[ChartEvent] = []

    pos_map = options.ai_position_map or None
    pos_index = build_position_index(pos_map) if pos_map else None

    for event in raw_events:
        profile_id = _resolve_profile_for_event(mapping, options.profile, event.program)
        mapped_key, snap_info = _lookup_key(
            mapping, profile_id, event.note, options.transpose, options.octave,
            snap=options.snap, ai_note_map=options.ai_note_map,
            ai_position_map=pos_map,
            ai_position_index=pos_index,
            time_ms=event.time_ms,
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
    if options.denoise:
        chart, removed = denoise_chart(
            chart,
            max_simultaneous=options.denoise_max_simultaneous,
            max_chord_repeats=options.denoise_max_chord_repeats,
        )
        if removed:
            warnings.append(f"Denoise: removed {removed} duplicate/dense note events")

    return chart, warnings


# ── Post-processing: denoise ───────────────────────────────


def _dedup_simultaneous(
    events: list[ChartEvent],
    window_ms: int,
) -> list[ChartEvent]:
    """Remove duplicate taps on the same key within a short time window.

    Keeps the first occurrence. Handles unison doublings and octave
    doublings that collapsed onto the same game key.
    """
    kept: list[ChartEvent] = []
    last_time_for_key: dict[str, int] = {}
    for ev in events:
        prev = last_time_for_key.get(ev.key)
        if prev is not None and ev.time_ms - prev <= window_ms:
            continue
        last_time_for_key[ev.key] = ev.time_ms
        kept.append(ev)
    return kept


def _limit_key_repeat_rate(
    events: list[ChartEvent],
    max_per_burst: int,
    gap_ms: int = 500,
) -> list[ChartEvent]:
    """Limit how often each key can repeat within a burst, tracked per-key.

    Unlike the old sequential-run approach, this tracks each key
    independently so that repeating chord patterns (e.g. the same 6+3
    chord on every eighth note for a whole bar) are properly detected
    even though different keys interleave in the event stream.

    A "burst" for a given key is a sequence of hits where each
    consecutive pair is within *gap_ms*. When a burst exceeds
    *max_per_burst*, only the first, last, and evenly spaced middle
    hits are kept.
    """
    if not events:
        return events

    key_indices: dict[str, list[int]] = {}
    for i, ev in enumerate(events):
        key_indices.setdefault(ev.key, []).append(i)

    remove: set[int] = set()
    for indices in key_indices.values():
        if len(indices) <= max_per_burst:
            continue
        bursts: list[list[int]] = []
        current_burst = [indices[0]]
        for idx in indices[1:]:
            if events[idx].time_ms - events[current_burst[-1]].time_ms < gap_ms:
                current_burst.append(idx)
            else:
                bursts.append(current_burst)
                current_burst = [idx]
        bursts.append(current_burst)

        for burst in bursts:
            if len(burst) <= max_per_burst:
                continue
            keep = {burst[0], burst[-1]}
            inner_count = max_per_burst - 2
            if inner_count > 0:
                step = (len(burst) - 2) / (inner_count + 1)
                for i in range(1, inner_count + 1):
                    keep.add(burst[int(i * step)])
            remove.update(set(burst) - keep)

    return [ev for i, ev in enumerate(events) if i not in remove]


def _thin_repeating_chord(
    events: list[ChartEvent],
    max_repeats: int = 4,
    window_ms: int = 50,
) -> list[ChartEvent]:
    """Detect and thin repeating chord patterns (same set of keys).

    Groups events into time slots (within *window_ms*), represents each
    slot as a frozenset of keys, then finds consecutive runs of identical
    chord shapes.  Runs longer than *max_repeats* are thinned to keep
    the first, last, and evenly spaced middle occurrences.
    """
    if not events:
        return events

    slots: list[tuple[frozenset[str], list[int]]] = []
    current_keys: list[str] = [events[0].key]
    current_indices: list[int] = [0]
    anchor = events[0].time_ms
    for i in range(1, len(events)):
        if events[i].time_ms - anchor <= window_ms:
            current_keys.append(events[i].key)
            current_indices.append(i)
        else:
            slots.append((frozenset(current_keys), current_indices))
            current_keys = [events[i].key]
            current_indices = [i]
            anchor = events[i].time_ms
    slots.append((frozenset(current_keys), current_indices))

    remove: set[int] = set()
    run_start = 0
    while run_start < len(slots):
        shape = slots[run_start][0]
        run_end = run_start + 1
        while run_end < len(slots) and slots[run_end][0] == shape:
            run_end += 1
        run_len = run_end - run_start
        if run_len > max_repeats:
            keep_slot_positions = {0, run_len - 1}
            inner = max_repeats - 2
            if inner > 0:
                step = (run_len - 2) / (inner + 1)
                for k in range(1, inner + 1):
                    keep_slot_positions.add(int(k * step))
            for offset in range(run_len):
                if offset not in keep_slot_positions:
                    remove.update(slots[run_start + offset][1])
        run_start = run_end

    return [ev for i, ev in enumerate(events) if i not in remove]


def _thin_simultaneous(
    events: list[ChartEvent],
    max_simultaneous: int,
    window_ms: int = 30,
) -> list[ChartEvent]:
    """When too many different keys fire at once, keep only the loudest ones.

    Groups events within *window_ms* and, if the group exceeds
    *max_simultaneous*, retains only those with the longest duration
    (proxy for musical importance when velocity is unavailable in chart).
    """
    if not events:
        return events

    groups: list[list[int]] = []
    current_group: list[int] = [0]
    for i in range(1, len(events)):
        if events[i].time_ms - events[current_group[0]].time_ms <= window_ms:
            current_group.append(i)
        else:
            groups.append(current_group)
            current_group = [i]
    groups.append(current_group)

    keep_indices: set[int] = set()
    for group in groups:
        if len(group) <= max_simultaneous:
            keep_indices.update(group)
        else:
            ranked = sorted(
                group,
                key=lambda idx: (events[idx].duration_ms or 0),
                reverse=True,
            )
            keep_indices.update(ranked[:max_simultaneous])

    return [ev for i, ev in enumerate(events) if i in keep_indices]


def denoise_chart(
    chart: ChartDocument,
    *,
    dedup_window_ms: int = 30,
    max_key_per_burst: int = 3,
    burst_gap_ms: int = 500,
    max_chord_repeats: int = 4,
    max_simultaneous: int = 3,
) -> tuple[ChartDocument, int]:
    """Remove duplicate / overly dense note events from a converted chart.

    Applies four music-theory-informed rules in order:

    1. **Same-key dedup** — remove duplicate taps on the same key within
       *dedup_window_ms* (unison doublings, octave collapse).
    2. **Per-key rate limit** — for each key independently, detect bursts
       of rapid repetition and thin them.  Catches tremolo, pedal point,
       and repeating accompaniment patterns even when interleaved with
       other keys in chords.
    3. **Repeating-chord thin** — detect consecutive identical chord shapes
       (same set of keys) and reduce long runs.  Directly targets the
       "same chord on every eighth note for a whole bar" pattern.
    4. **Simultaneous thin** — when too many different keys fire at once,
       keep only the most important ones.

    Only ``tap`` events are filtered; ``down``/``up`` pairs are kept intact.
    Returns (denoised_chart, number_of_removed_events).
    """
    taps = [ev for ev in chart.events if ev.action == "tap"]
    others = [ev for ev in chart.events if ev.action != "tap"]
    original_count = len(taps)

    taps = _dedup_simultaneous(taps, dedup_window_ms)
    taps = _limit_key_repeat_rate(taps, max_key_per_burst, burst_gap_ms)
    taps = _thin_repeating_chord(taps, max_chord_repeats)
    taps = _thin_simultaneous(taps, max_simultaneous)

    merged = sorted(taps + others, key=lambda e: e.time_ms)
    removed = original_count - len(taps)
    denoised = ChartDocument(events=merged, metadata=chart.metadata)
    return denoised, removed


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


# ── Preview MIDI generation ────────────────────────────────


def chart_to_preview_midi(
    chart: ChartDocument,
    mapping: MappingConfig,
    output_path: Path,
) -> None:
    """Reverse-map chart key events back to MIDI notes and write a playable
    MIDI file so the user can audition the conversion result."""
    from mido import Message as Msg
    from mido import MetaMessage as MM
    from mido import MidiFile as MF
    from mido import MidiTrack as MT

    reverse: dict[str, dict[str, int]] = {}
    for pid, profile in mapping.profiles.items():
        key_to_note: dict[str, int] = {}
        for note_str, key in profile.note_to_key.items():
            try:
                num = int(note_str)
            except ValueError:
                parsed = _note_name_to_midi_number(note_str)
                if parsed is None:
                    continue
                num = parsed
            key_to_note.setdefault(key, num)
        reverse[pid] = key_to_note

    tpb = 480
    tempo = 500_000  # 120 BPM

    def _ms2tick(ms: int) -> int:
        return round(ms * tpb * 1000 / tempo)

    raw: list[tuple[int, int, int, int]] = []
    for ev in chart.events:
        pid = ev.mapping_profile or mapping.default_profile
        note = reverse.get(pid, {}).get(ev.key)
        if note is None:
            continue
        if ev.action == "tap":
            dur = ev.duration_ms or 30
            raw.append((_ms2tick(ev.time_ms), 1, note, 80))
            raw.append((_ms2tick(ev.time_ms + dur), 0, note, 0))
        elif ev.action == "down":
            raw.append((_ms2tick(ev.time_ms), 1, note, 80))
        elif ev.action == "up":
            raw.append((_ms2tick(ev.time_ms), 0, note, 0))

    raw.sort(key=lambda x: (x[0], x[1]))

    mid = MF(type=0, ticks_per_beat=tpb)
    track = MT()
    mid.tracks.append(track)
    track.append(MM("set_tempo", tempo=tempo, time=0))

    last_tick = 0
    for tick, is_on, note, vel in raw:
        delta = max(tick - last_tick, 0)
        msg_type = "note_on" if is_on else "note_off"
        track.append(Msg(msg_type, note=note, velocity=vel, time=delta))
        last_tick = tick

    output_path.parent.mkdir(parents=True, exist_ok=True)
    mid.save(str(output_path))


# ── Jianpu (numbered musical notation) export ──────────────


def _midi_to_jianpu(note: int) -> str:
    """Convert a MIDI note number to Jianpu notation with octave markers.

    Reference octave is 4 (C4 = middle C = ``1`` with no marker).
    Higher octaves append ``'``, lower octaves append ``,``.
    """
    pc = note % 12
    octave = (note // 12) - 1

    base = _JIANPU_NOTE.get(pc)
    if base is None:
        lower = (pc - 1) % 12
        upper = (pc + 1) % 12
        if lower in _JIANPU_NOTE:
            base = f"#{_JIANPU_NOTE[lower]}"
        elif upper in _JIANPU_NOTE:
            base = f"b{_JIANPU_NOTE[upper]}"
        else:
            return "?"

    diff = octave - 4
    if diff > 0:
        base += "'" * diff
    elif diff < 0:
        base += "," * (-diff)
    return base


def chart_to_jianpu(
    chart: ChartDocument,
    mapping: MappingConfig,
    bpm: float = 120.0,
    time_signature: str = "4/4",
    title: str = "",
) -> str:
    """Convert a chart to text-based Jianpu (numbered musical notation).

    The output uses quarter-note beat positions with sustain markers (``-``)
    and rest markers (``0``), grouped into bars separated by ``|``.
    """
    reverse: dict[str, int] = {}
    for _pid, profile in mapping.profiles.items():
        for note_str, key in profile.note_to_key.items():
            try:
                num = int(note_str)
            except ValueError:
                parsed = _note_name_to_midi_number(note_str)
                if parsed is None:
                    continue
                num = parsed
            reverse.setdefault(key, num)

    ts_parts = time_signature.split("/")
    beats_per_bar = int(ts_parts[0]) if len(ts_parts) >= 2 else 4
    beat_ms = 60000.0 / bpm

    events = sorted(
        (e for e in chart.events if e.action in ("tap", "down")),
        key=lambda e: e.time_ms,
    )
    if not events:
        return ""

    grid_notes: dict[int, list[str]] = {}
    grid_end_ms: dict[int, float] = {}

    for ev in events:
        midi_note = reverse.get(ev.key)
        if midi_note is None:
            continue
        beat_idx = round(ev.time_ms / beat_ms)
        grid_notes.setdefault(beat_idx, []).append(_midi_to_jianpu(midi_note))
        end = ev.time_ms + (ev.duration_ms or 0)
        if end > grid_end_ms.get(beat_idx, 0):
            grid_end_ms[beat_idx] = end

    if not grid_notes:
        return ""

    last_beat = max(grid_notes.keys())
    total_beats = ((last_beat // beats_per_bar) + 1) * beats_per_bar

    cells: list[str] = []
    sustain_end = 0.0
    for i in range(total_beats):
        notes = grid_notes.get(i)
        beat_start = i * beat_ms
        if notes:
            unique = list(dict.fromkeys(notes))
            cells.append(
                unique[0] if len(unique) == 1 else f"({' '.join(unique)})"
            )
            sustain_end = max(sustain_end, grid_end_ms.get(i, 0))
        elif beat_start < sustain_end - beat_ms * 0.25:
            cells.append("-")
        else:
            cells.append("0")

    col_w = max((len(c) for c in cells), default=1) + 1
    col_w = max(col_w, 3)

    lines: list[str] = []
    if title:
        lines.append(title)
    lines.append(f"1=C  {time_signature}  ♩={int(bpm)}")
    lines.append("")

    bar_strs: list[str] = []
    for bar in range(0, total_beats, beats_per_bar):
        bar_cells = cells[bar : bar + beats_per_bar]
        bar_strs.append("  ".join(c.center(col_w) for c in bar_cells))

    bars_per_line = 4
    for i in range(0, len(bar_strs), bars_per_line):
        chunk = bar_strs[i : i + bars_per_line]
        is_last = i + bars_per_line >= len(bar_strs)
        lines.append("| " + " | ".join(chunk) + (" ‖" if is_last else " |"))

    lines.append("")
    return "\n".join(lines)


# ── Jianpu PDF export ──────────────────────────────────────


@dataclass(slots=True)
class JianpuNote:
    digit: str
    dots_above: int = 0
    dots_below: int = 0


@dataclass(slots=True)
class JianpuCell:
    notes: list[JianpuNote] = field(default_factory=list)


def _midi_to_jianpu_note(note: int) -> JianpuNote:
    """Convert a MIDI note number to a structured ``JianpuNote``."""
    pc = note % 12
    octave = (note // 12) - 1

    base = _JIANPU_NOTE.get(pc)
    prefix = ""
    if base is None:
        lower = (pc - 1) % 12
        upper = (pc + 1) % 12
        if lower in _JIANPU_NOTE:
            base = _JIANPU_NOTE[lower]
            prefix = "#"
        elif upper in _JIANPU_NOTE:
            base = _JIANPU_NOTE[upper]
            prefix = "b"
        else:
            return JianpuNote(digit="?")

    diff = octave - 4
    return JianpuNote(
        digit=f"{prefix}{base}",
        dots_above=max(diff, 0),
        dots_below=max(-diff, 0),
    )


def _build_reverse_mapping(mapping: MappingConfig) -> dict[str, int]:
    """Build keyboard-key to MIDI-note mapping (first match per key wins)."""
    reverse: dict[str, int] = {}
    for _pid, profile in mapping.profiles.items():
        for note_str, key in profile.note_to_key.items():
            try:
                num = int(note_str)
            except ValueError:
                parsed = _note_name_to_midi_number(note_str)
                if parsed is None:
                    continue
                num = parsed
            reverse.setdefault(key, num)
    return reverse


def chart_to_jianpu_pdf(
    chart: ChartDocument,
    mapping: MappingConfig,
    output_path: Path,
    bpm: float = 120.0,
    time_signature: str = "4/4",
    title: str = "",
) -> None:
    """Render a chart as a Jianpu PDF using Sky-community notation style.

    Layout: A4 landscape, eighth-note grid, chords stacked vertically,
    octave dots drawn above / below each digit, empty slots left blank.
    """
    from fpdf import FPDF

    reverse = _build_reverse_mapping(mapping)

    ts_parts = time_signature.split("/")
    beats_per_bar = int(ts_parts[0]) if len(ts_parts) >= 2 else 4
    beat_ms = 60000.0 / bpm
    grid_ms = beat_ms / 2
    slots_per_bar = beats_per_bar * 2

    events = sorted(
        (e for e in chart.events if e.action in ("tap", "down")),
        key=lambda e: e.time_ms,
    )
    if not events:
        return

    grid: dict[int, list[JianpuNote]] = {}
    for ev in events:
        midi_note = reverse.get(ev.key)
        if midi_note is None:
            continue
        slot = round(ev.time_ms / grid_ms)
        grid.setdefault(slot, []).append(_midi_to_jianpu_note(midi_note))

    if not grid:
        return

    last_slot = max(grid.keys())
    total_bars = (last_slot // slots_per_bar) + 1

    bars: list[list[JianpuCell]] = []
    for bar_idx in range(total_bars):
        bar_cells: list[JianpuCell] = []
        for slot in range(slots_per_bar):
            abs_slot = bar_idx * slots_per_bar + slot
            raw_notes = grid.get(abs_slot, [])
            seen: set[tuple[str, int, int]] = set()
            unique: list[JianpuNote] = []
            for n in raw_notes:
                k = (n.digit, n.dots_above, n.dots_below)
                if k not in seen:
                    seen.add(k)
                    unique.append(n)
            bar_cells.append(JianpuCell(notes=unique))
        bars.append(bar_cells)

    _render_jianpu_pdf(
        bars, slots_per_bar, total_bars, output_path,
        time_signature, bpm, title,
    )


# ── PDF renderer internals ─────────────────────────────────

_CJK_FONT_CANDIDATES: list[tuple[str, str | None]] = [
    ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc"),
    ("C:/Windows/Fonts/simhei.ttf", None),
    ("/System/Library/Fonts/PingFang.ttc", None),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", None),
    ("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc", None),
]


def _register_cjk_font(pdf: object) -> str:
    """Try to register a CJK-capable font. Returns the font family name."""
    import os

    for regular_path, bold_path in _CJK_FONT_CANDIDATES:
        if os.path.isfile(regular_path):
            pdf.add_font("cjk", fname=regular_path)  # type: ignore[union-attr]
            if bold_path and os.path.isfile(bold_path):
                pdf.add_font("cjk", style="B", fname=bold_path)  # type: ignore[union-attr]
            else:
                pdf.add_font("cjk", style="B", fname=regular_path)  # type: ignore[union-attr]
            return "cjk"
    return "Helvetica"


_BARS_PER_LINE = 4
_MARGIN = 15.0
_PAGE_W = 297.0
_PAGE_H = 210.0
_LABEL_W = 12.0

_DIGIT_PT = 14
_DIGIT_MM = _DIGIT_PT * 0.3528
_ASCENT = _DIGIT_MM * 0.72
_NOTE_SPACING = _DIGIT_MM + 1.5
_DOT_R = 0.55
_DOT_ABOVE_GAP = 1.5
_DOT_ABOVE_OFFSET = 1.6
_DOT_BELOW_GAP = 1.5
_DOT_BELOW_OFFSET = 1.0
_ROW_GAP = 8.0
_BAR_LINE_W = 0.3


def _row_height(row_bars: list[list[JianpuCell]]) -> float:
    max_chord = 1
    max_da = 0
    max_db = 0
    for bar in row_bars:
        for cell in bar:
            if cell.notes:
                max_chord = max(max_chord, len(cell.notes))
                for n in cell.notes:
                    max_da = max(max_da, n.dots_above)
                    max_db = max(max_db, n.dots_below)
    above_h = _DOT_ABOVE_OFFSET + max_da * _DOT_ABOVE_GAP if max_da else 0
    below_h = _DOT_BELOW_OFFSET + max_db * _DOT_BELOW_GAP if max_db else 0
    return above_h + max_chord * _NOTE_SPACING + below_h + _ROW_GAP


def _render_jianpu_pdf(
    bars: list[list[JianpuCell]],
    slots_per_bar: int,
    total_bars: int,
    output_path: Path,
    time_signature: str,
    bpm: float,
    title: str,
) -> None:
    from fpdf import FPDF

    content_w = _PAGE_W - 2 * _MARGIN
    music_w = content_w - _LABEL_W
    cols_per_line = _BARS_PER_LINE * slots_per_bar
    col_w = music_w / cols_per_line

    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=False)
    font = _register_cjk_font(pdf)
    pdf.add_page()

    y = _MARGIN
    if title:
        pdf.set_font(font, "B", 16)
        tw = pdf.get_string_width(title)
        pdf.text((_PAGE_W - tw) / 2, y + 5.5, title)
        y += 10

    info = f"1=C  {time_signature}  BPM={int(bpm)}"
    pdf.set_font(font, "", 11)
    tw = pdf.get_string_width(info)
    pdf.text((_PAGE_W - tw) / 2, y + 4, info)
    y += 9

    pdf.set_font(font, "", _DIGIT_PT)
    pdf.set_draw_color(0, 0, 0)
    pdf.set_fill_color(0, 0, 0)

    for row_start in range(0, total_bars, _BARS_PER_LINE):
        row_bars = bars[row_start : row_start + _BARS_PER_LINE]
        rh = _row_height(row_bars)

        if y + rh > _PAGE_H - _MARGIN:
            pdf.add_page()
            y = _MARGIN

        max_da = 0
        max_chord = 1
        for bar in row_bars:
            for cell in bar:
                if cell.notes:
                    max_chord = max(max_chord, len(cell.notes))
                    for n in cell.notes:
                        max_da = max(max_da, n.dots_above)
        above_h = (_DOT_ABOVE_OFFSET + max_da * _DOT_ABOVE_GAP) if max_da else 0
        first_baseline_y = y + above_h + _ASCENT

        bar_top = y - 1
        bar_bot = y + rh - _ROW_GAP + 1

        pdf.set_font(font, "", 9)
        pdf.text(_MARGIN + 1, first_baseline_y, f"({row_start + 1})")
        pdf.set_font(font, "", _DIGIT_PT)

        music_x = _MARGIN + _LABEL_W
        num_bars = len(row_bars)

        for bi, bar in enumerate(row_bars):
            bar_x = music_x + bi * slots_per_bar * col_w

            pdf.set_line_width(_BAR_LINE_W)
            pdf.line(bar_x, bar_top, bar_x, bar_bot)

            for si, cell in enumerate(bar):
                if not cell.notes:
                    continue
                cx = bar_x + si * col_w + col_w / 2

                for ni, note in enumerate(cell.notes):
                    baseline_y = first_baseline_y + ni * _NOTE_SPACING
                    digit_w = pdf.get_string_width(note.digit)
                    pdf.text(cx - digit_w / 2, baseline_y, note.digit)

                    for d in range(note.dots_above):
                        dy = baseline_y - _ASCENT - _DOT_ABOVE_OFFSET - d * _DOT_ABOVE_GAP
                        pdf.ellipse(
                            cx - _DOT_R, dy - _DOT_R, 2 * _DOT_R, 2 * _DOT_R, "F",
                        )

                    for d in range(note.dots_below):
                        dy = baseline_y + _DOT_BELOW_OFFSET + d * _DOT_BELOW_GAP
                        pdf.ellipse(
                            cx - _DOT_R, dy - _DOT_R, 2 * _DOT_R, 2 * _DOT_R, "F",
                        )

        final_x = music_x + num_bars * slots_per_bar * col_w
        is_last = row_start + num_bars >= total_bars
        if is_last:
            pdf.set_line_width(_BAR_LINE_W)
            pdf.line(final_x - 1.5, bar_top, final_x - 1.5, bar_bot)
            pdf.set_line_width(_BAR_LINE_W * 3)
            pdf.line(final_x, bar_top, final_x, bar_bot)
            pdf.set_line_width(_BAR_LINE_W)
        else:
            pdf.set_line_width(_BAR_LINE_W)
            pdf.line(final_x, bar_top, final_x, bar_bot)

        y += rh

    output_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(output_path))
