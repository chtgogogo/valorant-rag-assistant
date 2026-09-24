# -*- coding: utf-8 -*-
"""官方结构化数据问答：英雄列表、英雄定位、武器价格、地图列表。
数据源：knowledge_base/*.json（由 scripts/sync_official_data.py 从 valorant-api.com 同步）
并加载 knowledge_base/aliases.json 支持社区外号、拼音、错别字、武器俗名。
"""
import json
import re
from pathlib import Path

from config.settings import PROJECT_ROOT

DATA_DIR = PROJECT_ROOT / "knowledge_base"


def _load(name: str):
    p = DATA_DIR / name
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


HEROES = _load("heroes.json")
WEAPONS = _load("weapons.json")
MAPS = _load("maps.json")
ALIASES = _load("aliases.json")

ROLE_ALIASES = {
    "决斗者": "决斗", "决斗": "决斗",
    "先锋": "先锋",
    "控场者": "控场", "控场": "控场", "烟位": "控场",
    "哨卫": "哨卫", "哨位": "哨卫", "哨兵": "哨卫",
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", (text or "")).lower()


def _hero_alias_map():
    m = {}
    for h in HEROES:
        keys = list(h.get("aliases") or []) + [h.get("cn", ""), h.get("en", ""), h.get("developerName", "")]
        for k in keys:
            if k:
                m[_norm(str(k))] = h
    for en, keys in (ALIASES.get("heroes") or {}).items():
        h = next((x for x in HEROES if x.get("en") == en), None)
        if h:
            for k in keys:
                m[_norm(str(k))] = h
    return m


def _weapon_alias_map():
    m = {}
    for w in WEAPONS:
        for k in [w.get("cn", ""), w.get("en", "")]:
            if k:
                m[_norm(str(k))] = w
    for en, keys in (ALIASES.get("weapons") or {}).items():
        w = next((x for x in WEAPONS if x.get("en") == en), None)
        if w:
            for k in keys:
                m[_norm(str(k))] = w
    return m


HERO_ALIASES = _hero_alias_map()
WEAPON_ALIASES = _weapon_alias_map()


def _map_alias_map():
    m = {}
    for mp in MAPS:
        for k in [mp.get("cn", ""), mp.get("en", "")]:
            if k:
                m[_norm(str(k))] = mp
    for en, keys in (ALIASES.get("maps") or {}).items():
        mp = next((x for x in MAPS if x.get("en") == en), None)
        if mp:
            for k in keys:
                m[_norm(str(k))] = mp
    return m


MAP_ALIASES = _map_alias_map()


def _role(role: str) -> str:
    return ROLE_ALIASES.get((role or "").strip(), (role or "").strip())


def _find_hero(question: str):
    q = _norm(question)
    best = None
    for alias, h in HERO_ALIASES.items():
        if alias and alias in q:
            if best is None or len(alias) > len(best[0]):
                best = (alias, h)
    return best[1] if best else None


def _find_weapon(question: str):
    q = _norm(question)
    best = None
    for alias, w in WEAPON_ALIASES.items():
        if alias and alias in q:
            if best is None or len(alias) > len(best[0]):
                best = (alias, w)
    return best[1] if best else None


def expand_query_aliases(question: str) -> str:
    """把用户输入中的外号/拼音/错别字扩展为官方名，附加到检索 query 后面。"""
    extras = []
    for alias, h in HERO_ALIASES.items():
        if alias and alias in _norm(question):
            extras.extend([h.get("cn", ""), h.get("en", "")])
    for alias, w in WEAPON_ALIASES.items():
        if alias and alias in _norm(question):
            extras.extend([w.get("cn", ""), w.get("en", "")])
    for alias, mp in MAP_ALIASES.items():
        if alias and alias in _norm(question):
            extras.extend([mp.get("cn", ""), mp.get("en", "")])
    extras = [x for x in extras if x]
    if extras:
        return question + " " + " ".join(dict.fromkeys(extras))
    return question


def _is_hero_list_question(q: str) -> bool:
    return any(k in q for k in ["全部英雄", "所有英雄", "英雄列表", "英雄大全", "英雄汇总", "有哪些英雄", "多少英雄", "几个英雄", "全英雄", "英雄都有谁", "多少个英雄", "个英雄"])


def _is_map_list_question(q: str) -> bool:
    return any(k in q for k in ["全部地图", "所有地图", "地图列表", "地图大全", "有哪些地图", "多少张地图", "地图池", "地图汇总"])


def _is_weapon_list_question(q: str) -> bool:
    return any(k in q for k in ["全部武器", "所有武器", "武器列表", "武器大全", "武器汇总", "枪械列表", "有哪些武器", "武器价格表", "武器价格", "多少把武器", "多少武器", "把武器"])


def _render_hero_table(heroes):
    lines = ["| 官方定位 | 国服ID | 英文ID | 别名 |", "|---|---|---|---|"]
    for h in heroes:
        aliases = "、".join([x for x in h.get("aliases", []) if x not in {h.get("cn"), h.get("en")}][:6])
        lines.append(f"| {h.get('role','')} | {h.get('cn','')} | {h.get('en','')} | {aliases or '—'} |")
    lines.append("")
    lines.append("> 数据来源：`knowledge_base/heroes.json`（valorant-api.com zh-CN）")
    return "\n".join(lines)


def answer_hero_query(q: str):
    if _is_hero_list_question(q):
        role = next((r for a, r in ROLE_ALIASES.items() if a in q), None)
        heroes = [h for h in HEROES if not role or _role(h.get("role")) == role]
        title = f"### {role or '全部'}英雄（共 {len(heroes)} 位）"
        return title + "\n\n" + _render_hero_table(heroes)
    if any(k in q for k in ["什么定位", "什么位置", "定位是", "是啥定位", "打什么位置"]):
        h = _find_hero(q)
        if h:
            return f"**{h.get('cn')} / {h.get('en')}** 的官方定位是：**{h.get('role')}**（社区常说：{h.get('role_community')}）。\n\n> 数据来源：`knowledge_base/heroes.json`"
    return None


def _render_weapon_table(weapons):
    lines = ["| 武器 | 英文 | 价格 | 近距离头/身体/腿 |", "|---|---|---|---|"]
    for w in weapons:
        dr = (w.get("damageRanges") or [{}])[0]
        dmg = f"{dr.get('headDamage','?')} / {dr.get('bodyDamage','?')} / {dr.get('legDamage','?')}"
        lines.append(f"| {w.get('cn','')} | {w.get('en','')} | {w.get('cost','—')} | {dmg} |")
    lines.append("")
    lines.append("> 数据来源：`knowledge_base/weapons.json`（valorant-api.com zh-CN）")
    return "\n".join(lines)


def answer_weapon_query(q: str):
    if _is_weapon_list_question(q):
        return f"### 武器价格与基础伤害（共 {len(WEAPONS)} 把）\n\n" + _render_weapon_table(WEAPONS)
    w = _find_weapon(q)
    if not w:
        return None
    if any(k in q for k in ["多少钱", "价格", "售价", "买", "要钱", "免费", "cost", "price"]):
        return f"**{w.get('cn')} / {w.get('en')}** 的价格是 **{w.get('cost')}** 信用点。\n\n> 数据来源：`knowledge_base/weapons.json`"
    if any(k in q for k in ["伤害", "伤害值", "打身体", "打头", "爆头"]):
        dr = (w.get("damageRanges") or [{}])[0]
        return (f"**{w.get('cn')} / {w.get('en')}** 近距离伤害："
                f"头 **{dr.get('headDamage','?')}** / 身体 **{dr.get('bodyDamage','?')}** / 腿 **{dr.get('legDamage','?')}**。\n\n"
                f"> 数据来源：`knowledge_base/weapons.json`（不同距离有伤害衰减）")
    return None


def answer_map_query(q: str):
    if _is_map_list_question(q):
        rows = [f"- {m.get('cn')} / {m.get('en')}" for m in MAPS if m.get("en") not in {"Skirmish A", "Skirmish B", "Skirmish C", "Skirmish D", "Skirmish E", "Basic Training", "The Range", "District", "Kasbah", "Drift", "Glitch", "Piazza"}]
        return "### 当前地图\n\n" + "\n".join(rows) + "\n\n> 数据来源：`knowledge_base/maps.json`（valorant-api.com zh-CN）"
    return None


def answer_official_data_query(q: str):
    for fn in (answer_hero_query, guide_hero, answer_weapon_query, answer_map_query):
        ans = fn(q)
        if ans:
            return ans
    return None


GUIDE_KEYWORDS = ["怎么玩", "咋玩", "玩法", "攻略", "技能", "教学", "怎么用", "厉害吗", "强吗", "怎么打"]
ROLE_TIPS = {
    "决斗": ["用位移/自愈技能制造对枪优势，不要无脑先手送。", "进点前等烟、闪、信息技能到位，再拉枪线。"],
    "先锋": ["先手侦察/闪光/震荡，给决斗者开路。", "技能命中后队友再进，别自己交完技能队友没跟上。"],
    "控场": ["烟封敌方枪线，不是封自己人的路。", "守包/下包阶段用毒、火、烟拖延拆包。"],
    "哨卫": ["陷阱和摄像头放在侧翼/包点入口，不要放在一眼就能被打掉的位置。", "设备被清掉后要及时换位，不要依赖设备硬守。"],
}


def guide_hero(q: str):
    """英雄怎么玩/技能/攻略：用官方结构化数据直接回答，不走 LLM。"""
    if not any(k in q for k in GUIDE_KEYWORDS):
        return None
    h = _find_hero(q)
    if not h:
        return None
    abilities = h.get("abilities") or {}
    lines = [
        f"**{h.get('cn')} / {h.get('en')}** 是 **{h.get('role')}**，社区常说 **{h.get('role_community')}**。",
        "",
        "技能：",
    ]
    for slot in ["C", "Q", "E", "X"]:
        ab = abilities.get(slot) or {}
        name = ab.get("name", "—")
        desc = (ab.get("desc", "") or "").replace("\n", " ").strip()
        lines.append(f"- **{slot} {name}**：{desc or '以游戏内说明为准'}")
    lines.append("")
    lines.append("玩法提示：")
    for t in ROLE_TIPS.get(h.get("role"), []):
        lines.append(f"- {t}")
    lines.append("")
    lines.append("> 数据来源：`knowledge_base/heroes.json`（valorant-api.com zh-CN）")
    return "\n".join(lines)


def replace_aliases_with_official(q: str) -> str:
    """把用户输入里的外号/拼音/错别字替换成官方名，供检索和 LLM prompt 使用。"""
    pairs = {}
    for alias, h in HERO_ALIASES.items():
        pairs[alias] = f"{h.get('cn')} / {h.get('en')}"
    for alias, w in WEAPON_ALIASES.items():
        pairs[alias] = f"{w.get('cn')} / {w.get('en')}"
    for alias, mp in MAP_ALIASES.items():
        pairs[alias] = f"{mp.get('cn')} / {mp.get('en')}"
    aliases = sorted(pairs.keys(), key=len, reverse=True)
    pattern = re.compile("|".join(re.escape(a) for a in aliases if a), re.IGNORECASE)

    def repl(m):
        return pairs.get(m.group(0).lower(), m.group(0))

    return pattern.sub(repl, q)
