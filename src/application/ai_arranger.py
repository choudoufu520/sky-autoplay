from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from src.domain.mapping import MappingConfig
from src.infrastructure.midi_reader import RawMidiEvent, read_midi_events

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


def build_remap_prompt(
    available: list[int],
    unmapped: list[int],
    unmapped_counts: Counter[int],
) -> str:
    avail_desc = ", ".join(f"{n}({_midi_to_name(n)})" for n in available)
    unmapped_lines = []
    for n in unmapped:
        unmapped_lines.append(f"  - {n} ({_midi_to_name(n)}), appears {unmapped_counts[n]} times")

    return f"""You are a professional music arranger. An instrument only has these notes available:
[{avail_desc}]

The following MIDI notes from the original piece cannot be played on this instrument:
{chr(10).join(unmapped_lines)}

For each unmapped note, suggest which available note should replace it to preserve the melody and harmony as much as possible. Consider:
1. Prefer notes within the same octave when possible
2. For sharps/flats, choose the natural note that best fits musically
3. For out-of-range notes, fold into the nearest available octave
4. Preserve melodic intervals where possible

Return ONLY a JSON object mapping each unmapped MIDI number to its replacement, like:
{{"61": 60, "63": 64}}

No explanation, just the JSON."""


def parse_remap_response(response: str) -> dict[int, int]:
    text = _extract_clean_text(response)
    match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if match:
        text = match.group(0)
    data = json.loads(text)
    return {int(k): int(v) for k, v in data.items()}


# ── Context mode ────────────────────────────────────────────


def build_context_prompt(
    available: list[int],
    events: list[RawMidiEvent],
    shift: int,
    available_set: set[int],
) -> str:
    avail_desc = ", ".join(f"{n}({_midi_to_name(n)})" for n in available)

    note_lines: list[str] = []
    for ev in events:
        final = ev.note + shift
        mapped = "OK" if final in available_set else "UNMAPPED"
        note_lines.append(f"  t={ev.time_ms}ms, note={final}({_midi_to_name(final)}), dur={ev.duration_ms}ms [{mapped}]")

    return f"""You are a professional music arranger. An instrument only has these notes available:
[{avail_desc}]

Below is the note sequence from a MIDI track. Notes marked [UNMAPPED] cannot be played on this instrument.
Analyze the melodic and harmonic context, then for each [UNMAPPED] note, suggest the best available replacement.
The same original note at different positions MAY map to different replacements based on context.

Note sequence:
{chr(10).join(note_lines)}

Return ONLY a JSON array of replacements for UNMAPPED notes, like:
[{{"time_ms": 1000, "original": 61, "replacement": 60}}, {{"time_ms": 2000, "original": 61, "replacement": 62}}]

No explanation, just the JSON array."""


def parse_context_response(response: str) -> list[PositionRemap]:
    text = _extract_clean_text(response)
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        text = match.group(0)
    data = json.loads(text)
    result: list[PositionRemap] = []
    for item in data:
        result.append(PositionRemap(
            time_ms=int(item["time_ms"]),
            original=int(item["original"]),
            replacement=int(item["replacement"]),
        ))
    return result


# ── Shared utilities ────────────────────────────────────────


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
) -> str:
    import httpx
    from openai import OpenAI

    normalized_url = _normalize_base_url(base_url) if base_url else None
    client = OpenAI(
        api_key=api_key,
        base_url=normalized_url,
        http_client=httpx.Client(timeout=httpx.Timeout(300, connect=30)),
    )
    stream = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=4000,
        stream=True,
    )

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

    if mode == "context":
        prompt = build_context_prompt(available, raw_events, shift, available_set)
        raw = call_openai(api_key, prompt, base_url=base_url, model=model, on_chunk=on_chunk)
        position_map = parse_context_response(raw)
        return AiArrangeResult(
            position_map=position_map,
            mode="context",
            explanation=raw,
            unmapped_count=len(unmapped_unique),
            total_notes=len(raw_events),
        )

    prompt = build_remap_prompt(available, unmapped_unique, unmapped_counts)
    raw = call_openai(api_key, prompt, base_url=base_url, model=model, on_chunk=on_chunk)
    note_map = parse_remap_response(raw)

    return AiArrangeResult(
        note_map=note_map,
        mode="remap",
        explanation=raw,
        unmapped_count=len(unmapped_unique),
        total_notes=len(raw_events),
    )
