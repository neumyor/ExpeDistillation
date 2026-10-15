日期：2026-10-15
一句话说明：执行—学习闭环重构方案（scope 级）的第一轮评审 C——迁移风险与可复现性。
对应 log.md 条目：2026-10-15 执行—学习闭环重构方案（scope 级）。

---

## 0. 总评

**结论先行**：方向可行，§7"续跑契约不变"对 run 本身的承诺成立（逐单元落盘、按确定
ID 跳过、manifest 逐字段比对、runlock 这四样确实都不需要动）。但方案的迁移面
（§6–§8）按现在的写法**不可验收**：本评审提出 9 条【偏移约束】，其中三条不补则
M1 不能开工——

1. **golden 矩阵的处置完全缺位**（C1）。"golden 矩阵"是用假模型把 8 种配置组合各跑
   一遍全流程、把 run 目录里每个产物的字节拍成标准照的回归测试。方案 M1 只写"测试
   全绿"，但 M1 给 Skill/Exam 加字段会改变**每一个落盘对象的字节**，现行
   `tests/golden/test_golden_matrix.py:46-52` 的逐字节断言当场全红——M1 的验收标准
   本身就是不可满足的。
2. **"exam 预期行为不变"没有可执行的等价准则**（C2、C3）。技能 ID 换名后，golden
   里连"模型调用多重集"都不可直接比较（curation prompt 的正文里就嵌着 skill id，
   `prompts/curate.py:20/90`）；方案引用的三个语义要素需要先做 ID 归一化和布局映射
   才能落地。另外 §3.1"只测变化的部分……与现有行为一致"与 K4 现状**直接矛盾**。
3. **assemble_reuse 只字未提**（C8）。它是"新代码读旧 run 目录"的唯一常驻消费者
   （复用实验用它从旧 run 装配新 run），其白名单、负清单、身份比对、溯源函数全部绑
   死在 stage 目录布局与现行 ID 规则上。迁移方案不交代它，等于把它留到实施时随机
   处理。

两条术语解释（通俗版）：
- **golden 矩阵**：如上，作用是"这次重构有没有改坏行为"的裁判；
- **续跑契约**：跑到一半崩了的实验，重新用同一条命令启动能接着跑，且最终结果和
  一口气跑完相同。

评审方法：通读方案（`docs/decisions/execute_learn_loop_20261015.md`，下称"方案"）与
上一轮决定（`docs/decisions/multi_method_framework_20261015.md`，下称"上轮方案"），
逐文件核对 src/expedistill 的相关实现与 tests/golden 采集器，只读仓库，未触碰
TB-dev 与任何 runs/。发现编号 C1–C9，正文按任务六个重点组织，分级汇总见 §7。

---

## 1. 重点 1：schema 升级清单的完整性 ——【偏移约束 C6】【偏移约束 C8】

**结论**：方案 §5.6 只写了"这是数据对象变更，升 schema"一句话；§8 M4 只写了"配置新
schema 与转换脚本"。实际要动的清单远比这两句长，且有两处上一轮的先例必须对齐。

### 1.1 配置侧（缺项见 C6）

现行 `core/config.py:131` `CONFIG_SCHEMA_VERSION = 3`，五槽位由 `METHOD_SLOTS` /
`METHOD_DEFAULTS`（`config.py:78-92`）承载。v3→v4 应完整复刻上一轮 v2→v3 的先例
（上轮方案 §7 P1b 行 + `upgrade_v2_config` docstring）：

- **`upgrade_v3_config` 纯函数**（`core/config.py:539-596` 是样板）：两个消费者——一次性
  转换脚本 + `assemble_reuse` 读 v3 源 run 的 manifest 做身份比对（现在
  `assemble_reuse.py:1031-1040` 对 v2 硬编码 `== 2`，v4 代码要同时认识 v2/v3 源）；
- **转换器写新文件名**（`scripts/run/convert_v2_configs.py` 是样板）：7 份
  `configs/experiments/*_v3.yaml`（alfworld_skillopt_split、alfworld_skillopt_split_v2、
  searchqa_skillopt_split、tb21_split_live_run、tb21_full_v2、tb21_split_v2、
  tb21_split_reuse 各自的 _v3）是冻结的实验记录，**不修改、原地拒载**，转换产出
  `_v4.yaml`；已有 `*_v2_v3.yaml` 双后缀先例，命名规则在方案里定一句即可；
- **两处拒载提示要泛化**：`load_config` 对 version < 当前版的报错文案
  （`config.py:629-643`）与 stage0 对旧冻结 run 的拒载检查——现在硬编码
  `frozen_version == 2`（`stage0_freeze.py:297`）——都要改成"version < 当前版即拒，
  指向维护分支规则（AGENTS 六.1）与转换器"。v3 冻结的 run 若被 v4 代码续跑，必须得到
  和 v2 今天一样的明确拒绝，而不是掉进 B4 的泛型 manifest diff 报错里；
