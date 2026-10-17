"""命名模板测试（FR-P-11，红线 6）。"""

from __future__ import annotations

import pytest

from app.core.naming import ensure_extension, preview_names, render_filename, sanitize_name


def test_default_template_two_digit():
    assert render_filename("{i:02d}.mp3", i=1, out_format="mp3") == "01.mp3"
    assert render_filename("{i:02d}.mp3", i=41, out_format="mp3") == "41.mp3"


def test_placeholders():
    name = render_filename("{i:02d}_{name}_{voice}.mp3", i=3, name="从隋唐走来的产业",
                           voice="YunxiNeural", date="20261017", out_format="mp3")
    assert name == "03_从隋唐走来的产业_YunxiNeural.mp3"
    assert render_filename("{date}_{i}.wav", i=7, date="20261017", out_format="wav") == "20261017_7.wav"


def test_extension_corrected_by_format():
    """模板写死 .mp3 但输出 wav 时自动纠正扩展名（设计决策）。"""
    assert render_filename("{i:02d}.mp3", i=1, out_format="wav") == "01.wav"
    assert render_filename("{i:02d}", i=2, out_format="flac") == "02.flac"
    assert ensure_extension("a.mp3", "wav") == "a.wav"


def test_override_filename_from_csv():
    """CSV 文件名列优先于模板，但仍清洗并纠正扩展名。"""
    assert render_filename("{i:02d}.mp3", i=14, override="01_开场.mp3", out_format="mp3") == "01_开场.mp3"
    assert render_filename("{i:02d}.mp3", i=14, override="怪名/带:非法*字符", out_format="mp3") == "怪名_带_非法_字符.mp3"


def test_sanitize_windows_illegal():
    assert sanitize_name('a/b\\c:d*e?f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"
    assert sanitize_name("...") == "未命名"
    assert sanitize_name("CON") == "_CON"
    assert not sanitize_name("x  y").endswith(" ")


def test_bad_template_raises():
    with pytest.raises(ValueError):
        render_filename("{unknown}.mp3", i=1)
    with pytest.raises(ValueError):
        render_filename("{i:bad}.mp3", i=1)


def test_preview_names():
    names = preview_names("{i:02d}.mp3", 3)
    assert names == ["01.mp3", "02.mp3", "03.mp3"]
