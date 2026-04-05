from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.infrastructure.midi_reader import MidiKeyAnalysis, MidiTrackInfo, analyze_midi_key, list_midi_tracks
from src.interfaces.gui.i18n import on_language_changed, tr


class TracksTab(QWidget):
    midi_loaded = Signal(str)
    preview_requested = Signal(str, int)
    key_analyzed = Signal(object)  # MidiKeyAnalysis
    tracks_selected = Signal(list)  # list[int] of selected track indices

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)

        file_row = QHBoxLayout()
        self.file_label = QLabel()
        file_row.addWidget(self.file_label)
        self.file_edit = QLineEdit()
        file_row.addWidget(self.file_edit, 1)
        self.browse_btn = QPushButton()
        self.browse_btn.clicked.connect(self._browse)
        file_row.addWidget(self.browse_btn)
        self.load_btn = QPushButton()
        self.load_btn.clicked.connect(self._load)
        file_row.addWidget(self.load_btn)
        layout.addLayout(file_row)

        self.info_label = QLabel("")
        self.info_label.setObjectName("infoLabel")
        layout.addWidget(self.info_label)

        self.key_label = QLabel("")
        self.key_label.setObjectName("keyLabel")
        layout.addWidget(self.key_label)

        self.note_dist_label = QLabel("")
        self.note_dist_label.setObjectName("noteDistLabel")
        self.note_dist_label.setWordWrap(True)
        layout.addWidget(self.note_dist_label)

        self.table = QTableWidget(0, 8)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.doubleClicked.connect(self._on_double_click)
        layout.addWidget(self.table, 1)

        sel_row = QHBoxLayout()
        self.select_all_btn = QPushButton()
        self.select_all_btn.clicked.connect(self._select_all)
        sel_row.addWidget(self.select_all_btn)
        self.deselect_all_btn = QPushButton()
        self.deselect_all_btn.clicked.connect(self._deselect_all)
        sel_row.addWidget(self.deselect_all_btn)
        self.invert_btn = QPushButton()
        self.invert_btn.clicked.connect(self._invert_selection)
        sel_row.addWidget(self.invert_btn)
        sel_row.addStretch()
        layout.addLayout(sel_row)

        self.hint_label = QLabel("")
        self.hint_label.setObjectName("statusHint")
        layout.addWidget(self.hint_label)

        self._last_analysis: MidiKeyAnalysis | None = None
        self._track_infos: list[MidiTrackInfo] = []
        self._suppress_check_signal = False

        on_language_changed(self.retranslate)
        self.retranslate()

    def retranslate(self) -> None:
        self.file_label.setText(tr("tracks.midi_file"))
        self.file_edit.setPlaceholderText(tr("tracks.placeholder"))
        self.browse_btn.setText(tr("browse"))
        self.load_btn.setText(tr("tracks.load"))
        self.table.setHorizontalHeaderLabels([
            tr("tracks.col_select"),
            tr("tracks.col_index"),
            tr("tracks.col_name"),
            tr("tracks.col_messages"),
            tr("tracks.col_note_on"),
            tr("tracks.col_tempo"),
            tr("tracks.col_programs"),
            tr("tracks.col_key"),
        ])
        self.select_all_btn.setText(tr("tracks.select_all"))
        self.deselect_all_btn.setText(tr("tracks.deselect_all"))
        self.invert_btn.setText(tr("tracks.invert_selection"))
        self.hint_label.setText(tr("tracks.hint_double_click"))
        if self._last_analysis is not None:
            self._show_key_analysis(self._last_analysis)

    def set_midi_path(self, path: str) -> None:
        self.file_edit.setText(path)

    def selected_track_indices(self) -> list[int]:
        indices: list[int] = []
        for row in range(self.table.rowCount()):
            w = self.table.cellWidget(row, 0)
            if isinstance(w, QCheckBox) and w.isChecked():
                item = self.table.item(row, 1)
                if item:
                    indices.append(int(item.text()))
        return indices

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("tracks.dialog_title"), "", "MIDI Files (*.mid *.midi)")
        if path:
            self.file_edit.setText(path)
            self._load()

    def _load(self) -> None:
        path = self.file_edit.text().strip()
        if not path:
            return
        try:
            ppq, tracks = list_midi_tracks(Path(path))
        except Exception as exc:
            self.info_label.setText(f"{tr('error')}: {exc}")
            return

        self._track_infos = tracks
        self.info_label.setText(tr("tracks.info_ppq").format(ppq=ppq, count=len(tracks)))
        self._suppress_check_signal = True
        self.table.setRowCount(len(tracks))
        p = Path(path)
        for row, t in enumerate(tracks):
            cb = QCheckBox()
            cb.setChecked(t.note_on_count > 0)
            cb.stateChanged.connect(self._on_check_changed)
            self.table.setCellWidget(row, 0, cb)

            self.table.setItem(row, 1, QTableWidgetItem(str(t.index)))
            self.table.setItem(row, 2, QTableWidgetItem(t.name))
            self.table.setItem(row, 3, QTableWidgetItem(str(t.message_count)))
            self.table.setItem(row, 4, QTableWidgetItem(str(t.note_on_count)))
            self.table.setItem(row, 5, QTableWidgetItem(tr("yes") if t.has_tempo else tr("no")))
            programs = ", ".join(str(p_id) for p_id in t.program_changes) if t.program_changes else "-"
            self.table.setItem(row, 6, QTableWidgetItem(programs))

            key_text = "-"
            if t.note_on_count > 0:
                try:
                    tk = analyze_midi_key(p, tracks=[t.index])
                    if tk.detected_key:
                        key_text = f"{tk.detected_key} {tk.detected_mode}"
                        if tk.suggested_transpose != 0:
                            key_text += f" ({tk.suggested_transpose:+d})"
                except Exception:
                    pass
            self.table.setItem(row, 7, QTableWidgetItem(key_text))

        self._suppress_check_signal = False

        selected = self.selected_track_indices()
        tracks_arg = selected if selected else None
        try:
            analysis = analyze_midi_key(p, tracks=tracks_arg)
        except Exception:
            analysis = MidiKeyAnalysis()

        self._last_analysis = analysis
        self._show_key_analysis(analysis)
        self.key_analyzed.emit(analysis)
        self.midi_loaded.emit(path)
        self.tracks_selected.emit(selected)

    def _on_check_changed(self) -> None:
        if self._suppress_check_signal:
            return
        selected = self.selected_track_indices()
        self.tracks_selected.emit(selected)

        path = self.file_edit.text().strip()
        if path:
            tracks_arg = selected if selected else None
            try:
                analysis = analyze_midi_key(Path(path), tracks=tracks_arg)
            except Exception:
                analysis = MidiKeyAnalysis()
            self._last_analysis = analysis
            self._show_key_analysis(analysis)
            self.key_analyzed.emit(analysis)

    def _select_all(self) -> None:
        self._suppress_check_signal = True
        for row in range(self.table.rowCount()):
            w = self.table.cellWidget(row, 0)
            if isinstance(w, QCheckBox):
                w.setChecked(True)
        self._suppress_check_signal = False
        self._on_check_changed()

    def _deselect_all(self) -> None:
        self._suppress_check_signal = True
        for row in range(self.table.rowCount()):
            w = self.table.cellWidget(row, 0)
            if isinstance(w, QCheckBox):
                w.setChecked(False)
        self._suppress_check_signal = False
        self._on_check_changed()

    def _invert_selection(self) -> None:
        self._suppress_check_signal = True
        for row in range(self.table.rowCount()):
            w = self.table.cellWidget(row, 0)
            if isinstance(w, QCheckBox):
                w.setChecked(not w.isChecked())
        self._suppress_check_signal = False
        self._on_check_changed()

    def _show_key_analysis(self, analysis: MidiKeyAnalysis) -> None:
        key_parts: list[str] = []
        if analysis.key_signature:
            key_parts.append(tr("tracks.key_sig").format(sig=analysis.key_signature))
        if analysis.detected_key:
            key_parts.append(tr("tracks.detected").format(key=analysis.detected_key, mode=analysis.detected_mode))
            if analysis.suggested_transpose != 0:
                key_parts.append(tr("tracks.suggest_transpose").format(val=f"{analysis.suggested_transpose:+d}"))
            else:
                key_parts.append(tr("tracks.no_transpose"))
        self.key_label.setText("  |  ".join(key_parts) if key_parts else tr("tracks.key_unknown"))

        if analysis.note_distribution:
            top = [f"{name}: {count}" for name, count in analysis.note_distribution[:7]]
            self.note_dist_label.setText(tr("tracks.note_dist").format(dist="  ".join(top)))
        else:
            self.note_dist_label.setText("")

    def _on_double_click(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        path = self.file_edit.text().strip()
        if path:
            self.preview_requested.emit(path, row)
