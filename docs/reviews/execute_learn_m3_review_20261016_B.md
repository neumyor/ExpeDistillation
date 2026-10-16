日期：2026-10-16
一句话说明：执行—学习重构 M3 步骤的验收评审 B——简洁性与反过度设计。
对应 log.md 条目：2026-10-16 M3 学习侧抽取。

---

## 0. 评审范围与独立复验

- 对象：`git diff 95442b9 d80d6d3`（+1478/−127，21 个文件），HEAD d80d6d3。
- 契约：docs/decisions/execute_learn_implementation_20261015.md 的 §1 纪律、§3（读取器表 + 决策清单 D10）、§9 M3 行。
- 本评审独立复验的证据（非转述提交说明）：
  1. `pytest`：**2039 passed + 3 deselected**（53s，与提交说明的数字一致），其中金矩阵 8 组合快照 + 2 个崩溃续跑组合在套件内原样通过；
  2. `ruff check src/ tests/` 通过；`tests/test_layering.py` 在套件内通过；
  3. 金 fixtures 在本 diff 中**零改动**（`git diff --stat -- tests/fixtures/` 为空）——"golden 逐字节无 diff"有真实仪器支撑（tests/golden/test_golden_matrix.py 是常备套件，非一次性脚本）；
  4. 两套崩溃续跑套件（tests/composites/test_acceptance_resume.py、test_live_acceptance_resume.py）不在 diff 文件清单里，零行改动；
  5. 四条"留原处"逐行读码核对；decision.py 全部 12 个规则函数的生产调用点逐一 grep 核对；决策测试的关键期望对照方案 §3 手工推导（见 §8）。

先说总判断：**M3 是一次"每个新函数都有当下调用方"的抽取，不是提前造机器**。两个新模块的体量需要先拆穿：decision.py 339 行里模块 docstring（决策清单逐条落点表，本身是 D10 验收基线的载体）占 98 行、imports+`__all__` 32 行，真正的规则代码约 160 行；reports.py 244 行中约 22 行 docstring，三个读取器各 50–60 行。代码密度低是因为每条规则都带"为什么"的 docstring，不是逻辑膨胀。

---

## 1. Action 抽象的最小性 ——【赞同】（附 2 条琐细）

**事实核对**（任务提示说"13 个函数"，实际是 **12 个规则函数 + 1 个 Action dataclass**；提交说明里的"decision rules 13"是测试条数）：

- `Action(kind, reason)` 两个字段，frozen dataclass，无第三字段、无携带数据的 payload。kind 是裸字符串常量 KEEP/DELETE/REVISE（decision.py:134–136），**没有第四种 kind**：全仓 `Action(` 构造只出现在 decision.py 内部 12 处，kind 全部取自这三个常量。不用 Enum 而用字符串常量，与 `Skill.discard_reason` 是自由文本字段的既有风格一致——这里造 Enum 反而是过度设计。
- REVISE 动作不携带改写素材（judge_reason、weak_answer 仍由调用方持有的 Attempt 对象提供），Action 没有长成消息总线。这是本次抽取最克制的一处。
- 12 个函数每个恰好对应方案 §3 清单的一个 bullet，每个的形参就是该规则需要的事实，无一个是投机签名。碎片化检查：最接近重复的是 `exam_stored_pass_action(passed)` 与 `exam_answer_action(verdict=True, last_round=...)`（同一规则"本版答对→keep"在续跑预检与轮内分支两个时点的两种事实形状）；合成一个函数需要谎传 `last_round`，两函数是正确粒度。最薄的两个：`live_no_op_fires`（函数体一行 `==`，单调用点 K6 `_no_op_guard`）与 `exam_voided_rewrite_source`（coalesce，但**双消费者**——K4 泄露分支与 reports.exam_report 同用，防读取器与循环漂移，抽取有实价）。
- decision.py 只 import core.models（第 5 层→第 0 层），不 import K4/K6，方向正确（见 §2）。

【琐细 B-1】`exam_measurement_exhausted_action()`（decision.py:233–240）是**零参、返回常量**的函数——`Action(DELETE, MODEL_OUTPUT_ERROR)` 的包装。调用点 methods/acceptance/exam.py:169 写 `discard_skill(store, skill, exam_measurement_exhausted_action().reason)`，而 `MODEL_OUTPUT_ERROR` 已在同文件 import，直接用常量更短更直。它的存在价值只剩"清单 17 条逐条有具名落点"的完备性（docstring 里"there is no read state to decide from"的自我辩解是诚实的：这条确实没有可读状态）。为完备性付的仪式最小、仅一处调用点，不阻塞；M4 调用点迁移时可顺手简化。