- **转换的语义映射本身要预注册**：exam 的 `retry_limit: 2` 展开为规范序列
  `D L A L A L A L C`（转换器生成，不许手写漂移）；live 配置转换时**两处身份变化要逐
  文件打印**（`authoring.exams` 消失 = 变化①；curation 换 `merge_check: none` = 变化
  ②）——这是 `upgrade_v2_config` 对 live retry_limit 的"披露式丢弃"先例
  （`config.py:576-578`）的要求。

### 1.2 数据对象侧（方案没列的项）

- **Skill**：+来源字段，`SKILL_SCHEMA_VERSION` 升（`core/models.py:33`）。但
  `Skill.subtask_id` 去留未定——它不是小事：`core/ids.py:111-117`
  `is_stage3_candidate`（skill_id == subtask_id+"/skill）被 K4 入口检查
  （`composites/acceptance.py:312`）、候选枚举（`composites/skill_status.py:46`）、live
  分组（`methods/acceptance/live_run.py:203-209`）、审计脚本
  （`scripts/audit/audit_live_run.py:161`）、装配溯源（`assemble_reuse.py:194/253`）六
  处消费。来源字段与 subtask_id 的关系（保留为来源之一 / 彻底删除）必须在 M1 之前定；
- **Exam**：挂 skill_id，`EXAM_SCHEMA_VERSION` 升（`models.py:34`）。若 exam_id/
  attempt_id 的派生规则随之改变，Attempt 的引用链也变（`models.py:273-298`，README §2
  的契约要同步）；
- **Subtask 是否保留：方案没有说**。§2 说段是"某个模块的私有产物"，暗示 Subtask 从
  五个核心对象中降级为学习相位证据模块的私产；但装配溯源（`skill_lineage_tasks`）、
  审计、K5 的来源技能解析（`composites/curation.py:672-707`）都靠 subtask→task 的查
  表。若删除，来源字段必须携带 task 级引用；若保留，README §2 的"五对象"表述要改。
  这是 M1 的前置决定，不能边实施边定；
- **Trajectory**：`skills_injected`（`models.py:146-147`）记录 skill_id+version 列表，
  skill ID 换名后其记录值的形态变化，README §2 契约需注明（是否升版可讨论，但沉默不
  行）；
- **旧对象记录的读取**：AGENTS 七.3 要求"读旧版本对象要么能正确迁移、要么明确报错，
  不能静默读成默认值"。新字段若无默认值，`from_dict` 对旧记录直接 TypeError
  （`models.py:98-104` `_known_kwargs` 的语义）；若有默认值，就踩了"静默默认"的线。
  需要一个**显式的 v1→v2 对象映射纯函数**（来源 ← subtask_id 推导、Exam.skill_id ←
  make_skill_id(subtask_id) 推导），装配器与审计脚本对旧源 run 走它，并配测试；
- **验收报告是新对象**：建议按相位私有 JSONL 落盘（不进 store 五类，避免动
  `infra/store.py:29-35` 的 KIND_DIRS/LAYOUT_DIRS），文件名与行 schema 在方案里冻结。

### 1.3 golden 矩阵如何再生成

见 C1/C2：M1 起每步要么 golden 无 diff、要么同提交重生成 + diff 披露；M4 全量重生成
（采集器 `tests/golden/generate.py` 8 组合 × 3 崩溃点续跑等价验证照旧），并**对归档的
v3 基线**按 §2 的预注册准则做语义比对——现行 fixtures 在 M4 会被新布局覆盖，所以
M1 开工前要把它们归档成只读基线（或验收脚本从 git 固定 commit 检出）。

### 1.4 文档同步

AGENTS 五规定的改设计顺序是"README → decisions → 代码 → log.md"。README §1–§3（积木
表、五对象、阶段 0–6 全文）与 AGENTS §四/§六、`docs/runbooks/
live_run_acceptance_runbook_20261014.md` 都要随 M4 同步重写；方案 §8 M4 的清单里没有
这一项（对照上轮 P1b 行明确列了"同步 scripts/ 与 runbook"）。

---

## 2. 重点 2："exam 预期行为不变"的验证设计 ——【偏移约束 C2】【偏移约束 C3】

**结论**：方案 §6 的三要素（最终库、测试指标、模型调用多重集）方向对，但照字面**不可
执行**；且 §3.1 的"只测变化的部分"与现状矛盾。建议按下文预注册。

### 2.1 为什么"逐字节"不可能、字面"多重集"也不行

技能 ID 与段解绑（§5.6）后，skill/exam/attempt 的 ID 换名，连锁反应：

- 对象文件名 = URL-quote(ID)（`assemble_reuse.py:449-451`、store 按 ID 落盘）→ golden
  每个对象**路径**都变；
