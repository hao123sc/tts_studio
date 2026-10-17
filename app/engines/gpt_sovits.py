"""P2 音色克隆引擎占位（GPT-SoVITS v2Pro/v4，MIT）。

P2 接入要点：推理 ≥6GB 显存、v4 原生 48kHz、中文克隆最稳；微调建议 12GB。
本文件只保留插件接口，P1 不实现。
"""

from __future__ import annotations

from typing import Callable

from .base import EngineError, SynthResult, TTSRequest, Voice


class GptSovitsEngine:
    """GPT-SoVITS 适配器（P2 预留）。"""

    name = "gpt_sovits"

    def capabilities(self) -> dict:
        """能力占位（P2 实现时按实际填写）。"""
        return {"rate": True, "pitch": False, "volume": True, "style": False,
                "style_degree": False, "pauses": True, "offline": True,
                "formats": ["wav"], "note": "P2 预留：GPT-SoVITS 音色克隆。"}

    def list_voices(self) -> list[Voice]:
        """P2：返回用户克隆音色（custom=True，与内置音色同列表）。"""
        return []

    def synth(self, req: TTSRequest, out_path: str, on_progress: Callable[..., None] | None = None) -> SynthResult:
        """P2 预留。"""
        raise EngineError("GPT-SoVITS 为 P2 预留插件，本期未启用（下期接入音色克隆）")

    def preview(self, voice_id: str, out_path: str) -> SynthResult:
        """P2 预留。"""
        return self.synth(TTSRequest(text="", voice_id=voice_id), out_path)