【琐细 B-2】acceptance.py:338 的 `if decision.exam_stored_pass_action(accepted is not None) is not None:` 是双重 None 包装，可读性差于原先的 `if accepted is not None:`。规则落点进 decision.py 的收益与调用点的绕口是真实的取舍，但这一行的形态可以更好（例如让该调用点直接比较 `action is not None` 之外，保 `Action` 但把布尔→Action 的翻转写成一行注释清楚的形状）。不阻塞，M4 相位驱动成为主调用方时自然消失。

---

## 2. 词表 re-export 模式 ——【赞同】

**为什么定义处必须搬**（三个理由，代码可证）：

1. **import 环**：K6（live_acceptance.py）必须 import decision（`_finalize_group`/`_no_op_guard`/`_judge_round`/`_attribute_group` 都调规则函数）；若词表留在 K6、decision 反向 import K6，即成环。备选"词表进 core 之类的小模块"会为 8 个字符串常量新建一个文件，比放进本就存在的 decision.py 更重。
2. **消除既有三处定义**：M3 之前 `weak_already_knows`/`leak_audit`/`retry_exhausted` 是 K4 的字面量、六个常量在 K6、`model_output_error` 又在 exam.py 有一份 `MODEL_OUTPUT_DISCARD`。M3 后全部收敛为 decision.py 单一定义；`MODEL_OUTPUT_DISCARD` 全仓无残留（grep 验证）。
3. **exam 驱动跨层取词**：exam.py（第 6 层）改从 decision（第 5 层）取词，composites 不得 import methods 的纪律未被破坏（方向是 methods→composites，合法）。

**re-export 是否让定义难找 / 单一真相是否钉住**：K6 侧 re-export 带注释明说"Defined in composites.decision since M3...See decision.py"（live_acceptance.py:164–172）；tests/unit/composites/test_decision.py 的 `test_vocabulary_is_the_one_definition` 用 **`is` 恒等**（不是 `==`）钉住 6 个常量与 `STRICT_SHORT_CIRCUIT_RULES is la._ALL_SUCCESS_SAFE_RULES`——两个独立构造的同元素 frozenset 不会通过 `is`，所以 re-export 退化为复制会被测试抓红。单一真相钉死。re-export 的现有消费者是真实的：K6 内部（`_no_op_guard` 用 `LIVE_NO_IMPROVEMENT`、judge_round 用 `WEAK_ALREADY_KNOWS`/`_ALL_SUCCESS_SAFE_RULES`）与 test_live_acceptance_resume.py:261。保留别名是最小改动量的正确形态。

---

## 3. 留原处四条的诚实性 ——【赞同】（逐条独立判断）

方案清单 exam 8 条中抽 7、live 9 条中抽 6（含 L16 抽判据、留写侧），四条留原处。我逐条读码核对，结论：**四条全部是"判定与落盘次序熔合"的真实约束，不是抽取不彻底的借口**。

- **E8 改写预算耗尽（留 K4 `_rewrite_round`，acceptance.py:232–279）**：预算事实本身存在于逐 try 的 REWRITE_FAILURE_LEDGER 追加里（try_index、exhausted 标记跨崩溃续算），"耗尽→版本不变、轮已消耗"是循环的 fall-through 而非一个可返回的动作——抽出的"决策函数"将只会返回"什么都不做，继续循环"，而循环记账就在它想抽走的那几行里。判定正确。
- **L10 G10 计划应用（留 K6 `_apply_revisions`，live_acceptance.py:1063–1099）**：逐条目的版本重放守卫（version==from_version→apply，+1→already landed，其他→CodeBugError）必须对着 store 的**当下**版本判，半应用计划的续跑真相就是这几行；从报告行决策等于把守卫语义复制一份。判定正确。
- **L16 G10 耗尽（判据抽出 `live_attribution_exhausted`，in-loop 分支留 `_attribute_group` 901–912）**：这是四条里唯一的"半抽取"，切分线画得准——**读侧判据**（账本里已有 exhausted 行、或失败数已达预算）是纯函数，抽走了且有生产调用点；**写侧分支**（先 `_discard_group` 终态、后补 exhausted 回执行，注释明言这个次序就是续跑协议）留在原地。判定正确。
- **L17 G11 耗尽（留 K6 `_revise_one`，1212–1231）**：与 L16 同构——discard_skill 先行、REVISION_FAILURES_LEDGER 回执在后，逐 try 追加是崩溃窗口的唯一记账。判定正确。

