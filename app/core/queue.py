"""生成队列（FR-G-1~7）：单条/全部/仅选中/仅失败，串行执行、断点续传、失败重试、
原子写入（.part→改名）、逐条释放内存（红线 4）、单条失败不打断整批（红线 5）。

时间轴处理顺序（与需求文档 §3.2 一致）：

1. 文本预处理：停顿标记解析 → 词典/读法规则 → 超长按句切分；
2. 逐块调引擎合成 → 解码 → ①重采样 → ②剪 MP3 延迟 → ③去静音（保留 100ms）；
3. 块间拼接：停顿处精确插静音（补偿已保留边缘静音）、长句切分处 20ms 交叉淡化；
4. 成品：④淡入淡出 → ⑤响度归一 → 声道映射 → ⑥编码（lameenc）→ .part 改名；
5. 回写项目（hash/status/file/duration）与清单.csv。
"""

from __future__ import annotations

import csv
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

import numpy as np

from ..engines.base import (
    Engine,
    EngineError,
    EngineNetworkError,
    SynthResult,
    TTSRequest,
    Voice,
    voice_short_name,
)
from . import post
from .hashing import segment_hash
from .lexicon import Lexicon
from .log import get_logger, short
from .naming import render_filename
from .project import (
    STATUS_FAILED,
    STATUS_OK,
    STATUS_SKIPPED,
    Project,
    Segment,
)
from .textproc import count_chars, parse_pauses, split_sentences

log = get_logger()

#: 失败自动重试次数（FR-G-4）
DEFAULT_RETRY = 2
#: 重试指数退避基数（秒）
RETRY_BACKOFF_SEC = 0.5


class CancelledError(Exception):
    """用户取消。"""


@dataclass
class QueueEvent:
    """队列事件（UI 通过回调接收）。"""

    type: Literal["item_start", "item_done", "item_failed", "item_skipped", "warning", "log", "finished"]
    index: int = 0
    total: int = 0
    message: str = ""
    data: dict = field(default_factory=dict)


@dataclass
class ItemResult:
    """单条生成结果。"""

    index: int
    status: str
    file: str = ""
    duration: float = 0.0
    hash: str = ""
    error: str = ""
    warnings: list[str] = field(default_factory=list)
    skipped: bool = False


@dataclass
class GenerationSummary:
    """生成汇总（FR-G-6）。"""

    total: int = 0
    done: int = 0
    skipped: int = 0
    failed: int = 0
    total_duration: float = 0.0
    total_bytes: int = 0
    elapsed: float = 0.0
    failures: list[tuple[int, str]] = field(default_factory=list)  # (序号, 可复制错误)
    results: list[ItemResult] = field(default_factory=list)


def build_params(project: Project, segment: Segment) -> dict[str, Any]:
    """合并段参数：项目 preset 为底，段 params 覆盖。"""
    params: dict[str, Any] = {
        "rate": 0.0, "pitch": 0.0, "volume": 0.0, "style": "", "style_degree": 1.0,
        "out_format": "mp3", "sample_rate": 44100, "bitrate": 128, "channels": 1,
        "normalize": "peak", "target_db": -3.0, "name_template": "{i:02d}.mp3",
    }
    for key in list(params):
        if key in (project.preset or {}):
            params[key] = project.preset[key]
    for key, val in (segment.params or {}).items():
        params[key] = val
    return params


def manifest_rows(project: Project, output_dir: Path) -> list[list[str]]:
    """生成清单行（序号/文件名/音色/字数/时长/文件路径，FR-G-7）。"""
    rows: list[list[str]] = []
    for seg in sorted(project.segments, key=lambda s: s.i):
        rows.append([
            str(seg.i),
            Path(seg.file).name if seg.file else "",
            seg.voice or project.default_voice,
            str(count_chars(seg.text)),
            f"{seg.duration:.2f}" if seg.duration else "",
            str((output_dir / seg.file) if seg.file else ""),
        ])
    return rows


