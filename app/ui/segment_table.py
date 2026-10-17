"""多段文本编辑区（FR-T-1/3/7/8）：段表 + 文本编辑（停顿标记高亮）+ 校验提示。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QTextDocument
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.project import Segment
from ..core.textproc import count_chars, estimate_duration, validate_text
from ..core.textproc import PAUSE_RE


class PauseHighlighter(QSyntaxHighlighter):
    """停顿标记高亮（FR-T-3 界面高亮显示）。"""

    def __init__(self, doc: QTextDocument) -> None:
        super().__init__(doc)
        self._fmt = QTextCharFormat()
        self._fmt.setBackground(QColor("#FFF3B0"))
        self._fmt.setForeground(QColor("#B58900"))

    def highlightBlock(self, text: str) -> None:  # noqa: D102
        for m in PAUSE_RE.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), self._fmt)


class SegmentTable(QWidget):
    """中间：段表格 + 段文本编辑。"""

    selection_changed = Signal(object)     # 当前 Segment 或 None
    content_edited = Signal()              # 文本/备注被编辑（刷新统计）
    open_file_requested = Signal(int)      # 双击"状态"列：系统播放器打开
    voice_assign_requested = Signal(list)  # 把当前段音色应用到选中段（发段号列表）

    COLS = ["选", "序号", "备注", "音色", "文本", "字数", "预估", "状态"]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._segments: list[Segment] = []
        self._voice_names: dict[str, str] = {}
        self._current: Segment | None = None
        self._loading = False
        self._build()

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        bar = QHBoxLayout()
        self.btn_add = QPushButton("＋ 新增段")
        self.btn_del = QPushButton("－ 删除选中")
        self.btn_up = QPushButton("↑ 上移")
        self.btn_down = QPushButton("↓ 下移")
        self.btn_split = QPushButton("✂ 拆句")
        self.btn_check = QPushButton("✓ 校验文本")
        for b in (self.btn_add, self.btn_del, self.btn_up, self.btn_down, self.btn_split, self.btn_check):
            bar.addWidget(b)
        bar.addStretch(1)
        lay.addLayout(bar)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.itemSelectionChanged.connect(self._on_sel)
        self.table.cellDoubleClicked.connect(self._on_double)
        lay.addWidget(self.table, 3)

        tip = QLabel("双击「状态」列用系统播放器打开该段文件 ｜ 文本里的 [pause=1.5] 或 [[停顿1.5s]] 会高亮并在生成时精确停顿")
        tip.setStyleSheet("color:#888; font-size:9pt;")
        lay.addWidget(tip)

        self.edit_label = QLabel("段文本：")
        lay.addWidget(self.edit_label)
        self.editor = QWidget()
        edit_lay = QVBoxLayout(self.editor)
        edit_lay.setContentsMargins(0, 0, 0, 0)
        from PySide6.QtWidgets import QPlainTextEdit

        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText("在这里贴台词……支持多段，[pause=1.5] 表示停顿 1.5 秒")
        font = QFont()
        font.setPointSize(12)
        self.text_edit.setFont(font)
        self.highlighter = PauseHighlighter(self.text_edit.document())
        self.text_edit.textChanged.connect(self._on_text_changed)
        edit_lay.addWidget(self.text_edit, 3)
        lay.addWidget(self.editor, 2)

        self.stats = QLabel("字数 0 ｜ 预估 0:00")
        self.stats.setStyleSheet("font-size:11pt;")
        lay.addWidget(self.stats)

    # -- 数据装载 ----------------------------------------------------------
    def set_segments(self, segments: list[Segment], voice_names: dict[str, str]) -> None:
        """刷新表格数据。"""
        self._segments = list(segments)
        self._voice_names = voice_names
        self._reload_table()

    def _reload_table(self) -> None:
        self._loading = True
        self.table.setRowCount(len(self._segments))
        for row, seg in enumerate(self._segments):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Checked)
            self.table.setItem(row, 0, chk)
            self.table.setItem(row, 1, QTableWidgetItem(str(seg.i)))
            self.table.setItem(row, 2, QTableWidgetItem(seg.display_name()))
            self.table.setItem(row, 3, QTableWidgetItem(self._voice_names.get(seg.voice, seg.voice or "（默认）")))
            preview = seg.text.replace("\n", " ")
            self.table.setItem(row, 4, QTableWidgetItem(preview[:40] + ("…" if len(preview) > 40 else "")))
            self.table.setItem(row, 5, QTableWidgetItem(str(count_chars(seg.text))))
            self.table.setItem(row, 6, QTableWidgetItem(f"{estimate_duration(seg.text):.1f}s"))
            self.table.setItem(row, 7, QTableWidgetItem(self._status_text(seg)))
        self._loading = False

    @staticmethod
    def _status_text(seg: Segment) -> str:
        return {"ok": "✓ 已生成", "failed": "✗ 失败", "skipped": "跳过"}.get(seg.status, "待生成")

    def refresh_status(self, index: int) -> None:
        """刷新某段的状态显示（生成后回填）。"""
        for row, seg in enumerate(self._segments):
            if seg.i == index:
                self.table.setItem(row, 7, QTableWidgetItem(self._status_text(seg)))
                self.table.setItem(row, 6, QTableWidgetItem(f"{seg.duration:.1f}s" if seg.duration else
                                                            f"{estimate_duration(seg.text):.1f}s"))
                break

    def selected_indexes(self) -> list[int]:
        """勾选列选中的段序号集合。"""
        out = []
        for row in range(self.table.rowCount()):
            chk = self.table.item(row, 0)
            if chk and chk.checkState() == Qt.Checked:
                out.append(self._segments[row].i)
        return out

    def current_segment(self) -> Segment | None:
        """当前高亮的段。"""
        return self._current

    # -- 交互 --------------------------------------------------------------
    def _on_sel(self) -> None:
        if self._loading:
            return
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return
        row = rows[0].row()
        if 0 <= row < len(self._segments):
            self._current = self._segments[row]
            self._loading = True
            self.text_edit.setPlainText(self._segments[row].text)
            self._loading = False
            self.edit_label.setText(f"段文本（第 {self._segments[row].i} 段）：")
            self._update_stats()
            self.selection_changed.emit(self._current)

    def _on_double(self, row: int, col: int) -> None:
        if col == 7 and 0 <= row < len(self._segments):  # 状态列
            self.open_file_requested.emit(self._segments[row].i)

    def _on_text_changed(self) -> None:
        if self._loading or not self._current:
            return
        self._current.text = self.text_edit.toPlainText()
        self._update_stats()
        # 行内字数/预估同步
        for row, seg in enumerate(self._segments):
            if seg is self._current:
                self.table.setItem(row, 5, QTableWidgetItem(str(count_chars(seg.text))))
                self.table.setItem(row, 6, QTableWidgetItem(f"{estimate_duration(seg.text):.1f}s"))
                preview = seg.text.replace("\n", " ")
                self.table.setItem(row, 4, QTableWidgetItem(preview[:40] + ("…" if len(preview) > 40 else "")))
                break
        self.content_edited.emit()

    def _update_stats(self) -> None:
        if not self._current:
            self.stats.setText("字数 0 ｜ 预估 0:00")
            return
        n = count_chars(self._current.text)
        est = estimate_duration(self._current.text)
        warns = validate_text(self._current.text)
        warn_txt = f" ｜ ⚠ {'；'.join(warns)}" if warns else ""
        self.stats.setText(f"字数 {n} ｜ 预估 {int(est // 60)}:{int(est % 60):02d}{warn_txt}")

    def mark_checked(self) -> None:
        """「校验文本」按钮反馈。"""
        self._update_stats()
