"""Windows SAPI 离线兜底引擎（pyttsx3 / win32com）。

能力矩阵（文档 §6）：语速 ✅（SAPI −10~+10），音调 ❌，音量 ❌，情感 ❌，停顿 ✅（插静音），离线 ✅。
非 Windows 平台走 pyttsx3（Linux/macOS 需系统安装 espeak 等后端）；无可用语音时
报中文错误，不崩溃。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Callable

from .base import (
    PREVIEW_TEXT,
    EngineConfigError,
    EngineError,
    SynthResult,
    TTSRequest,
    Voice,
    clamp_params,
)


class SapiEngine:
    """SAPI / pyttsx3 适配器。"""

    name = "sapi"

    def __init__(self) -> None:
        self._engine = None
        self._voices: list[Voice] | None = None

    # -- 能力 --------------------------------------------------------------
    def capabilities(self) -> dict:
        """SAPI 能力：只支持语速，其余在 UI 置灰。"""
        return {
            "rate": True,
            "pitch": False,
            "volume": False,
            "style": False,
            "style_degree": False,
            "pauses": True,
            "offline": True,
            "formats": ["wav"],
            "max_chars": 4000,
            "rate_range": (-1.0, 1.0),
            "note": "系统 SAPI 离线音色：仅支持语速；音调/音量/风格不支持（已置灰）。",
        }

    # -- 后端初始化 ----------------------------------------------------------
    def _init_backend(self):
        """初始化 pyttsx3 后端（Windows 上 pyttsx3 即调用 SAPI）。"""
        if self._engine is not None:
            return self._engine
        try:
            import pyttsx3

            self._engine = pyttsx3.init()
        except Exception as exc:
            raise EngineConfigError(
                "系统离线语音（SAPI）不可用：请确认 Windows 语音组件已安装"
                if sys.platform.startswith("win")
                else "当前系统没有可用的离线语音后端（Windows 为 SAPI；Linux/macOS 需安装 espeak）",
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc
        return self._engine

    # -- 音色 --------------------------------------------------------------
    def list_voices(self) -> list[Voice]:
        """列出系统语音。"""
        if self._voices is not None:
            return self._voices
        try:
            eng = self._init_backend()
        except EngineError:
            self._voices = []
            return []
        out: list[Voice] = []
        try:
            for idx, v in enumerate(eng.getProperty("voices") or []):
                name = str(getattr(v, "name", "") or f"系统语音{idx + 1}")
                langs = getattr(v, "languages", None) or []
                lang = str(langs[0]) if langs else "zh-CN"
                if "zh" in name.lower() or "chinese" in name.lower() or "zh" in lang.lower():
                    lang = "zh-CN"
                gender = ""
                mid = name.lower()
                if "female" in mid or "女" in name:
                    gender = "female"
                elif "male" in mid or "男" in name:
                    gender = "male"
                out.append(Voice(id=f"sapi:{name}", name=f"{name}（系统离线）",
                                 engine="sapi", lang=lang, gender=gender, tags=["离线"]))
        except Exception:
            out = []
        self._voices = out
        return out

    # -- 合成 --------------------------------------------------------------
    def synth(self, req: TTSRequest, out_path: str, on_progress: Callable[..., None] | None = None) -> SynthResult:
        """合成 WAV（SAPI 原生格式；后续后处理统一重采样/编码）。"""
        warnings = clamp_params(req)
        if req.pitch:
            warnings.append("SAPI 不支持音调参数，已忽略")
        if req.volume:
            warnings.append("SAPI 不支持音量参数，已忽略")
        if req.style:
            warnings.append("SAPI 不支持情感/风格参数，已忽略")
        text = req.text.strip()
        if not text:
            raise EngineError("文本为空，无法合成")
        eng = self._init_backend()
        voice_name = req.voice_id.split(":", 1)[-1]
        applied = False
        for v in eng.getProperty("voices") or []:
            if str(getattr(v, "name", "")) == voice_name or str(getattr(v, "id", "")) == req.voice_id:
                eng.setProperty("voice", v.id)
                applied = True
                break
        if not applied and self.list_voices():
            warnings.append(f"未找到音色 {voice_name}，已使用系统默认音色")
        # SAPI 语速 −10~+10（FR-P 矩阵），映射自 −50%~+100%
        eng.setProperty("rate", max(-10, min(10, int(round(req.rate * 10)))))
        outp = Path(out_path)
        outp.parent.mkdir(parents=True, exist_ok=True)
        # pyttsx3 保存 wav；若目标无扩展名，补 .wav 后再改名
        save_path = outp if outp.suffix.lower() == ".wav" else Path(str(outp) + ".wav")
        try:
            eng.save_to_file(text, str(save_path))
            eng.runAndWait()
        except Exception as exc:
            raise EngineError("系统离线语音合成失败", detail=f"{type(exc).__name__}: {exc}") from exc
        if not save_path.exists() or save_path.stat().st_size == 0:
            raise EngineError("系统离线语音没有产生音频（请检查系统语音组件）")
        if save_path != outp:
            save_path.replace(outp)
        return SynthResult(path=str(outp), duration=0.0, sample_rate=0, warnings=warnings)

    def preview(self, voice_id: str, out_path: str) -> SynthResult:
        """固定测试句试听。"""
        req = TTSRequest(text=PREVIEW_TEXT, voice_id=voice_id, out_format="mp3")
        return self.synth(req, out_path)

    # -- 降级映射（edge → sapi）----------------------------------------------
    @staticmethod
    def map_voice_id(edge_voice_id: str, candidates: list[Voice]) -> str:
        """把 edge 音色映射到性别最接近的系统音色（网络降级用）。"""
        gender = ""
        name = edge_voice_id.lower()
        if any(k in name for k in ("xiaoxiao", "xiaoyi", "xiaoshuang", "xiaochen", "xiaobei", "xiaoni", "hiumaan", "female")):
            gender = "female"
        elif any(k in name for k in ("yunxi", "yunjian", "yunyang", "yunxia", "male")):
            gender = "male"
        for v in candidates:
            if gender and v.gender == gender and v.lang.lower().startswith("zh"):
                return v.id
        for v in candidates:
            if v.lang.lower().startswith("zh"):
                return v.id
        return candidates[0].id if candidates else ""
