"""断点续传哈希测试（FR-G-3）。"""

from __future__ import annotations

from app.core.hashing import segment_hash


def test_hash_stable():
    p = {"rate": 0.0, "pitch": 0.0, "volume": 0.0}
    h1 = segment_hash("同学们好", "edge:zh-CN-YunxiNeural", p)
    h2 = segment_hash("同学们好", "edge:zh-CN-YunxiNeural", dict(p))
    assert h1 == h2
    assert len(h1) == 64


def test_hash_key_order_independent():
    a = segment_hash("文本", "v", {"rate": 0.1, "pitch": 2.0})
    b = segment_hash("文本", "v", {"pitch": 2.0, "rate": 0.1})
    assert a == b


def test_hash_changes_on_text_or_params():
    p = {"rate": 0.0}
    base = segment_hash("第14段", "voice", p)
    assert segment_hash("第14段改了", "voice", p) != base
    assert segment_hash("第14段", "other-voice", p) != base
    assert segment_hash("第14段", "voice", {"rate": -0.05}) != base


def test_hash_lexicon_participates():
    p = {"rate": 0.0}
    base = segment_hash("缫丝", "voice", p, {"缫丝": "骚丝"})
    assert segment_hash("缫丝", "voice", p, {}) != base
    assert segment_hash("缫丝", "voice", p, {"缫丝": "骚丝"}) == base
