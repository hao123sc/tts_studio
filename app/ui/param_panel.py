"""参数面板（FR-P-1~13）：语速/音调/音量/风格/输出参数 + 预设 + 随音色记忆。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

#: 内置预设（FR-P-12）
BUILTIN_PRESETS: dict[str, dict] = {
    "旁白（正常偏慢）": {"rate": -0.05, "pitch": 0.0, "volume": 0.0, "normalize": "peak", "target_db": -3.0},
    "学生（轻快）": {"rate": 0.05, "pitch": 1.0, "volume": 0.0, "normalize": "peak", "target_db": -3.0},
    "新闻（平稳）": {"rate": 0.0, "pitch": -1.0, "volume": 0.0, "normalize": "peak", "target_db": -3.0},
}

STYLE_TIP = "情感/风格仅部分引擎支持（如 Azure）。当前引擎不支持时自动置灰，不影响生成。"


class ParamPanel(QWidget):
    """右侧参数面板。"""

    params_changed = Signal(dict)      # 参数变化（用于随音色记忆）
    preset_apply = Signal(str)         # 应用预设名
    template_changed = Signal(str)     # 命名模板变化

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(280)
        self._loading = False
        self._build()

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        title = QLabel("参数")
        title.setStyleSheet("font-size:14pt; font-weight:bold;")
        lay.addWidget(title)

        # 预设
        grp_preset = QGroupBox("预设（一键套用）")
        fl = QFormLayout(grp_preset)
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(list(BUILTIN_PRESETS))
        self.btn_preset_apply = QPushButton("套用")
        row = QHBoxLayout()
        row.addWidget(self.preset_combo, 1)
        row.addWidget(self.btn_preset_apply)
        fl.addRow(row)
        row2 = QHBoxLayout()
        self.btn_preset_save = QPushButton("另存")
        self.btn_preset_del = QPushButton("删除")
        self.btn_preset_import = QPushButton("导入")
        self.btn_preset_export = QPushButton("导出")
        for b in (self.btn_preset_save, self.btn_preset_del, self.btn_preset_import, self.btn_preset_export):
            row2.addWidget(b)
        fl.addRow(row2)
        lay.addWidget(grp_preset)

        # 语音参数
        grp_voice = QGroupBox("语音参数")
        form = QFormLayout(grp_voice)
        self.rate_slider, self.rate_spin = self._slider_pair(-50, 100, 0, " %")
        self.pitch_slider, self.pitch_spin = self._slider_pair(-12, 12, 0, " 半音")
        self.volume_slider, self.volume_spin = self._slider_pair(-12, 12, 0, " dB")
        form.addRow("语速", self._pair_row(self.rate_slider, self.rate_spin))
        form.addRow("音调", self._pair_row(self.pitch_slider, self.pitch_spin))
        form.addRow("音量", self._pair_row(self.volume_slider, self.volume_spin))
        self.style_combo = QComboBox()
        self.style_combo.addItems(["（无）", "新闻", "友好", "愉快", "生气", "悲伤", "严肃"])
        self.style_degree = QDoubleSpinBox()
        self.style_degree.setRange(0.1, 2.0)
        self.style_degree.setSingleStep(0.1)
        self.style_degree.setValue(1.0)
        self.style_label = QLabel("风格")
        self.style_degree_label = QLabel("风格强度")
        form.addRow(self.style_label, self.style_combo)
        form.addRow(self.style_degree_label, self.style_degree)
        self.style_note = QLabel(STYLE_TIP)
        self.style_note.setWordWrap(True)
        self.style_note.setStyleSheet("color:#a60; font-size:9pt;")
        form.addRow(self.style_note)
        lay.addWidget(grp_voice)

        # 输出参数
        grp_out = QGroupBox("输出参数")
        fo = QFormLayout(grp_out)
        self.sample_rate = QComboBox()
        self.sample_rate.addItems(["22050", "24000", "44100", "48000"])
        self.sample_rate.setCurrentText("44100")
        self.bitrate = QComboBox()
        self.bitrate.addItems(["64", "96", "128", "160", "192"])
        self.bitrate.setCurrentText("128")
        self.channels = QComboBox()
        self.channels.addItems(["单声道", "立体声"])
        self.normalize = QComboBox()
        self.normalize.addItems(["峰值归一", "LUFS -16", "关闭"])
        self.target_db = QDoubleSpinBox()
        self.target_db.setRange(-12.0, 0.0)
        self.target_db.setSingleStep(0.5)
        self.target_db.setValue(-3.0)
        self.target_db.setSuffix(" dBFS")
        self.out_format = QComboBox()
        self.out_format.addItems(["mp3", "wav", "flac"])
        fo.addRow("采样率", self.sample_rate)
        fo.addRow("码率（MP3）", self.bitrate)
        fo.addRow("声道", self.channels)
        fo.addRow("响度归一", self.normalize)
        fo.addRow("峰值目标", self.target_db)
        fo.addRow("输出格式", self.out_format)
        self.name_template = QLineEdit("{i:02d}.mp3")
        fo.addRow("命名模板", self.name_template)
        self.name_preview = QLabel("预览：01.mp3 … 41.mp3")
        self.name_preview.setStyleSheet("color:#666; font-size:9pt;")
        fo.addRow(self.name_preview)
        fo.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)
        for w in (self.sample_rate, self.bitrate, self.channels, self.normalize,
                  self.out_format, self.target_db, self.style_degree):
            w.setMaximumWidth(150)
        lay.addWidget(grp_out)

        # 随音色记忆 + 输出目录
        self.chk_remember = QCheckBox("参数随音色记忆（换回音色自动带出）")
        self.chk_remember.setChecked(True)
        lay.addWidget(self.chk_remember)
        row3 = QHBoxLayout()
        self.out_dir = QLineEdit()
        self.out_dir.setPlaceholderText("输出目录（默认项目下 out/）")
        self.btn_out_dir = QPushButton("…")
        self.btn_out_dir.setFixedWidth(32)
        row3.addWidget(self.out_dir, 1)
        row3.addWidget(self.btn_out_dir)
        lay.addLayout(row3)
        lay.addStretch(1)

        # 信号
        for w in (self.rate_spin, self.pitch_spin, self.volume_spin, self.style_degree,
                  self.target_db):
            w.valueChanged.connect(self._emit_changed)
        for w in (self.sample_rate, self.bitrate, self.channels, self.normalize, self.out_format,
                  self.style_combo):
            w.currentIndexChanged.connect(self._emit_changed)
        self.rate_slider.valueChanged.connect(self.rate_spin.setValue)
        self.pitch_slider.valueChanged.connect(self.pitch_spin.setValue)
        self.volume_slider.valueChanged.connect(self.volume_spin.setValue)
        self.rate_spin.valueChanged.connect(self.rate_slider.setValue)
        self.pitch_spin.valueChanged.connect(self.pitch_slider.setValue)
        self.volume_spin.valueChanged.connect(self.volume_slider.setValue)
        self.name_template.textChanged.connect(self._on_template)
        self.btn_preset_apply.clicked.connect(lambda: self.preset_apply.emit(self.preset_combo.currentText()))

    def _slider_pair(self, lo: int, hi: int, val: int, suffix: str):
        s = QSlider(Qt.Horizontal)
        s.setRange(lo, hi)
        s.setValue(val)
        b = QSpinBox()
        b.setRange(lo, hi)
        b.setValue(val)
        b.setSuffix(suffix)
        return s, b

    @staticmethod
    def _pair_row(slider, spin):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(slider, 1)
        h.addWidget(spin)
        return w

    # -- 读写参数 ----------------------------------------------------------
    def get_params(self) -> dict:
        """收集当前面板参数。"""
        norm_map = {"峰值归一": "peak", "LUFS -16": "lufs", "关闭": "none"}
        return {
            "rate": self.rate_spin.value() / 100.0,
            "pitch": float(self.pitch_spin.value()),
            "volume": float(self.volume_spin.value()),
            "style": "" if self.style_combo.currentIndex() == 0 else self.style_combo.currentText(),
            "style_degree": self.style_degree.value(),
            "sample_rate": int(self.sample_rate.currentText()),
            "bitrate": int(self.bitrate.currentText()),
            "channels": 1 if self.channels.currentIndex() == 0 else 2,
            "normalize": norm_map[self.normalize.currentText()],
            "target_db": self.target_db.value(),
            "out_format": self.out_format.currentText(),
            "name_template": self.name_template.text().strip() or "{i:02d}.mp3",
        }

    def set_params(self, params: dict) -> None:
        """回填面板参数（随音色记忆/项目加载）。"""
        self._loading = True
        try:
            if "rate" in params:
                self.rate_spin.setValue(int(round(float(params["rate"]) * 100)))
            if "pitch" in params:
                self.pitch_spin.setValue(int(round(float(params["pitch"]))))
            if "volume" in params:
                self.volume_spin.setValue(int(round(float(params["volume"]))))
            if params.get("style"):
                idx = self.style_combo.findText(str(params["style"]))
                self.style_combo.setCurrentIndex(max(0, idx))
            if "style_degree" in params:
                self.style_degree.setValue(float(params["style_degree"]))
            if "sample_rate" in params:
                self.sample_rate.setCurrentText(str(params["sample_rate"]))
            if "bitrate" in params:
                self.bitrate.setCurrentText(str(params["bitrate"]))
            if "channels" in params:
                self.channels.setCurrentIndex(0 if int(params["channels"]) == 1 else 1)
            if "normalize" in params:
                inv = {"peak": "峰值归一", "lufs": "LUFS -16", "none": "关闭"}
                self.normalize.setCurrentText(inv.get(str(params["normalize"]), "峰值归一"))
            if "target_db" in params:
                self.target_db.setValue(float(params["target_db"]))
            if "out_format" in params:
                self.out_format.setCurrentText(str(params["out_format"]))
            if "name_template" in params:
                self.name_template.setText(str(params["name_template"]))
        finally:
            self._loading = False
        self._on_template()

    def apply_preset(self, name: str, user_presets: dict[str, dict]) -> None:
        """套用内置/用户预设。"""
        data = BUILTIN_PRESETS.get(name) or user_presets.get(name) or {}
        self.set_params(data)

    def set_style_supported(self, supported: bool, note: str = "") -> None:
        """按引擎能力置灰风格参数（FR-P-4 不支持时置灰并说明）。"""
        self.style_combo.setEnabled(supported)
        self.style_degree.setEnabled(supported)
        self.style_label.setEnabled(supported)
        self.style_degree_label.setEnabled(supported)
        self.style_note.setText(note or (STYLE_TIP if not supported else "当前引擎支持风格参数。"))

    def set_pitch_volume_supported(self, pitch_ok: bool, volume_ok: bool, note: str = "") -> None:
        """SAPI 等引擎不支持音调/音量时置灰。"""
        self.pitch_slider.setEnabled(pitch_ok)
        self.pitch_spin.setEnabled(pitch_ok)
        self.volume_slider.setEnabled(volume_ok)
        self.volume_spin.setEnabled(volume_ok)
        if note:
            self.style_note.setText(note)

    def _emit_changed(self, *_a) -> None:
        if not self._loading:
            self.params_changed.emit(self.get_params())

    def _on_template(self, *_a) -> None:
        from ..core.naming import preview_names

        tpl = self.name_template.text().strip() or "{i:02d}.mp3"
        try:
            names = preview_names(tpl, 3, self.out_format.currentText())
            total = 41
            last = preview_names(tpl, total, self.out_format.currentText())[-1]
            self.name_preview.setText(f"预览：{names[0]} … {last}")
        except ValueError as exc:
            self.name_preview.setText(f"模板错误：{exc}")
        if not self._loading:
            self.template_changed.emit(tpl)
