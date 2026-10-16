日期：2026-10-16
一句话说明：Wave 2 三支合并结果（`16c3791..1df37d4`，9 个提交）的简洁性评审 B——文件拆分线、过渡物清理、死代码、初级可读性、测试可读性。
对应 log.md 条目：2026-10-16 22:10「Wave 2 收口：三支合并后的集成咬合修复与 K4/K6 旧壳清理」。

---

## 0. 评审范围与独立复验

- 对象：`feature/multi-method` 上 `16c3791..1df37d4` 的合并结果，共 96 个文件、
  +8627/−15133。逐行读了新增的 `src/expedistill/stages/phase_execute.py`
  （HEAD 实测 1660 行；W2-A 自报口径约 1706 行，差异应该是统计时点不同）、
  `src/expedistill/stages/phase_learn.py`（368 行）、
  `src/expedistill/stages/learn_tasks.py`（581 行）、
  `src/expedistill/core/config.py` 的 v4 解析段、`src/expedistill/cli.py` 的
  相位循环；抽读了收口提交 1df37d4 触及的 `composites/decision.py`、
  `composites/live_measurement.py`、`composites/live_verdicts.py`、
  `composites/verdicts.py`、`composites/skill_status.py`、`methods/author/`、
  `methods/evidence/`。
- 纯静态评审：没有运行 pytest（按任务指令）；只跑了 `ruff check`（静态
  lint，E9/F 全过）和若干 grep / AST 扫描。死代码结论来自一次全仓符号扫描
  （对 `src/expedistill` 里每个公开 def/class，统计其在自身文件之外的生产引用）
  加逐个 grep 复核，扫描方法见第 6 条发现的证据。
- 用 `git log` 核对了收口提交的自述（"each grepped for production callers
  first"）与实际代码，没有只转述提交说明。

先说总体判断。这轮合并**在"不造通用框架"上守住了**：计划硬约束里的依赖图、
调度引擎、插件自动发现、决策规则注册表逐项核对都不存在——`cli.py` 的相位
循环只有 17 行（443–459 行），注册表是纯数据表，`phase_execute.run` 的角色
分发是平铺的 if/elif。W2-B 把 `phase_learn` 拆到 368 行（拆出 `learn_tasks`），
说明 400 行拆分线是被当作真实约束执行的。但是 **W2-A 的 `phase_execute.py`
以 1660 行、超过拆分线 4 倍的状态合入了**，这是本轮唯一的硬约束违反，也是
后续每个波次（Wave 3 审计脚本、Wave 4 金标重生成）都要在其上继续垒代码的
地基，应该在 Wave 3 开工前先拆。过渡物方面：适配器换点本身清干净了
（`_PhaseMethods` / `_RealParams` 已删、全仓 grep 无残留），但**"等另一支
落地再换回"式的文本和兜底留下了约 15 处，且全部已经过时**——它们等待的
W2-C v4 翻转就在同一个合并范围内落地了，这些注释描述的"将来"已经变成了
"过去"，对后来者是纯误导。死代码清理总体认真，独立扫描只发现 3 处漏网。

发现汇总：**1 条阻断、5 条偏移、8 条细节**。逐条如下，每条给严重程度、
证据（文件:行号）、直接方案。

---

## 1. 阻断：`phase_execute.py` 一个文件装四种角色，1660 行，违反 400 行拆分线 4 倍

**严重程度：阻断（计划硬约束级的结构问题；行为本身有测试钉住，不影响已跑
通的语义，但在 Wave 3 任何代码落进来之前必须先拆）**

证据：`src/expedistill/stages/phase_execute.py` 全文 1660 行。修复方案
（`docs/decisions/execute_learn_repair_plan_20261016.md` 第 614 行）写得明确：
"超过 400 行的驱动先拆分再写"。W2-A 自报超线后仍然单文件合入。文件内实际
装了七个互不相干的段落（行号是 HEAD 实测）：

