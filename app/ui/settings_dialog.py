"""设置对话框（FR-S-3）：缓存目录、代理、API Key（仅存本地）、日志级别。"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QVBoxLayout,
)

from ..core.config import AppConfig, save_config


class SettingsDialog(QDialog):
    """设置。"""

    def __init__(self, cfg: AppConfig, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(480)
        self.cfg = cfg
        lay = QVBoxLayout(self)
        form = QFormLayout()

        self.cache_dir = QLineEdit(cfg.cache_dir)
        self.cache_dir.setPlaceholderText("留空 = 使用系统用户目录下缓存")
        form.addRow("缓存目录", self.cache_dir)

        self.proxy = QLineEdit(cfg.proxy)
        self.proxy.setPlaceholderText("如 http://127.0.0.1:7890（Edge-TTS 在部分网络下需要）")
        form.addRow("网络代理", self.proxy)

        self.fallback = QCheckBox("网络失败时自动降级为系统 SAPI 离线音色")
        self.fallback.setChecked(cfg.fallback_to_sapi)
        form.addRow(self.fallback)

        self.remember = QCheckBox("参数随音色记忆")
        self.remember.setChecked(cfg.remember_voice_params)
        form.addRow(self.remember)

        self.concurrency = QComboBox()
        self.concurrency.addItems(["1", "2"])
        self.concurrency.setCurrentText(str(cfg.concurrency))
        form.addRow("生成并发", self.concurrency)

        self.log_level = QComboBox()
        self.log_level.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        self.log_level.setCurrentText(cfg.log_level)
        form.addRow("日志级别", self.log_level)

        self.key_azure = QLineEdit(cfg.api_keys.get("azure", ""))
        self.key_azure.setEchoMode(QLineEdit.Password)
        self.key_iflytek = QLineEdit(cfg.api_keys.get("iflytek", ""))
        self.key_iflytek.setEchoMode(QLineEdit.Password)
        self.key_tencent = QLineEdit(cfg.api_keys.get("tencent", ""))
        self.key_tencent.setEchoMode(QLineEdit.Password)
        form.addRow("Azure Key（预留）", self.key_azure)
        form.addRow("讯飞 Key（预留）", self.key_iflytek)
        form.addRow("腾讯 Key（预留）", self.key_tencent)

        lay.addLayout(form)
        compliance = QLabel(
            "合规提示：本软件生成的音频为 AI 合成语音，请勿用于冒充他人；"
            "下期声音克隆功能仅可用于克隆本人或已获授权的声音。API Key 只保存在本机配置文件中。"
        )
        compliance.setWordWrap(True)
        compliance.setStyleSheet("color:#a60; font-size:9pt;")
        lay.addWidget(compliance)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _save(self) -> None:
        self.cfg.cache_dir = self.cache_dir.text().strip()
        self.cfg.proxy = self.proxy.text().strip()
        self.cfg.fallback_to_sapi = self.fallback.isChecked()
        self.cfg.remember_voice_params = self.remember.isChecked()
        self.cfg.concurrency = int(self.concurrency.currentText())
        self.cfg.log_level = self.log_level.currentText()
        self.cfg.api_keys["azure"] = self.key_azure.text().strip()
        self.cfg.api_keys["iflytek"] = self.key_iflytek.text().strip()
        self.cfg.api_keys["tencent"] = self.key_tencent.text().strip()
        save_config(self.cfg)
        self.accept()
