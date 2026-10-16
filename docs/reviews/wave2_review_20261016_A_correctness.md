日期：2026-10-16
一句话说明：Wave 2 四提交（bca688d 配置冻结、b3985a0 v4 翻转、dc24d3a phase_execute、fe83c8c phase_learn）加集成收口（1df37d4）的合并结果 `16c3791..1df37d4` 的正确性静态评审 A 路：接线清单 12 项里 Wave 2 应完成的 9 项全部落实且 Wave 1 的 B1/P1/P2/D2/D3 咬合冲突全部收口，但发现 1 条阻断（exam 相位的 units.jsonl 与 acceptance_report.jsonl 的 unit_id 用了不同的 benchmark 槽，规则 1 复用在 exam 相位上一命中就 CodeBugError 崩溃——恰是 R5 的 no-op 改写场景）、3 条偏移和 6 条细节。
对应 log.md 条目：2026-10-16 21:14 / 21:44 / 21:40 / 22:10 四条 Wave 2 条目。

评审对象：`src/expedistill/`、`tests/`、`scripts/`、`AGENTS.md`、`log.md` 在 `16c3791..1df37d4` 的合并结果（分支 `feature/multi-method` HEAD `1df37d4`）。
基准：docs/decisions/repair_plan_simple_20261016.md（R8/R9 与 Wave 2 验收）、docs/decisions/execute_learn_module_boundaries_20261016.md（边界定稿 §2/§3/§4）、docs/reviews/wave1_review_20261016_A_correctness.md 末节接线清单、四个提交的 diff 与 log.md 对应条目。
方法：纯静态评审。不运行任何测试、不执行任何脚本；逐文件阅读合并后的 phase_execute.py / phase_learn.py / learn_tasks.py / cli.py / core/config.py / decision.py / verdicts.py / reports.py / phase_bank.py / 五个交付驱动 / 注册表与 stage0，grep 抽查已删符号的残留引用（含 scripts/），并与被删的 stage1_baseline.py / stage6_test.py（`git show 16c3791:`）逐段对照续跑语义。测试绿数以提交说明自报为准，本评审未复跑。

---

## 总体结论

**有条件不通过：接线本身是干净的——Wave 1 评审留下的两条星号裁决（zero_execution 一词一义、unit_id 规范化单落点）都已按裁决落地并有测试钉住，12 项接线清单里 Wave 2 认领的 9 项全部落实；但 exam 相位的两份相位内产物对同一个单元用了两个不同的 unit_id，规则 1 复用（含 R5 的"改写没产出新版本→下一轮沿用旧判定"）在 exam 方法上一命中就崩。** 这不是接口没接上，而是同一个驱动内部两个写入点对冻结键的构造不一致：`_plan_units` 用 `benchmark=ctx.config.benchmark` 重建 units.jsonl 的完整冻结键，`_exam_report` 用字面量 `benchmark="exam"` 写报告行。复用连接 `decision.reusable_units` 的匹配键（任务+角色+注入块字节）不受影响、照常命中，随后 `_reused_rows` 拿 units.jsonl 的旧 id 去报告字典里查行——查不到，抛 CodeBugError"phase directory is internally inconsistent"。基准（baseline）与 live 两个角色同链路各自的 id 两端一致（live 还有跨相位复用的测试钉住），只有 exam 一侧两写不一致，且 W2-A 的测试全是单 A 相位场景，恰好绕开。

D（基线）相位另有一条续跑语义回归：旧 stage1 只对"接受集之外"的新洞抛 InfrastructureError，新的 `_run_baseline` 在**不带旗标**的续跑上会把**先前已接受**的洞重新计入 `holes` 并再次抛错，破坏了 CLI 文档明文承诺的耐久接受契约（live 与 C 两个角色都正确兑现了同一契约，唯 D 失守）。其余发现是删除完整性的一处未披露残留（`scripts/smoke/smoke_tb_distill_chain.py` 仍 import 已删的 `stages.pipeline`，log 只披露了另外两个 smoke 脚本）、配置检查清单的三处"晚失败"缺口，和若干整洁问题。

