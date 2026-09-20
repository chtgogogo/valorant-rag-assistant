# 【v3.4】官方数据直答评测集自动生成器
# ------------------------------------------------------------
# 从 knowledge_base 官方 JSON（heroes/weapons/maps）自动生成
# eval_set_official.json —— 题目与数据永同步、零泄漏、改库重跑即新。
# 用法（backend 目录下）：python scripts/gen_official_eval.py
# ------------------------------------------------------------
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "knowledge_base")


def load(name):
    with open(os.path.join(KB_DIR, name), encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else data.get(next(k for k in data if isinstance(data[k], list)), [])


def main():
    heroes = load("heroes.json")
    weapons = load("weapons.json")
    maps = load("maps.json")
    by_cn = {w.get("cn"): w for w in weapons}

    def cost(cn):
        return str(by_cn[cn].get("cost", "?"))

    cases = [
        {"id": "O01", "type": "官方直答", "path": "official",
         "question": "游戏里现在一共有多少个英雄？",
         "expected_keywords": [str(len(heroes))],
         "note": f"heroes.json count={len(heroes)}"},
        {"id": "O02", "type": "官方直答", "path": "official",
         "question": "游戏里一共有多少把武器？",
         "expected_keywords": [str(len(weapons))],
         "note": f"weapons.json count={len(weapons)}"},
        {"id": "O03", "type": "官方直答", "path": "official",
         "question": "狂徒多少钱？",  # 黑话+价格组合题
         "expected_keywords": [cost("狂徒")], "note": "Vandal=狂徒"},
        {"id": "O04", "type": "官方直答", "path": "official",
         "question": "大狙价格是多少？",  # 黑话+价格组合题
         "expected_keywords": [cost("冥驹")], "note": "Operator=冥驹"},
        {"id": "O05", "type": "官方直答", "path": "official",
         "question": "游戏里有哪些地图？",
         "expected_keywords": ["亚海悬城", "森寒冬港", "莲华古城"], "note": "列表抽查3张"},
        {"id": "O06", "type": "官方直答", "path": "official",
         "question": "标配手枪要钱吗？",  # 黑话+免费枪
         "expected_keywords": [cost("标配")], "note": "Classic=标配, cost 0"},
    ]

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "eval", "eval_set_official.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"description": "官方数据直答评测集（自动生成，勿手改；改库后重跑本脚本）",
                   "generated_note": "by gen_official_eval.py", "cases": cases},
                  f, ensure_ascii=False, indent=2)
    print(f"已生成 {len(cases)} 题到 {os.path.normpath(out)}")
    for c in cases:
        print(f"  {c['id']} {c['question']} → {c['expected_keywords']}")


if __name__ == "__main__":
    main()
