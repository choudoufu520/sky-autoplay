from __future__ import annotations

import json
import logging
import re
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


def _transpose_to_key_name(transpose: int) -> str:
    """Map a transpose offset to the in-game instrument key name."""
    root_pc = (0 - transpose) % 12
    return f"{_KEY_NAMES[root_pc]} major"


@dataclass(slots=True)
class PositionRemap:
    time_ms: int
    original: int
    replacement: int


@dataclass(slots=True)
class AiArrangeResult:
    note_map: dict[int, int] = field(default_factory=dict)
    position_map: list[PositionRemap] = field(default_factory=list)
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
) -> list[OptimalSetting]:
    """Search all transpose (-6..+5) x octave offset (-3..+1) combinations.

    Returns results sorted by unmapped_count ascending (best first).
    """
    available = _get_available_notes(mapping, profile_id)
    if not available:
        return []

    available_set = set(available)
    raw_events, _, _ = read_midi_events(midi_path, tracks=tracks)
    if not raw_events:
        return []

    results: list[OptimalSetting] = []
    for oct_offset in sorted(INSTRUMENT_GROUPS.keys()):
        instruments = INSTRUMENT_GROUPS[oct_offset]
        for t in range(-6, 6):
            shift = _get_shift(mapping, profile_id, t, oct_offset)
            unmapped = 0
            for ev in raw_events:
                if (ev.note + shift) not in available_set:
                    unmapped += 1
            key_name = _transpose_to_key_name(t)
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
    previous_melody: int | None = None
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
) -> str:
    avail_desc = ", ".join(f"{n}({_midi_to_name(n)})" for n in available)

    groups = _group_notes_by_time(events, shift, available_set, meta=meta)
    sequence_text = _format_grouped_sequence(groups, meta)

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


_CONTEXT_TOKEN_THRESHOLD = 24_000
_CHARS_PER_TOKEN = 3.5
_BARS_PER_CHUNK = 50
_OVERLAP_BARS = 2


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

    if mode != "context":
        return ArrangePrecheck(
            available_notes=available,
            shift=shift,
            likely_key=likely_key,
        )

    events, _, _ = read_midi_events(midi_path, tracks=tracks)
    meta = read_midi_meta(midi_path, tracks=tracks)
    key_analysis = analyze_midi_key(midi_path, tracks=tracks)
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
        requires_chunking=estimated > _CONTEXT_TOKEN_THRESHOLD,
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
        t_end = _find_phrase_boundary(events, bar_end * bar_ms, bar_ms)
        chunk = [ev for ev in events if t_start <= ev.time_ms < t_end]
        if chunk:
            chunks.append(chunk)
        bar_start = bar_end - overlap_bars
        if bar_start >= total_bars:
            break
    return chunks


StreamCallback = Callable[[str], None]
CancelCallback = Callable[[], bool]


_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2.0


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


