from __future__ import annotations

import json
import logging
import queue as _queue
import re
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from src.domain.mapping import MappingConfig
from src.infrastructure.midi_reader import (
    MidiKeyAnalysis,
    MidiMeta,
    RawMidiEvent,
    analyze_midi_key,
    read_midi_events,
    read_midi_meta,
)

_PITCH_CLASSES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

_KEY_NAMES = ["C", "Db", "D", "Eb", "E", "F", "F#/Gb", "G", "Ab", "A", "Bb", "B"]

INSTRUMENT_GROUPS: dict[int, list[str]] = {
    0:  ["钢琴/Piano", "长笛/Flute", "排箫/Panflute", "卡林巴/Kalimba",
         "军号/Bugle", "陶笛/Ocarina", "口琴/Harmonica", "小提琴/Violin"],
    1:  ["冬季钢琴/Winter Piano", "木琴/Xylophone"],
    -1: ["竖琴/Harp", "吉他/Guitar", "尤克里里/Ukulele",
         "鲁特琴/Lute", "电吉他/Electric Guitar", "萨克斯/Saxophone"],
    -2: ["号角/Horn", "大提琴/Cello"],
    -3: ["低音提琴/Contrabass"],
}

MUSIC_KEY_WIKI = "https://sky-children-of-the-light.fandom.com/wiki/Music_Key"

logger = logging.getLogger(__name__)


def midi_to_name(n: int) -> str:
    return f"{_PITCH_CLASSES[n % 12]}{n // 12 - 1}"


def _midi_to_name(n: int) -> str:
    return midi_to_name(n)


def _detect_scale_key(available: list[int], key_analysis: MidiKeyAnalysis | None = None) -> str:
    if key_analysis:
        if key_analysis.detected_key and key_analysis.detected_mode:
            return f"{key_analysis.detected_key} {key_analysis.detected_mode}"
        if key_analysis.key_signature:
            return key_analysis.key_signature
    if not available:
        return "C major"
    root_pc = available[0] % 12
    return f"{_KEY_NAMES[root_pc]} major"


def transpose_to_key_name(transpose: int, profile_transpose: int = 0) -> str:
    """Map a transpose offset to the in-game instrument key name.

    *profile_transpose* is ``MappingProfile.transpose_semitones`` so the
    displayed key accounts for any built-in transposition of the profile.
    """
    root_pc = (0 - transpose - profile_transpose) % 12
    return f"{_KEY_NAMES[root_pc]} major"


_transpose_to_key_name = transpose_to_key_name


@dataclass(slots=True)
class PositionRemap:
    time_ms: int
    original: int
    replacement: int


@dataclass(slots=True)
class AiArrangeResult:
    note_map: dict[int, int] = field(default_factory=dict)
    position_map: list[PositionRemap] = field(default_factory=list)
    role_map: dict[tuple[int, int], str] = field(default_factory=dict)
    mode: str = "remap"
    explanation: str = ""
    analysis_text: str = ""
    prompt: str = ""
    unmapped_count: int = 0
    total_notes: int = 0


@dataclass(slots=True)
class ArrangePrecheck:
    available_notes: list[int] = field(default_factory=list)
    shift: int = 0
    estimated_tokens: int = 0
    requires_chunking: bool = False
    likely_key: str = "C major"


class AiArrangeCancelled(RuntimeError):
    """Raised when the user cancels an in-flight AI arrangement."""


class AiArrangeError(RuntimeError):
    """Structured user-facing error for AI arrangement failures."""

    def __init__(self, code: str, user_message: str, *, detail: str = "") -> None:
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message
        self.detail = detail


def get_available_notes(mapping: MappingConfig, profile_id: str) -> list[int]:
    profile = mapping.profiles.get(profile_id)
    if profile is None:
        return []
    available: list[int] = []
    for key_str in profile.note_to_key:
        try:
            available.append(int(key_str))
        except ValueError:
            pass
    available.sort()
    return available


def _get_available_notes(mapping: MappingConfig, profile_id: str) -> list[int]:
    return get_available_notes(mapping, profile_id)


def get_shift(mapping: MappingConfig, profile_id: str, transpose: int, octave: int) -> int:
    profile = mapping.profiles.get(profile_id)
    if profile is None:
        return 0
    return profile.transpose_semitones + transpose + (profile.octave_shift + octave) * 12


def _get_shift(mapping: MappingConfig, profile_id: str, transpose: int, octave: int) -> int:
    return get_shift(mapping, profile_id, transpose, octave)


def analyze_unmapped_notes(
    midi_path: Path,
    mapping: MappingConfig,
    profile_id: str,
    transpose: int = 0,
    octave: int = 0,
    tracks: list[int] | None = None,
) -> tuple[list[int], list[int], Counter[int]]:
    """Return (available_notes, unmapped_notes, unmapped_counts)."""
    available = _get_available_notes(mapping, profile_id)
    if not available:
        return [], [], Counter()

    raw_events, _, _ = read_midi_events(midi_path, tracks=tracks)
    shift = _get_shift(mapping, profile_id, transpose, octave)

    unmapped_counts: Counter[int] = Counter()
    for ev in raw_events:
        final = ev.note + shift
        if final not in available:
            unmapped_counts[final] += 1

    unmapped = sorted(unmapped_counts.keys())
    return available, unmapped, unmapped_counts


@dataclass(slots=True)
class OptimalSetting:
    transpose: int
    octave: int
    key_name: str
    unmapped_count: int
    instruments: list[str]


def find_optimal_settings(
    midi_path: Path,
    mapping: MappingConfig,
    profile_id: str,
    tracks: list[int] | None = None,
    should_cancel: CancelCallback | None = None,
) -> list[OptimalSetting]:
    """Search all transpose (-6..+5) x octave offset (-3..+1) combinations.

    Returns results sorted by unmapped_count ascending (best first).
    """
    _raise_if_cancelled(should_cancel)
    available = _get_available_notes(mapping, profile_id)
    if not available:
        return []

    available_set = set(available)
    raw_events, _, _ = read_midi_events(midi_path, tracks=tracks)
    if not raw_events:
        return []

    profile = mapping.profiles.get(profile_id)
    profile_transpose = profile.transpose_semitones if profile else 0

    results: list[OptimalSetting] = []
    for oct_offset in sorted(INSTRUMENT_GROUPS.keys()):
        _raise_if_cancelled(should_cancel)
        instruments = INSTRUMENT_GROUPS[oct_offset]
        for t in range(-6, 6):
            _raise_if_cancelled(should_cancel)
            shift = _get_shift(mapping, profile_id, t, oct_offset)
            unmapped = 0
            for idx, ev in enumerate(raw_events):
                if idx % 256 == 0:
                    _raise_if_cancelled(should_cancel)
                if (ev.note + shift) not in available_set:
                    unmapped += 1
            key_name = _transpose_to_key_name(t, profile_transpose)
            results.append(OptimalSetting(
                transpose=t,
                octave=oct_offset,
                key_name=key_name,
                unmapped_count=unmapped,
                instruments=instruments,
            ))
    results.sort(key=lambda s: s.unmapped_count)
    return results


# ── Remap mode ──────────────────────────────────────────────


def _format_midi_meta(
    meta: MidiMeta,
    filename: str,
    key_analysis: MidiKeyAnalysis | None = None,
) -> str:
    parts: list[str] = []
    if filename:
        parts.append(f"MIDI file: {filename}")
    parts.append(f"BPM: {meta.bpm}, Time signature: {meta.time_signature}")
    if meta.key_signature:
        parts.append(f"Key signature (from MIDI): {meta.key_signature}")
    elif key_analysis and key_analysis.detected_key and key_analysis.detected_mode:
        parts.append(f"Detected key: {key_analysis.detected_key} {key_analysis.detected_mode}")
    parts.append(f"Total notes: {meta.total_notes}, Duration: {meta.duration_sec}s")
    header = "\n".join(parts)
    song_hint = (
        "\nIf you can identify the song from the filename or musical features, "
        "use your knowledge of the original piece to make better arrangement decisions."
        if filename else ""
    )
    return f"\n{header}{song_hint}\n"


def _load_templates() -> dict[str, str]:
    from src.application.prompt_store import load_custom_prompts
    return load_custom_prompts()


def _get_style_block(style: str) -> str:
    templates = _load_templates()
    key = f"style_{style}"
    block = templates.get(key, "")
    return f"\n{block}" if block else ""


