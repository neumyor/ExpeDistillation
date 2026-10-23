# 找证据模块（evidence）

按任务清单收集写作需要的材料，整理成统一的 fact-only 证据包。它只报告事实，
不决定对技能做什么，也不写技能。

## `segment`：切段找证据（P02 add）

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
| `retry_limit` | 切出来的段不合格时最多重切几次；用完这条轨迹就跳过 | 0 或正整数 | `2` |
| `write_lessons` | add 时是否也从弱模型失败片段学 | `true` 强、弱轨迹都切；`false` 只切强轨迹 | `true` |

## 采集器

| 采集器 | 用途 | 证据包里有什么 | 调用模型 |
|---|---|---|---|
| `add_path.collect_add_evidence` | P02 add | 强模型成功段、弱模型失败段（`write_lessons` 开着时） | 不调用（切段由 `segment` 驱动） |
| `exam_sheet.collect_exam_evidence` | exam revise | 考题、弱模型答案、判卷理由 | 不调用 |

P04/P06 的 live 归因不在这个 family：新路径见
`src/expedistill/methods/attribution/group.py`，它每 task 调一次
`LIVE_USAGE_ATTRIBUTION`，产出同样的 fact-only 证据包。

旧配置里的 `group_evidence` / `group_author` 已随 usage 策略一起移除；
P04/P06 只读 `decision`。

证据从哪来，由任务清单的动作和验收方式自动决定：

| 任务清单动作 | 证据包里有什么 |
|---|---|
| 新增（对象是任务） | 摸底轨迹切成的片段 |
| 修改，纸面验收 | 考题、弱模型答案、判卷说明 |
| 修改，实战验收 | 失败轨迹、组诊断、每条技能的相关/应用事实 |

注意：

- 挑轨迹、留片段由全局规则 `rules.trajectory_select` / `rules.segment_select`
  决定；仓库配置用的 `strong_success_weak_failed` 只留强成功段和弱失败段。
- 只有 add 行才切段；只做验收修改的阶段不花切段的钱。

模块边界见 `docs/decisions/execute_learn_module_boundaries_20261016.md`、
`docs/decisions/unified_skill_structure_20261019.md` 和
`docs/decisions/p04_decision_strategy_split_20261023.md`。
