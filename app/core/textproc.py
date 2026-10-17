"""文本处理：停顿标记（FR-T-3）、文本校验（FR-T-7）、拆句/切分（FR-T-8、§11）。

停顿标记写法（两种都支持，大小写不敏感）::

    [pause=1.5]      [pause=1.5s]
    [[停顿1.5s]]     [[停顿1.5]]

生成时把标记处转为**精确秒数的静音**（验收 8：1.5±0.1 秒）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 停顿标记：[pause=1.5] / [pause=1.5s] / [[停顿1.5s]] / [[停顿1.5]]
PAUSE_RE = re.compile(
    r"\[\s*pause\s*[=:]\s*(\d+(?:\.\d+)?)\s*s?\s*\]"      # [pause=1.5] / [pause:1.5s]
    r"|\[\[\s*停顿\s*(\d+(?:\.\d+)?)\s*s?\s*\]\]",        # [[停顿1.5s]] / [[停顿1.5]]
    re.IGNORECASE,
)

#: 引擎单次合成的建议字符上限（超过则按句切分后拼接，§11）
DEFAULT_MAX_CHARS = 1200

# 中英文括号/引号配对检查
_PAIRS = {"（": "）", "(": ")", "【": "】", "[": "]", "“": "”", "《": "》"}
_OPEN = set(_PAIRS)
_CLOSE = set(_PAIRS.values())


@dataclass
class Piece:
    """一段带后置停顿的文本块。"""

    text: str
    pause_after: float = 0.0  # 该块之后插入的静音秒数


def parse_pauses(text: str) -> tuple[list[Piece], list[tuple[int, float]]]:
    """解析停顿标记。

    返回 ``(文本块列表, pauses)``：

    * 文本块列表：按标记切开的 :class:`Piece`，``pause_after`` 为该块后的静音秒数；
    * pauses：与 :class:`~app.engines.base.TTSRequest.pauses` 同构的
      ``[(字符位置, 秒)]``，在原字符串中的位置（供支持原生停顿的引擎使用）。
    """
    pieces: list[Piece] = []
    pauses: list[tuple[int, float]] = []
    pos = 0
    for m in PAUSE_RE.finditer(text):
        seg = text[pos:m.start()]
        sec = float(m.group(1) or m.group(2))
        pauses.append((m.start(), sec))
        pieces.append(Piece(text=seg, pause_after=sec))
        pos = m.end()
    pieces.append(Piece(text=text[pos:], pause_after=0.0))
    # 合并空白块，但保留停顿语义：空文本 + 停顿 → 纯静音块
    out: list[Piece] = []
    for p in pieces:
        if out and not p.text.strip() and p.pause_after == 0.0 and not out[-1].text.strip():
            continue
        out.append(p)
    return out, pauses


def strip_pauses(text: str) -> str:
    """去掉停顿标记，返回纯文本（字数统计用）。"""
    return PAUSE_RE.sub("", text)


def count_chars(text: str) -> int:
    """字数（去掉停顿标记与全部空白）。"""
    return len(re.sub(r"\s", "", strip_pauses(text)))


def estimate_duration(text: str, chars_per_sec: float = 4.2) -> float:
    """预估时长（秒）。中文 ≈ 4.2 字/秒（FR-T-1），可按音色校准。"""
    n = count_chars(text)
    pause_total = sum(sec for _, sec in parse_pauses(text)[1])
    return (n / chars_per_sec if chars_per_sec > 0 else 0.0) + pause_total


def split_sentences(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> list[str]:
    """按句读切分长文本（。！？；!?; 与换行），必要时再按逗号/长度强制切分。

    用于超过引擎单次上限的段落（§11：自动按句切分后拼接，边界 20ms 淡入淡出）。
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    # 先按句读符切
    parts = re.split(r"(?<=[。！？；!?;\n])", text)
    chunks: list[str] = []
    buf = ""
    for part in parts:
        if not part:
            continue
        if buf and len(buf) + len(part) > max_chars:
            chunks.append(buf)
            buf = part
        else:
            buf += part
        while len(buf) > max_chars:  # 单句仍超长 → 硬切
            chunks.append(buf[:max_chars])
            buf = buf[max_chars:]
    if buf:
        chunks.append(buf)
    return [c for c in chunks if c.strip()]


def validate_text(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> list[str]:
    """文本校验，返回中文警告列表（FR-T-7）。"""
    warnings: list[str] = []
    plain = strip_pauses(text)
    if not plain.strip():
        warnings.append("文本为空")
    if len(plain) > max_chars:
        warnings.append(f"文本 {len(plain)} 字超过引擎单次上限 {max_chars} 字，生成时将自动按句切分")
    bad = sorted({ch for ch in text if ord(ch) < 32 and ch not in "\t\n\r"})
    if bad:
        warnings.append(f"含不支持的控制字符：{' '.join(repr(c) for c in bad)}")
    # 括号/引号配对（忽略停顿标记本身）
    stack: list[str] = []
    clean = PAUSE_RE.sub("", text)
    for ch in clean:
        if ch in _OPEN:
            stack.append(ch)
        elif ch in _CLOSE:
            if not stack:
                warnings.append(f"括号/引号不配对：多余的「{ch}」")
                break
            open_ch = stack.pop()
            if _PAIRS[open_ch] != ch:
                warnings.append(f"括号/引号不配对：「{open_ch}」与「{ch}」")
                break
    else:
        if stack:
            warnings.append(f"括号/引号不配对：未闭合的「{stack[-1]}」")
    return warnings
