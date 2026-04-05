"""Tests for NoteRole classification, accompaniment filtering, and AI extract parsing."""

import json

import pytest

from src.application.ai_arranger import (
    RoleClassification,
    _format_extract_sequence,
    _group_notes_for_extract,
    build_extract_prompt,
    parse_extract_response,
    _parse_extract_ai_response,
)
from src.application.converter import (
    ConvertOptions,
    _filter_accompaniment,
    _is_strong_beat,
    _resolve_event_role,
)
from src.domain.chart import NoteRole
from src.infrastructure.midi_reader import MidiMeta, RawMidiEvent


# ── NoteRole enum ──────────────────────────────────────────


class TestNoteRole:
    def test_values(self):
        assert NoteRole.melody == "melody"
        assert NoteRole.accompaniment == "accompaniment"
        assert NoteRole.bass == "bass"

    def test_string_comparison(self):
        assert NoteRole.melody == "melody"
        assert NoteRole("accompaniment") == NoteRole.accompaniment


# ── RawMidiEvent role field ────────────────────────────────


class TestRawMidiEventRole:
    def test_default_role_is_none(self):
        ev = RawMidiEvent(time_ms=0, note=60, duration_ms=100, velocity=80, program=None)
        assert ev.role is None

    def test_role_can_be_set(self):
        ev = RawMidiEvent(time_ms=0, note=60, duration_ms=100, velocity=80, program=None, role="melody")
        assert ev.role == "melody"


# ── _resolve_event_role ────────────────────────────────────


class TestResolveEventRole:
    def test_from_event_role(self):
        ev = RawMidiEvent(time_ms=100, note=60, duration_ms=50, velocity=80, program=None, role="melody")
        assert _resolve_event_role(ev, None) == "melody"

    def test_from_role_map(self):
        ev = RawMidiEvent(time_ms=100, note=60, duration_ms=50, velocity=80, program=None)
        role_map = {(100, 60): "bass"}
        assert _resolve_event_role(ev, role_map) == "bass"

    def test_event_role_takes_priority(self):
        ev = RawMidiEvent(time_ms=100, note=60, duration_ms=50, velocity=80, program=None, role="melody")
        role_map = {(100, 60): "bass"}
        assert _resolve_event_role(ev, role_map) == "melody"

    def test_no_role(self):
        ev = RawMidiEvent(time_ms=100, note=60, duration_ms=50, velocity=80, program=None)
        assert _resolve_event_role(ev, None) is None


# ── _is_strong_beat ────────────────────────────────────────


class TestIsStrongBeat:
    def test_beat_1(self):
        assert _is_strong_beat(0, 500.0) is True

    def test_beat_3(self):
        assert _is_strong_beat(1000, 500.0) is True

    def test_beat_2_not_strong(self):
        assert _is_strong_beat(500, 500.0) is False

    def test_beat_4_not_strong(self):
        assert _is_strong_beat(1500, 500.0) is False

    def test_tolerance(self):
        assert _is_strong_beat(30, 500.0) is True
        assert _is_strong_beat(1030, 500.0) is True


# ── _filter_accompaniment ─────────────────────────────────


def _make_events(specs: list[tuple[int, int, str | None]]) -> list[RawMidiEvent]:
    """Create events from (time_ms, note, role) tuples."""
    return [
        RawMidiEvent(time_ms=t, note=n, duration_ms=100, velocity=80, program=None, role=r)
        for t, n, r in specs
    ]


class TestFilterAccompaniment:
    def test_keep_strategy_no_change(self):
        events = _make_events([(0, 60, "melody"), (0, 48, "accompaniment")])
        filtered, removed = _filter_accompaniment(events, "keep", None, None, None)
        assert len(filtered) == 2
        assert removed == 0

    def test_drop_strategy_removes_accompaniment(self):
        events = _make_events([
            (0, 72, "melody"),
            (0, 60, "accompaniment"),
            (0, 48, "bass"),
            (500, 64, "accompaniment"),
        ])
        filtered, removed = _filter_accompaniment(events, "drop", None, None, None)
        assert removed == 2
        assert len(filtered) == 2
        assert all(ev.role != "accompaniment" for ev in filtered)

    def test_drop_preserves_melody_and_bass(self):
        events = _make_events([
            (0, 72, "melody"),
            (0, 48, "bass"),
            (0, 64, "accompaniment"),
        ])
        filtered, removed = _filter_accompaniment(events, "drop", None, None, None)
        assert len(filtered) == 2
        assert filtered[0].role == "melody"
        assert filtered[1].role == "bass"

    def test_none_role_kept(self):
        events = _make_events([(0, 60, None)])
        filtered, removed = _filter_accompaniment(events, "drop", None, None, None)
        assert len(filtered) == 1
        assert removed == 0

    def test_simplify_limits_accompaniment_per_timeslot(self):
        events = _make_events([
            (0, 72, "melody"),
            (0, 60, "accompaniment"),
            (0, 64, "accompaniment"),
            (0, 67, "accompaniment"),
        ])
        filtered, removed = _filter_accompaniment(events, "simplify", None, None, None)
        assert removed == 1
        accomp_kept = [ev for ev in filtered if ev.role == "accompaniment"]
        assert len(accomp_kept) == 2

    def test_role_map_fallback(self):
        events = _make_events([(0, 60, None), (0, 72, None)])
        role_map = {(0, 60): "accompaniment", (0, 72): "melody"}
        filtered, removed = _filter_accompaniment(events, "drop", role_map, None, None)
        assert removed == 1
        assert len(filtered) == 1
        assert filtered[0].note == 72


