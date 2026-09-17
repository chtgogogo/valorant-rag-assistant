# -*- coding: utf-8 -*-
"""从权威英雄介绍 Markdown 生成结构化 heroes.json。
用法：cd backend && python scripts/build_hero_catalog.py
"""
import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SRC = PROJECT_ROOT / "knowledge_base" / "无畏契约英雄介绍.md"
DST = PROJECT_ROOT / "knowledge_base" / "heroes.json"


def main():
    text = SRC.read_text(encoding="utf-8")
    chunks = re.split(r"\n\s*##\s+", text)
    heroes = []
    for chunk in chunks[1:]:
        lines = chunk.splitlines()
        if not lines:
            continue
        title = lines[0].strip()
        body = "\n".join(lines[1:])
        fields = {}
        for key, value in re.findall(r"-\s*\*\*(国服ID|港服ID|外服ID|定位)\*\*[：:]\s*(.+)", body):
            fields[key] = value.strip()
        if "外服ID" not in fields or "定位" not in fields:
            continue
        cn = fields.get("国服ID") or title.split("/")[0].strip()
        tw = fields.get("港服ID") or ""
        en = fields.get("外服ID") or ""
        role = fields.get("定位") or ""
        aliases = [x for x in {cn, tw, en} if x]
        heroes.append({
            "cn": cn,
            "tw": tw,
            "en": en,
            "role": role,
            "aliases": aliases,
            "source": "knowledge_base/无畏契约英雄介绍.md",
        })

    DST.write_text(json.dumps(heroes, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"生成 {DST}，共 {len(heroes)} 个英雄")
    for h in heroes:
        print(f"  {h['cn']} / {h['tw']} / {h['en']} -> {h['role']}")


if __name__ == "__main__":
    main()
