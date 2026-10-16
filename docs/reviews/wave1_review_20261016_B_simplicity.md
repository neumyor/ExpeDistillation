日期：2026-10-16
一句话说明：Wave 1 六提交（R1–R7）合并结果的简洁性评审 B——过度设计、过渡代码标注、初级程序员可读性、命名、测试可读性、死代码。
对应 log.md 条目：2026-10-16 Wave 1（R0–R7）合并（待补 log.md 条目）。

---

## 0. 评审范围与独立复验

- 对象：`feature/multi-method` 分支 `c6a9fb8..HEAD`（合并 HEAD 87a047b），共 13 个提交、
  74 个文件、+11465/−2669。重点逐行读了：`composites/verdicts.py`、`composites/decision.py`、
  `composites/reports.py`、`composites/phase_bank.py`、`methods/delivery/`（units.py + 五个驱动）、
  `methods/evidence/`（三个文件）、`methods/author/`、`methods/curation/phase_bank.py`、
  `benchmarks/tb/plugin.py` 的改动、`core/contracts.py`、`stages/stage6_test.py`；
  12 个 `tests/unit/test_w1*_*` 测试文件全部读了测试名，抽读了其中 5 个全文。
- 独立复验（不是转述提交说明）：
  1. `.venv/bin/python -m pytest -q` 全量：**2311 passed + 3 deselected**（57s，exit 0）；
  2. `.venv/bin/python -m ruff check src/ tests/`：All checks passed；
  3. `tests/test_layering.py`：3 passed；
  4. 对 12 个 exam_*/live_* 决策规则、全部新入口函数逐一 grep 了生产调用方（结果见下文）；
  5. 词表核对：拿边界定稿（`docs/decisions/execute_learn_module_boundaries_20261016.md` 第 4 节）
     逐行对照了 decision.py 的结论常量与动作表。

先说总体判断：**这轮合并没有造"通用框架"，计划硬约束第 9 条（不引入依赖图、调度引擎、
插件自动发现、决策规则注册表）逐项核对都守住了**。代码明显在往"一个函数只回答一个问题"
的方向收敛：比较规则是固定两行的表（`_COMPARE_RULES`，注释里明确写了"不做注册表"），
验收词表收进一个模块，五个 delivery 驱动共用一套拼行的辅助函数。测试的命名质量是这轮
最好的部分。发现 **0 条阻断、3 条偏移、8 条细节**。三条偏移的共同点是：为"同一名字服务
两种形态/两个定义点"付出的复杂度，都有更直的写法，且调用方全在本仓库里、现在就能改。

下面逐条。每条给：严重程度、证据（文件:行号）、直接简化方案。

---

## 1. 偏移：`units.dispatch()` 按参数形状嗅探路由，为保住一个名字付出了一台小机器

**严重程度：偏移**

证据：`src/expedistill/methods/delivery/units.py:84-111`。五个驱动的公开入口都叫
`resolve`，但接受**两种签名、返回两种完全不同的东西**：冻结形
`resolve(bank, tasks, params) -> ExecutionUnitList` 和旧形
`resolve(ctx, params, ...) -> (K1 kwargs, audit)`。`dispatch` 靠数参数个数加
`isinstance(args[1], params_class)`（旧形参数在第二位、冻结形在第三位）猜调用方
想用哪个形态。同一个函数名，一次调用返回"单元行列表"，另一次返回"给 K1 的关键字
参数字典加一个 audit"——对初级程序员，这是整轮合并里最难一眼看懂的一处；
猜错形态时不一定报得清楚（比如把冻结形的 `(bank, params, tasks)` 误传成参数在
第二位，会落进旧分支、以完全错误的类型继续跑）。

调用方核对过了：旧形只有 **4 个调用点，全部在本仓库本轮已改的文件里**——
`stages/stage6_test.py:275`（retrieval）、`:313`（none）、
`methods/acceptance/live_run.py:777`（group_by_task）——加上一个**没有任何生产
调用方**的 per_skill 旧形（见第 4 条）。也就是说这台路由机器服务的调用方全部
是可编辑的。

直接简化方案：`resolve` 只保留冻结签名；旧形态给一个直白的独立名字（如
`resolve_k1_kwargs(ctx, params, ...)`），把 4 个调用点改成新名字，删掉
`dispatch` 和 `units.py` 里 28 行路由代码。调用点在 stage6_test.py 和
live_run.py 里本来就只各占一行。如果本轮不想动调用点，至少给 `dispatch` 的
docstring 加一句"误传形态时的失败样子"，并在 Wave 2 清单里点名删它。

