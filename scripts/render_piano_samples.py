#!/usr/bin/env python3
"""Render individual piano note WAV files from a SoundFont (SF2).

Usage::

    pip install midi2audio
    python scripts/render_piano_samples.py path/to/piano.sf2

This creates ``note_48.wav`` through ``note_88.wav`` in
``assets/instruments/piano/``, covering C3–E6 (enough for the default
Sky keyboard plus ±12 semitone transpose).

Requirements:
    - ``midi2audio`` Python package  (``pip install midi2audio``)
    - ``fluidsynth`` system binary   (https://www.fluidsynth.org/)

Recommended free SoundFont:
    Salamander Grand Piano (CC BY 3.0)
    https://freepats.zenvoid.org/Piano/acoustic-grand-piano.html
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import mido
except ImportError:
    sys.exit("mido is required. Install with: pip install mido")


def _create_single_note_midi(note: int, duration_s: float = 1.5, velocity: int = 80) -> bytes:
    """Create a minimal MIDI file with a single note."""
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)

    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(120)))
    track.append(mido.Message("note_on", note=note, velocity=velocity, time=0))
    ticks = int(duration_s * 480 * 2)
    track.append(mido.Message("note_off", note=note, velocity=0, time=ticks))
    track.append(mido.MetaMessage("end_of_track", time=0))

    import io

    buf = io.BytesIO()
    mid.save(file=buf)
    return buf.getvalue()


def render_samples(
    sf2_path: Path,
    output_dir: Path,
    note_range: tuple[int, int] = (48, 88),
) -> None:
    """Render WAV samples for each MIDI note in the given range."""
    output_dir.mkdir(parents=True, exist_ok=True)

    for note in range(note_range[0], note_range[1] + 1):
        out_wav = output_dir / f"note_{note}.wav"
        midi_data = _create_single_note_midi(note)

        with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as tmp:
            tmp.write(midi_data)
            tmp_midi = Path(tmp.name)

        try:
            cmd = [
                "fluidsynth",
                "-ni",
                str(sf2_path),
                str(tmp_midi),
                "-F", str(out_wav),
                "-r", "44100",
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            print(f"  [OK] {out_wav.name}  (MIDI {note})")
        except FileNotFoundError:
            sys.exit(
                "fluidsynth not found. Install it:\n"
                "  Windows: choco install fluidsynth  /  scoop install fluidsynth\n"
                "  macOS:   brew install fluid-synth\n"
                "  Linux:   sudo apt install fluidsynth"
            )
        except subprocess.CalledProcessError as exc:
            print(f"  [FAIL] note {note}: {exc.stderr.decode()}", file=sys.stderr)
        finally:
            tmp_midi.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sf2", type=Path, help="Path to a SoundFont (.sf2) file")
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "assets" / "instruments" / "piano",
        help="Output directory (default: assets/instruments/piano/)",
    )
    parser.add_argument("--low", type=int, default=48, help="Lowest MIDI note (default: 48 = C3)")
    parser.add_argument("--high", type=int, default=88, help="Highest MIDI note (default: 88 = E6)")
    args = parser.parse_args()

    if not args.sf2.is_file():
        sys.exit(f"SoundFont not found: {args.sf2}")

    print(f"SoundFont: {args.sf2}")
    print(f"Output:    {args.output}")
    print(f"Range:     MIDI {args.low}–{args.high}")
    print()

    render_samples(args.sf2, args.output, (args.low, args.high))
    print(f"\nDone. {args.high - args.low + 1} samples written to {args.output}")


if __name__ == "__main__":
    main()
