"""队列/日志面板（FR-G-2/6）：进度、剩余时间、可复制日志。"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class QueuePanel(QWidget):
    """底部队列面板。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setMinimum(0)
        self.progress.setValue(0)
        self.status = QLabel("待生成")
        self.eta = QLabel("")
        self.btn_copy = QPushButton("复制日志")
        self.btn_copy.clicked.connect(self._copy)
        self.btn_clear = QPushButton("清空")
        self.btn_clear.clicked.connect(lambda: self.log.clear())
        row.addWidget(self.progress, 2)
        row.addWidget(self.status, 1)
        row.addWidget(self.eta, 1)
        row.addWidget(self.btn_copy)
        row.addWidget(self.btn_clear)
        lay.addLayout(row)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log.setPlaceholderText("生成日志（错误信息可直接选中复制）……")
        lay.addWidget(self.log, 1)

    # -- 更新接口 ----------------------------------------------------------
    def set_total(self, total: int) -> None:
        """设置总条数。"""
        self.progress.setMaximum(max(1, total))
        self.progress.setValue(0)

    def set_progress(self, done: int, total: int, message: str = "", eta_text: str = "") -> None:
        """更新进度与剩余时间。"""
        self.progress.setMaximum(max(1, total))
        self.progress.setValue(done)
        if message:
            self.status.setText(message)
        self.eta.setText(eta_text)

    def append(self, text: str) -> None:
        """追加日志行。"""
        self.log.appendPlainText(text)

    def _copy(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(self.log.toPlainText())
        self.status.setText("日志已复制到剪贴板")
