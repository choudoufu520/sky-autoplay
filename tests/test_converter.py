from mido import Message, MetaMessage, MidiFile, MidiTrack

from src.application.converter import (
    _apply_shifts,
    _exact_lookup,
    _lookup_key,
    _mapped_note_numbers,
    _octave_fold,
    _snap_to_nearest,
    build_position_index,
    ConvertOptions,
    _fuzzy_position_lookup,
    convert_midi_to_chart,
)
from src.domain.mapping import MappingConfig, MappingProfile


def _make_mapping(notes: dict[str, str]) -> MappingConfig:
    return MappingConfig(
        default_profile="default",
        profiles={"default": MappingProfile(note_to_key=notes)},
    )


C_MAJOR_NOTES = {
    "60": "y", "62": "u", "64": "i", "65": "o", "67": "p",
    "69": "h", "71": "j", "72": "k", "74": "l", "76": ";",
    "77": "n", "79": "m", "81": ",", "83": ".", "84": "/",
}


class TestApplyShifts:
    def test_no_shift(self):
        assert _apply_shifts(60, 0, 0, 0, 0) == 60

    def test_transpose(self):
        assert _apply_shifts(60, 0, 3, 0, 0) == 63

    def test_octave(self):
        assert _apply_shifts(60, 0, 0, 0, 1) == 72

    def test_combined(self):
        assert _apply_shifts(60, 2, 3, 1, -1) == 60 + 2 + 3 + (1 + -1) * 12


class TestExactLookup:
    def test_found_by_number(self):
        assert _exact_lookup(C_MAJOR_NOTES, 60) == "y"

    def test_not_found(self):
        assert _exact_lookup(C_MAJOR_NOTES, 61) is None


class TestMappedNoteNumbers:
    def test_sorted(self):
        result = _mapped_note_numbers(C_MAJOR_NOTES)
        assert result == sorted(result)
        assert 60 in result
        assert 84 in result

    def test_note_names(self):
        result = _mapped_note_numbers({"C4": "a", "D4": "b"})
        assert 60 in result
        assert 62 in result


class TestSnapToNearest:
    def test_exact_match(self):
        mapped = [60, 62, 64, 65, 67]
        assert _snap_to_nearest(60, mapped, 2) == 60

    def test_snap_up(self):
        mapped = [60, 62, 64]
        assert _snap_to_nearest(61, mapped, 2) == 60  # prefers lower

    def test_snap_down(self):
        mapped = [60, 62, 64]
        assert _snap_to_nearest(63, mapped, 2) == 62 or _snap_to_nearest(63, mapped, 2) == 64

    def test_out_of_range(self):
        mapped = [60, 62, 64]
        assert _snap_to_nearest(57, mapped, 2) is None

    def test_empty(self):
        assert _snap_to_nearest(60, [], 2) is None


class TestOctaveFold:
    def test_in_range(self):
        mapped = [60, 62, 64, 65, 67, 69, 71, 72, 74, 76, 77, 79, 81, 83, 84]
        assert _octave_fold(65, mapped) == 65

    def test_below_range(self):
        mapped = [60, 62, 64, 65, 67, 69, 71, 72, 74, 76, 77, 79, 81, 83, 84]
        result = _octave_fold(48, mapped)
        assert result % 12 == 48 % 12
        assert 60 <= result <= 84

    def test_above_range(self):
        mapped = [60, 62, 64, 65, 67, 69, 71, 72, 74, 76, 77, 79, 81, 83, 84]
        result = _octave_fold(96, mapped)
        assert result % 12 == 96 % 12
        assert 60 <= result <= 84