_SIMPLIFY_BLOCK = """
SIMPLIFICATION MODE — ACTIVE
This arrangement targets real-time manual performance in the mobile game "Sky: Children of the Light".
The player physically taps each note on a 15-key virtual instrument, so every extra key press increases difficulty.

Requirements:
- PRESERVE the main melody line at all costs — it must remain clearly recognizable.
- DROP repetitive accompaniment patterns, sustained pedal tones, bass drones, and harmonic padding.
- For chords with 3+ simultaneous notes, keep only the melody note and at most one supporting harmony note.
- Remove octave doublings — keep the octave closer to the melody register.
- Drop short ornamental notes (grace notes, trills, rapid runs) that add difficulty without melodic significance.
- Prefer silence over a wrong-sounding substitute — use -1 to drop a note rather than mapping it to a poor replacement.
- Target: reduce total note events by roughly 30-50% while keeping the piece musically recognizable.
"""


def build_remap_prompt(
    available: list[int],
    unmapped: list[int],
    unmapped_counts: Counter[int],
    style: str = "conservative",
    filename: str = "",
    meta: MidiMeta | None = None,
    key_analysis: MidiKeyAnalysis | None = None,
    optimal_hint: str = "",
    simplify: bool = False,
) -> str:
    avail_desc = ", ".join(f"{n}({_midi_to_name(n)})" for n in available)
    unmapped_lines = []
    for n in unmapped:
        unmapped_lines.append(f"  - {n} ({_midi_to_name(n)}), appears {unmapped_counts[n]} times")

    avail_min = min(available)
    avail_max = max(available)
    scale_key = _detect_scale_key(available, key_analysis)
    style_block = _get_style_block(style)

    drop_hint = ""
    if simplify or style in ("balanced", "creative"):
        drop_hint = '\nUse -1 as the value to drop a note: {{"61": 60, "73": -1}}'

    simplify_block = _SIMPLIFY_BLOCK if simplify else ""

    meta_block = _format_midi_meta(meta, filename, key_analysis) if meta else ""

    total_unmapped = sum(unmapped_counts[n] for n in unmapped)
    high_freq = [n for n in unmapped if unmapped_counts[n] >= max(3, total_unmapped * 0.1)]
    high_freq_hint = ""
    if high_freq:
        names = ", ".join(f"{n}({_midi_to_name(n)})" for n in high_freq)
        high_freq_hint = f"\nHIGH-FREQUENCY NOTES (appear often, choose replacements extra carefully): {names}\n"

    templates = _load_templates()
    template = templates.get("remap_template", "")
    return template.format_map({
        "meta_block": meta_block,
        "avail_desc": avail_desc,
        "avail_min_desc": f"{avail_min}({_midi_to_name(avail_min)})",
        "avail_max_desc": f"{avail_max}({_midi_to_name(avail_max)})",
        "scale_key": scale_key,
        "optimal_hint": optimal_hint,
        "unmapped_lines": chr(10).join(unmapped_lines),
        "high_freq_hint": high_freq_hint,
        "style_block": style_block,
        "drop_hint": drop_hint,
        "simplify_block": simplify_block,
    })


def _snap_replacement(note: int, available: list[int]) -> int:
    """Snap an invalid replacement to the nearest available note."""
    best = available[0]
    best_dist = abs(note - best)
    for n in available:
        d = abs(note - n)
        if d < best_dist:
            best = n
            best_dist = d
    return best


def validate_note_map(
    note_map: dict[int, int],
    available_set: set[int],
    available_sorted: list[int],
) -> tuple[dict[int, int], int]:
    """Fix replacements not in the available set. Returns (fixed_map, fix_count)."""
    fixed: dict[int, int] = {}
    fix_count = 0
    for orig, repl in note_map.items():
        if repl == -1 or repl in available_set:
            fixed[orig] = repl
        else:
            fixed[orig] = _snap_replacement(repl, available_sorted)
            fix_count += 1
    return fixed, fix_count


def _redistribute_convergent(
    note_map: dict[int, int],
    available_sorted: list[int],
) -> tuple[dict[int, int], int]:
    """Detect multiple originals converging on the same replacement and spread them.

    When N different original notes (that are adjacent/close in pitch) all map
    to the same replacement R, redistribute them across the N nearest available
    notes around R, preserving the ascending/descending order of the originals.

    Returns (fixed_map, redistribution_count).
    """
    if not note_map or len(available_sorted) < 2:
        return note_map, 0

    from collections import defaultdict
    repl_to_originals: dict[int, list[int]] = defaultdict(list)
    for orig, repl in note_map.items():
        if repl == -1:
            continue
        repl_to_originals[repl].append(orig)

    fixed = dict(note_map)
    total_redistributed = 0

    used_replacements = set(fixed.values()) - {-1}

    for repl, originals in repl_to_originals.items():
        if len(originals) < 2:
            continue

        originals_sorted = sorted(originals)

        max_gap = max(
            originals_sorted[i + 1] - originals_sorted[i]
            for i in range(len(originals_sorted) - 1)
        )
        if max_gap > 12:
            continue

        n = len(originals_sorted)
        repl_idx = -1
        for i, note in enumerate(available_sorted):
            if note == repl:
                repl_idx = i
                break
        if repl_idx < 0:
            continue

        ascending = originals_sorted[-1] >= originals_sorted[0]

        candidates: list[int] = []
        left = repl_idx - 1
        right = repl_idx + 1
        candidates.append(repl)

        while len(candidates) < n:
            pick_left = left >= 0
            pick_right = right < len(available_sorted)
            if not pick_left and not pick_right:
                break
            if pick_right and (not pick_left or (available_sorted[right] - repl) <= (repl - available_sorted[left])):
                candidates.append(available_sorted[right])
                right += 1
            elif pick_left:
                candidates.append(available_sorted[left])
                left -= 1

        candidates.sort()

        if len(candidates) < n:
            continue

        if len(candidates) > n:
            center_idx = candidates.index(repl)
            half = n // 2
            start = max(0, center_idx - half)
            end = start + n
            if end > len(candidates):
                end = len(candidates)
                start = end - n
            candidates = candidates[start:end]

        if ascending:
            for orig, cand in zip(originals_sorted, candidates):
                if fixed[orig] != cand:
                    fixed[orig] = cand
                    total_redistributed += 1
        else:
            for orig, cand in zip(originals_sorted, reversed(candidates)):
                if fixed[orig] != cand:
                    fixed[orig] = cand
                    total_redistributed += 1

    return fixed, total_redistributed


def validate_position_map(
    position_map: list[PositionRemap],
    available_set: set[int],
    available_sorted: list[int],
) -> tuple[list[PositionRemap], int]:
    """Fix replacements not in the available set. Returns (fixed_list, fix_count)."""
    fixed: list[PositionRemap] = []
    fix_count = 0
    for pr in position_map:
        if pr.replacement == -1 or pr.replacement in available_set:
            fixed.append(pr)
        else:
            corrected = _snap_replacement(pr.replacement, available_sorted)
            fixed.append(PositionRemap(pr.time_ms, pr.original, corrected))
            fix_count += 1
    return fixed, fix_count


def _extract_balanced(text: str, open_ch: str, close_ch: str) -> str | None:
    """Extract the first balanced block delimited by *open_ch*/*close_ch*.

    Handles nested braces and skips characters inside JSON string literals.
    """
    start = text.find(open_ch)
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text[start:]


def parse_remap_response(response: str) -> dict[int, int]:
    text = _extract_clean_text(response)
    block = _extract_balanced(text, "{", "}")
    if block:
        text = block
    else:
        idx = text.find("{")
        if idx >= 0:
            text = text[idx:]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        text = _repair_truncated_json(text)
        data = json.loads(text)
    return {int(k): int(v) for k, v in data.items()}


# ── Context mode ────────────────────────────────────────────


@dataclass(slots=True)
class _GroupedNote:
    final_note: int
    duration_ms: int
    velocity: int
    status: str


@dataclass(slots=True)
class _NoteGroup:
    """Notes sounding at approximately the same time."""
    time_ms: int
    notes: list[_GroupedNote]


def _group_threshold_ms(meta: MidiMeta | None) -> int:
    if meta and meta.bpm > 0:
        beat_ms = 60_000 / meta.bpm
        return int(min(max(beat_ms * 0.08, 20), 60))
    return 30