def call_openai(
    api_key: str,
    prompt: str,
    base_url: str | None = None,
    model: str = "gpt-4o-mini",
    on_chunk: StreamCallback | None = None,
    max_tokens: int = 16384,
    should_cancel: CancelCallback | None = None,
) -> str:
    import httpx
    from openai import OpenAI, BadRequestError

    normalized_url = _normalize_base_url(base_url) if base_url else None
    client = OpenAI(
        api_key=api_key,
        base_url=normalized_url,
        http_client=httpx.Client(timeout=httpx.Timeout(3000, connect=30)),
    )

    create_kwargs: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": max_tokens,
        "stream": True,
    }

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            _raise_if_cancelled(should_cancel)
            try:
                stream = client.chat.completions.create(**create_kwargs)
            except BadRequestError:
                kw = dict(create_kwargs)
                kw.pop("max_tokens", None)
                stream = client.chat.completions.create(**kw)

            parts: list[str] = []
            accumulated = ""
            for chunk in stream:
                _raise_if_cancelled(should_cancel)
                if chunk.choices and chunk.choices[0].delta.content:
                    text = chunk.choices[0].delta.content
                    parts.append(text)
                    if on_chunk:
                        accumulated += text
                        on_chunk(accumulated)

            content = accumulated if accumulated else "".join(parts)
            if not content:
                raise ValueError("AI returned empty content (streaming)")
            return content
        except AiArrangeCancelled:
            logger.info("AI arrangement cancelled during streaming")
            raise
        except Exception as exc:
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
) -> AiArrangeResult:
    _raise_if_cancelled(should_cancel)
    available = get_available_notes(mapping, profile_id)
    if not available:
        return AiArrangeResult(mode=mode, explanation="No available notes in profile.")

    available_set = set(available)
    shift = get_shift(mapping, profile_id, transpose, octave)
    raw_events, _, _ = read_midi_events(midi_path, tracks=tracks)
    key_analysis = analyze_midi_key(midi_path, tracks=tracks)

    unmapped_counts: Counter[int] = Counter()
    for ev in raw_events:
        if (ev.note + shift) not in available_set:
            unmapped_counts[ev.note + shift] += 1

    unmapped_unique = sorted(unmapped_counts.keys())
    total_unmapped = sum(unmapped_counts.values())

    if not unmapped_unique:
        return AiArrangeResult(
            mode=mode,
            unmapped_count=0,
            total_notes=len(raw_events),
            explanation="All notes are already mapped.",
        )

    midi_filename = midi_path.stem
    meta = read_midi_meta(midi_path, tracks=tracks)

    optimal_hint = ""
    try:
        opts = find_optimal_settings(midi_path, mapping, profile_id, tracks=tracks)
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
        logger.warning("Unable to compute optimal settings: %s", _describe_exception(exc))

    if mode == "context":
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

        if estimated <= _CONTEXT_TOKEN_THRESHOLD:
            raw = call_openai(
                api_key,
                prompt,
                base_url=base_url,
                model=model,
                on_chunk=on_chunk,
                max_tokens=65536,
                should_cancel=should_cancel,
            )
            analysis_text, _, position_map = parse_ai_response(raw, "context")
        else:
            chunks = _split_events_into_chunks(raw_events, meta)
            all_positions: list[PositionRemap] = []
            all_raw_parts: list[str] = []
            analysis_text = ""
            seen_positions: set[tuple[int, int]] = set()
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
                    optimal_hint="" if ci > 0 else optimal_hint,
                    simplify=simplify,
                )
                chunk_label = f"[Chunk {ci + 1}/{len(chunks)}]"
                if on_chunk:
                    on_chunk(f"{chunk_label} Processing...\n" + "\n".join(all_raw_parts))
                chunk_raw = call_openai(
                    api_key,
                    chunk_prompt,
                    base_url=base_url,
                    model=model,
                    on_chunk=None,
                    max_tokens=65536,
                    should_cancel=should_cancel,
                )
                all_raw_parts.append(f"{chunk_label}\n{chunk_raw}")
                chunk_analysis, _, chunk_positions = parse_ai_response(chunk_raw, "context")
                if ci == 0 and chunk_analysis:
                    analysis_text = chunk_analysis
                for pr in chunk_positions:
                    key = (pr.time_ms, pr.original)
                    if key not in seen_positions:
                        seen_positions.add(key)
                        all_positions.append(pr)
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
    raw = call_openai(
        api_key,
        prompt,
        base_url=base_url,
        model=model,
        on_chunk=on_chunk,
        should_cancel=should_cancel,
    )
    analysis_text, note_map, _ = parse_ai_response(raw, "remap")
    note_map, fix_count = validate_note_map(note_map, available_set, available)
    if fix_count and analysis_text:
        analysis_text += f"\n[Auto-corrected {fix_count} invalid replacement(s) to nearest available note]"

    return AiArrangeResult(
        note_map=note_map,
        mode="remap",
        explanation=raw,
        analysis_text=analysis_text,
        prompt=prompt,
        unmapped_count=len(unmapped_unique),
        total_notes=len(raw_events),
    )
