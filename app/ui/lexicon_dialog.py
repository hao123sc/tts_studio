"""发音词典与读法规则对话框（FR-T-4/5/6）：词典可编辑、可导入；规则逐条开关。"""

from __future__ import annotations

import csv
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..core.lexicon import Lexicon


class LexiconDialog(QDialog):
    """词典编辑。返回结果通过 :meth:`result_lexicon` 获取。"""

    def __init__(self, lexicon: Lexicon, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("发音词典与读法规则")
        self.setMinimumSize(560, 420)
        self._lex = lexicon
        lay = QVBoxLayout(self)

        tip = QLabel("词典优先于自动规则：如「乐至」→「乐志」、「缫丝」→「骚丝」。长词优先匹配。")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["原词", "替换成（想让机器读出的写法）"])
        self.table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.table, 1)
        for src, dst in lexicon.entries.items():
            self._add_row(src, dst)

        row = QHBoxLayout()
        btn_add = QPushButton("＋ 加一行")
        btn_del = QPushButton("－ 删选中行")
        btn_imp = QPushButton("导入 CSV（原词,替换）")
        btn_add.clicked.connect(lambda: self._add_row("", ""))
        btn_del.clicked.connect(self._del_rows)
        btn_imp.clicked.connect(self._import_csv)
        for b in (btn_add, btn_del, btn_imp):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)

        lay.addWidget(QLabel("读法规则（可逐条关闭）："))
        opt = lexicon.options
        self.chk_standard = QCheckBox("标准号：GB/T 24067 → GB/T 二四〇六七（字母＋逐位数字）")
        self.chk_decimal = QCheckBox("小数：2.0 → 二点零")
        self.chk_int_unit = QCheckBox("整数＋单位：5000吨 → 五千吨")
        self.chk_abbrev = QCheckBox("全大写缩写按字母读：LCA → L C A（白名单 NASA 等按单词读）")
        self.chk_standard.setChecked(opt.standard)
        self.chk_decimal.setChecked(opt.decimal)
        self.chk_int_unit.setChecked(opt.int_unit)
        self.chk_abbrev.setChecked(opt.abbrev)
        for c in (self.chk_standard, self.chk_decimal, self.chk_int_unit, self.chk_abbrev):
            lay.addWidget(c)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _add_row(self, src: str, dst: str) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(src))
        self.table.setItem(r, 1, QTableWidgetItem(dst))

    def _del_rows(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)

    def _import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "导入词典 CSV", "", "CSV (*.csv)")
        if not path:
            return
        try:
            lex = Lexicon.import_csv(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "导入词典", f"导入失败：{exc}")
            return
        for src, dst in lex.entries.items():
            self._add_row(src, dst)

    def result_lexicon(self) -> Lexicon:
        """返回编辑后的词典。"""
        lex = Lexicon()
        for r in range(self.table.rowCount()):
            src = (self.table.item(r, 0).text() if self.table.item(r, 0) else "").strip()
            dst = (self.table.item(r, 1).text() if self.table.item(r, 1) else "").strip()
            if src:
                lex.entries[src] = dst
        lex.options.standard = self.chk_standard.isChecked()
        lex.options.decimal = self.chk_decimal.isChecked()
        lex.options.int_unit = self.chk_int_unit.isChecked()
        lex.options.abbrev = self.chk_abbrev.isChecked()
        lex.options.abbrev_whitelist = set(self._lex.options.abbrev_whitelist)
        return lex