- curation 的 G7/G9 prompt 正文嵌 `{skills_listing}`（skill id 清单，
  `prompts/curate.py:20/90`），假模型还按 prompt 里的 id 回键输出（
  `tests/golden/harness.py:289-298`）→ ID 变则**请求正文与输出**都变；
- `skills_injected`、attempt 的 skill_id 引用同理。

所以"模型调用多重集"比较前必须先做 **ID 归一化**。现有工具不够：
`assert_equal_ignoring`（`tests/golden/harness.py:506-521`）只会"整键跳过"，
`assert_resume_equivalent`（`harness.py:534-569`）只容忍崩溃行与重问，二者都不能表达
"两套布局之间的语义等价"。M4 需要一个新的比对器，方案应预注册它的口径。

### 2.2 建议的预注册等价准则（并入方案 §6，M4 验收引用）

归一化（在 harness 现有四类——time/duration_ms/request_id/processed_this_call 置占
位、run 目录路径擦除，`harness.py:421-454`——之外新增两类）：

- **N2 ID 映射**：对同一 subtask 集合，按 v3 与 v4 两条确定性 ID 规则生成一一映射，
  应用于所有字符串字段（含 prompt 正文、skills_injected、账本行）；
- **N3 布局/标签映射**：旧 stageN 路径与 pipeline_log/failed_units 的 stage 标签 →
  新相位路径/标签的固定映射表。

必须相等：

- **E1 最终库**：按来源段配对的 {正文, version, status, group, rewrite_history 逐条}
  多重集相等；
- **E2 判定**：每个来源段的 accepted/discarded 与 discard_reason 配对相等；
- **E3 测试指标**：C 相位 summary 的 with_bank / control / delta（两种 avg@N、
  pass@N、aux、infra 口径）与旧 `stage6_summary.json` 逐字段相等；
- **E4 模型调用史**：(role, prompt 名, N2 归一化请求正文) 的多重集相等（复用
  `assert_resume_equivalent` 的多重集比较器）；
- **E5 续跑**：新框架内部沿用现行准则——8 组合 × 3 崩溃点重生成时全等价。

明确**不**比较（构造性差异，披露即可）：各阶段 summary 的分桶（G3 失败从 stage3 的
`model_output_failed` 挪进 A 的报告）、失败账本的阶段标签、目录布局、"存在后被删除"
的 discarded 技能对象（旧：G3 失败时 skill 根本不落盘；新：落盘后由决策删除，最终库
相同但 store 里多一个对象）。

**golden 测不到的部分必须用单元级协议断言钉住**：假模型永不泄题（leaked 恒 False，
`harness.py:270-271`）、裸答恒错（"I have no idea"，`harness.py:265-269`），所以
leak-void、weak_already_knows 短路在 golden 里**不可达**——这些语义只能靠假模型单元
测试钉（`tests/composites/test_acceptance.py:189/243/531` 是现成的迁移底版）。

**真实 run 的比较边界要写明**：ID 换名会改变真实调用的 prompt 正文，因此"exam 预期
行为不变"只在**协议层**成立，新旧真实 run 之间不可能 run 级逐一对上（live §6 已对
TB-dev 声明了这一点，exam 同样适用，只是方向相反：不是"变了"，而是"承诺不变也只
能承诺到协议"）。

**强烈建议评估的备选**：规定两种既有方法的 ID 派生规则**不变**
（skill = subtask+"/skill"、exam = subtask+"/exam"），"解绑"只落在 schema 层——
Skill 新增"来源"字段、Exam 新增 skill_id 字段、ID 规则对新方法放开。此备选下 N2
退化为恒等映射，E4 直接可比，§2.1 的连锁反应消失大半，迁移风险骤降。方案的动机
（"不切段的方法无法表达"）并不要求既有方法换 ID。

### 2.3 "只测变化的部分"与现状矛盾（C3）

方案 §3.1 末条："注入内容与上次测过时完全相同的单元，直接沿用上次的报告行，不重跑
（按技能版本判断；**与现有行为一致**）"。**不一致**：K4 的现行行为是

- 泄题判定按 (skill, version) 一次（`acceptance.py:127-138` 首判粘滞）——这部分确实
  "按版本判断"；
- 但**作答**按 (skill, version, **retry_round**) 键（`acceptance.py:182-193`
  `_find_round_attempt`；round 循环 `acceptance.py:360-416`）：G6 改写预算耗尽时轮次
  照耗、版本不变（`acceptance.py:229-276`），**下一轮对同一版本重新作答**——第二次
  采样可能翻盘，这是现行行为的一部分（`test_acceptance.py:531`
  `test_rewrite_budget_exhaustion_consumes_the_round` 钉的就是它）。

若新框架按 (skill, version) 去重跳过重考，exam 的最终库**可能**不同（一个改写预算耗
尽的技能失去第二次作答机会）。修法二选一：(a) 把括号里改成"按
(skill, version, round) 判断——即现行 K4 的续跑键"，行为不变；(b) 披露为 §6 的有意变
化并把 E1–E4 准则相应放宽。推荐 (a)。

