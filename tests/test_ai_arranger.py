import json

import pytest

import src.application.ai_arranger as ai_arranger
from src.application.ai_arranger import (
    PositionRemap,
    _enforce_context_rules,
    _extract_balanced,
    _midi_to_name,
    _repair_truncated_json,
    _split_events_into_chunks,
    get_arrange_precheck,
    parse_analysis_and_json,
    parse_ai_response,
    parse_context_response,
    parse_remap_response,
    validate_note_map,
    validate_position_map,
)
from src.domain.mapping import MappingConfig, MappingProfile
from src.infrastructure.midi_reader import MidiKeyAnalysis, MidiMeta, RawMidiEvent


class TestExtractBalanced:
    def test_simple_object(self):
        assert _extract_balanced('{"a": 1}', "{", "}") == '{"a": 1}'

    def test_nested_object(self):
        text = '{"a": {"b": 1}}'
        assert _extract_balanced(text, "{", "}") == text

    def test_with_prefix(self):
        text = 'some text {"a": 1} more text'
        assert _extract_balanced(text, "{", "}") == '{"a": 1}'

    def test_simple_array(self):
        text = '[1, 2, 3]'
        assert _extract_balanced(text, "[", "]") == text

    def test_nested_array(self):
        text = '[{"a": 1}, {"b": [2, 3]}]'
        assert _extract_balanced(text, "[", "]") == text

    def test_string_with_braces(self):
        text = '{"key": "value with {braces}"}'
        assert _extract_balanced(text, "{", "}") == text

    def test_no_match(self):
        assert _extract_balanced("no braces here", "{", "}") is None

    def test_unclosed(self):
        text = '{"a": 1'
        result = _extract_balanced(text, "{", "}")
        assert result == text


class TestParseRemapResponse:
    def test_simple(self):
        result = parse_remap_response('{"61": 60, "63": 64}')
        assert result == {61: 60, 63: 64}

    def test_with_analysis(self):
        text = """## Analysis
Some analysis text

## Mapping
{"61": 60, "63": 64}"""
        result = parse_remap_response(text)
        assert result == {61: 60, 63: 64}

    def test_with_code_fence(self):
        text = """```json
{"61": 60}
```"""
        result = parse_remap_response(text)
        assert result == {61: 60}

    def test_with_drop(self):
        result = parse_remap_response('{"61": 60, "73": -1}')
        assert result[73] == -1


class TestParseContextResponse:
    def test_simple(self):
        data = [
            {"time_ms": 100, "original": 61, "replacement": 60},
            {"time_ms": 200, "original": 63, "replacement": 64},
        ]
        result = parse_context_response(json.dumps(data))
        assert len(result) == 2
        assert result[0].time_ms == 100
        assert result[0].original == 61
        assert result[0].replacement == 60

    def test_skips_invalid(self):
        data = [
            {"time_ms": 100, "original": 61, "replacement": 60},
            {"bad": "entry"},
            {"time_ms": 200, "original": 63, "replacement": 64},
        ]
        result = parse_context_response(json.dumps(data))
        assert len(result) == 2

    def test_with_code_fence(self):
        text = """```json
[{"time_ms": 100, "original": 61, "replacement": 60}]
```"""
        result = parse_context_response(text)
        assert len(result) == 1


class TestParseAnalysisAndJson:
    def test_with_headers(self):
        text = """## Analysis
Some analysis here

## Mapping
{"61": 60}"""
        analysis, json_text = parse_analysis_and_json(text)
        assert "analysis" in analysis.lower()
        assert "{" in json_text

    def test_no_headers(self):
        text = 'Some text {"61": 60}'
        analysis, json_text = parse_analysis_and_json(text)
        assert "Some text" in analysis
        assert "{" in json_text


class TestParseAiResponse:
    def test_context_mode(self):
        text = """## Analysis
旋律下行

## Mapping
[{"time_ms": 100, "original": 61, "replacement": 60}]"""
        analysis, note_map, position_map = parse_ai_response(text, "context")
        assert "旋律" in analysis
        assert note_map == {}
        assert len(position_map) == 1

    def test_invalid_json_raises_structured_error(self):
        with pytest.raises(ai_arranger.AiArrangeError):
            parse_ai_response("## Mapping\n{broken", "remap")


class TestRepairTruncatedJson:
    def test_truncated_object_after_value(self):
        text = '{"61": 60, "63": 64, "65": 67, "70"'
        repaired = _repair_truncated_json(text)
        data = json.loads(repaired)
        assert "63" in data

    def test_truncated_array(self):
        text = '[{"time_ms": 100, "original": 61, "replacement": 60}, {"time_ms": 200'
        repaired = _repair_truncated_json(text)
        data = json.loads(repaired)
        assert len(data) >= 1

    def test_valid_json(self):
        text = '{"a": 1}'
        assert _repair_truncated_json(text) == text


