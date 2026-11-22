import logging
from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame,
    QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QSizePolicy, QSplitter, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from .config import Config, Settings
from .hotkeys import Hotkeys
from .models import Capture, Event, Match, SubstitutionReason
from .sessions import Sessions

logger = logging.getLogger(__name__)

STYLE = """
QWidget { background: #10151f; color: #e7edf7; font-size: 14px; }
QLabel#title { font-size: 27px; font-weight: 700; }
QLabel[muted="true"] { color: #aab8cc; }
QFrame#panel, QLabel#preview { background: #171f2e; border-radius: 10px; }
QLineEdit, QPlainTextEdit, QComboBox, QPushButton {
    background: #202a3b; border: 1px solid #36435a; border-radius: 6px; padding: 8px;
}
QPushButton:hover { background: #303e54; }
QPushButton#primary { background: #66e0b6; color: #10251f; font-weight: bold; }
QPushButton#primary:hover { background: #91ebce; }
QPushButton:disabled { background: #181f2b; color: #8290a4; }
QLineEdit:focus, QPushButton:focus, QComboBox:focus { border-color: #66e0b6; }
QTableWidget { background: #171f2e; gridline-color: #263245; selection-background-color: #315669; }
QHeaderView::section { background: #202a3b; color: #e7edf7; border: none; padding: 8px; }
QSplitter::handle { background: #36435a; }
"""


def local_time(value: str) -> str:
    return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M:%S")


class ShortcutDialog(QDialog):
    def __init__(self, settings: Settings, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Global shortcuts")
        layout = QFormLayout(self)
        self.enabled = QCheckBox("Enable global shortcuts")
        self.enabled.setChecked(settings.enabled)
        layout.addRow(self.enabled)
        self.fields = {event: QLineEdit(settings.shortcuts[event]) for event in Event}
        for event, field in self.fields.items():
            layout.addRow(event.label, field)
        layout.addRow(QLabel("Use F1–F12 or a combination such as Ctrl+Shift+G."))
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)
        self.settings = settings

    def accept(self) -> None:
        settings = Settings(self.enabled.isChecked(),
                            {event: field.text().strip() for event, field in self.fields.items()})
        try:
            settings.bindings()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid shortcut", str(exc))
            return
        self.settings = settings
        super().accept()


class SubstitutionDialog(QDialog):
    def __init__(self, capture: Capture, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Substitution reason")
        layout = QFormLayout(self)
        self.reason = QComboBox()
        self.reason.addItem("Select a reason")
        for reason in SubstitutionReason:
            self.reason.addItem(reason.label, reason.value)
        if capture.substitution_reason is not None:
            self.reason.setCurrentIndex(self.reason.findData(capture.substitution_reason.value))
        self.notes = QPlainTextEdit(capture.substitution_notes or "")
        self.notes.setMaximumHeight(95)
        layout.addRow("Reason", self.reason)
        layout.addRow("Notes (optional)", self.notes)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        save = buttons.button(QDialogButtonBox.StandardButton.Save)
        save.setEnabled(self.reason.currentIndex() > 0)
        self.reason.currentIndexChanged.connect(lambda index: save.setEnabled(index > 0))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)


