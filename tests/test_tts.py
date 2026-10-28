import requests
import os

# 大脑和嘴巴都在本地，所以用 127.0.0.1
TTS_URL = "http://127.0.0.1:9880/tts"

# ======== 你的纳西妲参考音频路径和原话 ========
# 注意前面的 r 不能删，它能防止 Windows 路径里的反斜杠报错
REF_AUDIO = r"E:\fariy\character voice\naxida\v4\naxida_ZH\reference_audios\chinese\emotions\【默认】很抱歉地告诉大家，根据我们的调查，在梦境的背后隐藏着某种阴谋。.wav"
PROMPT_TEXT = "很抱歉地告诉大家，根据我们的调查，在梦境的背后隐藏着某种阴谋。"

def generate_voice(text):
    print("正在请求 GPT-Sovits 合成语音...")
    payload = {
        "text": text,
        "text_lang": "zh",
        "ref_audio_path": REF_AUDIO,
        "prompt_text": PROMPT_TEXT,
        "prompt_lang": "zh",
        "text_split_method": "cut5",
        "streaming_mode": False
    }
    try:
        resp = requests.post(TTS_URL, json=payload, timeout=60)
        if resp.status_code == 200:
            # 保存音频
            with open("test_output.wav", "wb") as f:
                f.write(resp.content)
            print("成功！音频已保存为 test_output.wav，正在播放...")
            # 自动用电脑默认播放器打开，听听是不是纳西妲的声音
            os.startfile("test_output.wav")
        else:
            print(f"合成失败，状态码：{resp.status_code}")
            print(resp.text)
    except Exception as e:
        print(f"连接错误：{e}")

if __name__ == "__main__":
    generate_voice("你好呀，我是纳西妲。今天我们一起探索什么新知识呢？")