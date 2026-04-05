# Piano Samples

Place WAV files in this directory to override the built-in synthesized tones.

## Naming Convention

Each file must be named `note_{MIDI_NOTE}.wav`, where `{MIDI_NOTE}` is the
MIDI note number.  For example:

| File          | Note | Pitch |
|---------------|------|-------|
| `note_48.wav` | C3   | 130.81 Hz |
| `note_50.wav` | D3   | 146.83 Hz |
| `note_52.wav` | E3   | 164.81 Hz |
| `note_53.wav` | F3   | 174.61 Hz |
| `note_55.wav` | G3   | 196.00 Hz |
| `note_57.wav` | A3   | 220.00 Hz |
| `note_59.wav` | B3   | 246.94 Hz |
| `note_60.wav` | C4   | 261.63 Hz |
| `note_62.wav` | D4   | 293.66 Hz |
| `note_64.wav` | E4   | 329.63 Hz |
| `note_65.wav` | F4   | 349.23 Hz |
| `note_67.wav` | G4   | 392.00 Hz |
| `note_69.wav` | A4   | 440.00 Hz |
| `note_71.wav` | B4   | 493.88 Hz |
| `note_72.wav` | C5   | 523.25 Hz |
| `note_74.wav` | D5   | 587.33 Hz |
| `note_76.wav` | E5   | 659.26 Hz |

The default Sky keyboard uses 15 notes (C4–E5, MIDI 60–76).  If you want to
use the transpose feature (±12 semitones), provide samples for a wider range
(C3–E6, MIDI 48–88).

## Format Requirements

- **Format**: WAV (PCM, 16-bit or higher)
- **Channels**: Mono or stereo
- **Sample rate**: 44100 Hz recommended
- **Duration**: 1–2 seconds per note is sufficient

## Where to Get Samples

Free piano sample sources:

- **Salamander Grand Piano** (CC BY 3.0):
  https://freepats.zenvoid.org/Piano/acoustic-grand-piano.html
- **University of Iowa Piano Samples** (public domain):
  http://theremin.music.uiowa.edu/MISpiano.html

You can also use the `scripts/render_piano_samples.py` helper script to render
samples from a SoundFont (SF2) file.
