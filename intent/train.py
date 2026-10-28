# -*- coding: utf-8 -*-
"""意图分类器训练脚本：rbt3 微调，纯CPU，约2-3分钟"""
import os, json, random
os.environ["MODELSCOPE_CACHE"] = r"E:\fariy\ai\naxida_ai\models\modelscope_cache"

import numpy as np
import torch
from torch.utils.data import Dataset, DataLoader
from transformers import BertTokenizer, BertForSequenceClassification, get_linear_schedule_with_warmup
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, classification_report

MODEL_DIR = r"E:\fariy\ai\naxida_ai\models\intent_classifier\pretrained\hfl_rbt3"
SAVE_DIR = r"E:\fariy\ai\naxida_ai\models\intent_classifier\finetuned"
DATA_PATH = r"E:\fariy\ai\naxida_ai\intent\train_data.json"

LABELS = ["chat", "sing", "weather", "news", "hijack", "search"]
LABEL2ID = {l: i for i, l in enumerate(LABELS)}
ID2LABEL = {i: l for i, l in enumerate(LABELS)}
NUM_LABELS = len(LABELS)

random.seed(42)
np.random.seed(42)
torch.manual_seed(42)

class IntentDataset(Dataset):
    def __init__(self, texts, labels, tokenizer, max_len=64):
        self.texts = texts
        self.labels = labels
        self.tokenizer = tokenizer
        self.max_len = max_len

    def __len__(self):
        return len(self.texts)

    def __getitem__(self, idx):
        encoding = self.tokenizer(
            self.texts[idx],
            truncation=True,
            padding="max_length",
            max_length=self.max_len,
            return_tensors="pt",
        )
        return {
            "input_ids": encoding["input_ids"].squeeze(0),
            "attention_mask": encoding["attention_mask"].squeeze(0),
            "labels": torch.tensor(self.labels[idx], dtype=torch.long),
        }


def main():
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    texts = [d["text"] for d in data]
    labels = [LABEL2ID[d["label"]] for d in data]

    X_train, X_test, y_train, y_test = train_test_split(
        texts, labels, test_size=0.15, random_state=42, stratify=labels
    )
    print(f"训练集 {len(X_train)} 条，测试集 {len(X_test)} 条")

    tokenizer = BertTokenizer.from_pretrained(MODEL_DIR)
    model = BertForSequenceClassification.from_pretrained(
        MODEL_DIR, num_labels=NUM_LABELS, id2label=ID2LABEL, label2id=LABEL2ID
    )

    train_ds = IntentDataset(X_train, y_train, tokenizer)
    test_ds = IntentDataset(X_test, y_test, tokenizer)
    train_loader = DataLoader(train_ds, batch_size=16, shuffle=True)
    test_loader = DataLoader(test_ds, batch_size=32, shuffle=False)

    device = torch.device("cpu")
    model.to(device)

    optimizer = torch.optim.AdamW(model.parameters(), lr=2e-5, weight_decay=0.01)
    total_steps = len(train_loader) * 10
    scheduler = get_linear_schedule_with_warmup(
        optimizer, num_warmup_steps=100, num_training_steps=total_steps
    )

    best_acc = 0
    for epoch in range(10):
        model.train()
        total_loss = 0
        for batch in train_loader:
            batch = {k: v.to(device) for k, v in batch.items()}
            outputs = model(**batch)
            loss = outputs.loss
            total_loss += loss.item()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            optimizer.zero_grad()

        model.eval()
        preds, trues = [], []
        with torch.no_grad():
            for batch in test_loader:
                batch = {k: v.to(device) for k, v in batch.items()}
                outputs = model(**batch)
                preds.extend(torch.argmax(outputs.logits, dim=1).cpu().numpy())
                trues.extend(batch["labels"].cpu().numpy())

        acc = accuracy_score(trues, preds)
        print(f"Epoch {epoch+1}: loss={total_loss/len(train_loader):.4f}, acc={acc:.4f}")

        if acc > best_acc:
            best_acc = acc
            model.save_pretrained(SAVE_DIR)
            tokenizer.save_pretrained(SAVE_DIR)

    print(f"\n最佳准确率: {best_acc:.4f}")
    print(classification_report(trues, preds, target_names=LABELS))

    # 保存标签映射
    with open(os.path.join(SAVE_DIR, "label_map.json"), "w", encoding="utf-8") as f:
        json.dump({"label2id": LABEL2ID, "id2label": ID2LABEL}, f, ensure_ascii=False, indent=2)

    if best_acc < 0.90:
        print("【警告】准确率未达90%，不建议接入主程序")
    else:
        print("【OK】准确率达标，可接入主程序")


if __name__ == "__main__":
    main()