class Window(QMainWindow):
    shortcut_pressed = Signal(object)

    def __init__(self, sessions: Sessions, config: Config) -> None:
        super().__init__()
        self.sessions = sessions
        self.config = config
        self.settings = config.load()
        self.hotkeys = Hotkeys(self.shortcut_pressed.emit)
        self.shortcut_pressed.connect(self.capture_from_hotkey, Qt.ConnectionType.QueuedConnection)
        self.captures: list[Capture] = []
        self.pixmap = QPixmap()
        self.active: Match | None = None
        self.classifying_substitution = False
        self.setWindowTitle("eFootball Analytics")
        self.resize(1150, 760)
        self.setMinimumSize(820, 590)
        self.setStyleSheet(STYLE)
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(22, 20, 22, 12)
        layout.setSpacing(14)

        header = QHBoxLayout()
        title = QLabel("eFootball Analytics")
        title.setObjectName("title")
        header.addWidget(title)
        header.addStretch()
        self.shortcut_button = QPushButton("Global shortcuts")
        self.shortcut_button.clicked.connect(self.edit_shortcuts)
        header.addWidget(self.shortcut_button)
        layout.addLayout(header)

        panel = QFrame()
        panel.setObjectName("panel")
        controls = QGridLayout(panel)
        self.team = QLineEdit()
        self.team.setPlaceholderText("Your team")
        self.team.setAccessibleName("Your team")
        self.opponent = QLineEdit()
        self.opponent.setPlaceholderText("Opponent")
        self.opponent.setAccessibleName("Opponent")
        self.start_button = QPushButton("Start match")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.start_match)
        self.finish_button = QPushButton("Finish match")
        self.finish_button.clicked.connect(self.finish_match)
        self.match_status = QLabel()
        self.match_status.setProperty("muted", True)
        self.match_status.setWordWrap(True)
        controls.addWidget(self.team, 0, 0)
        controls.addWidget(self.opponent, 0, 1)
        controls.addWidget(self.start_button, 0, 2)
        controls.addWidget(self.finish_button, 0, 3)
        controls.addWidget(self.match_status, 1, 0, 1, 4)
        self.event_buttons: dict[Event, QPushButton] = {}
        for index, event in enumerate(Event):
            button = QPushButton(event.label)
            button.clicked.connect(lambda checked=False, category=event: self.save_capture(category))
            self.event_buttons[event] = button
            row, column = divmod(index, 3)
            controls.addWidget(button, row + 2, column)
        self.capture_minute = QLineEdit()
        self.capture_minute.setPlaceholderText("Minute (optional)")
        self.capture_minute.setAccessibleName("Minute for next capture")
        controls.addWidget(self.capture_minute, 2, 3)
        layout.addWidget(panel)

        history = QHBoxLayout()
        history.addWidget(QLabel("Capture history"))
        self.match_picker = QComboBox()
        self.match_picker.setAccessibleName("Match history")
        history.addWidget(self.match_picker, 1)
        layout.addLayout(history)

        splitter = QSplitter()
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Captured", "Event", "Minute", "Substitution reason"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self.show_selection)
        splitter.addWidget(self.table)
        self.preview = QLabel("Select a capture to preview it")
        self.preview.setObjectName("preview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(280, 160)
        self.preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        splitter.addWidget(self.preview)
        splitter.setSizes([470, 630])
        splitter.splitterMoved.connect(self.render_preview)
        layout.addWidget(splitter, 1)

        actions = QHBoxLayout()
        self.minute = QLineEdit()
        self.minute.setPlaceholderText("Correct minute")
        self.minute.setAccessibleName("Correct selected capture minute")
        self.save_minute_button = QPushButton("Save minute")
        self.save_minute_button.clicked.connect(self.save_minute)
        self.edit_reason_button = QPushButton("Edit substitution reason")
        self.edit_reason_button.clicked.connect(self.edit_substitution)
        self.delete_button = QPushButton("Delete capture")
        self.delete_button.clicked.connect(self.delete_capture)
        actions.addWidget(self.minute)
        actions.addWidget(self.save_minute_button)
        actions.addWidget(self.edit_reason_button)
        actions.addStretch()
        actions.addWidget(self.delete_button)
        layout.addLayout(actions)
        self.match_picker.currentIndexChanged.connect(self.load_history)
        self.refresh_matches()
        self.apply_shortcuts()

    def attempt(self, action: Callable[[], None]) -> None:
        try:
            action()
        except ValueError as exc:
            self.statusBar().showMessage(str(exc), 7000)
        except Exception as exc:
            logger.exception("Desktop operation failed")
            self.statusBar().showMessage(str(exc), 10000)

    def refresh_matches(self, preferred: str | None = None) -> None:
        matches = self.sessions.database.matches()
        self.active = next((match for match in matches if match.finished_at is None), None)
        preferred = preferred or self.match_picker.currentData()
        self.match_picker.blockSignals(True)
        self.match_picker.clear()
        for match in matches:
            status = " • Active" if match.finished_at is None else ""
            self.match_picker.addItem(
                f"{local_time(match.started_at)}  ·  {match.team} vs {match.opponent}{status}",
                match.id,
            )
            self.match_picker.setItemData(
                self.match_picker.count() - 1, match.id, Qt.ItemDataRole.ToolTipRole
            )
        if preferred and (index := self.match_picker.findData(preferred)) >= 0:
            self.match_picker.setCurrentIndex(index)
        self.match_picker.blockSignals(False)
        active = self.active is not None
        for widget in (self.team, self.opponent, self.start_button):
            widget.setEnabled(not active)
        for widget in (self.finish_button, self.capture_minute, *self.event_buttons.values()):
            widget.setEnabled(active)
        if self.active:
            self.team.setText(self.active.team)
            self.opponent.setText(self.active.opponent)
            self.match_status.setText(f"Active match  ·  {self.active.id}")
        else:
            self.match_status.setText("No active match")
        self.refresh_history()

    def start_match(self) -> None:
        def start() -> None:
            match = self.sessions.start(self.opponent.text(), self.team.text())
            self.capture_minute.clear()
            self.refresh_matches(match.id)
            self.statusBar().showMessage("Match started", 4000)

        self.attempt(start)

    def finish_match(self) -> None:
        def finish() -> None:
            self.sessions.finish()
            self.refresh_matches()
            self.statusBar().showMessage("Match finished", 4000)

        self.attempt(finish)

    def capture_from_hotkey(self, event: Event) -> None:
        if self.settings.enabled and self.active:
            self.save_capture(event)

    def save_capture(self, event: Event) -> None:
        if event == Event.SUBSTITUTION and self.classifying_substitution:
            return

        def save() -> None:
            capture = self.sessions.capture(event, self.capture_minute.text())
            self.capture_minute.clear()
            if self.match_picker.currentData() == capture.match_id:
                self.refresh_history(capture.id)
            self.statusBar().showMessage(f"{event.label} saved", 4000)
            if event == Event.SUBSTITUTION:
                self.prompt_substitution(capture)

        self.attempt(save)

    def load_history(self) -> None:
        self.attempt(self.refresh_history)

    def refresh_history(self, selected_id: str | None = None) -> None:
        match_id = self.match_picker.currentData()
        self.captures = self.sessions.database.captures(match_id) if match_id else []
        self.table.blockSignals(True)
        self.table.clearSelection()
        self.table.setRowCount(len(self.captures))
        for row, capture in enumerate(self.captures):
            for column, value in enumerate(
                (local_time(capture.captured_at), capture.event.label, capture.minute or "",
                 capture.substitution_reason.label if capture.substitution_reason else "")
            ):
                self.table.setItem(row, column, QTableWidgetItem(value))
            if capture.id == selected_id:
                self.table.selectRow(row)
        self.table.blockSignals(False)
        self.show_selection()

    def selected_capture(self) -> Capture | None:
        rows = self.table.selectionModel().selectedRows()
        return self.captures[rows[0].row()] if rows else None

    def show_selection(self) -> None:
        capture = self.selected_capture()
        for widget in (self.minute, self.save_minute_button, self.delete_button):
            widget.setEnabled(capture is not None)
        self.edit_reason_button.setEnabled(capture is not None and capture.event == Event.SUBSTITUTION)
        self.minute.setText((capture.minute or "") if capture else "")
        self.pixmap = QPixmap(str(self.sessions.root / capture.path)) if capture else QPixmap()
        self.render_preview()

    def render_preview(self, *_: int) -> None:
        if self.pixmap.isNull():
            self.preview.setText("Screenshot unavailable" if self.selected_capture()
                                 else "Select a capture to preview it")
        else:
            self.preview.setPixmap(self.pixmap.scaled(
                self.preview.contentsRect().size(), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.render_preview()

    def save_minute(self) -> None:
        capture = self.selected_capture()
        if capture is None:
            return

        def save() -> None:
            self.sessions.correct_minute(capture, self.minute.text())
            self.refresh_history(capture.id)
            self.statusBar().showMessage("Minute updated", 4000)

        self.attempt(save)

    def edit_substitution(self) -> None:
        capture = self.selected_capture()
        if capture is not None and capture.event == Event.SUBSTITUTION:
            self.attempt(lambda: self.prompt_substitution(capture))

    def prompt_substitution(self, capture: Capture) -> None:
        dialog = SubstitutionDialog(capture, self)
        self.classifying_substitution = True
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            reason = SubstitutionReason(dialog.reason.currentData())
            self.sessions.classify_substitution(capture, reason, dialog.notes.toPlainText())
            if self.match_picker.currentData() == capture.match_id:
                self.refresh_history(capture.id)
            self.statusBar().showMessage("Substitution reason saved", 4000)
        finally:
            self.classifying_substitution = False
            dialog.deleteLater()

    def delete_capture(self) -> None:
        capture = self.selected_capture()
        if capture is None:
            return
        answer = QMessageBox.question(self, "Delete capture", "Delete the selected screenshot?",
                                      defaultButton=QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return

        def delete() -> None:
            self.sessions.delete(capture)
            self.refresh_history()
            self.statusBar().showMessage("Capture deleted", 4000)

        self.attempt(delete)

    def edit_shortcuts(self) -> None:
        dialog = ShortcutDialog(self.settings, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        def save() -> None:
            self.config.save(dialog.settings)
            self.settings = dialog.settings
            self.apply_shortcuts()

        self.attempt(save)

    def apply_shortcuts(self) -> None:
        label = "On" if self.settings.enabled else "Off"
        try:
            self.hotkeys.apply(self.settings)
        except Exception:
            logger.exception("Could not start global shortcuts")
            label = "Unavailable"
            self.statusBar().showMessage("Global shortcuts could not start. See app.log.", 10000)
        self.shortcut_button.setText(f"Global shortcuts · {label}")
        for event, button in self.event_buttons.items():
            shortcut = f"  [{self.settings.shortcuts[event]}]" if label == "On" else ""
            button.setText(event.label + shortcut)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.hotkeys.stop()
        super().closeEvent(event)
