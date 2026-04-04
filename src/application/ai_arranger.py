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


def _midi_to_name(n: int) -> str:
    return f"{_PITCH_CLASSES[n % 12]}{n // 12 - 1}"


def _detect_scale_key(available: list[int]) -> str:
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
    single_track: int | None = None,
) -> list[OptimalSetting]:
    """Search all transpose (-6..+5) x octave offset (-3..+1) combinations.

    Returns results sorted by unmapped_count ascending (best first).
    """
    available = _get_available_notes(mapping, profile_id)
    if not available:
        return []

    available_set = set(available)
    raw_events, _, _ = read_midi_events(midi_path, single_track=single_track)
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


def _load_templates() -> dict[str, str]:
    from src.application.prompt_store import load_custom_prompts
    return load_custom_prompts()


def _get_style_block(style: str) -> str:
    templates = _load_templates()
    key = f"style_{style}"
    block = templates.get(key, "")
    return f"\n{block}" if block else ""


def build_remap_prompt(
    available: list[int],
    unmapped: list[int],
    unmapped_counts: Counter[int],
    style: str = "conservative",
    filename: str = "",
    meta: MidiMeta | None = None,
    optimal_hint: str = "",
) -> str:
    avail_desc = ", ".join(f"{n}({_midi_to_name(n)})" for n in available)
    unmapped_lines = []
    for n in unmapped:
        unmapped_lines.append(f"  - {n} ({_midi_to_name(n)}), appears {unmapped_counts[n]} times")

    avail_min = min(available)
    avail_max = max(available)
    scale_key = _detect_scale_key(available)
    style_block = _get_style_block(style)

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
    })


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
    optimal_hint: str = "",
) -> str:
    avail_desc = ", ".join(f"{n}({_midi_to_name(n)})" for n in available)

    groups = _group_notes_by_time(events, shift, available_set)
    sequence_text = _format_grouped_sequence(groups, meta)

    avail_min = min(available)
    avail_max = max(available)
    scale_key = _detect_scale_key(available)
    style_block = _get_style_block(style)

    drop_hint = ""
    if style in ("balanced", "creative"):
        drop_hint = '\nUse -1 as replacement to drop a note: {{"time_ms": 500, "original": 73, "replacement": -1}}'

    meta_block = _format_midi_meta(meta, filename) if meta else ""

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
        "sequence_text": sequence_text,
    })


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

    optimal_hint = ""
    try:
        opts = find_optimal_settings(midi_path, mapping, profile_id, single_track=single_track)
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
    except Exception:
        pass

    if mode == "context":
        prompt = build_context_prompt(available, raw_events, shift, available_set, style=style, filename=midi_filename, meta=meta, optimal_hint=optimal_hint)
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

    prompt = build_remap_prompt(available, unmapped_unique, unmapped_counts, style=style, filename=midi_filename, meta=meta, optimal_hint=optimal_hint)
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
