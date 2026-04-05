from mido import Message, MidiFile, MidiTrack

from src.infrastructure.midi_reader import analyze_midi_key


class TestAnalyzeMidiKey:
    def test_detects_major_center_from_weighted_note_lengths(self, tmp_path):
        midi_path = tmp_path / "weighted-key.mid"
        midi = MidiFile(ticks_per_beat=480)
        track = MidiTrack()
        midi.tracks.append(track)

        # Long D major chord tones.
        for note in (62, 66, 69):
            track.append(Message("note_on", note=note, velocity=80, time=0))
        track.append(Message("note_off", note=62, velocity=0, time=480))
        track.append(Message("note_off", note=66, velocity=0, time=0))
        track.append(Message("note_off", note=69, velocity=0, time=0))

        # Short passing chromatic notes should not dominate the estimate.
        for note in (60, 63, 65, 68):
            track.append(Message("note_on", note=note, velocity=60, time=0))
            track.append(Message("note_off", note=note, velocity=0, time=60))

        midi.save(str(midi_path))

        result = analyze_midi_key(midi_path)

        top_note, top_weight = result.note_distribution[0]
        chromatic_weights = {name: weight for name, weight in result.note_distribution if name in {"C", "D#", "F", "G#"}}

        assert top_note == "D"
        assert all(top_weight > weight for weight in chromatic_weights.values())
