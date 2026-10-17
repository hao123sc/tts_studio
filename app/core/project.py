"""项目文件 ``.dvproj``（FR-S-1/2）：段列表、音色、参数、输出目录、词典、预设。

* JSON 格式（UTF-8，``ensure_ascii=False``），版本号 ``version=1``；
* 自动保存：每 30 秒写一次；写入时把旧文件留作 ``.dvproj.bak``（崩溃恢复）；
* 每段带 ``hash``（断点续传）/``status``/``file``/``duration``，生成中强退后重启可识别。
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .lexicon import Lexicon

PROJECT_VERSION = 1

#: 段状态
STATUS_PENDING = "pending"
STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"


@dataclass
class Segment:
    """一段台词（项目中的最小单元）。"""

    i: int                                       # 序号（FR-T-1）
    name: str = ""                               # 备注/段名
    text: str = ""
    voice: str = ""                              # 目标音色 id（空 = 项目默认）
    params: dict = field(default_factory=dict)   # 覆盖参数 {rate/pitch/volume/style...}
    filename: str = ""                           # CSV 文件名列（覆盖命名模板）
    note: str = ""                               # 备注（与 name 同义，导入兼容）
    duration: float = 0.0                        # 实际时长（生成后回填）
    file: str = ""                               # 输出文件（相对 output_dir）
    hash: str = ""                               # 断点续传哈希
    status: str = STATUS_PENDING
    error: str = ""

    def display_name(self) -> str:
        """显示用段名（备注优先）。"""
        return self.name or self.note


@dataclass
class Project:
    """配音项目。"""

    project: str = "未命名项目"
    default_voice: str = ""
    preset: dict = field(default_factory=dict)   # 输出级参数（FR-P-6~11）
    segments: list[Segment] = field(default_factory=list)
    output_dir: str = "out"
    lexicon: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)    # 预留：P2 字段

    # -- 段操作 ------------------------------------------------------------
    def next_index(self) -> int:
        """下一个可用序号。"""
        return max((s.i for s in self.segments), default=0) + 1

    def find(self, i: int) -> Segment | None:
        """按序号查找段。"""
        for s in self.segments:
            if s.i == i:
                return s
        return None

    def renumber(self) -> None:
        """按当前顺序重排序号（上移/下移/删除后调用）。"""
        for n, s in enumerate(sorted(self.segments, key=lambda x: x.i), start=1):
            s.i = n

    def total_chars(self) -> int:
        """总字数。"""
        from .textproc import count_chars

        return sum(count_chars(s.text) for s in self.segments)

    def lexicon_obj(self) -> Lexicon:
        """项目词典对象。"""
        return Lexicon.from_dict(self.lexicon or {})

    # -- 序列化 ------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """转 JSON 兼容 dict。"""
        d = asdict(self)
        d["version"] = PROJECT_VERSION
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Project":
        """从 dict 恢复（向前兼容：未知字段进 extra）。"""
        proj = cls()
        proj.project = str(d.get("project") or "未命名项目")
        proj.default_voice = str(d.get("default_voice") or "")
        proj.preset = dict(d.get("preset") or {})
        proj.output_dir = str(d.get("output_dir") or "out")
        proj.lexicon = dict(d.get("lexicon") or {})
        known = {"version", "project", "default_voice", "preset", "segments", "output_dir", "lexicon"}
        proj.extra = {k: v for k, v in d.items() if k not in known}
        for sd in d.get("segments") or []:
            proj.segments.append(
                Segment(
                    i=int(sd.get("i") or 0),
                    name=str(sd.get("name") or ""),
                    text=str(sd.get("text") or ""),
                    voice=str(sd.get("voice") or ""),
                    params=dict(sd.get("params") or {}),
                    filename=str(sd.get("filename") or ""),
                    note=str(sd.get("note") or ""),
                    duration=float(sd.get("duration") or 0.0),
                    file=str(sd.get("file") or ""),
                    hash=str(sd.get("hash") or ""),
                    status=str(sd.get("status") or STATUS_PENDING),
                    error=str(sd.get("error") or ""),
                )
            )
        return proj


def save_project(path: str | Path, project: Project) -> None:
    """保存项目；旧文件备份为 ``.bak``（崩溃恢复用）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        shutil.copy2(p, p.with_suffix(p.suffix + ".bak"))
    tmp = p.with_suffix(p.suffix + ".part")
    tmp.write_text(json.dumps(project.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def load_project(path: str | Path) -> Project:
    """加载项目；主文件损坏时自动尝试 ``.bak``（FR-S-2 崩溃恢复）。

    返回 ``(Project)``。两份都损坏时抛 :class:`ValueError`。
    """
    p = Path(path)
    errors: list[str] = []
    for candidate in (p, p.with_suffix(p.suffix + ".bak")):
        if not candidate.exists():
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
            proj = Project.from_dict(data)
            if candidate != p:
                proj.extra["recovered_from_backup"] = True
            return proj
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            errors.append(f"{candidate.name}: {exc}")
    raise ValueError("项目文件无法读取：" + ("；".join(errors) or "文件不存在"))


def autosave_snapshot(path: str | Path, project: Project) -> None:
    """自动保存快照（与 :func:`save_project` 相同，语义化命名便于调用点识别）。"""
    save_project(path, project)
