# 找证据模块（evidence）

按决策给的任务清单，收集写作需要的材料，整理成统一格式的证据包。它只报告事实
（这次怎么错的、哪条技能出了什么问题），不决定对技能做什么，也不写技能。

每个学习阶段都会执行。配置写在学习阶段的 `evidence` 里，`name` 选方法。

## `segment`：切段找证据

```yaml
evidence:
  name: segment
  K: 5                # 必填
  retry_limit: 2
  write_lessons: true
```

| 参数 | 含义 | 可选项 | 默认 |
|---|---|---|---|
| `K` | 一条轨迹最多切成几段 | 正整数 | 必填 |
| `retry_limit` | 切出来的段不合格（没覆盖全轨迹、越界等）时，最多重切几次；用完这条轨迹就跳过 | 0 或正整数 | `2` |
| `write_lessons` | 新增技能时，是否也从弱模型做失败的片段里学 | `true` 强、弱模型的轨迹都切，弱模型失败段交给写作写成教训；`false` 只切强模型轨迹 | `true` |

证据从哪来，由任务清单的动作和验收方式自动决定，不用配置：

| 任务清单动作 | 证据包里有什么 | 调用模型 |
|---|---|---|
| 新增（对象是一道题） | 摸底阶段这道题的轨迹切成的片段 | `segmenter`（切段） |
| 修改，纸面验收 | 考题、弱模型的答案、判卷说明 | 不调用 |
| 修改，实战验收 | 失败轨迹、整组诊断、每条技能的问题 | `author`（归因） |

注意：

- 挑哪几条轨迹、留哪些片段，**不在这里设**，由全局规则 `rules.trajectory_select` 和
  `rules.segment_select` 决定。仓库配置用的 `strong_success_weak_failed` 只留强模型成功段和弱模型失败段。
- 只有任务清单里有"新增"时才切段，只做验收修改的阶段不花切段的钱。

模块边界见 `docs/decisions/execute_learn_module_boundaries_20261016.md`，
`write_lessons` 的来由见 `docs/decisions/unified_skill_structure_20261019.md`。
