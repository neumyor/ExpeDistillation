> **已过时（2026-10-29）**：本文的读取端优先级（sheet_id → 锚点轨迹 → unit_id）已被
> 写入端的精确指针取代，`_source_sheet`、`_fallback_origin` 与 `skill_origins.jsonl`
> 均已删除。替代文档：`docs/decisions/provenance_trace_context_20261029.md`。

# 决定：考场场景按技能的锚点轨迹定位来源答卷

日期：2026-10-29
一句话：技能的来源答卷改由 `Skill.subtask_id` 的锚点轨迹唯一确定；unit_id 只在没有锚点时使用，且必须唯一，不再取最后一张。
对应 log.md：2026-10-29 的“修正 exam 场景的来源答卷定位”条目。

## 背景

对 P03/P05 的审计发现：P03 的 37 道题、P05 的 21 道改写题，场景全部取自 baseline
的 **attempt 02**，而技能锚点分别来自 attempt 00（34 条）和 01（3 条），0/37
命中；另有 3 道 P03 题和 1 道 P05 题因为切点步号超过了 attempt 02 的步数，被
clamp 到轨迹末尾，“切点之后”为空。

根因是来源指针不唯一：

- P02 的两条写作路径（逐段、按簇）写 `skill_origins.jsonl` 时只记
  `unit_id` + `scene_step`；
- baseline 的 `unit_id` 是 `phase:benchmark:task:role:sha`，**同一 task+role 的
  三个 attempt 共用同一个 unit_id**；
- `_source_sheet` 按 unit_id 匹配到多张答卷后 `return matches[-1]`，答卷按文件名
  排序，最后一张就是 attempt 02。

技能对象本身一直带着唯一指针：`Skill.subtask_id` 是它的锚点 subtask（按簇路径取
簇内最小 id，逐段路径取来源段），解析后就是来源轨迹。`_fallback_origin` 早已在用
这条规则，只是它只在“完全没有 origin”时才被调用。

## 决定

`_source_sheet(ctx, skill, source_ref)` 按以下优先级取答卷：

1. `source_ref["sheet_id"]` 存在 → 精确匹配；找不到就报错（`ModelOutputError`）；
2. 否则从 `skill.subtask_id` 解析轨迹 id，选 `execution.artifacts.trajectory_id`
   等于它的那张答卷；
3. 只有既没有 `sheet_id`、又没有可解析锚点时才退回 `unit_id`，且必须**唯一命中**；
   多于一张是 `CodeBugError`，不再静默取最后一张；
4. 都没有 → `ModelOutputError`（记为出题失败）。

不修改任何写入路径：`Skill.subtask_id` 已经随技能对象流转，读侧零迁移即可修正
P02 逐段、P02 按簇、改写版本（v2/v3 保留原 subtask_id）和继承 `source_ref` 的
exam 改写；需求技能的 `req/...` subtask 无法解析为轨迹，但它们的 origin 一定带
`sheet_id`，走第 1 条。合并技能（`bank/merge/*`）不参与考试。

没有选择“在每个写入点补 `sheet_id`”的方案：那需要在写入时查一次源相位报告，
是同一件事换到更早的位置；而且对已有工件无效。将来若想让 origin 自描述，可以
再把 `sheet_id` / `trajectory_id` 写进 `source_ref`，但不是修这个缺陷的必要条件。

## 后果

- 新 run 的 P03/P05 场景会取自技能真正来源的那条轨迹；越界 clamp 消失（三条
  P03 题的 cut 100/21/16 在锚点轨迹长度 122/23/18 之内）。
- 已完成的 run（1322、2053）的题面不会自动变化；它们的 P03/P05 判分以及基于
  这些答卷的 P04/P06 归因都受此缺陷污染，**不能当干净结论**。需要新建 run 重跑
  P03–P07 才能得到可用数字。
- 测试：同一 unit 三张答卷时按锚点选 attempt 00；`sheet_id` 优先；无锚点且 unit
  多命中报 `CodeBugError`；未知 `sheet_id` 报 `ModelOutputError`
  （`tests/unit/stages/test_exam_source_sheet.py`）。
- 用新解析器在已完成的 2053 run 工件上重放：P03 37/37、P05 34/34 全部命中锚点
  轨迹，越界 clamp 为 0（只验证解析，不重新生成题面）。
