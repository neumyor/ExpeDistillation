日期：2026-10-16
一句话说明：对 docs/decisions/execute_learn_repair_plan_20261016.md 的接口设计专项评审（评审代理 B）：R0 冻结的四个交接物形状、模块间数据流闭环、与现有代码的衔接成本、续跑键兼容性。
对应 log.md 条目：2026-10-16 执行—学习闭环修复方案三路评审（B：接口）。

---

## 结论先行

方案的模块划分本身是自洽的，三类现行跳过键（泄题按 (skill,version)、带技能作答按 (skill,version,轮键)、live 按轮仓完备性）在相位化后**都能**保留——live 的"两轮"天然映射为两个 B 相位，这一点方案没有明说但成立。但 R0 冻结的四个形状有 **3 个阻断级缺口**，不补上就冻结，Wave 1 六个代理会各猜一版：

1. **C5 拆分丢失了"运行是否完整"这个维度**。`correctness: Optional[bool]` 加"infra_error 降级为指标原文"之后，`None` 同时意味着"无判分器"和"运行没拿到结果"，K1 的机械重试触发器（`batch_execute.py:354` 靠 `status == "infra_error"`）无家可归。
2. **ExecutionUnitList `[(task, injected_skills)]` 装不下 K1 的续跑契约**。跳过键、stored-unit 比对、远程 run id 三者同时依赖 `skills_injected`（记账值，三种编码）和 `skills_block`（渲染字节）；清单不带 block，whole_bank 的 digest 串也无法重渲染 block。
3. **单元 id 方案未定义**。R0 只写"单元 id"，A/B/D 三种相位的单元键怎么构成、"轮键"（retry_round）映射到哪个相位标签，全部悬空——而这正是"跳过键=现行续跑键"的落点。

另有 8 条偏移（最重要者：R6 的判据与边界文档 §4.3"没变的单元不重测"**直接矛盾**；三种报告收敛为一种的映射未说清；"测不了+原因"必须是受控词表否则决策表无法分支）和 5 条琐细。共 16 条。

逐字核实：`reports.py` 跨模块私有访问确为 **17 处**（方案数字正确）；`.status ==` 在 src 下实为 **21 处、12 个文件**（方案写 24 处），其中 **15 处是 Skill.status**（与 C5 无关），真正的 trajectory/runoutcome status 消费点只有约 6 处，真正的大面积涟漪在 `RUN_STATUSES / TRAJECTORY_STATUSES / VALID_STATUSES / infra_error` 的 **51 处引用、10 个文件**（含 `metrics.py` 的分母记账与 aux 契约）——方案的风险表把两套 status 混为一谈。

---

## 逐条发现

### 阻断

#### 1.【阻断】C5 拆分后，"对不对留空"与"运行无效"两个正交维度挤在一个 `Optional[bool]` 里

**问题**：R0/C5 规定 `RunOutcome.status` 拆成 `correctness: Optional[bool]`（留空 = benchmark 无判分器）+ 指标字段，`infra_error`"从记录词汇里移出，降级为指标里的一条原文"。但：

- 现行 K1 的机械重试触发器是 `batch_execute.py:354`：`if traj.status == "infra_error"` → 推进 `k1_retry_cursor`、抛 `InfrastructureError` 重跑。若 infra 失败变成"返回一条带错误原文的记录"，执行层**没有任何机器可读信号**区分"该重试"与"该存储"。
- TB 的 `classify_result`（`benchmarks/tb/plugin.py:236-268`）会产出三种"返回了数据但运行已坏"的结果：伪零分（reward 在、pytest 没跑）、`no_reward_no_exception`、tmux 报错。它们的 `correctness` 落值分别是 False / None / None——其中 `None` 与"考试类 benchmark 无判分器"的编码**完全相同**。验收读报告时无法从记录本身分辨"没有判分器"和"判分器没产出（因为运行坏了）"。

