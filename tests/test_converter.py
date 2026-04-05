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
    DenoiseReport,
    _fuzzy_position_lookup,
    convert_midi_to_chart,
    denoise_chart,
    midi_events_to_jianpu,
)
from src.application.ai_arranger import _redistribute_convergent
from src.domain.chart import ChartDocument, ChartEvent, ChartMetadata
from src.infrastructure.midi_reader import RawMidiEvent
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

        chart, warnings, _report = convert_midi_to_chart(midi_path, mapping, options)

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
        result, report = denoise_chart(chart)
        assert len(result.events) == 1
        assert report.total_removed == 1

    def test_removes_near_duplicate_within_window(self):
        chart = _chart(_tap(0, "y"), _tap(20, "y"))
        result, report = denoise_chart(chart, dedup_window_ms=30)
        assert len(result.events) == 1
        assert report.total_removed == 1

    def test_keeps_different_keys_at_same_time(self):
        chart = _chart(_tap(0, "y"), _tap(0, "u"))
        result, report = denoise_chart(chart)
        assert len(result.events) == 2
        assert report.total_removed == 0

    def test_keeps_same_key_beyond_window(self):
        chart = _chart(_tap(0, "y"), _tap(100, "y"))
        result, report = denoise_chart(chart, dedup_window_ms=30)
        assert len(result.events) == 2
        assert report.total_removed == 0


class TestDenoiseKeyRepeatRate:
    """Rule 2: per-key rate limiting (tremolo / pedal / chord repetition)."""

    def test_trims_single_key_burst(self):
        events = [_tap(i * 50, "y") for i in range(8)]
        chart = _chart(*events)
        result, report = denoise_chart(chart, dedup_window_ms=0, max_key_per_burst=3)
        keys = [e.key for e in result.events]
        assert all(k == "y" for k in keys)
        assert len(keys) == 3
        assert report.total_removed == 5

    def test_keeps_short_burst(self):
        events = [_tap(i * 50, "y") for i in range(3)]
        chart = _chart(*events)
        result, report = denoise_chart(chart, dedup_window_ms=0, max_key_per_burst=3)
        assert len(result.events) == 3
        assert report.total_removed == 0

    def test_tracks_keys_independently_through_chords(self):
        """The key improvement: detects per-key repetition even when
        different keys in a chord interleave in the event stream."""
        events = []
        for i in range(8):
            events.append(_tap(i * 100, "y"))
            events.append(_tap(i * 100, "u"))
        chart = _chart(*events)
        result, report = denoise_chart(
            chart, dedup_window_ms=0, max_key_per_burst=3,
            max_chord_repeats=99, max_simultaneous=99,
        )
        assert report.total_removed > 0
        y_count = sum(1 for e in result.events if e.key == "y")
        u_count = sum(1 for e in result.events if e.key == "u")
        assert y_count <= 3
        assert u_count <= 3

    def test_preserves_first_and_last(self):
        events = [_tap(i * 50, "y") for i in range(10)]
        chart = _chart(*events)
        result, _report = denoise_chart(chart, dedup_window_ms=0, max_key_per_burst=3)
        y_events = [e for e in result.events if e.key == "y"]
        assert y_events[0].time_ms == 0
        assert y_events[-1].time_ms == 450

    def test_separate_bursts_kept_independently(self):
        """Two bursts separated by a large gap should be thinned separately."""
        burst1 = [_tap(i * 50, "y") for i in range(3)]
        burst2 = [_tap(2000 + i * 50, "y") for i in range(3)]
        chart = _chart(*(burst1 + burst2))
        result, report = denoise_chart(
            chart, dedup_window_ms=0, max_key_per_burst=3, burst_gap_ms=500,
        )
        assert len(result.events) == 6
        assert report.total_removed == 0