发现计数：阻断 1、偏移 3、细节 6，共 10 条。

---

## 逐项发现

### 【阻断 B1】exam 相位两份产物的 unit_id 用了不同的 benchmark 槽：规则 1 复用一命中就 CodeBugError，R5 在 exam 方法上不可达

- **两套 id 的构造点**（同一驱动、同一相位）：
  - units.jsonl：`_run_exam` → `_plan_units(rows, None, phase_label=…, benchmark=ctx.config.benchmark, role="weak", …)`（`src/expedistill/stages/phase_execute.py:873-875`），写盘的是 `pNN:A:<真实 benchmark>:<exam_id>:weak:<sha8>`；
  - acceptance_report.jsonl：`_exam_report` 全部四处 `verdicts.make_unit_id(..., benchmark="exam", ...)`（`phase_execute.py:1005-1007、1010-1012、1041-1043、`带技能行同 1005 处的 unit_id），写盘的是 `pNN:A:exam:<exam_id>:weak:<sha8>`。
  冻结读取器（Wave 1 的 W1-A 产物）在这点上站在 units 一侧：`reports._exam_rows` 经 `acceptance_report(kind="exam", benchmark=ctx.config.benchmark)`（`reports.py:168、278`）用真实 benchmark 构造行 id——偏差方是 W2-A 的 `_exam_report`。
- **崩溃路径**（静态推演，全链路代码均已合入）：exam 方法标准序列 `D L A L A L A L C` 有三个 A 相位。`_reused_rows`（`phase_execute.py:348-388`）对更早执行相位按 `decision.reusable_units` 的（任务， 角色, 注入块 sha）匹配——匹配键不读 id 的 benchmark 槽，照常命中；随后 `rows.get(previous_id)`（`:377`）拿**早相位 units.jsonl 的 id**（真实 benchmark 槽）去查**早相位报告行的字典**（"exam" 槽）→ None → `:379-383` 抛 CodeBugError "phase … has a unit … but no report row for it"。
- **命中场景恰是 R5**：A 相位宇宙是 pending 技能（`_pending_bank_skills`）。一个 revise 决策的改写没产出新版本（R5：版本不动、内容不动、下轮应沿用旧判定）或改写因模型输出错误没落成时，技能留在 pending、注入块逐字节不变——下一个 A 相位 `_reused_rows` 命中 → 崩溃。W2-A 提交说明与 `_run_exam` 的 reused 处理都明文承诺此路径（"the rewrite loop belongs to the learn phase, which re-measures the new version in the following A phase (rule 1 reuses every unchanged unit)"），兑现不了。
- **即使不崩，join 也接不上**：`_exam_report` 的 `reused.get(unit_id)`（`:1050`）用的是"exam"槽 id，而 `_reused_rows` 返回的映射以 plan id（真实 benchmark 槽）为键——复用行永远 join 不进报告（`exam_unit` 的 `if plan.unit.unit_id in reused` 倒是键一致、会正确跳过执行，于是报告端只能靠共享答卷仓里上一相位的旧 attempt 兜出旧判定——偶然近似正确，但与摘要里"reused"的记账口径脱节）。
- **为什么测试没拦住**：`tests/unit/test_w2a_phase_execute.py` 的 exam 三个用例（:614、:645、:669）都是单 A 相位；跨相位复用只有 live 有测试（`test_live_measures_then_reuses_across_phases`，:500——live 的两端 id 一致所以绿）。
- **修法方向**（供 Wave 3 前裁决，二选一）：`_run_exam` 的 `_plan_units` 改传 `benchmark="exam"`，或 `_exam_report` 四处改传 `ctx.config.benchmark` 与冻结读取器对齐；两边各自只有一处调用点，并补一个"两 A 相位 + no-op 改写"的跨相位测试。

