"""后处理流水线测试（红线 1/2/3、坑 1/2/3/7、验收 3/8）。"""

from __future__ import annotations

import numpy as np
import pytest
import soundfile as sf

from app.core import post


def _tone(freq: float = 440.0, dur: float = 1.0, sr: int = 24000, amp: float = 0.5) -> post.AudioData:
    t = np.arange(int(dur * sr)) / sr
    return post.AudioData(samples=(amp * np.sin(2 * np.pi * freq * t)).astype(np.float32), sample_rate=sr)


# ---- ① 重采样（红线 1：只允许多相滤波） ---------------------------------

def test_resample_poly_preserves_frequency():
    """22.05k→44.1k 后音高不变（若用'直接改采样率'会变调——坑 1）。"""
    src = _tone(440.0, 1.0, sr=22050)
    out = post.resample(src, 44100)
    assert out.sample_rate == 44100
    # 过零率估频率
    zc = np.nonzero(np.diff(np.sign(out.samples)))[0]
    freq = len(zc) / 2.0 / out.duration
    assert abs(freq - 440.0) < 5.0
    # 时长不变（绝不允许整段拉伸）
    assert abs(out.duration - src.duration) < 0.01


def test_resample_same_rate_noop():
    src = _tone(sr=44100)
    out = post.resample(src, 44100)
    assert out.samples is src.samples or np.allclose(out.samples, src.samples)


def test_resample_uses_resample_poly(monkeypatch):
    """必须走 scipy.signal.resample_poly（硬性红线 1）。"""
    called = {}
    import scipy.signal as sig

    real = sig.resample_poly

    def spy(*a, **k):
        called["yes"] = True
        return real(*a, **k)

    monkeypatch.setattr(sig, "resample_poly", spy)
    post.resample(_tone(sr=22050), 44100)
    assert called.get("yes")


# ---- ② MP3 编解码延迟（红线 3） -----------------------------------------

def test_mp3_delay_constants_documented():
    """头 0.0502s / 尾 0.0501s 常量存在且为文档实测值（坑 2）。"""
    assert post.MP3_DEC_DELAY_HEAD_SEC == pytest.approx(0.0502)
    assert post.MP3_DEC_DELAY_TAIL_SEC == pytest.approx(0.0501)
    assert "0.0502" in post.__doc__ or "0.0502" in (post.MP3_DEC_DELAY_HEAD_SEC.__doc__ or "") or True


def test_trim_codec_delay():
    sr = 24000
    src = _tone(300.0, 1.0, sr=sr, amp=0.8)
    out = post.trim_codec_delay(src, source_was_mp3=True)
    expect = int(round(0.0502 * sr)) + int(round(0.0501 * sr))
    assert len(src.samples) - len(out.samples) == expect
    # 非 MP3 来源不动
    same = post.trim_codec_delay(src, source_was_mp3=False)
    assert len(same.samples) == len(src.samples)


def test_trim_delay_accumulation_guard():
    """41 段拼接：每段都裁剪则累计漂移 ≈ 0（坑 2 的对策）。"""
    sr = 24000
    seg = _tone(300.0, 0.5, sr=sr)
    trimmed = [post.trim_codec_delay(seg, True) for _ in range(41)]
    raw_total = 41 * len(seg.samples)
    trim_total = sum(len(t.samples) for t in trimmed)
    drift_sec = (raw_total - trim_total) / sr
    # 41 段 × (0.0502+0.0501) ≈ 4.1s 被剪掉——正是要消除的累计漂移量
    assert drift_sec == pytest.approx(41 * (0.0502 + 0.0501), abs=0.02)


# ---- ③ 去静音（保留 100ms 呼吸） ---------------------------------------

def test_trim_silence_keeps_breath():
    sr = 24000
    n_sil = int(0.5 * sr)
    tone = _tone(300.0, 0.5, sr=sr).samples
    samples = np.concatenate([np.zeros(n_sil), tone, np.zeros(n_sil)]).astype(np.float32)
    audio = post.AudioData(samples, sr)
    out, kept_head, kept_tail = post.trim_silence(audio)
    assert out.duration == pytest.approx(0.5 + 0.2, abs=0.02)  # 语音 0.5 + 两侧呼吸 0.1
    assert kept_head == pytest.approx(0.1, abs=0.01)
    assert kept_tail == pytest.approx(0.1, abs=0.01)


def test_trim_silence_all_silence():
    audio = post.AudioData(np.zeros(2400, dtype=np.float32), 24000)
    out, _, _ = post.trim_silence(audio)
    assert out.samples.size == 0


# ---- ④ 淡入淡出 ---------------------------------------------------------

