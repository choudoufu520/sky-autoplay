from __future__ import annotations

import os
import tempfile
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from PySide6.QtCore import Signal

from src.infrastructure.midi_reader import export_single_track_midi, list_midi_tracks
from src.interfaces.gui.i18n import on_language_changed, tr


class PreviewTab(QWidget):
    midi_changed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)

        midi_row = QHBoxLayout()
        self.midi_label = QLabel()
        midi_row.addWidget(self.midi_label)
        self.midi_edit = QLineEdit()
        midi_row.addWidget(self.midi_edit, 1)
        self.browse_btn = QPushButton()
        self.browse_btn.clicked.connect(self._browse)
        midi_row.addWidget(self.browse_btn)
        layout.addLayout(midi_row)

        track_row = QHBoxLayout()
        self.track_label = QLabel()
        track_row.addWidget(self.track_label)
        self.track_combo = QComboBox()
        self.track_combo.setMinimumWidth(250)
        track_row.addWidget(self.track_combo)
        track_row.addStretch(1)
        layout.addLayout(track_row)

        save_row = QHBoxLayout()
        self.save_label = QLabel()
        save_row.addWidget(self.save_label)
        self.save_edit = QLineEdit()
        save_row.addWidget(self.save_edit, 1)
        self.save_browse = QPushButton()
        self.save_browse.clicked.connect(self._browse_save)
        save_row.addWidget(self.save_browse)
        layout.addLayout(save_row)

        btn_row = QHBoxLayout()
        self.preview_btn = QPushButton()
        self.preview_btn.setObjectName("primaryBtn")
        self.preview_btn.clicked.connect(self._preview)
        btn_row.addWidget(self.preview_btn)
        self.export_btn = QPushButton()
        self.export_btn.clicked.connect(self._export_only)
        btn_row.addWidget(self.export_btn)
        layout.addLayout(btn_row)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)
        layout.addStretch(1)

        on_language_changed(self.retranslate)
        self.retranslate()

    def retranslate(self) -> None:
        self.midi_label.setText(tr("preview.midi"))
        self.midi_edit.setPlaceholderText(tr("preview.midi_placeholder"))
        self.browse_btn.setText(tr("browse"))
        self.track_label.setText(tr("preview.track"))
        self.save_label.setText(tr("preview.save_to"))
        self.save_edit.setPlaceholderText(tr("preview.save_placeholder"))
        self.save_browse.setText(tr("browse"))
        self.preview_btn.setText(tr("preview.btn_preview"))
        self.export_btn.setText(tr("preview.btn_export"))

    def set_midi_path(self, path: str) -> None:
        self.midi_edit.setText(path)
        self._refresh_track_list(path)

    def _refresh_track_list(self, path: str) -> None:
        self.track_combo.clear()
        try:
            _, tracks = list_midi_tracks(Path(path))
            for t in tracks:
                label = f"[{t.index}] {t.name}" if t.name else f"[{t.index}]"
                if t.note_on_count > 0:
                    label += f"  ({t.note_on_count} notes)"
                self.track_combo.addItem(label, userData=t.index)
        except Exception:
            pass

    def set_track_index(self, index: int) -> None:
        for i in range(self.track_combo.count()):
            if self.track_combo.itemData(i) == index:
                self.track_combo.setCurrentIndex(i)
                return

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("preview.dialog_midi"), "", "MIDI Files (*.mid *.midi)")
        if path:
            self.midi_edit.setText(path)
            self.midi_changed.emit(path)

    def _browse_save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, tr("preview.dialog_save"), "", "MIDI Files (*.mid)")
        if path:
            self.save_edit.setText(path)

    def _do_export(self) -> Path | None:
        midi_path = self.midi_edit.text().strip()
        if not midi_path:
            self.status_label.setText(tr("preview.err_no_midi"))
            return None

        save_path = self.save_edit.text().strip()
        if save_path:
            output = Path(save_path)
        else:
            tmp = tempfile.NamedTemporaryFile(prefix="sky_preview_", suffix=".mid", delete=False)
            tmp.close()
            output = Path(tmp.name)

        track_index = self.track_combo.currentData()
        if track_index is None:
            track_index = 0

        try:
            export_single_track_midi(
                midi_path=Path(midi_path),
                track_index=track_index,
                output_path=output,
            )
        except Exception as exc:
            self.status_label.setText(f"{tr('error')}: {exc}")
            return None

        self.status_label.setText(tr("preview.exported").format(path=output))
        return output

    def _preview(self) -> None:
        output = self._do_export()
        if output is None:
            return
        os.startfile(str(output))  # type: ignore[attr-defined]
        self.status_label.setText(tr("preview.opened").format(path=output))

    def _export_only(self) -> None:
        self._do_export()
