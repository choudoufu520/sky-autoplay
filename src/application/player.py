from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from src.domain.chart import ChartDocument, ChartEvent
from src.infrastructure.input_backends import BaseInputBackend, DryRunInputBackend

LogCallback = Callable[[str], None]


@dataclass(slots=True)
class PlayOptions:
    latency_offset_ms: int = 0
    countdown_sec: int = 3
    chord_stagger_ms: int = 0
    dry_run: bool = False
    debug: bool = False


def play_chart(
    chart: ChartDocument,
    backend: BaseInputBackend | None,
    options: PlayOptions,
    stop_event: threading.Event | None = None,
    log: LogCallback | None = None,
) -> None:
    def _log(msg: str) -> None:
        if log:
            log(msg)
        else:
            print(msg)

    def _stopped() -> bool:
        return stop_event is not None and stop_event.is_set()

    if options.dry_run:
        _print_dry_run(chart, log=_log)
        return

    if backend is None:
        backend = DryRunInputBackend()

    if options.countdown_sec > 0:
        for remain in range(options.countdown_sec, 0, -1):
            if _stopped():
                _log("[stopped] cancelled during countdown")
                return
            _log(f"[countdown] {remain}...")
            time.sleep(1)

    start = time.perf_counter()
    pressed_keys: set[str] = set()

    grouped = _group_by_time(chart.events)
    for time_ms, events in grouped:
        if _stopped():
            break
        target_ms = time_ms + options.latency_offset_ms
        _wait_until(start, target_ms, stop_event)
        if _stopped():
            break

        for idx, event in enumerate(events):
            if _stopped():
                break
            if options.chord_stagger_ms > 0 and idx > 0:
                time.sleep(options.chord_stagger_ms / 1000)
            _dispatch_event(event, backend, pressed_keys, options.debug, _log)

    for key in list(pressed_keys):
        backend.key_up(key)

    if _stopped():
        _log("[stopped] playback interrupted, all keys released")
    else:
        _log("[done] playback complete")


def _group_by_time(events: list[ChartEvent]) -> list[tuple[int, list[ChartEvent]]]:
    ordered = sorted(events, key=lambda x: x.time_ms)
    buckets: list[tuple[int, list[ChartEvent]]] = []
    for event in ordered:
        if buckets and buckets[-1][0] == event.time_ms:
            buckets[-1][1].append(event)
        else:
            buckets.append((event.time_ms, [event]))
    return buckets


def _wait_until(
    start_time: float, target_ms: int, stop_event: threading.Event | None = None
) -> None:
    while True:
        if stop_event is not None and stop_event.is_set():
            return
        now_ms = int((time.perf_counter() - start_time) * 1000)
        remain = target_ms - now_ms
        if remain <= 0:
            return
        if remain > 4:
            time.sleep((remain - 2) / 1000)


def _dispatch_event(
    event: ChartEvent,
    backend: BaseInputBackend,
    pressed_keys: set[str],
    debug: bool,
    log: LogCallback | None = None,
) -> None:
    if debug:
        msg = f"[event] t={event.time_ms} action={event.action} key={event.key}"
        if log:
            log(msg)
        else:
            print(msg)

    if event.action == "tap":
        backend.tap(event.key, event.duration_ms or 30)
        return

    if event.action == "down":
        if event.key not in pressed_keys:
            backend.key_down(event.key)
            pressed_keys.add(event.key)
        return

    if event.action == "up" and event.key in pressed_keys:
        backend.key_up(event.key)
        pressed_keys.remove(event.key)


def _print_dry_run(chart: ChartDocument, limit: int = 80, log: LogCallback | None = None) -> None:
    def _out(msg: str) -> None:
        if log:
            log(msg)
        else:
            print(msg)

    _out(f"[dry-run] total_events={len(chart.events)}")
    for idx, event in enumerate(sorted(chart.events, key=lambda x: x.time_ms)):
        if idx >= limit:
            _out(f"[dry-run] ... truncated, remaining={len(chart.events) - limit}")
            break
        _out(f"{event.time_ms:>6}ms  {event.action:<4}  key={event.key:<8} profile={event.mapping_profile}")
