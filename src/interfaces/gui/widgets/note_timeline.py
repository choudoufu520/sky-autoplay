"""Clickable note-density timeline for choosing a playback start position.

Supports zoom (mouse wheel) and pan (Shift+drag or middle-button drag).
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPainterPath, QPen, QWheelEvent
from PySide6.QtWidgets import QToolTip, QWidget

from src.domain.chart import ChartEvent
from src.interfaces.gui.style import current_theme


_BAR_LEFT = 40
_BAR_RIGHT = 40
_BAR_TOP = 6
_BAR_HEIGHT = 20
_WIDGET_HEIGHT = 40

_ZOOM_MIN = 1.0
_ZOOM_MAX = 16.0
_ZOOM_STEP = 1.25


class NoteTimelineWidget(QWidget):
    """Displays note density over time and lets the user click to pick a
    start position.  Emits *position_changed(ms)* when the marker moves."""

    position_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None, *, bar_height: int = _BAR_HEIGHT) -> None:
        super().__init__(parent)
        self._bar_height = bar_height
        widget_h = _BAR_TOP + bar_height + _BAR_TOP
        self.setFixedHeight(widget_h)
        self.setMouseTracking(True)

        self._events: list[ChartEvent] = []
        self._bins: list[float] = []
        self._total_ms: int = 0
        self._position_ms: int = 0

        self._zoom: float = 1.0
        self._view_start_ms: int = 0
        self._pan_origin: int | None = None

    # ── public API ───────────────────────────────────────────

    @property
    def position_ms(self) -> int:
        return self._position_ms

    @property
    def total_ms(self) -> int:
        return self._total_ms

    @property
    def events(self) -> list[ChartEvent]:
        return self._events

    def set_events(self, events: list[ChartEvent], total_ms: int) -> None:
        self._events = list(events)
        self._total_ms = max(total_ms, 1)
        self._position_ms = 0
        self._zoom = 1.0
        self._view_start_ms = 0
        self._recompute_bins()
        self.update()

    def set_position(self, ms: int) -> None:
        ms = max(0, min(ms, self._total_ms))
        if ms != self._position_ms:
            self._position_ms = ms
            self.position_changed.emit(ms)
            self.update()

    def reset(self) -> None:
        self.set_position(0)

    def clear(self) -> None:
        self._events.clear()
        self._bins.clear()
        self._total_ms = 0
        self._position_ms = 0
        self._zoom = 1.0
        self._view_start_ms = 0
        self.update()

    # ── geometry helpers ─────────────────────────────────────

    def _bar_width(self) -> int:
        return max(self.width() - _BAR_LEFT - _BAR_RIGHT, 1)

    def _bar_rect(self) -> QRectF:
        return QRectF(_BAR_LEFT, _BAR_TOP, self._bar_width(), self._bar_height)

    def _view_span_ms(self) -> int:
        if self._zoom <= 1.0:
            return self._total_ms
        return max(int(self._total_ms / self._zoom), 1)

    def _clamp_view(self) -> None:
        span = self._view_span_ms()
        self._view_start_ms = max(0, min(self._view_start_ms, self._total_ms - span))

    def _x_to_ms(self, x: float) -> int:
        bw = self._bar_width()
        ratio = max(0.0, min((x - _BAR_LEFT) / bw, 1.0))
        span = self._view_span_ms()
        return int(self._view_start_ms + ratio * span)

    def _ms_to_x(self, ms: int) -> float:
        span = self._view_span_ms()
        if span <= 0:
            return float(_BAR_LEFT)
        return _BAR_LEFT + ((ms - self._view_start_ms) / span) * self._bar_width()

    # ── bins ─────────────────────────────────────────────────

    def _recompute_bins(self) -> None:
        num = max(self._bar_width(), 1)
        self._bins = _compute_bins(self._events, self._total_ms, num)

    # ── painting ─────────────────────────────────────────────

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._total_ms:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        dark = current_theme() == "dark"
        bar = self._bar_rect()

        bg = QColor(44, 44, 74, 160) if dark else QColor(220, 220, 235, 180)
        path = QPainterPath()
        path.addRoundedRect(bar, 4, 4)
        painter.fillPath(path, bg)

        bins = self._bins
        bw = self._bar_width()
        n = len(bins)
        if n > 0:
            span = self._view_span_ms()
            total = self._total_ms
            bin_ms = total / n if n else 1
            vis_start = max(int(self._view_start_ms / bin_ms), 0)
            vis_end = min(int((self._view_start_ms + span) / bin_ms) + 1, n)

            col_w_full = bw * (total / span) / n if span > 0 else bw / n
            accent = QColor("#7c6bf5") if dark else QColor("#6c5ce7")

            for i in range(vis_start, vis_end):
                v = bins[i]
                if v <= 0:
                    continue
                bin_start_ms = i * bin_ms
                x = _BAR_LEFT + (bin_start_ms - self._view_start_ms) / span * bw
                w = col_w_full
                if x < _BAR_LEFT:
                    w -= (_BAR_LEFT - x)
                    x = _BAR_LEFT
                if x + w > bar.right():
                    w = bar.right() - x
                if w <= 0:
                    continue
                alpha = int(40 + 200 * v)
                c = QColor(accent)
                c.setAlpha(alpha)
                h = max(v * self._bar_height, 1)
                painter.fillRect(QRectF(x, bar.bottom() - h, w + 0.5, h), c)

        border_col = QColor("#3d3d5c") if dark else QColor("#d0d0da")
        painter.setPen(QPen(border_col, 1))
        painter.drawRoundedRect(bar, 4, 4)

        mx = self._ms_to_x(self._position_ms)
        if bar.left() <= mx <= bar.right():
            marker_col = QColor("#ffffff") if dark else QColor("#333333")
            painter.setPen(QPen(marker_col, 2))
            painter.drawLine(QPointF(mx, bar.top()), QPointF(mx, bar.bottom()))
            tri = QPainterPath()
            tri.moveTo(mx - 4, bar.top())
            tri.lineTo(mx + 4, bar.top())
            tri.lineTo(mx, bar.top() + 5)
            tri.closeSubpath()
            painter.fillPath(tri, marker_col)

        text_col = QColor("#9999b3") if dark else QColor("#7a7a8c")
        painter.setPen(text_col)
        f = painter.font()
        f.setPixelSize(10)
        painter.setFont(f)

        left_ms = self._view_start_ms
        right_ms = self._view_start_ms + self._view_span_ms()
        painter.drawText(
            QRectF(0, _BAR_TOP, _BAR_LEFT - 4, self._bar_height),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            _fmt(left_ms),
        )
        painter.drawText(
            QRectF(bar.right() + 4, _BAR_TOP, _BAR_RIGHT - 4, self._bar_height),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            _fmt(right_ms),
        )

        if self._zoom > 1.05:
            painter.drawText(
                QRectF(bar.right() - 50, bar.bottom() - 14, 46, 12),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
                f"{self._zoom:.1f}x",
            )

        painter.end()

    # ── mouse interaction ────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.MiddleButton and self._total_ms:
            self._pan_origin = int(event.position().x())
            return
        if event.button() == Qt.MouseButton.LeftButton and self._total_ms:
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self._pan_origin = int(event.position().x())
            else:
                self._apply_mouse(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if not self._total_ms:
            return

        if self._pan_origin is not None:
            dx = int(event.position().x()) - self._pan_origin
            self._pan_origin = int(event.position().x())
            span = self._view_span_ms()
            ms_per_px = span / self._bar_width() if self._bar_width() else 1
            self._view_start_ms -= int(dx * ms_per_px)
            self._clamp_view()
            self.update()
            return

        ms = self._x_to_ms(event.position().x())
        QToolTip.showText(event.globalPosition().toPoint(), _fmt(ms), self)
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._apply_mouse(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.LeftButton):
            self._pan_origin = None

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if not self._total_ms:
            return
        mouse_ms = self._x_to_ms(event.position().x())

        if event.angleDelta().y() > 0:
            self._zoom = min(self._zoom * _ZOOM_STEP, _ZOOM_MAX)
        else:
            self._zoom = max(self._zoom / _ZOOM_STEP, _ZOOM_MIN)

        span = self._view_span_ms()
        bw = self._bar_width()
        ratio = max(0.0, min((event.position().x() - _BAR_LEFT) / bw, 1.0))
        self._view_start_ms = int(mouse_ms - ratio * span)
        self._clamp_view()
        self.update()

    def _apply_mouse(self, event: QMouseEvent) -> None:
        ms = self._x_to_ms(event.position().x())
        self.set_position(ms)

    def resizeEvent(self, event) -> None:  # noqa: N802
        if self._events:
            self._recompute_bins()
        super().resizeEvent(event)


# ── helpers ──────────────────────────────────────────────────


def _compute_bins(events: list[ChartEvent], total_ms: int, num_bins: int) -> list[float]:
    if num_bins <= 0 or total_ms <= 0:
        return []
    counts = [0] * num_bins
    for ev in events:
        idx = int(ev.time_ms / total_ms * num_bins)
        idx = min(idx, num_bins - 1)
        if ev.action in ("tap", "down"):
            counts[idx] += 1
    peak = max(counts) if counts else 1
    if peak == 0:
        peak = 1
    return [c / peak for c in counts]


def _fmt(ms: int) -> str:
    total_sec = max(0, ms // 1000)
    m, s = divmod(total_sec, 60)
    return f"{m:02d}:{s:02d}"
