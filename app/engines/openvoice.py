"""P2 音色迁移引擎占位（OpenVoice V2，MIT，CPU 可跑，22.05kHz，时长不变）。

本课程已验证：OpenVoice V2 + 7 分钟干声 → 41 条配音，时长漂移 <0.02 秒。
P2 先接它跑通克隆链路，再接 GPT-SoVITS 提升相似度。本文件只保留插件接口。

合规（FR-C-8）：训练/迁移前必须弹出「仅可克隆本人或已获授权的声音」确认；
生成文件写入 AI 生成标记。
"""

from __future__ import annotations

from typing import Callable

from .base import EngineError, SynthResult, TTSRequest, Voice


class OpenVoiceEngine:
    """OpenVoice V2 音色迁移适配器（P2 预留）。"""

    name = "openvoice"

    def capabilities(self) -> dict:
        """能力占位（迁移模式：时长不变，语速/音调/音量 ❌）。"""
        return {"rate": False, "pitch": False, "volume": False, "style": False,
                "style_degree": False, "pauses": False, "offline": True,
                "formats": ["wav"], "note": "P2 预留：OpenVoice 音色迁移（时长不变）。"}

    def list_voices(self) -> list[Voice]:
        """P2：声纹库音色列表。"""
        return []

    def synth(self, req: TTSRequest, out_path: str, on_progress: Callable[..., None] | None = None) -> SynthResult:
        """P2 预留。"""
        raise EngineError("OpenVoice 为 P2 预留插件，本期未启用（下期做音色迁移）")

    def preview(self, voice_id: str, out_path: str) -> SynthResult:
        """P2 预留。"""
        return self.synth(TTSRequest(text="", voice_id=voice_id), out_path)
