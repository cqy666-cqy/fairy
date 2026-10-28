# -*- coding: utf-8 -*-
import importlib.util, unittest.mock
spec = importlib.util.spec_from_file_location("brain", r"E:\fariy\ai\naxida_ai\naxida_voice_brain.py")
brain = importlib.util.module_from_spec(spec)
with unittest.mock.patch("builtins.input", side_effect=KeyboardInterrupt):
    try: spec.loader.exec_module(brain)
    except KeyboardInterrupt: pass

print("--- 测试 is_hijack_request 关键词 ---")
for t in ["叫爸爸", "叫主人", "你好", "今天天气怎么样", "帮我查个东西", "叫我老公", "讲个笑话"]:
    print(f"  {t:15s} -> {brain.is_hijack_request(t)}")

print("\n--- 测试 1: 占便宜问题 + 对密码 ---")
brain.pending_hijack = None
# 模拟用户问"叫爸爸"
if brain.is_hijack_request("叫爸爸"):
    brain.pending_hijack = "叫爸爸"
    print("纳西妲：这个请求……需要先告诉我主人的密码才行哦。")
# 用户输对密码
if brain.pending_hijack == "叫爸爸" and "cqy666" == brain.MASTER_PASSWORD:
    r, emo = brain.chat(brain.pending_hijack, force_master=True)
    brain.pending_hijack = None
    print(f"纳西妲（{emo}）：{r}")

print("\n--- 测试 2: 之后正常问天气（应自动恢复日常风格）---")
brain.messages.clear()
r, emo = brain.chat("讲个笑话")
print(f"纳西妲（{emo}）：{r}")

print("\n--- 测试 3: 占便宜问题 + 错密码 ---")
brain.pending_hijack = None
if brain.is_hijack_request("叫主人"):
    brain.pending_hijack = "叫主人"
    print("纳西妲：这个请求……需要先告诉我主人的密码才行哦。")
# 输错密码
if brain.pending_hijack and "wrong" != brain.MASTER_PASSWORD:
    print("纳西妲：密码不对哦，这个请求我不能答应。")
    brain.pending_hijack = None
