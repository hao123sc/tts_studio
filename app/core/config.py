"""应用配置（FR-S-3）。配置与缓存存用户目录；API Key 只落本地配置文件，绝不写进代码。"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_NAME = "TTS Studio 配音工作站"
APP_VERSION = "1.0.0"
CONFIG_FILE = "config.json"


def user_data_dir() -> Path:
    """用户数据目录：Windows %APPDATA%/TTSStudio，其他 ~/.tts_studio。"""
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        d = base / "TTSStudio"
    else:
        d = Path.home() / ".tts_studio"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class AppConfig:
    """应用配置（JSON 持久化到用户目录）。"""

    cache_dir: str = ""                 # 试听缓存目录；空 = 用户目录下 cache
    proxy: str = ""                     # Edge-TTS 代理，如 http://127.0.0.1:7890
    log_level: str = "INFO"
    fallback_to_sapi: bool = True       # 网络失败自动降级 SAPI
    retry_times: int = 2                # 失败自动重试次数（FR-G-4）
    concurrency: int = 1                # 队列并发（默认 1，可调 2）
    autosave_sec: int = 30              # 自动保存间隔
    remember_voice_params: bool = True  # 参数随音色记忆（FR-P-13）
    last_project: str = ""
    # 各云引擎 API Key（仅存本地配置文件，不入库、不进代码）
    api_keys: dict = field(default_factory=dict)

    def cache_path(self) -> Path:
        """试听/临时缓存目录。"""
        if self.cache_dir:
            d = Path(self.cache_dir)
        else:
            d = user_data_dir() / "cache"
        (d / "preview").mkdir(parents=True, exist_ok=True)
        return d

    def logs_dir(self) -> Path:
        """日志目录（按天滚动）。"""
        d = user_data_dir() / "logs"
        d.mkdir(parents=True, exist_ok=True)
        return d


def load_config() -> AppConfig:
    """读取配置；损坏或缺失时返回默认配置。"""
    p = user_data_dir() / CONFIG_FILE
    if not p.exists():
        return AppConfig()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AppConfig()
    cfg = AppConfig()
    for key, val in data.items():
        if hasattr(cfg, key):
            setattr(cfg, key, val)
    return cfg


def save_config(cfg: AppConfig) -> None:
    """保存配置到用户目录。"""
    p = user_data_dir() / CONFIG_FILE
    p.write_text(json.dumps(asdict(cfg), ensure_ascii=False, indent=2), encoding="utf-8")


def validate_proxy(value: str) -> tuple[str, str]:
    """校验/清洗代理地址（FR-S-3，Edge-TTS 403/超时时用）。

    :returns: ``(清洗后的代理串, 错误提示)``；合法时错误提示为空字符串。
      空值合法（表示直连）。
    """
    v = (value or "").strip()
    if not v:
        return "", ""
    if "://" not in v:
        v = "http://" + v
    scheme, _, rest = v.partition("://")
    scheme = scheme.lower()
    if scheme not in ("http", "https"):
        return "", f"代理协议「{scheme}」暂不支持；请用 http:// 或 https:// 开头（如 http://127.0.0.1:7890）"
    if not rest or " " in v:
        return "", "代理地址不完整；正确格式如 http://127.0.0.1:7890 或 http://用户名:密码@主机:端口"
    hostport = rest.rsplit("@", 1)[-1]
    host, _, port = hostport.partition(":")
    if not host:
        return "", "代理地址缺少主机名"
    if port and not port.isdigit():
        return "", f"代理端口「{port}」不是数字"
    return v, ""
