# 审查模块（review）

P02/P04/P06 共用的审查方法族。它把原来的决策、归因、找证据三个 learn 槽位合并成
一个入口：先决定每个测量目标要做什么，再只给需要写作的行收集证据。

## 输入

```text
review(
    current=PhaseReport,       # 当前执行相位的 AnswerSheet + UnitSummary
    control=PhaseReport|None,  # baseline 对照；P02 与 fresh_bare 可省略
    bank=Skill[],              # 上一学习相位的库快照
    context=ReviewContext,     # 相位、轮次、剩余检查次数、交付方式、规则名
    params=ReviewParams,       # 决策策略与证据预算
    store=Store,
    handles={...},             # segmenter / author 两个模型句柄
    usage=..., run_dir=...,
)
```

`PhaseReport` 已包含 trial 级 `AnswerSheet` 和聚合后的 `UnitSummary`；审查不重复
构造这两类对象。`current.summary["better_by_task"]` 是 live 轮次已经写下的组级结论。

## 输出

```text
ReviewResult
  items: ReviewItem[]          # 每行 TaskListRow + 可选的证据包
  failed_targets: set[str]     # 基础设施失败的目标
  model_errors: set[str]       # 模型输出失败的目标
  superseded_units: list[str]  # exam 多版本合并的被取代行
```

`ReviewItem.evidence` 的取值：

| 动作 | 证据 |
|---|---|
| `add` | `EvidencePackage`（`source_experiences` 是切好的段落） |
| `revise` + live | `SkillAttemptEvidence`（每个 skill 的归因窗口） |
| `revise` + exam | `EvidencePackage`（考题、弱模型答案、判卷理由） |
| `keep` / `delete` | `None`，不调用模型 |

## 路径

- **baseline**：`decision.decide_task_list` 的 baseline-gap 路径，输出 task 级 `add`；
- **live**：`strategies.skill_level` 或 `task_level`，输出 skill 级 keep / revise / delete；
- **exam**：`decision.decide_task_list` 后做 `strategies.merge_exam_rows`，输出合并后的 skill 行。

`keep` / `delete` 不跑归因；`revise` 才调用 `attribution.collect_skill_evidence`。
已经在本相位完成改写的目标由 `learn_ledger.revision_skip_targets` 过滤，续跑不会重复
收集证据。

## 配置

```yaml
review:
  name: review
  decision: skill_level          # task_level / skill_level
  unread_in_won_group: delete    # delete / keep
  before_steps: 10
  after_steps: 10
  K: 5
  retry_limit: 2
  write_lessons: true
```

P02 的 add 路径只使用 `K` / `retry_limit` / `write_lessons`；P04/P06 使用
`decision` / `unread_in_won_group` / `before_steps` / `after_steps`。所有学习相位共用
一份 `review` 配置时，未使用的字段保持默认值即可。

## 边界

审查不写任务清单、不写技能、不提交版本。落地仍在 `stages/learn_add.py`、
`stages/learn_apply.py`、`stages/learn_exam.py`；写作仍在 `methods/author/`；整理仍在
`methods/curation/`。
