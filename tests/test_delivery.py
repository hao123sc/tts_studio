"""交付包导出测试（FR-S-5，红线 7 合规声明）。"""

from __future__ import annotations

import zipfile

from app.core.delivery import export_delivery_zip
from app.core.project import Project, Segment
from app.core.queue import JobQueue
from app.engines.mock import MockEngine


def test_delivery_zip_contents(tmp_path):
    out_dir = tmp_path / "out"
    segs = [
        Segment(i=1, name="开场", text="同学们好，今天讲一枚蚕茧。"),
        Segment(i=2, name="提问", text="老师，缫丝是什么呀？"),
    ]
    proj = Project(project="一枚蚕茧的低碳之旅", default_voice="mock:a")
    proj.preset = {"out_format": "wav", "sample_rate": 44100, "bitrate": 128, "channels": 1,
                   "normalize": "peak", "target_db": -3.0, "name_template": "{i:02d}.wav",
                   "rate": 0.0, "pitch": 0.0, "volume": 0.0}
    proj.segments = segs
    q = JobQueue({"mock": MockEngine()}, fallback_to_sapi=False)
    summary = q.run(proj, segs, out_dir)
    assert summary.done == 2

    zip_path = tmp_path / "交付包.zip"
    export_delivery_zip(proj, out_dir, zip_path)
    assert zip_path.exists()
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        assert "audio/01.wav" in names
        assert "audio/02.wav" in names
        assert "清单.csv" in names
        assert "参数.json" in names
        assert "README.txt" in names
        readme = zf.read("README.txt").decode("utf-8")
        # 红线 7：合规声明必须在交付说明里
        assert "AI 合成" in readme
        assert "本人或已获授权" in readme
        manifest = zf.read("清单.csv").decode("utf-8-sig")
        assert "序号,文件名,音色,字数,时长,文件路径" in manifest
        params = zf.read("参数.json").decode("utf-8")
        assert "一枚蚕茧的低碳之旅" in params