### 【偏移 P1】D 相位无旗标续跑把先前已接受的洞重新抛错：耐久接受契约在 baseline 角色上失守

- **新代码**：`_run_baseline`（`phase_execute.py:761-775、820-825`）里 `_run_arm_units` 返回的 `arm_holes` 是"本次调用中所有 missing 的单元"，其内部的 accepted 集读的是**相位目录下 per-arm 的 shortfall 文件**（`_run_arm_units` :678，`phases/pNN_D/<role>/accepted_infra_shortfall.json`）——baseline 角色从来不写这个文件，先前接受的洞只记录在 stage1 路径里、没有传进 `_unit_runs` 的 accepted 参数。于是旗标关闭的续跑：已接受的洞照旧 missing → `holes |= arm_holes` → `:821-825` 再次 InfrastructureError。
- **旧 stage1 的语义**（`git show 16c3791:src/expedistill/stages/stage1_baseline.py`，run() :211 起）：`new_holes = [unit for unit in missing if unit not in accepted]`，只对接受集**之外**的新洞抛错，docstring 明文"every later call -- with or without the flag -- passes them to K1 as skip_unit_ids … Only holes outside the accepted set take the InfrastructureError path"。CLI 的 `--allow-infra-shortfall` 帮助文本（cli.py:694-701）与 run_experiment docstring 也承诺"stays accepted (never re-executed) on every later resume"。
- **不重执行是保住的**（洞单元的重派预算在 `redispatch.json` 里已耗尽，`open_budget` 排除它们，K1 不会真跑），破的是"后续调用兑现接受决定"这一半：操作者一旦在某次续跑中不带旗标，运行就永远卡在 D 相位（CLI 的续跑提示会保留旗标，走提示的常规路径不受影响）。live 与 C 两个角色对同一契约兑现正确（live 的 accepted 从轮目录 shortfall 读进 `skip_for`，C 的 with_bank/control accepted 各自从臂目录读、只对 `new_holes` 抛错），唯 D 失守。
- **修法**：`_run_baseline` 把 `stage1_holes` 并进每个臂的 accepted 集传给 `_run_arm_units`（或让 `_run_arm_units` 接受显式 accepted 参数），`holes` 只累计 `arm_holes - stage1_holes`。

### 【偏移 P2】删除完整性：`smoke_tb_distill_chain.py` 仍 import 已删的 `stages.pipeline`，且未在 log.md 披露；`llm/call.py` 的 docstring 指向已删的 `stages.pipeline._build_handles`

- `scripts/smoke/smoke_tb_distill_chain.py:37`：`from expedistill.stages.pipeline import run_pipeline`——`pipeline.py` 在 b3985a0 已删，该脚本现在 import 即崩。集成收口的 log 条目（2026-10-16 22:10）披露的是 `scripts/smoke/smoke_stage01_{alfworld,searchqa}.py` 仍 import 已删的 stage1_baseline（"已知遗留……属 W2-A 清理尾巴"），**没有**披露这一处；集成提交说明里"each grepped for production callers first"的完整性审计漏了它。修复方案把 `scripts/audit/`、`scripts/run/` 划给 W3-B、README/runbook 划给 W3-C，但 `scripts/smoke/` 没有归属任何 Wave 3 代理——这三个冒烟脚本（两个已披露、一个未披露）目前无人认领。
- `src/expedistill/llm/call.py:89`：docstring "(see stages.pipeline._build_handles)"——`_build_handles` 随 pipeline.py 移入了 `cli.py`，引用已失效（纯文档腐坏，不影响运行）。
- 顺带核对通过项：`scripts/audit/audit_live_run.py` 只 import 存活模块（其读 v3 manifest 的 `config.methods.acceptance` 属 W3-B 既定迁移债）；`scripts/run/fork_run_stage04.py`、`prepare_inplace_recovery.py` 只引用目录字符串，不 import 已删符号；8 条被删的旧决策规则（exam_bare_answer / exam_stored_pass / exam_leak / exam_answer / exam_voided_rewrite_source / exam_measurement_exhausted / live_zero_execution_short_circuit / live_attribution_exhausted）全仓零引用；`methods/acceptance/`、`composites/acceptance_submit.py`、`composites/live_attribution.py`、`methods/segmentation/`、`methods/authoring/` 零残留 import。

