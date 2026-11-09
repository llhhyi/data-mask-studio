"""Rule editing and the required per-file, per-column export confirmation."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel,
    QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from data_mask_studio.anonymization import ColumnAction
from data_mask_studio.batch.models import BatchFileStatus
from data_mask_studio.field_rules import FieldRule, FieldRuleSet, FieldRuleError, alias_key
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.gui.components.scroll_safe_combo_box import ScrollSafeComboBox
from data_mask_studio.gui.dialog_localization import localize_standard_buttons


class FieldRuleEditor(QDialog):
    def __init__(self, repository, profiles, parent, translator):
        super().__init__(parent)
        self.repository, self.translator = repository, translator
        self.initial = self.rules = repository.load()
        self.profiles = profiles
        self.setWindowTitle(translator.text("field_rules.editor.title"))
        self.resize(900, 620)
        layout = QVBoxLayout(self)
        note = QLabel(translator.text("field_rules.editor.help"))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([translator.text("field_rules.column." + key)
                                             for key in ("category", "prefix", "aliases", "normalization")])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        actions = QHBoxLayout()
        add = QPushButton(translator.text("field_rules.add"))
        remove = QPushButton(translator.text("field_rules.remove"))
        add.clicked.connect(lambda: self.add_rule())
        remove.clicked.connect(self.remove_selected)
        actions.addWidget(add)
        actions.addWidget(remove)
        self.profile_combo = ScrollSafeComboBox()
        for profile in profiles:
            self.profile_combo.addItem(profile.name, profile.identifier)
        import_button = QPushButton(translator.text("field_rules.import_profile"))
        import_button.setEnabled(bool(profiles))
        import_button.clicked.connect(self.import_profile)
        actions.addWidget(self.profile_combo, 1)
        actions.addWidget(import_button)
        layout.addLayout(actions)
        self.error = QLabel()
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText(translator.text("field_rules.save"))
        localize_standard_buttons(self.buttons, translator)
        self.buttons.accepted.connect(self.save_rules)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        for rule in self.rules.rules:
            self.add_rule(rule)

    def add_rule(self, rule=None):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setRowHeight(row, 95)
        self.table.setItem(row, 0, QTableWidgetItem(rule.name if rule else ""))
        self.table.setItem(row, 1, QTableWidgetItem(rule.prefix if rule else ""))
        aliases = QPlainTextEdit()
        aliases.setPlaceholderText(self.translator.text("field_rules.aliases_hint"))
        aliases.setPlainText("\n".join(rule.aliases) if rule else "")
        self.table.setCellWidget(row, 2, aliases)
        normalization = ScrollSafeComboBox()
        for value in NormalizationRule:
            normalization.addItem(self.translator.text("masking.normalization." + value.value), value.value)
        normalization.setCurrentIndex(normalization.findData((rule.normalization if rule else NormalizationRule.EXACT).value))
        self.table.setCellWidget(row, 3, normalization)

    def remove_selected(self):
        for row in sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(row)

    def read_rules(self):
        rules = []
        for row in range(self.table.rowCount()):
            name, prefix = (self.table.item(row, column).text().strip() for column in (0, 1))
            aliases = tuple(line.strip() for line in self.table.cellWidget(row, 2).toPlainText().splitlines() if line.strip())
            if not name and not prefix and not aliases:
                continue
            rules.append(FieldRule(name, prefix, aliases,
                                   NormalizationRule(self.table.cellWidget(row, 3).currentData())))
        return FieldRuleSet(tuple(rules))

    def import_profile(self):
        profile = next((item for item in self.profiles if item.identifier == self.profile_combo.currentData()), None)
        if profile is None:
            return
        try:
            rules = list(self.read_rules().rules)
            for column in profile.columns:
                if column.action is not ColumnAction.MASK:
                    continue
                alias = " ".join(column.header.split())
                existing = next((rule for rule in rules if rule.prefix == column.prefix), None)
                if existing:
                    if existing.normalization != column.normalization_rule:
                        raise FieldRuleError("field_rules.error.conflict")
                    if alias_key(alias) not in {alias_key(value) for value in existing.aliases}:
                        rules[rules.index(existing)] = FieldRule(existing.name, existing.prefix,
                                                               existing.aliases + (alias,), existing.normalization)
                else:
                    rules.append(FieldRule(alias, column.prefix, (alias,), column.normalization_rule))
            result = FieldRuleSet(tuple(rules))
        except FieldRuleError as error:
            self.error.setText(self.translator.text(error.message_id))
            return
        self.table.setRowCount(0)
        for rule in result.rules:
            self.add_rule(rule)
        self.error.clear()

    def save_rules(self):
        try:
            rules = self.read_rules()
            self.repository.save(rules, expected=self.initial)
        except FieldRuleError as error:
            self.error.setText(self.translator.text(error.message_id))
            return
        self.rules = rules
        self.accept()


class FieldRuleReviewDialog(QDialog):
    def __init__(self, files, parent, translator):
        super().__init__(parent)
        self.setWindowTitle(translator.text("field_rules.review.title"))
        self.resize(1080, 660)
        layout = QVBoxLayout(self)
        note = QLabel(translator.text("field_rules.review.help"))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([translator.text("field_rules.review." + key)
                                             for key in ("file", "index", "header", "action", "category", "prefix", "normalization")])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)
        valid = bool(files)
        for item in files:
            review = item.field_review
            if review is None or item.status is not BatchFileStatus.COMPATIBLE:
                valid = False
                error = translator.text(item.rule_error_id) if item.rule_error_id else item.result_message
                self.add_row([item.path.name, "—", ", ".join(item.headers), error, "—", "—", "—"], item.path)
                continue
            for column in review.columns:
                rule = column.rule
                self.add_row([item.path.name, str(column.index + 1), column.header,
                              translator.text("masking.action.mask" if rule else "masking.action.preserve"),
                              rule.name if rule else translator.text("field_rules.review.unmatched"),
                              rule.prefix if rule else "—",
                              translator.text("masking.normalization." + rule.normalization.value) if rule else "—"], item.path)
        self.confirmation = QCheckBox(translator.text("field_rules.review.acknowledge"))
        self.confirmation.setEnabled(valid)
        layout.addWidget(self.confirmation)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        localize_standard_buttons(self.buttons, translator)
        button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        button.setText(translator.text("field_rules.review.confirm"))
        button.setEnabled(False)
        self.confirmation.toggled.connect(button.setEnabled)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def add_row(self, values, path):
        row = self.table.rowCount()
        self.table.insertRow(row)
        for column, value in enumerate(values):
            cell = QTableWidgetItem(value)
            cell.setToolTip(str(path) if column == 0 else value)
            self.table.setItem(row, column, cell)
