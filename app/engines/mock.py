"""测试/离线演示引擎（仅用于自动化测试与无网络演示，不进产品音色列表）。

产生一段「语音状」音频：正弦扫频 + 幅度包络 + 首尾静音，使去静音、停顿插入、
响度归一、MP3 编码等后处理链路可被完整验证。
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np

from .base import PREVIEW_TEXT, SynthResult, TTSRequest, Voice


class MockEngine:
    """确定性合成引擎：相同文本 → 相同波形（便于断点续传/时长测试）。"""

    name = "mock"

    def __init__(self, fail_ids: set[str] | None = None, source_rate: int = 24000) -> None:
        #: 指定哪些文本前缀要失败（重试/降级测试用）
        self.fail_ids = fail_ids or set()
        self.source_rate = source_rate
        self.calls: list[str] = []

    def capabilities(self) -> dict:
        """全能力（测试用）。"""
        return {
            "rate": True, "pitch": True, "volume": True, "style": False,
            "style_degree": False, "pauses": True, "offline": True,
            "formats": ["wav"], "max_chars": 5000,
        }

    def list_voices(self) -> list[Voice]:
        """两个测试音色。"""
        return [
            Voice(id="mock:a", name="测试男声", engine="mock", lang="zh-CN", gender="male"),
            Voice(id="mock:b", name="测试女声", engine="mock", lang="zh-CN", gender="female"),
        ]

    def synth(self, req: TTSRequest, out_path: str, on_progress: Callable[..., None] | None = None) -> SynthResult:
        """生成 WAV：语速影响时长，文本决定包络形状。"""
        import soundfile as sf

        self.calls.append(req.text)
        for key in self.fail_ids:
            if key and key in req.text:
                raise RuntimeError(f"模拟失败：{key}")
        chars = max(1, len(req.text.strip()))
        # 中文 ≈ 4.2 字/秒；语速 +20% → 时长 ×1/1.2
        dur = chars / 4.2 / (1.0 + req.rate)
        sr = self.source_rate
        n = int(dur * sr)
        t = np.arange(n) / sr
        # 首尾各留 0.3s 静音（考验去静音），中间扫频 200→400Hz
        edge = int(0.3 * sr)
        freq = 200.0 + 200.0 * (t / max(dur, 1e-6))
        wave = 0.4 * np.sin(2 * np.pi * np.cumsum(freq) / sr)
        env = np.ones(n)
        env[:edge] = 0.0
        env[-edge:] = 0.0
        fade_n = int(0.05 * sr)
        env[:edge + fade_n] = np.linspace(0, 1, edge + fade_n) ** 2
        env[-(edge + fade_n):] = np.linspace(1, 0, edge + fade_n) ** 2
        wave = (wave * env).astype(np.float32)
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        sf.write(out_path, wave, sr)
        return SynthResult(path=out_path, duration=float(dur), sample_rate=sr)

    def preview(self, voice_id: str, out_path: str) -> SynthResult:
        """固定测试句试听。"""
        return self.synth(TTSRequest(text=PREVIEW_TEXT, voice_id=voice_id), out_path)
