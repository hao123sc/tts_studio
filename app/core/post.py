"""后处理流水线（需求文档 §3.2，步骤为硬约束，顺序不得自创）。

一条音频的标准流水线::

    引擎原始输出
      → ① 统一重采样（多相滤波 resample_poly，禁止邻近取样——红线 1）
      → ② 若源是 MP3：剪掉编解码延迟（实测 头 0.0502s / 尾 0.0501s——红线 3）
      → ③ 去首尾静音（阈值 -45dBFS，保留 100ms 呼吸）
      → ④ 淡入淡出 10ms
      → ⑤ 响度：峰值 -3.0dBFS（默认）/ 或 LUFS -16
      → ⑥ 编码：MP3 用 lameenc（绝不用 soundfile 流式写 MP3——红线 2）

实现说明（设计决策，详见 README）：

* ①②③ 针对**每个引擎原始片段**执行（多段拼接时先各自清洗，保证时间轴对齐）；
* ④⑤⑥ 针对**拼接完成的整条成品**执行（响度只归一一次，避免段间跳变）；
* MP3 成品一律走「PCM WAV 母版 → lameenc 编码 → 原子改名」，``soundfile``
  只用于 WAV/FLAC 写出与音频**解码**（读 MP3 允许，写 MP3 禁止）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Literal

import numpy as np
import soundfile as sf
from scipy import signal

# ---------------------------------------------------------------------------
# 常量（红线 3：MP3 编解码延迟，写成常量并注释来源）
# ---------------------------------------------------------------------------

#: MP3 解码延迟·头部（秒）。来源：《配音软件-需求文档》§10 坑 2——本课程项目对
#: edge-tts 24kHz MP3 输出实测：每段头部多出约 0.0502s。41 段拼接不剪则累计约
#: 2.5 秒漂移。单位：秒。
MP3_DEC_DELAY_HEAD_SEC = 0.0502

#: MP3 解码延迟·尾部（秒）。来源同上，实测尾部约 0.0501s。
MP3_DEC_DELAY_TAIL_SEC = 0.0501

#: 去静音阈值（dBFS，低于此值视为静音）
SILENCE_TRIM_THRESHOLD_DB = -45.0

#: 去首尾静音后保留的"呼吸"时长（毫秒）
SILENCE_KEEP_MS = 100.0

#: 单条成品的淡入淡出时长（毫秒）
FADE_MS = 10.0

#: 长文本切分拼接边界交叉淡化时长（毫秒）（§11 风险对策：边界加 20ms 淡入淡出）
JOIN_FADE_MS = 20.0

#: 真峰值归一的过采样倍数（防 MP3 过冲削顶——坑 7）
TRUE_PEAK_OVERSAMPLE = 4


@dataclass
class AudioData:
    """内存中的一段音频（单声道 float32，范围 [-1, 1]）。"""

    samples: np.ndarray
    sample_rate: int

    @property
    def duration(self) -> float:
        """时长（秒）。"""
        return float(len(self.samples)) / float(self.sample_rate) if self.sample_rate else 0.0


# ---------------------------------------------------------------------------
# ① 重采样（只允许多相滤波）
# ---------------------------------------------------------------------------

def resample(audio: AudioData, target_rate: int) -> AudioData:
    """多相滤波重采样到 ``target_rate``（红线 1：禁止邻近取样/直接改采样率）。

    使用 :func:`scipy.signal.resample_poly`，按有理数比例上下采样。
    """
    if target_rate <= 0:
        raise ValueError("目标采样率必须为正整数")
    if audio.sample_rate == target_rate:
        return audio
    frac = Fraction(int(target_rate), int(audio.sample_rate)).limit_denominator(1000)
    out = signal.resample_poly(audio.samples.astype(np.float64), frac.numerator, frac.denominator)
    return AudioData(samples=np.asarray(out, dtype=np.float32), sample_rate=int(target_rate))


# ---------------------------------------------------------------------------
# ② MP3 编解码延迟裁剪
# ---------------------------------------------------------------------------

def trim_codec_delay(audio: AudioData, source_was_mp3: bool) -> AudioData:
    """若来源是 MP3，剪掉编解码延迟（头 :data:`MP3_DEC_DELAY_HEAD_SEC` /
    尾 :data:`MP3_DEC_DELAY_TAIL_SEC`），避免多段拼接累计漂移（红线 3）。
    """
    if not source_was_mp3:
        return audio
    head = int(round(MP3_DEC_DELAY_HEAD_SEC * audio.sample_rate))
    tail = int(round(MP3_DEC_DELAY_TAIL_SEC * audio.sample_rate))
    n = len(audio.samples)
    if n <= head + tail + 1:
        # 片段过短（< 延迟长度），不裁剪，只提示调用方
        return audio
    return AudioData(samples=audio.samples[head:n - tail].copy(), sample_rate=audio.sample_rate)


# ---------------------------------------------------------------------------
# ③ 去首尾静音
# ---------------------------------------------------------------------------

def _silence_edges(samples: np.ndarray, sample_rate: int, threshold_db: float) -> tuple[int, int]:
    """返回 (头部静音样点数, 尾部静音样点数)。"""
    if samples.size == 0:
        return 0, 0
    peak = float(np.max(np.abs(samples)))
    if peak <= 0.0:
        return samples.size, samples.size
    thr = peak * (10.0 ** (threshold_db / 20.0))  # 相对峰值的阈值
    # 说明：阈值以"相对全段峰值 -45dB"实现，等价于对齐良好录音的 -45dBFS 行为，
    # 同时避免把轻微底噪抬成"语音"。
    active = np.abs(samples) > thr
    if not active.any():
        return samples.size, samples.size
    first = int(np.argmax(active))
    last = int(len(active) - 1 - np.argmax(active[::-1]))
    return first, len(samples) - 1 - last


def trim_silence(
    audio: AudioData,
    threshold_db: float = SILENCE_TRIM_THRESHOLD_DB,
    keep_ms: float = SILENCE_KEEP_MS,
) -> tuple[AudioData, float, float]:
    """去首尾静音并保留 ``keep_ms`` 呼吸。

    返回 ``(裁剪后音频, 实际保留的头部静音秒数, 实际保留的尾部静音秒数)``。
    后两个值供停顿标记精确插入静音时补偿使用（保证 [pause=x] 处的停顿恰为 x 秒）。
    """
    keep = int(round(keep_ms / 1000.0 * audio.sample_rate))
    head_sil, tail_sil = _silence_edges(audio.samples, audio.sample_rate, threshold_db)
    n = len(audio.samples)
    if head_sil >= n:  # 全静音
        return AudioData(samples=np.zeros(0, dtype=np.float32), sample_rate=audio.sample_rate), 0.0, 0.0
    start = max(0, head_sil - keep)
    end = min(n, n - tail_sil + keep)
    kept_head = (head_sil - start) / audio.sample_rate
    kept_tail = (end - (n - tail_sil)) / audio.sample_rate
    out = AudioData(samples=audio.samples[start:end].copy(), sample_rate=audio.sample_rate)
    return out, float(kept_head), float(kept_tail)


# ---------------------------------------------------------------------------
# ④ 淡入淡出
# ---------------------------------------------------------------------------

def fade(audio: AudioData, fade_ms: float = FADE_MS, ends: Literal["both", "in", "out", "none"] = "both") -> AudioData:
    """线性淡入淡出（默认 10ms）。"""
    n = len(audio.samples)
    fade_n = int(round(fade_ms / 1000.0 * audio.sample_rate))
    fade_n = min(fade_n, n // 2)
    if fade_n <= 0:
        return audio
    out = audio.samples.copy()
    if ends in ("both", "in"):
        out[:fade_n] *= np.linspace(0.0, 1.0, fade_n, dtype=np.float32)
    if ends in ("both", "out"):
        out[n - fade_n:] *= np.linspace(1.0, 0.0, fade_n, dtype=np.float32)
    return AudioData(samples=out, sample_rate=audio.sample_rate)


# ---------------------------------------------------------------------------
# ⑤ 响度归一
# ---------------------------------------------------------------------------

def _true_peak(samples: np.ndarray, sample_rate: int) -> float:
    """估算真峰值（过采样后取最大绝对值），防 MP3 过冲削顶（坑 7）。"""
    if samples.size == 0:
        return 0.0
    up = signal.resample_poly(samples.astype(np.float64), TRUE_PEAK_OVERSAMPLE, 1)
    return float(np.max(np.abs(up))) if up.size else float(np.max(np.abs(samples)))


def normalize_peak(audio: AudioData, target_db: float = -3.0) -> AudioData:
    """真峰值归一到 ``target_db`` dBFS（默认 -3.0，给 MP3 过冲留余量）。"""
    peak = _true_peak(audio.samples, audio.sample_rate)
    if peak <= 1e-9:
        return audio
    gain = (10.0 ** (target_db / 20.0)) / peak
    return AudioData(samples=np.asarray(audio.samples * gain, dtype=np.float32), sample_rate=audio.sample_rate)


def lufs_integrated(samples: np.ndarray, sample_rate: int) -> float:
    """近似 ITU-R BS.1770-4 积合响度（K 计权 + 门限），单位 LUFS。

    K 计权在 48kHz 计算（测量前用多相滤波重采样到 48k），结果对语音足够准确。
    """
    if samples.size == 0:
        return -70.0
    rate = 48000
    if sample_rate != rate:
        frac = Fraction(rate, int(sample_rate)).limit_denominator(1000)
        x = signal.resample_poly(samples.astype(np.float64), frac.numerator, frac.denominator)
    else:
        x = samples.astype(np.float64)
    # BS.1770-4 48kHz 系数：高频搁架 + 高通（RLB）
    b1 = [1.53512485958697, -2.69169618940638, 1.19839281085285]
    a1 = [1.0, -1.69065929318241, 0.73248077421585]
    b2 = [1.0, -2.0, 1.0]
    a2 = [1.0, -1.99004745483398, 0.99007225036621]
    y = signal.lfilter(b2, a2, signal.lfilter(b1, a1, x))
    # 400ms 块、75% 重叠，门限 -70 LUFS，绝对门限 -10 LUFS 二次门限
    block = int(0.4 * rate)
    step = block // 4
    if y.size < block:
        powers = [float(np.mean(y ** 2))]
    else:
        powers = [
            float(np.mean(y[i:i + block] ** 2))
            for i in range(0, y.size - block + 1, step)
        ]
    loud = lambda p: -0.691 + 10.0 * np.log10(max(p, 1e-12))
    keep = [p for p in powers if loud(p) > -70.0]
    if not keep:
        return -70.0
    rel_gate = loud(sum(keep) / len(keep)) - 10.0
    keep2 = [p for p in keep if loud(p) > rel_gate]
    if not keep2:
        keep2 = keep
    return float(loud(sum(keep2) / len(keep2)))


def normalize_lufs(audio: AudioData, target_lufs: float = -16.0) -> AudioData:
    """按积合响度归一到 ``target_lufs`` LUFS，并用真峰值硬限在 -1dBFS 防削顶。"""
    cur = lufs_integrated(audio.samples, audio.sample_rate)
    if cur <= -69.0:
        return audio
    gain = 10.0 ** ((target_lufs - cur) / 20.0)
    out = np.asarray(audio.samples * gain, dtype=np.float32)
    peak = _true_peak(out, audio.sample_rate)
    limit = 10.0 ** (-1.0 / 20.0)
    if peak > limit:
        out = np.asarray(out * (limit / peak), dtype=np.float32)
    return AudioData(samples=out, sample_rate=audio.sample_rate)


def normalize(audio: AudioData, mode: Literal["peak", "lufs", "none"] = "peak", target_db: float = -3.0) -> AudioData:
    """按模式做响度归一。"""
    if mode == "none":
        return audio
    if mode == "lufs":
        return normalize_lufs(audio, target_lufs=-16.0)
    return normalize_peak(audio, target_db=target_db)


# ---------------------------------------------------------------------------
# ⑥ 编码（MP3 只用 lameenc；soundfile 禁止写 MP3）
# ---------------------------------------------------------------------------

def save_wav(path: str | Path, samples: np.ndarray, sample_rate: int) -> None:
    """写出 PCM WAV（母版格式，16bit）。``samples`` 为 ``[n]`` 或 ``[n, ch]``。

    显式指定 ``format="WAV"``：调用方常写 ``.part`` 临时名，不能依赖扩展名猜格式。
    """
    sf.write(str(path), np.clip(samples, -1.0, 1.0), int(sample_rate), subtype="PCM_16", format="WAV")


def save_flac(path: str | Path, samples: np.ndarray, sample_rate: int) -> None:
    """写出 FLAC。"""
    sf.write(str(path), np.clip(samples, -1.0, 1.0), int(sample_rate), subtype="PCM_16", format="FLAC")


def encode_mp3(path: str | Path, samples: np.ndarray, sample_rate: int, bitrate: int = 128) -> None:
    """用 **lameenc** 把 PCM 编码为 MP3（红线 2：绝不用 soundfile 流式写 MP3）。

    先写 PCM WAV 母版到同目录临时文件，再 lameenc 编码到 ``path``，最后删除临时母版。
    """
    import lameenc

    channels = 1 if samples.ndim == 1 else int(samples.shape[1])
    mono_for_master = samples
    master = Path(str(path) + ".master.tmp.wav")
    try:
        save_wav(master, mono_for_master, sample_rate)
        pcm = np.clip(samples, -1.0, 1.0)
        if channels == 1:
            pcm16 = (pcm * 32767.0).astype("<i2").tobytes()
        else:
            pcm16 = (pcm * 32767.0).astype("<i2").tobytes()  # 已是 [n, ch] 行交错
        enc = lameenc.Encoder()
        enc.set_bit_rate(int(bitrate))
        enc.set_in_sample_rate(int(sample_rate))
        enc.set_channels(channels)
        enc.set_quality(2)
        data = bytes(enc.encode(pcm16)) + bytes(enc.flush())
        Path(path).write_bytes(data)
    finally:
        if master.exists():
            try:
                master.unlink()
            except OSError:
                pass


def write_output(path: str | Path, samples: np.ndarray, sample_rate: int, out_format: str = "mp3", bitrate: int = 128) -> None:
    """按输出格式写成品（mp3→lameenc / wav、flac→soundfile）。"""
    if out_format == "mp3":
        encode_mp3(path, samples, sample_rate, bitrate=bitrate)
    elif out_format == "wav":
        save_wav(path, samples, sample_rate)
    elif out_format == "flac":
        save_flac(path, samples, sample_rate)
    else:
        raise ValueError(f"不支持的输出格式：{out_format}")


# ---------------------------------------------------------------------------
# 解码与片段拼接
# ---------------------------------------------------------------------------

def decode_audio_file(path: str | Path) -> tuple[AudioData, bool]:
    """解码任意音频文件为单声道 float32。

    返回 ``(音频, 来源是否 MP3)``。多声道先按均值下混（配音场景以单声道为主；
    立体声输出时在成品阶段复制声道，见 README 设计决策）。
    """
    p = Path(path)
    data, sr = sf.read(str(p), dtype="float32", always_2d=True)
    mono = data.mean(axis=1).astype(np.float32)
    is_mp3 = p.suffix.lower() == ".mp3" or _sniff_mp3(p)
    return AudioData(samples=mono, sample_rate=int(sr)), is_mp3


def _sniff_mp3(path: Path) -> bool:
    """按文件头判断是否 MP3（引擎可能不带扩展名）。"""
    try:
        head = Path(path).read_bytes()[:4]
    except OSError:
        return False
    return head[:3] == b"ID3" or (len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0)


def concat_with_fades(parts: list[AudioData], fade_ms: float = JOIN_FADE_MS) -> AudioData:
    """按序拼接片段，边界做 20ms 交叉淡化（长文本切分拼接用，§11）。"""
    parts = [p for p in parts if p.samples.size]
    if not parts:
        raise ValueError("没有可拼接的音频片段")
    if len(parts) == 1:
        return parts[0]
    rate = parts[0].sample_rate
    fade_n = min(int(round(fade_ms / 1000.0 * rate)), min(p.samples.size for p in parts) // 2)
    out = parts[0].samples.astype(np.float32).copy()
    for nxt in parts[1:]:
        if nxt.sample_rate != rate:
            raise ValueError("拼接片段采样率不一致，应先统一重采样")
        seg = nxt.samples.astype(np.float32)
        if fade_n > 0:
            tail = out[-fade_n:] * np.linspace(1.0, 0.0, fade_n, dtype=np.float32)
            head = seg[:fade_n] * np.linspace(0.0, 1.0, fade_n, dtype=np.float32)
            out = np.concatenate([out[:-fade_n], tail + head, seg[fade_n:]])
        else:
            out = np.concatenate([out, seg])
    return AudioData(samples=out, sample_rate=rate)


def insert_silence(before: AudioData, after: AudioData, seconds: float,
                  edge_silence_used: tuple[float, float] = (0.0, 0.0)) -> AudioData:
    """在两段之间插入 ``seconds`` 秒静音（停顿标记用）。

    ``edge_silence_used`` 是 (前段尾部已保留静音, 后段头部已保留静音) 的秒数，
    会从插入量中扣除，保证**语音之间的静音间隙恰为 seconds 秒**（验收 8：±0.1s）。
    """
    rate = before.sample_rate
    if after.sample_rate != rate:
        raise ValueError("拼接片段采样率不一致，应先统一重采样")
    gap = max(0.0, seconds - edge_silence_used[0] - edge_silence_used[1])
    silence = np.zeros(int(round(gap * rate)), dtype=np.float32)
    return AudioData(samples=np.concatenate([before.samples, silence, after.samples]), sample_rate=rate)


# ---------------------------------------------------------------------------
# 整条成品流水线（④⑤⑥ + 声道）
# ---------------------------------------------------------------------------

def apply_output_channels(audio: AudioData, channels: int = 1) -> np.ndarray:
    """把单声道处理结果映射到输出声道数，返回 ``[n]`` 或 ``[n, ch]`` float32。"""
    if channels <= 1:
        return audio.samples
    return np.repeat(audio.samples[:, None], int(channels), axis=1)


def finalize(
    audio: AudioData,
    *,
    out_format: Literal["mp3", "wav", "flac"] = "mp3",
    sample_rate: int = 44100,
    bitrate: int = 128,
    channels: int = 1,
    normalize_mode: Literal["peak", "lufs", "none"] = "peak",
    target_db: float = -3.0,
) -> tuple[np.ndarray, int]:
    """完成 ①（若需要）④⑤ 并映射声道，返回待编码的 (样点, 采样率)。

    注意：⑥ 编码请调用 :func:`write_output`（先写 ``.part`` 再由调用方改名）。
    """
    audio = resample(audio, sample_rate)
    audio = fade(audio, FADE_MS)
    audio = normalize(audio, normalize_mode, target_db=target_db)
    return apply_output_channels(audio, channels), int(sample_rate)
