# 归因模块（attribution）

P04/P06 的共享归因 driver。它只解释“为什么”，不决定 keep/revise/delete。

## `skill_attempts.collect_skill_evidence`

输入：task、目标 skill 列表、round 轨迹、`skill_usage.jsonl`、
task instruction、delivery 类型、folder manifest。

- folder / folder_by_task：`skill_use` 找出每个 skill 的 `cat` 调用；
  每个调用切前后窗口（默认前后 10 步）；
- group_by_task / whole_bank：没有真实 cat 调用，每个 attempt 只取开头
  窗口和结尾窗口，中间轨迹忽略；
- 同一窗口内的多条目标 skill 合并成一次归因调用；
- folder 的目录名/文件名（模型常混用）通过 `skill_aliases` 归一化到完整
  skill_id；窗口外技能条目忽略，当前窗口的期望技能仍必须全覆盖；
- 输出每个 skill 的 `SkillAttemptEvidence`，每个窗口含
  `trigger_context` / `behavior_after_trigger` / `improvement`。

task instruction 只取 P03 初始 prompt 的 `Task Description:` 段，避免把
Terminal agent 的 `analysis/plan/commands` 输出格式带进 attribution。
`blocks/attribution.py` 对业务契约失败（如 `skills: null`）重试 2 次。

参数：`AttributionParams(before_steps=10, after_steps=10)`。

它不读决策结果，不做 keep/delete，不写技能。写作交给
`methods/author/revise.py`；决策交给
`composites/decision_strategies.py`。
