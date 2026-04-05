from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from src.interfaces.gui.i18n import tr

STOP_KEY = "F9"


class PlayOverlay(QWidget):
    """Semi-transparent always-on-top overlay that shows playback progress
    without stealing focus from the game window."""

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(360, 140)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(5)

        _label_base = "background: transparent;"

        row1 = QHBoxLayout()
        self.status_label = QLabel()
        self.status_label.setStyleSheet(
            f"color: #ffffff; font-size: 13px; font-weight: bold; {_label_base}"
        )
        row1.addWidget(self.status_label)
        row1.addStretch()
        self.time_label = QLabel()
        self.time_label.setStyleSheet(f"color: #ccccee; font-size: 12px; {_label_base}")
        row1.addWidget(self.time_label)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(10)
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(14)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet(
            "QProgressBar {"
            "  background: rgba(255,255,255,30);"
            "  border: none; border-radius: 7px;"
            "}"
            "QProgressBar::chunk {"
            "  background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "    stop:0 #7c6bf5, stop:1 #9180ff);"
            "  border-radius: 7px;"
            "}"
        )
        row2.addWidget(self.progress_bar, 1)

        self.hotkey_label = QLabel(f"{STOP_KEY} {tr('overlay.stop')}")
        self.hotkey_label.setStyleSheet(
            "color: #ff7777; font-size: 11px; font-weight: bold;"
            "background: rgba(255,80,80,25);"
            "border: 1px solid rgba(255,80,80,60);"
            "border-radius: 4px; padding: 1px 6px;"
        )
        row2.addWidget(self.hotkey_label)
        layout.addLayout(row2)

        row3 = QHBoxLayout()
        row3.setSpacing(4)
        self.current_prefix = QLabel()
        self.current_prefix.setStyleSheet(
            f"color: #9180ff; font-size: 12px; font-weight: bold; {_label_base}"
        )
        self.current_prefix.setFixedWidth(32)
        row3.addWidget(self.current_prefix)
        self.current_keys_label = QLabel()
        self.current_keys_label.setTextFormat(Qt.TextFormat.RichText)
        self.current_keys_label.setStyleSheet(f"font-size: 12px; {_label_base}")
        row3.addWidget(self.current_keys_label, 1)
        layout.addLayout(row3)

        row4 = QHBoxLayout()
        row4.setSpacing(4)
        self.upcoming_prefix = QLabel()
        self.upcoming_prefix.setStyleSheet(
            f"color: #8888aa; font-size: 11px; {_label_base}"
        )
        self.upcoming_prefix.setFixedWidth(32)
        row4.addWidget(self.upcoming_prefix)
        self.upcoming_keys_label = QLabel()
        self.upcoming_keys_label.setTextFormat(Qt.TextFormat.RichText)
        self.upcoming_keys_label.setStyleSheet(f"font-size: 11px; {_label_base}")
        row4.addWidget(self.upcoming_keys_label, 1)
        layout.addLayout(row4)

        self._update_prefix_text()

        self._drag_pos = None
        self._speed = 1.0
        self._move_to_default()

    # ── public API ──────────────────────────────────────────

    def set_speed(self, speed: float) -> None:
        self._speed = speed

    def update_progress(self, current: int, total: int, elapsed_ms: int, total_ms: int) -> None:
        if total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(current)
        self.time_label.setText(f"{_fmt_time(elapsed_ms)} / {_fmt_time(total_ms)}")
        status = f"♪ {tr('overlay.playing')}  {current}/{total}"
        if self._speed != 1.0:
            status += f"  [{self._speed:.2g}x]"
        self.status_label.setText(status)

    def set_countdown(self, seconds: int) -> None:
        self.status_label.setText(tr("overlay.countdown").format(sec=seconds))
        self.progress_bar.setRange(0, 0)
        self.time_label.setText("")
        self.current_keys_label.setText("")
        self.upcoming_keys_label.setText("")

    def update_keys(
        self,
        current_keys: list[str],
        upcoming: list[tuple[int, str]],
    ) -> None:
        self._update_prefix_text()

        if current_keys:
            badges = " ".join(_key_badge(k, current=True) for k in current_keys)
            self.current_keys_label.setText(badges)
        else:
            self.current_keys_label.setText(
                '<span style="color:#666688;">—</span>'
            )

        if upcoming:
            parts: list[str] = []
            last_offset = -1
            for offset_ms, key in upcoming:
                if offset_ms != last_offset:
                    if parts:
                        parts.append(
                            f'<span style="color:#666688; font-size:10px;">'
                            f" +{offset_ms / 1000:.1f}s</span>&nbsp;"
                        )
                    last_offset = offset_ms
                parts.append(_key_badge(key, current=False))
            self.upcoming_keys_label.setText(" ".join(parts))
        else:
            self.upcoming_keys_label.setText(
                '<span style="color:#666688;">—</span>'
            )

    def _update_prefix_text(self) -> None:
        self.current_prefix.setText(f"▶ {tr('overlay.current')}")
        self.upcoming_prefix.setText(tr("overlay.upcoming"))

    # ── painting ────────────────────────────────────────────

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, self.width(), self.height()), 12, 12)
        painter.fillPath(path, QColor(30, 30, 60, 210))
        painter.setPen(QColor(100, 100, 160, 100))
        painter.drawPath(path)
        painter.end()

    # ── dragging ────────────────────────────────────────────

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_pos = None

    # ── helpers ─────────────────────────────────────────────

    def _move_to_default(self) -> None:
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(geo.right() - self.width() - 20, geo.top() + 20)


def _fmt_time(ms: int) -> str:
    total_sec = max(0, ms // 1000)
    m, s = divmod(total_sec, 60)
    return f"{m:02d}:{s:02d}"


def _key_badge(key: str, *, current: bool) -> str:
    if current:
        return (
            f'<span style="background:#7c6bf5; color:#ffffff;'
            f" padding:1px 7px; border-radius:4px;"
            f' font-weight:bold;">{key}</span>'
        )
    return (
        f'<span style="background:rgba(255,255,255,40); color:#bbbbdd;'
        f' padding:1px 5px; border-radius:3px;">{key}</span>'
    )