| 段落 | 行 | 内容 |
|---|---|---|
| 模块 docstring | 1–125 | 125 行总述 |
| 分发入口 + 磁盘推导 | 223–396 | `run`、相位目录、早期相位、库加载、规则 1 复用 |
| 方法解析 | 398–462 | delivery / execution / 判分规则 / 比较规则 / 重派发预算 |
| 真实执行引擎 | 464–723 | `_Plan`、派发、判分、重派发循环、已接受空洞 |
| 基线相位（D） | 725–827 | |
| 考试相位（A） | 829–1110 | 含考卷生成、考试报告两段 |
| live 相位（B） | 1112–1472 | 含轮账本读取、审计行冻结 |
| 终测相位（C） | 1474–1660 | |

"该不该拆"的判断：**该拆，且拆法是现成的**。四个角色段落之间已经用横幅
注释分好界（`# The baseline phase (D)` 等），每个 `_run_*` 都有完整的
docstring；共享段落（磁盘推导、方法解析、引擎）被哪些角色使用也一清二楚
（基线、终测用引擎的重派发循环；live、考试走各自的协议）。拆分是机械的
搬移加 import 调整，语义零改动。

**具体拆分方案**（目录 `src/expedistill/stages/phase_execute/`，变成包；
`__init__` 仍暴露 `run`，`cli._phase_driver` 的
`importlib.import_module("expedistill.stages.phase_execute")` 与测试的
`from expedistill.stages import phase_execute` 都不需要改一个字）：

- **`__init__.py`（约 120 行）**——分发入口 `run(ctx, phase) -> PhaseResult`。
  保留现 125 行模块 docstring 里的三样东西：四角色一览、相位内固定顺序
  （对照先行、跳过判定、后跑带 skill 单元）、相位目录布局图。把"2026-10-16
  coordinator ruling"这类裁决出处引用移进各角色文件（见第 9 条细节）。
- **`phase_disk.py`（约 180 行）**——只回答"磁盘上有什么"：`_phase_dir` /
  `_arm_dir` / `_earlier_phases` / `_bank_skills`（前一学习相位的库工件，
  否则 run 级冻结库）/ `_write_units` / `_write_report` / `_reused_rows` /
  `_rebased_row`（规则 1 的复用拼装）。这一段的共同点是全部从磁盘推导、
  全部被多于一个角色使用。
- **`resolve.py`（约 100 行）**——把配置映射翻译成驱动与参数：
  `_resolve_delivery` / `_execution_params` / `_resolve_verdict_rule` /
  `_compare_rule` / `_redispatch_limit`。
- **`engine.py`（约 260 行）**——真实执行引擎：`_Plan` / `_plan_units` /
  `_task_objects` / `_prefixed_task_map` / `_skills_for_task` / `_dispatch_arm` /
  `_unit_runs` / `_unit_row_for` / `_run_arm_units`（重派发循环）/
  `_accept_new_holes` / `_arm_trajectories`。
- **`baseline.py`（约 130 行）**——基线相位：强弱两条裸臂跑 train 集、
  轨迹进 run 级存储、报告走冻结的 baseline 读取器。
- **`exam.py`（约 300 行）**——考试相位：考卷执行期生成（`_exam_or_generate`）、
  裸答→泄题审计→带 skill 作答的单元函数、考试报告（`_exam_report`）。
- **`live.py`（约 360 行）**——live 相位：轮号推导、入口守卫（对照完美、
  注入超长的零执行终态）、K1 批派发、`judge_round` / `finalize_group` 提交、
  审计行冻结（`_freeze_audit_row`）。
- **`final_test.py`（约 200 行）**——终测相位：对照臂（复用基线或新鲜裸跑）、
  带库臂、delta。不叫 `test.py`，避免和 pytest 的收集习惯打架。

全部落在 400 行以内。落地的配套动作（按 AGENTS.md 十一.5 的顺序）：先在
AGENTS.md 第四节目录树里登记新文件，再搬代码，log.md 记一条。`test_w2a_
phase_execute.py` 的 840 行测试不需要动（它们 import 的是模块名）。

顺带回答评审重点里的两个子问题：分发入口 `run` 的 if/elif 结构**清晰**
（`phase_execute.py:246-255`，未知角色响亮失败，`__all__ = ["run"]` 把出口
收成一条）；"每段有没有独立 docstring"——函数级有且质量好，模块级没有
（因为现在只有一个模块），这正是拆包能白拿的收益。

