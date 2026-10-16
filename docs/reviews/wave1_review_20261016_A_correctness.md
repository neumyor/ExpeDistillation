日期：2026-10-16
一句话说明：Wave 1 六提交合并结果（`c6a9fb8..87a047b`，重点 ee97c50 / c5aa94d+c3c463c / 529cdc9 / 09a3496 / 8c05487 / 907cf43）的跨模块咬合正确性评审 A 路：六代理在隔离环境按冻结契约开发，词汇与形状在机械层面全部咬合、金矩阵等价断言未被绕过，但合并后首次碰面暴露出 1 条必须在 Wave 2 接线前裁决的语义冲突（zero_execution 的两种含义）、4 条偏移和 5 条细节。
对应 log.md 条目：2026-10-16 18:25 Wave 1 / W1-E（六提交中唯一已记条目；本次评审对象为六提交合并后的 `feature/multi-method` HEAD `87a047b`）。

评审对象：`src/expedistill/` 六提交的合并结果（只读评审，未改任何代码文件；本报告是唯一产出）。
基准：docs/decisions/execute_learn_module_boundaries_20261016.md（边界定稿）、docs/decisions/repair_plan_simple_20261016.md（修复方案）、`src/expedistill/core/contracts.py`（Wave 0 冻结契约）。
方法：静态阅读六提交全量 diff 与合并后 HEAD 的九个咬合面文件（contracts / verdicts / reports / decision / units / 五个 delivery 驱动 / tb/plugin / live_verdicts / evidence+author / phase_bank），逐项核对任务清单的七个评审点；golden 等价按更正后的口径**不跑测试**，改为静态核对六提交是否触碰 `tests/golden/` 与金夹具、以及 tamper 自检是否仍然有效。全量测试的绿色以合并时（2311 通过）为准，本评审未重复运行。

---

## 总体结论

**有条件通过：Wave 1 各自的验收判据（测试全绿、金矩阵对基线全等、分层与 ruff 干净）成立，但"冻结契约"只冻结了词汇的取值，没有冻结每个取值的唯一含义，两个代理对同一个 cause 做出了相互矛盾的解释。**

机械咬合全部成立：status 三值、cause 六值在 verdicts 与 decision 两端逐字对应，任何一方产出的值另一方都认识，不会出现未知值崩溃；报告行的构造严格走 `summarize_unit` → `AcceptanceReportRow`，字段与 contracts.py 逐字段一致；`_parse_unit_id` 的五段解析与 `make_unit_id` 在 R9 的 `pNN:<name>` 标签下自洽（含 `traj/tb:t1/.../exam` 这类带链冒号的 task_ref，从两端解析验证通过）；R4 两侧都有测试钉住（调用面 + 行为面）；golden 等价断言没有被 Wave 1 绕过（六提交零触碰 `tests/golden/` 与金夹具）。

问题出在语义层：**W1-A 把 `zero_execution` 当作"没有可报告的完成运行"的兜底 cause（未作答的裸答、未作答的存量版本、已派发未判定的 live 轮、判卷预算耗尽，四种状态共用），而 W1-C 把同一个 `zero_execution` 严格解释为"weak_already_knows 短路的零运行标记"并映射到 delete(weak_already_knows)**。W1-A 自己的场景钉子表写着"判卷预算耗尽 → model_output_error"，而 W1-C 的统一入口对同一份盘上状态会给出 weak_already_knows。这不是测试能拦住的（两端各自的测试都过），是两份"留给 Wave 2 的接线说明"之间的正面冲突，Wave 2 若按字面把 `decide_task_list` 接到 `acceptance_report` 上，会把在途/未测量单元判成"弱模型已会"而误删技能。

发现计数：阻断 1、偏移 4、细节 5，共 10 条。

---

## 逐项发现

### 【阻塞 B1】zero_execution 一词两义：验收侧当"无可报运行"的兜底，决策侧当"weak_already_knows 短路标记"，两边对同一盘上状态给出互相矛盾的处置

- **验收侧（W1-A）的四种用法**，全部落 `not_testable/zero_execution`：
  - 裸答考题还没作答：`reports.py:314-316`（`_exam_bare_row`，"none yet -> not_testable/zero_execution"）；
  - 存量版本本相位未作答：`reports.py:283-285`（docstring 明说"the stored version always has a row **so the decision side can see 'not yet answered' as not_testable/zero_execution**"）；
  - live 轮已派发未判定：`reports.py:374-378`（`_live_rows` 的非终态分支，无有效轨迹即 zero_execution）；
  - **判卷预算耗尽**：`tests/unit/test_w1a_discard_scenarios.py:183-201` 把它钉为 zero_execution（"judge budget died"），且该文件 424 行的钉子表写明此场景的决策结果是 **model_output_error**。
