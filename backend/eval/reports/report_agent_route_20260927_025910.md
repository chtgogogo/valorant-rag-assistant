# W8-卡3 路由评测（2026-09-27 02:59:10）

- 评测集：eval_set_agent.json 16 题｜双路径对照：关
- **路由准确率：16/16 = 100%**

### 路由逐题判定

| id | 类型 | 期望 | 实判 | 结果 | 问题 |
|---|---|---|---|---|---|
| A01 | 对比 | agent | agent | ✓ | 捷风和雷兹都是决斗类型英雄，玩法和技能有什么不同 |
| A02 | 对比 | agent | agent | ✓ | 狂徒和幻影哪个更好用？ |
| A03 | 对比 | agent | agent | ✓ | 暴徒和正义的差异是什么？ |
| A04 | 对比 | agent | agent | ✓ | 捷风相比霓虹有什么优势？ |
| A05 | 对比 | agent | agent | ✓ | 决斗者和先锋的定位区别是什么？ |
| A06 | 对比(英文ID) | agent | agent | ✓ | Vandal和Phantom有什么区别？ |
| A07 | 统计 | agent | agent | ✓ | 游戏里一共有多少种武器？ |
| A08 | 统计 | agent | agent | ✓ | 伤害最高的枪是哪把？和其他步枪比排名如何？ |
| A09 | 操作复合 | agent | agent | ✓ | 帮我查一下捷风的技能，并且告诉我雷兹的大招 |
| A10 | 对比(法律域) | agent | agent | ✓ | 取保候审和缓刑有什么区别？ |
| W01 | 单跳事实 | workflow | workflow | ✓ | 捷风的大招是什么？ |
| W02 | 单跳事实 | workflow | workflow | ✓ | 狂徒多少钱？ |
| W03 | 模糊 | workflow | workflow | ✓ | 这个怎么弄？ |
| W04 | 寒暄 | workflow | workflow | ✓ | 你好呀 |
| W05 | 单跳边界 | workflow | workflow | ✓ | 决斗者有哪些？ |
| W06 | 单跳边界 | workflow | workflow | ✓ | 捷风有几个技能？ |

### 双路径事实错误对照

未运行（加 --with-llm 真跑，低峰期执行）。
