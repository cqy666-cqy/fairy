# -*- coding: utf-8 -*-
"""纳西妲 bot 启动器：重定向日志后用独立进程跑 run_bot.py。
用法：WMI Create 或终端直接 python launch_bot.py
日志在 %TEMP%\\naxida_bot.log"""
import os
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(os.environ.get("TEMP", r"C:\Users\admin\AppData\Local\Temp"), "naxida_bot.log")

log = open(LOG, "a", encoding="utf-8", buffering=1)
# 子进程 sys.stdout 编码默认取系统 locale（GBK），模型输出稀有字符会把 print 打崩、中断回复发送
env = dict(os.environ, PYTHONIOENCODING="utf-8")
subprocess.run([sys.executable, "-u", os.path.join(BASE_DIR, "bot", "run_bot.py")],
               cwd=BASE_DIR, stdout=log, stderr=subprocess.STDOUT, env=env)
