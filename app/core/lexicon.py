"""发音词典（FR-T-4）与数字/单位/缩写读法规则（FR-T-5、FR-T-6）。

处理顺序（设计决策，见 README）：

1. **词典替换**（显式覆盖优先，长词优先）：如 "乐至"→"乐志"、"缫丝"→"骚丝"；
2. **标准号规则**：``GB/T 24067`` → ``GB/T 二四〇六七``（字母＋逐位数字）；
3. **小数规则**：``2.0`` → ``二点零``；
4. **整数＋单位规则**：``5000吨`` → ``五千吨``；
5. **全大写缩写规则**：``LCA`` → ``L C A``（按字母读）；白名单（如 NASA）保留按单词读。

每条规则都可单独开关（FR-T-5"可逐条覆盖"——词典条目即覆盖手段）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# 中文数字
# ---------------------------------------------------------------------------

_DIGITS = "零一二三四五六七八九"
_UNITS = ["", "十", "百", "千"]
_SECTIONS = ["", "万", "亿", "兆"]

#: 数字逐位读法用（含"零"读作"〇"，与课程读法一致：二四〇六七）
_DIGIT_BY_DIGIT = {str(d): c for d, c in enumerate("〇一二三四五六七八九")}


def int_to_chinese(n: int) -> str:
    """整数转中文读法（5000 → 五千，24067 → 二万四千六十七）。"""
    if n == 0:
        return "零"
    if n < 0:
        return "负" + int_to_chinese(-n)
    parts: list[str] = []
    section = 0
    while n > 0:
        chunk = n % 10000
        n //= 10000
        if chunk:
            s = _chunk_to_chinese(chunk)
            parts.append(s + _SECTIONS[section])
        elif parts and not parts[-1].startswith("零"):
            parts.append("零")
        section += 1
    out = "".join(reversed(parts))
    out = out.replace("零零", "零").rstrip("零")
    # "一十" 开头读作 "十"
    if out.startswith("一十"):
        out = out[1:]
    return out or "零"


def _chunk_to_chinese(chunk: int) -> str:
    """0 < chunk < 10000 的中文读法。"""
    s = ""
    zero = False
    for pos in range(3, -1, -1):
        d = (chunk // (10 ** pos)) % 10
        if d == 0:
            zero = True
            continue
        if zero and s:
            s += "零"
        zero = False
        s += _DIGITS[d] + _UNITS[pos]
    return s


def digits_to_chinese(num: str, zero_char: str = "〇") -> str:
    """数字串逐位读（"24067" → "二四〇六七"）。

    小数读法传 ``zero_char="零"``（文档："2.0" 读"二点零"），逐位读用 "〇"。
    """
    out = []
    for ch in num:
        if ch.isdigit():
            out.append(zero_char if ch == "0" else _DIGIT_BY_DIGIT[ch])
        elif ch == ".":
            out.append("点")
        else:
            out.append(ch)
    return "".join(out)


# ---------------------------------------------------------------------------
# 规则
# ---------------------------------------------------------------------------

#: 常用单位/量词（整数＋单位 → 中文数字＋单位）
UNIT_PATTERN = (
    r"吨|千克|公斤|克|毫克|米|厘米|毫米|公里|千米|平方公里|公顷|亩|"
    r"元|万元|亿元|年|月|日|天|小时|分钟|秒|个|人|次|张|条|段|页|本|部|种|类|"
    r"℃|度|千瓦时|千瓦|瓦|升|毫升|倍|分|%|％|字|篇|集|阶|级|层|组|批|件|台|辆|只"
)

#: 标准号/规范号：GB/T 24067、ISO 9001、GB 12345
STANDARD_RE = re.compile(r"\b([A-Z]{1,6}(?:/[A-Z]{1,4})?)\s*(\d{3,6})\b")

#: 小数（前面不能是字母，避免吃掉 ISO9001 里的数字）
DECIMAL_RE = re.compile(r"(?<![A-Za-z0-9])(\d+\.\d+)(?![A-Za-z0-9])")

#: 整数＋单位
INT_UNIT_RE = re.compile(rf"(?<![A-Za-z0-9.])(\d+)\s*({UNIT_PATTERN})")

#: 全大写缩写（≥2 字母）；后面紧跟 "/" 的不拆（GB/T 等标准号前缀保持原样）
ABBREV_RE = re.compile(r"\b([A-Z]{2,6})\b(?!\s*/)")


@dataclass
class RuleOptions:
    """读法规则开关（FR-T-5 逐条可关）。"""

    standard: bool = True    # GB/T 24067 → GB/T 二四〇六七
    decimal: bool = True     # 2.0 → 二点零
    int_unit: bool = True    # 5000吨 → 五千吨
    abbrev: bool = True      # LCA → L C A
    #: 按单词读的缩写白名单（不拆字母）
    abbrev_whitelist: set[str] = field(default_factory=lambda: {"NASA", "LED", "USB", "AI"})


@dataclass
class Lexicon:
    """发音词典（有序替换表，长词优先）。"""

    entries: dict[str, str] = field(default_factory=dict)
    options: RuleOptions = field(default_factory=RuleOptions)

    def set_entry(self, src: str, dst: str) -> None:
        """新增/更新一条词典。"""
        if src:
            self.entries[src] = dst

    def remove_entry(self, src: str) -> None:
        """删除一条词典。"""
        self.entries.pop(src, None)

    def apply(self, text: str) -> str:
        """依次执行词典替换与读法规则，返回引擎用文本。"""
        out = self._apply_entries(text)
        out = self._apply_rules(out)
        return out

    def _apply_entries(self, text: str) -> str:
        for src in sorted(self.entries, key=len, reverse=True):
            dst = self.entries[src]
            if src and src in text:
                text = text.replace(src, dst)
        return text

    def _apply_rules(self, text: str) -> str:
        opt = self.options
        if opt.standard:
            text = STANDARD_RE.sub(lambda m: f"{m.group(1)} {digits_to_chinese(m.group(2))}", text)
        if opt.decimal:
            text = DECIMAL_RE.sub(lambda m: digits_to_chinese(m.group(1), zero_char="零"), text)
        if opt.int_unit:
            text = INT_UNIT_RE.sub(lambda m: f"{int_to_chinese(int(m.group(1)))}{m.group(2)}", text)
        if opt.abbrev:

            def abbrev_sub(m: re.Match) -> str:
                word = m.group(1)
                if word in opt.abbrev_whitelist:
                    return word
                return " ".join(word)

            text = ABBREV_RE.sub(abbrev_sub, text)
        return text

    # -- 持久化 ------------------------------------------------------------
    def to_dict(self) -> dict:
        """可 JSON 化的 dict。"""
        return {
            "entries": dict(self.entries),
            "options": {
                "standard": self.options.standard,
                "decimal": self.options.decimal,
                "int_unit": self.options.int_unit,
                "abbrev": self.options.abbrev,
                "abbrev_whitelist": sorted(self.options.abbrev_whitelist),
            },
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Lexicon":
        """从 dict 恢复。"""
        lex = cls()
        lex.entries = {str(k): str(v) for k, v in (d.get("entries") or {}).items()}
        o = d.get("options") or {}
        lex.options.standard = bool(o.get("standard", True))
        lex.options.decimal = bool(o.get("decimal", True))
        lex.options.int_unit = bool(o.get("int_unit", True))
        lex.options.abbrev = bool(o.get("abbrev", True))
        lex.options.abbrev_whitelist = set(o.get("abbrev_whitelist") or ["NASA", "LED", "USB", "AI"])
        return lex

    def save(self, path: str | Path) -> None:
        """保存为 JSON。"""
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Lexicon":
        """从 JSON 加载（文件缺失返回空词典）。"""
        p = Path(path)
        if not p.exists():
            return cls()
        try:
            return cls.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            return cls()

    @classmethod
    def import_csv(cls, path: str | Path) -> "Lexicon":
        """从 CSV 导入（列：原词,替换，UTF-8 或 GBK）。"""
        import csv

        raw = Path(path).read_bytes()
        for enc in ("utf-8-sig", "gbk", "utf-8"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = raw.decode("utf-8", errors="replace")
        lex = cls()
        for row in csv.reader(text.splitlines()):
            if len(row) >= 2 and row[0].strip() and not row[0].startswith("#"):
                lex.entries[row[0].strip()] = row[1].strip()
        return lex
