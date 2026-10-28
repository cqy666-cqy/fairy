# -*- coding: utf-8 -*-
# 按 table 块切，取每个台词块的中文(第1个)音频 + 标题
import re, html, json, urllib.request, os

URL = "https://wiki.biligame.com/ys/%E7%BA%B3%E8%A5%BF%E5%A6%B2%E8%AF%AD%E9%9F%B3"
req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
raw = urllib.request.urlopen(req, timeout=30).read().decode("utf-8")

# 按 <div style="...display: table;..."> 块切
blocks = re.split(r'<div style="[^"]*display:\s*table;[^"]*">', raw)
print(f"切出 {len(blocks)} 个块")

voices = []
for b in blocks[1:]:  # 第0个是页头
    # 标题：第一个 <div ...>#8F98A6...>标题</div>
    title_m = re.search(r'background:\s*#8F98A6[^>]*>([^<]+)</div>', b)
    title = html.unescape(title_m.group(1).strip()) if title_m else ""
    # 所有 data-src
    srcs = re.findall(r'data-src="(https&#58;//patchwiki\.biligame\.com/images/[^"]+\.mp3)"', b)
    if not srcs:
        continue
    cn_url = html.unescape(srcs[0])  # 第1个=中文
    if not title:
        continue
    voices.append({"title": title, "cn_url": cn_url})

# 去重（按 URL）
seen = set()
uniq = []
for v in voices:
    if v["cn_url"] in seen:
        continue
    seen.add(v["cn_url"])
    uniq.append(v)

print(f"共 {len(uniq)} 条中文语音")
for i, v in enumerate(uniq):
    print(f"  {i+1:2d}. {v['title']}")

out = r"E:\fariy\ai\naxida_ai\素材\原始视频\naxida_wiki_cn_voices.json"
os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, "w", encoding="utf-8") as f:
    json.dump(uniq, f, ensure_ascii=False, indent=2)
print(f"\n保存到: {out}")
