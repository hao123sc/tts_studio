"""Azure / 讯飞 / 腾讯云 适配器骨架（P1 只留接口与配置项，不要求接通）。

接入方法（P2 或付费需要时）：

1. 在「设置」中填入对应 API Key（存用户目录 ``config.json``，不进代码）；
2. 在本文件实现 ``_do_synth``：调用官方 SDK，把音频写到 ``out_path``；
3. 在 :mod:`app.engines` 的 ``ENGINE_FACTORIES`` 注册（已预留）。

合规提醒：仅可克隆本人/已获授权的声音；云端服务的文本会发送至对应云厂商。
"""

from __future__ import annotations

from typing import Callable

from .base import EngineConfigError, EngineError, SynthResult, TTSRequest, Voice


class _CloudSkeleton:
    """云引擎骨架基类。"""

    name = "cloud"

    def __init__(self, api_key: str = "", region: str = "") -> None:
        self.api_key = api_key
        self.region = region

    def capabilities(self) -> dict:
        """能力（占位）：按目标云服务填，UI 会据此置灰。"""
        return {
            "rate": True,
            "pitch": True,
            "volume": True,
            "style": True,
            "style_degree": True,
            "pauses": True,
            "offline": False,
            "formats": ["mp3"],
            "max_chars": 3000,
            "note": f"{self.name} 为 P1 预留骨架，需在设置中填入 API Key 后启用。",
        }

    def list_voices(self) -> list[Voice]:
        """骨架：不返回音色。"""
        return []

    def synth(self, req: TTSRequest, out_path: str, on_progress: Callable[..., None] | None = None) -> SynthResult:
        """骨架：未接通。"""
        if not self.api_key:
            raise EngineConfigError(
                f"{self.name} 适配器尚未配置 API Key（P1 为预留骨架，请改用 Edge-TTS 或系统 SAPI）",
                detail=f"engine={self.name}",
            )
        raise EngineError(f"{self.name} 适配器为 P1 预留骨架，尚未接通", detail=f"engine={self.name}")

    def preview(self, voice_id: str, out_path: str) -> SynthResult:
        """骨架：未接通。"""
        return self.synth(TTSRequest(text="试听", voice_id=voice_id), out_path)


class AzureEngine(_CloudSkeleton):
    """Azure Cognitive Services 语音（预留骨架）。"""

    name = "azure"


class IflytekEngine(_CloudSkeleton):
    """讯飞开放平台语音（预留骨架）。"""

    name = "iflytek"


class TencentEngine(_CloudSkeleton):
    """腾讯云语音合成（预留骨架）。"""

    name = "tencent"