class TestDenoiseRepeatingChord:
    """Rule 3: thin repeating chord patterns (same shape on every beat)."""

    def test_thins_identical_chord_run(self):
        """8 repetitions of the same 2-key chord → kept to max_chord_repeats."""
        events = []
        for i in range(8):
            events.append(_tap(i * 250, "y"))
            events.append(_tap(i * 250, "u"))
        chart = _chart(*events)
        result, report = denoise_chart(
            chart, dedup_window_ms=0, max_key_per_burst=99,
            max_chord_repeats=3, max_simultaneous=99,
        )
        assert report.total_removed > 0
        slots = set()
        for e in result.events:
            slots.add(e.time_ms)
        assert len(slots) <= 3

    def test_keeps_varied_chords(self):
        """Different chord shapes should not be thinned."""
        events = [
            _tap(0, "y"), _tap(0, "u"),
            _tap(250, "y"), _tap(250, "i"),
            _tap(500, "y"), _tap(500, "o"),
        ]
        chart = _chart(*events)
        result, report = denoise_chart(
            chart, dedup_window_ms=0, max_key_per_burst=99,
            max_chord_repeats=2, max_simultaneous=99,
        )
        assert report.total_removed == 0

    def test_realistic_accompaniment_pattern(self):
        """Simulates measure 45 from the screenshot: same chord 8x per bar."""
        events = []
        for i in range(8):
            events.append(_tap(i * 250, "6", duration_ms=200))
            events.append(_tap(i * 250, "3", duration_ms=200))
        chart = _chart(*events)
        result, report = denoise_chart(chart, dedup_window_ms=0)
        assert report.total_removed >= 8
        total = len(result.events)
        assert total <= 8


class TestDenoiseThinSimultaneous:
    """Rule 4: thin dense simultaneous events beyond max_simultaneous."""

    def test_thins_dense_chord(self):
        events = [
            _tap(0, "y", duration_ms=200),
            _tap(0, "u", duration_ms=100),
            _tap(0, "i", duration_ms=300),
            _tap(0, "o", duration_ms=50),
            _tap(0, "p", duration_ms=150),
        ]
        chart = _chart(*events)
        result, report = denoise_chart(chart, dedup_window_ms=0, max_simultaneous=3)
        assert len(result.events) == 3
        assert report.total_removed == 2
        kept_keys = {e.key for e in result.events}
        assert "i" in kept_keys
        assert "y" in kept_keys

    def test_keeps_within_limit(self):
        events = [_tap(0, "y"), _tap(0, "u"), _tap(0, "i")]
        chart = _chart(*events)
        result, report = denoise_chart(chart, max_simultaneous=4)
        assert len(result.events) == 3
        assert report.total_removed == 0

    def test_preserves_hold_events(self):
        hold_down = ChartEvent(time_ms=0, key="y", action="down")
        hold_up = ChartEvent(time_ms=100, key="y", action="up")
        chart = _chart(hold_down, hold_up, _tap(0, "y"))
        result, report = denoise_chart(chart)
        actions = [e.action for e in result.events]
        assert "down" in actions
        assert "up" in actions


class TestDenoiseIntegration:
    """All four rules applied together."""

    def test_combined_dedup_and_thin(self):
        events = [
            _tap(0, "y"), _tap(0, "y"),
            _tap(0, "u"), _tap(5, "i"),
            _tap(0, "o"), _tap(0, "p"),
        ]
        chart = _chart(*events)
        result, report = denoise_chart(
            chart, dedup_window_ms=30, max_simultaneous=3,
        )
        assert report.total_removed > 0
        keys_at_0 = [e.key for e in result.events if e.time_ms <= 30]
        assert len(set(keys_at_0)) <= 3

    def test_empty_chart(self):
        chart = _chart()
        result, report = denoise_chart(chart)
        assert len(result.events) == 0
        assert report.total_removed == 0

    def test_full_pipeline_realistic(self):
        """Simulates a bar with dense repeating chords + unison duplicates."""
        events = []
        for i in range(8):
            t = i * 250
            events.append(_tap(t, "6", duration_ms=200))
            events.append(_tap(t, "3", duration_ms=200))
            events.append(_tap(t, "6", duration_ms=100))  # unison dup
        chart = _chart(*events)
        result, report = denoise_chart(chart)
        assert report.total_removed >= 10
        assert len(result.events) <= 8

    def test_report_categories(self):
        """Verify DenoiseReport tracks per-category counts."""
        events = [
            _tap(0, "y"), _tap(0, "y"),
            _tap(0, "u"), _tap(0, "i"), _tap(0, "o"), _tap(0, "p"),
        ]
        chart = _chart(*events)
        _result, report = denoise_chart(chart, dedup_window_ms=30, max_simultaneous=3)
        assert isinstance(report, DenoiseReport)
        assert report.dedup_removed >= 1
        assert report.total_removed == (
            report.dedup_removed + report.rate_limit_removed
            + report.chord_repeat_removed + report.simultaneous_removed
        )