---

## 2. 偏移：冻结的 cause/status 词表有两个定义点，与"只定义一次"的自述矛盾

**严重程度：偏移**

证据：六个 not_testable 原因（leak、injection_overflow、exam_generation_failed、
invalid_redispatch_exhausted、all_holes、zero_execution）在
`composites/decision.py:442-456` 定义一遍，又在 `composites/verdicts.py:114-127`
原样再定义一遍；三个行状态（normal / not_testable / not_run_control_perfect）同样
两处定义（`decision.py:434-436` 的 ROW_* 与 `verdicts.py:106-110` 的 STATUS_*）。
两个模块都把这些名字放进 `__all__` 导出。而 `verdicts.py:53-55` 的注释自己写着
"defined once in composites.decision ... so K4, K6 and this module cannot drift"，
`core/contracts.py:36-39` 也声明 R0 把 cause 词表放在 decision——实际只有
`WEAK_ALREADY_KNOWS` 真正做到了一处定义（verdicts 从 decision 导入），其余九个
常量是两份手抄。今天不会漂（两个测试文件各自把词表钉在字面值上，改哪边都会红），
但"一个词表、两个定义点、靠两份字面值测试间接对齐"正是未来重构时最容易漏改的
形状，也和模块自己声称的设计不符。

直接简化方案：verdicts.py 已经 `from expedistill.composites import decision`，
把六行 `CAUSE_X = "x"` 换成从 decision 导入（保留 `CAUSE_WEAK_ALREADY_KNOWS =
decision.WEAK_ALREADY_KNOWS` 的现有写法即可），STATUS_* 三行同理对齐或导入；
verdicts 若想维持对外名字不变，加一行再导出。净删约 14 行，"只定义一次"从注释
变成事实。

---

## 3. 偏移：`unit_id` 一个字段名、两种构造格式，读它的函数只认其中一种

**严重程度：偏移**

证据：冻结的单元键是五段式 `phase:benchmark:task:role:sha8`
（`core/contracts.py` 模块注释、`verdicts.make_unit_id`）；而 delivery 侧
`units.unit_id()`（`methods/delivery/units.py:57-69`）写进
`ExecutionUnitRow.unit_id` 的是两段式 `<task_ref>:<sha8>`。`decision.reuse_key`
（`composites/decision.py:913-928`）和 `decision._parse_unit_id` 按五段式解析
这个字段。也就是说：当前树里唯一在生产 `ExecutionUnitRow` 的代码（五个 delivery
驱动）写出的 unit_id，喂给 reuse_key / reusable_units / decide_task_list 会
立刻 `CodeBugError: malformed unit id`——会响亮失败（这是好事），但"哪个格式
才算 unit_id"的答案要读三个文件才能拼出来：units.py 的 docstring 说"Phase 2 的
阶段驱动会重建完整冻结键"，contracts.py 说规则冻结在验收侧，decision._parse_unit_id
的 docstring 只描述五段式。Wave 2 的接线者（正是要写 phase driver 的人）只要
直接用 delivery 的 make_row，就会踩中。

直接简化方案（三选一，成本都低）：a) `units.make_row` 增加一个显式参数收
phase_label/benchmark/role，直接产出五段式（两段式本来就是冻结键的缺件版）；
b) 把 delivery 侧函数改名（如 `row_key`），并在 `reuse_key` 的 docstring 里加
一句"unit_id 必须是冻结五段式，delivery 的 Wave 1 短格式喂进来会 malformed"；
c) 至少在 `units.unit_id` 的 docstring 里点出"decision.reuse_key 不认这个格式"。
推荐 a 或 b，能让 Wave 2 不靠踩坑知道这件事。

---

## 4. 细节：per_skill 的旧形态没有任何生产调用方，是"立即死掉"的过渡代码

**严重程度：细节**

证据：`methods/delivery/per_skill.py:66-87`。`_legacy_resolve` 的 docstring 自己
写着"M2 wired no caller"；全仓库 grep，唯一调用它的是契约测试
`tests/unit/methods/delivery/test_drivers.py:59-68`。其余四个驱动的旧形态都有
真实调用点（stage6_test、live_run），只有这一个是为一个从未存在的调用方保留的
兼容层，还要靠 `dispatch` 的 isinstance 分支来猜。