### 【偏移 P3】配置加载期检查清单的三个"晚失败"缺口：形状合法的配置把错误推迟到花过钱之后、并以错误的异常类别爆出

三个缺口同族——`parse_phases`（core/config.py:482-560）放行的形状，要到相位驱动或学习相位才炸，且炸的是 CodeBugError（代码 bug 类，带 traceback，不走干净退出）：

1. **live 相位不带 `compare`**。`_parse_execute_phase` 对 compare 一律可选；但 live 报告没有相位内裸答行（每行都是整包注入），`phase_learn._report_inputs`（phase_learn.py:236-237）对 compare 为 None 返回 control_report=None，`decide_task_list` 对每个目标在 `decision.py:755-760` 抛 "no control row for unit … a wiring bug"。live 相位已经把 n_attempts × 组数的真实执行花完，学习相位才以 CodeBugError 崩。upgrade_v3_config 的产物永远带 compare（against: D），所以只打手写 v4 配置——但检查清单存在的意义正是这一层。
2. **`compare.against` 允许指向 live 相位**。`_check_compare_targets`（config.py:581-588）接受 baseline 或 live 并注释" a control is a bare measurement (baseline or live)"——live 相位的报告行是**带技能的整包行**，不是裸测。`decide_task_list` 收集对照行时只按 `role == "weak"` 过滤、不过滤 `injected_skills`（decision.py:741-750），一个 B 对照 B 的配置会拿"带技能臂"当对照参与比较。upgrade 产物不会生成此形状；建议清单改为"对照必须是 baseline 相位"或在决策端要求对照行 `injected_skills` 为空。
3. **序列以 L 开头**。`parse_phases` 不要求首个相位是 execute；`phase_learn._current_execute_position`（phase_learn.py:201-204）在学习相位才抛 CodeBugError。同类：超过两个 live 相位在 `_round_of`（phase_execute.py:1131-1139）运行期才 ConfigError。

三条都应在 `parse_phases` 的加载期检查清单里补齐（对照项 4 已有的"against 必须更早"的先例），把失败点移到 stage 0 之前。

### 【细节 D1】`_accept_new_holes` 是死代码

`phase_execute.py:705-716` 定义后全仓零调用：baseline 走 stage1 路径、live 走轮目录、C 走臂目录，各自内联了等价逻辑。要么删掉，要么让三个角色复用它（后者顺手能给 P1 一个统一的 accepted 传参口）。

### 【细节 D2】`_run_exam` 把摘要写了两遍

`phase_execute.py:958` 与 `:964` 对同一 `summary` 连写两次 `phase_summary.json`（中间只有 raise 分支）。内容相同、无行为差异，但两段代码读起来像漏了什么。

### 【细节 D3】D 相位的 delivery 未约束为 `none`

`_run_baseline` 无条件 `delivery.resolve(None, list(tasks), delivery_params)` 并注释"the none driver ignores bank"（:741-742），但加载期清单不强制 baseline 相位的 delivery 是 `none`——手写配置给 D 相位配 retrieval/per_skill 会在 resolve 内部以 `None` bank 崩出与本层无关的错误。upgrade 产物恒为 none；建议清单加一条角色-交付一致性检查。

### 【细节 D4】`control_arm_source` 的标签从 `stage1_reuse` 变成 `<相位名>_reuse`

