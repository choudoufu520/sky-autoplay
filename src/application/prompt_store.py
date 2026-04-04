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
These notes form a {scale_key} scale spanning 2 octaves.
Replacements should respect scale-degree function: the 3rd of a chord is harmonically more critical than the 5th; a leading tone (7th degree) resolving to tonic should be preserved.
{optimal_hint}
The following MIDI notes from the original piece cannot be played on this instrument:
{unmapped_lines}
{high_freq_hint}
For each unmapped note, suggest which available note should replace it.

=== REPLACEMENT PRINCIPLES ===
1. CLOSEST PITCH — replacement must be as close to the original as possible. Prefer the nearest available note (up or down).
2. PRESERVE DIRECTION — if the note is above the instrument range, map to the highest available note. If below, map to the lowest.
3. SHARPS/FLATS — for accidentals (C#, Eb), prefer the adjacent natural note that fits the musical context (half-step toward the key center).
4. PRESERVE EMOTION — high notes carry tension/brightness; do NOT fold them down. Low notes carry depth; do NOT fold them up. Keep the emotional register.
5. OCTAVE FOLDING — for notes far outside the range (more than 1 octave away), fold by +12 or -12 until they land near an available note, then pick the closest. A folded note is ALWAYS better than a dropped note because it preserves rhythm and pitch class.
6. VOICE LEADING — if two unmapped notes are close in pitch (within 4 semitones), their replacements should also be close, preserving the relative spacing.

=== FORBIDDEN ===
- Do NOT map two different adjacent unmapped notes to the same replacement. Each distinct original pitch should get a distinct replacement when possible.
- Do NOT create semitone clusters: if two unmapped notes are next to each other (e.g. 61 and 63), their replacements must not be adjacent semitones (e.g. both mapping to 60).

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
These notes form a {scale_key} scale spanning 2 octaves.
Replacements should respect scale-degree function: the 3rd of a chord is harmonically more critical than the 5th; a leading tone (7th degree) resolving to tonic should be preserved.
{optimal_hint}
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
5. INTERVAL LIMIT — the interval between two consecutive melody replacements should not deviate from the original interval by more than 3 semitones. Example: original melody goes up 4 semitones (M3), replacement should go up 1-7 semitones. Never invert the direction.

=== ACCOMPANIMENT / CHORD NOTES ===
These provide harmonic support. More flexibility allowed:
1. KEEP CHORD FUNCTION — prefer notes that preserve the chord quality (root, 3rd, 5th).
2. AVOID CLASHES — within the same group, do not create adjacent semitones (e.g. C and C#) in the replacements.
3. SIMPLIFICATION OK — if a chord has too many unmapped notes, it is better to keep root + one color tone than to force all notes into awkward positions.
4. VOICE LEADING — when the same chord voice appears in consecutive groups, prefer the replacement closest to the previous group's replacement for that voice. Minimize total pitch movement of inner voices between consecutive chords.

=== GENERAL RULES ===
1. SHARPS/FLATS — for accidentals (C#, Eb, etc.), prefer the adjacent natural note in the direction of the melody movement.
2. OCTAVE FOLDING — for notes far outside the range (more than 1 octave away), fold by +12 or -12 until they land near an available note, then pick the closest. A folded note is ALWAYS better than a dropped note because it preserves rhythm and pitch class.
3. PRESERVE EMOTION — high notes carry tension/brightness, low notes carry warmth/depth. Do not systematically flatten the pitch range.

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