def write_manifest(output_dir: Path, project: Project) -> Path:
    """写出清单.csv（UTF-8 BOM，Excel 直接打开不乱码；红线 6：与文件名同步）。"""
    out = output_dir / "清单.csv"
    output_dir.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["序号", "文件名", "音色", "字数", "时长", "文件路径"])
        w.writerows(manifest_rows(project, output_dir))
    return out


class JobQueue:
    """生成队列。串行（默认并发 1）；支持取消、重试、降级、断点续传。"""

    def __init__(
        self,
        engines: dict[str, Engine],
        *,
        fallback_to_sapi: bool = True,
        retry_times: int = DEFAULT_RETRY,
        on_event: Callable[[QueueEvent], None] | None = None,
    ) -> None:
        self.engines = engines
        self.fallback_to_sapi = fallback_to_sapi
        self.retry_times = max(0, int(retry_times))
        self.on_event = on_event
        self.cancelled = False
        self._sapi_fallback_voice: str | None = None  # 非空 = 已降级

    # -- 对外入口 ----------------------------------------------------------
    def cancel(self) -> None:
        """请求取消（生成中可取消，不产生半截文件——FR-G-5）。"""
        self.cancelled = True

    def run(
        self,
        project: Project,
        segments: list[Segment],
        output_dir: str | Path,
        *,
        resume: bool = True,
        force: bool = False,
    ) -> GenerationSummary:
        """生成指定段集合。

        :param resume: 断点续传——文本/参数未变（哈希一致）且文件存在则跳过
        :param force: 忽略哈希强制重做（"只重做这一段"用）
        """
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        tmp_dir = out_dir / ".tts_tmp"
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
        tmp_dir.mkdir(parents=True, exist_ok=True)

        summary = GenerationSummary(total=len(segments))
        t0 = time.monotonic()
        lexicon = project.lexicon_obj()
        try:
            for pos, seg in enumerate(sorted(segments, key=lambda s: s.i)):
                if self.cancelled:
                    break
                params = build_params(project, seg)
                voice_id = seg.voice or project.default_voice
                text = seg.text
                h = segment_hash(text, voice_id, params, lexicon.entries)
                out_name = render_filename(
                    params["name_template"], i=seg.i, name=seg.display_name(),
                    voice=voice_short_name(voice_id), out_format=str(params["out_format"]),
                    override=seg.filename,
                )
                out_path = out_dir / out_name

                self._emit(QueueEvent("item_start", index=seg.i, total=len(segments),
                                      message=f"开始生成第 {seg.i} 段", data={"pos": pos}))
                # 断点续传（FR-G-3）
                if resume and not force and seg.hash == h and out_path.exists() and seg.status == STATUS_OK:
                    res = ItemResult(index=seg.i, status=STATUS_SKIPPED, file=out_name,
                                     duration=seg.duration, hash=h, skipped=True)
                    summary.skipped += 1
                    summary.results.append(res)
                    self._emit(QueueEvent("item_skipped", index=seg.i, total=len(segments),
                                          message=f"第 {seg.i} 段未变化，跳过", data={"file": out_name}))
                    self._write_manifest_safe(out_dir, project)
                    continue

                t_item = time.monotonic()
                try:
                    result = self._generate_one(
                        project, seg, params, voice_id, text, lexicon,
                        out_dir, out_path, out_name, h, tmp_dir,
                    )
                except CancelledError:
                    self._cleanup_part(out_path)
                    raise
                except Exception as exc:  # noqa: BLE001 —— 单条失败不打断整批（红线 5）
                    err = exc.copyable() if isinstance(exc, EngineError) else f"{type(exc).__name__}: {exc}"
                    seg.status = STATUS_FAILED
                    seg.error = err
                    seg.hash = ""
                    summary.failed += 1
                    summary.failures.append((seg.i, err))
                    summary.results.append(ItemResult(index=seg.i, status=STATUS_FAILED, error=err))
                    log.error("第 %s 段失败：%s | 文本：%s", seg.i, err, short(text))
                    self._emit(QueueEvent("item_failed", index=seg.i, total=len(segments),
                                          message=f"第 {seg.i} 段失败：{err}",
                                          data={"error": err, "copyable": err}))
                    self._write_manifest_safe(out_dir, project)
                    continue

                result.hash = h
                summary.done += 1
                summary.total_duration += result.duration
                summary.results.append(result)
                elapsed_item = time.monotonic() - t_item
                log.info("第 %s 段完成 %.2fs 用时 %.1fs | 文本：%s", seg.i, result.duration, elapsed_item, short(text))
                self._emit(QueueEvent("item_done", index=seg.i, total=len(segments),
                                      message=f"第 {seg.i} 段完成",
                                      data={"file": result.file, "duration": result.duration,
                                            "warnings": result.warnings, "pos": pos,
                                            "elapsed": elapsed_item}))
                self._write_manifest_safe(out_dir, project)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        summary.elapsed = time.monotonic() - t0
        for res in summary.results:
            if res.status == STATUS_OK and res.file:
                try:
                    summary.total_bytes += (out_dir / res.file).stat().st_size
                except OSError:
                    pass
        self._emit(QueueEvent("finished", total=summary.total,
                              message=self._summary_text(summary), data={"summary": summary}))
        return summary

    @staticmethod
    def _summary_text(s: GenerationSummary) -> str:
        """汇总的中文描述。"""
        lines = [
            f"生成完成：成功 {s.done} 条，跳过 {s.skipped} 条，失败 {s.failed} 条（共 {s.total} 条）",
            f"音频总时长 {s.total_duration / 60:.1f} 分钟，总大小 {s.total_bytes / 1024 / 1024:.1f} MB，"
            f"耗时 {s.elapsed / 60:.1f} 分钟",
        ]
        if s.failures:
            lines.append("异常清单（可复制）：")
            lines.extend(f"  第 {i} 段：{err}" for i, err in s.failures)
        return "\n".join(lines)

    # -- 单条生成 ----------------------------------------------------------
    def _generate_one(
        self,
        project: Project,
        seg: Segment,
        params: dict,
        voice_id: str,
        text: str,
        lexicon: Lexicon,
        out_dir: Path,
        out_path: Path,
        out_name: str,
        h: str,
        tmp_dir: Path,
    ) -> ItemResult:
        """合成一段：文本处理 → 逐块合成清洗 → 拼接 → 成品编码 → 原子落盘。"""
        pieces, pauses = parse_pauses(text)
        if not any(p.text.strip() for p in pieces):
            raise EngineError("文本为空（只有停顿标记），无法合成")

        engine_name = voice_id.split(":", 1)[0] if ":" in voice_id else "edge"
        engine = self._engine_for(voice_id, engine_name)
        caps = engine.capabilities()
        max_chars = int(caps.get("max_chars") or 1200)

        piece_audios: list[tuple[post.AudioData, float, float]] = []
        all_warnings: list[str] = []
        for pi, piece in enumerate(pieces):
            if self.cancelled:
                raise CancelledError()
            if not piece.text.strip():
                # 纯停顿块（开头/连续标记）：只贡献停顿秒数，不产生音频
                piece_audios.append((post.AudioData(np.zeros(0, dtype=np.float32), int(params["sample_rate"])), 0.0, 0.0))
                continue
            processed = lexicon.apply(piece.text)
            chunks = split_sentences(processed, max_chars=max_chars)
            chunk_audios: list[post.AudioData] = []
            kept_edges: list[tuple[float, float]] = []
            # 引擎原始格式后缀（edge=mp3，sapi/mock=wav）
            raw_ext = str((caps.get("formats") or ["wav"])[0])
            for ci, chunk in enumerate(chunks):
                target = tmp_dir / f"raw_{seg.i}_{pi}_{ci}.{raw_ext}"
                req = self._make_request(chunk, voice_id, params, pauses, lexicon)
                result = self._synth_with_retry(engine, req, str(target), voice_id)
                all_warnings.extend(result.warnings)
                audio, was_mp3 = post.decode_audio_file(target)
                audio = post.resample(audio, int(params["sample_rate"]))          # ①
                audio = post.trim_codec_delay(audio, was_mp3)                     # ②
                audio, kept_head, kept_tail = post.trim_silence(audio)            # ③
                chunk_audios.append(audio)
                kept_edges.append((kept_head, kept_tail))
                try:
                    target.unlink(missing_ok=True)
                except OSError:
                    pass
                if self.cancelled:
                    raise CancelledError()
            # 同一块内（长句切分）边界 20ms 交叉淡化
            if len(chunk_audios) > 1:
                joined = post.concat_with_fades(chunk_audios)
                head, tail = kept_edges[0][0], kept_edges[-1][1]
            elif chunk_audios:
                joined = chunk_audios[0]
                head, tail = kept_edges[0]
            else:
                joined = post.AudioData(np.zeros(0, dtype=np.float32), int(params["sample_rate"]))
                head = tail = 0.0
            piece_audios.append((joined, head, tail))
            del chunk_audios

        # 拼接整段：块间停顿 = 前块 pause_after + 中间纯停顿块之和；
        # 扣除两边已保留的边缘静音，使语音间隙恰为标记秒数（验收 8：±0.1s）
        final_audio: post.AudioData | None = None
        pending = 0.0        # 语音块之间欠的静音秒数
        prev_tail = 0.0
        rate = int(params["sample_rate"])
        for (audio, head, tail), piece in zip(piece_audios, pieces):
            if final_audio is None and audio.samples.size == 0:
                pending += piece.pause_after
                continue
            if final_audio is None:
                gap = max(0.0, pending - head)
                lead = np.zeros(int(round(gap * rate)), dtype=np.float32)
                final_audio = post.AudioData(np.concatenate([lead, audio.samples]), rate)
            else:
                gap = max(0.0, pending - prev_tail - head)
                mid = np.zeros(int(round(gap * rate)), dtype=np.float32)
                final_audio = post.AudioData(
                    np.concatenate([final_audio.samples, mid, audio.samples]), rate
                )
            pending = piece.pause_after
            prev_tail = tail
        if final_audio is None or final_audio.samples.size == 0:
            raise EngineError("合成结果为空音频")

        # ④⑤⑥ 成品
        samples, sr = post.finalize(
            final_audio,
            out_format=params["out_format"],
            sample_rate=int(params["sample_rate"]),
            bitrate=int(params["bitrate"]),
            channels=int(params["channels"]),
            normalize_mode=params["normalize"],
            target_db=float(params["target_db"]),
        )
        part_path = Path(str(out_path) + ".part")
        try:
            post.write_output(part_path, samples, sr, out_format=str(params["out_format"]),
                              bitrate=int(params["bitrate"]))
            os.replace(part_path, out_path)  # 先写 .part 再改名（FR-G-5）
        finally:
            if part_path.exists():
                try:
                    part_path.unlink()
                except OSError:
                    pass

        duration = float(samples.shape[0]) / float(sr)
        # 回写项目（断点续传/崩溃可识别）
        seg.hash = h
        seg.status = STATUS_OK
        seg.error = ""
        seg.file = out_name
        seg.duration = duration
        return ItemResult(index=seg.i, status=STATUS_OK, file=out_name,
                          duration=duration, hash=h, warnings=all_warnings)

    def _make_request(self, text: str, voice_id: str, params: dict,
                      pauses: list[tuple[int, float]], lexicon: Lexicon) -> TTSRequest:
        """构建引擎请求。"""
        return TTSRequest(
            text=text,
            voice_id=voice_id,
            rate=float(params.get("rate", 0.0)),
            pitch=float(params.get("pitch", 0.0)),
            volume=float(params.get("volume", 0.0)),
            style=str(params.get("style", "")),
            style_degree=float(params.get("style_degree", 1.0)),
            pauses=list(pauses),
            out_format=params.get("out_format", "mp3"),
            sample_rate=int(params.get("sample_rate", 44100)),
            bitrate=int(params.get("bitrate", 128)),
            normalize=params.get("normalize", "peak"),
            target_db=float(params.get("target_db", -3.0)),
            lexicon=dict(lexicon.entries),
        )

    def _synth_with_retry(self, engine: Engine, req: TTSRequest, out_path: str, voice_id: str) -> SynthResult:
        """带重试的合成；网络类错误在重试耗尽后触发 SAPI 降级（FR 稳定性 10）。"""
        last_exc: Exception | None = None
        attempts = self.retry_times + 1
        for attempt in range(attempts):
            if self.cancelled:
                raise CancelledError()
            try:
                return engine.synth(req, out_path)
            except CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                is_net = isinstance(exc, EngineNetworkError)
                log.warning("合成失败（第 %s/%s 次）：%s | 文本：%s",
                            attempt + 1, attempts, exc, short(req.text))
                if attempt < attempts - 1:
                    time.sleep(RETRY_BACKOFF_SEC * (2 ** attempt))
                    continue
                if is_net and self.fallback_to_sapi and self.engines.get("sapi"):
                    return self._fallback_sapi(req, out_path, voice_id, exc)
                break
        assert last_exc is not None
        raise last_exc

    def _fallback_sapi(self, req: TTSRequest, out_path: str, voice_id: str, cause: Exception) -> SynthResult:
        """网络失败自动降级 SAPI，并给出中文提示。"""
        sapi = self.engines["sapi"]
        if self._sapi_fallback_voice is None:
            candidates = sapi.list_voices()
            mapped = SapiEngine_map_voice(voice_id, candidates)
            self._sapi_fallback_voice = mapped
            msg = ("无法连接 Edge-TTS（网络问题），已自动降级为系统 SAPI 离线音色，"
                   "音质可能下降；请检查网络后重新生成受影响段落。")
            log.warning("%s 原因：%s", msg, cause)
            self._emit(QueueEvent("warning", message=msg,
                                  data={"detail": f"{type(cause).__name__}: {cause}"}))
        req2 = TTSRequest(
            text=req.text, voice_id=self._sapi_fallback_voice or req.voice_id,
            rate=req.rate, pitch=0.0, volume=0.0, style="", style_degree=1.0,
            pauses=req.pauses, out_format=req.out_format,
            sample_rate=req.sample_rate, bitrate=req.bitrate,
            normalize=req.normalize, target_db=req.target_db, lexicon=req.lexicon,
        )
        return sapi.synth(req2, out_path)

    def _engine_for(self, voice_id: str, engine_name: str) -> Engine:
        """取引擎；未知引擎给出中文错误。"""
        eng = self.engines.get(engine_name)
        if eng is None:
            raise EngineError(f"音色 {voice_id} 所属引擎「{engine_name}」未启用")
        return eng

    # -- 工具 --------------------------------------------------------------
    def _emit(self, event: QueueEvent) -> None:
        """发事件。"""
        if self.on_event:
            try:
                self.on_event(event)
            except Exception:  # noqa: BLE001 —— UI 回调异常不影响生成
                log.exception("队列事件回调异常")

    @staticmethod
    def _cleanup_part(out_path: Path) -> None:
        """清理 .part 半截文件。"""
        part = Path(str(out_path) + ".part")
        if part.exists():
            try:
                part.unlink()
            except OSError:
                pass

    def _write_manifest_safe(self, out_dir: Path, project: Project) -> None:
        """增量写清单（崩溃后清单与文件仍对应，红线 6）。"""
        try:
            write_manifest(out_dir, project)
        except OSError as exc:
            log.warning("写清单失败：%s", exc)


def SapiEngine_map_voice(edge_voice_id: str, candidates: list[Voice]) -> str:
    """edge→sapi 音色映射（性别就近）。"""
    from ..engines.sapi import SapiEngine

    return SapiEngine.map_voice_id(edge_voice_id, candidates)