**docstring 清单表是否如实**：decision.py 模块 docstring 的逐条落点表（exam 8 + live 9）我逐条对了调用点：12 个函数的生产调用点全部核实（K4 四处、exam 驱动一处、K6 四处、live 驱动两处——`live_injection_overflows` 确在两个 dispatch 循环各调一次、`live_zero_execution_short_circuit` 确在入口守卫）；四条"STAYS"的理由陈述与实际代码逐行吻合；"K4's empty-range fall-through discards with the same rule's reason"（accept_skill 尾部 `decision.RETRY_EXHAUSTED`）也如实。**没有发现任何一条谎报落点**。

---

## 4. 读取器的形状 ——【赞同】（附 1 个 M4 观察点）

**字段对照方案 §3 表，逐字段核对，无偷加**：

- baseline_report：`{"tasks": [{task_id, strong{success,total}, weak{...}}], "F": [...]}` —— 与表"每任务 {task_id, 各 role 成功数/总数}；F 集由 task_select 规则派生"逐字段一致。
- exam_report：`{skill_id, version, status, bare_verdict, leak_verdict, attempt{attempt_id, verdict, judge_reason, weak_answer}, rewrite_exhausted}` —— 表的"terminal"落实为 `status`（store 里的真实状态，stage3_candidates 不过滤 status，故该字段携带真信息而非恒为 pending 的死字段）；"来源 attempt 指针"即 `attempt_id`；"改写结果(exhausted 计数)"即 `rewrite_exhausted`。P7a 的"不含字节等价字段"如约缺席（docstring 还写明了为什么）。
- live_report：`{task_id, members, baseline, result, better, verdict, attribution}` —— 表的"本轮结果"落实为 result（逐 attempt 状态）+ better（规则判定）两个字段，两个都有 M4 消费者。

**"恰好够用"检查**（P5 教训，按任务要求读 M4 相位驱动还需要什么）：

- exam_report 每个字段都能指认 M4 消费者：bare_verdict→`exam_bare_answer_action`、leak_verdict→`exam_leak_action`、attempt（P7b 弱答与来源）→G6 改写取材、rewrite_exhausted→E8 的"下个 A 重答同版"需要知道轮已耗、status→跳过已终态技能。
- live_report 的 better/verdict→组级动作分发；attribution→"已落地计划不得重问"（读取器 docstring 明说）。
- 三函数分立 + 返回 dict 行、不建新对象、不落盘——与 D3"纯函数、只读、不落盘不建新对象"逐点吻合；`test_readers_write_nothing` 用整树字节快照钉住只读性（含 live_report 对不存在轮目录的调用），这是强断言不是摆设。
- 路径零自造：全部复用 K4/K6 的 helper（`_ledger_path`/`round_dir`/`_first_row_from` 等）——读取器与写入者永远不会对"文件住哪"各执一词。同包内跨模块用下划线私有名是灰色地带，但 M2 已定下"显式路径缝"（s-8），promote 成公共 API 只会加面；M4 若相位驱动也要这些 helper，届时应 promote 而非再加深私有互引（观察，非缺陷）。
- `_ANY_ROUND = float("inf")` 复用 `_last_with_skill` 的边界参数取"任意轮最近带技能作答"，避免了复制选择循环——注释解释了为什么恒真（retry_round 是 int）。聪明的复用，接受。

【琐细 B-3，M4 观察点】baseline_report 的**每任务 role 计数行**：表内规定的形状（两次评审存活的 D3），但 M4 消费者目前只有 F 派生——trajectory_select 规则直接吃 `Sequence[Trajectory]`（blocks/rules.py:17–27），不吃计数行；B 相位的 strict 短路走 K6 的 load_baseline 而非本报告。若 M4 验收时 phase_learn 只消费 F，"tasks" 行就是包内声明的死重——读取器不落盘，届时裁剪免费。不阻塞：本步忠实实现了契约表的形状，裁与不裁的裁决属于方案表，不属于 M3 实现。

---

## 5. merge_check 的接线 ——【赞同】

