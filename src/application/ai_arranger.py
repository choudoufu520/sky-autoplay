from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from src.domain.mapping import MappingConfig
from src.infrastructure.midi_reader import MidiMeta, RawMidiEvent, read_midi_events, read_midi_meta

_PITCH_CLASSES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]



def _midi_to_name(n: int) -> str:
    return f"{_PITCH_CLASSES[n % 12]}{n // 12 - 1}"


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


def _get_available_notes(mapping: MappingConfig, profile_id: str) -> list[int]:
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


def _get_shift(mapping: MappingConfig, profile_id: str, transpose: int, octave: int) -> int:
    profile = mapping.profiles.get(profile_id)
    if profile is None:
        return 0
    return profile.transpose_semitones + transpose + (profile.octave_shift + octave) * 12


def analyze_unmapped_notes(
    midi_path: Path,
    mapping: MappingConfig,
    profile_id: str,
    transpose: int = 0,
    octave: int = 0,
    single_track: int | None = None,
) -> tuple[list[int], list[int], Counter[int]]:
    """Return (available_notes, unmapped_notes, unmapped_counts)."""
    available = _get_available_notes(mapping, profile_id)
    if not available:
        return [], [], Counter()

    raw_events, _, _ = read_midi_events(midi_path, single_track=single_track)
    shift = _get_shift(mapping, profile_id, transpose, octave)

    unmapped_counts: Counter[int] = Counter()
    for ev in raw_events:
        final = ev.note + shift
        if final not in available:
            unmapped_counts[final] += 1

    unmapped = sorted(unmapped_counts.keys())
    return available, unmapped, unmapped_counts


# ── Remap mode ──────────────────────────────────────────────


def _format_midi_meta(meta: MidiMeta, filename: str) -> str:
    parts: list[str] = []
    if filename:
        parts.append(f"MIDI file: {filename}")
    parts.append(f"BPM: {meta.bpm}, Time signature: {meta.time_signature}")
    if meta.key_signature:
        parts.append(f"Key signature (from MIDI): {meta.key_signature}")
    parts.append(f"Total notes: {meta.total_notes}, Duration: {meta.duration_sec}s")
    header = "\n".join(parts)
    song_hint = (
        "\nIf you can identify the song from the filename or musical features, "
        "use your knowledge of the original piece to make better arrangement decisions."
        if filename else ""
    )
    return f"\n{header}{song_hint}\n"


_STYLE_INSTRUCTIONS = {
    "conservative": "",
    "balanced": """
ADDITIONAL FREEDOM — You MAY use -1 as the replacement value to DROP a note entirely. Use this when:
- The note is an ornamental/passing tone with no good available replacement
- It is an octave doubling of another note already in the chord
- Removing it does not break the core melody line
Only drop notes that are clearly non-essential. The main melody must remain intact.""",
    "creative": """
CREATIVE FREEDOM — You MAY use -1 as the replacement value to DROP a note. You have full artistic freedom:
- Drop non-essential notes freely to produce a cleaner arrangement
- Simplify dense chords to root + melody note only
- Simplify fast ornamental runs to their key structural notes
- Prioritize overall musicality and emotional impact on this limited instrument over note-for-note fidelity
- It is better to have a clean, musical arrangement with fewer notes than a cluttered one trying to keep everything""",
}


