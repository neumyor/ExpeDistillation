# 决定：验收只产 trial 级 AnswerSheet，unit 汇总改为共享纯函数

日期：2026-10-26
一句话：把验收从“逐单元报告行”改成“逐 trial 的答题卡（execution + coverage + grading）”，unit/task 级汇总由 `unit_summary.py` 的纯函数按需计算，不再作为验收产物。
对应 log.md：2026-10-26 的验收模块重构条目。

## 1. 背景

旧实现里 `AcceptanceReportRow` 同时承担三件事：

- 每张 trial 的判分事实（runs / verdict / basis）；
- 覆盖事实（这个 unit 该有几个 attempt、缺了哪个、为什么缺）；
- unit 级聚合（valid_count / success_count / status / cause）。

这使验收模块既判分又聚合，`reports.py` 的 baseline / exam / live 三个 reader
各自混合了 expected、leak、injection audit、control perfect、grading 和计数。
下游（决策、归因、审计、test）需要的信息其实不同，却都被迫消费同一张
unit 级报告行。

## 2. 决定

### 2.1 trial 级 AnswerSheet

验收的落盘输出改为 trial 级：

```text
AnswerSheet
  execution: ExecutionRecord | None
  coverage:  CoverageRecord
  grading:   GradingRecord | None
```

- `ExecutionRecord`：一次 trial 的 execution 事实（轨迹 steps / exam answer /
  external reward、raw correctness、metrics、aux、artifacts、source、arm）；
- `CoverageRecord`：这个 trial 是否 present、missing 或 terminal，以及 cause；
- `GradingRecord`：按具名规则判出的 success / failure / invalid + basis + rule。
- `execution=None` 表示 missing/terminal trial（“负答题卡”），不是伪造的空答卷。

JSON 是 canonical；`blocks/view.py::render_answer_sheet` 生成 Markdown 视图，
Markdown 不作为源。

### 2.2 execution / coverage / grading 分开实现

- `composites/answer_sheets.py`：只把 Trajectory/Attempt/external reward 转成
  ExecutionRecord，装配 AnswerSheet，逐 trial 判分，写 JSON/Markdown；
- `composites/coverage.py`：只回答 expected vs stored / terminal cause
  （accepted hole、leak、injection overflow、exam generation failed、
  control perfect、zero execution），不判分、不聚合；
- `composites/verdicts.py`：只保留具名判分规则与 `grade_sheet`，不读 coverage；
- `core/vocabulary.py`：success/failure/invalid、status/cause 的唯一定义处。

### 2.3 unit 汇总改为纯函数

- `composites/unit_summary.py`：`summarize_unit` / `summarize_phase` /
  `task_metrics`，输入 AnswerSheet + UnitSpec，输出 UnitSummary；
- 决策、审计、test 调同一组函数；验收模块不调用它，也不落 report card；
- `UnitSummary` 是 unit 级视图（valid/success/status/cause），不是持久对象。

### 2.4 phase envelope

- `composites/phase_report.py`：`build_phase_report` / `read_phase_report`；
- 每个执行相位写 `unit_specs.jsonl` + `answer_sheets/` + `coverage.jsonl`；
- `reuse_index.jsonl` 是 execution-side 的规则 1 缓存，不是 acceptance report；
- test 相位也写 answer sheets，但 `learning_usable=False`，决策入口拒绝。

## 3. 被否决的备选

1. **继续保留 unit 级报告行**：验收仍然混合判分/覆盖/聚合，下游无法按需取数，
   否决。
2. **把 Markdown 作为 canonical**：解析 steps/`cat` 事件脆弱、有损、重复存储，
   否决；Markdown 只做派生视图。
3. **把 coverage 塞进 grading**：grading 只该看一张 execution record；
   missing/terminal 没有 execution，塞进去会让 grading 读 expected/leak/audit，
   违背职责边界，否决。
4. **让每个下游自己从轨迹算 unit 计数**：会出现多套口径；必须共享
   `unit_summary.py`，否决“各算一套”。

## 4. 影响

正向：

- 验收输出严格 trial 级，职责单一；
- coverage 的复杂逻辑集中在一个模块，execution 导出只做记录；
- 决策/审计/test 共用一份聚合函数，口径不漂移；
- missing 不再伪装成 invalid run；control perfect 是 coverage 事实；
- AnswerSheet 可导出 Markdown，便于人工复核和论文附录；
- baseline / exam / live / test 统一在 trial 级 envelope，payload 由 kind 区分。

反向 / 风险：

- 旧 run 没有新工件；实现上已删除 `reports.py`、
  `AcceptanceReportRow`、`verdicts.summarize_unit` 和
  `unit_verdicts.jsonl` 双轨，不提供旧 run 读取兼容；
- 规则 1 改读 `reuse_index.jsonl`；
- coverage 必须显式处理 expected/missing/leak/overflow/generation/control
  perfect；漏一个就会把“没测”当 0 分；
- `UnitSummary` 只能由共享函数产生，禁止下游各自计数；
- 外部 campaign 的 execution 必须显式 `source="external"` + sha256，不能伪装
  成本地轨迹；
