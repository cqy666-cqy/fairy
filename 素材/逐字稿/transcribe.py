# -*- coding: utf-8 -*-
# 用 SenseVoice + VAD 转写纳西妲语音素材，输出带时间戳的逐字稿
import os
import sys
import json
import time

# 切到 GPT-SoVITS 根目录
os.chdir(r"E:\fariy\GPT-SoVITS\GPT-SoVITS-v2pro-20261006")
sys.path.insert(0, os.getcwd())

from funasr import AutoModel

WAV_PATH = r"E:\fariy\ai\naxida_ai\素材\音频\纳西妲部分语音素材（纯语音）.wav"
OUT_JSON = r"E:\fariy\ai\naxida_ai\素材\逐字稿\transcript.json"
OUT_TXT = r"E:\fariy\ai\naxida_ai\素材\逐字稿\transcript.txt"

print("加载 SenseVoice 模型（首次会自动下载，约几百MB）...")
model = AutoModel(
    model="iic/SenseVoiceSmall",
    vad_model="fsmn-vad",
    device="cuda",
    disable_update=True,
)
print("模型加载完成，开始转写...")

t0 = time.time()
res = model.generate(
    input=WAV_PATH,
    batch_size_s=300,
    hotword="纳西妲 小吉祥草王 须弥 梦境 枣椰蜜糖 树莓薄荷饮",
)
print(f"转写完成，耗时 {time.time()-t0:.1f}s")

# 解析结果
segments = []
for item in res:
    # key 是 text，value 是文本；时间在 sentence_info 或没直接给
    text = item.get("text", "")
    # SenseVoice + VAD 会返回 sentence_info，包含每段起止
    sentence_info = item.get("sentence_info", [])
    if sentence_info:
        for s in sentence_info:
            segments.append({
                "start": s.get("start", 0) / 1000.0,
                "end": s.get("end", 0) / 1000.0,
                "text": s.get("text", "").strip(),
            })
    else:
        segments.append({"start": 0, "end": 0, "text": text.strip()})

# 保存 JSON
with open(OUT_JSON, "w", encoding="utf-8") as f:
    json.dump(segments, f, ensure_ascii=False, indent=2)

# 保存可读 txt
def fmt(t):
    m, s = divmod(int(t), 60)
    return f"{m:02d}:{s:02d}"

with open(OUT_TXT, "w", encoding="utf-8") as f:
    for seg in segments:
        f.write(f"[{fmt(seg['start'])}-{fmt(seg['end'])}] {seg['text']}\n")

print(f"共 {len(segments)} 段")
print(f"JSON: {OUT_JSON}")
print(f"TXT:  {OUT_TXT}")
