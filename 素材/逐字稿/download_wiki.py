# -*- coding: utf-8 -*-
# 下载 76 条 BWiki 官方中文语音
import json, os, re, urllib.request

LIST = r"E:\fariy\ai\naxida_ai\素材\原始视频\naxida_wiki_cn_voices.json"
OUT_DIR = r"E:\fariy\ai\naxida_ai\素材\参考音频\wiki官方"
os.makedirs(OUT_DIR, exist_ok=True)

with open(LIST, "r", encoding="utf-8") as f:
    voices = json.load(f)

def safe_name(s):
    return re.sub(r'[\\/:*?"<>|]', "_", s)

headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://wiki.biligame.com/"}
ok = fail = 0
for i, v in enumerate(voices):
    fn = f"{i+1:02d}_{safe_name(v['title'])}.mp3"
    fp = os.path.join(OUT_DIR, fn)
    if os.path.exists(fp) and os.path.getsize(fp) > 1000:
        ok += 1
        continue
    try:
        req = urllib.request.Request(v["cn_url"], headers=headers)
        data = urllib.request.urlopen(req, timeout=30).read()
        with open(fp, "wb") as f:
            f.write(data)
        ok += 1
        if (i+1) % 10 == 0:
            print(f"  已下载 {i+1}/76")
    except Exception as e:
        print(f"  [FAIL] {v['title']}: {e}")
        fail += 1

print(f"\n完成: 成功 {ok}, 失败 {fail}")
print(f"目录: {OUT_DIR}")
# 列文件大小
total = sum(os.path.getsize(os.path.join(OUT_DIR, f)) for f in os.listdir(OUT_DIR))
print(f"总大小: {total/1024/1024:.1f} MB")
