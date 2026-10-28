# -*- coding: utf-8 -*-
# 重切4条参考音频，结尾多留1秒尾音
import os, subprocess

SRC = r"E:\fariy\ai\naxida_ai\素材\音频\纳西妲部分语音素材（纯语音）.wav"
OUT_DIR = r"E:\fariy\ai\naxida_ai\素材\参考音频"
FFMPEG = r"E:\fariy\GPT-SoVITS\GPT-SoVITS-v2pro-20261006\runtime\ffmpeg.exe"

# (文件名, 起秒, 止秒) —— 起止都比原VAD多留余量
CLIPS = [
    ("【default_温柔】又有心事吗？我来陪你一起想吧。.wav",
     36.8, 43.0),
    ("【happy_开心】天气真好呀，暖洋洋的，我们的身边马上也要热闹起来了。.wav",
     103.8, 112.5),
    ("【neutral_沉思】我们无法观测未来的所有可能性，只能靠这样略显笨拙的方式获取知识。.wav",
     291.8, 301.0),
    ("【surprised_惊讶】什么？你说他和艾尔海森是室友？哦，世上居然还有这种可能性。.wav",
     513.8, 526.5),
]

for name, s, e in CLIPS:
    out = os.path.join(OUT_DIR, name)
    if os.path.exists(out):
        os.remove(out)
    cmd = [FFMPEG, "-y", "-ss", str(s), "-to", str(e), "-i", SRC,
           "-ar", "32000", "-ac", "1", "-sample_fmt", "s16", out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    size = os.path.getsize(out) // 1024
    dur = e - s
    print(f"[OK] {name[:30]}... {dur:.1f}s  {size}KB")
