"""引擎插件目录。

新引擎 = 新增一个 ``engines/xxx.py`` 并在 :data:`ENGINE_FACTORIES` 注册，
不改核心（需求文档 §7 可维护性）。P2 克隆引擎（gpt_sovits / indextts /
cosyvoice / openvoice）已预留插件位。
"""

from __future__ import annotations

from typing import Callable

from .base import Engine, EngineError, SynthResult, TTSRequest, Voice
from .edge import EdgeEngine
from .sapi import SapiEngine
from .skeletons import AzureEngine, IflytekEngine, TencentEngine

#: 引擎工厂表（插件注册点）
ENGINE_FACTORIES: dict[str, Callable[..., Engine]] = {
    "edge": EdgeEngine,
    "sapi": SapiEngine,
    "azure": AzureEngine,
    "iflytek": IflytekEngine,
    "tencent": TencentEngine,
    # P2 预留：
    # "gpt_sovits": GptSovitsEngine,
    # "indextts": IndexTTSEngine,
    # "cosyvoice": CosyVoiceEngine,
    # "openvoice": OpenVoiceEngine,
}

#: P1 产品可见引擎（mock 不进产品列表）
PRODUCT_ENGINES = ["edge", "sapi", "azure", "iflytek", "tencent"]


def get_engine(name: str, **kwargs) -> Engine:
    """按名创建引擎实例。"""
    try:
        factory = ENGINE_FACTORIES[name]
    except KeyError as exc:
        raise EngineError(f"未知引擎：{name}") from exc
    return factory(**kwargs)


__all__ = [
    "Engine", "EngineError", "SynthResult", "TTSRequest", "Voice",
    "EdgeEngine", "SapiEngine", "get_engine", "ENGINE_FACTORIES", "PRODUCT_ENGINES",
]
