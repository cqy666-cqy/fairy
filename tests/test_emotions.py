# -*- coding: utf-8 -*-
# 端到端测试：DeepSeek 生成一句纳西妲回复，分别用4种情绪合成 wav
import os, sys, time
sys.path.insert(0, r"E:\fariy\ai\naxida_ai")
# 直接 import 主脚本的函数
import importlib.util
spec = importlib.util.spec_from_file_location("brain", r"E:\fariy\ai\naxida_ai\naxida_voice_brain.py")
brain = importlib.util.module_from_spec(spec)
# 阻止主循环执行
import unittest.mock
with unittest.mock.patch("builtins.input", side_effect=KeyboardInterrupt):
    try:
        spec.loader.exec_module(brain)
    except KeyboardInterrupt:
        pass

# 1. 测试 DeepSeek 生成一句回复
print("=" * 50)
print("测试 1: DeepSeek 生成纳西妲回复")
print("=" * 50)
reply, emotion = brain.chat("你好呀，给我介绍一下你自己吧")
print(f"情绪标签: {emotion}")
print(f"纳西妲: {reply}")

# 2. 用 4 种情绪各合成一次
print("\n" + "=" * 50)
print("测试 2: 用4种情绪合成同一句话")
print("=" * 50)
test_text = "今天天气真好呀，要一起去散散步吗？"
out_dir = r"E:\fariy\fairy项目\情绪测试"
os.makedirs(out_dir, exist_ok=True)

for emo in ["gentle", "happy", "thoughtful", "playful"]:
    print(f"\n合成情绪: {emo}")
    path = brain.tts_once(test_text, emo)
    if path:
        # 复制到 out_dir
        import shutil
        dst = os.path.join(out_dir, f"情绪_{emo}.wav")
        shutil.copy(path, dst)
        print(f"  -> {dst}  ({os.path.getsize(dst)//1024}KB)")
    else:
        print(f"  [失败]")

print("\n完成！请到目录听效果:", out_dir)