def _group_notes_by_time(
    events: list[RawMidiEvent],
    shift: int,
    available_set: set[int],
    meta: MidiMeta | None = None,
    threshold_ms: int | None = None,
) -> list[_NoteGroup]:
    """Group simultaneous notes together (within threshold_ms)."""
    if not events:
        return []
    threshold = _group_threshold_ms(meta) if threshold_ms is None else threshold_ms
    groups: list[_NoteGroup] = []
    current = _NoteGroup(time_ms=events[0].time_ms, notes=[])
    for ev in events:
        final = ev.note + shift
        status = "OK" if final in available_set else "UNMAPPED"
        if ev.time_ms - current.time_ms > threshold:
            if current.notes:
                groups.append(current)
            current = _NoteGroup(time_ms=ev.time_ms, notes=[])
        current.notes.append(
            _GroupedNote(
                final_note=final,
                duration_ms=ev.duration_ms,
                velocity=ev.velocity,
                status=status,
            )
        )
    if current.notes:
        groups.append(current)
    return groups


def _choose_melody_note(group: _NoteGroup, previous_melody: int | None) -> int:
    best_note = group.notes[0].final_note
    best_score = float("-inf")
    for note in group.notes:
        score = float(note.final_note)
        score += min(note.velocity, 127) / 8.0
        score += min(note.duration_ms, 1200) / 150.0
        if previous_melody is not None:
            distance = abs(note.final_note - previous_melody)
            if distance <= 5:
                score += 5.0
            elif distance <= 12:
                score += 2.0
            elif distance >= 19:
                score -= 3.0
        if score > best_score:
            best_score = score
            best_note = note.final_note
    return best_note


def _format_grouped_sequence(
    groups: list[_NoteGroup],
    meta: MidiMeta | None,
    initial_previous_melody: int | None = None,
) -> str:
    """Format note groups with bar markers and melody tags."""
    bar_ms = 2000.0
    if meta:
        try:
            parts = meta.time_signature.split("/")
            beats = int(parts[0])
        except (ValueError, IndexError):
            beats = 4
        if meta.bpm > 0:
            bar_ms = (60_000 / meta.bpm) * beats

    lines: list[str] = []
    current_bar = 0
    previous_melody: int | None = initial_previous_melody
    for g in groups:
        bar_num = int(g.time_ms / bar_ms) + 1
        if bar_num != current_bar:
            current_bar = bar_num
            lines.append(f"\n--- Bar {bar_num} ---")

        melody_note = _choose_melody_note(g, previous_melody)
        previous_melody = melody_note
        parts: list[str] = []
        for grouped in sorted(g.notes, key=lambda x: x.final_note):
            tag = f" [{grouped.status}]"
            if grouped.final_note == melody_note:
                tag += " [MELODY]"
            if grouped.velocity >= 100:
                tag += " [ACCENT]"
            parts.append(
                f"{grouped.final_note}({midi_to_name(grouped.final_note)}) "
                f"dur={grouped.duration_ms}ms vel={grouped.velocity}{tag}"
            )
        lines.append(f"[t={g.time_ms}ms] {' | '.join(parts)}")
    return "\n".join(lines)


def build_context_prompt(
    available: list[int],
    events: list[RawMidiEvent],
    shift: int,
    available_set: set[int],
    style: str = "conservative",
    filename: str = "",
    meta: MidiMeta | None = None,
    key_analysis: MidiKeyAnalysis | None = None,
    optimal_hint: str = "",
    simplify: bool = False,
    continuation_context: str = "",
    initial_previous_melody: int | None = None,
) -> str:
    avail_desc = ", ".join(f"{n}({_midi_to_name(n)})" for n in available)

    groups = _group_notes_by_time(events, shift, available_set, meta=meta)
    sequence_text = _format_grouped_sequence(groups, meta, initial_previous_melody)

    avail_min = min(available)
    avail_max = max(available)
    scale_key = _detect_scale_key(available, key_analysis)
    style_block = _get_style_block(style)

    drop_hint = ""
    if simplify or style in ("balanced", "creative"):
        drop_hint = '\nUse -1 as replacement to drop a note: {{"time_ms": 500, "original": 73, "replacement": -1}}'

    simplify_block = _SIMPLIFY_BLOCK if simplify else ""

    meta_block = _format_midi_meta(meta, filename, key_analysis) if meta else ""

    templates = _load_templates()
    template = templates.get("context_template", "")
    return template.format_map({
        "meta_block": meta_block,
        "avail_desc": avail_desc,
        "avail_min_desc": f"{avail_min}({_midi_to_name(avail_min)})",
        "avail_max_desc": f"{avail_max}({_midi_to_name(avail_max)})",
        "scale_key": scale_key,
        "optimal_hint": optimal_hint,
        "continuation_context": continuation_context,
        "style_block": style_block,
        "drop_hint": drop_hint,
        "simplify_block": simplify_block,
        "sequence_text": sequence_text,
    })


def parse_context_response(response: str) -> list[PositionRemap]:
    text = _extract_clean_text(response)
    block = _extract_balanced(text, "[", "]")
    if block:
        text = block
    else:
        idx = text.find("[")
        if idx >= 0:
            text = text[idx:]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        text = _repair_truncated_json(text)
        data = json.loads(text)
    result: list[PositionRemap] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if "time_ms" not in item or "original" not in item or "replacement" not in item:
            continue
        result.append(PositionRemap(
            time_ms=int(item["time_ms"]),
            original=int(item["original"]),
            replacement=int(item["replacement"]),
        ))
    return result


# ── Extract mode (melody/accompaniment/bass classification) ─


@dataclass(slots=True)
class RoleClassification:
    time_ms: int
    note: int
    role: str  # "melody" | "accompaniment" | "bass"


def _format_extract_sequence(
    groups: list[_NoteGroup],
    meta: MidiMeta | None,
    initial_previous_melody: int | None = None,
) -> str:
    """Format note groups with bar markers and LIKELY_MELODY tags for extract mode."""
    bar_ms = 2000.0
    if meta:
        try:
            parts = meta.time_signature.split("/")
            beats = int(parts[0])
        except (ValueError, IndexError):
            beats = 4
        if meta.bpm > 0:
            bar_ms = (60_000 / meta.bpm) * beats

    lines: list[str] = []
    current_bar = 0
    previous_melody: int | None = initial_previous_melody
    for g in groups:
        bar_num = int(g.time_ms / bar_ms) + 1
        if bar_num != current_bar:
            current_bar = bar_num
            lines.append(f"\n--- Bar {bar_num} ---")

        melody_note = _choose_melody_note(g, previous_melody)
        previous_melody = melody_note
        parts: list[str] = []
        for grouped in sorted(g.notes, key=lambda x: x.final_note):
            tag = ""
            if grouped.final_note == melody_note:
                tag += " [LIKELY_MELODY]"
            if grouped.velocity >= 100:
                tag += " [ACCENT]"
            parts.append(
                f"{grouped.final_note}({midi_to_name(grouped.final_note)}) "
                f"dur={grouped.duration_ms}ms vel={grouped.velocity}{tag}"
            )
        lines.append(f"[t={g.time_ms}ms] {' | '.join(parts)}")
    return "\n".join(lines)


def _group_notes_for_extract(
    events: list[RawMidiEvent],
    meta: MidiMeta | None = None,
    threshold_ms: int | None = None,
) -> list[_NoteGroup]:
    """Group notes for extract mode (no shift / available_set filtering)."""
    if not events:
        return []
    threshold = _group_threshold_ms(meta) if threshold_ms is None else threshold_ms
    groups: list[_NoteGroup] = []
    current = _NoteGroup(time_ms=events[0].time_ms, notes=[])
    for ev in events:
        if ev.time_ms - current.time_ms > threshold:
            if current.notes:
                groups.append(current)
            current = _NoteGroup(time_ms=ev.time_ms, notes=[])
        current.notes.append(
            _GroupedNote(
                final_note=ev.note,
                duration_ms=ev.duration_ms,
                velocity=ev.velocity,
                status="OK",
            )
        )
    if current.notes:
        groups.append(current)
    return groups


def build_extract_prompt(
    events: list[RawMidiEvent],
    filename: str = "",
    meta: MidiMeta | None = None,
    key_analysis: MidiKeyAnalysis | None = None,
    continuation_context: str = "",
    initial_previous_melody: int | None = None,
) -> str:
    groups = _group_notes_for_extract(events, meta=meta)
    sequence_text = _format_extract_sequence(groups, meta, initial_previous_melody)
    meta_block = _format_midi_meta(meta, filename, key_analysis) if meta else ""

    templates = _load_templates()
    template = templates.get("extract_template", "")
    return template.format_map({
        "meta_block": meta_block,
        "continuation_context": continuation_context,
        "sequence_text": sequence_text,
    })