---

## 3. 重点 3：live 的隐性变化排查 ——【偏移约束 C4】【偏移约束 C5】

**结论**：除已披露的两处（§6：①不再因出题失败淘汰技能——今天 live 配置
`authoring.exams: true`，G3 失败即整段作废；②整理不再因考题回归失败撤销合并——K5 的
regression 撤销，`composites/curation.py:672-780`），还有**至少三处未披露的行为变化
候选**，以及一整族"失败处置/账目"的未定义项。

### 3.1 未披露变化候选（每处：现状证据 → 序列化后必须落在哪里）

1. **no-op 守卫（ruling A4）**：轮 0 归因修订后，若幸存组的注入块与轮 0 冻结行逐字节
   相同 → 不执行轮 1，直接 `live_no_improvement` 淘汰
   （`methods/acceptance/live_run.py:296-327 _complete_no_op` +
   `la.no_op_guard`；崩溃补齐协议靠"有成员 live_no_improvement 且无轮 1 判定行"这一
   持久线索，`live_run.py:59-73` docstring）。方案通篇未提守卫。若丢失：多余执行、
   判决可翻盘、discard_reason 语义漂移。**必须作为 L 决策模块的一条规则显式保留**
   （或列入 §6 披露为有意删除）。
2. **weak_already_knows 零执行终态**：基线全成功 → 零执行直接整组终态
   （`live_run.py:449-466` 短路 `judge_round(terminal=WEAK_ALREADY_KNOWS)`，判 strict
   族规则专用）。序列 `D L B L B L C` 里它是"L 读 D 的报告后决定 B 不必跑"的规则；
   若 B 无条件执行，会多花 n_attempts×任务数 次执行并走归因/修订路径（G10/G11 调
   用、版本+1 的改写），调用史与 rewrite_history 都变。须写进决策规则清单。
3. **空洞（infra shortfall）的跨相位继承政策**：现行三条——live 每轮 skip 集 =
   D（stage1）弱臂已接受空洞 ∪ 本轮自接受空洞（`la.skip_ids_for`）；**轮 1 刻意不
   继承轮 0 的空洞**（远程沙箱是新实例，ruling A9，`live_run.py:94-97`）；full 模式
   下 C 的对照臂空洞只能在 D 侧接受、C 永不自收（`stage6_test.py:76-86`）。
   "按相位记录"本身没问题（per-phase 记录与现行 per-round 同构），但**继承政策是方法
   级决策**，方案应成文，否则实施者会顺手做对称继承。

### 3.2 出题/泄题/改写失败的处置（C5 的 exam 侧）

现状：

- G3 出题失败（ModelOutputError）＝ 阶段 3 单元**终态**：先写
  `generation_failures.jsonl`（键 **subtask_id**）再写失败账本，resume 永不再试，
  skill 不落盘（`stage3_generation.py:270-279`、pair 落盘 `:283-287`）；已进化的
  skill 缺考题是另一处置（`exam_missing_advanced`，`:236-242`）；
- K3/判卷/改写/审计的 ModelOutputError：exam 驱动把技能存成
  discarded(model_output_error) + 账本行（`methods/acceptance/exam.py:164-169`）；
- K4 的轮次由"版本号 + 改写失败账本的 exhausted 行数"从磁盘反推
  （`acceptance.py:279-284`）。

新框架必须回答而方案未回答的：

- A 里 G3 失败的**终态账本**：键是什么（skill？）、写在哪、resume 如何跳过（否则
  重跑 A 会对同一技能无限重烧出题调用）？discard_reason 用什么词？
- "没测"（出题失败）与"测了不算"（泄题）与"测了没过"在报告里如何区分？决策模块的
  保留/修改/删除动作依赖这个区分。
- 泄题审计的 call_model 预算耗尽（ModelOutputError）在 A 的处置——照抄现行
  model_output_error 终态即可，但要写明。
- **泄题 void 的轮次语义**（任务点名的问题）：现行"该版作废、本轮算用掉一次"＝
  void 的版本**不发生带技能作答**、直接改写（`acceptance.py:377-389`）。序列化后
  "本轮"＝序列位置；A 的测量协议必须保留"leaked → 本版不消耗作答"（否则调用多重集
  多出作答+判卷）；这本质上是 A 内部的短路规则，方案应明确它属于伪执行的测量协议
  （与"裸答对即短路"同族），而不是硬套"验收只测量"的字面。

### 3.3 报告的最低字段集（C5 的核心）

§2 的报告字段（带技能结果、对照结果、是否提升、证据）**不足以支撑 mid-sequence 续
跑**。K4/K6 今天靠磁盘事实重导出一切相位（崩溃后从账本/版本号恢复，不经内存状态）；
拆分后这些事实必须进报告（或进相位私有账本并被报告引用），至少：

