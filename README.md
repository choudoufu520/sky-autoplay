# Sky Music Automation

[中文文档](README_zh.md)

Automated music playback tool for PC *Sky: Children of the Light* — import MIDI, play automatically.

## Features

| Module | Description |
|--------|-------------|
| **Track Analysis** | Load MIDI files, inspect tracks, note distribution, key detection (with auto-suggested transpose) |
| **Chart Conversion** | MIDI → game chart JSON, with transpose, octave shift, and snap-to-nearest |
| **AI Arrangement** | Remap out-of-range notes via OpenAI-compatible API; streaming, three styles (conservative / balanced / creative), two-step review |
| **MIDI Preview** | Generate a preview MIDI after conversion to audition the result in your system player |
| **Track Audition** | Export individual tracks as standalone MIDI and play them |
| **Auto Play** | Load a chart and send keyboard input automatically; semi-transparent overlay shows real-time progress and keys |
| **Simulated Play** | Visual 3×5 keyboard with audio feedback, adjustable speed and transpose |
| **Dry Run** | Preview the entire playback via the overlay without sending actual key presses |
| **Key Mapping** | GUI editor for note → key mappings, multi-instrument profiles |
| **Auto Update** | Checks GitHub Releases on startup, one-click download |
| **Bilingual / Dual Theme** | Chinese / English, dark / light theme |

## Quick Start

### Option 1: Download a pre-built release (recommended)

Go to [Releases](../../releases), download the latest `SkyMusicAutomation-windows.zip`, extract and run `SkyMusicAutomation.exe`.

### Option 2: Run from source

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[input,gui,ai]"
python -m src.interfaces.gui.app
```

Requires Python >= 3.11.

## Workflow

```
① Tracks  →  ② Convert  →  ③ AI Arrange (optional)  →  ④ Preview  →  ⑤ Play
```

1. **Tracks** — Select a MIDI file, inspect track info and key analysis
2. **Convert** — Choose track and mapping profile, set transpose / octave, convert to chart JSON; optionally generate a preview MIDI
3. **AI Arrange** (optional) — Enter your API key, pick mode and style, click "AI Arrange":
   - AI returns analysis and mapping suggestions for review
   - Accept, edit, or retry with feedback
4. **Preview** — Audition the selected track to confirm the result
5. **Play** — Load the chart, click "Start Play", switch to the game during countdown

> Tip: use **Dry Run** mode first to verify timing and keys before actual playback.

## AI Arrangement

Two modes:

- **Note Remap** (fast) — Each note always maps to the same substitute
- **Context Arrange** (smart) — Analyzes melodic context; the same note may map differently at different positions

Three styles:

| Style | Behavior |
|-------|----------|
| Conservative | Every unmapped note must be substituted, faithful to the original |
| Balanced | May drop ornamental / passing tones, simplify chords |
| Creative | Free rewriting, prioritizes musicality |

Two-step review: AI outputs analysis + suggestions → user reviews → apply or retry with feedback.

Compatible with any OpenAI-format API endpoint (OpenAI / DeepSeek / local models, etc.).

## Play Overlay

A semi-transparent overlay appears at the top-right corner during playback (draggable):

- Progress bar with elapsed / total time
- Currently pressed keys (highlighted)
- Upcoming keys (3-second look-ahead)
- **F9** global hotkey to stop anytime

The overlay does not steal game focus and is shown in both dry-run and actual playback.

## Simulated Play

The Simulate tab provides a visual 3×5 Sky keyboard with audio feedback:

- **Auto / Manual mode** — auto-play a chart or free-play with your keyboard
- **Speed control** — 0.25x to 2.0x playback speed
- **Transpose** — shift all notes ±12 semitones
- **Custom samples** — place WAV files in `assets/instruments/piano/` to replace the built-in piano synthesis (see [sample README](assets/instruments/piano/README.md))

## Key Mapping

Edit `configs/mapping.example.yaml` or use the GUI "Key Mapping" tab:

```yaml
default_profile: default
profiles:
  default:
    note_to_key:
      '60': y    # C4
      '62': u    # D4
      '64': i    # E4
      # ...
    transpose_semitones: 0
    octave_shift: 0
```

- Each profile maps notes to keyboard keys for a specific instrument
- Supports transpose (`transpose_semitones`) and octave shift (`octave_shift`)
- Unmapped notes can be auto-matched to the nearest available key via snap-to-nearest

## CLI

After installation, use the `skytool` command-line tool:

```bash
skytool tracks midis/song.mid
skytool convert midis/song.mid -m configs/mapping.example.yaml -o output/chart.json
skytool play output/chart.json --dry-run
```

## Project Structure

```
src/
├── domain/          # Domain models (ChartDocument, MappingConfig, etc.)
├── application/     # Business logic (converter, player, ai_arranger, updater)
├── infrastructure/  # Infrastructure (MIDI reader, file storage)
└── interfaces/
    ├── cli/         # CLI (Typer)
    └── gui/         # GUI (PySide6)
        ├── tabs/    # Feature tabs
        └── workers/ # Background thread workers
```

## Build & Release

Push a tag to automatically build and publish to GitHub Releases:

```bash
git tag v0.2.0
git push --tags
```

GitHub Actions extracts the version from the tag, packages with PyInstaller, and creates a Release.

## Disclaimer

*Sky: Children of the Light* is an online game. Automated input may violate its Terms of Service and risk your account. Use at your own discretion.