直接简化方案：删掉 per_skill 的 `_legacy_resolve` 和它的 dispatch 兼容（resolve
只留冻结签名），对应的契约测试断言（K4 渲染一致性）改写成直接调冻结形——
test_w1d 已经覆盖冻结形，这里只需保留"per_skill 的 block 与 K4 的渲染一致"那
一条断言。

---

## 5. 细节：12 个 exam_*/live_* 旧规则的退场标注只在模块 docstring 的第 100–113 行

**严重程度：细节**

证据：`composites/decision.py:100-113` 明确写了"the exam_*/live_* rules above
are the LEGACY per-mode rules ... they retire with the Wave 2 phase drivers"——
标注本身是清楚的。问题在位置：一个初级程序员从编辑器跳进
`exam_bare_answer_action`（第 249 行）时，看到的分节标题只是
`# Exam rules (K4)`，没有任何"这是要退役的旧规则"的信号；同文件里新入口
`decide_task_list`（第 779 行起）与旧规则混排，靠 docstring 长度区分新旧。

直接简化方案：两个分节标题改成
`# Legacy exam rules (K4; retire with the Wave 2 phase drivers)` 和
`# Legacy live rules (K6; retire with the Wave 2 phase drivers)`，两行注释解决，
不必逐函数加。

---

## 6. 细节：`verdict_from_attempt` 没写去留，与同文件的 `verdict_from_trajectory` 标注不一致

**严重程度：细节**

证据：`composites/verdicts.py:304-309` 的 `verdict_from_trajectory` 明确写了
"the R2b BRIDGE ... Deleted in R2b"；紧挨着的 `verdict_from_attempt`（324-339 行）
没有一句说它是长期 API 还是过渡桥——后来者无从判断该不该在 Wave 2 里保留它。
同样，`verdict_from_run`（293 行）倒是写了"once R2 lands"，但 `grade_attempt`
（438-447 行）作为纯转发包装也没有去留说明。

直接简化方案：`verdict_from_attempt` 的 docstring 加一句（我的理解：它是读已
判尝试的机械映射，Phase 2 仍在，如果不对就照实写）；`grade_attempt` 加一句
它就是 exam_once 的接线点、长期保留。各一行。

---

## 7. 细节：`Action` 的 docstring 与 `table_action` 的实际用法矛盾

**严重程度：细节**

证据：`composites/decision.py:232-243`，`Action` 的 docstring 写
"``reason`` is the Skill.discard_reason when the kind is ``DELETE``, else None"。
但 `table_action`（659-679 行）返回 `Action(KEEP, CONCLUSION_IMPROVED)`、
`Action(REVISE, CONCLUSION_NO_GAIN)`——非 DELETE 时 reason 不是 None，而且这些
reason 确实流进了 `TaskListRow.reason`（`decide_task_list` 902-905 行），TaskListRow
的契约（contracts.py:190-192）也定义 reason 为"为什么做这个动作"。是 Action 的
docstring 过时了，不是行为错了。

直接简化方案：把 Action 的 docstring 改成"reason 是这个动作的原因：DELETE 时
携带 discard_reason 词表，其他动作携带结论（如 improved / no_gain）或空"。

---

## 8. 细节：`live_group.py` 跨模块导入 5 个下划线私有名，没有像 reports.py 那样写 seam 说明

**严重程度：细节**

证据：`methods/evidence/live_group.py:33-44` 从 `composites.live_attribution`
导入 `_LIVE_VIEWS_BUDGET_CHARS`、`_clip_view`，从 `composites.live_measurement`
导入 `_require_audit_row_from`、`_require_group`、`_trajectories_from`，无一句
解释为什么用私名。同轮的 `composites/reports.py:50-53` 做同样的事时写了
"Private-name seams kept for the merge: ... (W1-B / W1-C own those files in this
wave)"。这两个被导入的模块现在是三处消费（原主、reports、evidence），私名已经
名不副实；不标注的话，W1-B/W1-C 的作者改函数名时不会想到 evidence 在跟。

直接简化方案：要么把这几个小函数转正（`clip_view`、`require_group`、
`trajectories_from`），要么照 reports.py 的写法补一行 seam 说明。

---

## 9. 细节：`register_verdict_rule` 出现在 `__all__` 里，是一个没有外部使用者的扩展点邀请

**严重程度：细节**

