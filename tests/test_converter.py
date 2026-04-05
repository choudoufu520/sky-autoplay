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
    denoise_chart,
)
from src.domain.chart import ChartDocument, ChartEvent, ChartMetadata
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


def _tap(time_ms: int, key: str, duration_ms: int = 100) -> ChartEvent:
    return ChartEvent(time_ms=time_ms, key=key, action="tap", duration_ms=duration_ms)


def _chart(*events: ChartEvent) -> ChartDocument:
    return ChartDocument(events=list(events), metadata=ChartMetadata())


class TestDenoiseDeduplicateSimultaneous:
    """Rule 1: remove same-key taps within dedup_window_ms (unison/octave collapse)."""

    def test_removes_exact_duplicate(self):
        chart = _chart(_tap(0, "y"), _tap(0, "y"))
        result, removed = denoise_chart(chart)
        assert len(result.events) == 1
        assert removed == 1

    def test_removes_near_duplicate_within_window(self):
        chart = _chart(_tap(0, "y"), _tap(20, "y"))
        result, removed = denoise_chart(chart, dedup_window_ms=30)
        assert len(result.events) == 1
        assert removed == 1

    def test_keeps_different_keys_at_same_time(self):
        chart = _chart(_tap(0, "y"), _tap(0, "u"))
        result, removed = denoise_chart(chart)
        assert len(result.events) == 2
        assert removed == 0

    def test_keeps_same_key_beyond_window(self):
        chart = _chart(_tap(0, "y"), _tap(100, "y"))
        result, removed = denoise_chart(chart, dedup_window_ms=30)
        assert len(result.events) == 2
        assert removed == 0


class TestDenoiseLimitConsecutiveSame:
    """Rule 2: limit consecutive same-key repeats (tremolo/pedal point)."""

    def test_trims_long_run(self):
        events = [_tap(i * 50, "y") for i in range(8)]
        chart = _chart(*events)
        result, removed = denoise_chart(chart, dedup_window_ms=0, max_consecutive_same=3)
        keys = [e.key for e in result.events]
        assert all(k == "y" for k in keys)
        assert len(keys) == 3
        assert removed == 5

    def test_keeps_short_run(self):
        events = [_tap(i * 50, "y") for i in range(3)]
        chart = _chart(*events)
        result, removed = denoise_chart(chart, dedup_window_ms=0, max_consecutive_same=3)
        assert len(result.events) == 3
        assert removed == 0

    def test_run_broken_by_different_key(self):
        events = [
            _tap(0, "y"), _tap(50, "y"), _tap(100, "y"),
            _tap(150, "u"),
            _tap(200, "y"), _tap(250, "y"), _tap(300, "y"),
        ]
        chart = _chart(*events)
        result, removed = denoise_chart(chart, dedup_window_ms=0, max_consecutive_same=3)
        assert len(result.events) == 7
        assert removed == 0

    def test_preserves_first_and_last(self):
        events = [_tap(i * 50, "y") for i in range(10)]
        chart = _chart(*events)
        result, _ = denoise_chart(chart, dedup_window_ms=0, max_consecutive_same=3)
        assert result.events[0].time_ms == 0
        assert result.events[-1].time_ms == 450


class TestDenoiseThinSimultaneous:
    """Rule 3: thin dense simultaneous events beyond max_simultaneous."""

    def test_thins_dense_chord(self):
        events = [
            _tap(0, "y", duration_ms=200),
            _tap(0, "u", duration_ms=100),
            _tap(0, "i", duration_ms=300),
            _tap(0, "o", duration_ms=50),
            _tap(0, "p", duration_ms=150),
            _tap(0, "h", duration_ms=10),
        ]
        chart = _chart(*events)
        result, removed = denoise_chart(chart, dedup_window_ms=0, max_simultaneous=3)
        assert len(result.events) == 3
        assert removed == 3
        kept_keys = {e.key for e in result.events}
        assert "i" in kept_keys
        assert "y" in kept_keys

    def test_keeps_within_limit(self):
        events = [_tap(0, "y"), _tap(0, "u"), _tap(0, "i")]
        chart = _chart(*events)
        result, removed = denoise_chart(chart, max_simultaneous=4)
        assert len(result.events) == 3
        assert removed == 0

    def test_preserves_hold_events(self):
        hold_down = ChartEvent(time_ms=0, key="y", action="down")
        hold_up = ChartEvent(time_ms=100, key="y", action="up")
        chart = _chart(hold_down, hold_up, _tap(0, "y"))
        result, removed = denoise_chart(chart)
        actions = [e.action for e in result.events]
        assert "down" in actions
        assert "up" in actions


class TestDenoiseIntegration:
    """All three rules applied together."""

    def test_combined_dedup_and_thin(self):
        events = [
            _tap(0, "y"), _tap(0, "y"),
            _tap(0, "u"), _tap(5, "i"),
            _tap(0, "o"), _tap(0, "p"),
            _tap(0, "h"), _tap(0, "j"),
        ]
        chart = _chart(*events)
        result, removed = denoise_chart(
            chart, dedup_window_ms=30, max_simultaneous=4,
        )
        assert removed > 0
        keys_at_0 = [e.key for e in result.events if e.time_ms <= 30]
        assert len(set(keys_at_0)) <= 4

    def test_empty_chart(self):
        chart = _chart()
        result, removed = denoise_chart(chart)
        assert len(result.events) == 0
        assert removed == 0
