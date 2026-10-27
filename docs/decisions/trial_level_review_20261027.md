# 决定：新增 trial-level review

日期：2026-10-27
一句话：在 `methods/review` 下新增 `trial_level` 策略，逐条失败 trial 分析，
由一个聚合 agent 产出 skill 级动作与跨 task 的新 skill 需求，并累积弱模型画像。
对应 log.md：2026-10-27 的 trial-level review 实施条目。

## 背景

现有的 `skill_level` 把 live 组级结论展开到 skill，归因只在 revise 行收集。
它回答的是“这个 skill 要不要保留”，没有逐条 trial 的失败原因，也没有
模型自身能力边界、未调用 skill、缺少 skill 的区分。

新需求是：读取当前执行相位的所有 trial 级信息和当前 skill bank，并发分析
每条失败 trial，得到一个自由文本原因；把“模型学不会”的事实累积到画像，
把“skill 有问题”或“缺少 skill”的结论转成 skill 级动作和新 skill 需求。

## 决定

1. `ReviewParams.decision` 增加 `trial_level`；旧 `task_level` / `skill_level`
   行为不变。
2. 只分析 `grading.verdict == "failure"` 且 `execution` 存在的 trial；成功、
   invalid、无 execution 的 trial 不调用模型。
3. `TRIAL_FAILURE_ANALYSIS` 每条 trial 一次，并发执行；输出自由文本
   `reason`、`profile_note`、`new_skill_note` 和涉及的 skill id，不使用
   failure 枚举。
4. `TRIAL_REQUIREMENT_AGGREGATION` 每相位一次，读取所有 findings 和当前
   skill bank，输出：
   - 每个 skill 一条 keep / revise / delete；
   - 一份跨 task、跨 trial 的新 skill 需求文档，每个需求对应一个新增 skill。
5. 新需求对象 `NewSkillRequirement` 只保存 capability 描述、trigger、证据和
   `source_sheet_ids`；source id 是来源记录，不把需求绑回 task/trial。
6. `PROFILE_UPDATE` 每相位一次，输入旧画像和所有 profile_note，输出新的
   `profile_text`。画像工件放在 `phases/pNN_learn/model_profile.json`，初始为空；
   本相位写完后的 author / revise 使用新画像，下一相位继续读取。
7. `TRIAL_FAILURE_ANALYSIS` 和 `PROFILE_UPDATE` 的自由文本保持原文；最终动作
   由聚合 agent 给出，`learn_apply` 继续只接收每个 target 一行。
8. 新 skill 写作走 `methods/author/requirement.py` 和
   `stages/learn_requirement.py`，不复用 `learn_add` 的 task 行；一个聚合需求
   写一条 `Skill`；skill id 锚定 requirement id 的确定性 synthetic subtask，
   `source_subtasks` 记录来源 trial 的 subtask，二者分离。
9. AnswerSheet 到 `EvidencePackage` / `SkillAttemptEvidence` 的转换集中在
   `methods/review/trial_evidence.py`；不新造证据对象。

## 已知边界

- `trial_level` 只允许 live 或 exam 执行相位之后；P02 baseline 仍走 add。
- profile 是 run 内相位工件，不写入 `ExperimentConfig` 或 manifest；初始空文本。
- 逐 trial 分析和聚合 agent 的模型输出都需要格式校验；业务校验失败按
  `ModelOutputError` 记录，不把半成品写进 skill 库。
- 新需求与 task/trial 解耦，`source_sheet_ids` 只用于证据追溯和审计。
