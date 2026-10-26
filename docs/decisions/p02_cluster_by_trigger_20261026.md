# 决定：P02 增加 cluster-by-trigger 写作路径

日期：2026-10-26
一句话：在 P02 的 add 写作上增加一个按 trigger 族跨 task 聚类、每类写一条 skill 的新 author 方法，现有 `distill_diagnose` 路径保持原样。
对应 log.md：2026-10-26 20:38 的“P02 新增 cluster-by-trigger 写作路径”条目。

## 背景

现有 P02 是 task-scoped：一个 add 行对应一个 task，`learn_add` 按 task 切段、
收证据，`author.write_new` 每段写一条 skill。新的需求是跨所有 add-authorized
task 的 subtask 先按 trigger 时机分五族，再在族内按“遇到的问题相似”聚类，
每个 cluster 写一条 skill。这样可以共享不同 task 之间的相似经验，而不是每个
task 各自写一条。

## 决定

1. 新增 author 方法 `cluster_by_trigger`，参数类
   `ClusterAuthoringParams`；只由 P02 的 add 分支选中，现有
   `distill_diagnose` 行为和文件路径不变。
2. 新增 phase-level 编排 `stages/learn_add_cluster.py`；`learn_add` 只在
   `author.name == "cluster_by_trigger"` 时转交，仍先写同一份
   `task_list.jsonl`。
3. 范围只覆盖 `add` 行授权的 task，以及 `segment_select` 保留的段；不绕过
   baseline-gap 决策，不改变 P04/P06 的决策、归因和 P06 的整理。
4. 五族固定为 `at_task_start` / `after_first_environment_check` /
   `when_command_fails` / `before_submitting` / `at_any_step`；每个 subtask
   只属于一族。
5. 聚类输出 JSON 为源：`theme`、`subtask_ids`（必须完整覆盖当前输入）、
   `reason`；Markdown 只作为人读视图。cluster id 由 family + 排序后的成员
   subtask id 的 sha256 前 12 位生成，不由模型命名。
6. 每个 cluster 写一条 Skill；锚点取排序后最小的 `subtask_id`，因此
   `skill_id == make_skill_id(subtask_id)` 仍满足
   `ids.is_stage3_candidate`。完整成员写入 `Skill.source_subtasks`。
7. 中间工件：`trigger_families.jsonl`、`clusters.jsonl`、
   `cluster_skills.jsonl`、`cluster_outcomes.jsonl`、`cluster_summary.json`。
   每个 subtask / family / cluster 都可从盘上恢复；`added` /
   `skipped_existing` 是终态，`failed` / `infra_failed` 可重试。
8. 新增 prompt `CLUSTER_TRIGGER_FAMILY`、`CLUSTER_SUBTASKS`、
   `CLUSTER_SKILL`。按现有 B2 全量冻结规则，它们进入每个 run 的 manifest
   与 golden；旧 run 不能靠旧 manifest 直接续跑，这是新实验身份。

## 已知边界

- cluster skill 在 live 上只属于锚点 subtask 所在的 task 组；`source_subtasks`
  是来源记录，不扩展 live 的分组。P07 的全局 folder 仍会交付它。
- v1 在一个 family 超过 `max_cards_per_prompt` 时按 subtask id 分 chunk
  聚类，不做跨 chunk 的二次合并；五个 family 之间、family 内 chunk 之间
  都并行，family 内并发额度按 run 的 `workers` 分摊。
- 分类/聚类是模型输出；同一个 run 内以盘上首份有效结果为准，重跑不会重新
  聚类已完成的 family。
- P02 的 `outcomes.jsonl` 仍是 task 级跳过回执；cluster skill 的回执单独写在
  `cluster_outcomes.jsonl`，避免把 task 和 cluster 两种 target 混在一起。
