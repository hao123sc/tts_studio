"""引擎统一接口（需求文档 §3.1）。

本模块中的 ``Voice`` / ``TTSRequest`` / ``SynthResult`` / ``Engine`` 签名是全项目
的硬约束：所有引擎适配器（edge / sapi / azure / iflytek / tencent / P2 克隆引擎）
都必须实现该接口，UI 与核心服务层只依赖本模块，不得另起一套。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Protocol, runtime_checkable

# 试听固定测试句（FR-V-3：所有音色共用同一句话，便于横向对比）
PREVIEW_TEXT = "同学们好，欢迎来到信息技术微课。这是一段试听文本。"

# 语速/音量/音调的合法范围（与 FR-P-1/2/3 对应）
RATE_MIN, RATE_MAX = -0.5, 1.0        # 语速：−50% ~ +100%
PITCH_MIN, PITCH_MAX = -12.0, 12.0    # 音调：半音 ±12
VOLUME_MIN, VOLUME_MAX = -12.0, 12.0  # 音量：dB ±12（引擎前增益）


@dataclass
class Voice:
    """一个可用音色。"""

    id: str                  # 全局唯一，如 "edge:zh-CN-YunxiNeural" / "clone:teacher01"
    name: str                # 显示名："云希（少年男声）"
    engine: str              # edge / sapi / azure / gpt_sovits ...
    lang: str = "zh-CN"
    gender: str = ""         # male / female / neutral
    tags: list[str] = field(default_factory=list)   # 旁白/少年/新闻/方言
    sample_path: str = ""    # 试听样音（首次生成后缓存）
    custom: bool = False     # 是否用户克隆音色


@dataclass
class TTSRequest:
    """一次合成请求（引擎输入）。"""

    text: str
    voice_id: str
    rate: float = 0.0        # 0 = 原速；+0.2 = 快 20%
    pitch: float = 0.0       # 半音，±12
    volume: float = 0.0      # dB，±12
    style: str = ""          # 情感/风格（仅支持的引擎）
    style_degree: float = 1.0
    pauses: list[tuple[int, float]] = field(default_factory=list)  # (字符位置, 秒)
    out_format: Literal["mp3", "wav", "flac"] = "mp3"
    sample_rate: int = 44100
    bitrate: int = 128
    normalize: Literal["peak", "lufs", "none"] = "peak"
    target_db: float = -3.0
    lexicon: dict[str, str] = field(default_factory=dict)  # 发音词典覆盖


@dataclass
class SynthResult:
    """一次合成的结果描述。"""

    path: str
    duration: float
    sample_rate: int
    warnings: list[str] = field(default_factory=list)


@runtime_checkable
class Engine(Protocol):
    """引擎适配器协议（插件式扩展：新引擎 = 新增 engines/xxx.py）。"""

    name: str

    def list_voices(self) -> list[Voice]:
        """列出该引擎当前可用音色。"""
        ...

    def capabilities(self) -> dict:
        """返回能力矩阵条目，UI 据此置灰（需求文档 §6）。

        约定键：``rate`` / ``pitch`` / ``volume`` / ``style`` / ``style_degree`` /
        ``pauses`` / ``offline`` / ``formats`` / ``max_chars`` / ``rate_range``。
        """
        ...

    def synth(self, req: TTSRequest, out_path: str, on_progress: Callable[..., None] | None = None) -> SynthResult:
        """合成一段音频写入 ``out_path``（引擎原始格式，如 MP3/WAV）。"""
        ...

    def preview(self, voice_id: str, out_path: str) -> SynthResult:
        """用固定测试句 :data:`PREVIEW_TEXT` 生成试听音频。"""
        ...


class EngineError(Exception):
    """引擎错误基类。message 必须是可读中文（硬性红线 5）。"""

    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail  # 可复制的底层细节（HTTP 状态、异常串等）

    def copyable(self) -> str:
        """返回可复制的完整错误信息（中文 + 细节）。"""
        return f"{self.message}\n细节：{self.detail}" if self.detail else self.message


class EngineNetworkError(EngineError):
    """网络类错误（可触发 SAPI 降级）。"""


class EngineConfigError(EngineError):
    """配置类错误（缺 API Key、参数超范围等）。"""


def clamp_params(req: TTSRequest) -> list[str]:
    """把请求参数夹到合法范围，返回越界警告列表。"""
    warnings: list[str] = []
    if not (RATE_MIN <= req.rate <= RATE_MAX):
        warnings.append(f"语速 {req.rate:+.0%} 超出范围，已限制到 [{RATE_MIN:+.0%}, {RATE_MAX:+.0%}]")
        req.rate = max(RATE_MIN, min(RATE_MAX, req.rate))
    if not (PITCH_MIN <= req.pitch <= PITCH_MAX):
        warnings.append(f"音调 {req.pitch:+g} 半音超出范围，已限制到 [{PITCH_MIN:g}, {PITCH_MAX:g}]")
        req.pitch = max(PITCH_MIN, min(PITCH_MAX, req.pitch))
    if not (VOLUME_MIN <= req.volume <= VOLUME_MAX):
        warnings.append(f"音量 {req.volume:+g} dB 超出范围，已限制到 [{VOLUME_MIN:g}, {VOLUME_MAX:g}]")
        req.volume = max(VOLUME_MIN, min(VOLUME_MAX, req.volume))
    return warnings


def voice_short_name(voice_id: str) -> str:
    """从 voice_id 提取短名（命名模板 ``{voice}`` 用），如 "edge:zh-CN-YunxiNeural"→"YunxiNeural"。"""
    tail = voice_id.split(":", 1)[-1]
    return tail.rsplit(".", 1)[0].split("-")[-1] if tail else voice_id


def engine_of(voice_id: str) -> str:
    """从 voice_id 解析引擎名。"""
    return voice_id.split(":", 1)[0] if ":" in voice_id else ""


def to_request_dict(req: TTSRequest) -> dict[str, Any]:
    """把 TTSRequest 转为可 JSON 化的 dict（项目文件/参数.json 用）。"""
    return {
        "text": req.text,
        "voice_id": req.voice_id,
        "rate": req.rate,
        "pitch": req.pitch,
        "volume": req.volume,
        "style": req.style,
        "style_degree": req.style_degree,
        "pauses": [list(p) for p in req.pauses],
        "out_format": req.out_format,
        "sample_rate": req.sample_rate,
        "bitrate": req.bitrate,
        "normalize": req.normalize,
        "target_db": req.target_db,
        "lexicon": dict(req.lexicon),
    }