---

## 2. 偏移：`learn_tasks.py` 581 行，超线 40%；判断是"轻拆一处即达标"

**严重程度：偏移**

证据：`src/expedistill/stages/learn_tasks.py` 581 行。拆分线的字面只约束
"驱动"（`phase_learn.py` 368 行合规），任务清单执行器属于灰色地带；但按
拆分线的本意（一个文件读完不用滚动三屏）它超了。文件里五个职责的行数：
模块 docstring 73 行、D4 多版本合并约 45 行、逐行执行器与四类动作落地约
290 行、live 组的证据输入约 30 行、三本账本（task_list / revisions /
outcomes 的写与续跑重放）约 130 行。W2-B 自报"文档占比高"属实：docstring
与注释约占四成，纯逻辑约 350 行，**当前可读性尚可，不构成阻断**。

直接方案（二选一，推荐前者）：

1. 把三本账本整段搬到 `stages/learn_ledgers.py`（约 140 行，含 `_write_
   task_list` / `_append_outcome` / `_rows_of` / `_revision_rows` /
   `_outcome_skills` 和三个 schema 常量；`phase_learn._phase_outcomes` 读
   `OUTCOMES_NAME` 的地方一并指过去）。剩下的 `learn_tasks.py` 约 440 行，
   再把 30 行 live 证据输入（`_task_instruction` / `_n_attempts`）并进，
   仍略超线但在一个滚动屏内，可以在 log.md 里记一句豁免理由。
2. 或者按动作拆：keep / delete / add 留在 `learn_tasks.py`，revise 的
   200 行（`_land_revise` / `_land_revision` / `_always_revise`）搬
   `learn_revise.py`。这个缝更宽，代价是 revise 的续跑账本要跨文件引用，
   不如方案 1 干净。

---

## 3. 偏移：stages 层的换点残留——为"等 W2-C 落地"写的兜底，等的东西已经落地了

**严重程度：偏移（每一处都会把初级读者引向一个已经不存在的世界）**

证据（4 处，全部过时）：

1. `stages/phase_execute.py:457-461`，`_redispatch_limit`：
   `getattr(ctx.config, "invalid_redispatch_limit", DEFAULT_...)`，docstring
   写"the frozen default **until the v4 config object carries the field**"。
   `ExperimentConfig` 在同一合并范围内的 b3985a0 已经有这个字段
   （`core/config.py:829`，`__post_init__` 里还有正整数校验）。这个 getattr
   兜底永远走不到，注释在描述一个已完成的未来。
2. `stages/phase_learn.py:156-166`，`_sequence`：用 `getattr(config,
   "sequence", None)` 探测 v4 产品，报错文案写"These fields land with
   W2-C's v4 flip (core/config.py, R9) -- **until then** the phase drivers
   cannot run against a real ExperimentConfig"。v4 翻转已落地：
   `sequence` / `phases` 是 dataclass 字段，真实配置永远有这两个属性
   （空序列会在 `parse_phases` 里被 ConfigError 拒掉），getattr 分支不可达。
3. `stages/phase_learn.py:26-30`，模块 docstring 第 2 条末尾："at the
   frozen commit those entries still resolve through the v3 slots, so the
   mapping-first form is the known coordination point with W2-C's second
   commit"——这是 W2-B 冻结时写给集成者看的协调备忘，合并完成后失去了
   全部读者。
4. `src/expedistill/cli.py:286-299`，`_phase_driver` docstring："the two
   phase drivers are **Wave 2's other two agents'** files"——多智能体分工
   是这轮开发的内部叙事，对仓库的读者毫无信息量；这个函数真正要解释的是
   惰性 import 与 sys.modules 测试缝（后半段写得很好，应该保留）。

直接方案：

1. `_redispatch_limit` 改成 `return int(ctx.config.invalid_redispatch_limit)`，
   docstring 删掉"until"从句；
2. `_sequence` 删掉两个 getattr，直接读 `config.sequence` / `config.phases`，
   报错文案改成陈述事实（"the v4 config products are missing: a directly
   constructed config bypassed parse_phases"）；