| 现行磁盘事实 | 证据 | 报告/账本里的对应物 |
|---|---|---|
| 泄题判定每 (skill, version) 一行、首判粘滞 | `acceptance.py:127-138, 366-376` | 每版本一行 leak 判定，append-once |
| 改写失败账本（耗尽行 = 轮次消耗证明） | `acceptance.py:100-109, 245-266` | 每版本改写失败次数 + exhausted 标志 |
| 作答按 (version, round) 键 | `acceptance.py:182-193` | 每轮带技能/裸答判定 |
| live verdicts/attributions 首判粘滞 + 版本重放守卫（version == from_version+1） | `live_acceptance.py` docstring 34-58、`live_run.py:271-293` | 同名账本原样保留（K6 机制见 §5） |
| 注入审计行首写冻结、重派发逐字节比对 | `live_run.py:75-98, 488-503` | 交付模块的审计账本，机制原样 |

方案 §8 M3 验收只写"单阶段测试 + 续跑测试"；这张表就是"续跑测试"要保住的东西的清
单，建议并入方案。

---

## 4. 重点 4：续跑契约与外部读取接口 ——【偏移约束 C9】【偏移约束 C8】

**结论**：run 级续跑契约（§7 第一条）成立；但"契约"还包括**外部读取接口**，这一层
方案没碰。

### 4.1 上轮决定明文冻结了阶段标签，本方案实质推翻而未声明

上轮方案 §6（R6 同段）："**失败账本与 pipeline_log 的阶段标签不改**（CLI summary、
审计脚本、runbook 都在读）"。序列驱动取代 stage0–6 后标签必然变，这没有错，但必须
**显式声明这是对上轮决定的修订**并列出消费者清单，否则就是静默推翻：

- `cli.py:232-237/252-257`：summary 子命令按 `STAGE_NAMES`（`pipeline.py:61-69`）逐
  阶段打印并做完备性检查（"missing stages" 报错）；
- `scripts/audit/audit_live_run.py`：读 `stage4_acceptance/live_run`（:86）、
  `stage1_baseline/accepted_infra_shortfall.json`（:93）、failed_units 里
  `stage4_live` 标签（:499-504）、manifest 的 `config.methods.acceptance`
  （:605-651，P1b 刚改过的按 manifest 判模式）；
- failed_units 的阶段标签还有 `stage2_segmentation`（`stage2_segmentation.py:87`）、
  `stage3_generation`（`stage3_generation.py:106`）、`stage4_acceptance`
  （`exam.py:78`）、`k1`（`batch_execute.py:151`）；
- golden 采集器按 `row["stage"]` 取每阶段最后一行（`harness.py:483-488`）——新标签自
  动兼容，但标签集合变了。

**建议**：方案加一节"标签与读取接口"——标签映射表（旧 stage 名 → 新相位标签，建议
形如 `p00:D`、`p01:L` 的"序号+局部名"）；cli.summary 的完备性检查从常量清单改为
**从 manifest 冻结序列推导**（这反而更严：序列里少跑一个相位就能报）；audit 脚本按
manifest 的 config schema 版本分派读取器。

### 4.2 mid-sequence 恢复（until_stage 的推广）

现行：`--until-stage N`（0–6，`pipeline.py:229-232` 校验），停止的 run 下次从 stage0
重放、各相位按单元键跳过。推广方案建议（方案 §7 只说"按阶段序列的序号组织子目录"）：

- `--until-phase k`：k 为**冻结序列的相位索引**（0..len(seq)-1）；每次启动仍从相位 0
  重放（manifest 逐字段比对是入口闸门，各相位按单元键跳过），与现行模型同构；
- 序列已冻结进 manifest，所以索引天然稳定；`k` 超出冻结序列长度应在配置校验期拒绝
  （对照 `_check_run_settings` 现在拒绝越界 until_stage 的先例）；
- run 目录按相位索引分子目录（方案 §7 已写），各相位的 shortfall / skip_audit /
  注入审计落在自己目录下，与现行"每轮一目录 + 每 store 一账本"的隔离纪律一致。

### 4.3 assemble_reuse 与 audit/分析脚本

- **audit_live_run / analysis 脚本**：读旧 run 走旧代码（维护分支），可接受；读新
  run 需要新版读取器（§4.1）。