- **决策侧（W1-C）的唯一解释**：`decision.py:480`（`CAUSE_ZERO_EXECUTION: WEAK_ALREADY_KNOWS`）+ `decision.py:669-676`（not_testable 非 leak 一律按 cause 表删除）——zero_execution 行进 `decide_task_list` 必得 `delete(weak_already_knows)`。`decision.py:472-473` 注释自陈"zero_execution is the weak_already_knows short-circuit's zero-run marker"；`tests/unit/test_w1c_decision_table.py:100`、`tests/unit/test_w1c_decision_entry.py:155` 按此钉死。
- **具体伤害路径**（静态推演，两端代码都已合入）：某技能带技能答卷判对（normal、success=1），但同考题裸答尚未作答（对照行 zero_execution、valid=0）。`decide_task_list` 里对照 valid=0 → `compare_conclusion` 携带对照 cause=zero_execution 返回 not_testable（`decision.py:626-632`）→ `table_action` → **delete(weak_already_knows)**：一个已判对的技能因对照还在途而被当作"弱模型已会"删除。判卷预算耗尽的技能同样会被打成 weak_already_knows，而 W1-A 的钉子表要求的是 model_output_error——同一棵树里两份互相矛盾的规格。
- **为什么现在标阻断而不是留给 Wave 2**：这不是"缺接线"，而是两个已合入模块对冻结词汇的含义冲突；Wave 2 的三个代理（W2-A/B/C）会各自按其中一份 docstring/测试注释理解词汇，冲突不先裁决，接出来的行为必然有一边是错的。裁决方向二选一（或组合）：验收侧为"未运行/预算耗尽"细分 cause（或对在途行不给 not_testable），或决策侧把 zero_execution 行当 CodeBugError（在途工作不得进入决策）并让短路标记只走 `not_run_control_perfect`。

### 【偏移 P1】`skills_injected=[]` 的冻结语义与决策入口正面冲突：R5 说它是合法的"检索未过阈"，`decide_task_list` 把它当接线 bug 拒绝

- R5 冻结的空值语义（`core/contracts.py:69-71`、`none.py` 与 `retrieval.py` 的 docstring、529cdc9 提交说明）："`None` 是对照单元，`[]` 是检索未达阈值的裸跑（带技能臂内的合法单元）"。W1-C 自己的 `reuse_key` 也承认它合法：`decision.py:920-924`"A bare unit and a retrieval-miss unit share the empty block because they are the same measurement"。
- 但 `decide_task_list` 对注入为空的目标行直接抛 CodeBugError：`decision.py:872-877`"unit … is a with-skill unit that injects nothing; **delivery produced a bare run under a with-skill label**"。
- 今天不可达（exam 相位每行恰一条技能，live 相位空组在交付侧就炸），但任何配置成 retrieval 交付的 A/B 相位一接线就崩。契约层面的"两个 Wave 1 模块对同一冻结值判断相反"，Wave 2 接线前必须统一（建议：决策侧把 `[]` 行按"本轮无注入、结论 no_gain 处理"或交由相位驱动过滤，而不是当 bug）。

### 【偏移 P2】unit_id 的规范化规则存在两套且都自称"唯一的落点"，Wave 2 的接线说明互相矛盾

