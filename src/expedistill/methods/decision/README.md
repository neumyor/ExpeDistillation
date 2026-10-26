# 决策模块（decision）

P04/P06 的两种 per-skill 决策策略通过
`phases.learn.decision.name` 选择：

| 名字 | 参数 | 做什么 |
|---|---|---|
| `task_level` | `TaskLevelParams()` | 沿用组级五种情况，把组动作展开到每个成员 |
| `skill_level` | `SkillLevelParams(unread_in_won_group="delete")` | 先按 unit 终态走共享动作表：control-perfect / not-testable 删除，leak 有余次改写到末次删除，zero_execution 不产行。正常单元：触发且组赢（accepted）→ `keep` 不改写；触发且组没赢（pending）→ 有余次 `revise`、末次 `delete(live_no_improvement)`；从未读 → `delete`；usage 缺失、delivery 不可信或技能未在 manifest 中 → `keep`（原因 `unverified_delivery`）。规则 1 复用的单元由 `carried_units.jsonl` 带进决策，并用原相位 sheets 补 usage。`unread_in_won_group="keep"` 时，“未读但组赢”的 accepted 技能保留而非删除 |

两种策略都输出 `target_kind="skill"` 的 `TaskListRow`，动作只有
`keep` / `revise` / `delete`。实际纯函数在
`composites/decision_strategies.py`；本 family 只提供参数契约和名字校验。
