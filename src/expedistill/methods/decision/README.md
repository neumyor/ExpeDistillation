# 决策模块（decision）

P04/P06 的两种 per-skill 决策策略通过
`phases.learn.decision.name` 选择：

| 名字 | 参数 | 做什么 |
|---|---|---|
| `task_level` | `TaskLevelParams()` | 沿用组级五种情况，把组动作展开到每个成员 |
| `skill_level` | `SkillLevelParams(unread_in_won_group="delete")` | 触发且组赢（accepted）→ `keep` 不改写；触发且组没赢（pending）→ `revise`；从未读 → `delete`；usage 缺失、delivery 不可信或技能未在 manifest 中 → `keep`（原因 `unverified_delivery`）。`unread_in_won_group="keep"` 时，“未读但组赢”的 accepted 技能保留而非删除 |

两种策略都输出 `target_kind="skill"` 的 `TaskListRow`，动作只有
`keep` / `revise` / `delete`。实际纯函数在
`composites/decision_strategies.py`；本 family 只提供参数契约和名字校验。
