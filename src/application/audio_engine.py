"""Audio engine for Sky keyboard simulation.

Generates piano-like tones as WAV data (pure Python) and provides
pluggable backends for playback.

Backend selection order:
  1. Qt ``QSoundEffect``  (cross-platform, needs QtMultimedia)
  2. Win32 MCI via ctypes  (Windows-only, zero extra deps)
  3. Silent fallback
"""

from __future__ import annotations

import array
import io
import logging
import math
import shutil
import sys
import tempfile
import wave
from abc import ABC, abstractmethod
from pathlib import Path

logger = logging.getLogger(__name__)


# ── tone synthesis ──────────────────────────────────────────


def midi_note_to_freq(note: int) -> float:
    return 440.0 * (2.0 ** ((note - 69) / 12.0))


def generate_tone_wav(
    frequency: float,
    duration_s: float = 1.8,
    sample_rate: int = 44100,
    volume: float = 0.7,
    midi_note: int = 60,
) -> bytes:
    """Return WAV bytes for a piano-like tone with rich harmonics and ADSR."""
    n = int(sample_rate * duration_s)
    atk_samples = max(int(0.005 * sample_rate), 1)

    _HARMONICS = [
        (1, 1.00), (2, 0.60), (3, 0.35), (4, 0.25),
        (5, 0.12), (6, 0.08), (7, 0.05), (8, 0.03),
    ]
    _DETUNE_CENTS = 0.8
    _NUM_STRINGS = 3

    note_factor = 1.0 + max(0, midi_note - 60) * 0.02
    base_decay = 2.5 * note_factor

    detune_ratios: list[float] = []
    for s in range(_NUM_STRINGS):
        cents_offset = _DETUNE_CENTS * (s - (_NUM_STRINGS - 1) / 2.0)
        detune_ratios.append(2.0 ** (cents_offset / 1200.0))

    samples = array.array("h", [0] * n)
    two_pi = 2.0 * math.pi

    for i in range(n):
        t = i / sample_rate

        if i < atk_samples:
            env = i / atk_samples
        else:
            t_after = t - atk_samples / sample_rate
            env = 0.15 + 0.85 * math.exp(-t_after * base_decay)
            env *= math.exp(-t_after * 0.4)

        val = 0.0
        for harmonic_n, amplitude in _HARMONICS:
            h_decay = math.exp(-t * base_decay * 0.3 * harmonic_n)
            for dr in detune_ratios:
                val += (amplitude * h_decay / _NUM_STRINGS) * math.sin(
                    two_pi * frequency * harmonic_n * dr * t
                )

        s = int(val * env * volume * 32767)
        samples[i] = max(-32767, min(32767, s))

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())
    return buf.getvalue()


def _prepare_wav_files(
    midi_notes: list[int],
    transpose: int = 0,
    sample_dir: Path | None = None,
) -> tuple[Path, dict[int, Path]]:
    """Prepare WAV files for playback.

    For each note N, looks for ``note_{N+transpose}.wav`` in *sample_dir*
    (and the built-in ``assets/instruments/piano/`` directory).  Missing
    samples fall back to the built-in piano synthesis.
    """
    temp_dir = Path(tempfile.mkdtemp(prefix="sky_tones_"))
    wav_files: dict[int, Path] = {}

    search_dirs = _sample_search_dirs(sample_dir)

    for note in midi_notes:
        target_note = note + transpose
        sample = _find_sample(target_note, search_dirs)
        if sample is not None:
            dest = temp_dir / f"tone_{note}.wav"
            shutil.copy2(sample, dest)
            wav_files[note] = dest
        else:
            freq = midi_note_to_freq(target_note)
            data = generate_tone_wav(freq, midi_note=target_note)
            p = temp_dir / f"tone_{note}.wav"
            p.write_bytes(data)
            wav_files[note] = p
    return temp_dir, wav_files


def _sample_search_dirs(extra: Path | None = None) -> list[Path]:
    """Return directories to search for WAV samples, in priority order."""
    dirs: list[Path] = []
    if extra is not None:
        dirs.append(extra)
    builtin = Path(__file__).resolve().parent.parent.parent / "assets" / "instruments" / "piano"
    if builtin.is_dir():
        dirs.append(builtin)
    return dirs


def _find_sample(midi_note: int, search_dirs: list[Path]) -> Path | None:
    """Find ``note_{midi_note}.wav`` in the given directories."""
    fname = f"note_{midi_note}.wav"
    for d in search_dirs:
        p = d / fname
        if p.is_file():
            return p
    return None


# ── backend abstraction ─────────────────────────────────────


class BaseAudioBackend(ABC):
    @abstractmethod
    def play_note(self, midi_note: int) -> None:
        raise NotImplementedError

    @abstractmethod
    def stop_note(self, midi_note: int) -> None:
        raise NotImplementedError

    def set_volume(self, volume: float) -> None:  # noqa: ARG002
        pass

    def cleanup(self) -> None:
        pass


class NullAudioBackend(BaseAudioBackend):
    """Silent fallback when no audio system is available."""

    def play_note(self, midi_note: int) -> None:  # noqa: ARG002
        pass

    def stop_note(self, midi_note: int) -> None:  # noqa: ARG002
        pass


# ── Qt backend ──────────────────────────────────────────────


class _QtAudioBackend(BaseAudioBackend):
    def __init__(self, sounds: dict, temp_dir: Path, volume: float) -> None:
        self._sounds = sounds
        self._temp_dir = temp_dir
        self._volume = volume

    def play_note(self, midi_note: int) -> None:
        snd = self._sounds.get(midi_note)
        if snd is not None:
            snd.setVolume(self._volume)
            if snd.isPlaying():
                snd.stop()
            snd.play()

    def stop_note(self, midi_note: int) -> None:
        snd = self._sounds.get(midi_note)
        if snd is not None and snd.isPlaying():
            snd.stop()

    def set_volume(self, volume: float) -> None:
        self._volume = volume
        for snd in self._sounds.values():
            snd.setVolume(volume)

    def cleanup(self) -> None:
        for snd in self._sounds.values():
            snd.stop()
        self._sounds.clear()
        if self._temp_dir.exists():
            shutil.rmtree(self._temp_dir, ignore_errors=True)