def parse_extract_response(response: str) -> list[RoleClassification]:
    text = _extract_clean_text(response)

    roles_match = re.search(r"##\s*Roles\s*\n", response, re.IGNORECASE)
    if roles_match:
        text = response[roles_match.end():].strip()

    block = _extract_balanced(text, "[", "]")
    if block:
        text = block
    else:
        idx = text.find("[")
        if idx >= 0:
            text = text[idx:]

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        text = _repair_truncated_json(text)
        data = json.loads(text)

    valid_roles = {"melody", "accompaniment", "bass"}
    result: list[RoleClassification] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if "time_ms" not in item or "note" not in item or "role" not in item:
            continue
        role = str(item["role"]).lower()
        if role not in valid_roles:
            role = "accompaniment"
        result.append(RoleClassification(
            time_ms=int(item["time_ms"]),
            note=int(item["note"]),
            role=role,
        ))
    return result


def _parse_extract_ai_response(response: str) -> tuple[str, list[RoleClassification]]:
    """Parse AI extract response into (analysis_text, role_classifications)."""
    roles_match = re.search(r"##\s*Roles\s*\n", response, re.IGNORECASE)
    analysis_match = re.search(r"##\s*Analysis\s*\n", response, re.IGNORECASE)

    analysis_text = ""
    if analysis_match and roles_match:
        analysis_text = response[analysis_match.end():roles_match.start()].strip()
    elif analysis_match:
        analysis_text = response[analysis_match.end():].strip()

    try:
        roles = parse_extract_response(response)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise AiArrangeError(
            "invalid_response",
            "AI 返回的角色分类无法解析，请重试。",
            detail=str(exc),
        ) from exc

    return analysis_text, roles


# ── Shared utilities ────────────────────────────────────────


def parse_analysis_and_json(response: str) -> tuple[str, str]:
    """Split AI response into (analysis_text, json_text).

    Expects ## Analysis / ## Mapping headers. Falls back to extracting
    JSON from the full response if headers are missing.
    """
    mapping_match = re.search(r"##\s*Mapping\s*\n", response, re.IGNORECASE)
    if mapping_match:
        analysis_match = re.search(r"##\s*Analysis\s*\n", response, re.IGNORECASE)
        if analysis_match:
            analysis_text = response[analysis_match.end():mapping_match.start()].strip()
        else:
            analysis_text = response[:mapping_match.start()].strip()
        json_text = response[mapping_match.end():].strip()
        return analysis_text, json_text

    json_start = -1
    for marker in ("[", "{"):
        idx = response.find(marker)
        if idx >= 0 and (json_start < 0 or idx < json_start):
            json_start = idx
    if json_start > 0:
        return response[:json_start].strip(), response[json_start:].strip()

    return "", response.strip()


def parse_ai_response(
    response: str,
    mode: str,
) -> tuple[str, dict[int, int], list[PositionRemap]]:
    analysis_text, json_text = parse_analysis_and_json(response)
    try:
        if mode == "context":
            return analysis_text, {}, parse_context_response(json_text) if json_text else []
        return analysis_text, parse_remap_response(json_text) if json_text else {}, []
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise AiArrangeError(
            "invalid_response",
            "AI 返回内容无法解析，请重试或调整提示词。",
            detail=str(exc),
        ) from exc


def build_retry_prompt(
    original_prompt: str,
    analysis: str,
    user_feedback: str,
) -> str:
    """Build a retry prompt incorporating previous analysis and user feedback."""
    return f"""{original_prompt}

---
PREVIOUS ATTEMPT — your earlier analysis was:
{analysis}

USER FEEDBACK — the user wants these adjustments:
{user_feedback}

Please revise your arrangement based on this feedback. Keep the same two-section format (## Analysis, ## Mapping)."""


def _extract_clean_text(response: str) -> str:
    text = response.strip()
    if not text:
        raise ValueError("AI returned empty response")
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:])
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    return text


def _repair_truncated_json(text: str) -> str:
    """Repair JSON truncated mid-stream by finding the last parseable cut point."""
    text = text.rstrip()
    stripped = text.lstrip()

    if stripped.startswith("["):
        repaired = _repair_json_array(text)
        if repaired is not None:
            return repaired

    if stripped.startswith("{"):
        repaired = _repair_json_object(text)
        if repaired is not None:
            return repaired

    return text


def _repair_json_array(text: str) -> str | None:
    """Find the last complete element in a truncated JSON array."""
    pos = len(text)
    for _ in range(30):
        pos = text.rfind("}", 0, pos)
        if pos < 0:
            break
        candidate = text[: pos + 1].rstrip()
        candidate = re.sub(r",\s*$", "", candidate)
        candidate += "]"
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            continue
    return None


def _repair_json_object(text: str) -> str | None:
    """Find the last complete key-value pair in a truncated JSON object."""
    pos = len(text)
    for _ in range(40):
        comma_pos = text.rfind(",", 0, pos)
        if comma_pos < 0:
            break
        candidate = text[:comma_pos].rstrip() + "}"
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pos = comma_pos
            continue

    search_chars = ('"', "}", "]")
    pos = len(text)
    for _ in range(30):
        best = -1
        for ch in search_chars:
            p = text.rfind(ch, 0, pos)
            if p > best:
                best = p
        if best < 0:
            break
        pos = best
        candidate = text[: pos + 1].rstrip()
        candidate = re.sub(r",\s*$", "", candidate)
        candidate += "}"
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            continue
    return None


def _normalize_base_url(url: str) -> str:
    url = url.rstrip("/")
    if not url.endswith("/v1"):
        url += "/v1"
    return url


def _build_context_continuation(
    chunk_positions: list[PositionRemap],
    n_tail: int = 8,
) -> str:
    """Build continuation context from previous chunk's position mappings."""
    if not chunk_positions:
        return ""
    tail = chunk_positions[-n_tail:]
    lines = [
        "=== CONTINUATION FROM PREVIOUS SEGMENT ===",
        "This segment continues from a previous one. The last replacements were:",
    ]
    for pr in tail:
        lines.append(
            f"  [t={pr.time_ms}ms] {pr.original}({midi_to_name(pr.original)}) -> "
            f"{pr.replacement}({midi_to_name(pr.replacement)})"
        )
    lines.append(
        "Maintain melodic and harmonic continuity with these prior mappings. "
        "Do not abruptly change direction or register at the start of this segment."
    )
    return "\n".join(lines)


def _build_extract_continuation(
    chunk_roles: list[RoleClassification],
    n_tail: int = 10,
) -> str:
    """Build continuation context from previous chunk's role classifications."""
    if not chunk_roles:
        return ""
    tail = sorted(chunk_roles[-n_tail:], key=lambda r: r.time_ms)
    lines = [
        "=== CONTINUATION FROM PREVIOUS SEGMENT ===",
        "This segment continues from a previous one. The last role classifications were:",
    ]
    current_time: int | None = None
    parts: list[str] = []
    for r in tail:
        if current_time is not None and r.time_ms != current_time:
            lines.append(f"  [t={current_time}ms] {' | '.join(parts)}")
            parts = []
        current_time = r.time_ms
        parts.append(f"{r.note}({midi_to_name(r.note)})={r.role}")
    if parts and current_time is not None:
        lines.append(f"  [t={current_time}ms] {' | '.join(parts)}")
    lines.append(
        "Maintain role assignment continuity with the previous segment. "
        "Do not abruptly change which voice carries the melody."
    )
    return "\n".join(lines)


def _compute_final_melody(
    events: list[RawMidiEvent],
    shift: int,
    available_set: set[int],
    meta: MidiMeta | None,
    initial_previous_melody: int | None = None,
) -> int | None:
    """Walk through groups and return the last chosen melody note (context mode)."""
    groups = _group_notes_by_time(events, shift, available_set, meta=meta)
    prev = initial_previous_melody
    for g in groups:
        prev = _choose_melody_note(g, prev)
    return prev


def _compute_final_melody_extract(
    events: list[RawMidiEvent],
    meta: MidiMeta | None,
    initial_previous_melody: int | None = None,
) -> int | None:
    """Walk through groups and return the last chosen melody note (extract mode)."""
    groups = _group_notes_for_extract(events, meta=meta)
    prev = initial_previous_melody
    for g in groups:
        prev = _choose_melody_note(g, prev)
    return prev


_CONTEXT_TOKEN_THRESHOLD = 48_000
_CHARS_PER_TOKEN = 3.5
_BARS_PER_CHUNK = 100
_OVERLAP_BARS = 4


def _estimate_tokens(text: str) -> int:
    return int(len(text) / _CHARS_PER_TOKEN)


