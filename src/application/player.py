from __future__ import annotations

import threading
import time
from bisect import bisect_left
from collections.abc import Callable
from dataclasses import dataclass, field

from src.domain.chart import ChartDocument, ChartEvent
from src.infrastructure.input_backends import BaseInputBackend, DryRunInputBackend

LogCallback = Callable[[str], None]
ProgressCallback = Callable[[int, int, int, int], None]
CountdownCallback = Callable[[int], None]
KeyDisplayCallback = Callable[[list[str], list[tuple[int, str]]], None]

LOOKAHEAD_MS = 3000
DEFAULT_TAP_PRESS_MS = 50


@dataclass(slots=True)
class PlayOptions:
    latency_offset_ms: int = 0
    countdown_sec: int = 3
    chord_stagger_ms: int = 0
    tap_press_ms: int = DEFAULT_TAP_PRESS_MS
    dry_run: bool = False
    debug: bool = False
    speed: float = 1.0
    start_ms: int = 0


def play_chart(
    chart: ChartDocument,
    backend: BaseInputBackend | None,
    options: PlayOptions,
    stop_event: threading.Event | None = None,
    log: LogCallback | None = None,
    progress: ProgressCallback | None = None,
    countdown: CountdownCallback | None = None,
    key_display: KeyDisplayCallback | None = None,
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

    if backend is None:
        backend = DryRunInputBackend()

    if options.countdown_sec > 0:
        for remain in range(options.countdown_sec, 0, -1):
            if _stopped():
                _log("[stopped] cancelled during countdown")
                return
            if countdown:
                countdown(remain)
            _log(f"[countdown] {remain}...")
            time.sleep(1)

    speed = max(options.speed, 0.1)
    start_from = max(options.start_ms, 0)

    start = time.perf_counter()
    pressed_keys: set[str] = set()

    grouped = _group_by_time(chart.events)
    if start_from > 0:
        idx = bisect_left(grouped, (start_from,))
        grouped = grouped[idx:]
    total_groups = len(grouped)
    total_ms = (grouped[-1][0] - start_from) if grouped else 0

    for gi, (time_ms, events) in enumerate(grouped):
        if _stopped():
            break
        target_real_ms = int((time_ms - start_from) / speed) + options.latency_offset_ms
        _wait_until(start, target_real_ms, stop_event)
        if _stopped():
            break

        if key_display:
            current_keys = [e.key for e in events if e.action in ("tap", "down")]
            upcoming: list[tuple[int, str]] = []
            for fgi in range(gi + 1, len(grouped)):
                ft, fevts = grouped[fgi]
                offset = int((ft - time_ms) / speed)
                if offset > LOOKAHEAD_MS:
                    break
                for fe in fevts:
                    if fe.action in ("tap", "down"):
                        upcoming.append((offset, fe.key))
            key_display(current_keys, upcoming)

        tap_events = [e for e in events if e.action == "tap"]
        other_events = [e for e in events if e.action != "tap"]

        for event in other_events:
            if _stopped():
                break
            _dispatch_event(event, backend, pressed_keys, options.debug, _log)

        if tap_events and not _stopped():
            _dispatch_tap_group(
                tap_events, backend, options.chord_stagger_ms,
                options.tap_press_ms, options.debug, _log,
            )

        if progress:
            elapsed = int((time.perf_counter() - start) * 1000 * speed)
            progress(gi + 1, total_groups, elapsed, total_ms)

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


def _dispatch_tap_group(
    events: list[ChartEvent],
    backend: BaseInputBackend,
    stagger_ms: int,
    tap_press_ms: int,
    debug: bool,
    log: LogCallback | None = None,
) -> None:
    """Press all tap keys nearly simultaneously, hold for one game-frame
    duration, then release all.  This avoids serial behaviour where each tap
    blocked before the next key could fire."""
    for idx, event in enumerate(events):
        if debug:
            msg = f"[event] t={event.time_ms} action={event.action} key={event.key}"
            if log:
                log(msg)
            else:
                print(msg)
        if stagger_ms > 0 and idx > 0:
            time.sleep(stagger_ms / 1000)
        backend.key_down(event.key)

    time.sleep(max(tap_press_ms, 1) / 1000)

    for event in events:
        backend.key_up(event.key)


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
