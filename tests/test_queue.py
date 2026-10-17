"""生成队列集成测试（FR-G 全部、红线 4/5/6、验收 3/4/8）。"""

from __future__ import annotations

import csv
import wave

import numpy as np
import pytest
import soundfile as sf

from app.core.project import STATUS_FAILED, STATUS_OK, Project, Segment, load_project, save_project
from app.core.queue import JobQueue, build_params, write_manifest
from app.engines.mock import MockEngine


def _project(segments, **preset) -> Project:
    p = Project(project="测试项目", default_voice="mock:a")
    p.preset = {
        "rate": 0.0, "pitch": 0.0, "volume": 0.0,
        "out_format": "wav", "sample_rate": 44100, "bitrate": 128, "channels": 1,
        "normalize": "peak", "target_db": -3.0, "name_template": "{i:02d}.wav",
    }
    p.preset.update(preset)
    p.segments = segments
    return p


def _queue(engine=None, **kw) -> JobQueue:
    eng = engine or MockEngine()
    return JobQueue({"mock": eng, "sapi": MockEngine()}, fallback_to_sapi=False, **kw)


def test_basic_generate_and_naming(tmp_path):
    segs = [Segment(i=n, text=f"第{n}段台词内容测试") for n in range(1, 4)]
    proj = _project(segs)
    q = _queue()
    summary = q.run(proj, segs, tmp_path)
    assert summary.done == 3 and summary.failed == 0
    for n in (1, 2, 3):
        assert (tmp_path / f"{n:02d}.wav").exists()
    # 清单与文件名同步（红线 6）
    rows = list(csv.reader(open(tmp_path / "清单.csv", encoding="utf-8-sig")))
    assert rows[0] == ["序号", "文件名", "音色", "字数", "时长", "文件路径"]
    assert [r[1] for r in rows[1:]] == ["01.wav", "02.wav", "03.wav"]


def test_resume_skips_unchanged_redoes_changed(tmp_path):
    segs = [Segment(i=n, text=f"第{n}段") for n in range(1, 4)]
    proj = _project(segs)
    q = _queue()
    q.run(proj, segs, tmp_path)
    engine = MockEngine()
    q2 = _queue(engine)
    # 改第 2 段 → 只重做第 2 段（验收 4）
    segs[1].text = "第2段改过了"
    summary = q2.run(proj, segs, tmp_path)
    assert summary.skipped == 2 and summary.done == 1
    assert len(engine.calls) == 1
    assert "改过了" in engine.calls[0]


def test_force_regenerate_single(tmp_path):
    segs = [Segment(i=1, text="唯一一段")]
    proj = _project(segs)
    q = _queue()
    q.run(proj, segs, tmp_path)
    engine = MockEngine()
    q2 = _queue(engine)
    summary = q2.run(proj, segs, tmp_path, force=True)  # 只重做这一段
    assert summary.done == 1 and summary.skipped == 0
    assert len(engine.calls) == 1


def test_pause_gap_within_tolerance(tmp_path):
    """[pause=1.5] 处停顿 1.5±0.1 秒（验收 8）。"""
    segs = [Segment(i=1, text="前半段台词[pause=1.5]后半段台词")]
    proj = _project(segs)
    q = _queue()
    summary = q.run(proj, segs, tmp_path)
    assert summary.done == 1
    data, sr = sf.read(tmp_path / "01.wav", dtype="float32")
    active = np.nonzero(np.abs(data) > 0.02)[0]
    assert active.size > 10
    gaps = np.diff(active) / sr
    big = gaps[gaps > 0.3]
    assert big.size >= 1
    assert float(big.max()) == pytest.approx(1.5, abs=0.1), f"实际停顿 {big.max():.3f}s"


def test_single_failure_does_not_break_batch(tmp_path):
    """任意单条失败不影响其余（红线 5），错误中文可复制。"""
    engine = MockEngine(fail_ids={"坏段"})
    segs = [Segment(i=1, text="好段一"), Segment(i=2, text="这是坏段内容"), Segment(i=3, text="好段二")]
    proj = _project(segs)
    events = []
    q = _queue(engine, on_event=events.append)
    summary = q.run(proj, segs, tmp_path)
    assert summary.done == 2 and summary.failed == 1
    assert summary.failures[0][0] == 2
    assert "模拟失败" in summary.failures[0][1]
    assert (tmp_path / "01.wav").exists() and (tmp_path / "03.wav").exists()
    assert not (tmp_path / "02.wav").exists()
    assert segs[1].status == STATUS_FAILED and segs[1].error
    # 失败段不写 .part 残留
    assert not list(tmp_path.glob("*.part"))