**证据**：`benchmarks/base.py:53`（RUN_STATUSES 三值词汇是插件契约）；`batch_execute.py:354-362`；`benchmarks/tb/plugin.py:254-268`；边界文档 §2.2"benchmark 没有判分器或标准答案时，这一项留空"。

**建议**：R0 必须二选一并写死：

- **方案甲（推荐）**：契约改为"硬基础设施失败一律由插件**抛出** `InfrastructureError`（`environment_call` 已经如此），凡返回的 RunOutcome 一律视为已拿到结果"。此时 `correctness=None` 唯一含义是"该 benchmark 无判分器"；伪零分这类软异常返回 `correctness=False` + 指标原文，由验收的 TB 具名规则判无效。`Attempt.judge_verdict: Optional[bool]`（`core/models.py:307`）是现成先例。
- **方案乙**：RunRecord 增加独立字段 `completed: bool`（或 `invalid_reason: Optional[str]`），两个维度各自表达。

不裁决就冻结，W1-A（验收）与 W1-B（执行）必然实现出两套语义。另注意：伪零分从"执行层重试"改为"验收判无效→阶段调度重派"，重派需要新的 attempt_token/namespace 推进规则，方案未提。

#### 2.【阻断】ExecutionUnitList `[(task, injected_skills)]` 装不下 K1 的续跑契约

**问题**：K1 的三套机制同时消费两个值：

- **跳过键 / stored-unit 比对**：`batch_execute.py:258` 按 `_canonical(traj.skills_injected)` 逐字节比对；
- **远程 run id**：`_run_namespace`（`batch_execute.py:202-225`）把 `skills_injected` 的 canonical JSON **和 `skills_block` 的 sha256** 一起哈希；
- **live 的 freeze-compare**：`audit_row`（`live_acceptance.py:558-572`）冻结注入 block 的 sha，resume 时逐字节重比。

`[(task, injected_skills)]` 不含 `skills_block`。而 `whole_bank` 驱动的 `skills_injected` 是 bank digest **字符串**（`whole_bank.py:23`，`bank_reference(bank)`），从 digest 无法重渲染 block；`group_by_task`/`retrieval` 的 block 依赖"读取 store 当时的 pending 组员版本"。block 不进清单，要么执行层无法渲染，要么重派/续跑时渲染出与冻结行不一致的字节。

另一个语义必须钉住：`none` 驱动用 `None`、检索低于阈值用 `[]`（`none.py:26`、`retrieval.py:74`）——**两种"没注入"的编码在远程 run id 上不同**。统一清单必须原样携带这个值。

**建议**：R0 把执行单元行定为（`skills_injected` 必须与写进 Trajectory 的值逐字节一致）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `task` | Task / Exam 引用 | A 相位为确定性 exam_id（见发现 10） |
| `skills_injected` | Any | None=对照/裸跑；[]=检索未过阈；digest 串=整库；引用列表=逐条或整包。原值透传 |
| `skills_block` | str | D9 渲染字节；对照行 = `""` |
| `aux_payload` | Optional | A 相位的参考答案等"附带信息"（边界文档 §3.5） |

#### 3.【阻断】单元 id 方案未定义，"跳过键 = 现行续跑键"落不了地

**问题**：R0 的 AcceptanceReport 写了"单元 id"，但全方案没有定义单元 id 的构成。三种相位的单元本体完全不同：A 相位 = (exam, skill, **version**)；B 相位 = (task, 整包成员及其版本)；D 相位 = (task, role)。而"跳过键=现行续跑键"（§3.2 条 7）要求：

