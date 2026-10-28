# 写作模块（author）

按决策结果和证据包写内容。它不判断技能好不好用，不做 keep/delete 决策，
也不做测量。

## `distill_diagnose`：P02 的提炼 / 诊断写作

```yaml
author:
  name: distill_diagnose
```

没有参数。P02 的 add 行用它：

| 任务清单动作 | 做什么 | 模型 |
|---|---|---|
| 新增 | 每个片段写一条新技能；强模型成功段用提炼，弱模型失败段用诊断；两种都产出三段结构 `[When to use]` / `[Steps]` / `[Do not use when]` | `author` |

## `cluster_by_trigger`：跨 task 的 trigger 族聚类写作

```yaml
author:
  name: cluster_by_trigger
  max_cards_per_prompt: 40
  max_chars_per_card: 1200
  min_cluster_size: 1
  classify_retry_limit: 2
  cluster_retry_limit: 2
  skill_retry_limit: 2
```

只用在 P02 的 `add` 路径，仍然只处理 `add` 行授权的 task 和
`segment_select` 保留的段；决策、归因、整理三条链路不变。

流程：

1. 对每个 subtask 调强模型分成五个固定 trigger 族：
   `at_task_start` / `after_first_environment_check` /
   `when_command_fails` / `before_submitting` / `at_any_step`；
2. 每个族内把所有 subtask 卡片拼给强模型，按“遇到的问题相似”聚类；
   聚类输出 JSON，必须覆盖族内每个 subtask 恰好一次；
3. 每个 cluster 调强模型写一条三段式 skill；锚点取 cluster 中最小
   `subtask_id`，`source_subtasks` 记录全部成员；
4. 中间结果落在学习相位的 `trigger_families.jsonl`、`clusters.jsonl`、
   `cluster_skills.jsonl`、`cluster_outcomes.jsonl`、
   `cluster_summary.json`；已完成的 subtask/family/cluster 续跑只读盘。

单条 skill 仍是 `pending`、`version=1`，由后续 live/exam 路径正常验收。
`source_subtasks` 只是来源记录；live 的分组仍按锚点 subtask 所在的 task
进行，因此一个跨 task cluster 在 live 上只在锚点 task 组里被测量，
P07 的全局 folder 仍会交付它。`cluster_summary.json` / `learn_summary.json`
用 `uncovered_add_tasks` 报告“被授权 add 但没有锚点技能”的任务，逐条给出
task id、add 行序号、基线轨迹里是否至少一次强模型判对且一次弱模型判错、
被哪些 cluster 吸收；这是只读报告，不改变分组或投递，`failed_tasks` 也不
与之混用。`eligible` 统计的是基线 trajectory 的判分，不是被选中的段。

## P04/P06 的 revise 写作

新的共享写作入口是 `revise.py` 的 `revise_one`：

- 决策已经给出 per-skill 的 `revise`，这里只写内容；
- 输入是 `SkillAttemptEvidence`（逐 attempt、逐窗口的证据），不再读
  group diagnosis / failure_view / retrigger 判断；
- 多个窗口的 X 可能冲突，由写作模型自己综合；
- 整条重写，保留三段结构；
- 改写失败或输出与原文相同 = `unchanged`，版本不动。

## `revise_group`：exam 兼容路径

`write.py` 的 `revise_group` 仍保留给 exam 的考卷证据路径：它对每个包调用
`rewrite_skill`，不再有 `default_decide` 的 per-skill 策略。

## 参数

| 参数 | 含义 | 默认 |
|---|---|---|
| `use_weak_profile` | 是否把弱模型画像送进写作/改写提示（实验级配置，不在 author family） | `false` |
| `evidence.write_lessons` | P02 是否从弱模型失败段学 | `true` |

设计取舍见 `docs/decisions/p04_decision_strategy_split_20261023.md`、
`docs/decisions/skill_structure_three_sections_20261026.md`、
`docs/decisions/unified_skill_structure_20261019.md`（后者已过时，仅作历史记录）。
