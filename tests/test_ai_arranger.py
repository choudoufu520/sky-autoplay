import json
import sys
import types

import pytest

import src.application.ai_arranger as ai_arranger
from src.application.ai_arranger import (
    PositionRemap,
    _count_total_bars,
    _enforce_context_rules,
    _extract_balanced,
    _format_stream_progress_text,
    _midi_to_name,
    _repair_truncated_json,
    _requires_context_chunking,
    _requires_context_chunking_by_bars,
    _split_events_into_chunks,
    ai_arrange,
    find_optimal_settings,
    get_arrange_precheck,
    parse_analysis_and_json,
    parse_ai_response,
    parse_context_response,
    parse_remap_response,
    transpose_to_key_name,
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


class TestStreamProgress:
    def test_waiting_for_first_token_message(self):
        assert _format_stream_progress_text("", 10, waiting_for_first_token=True) == (
            "[Waiting for AI response... 10s]"
        )

    def test_waiting_for_next_token_message(self):
        text = _format_stream_progress_text("## Analysis\nWorking", 12, waiting_for_first_token=False)
        assert text.startswith("## Analysis\nWorking")
        assert text.endswith("[Streaming paused... 12s since last update]")


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

    def test_context_precheck_chunks_when_bar_count_is_large(self, monkeypatch, tmp_path):
        mapping = MappingConfig(
            default_profile="default",
            profiles={"default": MappingProfile(note_to_key={"60": "a", "62": "b", "64": "c"})},
        )
        midi_path = tmp_path / "long.mid"
        midi_path.write_bytes(b"")
        events = [
            RawMidiEvent(time_ms=i * 2000, note=60, duration_ms=300, velocity=80, program=None)
            for i in range(60)
        ]

        monkeypatch.setattr(ai_arranger, "read_midi_events", lambda *args, **kwargs: (events, 480, 1))
        monkeypatch.setattr(
            ai_arranger,
            "read_midi_meta",
            lambda *args, **kwargs: MidiMeta(bpm=120.0, time_signature="4/4", total_notes=len(events), duration_sec=120.0),
        )
        monkeypatch.setattr(ai_arranger, "analyze_midi_key", lambda *args, **kwargs: MidiKeyAnalysis())

        precheck = get_arrange_precheck(
            midi_path,
            mapping,
            "default",
            mode="context",
        )

        assert precheck.estimated_tokens < 24_000
        assert precheck.requires_chunking is True

    def test_context_precheck_uses_chunk_sample_for_long_song(self, monkeypatch, tmp_path):
        mapping = MappingConfig(
            default_profile="default",
            profiles={"default": MappingProfile(note_to_key={"60": "a", "62": "b", "64": "c"})},
        )
        midi_path = tmp_path / "long.mid"
        midi_path.write_bytes(b"")
        events = [
            RawMidiEvent(time_ms=i * 2000, note=60, duration_ms=300, velocity=80, program=None)
            for i in range(60)
        ]
        seen_lengths: list[int] = []

        monkeypatch.setattr(ai_arranger, "read_midi_events", lambda *args, **kwargs: (events, 480, 1))
        monkeypatch.setattr(
            ai_arranger,
            "read_midi_meta",
            lambda *args, **kwargs: MidiMeta(bpm=120.0, time_signature="4/4", total_notes=len(events), duration_sec=120.0),
        )
        monkeypatch.setattr(ai_arranger, "analyze_midi_key", lambda *args, **kwargs: MidiKeyAnalysis())

        def _fake_build_context_prompt(available, prompt_events, *args, **kwargs):
            seen_lengths.append(len(prompt_events))
            return "chunk prompt"

        monkeypatch.setattr(ai_arranger, "build_context_prompt", _fake_build_context_prompt)

        precheck = get_arrange_precheck(midi_path, mapping, "default", mode="context")

        assert precheck.requires_chunking is True
        assert seen_lengths
        assert max(seen_lengths) < len(events)

    def test_context_precheck_sums_each_chunk_token_estimate(self, monkeypatch, tmp_path):
        mapping = MappingConfig(
            default_profile="default",
            profiles={"default": MappingProfile(note_to_key={"60": "a", "62": "b", "64": "c"})},
        )
        midi_path = tmp_path / "chunked.mid"
        midi_path.write_bytes(b"")
        events = [
            RawMidiEvent(time_ms=i * 1000, note=60 + (i % 3), duration_ms=300, velocity=80, program=None)
            for i in range(8)
        ]
        chunks = [events[:2], events[:5], events[:3]]
        seen_lengths: list[int] = []

        monkeypatch.setattr(ai_arranger, "read_midi_events", lambda *args, **kwargs: (events, 480, 1))
        monkeypatch.setattr(
            ai_arranger,
            "read_midi_meta",
            lambda *args, **kwargs: MidiMeta(bpm=120.0, time_signature="4/4", total_notes=len(events), duration_sec=8.0),
        )
        monkeypatch.setattr(ai_arranger, "analyze_midi_key", lambda *args, **kwargs: MidiKeyAnalysis())
        monkeypatch.setattr(ai_arranger, "_requires_context_chunking_by_bars", lambda *args, **kwargs: True)
        monkeypatch.setattr(ai_arranger, "_split_events_into_chunks", lambda *args, **kwargs: chunks)

        def _fake_build_context_prompt(available, prompt_events, *args, **kwargs):
            seen_lengths.append(len(prompt_events))
            return "x" * len(prompt_events)

        monkeypatch.setattr(ai_arranger, "build_context_prompt", _fake_build_context_prompt)
        monkeypatch.setattr(ai_arranger, "_estimate_tokens", lambda prompt: len(prompt))

        precheck = get_arrange_precheck(midi_path, mapping, "default", mode="context")

        assert seen_lengths == [2, 5, 3]
        assert precheck.estimated_tokens == 10

    def test_call_openai_closes_attempt_resources_between_retries(self, monkeypatch):
        created_clients = []
        created_streams = []

        class FakeHttpClient:
            def __init__(self, timeout):
                self.timeout = timeout
                self.closed = 0
                created_clients.append(self)

            def close(self):
                self.closed += 1

        class FakeStream:
            def __init__(self, text):
                self.text = text
                self.closed = 0
                created_streams.append(self)

            def __iter__(self):
                yield types.SimpleNamespace(
                    choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content=self.text))]
                )

            def close(self):
                self.closed += 1

        class APITimeoutError(Exception):
            pass

        class FakeOpenAI:
            create_calls = 0

            def __init__(self, **kwargs):
                self.chat = types.SimpleNamespace(
                    completions=types.SimpleNamespace(create=self._create)
                )

            def _create(self, **kwargs):
                call_index = FakeOpenAI.create_calls
                FakeOpenAI.create_calls += 1
                if call_index < 2:
                    raise APITimeoutError("timed out")
                return FakeStream("ok")

        fake_httpx = types.ModuleType("httpx")
        fake_httpx.Client = FakeHttpClient
        fake_httpx.Timeout = lambda *args, **kwargs: object()
        fake_httpx.TimeoutException = type("TimeoutException", (Exception,), {})
        fake_httpx.NetworkError = type("NetworkError", (Exception,), {})
        fake_httpx.RemoteProtocolError = type("RemoteProtocolError", (Exception,), {})
        fake_httpx.ReadError = type("ReadError", (Exception,), {})
        fake_httpx.WriteError = type("WriteError", (Exception,), {})

        fake_openai = types.ModuleType("openai")
        fake_openai.OpenAI = FakeOpenAI
        fake_openai.BadRequestError = type("BadRequestError", (Exception,), {})

        monkeypatch.setitem(sys.modules, "httpx", fake_httpx)
        monkeypatch.setitem(sys.modules, "openai", fake_openai)
        monkeypatch.setattr(ai_arranger, "_sleep_with_cancel", lambda *args, **kwargs: None)

        result = ai_arranger.call_openai(
            api_key="test-key",
            prompt="hello",
            cancel_state=ai_arranger.CancellableState(),
        )

        assert result == "ok"
        assert len(created_clients) == 3
        assert [client.closed for client in created_clients] == [1, 1, 1]
        assert len(created_streams) == 1
        assert created_streams[0].closed == 1

    def test_find_optimal_settings_respects_cancel(self, monkeypatch, tmp_path):
        mapping = MappingConfig(
            default_profile="default",
            profiles={"default": MappingProfile(note_to_key={"60": "a", "62": "b", "64": "c"})},
        )
        midi_path = tmp_path / "demo.mid"
        midi_path.write_bytes(b"")
        events = [
            RawMidiEvent(time_ms=i * 100, note=60 + (i % 5), duration_ms=200, velocity=80, program=None)
            for i in range(32)
        ]

        monkeypatch.setattr(ai_arranger, "read_midi_events", lambda *args, **kwargs: (events, 480, 1))

        calls = {"count": 0}

        def _should_cancel() -> bool:
            calls["count"] += 1
            return calls["count"] >= 2

        with pytest.raises(ai_arranger.AiArrangeCancelled):
            find_optimal_settings(
                midi_path,
                mapping,
                "default",
                should_cancel=_should_cancel,
            )


