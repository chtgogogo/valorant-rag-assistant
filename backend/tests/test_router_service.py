# 【W8-卡3】规则路由器回归测试：显式多跳/对比/统计/操作 → Agent；
# 单跳事实/模糊/寒暄/边界题 → Workflow（拍板 D3：拿不准默认 Workflow）；
# 评测集 eval_set_agent.json 全量一致性（防改规则时悄悄弄破评测集）。
import json
from pathlib import Path

import pytest

from services.router_service import ROUTE_AGENT, ROUTE_WORKFLOW, RouteDecision, route_query

AGENT_SET = Path(__file__).resolve().parent.parent / "eval" / "eval_set_agent.json"


class TestAgentRoute:
    @pytest.mark.parametrize("q", [
        "捷风和雷兹都是决斗类型英雄，玩法和技能有什么不同",  # 阶段0原题
        "狂徒和幻影哪个更好用？",
        "决斗者和先锋的定位区别是什么？",
        "取保候审和缓刑有什么区别？",  # 法律域
        "游戏里一共有多少种武器？",  # 统计
        "帮我查一下捷风的技能，并且告诉我雷兹的大招",  # 操作复合
        "哪个伤害更高的枪适合新手？",  # 弱信号正则：哪个…高
    ])
    def test_multi_hop_goes_agent(self, q):
        d = route_query(q)
        assert d.route == ROUTE_AGENT
        assert d.reason, "Agent 决策必须带可解释 reason"


class TestWorkflowRoute:
    @pytest.mark.parametrize("q", [
        "捷风的大招是什么？",  # 单跳事实
        "狂徒多少钱？",  # 单跳价格
        "捷风有几个技能？",  # 边界：'几个'不上表（单跳事实非统计）
        "决斗者有哪些？",  # 边界：'有哪些'不上表（单跳列举）
        "这个怎么弄？",  # 模糊指代
        "你好呀",  # 寒暄
    ])
    def test_single_hop_stays_workflow(self, q):
        d = route_query(q)
        assert d.route == ROUTE_WORKFLOW, f"'{q}' 被误路由：{d.reason}"


class TestDefault:
    def test_empty_and_none_safe(self):
        """空串/None 不崩且默认 Workflow（fail-safe）"""
        assert route_query("").route == ROUTE_WORKFLOW
        assert route_query(None).route == ROUTE_WORKFLOW

    def test_decision_shape(self):
        d = route_query("捷风大招是什么")
        assert isinstance(d, RouteDecision)
        assert d.route == ROUTE_WORKFLOW and "默认" in d.reason


class TestEvalSetConsistency:
    def test_eval_set_matches_router(self):
        """评测集 16 题 expect_route 与路由器实际判定全一致（路由准确率雏形；真实误路由率待接线后用提问日志测）"""
        data = json.loads(AGENT_SET.read_text(encoding="utf-8"))
        cases = data["cases"]
        agent_cases = [c for c in cases if c["expect_route"] == "agent"]
        assert len(agent_cases) >= 10, "任务卡要求 ≥10 道 Agent 题"
        for c in cases:
            d = route_query(c["question"])
            assert d.route == c["expect_route"], (
                f"{c['id']} '{c['question']}' 期望 {c['expect_route']} 实际 {d.route}（{d.reason}）")
