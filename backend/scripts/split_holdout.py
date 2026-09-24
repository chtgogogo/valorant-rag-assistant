# -*- coding: utf-8 -*-
"""
冻结 holdout 切分（v3.23）：把 主集+变体+L2 去重后按 7:3 切成
eval_train.json（调参可看）与 eval_holdout.json（对外数字只认这份）。

规则（写入 CHANGELOG）：
  - 随机种子 seed=42 写死在脚本里——任何重跑都得到完全相同的切分（可复现、不可挑集）
  - 去重键 = 题目原文 question（主集/变体/L2 三集间若出现同题只保留一份）
  - 按题目文本哈希确定性排序后再打乱，避免不同 Python 版本 dict 顺序影响结果
  - 调参只准看 train 集；对外汇报的数字只认 holdout 集（防"全集合计=变相过拟合"）

用法（在 backend 目录下）：python scripts/split_holdout.py
"""
import json
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BACKEND_DIR = Path(__file__).resolve().parent.parent
EVAL_DIR = BACKEND_DIR / "eval"
SEED = 42  # 冻结种子：写死不改，保证切分永远可复现
TRAIN_RATIO = 0.7
SOURCES = ["eval_set.json", "eval_set_variants.json", "eval_set_l2.json"]


def main():
    merged: dict[str, dict] = {}  # question -> case（按题目原文去重）
    dup_count = 0
    for name in SOURCES:
        with open(EVAL_DIR / name, "r", encoding="utf-8") as f:
            suite = json.load(f)
        for case in suite["cases"]:
            q = case["question"]
            if q in merged:
                dup_count += 1
                continue
            merged[q] = case

    # 按题目文本哈希排序后再打乱：与文件顺序/加载顺序彻底解耦，纯由种子决定
    cases = sorted(merged.values(), key=lambda c: hash(c["question"]))
    rng = random.Random(SEED)
    rng.shuffle(cases)

    n_train = round(len(cases) * TRAIN_RATIO)
    train, holdout = cases[:n_train], cases[n_train:]

    out_train = {"cases": train, "meta": {
        "purpose": "调参集：调参/迭代只准看这份（v3.23 冻结切分 seed=42）",
        "seed": SEED, "from": SOURCES, "n": len(train)}}
    out_holdout = {"cases": holdout, "meta": {
        "purpose": "冻结盲集：对外汇报数字只认这份（调参不得触碰）",
        "seed": SEED, "from": SOURCES, "n": len(holdout)}}

    with open(EVAL_DIR / "eval_train.json", "w", encoding="utf-8") as f:
        json.dump(out_train, f, ensure_ascii=False, indent=2)
    with open(EVAL_DIR / "eval_holdout.json", "w", encoding="utf-8") as f:
        json.dump(out_holdout, f, ensure_ascii=False, indent=2)

    print(f"[切分] 去重前合计 {sum(1 for _ in SOURCES)} 集共载入，去重 {dup_count} 题，"
          f"剩 {len(cases)} 题（seed={SEED}）")
    print(f"[切分] train={len(train)} 题 → eval_train.json；holdout={len(holdout)} 题 → eval_holdout.json")
    print("[规则] 调参只看 train；对外数字只认 holdout（CHANGELOG v3.23 已写明）")


if __name__ == "__main__":
    main()