- golden fixtures、审计脚本、test 相位两臂的 sheet id 需要同步更新；
- `unit_id` 不含 arm；若出现两个完全相同的 bare arm，`summarize_phase` 会因
  重复 unit id 报错，而不是静默双计。

## 5. 验证

- 新模块单测覆盖 AnswerSheet 构建/装配/coverage/unit summary/Markdown；
- P03/P05 live 与 P01 baseline：新 UnitSummary 与旧 `AcceptanceReportRow`
  在 valid/success/status/cause 上逐单元一致（mismatch 0）；
- P07 test：用新模块重算 with_bank/control 的 avg@3、pass@3、infra_tasks，
  与旧 summary 逐位一致；
- golden 矩阵 4 组合重新生成，fresh vs crash+resume 全部等价；
- 全量 pytest、ruff E9/F、layering 全绿；
- `audit_live_run.py --self-test` 通过，P07 旧 run 走 legacy fallback
  0 error / 0 warn / 1 info。

## 6. 后续：归因与 skill_usage 改为消费 AnswerSheet

在初版落地后继续收口了下游的事实来源：

- `ExecutionRecord.artifacts` 在 live folder 交付时携带
  `skill_folder`（folder manifest），AnswerSheet 因此能独立回答“这次
  attempt 的轨迹和可见技能清单是什么”；
- `composites/skill_usage.build_usage_rows_from_sheets` 从 AnswerSheet 的
  `ExecutionRecord.steps` + `artifacts.skill_folder` 提取读取/未读事实；
  `skill_usage.jsonl` 的写盘和旧 trajectory 入口已删除；
- `phase_learn` 从上一执行相位的 `answer_sheets/` 现算
  `usage_by_task`；
- `methods/attribution/skill_attempts.collect_skill_evidence` 的唯一输入是
  `sheets: Sequence[AnswerSheet]`，从 `ExecutionRecord` 取 steps、
  attempt、raw correctness 和 folder；`store`/`round_` fallback 已删除；
- `learn_apply` 把当前 phase 的 sheets 传给归因，不再传
  `folder_manifests`。

最终清理提交还删除了 `composites/reports.py`、`AcceptanceReportRow`、
`verdicts.summarize_unit` / `status_cause_for_terminal`、phase-world
`unit_verdicts.jsonl` 读取和审计 fallback；新代码只认 AnswerSheet 协议。

决策仍消费 UnitSummary（unit 级视图），不直接读 trial 轨迹；这是有意的
分层。归因/写作消费 trial 级 execution/evidence。

## 7. 后续收口：删掉最后一层旧接口

AnswerSheet 落地后，`core.contracts.RunRecord` 和
`verdicts.verdict_from_run` 已经没有 `src/` 调用方：执行侧直接产
`core.answer_sheet.ExecutionRecord`，判分只走 `verdicts.grade_sheet`。
两者连同以下兼容面一并删除：

- `contracts.py` 只保留交付/决策的形状（`ExecutionUnitRow` /
  `ExecutionUnitList` / `TaskListRow`）；
- `verdicts` 不再 re-export `unit_ids` 的 `make_unit_id` /
  `canonical_skills`，交付侧直接 import `composites.unit_ids`；
- `skill_usage.build_usage_rows_from_sheets` 删除 `folder_by_task` 兜底，
  folder manifest 只从 `ExecutionRecord.artifacts["skill_folder"]` 读；
- 修正声称旧 fallback 仍存在的 docstring/注释（归因、phase report、
  phase execute、skill_usage），以及 README / AGENTS 里的四形状与
  `verdict_from_run` 描述。

有意保留的旧读路径只有两类：审计脚本对 pre-v4 run 的 `_audit_legacy`，
以及 `assemble_reuse` / `fork_run_stage04.py` 对 v2/v3 源 run 的装配支持。

## 8. 后续修正：规则 1 复用与 skill_level 终态

AnswerSheet 落地后发现两个语义缺口，已修正：

- **复用单元对学习相位隐身**：执行相位只把 measured units 写进
  `unit_specs.jsonl`，规则 1 复用的单元没有 sheet，`phase_report` 因此读不到，
  下一次学习相位不会重试“改写没产出新版本”的技能。现在执行相位把复用单元的
  上一份 `UnitSummary` 写成 `carried_units.jsonl`（新 unit_id、原计数、
  `reused_from` 指向原测量单元）；学习相位把它并入决策输入，并按
  `reused_from` 回读原相位的 `answer_sheets/` 作为 usage 证据。没有复用单元时
  不写空文件。
- **`skill_level` 不消费 unit 终态、也不看剩余检验次数**：现在先按
  `UnitSummary.status/cause` 走共享 `table_action`（control-perfect、
  not-testable、leak、zero_execution），正常单元在“触发 + pending + 组没赢”
  时按 `checks_left` 决定 revise 还是末次 delete。比较规则也改为直接使用
  `blocks/rules.py` 的 `ACCEPTANCE_IMPROVE` 注册表，不再维护第二张固定表。