- **assemble_reuse 是唯一"新代码读旧 run"的常驻路径**，方案只字未提（C8）。逐条硬编
  码假设：`_ALLOWED_DIFF_PREFIXES` 含 `config.methods.delivery`（
  `assemble_reuse.py:121-127`——v4 里 delivery 是逐相位参数，"哪个槽允许不同"的白名
  单语义要重定义）；负清单全是 stage 名（`:148-165`、`:178-188`）；五类对象的拷贝走
  KIND_DIRS（`:1268-1277`）；`skill_lineage_tasks` 靠 subtask 溯源 + "/skill" 后缀解
  析（`:201-268`）；shortfall / generation_failures / segmentation_audit 路径
  （`:472-484`、`:1225-1245`）；v2 检查硬编码（`:1032`）。
  **修法（二选一，写进方案）**：(a) v4 装配器支持 v3 源 run——KIND_DIRS 直读旧布局、
  `upgrade_v3_config` 身份比对、lineage 走 subtask_id 兜底（前提：Skill 保留
  subtask_id 或有 v1→v2 映射，见 §1.2）；(b) 明确声明"对 v3 及更早源 run 的复用走其
  维护分支上的旧代码，v4 装配器只支持 v4 源"，写进 runbook 并在 v4 装配器里对旧源
  响亮拒绝。上轮 §7 把 upgrade_v2 的近期使用场景定为"用新 delivery 复用旧 run 的冻
  结库"——这个场景在 v4 下只会更常用，选 (b) 等于放弃它，需要用户裁决。

### 4.4 AGENTS 六.1 的延续

"旧 run 固定在维护分支续跑"规则本身不受影响（v3 run 的维护分支上跑 v3 代码）。要补
的只是 §1.1 说的拒载提示泛化：v4 代码读到 v3 冻结 manifest 必须明确报"此 run 由
schema v3 冻结，请在它的维护分支续跑或用转换器开新 run"，与今天 v2 的文案同级。

---

## 5. 重点 5：M1–M4 切分的自洽性 ——【偏移约束 C1】【偏移约束 C8a→C9 切分】

**结论**：按 §8 的写法，**没有任何一步是"测试全绿 + 可验收"的自洽中间态**。逐条：

- **M1 改数据对象**：谁消费新字段？没有（相位 M2–M4 才出现）；旧字段还在吗？方案没
  说。且加字段本身改变所有落盘对象字节 → golden 全变（C1），"测试全绿"这个验收甚至
  **不能通过**（golden 套件是测试的成员，`test_fresh_run_matches_golden` 当场红）。
- **M2 执行阶段化**：方案把"出题与泄题审计挪进伪执行"放 M2，但伪执行作为**相位**要
  M4 才存在——M2 结束时系统处于"出题代码在伪执行模块里、调用方还是 stage3/stage4"
  的状态。这个状态可以自洽，但必须**选定**：(a) stage3/stage4 暂时调用新模块、调用
  序与落盘序完全不变（golden 逐字节不变），或 (b) 出题真的挪到 stage4 时机（pair 落
  盘纪律、`generation_failures` 键、stage3 summary 分桶全变，golden 重生成 + 大 diff
  披露）。方案哪个都没说。
- **M2/M3 之间**：K4 处于半拆状态（测量半已抽出为验收模块、决策半还在旧驱动
  accept_skill 循环里）。若两步都以"golden 逐字节不变"为验收，半拆态自洽；否则这个
  中间态既不逐字节相同、又没有语义比对器，**无法验收**。
- **K4 拆"测量/决策"是 M2 还是 M3**：方案把它劈成两半（M2 测量、M3 决策），中间态
  如上——可行但必须配 golden 无 diff 纪律。
- **M3 学习阶段化**：agentbrew 加 `merge_check` 放 M3——注意**默认值必须取
  `exam`**（行为不变），`none` 只在 M4 的 live 序列里启用；否则 M3 就偷跑了 live 的
  变化②。
- **M4**：清单严重不全（§4.1/§4.3/§1.4：CLI、审计脚本、装配器、README、AGENTS、
  runbook、拒载泛化、标签映射）。

### 建议的更安全切分（替换 §8 的表）

| 步骤 | 内容 | 验收 |
|---|---|---|
| **M0 基线归档**（不改源码） | 归档现行 golden 为只读语义基线；把 §2.2 准则、§3.3 报告字段表、§4.1 标签映射、§1.2 的 Subtask/ID 决定写进方案修订版 | 三路评审通过 |
| **M1 数据对象** | Skill +来源、Exam +skill_id（含 subtask_id 去留决定）、报告对象 schema 定稿（相位私有 JSONL）、SKILL/EXAM（及必要的 Attempt）升版、**v1→v2 旧对象显式映射纯函数 + 测试** | 测试全绿 + golden **同提交重生成、diff 披露**（预期 diff：仅新增字段与版本号） |
| **M2 执行侧抽取**（纯重构） | 交付+K1 合并为执行相位模块；K4/K6 测量半抽出为验收模块；出题/泄题审计**代码**搬进伪执行模块，stage3/stage4 暂仍作调用方（调用序不变） | 测试全绿（含 `test_acceptance_resume` / `test_live_acceptance_resume` 的每个调用点/写盘点崩溃续跑测试）+ golden **无 diff** |
| **M3 学习侧抽取**（纯重构） | 决策半抽出；切段/归因/写作/改写/整理模块化；agentbrew 加 `merge_check`（**默认 exam**） | 同上：测试全绿 + golden **无 diff** |
| **M4 序列驱动 + schema v4** | 序列驱动替换 stage0–6；config v3→v4 + `upgrade_v3_config` + 一次性转换器 + 拒载泛化；目录/标签按相位索引重组；cli.summary / audit_live_run / assemble_reuse（按 §4.3 的裁决）/ README / AGENTS / runbook 同步；live 变化①②在转换器输出逐文件披露 | 新矩阵重生成（8 组合 × 3 崩溃点续跑全等价）+ **对归档 v3 基线的 E1–E4 全等** + live 差异清单恰为已披露两项 + 单元级协议断言（§2.2 末段）全绿 |

