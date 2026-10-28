# -*- coding: utf-8 -*-
# 抓 BWiki 纳西妲语音页面，解析 台词<->音频URL 对应关系
import re, html, json, urllib.request

URL = "https://wiki.biligame.com/ys/%E7%BA%B3%E8%A5%BF%E5%A6%B2%E8%AF%AD%E9%9F%B3"
req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
raw = urllib.request.urlopen(req, timeout=30).read().decode("utf-8")

# 找所有 data-src="..." （HTML转义过）
# 模式：data-src="https&#58;//patchwiki.biligame.com/...mp3"
pat = re.compile(r'data-src="(https&#58;//patchwiki\.biligame\.com/images/[^"]+\.mp3)"')
urls = [html.unescape(m) for m in pat.findall(raw)]
print(f"找到 {len(urls)} 个音频 URL（去重后 {len(set(urls))} 个）")

# 把每个 audio button 所在的表格行抓出来，定位中文台词
# 思路：按 data-src 切分 HTML，往前找最近的中文台词
# 用更简单的方法：找所有含 data-src 的标签，然后取它所在 <tr>...</tr> 内的中文文本
rows = re.findall(r'<tr[^>]*>(.*?)</tr>', raw, re.DOTALL)
pairs = []
for row in rows:
    m = re.search(r'data-src="(https&#58;//patchwiki\.biligame\.com/images/[^"]+\.mp3)"', row)
    if not m:
        continue
    audio_url = html.unescape(m.group(1))
    # 提取中文台词：去掉所有标签，留文本
    text = re.sub(r'<[^>]+>', ' ', row)
    text = html.unescape(text)
    # 只保留中文、中英文标点
    cn = re.findall(r'[\u4e00-\u9fff，。！？、「」…—\-\s]{4,}', text)
    cn = [c.strip() for c in cn if len(c.strip()) >= 4]
    # 取最长的一段作为台词
    line = max(cn, key=len) if cn else ""
    pairs.append({"url": audio_url, "line": line})

# 去重
seen = set()
uniq = []
for p in pairs:
    if p["url"] in seen:
        continue
    seen.add(p["url"])
    uniq.append(p)

print(f"去重后 {len(uniq)} 条")
for i, p in enumerate(uniq[:15]):
    print(f"  {i+1}. {p['line'][:50]}")
print("  ...")

# 保存
out = r"E:\fariy\ai\naxida_ai\素材\原始视频\naxida_wiki_voice_list.json"
import os
os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, "w", encoding="utf-8") as f:
    json.dump(uniq, f, ensure_ascii=False, indent=2)
print(f"\n保存到: {out}")
