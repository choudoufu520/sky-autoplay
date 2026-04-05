from __future__ import annotations

from collections import Counter
from datetime import datetime
import logging
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListView,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.application.converter import (
    ConvertOptions,
    MappingError,
    chart_to_jianpu_pdf,
    chart_to_preview_midi,
    convert_midi_to_chart,
)
from src.infrastructure.midi_reader import MidiKeyAnalysis, analyze_midi_key, list_midi_tracks, read_midi_meta
from src.infrastructure.repository import load_mapping, save_chart
from src.interfaces.gui.paths import default_mapping_path
from src.interfaces.gui.i18n import on_language_changed, tr

logger = logging.getLogger(__name__)


class _OptimalWorker(QThread):
    finished = Signal(object)

    def __init__(self, midi_path: Path, mapping, profile: str, tracks, parent=None):
        super().__init__(parent)
        self.midi_path = midi_path
        self.mapping = mapping
        self.profile = profile
        self.tracks = tracks

    def run(self) -> None:
        from src.application.ai_arranger import find_optimal_settings
        try:
            results = find_optimal_settings(
                self.midi_path, self.mapping, self.profile,
                tracks=self.tracks,
            )
            self.finished.emit(results)
        except Exception:
            self.finished.emit([])


class ConvertTab(QWidget):
    chart_saved = Signal(str)
    midi_changed = Signal(str)

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
        self.mapping_edit = QLineEdit(default_mapping_path())
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

        self.tracks_label = QLabel()
        self.tracks_combo = QComboBox()
        self.tracks_combo.setView(QListView())
        self._tracks_model = QStandardItemModel(self.tracks_combo)
        self.tracks_combo.setModel(self._tracks_model)
        self.tracks_combo.setEditable(True)
        self.tracks_combo.lineEdit().setReadOnly(True)
        self.form.addRow(self.tracks_label, self.tracks_combo)

        self.snap_check = QCheckBox()
        self.snap_check.setChecked(False)
        self.form.addRow(self.snap_check)

        self.strict_check = QCheckBox()
        self.form.addRow(self.strict_check)

        self.denoise_check = QCheckBox()
        self.denoise_check.setChecked(True)
        self.denoise_check.stateChanged.connect(self._toggle_denoise_params)
        self.form.addRow(self.denoise_check)

        denoise_params = QHBoxLayout()
        self.denoise_max_sim_label = QLabel()
        denoise_params.addWidget(self.denoise_max_sim_label)
        self.denoise_max_sim_spin = QSpinBox()
        self.denoise_max_sim_spin.setRange(1, 8)
        self.denoise_max_sim_spin.setValue(3)
        self.denoise_max_sim_spin.setFixedWidth(60)
        denoise_params.addWidget(self.denoise_max_sim_spin)
        denoise_params.addSpacing(12)
        self.denoise_max_repeat_label = QLabel()
        denoise_params.addWidget(self.denoise_max_repeat_label)
        self.denoise_max_repeat_spin = QSpinBox()
        self.denoise_max_repeat_spin.setRange(1, 16)
        self.denoise_max_repeat_spin.setValue(4)
        self.denoise_max_repeat_spin.setFixedWidth(60)
        denoise_params.addWidget(self.denoise_max_repeat_spin)
        denoise_params.addStretch()
        self.form.addRow(denoise_params)

        self.preview_midi_check = QCheckBox()
        self.form.addRow(self.preview_midi_check)

        layout.addLayout(self.form)

        # AI Arrange section
        self.ai_group = QGroupBox()
        self.ai_group.setObjectName("aiGroupBox")
        self.ai_group.setCheckable(True)
        self.ai_group.setChecked(True)
        ai_group_layout = QVBoxLayout(self.ai_group)
        ai_group_layout.setContentsMargins(0, 0, 0, 0)

        self.ai_hint_label = QLabel()
        self.ai_hint_label.setObjectName("infoLabel")
        self.ai_hint_label.setWordWrap(True)
        ai_group_layout.addWidget(self.ai_hint_label)

        self._ai_content = QWidget()
        ai_layout = QVBoxLayout(self._ai_content)
        ai_layout.setContentsMargins(6, 2, 6, 2)

        self._settings = QSettings("SkyMusicAutomation", "SkyMusicAutomation")

        # Collapsible API settings sub-section
        self.ai_settings_toggle = QToolButton()
        self.ai_settings_toggle.setCheckable(True)
        self.ai_settings_toggle.setChecked(False)
        self.ai_settings_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.ai_settings_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.ai_settings_toggle.clicked.connect(self._toggle_api_settings)
        ai_layout.addWidget(self.ai_settings_toggle)

        self.ai_settings_container = QWidget()
        api_container_layout = QVBoxLayout(self.ai_settings_container)
        api_container_layout.setContentsMargins(0, 0, 0, 0)

        ai_row1 = QHBoxLayout()
        self.ai_key_label = QLabel("API Key:")
        ai_row1.addWidget(self.ai_key_label)
        self.ai_key_edit = QLineEdit()
        self.ai_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.ai_key_edit.setPlaceholderText("sk-...")
        self.ai_key_edit.setText(self._settings.value("ai/api_key", ""))
        self.ai_key_edit.textChanged.connect(lambda v: self._settings.setValue("ai/api_key", v))
        ai_row1.addWidget(self.ai_key_edit, 1)
        api_container_layout.addLayout(ai_row1)

        ai_row2 = QHBoxLayout()
        self.ai_url_label = QLabel("Base URL:")
        ai_row2.addWidget(self.ai_url_label)
        self.ai_url_edit = QLineEdit()
        self.ai_url_edit.setPlaceholderText(tr("convert.ai_url_placeholder"))
        self.ai_url_edit.setText(self._settings.value("ai/base_url", ""))
        self.ai_url_edit.textChanged.connect(lambda v: self._settings.setValue("ai/base_url", v))
        ai_row2.addWidget(self.ai_url_edit, 1)
        api_container_layout.addLayout(ai_row2)

        ai_row3 = QHBoxLayout()
        self.ai_model_label = QLabel("Model:")
        ai_row3.addWidget(self.ai_model_label)
        self.ai_model_edit = QLineEdit(self._settings.value("ai/model", "gpt-4o-mini"))
        self.ai_model_edit.textChanged.connect(lambda v: self._settings.setValue("ai/model", v))
        ai_row3.addWidget(self.ai_model_edit, 1)
        api_container_layout.addLayout(ai_row3)

        self.ai_settings_container.setVisible(False)
        ai_layout.addWidget(self.ai_settings_container)

        ai_row4 = QHBoxLayout()
        self.ai_mode_label = QLabel()
        ai_row4.addWidget(self.ai_mode_label)
        self.ai_mode_combo = QComboBox()
        self.ai_mode_combo.addItem(tr("convert.ai_mode_remap"), userData="remap")
        self.ai_mode_combo.addItem(tr("convert.ai_mode_context"), userData="context")
        self.ai_mode_combo.setCurrentIndex(1)
        ai_row4.addWidget(self.ai_mode_combo, 1)

        self.ai_style_label = QLabel()
        ai_row4.addWidget(self.ai_style_label)
        self.ai_style_combo = QComboBox()
        self.ai_style_combo.addItem(tr("convert.ai_style_conservative"), userData="conservative")
        self.ai_style_combo.addItem(tr("convert.ai_style_balanced"), userData="balanced")
        self.ai_style_combo.addItem(tr("convert.ai_style_creative"), userData="creative")
        self.ai_style_combo.setCurrentIndex(2)
        ai_row4.addWidget(self.ai_style_combo, 1)

        self.ai_arrange_btn = QPushButton()
        self.ai_arrange_btn.clicked.connect(self._ai_arrange)
        ai_row4.addWidget(self.ai_arrange_btn)

        self.ai_edit_prompt_btn = QPushButton()
        self.ai_edit_prompt_btn.clicked.connect(self._open_prompt_editor)
        ai_row4.addWidget(self.ai_edit_prompt_btn)
        ai_layout.addLayout(ai_row4)

        self.ai_simplify_check = QCheckBox()
        self.ai_simplify_check.setChecked(False)
        ai_layout.addWidget(self.ai_simplify_check)

        ai_opt_row = QHBoxLayout()
        self.ai_optimal_label = QLabel()
        self.ai_optimal_label.setWordWrap(True)
        self.ai_optimal_label.setOpenExternalLinks(True)
        ai_opt_row.addWidget(self.ai_optimal_label, 1)
        self.ai_optimal_apply_btn = QPushButton()
        self.ai_optimal_apply_btn.setFixedWidth(60)
        self.ai_optimal_apply_btn.setVisible(False)
        self.ai_optimal_apply_btn.clicked.connect(self._apply_optimal_settings)
        ai_opt_row.addWidget(self.ai_optimal_apply_btn)
        ai_layout.addLayout(ai_opt_row)

        ai_status_row = QHBoxLayout()
        self.ai_status_label = QLabel()
        self.ai_status_label.setWordWrap(True)
        self.ai_status_label.setTextInteractionFlags(
            self.ai_status_label.textInteractionFlags()
            | self.ai_status_label.textInteractionFlags().TextSelectableByMouse
        )
        ai_status_row.addWidget(self.ai_status_label, 1)
        self.ai_copy_btn = QPushButton()
        self.ai_copy_btn.setFixedWidth(60)
        self.ai_copy_btn.setVisible(False)
        self.ai_copy_btn.clicked.connect(self._copy_ai_status)
        ai_status_row.addWidget(self.ai_copy_btn)
        ai_layout.addLayout(ai_status_row)

        self.ai_review_table = QTableWidget(0, 5)
        self.ai_review_table.setVisible(False)
        self.ai_review_table.setMaximumHeight(200)
        self.ai_review_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.ai_review_table.verticalHeader().setVisible(False)
        ai_layout.addWidget(self.ai_review_table)

        self.ai_feedback_edit = QPlainTextEdit()
        self.ai_feedback_edit.setVisible(False)
        self.ai_feedback_edit.setMaximumHeight(50)
        self.ai_feedback_edit.setPlaceholderText(tr("convert.ai_feedback_placeholder"))
        ai_layout.addWidget(self.ai_feedback_edit)

        ai_action_row = QHBoxLayout()
        self.ai_preview_btn = QPushButton()
        self.ai_preview_btn.setVisible(False)
        self.ai_preview_btn.clicked.connect(self._preview_ai_mapping)
        ai_action_row.addWidget(self.ai_preview_btn)
        self.ai_apply_convert_btn = QPushButton()
        self.ai_apply_convert_btn.setObjectName("primaryBtn")
        self.ai_apply_convert_btn.setVisible(False)
        self.ai_apply_convert_btn.clicked.connect(self._apply_and_convert)
        ai_action_row.addWidget(self.ai_apply_convert_btn)
        self.ai_apply_btn = QPushButton()
        self.ai_apply_btn.setVisible(False)
        self.ai_apply_btn.clicked.connect(self._apply_review_table)
        ai_action_row.addWidget(self.ai_apply_btn)
        self.ai_retry_btn = QPushButton()
        self.ai_retry_btn.setVisible(False)
        self.ai_retry_btn.clicked.connect(self._retry_with_feedback)
        ai_action_row.addWidget(self.ai_retry_btn)
        self.ai_cancel_btn = QPushButton()
        self.ai_cancel_btn.setVisible(False)
        self.ai_cancel_btn.clicked.connect(self._handle_ai_cancel)
        ai_action_row.addWidget(self.ai_cancel_btn)
        ai_layout.addLayout(ai_action_row)

        ai_group_layout.addWidget(self._ai_content)

        layout.addWidget(self.ai_group)

        convert_row = QHBoxLayout()
        self.convert_btn = QPushButton()
        self.convert_btn.setObjectName("primaryBtn")
        self.convert_btn.clicked.connect(self._convert)
        convert_row.addWidget(self.convert_btn)

        self.listen_btn = QPushButton()
        self.listen_btn.setEnabled(False)
        self.listen_btn.clicked.connect(self._listen_preview)
        convert_row.addWidget(self.listen_btn)

        self.export_jianpu_btn = QPushButton()
        self.export_jianpu_btn.setEnabled(False)
        self.export_jianpu_btn.clicked.connect(self._export_jianpu)
        convert_row.addWidget(self.export_jianpu_btn)
        layout.addLayout(convert_row)

        self.result_text = QPlainTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setMaximumHeight(180)
        layout.addWidget(self.result_text, 1)

        self._suggested_transpose: int = 0
        self._midi_path: str = ""
        self._last_chart: object | None = None
        self._ai_note_map: dict[int, int] | None = None
        self._ai_position_map: dict[tuple[int, int], int] | None = None
        self._ai_worker = None
        self._preview_midi_path: str | None = None
        self._ai_available_notes: list[int] = []
        self._ai_last_result: object | None = None
        self._optimal_setting: object | None = None

        self._ai_timer = QTimer(self)
        self._ai_timer.setInterval(1000)
        self._ai_timer.timeout.connect(self._tick_ai_timer)
        self._ai_elapsed = 0

        self._ai_chunk_buffer: str = ""
        self._ai_chunk_dirty = False
        self._ai_chunk_timer = QTimer(self)
        self._ai_chunk_timer.setInterval(100)
        self._ai_chunk_timer.timeout.connect(self._flush_chunk_buffer)

        self._optimal_worker: _OptimalWorker | None = None
        self._optimal_debounce = QTimer(self)
        self._optimal_debounce.setSingleShot(True)
        self._optimal_debounce.setInterval(300)
        self._optimal_debounce.timeout.connect(self._do_refresh_optimal)

        self._tracks_model.itemChanged.connect(self._on_track_changed)
        self.mapping_edit.textChanged.connect(self._refresh_profiles)
        self._refresh_profiles()
        self.transpose_spin.valueChanged.connect(self._refresh_optimal_hint)
        self.octave_spin.valueChanged.connect(self._refresh_optimal_hint)
        self._tracks_model.itemChanged.connect(lambda: self._refresh_optimal_hint())
        self.mapping_edit.textChanged.connect(self._refresh_optimal_hint)

        self.ai_group.toggled.connect(self._on_ai_toggled)
        self._on_ai_toggled(True)

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
        self.tracks_label.setText(tr("convert.tracks"))

        self.key_info_label.setText(tr("convert.key_hint"))
        self.apply_suggest_btn.setText(tr("convert.apply_suggested"))
        self.apply_suggest_btn.setToolTip(tr("convert.apply_tooltip"))
        self.snap_check.setText(tr("convert.snap"))
        self.strict_check.setText(tr("convert.strict"))
        self.denoise_check.setText(tr("convert.denoise"))
        self.denoise_check.setToolTip(tr("convert.denoise_tip"))
        self.denoise_max_sim_label.setText(tr("convert.denoise_max_sim"))
        self.denoise_max_sim_spin.setToolTip(tr("convert.denoise_max_sim_tip"))
        self.denoise_max_repeat_label.setText(tr("convert.denoise_max_repeat"))
        self.denoise_max_repeat_spin.setToolTip(tr("convert.denoise_max_repeat_tip"))
        self.preview_midi_check.setText(tr("convert.preview_midi"))
        self.convert_btn.setText(tr("convert.btn"))
        self.listen_btn.setText(tr("convert.listen"))
        self.export_jianpu_btn.setText(tr("convert.export_jianpu"))

        self.transpose_spin.setToolTip(tr("convert.tip_transpose"))
        self.octave_spin.setToolTip(tr("convert.tip_octave"))
        self.note_mode_combo.setToolTip(tr("convert.tip_note_mode"))
        self.snap_check.setToolTip(tr("convert.tip_snap"))
        self.strict_check.setToolTip(tr("convert.tip_strict"))

        self.ai_group.setTitle(tr("convert.ai_group"))
        self.ai_hint_label.setText(tr("convert.ai_hint"))
        self.ai_settings_toggle.setText(tr("convert.ai_settings"))
        self.ai_key_label.setText(tr("convert.ai_key"))
        self.ai_url_label.setText(tr("convert.ai_url"))
        self.ai_model_label.setText(tr("convert.ai_model"))
        self.ai_mode_label.setText(tr("convert.ai_mode"))
        self.ai_mode_combo.setItemText(0, tr("convert.ai_mode_remap"))
        self.ai_mode_combo.setItemText(1, tr("convert.ai_mode_context"))
        self.ai_mode_combo.setToolTip(tr("convert.ai_mode_tip"))
        self.ai_style_label.setText(tr("convert.ai_style"))
        self.ai_style_combo.setItemText(0, tr("convert.ai_style_conservative"))
        self.ai_style_combo.setItemText(1, tr("convert.ai_style_balanced"))
        self.ai_style_combo.setItemText(2, tr("convert.ai_style_creative"))
        self.ai_style_combo.setToolTip(tr("convert.ai_style_tip"))
        self.ai_arrange_btn.setText(tr("convert.ai_arrange"))
        self.ai_edit_prompt_btn.setText(tr("convert.ai_edit_prompt"))
        self.ai_simplify_check.setText(tr("convert.ai_simplify"))
        self.ai_simplify_check.setToolTip(tr("convert.ai_simplify_tip"))
        self.ai_url_edit.setPlaceholderText(tr("convert.ai_url_placeholder"))
        self.ai_arrange_btn.setToolTip(tr("convert.ai_tip"))
        self.ai_optimal_apply_btn.setText(tr("convert.ai_key_apply"))
        self.ai_copy_btn.setText(tr("convert.ai_copy"))
        self.ai_review_table.setHorizontalHeaderLabels([
            tr("convert.ai_review_original"),
            tr("convert.ai_review_name"),
            tr("convert.ai_review_suggested"),
            tr("convert.ai_review_suggested_name"),
            tr("convert.ai_review_action"),
        ])
        self.ai_feedback_edit.setPlaceholderText(tr("convert.ai_feedback_placeholder"))
        self.ai_preview_btn.setText(tr("convert.ai_preview"))
        self.ai_apply_convert_btn.setText(tr("convert.ai_apply_convert"))
        self.ai_apply_btn.setText(tr("convert.ai_apply"))
        self.ai_retry_btn.setText(tr("convert.ai_retry"))
        self.ai_cancel_btn.setText(tr("convert.ai_cancel"))

    def set_midi_path(self, path: str) -> None:
        self._midi_path = path
        self.midi_edit.setText(path)
        stem = Path(path).stem
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.output_edit.setText(f"output/{stem}_{ts}.json")
        self._refresh_track_list(path)
        self._refresh_optimal_hint()

    def _refresh_track_list(self, path: str) -> None:
        self._tracks_model.blockSignals(True)
        self._tracks_model.clear()
        try:
            _, tracks = list_midi_tracks(Path(path))
            for t in tracks:
                label = f"[{t.index}] {t.name}" if t.name else f"[{t.index}]"
                if t.note_on_count > 0:
                    label += f"  ({t.note_on_count} notes)"
                item = QStandardItem(label)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if t.note_on_count > 0 else Qt.CheckState.Unchecked)
                item.setData(t.index, Qt.ItemDataRole.UserRole)
                self._tracks_model.appendRow(item)
        except Exception:
            pass
        self._tracks_model.blockSignals(False)
        self._update_tracks_combo_text()

    def _get_selected_tracks(self) -> list[int] | None:
        selected: list[int] = []
        total = self._tracks_model.rowCount()
        for i in range(total):
            item = self._tracks_model.item(i)
            if item and item.checkState() == Qt.CheckState.Checked:
                idx = item.data(Qt.ItemDataRole.UserRole)
                if idx is not None:
                    selected.append(idx)
        if not selected or len(selected) == total:
            return None
        return selected

    def _update_tracks_combo_text(self) -> None:
        selected = self._get_selected_tracks()
        if selected is None:
            self.tracks_combo.setCurrentText(tr("convert.select_all_tracks"))
        else:
            self.tracks_combo.setCurrentText(", ".join(str(i) for i in selected))

    def set_selected_tracks(self, indices: list[int]) -> None:
        self._tracks_model.blockSignals(True)
        for i in range(self._tracks_model.rowCount()):
            item = self._tracks_model.item(i)
            if item:
                idx = item.data(Qt.ItemDataRole.UserRole)
                item.setCheckState(
                    Qt.CheckState.Checked if idx in indices else Qt.CheckState.Unchecked
                )
        self._tracks_model.blockSignals(False)
        self._update_tracks_combo_text()
        self._on_track_changed()

    def _on_track_changed(self, _item=None) -> None:
        self._update_tracks_combo_text()
        midi_path = self._midi_path or self.midi_edit.text().strip()
        if not midi_path:
            return
        tracks = self._get_selected_tracks()
        try:
            analysis = analyze_midi_key(Path(midi_path), tracks=tracks)
        except Exception:
            analysis = MidiKeyAnalysis()
        self._apply_key_analysis(analysis)
        self._refresh_optimal_hint()

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

        tracks = self._get_selected_tracks()
        if tracks is not None:
            scope = tr("convert.key_scope").format(tracks=", ".join(str(i) for i in tracks))
        else:
            scope = tr("convert.key_scope_all")
        parts.append(f"[{scope}]")

        self.key_info_label.setText("  ".join(parts) if parts else tr("unknown"))
        self.key_info_label.setObjectName("keyInfoLabelActive")
        self._suggested_transpose = analysis.suggested_transpose
        self.apply_suggest_btn.setEnabled(True)

    def set_key_analysis(self, analysis: object) -> None:
        if not isinstance(analysis, MidiKeyAnalysis):
            return
        self._apply_key_analysis(analysis)

    def _on_ai_toggled(self, enabled: bool) -> None:
        """Show/hide AI internals and adapt the bottom action bar."""
        self._ai_content.setVisible(enabled)
        self.convert_btn.setVisible(not enabled)

    def _toggle_denoise_params(self) -> None:
        enabled = self.denoise_check.isChecked()
        self.denoise_max_sim_spin.setEnabled(enabled)
        self.denoise_max_sim_label.setEnabled(enabled)
        self.denoise_max_repeat_spin.setEnabled(enabled)
        self.denoise_max_repeat_label.setEnabled(enabled)

    def _toggle_api_settings(self) -> None:
        expanded = self.ai_settings_toggle.isChecked()
        self.ai_settings_container.setVisible(expanded)
        arrow = (
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self.ai_settings_toggle.setArrowType(arrow)

    def _apply_and_convert(self) -> None:
        self._apply_review_table()
        self._convert()

    def _apply_suggested_transpose(self) -> None:
        self.transpose_spin.setValue(self._suggested_transpose)

    def _refresh_optimal_hint(self) -> None:
        self._optimal_debounce.start()

    def _do_refresh_optimal(self) -> None:
        midi_path = self._midi_path or self.midi_edit.text().strip()
        mapping_path = self.mapping_edit.text().strip()
        if not midi_path or not mapping_path or not Path(midi_path).exists() or not Path(mapping_path).exists():
            self.ai_optimal_label.clear()
            self.ai_optimal_apply_btn.setVisible(False)
            self._optimal_setting = None
            return

        try:
            mapping_config = load_mapping(Path(mapping_path))
        except Exception:
            self.ai_optimal_label.clear()
            self.ai_optimal_apply_btn.setVisible(False)
            self._optimal_setting = None
            return

        profile = self.profile_combo.currentText().strip() or mapping_config.default_profile
        tracks = self._get_selected_tracks()

        if self._optimal_worker is not None:
            self._optimal_worker.finished.disconnect()
            self._optimal_worker.quit()
            self._optimal_worker.wait(500)
            self._optimal_worker = None

        self._optimal_worker = _OptimalWorker(
            Path(midi_path), mapping_config, profile, tracks, parent=self,
        )
        self._optimal_worker.finished.connect(self._on_optimal_results)
        self._optimal_worker.start()

    def _on_optimal_results(self, results: object) -> None:
        from src.application.ai_arranger import MUSIC_KEY_WIKI

        self._optimal_worker = None
        if not isinstance(results, list) or not results:
            self.ai_optimal_label.clear()
            self.ai_optimal_apply_btn.setVisible(False)
            self._optimal_setting = None
            return

        cur_transpose = self.transpose_spin.value()
        cur_octave = self.octave_spin.value()
        current = next(
            (r for r in results if r.transpose == cur_transpose and r.octave == cur_octave),
            None,
        )
        best = results[0]

        if current is None:
            current_count = best.unmapped_count
        else:
            current_count = current.unmapped_count

        if best.transpose == cur_transpose and best.octave == cur_octave:
            self.ai_optimal_label.setText(tr("convert.ai_key_optimal"))
            self.ai_optimal_apply_btn.setVisible(False)
            self._optimal_setting = None
            return

        instr_short = best.instruments[0].split("/")[0] if best.instruments else ""
        self._optimal_setting = best
        self.ai_optimal_label.setText(
            tr("convert.ai_key_hint").format(
                current_count=current_count,
                val=f"{best.transpose:+d}",
                octave=f"{best.octave:+d}",
                key=best.key_name,
                instrument=instr_short,
                best_count=best.unmapped_count,
                wiki=MUSIC_KEY_WIKI,
            )
        )
        self.ai_optimal_apply_btn.setVisible(self.ai_group.isChecked())

    def _apply_optimal_settings(self) -> None:
        from src.application.ai_arranger import OptimalSetting

        if not isinstance(self._optimal_setting, OptimalSetting):
            return
        self.transpose_spin.blockSignals(True)
        self.octave_spin.blockSignals(True)
        self.transpose_spin.setValue(self._optimal_setting.transpose)
        self.octave_spin.setValue(self._optimal_setting.octave)
        self.transpose_spin.blockSignals(False)
        self.octave_spin.blockSignals(False)
        self._refresh_optimal_hint()

    def _open_prompt_editor(self) -> None:
        from src.interfaces.gui.prompt_editor_dialog import PromptEditorDialog

        dlg = PromptEditorDialog(self)
        dlg.exec()

    def _browse_midi(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("convert.dialog_midi"), "", "MIDI Files (*.mid *.midi)")
        if path:
            self.set_midi_path(path)
            self.midi_changed.emit(path)

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

    def _ai_arrange(self) -> None:
        from src.application.ai_arranger import (
            get_arrange_precheck,
        )
        from src.interfaces.gui.workers.ai_worker import AiArrangeWorker

        midi_path = self._midi_path or self.midi_edit.text().strip()
        mapping_path = self.mapping_edit.text().strip()
        api_key = self.ai_key_edit.text().strip()

        if not midi_path or not mapping_path:
            self.ai_status_label.setText(tr("convert.err_required"))
            return
        if not api_key:
            self.ai_status_label.setText(tr("convert.ai_err_key"))
            return

        try:
            mapping_config = load_mapping(Path(mapping_path))
        except Exception as exc:
            self.ai_status_label.setText(tr("convert.err_mapping").format(err=exc))
            return

        profile = self.profile_combo.currentText().strip() or mapping_config.default_profile
        tracks = self._get_selected_tracks()
        base_url = self.ai_url_edit.text().strip() or None
        model = self.ai_model_edit.text().strip() or "gpt-4o-mini"

        mode = self.ai_mode_combo.currentData() or "remap"
        style = self.ai_style_combo.currentData() or "conservative"
        simplify = self.ai_simplify_check.isChecked()

        try:
            precheck = get_arrange_precheck(
                Path(midi_path),
                mapping_config,
                profile,
                transpose=self.transpose_spin.value(),
                octave=self.octave_spin.value(),
                tracks=tracks,
                mode=mode,
                style=style,
                simplify=simplify,
            )
        except Exception as exc:
            self.ai_status_label.setText(tr("convert.ai_err").format(err=exc))
            return

        self._ai_available_notes = precheck.available_notes

        if mode == "context" and precheck.requires_chunking:
            try:
                from PySide6.QtWidgets import QMessageBox
                reply = QMessageBox.question(
                    self,
                    tr("convert.ai_token_warn_title"),
                    tr("convert.ai_token_warn").format(tokens=precheck.estimated_tokens),
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
            except Exception:
                pass

        self.ai_arrange_btn.setEnabled(False)
        self.ai_arrange_btn.setVisible(False)
        self.ai_cancel_btn.setVisible(True)
        self.ai_cancel_btn.setEnabled(True)
        self._ai_elapsed = 0
        self.ai_status_label.setText(tr("convert.ai_working_time").format(sec=0))
        self._ai_timer.start()

        self._ai_worker = AiArrangeWorker(
            midi_path=Path(midi_path),
            mapping=mapping_config,
            profile_id=profile,
            api_key=api_key,
            transpose=self.transpose_spin.value(),
            octave=self.octave_spin.value(),
            tracks=tracks,
            base_url=base_url,
            model=model,
            mode=mode,
            style=style,
            simplify=simplify,
            parent=self,
        )
        self._ai_worker.finished.connect(self._on_ai_finished)
        self._ai_worker.error.connect(self._on_ai_error)
        self._ai_worker.chunk_received.connect(self._on_ai_chunk)
        self.result_text.clear()
        self._ai_chunk_buffer = ""
        self._ai_chunk_dirty = False
        self._ai_chunk_timer.start()
        self._ai_worker.start()

    def _on_ai_finished(self, result: object) -> None:
        from src.application.ai_arranger import AiArrangeResult

        self._ai_timer.stop()
        self._ai_chunk_timer.stop()
        self._flush_chunk_buffer()
        self.ai_copy_btn.setVisible(False)
        self.ai_cancel_btn.setText(tr("convert.ai_cancel"))
        self._ai_worker = None
        if not isinstance(result, AiArrangeResult):
            self.ai_arrange_btn.setEnabled(True)
            self.ai_arrange_btn.setVisible(True)
            self.ai_cancel_btn.setVisible(False)
            return

        self._ai_last_result = result
        elapsed = self._ai_elapsed

        self.ai_status_label.setText(tr("convert.ai_analyze_done").format(sec=elapsed))

        if result.analysis_text:
            self.result_text.setPlainText(result.analysis_text)
        else:
            self.result_text.setPlainText(result.explanation)

        if result.mode == "remap" and result.note_map:
            self._populate_review_table(result.note_map)
        elif result.mode == "context" and result.position_map:
            self._populate_context_review_table(result.position_map)
        self._enter_review_mode()

    def _on_ai_chunk(self, accumulated: str) -> None:
        self._ai_chunk_buffer = accumulated
        self._ai_chunk_dirty = True

    def _flush_chunk_buffer(self) -> None:
        if not self._ai_chunk_dirty:
            return
        self._ai_chunk_dirty = False
        self.result_text.setPlainText(self._ai_chunk_buffer)
        scrollbar = self.result_text.verticalScrollBar()
        if scrollbar:
            scrollbar.setValue(scrollbar.maximum())

    def _tick_ai_timer(self) -> None:
        self._ai_elapsed += 1
        self.ai_status_label.setText(tr("convert.ai_working_time").format(sec=self._ai_elapsed))

    def _on_ai_error(self, msg: str) -> None:
        from src.application.ai_arranger import AiArrangeCancelled, AiArrangeError

        self._ai_timer.stop()
        self._ai_chunk_timer.stop()
        elapsed = self._ai_elapsed
        self.ai_arrange_btn.setVisible(True)
        self.ai_arrange_btn.setEnabled(True)
        self.ai_cancel_btn.setVisible(False)
        self.ai_cancel_btn.setText(tr("convert.ai_cancel"))
        self._ai_note_map = None
        self._ai_position_map = None
        self._ai_worker = None
        if isinstance(msg, AiArrangeCancelled):
            self.ai_status_label.setText(tr("convert.ai_cancelled").format(sec=elapsed))
            self.ai_copy_btn.setVisible(False)
            return
        if isinstance(msg, AiArrangeError):
            detail = f" ({msg.detail})" if msg.detail else ""
            self.ai_status_label.setText(tr("convert.ai_err").format(err=f"{msg.user_message}{detail}") + f"  ({elapsed}s)")
        else:
            self.ai_status_label.setText(tr("convert.ai_err").format(err=msg) + f"  ({elapsed}s)")
        self.ai_copy_btn.setVisible(True)

    def _copy_ai_status(self) -> None:
        from PySide6.QtWidgets import QApplication

        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(self.ai_status_label.text())

    # ── Review mode ─────────────────────────────────────────

    def _enter_review_mode(self) -> None:
        self.ai_arrange_btn.setVisible(False)
        self.convert_btn.setVisible(False)
        has_table = self.ai_review_table.rowCount() > 0
        self.ai_review_table.setVisible(has_table)
        self.ai_feedback_edit.setVisible(True)
        self.ai_feedback_edit.clear()
        self.ai_preview_btn.setVisible(True)
        self.ai_apply_convert_btn.setVisible(True)
        self.ai_apply_btn.setVisible(True)
        self.ai_retry_btn.setVisible(True)
        self.ai_cancel_btn.setVisible(True)

    def _exit_review_mode(self) -> None:
        self.ai_review_table.setVisible(False)
        self.ai_review_table.setRowCount(0)
        self.ai_feedback_edit.setVisible(False)
        self.ai_preview_btn.setVisible(False)
        self.ai_apply_convert_btn.setVisible(False)
        self.ai_apply_btn.setVisible(False)
        self.ai_retry_btn.setVisible(False)
        self.ai_cancel_btn.setVisible(False)
        self.ai_arrange_btn.setVisible(True)
        self.ai_arrange_btn.setEnabled(True)
        self._ai_last_result = None
        self.ai_status_label.clear()
        self.convert_btn.setVisible(not self.ai_group.isChecked())

    def _handle_ai_cancel(self) -> None:
        worker = self._ai_worker
        if worker is not None and worker.isRunning():
            self.ai_cancel_btn.setEnabled(False)
            self.ai_status_label.setText(tr("convert.ai_cancelling"))
            try:
                cancel = getattr(worker, "cancel", None)
                if callable(cancel):
                    cancel()
                else:
                    worker.requestInterruption()
            except Exception as exc:
                logger.warning("Failed to request AI cancellation: %s", exc)
            return
        self._exit_review_mode()

    def _populate_review_table(self, note_map: dict[int, int]) -> None:
        from src.application.ai_arranger import midi_to_name

        table = self.ai_review_table
        table.setRowCount(len(note_map))
        drop_label = tr("convert.ai_review_drop")

        for row, (orig, repl) in enumerate(sorted(note_map.items())):
            orig_item = QTableWidgetItem(str(orig))
            orig_item.setFlags(orig_item.flags() & ~orig_item.flags().ItemIsEditable)
            table.setItem(row, 0, orig_item)

            name_item = QTableWidgetItem(midi_to_name(orig))
            name_item.setFlags(name_item.flags() & ~name_item.flags().ItemIsEditable)
            table.setItem(row, 1, name_item)

            sugg_item = QTableWidgetItem(drop_label if repl == -1 else str(repl))
            sugg_item.setFlags(sugg_item.flags() & ~sugg_item.flags().ItemIsEditable)
            table.setItem(row, 2, sugg_item)

            sugg_name_item = QTableWidgetItem(
                drop_label if repl == -1 else midi_to_name(repl)
            )
            sugg_name_item.setFlags(sugg_name_item.flags() & ~sugg_name_item.flags().ItemIsEditable)
            table.setItem(row, 3, sugg_name_item)

            combo = QComboBox()
            combo.addItem(drop_label, userData=-1)
            for n in self._ai_available_notes:
                combo.addItem(f"{n} ({midi_to_name(n)})", userData=n)
            if repl == -1:
                combo.setCurrentIndex(0)
            else:
                for i in range(combo.count()):
                    if combo.itemData(i) == repl:
                        combo.setCurrentIndex(i)
                        break
            table.setCellWidget(row, 4, combo)

    def _populate_context_review_table(self, position_map: list) -> None:
        """Build a per-note summary table for context mode results."""
        from src.application.ai_arranger import midi_to_name

        note_votes: dict[int, Counter[int]] = {}
        for pr in position_map:
            note_votes.setdefault(pr.original, Counter())[pr.replacement] += 1

        if not note_votes:
            return

        table = self.ai_review_table
        table.setRowCount(len(note_votes))
        drop_label = tr("convert.ai_review_drop")

        for row, (orig, counts) in enumerate(sorted(note_votes.items())):
            most_common_repl = counts.most_common(1)[0][0]
            total = sum(counts.values())
            dist_parts: list[str] = []
            for repl, cnt in counts.most_common(3):
                label = drop_label if repl == -1 else f"{repl}({midi_to_name(repl)})"
                dist_parts.append(f"{label} x{cnt}")
            dist_str = ", ".join(dist_parts)
            if len(counts) > 3:
                dist_str += ", ..."

            orig_item = QTableWidgetItem(str(orig))
            orig_item.setFlags(orig_item.flags() & ~orig_item.flags().ItemIsEditable)
            table.setItem(row, 0, orig_item)

            name_item = QTableWidgetItem(midi_to_name(orig))
            name_item.setFlags(name_item.flags() & ~name_item.flags().ItemIsEditable)
            table.setItem(row, 1, name_item)

            sugg_item = QTableWidgetItem(
                drop_label if most_common_repl == -1 else str(most_common_repl)
            )
            sugg_item.setFlags(sugg_item.flags() & ~sugg_item.flags().ItemIsEditable)
            table.setItem(row, 2, sugg_item)

            dist_item = QTableWidgetItem(f"({total}) {dist_str}")
            dist_item.setFlags(dist_item.flags() & ~dist_item.flags().ItemIsEditable)
            table.setItem(row, 3, dist_item)

            combo = QComboBox()
            combo.addItem(tr("convert.ai_review_keep_ctx"), userData=None)
            combo.addItem(drop_label, userData=-1)
            for n in self._ai_available_notes:
                combo.addItem(f"{n} ({midi_to_name(n)})", userData=n)
            combo.setCurrentIndex(0)
            table.setCellWidget(row, 4, combo)

    def _apply_review_table(self) -> None:
        from src.application.ai_arranger import AiArrangeResult

        result = self._ai_last_result
        if not isinstance(result, AiArrangeResult):
            self._exit_review_mode()
            return

        status_msg = ""
        if result.mode == "remap":
            note_map: dict[int, int] = {}
            for row in range(self.ai_review_table.rowCount()):
                orig_item = self.ai_review_table.item(row, 0)
                combo = self.ai_review_table.cellWidget(row, 4)
                if orig_item and isinstance(combo, QComboBox):
                    orig = int(orig_item.text())
                    repl = combo.currentData()
                    if isinstance(repl, int):
                        note_map[orig] = repl
            self._ai_note_map = note_map if note_map else None
            self._ai_position_map = None
            status_msg = tr("convert.ai_applied_direct").format(count=len(note_map))
        elif result.mode == "context" and result.position_map:
            pos_dict: dict[tuple[int, int], int] = {}
            note_votes: dict[int, Counter[int]] = {}
            for pr in result.position_map:
                pos_dict[(pr.time_ms, pr.original)] = pr.replacement
                if pr.replacement != -1:
                    note_votes.setdefault(pr.original, Counter())[pr.replacement] += 1
            self._ai_position_map = pos_dict
            fallback: dict[int, int] = {}
            for orig, counts in note_votes.items():
                fallback[orig] = counts.most_common(1)[0][0]

            overrides: dict[int, int] = {}
            for row in range(self.ai_review_table.rowCount()):
                orig_item = self.ai_review_table.item(row, 0)
                combo = self.ai_review_table.cellWidget(row, 4)
                if orig_item and isinstance(combo, QComboBox):
                    repl = combo.currentData()
                    if repl is not None:
                        orig = int(orig_item.text())
                        overrides[orig] = repl

            if overrides:
                for orig, forced_repl in overrides.items():
                    fallback[orig] = forced_repl
                    for key in list(pos_dict):
                        if key[1] == orig:
                            pos_dict[key] = forced_repl

            self._ai_note_map = fallback if fallback else None
            status_msg = tr("convert.ai_done_context").format(
                mapped=len(result.position_map), unmapped=result.unmapped_count,
            )

        self._exit_review_mode()
        self.ai_status_label.setText(status_msg)

    def _preview_ai_mapping(self) -> None:
        import os
        import tempfile

        from src.application.ai_arranger import AiArrangeResult

        result = self._ai_last_result
        if not isinstance(result, AiArrangeResult):
            return

        midi_path = self.midi_edit.text().strip()
        mapping_path = self.mapping_edit.text().strip()
        if not midi_path or not mapping_path:
            return

        try:
            mapping_config = load_mapping(Path(mapping_path))
        except Exception:
            return

        ai_note_map: dict[int, int] | None = None
        ai_position_map: dict[tuple[int, int], int] | None = None

        if result.mode == "remap":
            note_map: dict[int, int] = {}
            for row in range(self.ai_review_table.rowCount()):
                orig_item = self.ai_review_table.item(row, 0)
                combo = self.ai_review_table.cellWidget(row, 4)
                if orig_item and isinstance(combo, QComboBox):
                    orig = int(orig_item.text())
                    repl = combo.currentData()
                    if isinstance(repl, int):
                        note_map[orig] = repl
            ai_note_map = note_map if note_map else None
        elif result.mode == "context" and result.position_map:
            pos_dict: dict[tuple[int, int], int] = {}
            note_votes: dict[int, Counter[int]] = {}
            for pr in result.position_map:
                pos_dict[(pr.time_ms, pr.original)] = pr.replacement
                if pr.replacement != -1:
                    note_votes.setdefault(pr.original, Counter())[pr.replacement] += 1
            ai_position_map = pos_dict
            fallback_preview: dict[int, int] = {}
            for orig, counts in note_votes.items():
                fallback_preview[orig] = counts.most_common(1)[0][0]
            ai_note_map = fallback_preview if fallback_preview else None

        profile = self.profile_combo.currentText().strip() or None
        tracks = self._get_selected_tracks()

        options = ConvertOptions(
            profile=profile,
            transpose=self.transpose_spin.value(),
            octave=self.octave_spin.value(),
            strict=self.strict_check.isChecked(),
            snap=self.snap_check.isChecked(),
            note_mode=self.note_mode_combo.currentText(),
            tracks=tracks,
            ai_note_map=ai_note_map,
            ai_position_map=ai_position_map or {},
            denoise=self.denoise_check.isChecked(),
            denoise_max_simultaneous=self.denoise_max_sim_spin.value(),
            denoise_max_chord_repeats=self.denoise_max_repeat_spin.value(),
        )

        try:
            chart, _warnings = convert_midi_to_chart(Path(midi_path), mapping_config, options)
            tmp = tempfile.NamedTemporaryFile(suffix=".mid", delete=False)
            tmp.close()
            preview_path = Path(tmp.name)
            chart_to_preview_midi(chart, mapping_config, preview_path)
            os.startfile(str(preview_path))  # type: ignore[attr-defined]
        except Exception as exc:
            self.ai_status_label.setText(tr("convert.ai_preview_err").format(err=exc))

    def _retry_with_feedback(self) -> None:
        from src.application.ai_arranger import AiArrangeResult
        from src.interfaces.gui.workers.ai_worker import AiRetryWorker

        result = self._ai_last_result
        if not isinstance(result, AiArrangeResult):
            return

        feedback = self.ai_feedback_edit.toPlainText().strip()
        if not feedback:
            feedback = "Please try again with better arrangement."

        api_key = self.ai_key_edit.text().strip()
        base_url = self.ai_url_edit.text().strip() or None
        model = self.ai_model_edit.text().strip() or "gpt-4o-mini"

        max_tokens = 65536 if result.mode == "context" else 16384

        self._exit_review_mode()
        self.ai_arrange_btn.setVisible(False)
        self.ai_cancel_btn.setVisible(True)
        self.ai_cancel_btn.setEnabled(True)
        self._ai_elapsed = 0
        self.ai_status_label.setText(tr("convert.ai_working_time").format(sec=0))
        self._ai_timer.start()

        self._ai_worker = AiRetryWorker(
            original_prompt=result.prompt,
            previous_analysis=result.analysis_text or result.explanation,
            user_feedback=feedback,
            api_key=api_key,
            base_url=base_url,
            model=model,
            mode=result.mode,
            max_tokens=max_tokens,
            parent=self,
        )
        self._ai_worker.finished.connect(self._on_ai_finished)
        self._ai_worker.error.connect(self._on_ai_error)
        self._ai_worker.chunk_received.connect(self._on_ai_chunk)
        self.result_text.clear()
        self._ai_chunk_buffer = ""
        self._ai_chunk_dirty = False
        self._ai_chunk_timer.start()
        self._ai_worker.start()

    def _export_jianpu(self) -> None:
        from src.domain.chart import ChartDocument

        chart = self._last_chart
        if not isinstance(chart, ChartDocument):
            return

        midi_path = self._midi_path or self.midi_edit.text().strip()
        mapping_path = self.mapping_edit.text().strip()
        if not mapping_path:
            return

        try:
            mapping_config = load_mapping(Path(mapping_path))
        except Exception:
            return

        bpm = 120.0
        time_sig = "4/4"
        if midi_path and Path(midi_path).exists():
            try:
                meta = read_midi_meta(Path(midi_path), tracks=self._get_selected_tracks())
                bpm = meta.bpm
                time_sig = meta.time_signature
            except Exception:
                pass

        title = Path(midi_path).stem if midi_path else ""

        default_name = f"{title}.pdf" if title else "jianpu.pdf"
        path, _ = QFileDialog.getSaveFileName(
            self, tr("convert.dialog_jianpu"), default_name, "PDF Files (*.pdf)",
        )
        if not path:
            return

        try:
            chart_to_jianpu_pdf(
                chart, mapping_config, Path(path),
                bpm=bpm, time_signature=time_sig, title=title,
            )
            self.result_text.appendPlainText(tr("convert.jianpu_saved").format(path=path))
        except Exception as exc:
            self.result_text.appendPlainText(f"Export error: {exc}")

    def _listen_preview(self) -> None:
        import os

        if self._preview_midi_path and Path(self._preview_midi_path).exists():
            os.startfile(self._preview_midi_path)  # type: ignore[attr-defined]

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
        tracks = self._get_selected_tracks()

        options = ConvertOptions(
            profile=profile,
            transpose=self.transpose_spin.value(),
            octave=self.octave_spin.value(),
            strict=self.strict_check.isChecked(),
            snap=self.snap_check.isChecked(),
            note_mode=self.note_mode_combo.currentText(),
            tracks=tracks,
            ai_note_map=self._ai_note_map,
            ai_position_map=self._ai_position_map or {},
            denoise=self.denoise_check.isChecked(),
            denoise_max_simultaneous=self.denoise_max_sim_spin.value(),
            denoise_max_chord_repeats=self.denoise_max_repeat_spin.value(),
        )

        try:
            chart, warnings = convert_midi_to_chart(Path(midi_path), mapping_config, options)
        except MappingError as exc:
            self.result_text.setPlainText(tr("convert.err_map").format(err=exc))
            return
        except Exception as exc:
            self.result_text.setPlainText(tr("convert.err_convert").format(err=exc))
            return

        out = Path(output_path)
        if self._ai_position_map:
            output_path = str(out.with_stem(out.stem + "_ai-context"))
        elif self._ai_note_map:
            output_path = str(out.with_stem(out.stem + "_ai-remap"))
        save_chart(Path(output_path), chart)
        self._last_chart = chart
        self.export_jianpu_btn.setEnabled(True)

        lines = [
            tr("convert.saved").format(path=output_path),
            tr("convert.events").format(count=len(chart.events)),
        ]
        if self._ai_position_map:
            lines.append(tr("convert.ai_applied_context").format(count=len(self._ai_position_map)))
        elif self._ai_note_map:
            lines.append(tr("convert.ai_applied").format(count=len(self._ai_note_map)))

        self._preview_midi_path = None
        self.listen_btn.setEnabled(False)
        if self.preview_midi_check.isChecked():
            preview_path = Path(output_path).with_suffix(".preview.mid")
            try:
                chart_to_preview_midi(chart, mapping_config, preview_path)
                self._preview_midi_path = str(preview_path)
                self.listen_btn.setEnabled(True)
                lines.append(tr("convert.preview_midi_saved").format(path=preview_path))
            except Exception as exc:
                lines.append(tr("convert.preview_midi_err").format(err=exc))

        if warnings:
            lines.append(tr("convert.warnings").format(count=len(warnings)))
            for w in warnings[:30]:
                lines.append(f"  - {w}")
            if len(warnings) > 30:
                lines.append(f"  ... {len(warnings) - 30} more")
        self.result_text.setPlainText("\n".join(lines))
        self.chart_saved.emit(output_path)