def _raise_if_cancelled(should_cancel: Callable[[], bool] | None) -> None:
    if should_cancel and should_cancel():
        raise AiArrangeCancelled("AI 编曲已取消")


def _describe_exception(exc: Exception) -> str:
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def get_arrange_precheck(
    midi_path: Path,
    mapping: MappingConfig,
    profile_id: str,
    transpose: int = 0,
    octave: int = 0,
    tracks: list[int] | None = None,
    mode: str = "remap",
    style: str = "conservative",
    simplify: bool = False,
) -> ArrangePrecheck:
    available = get_available_notes(mapping, profile_id)
    shift = get_shift(mapping, profile_id, transpose, octave)
    likely_key = _detect_scale_key(available)
    if not available:
        return ArrangePrecheck(shift=shift)

    if mode == "extract":
        events, _, _ = read_midi_events(midi_path, tracks=tracks)
        meta = read_midi_meta(midi_path, tracks=tracks)
        key_analysis = analyze_midi_key(midi_path, tracks=tracks)
        requires_chunking = _count_total_bars(events, meta) > _BARS_PER_CHUNK
        prompt = build_extract_prompt(events, filename=midi_path.stem, meta=meta, key_analysis=key_analysis)
        estimated = _estimate_tokens(prompt)
        return ArrangePrecheck(
            available_notes=available,
            shift=shift,
            estimated_tokens=estimated,
            requires_chunking=requires_chunking,
            likely_key=_detect_scale_key(available, key_analysis),
        )

    if mode != "context":
        return ArrangePrecheck(
            available_notes=available,
            shift=shift,
            likely_key=likely_key,
        )

    events, _, _ = read_midi_events(midi_path, tracks=tracks)
    meta = read_midi_meta(midi_path, tracks=tracks)
    key_analysis = analyze_midi_key(midi_path, tracks=tracks)
    if _requires_context_chunking_by_bars(events, meta):
        chunks = _split_events_into_chunks(events, meta)
        estimated = _estimate_chunked_context_tokens(
            chunks or [events],
            available,
            shift,
            midi_path.stem,
            meta,
            key_analysis,
            style=style,
            simplify=simplify,
        )
        return ArrangePrecheck(
            available_notes=available,
            shift=shift,
            estimated_tokens=estimated,
            requires_chunking=True,
            likely_key=_detect_scale_key(available, key_analysis),
        )
    prompt = build_context_prompt(
        available,
        events,
        shift,
        set(available),
        style=style,
        filename=midi_path.stem,
        meta=meta,
        key_analysis=key_analysis,
        simplify=simplify,
    )
    estimated = _estimate_tokens(prompt)
    return ArrangePrecheck(
        available_notes=available,
        shift=shift,
        estimated_tokens=estimated,
        requires_chunking=_requires_context_chunking(events, meta, estimated),
        likely_key=_detect_scale_key(available, key_analysis),
    )


def _get_bar_ms(meta: MidiMeta | None) -> float:
    if meta and meta.bpm > 0:
        try:
            parts = meta.time_signature.split("/")
            beats = int(parts[0])
        except (ValueError, IndexError):
            beats = 4
        return (60_000 / meta.bpm) * beats
    return 2000.0


def _count_total_bars(events: list[RawMidiEvent], meta: MidiMeta | None) -> int:
    if not events:
        return 0
    bar_ms = _get_bar_ms(meta)
    return int(events[-1].time_ms / bar_ms) + 1


def _requires_context_chunking(
    events: list[RawMidiEvent],
    meta: MidiMeta | None,
    estimated_tokens: int,
) -> bool:
    if estimated_tokens > _CONTEXT_TOKEN_THRESHOLD:
        return True
    return _count_total_bars(events, meta) > _BARS_PER_CHUNK


def _requires_context_chunking_by_bars(
    events: list[RawMidiEvent],
    meta: MidiMeta | None,
) -> bool:
    return _count_total_bars(events, meta) > _BARS_PER_CHUNK


def _estimate_chunked_context_tokens(
    chunks: list[list[RawMidiEvent]],
    available: list[int],
    shift: int,
    filename: str,
    meta: MidiMeta | None,
    key_analysis: MidiKeyAnalysis | None,
    *,
    style: str,
    simplify: bool,
) -> int:
    available_set = set(available)
    total = 0
    for chunk in chunks:
        prompt = build_context_prompt(
            available,
            chunk,
            shift,
            available_set,
            style=style,
            filename=filename,
            meta=meta,
            key_analysis=key_analysis,
            simplify=simplify,
        )
        total += _estimate_tokens(prompt)
    return total


def _find_phrase_boundary(events: list[RawMidiEvent], target_ms: float, bar_ms: float) -> float:
    if len(events) < 2:
        return target_ms
    search_window = bar_ms * 1.5
    best_gap = 0
    best_boundary: float | None = None
    previous = events[0].time_ms
    for ev in events[1:]:
        if target_ms - search_window <= ev.time_ms <= target_ms + search_window:
            gap = ev.time_ms - previous
            if gap > best_gap:
                best_gap = gap
                best_boundary = float(ev.time_ms)
        previous = ev.time_ms
    if best_boundary is not None and best_gap >= max(120, int(bar_ms * 0.15)):
        return best_boundary
    return target_ms


def _split_events_into_chunks(
    events: list[RawMidiEvent],
    meta: MidiMeta | None,
    bars_per_chunk: int = _BARS_PER_CHUNK,
    overlap_bars: int = _OVERLAP_BARS,
) -> list[list[RawMidiEvent]]:
    """Split events into bar-based chunks with overlap."""
    if not events:
        return []
    bar_ms = _get_bar_ms(meta)
    last_time = events[-1].time_ms
    total_bars = int(last_time / bar_ms) + 1

    if total_bars <= bars_per_chunk:
        return [events]

    chunks: list[list[RawMidiEvent]] = []
    bar_start = 0
    while bar_start < total_bars:
        bar_end = min(bar_start + bars_per_chunk, total_bars)
        t_start = bar_start * bar_ms
        if bar_end >= total_bars:
            t_end = events[-1].time_ms + 1
        else:
            t_end = _find_phrase_boundary(events, bar_end * bar_ms, bar_ms)
        chunk = [ev for ev in events if t_start <= ev.time_ms < t_end]
        if chunk:
            chunks.append(chunk)
        if bar_end >= total_bars:
            break
        next_bar_start = bar_end - overlap_bars
        if next_bar_start <= bar_start:
            next_bar_start = bar_end
        bar_start = next_bar_start
    return chunks


StreamCallback = Callable[[str], None]
CancelCallback = Callable[[], bool]


def _emit_progress(on_chunk: StreamCallback | None, message: str) -> None:
    if on_chunk:
        on_chunk(message)


