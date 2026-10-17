"""P2 音色克隆引擎占位（IndexTTS2，Apache-2.0，约 8GB 显存，24kHz，时长/情感可控）。

本文件只保留插件接口，P1 不实现。
"""

from __future__ import annotations

from typing import Callable

from .base import EngineError, SynthResult, TTSRequest, Voice


class IndexTTSEngine:
    """IndexTTS2 适配器（P2 预留）。"""

    name = "indextts"

    def capabilities(self) -> dict:
        """能力占位。"""
        return {"rate": True, "pitch": True, "volume": True, "style": True,
                "style_degree": True, "pauses": True, "offline": True,
                "formats": ["wav"], "note": "P2 预留：IndexTTS2（时长/情感可控）。"}

    def list_voices(self) -> list[Voice]:
        """P2：克隆音色列表。"""
        return []

    def synth(self, req: TTSRequest, out_path: str, on_progress: Callable[..., None] | None = None) -> SynthResult:
        """P2 预留。"""
        raise EngineError("IndexTTS2 为 P2 预留插件，本期未启用")

    def preview(self, voice_id: str, out_path: str) -> SynthResult:
        """P2 预留。"""
        return self.synth(TTSRequest(text="", voice_id=voice_id), out_path)