class TestChunkingAndContextRules:
    def test_requires_context_chunking_for_long_bar_count(self):
        meta = MidiMeta(bpm=120.0, time_signature="4/4")
        events = [
            RawMidiEvent(time_ms=i * 2000, note=60, duration_ms=300, velocity=80, program=None)
            for i in range(60)
        ]

        assert _count_total_bars(events, meta) == 60
        assert _requires_context_chunking_by_bars(events, meta) is True
        assert _requires_context_chunking(events, meta, estimated_tokens=1000) is True

    def test_ai_arrange_skips_optimal_scan_before_chunked_requests(self, monkeypatch, tmp_path):
        mapping = MappingConfig(
            default_profile="default",
            profiles={"default": MappingProfile(note_to_key={"60": "a", "62": "b", "64": "c"})},
        )
        midi_path = tmp_path / "long.mid"
        midi_path.write_bytes(b"")
        events = [
            RawMidiEvent(time_ms=i * 2000, note=61, duration_ms=300, velocity=80, program=None)
            for i in range(60)
        ]
        calls: list[str] = []

        monkeypatch.setattr(ai_arranger, "read_midi_events", lambda *args, **kwargs: (events, 480, 1))
        monkeypatch.setattr(
            ai_arranger,
            "read_midi_meta",
            lambda *args, **kwargs: MidiMeta(bpm=120.0, time_signature="4/4", total_notes=len(events), duration_sec=120.0),
        )
        monkeypatch.setattr(ai_arranger, "analyze_midi_key", lambda *args, **kwargs: MidiKeyAnalysis())

        def _fake_find_optimal_settings(*args, **kwargs):
            calls.append("optimize")
            raise AssertionError("chunked path should skip optimal scan")

        monkeypatch.setattr(ai_arranger, "find_optimal_settings", _fake_find_optimal_settings)

        def _fake_build_context_prompt(available, prompt_events, *args, **kwargs):
            calls.append(f"prompt:{len(prompt_events)}")
            return "context prompt"

        monkeypatch.setattr(ai_arranger, "build_context_prompt", _fake_build_context_prompt)

        def _fake_call_openai(*args, **kwargs):
            calls.append("request")
            return '## Analysis\nok\n\n## Mapping\n[{"time_ms": 0, "original": 61, "replacement": 60}]'

        monkeypatch.setattr(ai_arranger, "call_openai", _fake_call_openai)

        result = ai_arrange(
            midi_path,
            mapping,
            "default",
            api_key="test-key",
            mode="context",
        )

        assert "optimize" not in calls
        assert "request" in calls
        assert result.mode == "context"

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

    def test_split_events_with_overlap_does_not_loop_forever(self):
        meta = MidiMeta(bpm=120.0, time_signature="4/4")
        events = [
            RawMidiEvent(time_ms=i * 2000, note=60, duration_ms=300, velocity=80, program=None)
            for i in range(60)
        ]

        chunks = _split_events_into_chunks(events, meta, bars_per_chunk=50, overlap_bars=2)

        assert len(chunks) == 2
        assert chunks[0][0].time_ms == 0
        assert chunks[-1][-1].time_ms == events[-1].time_ms

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


class TestTransposeToKeyName:
    def test_no_transpose_is_c_major(self):
        assert transpose_to_key_name(0) == "C major"

    def test_transpose_minus2_is_d_major(self):
        assert transpose_to_key_name(-2) == "D major"

    def test_transpose_plus5_is_g_major(self):
        assert transpose_to_key_name(5) == "G major"
        assert transpose_to_key_name(-7) == "G major"

    def test_profile_transpose_shifts_key(self):
        assert transpose_to_key_name(0, profile_transpose=3) == "A major"

    def test_combined_transpose(self):
        result = transpose_to_key_name(-2, profile_transpose=3)
        root = (0 - (-2) - 3) % 12
        assert root == 11
        assert result == "B major"

    def test_wrap_around(self):
        assert transpose_to_key_name(0, profile_transpose=0) == "C major"
        assert transpose_to_key_name(-12, profile_transpose=0) == "C major"
