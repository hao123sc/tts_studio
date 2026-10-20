"""Qt 后台线程：音色加载、试听生成、代理测试、批量生成（UI 不阻塞）。

线程生命周期约定：所有 QThread 实例在 ``finished`` 前由全局 :data:`_LIVE`
保活，防止被垃圾回收时打印 "QThread: Destroyed while thread is still running"。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ..core.log import get_logger, short
from ..core.project import Project, Segment
from ..core.queue import JobQueue, QueueEvent

log = get_logger()

#: 运行中线程保活集合（finished 后自动移除）
_LIVE: set = set()


def _keep(thread: QThread) -> QThread:
    """把线程登记进保活集合，finished 后释放。"""
    _LIVE.add(thread)

    def _release() -> None:
        _LIVE.discard(thread)

    thread.finished.connect(_release)
    return thread


class VoiceLoadWorker(QThread):
    """后台拉取各引擎音色清单（edge 动态拉取，失败用内置清单）。"""

    done = Signal(list, list)   # (音色列表, 警告列表)
    failed = Signal(str)

    def __init__(self, engines: dict, registry, parent=None) -> None:
        super().__init__(parent)
        self.engines = engines
        self.registry = registry
        _keep(self)

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
        _keep(self)

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
        _keep(self)

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


class ProxyTestWorker(QThread):
    """测试代理/网络连通性（两步，对应真实使用链路）：

    ① 拉取音色清单（list_voices）；
    ② **真实合成一小段音频**（synth）—— 试听/生成走的就是这一步。

    只测 ① 不够：曾出现"清单拉取成功但合成 403"（合成端点 IP 检查更严），
    所以必须两步都过才算可用。
    """

    done = Signal(bool, str)   # (是否全部成功, 中文说明)

    def __init__(self, proxy: str, parent=None) -> None:
        super().__init__(parent)
        self.proxy = proxy
        _keep(self)

    def run(self) -> None:  # noqa: D102
        from ..engines.base import TTSRequest
        from ..engines.edge import EdgeEngine

        src = f"通过代理 {self.proxy}" if self.proxy else "直连"
        eng = EdgeEngine(proxy=self.proxy, timeout=10.0)

        # ① 音色清单
        try:
            voices = eng.list_voices()
        except Exception as exc:  # noqa: BLE001
            self.done.emit(False, f"① 音色清单 ✗：{src}失败（{exc}）。请检查代理地址/端口。")
            return
        if eng.used_builtin_fallback:
            self.done.emit(False, f"① 音色清单 ✗：{src}连不上 Edge-TTS。请检查代理地址/端口，或换网络。")
            return

        # ② 真实合成（试听/生成同链路）
        try:
            with tempfile.TemporaryDirectory() as td:
                out = os.path.join(td, "proxy_test.mp3")
                eng.synth(TTSRequest(text="你好，测试。", voice_id="edge:zh-CN-YunxiNeural"), out)
                size = os.path.getsize(out)
            if size <= 0:
                raise RuntimeError("合成结果为空")
            self.done.emit(True, f"① 音色清单 ✓（{len(voices)} 个）；② 语音合成 ✓ —— 试听/生成可用。")
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if "403" in msg:
                hint = ("① 音色清单 ✓（%d 个）；② 语音合成 ✗：出口 IP 被微软拒绝（403）。\n"
                        "清单能拉到但合成被拒，通常是代理出口节点问题：请在代理软件里换一个"
                        "节点/地区（如换到日本、新加坡），再点「测试连接」。" % len(voices))
            else:
                hint = f"① 音色清单 ✓（{len(voices)} 个）；② 语音合成 ✗：{msg}"
            self.done.emit(False, hint)