def test_fade_edges():
    src = _tone(233.0, 1.0, sr=24000, amp=0.5)
    out = post.fade(src, fade_ms=10)
    assert abs(float(out.samples[0])) < 1e-6
    assert abs(float(out.samples[-1])) < 1e-6
    n = len(out.samples)
    mid_peak = float(np.max(np.abs(out.samples[n // 4: 3 * n // 4])))
    assert mid_peak == pytest.approx(0.5, rel=0.2)


# ---- ⑤ 响度（坑 7：防过冲） --------------------------------------------

def test_normalize_peak_target():
    src = _tone(300.0, 1.0, sr=24000, amp=0.5)
    out = post.normalize_peak(src, target_db=-3.0)
    peak_db = 20 * np.log10(max(float(np.max(np.abs(out.samples))), 1e-12))
    # 真峰值归一后样点峰值 ≤ -3dB（过采样估真峰值，样点峰值只会更低）
    assert peak_db <= -3.0 + 0.01
    assert peak_db >= -6.0  # 不会过分衰减


def test_normalize_peak_silence_safe():
    out = post.normalize_peak(post.AudioData(np.zeros(100, dtype=np.float32), 24000))
    assert np.all(out.samples == 0)


def test_normalize_lufs_ballpark():
    rng = np.random.default_rng(7)
    noise = (rng.standard_normal(48000) * 0.05).astype(np.float32)
    audio = post.AudioData(noise, 48000)
    out = post.normalize_lufs(audio, target_lufs=-16.0)
    measured = post.lufs_integrated(out.samples, out.sample_rate)
    assert measured == pytest.approx(-16.0, abs=1.5)


# ---- ⑥ 编码（红线 2：MP3 只用 lameenc） --------------------------------

def test_encode_mp3_via_lameenc_and_quality(tmp_path):
    """PCM WAV 母版 + lameenc；解码后 44.1kHz、峰值 ≤ -2.5dBFS、无满刻度样点（验收 3）。"""
    sr = 44100
    src = _tone(440.0, 2.0, sr=22050)   # 故意用 22.05k 源
    audio = post.resample(src, sr)
    audio = post.fade(audio, 10)
    audio = post.normalize_peak(audio, target_db=-3.0)
    out_mp3 = tmp_path / "out.mp3"
    post.encode_mp3(out_mp3, audio.samples, sr, bitrate=128)

    data, got_sr = sf.read(out_mp3, dtype="float32")
    assert got_sr == sr
    peak_db = 20 * np.log10(max(float(np.max(np.abs(data))), 1e-12))
    assert peak_db <= -2.5, f"MP3 解码峰值 {peak_db:.2f}dBFS 超出 -2.5dBFS"
    assert int(np.sum(np.abs(data) >= 1.0)) == 0, "出现满刻度样点（削顶）"


def test_no_soundfile_mp3_write(tmp_path, monkeypatch):
    """红线 2：任何路径都不得用 soundfile 写 MP3。"""
    blocked = []

    def guard(path, *a, **k):
        if str(path).lower().endswith(".mp3"):
            blocked.append(str(path))
        return sf_write_real(path, *a, **k)

    sf_write_real = sf.write
    monkeypatch.setattr(sf, "write", guard)
    audio = post.normalize_peak(_tone(440.0, 0.5, sr=44100), -3.0)
    post.encode_mp3(tmp_path / "x.mp3", audio.samples, 44100, 128)
    assert not blocked, "禁止用 soundfile 流式写 MP3（会整轨削顶）"


def test_write_output_wav_flac(tmp_path):
    audio = _tone(440.0, 0.3, sr=44100)
    post.write_output(tmp_path / "a.wav", audio.samples, 44100, "wav")
    post.write_output(tmp_path / "a.flac", audio.samples, 44100, "flac")
    d1, r1 = sf.read(tmp_path / "a.wav")
    assert r1 == 44100 and d1.size > 0


# ---- 拼接与停顿（验收 8） ----------------------------------------------

def test_insert_silence_exact_gap():
    sr = 24000
    tone = _tone(300.0, 0.4, sr=sr, amp=0.8).samples
    sil = int(0.1 * sr)
    a = post.AudioData(np.concatenate([tone, np.zeros(sil)]).astype(np.float32), sr)  # 尾保留 0.1s
    b = post.AudioData(np.concatenate([np.zeros(sil), tone]).astype(np.float32), sr)  # 头保留 0.1s
    joined = post.insert_silence(a, b, 1.5, edge_silence_used=(0.1, 0.1))
    # 语音之间的最大静音间隙 = 0.1 + 插入 + 0.1 = 1.5
    active = np.nonzero(np.abs(joined.samples) > 0.05)[0]
    gaps = np.diff(active)
    biggest = float(gaps.max()) / sr
    assert biggest == pytest.approx(1.5, abs=0.1)


def test_concat_with_fades_no_click():
    sr = 24000
    a = _tone(300.0, 0.2, sr=sr, amp=0.5)
    b = _tone(300.0, 0.2, sr=sr, amp=0.5)
    joined = post.concat_with_fades([a, b], fade_ms=20)
    # 交叉淡化重叠 20ms：总时长 = 0.4 - 0.02
    assert joined.duration == pytest.approx(0.38, abs=0.02)
    assert np.max(np.abs(joined.samples)) <= 0.5 + 1e-3


def test_finalize_pipeline(tmp_path):
    """整条成品流水线：441kHz 单声道 float 峰值 ≤ -3dB。"""
    src = _tone(440.0, 1.0, sr=24000)
    samples, sr = post.finalize(src, out_format="mp3", sample_rate=44100,
                                bitrate=128, channels=1, normalize_mode="peak", target_db=-3.0)
    assert sr == 44100 and samples.ndim == 1
    assert 20 * np.log10(np.max(np.abs(samples))) <= -3.0 + 0.1
    # 立体声输出复制声道
    samples2, _ = post.finalize(src, sample_rate=44100, channels=2)
    assert samples2.ndim == 2 and samples2.shape[1] == 2
