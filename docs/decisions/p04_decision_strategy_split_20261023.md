# 决定：P04 决策拆成两种策略，归因与写作共享

> 2026-10-23 19:42 更新：本文件的 group 归因方案已被
> `p04_attempt_window_attribution_20261023.md` 的“触发窗口归因”取代；
> 决策层的 task_level / skill_level 仍然有效，归因与证据形状以新文件为准。

日期：2026-10-23
一句话：把 P04/P06 的学习决策从旧的 group + per-skill policy 管道拆成独立的 per-skill 决策模块，支持 `task_level` / `skill_level` 两种策略，归因与写作共用；P02 的 add 路径保持不变。
对应 log.md：2026-10-23 17:04 条目。

## 背景

旧 P04 的决策分两层：`decision.decide_task_list` 先做任务组级 keep/revise/delete，
`usage_revise` 再在 revise 组内部按 usage 事实做 per-skill keep/retrigger/revise/
delete/observe。策略散在两个模块里，`usage_group` 还同时承担“是否归因”的判断。
用户裁定旧的 per-skill 策略整体 deprecated，包括 `usage_group` / `usage_revise`。

## 决定（用户 2026-10-23 裁定）

1. **决策输出统一为 per-skill 的 JSONL**：每行是一个 skill 的
   `keep` / `revise` / `delete`。写在相位的 `task_list.jsonl`（沿用现有账本名）。
2. **两种策略，通过 `phases.learn.decision.name` 切换**：
   - `task_level`：沿用原任务组级五种情况，最后把组动作展开到每个成员；
   - `skill_level`：按 usage 事实决定：至少被 cat 过一次 → `revise`；
     从未被读 → `delete`。
3. **安全边界**：usage 缺失、`delivery_status != ok`、或技能不在 delivered
   manifest 里，统一视为交付不可信；不删，输出 `keep`（原因
   `unverified_delivery`）。这里的 `keep` 沿用决策词表的“保留/接受”语义。
4. **归因与写作共享**：新的 `methods/attribution/group.py` 每个 task 只调一次
   `LIVE_USAGE_ATTRIBUTION`，产出 fact-only 包；新的
   `methods/author/revise.py` 只写内容，不再决定 keep/delete。`retrigger`
   作为 `revise` 的内部子模式保留（相关、从未读、应用 unclear 时只改 summary
   和 `[When to use]`）。
5. **P02 的 add 路径不变**：`stages/learn_add.py` 继续处理 task 级 add；
   `stages/learn_apply.py` 只处理 P04/P06 的 per-skill 行；exam 路径保留在
   `stages/learn_exam.py`，继续使用多版本 merge 和考卷证据。

## 模块边界

```text
composites/decision.py             共享动作词表、table_action、baseline/exam 规则
composites/decision_strategies.py  task_level / skill_level → per-skill rows
methods/attribution/group.py       每 task 一次归因，fact-only packages
methods/author/revise.py           每 skill 一次写作，无策略
stages/learn_add.py                P02 add
stages/learn_apply.py              P04/P06 per-skill apply
stages/learn_exam.py               exam 多版本路径
stages/learn_ledger.py             共享账本、回执、版本守卫
```

删除了 `methods/evidence/usage_group.py`、`methods/author/usage_revise.py`、
`UsageGroupParams`、`UsageReviseParams` 及对应 registry 项和测试。
`methods/evidence/package.py` 保留为兼容 re-export，实际类移到
`composites/evidence_package.py`，避免 methods 族之间互相 import。

## 影响

- `task_list.jsonl` 在 P04/P06 从每 task 一行变成每 skill 一行；
  `learn_summary.task_list` 的计数也随之变成 per-skill。
- P02 的 `task_list.jsonl` 仍是 task 级 add 行。
- 旧 `usage_group` / `usage_revise` 配置值不再合法；当前实验配置
  `tb21_split_folder_usage_20261022.yaml` 已改用 `decision: {name: task_level}`。
- 旧 run 的 P04 结果不能与新代码的 P04 直接比较：决策粒度从 group 变成 skill。
- `group_evidence` / `group_author` 字段已随旧 usage 策略一起从配置移除；
  P04/P06 只读 `decision`。
