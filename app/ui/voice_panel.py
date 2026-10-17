"""音色列表面板（FR-V-1~5）：分组展示、搜索、收藏置顶、一键试听。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..engines.base import Voice

#: 头像色块颜色（按性别）
_GENDER_COLOR = {"male": "#4A90D9", "female": "#D96AA0", "neutral": "#888888"}


class VoicePanel(QWidget):
    """左侧音色面板。"""

    voice_selected = Signal(str)        # 双击/回车：设为当前音色
    preview_requested = Signal(str)     # 试听按钮
    favorite_toggled = Signal(str)      # 收藏切换

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(240)
        self._voices: list[Voice] = []
        self._favorites: set[str] = set()
        self._build()

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        title = QLabel("配音的人（音色）")
        title.setStyleSheet("font-size:14pt; font-weight:bold;")
        lay.addWidget(title)

        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索：中文名 / 英文ID / 标签")
        self.search.textChanged.connect(self._rebuild)
        self.chk_fav = QCheckBox("只看收藏")
        self.chk_fav.stateChanged.connect(self._rebuild)
        row.addWidget(self.search, 1)
        row.addWidget(self.chk_fav)
        lay.addLayout(row)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemDoubleClicked.connect(self._on_double)
        self.tree.itemSelectionChanged.connect(self._on_select)
        lay.addWidget(self.tree, 1)

        btns = QHBoxLayout()
        self.btn_preview = QPushButton("▶ 试听")
        self.btn_preview.clicked.connect(self._on_preview)
        self.btn_fav = QPushButton("★ 收藏/取消")
        self.btn_fav.clicked.connect(self._on_fav)
        self.btn_use = QPushButton("设为当前音色")
        self.btn_use.clicked.connect(self._on_use)
        btns.addWidget(self.btn_preview)
        btns.addWidget(self.btn_fav)
        btns.addWidget(self.btn_use)
        lay.addLayout(btns)
        self.hint = QLabel("双击音色 = 设为当前音色\n（P2 克隆音色将出现在本列表；仅可克隆本人/授权声音）")
        self.hint.setStyleSheet("color:#888; font-size:9pt;")
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)

    # -- 数据 --------------------------------------------------------------
    def set_voices(self, voices: list[Voice], favorites: set[str]) -> None:
        """设置音色列表与收藏集。"""
        self._voices = list(voices)
        self._favorites = set(favorites)
        self._rebuild()

    def current_voice_id(self) -> str:
        """当前选中的音色 id。"""
        items = self.tree.selectedItems()
        if items and items[0].data(0, Qt.UserRole):
            return str(items[0].data(0, Qt.UserRole))
        return ""

    def _rebuild(self) -> None:
        kw = self.search.text().strip().lower()
        only_fav = self.chk_fav.isChecked()
        self.tree.clear()
        groups: dict[tuple, list[Voice]] = {}
        for v in self._voices:
            if only_fav and v.id not in self._favorites:
                continue
            if kw and not (kw in v.name.lower() or kw in v.id.lower()
                           or any(kw in t.lower() for t in v.tags)):
                continue
            key = (v.engine, v.lang, v.gender or "neutral")
            groups.setdefault(key, []).append(v)
        for key in sorted(groups):
            engine, lang, gender = key
            label = f"{engine} · {lang} · { {'male': '男声', 'female': '女声'}.get(gender, gender) }"
            top = QTreeWidgetItem([label])
            top.setFlags(top.flags() & ~Qt.ItemIsSelectable)
            top.setForeground(0, QBrush(QColor("#555")))
            self.tree.addTopLevelItem(top)
            for v in sorted(groups[key], key=lambda x: (x.id not in self._favorites, x.name)):
                name = v.name + ("★" if v.id in self._favorites else "")
                if v.custom:
                    name += "（我的）"
                if v.tags:
                    name += f"  [{'/'.join(v.tags[:3])}]"
                item = QTreeWidgetItem([f"● {name}"])
                item.setData(0, Qt.UserRole, v.id)
                item.setForeground(0, QBrush(QColor(_GENDER_COLOR.get(v.gender, "#888"))))
                top.addChild(item)
            top.setExpanded(True)

    # -- 动作 --------------------------------------------------------------
    def _on_double(self, item: QTreeWidgetItem, _col: int) -> None:
        vid = item.data(0, Qt.UserRole)
        if vid:
            self.voice_selected.emit(str(vid))

    def _on_select(self) -> None:
        vid = self.current_voice_id()
        if vid:
            self.voice_selected.emit(vid)

    def _on_preview(self) -> None:
        vid = self.current_voice_id()
        if vid:
            self.preview_requested.emit(vid)

    def _on_fav(self) -> None:
        vid = self.current_voice_id()
        if vid:
            self.favorite_toggled.emit(vid)

    def _on_use(self) -> None:
        vid = self.current_voice_id()
        if vid:
            self.voice_selected.emit(vid)
