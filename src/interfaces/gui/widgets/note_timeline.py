"""Clickable note-density timeline for choosing a playback start position."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from src.domain.chart import ChartEvent
from src.interfaces.gui.style import current_theme


_BAR_LEFT = 40
_BAR_RIGHT = 40
_BAR_TOP = 6
_BAR_HEIGHT = 20
_WIDGET_HEIGHT = 40


class NoteTimelineWidget(QWidget):
    """Displays note density over time and lets the user click to pick a
    start position.  Emits *position_changed(ms)* when the marker moves."""

    position_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(_WIDGET_HEIGHT)
        self.setMouseTracking(True)

        self._bins: list[float] = []
        self._total_ms: int = 0
        self._position_ms: int = 0
        self._hover_x: int | None = None

    # ── public API ───────────────────────────────────────────

    @property
    def position_ms(self) -> int:
        return self._position_ms

    def set_events(self, events: list[ChartEvent], total_ms: int) -> None:
        self._total_ms = max(total_ms, 1)
        self._position_ms = 0
        self._bins = _compute_bins(events, self._total_ms, self._bar_width())
        self.update()

    def set_position(self, ms: int) -> None:
        ms = max(0, min(ms, self._total_ms))
        if ms != self._position_ms:
            self._position_ms = ms
            self.position_changed.emit(ms)
            self.update()

    def clear(self) -> None:
        self._bins.clear()
        self._total_ms = 0
        self._position_ms = 0
        self.update()

    # ── geometry helpers ─────────────────────────────────────

    def _bar_width(self) -> int:
        return max(self.width() - _BAR_LEFT - _BAR_RIGHT, 1)

    def _bar_rect(self) -> QRectF:
        return QRectF(_BAR_LEFT, _BAR_TOP, self._bar_width(), _BAR_HEIGHT)

    def _x_to_ms(self, x: float) -> int:
        bw = self._bar_width()
        ratio = max(0.0, min((x - _BAR_LEFT) / bw, 1.0))
        return int(ratio * self._total_ms)

    def _ms_to_x(self, ms: int) -> float:
        if self._total_ms <= 0:
            return float(_BAR_LEFT)
        return _BAR_LEFT + (ms / self._total_ms) * self._bar_width()

    # ── painting ─────────────────────────────────────────────

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._total_ms:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        dark = current_theme() == "dark"
        bar = self._bar_rect()

        # background
        bg = QColor(44, 44, 74, 160) if dark else QColor(220, 220, 235, 180)
        path = QPainterPath()
        path.addRoundedRect(bar, 4, 4)
        painter.fillPath(path, bg)

        # density bars
        bins = self._bins
        bw = self._bar_width()
        n = min(len(bins), bw)
        if n > 0:
            col_w = bw / n
            accent = QColor("#7c6bf5") if dark else QColor("#6c5ce7")
            for i in range(n):
                v = bins[i]
                if v <= 0:
                    continue
                alpha = int(40 + 200 * v)
                c = QColor(accent)
                c.setAlpha(alpha)
                x = bar.left() + i * col_w
                h = max(v * _BAR_HEIGHT, 1)
                painter.fillRect(QRectF(x, bar.bottom() - h, col_w + 0.5, h), c)

        # border
        border_col = QColor("#3d3d5c") if dark else QColor("#d0d0da")
        painter.setPen(QPen(border_col, 1))
        painter.drawRoundedRect(bar, 4, 4)

        # position marker
        mx = self._ms_to_x(self._position_ms)
        marker_col = QColor("#ffffff") if dark else QColor("#333333")
        painter.setPen(QPen(marker_col, 2))
        painter.drawLine(QPointF(mx, bar.top()), QPointF(mx, bar.bottom()))
        tri = QPainterPath()
        tri.moveTo(mx - 4, bar.top())
        tri.lineTo(mx + 4, bar.top())
        tri.lineTo(mx, bar.top() + 5)
        tri.closeSubpath()
        painter.fillPath(tri, marker_col)

        # time labels
        text_col = QColor("#9999b3") if dark else QColor("#7a7a8c")
        painter.setPen(text_col)
        f = painter.font()
        f.setPixelSize(10)
        painter.setFont(f)
        painter.drawText(
            QRectF(0, _BAR_TOP, _BAR_LEFT - 4, _BAR_HEIGHT),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            _fmt(self._position_ms),
        )
        painter.drawText(
            QRectF(bar.right() + 4, _BAR_TOP, _BAR_RIGHT - 4, _BAR_HEIGHT),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            _fmt(self._total_ms),
        )
        painter.end()

    # ── mouse interaction ────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._total_ms:
            self._apply_mouse(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._total_ms:
            ms = self._x_to_ms(event.position().x())
            QToolTip.showText(event.globalPosition().toPoint(), _fmt(ms), self)
            if event.buttons() & Qt.MouseButton.LeftButton:
                self._apply_mouse(event)

    def _apply_mouse(self, event: QMouseEvent) -> None:
        ms = self._x_to_ms(event.position().x())
        self.set_position(ms)

    def resizeEvent(self, event) -> None:  # noqa: N802
        if self._total_ms:
            self._bins = _compute_bins_from_cache(self._bins, self._bar_width())
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


def _compute_bins_from_cache(old_bins: list[float], new_count: int) -> list[float]:
    """Quick resample when widget resizes — avoids keeping full event list."""
    if not old_bins or new_count <= 0:
        return []
    old_n = len(old_bins)
    result: list[float] = []
    for i in range(new_count):
        src = i * old_n / new_count
        idx = min(int(src), old_n - 1)
        result.append(old_bins[idx])
    return result


def _fmt(ms: int) -> str:
    total_sec = max(0, ms // 1000)
    m, s = divmod(total_sec, 60)
    return f"{m:02d}:{s:02d}"
