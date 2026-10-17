"""断点续传哈希（FR-G-3）。

对「文本 + 全部生成参数」做 SHA-256：内容未变则跳过重做，任一变化即重做。
哈希输入采用规范 JSON（键排序），与 dict 插入顺序无关。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


def segment_hash(text: str, voice_id: str, params: Mapping[str, Any], lexicon: Mapping[str, str] | None = None) -> str:
    """计算段内容哈希（十六进制 64 字符）。

    :param text: 段文本（原始文本，未做词典替换）
    :param voice_id: 目标音色 id
    :param params: 生成参数（rate/pitch/volume/style/format/sample_rate/bitrate/...）
    :param lexicon: 发音词典（参与输出的词典变更也应触发重做）
    """
    payload = {
        "text": text,
        "voice_id": voice_id,
        "params": {k: params[k] for k in sorted(params)},
        "lexicon": {k: lexicon[k] for k in sorted(lexicon)} if lexicon else {},
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def file_hash(path: str) -> str:
    """流式计算文件 SHA-256（导出校验用，逐块读避免占内存）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
