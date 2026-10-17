"""试听播放器（FR-A-1）：播放/暂停、进度、时长；双击/按钮在系统播放器打开（FR-A-4）。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QWidget,
)

try:  # QtMultimedia 在精简环境可能缺失 → 播放按钮置灰
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

    _HAS_MULTIMEDIA = True
except Exception:  # noqa: BLE001
    _HAS_MULTIMEDIA = False


def _fmt_ms(ms: int) -> str:
    """毫秒 → m:ss。"""
    s = max(0, int(ms // 1000))
    return f"{s // 60}:{s % 60:02d}"


class PlayerBar(QWidget):
    """底部播放器条。"""

    open_system_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.current_path = ""
        self._player = None
        self._audio = None
        self._building_ui()
        if _HAS_MULTIMEDIA:
            self._player = QMediaPlayer(self)
            self._audio = QAudioOutput(self)
            self._player.setAudioOutput(self._audio)
            self._player.positionChanged.connect(self._on_pos)
            self._player.durationChanged.connect(self._on_dur)
            self._player.playbackStateChanged.connect(self._on_state)
        else:
            self.btn_play.setEnabled(False)
            self.btn_play.setToolTip("当前环境缺少 QtMultimedia 播放组件；可双击段落用系统播放器试听")

    def _building_ui(self) -> None:
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        self.btn_play = QPushButton("▶ 播放")
        self.btn_play.setFixedWidth(96)
        self.btn_play.clicked.connect(self.toggle)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.sliderMoved.connect(self._on_seek)
        self.label_time = QLabel("0:00 / 0:00")
        self.label_file = QLabel("（未加载音频）")
        self.label_file.setStyleSheet("color:#666;")
        self.btn_system = QPushButton("用系统播放器打开")
        self.btn_system.clicked.connect(self._open_system)
        self.btn_folder = QPushButton("打开文件夹")
        self.btn_folder.clicked.connect(self._open_folder)
        lay.addWidget(self.btn_play)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.label_time)
        lay.addWidget(self.label_file, 1)
        lay.addWidget(self.btn_folder)
        lay.addWidget(self.btn_system)

    # -- 公共接口 ----------------------------------------------------------
    def load(self, path: str, autoplay: bool = False) -> None:
        """加载并（可选）播放音频文件。"""
        self.current_path = path or ""
        self.label_file.setText(Path(path).name if path else "（未加载音频）")
        if not path or not Path(path).exists():
            return
        if self._player is not None:
            self._player.setSource(QUrl.fromLocalFile(str(Path(path).resolve())))
            self._audio.setVolume(1.0)
            if autoplay:
                self._player.play()

    def play(self) -> None:
        """播放。"""
        if self._player and self.current_path:
            self._player.play()

    def pause(self) -> None:
        """暂停。"""
        if self._player:
            self._player.pause()

    def toggle(self) -> None:
        """播放/暂停切换。"""
        if not self._player or not self.current_path:
            return
        if self._player.playbackState() == QMediaPlayer.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    # -- 内部槽 ------------------------------------------------------------
    def _on_pos(self, pos: int) -> None:
        if not self.slider.isSliderDown():
            self.slider.setValue(pos)
        dur = self._player.duration() if self._player else 0
        self.label_time.setText(f"{_fmt_ms(pos)} / {_fmt_ms(dur)}")

    def _on_dur(self, dur: int) -> None:
        self.slider.setRange(0, int(dur))

    def _on_state(self, state) -> None:
        playing = state == QMediaPlayer.PlayingState if _HAS_MULTIMEDIA else False
        self.btn_play.setText("⏸ 暂停" if playing else "▶ 播放")

    def _on_seek(self, pos: int) -> None:
        if self._player:
            self._player.setPosition(pos)

    def _open_system(self) -> None:
        if self.current_path:
            self.open_system_requested.emit(self.current_path)

    def _open_folder(self) -> None:
        if self.current_path:
            folder = Path(self.current_path).parent
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