3. phase_learn 模块 docstring 删协调备忘两句；
4. `_phase_driver` docstring 删掉"agents"一句，保留惰性 import 与测试缝的
   解释。

同类还有 `stages/__init__.py:7-9` 的注释（"W2-A/W2-B had added these
imports, W2-C's loop tests went red"）——它解释的"为什么必须惰性 import"
是真实且重要的约束，但用三个波次代号当主语。建议改成陈述约束本身：
"an eager import binds the real modules as package attributes and routes
around the tests' fake-driver seam"，把波次历史留在 log.md。

---

## 4. 偏移：composites / methods 层的"M4 之后删"承诺全部落空，文本一个没改

**严重程度：偏移**

证据：`docs/decisions/execute_learn_repair_plan_20261016.md` 第 62 行把
"M4.3 以及它之后的所有工作"划进了本轮范围。但以下"到时候就删"的标注在
合并完成后全部原样保留，而它们等的"到时候"就是现在：

- `composites/live_verdicts.py:32`（"the shells are deleted in M4"）、
  `:305`、`:412`、`:472`（三个薄壳函数 docstring 末尾"deleted in M4"）；
- `composites/live_measurement.py:64`、`:85`（"the path derivation dies
  with the shells in M4"）、`:396`、`:426`；
- `composites/verdicts.py:38-39` 与 `composites/live_verdicts.py:37-38`
  （"keeps K6's frozen verdicts-ledger protocol **for the stages/ callers
  until Wave 2's phase drivers replace it**"——stages 侧调用方已删光，
  现在的调用方正是 Wave 2 的 `phase_execute`）；
- `methods/author/__init__.py:11-13`（"The stage drivers do not call it
  yet: the legacy paths keep their own G6 / G11 wiring **until Wave 2
  rewires phase_learn**"——`learn_tasks` 已经在调 `author.write_new` /
  `revise_group`，这句话现在是反的）。

这里有一个值得点名的**计划偏离**：去轮化接缝（`_` 前缀核心 + 薄壳）的设计
意图是相位驱动直接调核心、传显式路径，薄壳随 M4 删除。实际合并结果里
`phase_execute` 调的是薄壳（`judge_round` / `finalize_group` / `audit_row`，
`phase_execute.py:1261-1361`），薄壳成了相位驱动的正式 API。这个选择本身
说得通（少改一行冻结协议），但代码文本必须改写，否则读者会以为这些壳
马上要死、不敢调用。直接方案：把"deleted in M4"全部改成一句事实——
"the shell is the phase driver's API (stages/phase_execute); the
de-rounding seam stays so tests can drive the core directly"；或者真的
按原计划删壳、让驱动传路径。前者小、后者大，推荐前者。

---

## 5. 偏移：跨模块 import 私有名 9 处——`_` 前缀在这里已经是谎报

**严重程度：偏移**

证据：`_` 前缀在 Python 里的含义是"本模块私有"。合并后这些名字有稳定的
跨模块生产调用方，前缀不再表达任何事实：

- `stages/phase_execute.py:148-151`：从 `acceptance_skill_status` import
  `_exam_attempts` / `_find_bare`；
- `stages/phase_execute.py:198-201`：从 `reports` import
  `_run_record_from_attempt` / `_run_record_from_trajectory`（docstring
  自己承认"Private-name seams"）；
- `stages/phase_execute.py:1133`（`lm._ROUNDS`）、`:1431-1440`（`lm.
  _first_row_from` × 2、`lm._ledger_path` × 2）、`:1378`
  （`decision._parse_unit_id`）；
- `methods/evidence/exam_sheet.py:22`：又一个 `_exam_attempts`。

phase_execute 的 docstring 把这辩护为"与 reports 的既有纪律一致"——既有
纪律本身就该修。直接方案：在归属模块里去掉下划线转正（`exam_attempts` /
`find_bare` / `run_record_from_attempt` / `run_record_from_trajectory` /
`ROUNDS` / `first_row_from` / `ledger_path` / `parse_unit_id`），旧名不留
别名，9 个 import 点一次提交改完。对初级程序员，"驱动伸手进别的模块拿
下划线名字"是排第一的可疑信号。

