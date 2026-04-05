from __future__ import annotations

from pathlib import Path

import yaml

DEFAULT_TEMPLATES: dict[str, str] = {
    "remap_template": """\
You are a professional music arranger.
{meta_block}
An instrument only has these notes available:
[{avail_desc}]
Instrument range: {avail_min_desc} ~ {avail_max_desc}
Likely tonal center to preserve: {scale_key}.
Replacements should respect scale-degree function: the 3rd of a chord is harmonically more critical than the 5th; a leading tone (7th degree) resolving to tonic should be preserved.
{optimal_hint}
The following MIDI notes from the original piece cannot be played on this instrument:
{unmapped_lines}
{high_freq_hint}
For each unmapped note, suggest which available note should replace it.

=== REPLACEMENT PRINCIPLES ===
1. CLOSEST PITCH — replacement must be as close to the original as possible. Prefer the nearest available note (up or down).
2. RANGE COMPRESSION — when multiple unmapped notes cluster above (or below) the instrument range, do NOT clamp them all to the same boundary note. Distribute them proportionally across the top (or bottom) of the available range to preserve their relative intervals. Example: unmapped 85,86,88 with max available 84 -> map to 81,83,84 (ascending contour preserved).
3. SHARPS/FLATS — for accidentals (C#, Eb), prefer the adjacent natural note that fits the musical context (half-step toward the key center).
4. PRESERVE EMOTION — high notes carry tension/brightness; do NOT fold them down. Low notes carry depth; do NOT fold them up. Keep the emotional register. EXCEPTION: when multiple high notes all exceed the range, shift the entire group down proportionally to preserve the melodic contour — this preserves emotion far better than clamping them all to the same ceiling note.
5. OCTAVE FOLDING — for notes far outside the range (more than 1 octave away), fold by +12 or -12 until they land near an available note, then pick the closest. A folded note is ALWAYS better than a dropped note because it preserves rhythm and pitch class.
6. VOICE LEADING — if two unmapped notes are close in pitch (within 4 semitones), their replacements should also be close, preserving the relative spacing.

=== FORBIDDEN ===
- Do NOT map two different adjacent unmapped notes to the same replacement. Each distinct original pitch should get a distinct replacement when possible.
- Do NOT create semitone clusters: if two unmapped notes are next to each other (e.g. 61 and 63), their replacements must not be adjacent semitones (e.g. both mapping to 60).
- ANTI-CONVERGENCE — if two different unmapped notes frequently co-occur (appear within 50ms of each other in the piece), they MUST NOT map to the same replacement. Converging different pitches onto one key creates a "double tap" stutter that sounds noisy. Spread them to distinct available notes, or drop one with -1 if a style allows dropping.

=== EXAMPLE (good vs. bad) ===
Unmapped: 61(C#4), 63(D#4).  Available: [..., 60(C4), 62(D4), 64(E4), ...]
GOOD: {{"61": 62, "63": 64}} — each gets a distinct nearby replacement, spacing preserved.
BAD:  {{"61": 60, "63": 60}} — two different notes mapped to same replacement, destroys detail.
BAD:  {{"61": 60, "63": 62}} — both shifted down, but 60 and 62 lose the half-step tension of 61 vs 63.
{style_block}{simplify_block}
Your response MUST have exactly two sections with these headers. Write the Analysis section in Chinese (中文).

## Analysis
简要说明你的编曲策略：哪些音超出范围、你打算如何处理、你的推理过程。如果你能从文件名识别出这首曲子，请说明曲名并结合对原曲的理解来编排。保持简洁（3-8行）。

## Mapping
A JSON object mapping each unmapped MIDI number to its replacement, like:
{{"61": 60, "63": 64}}{drop_hint}

Nothing else after the JSON.""",

    "context_template": """\
You are a professional music arranger.
{meta_block}
An instrument only has these notes available:
[{avail_desc}]
Instrument range: {avail_min_desc} ~ {avail_max_desc}
Likely tonal center to preserve: {scale_key}.
Replacements should respect scale-degree function: the 3rd of a chord is harmonically more critical than the 5th; a leading tone (7th degree) resolving to tonic should be preserved.
{optimal_hint}
{continuation_context}
Below is the note sequence from a MIDI track, grouped by simultaneous notes and organized by bar.
- Notes marked [UNMAPPED] cannot be played on this instrument and need replacements.
- Notes marked [MELODY] are the most likely foreground note in each group based on pitch, duration, velocity, and continuity.
- Notes marked [ACCENT] are louder notes and may deserve foreground priority.
- Notes without [MELODY] are accompaniment/chord tones.

For each [UNMAPPED] note, suggest the best available replacement. The same original note at different positions MAY map to different replacements based on context.

=== MELODY NOTES (tagged [MELODY]) ===
These carry the main tune the listener hears. Treat with highest care:
1. PRESERVE MELODIC CONTOUR — if the melody rises, the replacement must also rise. If it falls, the replacement must fall. Direction is more important than exact pitch.
2. PRESERVE REGISTER — replacement must be as close to the original pitch as possible. NEVER move a melody note down by more than one octave.
3. PRESERVE INTERVALS — maintain the relative distance between consecutive melody notes. A 3rd should stay roughly a 3rd.
4. NO CONSECUTIVE DUPLICATES — do not map two originally different consecutive melody notes to the same replacement (unless the original already repeated).
5. INTERVAL LIMIT — the interval between two consecutive melody replacements should not deviate from the original interval by more than 3 semitones. Example: original melody goes up 4 semitones (M3), replacement should go up 1-7 semitones. Never invert the direction.
6. RANGE COMPRESSION — when consecutive melody notes all exceed the instrument ceiling (or floor), do NOT map them all to the boundary note. Spread them proportionally into the top (or bottom) of the available range to preserve the ascending/descending contour. Three different notes must produce three different replacements.

=== ACCOMPANIMENT / CHORD NOTES ===
These provide harmonic support. More flexibility allowed:
1. KEEP CHORD FUNCTION — prefer notes that preserve the chord quality (root, 3rd, 5th).
2. AVOID CLASHES — within the same group, do not create adjacent semitones (e.g. C and C#) in the replacements.
3. SIMPLIFICATION OK — if a chord has too many unmapped notes, it is better to keep root + one color tone than to force all notes into awkward positions.
4. VOICE LEADING — when the same chord voice appears in consecutive groups, prefer the replacement closest to the previous group's replacement for that voice. Minimize total pitch movement of inner voices between consecutive chords.
5. ANTI-CONVERGENCE — do NOT map two different unmapped notes within the same group to the same replacement. This creates a "double tap" on one key that sounds stuttery and noisy. Spread them to different available notes, or drop one with -1 if the style permits.

=== GENERAL RULES ===
1. SHARPS/FLATS — for accidentals (C#, Eb, etc.), prefer the adjacent natural note in the direction of the melody movement.
2. OCTAVE FOLDING — for notes far outside the range (more than 1 octave away), fold by +12 or -12 until they land near an available note, then pick the closest. A folded note is ALWAYS better than a dropped note because it preserves rhythm and pitch class.
3. PRESERVE EMOTION — high notes carry tension/brightness, low notes carry warmth/depth. Do not systematically flatten the pitch range. When a group of notes all exceed the ceiling, shift the entire group down proportionally rather than clamping to the same boundary.
4. RANGE COMPRESSION — when multiple unmapped notes in the same group or consecutive groups cluster beyond the range, distribute them across distinct available notes. Never map two different originals to the same replacement within the same time window.

=== EXAMPLE (good vs. bad) ===
Original melody: 67(G4) -> 69(A4) -> 73(C#5)   (intervals: up 2, up 4)
Available: [60,62,64,65,67,69,71,72,74,76,77,79,81,83,84]
GOOD: 67 -> 69 -> 74    (up 2, up 5 — direction and rough interval preserved)
BAD:  67 -> 69 -> 64    (up 2, DOWN 5 — direction inverted, destroys melodic contour)
BAD:  67 -> 69 -> 69    (up 2, +0   — consecutive duplicates, melody stalls)
{style_block}{simplify_block}

Note sequence:
{sequence_text}

Your response MUST have exactly two sections with these headers. Write the Analysis section in Chinese (中文).

## Analysis
简要说明你的编曲策略：共有多少音符未映射、影响哪些音高范围、旋律音和伴奏音分别如何处理。如果你能从文件名识别出这首曲子，请说明曲名并结合对原曲的理解来编排。保持简洁（3-8行）。

## Mapping
A JSON array of replacements for UNMAPPED notes, like:
[{{"time_ms": 1000, "original": 61, "replacement": 60}}, {{"time_ms": 2000, "original": 61, "replacement": 62}}]{drop_hint}

Nothing else after the JSON array.""",

    "extract_template": """\
You are a professional music analyst and arranger.
{meta_block}
{continuation_context}
Below is a note sequence from a MIDI file, grouped by simultaneous notes and organized by bar.
Each note shows its MIDI number, note name, duration, velocity, and a heuristic tag:
- [LIKELY_MELODY] — heuristically identified as the most likely melody note in its group (based on pitch, velocity, duration, and continuity).
- [ACCENT] — louder note that may be melodically prominent.
- Notes without tags are candidates for accompaniment or bass.

Your task: classify EVERY note in the sequence into exactly one role:
- **melody** — the main tune the listener hears and would sing along to. Usually one note at a time, occasionally two in parallel thirds/sixths. Characteristics: highest or most prominent pitch in each group, longest duration, strongest velocity, smooth stepwise or small-leap motion between consecutive groups.
- **accompaniment** — harmonic support, arpeggios, chords, rhythmic patterns, counter-melodies. Characteristics: repetitive patterns, block chords, broken chords (Alberti bass, arpeggios), inner voices, octave doublings of the melody.
- **bass** — the lowest voice providing harmonic foundation. Characteristics: lowest pitch in each group, often root notes of chords, slower rhythm than accompaniment, typically in a lower octave register.

=== CLASSIFICATION RULES ===
1. MELODY CONTINUITY — the melody line should be smooth and continuous. Avoid jumping the melody label between wildly different pitch registers from one group to the next.
2. ONE MELODY PER GROUP — in most groups, only one note is melody. Exception: parallel thirds/sixths where two notes move together can both be melody.
3. BASS IS THE BOTTOM — bass is typically the single lowest note. If the lowest note is part of a dense chord voicing and not separated by an octave gap, it may be accompaniment instead.
4. EVERYTHING ELSE IS ACCOMPANIMENT — inner chord tones, arpeggiated patterns, repeated chords, counter-melodies, and octave doublings.
5. USE CONTEXT — a note that is melody in one bar might become accompaniment in another if the melody moves to a different register.

Note sequence:
{sequence_text}

Your response MUST have exactly two sections with these headers. Write the Analysis section in Chinese (中文).

## Analysis
简要分析这段音乐的结构：旋律线在什么音域、伴奏是什么形态（柱式和弦、分解和弦、琶音等）、低音走向如何。如果能从文件名识别曲目请说明。保持简洁（3-8行）。

## Roles
A JSON array classifying each note. Use this format:
[{{"time_ms": 1000, "note": 72, "role": "melody"}}, {{"time_ms": 1000, "note": 60, "role": "bass"}}, {{"time_ms": 1000, "note": 64, "role": "accompaniment"}}]

Include ALL notes from the input. Every (time_ms, note) pair must appear exactly once.
Nothing else after the JSON array.""",

    "style_conservative": """\
=== STRICT REPLACEMENT ===
You MUST NOT use -1. Every unmapped note MUST receive a valid available replacement.
For notes far outside the instrument range, fold them by octaves (+12 or -12) until they land on or near an available note. Even a 2-3 octave fold is acceptable — it preserves rhythm and harmonic function.""",

    "style_balanced": """\
=== ADDITIONAL FREEDOM ===
You MAY use -1 to DROP a note, but ONLY when ALL of these conditions are met:
- The note is an ornamental/passing tone or an octave doubling
- Removing it does not break the rhythmic pulse or core melody
- You have NOT already dropped more than 20% of the unmapped notes
For notes far outside the range, prefer octave-folding (+12/-12) over dropping. A folded note preserves rhythm; a dropped note creates silence.""",

    "style_creative": """\
=== CREATIVE FREEDOM ===
You MAY use -1 to DROP notes with artistic freedom, but:
- Do NOT drop more than 40% of unmapped notes — the piece must remain recognizable
- NEVER drop consecutive melody notes — at most 1 in every 3
- For notes far outside the range, prefer octave-folding over dropping when the note provides rhythmic structure (bass lines, chord roots)
- Simplify dense chords to root + melody note only
- Prioritize overall musicality and emotional impact over note-for-note fidelity""",
}

TEMPLATE_KEYS = list(DEFAULT_TEMPLATES.keys())


def get_prompts_path() -> Path:
    return Path("configs/prompts.yaml")


def load_custom_prompts(path: Path | None = None) -> dict[str, str]:
    if path is None:
        path = get_prompts_path()
    templates = dict(DEFAULT_TEMPLATES)
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if isinstance(data, dict):
                for key in TEMPLATE_KEYS:
                    if key in data and isinstance(data[key], str):
                        templates[key] = data[key]
        except Exception:
            pass
    return templates


def save_custom_prompts(templates: dict[str, str], path: Path | None = None) -> None:
    if path is None:
        path = get_prompts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(templates, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