`decision.control_arm_source`（decision.py:917-926）按 v4 相位名生成（against=D → `"D_reuse"`），而金矩阵语义仪器钉的是旧词（tests/golden/semantics.py:141 `"stage1_reuse": "D_weak_arm"`），`scripts/audit/` 与历史 run 的 stage6_summary 也用旧词。golden 已自愈跳过、重生成归 Wave 4，但这是一处**有意改名**，应在 Wave 4 的"差异逐处披露"清单里预先登记，避免重生成时被当成回归。

### 【细节 D5】live 相位开局的终态提交扫过相位任务集之外的组

`_run_live` 开局的 `for task_id in sorted(lm.pending_groups(store)): lv.finalize_group(store, task_id)`（:1212-1213）作用于**全部** pending 组，不按 `phase.task_ids` 过滤。这是裁决明文保留的"账本协议自身的 commit"（只提交已判定轮的挂起 commit、不新判任何东西），R4 例外成立；但一个任务集受限的 live 相位会顺带提交别的任务的组，这个超出相位范围的副作用值得在 docstring 的例外条款里点名（目前例外条款只写了"the live finalization sweep"而未提范围）。

### 【细节 D6】`_run_arm_units` 的 per-arm accepted 读取对 baseline 角色是永远为空的摆设

`_run_arm_units`（:676-679）从 `phases/pNN_<role>/…/accepted_infra_shortfall.json` 读 accepted，但 baseline 角色没人写这个文件（见 P1），exam/live/C 又不走这个函数——这个读取分支当前只服务于一个永远为空的集合，是 P1 回归的直接病灶。修 P1 时一并处理。

---

## 接线清单落实核对表

Wave 1 评审 A 末节 12 项，逐条（★ = 开工前须裁决项；第 1、2、4 项不在 Wave 2 范围的注明）：