def build_remap_prompt(
    available: list[int],
    unmapped: list[int],
    unmapped_counts: Counter[int],
    style: str = "conservative",
    filename: str = "",
    meta: MidiMeta | None = None,
) -> str:
    avail_desc = ", ".join(f"{n}({_midi_to_name(n)})" for n in available)
    unmapped_lines = []
    for n in unmapped:
        unmapped_lines.append(f"  - {n} ({_midi_to_name(n)}), appears {unmapped_counts[n]} times")

    avail_min = min(available)
    avail_max = max(available)
    style_block = _STYLE_INSTRUCTIONS.get(style, "")

    drop_hint = ""
    if style in ("balanced", "creative"):
        drop_hint = '\nUse -1 as the value to drop a note: {{"61": 60, "73": -1}}'

    meta_block = _format_midi_meta(meta, filename) if meta else ""

    total_unmapped = sum(unmapped_counts[n] for n in unmapped)
    high_freq = [n for n in unmapped if unmapped_counts[n] >= max(3, total_unmapped * 0.1)]
    high_freq_hint = ""
    if high_freq:
        names = ", ".join(f"{n}({_midi_to_name(n)})" for n in high_freq)
        high_freq_hint = f"\nHIGH-FREQUENCY NOTES (appear often, choose replacements extra carefully): {names}\n"

    return f"""You are a professional music arranger.
{meta_block}
An instrument only has these notes available:
[{avail_desc}]
Instrument range: {avail_min}({_midi_to_name(avail_min)}) ~ {avail_max}({_midi_to_name(avail_max)})

The following MIDI notes from the original piece cannot be played on this instrument:
{chr(10).join(unmapped_lines)}
{high_freq_hint}
For each unmapped note, suggest which available note should replace it.

=== REPLACEMENT PRINCIPLES ===
1. CLOSEST PITCH — replacement must be as close to the original as possible. Prefer the nearest available note (up or down).
2. PRESERVE DIRECTION — if the note is above the instrument range, map to the highest available note. If below, map to the lowest.
3. SHARPS/FLATS — for accidentals (C#, Eb), prefer the adjacent natural note that fits the musical context (half-step toward the key center).
4. PRESERVE EMOTION — high notes carry tension/brightness; do NOT fold them down. Low notes carry depth; do NOT fold them up. Keep the emotional register.
5. MINIMIZE OCTAVE FOLDING — only fold as a last resort. When unavoidable, fold UP rather than DOWN.

=== FORBIDDEN ===
- Do NOT map two different adjacent unmapped notes to the same replacement. Each distinct original pitch should get a distinct replacement when possible.
- Do NOT create semitone clusters: if two unmapped notes are next to each other (e.g. 61 and 63), their replacements must not be adjacent semitones (e.g. both mapping to 60).
{style_block}
Your response MUST have exactly two sections with these headers. Write the Analysis section in Chinese (中文).

## Analysis
简要说明你的编曲策略：哪些音超出范围、你打算如何处理、你的推理过程。如果你能从文件名识别出这首曲子，请说明曲名并结合对原曲的理解来编排。保持简洁（3-8行）。

## Mapping
A JSON object mapping each unmapped MIDI number to its replacement, like:
{{"61": 60, "63": 64}}{drop_hint}

Nothing else after the JSON."""


def parse_remap_response(response: str) -> dict[int, int]:
    text = _extract_clean_text(response)
    match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if match:
        text = match.group(0)
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
class _NoteGroup:
    """Notes sounding at approximately the same time."""
    time_ms: int
    notes: list[tuple[int, int, str]]  # (final_note, duration_ms, status)


def _group_notes_by_time(
    events: list[RawMidiEvent],
    shift: int,
    available_set: set[int],
    threshold_ms: int = 30,
) -> list[_NoteGroup]:
    """Group simultaneous notes together (within threshold_ms)."""
    if not events:
        return []
    groups: list[_NoteGroup] = []
    current = _NoteGroup(time_ms=events[0].time_ms, notes=[])
    for ev in events:
        final = ev.note + shift
        status = "OK" if final in available_set else "UNMAPPED"
        if ev.time_ms - current.time_ms > threshold_ms:
            if current.notes:
                groups.append(current)
            current = _NoteGroup(time_ms=ev.time_ms, notes=[])
        current.notes.append((final, ev.duration_ms, status))
    if current.notes:
        groups.append(current)
    return groups


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
    for g in groups:
        bar_num = int(g.time_ms / bar_ms) + 1
        if bar_num != current_bar:
            current_bar = bar_num
            lines.append(f"\n--- Bar {bar_num} ---")

        highest = max(n[0] for n in g.notes)
        parts: list[str] = []
        for note, dur, status in sorted(g.notes, key=lambda x: x[0]):
            tag = f" [{status}]"
            if note == highest:
                tag += " [MELODY]"
            parts.append(f"{note}({_midi_to_name(note)}) dur={dur}ms{tag}")
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
) -> str:
    avail_desc = ", ".join(f"{n}({_midi_to_name(n)})" for n in available)

    groups = _group_notes_by_time(events, shift, available_set)
    sequence_text = _format_grouped_sequence(groups, meta)

    avail_min = min(available)
    avail_max = max(available)
    style_block = _STYLE_INSTRUCTIONS.get(style, "")

    drop_hint = ""
    if style in ("balanced", "creative"):
        drop_hint = '\nUse -1 as replacement to drop a note: {{"time_ms": 500, "original": 73, "replacement": -1}}'

    meta_block = _format_midi_meta(meta, filename) if meta else ""

    return f"""You are a professional music arranger.
{meta_block}
An instrument only has these notes available:
[{avail_desc}]
Instrument range: {avail_min}({_midi_to_name(avail_min)}) ~ {avail_max}({_midi_to_name(avail_max)})

Below is the note sequence from a MIDI track, grouped by simultaneous notes and organized by bar.
- Notes marked [UNMAPPED] cannot be played on this instrument and need replacements.
- Notes marked [MELODY] are the highest note in each group (typically the melody voice).
- Notes without [MELODY] are accompaniment/chord tones.

For each [UNMAPPED] note, suggest the best available replacement. The same original note at different positions MAY map to different replacements based on context.

=== MELODY NOTES (tagged [MELODY]) ===
These carry the main tune the listener hears. Treat with highest care:
1. PRESERVE MELODIC CONTOUR — if the melody rises, the replacement must also rise. If it falls, the replacement must fall. Direction is more important than exact pitch.
2. PRESERVE REGISTER — replacement must be as close to the original pitch as possible. NEVER move a melody note down by more than one octave.
3. PRESERVE INTERVALS — maintain the relative distance between consecutive melody notes. A 3rd should stay roughly a 3rd.
4. NO CONSECUTIVE DUPLICATES — do not map two originally different consecutive melody notes to the same replacement (unless the original already repeated).

=== ACCOMPANIMENT / CHORD NOTES ===
These provide harmonic support. More flexibility allowed:
1. KEEP CHORD FUNCTION — prefer notes that preserve the chord quality (root, 3rd, 5th).
2. AVOID CLASHES — within the same group, do not create adjacent semitones (e.g. C and C#) in the replacements.
3. SIMPLIFICATION OK — if a chord has too many unmapped notes, it is better to keep root + one color tone than to force all notes into awkward positions.

=== GENERAL RULES ===
1. SHARPS/FLATS — for accidentals (C#, Eb, etc.), prefer the adjacent natural note in the direction of the melody movement.
2. MINIMIZE OCTAVE FOLDING — only fold as a last resort. When unavoidable, prefer folding UP over DOWN.
3. PRESERVE EMOTION — high notes carry tension/brightness, low notes carry warmth/depth. Do not systematically flatten the pitch range.
{style_block}

Note sequence:
{sequence_text}

Your response MUST have exactly two sections with these headers. Write the Analysis section in Chinese (中文).

## Analysis
简要说明你的编曲策略：共有多少音符未映射、影响哪些音高范围、旋律音和伴奏音分别如何处理。如果你能从文件名识别出这首曲子，请说明曲名并结合对原曲的理解来编排。保持简洁（3-8行）。

## Mapping
A JSON array of replacements for UNMAPPED notes, like:
[{{"time_ms": 1000, "original": 61, "replacement": 60}}, {{"time_ms": 2000, "original": 61, "replacement": 62}}]{drop_hint}

Nothing else after the JSON array."""


