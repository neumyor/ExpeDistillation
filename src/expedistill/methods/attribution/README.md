# 归因模块（attribution）

P04/P06 的共享归因 driver。它只解释“为什么”，不决定 keep/revise/delete。

## `group.collect_group_attribution`

每个 task 调一次 `LIVE_USAGE_ATTRIBUTION`：

- 输入：该 task 的成员技能、P01 baseline 事实、P03 轮次轨迹视图、
  `skill_usage.jsonl` 的读取/未读事实、兄弟技能描述、弱模型画像；
- 输出：每个成员一个 fact-only `EvidencePackage`，包含
  `group_diagnosis`、`skill_relevance`、`skill_application`、
  `skill_problem`、`skill_read_attempts`、`skill_usage`、
  `failure_view` 等事实；
- 不做 keep/delete，不写盘；投递失败或没有 usage 行时不调模型。

参数：`AttributionParams`（空）。

写作交给 `methods/author/revise.py`；决策交给
`composites/decision_strategies.py`。
