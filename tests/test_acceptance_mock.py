"""验收场景回归（用测试引擎代替 Edge-TTS，全流程走真实后处理/命名/清单/断点逻辑）。

对应需求文档 §9 验收 2/3/4/8：41 段批量、命名 01..41、规格统一无削顶、
改第 14 段只重做第 14 段、[pause=1.5] 精确停顿。
"""

from __future__ import annotations

import csv
import re

import numpy as np
import pytest
import soundfile as sf

from app.core.project import Project, Segment
from app.core.queue import JobQueue
from app.engines.mock import MockEngine


def _load_sample() -> list[Segment]:
    path = "示例数据/蚕茧课程41段.csv"
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    segs = []
    for r in rows:
        segs.append(Segment(i=int(r["序号"]), name=r.get("文件名", ""), text=r["文本"],
                            filename=""))
    return segs


def _project(segs: list[Segment]) -> Project:
    proj = Project(project="一枚蚕茧的低碳之旅", default_voice="mock:a")
    proj.preset = {
        "rate": -0.05, "pitch": 0.0, "volume": 0.0,
        "out_format": "mp3", "sample_rate": 44100, "bitrate": 128, "channels": 1,
        "normalize": "peak", "target_db": -3.0, "name_template": "{i:02d}.mp3",
    }
    proj.segments = segs
    return proj


@pytest.mark.slow
def test_full_41_segment_batch_and_redo_14(tmp_path):
    segs = _load_sample()
    assert len(segs) == 41
    proj = _project(segs)
    engine = MockEngine()
    q = JobQueue({"mock": engine}, fallback_to_sapi=False)

    # ① 全部生成
    summary = q.run(proj, segs, tmp_path)
    assert summary.done == 41 and summary.failed == 0
    for n in (1, 14, 41):
        assert (tmp_path / f"{n:02d}.mp3").exists(), f"{n:02d}.mp3 缺失"

    # ② 抽查规格：44.1kHz、单声道、峰值 ≤ −2.5dBFS、无满刻度样点（验收 3）
    for n in (1, 14, 41):
        data, sr = sf.read(tmp_path / f"{n:02d}.mp3", dtype="float32")
        assert sr == 44100
        assert data.ndim == 1
        peak_db = 20 * np.log10(max(float(np.max(np.abs(data))), 1e-9))
        assert peak_db <= -2.5
        assert int(np.sum(np.abs(data) >= 1.0)) == 0

    # ③ 清单与文件名同步（红线 6）
    rows = list(csv.reader(open(tmp_path / "清单.csv", encoding="utf-8-sig")))
    assert [r[1] for r in rows[1:]] == [f"{n:02d}.mp3" for n in range(1, 42)]

    # ④ 改第 14 段再全部生成 → 只重做第 14 段（验收 4）
    engine2 = MockEngine()
    q2 = JobQueue({"mock": engine2}, fallback_to_sapi=False)
    segs[13].text = segs[13].text + "（改过了）"
    summary2 = q2.run(proj, segs, tmp_path)
    assert summary2.done == 1 and summary2.skipped == 40
    assert len(engine2.calls) == 1
    assert "改过了" in engine2.calls[0]


@pytest.mark.slow
def test_sample_csv_has_pause_marker_gap(tmp_path):
    """示例第 1 段含 [pause=1.5]，生成后该处间隙 1.5±0.1s（验收 8）。"""
    segs = _load_sample()
    proj = _project([segs[0]])
    proj.preset["sample_rate"] = 44100
    q = JobQueue({"mock": MockEngine()}, fallback_to_sapi=False)
    q.run(proj, [segs[0]], tmp_path)
    data, sr = sf.read(tmp_path / "01.mp3", dtype="float32")
    active = np.nonzero(np.abs(data) > 0.02)[0]
    gaps = np.diff(active) / sr
    big = gaps[gaps > 0.3]
    assert big.size >= 1
    assert float(big.max()) == pytest.approx(1.5, abs=0.1)


def test_sample_csv_content_sanity():
    """示例 CSV 形态：41 段、含验收场景字符串。"""
    segs = _load_sample()
    assert len(segs) == 41
    whole = "".join(s.text for s in segs)
    assert "[pause=1.5]" in whole
    assert "2.0千克" in whole
    assert "GB/T 24067" in whole
    assert "LCA" in whole
    assert "缫丝" in whole
    chars = len(re.sub(r"\s|\[pause=[\d.]+\]", "", whole))
    assert chars > 2500
