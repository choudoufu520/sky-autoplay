from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, QTimer, Signal
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
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.application.converter import ConvertOptions, MappingError, chart_to_preview_midi, convert_midi_to_chart
from src.infrastructure.midi_reader import MidiKeyAnalysis, analyze_midi_key, list_midi_tracks
from src.infrastructure.repository import load_mapping, save_chart
from src.interfaces.gui.paths import default_mapping_path
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

        self.single_track_label = QLabel()
        self.single_track_combo = QComboBox()
        self.single_track_combo.addItem(tr("convert.all"), userData=None)
        self.form.addRow(self.single_track_label, self.single_track_combo)

        self.snap_check = QCheckBox()
        self.snap_check.setChecked(True)
        self.form.addRow(self.snap_check)

        self.strict_check = QCheckBox()
        self.form.addRow(self.strict_check)

        self.preview_midi_check = QCheckBox()
        self.form.addRow(self.preview_midi_check)

        layout.addLayout(self.form)

        # AI Arrange section
        self.ai_group = QGroupBox()
        ai_layout = QVBoxLayout(self.ai_group)

        self._settings = QSettings("SkyMusicAutomation", "SkyMusicAutomation")

        ai_row1 = QHBoxLayout()
        self.ai_key_label = QLabel("API Key:")
        ai_row1.addWidget(self.ai_key_label)
        self.ai_key_edit = QLineEdit()
        self.ai_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.ai_key_edit.setPlaceholderText("sk-...")
        self.ai_key_edit.setText(self._settings.value("ai/api_key", ""))
        self.ai_key_edit.textChanged.connect(lambda v: self._settings.setValue("ai/api_key", v))
        ai_row1.addWidget(self.ai_key_edit, 1)
        ai_layout.addLayout(ai_row1)

        ai_row2 = QHBoxLayout()
        self.ai_url_label = QLabel("Base URL:")
        ai_row2.addWidget(self.ai_url_label)
        self.ai_url_edit = QLineEdit()
        self.ai_url_edit.setPlaceholderText(tr("convert.ai_url_placeholder"))
        self.ai_url_edit.setText(self._settings.value("ai/base_url", ""))
        self.ai_url_edit.textChanged.connect(lambda v: self._settings.setValue("ai/base_url", v))
        ai_row2.addWidget(self.ai_url_edit, 1)
        ai_layout.addLayout(ai_row2)

        ai_row3 = QHBoxLayout()
        self.ai_model_label = QLabel("Model:")
        ai_row3.addWidget(self.ai_model_label)
        self.ai_model_edit = QLineEdit(self._settings.value("ai/model", "gpt-4o-mini"))
        self.ai_model_edit.textChanged.connect(lambda v: self._settings.setValue("ai/model", v))
        ai_row3.addWidget(self.ai_model_edit, 1)
        ai_layout.addLayout(ai_row3)

        ai_row4 = QHBoxLayout()
        self.ai_mode_label = QLabel()
        ai_row4.addWidget(self.ai_mode_label)
        self.ai_mode_combo = QComboBox()
        self.ai_mode_combo.addItem(tr("convert.ai_mode_remap"), userData="remap")
        self.ai_mode_combo.addItem(tr("convert.ai_mode_context"), userData="context")
        ai_row4.addWidget(self.ai_mode_combo, 1)

        self.ai_style_label = QLabel()
        ai_row4.addWidget(self.ai_style_label)
        self.ai_style_combo = QComboBox()
        self.ai_style_combo.addItem(tr("convert.ai_style_conservative"), userData="conservative")
        self.ai_style_combo.addItem(tr("convert.ai_style_balanced"), userData="balanced")
        self.ai_style_combo.addItem(tr("convert.ai_style_creative"), userData="creative")
        ai_row4.addWidget(self.ai_style_combo, 1)

        self.ai_arrange_btn = QPushButton()
        self.ai_arrange_btn.clicked.connect(self._ai_arrange)
        ai_row4.addWidget(self.ai_arrange_btn)
        ai_layout.addLayout(ai_row4)

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
        self.ai_cancel_btn.clicked.connect(self._exit_review_mode)
        ai_action_row.addWidget(self.ai_cancel_btn)
        ai_layout.addLayout(ai_action_row)

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
        layout.addLayout(convert_row)

        self.result_text = QPlainTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setMaximumHeight(180)
        layout.addWidget(self.result_text, 1)

        self._suggested_transpose: int = 0
        self._midi_path: str = ""
        self._ai_note_map: dict[int, int] | None = None
        self._ai_position_map: dict[tuple[int, int], int] | None = None
        self._ai_worker = None
        self._preview_midi_path: str | None = None
        self._ai_available_notes: list[int] = []
        self._ai_last_result: object | None = None

        self._ai_timer = QTimer(self)
        self._ai_timer.setInterval(1000)
        self._ai_timer.timeout.connect(self._tick_ai_timer)
        self._ai_elapsed = 0

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
        self.preview_midi_check.setText(tr("convert.preview_midi"))
        self.convert_btn.setText(tr("convert.btn"))
        self.listen_btn.setText(tr("convert.listen"))

        self.transpose_spin.setToolTip(tr("convert.tip_transpose"))
        self.octave_spin.setToolTip(tr("convert.tip_octave"))
        self.note_mode_combo.setToolTip(tr("convert.tip_note_mode"))
        self.snap_check.setToolTip(tr("convert.tip_snap"))
        self.strict_check.setToolTip(tr("convert.tip_strict"))

        self.ai_group.setTitle(tr("convert.ai_group"))
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
        self.ai_url_edit.setPlaceholderText(tr("convert.ai_url_placeholder"))
        self.ai_arrange_btn.setToolTip(tr("convert.ai_tip"))
        self.ai_copy_btn.setText(tr("convert.ai_copy"))
        self.ai_review_table.setHorizontalHeaderLabels([
            tr("convert.ai_review_original"),
            tr("convert.ai_review_name"),
            tr("convert.ai_review_suggested"),
            tr("convert.ai_review_suggested_name"),
            tr("convert.ai_review_action"),
        ])
        self.ai_feedback_edit.setPlaceholderText(tr("convert.ai_feedback_placeholder"))
        self.ai_apply_btn.setText(tr("convert.ai_apply"))
        self.ai_retry_btn.setText(tr("convert.ai_retry"))
        self.ai_cancel_btn.setText(tr("convert.ai_cancel"))

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

    def _ai_arrange(self) -> None:
        from src.application.ai_arranger import _get_available_notes
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
        self._ai_available_notes = _get_available_notes(mapping_config, profile)
        single_track_val = self.single_track_combo.currentData()
        base_url = self.ai_url_edit.text().strip() or None
        model = self.ai_model_edit.text().strip() or "gpt-4o-mini"

        mode = self.ai_mode_combo.currentData() or "remap"
        style = self.ai_style_combo.currentData() or "conservative"

        self.ai_arrange_btn.setEnabled(False)
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
            single_track=single_track_val,
            base_url=base_url,
            model=model,
            mode=mode,
            style=style,
            parent=self,
        )
        self._ai_worker.finished.connect(self._on_ai_finished)
        self._ai_worker.error.connect(self._on_ai_error)
        self._ai_worker.chunk_received.connect(self._on_ai_chunk)
        self.result_text.clear()
        self._ai_worker.start()

    def _on_ai_finished(self, result: object) -> None:
        from src.application.ai_arranger import AiArrangeResult

        self._ai_timer.stop()
        self.ai_copy_btn.setVisible(False)
        if not isinstance(result, AiArrangeResult):
            self.ai_arrange_btn.setEnabled(True)
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
        self._enter_review_mode()

    def _on_ai_chunk(self, accumulated: str) -> None:
        self.result_text.setPlainText(accumulated)
        scrollbar = self.result_text.verticalScrollBar()
        if scrollbar:
            scrollbar.setValue(scrollbar.maximum())

    def _tick_ai_timer(self) -> None:
        self._ai_elapsed += 1
        self.ai_status_label.setText(tr("convert.ai_working_time").format(sec=self._ai_elapsed))

    def _on_ai_error(self, msg: str) -> None:
        self._ai_timer.stop()
        elapsed = self._ai_elapsed
        self.ai_arrange_btn.setVisible(True)
        self.ai_arrange_btn.setEnabled(True)
        self._ai_note_map = None
        self._ai_position_map = None
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
        is_remap = (
            self._ai_last_result is not None
            and getattr(self._ai_last_result, "mode", "") == "remap"
        )
        self.ai_review_table.setVisible(is_remap)
        self.ai_feedback_edit.setVisible(True)
        self.ai_feedback_edit.clear()
        self.ai_apply_btn.setVisible(True)
        self.ai_retry_btn.setVisible(True)
        self.ai_cancel_btn.setVisible(True)

    def _exit_review_mode(self) -> None:
        self.ai_review_table.setVisible(False)
        self.ai_review_table.setRowCount(0)
        self.ai_feedback_edit.setVisible(False)
        self.ai_apply_btn.setVisible(False)
        self.ai_retry_btn.setVisible(False)
        self.ai_cancel_btn.setVisible(False)
        self.ai_arrange_btn.setVisible(True)
        self.ai_arrange_btn.setEnabled(True)
        self._ai_last_result = None
        self.ai_status_label.clear()

    def _populate_review_table(self, note_map: dict[int, int]) -> None:
        from src.application.ai_arranger import _midi_to_name

        table = self.ai_review_table
        table.setRowCount(len(note_map))
        drop_label = tr("convert.ai_review_drop")

        for row, (orig, repl) in enumerate(sorted(note_map.items())):
            orig_item = QTableWidgetItem(str(orig))
            orig_item.setFlags(orig_item.flags() & ~orig_item.flags().ItemIsEditable)
            table.setItem(row, 0, orig_item)

            name_item = QTableWidgetItem(_midi_to_name(orig))
            name_item.setFlags(name_item.flags() & ~name_item.flags().ItemIsEditable)
            table.setItem(row, 1, name_item)

            sugg_item = QTableWidgetItem(drop_label if repl == -1 else str(repl))
            sugg_item.setFlags(sugg_item.flags() & ~sugg_item.flags().ItemIsEditable)
            table.setItem(row, 2, sugg_item)

            sugg_name_item = QTableWidgetItem(
                drop_label if repl == -1 else _midi_to_name(repl)
            )
            sugg_name_item.setFlags(sugg_name_item.flags() & ~sugg_name_item.flags().ItemIsEditable)
            table.setItem(row, 3, sugg_name_item)

            combo = QComboBox()
            combo.addItem(drop_label, userData=-1)
            for n in self._ai_available_notes:
                combo.addItem(f"{n} ({_midi_to_name(n)})", userData=n)
            if repl == -1:
                combo.setCurrentIndex(0)
            else:
                for i in range(combo.count()):
                    if combo.itemData(i) == repl:
                        combo.setCurrentIndex(i)
                        break
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
            for pr in result.position_map:
                pos_dict[(pr.time_ms, pr.original)] = pr.replacement
            self._ai_position_map = pos_dict
            self._ai_note_map = None
            status_msg = tr("convert.ai_done_context").format(
                mapped=len(result.position_map), unmapped=result.unmapped_count,
            )

        self._exit_review_mode()
        self.ai_status_label.setText(status_msg)

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
        self._ai_worker.start()

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
        single_track_val = self.single_track_combo.currentData()

        options = ConvertOptions(
            profile=profile,
            transpose=self.transpose_spin.value(),
            octave=self.octave_spin.value(),
            strict=self.strict_check.isChecked(),
            snap=self.snap_check.isChecked(),
            note_mode=self.note_mode_combo.currentText(),
            single_track=single_track_val,
            ai_note_map=self._ai_note_map,
            ai_position_map=self._ai_position_map or {},
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