def parse_context_response(response: str) -> list[PositionRemap]:
    text = _extract_clean_text(response)
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        text = match.group(0)
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


StreamCallback = Callable[[str], None]


def call_openai(
    api_key: str,
    prompt: str,
    base_url: str | None = None,
    model: str = "gpt-4o-mini",
    on_chunk: StreamCallback | None = None,
    max_tokens: int = 16384,
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

    try:
        stream = client.chat.completions.create(**create_kwargs)
    except BadRequestError:
        create_kwargs.pop("max_tokens", None)
        stream = client.chat.completions.create(**create_kwargs)

    chunks: list[str] = []
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            text = chunk.choices[0].delta.content
            chunks.append(text)
            if on_chunk:
                on_chunk("".join(chunks))

    content = "".join(chunks)
    if not content:
        raise ValueError("AI returned empty content (streaming)")
    return content


# ── Main entry ──────────────────────────────────────────────


def ai_arrange(
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
    on_chunk: StreamCallback | None = None,
) -> AiArrangeResult:
    available = _get_available_notes(mapping, profile_id)
    if not available:
        return AiArrangeResult(mode=mode, explanation="No available notes in profile.")

    available_set = set(available)
    shift = _get_shift(mapping, profile_id, transpose, octave)
    raw_events, _, _ = read_midi_events(midi_path, single_track=single_track)

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
    meta = read_midi_meta(midi_path, single_track=single_track)

    if mode == "context":
        prompt = build_context_prompt(available, raw_events, shift, available_set, style=style, filename=midi_filename, meta=meta)
        raw = call_openai(
            api_key, prompt, base_url=base_url, model=model,
            on_chunk=on_chunk, max_tokens=65536,
        )
        analysis_text, json_text = parse_analysis_and_json(raw)
        position_map = parse_context_response(json_text) if json_text else []
        return AiArrangeResult(
            position_map=position_map,
            mode="context",
            explanation=raw,
            analysis_text=analysis_text,
            prompt=prompt,
            unmapped_count=len(unmapped_unique),
            total_notes=len(raw_events),
        )

    prompt = build_remap_prompt(available, unmapped_unique, unmapped_counts, style=style, filename=midi_filename, meta=meta)
    raw = call_openai(api_key, prompt, base_url=base_url, model=model, on_chunk=on_chunk)
    analysis_text, json_text = parse_analysis_and_json(raw)
    note_map = parse_remap_response(json_text) if json_text else {}

    return AiArrangeResult(
        note_map=note_map,
        mode="remap",
        explanation=raw,
        analysis_text=analysis_text,
        prompt=prompt,
        unmapped_count=len(unmapped_unique),
        total_notes=len(raw_events),
    )