---

## 6. 偏移：`run()` 里的判分规则预检用错 benchmark、返回值丢弃、各角色重复推导

**严重程度：偏移**

证据：`stages/phase_execute.py:245` 在角色分发前调了
`_resolve_verdict_rule(spec, ctx.config.benchmark)`，返回值**没有接收**
（基线相位在 `:743` 重新推导一遍）。对考试相位这次预检还会验错对象：预检
用真实 benchmark（比如 `tb` → `tb_scorer`），而 `_run_exam:862` 实际用的是
`verdicts.default_verdict_rule("exam")` → `exam_judge`，并要求配置里显式
写的规则必须是 `exam_judge`。也就是说一个考试相位若写了
`acceptance: tb_scorer`，入口预检放行、跑到一半才被拒——"an unknown name
fails before any work"的注释目标对 A 角色落空了。

直接方案：删掉 `:245` 这一行预检，把"未知规则名在任何执行前响亮失败"的
责任交给每个角色自己（基线 `:743`、考试 `:862` 已经各自在做；live 用的是
比较规则 `_compare_rule`，终测不判分）。这样每个角色的规则只有一个推导点。
若想保留入口级预检，就必须让 `run` 接收返回值传下去，并让 `_resolve_
verdict_rule` 对考试角色用 `"exam"`——改动更大，不建议。

---

## 7. 细节：三处死代码（收口提交漏网）+ 一处重复写盘

**严重程度：细节**

独立扫描方法：对 `src/expedistill` 每个公开 def/class 统计自身文件之外的
引用（src + tests + scripts 全量 grep 复核）。收口提交自述逐个 grep 过
调用方，做得认真，漏了这三处：

1. `composites/skill_status.py:51` `tally_verdicts`：**零生产调用方**，
   测试也不引用（全仓 grep 只有定义处与 `__all__`）。它的消费者是已删的
   stage4 / acceptance_submit 摘要。模块 docstring（`:5`，"the cumulative
   verdict accounting both summaries carry"）同步过时。方案：删除函数、
   修 docstring。
2. `composites/live_verdicts.py:395` `no_op_guard` 公开薄壳：生产调用方
   已随旧 live 驱动删除，现在只被 `tests/composites/test_live_acceptance.py`
   引用（no-op 判定在 `judge_round` 内部走 `_no_op_guard` 核心）。方案：
   要么让测试改走 `judge_round` 的行为面、删壳；要么在 docstring 标明
   "test-only surface"。
3. `methods/author/__init__.py:31` `AuthoringParams.exams` 字段：v4 配置
   在 `core/config.py:463-471` 明确拒绝 author 里的 `exams` 键（"a v3
   authoring switch with no v4 image"），全仓没有任何生产代码读这个字段。
   docstring 给出的保留理由（"the stage-3 call point that reads it...goes
   away with the Wave 2 call-point move"）所描述的调用点已经删掉了，理由
   失效。方案：删字段（`registry.py:96` 的解析与 `tests/unit/methods/
   test_family_entries.py:76` 的钉子同步改）。
4. `stages/phase_execute.py:958` 与 `:964`：`_run_exam` 把同一个 summary
   `write_json` 写了两遍（`:958` 已经写过，`:959-963` 没抛错的话 `:964`
   原样再写一次）。方案：删 `:964`。

## 8. 细节：`_run_baseline` 的 `infra_shortfalls` 是一个 30 行内联表达式，弱臂指标算了两遍

**严重程度：细节**

证据：`stages/phase_execute.py:800-812`：summary 的 `infra_shortfalls`
键里塞了一整个 `sorted(... for ... in metrics_block.compute_task_metrics(
[traj for ...], expected=..., aux_scorer=...).items() if ...)`，长到需要
`# noqa: E501`，且它重算的正是 `:791-798` 刚为 `arm_summaries["weak"]`
算过的同一份指标。方案：在上面循环里保留 `weak_metrics` 变量，
`infra_shortfalls` 写成
`sorted(task_id for task_id, c in weak_metrics.items() if c["valid"] < ctx.config.n_attempts_weak)`，
`noqa` 随之消失。

