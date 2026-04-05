"""Visual 3x5 keyboard widget that mirrors the Sky in-game instrument."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

_PITCH_CLASSES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
COLS = 5


def _note_name(midi: int) -> str:
    return f"{_PITCH_CLASSES[midi % 12]}{midi // 12 - 1}"


class SkyKeyboardWidget(QWidget):
    """Animated 3x5 key grid with keyboard / mouse input support."""

    key_pressed = Signal(str)
    key_released = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(420, 260)

        self._keys: list[dict] = []
        self._key_map: dict[str, int] = {}
        self._note_map: dict[int, int] = {}
        self._glow: dict[int, float] = {}
        self._held: set[str] = set()

        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(16)
        self._anim_timer.timeout.connect(self._tick)

    # ── public API ──────────────────────────────────────────

    def set_mapping(self, note_to_key: dict[str, str]) -> None:
        entries: list[tuple[int, str]] = []
        for note_str, key in note_to_key.items():
            try:
                entries.append((int(note_str), key))
            except ValueError:
                continue
        entries.sort()

        self._keys.clear()
        self._key_map.clear()
        self._note_map.clear()
        self._glow.clear()

        for idx, (note, key) in enumerate(entries[:15]):
            row, col = divmod(idx, COLS)
            self._keys.append(
                {
                    "midi_note": note,
                    "name": _note_name(note),
                    "mapped_key": key,
                    "row": row,
                    "col": col,
                    "pressed": False,
                }
            )
            self._key_map[key] = idx
            self._note_map[note] = idx
            self._glow[idx] = 0.0

        self.update()

    def press_key(self, key: str) -> None:
        idx = self._key_map.get(key)
        if idx is not None:
            self._keys[idx]["pressed"] = True
            self._glow[idx] = 1.0
            self._ensure_timer()
            self.update()

    def release_key(self, key: str) -> None:
        idx = self._key_map.get(key)
        if idx is not None:
            self._keys[idx]["pressed"] = False
            self._ensure_timer()
            self.update()

    def release_all(self) -> None:
        for k in self._keys:
            k["pressed"] = False
        self._held.clear()
        self.update()

    def get_midi_note(self, key: str) -> int | None:
        idx = self._key_map.get(key)
        return self._keys[idx]["midi_note"] if idx is not None else None

    # ── animation ───────────────────────────────────────────

    def _ensure_timer(self) -> None:
        if not self._anim_timer.isActive():
            self._anim_timer.start()

    def _tick(self) -> None:
        active = False
        for idx, k in enumerate(self._keys):
            if k["pressed"]:
                self._glow[idx] = 1.0
                active = True
            elif self._glow[idx] > 0.01:
                self._glow[idx] *= 0.88
                active = True
            else:
                self._glow[idx] = 0.0
        self.update()
        if not active:
            self._anim_timer.stop()

    # ── painting ────────────────────────────────────────────

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._keys:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        bg = QPainterPath()
        bg.addRoundedRect(QRectF(0, 0, self.width(), self.height()), 16, 16)
        painter.fillPath(bg, QColor(22, 22, 46))
        painter.setPen(QPen(QColor(50, 50, 85), 1))
        painter.drawPath(bg)

        w, h = self.width(), self.height()
        pad, gap = 14, 8
        num_rows = (len(self._keys) - 1) // COLS + 1
        kw = (w - 2 * pad - (COLS - 1) * gap) / COLS
        kh = (h - 2 * pad - (num_rows - 1) * gap) / num_rows

        for idx, k in enumerate(self._keys):
            x = pad + k["col"] * (kw + gap)
            y = pad + k["row"] * (kh + gap)
            self._paint_key(painter, QRectF(x, y, kw, kh), k, self._glow.get(idx, 0.0))

        painter.end()

    def _paint_key(self, p: QPainter, r: QRectF, data: dict, glow: float) -> None:
        radius = 12.0
        base_r, base_g, base_b = 40, 40, 68
        acc_r, acc_g, acc_b = 124, 107, 245

        bg = QColor(
            int(base_r + (acc_r - base_r) * glow),
            int(base_g + (acc_g - base_g) * glow),
            int(base_b + (acc_b - base_b) * glow),
        )

        path = QPainterPath()
        path.addRoundedRect(r, radius, radius)
        p.fillPath(path, bg)

        if glow > 0.15:
            p.setPen(QPen(QColor(acc_r, acc_g, acc_b, int(80 * glow)), 2.5))
            p.drawPath(path)

        border = QColor(145, 128, 255) if glow > 0.3 else QColor(60, 60, 95)
        p.setPen(QPen(border, 1.0))
        p.drawPath(path)

        text_col = QColor(255, 255, 255) if glow > 0.2 else QColor(210, 210, 235)
        p.setPen(text_col)
        f = QFont(p.font())
        f.setPixelSize(max(int(r.height() * 0.28), 13))
        f.setBold(True)
        p.setFont(f)
        top = QRectF(r.x(), r.y() + r.height() * 0.08, r.width(), r.height() * 0.5)
        p.drawText(top, Qt.AlignmentFlag.AlignCenter, data["name"])

        if data["mapped_key"]:
            sub = QColor(220, 220, 240) if glow > 0.2 else QColor(110, 110, 155)
            p.setPen(sub)
            f.setPixelSize(max(int(r.height() * 0.2), 10))
            f.setBold(False)
            p.setFont(f)
            bot = QRectF(r.x(), r.y() + r.height() * 0.55, r.width(), r.height() * 0.35)
            p.drawText(bot, Qt.AlignmentFlag.AlignCenter, data["mapped_key"].upper())

    # ── keyboard input ──────────────────────────────────────

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.isAutoRepeat():
            return
        key = event.text()
        if key and key in self._key_map and key not in self._held:
            self._held.add(key)
            self.press_key(key)
            self.key_pressed.emit(key)

    def keyReleaseEvent(self, event) -> None:  # noqa: N802
        if event.isAutoRepeat():
            return
        key = event.text()
        if key and key in self._key_map:
            self._held.discard(key)
            self.release_key(key)
            self.key_released.emit(key)

    # ── mouse input ─────────────────────────────────────────

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            key = self._key_at(event.position())
            if key:
                self.press_key(key)
                self.key_pressed.emit(key)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            key = self._key_at(event.position())
            if key:
                self.release_key(key)
                self.key_released.emit(key)

    def _key_at(self, pos) -> str | None:
        if not self._keys:
            return None
        w, h = self.width(), self.height()
        pad, gap = 14, 8
        num_rows = (len(self._keys) - 1) // COLS + 1
        kw = (w - 2 * pad - (COLS - 1) * gap) / COLS
        kh = (h - 2 * pad - (num_rows - 1) * gap) / num_rows
        for k in self._keys:
            x = pad + k["col"] * (kw + gap)
            y = pad + k["row"] * (kh + gap)
            if QRectF(x, y, kw, kh).contains(pos):
                return k["mapped_key"]
        return None
