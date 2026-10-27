# 决定：把学习相位的决策与归因合并为 review 方法

日期：2026-10-27
一句话：把 `evidence` / `attribution` / `decision` 三个学习槽位合并为
`methods/review` 一个方法族，P02、P04/P06 和 exam 通过不同策略复用同一个入口。
对应 log.md：2026-10-27 的 review 模块重构条目。

## 背景

P04/P06 原有链路是 `decision_strategies` 先决定 keep / revise / delete，
`learn_apply` 再对 revise 行调用 `skill_attempts` 收集归因窗口。两者已经
在同一条调用链上，却分散在 composites、methods 和 stages 三层，输入也要在
`phase_learn` 里拆成 `current_rows`、`current_sheets`、`usage_by_task`、
`group_better_by_task`、`skill_status_by_id` 五份分别传递。

P02 的 add 路径又重复了一份切段与证据收集代码，和 `learn_add_cluster` 的
准备阶段几乎相同。

## 决定

1. `LearnPhaseSpec` 只保留 `review`、`author`、`curate` 三个内容槽位。
   配置 schema 升到 6；v5 配置由 `upgrade_v5_config` 在内存里转换。
2. `review` 方法族放在 `methods/review`，包含：
   - `driver.py`：先 `assess` 决定动作，再 `diagnose` 只给需要证据的行收集证据；
   - `strategies.py`：原 `composites/decision_strategies.py` 的
     `task_level` / `skill_level`，加上 exam 多版本合并；
   - `attribution.py`：原 `methods/attribution/skill_attempts.py`；
   - `add_evidence.py`、`segmentation.py`：原 `methods/evidence` 的 add 路径；
   - `exam_evidence.py`：原 `methods/evidence/exam_sheet.py`；
   - `contracts.py`：`ReviewContext`、`ReviewItem`、`ReviewResult`。
3. 输入复用 `PhaseReport`（`AnswerSheet` + `UnitSummary`）和 `Skill` 列表，
   不再新增 `ReviewInput`。输出复用 `TaskListRow`、`EvidencePackage`、
   `SkillAttemptEvidence`；只有 `ReviewItem` 把它们配成一对。
4. P02 是 `review` 的 baseline 策略：输入 baseline 报告和空库，输出 task 级
   `add` 行与 `EvidencePackage`。P04/P06 是 live 策略：输入 live 报告和
   上一相位库，输出 skill 级 keep / revise / delete 与可选归因证据。exam
   沿用同一入口，输出合并后的 skill 行。
5. 落地仍在 stages：`learn_add`、`learn_add_cluster`、`learn_apply`、
   `learn_exam` 只写任务清单、版本计划、回执和相位库；review 不写盘。
6. 续跑时 `learn_ledger.revision_skip_targets` 过滤已经完成改写的目标，
   review 不再为这些行收集证据。

## 已知边界

- 旧 v5 run 不能在新代码上直接续跑；它的 manifest 仍是 schema 5，转换会
  改变配置身份。需要续跑时使用它自己的维护分支。
- P02 的切段续跑仍依赖 store 中的 `Subtask`；review 负责调用切段，K2 的
  落盘与跳过语义不变。
- `task_level` 仍需要对照报告；`skill_level` 只消费 live 的 `better` 结论。
- 配置里的 `review` 块由三个阶段共用；未使用字段保持默认值。