# ── Win32 MCI backend ──────────────────────────────────────


class _WinMciAudioBackend(BaseAudioBackend):
    """Windows-only backend using *mciSendString* via ctypes."""

    def __init__(self, wav_files: dict[int, Path], temp_dir: Path, volume: float) -> None:
        import ctypes

        self._mci = ctypes.windll.winmm.mciSendStringW  # type: ignore[attr-defined]
        self._wav_files = wav_files
        self._temp_dir = temp_dir
        self._open: set[str] = set()
        self._volume = volume

        for note, path in wav_files.items():
            alias = f"sky{note}"
            rc = self._mci(f'open "{path}" type waveaudio alias {alias}', None, 0, None)
            if rc == 0:
                self._open.add(alias)
        if self._open:
            self.set_volume(volume)

    def play_note(self, midi_note: int) -> None:
        alias = f"sky{midi_note}"
        if alias in self._open:
            vol = int(max(0.0, min(1.0, self._volume)) * 1000)
            self._mci(f"setaudio {alias} volume to {vol}", None, 0, None)
            self._mci(f"play {alias} from 0", None, 0, None)

    def stop_note(self, midi_note: int) -> None:
        alias = f"sky{midi_note}"
        if alias in self._open:
            self._mci(f"stop {alias}", None, 0, None)

    def set_volume(self, volume: float) -> None:
        self._volume = volume
        vol = int(max(0.0, min(1.0, volume)) * 1000)
        for alias in self._open:
            self._mci(f"setaudio {alias} volume to {vol}", None, 0, None)

    def cleanup(self) -> None:
        for alias in list(self._open):
            self._mci(f"close {alias}", None, 0, None)
        self._open.clear()
        if self._temp_dir.exists():
            shutil.rmtree(self._temp_dir, ignore_errors=True)


# ── factory ─────────────────────────────────────────────────


def create_audio_backend(
    midi_notes: list[int],
    volume: float = 0.7,
    transpose: int = 0,
    sample_dir: Path | None = None,
) -> tuple[BaseAudioBackend, str]:
    """Create the best available audio backend.

    Returns ``(backend, human_readable_name)``.
    """
    backend = _try_qt_backend(midi_notes, volume, transpose, sample_dir)
    if backend is not None:
        return backend, "QSoundEffect"

    if sys.platform == "win32":
        backend = _try_winmci_backend(midi_notes, volume, transpose, sample_dir)
        if backend is not None:
            return backend, "Win32 MCI"

    return NullAudioBackend(), "None (silent)"


def _try_qt_backend(
    midi_notes: list[int],
    volume: float,
    transpose: int = 0,
    sample_dir: Path | None = None,
) -> _QtAudioBackend | None:
    try:
        from PySide6.QtCore import QCoreApplication, QUrl
        from PySide6.QtMultimedia import QSoundEffect
    except ImportError:
        logger.info("QtMultimedia not available, skipping Qt audio backend")
        return None

    try:
        temp_dir, wav_files = _prepare_wav_files(midi_notes, transpose, sample_dir)
        sounds: dict[int, object] = {}
        for note, path in wav_files.items():
            snd = QSoundEffect()
            snd.setSource(QUrl.fromLocalFile(str(path)))
            snd.setLoopCount(1)
            snd.setVolume(volume)
            sounds[note] = snd

        # QSoundEffect loads asynchronously; pump the event loop so that
        # every sound transitions from Loading -> Ready before we return.
        app = QCoreApplication.instance()
        if app is not None:
            app.processEvents()

        ready = sum(1 for s in sounds.values() if s.status() == QSoundEffect.Status.Ready)
        if ready < len(sounds):
            import time

            for _ in range(40):
                time.sleep(0.025)
                if app is not None:
                    app.processEvents()
                ready = sum(
                    1 for s in sounds.values() if s.status() == QSoundEffect.Status.Ready
                )
                if ready == len(sounds):
                    break

        logger.info("Qt audio: %d/%d sounds ready", ready, len(sounds))

        if ready == 0 and sounds:
            for s in sounds.values():
                s.deleteLater()  # type: ignore[union-attr]
            shutil.rmtree(temp_dir, ignore_errors=True)
            return None

        return _QtAudioBackend(sounds, temp_dir, volume)
    except Exception:
        logger.exception("Qt audio backend init failed")
        return None


def _try_winmci_backend(
    midi_notes: list[int],
    volume: float,
    transpose: int = 0,
    sample_dir: Path | None = None,
) -> _WinMciAudioBackend | None:
    try:
        import ctypes

        _ = ctypes.windll.winmm  # type: ignore[attr-defined]
    except (ImportError, AttributeError, OSError):
        return None

    try:
        temp_dir, wav_files = _prepare_wav_files(midi_notes, transpose, sample_dir)
        backend = _WinMciAudioBackend(wav_files, temp_dir, volume)
        if not backend._open:
            backend.cleanup()
            return None
        return backend
    except Exception:
        logger.exception("Win32 MCI audio backend init failed")
        return None


# Backwards-compatible wrapper used by simulate_tab
def create_qt_audio_backend(
    midi_notes: list[int],
    volume: float = 0.7,
) -> BaseAudioBackend:
    backend, name = create_audio_backend(midi_notes, volume)
    logger.info("Audio backend selected: %s", name)
    return backend