class CancellableState:
    """Thread-safe handle for force-cancelling an in-flight OpenAI request.

    The worker thread stores the httpx client and stream here via
    ``set_http_client`` / ``set_stream``.  The GUI thread calls
    ``force_close`` to immediately abort the HTTP connection, making
    the cancel responsive even while blocked on network I/O.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._attempt_token: object | None = None
        self._http_client: object | None = None
        self._stream: object | None = None
        self._closed = False

    def set_http_client(self, http_client: object, attempt_token: object | None = None) -> None:
        with self._lock:
            if self._closed:
                try:
                    http_client.close()  # type: ignore[union-attr]
                except Exception:
                    pass
                return
            self._attempt_token = attempt_token
            self._http_client = http_client
            self._stream = None

    def set_stream(self, stream: object, attempt_token: object | None = None) -> None:
        with self._lock:
            if self._closed:
                try:
                    stream.close()  # type: ignore[union-attr]
                except Exception:
                    pass
                return
            if attempt_token is not None and self._attempt_token is not attempt_token:
                try:
                    stream.close()  # type: ignore[union-attr]
                except Exception:
                    pass
                return
            self._stream = stream

    def clear_attempt(self, attempt_token: object | None = None) -> None:
        with self._lock:
            if attempt_token is not None and self._attempt_token is not attempt_token:
                return
            self._attempt_token = None
            self._stream = None
            self._http_client = None

    def force_close(self) -> None:
        with self._lock:
            self._closed = True
            stream = self._stream
            http_client = self._http_client
            self._stream = None
            self._http_client = None
        if stream is not None:
            try:
                stream.close()  # type: ignore[union-attr]
            except Exception:
                pass
        if http_client is not None:
            try:
                http_client.close()  # type: ignore[union-attr]
            except Exception:
                pass


_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2.0
_STREAM_PROGRESS_INTERVAL = 5.0


def _close_resource_quietly(resource: object | None) -> None:
    if resource is None:
        return
    try:
        resource.close()  # type: ignore[union-attr]
    except Exception:
        pass


def _is_retryable(exc: Exception) -> bool:
    try:
        import httpx
    except Exception:
        httpx = None  # type: ignore[assignment]

    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int) and status_code in {408, 409, 429, 500, 502, 503, 504}:
        return True
    if httpx and isinstance(
        exc,
        (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
            httpx.ReadError,
            httpx.WriteError,
        ),
    ):
        return True
    cause = exc.__cause__
    if isinstance(cause, Exception) and cause is not exc and _is_retryable(cause):
        return True
    cls_name = type(exc).__name__
    return cls_name in (
        "APIConnectionError", "RateLimitError", "APITimeoutError",
        "InternalServerError",
    )


def _sleep_with_cancel(delay: float, should_cancel: CancelCallback | None) -> None:
    import time

    deadline = time.monotonic() + delay
    while True:
        _raise_if_cancelled(should_cancel)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.2, remaining))


def _format_stream_progress_text(
    accumulated: str,
    wait_sec: int,
    *,
    waiting_for_first_token: bool,
) -> str:
    if waiting_for_first_token:
        return f"[Waiting for AI response... {wait_sec}s]"
    return f"{accumulated}\n\n[Streaming paused... {wait_sec}s since last update]"


def call_openai(
    api_key: str,
    prompt: str,
    base_url: str | None = None,
    model: str = "gpt-4o-mini",
    on_chunk: StreamCallback | None = None,
    max_tokens: int = 16384,
    should_cancel: CancelCallback | None = None,
    cancel_state: CancellableState | None = None,
) -> str:
    """Call OpenAI-compatible API with streaming.

    Uses a daemon thread for the actual HTTP request so that the caller
    can poll a queue with short timeouts, guaranteeing that cancellation
    is detected within ~0.5 s even while the network I/O is blocking.
    """
    import time

    import httpx
    from openai import OpenAI, BadRequestError

    normalized_url = _normalize_base_url(base_url) if base_url else None

    create_kwargs: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": max_tokens,
        "stream": True,
    }

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        _raise_if_cancelled(should_cancel)

        attempt_token = object()
        http_client = httpx.Client(timeout=httpx.Timeout(3000, connect=30))
        if cancel_state:
            cancel_state.set_http_client(http_client, attempt_token)
        client = OpenAI(
            api_key=api_key,
            base_url=normalized_url,
            http_client=http_client,
        )

        q: _queue.Queue[tuple[str, object]] = _queue.Queue()
        stream_holder: dict[str, object | None] = {"stream": None}

        def _cleanup_attempt() -> None:
            if cancel_state:
                cancel_state.clear_attempt(attempt_token)
            stream = stream_holder["stream"]
            stream_holder["stream"] = None
            _close_resource_quietly(stream)
            _close_resource_quietly(http_client)
            api_thread.join(timeout=1.0)

        def _api_call(
            _client: object = client,
            _kw: dict = create_kwargs,
            _q: _queue.Queue = q,
            _cs: CancellableState | None = cancel_state,
            _attempt_token: object = attempt_token,
            _stream_holder: dict[str, object | None] = stream_holder,
        ) -> None:
            try:
                try:
                    stream = _client.chat.completions.create(**_kw)  # type: ignore[union-attr]
                except BadRequestError:
                    fallback_kw = dict(_kw)
                    fallback_kw.pop("max_tokens", None)
                    stream = _client.chat.completions.create(**fallback_kw)  # type: ignore[union-attr]
                _stream_holder["stream"] = stream
                if _cs:
                    _cs.set_stream(stream, _attempt_token)
                for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        _q.put(("data", chunk.choices[0].delta.content))
                _q.put(("done", None))
            except Exception as exc:
                _q.put(("error", exc))

        api_thread = threading.Thread(target=_api_call, daemon=True)
        api_thread.start()

        try:
            parts: list[str] = []
            accumulated = ""
            started_at = time.monotonic()
            last_activity_at = started_at
            has_received_token = False
            last_progress_mark = 0
            while True:
                try:
                    msg_type, payload = q.get(timeout=0.5)
                except _queue.Empty:
                    _raise_if_cancelled(should_cancel)
                    if on_chunk:
                        now = time.monotonic()
                        idle_since = started_at if not has_received_token else last_activity_at
                        idle_sec = int(now - idle_since)
                        progress_mark = int(idle_sec // _STREAM_PROGRESS_INTERVAL)
                        if progress_mark > 0 and progress_mark != last_progress_mark:
                            last_progress_mark = progress_mark
                            on_chunk(
                                _format_stream_progress_text(
                                    accumulated,
                                    idle_sec,
                                    waiting_for_first_token=not has_received_token,
                                )
                            )
                    continue

                if msg_type == "data":
                    text = str(payload)
                    parts.append(text)
                    has_received_token = True
                    last_activity_at = time.monotonic()
                    last_progress_mark = 0
                    if on_chunk:
                        accumulated += text
                        on_chunk(accumulated)
                elif msg_type == "done":
                    break
                elif msg_type == "error":
                    raise payload  # type: ignore[misc]

            content = accumulated if accumulated else "".join(parts)
            if not content:
                raise ValueError("AI returned empty content (streaming)")
            return content
        except AiArrangeCancelled:
            logger.info("AI arrangement cancelled during streaming")
            raise
        except Exception as exc:
            if should_cancel and should_cancel():
                raise AiArrangeCancelled("AI 编曲已取消") from exc
            logger.warning("OpenAI request attempt %s failed: %s", attempt + 1, _describe_exception(exc))
            if not _is_retryable(exc) or attempt >= _MAX_RETRIES - 1:
                if isinstance(exc, BadRequestError):
                    raise AiArrangeError(
                        "bad_request",
                        "AI 请求被服务端拒绝，请检查模型配置或缩短上下文。",
                        detail=_describe_exception(exc),
                    ) from exc
                raise AiArrangeError(
                    "request_failed",
                    f"AI 请求失败：{type(exc).__name__}",
                    detail=_describe_exception(exc),
                ) from exc
            last_exc = exc
            delay = _RETRY_BASE_DELAY * (2 ** attempt)
            if on_chunk:
                on_chunk(f"[Retry {attempt + 1}/{_MAX_RETRIES} after {type(exc).__name__}, waiting {delay:.0f}s...]")
            _sleep_with_cancel(delay, should_cancel)
        finally:
            _cleanup_attempt()

    raise last_exc  # type: ignore[misc]


# ── Main entry ──────────────────────────────────────────────


def _find_directional_candidate(
    current: int,
    previous: int,
    original_delta: int,
    available_sorted: list[int],
    blocked: set[int],
) -> int | None:
    candidates: list[tuple[int, int, int]] = []
    for candidate in available_sorted:
        if candidate in blocked:
            continue
        candidate_delta = candidate - previous
        if original_delta > 0 and candidate_delta <= 0:
            continue
        if original_delta < 0 and candidate_delta >= 0:
            continue
        candidates.append(
            (
                abs(candidate - current),
                abs(abs(candidate_delta) - abs(original_delta)),
                candidate,
            )
        )
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][2]


def _find_non_clashing_replacement(
    original: int,
    current: int,
    available_sorted: list[int],
    occupied: set[int],
) -> int:
    candidates = sorted(
        available_sorted,
        key=lambda candidate: (abs(candidate - current), abs(candidate - original), candidate),
    )
    for candidate in candidates:
        if candidate in occupied:
            continue
        if any(abs(candidate - other) == 1 for other in occupied):
            continue
        return candidate
    return current


def _enforce_context_rules(
    position_map: list[PositionRemap],
    events: list[RawMidiEvent],
    shift: int,
    available_set: set[int],
    available_sorted: list[int],
    meta: MidiMeta | None,
) -> tuple[list[PositionRemap], int]:
    if not position_map:
        return position_map, 0

    groups = _group_notes_by_time(events, shift, available_set, meta=meta)
    replacements = {(pr.time_ms, pr.original): pr.replacement for pr in position_map}
    adjustments = 0
    previous_melody_original: int | None = None
    previous_melody_replacement: int | None = None

    for group in groups:
        melody_original = _choose_melody_note(group, previous_melody_original)
        effective: dict[int, int] = {}
        for note in group.notes:
            key = (group.time_ms, note.final_note)
            if key in replacements:
                effective[note.final_note] = replacements[key]
            elif note.final_note in available_set:
                effective[note.final_note] = note.final_note

        melody_replacement = effective.get(melody_original)
        melody_key = (group.time_ms, melody_original)
        if melody_replacement is not None and melody_replacement != -1:
            if previous_melody_original is not None and previous_melody_replacement is not None:
                original_delta = melody_original - previous_melody_original
                replacement_delta = melody_replacement - previous_melody_replacement
                if (
                    original_delta != 0
                    and replacement_delta != 0
                    and ((original_delta > 0 > replacement_delta) or (original_delta < 0 < replacement_delta))
                ):
                    candidate = _find_directional_candidate(
                        melody_replacement,
                        previous_melody_replacement,
                        original_delta,
                        available_sorted,
                        {
                            repl
                            for note, repl in effective.items()
                            if note != melody_original and repl != -1
                        },
                    )
                    if candidate is not None and candidate != melody_replacement and melody_key in replacements:
                        replacements[melody_key] = candidate
                        effective[melody_original] = candidate
                        melody_replacement = candidate
                        adjustments += 1

            previous_melody_original = melody_original
            previous_melody_replacement = melody_replacement

        convergent_groups: dict[int, list[int]] = {}
        for note_val, repl_val in effective.items():
            if repl_val != -1 and (group.time_ms, note_val) in replacements:
                convergent_groups.setdefault(repl_val, []).append(note_val)

        for conv_repl, conv_originals in convergent_groups.items():
            if len(conv_originals) < 2:
                continue
            conv_originals_sorted = sorted(conv_originals)
            repl_idx = -1
            for idx_i, av_note in enumerate(available_sorted):
                if av_note == conv_repl:
                    repl_idx = idx_i
                    break
            if repl_idx < 0:
                continue
            other_used = {
                r for o, r in effective.items()
                if o not in conv_originals and r != -1
            }
            cands: list[int] = []
            left_i = repl_idx
            right_i = repl_idx + 1
            while len(cands) < len(conv_originals):
                pick_l = left_i >= 0
                pick_r = right_i < len(available_sorted)
                if not pick_l and not pick_r:
                    break
                if pick_l and available_sorted[left_i] not in other_used:
                    cands.append(available_sorted[left_i])
                    left_i -= 1
                elif pick_l:
                    left_i -= 1
                    continue
                if len(cands) >= len(conv_originals):
                    break
                if pick_r and available_sorted[right_i] not in other_used:
                    cands.append(available_sorted[right_i])
                    right_i += 1
                elif pick_r:
                    right_i += 1
            cands.sort()
            if len(cands) >= len(conv_originals):
                cands = cands[:len(conv_originals)]
                for orig_v, cand_v in zip(conv_originals_sorted, cands):
                    conv_key = (group.time_ms, orig_v)
                    if conv_key in replacements and effective[orig_v] != cand_v:
                        replacements[conv_key] = cand_v
                        effective[orig_v] = cand_v
                        adjustments += 1

        for note in sorted(group.notes, key=lambda item: (item.final_note == melody_original, item.final_note)):
            key = (group.time_ms, note.final_note)
            replacement = effective.get(note.final_note)
            if replacement is None or replacement == -1:
                continue
            occupied = {
                repl
                for original, repl in effective.items()
                if original != note.final_note and repl != -1
            }
            if any(abs(replacement - other) == 1 for other in occupied):
                candidate = _find_non_clashing_replacement(
                    note.final_note,
                    replacement,
                    available_sorted,
                    occupied,
                )
                if candidate != replacement and key in replacements:
                    replacements[key] = candidate
                    effective[note.final_note] = candidate
                    adjustments += 1

    fixed = [
        PositionRemap(pr.time_ms, pr.original, replacements.get((pr.time_ms, pr.original), pr.replacement))
        for pr in position_map
    ]
    return fixed, adjustments


def _ai_arrange_extract(
    midi_path: Path,
    *,
    tracks: list[int] | None = None,
    api_key: str,
    base_url: str | None = None,
    model: str = "gpt-4o-mini",
    on_chunk: StreamCallback | None = None,
    should_cancel: CancelCallback | None = None,
    cancel_state: CancellableState | None = None,
) -> AiArrangeResult:
    """Run extract mode: classify notes as melody/accompaniment/bass."""
    raw_events, _, _ = read_midi_events(midi_path, tracks=tracks)
    _raise_if_cancelled(should_cancel)

    if not raw_events:
        return AiArrangeResult(mode="extract", explanation="No notes found.")

    _emit_progress(on_chunk, "[Stage analyze_key]")
    key_analysis = analyze_midi_key(midi_path, tracks=tracks)
    meta = read_midi_meta(midi_path, tracks=tracks)
    _raise_if_cancelled(should_cancel)

    midi_filename = midi_path.stem
    _emit_progress(on_chunk, "[Stage build_prompt]")
    prompt = build_extract_prompt(
        raw_events,
        filename=midi_filename,
        meta=meta,
        key_analysis=key_analysis,
    )
    _raise_if_cancelled(should_cancel)

    estimated = _estimate_tokens(prompt)
    requires_chunking = _count_total_bars(raw_events, meta) > _BARS_PER_CHUNK

    if not requires_chunking:
        raw = call_openai(
            api_key, prompt, base_url=base_url, model=model,
            on_chunk=on_chunk, max_tokens=65536,
            should_cancel=should_cancel, cancel_state=cancel_state,
        )
        analysis_text, roles = _parse_extract_ai_response(raw)
    else:
        chunks = _split_events_into_chunks(raw_events, meta)
        _emit_progress(on_chunk, f"[Stage split_chunks total={len(chunks)}]")
        all_roles: list[RoleClassification] = []
        all_raw_parts: list[str] = []
        analysis_text = ""
        role_index: dict[tuple[int, int], int] = {}
        carry_melody: int | None = None
        continuation_ctx = ""

        for ci, chunk_events in enumerate(chunks):
            _raise_if_cancelled(should_cancel)
            chunk_prompt = build_extract_prompt(
                chunk_events,
                filename=midi_filename,
                meta=meta,
                key_analysis=key_analysis,
                continuation_context=continuation_ctx,
                initial_previous_melody=carry_melody,
            )
            if ci == 0 and not prompt:
                prompt = chunk_prompt
            chunk_label = f"[Chunk {ci + 1}/{len(chunks)}]"
            if on_chunk:
                on_chunk(f"{chunk_label} Processing...\n" + "\n".join(all_raw_parts))

            def _chunk_cb(
                accumulated: str,
                _label: str = chunk_label,
            ) -> None:
                parts = list(all_raw_parts)
                parts.append(f"{_label}\n{accumulated}")
                on_chunk("\n\n".join(parts))

            chunk_raw = call_openai(
                api_key, chunk_prompt, base_url=base_url, model=model,
                on_chunk=_chunk_cb if on_chunk else None,
                max_tokens=65536,
                should_cancel=should_cancel, cancel_state=cancel_state,
            )
            all_raw_parts.append(f"{chunk_label}\n{chunk_raw}")
            chunk_analysis, chunk_roles = _parse_extract_ai_response(chunk_raw)
            if ci == 0 and chunk_analysis:
                analysis_text = chunk_analysis
            for rc in chunk_roles:
                key = (rc.time_ms, rc.note)
                if key in role_index:
                    all_roles[role_index[key]] = rc
                else:
                    role_index[key] = len(all_roles)
                    all_roles.append(rc)
            carry_melody = _compute_final_melody_extract(
                chunk_events, meta, carry_melody,
            )
            continuation_ctx = _build_extract_continuation(chunk_roles)

        roles = all_roles
        raw = "\n\n".join(all_raw_parts)
        if on_chunk:
            on_chunk(raw)
        if len(chunks) > 1:
            analysis_text += f"\n[Processed in {len(chunks)} chunks]"

    role_map: dict[tuple[int, int], str] = {}
    for rc in roles:
        role_map[(rc.time_ms, rc.note)] = rc.role

    counts = Counter(rc.role for rc in roles)
    summary = ", ".join(f"{r}: {c}" for r, c in sorted(counts.items()))
    if analysis_text:
        analysis_text += f"\n[Classification: {summary}]"

    return AiArrangeResult(
        role_map=role_map,
        mode="extract",
        explanation=raw,
        analysis_text=analysis_text,
        prompt=prompt,
        total_notes=len(raw_events),
    )


def ai_arrange(
    midi_path: Path,
    mapping: MappingConfig,
    profile_id: str,
    api_key: str,
    transpose: int = 0,
    octave: int = 0,
    tracks: list[int] | None = None,
    base_url: str | None = None,
    model: str = "gpt-4o-mini",
    mode: str = "remap",
    style: str = "conservative",
    simplify: bool = False,
    on_chunk: StreamCallback | None = None,
    should_cancel: CancelCallback | None = None,
    cancel_state: CancellableState | None = None,
) -> AiArrangeResult:
    _raise_if_cancelled(should_cancel)

    if mode == "extract":
        return _ai_arrange_extract(
            midi_path, tracks=tracks, api_key=api_key, base_url=base_url,
            model=model, on_chunk=on_chunk, should_cancel=should_cancel,
            cancel_state=cancel_state,
        )

    available = get_available_notes(mapping, profile_id)
    if not available:
        return AiArrangeResult(mode=mode, explanation="No available notes in profile.")

    available_set = set(available)
    shift = get_shift(mapping, profile_id, transpose, octave)
    raw_events, _, _ = read_midi_events(midi_path, tracks=tracks)
    _raise_if_cancelled(should_cancel)
    _emit_progress(on_chunk, "[Stage analyze_key]")
    key_analysis = analyze_midi_key(midi_path, tracks=tracks)
    _raise_if_cancelled(should_cancel)

    unmapped_counts: Counter[int] = Counter()
    for ev in raw_events:
        if (ev.note + shift) not in available_set:
            unmapped_counts[ev.note + shift] += 1

    unmapped_unique = sorted(unmapped_counts.keys())
    total_unmapped = sum(unmapped_counts.values())
    _raise_if_cancelled(should_cancel)

    if not unmapped_unique:
        return AiArrangeResult(
            mode=mode,
            unmapped_count=0,
            total_notes=len(raw_events),
            explanation="All notes are already mapped.",
        )

    midi_filename = midi_path.stem
    meta = read_midi_meta(midi_path, tracks=tracks)
    force_chunking_by_bars = mode == "context" and _requires_context_chunking_by_bars(raw_events, meta)

    optimal_hint = ""
    if not force_chunking_by_bars:
        try:
            _emit_progress(on_chunk, "[Stage optimize]")
            opts = find_optimal_settings(
                midi_path,
                mapping,
                profile_id,
                tracks=tracks,
                should_cancel=should_cancel,
            )
            if opts:
                best = opts[0]
                current_unmapped = total_unmapped
                if best.unmapped_count < current_unmapped * 0.7:
                    instr = best.instruments[0].split("/")[0] if best.instruments else ""
                    optimal_hint = (
                        f"\nNOTE: The user's current settings produce {current_unmapped} unmapped note events. "
                        f"An alternative — transpose {best.transpose:+d} with octave {best.octave:+d} "
                        f"(instrument key: {best.key_name}, instrument: {instr}) — "
                        f"would reduce unmapped events to {best.unmapped_count}. "
                        f"You may mention this in your Analysis if the difference is significant.\n"
                    )
        except Exception as exc:
            if isinstance(exc, AiArrangeCancelled):
                raise
            logger.warning("Unable to compute optimal settings: %s", _describe_exception(exc))

    if mode == "context":
        _raise_if_cancelled(should_cancel)
        prompt = ""
        estimated = 0
        requires_chunking = force_chunking_by_bars
        if not force_chunking_by_bars:
            _emit_progress(on_chunk, "[Stage build_prompt]")
            prompt = build_context_prompt(
                available,
                raw_events,
                shift,
                available_set,
                style=style,
                filename=midi_filename,
                meta=meta,
                key_analysis=key_analysis,
                optimal_hint=optimal_hint,
                simplify=simplify,
            )
            estimated = _estimate_tokens(prompt)
            _raise_if_cancelled(should_cancel)
            requires_chunking = _requires_context_chunking(raw_events, meta, estimated)

        if not requires_chunking:
            raw = call_openai(
                api_key,
                prompt,
                base_url=base_url,
                model=model,
                on_chunk=on_chunk,
                max_tokens=65536,
                should_cancel=should_cancel,
                cancel_state=cancel_state,
            )
            analysis_text, _, position_map = parse_ai_response(raw, "context")
        else:
            chunks = _split_events_into_chunks(raw_events, meta)
            _emit_progress(on_chunk, f"[Stage split_chunks total={len(chunks)}]")
            all_positions: list[PositionRemap] = []
            all_raw_parts: list[str] = []
            analysis_text = ""
            position_index: dict[tuple[int, int], int] = {}
            carry_melody: int | None = None
            continuation_ctx = ""
            for ci, chunk_events in enumerate(chunks):
                _raise_if_cancelled(should_cancel)
                chunk_prompt = build_context_prompt(
                    available,
                    chunk_events,
                    shift,
                    available_set,
                    style=style,
                    filename=midi_filename,
                    meta=meta,
                    key_analysis=key_analysis,
                    optimal_hint=optimal_hint,
                    simplify=simplify,
                    continuation_context=continuation_ctx,
                    initial_previous_melody=carry_melody,
                )
                if ci == 0 and not prompt:
                    prompt = chunk_prompt
                chunk_label = f"[Chunk {ci + 1}/{len(chunks)}]"
                if on_chunk:
                    on_chunk(f"{chunk_label} Processing...\n" + "\n".join(all_raw_parts))

                def _chunk_cb(
                    accumulated: str,
                    _label: str = chunk_label,
                ) -> None:
                    parts = list(all_raw_parts)
                    parts.append(f"{_label}\n{accumulated}")
                    on_chunk("\n\n".join(parts))

                chunk_raw = call_openai(
                    api_key,
                    chunk_prompt,
                    base_url=base_url,
                    model=model,
                    on_chunk=_chunk_cb if on_chunk else None,
                    max_tokens=65536,
                    should_cancel=should_cancel,
                    cancel_state=cancel_state,
                )
                all_raw_parts.append(f"{chunk_label}\n{chunk_raw}")
                chunk_analysis, _, chunk_positions = parse_ai_response(chunk_raw, "context")
                if ci == 0 and chunk_analysis:
                    analysis_text = chunk_analysis
                for pr in chunk_positions:
                    key = (pr.time_ms, pr.original)
                    if key in position_index:
                        all_positions[position_index[key]] = pr
                    else:
                        position_index[key] = len(all_positions)
                        all_positions.append(pr)
                carry_melody = _compute_final_melody(
                    chunk_events, shift, available_set, meta, carry_melody,
                )
                continuation_ctx = _build_context_continuation(chunk_positions)
            position_map = all_positions
            raw = "\n\n".join(all_raw_parts)
            if on_chunk:
                on_chunk(raw)
            if len(chunks) > 1:
                analysis_text += f"\n[Processed in {len(chunks)} chunks due to sequence length]"

        position_map, fix_count = validate_position_map(position_map, available_set, available)
        position_map, rule_fix_count = _enforce_context_rules(
            position_map,
            raw_events,
            shift,
            available_set,
            available,
            meta,
        )
        if fix_count and analysis_text:
            analysis_text += f"\n[Auto-corrected {fix_count} invalid replacement(s) to nearest available note]"
        if rule_fix_count and analysis_text:
            analysis_text += f"\n[Adjusted {rule_fix_count} context replacement(s) for melody direction / clash avoidance]"
        return AiArrangeResult(
            position_map=position_map,
            mode="context",
            explanation=raw,
            analysis_text=analysis_text,
            prompt=prompt,
            unmapped_count=len(unmapped_unique),
            total_notes=len(raw_events),
        )

    prompt = build_remap_prompt(
        available,
        unmapped_unique,
        unmapped_counts,
        style=style,
        filename=midi_filename,
        meta=meta,
        key_analysis=key_analysis,
        optimal_hint=optimal_hint,
        simplify=simplify,
    )
    _raise_if_cancelled(should_cancel)
    raw = call_openai(
        api_key,
        prompt,
        base_url=base_url,
        model=model,
        on_chunk=on_chunk,
        should_cancel=should_cancel,
        cancel_state=cancel_state,
    )
    analysis_text, note_map, _ = parse_ai_response(raw, "remap")
    note_map, fix_count = validate_note_map(note_map, available_set, available)
    if fix_count and analysis_text:
        analysis_text += f"\n[Auto-corrected {fix_count} invalid replacement(s) to nearest available note]"

    note_map, redist_count = _redistribute_convergent(note_map, available)
    if redist_count and analysis_text:
        analysis_text += f"\n[Redistributed {redist_count} convergent mapping(s) to preserve melodic contour]"

    return AiArrangeResult(
        note_map=note_map,
        mode="remap",
        explanation=raw,
        analysis_text=analysis_text,
        prompt=prompt,
        unmapped_count=len(unmapped_unique),
        total_notes=len(raw_events),
    )
