from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QScrollBar,
    QVBoxLayout,
)

from src.interfaces.gui.i18n import tr


class JianpuCompareDialog(QDialog):
    """Side-by-side comparison of original MIDI Jianpu and converted chart Jianpu."""

    def __init__(
        self,
        original_jianpu: str,
        converted_jianpu: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("convert.compare_jianpu"))
        self.resize(900, 600)
        self.setMinimumSize(600, 400)

        layout = QVBoxLayout(self)

        panels = QHBoxLayout()

        left_layout = QVBoxLayout()
        left_title = QLabel(tr("convert.compare_original"))
        left_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_title.setStyleSheet("font-weight: bold; font-size: 14px;")
        left_layout.addWidget(left_title)
        self.left_edit = QPlainTextEdit()
        self.left_edit.setReadOnly(True)
        self.left_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        left_layout.addWidget(self.left_edit)
        panels.addLayout(left_layout)

        right_layout = QVBoxLayout()
        right_title = QLabel(tr("convert.compare_converted"))
        right_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_title.setStyleSheet("font-weight: bold; font-size: 14px;")
        right_layout.addWidget(right_title)
        self.right_edit = QPlainTextEdit()
        self.right_edit.setReadOnly(True)
        self.right_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        right_layout.addWidget(self.right_edit)
        panels.addLayout(right_layout)

        layout.addLayout(panels)

        self.left_edit.setPlainText(original_jianpu)
        self.right_edit.setPlainText(converted_jianpu)

        self._highlight_differences(original_jianpu, converted_jianpu)

        left_vbar = self.left_edit.verticalScrollBar()
        right_vbar = self.right_edit.verticalScrollBar()
        left_hbar = self.left_edit.horizontalScrollBar()
        right_hbar = self.right_edit.horizontalScrollBar()

        if left_vbar and right_vbar:
            left_vbar.valueChanged.connect(self._sync_scroll_v)
            right_vbar.valueChanged.connect(self._sync_scroll_v_reverse)
        if left_hbar and right_hbar:
            left_hbar.valueChanged.connect(self._sync_scroll_h)
            right_hbar.valueChanged.connect(self._sync_scroll_h_reverse)

        self._syncing = False

    def _sync_scroll_v(self, value: int) -> None:
        if self._syncing:
            return
        self._syncing = True
        bar = self.right_edit.verticalScrollBar()
        if bar:
            bar.setValue(value)
        self._syncing = False

    def _sync_scroll_v_reverse(self, value: int) -> None:
        if self._syncing:
            return
        self._syncing = True
        bar = self.left_edit.verticalScrollBar()
        if bar:
            bar.setValue(value)
        self._syncing = False

    def _sync_scroll_h(self, value: int) -> None:
        if self._syncing:
            return
        self._syncing = True
        bar = self.right_edit.horizontalScrollBar()
        if bar:
            bar.setValue(value)
        self._syncing = False

    def _sync_scroll_h_reverse(self, value: int) -> None:
        if self._syncing:
            return
        self._syncing = True
        bar = self.left_edit.horizontalScrollBar()
        if bar:
            bar.setValue(value)
        self._syncing = False

    def _highlight_differences(self, original: str, converted: str) -> None:
        orig_lines = original.split("\n")
        conv_lines = converted.split("\n")

        diff_fmt = QTextCharFormat()
        diff_fmt.setBackground(QColor(255, 255, 150, 100))

        skip_lines = 2

        max_lines = max(len(orig_lines), len(conv_lines))
        right_cursor = QTextCursor(self.right_edit.document())
        right_cursor.movePosition(QTextCursor.MoveOperation.Start)

        for i in range(max_lines):
            orig_line = orig_lines[i] if i < len(orig_lines) else ""
            conv_line = conv_lines[i] if i < len(conv_lines) else ""

            if i < skip_lines:
                if i < max_lines - 1:
                    right_cursor.movePosition(QTextCursor.MoveOperation.Down)
                continue

            if orig_line.strip() != conv_line.strip() and conv_line.strip():
                right_cursor.movePosition(
                    QTextCursor.MoveOperation.StartOfBlock
                )
                right_cursor.movePosition(
                    QTextCursor.MoveOperation.EndOfBlock,
                    QTextCursor.MoveMode.KeepAnchor,
                )
                right_cursor.mergeCharFormat(diff_fmt)

            if i < max_lines - 1:
                right_cursor.movePosition(QTextCursor.MoveOperation.Down)