class TestLookupKey:
    def test_exact_match(self):
        mapping = _make_mapping(C_MAJOR_NOTES)
        key, info = _lookup_key(mapping, "default", 60, 0, 0)
        assert key == "y"
        assert info is None

    def test_ai_position_map(self):
        mapping = _make_mapping(C_MAJOR_NOTES)
        pos_map = {(100, 61): 60}
        key, info = _lookup_key(mapping, "default", 61, 0, 0, ai_position_map=pos_map, time_ms=100)
        assert key == "y"
        assert "ai-ctx" in info

    def test_ai_position_map_drop(self):
        mapping = _make_mapping(C_MAJOR_NOTES)
        pos_map = {(100, 61): -1}
        key, info = _lookup_key(mapping, "default", 61, 0, 0, ai_position_map=pos_map, time_ms=100)
        assert key is None
        assert "ai-drop" in info

    def test_ai_note_map(self):
        mapping = _make_mapping(C_MAJOR_NOTES)
        note_map = {61: 62}
        key, info = _lookup_key(mapping, "default", 61, 0, 0, ai_note_map=note_map)
        assert key == "u"
        assert "ai:" in info

    def test_snap(self):
        mapping = _make_mapping(C_MAJOR_NOTES)
        key, info = _lookup_key(mapping, "default", 61, 0, 0, snap=True)
        assert key is not None
        assert "snap" in info

    def test_unmapped_no_snap(self):
        mapping = _make_mapping(C_MAJOR_NOTES)
        key, info = _lookup_key(mapping, "default", 61, 0, 0, snap=False)
        assert key is None

    def test_fuzzy_position_fallback(self):
        mapping = _make_mapping(C_MAJOR_NOTES)
        pos_map = {(100, 61): 60}
        pos_index = build_position_index(pos_map)
        key, info = _lookup_key(
            mapping, "default", 61, 0, 0,
            ai_position_map=pos_map, ai_position_index=pos_index,
            time_ms=115,
        )
        assert key == "y"
        assert "ai-ctx~" in info

    def test_fuzzy_position_out_of_tolerance(self):
        mapping = _make_mapping(C_MAJOR_NOTES)
        pos_map = {(100, 61): 60}
        pos_index = build_position_index(pos_map)
        key, info = _lookup_key(
            mapping, "default", 61, 0, 0,
            ai_position_map=pos_map, ai_position_index=pos_index,
            time_ms=200,
        )
        assert key is None


class TestPositionIndex:
    def test_build_and_lookup(self):
        pos_map = {(100, 61): 60, (200, 61): 62, (300, 65): 64}
        idx = build_position_index(pos_map)
        assert 61 in idx
        assert 65 in idx
        assert len(idx[61]) == 2

    def test_fuzzy_exact(self):
        pos_map = {(100, 61): 60}
        idx = build_position_index(pos_map)
        assert _fuzzy_position_lookup(idx, 100, 61) == 60

    def test_fuzzy_within_tolerance(self):
        pos_map = {(100, 61): 60}
        idx = build_position_index(pos_map)
        assert _fuzzy_position_lookup(idx, 120, 61) == 60

    def test_fuzzy_outside_tolerance(self):
        pos_map = {(100, 61): 60}
        idx = build_position_index(pos_map)
        assert _fuzzy_position_lookup(idx, 200, 61) is None

    def test_fuzzy_picks_closest(self):
        pos_map = {(100, 61): 60, (200, 61): 62}
        idx = build_position_index(pos_map)
        assert _fuzzy_position_lookup(idx, 110, 61) == 60
        assert _fuzzy_position_lookup(idx, 190, 61) == 62

    def test_fuzzy_wrong_note(self):
        pos_map = {(100, 61): 60}
        idx = build_position_index(pos_map)
        assert _fuzzy_position_lookup(idx, 100, 62) is None


class TestConvertMidiToChart:
    def test_end_to_end_conversion_with_ai_position_map(self, tmp_path):
        midi_path = tmp_path / "demo.mid"
        midi = MidiFile(ticks_per_beat=480)
        track = MidiTrack()
        midi.tracks.append(track)
        track.append(MetaMessage("set_tempo", tempo=500_000, time=0))
        track.append(Message("note_on", note=61, velocity=80, time=0))
        track.append(Message("note_off", note=61, velocity=0, time=480))
        midi.save(str(midi_path))

        mapping = _make_mapping(C_MAJOR_NOTES)
        options = ConvertOptions(
            profile="default",
            snap=False,
            ai_position_map={(0, 61): 60},
        )

        chart, warnings = convert_midi_to_chart(midi_path, mapping, options)

        assert len(chart.events) == 1
        assert chart.events[0].key == "y"
        assert any("ai-ctx" in warning for warning in warnings)
