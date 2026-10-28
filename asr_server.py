# -*- coding: utf-8 -*-
r"""纳西妲 ASR 微服务：QQ 语音消息识别专用

复用网页端同款 SenseVoiceSmall + 原神热词 + 同音字纠正（词表直接 import mic_input，单一来源），
用 GPT-SoVITS 自带 runtime 的 python 运行（那里装了 funasr），CPU 推理不占显存。

接口：
  GET  /ping                -> {"ok": true}
  POST /asr {"path": "..."} -> {"text": "识别文字", "emotion": "开心"|null}

启动：runtime\python.exe asr_server.py（工作目录须为 naxida_ai，便于 import mic_input）
"""
import os
import re
import sys

os.environ["MODELSCOPE_CACHE"] = r"E:\fariy\ai\naxida_ai\models\modelscope_cache"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, jsonify, request
from funasr import AutoModel

from mic_input import HOTWORDS, correct_text, _USER_EMOTION_MAP

app = Flask(__name__)

print("[ASR] 正在加载 SenseVoiceSmall（CPU）...")
_model = AutoModel(model="iic/SenseVoiceSmall", device="cpu", disable_update=True)
print("[ASR] SenseVoiceSmall 就绪")


@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"ok": True})


@app.route("/asr", methods=["POST"])
def asr():
    data = request.get_json(silent=True) or {}
    path = data.get("path", "")
    if not path or not os.path.isfile(path):
        return jsonify({"text": "", "emotion": None, "error": "file not found"})
    try:
        res = _model.generate(input=path, hotword=HOTWORDS)
        raw = res[0]["text"]
        # 先捡情绪标签，再统一清洗所有 <|...|> 标签
        emotion = None
        for tag in re.findall(r"<\|([A-Z]+)\|>", raw):
            if tag in _USER_EMOTION_MAP:
                emotion = _USER_EMOTION_MAP[tag]
                break
        text = re.sub(r"<\|[^|]+\|>", "", raw).strip()
        text = correct_text(text)
        return jsonify({"text": text, "emotion": emotion})
    except Exception as e:
        return jsonify({"text": "", "emotion": None, "error": str(e)})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=9881, debug=False, use_reloader=False)