证据：`composites/verdicts.py:81` 把注册装饰器导出为公开 API，但仓库里没有任何
模块在 verdicts.py 之外注册规则，两条规则（scorer、exam_judge）都在本文件里。
边界定稿第 2.3 节说"换判据 = 起新名字"是对的，但按分层规则，benchmark 插件
（第 3 层）不能 import 组合层（第 5 层），未来的新规则也只能写在 verdicts.py
本文件里——公开装饰器并不换来任何可插拔性，只多了一个让人以为"可以从外面注册"
的入口。

直接简化方案：从 `__all__` 去掉 `register_verdict_rule`（保留 `get_verdict_rule` /
`default_verdict_rule` 这些真正被消费的名字即可）；装饰器本体留在文件里不动。

---

## 10. 细节：两个模块各有一个 `_baseline_rows`，同名不同义

**严重程度：细节**

证据：`composites/decision.py:741` 的 `_baseline_rows` 是"第一个学习阶段：从 D 阶段
报告生成新增任务行"；`composites/reports.py:135` 的 `_baseline_rows` 是"D 阶段
执行单元的验收报告行"。都是模块私有，不影响运行，但 grep 时两个命中会让读者
困惑片刻。

直接简化方案：decision 侧改名 `_baseline_add_rows`（它产出的全是 add 行），
一行事。

---

## 11. 细节：decision.py 的模块 docstring 有 114 行，把"清单落点记录"搬进了代码

**严重程度：细节**

证据：`composites/decision.py:21-98` 逐条复述了 exam 8 条 + live 9 条决策规则
各自的落点（哪些进了本模块、哪些留在 K4/K6、为什么留下）。这些内容在修复计划
文档（repair_plan 第 3 节）里本来就有，docstring 里再放一遍的好处是"代码旁边
能查到"，代价是：初级程序员打开这个文件，前 114 行没有一个可执行的句子，
而且新旧规则的分界（真正的 API 说明）埋在第 100 行之后。verdicts.py（43 行）、
reports.py（54 行）、stage6_test.py（93 行）的模块 docstring 也有同样倾向，
只是程度轻些。这个写法与本仓库"文档密度高"的既有风格一致，所以只记细节、
不要求整改；如果改，方向是：模块 docstring 留"这个模块是什么 + 输入输出 +
词表"（decision.py 的 1-20 行和 100-113 行已经够了），清单落点记录移回
docs/decisions/ 并留一行指针。

---

## 12. 细节：`ensure_fact_only` 的递归走查会进 `old_skill` 的全部字段，未来有误伤面

**严重程度：细节**

证据：`methods/evidence/package.py:91-108`。守卫本身目的明确（证据包只装事实、
不装动作），对 facts 的 kind 检查也对。但 `_walk(asdict(package))` 递归进
`old_skill`（一个完整的 Skill 对象），只要 Skill 将来新增一个恰好叫 `action`
的字段（contracts/模型层并不会阻止），所有修订包都会被这个守卫拦下来，报错
信息还指向"证据模块装了动作"。今天无事，误伤路径窄但隐蔽。

直接简化方案：走查时跳过 `old_skill`（它是既有持久化对象，不是证据包要守卫
的内容），只查 facts/segments/source_experiences/target。一行 if 的事；不改也
可以接受，但值得在守卫的 docstring 里写一句"old_skill 的字段不在守卫范围内"。

---

## 13. 核对通过清单

以下各项检查过、没有问题，列出以免重复劳动：

1. **计划硬约束第 9 条（反过度设计）整体守住**：没有依赖图、没有调度引擎、
   没有插件自动发现、没有决策规则注册表。比较规则是 `_COMPARE_RULES` 固定
   两行表（decision.py:552-555，注释明确写了"no decision-rule registry"）；
   判定规则注册表复用的是既有的 `core/registry.py` 通用基建，没有为新规则
   造框架。`EvidencePackage` 是第五个交接形状的唯一定义点，没有为"多种包
   格式"预留分发接口；author 的分发按包形状（有无 exam_question /
   failure_view）走，没有策略对象。
2. **没有只有一个调用方的"通用"框架**：`units.py` 的拼行辅助被五个驱动共用；
   `write_as_is` 被 run/produce_phase_bank 两处共用。唯一的单调用方转发是
   `verdicts.grade_attempt`（第 6 条已提），它至少在 docstring 里讲清了"验收
   拥有判卷"这一归属理由，可接受。