- 泄题按 (skill,version) —— `leak_verdicts.jsonl`（`acceptance.py:130-141`）；
- 带技能作答按 (skill, skill_version, **retry_round**) —— `_find_round_attempt`（`acceptance.py:185-196`）。**retry_round 在相位模型里的对应物是什么，方案没写**。边界文档把 K4 的轮内循环摊平成跨相位循环（修改→下个 A 相位重测），"轮键"应映射为相位标签（pNN），但 R9 只说标签从冻结序列派生，没有说跳过键含它；
- live 按轮仓完备性 —— `round00/round01` 目录映射为相位目录（R9 提了 pNN，但没说"轮=相位实例"这一等价）。

§4.3-1"没变的单元不重测"还要求**跨相位**按（题, 注入技能）匹配上一份验收报告——单元 id 必须在相位间稳定且含版本，否则"修改后一字不差也算没变"无从判定。

**建议**：R0 增加一节"单元 id 规则"，给出确定性构造，例如
`unit_id = f"{phase_label}:{benchmark}:{task_or_exam_id}:{role}:{sha(canonical(skills_injected))[:8]}"`，
并列表写明它与现行三类跳过键的逐一对应（含 retry_round → phase_label 的映射、attempt serial 仍是计数制）。

### 偏移

#### 4.【偏移】R6 判据与边界文档 §4.3 直接矛盾

**问题**：R6 判据写"改写预算耗尽仍按'版本不变、本轮消耗，**下轮重答同版**'"；边界文档 §4.3-1/2 写"没变的单元不重测……**沿用上一次的验收结果，不再重新执行**。修改后内容和原来一字不差，也算没变"。对同一种情形（改写失败、版本未变），一个要求下轮重新作答，一个要求沿用上次验收结果。方案开头声明"冲突时以边界文档为准"，但把矛盾的旧行为写成了自己的验收判据——实现代理会照判据做。

**证据**：修复方案 §4 R6 判据 vs 边界文档 §4.3；现行 `_rewrite_round`（`acceptance.py:232-279`）确是"版本不变→下轮重答同版"。

**建议**：明确裁决并回改 R6 判据。若按边界文档，"沿用上一次的验收结果"要求执行层能按单元 id 跨相位查到上一份报告行（与发现 3 的单元 id 强耦合）。

#### 5.【偏移】三种报告收敛为一种的映射没有说清

**问题**：R6/C2 说"交给决策的必须是同一种行结构"，但没给三→一的映射表：

- `live_report`（`reports.py:188-249`）每行带 `better`（比较结论——按边界文档 §3.1 应归决策）、`verdict`（零执行终态）、`attribution`（现行是动作计划，R6 要改成事实）。`better` 搬走后，`judge_round` 的反篡改重算比对（`live_acceptance.py:715-724`）要跟着改；零执行终态（weak_already_knows / live_injection_overflow / live_undetermined）在统一行的"终态+原因"里各占哪一格（无提升空间？测不了？）未定义。
- `baseline_report`（`reports.py:59-115`）每行含 strong+weak **两臂**计数；统一行是单单元的，D 相位报告必须拆行并携带 role 字段，方案没说。
- `exam_report`（`reports.py:122-180`）的 `attempt`（改写源选择：weak_answer + judge_reason，G6 改写的必需输入）必须迁入统一行的"逐次判定+依据"，否则写作模块拿不到改写素材；`rewrite_exhausted` 是 §4.3-2 的判据，同样要有去处。
- E6 golden（live 账本逐行）必然破，方案只在 §3.3 给了"或逐处披露"的总口子，没把 E4/E6 的预期差异列出来。

**建议**：R0 附一张"旧字段 → 新行字段"映射表，作为 W1-A 冻结字段时的对照清单；并单列一节预期 golden 差异（E4：泄题版本先作答后判无效的多余调用；E6：verdict 行去掉 better/加入相位标签）。

#### 6.【偏移】"测不了+原因"必须是受控词表，否则决策规则表无法分支

**问题**：边界文档 §4.2 对"测不了：泄题"（→修改）与"测不了：其他原因"（→删除）分支；R0 只写"测不了+原因"。若原因是自由文本，决策无法可靠分支，且"新增的判定规则"无法以具名方式进词表。同理，"逐次判定+依据"里的依据若纯自由文本，逐报告汇总和审计都无法做口径一致的统计。

