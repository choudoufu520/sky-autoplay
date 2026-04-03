from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.application.converter import ConvertOptions, MappingError, convert_midi_to_chart
from src.infrastructure.midi_reader import MidiKeyAnalysis, analyze_midi_key, list_midi_tracks
from src.infrastructure.repository import load_mapping, save_chart
from src.interfaces.gui.i18n import on_language_changed, tr


class ConvertTab(QWidget):
    chart_saved = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)

        # MIDI file
        midi_row = QHBoxLayout()
        self.midi_label = QLabel()
        midi_row.addWidget(self.midi_label)
        self.midi_edit = QLineEdit()
        midi_row.addWidget(self.midi_edit, 1)
        self.midi_browse = QPushButton()
        self.midi_browse.clicked.connect(self._browse_midi)
        midi_row.addWidget(self.midi_browse)
        layout.addLayout(midi_row)

        # Mapping file
        mapping_row = QHBoxLayout()
        self.mapping_label = QLabel()
        mapping_row.addWidget(self.mapping_label)
        self.mapping_edit = QLineEdit("configs/mapping.example.yaml")
        mapping_row.addWidget(self.mapping_edit, 1)
        self.mapping_browse = QPushButton()
        self.mapping_browse.clicked.connect(self._browse_mapping)
        mapping_row.addWidget(self.mapping_browse)
        layout.addLayout(mapping_row)

        # Output file
        output_row = QHBoxLayout()
        self.output_label = QLabel()
        output_row.addWidget(self.output_label)
        self.output_edit = QLineEdit()
        output_row.addWidget(self.output_edit, 1)
        self.output_browse = QPushButton()
        self.output_browse.clicked.connect(self._browse_output)
        output_row.addWidget(self.output_browse)
        layout.addLayout(output_row)

        # Parameters
        self.form = QFormLayout()
        self.profile_label = QLabel()
        self.profile_combo = QComboBox()
        self.profile_combo.setEditable(True)
        self.form.addRow(self.profile_label, self.profile_combo)

        self.detected_key_label = QLabel()
        self.key_info_label = QLabel()
        self.key_info_label.setObjectName("keyInfoLabel")
        self.form.addRow(self.detected_key_label, self.key_info_label)

        self.transpose_label = QLabel()
        transpose_row = QHBoxLayout()
        self.transpose_spin = QSpinBox()
        self.transpose_spin.setRange(-48, 48)
        transpose_row.addWidget(self.transpose_spin)
        self.apply_suggest_btn = QPushButton()
        self.apply_suggest_btn.setEnabled(False)
        self.apply_suggest_btn.clicked.connect(self._apply_suggested_transpose)
        transpose_row.addWidget(self.apply_suggest_btn)
        self.form.addRow(self.transpose_label, transpose_row)

        self.octave_label = QLabel()
        self.octave_spin = QSpinBox()
        self.octave_spin.setRange(-4, 4)
        self.form.addRow(self.octave_label, self.octave_spin)

        self.note_mode_label = QLabel()
        self.note_mode_combo = QComboBox()
        self.note_mode_combo.addItems(["tap", "hold"])
        self.form.addRow(self.note_mode_label, self.note_mode_combo)

        self.single_track_label = QLabel()
        self.single_track_combo = QComboBox()
        self.single_track_combo.addItem(tr("convert.all"), userData=None)
        self.form.addRow(self.single_track_label, self.single_track_combo)

        self.snap_check = QCheckBox()
        self.snap_check.setChecked(True)
        self.form.addRow(self.snap_check)

        self.strict_check = QCheckBox()
        self.form.addRow(self.strict_check)

        layout.addLayout(self.form)

        self.convert_btn = QPushButton()
        self.convert_btn.setObjectName("primaryBtn")
        self.convert_btn.clicked.connect(self._convert)
        layout.addWidget(self.convert_btn)

        self.result_text = QPlainTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setMaximumHeight(180)
        layout.addWidget(self.result_text, 1)

        self._suggested_transpose: int = 0
        self._midi_path: str = ""

        self.single_track_combo.currentIndexChanged.connect(self._on_track_changed)
        self.mapping_edit.textChanged.connect(self._refresh_profiles)
        self._refresh_profiles()

        on_language_changed(self.retranslate)
        self.retranslate()

    def retranslate(self) -> None:
        self.midi_label.setText(tr("convert.midi"))
        self.midi_edit.setPlaceholderText(tr("convert.midi_placeholder"))
        self.midi_browse.setText(tr("browse"))
        self.mapping_label.setText(tr("convert.mapping"))
        self.mapping_browse.setText(tr("browse"))
        self.output_label.setText(tr("convert.output"))
        self.output_edit.setPlaceholderText(tr("convert.output_placeholder"))
        self.output_browse.setText(tr("browse"))

        self.profile_label.setText(tr("convert.profile"))
        self.detected_key_label.setText(tr("convert.detected_key"))
        self.transpose_label.setText(tr("convert.transpose"))
        self.octave_label.setText(tr("convert.octave"))
        self.note_mode_label.setText(tr("convert.note_mode"))
        self.single_track_label.setText(tr("convert.single_track"))

        self.key_info_label.setText(tr("convert.key_hint"))
        self.apply_suggest_btn.setText(tr("convert.apply_suggested"))
        self.apply_suggest_btn.setToolTip(tr("convert.apply_tooltip"))
        if self.single_track_combo.count() > 0:
            self.single_track_combo.setItemText(0, tr("convert.all"))
        self.snap_check.setText(tr("convert.snap"))
        self.strict_check.setText(tr("convert.strict"))
        self.convert_btn.setText(tr("convert.btn"))

        self.transpose_spin.setToolTip(tr("convert.tip_transpose"))
        self.octave_spin.setToolTip(tr("convert.tip_octave"))
        self.note_mode_combo.setToolTip(tr("convert.tip_note_mode"))
        self.snap_check.setToolTip(tr("convert.tip_snap"))
        self.strict_check.setToolTip(tr("convert.tip_strict"))

    def set_midi_path(self, path: str) -> None:
        self._midi_path = path
        self.midi_edit.setText(path)
        stem = Path(path).stem
        self.output_edit.setText(f"output/{stem}.json")
        self._refresh_track_list(path)

    def _refresh_track_list(self, path: str) -> None:
        self.single_track_combo.clear()
        self.single_track_combo.addItem(tr("convert.all"), userData=None)
        try:
            _, tracks = list_midi_tracks(Path(path))
            for t in tracks:
                label = f"[{t.index}] {t.name}" if t.name else f"[{t.index}]"
                if t.note_on_count > 0:
                    label += f"  ({t.note_on_count} notes)"
                self.single_track_combo.addItem(label, userData=t.index)
        except Exception:
            pass

    def _on_track_changed(self) -> None:
        midi_path = self._midi_path or self.midi_edit.text().strip()
        if not midi_path:
            return
        track_idx = self.single_track_combo.currentData()
        try:
            analysis = analyze_midi_key(Path(midi_path), single_track=track_idx)
        except Exception:
            analysis = MidiKeyAnalysis()
        self._apply_key_analysis(analysis)

    def _apply_key_analysis(self, analysis: MidiKeyAnalysis) -> None:
        parts: list[str] = []
        if analysis.detected_key:
            parts.append(f"{analysis.detected_key} {analysis.detected_mode}")
        if analysis.key_signature:
            parts.append(f"(MIDI meta: {analysis.key_signature})")
        if analysis.suggested_transpose != 0:
            parts.append(tr("convert.suggested").format(val=f"{analysis.suggested_transpose:+d}"))
        else:
            parts.append(tr("convert.no_transpose"))

        self.key_info_label.setText("  ".join(parts) if parts else tr("unknown"))
        self.key_info_label.setObjectName("keyInfoLabelActive")
        self._suggested_transpose = analysis.suggested_transpose
        self.apply_suggest_btn.setEnabled(True)

    def set_key_analysis(self, analysis: object) -> None:
        if not isinstance(analysis, MidiKeyAnalysis):
            return
        self._apply_key_analysis(analysis)

    def _apply_suggested_transpose(self) -> None:
        self.transpose_spin.setValue(self._suggested_transpose)

    def _browse_midi(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("convert.dialog_midi"), "", "MIDI Files (*.mid *.midi)")
        if path:
            self.set_midi_path(path)

    def _browse_mapping(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("convert.dialog_mapping"), "", "YAML Files (*.yaml *.yml)")
        if path:
            self.mapping_edit.setText(path)

    def _browse_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, tr("convert.dialog_output"), "", "JSON Files (*.json)")
        if path:
            self.output_edit.setText(path)

    def _refresh_profiles(self) -> None:
        mapping_path = self.mapping_edit.text().strip()
        self.profile_combo.clear()
        if not mapping_path or not Path(mapping_path).exists():
            return
        try:
            config = load_mapping(Path(mapping_path))
            self.profile_combo.addItems(list(config.profiles.keys()))
        except Exception:
            pass

    def _convert(self) -> None:
        self.result_text.clear()
        midi_path = self.midi_edit.text().strip()
        mapping_path = self.mapping_edit.text().strip()
        output_path = self.output_edit.text().strip()

        if not midi_path or not mapping_path or not output_path:
            self.result_text.setPlainText(tr("convert.err_required"))
            return

        try:
            mapping_config = load_mapping(Path(mapping_path))
        except Exception as exc:
            self.result_text.setPlainText(tr("convert.err_mapping").format(err=exc))
            return

        profile = self.profile_combo.currentText().strip() or None
        single_track_val = self.single_track_combo.currentData()

        options = ConvertOptions(
            profile=profile,
            transpose=self.transpose_spin.value(),
            octave=self.octave_spin.value(),
            strict=self.strict_check.isChecked(),
            snap=self.snap_check.isChecked(),
            note_mode=self.note_mode_combo.currentText(),
            single_track=single_track_val,
        )

        try:
            chart, warnings = convert_midi_to_chart(Path(midi_path), mapping_config, options)
        except MappingError as exc:
            self.result_text.setPlainText(tr("convert.err_map").format(err=exc))
            return
        except Exception as exc:
            self.result_text.setPlainText(tr("convert.err_convert").format(err=exc))
            return

        save_chart(Path(output_path), chart)

        lines = [
            tr("convert.saved").format(path=output_path),
            tr("convert.events").format(count=len(chart.events)),
        ]
        if warnings:
            lines.append(tr("convert.warnings").format(count=len(warnings)))
            for w in warnings[:30]:
                lines.append(f"  - {w}")
            if len(warnings) > 30:
                lines.append(f"  ... {len(warnings) - 30} more")
        self.result_text.setPlainText("\n".join(lines))
        self.chart_saved.emit(output_path)