3. **过渡代码的退场路径逐个核过**：五个 `_legacy_resolve` 都写了
   "Wave 1 compat (removed in Wave 2 ...)"；`verdict_from_trajectory` 写了
   "Deleted in R2b"；tb 插件的 `classify_result` 写了"kept ONLY as the archive
   validator's historical lens"且有真实调用方（`scripts/analysis/tb_archive_validate.py:110`），
   `run` 已改走 `result_facts`；`stage6_test.py` 末段写了"dies with the Wave 2
   phase drivers"。缺标注的只有第 5、6 条两处。
4. **死代码核查**：12 个 exam_*/live_* 规则全部有生产调用方
   （acceptance_submit ×4、methods/acceptance/exam.py ×1、live_verdicts ×3、
   live_run ×2、live_attribution ×1），不是死代码；唯一"立即死"的是第 4 条的
   per_skill 旧形态。`pytest` 全量 2311 passed，stage6_test 对已搬走的比较逻辑
   无残留引用（`final_test_delta` / `control_arm_source` 已在 stage6_test.py:279,283
   正确接线）。
5. **命名与边界定稿一一对应**（拿 boundaries 文档第 4 节逐行对过）：提升→
   improved→保留；没提升→no_gain→修改/删除；无提升空间→no_headroom→删除；
   测不了→not_testable+六个原因；摸底行→baseline_gap→新增
   （BASELINE_GAP_REASON="weak_fails_and_no_skill" 正是"弱模型做不好、且还没有
   技能的任务"）；配套规则 1/2/3 分别落在 reuse_key/reusable_units、
   revise_outcome、control_perfect_skips_run，"修改后一字不差也算没变"在
   reuse_key 的 docstring 里原文出现。规则 9（测试结果不进学习）由
   `FinalTestReportRejected` 强制，两侧入口都挡。
6. **状态/原因的自洽性**：`not_run_control_perfect` 的 cause 与 decision 的
   `weak_already_knows` 是同一字符串、同一事实，一处定义（verdicts.py:134
   从 decision 导入），并有专项测试钉住（test_w1a_verdicts.py:54-58）。
7. **初级可读性的正面项**：`core/contracts.py` 四个交接形状每个字段都有注释，
   是全库读起来最省力的数据契约；`decision.table_action` 把第 4.2 节的动作表
   一个分支一行地写出来，docstring 即表格；`composites/phase_bank.py`、
   `methods/curation/phase_bank.py`、`methods/evidence/add_path.py` 的模块
   docstring 都做到了"是什么、输入、输出"三句话齐。长参数列表
   （`decide_task_list` 9 个、`collect_group_evidence` 10 个，全部关键字参数、
   大多带默认值）在当前用途下可接受，不建议引入参数对象（那会加一层抽象，
   违背本轮的简洁目标）。
8. **测试可读性**：test_w1 系列的命名全部行为化——看名字就知道钉什么
   （例：`test_scenario_bare_answer_right_is_weak_already_knows`、
   `test_not_testable_leak_revises_while_a_check_remains`、
   `test_budget_exhaustion_is_no_change_not_a_discard`、
   `test_retrieval_below_threshold_keeps_the_empty_list_semantics`、
   `test_every_discard_reason_has_a_scenario_pin`）。
   `test_w1a_discard_scenarios.py` 给每个 discard_reason 配了一个端到端场景，
   这是把"词表不是空话"钉进测试的正确做法。测试文件头的 docstring 也都
   写了各自钉的是哪条计划条款。
9. **执行侧只记事实（R2）**：tb 插件的 `result_facts` 是纯函数、无判定；
   旧的三态分类只留给归档校验器，`run` 路径已不再调用（diff 核过）；
   "pseudo zero 不再由执行判无效、verifier_ran 作为原始事实旁挂"与
   W1-B 的提交说明一致。
10. **验证命令**：`.venv/bin/python -m pytest -q` → 2311 passed + 3 deselected；
    `ruff check src/ tests/` → 全过；`tests/test_layering.py` → 3 passed。

---

## 14. 结论

三条偏移都是"同一处复杂度可以直接删掉"的形状：`units.dispatch` 的形态嗅探
（调用方全在树上）、词表双重定义（import 即可）、unit_id 双格式（改名或补全
构造即可）。都不阻塞 Wave 2，但都建议在 Wave 2 动 phase driver / 接线之前处理——
那时这三个位置的混淆成本最高。细节项按顺手程度处理即可，第 7 条（Action 的
docstring 与用法矛盾）建议本轮就修，一行字的事。