每步沿用方案 §8 已有的三路评审协议——赞同。

---

## 6. 重点 6：§7"不变的部分"的乐观程度核实

**结论**：§7 对 K1/K2/K3/K5 的"主体不动，只改调用方"经代码核实**成立**；对 K4/K6
方案 §7 没有直接声称（只列了 K1/K2/K3/K5），不算撒谎，但 §0.5"组合积木 K1–K6 的大
部分基本不动"对 **K4 不成立、对 K6 大体成立**：

- **K4（`composites/acceptance.py`，421 行）**：`accept_skill` 是一个 130 行的单循环，
  测量（作答/审计）与决策（接受/丢弃/改写）**逐轮交织**，且续跑推导（轮次 =
  版本-1+exhausted 数，`:279-284`）就长在循环里——不存在"抽出测量、决策留在原处"的
  无痛切口，循环骨架必须重组。可直接复用的是外围 helper 与账本读写；粗估主体复用
  率不到一半。"大部分不动"不成立。
- **K6（`composites/live_acceptance.py` 1212 行 + 驱动 825 行）**：K6 本来就按"逐组
  相位函数"组织（pending_groups / load_baseline / judge_round / audit_row /
  survivors_and_block / finalize_group / attribute_group / apply_revisions /
  no_op_guard），重组主要是**归类 + 换编排**，函数级复用度高——"大部分不动"大体成
  立。前提是 §3.1 列的守卫与首判粘滞协议逐一安家（C4/C5）。
- **stage6 双臂**：机制全部保留、动的是接线与目录——分臂 store（轨迹 ID 不带臂信
  息，README §2 :504-506）、同一分母（`stage6_test.py:145-159`）、full 模式对照臂 =
  D 弱臂复用 + 空洞继承（`:76-86`、`:278-290`）。**但 C 相位的对照臂定义方案没写
  清**：`compare: D_test`（§4.2 示例）在 split 模式下不存在——D 只跑了 train；现行
  split 模式是"fresh bare on test"（`stage6_test.py:333-350`）。C 必须明确是双臂相位
  （with-bank + 对照），对照臂的来源按 mode 分派（split = 新跑裸对照；full = 复用 D
  弱臂且空洞只能在 D 侧接受）。否则"对照是否真的是对照"（外层 AGENTS 一.3 的事故类
  型）没人把关。
- **stage1 的 aux/shortfall**：D 相位原样继承，机制零改动——赞同。
- **core 层**：§0.5 说"core……基本不动"，但 §5.6 的数据对象变更本身就动
  `core/models.py` 与 `core/ids.py`（ID 规则、`is_stage3_candidate`）——方案内部自
  相矛盾，属措辞问题（见琐细 T1），应改为"core 的对象字段与 ID 规则按 §5.6 升版，其
  余不动"。

---

## 7. 分级汇总

### 7.1 【偏移约束】（不修不能进实施；正文含证据与修法）

- **C1** golden 处置缺位：M1 加字段必改全部落盘对象字节，M1 验收"测试全绿"不可满足；
  M2/M3 验收应升为"golden 无 diff"。修法见 §5 切分表。（证据：方案 §8；`core/
  models.py:31-35,107-111`；`tests/golden/test_golden_matrix.py:46-52`；上轮方案
  §7.1。）
- **C2** "exam 预期行为不变"无可执行准则：ID 换名波及对象路径、prompt 正文
  （`prompts/curate.py:20/90`）、假模型回键输出；三要素准则需 ID/布局归一化新比对
  器；golden 测不到短路语义，需单元级协议断言。修法 = §2.2 预注册准则 + 认真评估
  "既有方法 ID 不变"备选。
- **C3** §3.1"只测变化的部分……与现有行为一致"与 K4 现状矛盾（改写预算耗尽后同版
  本重考，`acceptance.py:229-276/360-416`）。修法：改口径为按 (skill, version,
  round) 判断，或披露为行为变化。
- **C4** live 至少三处未披露变化候选：no-op 守卫（`live_run.py:296-327`）、
  weak_already_knows 零执行终态（`:449-466`）、空洞继承政策（`:94-97`、
  `stage6_test.py:76-86`）。修法：写进决策规则清单或列入 §6 披露。