**建议**：原因 ∈ 具名词表（`leak / injection_overflow / exam_generation_failed / invalid_redispatch_exhausted / all_holes / ...`），自由文本作为补充字段。词表放 `composites/decision.py`（现行 discard_reason 词表的家）。

#### 7.【偏移】新增路径的输入不可达：找证据拿不到"要从哪几段经验里学"的经验

**问题**：边界文档 §2.5 找证据的输入是"任务清单；验收报告里指向的轨迹和产物"。但"新增"要学的经验是 **D 相位的强模型轨迹/切段**——不在本轮验收报告指向的轨迹里（本轮报告只指向本轮运行）。且"新增"的动作对象是任务（不是技能/包），任务清单行没有承载"目标种类+身份"的字段，找证据不知道去哪份产物里找哪个任务的经验。

**证据**：边界文档 §2.5、§4.2 摸底行；`baseline_report` 只含计数（`{"success","total"}`），无任何轨迹指针。

**建议**：任务清单行加 `target_kind ∈ {skill, group, task}` 与 `target_id`；允许找证据对"新增"行读 D 相位对象仓（或规定 D 相位 AcceptanceReport 的产物位置字段指向强臂轨迹/切段）。

#### 8.【偏移】泄题检查移到验收后的成本变化未披露

**问题**：现行 K4 在带技能作答**之前**做泄题审计（`acceptance.py:371-396`，注释明言"The voided version is never examined"）。边界文档 C1 裁决泄题检查归验收——而验收的输入是运行记录，即必须先跑带技能作答。每个泄题版本因此多花一次作答+判卷的模型调用（现行是零）。这不是错，但 E4（模型调用多重集）必然变，方案没把它列入披露项，Wave 4 验收时会当成意外差异。

**建议**：在 R1 或 §3.3 预登记"E4 预期差异：泄题版本先作答后判无效"。

#### 9.【偏移】C5 波及面的数字与事实不符，会误导实现

**问题**：方案 §7 写"`RunOutcome.status` 词汇变更波及 24 处 `.status ==`，涉及 12 个文件"。实测（c6a9fb8 工作区）：src 下 `.status ==` 共 **21 处、12 个文件**；其中 **15 处是 `Skill.status`**（pending/accepted/discarded/merged——与 C5 完全无关，一个字都不用改）；真正受影响的 trajectory/runoutcome status 消费点约 **6 处**（`metrics.py:105/107`、`models.py:164`、`live_acceptance.py:1053`、`batch_execute.py:354`、`searchqa/plugin.py:273`）。真正的大面积涟漪在 `RUN_STATUSES / TRAJECTORY_STATUSES / VALID_STATUSES / infra_error` 的 **51 处引用、10 个文件**（`metrics.py` 的分母记账 `attempts == valid + infra` 不变式、aux_scorer 契约"只看 valid 轨迹"、`react_loop`、三个插件、`stage1_baseline`）。

**建议**：改写风险表：按"词表引用 51 处/10 文件 + status 消费 6 处"报，明确 Skill.status 不在范围内；并把 `Trajectory.status / success`（`models.py:154-164`，success 是派生字段）与 RunOutcome 的同步改动（TRAJECTORY_SCHEMA_VERSION 升版、E1-E6 落盘字节）列进 Wave 0 判据。

#### 10.【偏移】A 相位的"题"在交付时还不存在，出题的时序没有着落

**问题**：R5 写"per_skill …… 收库 + 题，返回每题一行单元——考试模式下题即考题"。但按边界文档 §3.5，出题（附参考答案）归**执行**。交付时考题尚未生成，`resolve(bank, tasks, params)` 拿到的只能是**确定性 exam_id**（`subtask_id + "/exam"`）而非考题内容；考题与参考答案按边界文档要"作为附带信息传下去"（给验收判卷用）。这条时序（交付引用 exam_id → 执行出题 → 运行记录附参考答案 → 验收判卷）方案没写。

