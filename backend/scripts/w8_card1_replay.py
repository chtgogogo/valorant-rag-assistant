# 【W8-卡1】重放实验：用真实检索管线重放「晚安焰火」幻觉原题
# ------------------------------------------------------------
# 目的：阶段 0 溯源遗留动作——当时（用户实测）的召回内容没有留痕，
#       无法判断"模型编造时检索送了什么"。本脚本用真实管线补齐这一环，
#       同时作为 Agent 路径的检索质量基线。
# 用法：.venv/Scripts/python.exe backend/scripts/w8_card1_replay.py
# 说明：强制 CPU（ZCode 沙箱 CUDA 环境下 torch 会崩，pitfall-3）；
#       --agent 开关额外用真模型跑 Agent 循环（需要 ZHIPU_API_KEY 与外网）。
# ------------------------------------------------------------
import os
import sys
from pathlib import Path

os.environ["EMBEDDING_DEVICE"] = "cpu"  # 官方设备开关（device_manager 显式优先）：沙箱内 CUDA 状态不稳，实验强制 CPU 保证可复现
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(BACKEND.parent / ".env")  # 仓库根 .env（ZHIPU_API_KEY）

from services.hybrid_retriever import hybrid_search  # noqa: E402
from services.reranker import rerank  # noqa: E402

# 用户 09-26 实测的原问题（溯源结论第 1 节）
QUESTION = ("捷风 / Jett 和 雷兹 / Raze 都是 决斗 类型的英雄，"
            "但他们在玩法和技能上有所不同。")


def replay_retrieval() -> bool:
    print("=" * 60)
    print("重放 1：真实检索管线（hybrid_search → rerank）")
    print("=" * 60)
    results = rerank(QUESTION, hybrid_search(QUESTION, kb_id="valorant"))
    hit_flamethrower = False
    for i, r in enumerate(results, start=1):
        has_firework = "晚安焰火" in r.content
        # 原文正确描述的判定词：火箭（数据层已核实为正确表述）
        correct = ("火箭" in r.content) and has_firework
        hit_flamethrower = hit_flamethrower or correct
        flag = " ← 含晚安焰火" + ("（正确描述：火箭发射器）" if correct else "")
        print(f"\n[S{i}] score={r.score:.3f} source={r.source}{flag}")
        print("    " + r.content[:150].replace("\n", " ") + "…")
    print("\n----- 判定 -----")
    if hit_flamethrower:
        print("召回中含『晚安焰火=火箭发射器』的正确描述：模型幻觉时手边就有正确资料")
        print("→ 定性：生成层（未照料说），与阶段 0 主结论一致")
    else:
        print("召回中【不含】晚安焰火的正确描述：当时模型很可能在缺料状态下凭记忆作答")
        print("→ 定性修正：检索+生成复合失败（按溯源结论的推翻条件执行）")
    return hit_flamethrower


def run_agent_live() -> None:
    print("\n" + "=" * 60)
    print("重放 2：Agent 循环真跑（GLM + 真实检索）")
    print("=" * 60)
    from services.agent.loop import run_agent
    out = run_agent(QUESTION, kb_id="valorant")
    print(f"\n步数轨迹（{len(out['steps'])} 步，degraded={out['degraded']}）：")
    for s in out["steps"]:
        print(f"  step{s['step']} [{s['type']}] {s.get('tool') or ''} "
              f"{(s.get('args') or {}).get('query', '')} → {_short(s['summary'])}")
    print(f"\n来源 {len(out['sources'])} 个：{[s['name'] for s in out['sources']]}")
    print(f"\n----- 最终答案 -----\n{out['answer']}")


def _short(t: str, n: int = 60) -> str:
    return t[:n].replace("\n", " ")


if __name__ == "__main__":
    hit = replay_retrieval()
    if "--agent" in sys.argv:
        run_agent_live()
    else:
        print("\n（加 --agent 参数可追加真模型 Agent 循环重放）")
