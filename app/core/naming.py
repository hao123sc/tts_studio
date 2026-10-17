"""命名模板（FR-P-11）。生成文件名必须严格按模板，并与清单同步（红线 6）。

支持占位符：``{i}`` 序号（可带格式如 ``{i:02d}``）、``{name}`` 段名、
``{voice}`` 音色短名、``{date}`` 日期（YYYYMMDD）、``{ext}`` 扩展名（不含点）。
默认模板 ``{i:02d}.mp3``。扩展名若与输出格式不符会自动纠正（设计决策，见 README）。
"""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import PureWindowsPath

# Windows 文件名非法字符（同时覆盖 Linux 以保证跨平台一致）
_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_name(name: str) -> str:
    """把任意字符串清洗为安全的 Windows 文件名主体（不含扩展名）。"""
    s = _ILLEGAL.sub("_", (name or "").strip())
    s = s.rstrip(". ")           # Windows 不允许结尾点/空格
    s = re.sub(r"\s+", " ", s)
    if not s:
        s = "未命名"
    if s.split(".")[0].upper() in _RESERVED:
        s = "_" + s
    return s[:120]


def ensure_extension(filename: str, out_format: str) -> str:
    """确保文件名扩展名与输出格式一致（模板写死 .mp3 但改为 wav 时自动纠正）。"""
    ext = (out_format or "mp3").lstrip(".").lower()
    p = PureWindowsPath(filename)
    if p.suffix.lower().lstrip(".") in {"mp3", "wav", "flac"}:
        return f"{p.stem}.{ext}"
    return f"{filename}.{ext}"


def render_filename(
    template: str,
    *,
    i: int,
    name: str = "",
    voice: str = "",
    date: str | None = None,
    out_format: str = "mp3",
    override: str = "",
) -> str:
    """按模板渲染最终文件名。

    :param template: 命名模板，如 ``{i:02d}.mp3`` 或 ``{i:02d}_{name}.wav``
    :param i: 段序号（从 1 开始）
    :param name: 段名（备注）
    :param voice: 音色短名
    :param date: 日期字符串，默认今天 YYYYMMDD
    :param out_format: mp3 / wav / flac
    :param override: CSV ``文件名`` 列的整段覆盖名（优先于模板；仍会清洗并纠正扩展名）
    """
    if override.strip():
        # 先清洗非法字符（/ \ : * ?等），再处理扩展名；
        # 不用 PureWindowsPath 拆路径（会把 / : 当分隔符吃掉用户文件名）
        return ensure_extension(sanitize_name(override.strip()), out_format)

    d = date or _dt.date.today().strftime("%Y%m%d")
    fields = {
        "i": int(i),
        "name": sanitize_name(name),
        "voice": sanitize_name(voice),
        "date": d,
        "ext": (out_format or "mp3").lstrip(".").lower(),
    }
    try:
        rendered = _format_template(template or "{i:02d}.{ext}", fields)
    except (KeyError, ValueError, IndexError) as exc:
        raise ValueError(f"命名模板无效：{template!r}（{exc}）") from exc
    # 允许模板写 "{i:02d}.mp3" 这种带死扩展名的写法：按输出格式纠正
    return ensure_extension(rendered, out_format)


_PLACEHOLDER = re.compile(r"\{(\w+)(:[^}]*)?\}")


def _format_template(template: str, fields: dict) -> str:
    """支持 ``{i}``/``{i:02d}`` 的安全格式化（不走 str.format 全量求值）。"""
    def repl(m: re.Match) -> str:
        key, spec = m.group(1), m.group(2)
        if key not in fields:
            raise KeyError(f"未知占位符 {{{key}}}")
        if spec:
            return format(fields[key], spec[1:])
        return str(fields[key])

    out = _PLACEHOLDER.sub(repl, template)
    # 清理残留非法字符（模板中手写的冒号等）
    stem, dot, ext = out.rpartition(".")
    if dot:
        return f"{sanitize_name(stem)}.{sanitize_name(ext).lower() or 'mp3'}"
    return sanitize_name(out)


def preview_names(template: str, count: int, out_format: str = "mp3") -> list[str]:
    """预览前 N 个文件名（设置界面用）。"""
    return [render_filename(template, i=i, out_format=out_format) for i in range(1, count + 1)]
