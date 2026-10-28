# -*- coding: utf-8 -*-
# 1) 单独跑 fsmn-vad 拿每段时间戳  2) 逐段 SenseVoice ASR
import os, sys, json, time, re
os.chdir(r"E:\fariy\GPT-SoVITS\GPT-SoVITS-v2pro-20261006")
sys.path.insert(0, os.getcwd())
from funasr import AutoModel

WAV = r"E:\fariy\ai\naxida_ai\素材\音频\纳西妲部分语音素材（纯语音）.wav"
OUT_JSON = r"E:\fariy\ai\naxida_ai\素材\逐字稿\transcript.json"
OUT_TXT = r"E:\fariy\ai\naxida_ai\素材\逐字稿\transcript.txt"

print("加载 VAD 模型...")
vad_model = AutoModel(model="fsmn-vad", device="cuda", disable_update=True)
print("VAD 跑切分...")
t0 = time.time()
vad_res = vad_model.generate(input=WAV)
print(f"VAD 完成 {time.time()-t0:.1f}s")
# vad_res[0]['value'] 是 [[start_ms, end_ms], ...]
segs = vad_res[0]["value"]
print(f"VAD 切出 {len(segs)} 段")

# 加载 ASR
print("加载 SenseVoice...")
asr_model = AutoModel(model="iic/SenseVoiceSmall", device="cuda", disable_update=True)

# 用 ffmpeg 把每段切出来临时 wav，再 ASR
import subprocess, tempfile
segs_out = []
emo_pat = re.compile(r"<\|(zh|en|ja|yue|ko)\|><\|([A-Z_]+)\|>")
for i, (s, e) in enumerate(segs):
    s_sec, e_sec = s/1000.0, e/1000.0
    dur = e_sec - s_sec
    if dur < 0.8 or dur > 15:  # 跳过太短（呼吸/杂音）和太长
        continue
    tmp = os.path.join(tempfile.gettempdir(), f"naxida_seg_{i}.wav")
    subprocess.run([
        "E:/fariy/GPT-SoVITS/GPT-SoVITS-v2pro-20261006/runtime/ffmpeg.exe",
        "-y", "-ss", str(s_sec), "-to", str(e_sec), "-i", WAV,
        "-ar", "16000", "-ac", "1", tmp
    ], capture_output=True)
    r = asr_model.generate(input=tmp)
    text = r[0]["text"]
    # 解析情绪
    emos = emo_pat.findall(text)
    emo = emos[0][1] if emos else "UNKNOWN"
    # 去掉所有 <|...|> 标签
    clean = re.sub(r"<\|[^|]+\|>", "", text).strip()
    if not clean:
        continue
    segs_out.append({
        "start": round(s_sec, 2), "end": round(e_sec, 2),
        "dur": round(dur, 2), "emotion": emo, "text": clean
    })
    if i % 20 == 0:
        print(f"  {i}/{len(segs)}  {s_sec:.1f}-{e_sec:.1f}s [{emo}] {clean[:30]}")

# 保存
with open(OUT_JSON, "w", encoding="utf-8") as f:
    json.dump(segs_out, f, ensure_ascii=False, indent=2)

def fmt(t):
    m, s = divmod(int(t), 60)
    return f"{m:02d}:{s:02d}"
with open(OUT_TXT, "w", encoding="utf-8") as f:
    for x in segs_out:
        f.write(f"[{fmt(x['start'])}-{fmt(x['end'])}] ({x['emotion']}, {x['dur']:.1f}s) {x['text']}\n")

print(f"\n共保留 {len(segs_out)} 段")
print(f"JSON: {OUT_JSON}")
print(f"TXT:  {OUT_TXT}")
