import threading

from src.application.player import PlayOptions, play_chart, _group_by_time
from src.domain.chart import ChartDocument, ChartEvent


def _make_chart(events: list[tuple[int, str]]) -> ChartDocument:
    return ChartDocument(
        events=[
            ChartEvent(time_ms=t, key=k, action="tap")
            for t, k in events
        ],
    )


class TestGroupByTime:
    def test_groups_same_time(self):
        events = [
            ChartEvent(time_ms=100, key="a", action="tap"),
            ChartEvent(time_ms=100, key="b", action="tap"),
            ChartEvent(time_ms=200, key="c", action="tap"),
        ]
        groups = _group_by_time(events)
        assert len(groups) == 2
        assert groups[0][0] == 100
        assert len(groups[0][1]) == 2
        assert groups[1][0] == 200

    def test_empty(self):
        assert _group_by_time([]) == []


class TestPlayChartStartMs:
    def test_start_ms_zero_plays_all(self):
        chart = _make_chart([(0, "a"), (100, "b"), (200, "c")])
        played: list[str] = []

        def on_key(current: list[str], _upcoming):
            played.extend(current)

        play_chart(
            chart,
            backend=None,
            options=PlayOptions(start_ms=0, countdown_sec=0),
            key_display=on_key,
        )
        assert played == ["a", "b", "c"]

    def test_start_ms_skips_early_events(self):
        chart = _make_chart([(0, "a"), (100, "b"), (200, "c"), (300, "d")])
        played: list[str] = []

        def on_key(current: list[str], _upcoming):
            played.extend(current)

        play_chart(
            chart,
            backend=None,
            options=PlayOptions(start_ms=150, countdown_sec=0),
            key_display=on_key,
        )
        assert played == ["c", "d"]

    def test_start_ms_beyond_end_plays_nothing(self):
        chart = _make_chart([(0, "a"), (100, "b")])
        played: list[str] = []

        def on_key(current: list[str], _upcoming):
            played.extend(current)

        play_chart(
            chart,
            backend=None,
            options=PlayOptions(start_ms=9999, countdown_sec=0),
            key_display=on_key,
        )
        assert played == []

    def test_progress_reports_adjusted_total(self):
        chart = _make_chart([(0, "a"), (500, "b"), (1000, "c")])
        totals: list[int] = []

        def on_progress(_cur, _tot, _elapsed, total_ms):
            totals.append(total_ms)

        play_chart(
            chart,
            backend=None,
            options=PlayOptions(start_ms=500, countdown_sec=0),
            progress=on_progress,
        )
        assert all(t == 500 for t in totals)

    def test_stop_event_respected(self):
        chart = _make_chart([(0, "a"), (5000, "b")])
        stop = threading.Event()
        stop.set()

        played: list[str] = []

        def on_key(current: list[str], _upcoming):
            played.extend(current)

        play_chart(
            chart,
            backend=None,
            options=PlayOptions(start_ms=0, countdown_sec=0),
            stop_event=stop,
            key_display=on_key,
        )
        assert played == []
