# -*- coding: utf-8 -*-
"""下载 rbt3 预训练模型（modelscope 源，国内稳定）"""
import os
os.environ["MODELSCOPE_CACHE"] = r"E:\fariy\ai\naxida_ai\models\modelscope_cache"

from modelscope import snapshot_download

save_dir = r"E:\fariy\ai\naxida_ai\models\intent_classifier\pretrained"
path = snapshot_download(
    "hfl/rbt3",
    cache_dir=save_dir,
    local_dir=os.path.join(save_dir, "hfl_rbt3"),
)
print("模型下载完成:", path)
