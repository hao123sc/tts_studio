"""Qt 后台线程：音色加载、试听生成、批量生成（UI 不阻塞）。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ..core.log import get_logger, short
from ..core.project import Project, Segment
from ..core.queue import JobQueue, QueueEvent

log = get_logger()


class VoiceLoadWorker(QThread):
    """后台拉取各引擎音色清单（edge 动态拉取，失败用内置清单）。"""

    done = Signal(list, list)   # (音色列表, 警告列表)
    failed = Signal(str)

    def __init__(self, engines: dict, registry, parent=None) -> None:
        super().__init__(parent)
        self.engines = engines
        self.registry = registry

    def run(self) -> None:  # noqa: D102
        warnings: list[str] = []
        try:
            all_voices = []
            for name, eng in self.engines.items():
                try:
                    voices = eng.list_voices()
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"{name} 引擎音色加载失败：{exc}")
                    voices = []
                if voices:
                    self.registry.replace_engine_voices(name, voices)
                    all_voices.extend(voices)
                if getattr(eng, "used_builtin_fallback", False):
                    warnings.append("Edge-TTS 音色清单拉取失败，已使用内置清单兜底")
            self.done.emit(self.registry.list_voices(), warnings)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class PreviewWorker(QThread):
    """生成某音色的试听音频（带本地缓存，只生成一次——FR-V-3）。"""

    ready = Signal(str, str)    # (voice_id, 音频路径)
    failed = Signal(str, str)   # (voice_id, 错误)

    def __init__(self, engines: dict, registry, cache_dir: Path, voice_id: str, parent=None) -> None:
        super().__init__(parent)
        self.engines = engines
        self.registry = registry
        self.cache_dir = Path(cache_dir)
        self.voice_id = voice_id

    def run(self) -> None:  # noqa: D102
        import hashlib

        try:
            engine_name = self.voice_id.split(":", 1)[0]
            engine = self.engines.get(engine_name)
            if engine is None:
                raise RuntimeError(f"引擎 {engine_name} 未启用")
            key = hashlib.sha256(self.voice_id.encode("utf-8")).hexdigest()[:16]
            out = self.cache_dir / "preview" / f"{key}.mp3"
            out.parent.mkdir(parents=True, exist_ok=True)
            if not out.exists() or out.stat().st_size == 0:
                engine.preview(self.voice_id, str(out))
            self.registry.set_sample_path(self.voice_id, str(out))
            self.ready.emit(self.voice_id, str(out))
        except Exception as exc:  # noqa: BLE001
            log.warning("试听失败：%s | %s", self.voice_id, exc)
            self.failed.emit(self.voice_id, str(exc))


class GenerateWorker(QThread):
    """批量生成线程：包装 :class:`JobQueue`，把事件转成 Qt 信号。"""

    event = Signal(object)      # QueueEvent
    finished_ok = Signal(object)  # GenerationSummary

    def __init__(self, queue: JobQueue, project: Project, segments: list[Segment],
                 output_dir: Path, resume: bool, force: bool, parent=None) -> None:
        super().__init__(parent)
        self.queue = queue
        self.project = project
        self.segments = list(segments)
        self.output_dir = Path(output_dir)
        self.resume = resume
        self.force = force

    def run(self) -> None:  # noqa: D102
        def _cb(ev: QueueEvent) -> None:
            self.event.emit(ev)

        self.queue.on_event = _cb
        try:
            summary = self.queue.run(self.project, self.segments, self.output_dir,
                                     resume=self.resume, force=self.force)
        except Exception as exc:  # noqa: BLE001
            log.exception("生成线程异常")
            from ..core.queue import GenerationSummary
            from ..engines.base import EngineError

            summary = GenerationSummary()
            msg = exc.copyable() if isinstance(exc, EngineError) else f"{type(exc).__name__}: {exc}"
            self.event.emit(QueueEvent("warning", message=f"生成中断：{msg}"))
        self.finished_ok.emit(summary)