- **参数 vs 复制驱动**：merge_check=none 不是"不整理"（合并、marker 门、预算阀全保留），只是跳过回归考。复制一份 none 驱动要整套复制 K5 的合并波编排（相似度、分组、合并、记账），为省一个调用点造第二套真相——参数分支（curation.py:1212，3 行）是明显正确的选择，且方案 §3 curate 族原文就写"`agentbrew` 加 `merge_check: exam|none`"。
- **枚举校验**：Params 侧（AgentBrewParams.__post_init__，methods/curation/__init__.py:50–53）+ K5 侧（curate_bank:934–936）双处。这不是新造的防御性重复——K5 对 `curation_rounds`/`workers` 本就同风格自校验（组合层可被绕过 Params 直调），跟从局部惯例，非过度设计。
- **exam 路径字节不变的核实**：diff 显示 `try/except _regression_passes` 整块仅**缩进**进 else 分支，逻辑零改动；v3 配置不写 merge_check → AgentBrewParams 缺省 "exam" → 走 else。端到端证据：金矩阵 8 组合（含 agentbrew 整理路径）快照零 diff + 两套崩溃续跑套件原样通过。merge_check 不出现在任何 summary/manifest/账本输出里（grep 全 src 只有 Params、薄壳、K5 三处使用）——参数化分支的声称成立。
- none 分支语义：`passed, reason = True, ""`，产物 pending→accepted，journal 记 kept——与一次通过回归考的行形状相同；单测用**空 answerer/judge 脚本**证明零模型调用（任何调用都会耗尽脚本而炸），这是不依赖实现细节的强测法。

---

## 6. 注册表两新条 ——【赞同】

- **方案预订形状**：§9 M3 行原文"evidence/author 族的 Params 就位（注册表加族，配置接线等 M4）"——死行一步是方案明文，不是实现自作主张。且 re-home 是前置：族条目要指向族包，参数类不搬则族条目只能反向指向槽位包。
- **与 per_skill 的差别要说清**：M2 的三新 delivery 驱动当步就有了生产调用方（`resolve_fixed("delivery","none")`、`resolve_fixed("delivery","group_by_task")`，M2 评审还留了"M4 须核其获得真实调用方否则删"的观察条）；本次 (evidence, segment)/(author, distill_diagnose) 到 M4.3 才有消费者，比 per_skill 更"死"一步。判为可接受的理由：把 params re-home 这个机械、可测（6 条族契约测试 + 金矩阵兜底）的部分挪出 M4.3，直接缩减风险清单里"原子核过大"这个头号风险。沿用 M2 的观察条：**M4.3 验收时必须核这两行经 `resolve_params` 获得真实配置消费者，否则删**。
- **槽位包 re-export 形态是否最简**：segmentation/__init__.py 缩成 docstring + 一行 import + `__all__`，authoring 多留 check_authoring（它读 METHOD_DEFAULTS 做跨槽校验，随槽位死在 M4）。另一形态（v3 槽位条目直指 evidence/author 包 + 当步删槽位包）少两个文件但要在 v3 缝里翻两条 provider 串——这条缝一步后本来就死，选不翻是低风险侧，合理。
- **分层纪律的合法性问题如实说明**：`methods/segmentation → methods.evidence` 是 methods 子包间的直接 import。test_layering 的 methods 互不 import 检查**明文豁免 `__init__.py`**（"the explicit registration/exports and are exempt"，tests/test_layering.py:87–88），re-export 恰好是"显式导出声明"——按纪律的字面与立法意图都合法，且槽位包 M4 即删。但这是纪律的边界用法，M4 删槽位包时必须兑现（方案已预订）。

---

## 7. 无残留无吞错 ——【赞同】

- 被替换的原分支删干净：旧的内联判定（`if bare.judge_verdict:`、`if last_round:`×2、`if all(unit in skip...)`、注入超限比较×2、`_finalize_group` 的内联裁决循环）在 diff 中全部移除，grep 全 src 无第二份拷贝（live_run.py:244 的 `unit in stored or unit in skipped` 是完备性检查，另一回事）。
- 无防御性 try/except：diff 中唯一的 try/except 变化是 curation.py 里原有块**缩进**进 else 分支；无新增 except、无 `pass`、无吞错。
- 异常四分类与续跑协议零触碰：reliability.py 不在 diff；K4/K6 改动的每一处都保持"置状态→save→return"的写入次序不变（逐处比对过 diff）；`_finalize_group` 里旧的局部变量 `decision` 改名 `action`，恰好避免了与模块名 `decision` 的遮蔽冲突（新代码要写 `decision.KEEP`，若沿用旧局部名会炸——改名是必须的且做对了）。
- 续跑证据：两套崩溃续跑套件零行改动通过 + 金矩阵 2 个 crash+resume 组合通过 + `discard is the truth, ledger row is the receipt` 的次序在 exam.py 的崩溃补账注释里原样保留。

