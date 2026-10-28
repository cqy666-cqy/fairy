# -*- coding: utf-8 -*-
"""意图分类推理模块：懒加载、CPU推理、置信度回退

用法：
    from intent import predict
    label, conf = predict.classify("今天天气怎么样")
    # label: "chat" / "sing" / "weather" / "news" / "hijack" / "search" / None(置信度不足)
"""
import os, json

os.environ.setdefault("MODELSCOPE_CACHE", r"E:\fariy\ai\naxida_ai\models\modelscope_cache")

_MODEL_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "models", "intent_classifier", "finetuned"
)
# 置信度阈值：低于此值返回 None，让调用方回退到旧的三级判断
_CONF_THRESHOLD = 0.75

# 加载标签映射
_LABEL_MAP_PATH = os.path.join(_MODEL_DIR, "label_map.json")
_ID2LABEL = {}
if os.path.exists(_LABEL_MAP_PATH):
    try:
        with open(_LABEL_MAP_PATH, "r", encoding="utf-8") as f:
            _ID2LABEL = {int(k): v for k, v in json.load(f)["id2label"].items()}
    except Exception:
        _ID2LABEL = {}

_tokenizer = None
_model = None


def _load():
    global _tokenizer, _model
    if _model is not None:
        return
    import torch
    from transformers import BertTokenizer, BertForSequenceClassification

    _tokenizer = BertTokenizer.from_pretrained(_MODEL_DIR)
    _model = BertForSequenceClassification.from_pretrained(_MODEL_DIR)
    _model.to("cpu")
    _model.eval()
    for p in _model.parameters():
        p.requires_grad_(False)


def classify(text):
    """返回 (label, confidence)。置信度不足时 label 为 None。"""
    try:
        _load()
        import torch

        enc = _tokenizer(
            text, truncation=True, padding="max_length", max_length=64, return_tensors="pt"
        )
        with torch.no_grad():
            logits = _model(**enc).logits
        probs = torch.softmax(logits, dim=-1)[0]
        conf, idx = probs.max(dim=0)
        conf = conf.item()
        idx = idx.item()

        if conf < _CONF_THRESHOLD:
            return None, conf
        label = _ID2LABEL.get(idx, "chat")
        return label, conf
    except Exception:
        # 模型加载/推理失败时静默回退，不影响主流程
        return None, 0.0


def unload():
    """释放模型内存（可选，内存紧张时调用）"""
    global _tokenizer, _model
    _tokenizer = None
    _model = None


# ===== 持续学习：低置信度样本记录 =====
_PENDING_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "pending_samples.json"
)


def record_pending(text, deepseek_result, classifier_conf):
    """DeepSeek 兜底判断后，记录样本供后续重训

    deepseek_result: True=需要联网(search), False=不需要(not_search)
    classifier_conf: 分类器当时的置信度（<0.85）
    """
    try:
        import json, datetime

        label = "search" if deepseek_result else "not_search"
        record = {
            "text": text,
            "label": label,
            "source": "deepseek_fallback",
            "classifier_conf": round(classifier_conf, 4) if classifier_conf else None,
            "time": datetime.datetime.now().isoformat(timespec="seconds"),
        }

        # 追加到 pending 文件（JSONL 格式，一行一条）
        with open(_PENDING_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 记录失败不影响主流程


def load_pending():
    """读取待重训样本（供 retrain.py 调用）"""
    import json
    if not os.path.exists(_PENDING_PATH):
        return []
    samples = []
    with open(_PENDING_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    samples.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return samples


def clear_pending():
    """清空待重训样本（retrain.py 重训完成后调用）"""
    if os.path.exists(_PENDING_PATH):
        os.remove(_PENDING_PATH)
