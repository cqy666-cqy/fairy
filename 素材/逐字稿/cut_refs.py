# -*- coding: utf-8 -*-
# 从原音频切 6 条多情绪参考音频
import os, subprocess

SRC = r"E:\fariy\ai\naxida_ai\素材\音频\纳西妲部分语音素材（纯语音）.wav"
OUT_DIR = r"E:\fariy\ai\naxida_ai\素材\参考音频"
os.makedirs(OUT_DIR, exist_ok=True)

FFMPEG = r"E:\fariy\GPT-SoVITS\GPT-SoVITS-v2pro-20261006\runtime\ffmpeg.exe"

# (情绪名, 起秒, 止秒, 参考文本——用正确版的原神台词，不是ASR错字)
CLIPS = [
    ("default_温柔",    37.0,  42.0, "又有心事吗？我来陪你一起想吧。"),
    ("happy_开心",      104.0, 111.0, "天气真好呀，暖洋洋的，我们的身边马上也要热闹起来了。"),
    ("neutral_平静",     61.0,  65.0, "快去避雨吧，小心头顶上长出蘑菇。"),
    ("sad_委屈",        148.0, 152.0, "我想喝……树莓薄荷饮。"),
    ("surprised_惊讶",   514.0, 525.0, "什么？你说他和艾尔海森是室友？哦，世上居然还有这种可能性。"),
    ("angry_认真",       1101.0, 1105.0, "听我的，准没错。"),
]

for name, s, e, text in CLIPS:
    out = os.path.join(OUT_DIR, f"【{name}】{text}.wav")
    cmd = [FFMPEG, "-y", "-ss", str(s), "-to", str(e), "-i", SRC,
           "-ar", "32000", "-ac", "1", "-sample_fmt", "s16", out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[FAIL] {name}: {r.stderr[-300:]}")
    else:
        size = os.path.getsize(out) // 1024
        print(f"[OK] {name}  {e-s:.1f}s  {size}KB  -> {os.path.basename(out)}")

print("\n参考文本（给 GPT-SoVITS 用）：")
for name, s, e, text in CLIPS:
    print(f"  {name}: {text}")