| # | 项 | 判定 | 证据 |
|---|---|---|---|
| 1 ★ | zero_execution 一词一义裁决 | **已落实** | 决策侧跳过 zero_execution 目标行（decision.py:769-775）、对照在途时 `compare_conclusion` 抛 CodeBugError 而非产出弱化删除（:487-494）；weak_already_knows 短路只走 `not_run_control_perfect`（verdicts._STATUS_CAUSE_BY_TERMINAL :140-148）；判卷预算耗尽改记 `exam_generation_failed`（phase_execute.py:1031-1036、:1095-1104），与 W1-A 钉子表一致；三个旧场景测试经 decide_task_list 重新钉住 |
| 2 ★ | unit_id 规范化单落点 | **已落实，但同一 join 上有新阻断** | units.py 删本地规范化、改引 `verdicts.canonical_skills`（units.py:60-70），"single place" 措辞改指 acceptance；驱动用 `verdicts.make_unit_id` 重建完整冻结键（phase_execute.py:529-532）。但 exam 相位 units 与报告的 benchmark 槽不一致——本报告 B1 |
| 3 | TB 具名验收规则 | **已落实** | `tb_scorer` 消费 verifier_ran / exception_type 四路判 invalid（verdicts.py:273-306），`BENCHMARK_VERDICT_RULES` 绑定 tb；经 `verdict_from_run` 进 `_unit_runs` 链路；Wave 1 P3 的"正向无凭据计成功"中间态随之关闭 |
| 4 | R2b 词汇迁移 | （Wave 1B 已完成，非本波范围） | — |
| 5 | 无效重派回路 | **已落实（live 角色按裁决保留账本协议形态）** | `_run_arm_units` 的持久化 per-unit 预算（redispatch.json）+ 耗尽即 not_testable/invalid_redispatch_exhausted（phase_execute.py:663-702）；"已存盘未判分=既成 all_holes、不重派"与冻结读取器逐字同构（对照 reports.py:247-256，静态自洽）；live 角色不走此回路、沿用 judge_round 的轮 shortfall 与 live_undetermined 终态（协调者裁决，已披露） |
| 6 | 交付调用点替换 | **已落实** | 五驱动只剩冻结签名 `resolve(bank, tasks, params)`；`_legacy_resolve` 与 `units.dispatch` 全仓零残留；相位驱动经五族注册表解析（1df37d4 换掉两处过渡 shim）；C 相位 retrieval 的 `audit_rows` 侧信道在派发前整份写盘（phase_execute.py:1564-1571） |
| 7 | 相位标签 `pNN:<name>` | **已落实** | 冻结推导 `phase_label`（config.py:210-213）、局部名禁冒号（PHASE_NAME_RE :200）、phase_bank 目录名校验（phase_bank.py:56-73，D3 关闭）、`_parse_unit_id` 拒绝无冒号 pNN 头（decision.py:592-598，D2 关闭）；W2-C 测试钉住与 decision/phase_bank 两端一致 |
| 8 | 决策接线 | **已落实** | `phase_learn.run`：current_kind/control_kind 由 spec.role 映射、remaining_checks 按冻结序列、上一学习相位技能库产物（`_previous_bank`）、compare.rule（`_report_inputs`）；`[]` 注入行并入对照不抛错（decision.py:740-750，Wave 1 P1 关闭）；D4 多版本合并 `merge_task_list` 最高版本裁定、被取代行入账本（learn_tasks.py:131-171） |
| 9 | 删 12 条旧 exam_*/live_* 规则 | **已落实（8 删 4 留，留者有真实调用方）** | 8 条零引用；live_final_action / live_no_op_fires / live_all_holes 的调用方是留存的 live_verdicts 账本协议（:211、:386、:426），live_injection_overflows 的调用方是 phase_execute（:1269）——"有调用方"判断逐一核实属实 |
| 10 | G10/G11 迁移 | **已落实** | `composites/live_attribution.py` 删除，其唯一生产调用方 evidence/live_group 的视图裁剪帮手迁入 live_measurement；学习侧 revise 走 live_group.collect_group_evidence + author.revise_group（learn_tasks.py:368-404）；exams 开关在 v4 被拒（config.py:463-471），upgrade 只放行唯一合法消融组合 |
| 11 | 整理的序列位置分支 | **已落实** | `_produce_bank`：末位学习相位跑配置整理（produce），中间相位产 as-is 工件（produce_as_is），预算阀两头都在（phase_learn.py:309-321） |
| 12 | 逐相位技能库的消费方 | **已落实** | `load_phase_bank` 有了真实调用方：execute 侧 `_bank_skills` 以工件存在为信号取上一个学习相位的产物（成员+版本随行），学习侧 `_previous_bank` 同源；首个学习相位之前用 run 级冻结库 |

---

## 核对通过清单

按评审任务的九个重点逐项，以下各项**通过**（B1/P1/P2/P3 已单列的不再重复）：

