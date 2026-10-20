"""设置对话框（FR-S-3）：缓存目录、代理（403/超时必设，保存后立即生效）、API Key（仅存本地）。"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ..core.config import AppConfig, save_config, validate_proxy
from .workers import ProxyTestWorker


class SettingsDialog(QDialog):
    """设置。"""

    def __init__(self, cfg: AppConfig, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(520)
        self.cfg = cfg
        self._test_worker: ProxyTestWorker | None = None
        lay = QVBoxLayout(self)
        form = QFormLayout()

        self.cache_dir = QLineEdit(cfg.cache_dir)
        self.cache_dir.setPlaceholderText("留空 = 使用系统用户目录下缓存")
        form.addRow("缓存目录", self.cache_dir)

        # ---- 网络代理（Edge-TTS 403/超时时在此配置）----
        proxy_box = QVBoxLayout()
        proxy_row = QHBoxLayout()
        self.proxy = QLineEdit(cfg.proxy)
        self.proxy.setPlaceholderText("http://127.0.0.1:7890（也支持 http://用户名:密码@主机:端口）")
        self.btn_test = QPushButton("测试连接")
        self.btn_test.setToolTip("用当前填写的代理拉取一次 Edge-TTS 音色清单，验证是否可用")
        self.btn_test.clicked.connect(self._test_proxy)
        proxy_row.addWidget(self.proxy, 1)
        proxy_row.addWidget(self.btn_test)
        proxy_box.addLayout(proxy_row)
        self.proxy_hint = QLabel(
            "Edge-TTS 报 403 / 超时 / 连不上时，在这里填代理地址，保存后立即生效，无需重启；\n"
            "留空 = 直连。仅支持 http:// 或 https:// 代理（如 Clash/V2Ray 的 http 入站端口）。"
        )
        self.proxy_hint.setWordWrap(True)
        self.proxy_hint.setStyleSheet("color:#666; font-size:9pt;")
        proxy_box.addWidget(self.proxy_hint)
        self.proxy_result = QLabel("")
        self.proxy_result.setWordWrap(True)
        self.proxy_result.setStyleSheet("font-size:10pt;")
        proxy_box.addWidget(self.proxy_result)
        form.addRow("网络代理\n(Edge-TTS)", proxy_box)

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
        buttons.button(QDialogButtonBox.Ok).setText("保存并立即生效")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _current_proxy(self) -> str:
        return self.proxy.text().strip()

    def _test_proxy(self) -> None:
        """测试代理/网络连通性（后台执行，不卡界面）。"""
        proxy, err = validate_proxy(self._current_proxy())
        if err:
            self.proxy_result.setStyleSheet("color:#b00; font-size:10pt;")
            self.proxy_result.setText(f"✗ {err}")
            return
        self.btn_test.setEnabled(False)
        self.proxy_result.setStyleSheet("color:#666; font-size:10pt;")
        self.proxy_result.setText("正在连接 Edge-TTS 测试……（最多约 15 秒）")
        self._test_worker = ProxyTestWorker(proxy)
        self._test_worker.done.connect(self._on_test_done)
        self._test_worker.start()

    def _on_test_done(self, ok: bool, message: str) -> None:
        self.btn_test.setEnabled(True)
        self.proxy_result.setStyleSheet(
            f"color:{'#070' if ok else '#b00'}; font-size:10pt;"
        )
        self.proxy_result.setText(("✓ " if ok else "✗ ") + message)

    def _save(self) -> None:
        proxy, err = validate_proxy(self.proxy.text())
        if err:
            QMessageBox.warning(self, "网络代理", err)
            return
        self.cfg.cache_dir = self.cache_dir.text().strip()
        self.cfg.proxy = proxy
        self.cfg.fallback_to_sapi = self.fallback.isChecked()
        self.cfg.remember_voice_params = self.remember.isChecked()
        self.cfg.concurrency = int(self.concurrency.currentText())
        self.cfg.log_level = self.log_level.currentText()
        self.cfg.api_keys["azure"] = self.key_azure.text().strip()
        self.cfg.api_keys["iflytek"] = self.key_iflytek.text().strip()
        self.cfg.api_keys["tencent"] = self.key_tencent.text().strip()
        save_config(self.cfg)
        self.accept()