# ── ConvertOptions new fields ──────────────────────────────


class TestConvertOptionsNewFields:
    def test_defaults(self):
        opts = ConvertOptions()
        assert opts.track_roles is None
        assert opts.accompaniment_strategy == "keep"
        assert opts.role_map is None

    def test_custom_values(self):
        role_map = {(100, 60): "melody"}
        opts = ConvertOptions(
            track_roles={0: "melody", 1: "accompaniment"},
            accompaniment_strategy="thin",
            role_map=role_map,
        )
        assert opts.track_roles == {0: "melody", 1: "accompaniment"}
        assert opts.accompaniment_strategy == "thin"
        assert opts.role_map == role_map


# ── AI extract prompt ──────────────────────────────────────


class TestGroupNotesForExtract:
    def test_single_note(self):
        events = [RawMidiEvent(time_ms=0, note=60, duration_ms=100, velocity=80, program=None)]
        groups = _group_notes_for_extract(events)
        assert len(groups) == 1
        assert len(groups[0].notes) == 1
        assert groups[0].notes[0].final_note == 60

    def test_simultaneous_notes_grouped(self):
        events = [
            RawMidiEvent(time_ms=0, note=60, duration_ms=100, velocity=80, program=None),
            RawMidiEvent(time_ms=10, note=64, duration_ms=100, velocity=80, program=None),
        ]
        groups = _group_notes_for_extract(events)
        assert len(groups) == 1
        assert len(groups[0].notes) == 2

    def test_distant_notes_separate_groups(self):
        events = [
            RawMidiEvent(time_ms=0, note=60, duration_ms=100, velocity=80, program=None),
            RawMidiEvent(time_ms=500, note=64, duration_ms=100, velocity=80, program=None),
        ]
        groups = _group_notes_for_extract(events)
        assert len(groups) == 2

    def test_empty_events(self):
        assert _group_notes_for_extract([]) == []


class TestFormatExtractSequence:
    def test_melody_tag(self):
        events = [
            RawMidiEvent(time_ms=0, note=72, duration_ms=500, velocity=100, program=None),
            RawMidiEvent(time_ms=0, note=60, duration_ms=100, velocity=60, program=None),
        ]
        groups = _group_notes_for_extract(events)
        text = _format_extract_sequence(groups, None)
        assert "[LIKELY_MELODY]" in text
        assert "[UNMAPPED]" not in text
        assert "[OK]" not in text


class TestBuildExtractPrompt:
    def test_contains_sequence(self):
        events = [
            RawMidiEvent(time_ms=0, note=60, duration_ms=100, velocity=80, program=None),
        ]
        meta = MidiMeta(bpm=120.0, time_signature="4/4")
        prompt = build_extract_prompt(events, filename="test_song", meta=meta)
        assert "60" in prompt
        assert "melody" in prompt.lower()
        assert "accompaniment" in prompt.lower()
        assert "bass" in prompt.lower()


# ── AI extract response parsing ────────────────────────────


class TestParseExtractResponse:
    def test_valid_response(self):
        data = [
            {"time_ms": 0, "note": 72, "role": "melody"},
            {"time_ms": 0, "note": 60, "role": "bass"},
            {"time_ms": 0, "note": 64, "role": "accompaniment"},
        ]
        response = json.dumps(data)
        result = parse_extract_response(response)
        assert len(result) == 3
        assert result[0].role == "melody"
        assert result[1].role == "bass"
        assert result[2].role == "accompaniment"

    def test_invalid_role_defaults_to_accompaniment(self):
        data = [{"time_ms": 0, "note": 60, "role": "unknown_role"}]
        result = parse_extract_response(json.dumps(data))
        assert result[0].role == "accompaniment"

    def test_skips_incomplete_items(self):
        data = [
            {"time_ms": 0, "note": 60, "role": "melody"},
            {"time_ms": 100},
            {"note": 60, "role": "bass"},
        ]
        result = parse_extract_response(json.dumps(data))
        assert len(result) == 1

    def test_with_markdown_wrapper(self):
        data = [{"time_ms": 0, "note": 60, "role": "melody"}]
        response = f"```json\n{json.dumps(data)}\n```"
        result = parse_extract_response(response)
        assert len(result) == 1

    def test_with_analysis_header(self):
        data = [{"time_ms": 0, "note": 60, "role": "melody"}]
        response = f"## Analysis\nSome analysis text.\n\n## Roles\n{json.dumps(data)}"
        analysis, roles = _parse_extract_ai_response(response)
        assert "Some analysis" in analysis
        assert len(roles) == 1
        assert roles[0].role == "melody"

    def test_case_insensitive_role(self):
        data = [{"time_ms": 0, "note": 60, "role": "MELODY"}]
        result = parse_extract_response(json.dumps(data))
        assert result[0].role == "melody"