def test_retry_twice_before_fail(tmp_path):
    class Flaky(MockEngine):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        def synth(self, req, out_path, on_progress=None):
            self.attempts += 1
            return super().synth(req, out_path, on_progress)

    engine = Flaky()
    engine.fail_ids = {"总是失败"}
    q = JobQueue({"mock": engine}, fallback_to_sapi=False, retry_times=2)
    segs = [Segment(i=1, text="总是失败")]
    summary = q.run(_project(segs), segs, tmp_path)
    assert summary.failed == 1
    assert engine.attempts == 3  # 1 次 + 2 次重试（FR-G-4）


def test_no_partial_files_atomic(tmp_path):
    """先写 .part 再改名：正常完成后无 .part，只有成品。"""
    segs = [Segment(i=1, text="原子写入测试")]
    q = _queue()
    q.run(_project(segs), segs, tmp_path)
    assert (tmp_path / "01.wav").exists()
    assert not list(tmp_path.glob("*.part"))
    assert not (tmp_path / ".tts_tmp").exists()


def test_mp3_output_peak_and_rate(tmp_path):
    segs = [Segment(i=1, text="峰值与采样率验收")]
    proj = _project(segs, out_format="mp3", name_template="{i:02d}.mp3", sample_rate=44100)
    q = _queue()
    q.run(proj, segs, tmp_path)
    data, sr = sf.read(tmp_path / "01.mp3", dtype="float32")
    assert sr == 44100
    assert 20 * np.log10(max(float(np.max(np.abs(data))), 1e-9)) <= -2.5
    assert int(np.sum(np.abs(data) >= 1.0)) == 0


def test_memory_bounded_41_items(tmp_path):
    """逐条释放：41 段批量后 RSS 增长有限（红线 4，目标常驻 <400MB）。"""
    import resource

    segs = [Segment(i=n, text="内存测试" * 40) for n in range(1, 42)]
    proj = _project(segs)
    q = _queue()
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    summary = q.run(proj, segs, tmp_path)
    after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    assert summary.done == 41
    # Linux ru_maxrss 单位 KB；增长 < 200MB（进程已含 numpy/scipy）
    assert (after - before) < 200 * 1024


def test_segment_filename_override(tmp_path):
    segs = [Segment(i=1, text="自定义文件名", filename="开场白")]
    proj = _project(segs)
    q = _queue()
    q.run(proj, segs, tmp_path)
    assert (tmp_path / "开场白.wav").exists()


def test_manifest_written_incrementally_with_failures(tmp_path):
    engine = MockEngine(fail_ids={"坏"})
    segs = [Segment(i=1, text="好的"), Segment(i=2, text="坏的")]
    proj = _project(segs)
    q = _queue(engine)
    q.run(proj, segs, tmp_path)
    rows = list(csv.reader(open(tmp_path / "清单.csv", encoding="utf-8-sig")))
    assert len(rows) == 3  # 表头 + 2 段（失败段也进清单，文件名为空）
    assert rows[1][1] == "01.wav"
    assert rows[2][1] == ""


def test_project_persist_and_recover(tmp_path):
    proj = _project([Segment(i=1, text="第一段", status=STATUS_OK, file="01.wav", hash="abc")])
    path = tmp_path / "demo.dvproj"
    save_project(path, proj)
    proj.project = "改过的名字"
    save_project(path, proj)  # 产生 .bak
    loaded = load_project(path)
    assert loaded.project == "改过的名字"
    # 主文件损坏 → 从 .bak 恢复（FR-S-2 崩溃恢复）
    path.write_text("{broken json", encoding="utf-8")
    recovered = load_project(path)
    assert recovered.segments[0].text == "第一段"
    assert recovered.extra.get("recovered_from_backup") is True