---

## 8. 测试质量 ——【赞同】

+31 条的构成与提交说明一致（族契约 6、决策 13、读取器 9、merge_check 3 = 2008→2039，我复跑验证）。重点核了任务点名的两个疑虑：

- **test_decision.py 是否同义反复（实现输出==实现输出）**：不是。期望值里的 discard_reason 全部用**字面量**（"weak_already_knows"、"leak_audit"、"retry_exhausted"、"model_output_error"、"live_undetermined"、"live_no_improvement"），不是复读实现常量——词表的字符串值被真正钉住。我对照方案 §3 与旧 K4/K6 代码手工推导核了三块：
  - `exam_answer_action` 四行真值表：答对→keep（不论轮次）、答错+有后续→revise、答错+末轮→delete(retry_exhausted)——与 §3 逐条吻合；
  - `live_final_action` 七个用例：零执行行优先（任意轮的 verdict 行压过已判行）、最晚有行轮裁决、早轮 better 也 keep、早轮 miss 不裁决（None）——与旧 `_finalize_group` 内联逻辑逐行等价（我读了旧代码的 diff 删除侧）；
  - `live_attribution_exhausted` 的边界：`len(failures) >= budget`（>= 不是 >）、exhausted 回执行、crash 吞回执场景——与旧内联谓词 `any(row.get("exhausted")) or len(failures) >= _ATTRIBUTION_ATTEMPTS` 完全一致。log.md 诚实披露了开发中一处预算边界笔误在测试侧修正。
- **读取器测试是否空洞**：fixture 用 K4/K6/stage1 的真实落盘形状（judge_round 是对种子 store 的纯重算，零模型调用）；`test_readers_write_nothing` 是整树字节快照；merge_check:none 用空脚本反证零调用。族测试用 `is` 钉"一个类不是复制品"。没有发现空洞断言。
- 读取器测试的九条覆盖了三个读取器的正例、边界（无考题的候选、零执行行、缺省补全 retry_limit）与只读不变量，但没有覆盖 live_report 的 members 回退分支以外的"audit 行存在但无 verdict 行"中间态——该分支代码被 zero_execution 用例间接触达，可接受。

---

## 9. 发现汇总

| # | 级别 | 内容 |
|---|---|---|
| B-1 | 【琐细】 | `exam_measurement_exhausted_action()` 零参常量函数，调用点 `.reason` 不如已 import 的 `MODEL_OUTPUT_ERROR` 直用；为"清单逐条具名"付出的最小仪式，M4 调用点迁移时顺手简化 |
| B-2 | 【琐细】 | acceptance.py:338 `exam_stored_pass_action(accepted is not None) is not None` 双重 None 包装，可读性差于原分支；M4 相位驱动接管后自然消失 |
| B-3 | 【琐细/观察点】 | baseline_report 每任务 role 计数行是方案表形状但 M4 消费者目前只有 F 派生（trajectory_select 直接吃 Trajectory 序列）；M4 验收核 phase_learn 是否真读 "tasks" 行，不读则届时免费裁剪 |
| — | 【赞同】 | Action 三 kind 两字段无膨胀；词表三处定义收敛为一处、`is` 钉死、环解开；四条留原处逐条属实；读取器逐字段对表、P7a/P7b 如约、只读有快照测试钉住；merge_check 参数化正确、exam 路径字节不变有金矩阵证据；注册表两新行为方案预订、re-export 形态最简且分层合法（__init__ 豁免 + M4 删槽位已预订）；原分支删净、零新增 try/except、续跑协议零触碰；31 条测试无空洞、决策测试用字面量钉词表、关键真值表手工推导核对一致 |

无【偏移约束】。

## 10. 结论

**M3 验收通过。** 0 条偏移约束、3 条琐细（B-1/B-2 为调用点形态，B-3 转为 M4 验收观察点）。本步的简洁性成色：12 个规则函数全部有当下生产调用方（无一投机签名），四条不抽的都有可指认的熔合约束并在 docstring 如实说明，两个新模块无任何 M4 需要拆掉的东西——re-export 与槽位 shim 均为一步寿命的预告形状，死期已写进方案。给 M4 的两个核验收口（本评审新增的观察，非本步义务）：①(evidence, segment)/(author, distill_diagnose) 两行须在 M4.3 获得真实配置消费者，否则删；②baseline_report 的 "tasks" 行若无消费者则裁剪。