- 冻结侧（W1-A）：`verdicts.canonical_skills`（`verdicts.py:168-182`）——`None`→`"none"`、字符串→`"bank:<digest>"`、列表→`"list:"+json`；`contracts.py:32-34` 明文"the canonicalization rule … frozen with the acceptance module (R1)"。
- 交付侧（W1-D）：`methods/delivery/units.py:57-69` 用的是**另一套**——`json.dumps(skills_injected)`（`None`→`"null"`、字符串→带引号 JSON），且 docstring 写"**this helper is the single place that rule gets rebased**"，即交付侧也声称自己是该规则的唯一落点。
- 同一份 `skills_injected` 两边算出的 sha8 必然不同（sha256("null") ≠ sha256("none)），`<task_ref>:<sha8>` 临时形态与冻结形态的 join 只能靠 Wave 2 用 `verdicts.make_unit_id` 重建。现在没有消费方把两者 join 起来，所以不炸；但两份"single place"的说法给 Wave 2 的是相反指令。修法：Wave 2 删掉 `units.unit_id` 的本地规范化、改 import `verdicts.canonical_skills`（methods 层 6 允许依赖 composites 层 5），并把 units.py docstring 的"single place"改为指向 verdicts。

### 【偏移 P3】R2 摘除伪零分守卫后的中间态，W1-B 的披露只覆盖了负向：正向无验证凭据的 reward 现在计为成功

- 改动事实（`plugin.py:1079-1086`）：reward 非 None 一律按得分器原话给 success/failure，**不再看 verifier 日志**；`verifier_ran` 只是旁证事实（`plugin.py:257-287`），全仓（src 内）无任何消费方——具名规则接线确属 Wave 2 缺口，此判断成立。
- 但中间态语义变化是双向的：改前 `classify_result`（`plugin.py:313-317`，现仅作历史镜片保留）对"有 reward 但 verifier 日志缺失/pytest 未收集"**不分正负一律判 infra_error 重跑**；改后 reward>0 且 `verifier_ran` 为 False 或 None（含日志拉取失败）**直接计成功**。W1-B 汇报的"伪零分按 0 分计"只覆盖 reward==0 的方向；正向上从"作废重跑"变成"计入成功"是评分通胀方向的变化，未在提交说明或 docstring 披露。
- 在"修复全部完成前不启动任何正式 run"（方案硬约束 6/8）之下这不构成 Wave 1 错误，但 Wave 2 的 TB 具名验收规则必须消费 `verifier_ran`，且此中间态差异应记入方案的有意变化清单。

### 【偏移 P4】`_CAUSE_DELETE_REASONS` 把 live 风格的弃用理由安到任意相位的 cause 上

- `decision.py:474-481`：`invalid_redispatch_exhausted → live_undetermined`、`injection_overflow → live_injection_overflow`。这两个 cause 的产生方不限于 live（`reports.py:196-197` 的 baseline 读取器就会产 invalid_redispatch_exhausted；注入超限在 A 相位同样可能），而 `table_action` 只有 no_gain 一行做了相位分流（`decision.py:664-665`，exam 得 retry_exhausted、live 得 live_no_improvement），not_testable 的删除理由不分相位。
- 后果：A 相位一个重派耗尽的单元会带着 `live_undetermined` 进 `Skill.discard_reason`，污染弃用原因统计（该字段是分析口径）。建议 Wave 2 前按 phase_kind 分流，或把这两个映射改成相位中性的理由。

### 【细节 D1】cause 固定集在两个模块各定义一份，无相等性钉子

- `verdicts.py:120-127` 与 `decision.py:449-456` 是两份独立的 frozenset 字面量；`weak_already_knows` 有跨模块相等断言（`tests/unit/test_w1a_verdicts.py:52-58`），六个 not_testable cause 却只各自对字面量钉（`test_w1a_verdicts.py:41-49`），没有 `verdicts.NOT_TESTABLE_CAUSES == decision.NOT_TESTABLE_CAUSES` 的断言。改一处漏一处在比较端会响（CodeBugError），但在删除理由表会静默走错格。加一行相等断言即可。

### 【细节 D2】`_parse_unit_id` 对"pNN"（无冒号）标签静默错切

- `decision.py:700-726`：标签必须形如 `pNN:<name>`（一次冒号）才能正确切出 task_ref。若调用方传 `phase_label="p1"`，`head.split(":", 3)` 会把 benchmark 槽吃掉一位，prefix[3] 变成裸任务名（如 `"t1"` 而非 `"tb:t1"`），**不报错、返回错值**——与 docstring 自己的"Anything malformed is a CodeBugError"相抵。今天 reports 的 phase_label 由调用方传入（测试都用 `p0:d` 形态）不会踩到；Wave 2 的相位驱动/配置解析必须强制 `pNN:<name>` 形态（见接线清单第 7 条）。

### 【细节 D3】phase_bank 的标签转写允许冒号与下划线互撞

- `phase_bank.py:57-79`：冒号映射为 `_`，但 `_` 本身合法，`"p1:a"` 与 `"p1:a_a"`、`"p1_a"` 会转写成同名目录；docstring 自己的动机（"a quiet rewrite could collide two phases into one directory"）没有被完全执行。补一条"标签必须恰含一个冒号且位于 pNN 之后"的校验即可。

### 【细节 D4】exam 报告每 (skill, version) 一行，决策每行一条任务清单：同一技能可产出多条、可能互相矛盾的任务行

- `reports.py:262-293` 对同一考题按版本各出一行（判定过的版本 + 泄题版本 + 存量版本），`decide_task_list` 逐行出 TaskListRow（`decision.py:872-905`），target_id 同为该技能。若存量版本与被测版本不一致（改写刚落），会同时出现一条 normal 行和一条 zero_execution 行（与 B1 叠加）→ 同一技能一行 keep 一行 delete。Wave 2 的任务清单执行器需要合并规则，或报告侧标注"存量版本行仅供可见性"。

### 【细节 D5】写作/证据驱动用字面量校验 action，未引用 decision 的常量

- `write.py:232-235`（`!= "revise"`）、`write.py:287-290`（`!= "add"`）、`add_path.py:62-65` 同。当前值与 `decision.REVISE/ADD`、`contracts.TaskListRow` 注释一致，改名会以 ValueError 响亮失败而非静默，故仅是整洁问题；建议改引 `decision.REVISE` / `decision.ADD`。

---

## 核对通过清单

按任务清单七个评审点逐项核对，以下各项**通过**：

1. **词汇机械一致**：verdicts 的 status 三值（normal / not_testable / not_run_control_perfect）与 cause 六值 + weak_already_knows，与 decision 消费端（ROW_*、NOT_TESTABLE_CAUSES、compare_conclusion 的校验集）逐词对应；不存在 verdicts 会产出而 decision 不认识（或反之）的取值；`summarize_unit`（verdicts.py:346-431）在产出端、`compare_conclusion`（decision.py:587-637）在消费端各自强制固定集，未知值两头都是 CodeBugError。语义层的冲突见 B1，机械层无缺口。
2. **报告形状**：`summarize_unit` 构造的字段与 `contracts.AcceptanceReportRow` 逐字段一致（`tests/unit/core/test_contracts.py` 钉字段名）；三种 kind 全走同一 `AcceptanceReportRow`；reports 只读（W1-A 有"读报告前后目录逐字节不动"的行为测试，`tests/unit/test_w1a_verdicts.py:328-373`）。`_parse_unit_id` 与 `make_unit_id` 输出自洽（对 `tb:t1`、`traj/tb:t1/strong/00/seg01/exam` 两类 task_ref 手工推演均正确切分）；W1-D 的临时形态不会与冻结形态混淆（缺 pNN 头，进 `_parse_unit_id` 必炸，属响亮失败），唯两套规范化并存见 P2。
3. **R4 断言成立，双侧有钉**：验收侧 `test_w1a_verdicts.py:316-325`（调用面：verdicts.py/reports.py 无 `discard_skill(`/`accept_skill(` 调用）+ `:328-373`（行为面：monkeypatch 炸弹 + 技能目录逐字节比对）；决策侧 `test_w1c_decision_entry.py:309-326`（库对象 to_dict 前后全等，即使表格删除全部覆盖技能）。`verdicts → decision` 为单向 import，无环。
4. **R2 摘除后链路**：`verifier_ran`/`exception_type`/`exception_message` 三项事实全仓无 src 消费方（仅插件自身与测试），具名规则（`BENCHMARK_VERDICT_RULES`、`get_verdict_rule`）同样无运行时消费方——两者都是 Wave 2 接线缺口而非 Wave 1 错误，确认成立；但中间态的双向语义变化（正向无凭据计成功）需补披露，见 P3。`classify_result` 仅为 `scripts/analysis/tb_archive_validate.py` 的历史镜片，`run()` 不再调用，与提交说明一致。
5. **过渡桥接互不矛盾（除 B1/P2 两处已单列）**：`verdict_from_trajectory`（R2b 删除项）只被 reports 的 baseline/live 读取器使用，R2b 迁移路径（改 `verdict_from_run`）与 docstring 一致；五个 `_legacy_resolve`（W1-D 的 Wave 2 删除项）结构化派发无歧义（legacy 形态 params 恒在第二位，五驱动的 legacy 形态逐一核对无碰撞）；12 个旧 `exam_*`/`live_*` 规则（W1-C 的 Wave 2 删除项）的调用方（acceptance_submit / live_verdicts / live_attribution / 两个验收驱动）在本波全部未动，无半删状态。
6. **E1–E6 等价断言未被绕过（静态核对）**：六提交对 `tests/golden/` 与 `tests/fixtures/` 的改动为零（`git diff c6a9fb8..HEAD --stat` 仅 R0 的 `semantics.py` 一处模块改名跟随，属 Wave 0）；逐字节金矩阵对照的是 Wave 1 之前的既存夹具，语义仪器对照 69904f7 基线束，tamper 自检（`test_semantic_instrument.py` 的五个篡改用例）证明每条 E 断言真的会红。等价成立的原因同时是它的边界：新模块（verdicts/reports/decision 新入口/evidence/author/phase_bank）均未接入流水线，金矩阵**验证不到它们**——这部分由各代理新增的场景测试（如 `test_w1a_discard_scenarios.py` 对全弃置分支的钉子）承担，本评审确认这些场景测试存在且断言到位。E6 attributions 逐字节全等、"允许形状差异"条款未动用，与 W1-E 汇报一致。
7. **其余咬合**：`grade_attempt` 重接线后 exam_once 判卷走验收入口（`exam_once.py:147-149`），`acceptance_leak.find_leak_verdict` 公有化无残留私有引用；reports 借用 W1-B/W1-C 文件的私有助手（`lm._ledger_path`、`_exam_attempts` 等）属 docstring 披露的合并缝隙，W1-C 提交未改名这些符号，缝隙完好；方案评审 A 曾标的"C 相位 delta 不得进决策"在本波实现正确（`final_test_delta` 定位为汇总统计、`decide_task_list` 双侧拒绝 C 相位报告，`decision.py:828-833`）；W1-E 的预算耗尽=无变化与边界 §4.3.2、W1-C 的 `revise_outcome`（`decision.py:956-969`）三方一致；`_STATUS_CAUSE_BY_TERMINAL` 覆盖 `live_verdicts.judge_round` 实际可写的全部终态（weak_already_knows / live_injection_overflow / live_undetermined；leak_audit 一键为前瞻条目，无害）。

---

## Wave 2 接线清单汇总

六提交的 docstring/提交说明留给 Wave 2 的接线项合并如下，**其中标 ★ 的两条是本评审发现的、原始接线说明之间互相矛盾、必须在开工前先裁决的**：

1. ★ **裁决 zero_execution 的唯一含义**（B1）：验收侧细分 cause 或决策侧拒收在途行；同时明确对照行 valid=0 时决策不得产出弱化理由的删除（对照未测量 ≠ 无提升空间）。
2. ★ **统一 unit_id 规范化的落点**（P2）：删除 `units.py` 的本地 json 规范化，相位驱动统一用 `verdicts.make_unit_id` 重建完整冻结键；`units.py` docstring 的"single place"措辞随之改写。
3. **TB 具名验收规则**：注册消费 `verifier_ran`/`exception_type` 事实的判定规则并把 `BENCHMARK_VERDICT_RULES` 接进执行→验收链路；在此之前正向无凭据 reward 计成功（P3，须列入有意变化清单）。
4. **R2b 词汇迁移**：`correctness: Optional[bool]` + 运行指标取代三值 status，`InfrastructureError` 承接基础设施失败；删除 `verdict_from_trajectory`，reports 的 baseline/live 读取器改走 `verdict_from_run`；轨迹产物字节差异逐处披露。
5. **无效重派回路**：`invalid_redispatch_exhausted` 目前只有 reports 的"missing"标记这一个产生方；相位驱动要实现"验收判无效 → 重派（带上限）"的调度。
6. **交付调用点替换**：删五个 `_legacy_resolve` + `units.dispatch`，stage 6 与 live 驱动改调 `resolve(bank, tasks, params)`；retrieval 审计保持 `audit_rows`/`write_audit` 侧信道。
7. **相位标签**：相位驱动产出 `pNN:<name>` 标签并在配置解析处校验形态（D2、D3 两处静默坑由此关闭）；逐相位技能库目录按此命名。
8. **决策接线**：`decide_task_list` + `remaining_checks` + `reusable_units`/`revise_outcome` 接入 phase_learn；任务清单执行器需要"同一技能多版本行"的合并规则（D4）与 `[]` 注入行的处置口径（P1）。
9. **删 12 个旧 exam_*/live_* 规则**：随 K4/K6 壳一起退役（acceptance_submit、live_verdicts、live_attribution、两个验收驱动是其他代理冻结文件，本波保留正确）。
10. **G10/G11 迁移**：live_attribution 旧路径（其行形状被 `_revisions_complete`、`scripts/audit/audit_live_run.py`、金矩阵三方钉死）换到 evidence/author 新驱动；"exams 开关删除"随调用点迁移（stage3 与 v3 配置校验仍读它，W1-E 已披露）。
11. **整理的序列位置分支**：仅末次学习相位跑配置的整理方法、中间相位产 `produce_as_is` 工件——归相位驱动（W1-F 明确留白）。
12. **逐相位技能库的消费方**：`load_phase_bank` 已就位但无调用方；Wave 2 的交付要从相位工件（而非 run 级库）读成员与版本。
