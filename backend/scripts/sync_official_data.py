# -*- coding: utf-8 -*-
"""从本地缓存的 valorant-api 数据同步官方结构化数据到 knowledge_base/。"""
import json
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
AUD = PROJECT_ROOT / "backend" / "data" / "audit"
KB = PROJECT_ROOT / "knowledge_base"

def load(name):
    p = AUD / name
    if not p.exists():
        raise FileNotFoundError(f"缺少 API 缓存文件: {p}")
    return json.loads(p.read_text(encoding="utf-8-sig"))

def write(name, data):
    p = KB / name
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"written {p} items={len(data)}")

today = date.today().isoformat()
zh = load("agents_zh.json")
en = load("agents_en.json")
ab = load("agents_abilities_zh.json")
en_by_uuid = {a["uuid"]: a for a in en}
ab_by_cn = {a.get("displayName"): a for a in ab}

old_aliases = {}
old_path = KB / "heroes.json"
if old_path.exists():
    try:
        for h in json.loads(old_path.read_text(encoding="utf-8")):
            if h.get("en"):
                old_aliases[h["en"]] = [x for x in (h.get("aliases") or []) if x]
    except Exception:
        pass

slot_map = {"Grenade": "C", "Ability1": "Q", "Ability2": "E", "Ultimate": "X"}
role_community = {"控场": "烟位", "哨卫": "哨位", "决斗": "决斗", "先锋": "先锋"}
agents = []
for a in zh:
    e = en_by_uuid.get(a["uuid"], {})
    abilities = {}
    for x in (ab_by_cn.get(a.get("displayName"), {}) or {}).get("abilities") or []:
        slot = slot_map.get(x.get("slot"))
        if slot:
            abilities[slot] = {"name": x.get("displayName", ""), "desc": x.get("description", "")}
    cn = a.get("displayName", "")
    en_name = e.get("displayName", "")
    role = a.get("role.displayName", "") or (a.get("role") or {}).get("displayName", "")
    aliases = []
    for x in [cn, en_name, a.get("developerName", "")] + old_aliases.get(en_name, []):
        if x and x not in aliases:
            aliases.append(x)
    agents.append({
        "uuid": a["uuid"],
        "cn": cn,
        "en": en_name,
        "developerName": a.get("developerName", ""),
        "role": role,
        "role_community": role_community.get(role, role),
        "aliases": aliases,
        "abilities": abilities,
        "source": "valorant-api.com zh-CN",
        "updated_at": today,
    })
write("heroes.json", agents)

wzh = load("weapons.json")
wen = load("weapons_en.json")
if len(wzh) != len(wen):
    raise ValueError("weapons zh/en 数量不一致")
weapons = []
for z, e in zip(wzh, wen):
    weapons.append({
        "cn": z.get("displayName", ""),
        "en": e.get("displayName", ""),
        "category": z.get("category", ""),
        "cost": z.get("cost"),
        "damageRanges": z.get("damageRanges") or [],
        "source": "valorant-api.com zh-CN",
        "updated_at": today,
    })
write("weapons.json", weapons)

mzh = load("maps_zh.json")
men = load("maps_en.json")
en_map = {m["uuid"]: m for m in men}
maps = []
for z in mzh:
    e = en_map.get(z["uuid"], {})
    maps.append({
        "uuid": z["uuid"],
        "cn": z.get("displayName", ""),
        "en": e.get("displayName", ""),
        "mapUrl": z.get("mapUrl", ""),
        "source": "valorant-api.com zh-CN",
        "updated_at": today,
    })
write("maps.json", maps)