另外，验收的声明输入（边界文档 §2.3："运行记录，以及记录指向的轨迹和产物"）**缺执行单元清单**：出题失败/无任何运行的单元要报"测不了"，验收必须知道单元全集——现行 `judge_round` 靠显式传入 `expected_ids`（`live_acceptance.py:627`）就是这个原因。R1 也没补。

**建议**：R0/R1 写明验收输入 = ExecutionUnitList + RunRecord 集；R5 写明 A 相位单元引用确定性 exam_id，出题是执行阶段的第一步，参考答案经 RunRecord 的 aux_payload/产物位置传给验收。

#### 11.【偏移】对照单元与"未跑：对照已全对"在报告里没有位置

**问题**：两个缺口：

- 对照单元本身：ExecutionUnitList 里可用 (task, skills_injected=None) 表达（发现 2 已要求钉死 None/[] 语义），这点可行。但决策取"对照的验收报告"按相位配置选（R9 的 `compare.against`），要求对照行与本轮行的**单元键可对齐**（B 相位单元键=task_id vs D 相位对照行键=task_id+role），对齐规则没写。
- 边界文档 §4.3-3："裸跑全对时可以跳过带技能的运行……报告中如实标注'**未跑：对照已全对**'"。R0 的终态词汇 {成功/失败/无效/测不了} 里没有这个槽位。若记成"测不了"，决策会得出"测不了：其他原因→删除"，动作恰好相同（都删），但结论（无提升空间 vs 测不了）与原因不同，且 §4.2 表里两者是不同行。另外这个跳过决策要求 phase_execute **先跑对照臂、验收、再决定是否跑带技能臂**——相位驱动内的执行顺序依赖，R9 的 phase_execute 描述没有提。

**建议**：终态词表加 `not_run_control_perfect`（或单列 `skipped` 标记 + 原因）；R9 写明 phase_execute 的两段式（对照臂先行）。

### 琐细

#### 12.【琐细】RunRecord 与 Trajectory/Attempt 的关系未定，存在重复风险

R0 说形状"落进 core/models.py（或新增 core/contracts.py）"——两可。RunRecord 的字段与 Trajectory（task_id/skills_injected/model_role/model_name/attempt_index/status/meta）几乎逐项重合；若新建对象并存，就是方案自己反对的重复词汇。A 相位的运行载体也未定：Attempt 有 weak_answer 无 steps，Trajectory 相反，二者不兼容。建议：声明 RunRecord 是 Trajectory 的**接口视图**（不是新持久化对象），A 相位以 Attempt 为运行记录、以答卷为产物，并把"落点二选一"在 Wave 0 定死。

#### 13.【琐细】决策入口对首次学习无对照报告

序列 `D L A …` 的第一个 L 相位：本轮报告 = D 相位报告，无对照报告、无既有技能动作（只有新增）。R3 的四元组签名应允许 `对照报告=None`，并规定此时候选规则 = 摸底挑题（边界文档 §4.2 最后一行）。

#### 14.【琐细】AcceptanceReport 的"终态(成功/失败)"与计数冗余

单元判定成功/失败可由 (成功数, 有效运行数) 推出；建议终态字段只保留特殊态（无效/测不了/未跑）+ 具名原因，普通单元靠计数表达，避免两个真相源打架。

#### 15.【琐细】无效重派上限没有配置槽位

边界文档 §2.3"重派次数有上限；用完上限仍无效的单元，就留作空缺"。R9 的配置清单（加载期检查列表）没有这个键。应在 `phases` 配置里加 `invalid_redispatch_limit`，并让"重派耗尽"成为测不了原因词表的一员（发现 6）。