- **C5** 失败处置与报告字段集未定义：G3 失败的终态账本/resume 键、泄题审计失败处
  置、"没测/测了不算/没过"的区分、报告最低字段表（§3.3）。修法：方案加处置表 + 字段
  表。
- **C6** schema 升级清单不完整：config v3→v4 全套先例（upgrade_v3_config、转换器写
  新名、两处硬编码拒载泛化 `stage0_freeze.py:297` / `assemble_reuse.py:1032`）；
  数据侧 SKILL/EXAM 之外 Attempt/Trajectory(skills_injected)/Subtask 去留；旧对象
  v1→v2 显式映射（AGENTS 七.3）；README/AGENTS/runbook 同步。
- **C7** assemble_reuse 未进方案：白名单/负清单/槽位 diff/溯源函数全绑 stage 布局与
  旧 ID 规则（`assemble_reuse.py:121-127/148-188/201-268/1268-1277`）。修法：§4.3
  二选一裁决。
- **C8** M1–M4 切分不自洽：M1 无消费者且验收不可满足；M2 挪出题时伪执行不存在、
  中间态未选型；M3 的 merge_check 默认值必须为 exam。修法：§5 重切分表。
- **C9** 标签/CLI/审计接口：上轮 §6 明文冻结的阶段标签被实质推翻而未声明；消费者
  清单（cli.summary、audit_live_run、failed_units 标签、runbook）与 until_phase 推
  广未列入 M4。修法：§4.1/§4.2。

### 7.2 【琐细】（不阻塞，实施时顺手处理）

- T1 §0.5"core 基本不动"与 §5.6 自相矛盾（`core/models.py`、`core/ids.py` 必动），
  措辞改为"对象字段与 ID 规则按 §5.6 升版，其余不动"。
- T2 §4.2 示例 `models: [strong, weak]` 与现行"角色表 + ROLE_DEFAULTS 锚点 +
  token_budgets.roles 按角色键控"（`config.py:102-113, 260-321`）不兼容，相位级模型
  清单需要一条与角色预算对接的规则，schema 设计时定。
- T3 阶段局部名进 manifest ⇒ 重命名即新实验（符合"改一个字也是新实验"），建议方案
  明说，或直接规定规范名 D/A/B/C/L。
- T4 组合表 C′（整库给）与现行 delivery `whole_bank` 应合并为一个名字，避免两套整
  库注入实现。
- T5 v4 转换器文件命名规则（`*_v4.yaml`，已有 `*_v2_v3.yaml` 双后缀先例）在方案里定
  一句。
- T6 `authoring.exams` 删除后 `METHOD_SLOTS`/`METHOD_DEFAULTS`（`config.py:78-92`）
  及其 pin 测试、"a test pins that they do" 要同步清；`check_live_run` 的
  n_attempts ≤ n_attempts_weak（`methods/acceptance/__init__.py:66-84`）与
  check_authoring 的冲突校验要在 v4 的配置加载检查里重新安家——方案 §3.3 只给了
  merge_check 一例。
- T7 `audit_live_run.py:161` 的候选过滤靠 `is_stage3_candidate`（ID 派生式）——ID 规
  则若变，须换成来源字段判定（C2/C6 的连带项，单独点名防漏）。

### 7.3 【赞同】

- A1 三对象跨阶段接口 + 中间产物不进接口（§2）：修正了"把一种做法的内部工序当系统
  接口"的真问题（上轮 §1 ④ 的"stage3 无条件出题因为 K5 要用"就是症状）。
- A2 验收只测量、决策归 L（§0.4/§5.2）：把散在 K4/K6/stage6 的丢弃决策归口，方向正
  确。
- A3 merge_check 参数化 + 配置加载时依赖检查（§3.3）：延续 check_authoring 集中校验
  先例。
- A4 序列 + 每相位配置进 manifest、"序列即实验身份"（§4.2）：与 B4 逐字段比对兼容。
- A5 live 两处变化的诚实披露与"与 TB-dev 在跑 run 比较须注明"（§6）。
- A6 整理只在最后一个 L、每轮整理留给未来方法（§3.2/§9）：复现现状、不开投机抽象。
- A7 明确不做 DAG/引擎/基类/自动发现/新哈希（§9）：延续上轮反过度设计裁决。
- A8 K1/K2/K3/K5 主体不动的判断经调用面核实成立（§6）。
- A9 每步沿用三路评审协议（§8 末行）。

---

## 8. 给实施的最低要求（一句话版）

M0 先行：归档 golden 基线、写定等价准则（§2.2）、报告字段表（§3.3）、标签映射与
until_phase 设计（§4）、Subtask 与 ID 规则两个前置决定（§1.2/§2.2 备选）、
assemble_reuse 裁决（§4.3）——这些都落进方案修订版之后，M1 才有可验收的起点。
