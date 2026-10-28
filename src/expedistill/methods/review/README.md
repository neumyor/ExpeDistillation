# 审查模块（review）

P02/P04/P06 共用的审查方法族。它把原来的决策、归因、找证据三个 learn 槽位合并成
一个入口：先决定每个测量目标要做什么，再只给需要写作的行收集证据。`trial_level`
另外提供逐失败 trial 分析和跨 task 的新 skill 需求。

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
    skip_evidence=...,        # 已结算的 revise 目标
    existing_findings=...,    # trial_level 的续跑缓存
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
  trial_findings: TrialFinding[]
  new_skill_requirements: NewSkillRequirement[]
  profile_update: str | None
```

`ReviewItem.evidence` 的取值：

| 动作 | 证据 |
|---|---|
| `add` | `EvidencePackage`（`source_experiences` 是切好的段落） |
| `revise` + live | `SkillAttemptEvidence`（每个 skill 的归因窗口） |
| `revise` + exam | `EvidencePackage`（场景、期望行为、打开的技能与计划、判卷理由） |
| `keep` / `delete` | `None`，不调用模型 |
| `trial_level` 新需求 | `NewSkillRequirement`，每个需求携带 `EvidencePackage` |

## 路径

- **baseline**：`decision.decide_task_list` 的 baseline-gap 路径，输出 task 级 `add`；
- **live**：`strategies.skill_level` 或 `task_level`，输出 skill 级 keep / revise / delete；
- **trial_level**：对每个失败 trial 并发调用 `TRIAL_FAILURE_ANALYSIS`，再由
  `TRIAL_REQUIREMENT_AGGREGATION` 合并成 skill 动作和跨 task 的新 skill 需求；
  `PROFILE_UPDATE` 累积模型画像；
- **exam**：`decision.decide_task_list` 后做 `strategies.merge_exam_rows`，输出合并后的 skill 行。

`review()` 统一分发旧策略和 `trial_level`。`keep` / `delete` 不跑归因；旧策略的
`revise` 才调用 `attribution.collect_skill_evidence`。已经在本相位完成改写的目标由
`learn_ledger.revision_skip_targets` 通过 `skip_evidence` 过滤；`trial_level` 在聚合后
用同一回调去掉已结算的 revise 行。

### trial_level 输出

`ReviewResult` 额外携带：

- `trial_findings`：每条失败 trial 的自由文本原因和 `profile_note`；
- `new_skill_requirements`：聚合后的需求文档，每个需求对应一个新增 skill，
  与 task/trial 解耦；
- `profile_update`：本相位累积后的模型画像文本。

画像工件初始为空，写在 `phases/pNN_learn/model_profile.json`，下一学习相位继续读取。

`TRIAL_FAILURE_ANALYSIS` 看到的是整条失败 trial（每步 observation 按 benchmark
的 `view_rules` 截断，不是按窗口切分）和 `all_skills` 里的整个库。
它的 `available_skills` 是**交付**清单，不是实际 `cat` 清单；实际读取事实由
`composites.skill_usage` 现算，目前不进入该 prompt。模型返回的 skill id 会先用
folder manifest 的别名归一化（目录名、文件名、`-SKILL.md` 形式都映射回完整 id），
否则一条 trial 的 finding 不会因为模型写了文件夹名而整条丢失。

`trial_level` 的长输入角色要求：

- `author`：`TRIAL_FAILURE_ANALYSIS` 的整条 trial + 整库 skill 卡；
- `rewriter`：`SKILL_ATTEMPT_REVISION` 的全部证据窗口。

两者在 `stage0_freeze` 里要求 `max_input_tokens >= 524288`；配置没声明或小于
512K 时在冻结步直接拒绝。该值是配置预算，端点本身的实际上下文仍须不小于它。

## 配置

```yaml
review:
  name: review
  decision: skill_level          # task_level / skill_level / trial_level
  unread_in_won_group: delete    # delete / keep
  before_steps: 10
  after_steps: 10
  K: 5
  retry_limit: 2
  write_lessons: true
```

P02 的 add 路径只使用 `K` / `retry_limit` / `write_lessons`；P04/P06 使用
`decision` / `unread_in_won_group` / `before_steps` / `after_steps`。`trial_level`
只使用 `decision` 和模型句柄，画像从空文本开始累积；配置里的 `weak_profile`
不参与该策略。所有学习相位共用一份 `review` 配置时，未使用的字段保持默认值即可。

## 边界

审查不写任务清单、不写技能、不提交版本。落地仍在 `stages/learn_add.py`、
`stages/learn_apply.py`、`stages/learn_exam.py`；`trial_level` 的新需求由
`stages/learn_requirement.py` 落地，写作由 `methods/author/` 完成；整理仍在
`methods/curation/`。画像工件由 `stages/model_profile.py` 读写。
