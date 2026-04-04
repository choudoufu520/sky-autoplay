from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.application.prompt_store import (
    DEFAULT_TEMPLATES,
    TEMPLATE_KEYS,
    get_prompts_path,
    load_custom_prompts,
    save_custom_prompts,
)
from src.interfaces.gui.i18n import tr

_KEY_TO_TR: dict[str, str] = {
    "remap_template": "prompt.remap_template",
    "context_template": "prompt.context_template",
    "style_conservative": "prompt.style_conservative",
    "style_balanced": "prompt.style_balanced",
    "style_creative": "prompt.style_creative",
}


class PromptEditorDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("prompt.title"))
        self.resize(820, 560)

        self._templates = load_custom_prompts()
        self._current_key: str = TEMPLATE_KEYS[0]

        root = QVBoxLayout(self)

        hint = QLabel(tr("prompt.hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888; margin-bottom: 4px;")
        root.addWidget(hint)

        body = QHBoxLayout()
        root.addLayout(body, 1)

        self._list = QListWidget()
        self._list.setFixedWidth(180)
        for key in TEMPLATE_KEYS:
            self._list.addItem(tr(_KEY_TO_TR[key]))
        self._list.setCurrentRow(0)
        self._list.currentRowChanged.connect(self._on_select)
        body.addWidget(self._list)

        self._editor = QPlainTextEdit()
        self._editor.setPlainText(self._templates[self._current_key])
        self._editor.textChanged.connect(self._on_text_changed)
        body.addWidget(self._editor, 1)

        btn_row = QHBoxLayout()
        root.addLayout(btn_row)

        self._btn_reset_current = QPushButton(tr("prompt.reset_current"))
        self._btn_reset_current.clicked.connect(self._reset_current)
        btn_row.addWidget(self._btn_reset_current)

        self._btn_reset_all = QPushButton(tr("prompt.reset_all"))
        self._btn_reset_all.clicked.connect(self._reset_all)
        btn_row.addWidget(self._btn_reset_all)

        btn_row.addStretch()

        self._btn_save = QPushButton(tr("prompt.save"))
        self._btn_save.clicked.connect(self._save)
        btn_row.addWidget(self._btn_save)

        self._btn_cancel = QPushButton(tr("prompt.cancel"))
        self._btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self._btn_cancel)

    def _on_select(self, row: int) -> None:
        if row < 0 or row >= len(TEMPLATE_KEYS):
            return
        self._current_key = TEMPLATE_KEYS[row]
        self._editor.blockSignals(True)
        self._editor.setPlainText(self._templates[self._current_key])
        self._editor.blockSignals(False)

    def _on_text_changed(self) -> None:
        self._templates[self._current_key] = self._editor.toPlainText()

    def _reset_current(self) -> None:
        self._templates[self._current_key] = DEFAULT_TEMPLATES[self._current_key]
        self._editor.blockSignals(True)
        self._editor.setPlainText(self._templates[self._current_key])
        self._editor.blockSignals(False)

    def _reset_all(self) -> None:
        self._templates = dict(DEFAULT_TEMPLATES)
        self._editor.blockSignals(True)
        self._editor.setPlainText(self._templates[self._current_key])
        self._editor.blockSignals(False)

    def _save(self) -> None:
        save_custom_prompts(self._templates, get_prompts_path())
        QMessageBox.information(self, tr("prompt.title"), tr("prompt.saved"))
        self.accept()
