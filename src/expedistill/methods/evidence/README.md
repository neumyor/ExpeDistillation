# 找证据模块（evidence）

按决策给的任务清单，收集写作需要的材料，整理成统一格式的证据包。它只报告事实
（这次怎么错的、哪条技能出了什么问题），不决定对技能做什么，也不写技能。

每个学习阶段都会执行。新增路径的证据方法写在 `evidence` 里，组修订路径的证据方法
写在 `group_evidence` 里，各自用 `name` 选方法。

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

## `group_evidence`：组修订的证据方法

```yaml
group_evidence:
  name: usage_group      # 或不写，默认 live_group
```

| 方法 | 证据包里有什么 | 调用模型 |
|---|---|---|
| `live_group` | 基线状态、整组诊断、每条技能的问题、失败轨迹视图 | `author`（G10） |
| `usage_group` | 在 `live_group` 的事实上增加：读取次数、首次读取步、每次尝试是否读取、相关/应用判断、兄弟技能描述、模型未读时先做了什么 | `author`（usage 归因） |

`usage_group` 读取 live folder 轮次写下的 `skill_usage.jsonl`。投递失败
（manifest 不匹配，或既看不到列表也没有任何读取的 `unverified`）的组不会调用
归因模型，证据包里只带投递失败事实，写作阶段据此只做 `observe`。

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
`write_lessons` 的来由见 `docs/decisions/unified_skill_structure_20261019.md`，
usage 证据见 `docs/decisions/usage_aware_live_folder_20261022.md`。
