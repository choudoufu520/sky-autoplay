from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.domain.mapping import MappingConfig, MappingProfile
from src.infrastructure.repository import load_mapping, save_mapping
from src.interfaces.gui.paths import default_mapping_path
from src.interfaces.gui.i18n import on_language_changed, tr

_PITCH_CLASSES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _midi_to_name(num: int) -> str:
    return f"{_PITCH_CLASSES[num % 12]}{num // 12 - 1}"


class MappingTab(QWidget):
    mapping_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._config: MappingConfig | None = None
        self._dirty = False
        self._suppress_signals = False
        self._file_path = ""

        root = QVBoxLayout(self)

        # ── file row ──
        file_row = QHBoxLayout()
        self.file_label = QLabel()
        file_row.addWidget(self.file_label)
        self.file_edit = QLineEdit(default_mapping_path())
        file_row.addWidget(self.file_edit, 1)
        self.browse_btn = QPushButton()
        self.browse_btn.clicked.connect(self._browse)
        file_row.addWidget(self.browse_btn)
        self.load_btn = QPushButton()
        self.load_btn.clicked.connect(self._load)
        file_row.addWidget(self.load_btn)
        self.save_btn = QPushButton()
        self.save_btn.setObjectName("primaryBtn")
        self.save_btn.clicked.connect(self._save)
        file_row.addWidget(self.save_btn)
        self.save_as_btn = QPushButton()
        self.save_as_btn.clicked.connect(self._save_as)
        file_row.addWidget(self.save_as_btn)
        root.addLayout(file_row)

        # ── default profile ──
        default_row = QHBoxLayout()
        self.default_label = QLabel()
        default_row.addWidget(self.default_label)
        self.default_combo = QComboBox()
        self.default_combo.currentTextChanged.connect(self._on_default_changed)
        default_row.addWidget(self.default_combo, 1)
        root.addLayout(default_row)

        self.status_label = QLabel("")
        self.status_label.setObjectName("statusHint")
        root.addWidget(self.status_label)

        # ── splitter: left=profiles, right=editor ──
        splitter = QSplitter()

        # left panel: profile list
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.profile_group = QGroupBox()
        pg_layout = QVBoxLayout(self.profile_group)
        self.profile_list = QListWidget()
        self.profile_list.currentRowChanged.connect(self._on_profile_selected)
        pg_layout.addWidget(self.profile_list)
        btn_row = QHBoxLayout()
        self.add_profile_btn = QPushButton()
        self.add_profile_btn.clicked.connect(self._add_profile)
        btn_row.addWidget(self.add_profile_btn)
        self.rename_profile_btn = QPushButton()
        self.rename_profile_btn.clicked.connect(self._rename_profile)
        btn_row.addWidget(self.rename_profile_btn)
        self.del_profile_btn = QPushButton()
        self.del_profile_btn.setObjectName("dangerBtn")
        self.del_profile_btn.clicked.connect(self._del_profile)
        btn_row.addWidget(self.del_profile_btn)
        pg_layout.addLayout(btn_row)
        left_layout.addWidget(self.profile_group)

        # program-to-profile
        self.program_group = QGroupBox()
        prg_layout = QVBoxLayout(self.program_group)
        self.program_table = QTableWidget(0, 2)
        self.program_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.program_table.cellChanged.connect(self._on_program_cell_changed)
        prg_layout.addWidget(self.program_table)
        prg_btn_row = QHBoxLayout()
        self.add_program_btn = QPushButton()
        self.add_program_btn.clicked.connect(self._add_program_row)
        prg_btn_row.addWidget(self.add_program_btn)
        self.del_program_btn = QPushButton()
        self.del_program_btn.clicked.connect(self._del_program_row)
        prg_btn_row.addWidget(self.del_program_btn)
        prg_layout.addLayout(prg_btn_row)
        left_layout.addWidget(self.program_group)

        splitter.addWidget(left)

        # right panel: profile editor
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        params_row = QHBoxLayout()
        self.transpose_label = QLabel()
        params_row.addWidget(self.transpose_label)
        self.transpose_spin = QSpinBox()
        self.transpose_spin.setRange(-48, 48)
        self.transpose_spin.valueChanged.connect(self._on_transpose_changed)
        params_row.addWidget(self.transpose_spin)
        self.octave_label = QLabel()
        params_row.addWidget(self.octave_label)
        self.octave_spin = QSpinBox()
        self.octave_spin.setRange(-4, 4)
        self.octave_spin.valueChanged.connect(self._on_octave_changed)
        params_row.addWidget(self.octave_spin)
        params_row.addStretch(1)
        right_layout.addLayout(params_row)

        self.note_group = QGroupBox()
        ng_layout = QVBoxLayout(self.note_group)
        self.note_table = QTableWidget(0, 3)
        self.note_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.note_table.cellChanged.connect(self._on_note_cell_changed)
        ng_layout.addWidget(self.note_table)
        note_btn_row = QHBoxLayout()
        self.add_row_btn = QPushButton()
        self.add_row_btn.clicked.connect(self._add_note_row)
        note_btn_row.addWidget(self.add_row_btn)
        self.del_row_btn = QPushButton()
        self.del_row_btn.clicked.connect(self._del_note_row)
        note_btn_row.addWidget(self.del_row_btn)
        ng_layout.addLayout(note_btn_row)
        right_layout.addWidget(self.note_group, 1)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, 1)

        on_language_changed(self.retranslate)
        self.retranslate()

    # ── i18n ──

    def retranslate(self) -> None:
        self.file_label.setText(tr("mapping.file"))
        self.file_edit.setPlaceholderText(tr("mapping.file_placeholder"))
        self.browse_btn.setText(tr("browse"))
        self.load_btn.setText(tr("mapping.load"))
        self.save_btn.setText(tr("mapping.save"))
        self.save_as_btn.setText(tr("mapping.save_as"))
        self.default_label.setText(tr("mapping.default_profile"))
        self.profile_group.setTitle(tr("mapping.profiles"))
        self.add_profile_btn.setText(tr("mapping.add_profile"))
        self.rename_profile_btn.setText(tr("mapping.rename_profile"))
        self.del_profile_btn.setText(tr("mapping.del_profile"))
        self.transpose_label.setText(tr("mapping.transpose"))
        self.octave_label.setText(tr("mapping.octave"))
        self.note_group.setTitle(tr("mapping.note_to_key"))
        self.note_table.setHorizontalHeaderLabels([
            tr("mapping.col_note"), tr("mapping.col_key"), tr("mapping.col_note_name"),
        ])
        self.add_row_btn.setText(tr("mapping.add_row"))
        self.del_row_btn.setText(tr("mapping.del_row"))
        self.program_group.setTitle(tr("mapping.program_map"))
        self.program_table.setHorizontalHeaderLabels([
            tr("mapping.col_program"), tr("mapping.col_profile"),
        ])
        self.add_program_btn.setText(tr("mapping.add_program"))
        self.del_program_btn.setText(tr("mapping.del_program"))

    # ── file ops ──

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("mapping.dialog_open"), "", "YAML Files (*.yaml *.yml)"
        )
        if path:
            self.file_edit.setText(path)

    def _load(self) -> None:
        path = self.file_edit.text().strip()
        if not path:
            return
        try:
            self._config = load_mapping(Path(path))
        except Exception as exc:
            self.status_label.setText(tr("mapping.err_load").format(err=exc))
            return
        self._file_path = path
        self._dirty = False
        self._populate_from_config()
        self.status_label.setText(tr("mapping.loaded").format(path=path))

    def _save(self) -> None:
        path = self.file_edit.text().strip()
        if not path:
            self.status_label.setText(tr("mapping.err_no_file"))
            return
        self._sync_config_from_ui()
        if self._config is None:
            return
        try:
            save_mapping(Path(path), self._config)
        except Exception as exc:
            self.status_label.setText(tr("mapping.err_save").format(err=exc))
            return
        self._file_path = path
        self._dirty = False
        self.status_label.setText(tr("mapping.saved").format(path=path))
        self.mapping_changed.emit()

    def _save_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, tr("mapping.dialog_save"), "", "YAML Files (*.yaml *.yml)"
        )
        if path:
            self.file_edit.setText(path)
            self._save()

    # ── populate UI from config ──

    def _populate_from_config(self) -> None:
        self._suppress_signals = True
        cfg = self._config
        if cfg is None:
            self._suppress_signals = False
            return

        self.default_combo.clear()
        self.default_combo.addItems(list(cfg.profiles.keys()))
        idx = self.default_combo.findText(cfg.default_profile)
        if idx >= 0:
            self.default_combo.setCurrentIndex(idx)

        self.profile_list.clear()
        self.profile_list.addItems(list(cfg.profiles.keys()))
        if self.profile_list.count() > 0:
            self.profile_list.setCurrentRow(0)

        self._populate_program_table()
        self._suppress_signals = False
        self._on_profile_selected(self.profile_list.currentRow())

    def _populate_program_table(self) -> None:
        self._suppress_signals = True
        cfg = self._config
        if cfg is None:
            self.program_table.setRowCount(0)
            self._suppress_signals = False
            return
        self.program_table.setRowCount(len(cfg.program_to_profile))
        for row, (prog, prof) in enumerate(cfg.program_to_profile.items()):
            self.program_table.setItem(row, 0, QTableWidgetItem(str(prog)))
            self.program_table.setItem(row, 1, QTableWidgetItem(prof))
        self._suppress_signals = False

    def _populate_note_table(self, profile: MappingProfile) -> None:
        self._suppress_signals = True
        self.transpose_spin.setValue(profile.transpose_semitones)
        self.octave_spin.setValue(profile.octave_shift)
        entries = sorted(profile.note_to_key.items(), key=lambda x: _sort_key(x[0]))
        self.note_table.setRowCount(len(entries))
        for row, (note, key) in enumerate(entries):
            self.note_table.setItem(row, 0, QTableWidgetItem(note))
            self.note_table.setItem(row, 1, QTableWidgetItem(key))
            try:
                name = _midi_to_name(int(note))
            except ValueError:
                name = note
            item = QTableWidgetItem(name)
            item.setFlags(item.flags() & ~item.flags().__class__.ItemIsEditable)
            self.note_table.setItem(row, 2, item)
        self._suppress_signals = False

    # ── profile management ──

    def _current_profile_name(self) -> str | None:
        item = self.profile_list.currentItem()
        return item.text() if item else None

    def _on_profile_selected(self, row: int) -> None:
        if self._suppress_signals or self._config is None or row < 0:
            return
        name = self.profile_list.item(row).text()
        profile = self._config.profiles.get(name)
        if profile:
            self._populate_note_table(profile)

    def _add_profile(self) -> None:
        if self._config is None:
            return
        name, ok = QInputDialog.getText(self, tr("mapping.add_profile"), tr("mapping.input_name"))
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in self._config.profiles:
            return
        self._config.profiles[name] = MappingProfile()
        self._refresh_profile_list(name)
        self._mark_dirty()

    def _rename_profile(self) -> None:
        if self._config is None:
            return
        old_name = self._current_profile_name()
        if old_name is None:
            return
        new_name, ok = QInputDialog.getText(
            self, tr("mapping.rename_profile"), tr("mapping.input_new_name"), text=old_name
        )
        if not ok or not new_name.strip() or new_name.strip() == old_name:
            return
        new_name = new_name.strip()
        profile = self._config.profiles.pop(old_name)
        self._config.profiles[new_name] = profile
        if self._config.default_profile == old_name:
            self._config.default_profile = new_name
        for prog, prof in self._config.program_to_profile.items():
            if prof == old_name:
                self._config.program_to_profile[prog] = new_name
        self._refresh_profile_list(new_name)
        self._populate_program_table()
        self._mark_dirty()

    def _del_profile(self) -> None:
        if self._config is None:
            return
        name = self._current_profile_name()
        if name is None or len(self._config.profiles) <= 1:
            return
        ans = QMessageBox.question(
            self, tr("mapping.del_profile"),
            tr("mapping.confirm_delete").format(name=name),
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        del self._config.profiles[name]
        if self._config.default_profile == name:
            self._config.default_profile = next(iter(self._config.profiles))
        self._refresh_profile_list()
        self._mark_dirty()

    def _refresh_profile_list(self, select: str | None = None) -> None:
        if self._config is None:
            return
        self._suppress_signals = True
        self.profile_list.clear()
        names = list(self._config.profiles.keys())
        self.profile_list.addItems(names)
        self.default_combo.clear()
        self.default_combo.addItems(names)
        idx = self.default_combo.findText(self._config.default_profile)
        if idx >= 0:
            self.default_combo.setCurrentIndex(idx)
        self._suppress_signals = False
        if select and select in names:
            self.profile_list.setCurrentRow(names.index(select))
        elif names:
            self.profile_list.setCurrentRow(0)

    # ── note table editing ──

    def _on_note_cell_changed(self, row: int, col: int) -> None:
        if self._suppress_signals or self._config is None:
            return
        name = self._current_profile_name()
        if name is None:
            return
        profile = self._config.profiles.get(name)
        if profile is None:
            return
        self._sync_note_table_to_profile(profile)
        if col == 0:
            note_item = self.note_table.item(row, 0)
            if note_item:
                try:
                    label = _midi_to_name(int(note_item.text()))
                except ValueError:
                    label = note_item.text()
                self._suppress_signals = True
                name_item = QTableWidgetItem(label)
                name_item.setFlags(name_item.flags() & ~name_item.flags().__class__.ItemIsEditable)
                self.note_table.setItem(row, 2, name_item)
                self._suppress_signals = False
        self._mark_dirty()

    def _sync_note_table_to_profile(self, profile: MappingProfile) -> None:
        new_map: dict[str, str] = {}
        for r in range(self.note_table.rowCount()):
            note_item = self.note_table.item(r, 0)
            key_item = self.note_table.item(r, 1)
            if note_item and key_item:
                note_str = note_item.text().strip()
                key_str = key_item.text().strip()
                if note_str and key_str:
                    new_map[note_str] = key_str
        profile.note_to_key = new_map

    def _add_note_row(self) -> None:
        row = self.note_table.rowCount()
        self._suppress_signals = True
        self.note_table.insertRow(row)
        self.note_table.setItem(row, 0, QTableWidgetItem(""))
        self.note_table.setItem(row, 1, QTableWidgetItem(""))
        name_item = QTableWidgetItem("")
        name_item.setFlags(name_item.flags() & ~name_item.flags().__class__.ItemIsEditable)
        self.note_table.setItem(row, 2, name_item)
        self._suppress_signals = False
        self.note_table.editItem(self.note_table.item(row, 0))

    def _del_note_row(self) -> None:
        row = self.note_table.currentRow()
        if row < 0:
            return
        self.note_table.removeRow(row)
        if self._config is None:
            return
        name = self._current_profile_name()
        if name is None:
            return
        profile = self._config.profiles.get(name)
        if profile:
            self._sync_note_table_to_profile(profile)
        self._mark_dirty()

    # ── transpose / octave ──

    def _on_transpose_changed(self, val: int) -> None:
        if self._suppress_signals or self._config is None:
            return
        name = self._current_profile_name()
        if name and name in self._config.profiles:
            self._config.profiles[name].transpose_semitones = val
            self._mark_dirty()

    def _on_octave_changed(self, val: int) -> None:
        if self._suppress_signals or self._config is None:
            return
        name = self._current_profile_name()
        if name and name in self._config.profiles:
            self._config.profiles[name].octave_shift = val
            self._mark_dirty()

    # ── default profile ──

    def _on_default_changed(self, text: str) -> None:
        if self._suppress_signals or self._config is None or not text:
            return
        self._config.default_profile = text
        self._mark_dirty()

    # ── program table ──

    def _on_program_cell_changed(self, _row: int, _col: int) -> None:
        if self._suppress_signals or self._config is None:
            return
        self._sync_program_table()
        self._mark_dirty()

    def _sync_program_table(self) -> None:
        if self._config is None:
            return
        new_map: dict[int, str] = {}
        for r in range(self.program_table.rowCount()):
            prog_item = self.program_table.item(r, 0)
            prof_item = self.program_table.item(r, 1)
            if prog_item and prof_item:
                try:
                    prog = int(prog_item.text().strip())
                except ValueError:
                    continue
                prof = prof_item.text().strip()
                if prof:
                    new_map[prog] = prof
        self._config.program_to_profile = new_map

    def _add_program_row(self) -> None:
        row = self.program_table.rowCount()
        self._suppress_signals = True
        self.program_table.insertRow(row)
        self.program_table.setItem(row, 0, QTableWidgetItem("0"))
        self.program_table.setItem(row, 1, QTableWidgetItem(""))
        self._suppress_signals = False
        self.program_table.editItem(self.program_table.item(row, 0))

    def _del_program_row(self) -> None:
        row = self.program_table.currentRow()
        if row < 0:
            return
        self.program_table.removeRow(row)
        self._sync_program_table()
        self._mark_dirty()

    # ── sync all UI → config ──

    def _sync_config_from_ui(self) -> None:
        if self._config is None:
            return
        name = self._current_profile_name()
        if name and name in self._config.profiles:
            profile = self._config.profiles[name]
            self._sync_note_table_to_profile(profile)
            profile.transpose_semitones = self.transpose_spin.value()
            profile.octave_shift = self.octave_spin.value()
        self._sync_program_table()
        self._config.default_profile = self.default_combo.currentText()

    # ── dirty tracking ──

    def _mark_dirty(self) -> None:
        self._dirty = True
        path = self.file_edit.text().strip() or "?"
        self.status_label.setText(f"{path} {tr('mapping.unsaved')}")


def _sort_key(note_str: str) -> int:
    try:
        return int(note_str)
    except ValueError:
        return 999
