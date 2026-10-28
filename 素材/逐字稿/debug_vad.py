# -*- coding: utf-8 -*-
# VAD 切分 + 逐段 ASR，输出每句 (start, end, text, emotion)
import os, sys, json, time, re
os.chdir(r"E:\fariy\GPT-SoVITS\GPT-SoVITS-v2pro-20261006")
sys.path.insert(0, os.getcwd())
from funasr import AutoModel

WAV = r"E:\fariy\ai\naxida_ai\素材\音频\纳西妲部分语音素材（纯语音）.wav"
OUT_JSON = r"E:\fariy\ai\naxida_ai\素材\逐字稿\transcript.json"
OUT_TXT = r"E:\fariy\ai\naxida_ai\素材\逐字稿\transcript.txt"

print("加载模型...")
model = AutoModel(
    model="iic/SenseVoiceSmall",
    vad_model="fsmn-vad",
    device="cuda",
    disable_update=True,
)

# 先只用 VAD 切分，拿到每段的起止时间
print("VAD 切分...")
vad_res = model.generate(input=WAV, batch_size_s=300, use_itn=False)
# 看一下结构
print("vad_res keys:", list(vad_res[0].keys()))
print("vad_res sample:", str(vad_res[0])[:500])