## 9. 细节：模块 docstring 的裁决编号密度——内容是好的，出处引用该减负

**严重程度：细节（初级可读性）**

评审重点问"没读过设计文档的人能不能靠 docstring 明白相位是什么、流程是
什么"。结论：**能**。`phase_execute.py` 的 125 行 docstring 把四角色各自
做什么、单元的固定顺序、复用规则、重派发预算、相位目录布局都讲到了；
`phase_learn.py` 与 `learn_tasks.py` 同样合格——一个新读者按
cli → phase_execute/phase_learn → learn_tasks 的顺序读下来，不需要任何
外部文档就能拼出全流程。真正扎眼的是出处引用的密度：R4 / R8 / R9 / P2 /
C1 / C8 / D8 / E6 / G3 / A7 / A10、"user ruling 8"、"the 2026-10-16
coordinator ruling"、"the W1-A merge note"、"boundary doc section 4.3
rule 3"——单是 phase_execute 一个 docstring 就有二十多个。它们对查阅
设计史的人有用，对第一次读代码的人是噪音。方案：把每个编号保留为括号
里的出处（如"the rounds are pinned to two — no third round exists
(user ruling 8)"），裁决内容用一句话写全，让 docstring 不查文档也能自洽。
拆包时顺手做。

## 10. 细节：三个字面量与两处小重复

**严重程度：细节**

1. `strict_rate_gain` 这个默认比较规则名在三个地方各写了一遍字面量：
   `phase_execute.py:454`（`_compare_rule` 的兜底）、`phase_learn.py:86`
   （`_DEFAULT_COMPARE_RULE`，注释还写着"named here so the config
   fallback is explicit"）、`composites/decision.py:660`
   （`decide_task_list` 的参数默认值）。方案：decision 暴露
   `DEFAULT_COMPARE_RULE` 常量，另两处引用它。
2. `phase_execute.py:1300-1305`：live 相位内联定义的 `skills_for_task`
   与引擎里现成的 `_skills_for_task`（`:539`）做同一件事，直接调
   `_skills_for_task(owing)` 即可。
3. `methods/evidence/__init__.py:44,47`：`SegmentationParams` 的报错文本
   写"methods.segmentation.K must be >= 1"——这是 v3 槽位名，v4 用户配置
   里对应键叫 `phases.<L>.evidence.K`。报错指路一个不存在的配置键。
   方案：改成不带槽位路径的措辞（"evidence method 'segment': K must
   be >= 1"）。同文件 `:37` docstring 里的"K2's re-segmentation budget"
   的 K2 也已随 stage2 退役，顺手改成"the re-segmentation budget"。
4. `core/config.py:615`：`_check_attempt_depths` 里用 `assert isinstance(
   target, ExecutePhaseSpec)`。`_check_compare_targets` 已保证该不变量，
   但全仓同类防御都用 `CodeBugError`，且 assert 在 `python -O` 下消失。
   方案：换成 CodeBugError，或者删掉（上游已验）。

## 11. 细节：测试命名总体行为化，个别名字带着计划编号

**严重程度：细节（测试可读性）**

逐个读了 `test_w2a_phase_execute.py`（21 个测试）、`test_w2b_phase_
learn.py`（19 个）、`test_w2c_config_v4.py`、`test_w2c_run_loop.py`
（19 个）的全部测试名。判断：**行为化质量好**，绝大多数名字读出来就是
断言的行为，例如 `test_baseline_leaves_the_bank_unchanged_r4`、
`test_live_control_perfect_skips_the_group`、`test_a_crashed_revision_
is_replayed_from_the_ledger`、`test_until_phase_stops_after_the_named_
entry`、`test_a_driver_answering_for_another_phase_is_a_loud_error`。
两个小瑕疵：

1. 少数名字以计划编号为主语，初级读者解不开：
   `test_r4_call_face`（w2a:365，内容其实是"驱动源码里不得出现
   discard_skill / accept_skill 调用"，名字可以叫
   `test_the_driver_source_contains_no_bank_mutation_calls`）、
   `test_the_d4_merge_decides_for_the_current_version`（"D4 merge"改成
   "multi-version merge"就和 docstring 一致了）。
