"""文本处理测试：停顿标记（FR-T-3，验收 8）、校验（FR-T-7）、拆句（FR-T-8）。"""

from __future__ import annotations

from app.core.textproc import (
    count_chars,
    estimate_duration,
    parse_pauses,
    split_sentences,
    strip_pauses,
    validate_text,
)


def test_parse_pause_bracket_forms():
    for text in ("前[pause=1.5]后", "前[pause=1.5s]后", "前[[停顿1.5s]]后", "前[[停顿1.5]]后"):
        pieces, pauses = parse_pauses(text)
        assert len(pieces) == 2, text
        assert pieces[0].text == "前" and pieces[1].text == "后", text
        assert pieces[0].pause_after == 1.5, text
        assert pauses == [(1, 1.5)], text


def test_parse_pause_positions():
    pieces, pauses = parse_pauses("abc[pause=2]def")
    assert pauses[0][0] == 3
    assert "".join(p.text for p in pieces) == "abcdef"


def test_multiple_and_leading_pauses():
    pieces, _ = parse_pauses("[pause=0.5]甲[pause=1][pause=2]乙")
    assert pieces[0].text == "" and pieces[0].pause_after == 0.5
    assert pieces[1].text == "甲" and pieces[1].pause_after == 1
    assert pieces[2].pause_after == 2
    assert pieces[3].text == "乙"


def test_count_chars_ignores_markers_and_space():
    assert count_chars("[pause=1.5]你好 世界") == 4


def test_estimate_duration_includes_pause():
    est = estimate_duration("一二三四[pause=1.5]五六七八", chars_per_sec=4.2)
    assert est == 0 + 8 / 4.2 + 1.5


def test_strip_pauses():
    assert strip_pauses("甲[pause=1.5]乙[[停顿2s]]丙") == "甲乙丙"


def test_split_sentences_respects_max():
    text = "第一句。第二句！第三句？第四句；"
    chunks = split_sentences(text, max_chars=10)
    assert all(len(c) <= 10 for c in chunks)
    assert "".join(chunks).replace("\n", "") == text


def test_split_sentences_short_passthrough():
    assert split_sentences("短文本。", max_chars=100) == ["短文本。"]


def test_validate_warnings():
    assert validate_text("正常文本。") == []
    assert any("空" in w for w in validate_text("   "))
    assert any("未闭合" in w for w in validate_text("（只有开"))
    assert any("配对" in w for w in validate_text("只有关）"))
    assert any("上限" in w for w in validate_text("啊" * 1300))
