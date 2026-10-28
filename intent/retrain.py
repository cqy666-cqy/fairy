# -*- coding: utf-8 -*-
"""持续学习重训脚本：合并 DeepSeek 兜底收集的样本，重新训练分类器

用法：
  cd E:\\fariy\\ai\\naxida_ai
  E:\\fariy\\GPT-SoVITS\\GPT-SoVITS-v2pro-20261006\\runtime\\python.exe intent\\retrain.py
"""
import os, sys, json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from intent import predict

TRAIN_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "train_data.json")


def main():
    # 1. 读取待重训样本
    pending = predict.load_pending()
    if not pending:
        print("没有待重训样本，无需重训")
        return

    print(f"发现 {len(pending)} 条待重训样本（来自 DeepSeek 兜底判断）")

    # 2. 合并到训练数据（去重：同文本不同标签以 pending 为准）
    with open(TRAIN_DATA, "r", encoding="utf-8") as f:
        train_data = json.load(f)

    existing = {d["text"]: d for d in train_data}
    added = 0
    for s in pending:
        text = s["text"]
        label = s["label"]
        if text not in existing:
            train_data.append({"text": text, "label": label})
            added += 1
        elif existing[text]["label"] != label:
            # 同文本标签冲突，以 DeepSeek 判断为准
            existing[text]["label"] = label
            added += 1

    # 重新写回（保持列表顺序）
    train_data = list(existing.values())

    with open(TRAIN_DATA, "w", encoding="utf-8") as f:
        json.dump(train_data, f, ensure_ascii=False, indent=2)

    print(f"训练数据已更新: {len(train_data)} 条（新增/修正 {added} 条）")

    # 3. 重新训练
    print("\n开始重新训练...")
    import subprocess
    result = subprocess.run(
        [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "train.py")],
        capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    print(result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr[-500:])

    # 4. 清空 pending
    predict.clear_pending()
    print("\n重训完成，pending 样本已清空")


if __name__ == "__main__":
    main()