2. 两个测试文件头 docstring 以波次代号开头（"W2-A: the execute-phase
   driver..."）。文件名已带 w2a/w2b/w2c，docstring 第一句更该描述被测
   对象。`test_r4_call_face` 这类源码扫描测试的写法本身（读源文件断言
   字符串不存在）值得保留，是 R4 不变量的廉价钉子。

---

## 12. 核对通过清单

以下各项逐项核对过，均通过，列出以便后续评审不必重查：

1. **计划硬约束（不引入依赖图 / 调度引擎 / 插件自动发现 / 决策规则
   注册表）**：逐项核对 `cli.py`、`phase_execute.py`、`phase_learn.py`、
   `core/config.py`，全部不存在；相位循环是 17 行的 for（`cli.py:443-459`），
   角色分发是平铺 if/elif（`phase_execute.py:246-255`），未知角色响亮失败。
2. **适配器换点删干净**：`_PhaseMethods` / `_RealParams` 全仓 grep 无残留
   （含测试与 docs）；phase_execute 的 delivery / execution 全走五族注册表
   （`method_registry.resolve` / `resolve_params`）。
3. **已删模块无生产引用**：`live_attribution`、`acceptance_submit`、
   `methods/acceptance`、`methods/segmentation`、`methods/authoring`、
   stage1–6 各文件，`src/` 下无 import 残留；`scripts/audit/audit_live_run.py`
   里的 `methods.acceptance` 引用属于读历史 run 的 manifest，正当。
   `LIVE_DIR_NAME = "stage4_acceptance/live_run"`（`live_measurement.py:66`）
   是冻结的磁盘路径，保留正确（注释里"stage-4 directory"的说法随第 4 条
   偏移一并改写即可）。
4. **决策模块收窄**：`decision.py` 只保留四个有存活调用方的 live 规则 +
   通用入口；`_ALL_SUCCESS_SAFE_RULES` 别名已删（grep 确认）。
5. **`phase_learn.py` 368 行合规**，且驱动职责边界讲清楚了（R4 的镜像：
   学习相位不执行任务，docstring 明说"a learn phase never runs a task"）。
6. **`cli.py` 相位循环**：`--until-phase` 的语义、退出码分类、账本行的
   `stage` 键兼容都写得清楚；`_phase_driver` 的 sys.modules 测试缝解释
   完整（除了第 3 条偏移里的"agents"一句）。
7. **`core/config.py` 的 v4 加载期检查清单组织**：`parse_phases`（5 条，
   每条一个具名函数：`_check_compare_targets` / `_check_attempt_depths` /
   `_check_merge_check_needs_exams`）+ 两个 spec 的 `__post_init__`，函数
   都在百行以内、检查逐条报错点名违规键，可读性合格。`_parse_execute_phase`
   98 行是本轮最长的一个函数，里面七个检查线性排列，拆分收益有限，可不动。
8. **AGENTS.md 目录树已同步**（phase_execute / phase_learn / learn_tasks
   均已登记）；README 仍是 v3 阶段世界的表述，按修复方案 W3-C 的分工
   （第 574 行）这属于 Wave 3 的文档波次，不算本轮遗漏——但要注意在
   W3-C 落地前，README 与代码不一致，新人入口以 AGENTS.md 第四节与
   各模块 docstring 为准。
9. **ruff（E9/F）全过**，五个目标文件无未用 import、无语法问题。

---

## 13. 建议的处理顺序

1. 先做第 1 条的拆包（机械搬移，测试不动），第 9 条的 docstring 减负随拆包
   顺手完成；
2. 第 3、4、5 条（换点文本清理 + 私有名转正 + M4 承诺改写）合成一个
   "Wave 2 收尾"提交，全部是文本与命名改动，语义零变化；
3. 第 6 条（判分规则预检）与第 7 条（死代码 + 重复写盘）一个提交；
4. 第 2、8、10、11 条排进下一次触及对应文件的工作里顺手做；
5. 全部做完在 log.md 各记一条，测试与分层检查保持全绿。