class TestValidateNoteMap:
    def test_all_valid(self):
        available = [60, 62, 64, 65, 67]
        available_set = set(available)
        note_map = {61: 60, 63: 64}
        fixed, count = validate_note_map(note_map, available_set, available)
        assert count == 0
        assert fixed == note_map

    def test_invalid_snapped(self):
        available = [60, 62, 64, 65, 67]
        available_set = set(available)
        note_map = {61: 63}  # 63 not available, should snap to 62 or 64
        fixed, count = validate_note_map(note_map, available_set, available)
        assert count == 1
        assert fixed[61] in available_set

    def test_drop_preserved(self):
        available = [60, 62, 64]
        available_set = set(available)
        note_map = {61: -1}
        fixed, count = validate_note_map(note_map, available_set, available)
        assert count == 0
        assert fixed[61] == -1


class TestValidatePositionMap:
    def test_all_valid(self):
        available = [60, 62, 64, 65, 67]
        available_set = set(available)
        pmap = [PositionRemap(100, 61, 60), PositionRemap(200, 63, 64)]
        fixed, count = validate_position_map(pmap, available_set, available)
        assert count == 0
        assert len(fixed) == 2

    def test_invalid_corrected(self):
        available = [60, 62, 64, 65, 67]
        available_set = set(available)
        pmap = [PositionRemap(100, 61, 63)]
        fixed, count = validate_position_map(pmap, available_set, available)
        assert count == 1
        assert fixed[0].replacement in available_set

    def test_drop_preserved(self):
        available = [60, 62, 64]
        available_set = set(available)
        pmap = [PositionRemap(100, 61, -1)]
        fixed, count = validate_position_map(pmap, available_set, available)
        assert count == 0
        assert fixed[0].replacement == -1


class TestMidiToName:
    def test_c4(self):
        assert _midi_to_name(60) == "C4"

    def test_a4(self):
        assert _midi_to_name(69) == "A4"

    def test_c_sharp(self):
        assert _midi_to_name(61) == "C#4"


class TestArrangePrecheck:
    def test_context_precheck_uses_detected_key(self, monkeypatch, tmp_path):
        mapping = MappingConfig(
            default_profile="default",
            profiles={"default": MappingProfile(note_to_key={"60": "a", "62": "b", "64": "c"})},
        )
        midi_path = tmp_path / "demo.mid"
        midi_path.write_bytes(b"")
        events = [
            RawMidiEvent(time_ms=0, note=60, duration_ms=400, velocity=80, program=None),
            RawMidiEvent(time_ms=500, note=62, duration_ms=400, velocity=90, program=None),
        ]

        monkeypatch.setattr(ai_arranger, "read_midi_events", lambda *args, **kwargs: (events, 480, 1))
        monkeypatch.setattr(
            ai_arranger,
            "read_midi_meta",
            lambda *args, **kwargs: MidiMeta(bpm=120.0, time_signature="4/4", total_notes=2, duration_sec=1.0),
        )
        monkeypatch.setattr(
            ai_arranger,
            "analyze_midi_key",
            lambda *args, **kwargs: MidiKeyAnalysis(detected_key="D", detected_mode="minor"),
        )

        precheck = get_arrange_precheck(
            midi_path,
            mapping,
            "default",
            mode="context",
            style="balanced",
        )

        assert precheck.available_notes == [60, 62, 64]
        assert precheck.likely_key == "D minor"
        assert precheck.estimated_tokens > 0


class TestChunkingAndContextRules:
    def test_split_events_prefers_phrase_boundary(self):
        meta = MidiMeta(bpm=120.0, time_signature="4/4")
        events = [
            RawMidiEvent(time_ms=0, note=60, duration_ms=200, velocity=80, program=None),
            RawMidiEvent(time_ms=100, note=64, duration_ms=200, velocity=70, program=None),
            RawMidiEvent(time_ms=1900, note=67, duration_ms=300, velocity=75, program=None),
            RawMidiEvent(time_ms=4100, note=72, duration_ms=300, velocity=95, program=None),
            RawMidiEvent(time_ms=4300, note=74, duration_ms=300, velocity=90, program=None),
            RawMidiEvent(time_ms=8000, note=76, duration_ms=300, velocity=88, program=None),
        ]

        chunks = _split_events_into_chunks(events, meta, bars_per_chunk=2, overlap_bars=0)

        assert len(chunks) >= 2
        assert chunks[0][-1].time_ms == 1900
        assert chunks[1][0].time_ms == 4100

    def test_enforce_context_rules_preserves_melody_direction(self):
        events = [
            RawMidiEvent(time_ms=0, note=72, duration_ms=400, velocity=110, program=None),
            RawMidiEvent(time_ms=0, note=60, duration_ms=400, velocity=60, program=None),
            RawMidiEvent(time_ms=500, note=69, duration_ms=400, velocity=108, program=None),
            RawMidiEvent(time_ms=500, note=60, duration_ms=400, velocity=60, program=None),
        ]
        position_map = [
            PositionRemap(0, 72, 64),
            PositionRemap(500, 69, 67),
        ]

        fixed, count = _enforce_context_rules(
            position_map,
            events,
            shift=0,
            available_set={60, 62, 64, 67},
            available_sorted=[60, 62, 64, 67],
            meta=MidiMeta(bpm=120.0, time_signature="4/4"),
        )

        assert count >= 1
        assert fixed[1].replacement < fixed[0].replacement