#### 16.【琐细】R5 签名里 "bank" 在 B 相位时刻的含义未定

`resolve(bank, tasks, params)`：stage 6 的 bank 是 `skill_bank.jsonl`（整理产物），但 B 相位（stage 4 时刻）现行 `group_by_task` 从 **store 派生 pending 组**（`live_acceptance.py:422-448`）。R7 的逐相位技能库产物落地后，B 相位的"bank"应是上一个 L 相位的产物——但第一次 B 相位之前必有一个 L（live 方法是 D L B …，成立），需要 R5/R7 写明"交付收到的 bank = 上一个 L 相位的技能库产物，含成员状态"，且组员版本取自该产物。

---

## 核实记录（供其他评审代理引用）

| 方案断言 | 实测 | 判定 |
|---|---|---|
| `reports.py` 17 处跨模块私有访问 | grep 计 17 处（`la._*` 7 处 + acceptance 私有导入 10 处） | 相符 |
| `.status ==` 24 处、12 文件 | src 下 21 处、12 文件；15 处为 Skill.status | 数字与归类均不符 |
| `live_acceptance.py` 1375 行 | wc -l = 1363 行 | 基本相符 |
| `exam_once.py` 301 行、作答/判卷同函数 | 301 行，作答 :270、判卷 :298 同在 `exam_once` | 相符 |
| 泄题判定按 (skill,version) 落盘 | `leak_verdicts.jsonl`，`_find_leak_verdict` | 相符 |
| 带技能作答按 (skill,version,轮键) | `_find_round_attempt`（skill_id, skill_version, retry_round） | 相符 |
| live 按轮仓完备性 | `_judge_round` 的 incomplete 检查 + accepted holes | 相符 |

## 建议的字段表（汇总）

**RunRecord**（视图或新对象，Wave 0 定）：

| 字段 | 类型 | 说明 |
|---|---|---|
| unit_id | str | 确定性单元键（发现 3） |
| attempt_index / dispatch_index | int | 采样序号 / 重派序号（"第几次"两个维度） |
| model_role / model_name | str | 谁做的 |
| skills_injected | Any | 与 Trajectory 同值原样透传 |
| correctness | Optional[bool] | None 仅 = 无判分器（契约见发现 1） |
| metrics | {retry_count, error_texts, duration, exit_status} | 报错原文在此 |
| artifacts | {store 对象种类+id, workdir 路径} | 轨迹、答卷、产物位置 |

**AcceptanceReport 行**：

| 字段 | 类型 | 说明 |
|---|---|---|
| unit_id | str | 与执行单元清单同键 |
| phase_label / arm | str | 所属相位；D 相位按 role 拆行（发现 5） |
| injected_skills | Any | |
| runs | [{run_key, verdict∈{success,failure,invalid}, basis:具名规则+文本}] | 依据须可携带 judge_reason/weak_answer（发现 5） |
| valid_count / success_count | int | |
| status | normal / not_testable / not_run_control_perfect | 普通单元靠计数（发现 14） |
| cause | 具名词表 | 测不了/未跑的原因（发现 6/11） |
| artifacts | | |

**任务清单行**：`{target_kind∈{skill,group,task}, target_id, action∈{keep,revise,delete,add}, reason(具名), basis_unit(指向报告行)}`（发现 7）。

**证据包**：`{target, facts(事实，无动作), segments:[{trajectory_id, step_range} 或 attempt_id], old_skill(修改时), source_experiences:[trajectory/subtask id](新增时)}`（发现 7）。

---

## 给 Wave 0 的一句话

R0 目前冻结的是"四个名字"，不是"四个形状"。先补：① 运行完整性维度的表达契约（发现 1）；② 执行单元行携带 skills_injected + skills_block 双值（发现 2）；③ 单元 id 规则及与三类现行跳过键的对应（发现 3）。这三条进 R0，其余按严重度排队进各修复项。