1. **执行相位内部顺序（R9 第 6 点）**：A 相位在单元内严格"裸答（对照）→ 判卷（验收对照）→ `decision.control_perfect_skips_run`（调决策的比较函数，驱动不自实现比较）→ D8 泄题审计（C1：在带技能调用之前）→ 带技能作答 → 判卷"（phase_execute.py:898-925）；B 相位"基线加载 → 对照全对短路 → 注入超限短路 → 派发 → judge_round/finalize_group"（:1243-1288）；C 相位不调跳过、不进重派（`_run_test` 直接 `_dispatch_arm`，无 `_run_arm_units`），with-bank 先于 fresh 对照臂派发与退役的 stage6 逐行同序，full 模式的复用对照完整性在花任何带技能执行费**之前**检查（:1530-1556）。
2. **学习相位链路**：`decide_task_list` 的五个输入来源全部核实（role 映射、`remaining_checks` 按冻结序列数后续 A/B 相位、上一学习相位的库工件、compare.rule、task_select_rule 走摸底路径）；`revise_outcome` 落地（摘要按它把无新版改写记为 keep，phase_learn.py:344-350；落地侧版本不动、不弃用、无任何盘上变化，learn_tasks.py:432-441）；改写预算耗尽=无变化（author outcome "unchanged"，:432-437）；D4 合并的三种矛盾形态（旧版误、新版过、旧版泄题）都收敛到最高版本一行、被取代者具名入账本，且 zero_execution 可见性行在决策入口就被跳过、到不了合并（learn_tasks.py:20-21、:146-171）。
3. **R4 不变量**：phase_execute 无 discard_skill/accept_skill 调用（调用面测试 :365-369）且 D/A/C 三角色字节级钉住（:339-363；live 的例外是账本协议自身的 commit，裁决明文）；决策为纯函数、不改库（W1-C 钉子随行）。学习相位才碰技能仓，与边界 §1.2 的两阶段分工一致。
4. **重派收窄的静态自洽**：`_unit_row_for` 的三态映射（任何有效运行→normal；全为 accepted 洞或全存盘未判分→all_holes；派发未完成→invalid_redispatch_exhausted）与冻结读取器 `_baseline_unit_row` 逐字同构（reports.py:247-256）；"已存盘未判分不重派"的限制（新采样会超出冻结 n_attempts 深度、读取器表达不了）与三份冻结读取器的表达力确实匹配——报告词汇里没有任何"深于一轮采样"的状态。
5. **跨模块契约**：PhaseInstance/PhaseResult 在 cli 循环（cli.py:443-459）、两个驱动（phase_execute.run/phase_learn.run 的签名与 label 断言）、W2-C 测试（sys.modules 假驱动缝隙）三处一致；惰性 import 契约彻底——stages/__init__ 无 eager import，`_phase_driver` 用 `importlib.import_module` 尊重 sys.modules 缝隙（cli.py:286-302）；驱动答错相位被循环响亮拒绝；`--until-phase` 的界在配置加载后按冻结序列长度校验；summary 完备清单从 manifest 冻结序列推导、不依赖代码常量。
6. **空洞继承政策（评审 A-09 的行为保证）**：三条款全部落地——live 轮 1 不继承轮 0 的洞（accepted 从 `round_dir/accepted_infra_shortfall.json` 按轮读取，phase_execute.py:1232-1243）；每轮 skip 含 D 弱臂空洞（`lm.skip_ids_for(stage1_holes, accepted, task_id)` 把 stage1 记录滤到本任务弱臂，live_measurement.py:365-383）；full 模式 C 的对照臂空洞只在 D 侧接受（复用对照缺失即 raise、C 内永不写对照臂 shortfall，:1534-1556、:1608-1619）。不再是阻断项。
7. **R5 断言（live 形态）**：改写没产出新版本→注入块逐字节不变→`reuse_key` 命中→下一 live 相位零执行沿用旧判定、不写新轮账本行，有跨相位测试钉住（test_live_measures_then_reuses_across_phases，:500-544）；live 相位内另有留存的 no-op 守卫（live_verdicts 经 `live_no_op_fires` 字节比对）。exam 形态被 B1 破坏，见该条。
8. **v4 配置与命令行**：schema 4 三处拒载旧版并指向升级映射与维护分支提示（stage0_freeze 的 manifest 闸含 v3）；五族注册表 + execution 族 driver-less 参数包；stage0 逐相位 fail-early 解析并校验具名规则；upgrade_v3_config 产物完整（7 份配置的转换测试在库）。golden 自愈跳过（tests/conftest.py）是诚实的大字横幅而非静默绿，Wave 4 重生成的债登记在案。
9. **B1/P1/P2/D2/D3/Wave-1 咬合冲突的收口**：除 B1 新引入的问题外，Wave 1 评审 A 的两条星号裁决与 P1（`[]` 行）、D2（pNN 无冒号错切）、D3（目录名互撞）全部按裁决关闭且有测试；集成收口对"有调用方才保留"的判断（acceptance_skill_status、四条 live 规则、discard 词表再导出）逐一 grep 核实属实。
