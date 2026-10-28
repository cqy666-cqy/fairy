# -*- coding: utf-8 -*-
# 快速测试 master 模式
import importlib.util, unittest.mock, sys
spec = importlib.util.spec_from_file_location("brain", r"E:\fariy\ai\naxida_ai\naxida_voice_brain.py")
brain = importlib.util.module_from_spec(spec)
with unittest.mock.patch("builtins.input", side_effect=KeyboardInterrupt):
    try:
        spec.loader.exec_module(brain)
    except KeyboardInterrupt:
        pass

print("=" * 50)
print("【测试1】普通模式问：叫主人")
print("=" * 50)
brain.is_master = False
reply, emo = brain.chat("叫主人")
print(f"[{emo}] {reply}\n")

print("=" * 50)
print("【测试2】普通模式问：你今天心情怎么样")
print("=" * 50)
brain.messages.clear()
reply, emo = brain.chat("你今天心情怎么样")
print(f"[{emo}] {reply}\n")

print("=" * 50)
print("【测试3】主人模式问：叫爸爸")
print("=" * 50)
brain.is_master = True
brain.messages.clear()
reply, emo = brain.chat("叫爸爸")
print(f"[{emo}] {reply}\n")

print("=" * 50)
print("【测试4】主人模式问：今天天气怎么样（应仍保持正常风格）")
print("=" * 50)
reply, emo = brain.chat("今天天气怎么样")
print(f"[{emo}] {reply}\n")