class TestRedistributeConvergent:
    """Tests for _redistribute_convergent in ai_arranger.py."""

    def test_no_convergence(self):
        note_map = {85: 84, 86: 83, 88: 81}
        available = [60, 62, 64, 65, 67, 69, 71, 72, 74, 76, 77, 79, 81, 83, 84]
        fixed, count = _redistribute_convergent(note_map, available)
        assert count == 0
        assert fixed == note_map

    def test_spreads_convergent_notes(self):
        """Three adjacent notes all mapped to 84 should be spread out."""
        note_map = {85: 84, 86: 84, 88: 84}
        available = [60, 62, 64, 65, 67, 69, 71, 72, 74, 76, 77, 79, 81, 83, 84]
        fixed, count = _redistribute_convergent(note_map, available)
        assert count > 0
        replacements = [fixed[85], fixed[86], fixed[88]]
        assert len(set(replacements)) == 3
        assert replacements == sorted(replacements)

    def test_preserves_ascending_order(self):
        note_map = {90: 84, 91: 84, 92: 84}
        available = [60, 62, 64, 65, 67, 69, 71, 72, 74, 76, 77, 79, 81, 83, 84]
        fixed, _count = _redistribute_convergent(note_map, available)
        assert fixed[90] <= fixed[91] <= fixed[92]

    def test_skips_drops(self):
        note_map = {85: 84, 86: -1, 88: 84}
        available = [60, 62, 64, 65, 67, 69, 71, 72, 74, 76, 77, 79, 81, 83, 84]
        fixed, count = _redistribute_convergent(note_map, available)
        assert fixed[86] == -1
        assert count > 0
        assert fixed[85] != fixed[88]

    def test_empty_map(self):
        fixed, count = _redistribute_convergent({}, [60, 62, 64])
        assert count == 0
        assert fixed == {}

    def test_skips_distant_originals(self):
        """Notes more than an octave apart should not be redistributed."""
        note_map = {40: 60, 55: 60}
        available = [60, 62, 64, 65, 67]
        fixed, count = _redistribute_convergent(note_map, available)
        assert count == 0

    def test_two_notes_same_replacement(self):
        note_map = {85: 84, 87: 84}
        available = [60, 62, 64, 65, 67, 69, 71, 72, 74, 76, 77, 79, 81, 83, 84]
        fixed, count = _redistribute_convergent(note_map, available)
        assert count > 0
        assert fixed[85] != fixed[87]


def _midi_event(note: int, time_ms: int, duration_ms: int = 100, velocity: int = 80) -> RawMidiEvent:
    return RawMidiEvent(note=note, time_ms=time_ms, duration_ms=duration_ms, velocity=velocity, program=None)


class TestMidiEventsToJianpu:
    """Tests for midi_events_to_jianpu."""

    def test_basic_output(self):
        events = [
            _midi_event(60, 0),
            _midi_event(62, 500),
            _midi_event(64, 1000),
        ]
        result = midi_events_to_jianpu(events, bpm=120.0, time_signature="4/4")
        assert result
        assert "1" in result

    def test_empty_events(self):
        result = midi_events_to_jianpu([], bpm=120.0)
        assert result == ""

    def test_contains_header(self):
        events = [_midi_event(60, 0)]
        result = midi_events_to_jianpu(events, bpm=120.0, time_signature="4/4", title="Test")
        assert "Test" in result
        assert "1=C" in result
        assert "♩=120" in result

    def test_chord_notation(self):
        """Simultaneous notes should be grouped."""
        events = [
            _midi_event(60, 0),
            _midi_event(64, 0),
            _midi_event(67, 0),
        ]
        result = midi_events_to_jianpu(events, bpm=120.0)
        assert "(" in result

    def test_rest_notation(self):
        """Gaps should produce rest markers."""
        events = [
            _midi_event(60, 0, duration_ms=100),
            _midi_event(62, 2000, duration_ms=100),
        ]
        result = midi_events_to_jianpu(events, bpm=120.0)
        assert "0" in result

    def test_octave_markers(self):
        """Notes above octave 4 should have ' markers."""
        events = [_midi_event(84, 0)]
        result = midi_events_to_jianpu(events, bpm=120.0)
        assert "'" in result
