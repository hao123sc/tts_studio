"""Edge-TTS 引擎适配器（P1 默认，免费免 Key，需联网）。

参数换算（FR-P-2"引擎只支持 Hz 的，内部换算"）：

* rate: ``+0.2`` → ``"+20%"``；
* pitch: 半音 → Hz 偏移：``Δf = 220Hz × (2^(半音/12) − 1)``（参考基频 220Hz）；
* volume: dB → 百分比：``Δ% = (10^(dB/20) − 1) × 100``，限制在 ±100%。

网络失败抛 :class:`EngineNetworkError`（可触发 SAPI 降级）；音色列表失败时用
内置清单兜底（FR-V-4）。
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Callable

from .base import (
    PREVIEW_TEXT,
    EngineError,
    EngineNetworkError,
    SynthResult,
    TTSRequest,
    Voice,
    clamp_params,
)

#: 音调换算参考基频（Hz）
SEMITONE_REFERENCE_HZ = 220.0

#: 已知中文音色的显示名（动态拉取时用于美化）
_NAME_MAP = {
    "zh-CN-YunxiNeural": "云希（少年男声）",
    "zh-CN-YunjianNeural": "云健（稳重男声）",
    "zh-CN-YunyangNeural": "云扬（明亮男声）",
    "zh-CN-YunxiaNeural": "云夏（童声男声）",
    "zh-CN-XiaoxiaoNeural": "晓晓（温柔女声）",
    "zh-CN-XiaoyiNeural": "晓伊（甜美女声）",
    "zh-CN-XiaoshuangNeural": "晓双（活泼女声）",
    "zh-CN-XiaochenNeural": "晓辰（知性女声）",
    "zh-CN-liaoning-XiaobeiNeural": "晓北（东北话）",
    "zh-CN-shaanxi-XiaoniNeural": "晓妮（陕西话）",
    "zh-HK-HiuMaanNeural": "晓曼（粤语女声）",
}

_TAG_MAP = {
    "YunxiNeural": ["旁白", "解说", "阳光"],
    "YunjianNeural": ["新闻", "体育"],
    "YunyangNeural": ["解说", "新闻"],
    "YunxiaNeural": ["学生", "童声"],
    "XiaoxiaoNeural": ["旁白", "温柔"],
    "XiaoyiNeural": ["学生", "甜美"],
    "XiaoshuangNeural": ["活泼", "童声"],
    "XiaochenNeural": ["知性", "旁白"],
    "XiaobeiNeural": ["方言", "趣味"],
    "XiaoniNeural": ["方言", "情景"],
    "HiuMaanNeural": ["粤语"],
}


def _rate_str(rate: float) -> str:
    """语速 float → edge 字符串。"""
    return f"{rate * 100:+.0f}%"


def _pitch_str(pitch: float) -> str:
    """半音 → edge Hz 偏移字符串。"""
    hz = SEMITONE_REFERENCE_HZ * (2.0 ** (pitch / 12.0) - 1.0)
    hz = max(-50.0, min(50.0, hz))
    return f"{hz:+.0f}Hz"


def _volume_str(volume: float) -> str:
    """dB → edge 百分比字符串。"""
    pct = (10.0 ** (volume / 20.0) - 1.0) * 100.0
    pct = max(-100.0, min(100.0, pct))
    return f"{pct:+.0f}%"


class EdgeEngine:
    """Edge-TTS 适配器。"""

    name = "edge"

    def __init__(self, proxy: str = "", timeout: float = 30.0) -> None:
        self.proxy = proxy or ""
        self.timeout = timeout
        #: 最近一次 list_voices 是否用了内置兜底
        self.used_builtin_fallback = False

    # -- 能力矩阵（文档 §6）-------------------------------------------------
    def capabilities(self) -> dict:
        """Edge-TTS 能力：语速/音调/音量/停顿✅，情感/风格❌，离线❌。"""
        return {
            "rate": True,
            "pitch": True,
            "volume": True,
            "style": False,
            "style_degree": False,
            "pauses": True,
            "offline": False,
            "formats": ["mp3"],
            "max_chars": 2000,
            "rate_range": (-0.5, 1.0),
            "note": "Edge-TTS 为在线服务，需联网；不支持情感/风格参数。",
        }

    # -- 音色 --------------------------------------------------------------
    def list_voices(self) -> list[Voice]:
        """动态拉取音色清单；失败时返回内置兜底清单（FR-V-4）。"""
        try:
            raw = self._run_async(self._fetch_voices(), timeout=15.0)
            self.used_builtin_fallback = False
            return [self._to_voice(v) for v in raw]
        except Exception:
            self.used_builtin_fallback = True
            from ..core.registry import builtin_edge_voices

            return builtin_edge_voices()

    async def _fetch_voices(self) -> list[dict]:
        import edge_tts

        return await edge_tts.list_voices(proxy=self.proxy or None)

    def _to_voice(self, raw: dict) -> Voice:
        short = str(raw.get("ShortName", ""))
        full = str(raw.get("Name", ""))
        gender = str(raw.get("Gender", "")).lower()
        locale = str(raw.get("Locale", "zh-CN"))
        tail = short.split("-")[-1] if short else full
        name = _NAME_MAP.get(short)
        if not name:
            zh = re.sub(r"(Neural|Standard)$", "", tail)
            name = f"{zh}（{locale}）"
        tags = list(_TAG_MAP.get(tail, []))
        return Voice(id=f"edge:{short}", name=name, engine="edge", lang=locale,
                     gender=gender or "", tags=tags)

    # -- 合成 --------------------------------------------------------------
    def synth(self, req: TTSRequest, out_path: str, on_progress: Callable[..., None] | None = None) -> SynthResult:
        """合成文本写入 ``out_path``（edge 原始 MP3 流；后续由后处理解码清洗）。"""
        warnings = clamp_params(req)
        if req.style:
            warnings.append("Edge-TTS 不支持情感/风格参数，已忽略")
        if req.volume:
            warnings.append(f"音量 {req.volume:+g} dB 以近似百分比换算施加")
        voice = req.voice_id.split(":", 1)[-1]
        text = req.text.strip()
        if not text:
            raise EngineError("文本为空，无法合成")
        try:
            self._run_async(self._synth_async(text, voice, req, out_path, on_progress))
        except EngineError:
            raise
        except Exception as exc:
            raise self._translate_error(exc) from exc
        if not Path(out_path).exists() or Path(out_path).stat().st_size == 0:
            raise EngineError("Edge-TTS 未返回音频数据", detail=f"输出文件为空：{out_path}")
        return SynthResult(path=out_path, duration=0.0, sample_rate=0, warnings=warnings)

    async def _synth_async(self, text: str, voice: str, req: TTSRequest, out_path: str,
                           on_progress: Callable[..., None] | None) -> None:
        import edge_tts

        comm = edge_tts.Communicate(
            text,
            voice=voice,
            rate=_rate_str(req.rate),
            volume=_volume_str(req.volume),
            pitch=_pitch_str(req.pitch),
            proxy=self.proxy or None,
        )
        done = 0
        with open(out_path, "wb") as f:
            async for chunk in comm.stream():
                if chunk.get("type") == "audio":
                    f.write(chunk["data"])
                    done += len(chunk["data"])
                    if on_progress:
                        on_progress(done)

    def preview(self, voice_id: str, out_path: str) -> SynthResult:
        """固定测试句试听。"""
        req = TTSRequest(text=PREVIEW_TEXT, voice_id=voice_id, out_format="mp3")
        return self.synth(req, out_path)

    # -- 工具 --------------------------------------------------------------
    def _run_async(self, coro, timeout: float | None = None):
        """在同步上下文运行协程（QThread 中无运行中事件循环，可直接 asyncio.run）。"""
        t = timeout if timeout is not None else self.timeout * 4
        return asyncio.run(asyncio.wait_for(coro, timeout=t))

    @staticmethod
    def _translate_error(exc: Exception) -> EngineError:
        """把底层异常翻译为中文可读错误（含代理设置入口提示）。"""
        detail = f"{type(exc).__name__}: {exc}"
        text = str(exc).lower()
        hint = "可在「设置 → 网络代理」配置代理（如 http://127.0.0.1:7890），保存后立即生效。"
        if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
            return EngineNetworkError(f"Edge-TTS 连接超时，请检查网络；{hint}", detail=detail)
        if "403" in text or "forbidden" in text:
            return EngineNetworkError(f"Edge-TTS 返回 403（网络环境被拒），请换网络或配置代理；{hint}", detail=detail)
        if any(k in text for k in ("resolve", "connect", "network", "timeout", "ssl", "proxy", "dns")):
            return EngineNetworkError(f"无法连接 Edge-TTS 服务器，请检查网络；{hint}", detail=detail)
        return EngineError("Edge-TTS 合成失败", detail=detail)
