from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
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

from src.infrastructure.midi_reader import MidiKeyAnalysis, analyze_midi_key, list_midi_tracks
from src.interfaces.gui.i18n import on_language_changed, tr


class TracksTab(QWidget):
    midi_loaded = Signal(str)
    preview_requested = Signal(str, int)
    key_analyzed = Signal(object)  # MidiKeyAnalysis

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

        self.table = QTableWidget(0, 7)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.doubleClicked.connect(self._on_double_click)
        layout.addWidget(self.table, 1)

        self.hint_label = QLabel("")
        self.hint_label.setObjectName("statusHint")
        layout.addWidget(self.hint_label)

        self._last_analysis: MidiKeyAnalysis | None = None

        on_language_changed(self.retranslate)
        self.retranslate()

    def retranslate(self) -> None:
        self.file_label.setText(tr("tracks.midi_file"))
        self.file_edit.setPlaceholderText(tr("tracks.placeholder"))
        self.browse_btn.setText(tr("browse"))
        self.load_btn.setText(tr("tracks.load"))
        self.table.setHorizontalHeaderLabels([
            tr("tracks.col_index"),
            tr("tracks.col_name"),
            tr("tracks.col_messages"),
            tr("tracks.col_note_on"),
            tr("tracks.col_tempo"),
            tr("tracks.col_programs"),
            tr("tracks.col_key"),
        ])
        self.hint_label.setText(tr("tracks.hint_double_click"))
        if self._last_analysis is not None:
            self._show_key_analysis(self._last_analysis)

    def set_midi_path(self, path: str) -> None:
        self.file_edit.setText(path)

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

        self.info_label.setText(tr("tracks.info_ppq").format(ppq=ppq, count=len(tracks)))
        self.table.setRowCount(len(tracks))
        p = Path(path)
        for row, t in enumerate(tracks):
            self.table.setItem(row, 0, QTableWidgetItem(str(t.index)))
            self.table.setItem(row, 1, QTableWidgetItem(t.name))
            self.table.setItem(row, 2, QTableWidgetItem(str(t.message_count)))
            self.table.setItem(row, 3, QTableWidgetItem(str(t.note_on_count)))
            self.table.setItem(row, 4, QTableWidgetItem(tr("yes") if t.has_tempo else tr("no")))
            programs = ", ".join(str(p_id) for p_id in t.program_changes) if t.program_changes else "-"
            self.table.setItem(row, 5, QTableWidgetItem(programs))

            key_text = "-"
            if t.note_on_count > 0:
                try:
                    tk = analyze_midi_key(p, single_track=t.index)
                    if tk.detected_key:
                        key_text = f"{tk.detected_key} {tk.detected_mode}"
                        if tk.suggested_transpose != 0:
                            key_text += f" ({tk.suggested_transpose:+d})"
                except Exception:
                    pass
            self.table.setItem(row, 6, QTableWidgetItem(key_text))

        try:
            analysis = analyze_midi_key(p)
        except Exception:
            analysis = MidiKeyAnalysis()

        self._last_analysis = analysis
        self._show_key_analysis(analysis)
        self.key_analyzed.emit(analysis)
        self.midi_loaded.emit(path)

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
