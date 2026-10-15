日期：2026-10-15
一句话说明：执行—学习闭环重构方案（scope 级）的第一轮评审 A——代码事实与可行性；逐条核对方案与 feature/multi-method 分支真实代码的一致性，找出遗漏机制、矛盾断言与不可行步骤。
对应 log.md 条目：2026-10-15 执行—学习闭环重构方案（scope 级）。

---

## 0. 总判

评审基线：feature/multi-method @ 69904f7，`pytest` 全绿（1927 passed, 3 deselected），全部 file:line 引用以该提交为准。

方向成立：E/L 两类阶段 + 阶段序列确实能覆盖现有 exam / live_run 两种方法的全部现有行为，"验收只测量、决策归 L"所需的全部信息今天都已持久化在盘上（Attempt 对象、各决策账本、verdict 行、冻结审计行），报告对象可以从中导出。但有 **7 条偏移约束**：其中影响最大的是①"按版本跳过＝与现有行为一致"这一断言在 exam 的 G6 预算耗尽轮不成立（会改变验收结果而不只是调用次数）、②原则 5"学习模块不调用执行"与 merge_check: exam 自相矛盾、③§4.2 配置形状无法表达 7 份正式配置里的三条具名规则名与若干方法参数、④C 的对照臂（split 模式新跑裸臂 / full 模式复用 D 弱臂）在序列记法里没有着落、⑤assemble_reuse 等对旧布局硬编码的外围机制方案只字未提。

---

## 一、逐项核对

### 1. 方法映射断言：exam = D L A L A L A L C；live_run = D L B L B L C

**exam 侧，逐环节对照 `composites/acceptance.py`（K4）：**

- 轮数：rounds 编号 0..retry_limit，最多 retry_limit+1 次带技能作答、retry_limit 次改写（acceptance.py:23-34、360-361）。`retry_limit=2` → 3 轮，与方案的 3 个 A、4 个 L（首 L 新增 + 2 个改写 L + 末 L 删除/整理）计数吻合。
- 裸答在前：先答裸题，裸答判对则 `weak_already_knows` 弃（acceptance.py:340-352）→ 对应方案 A 内自带的 A′ 空白对照。
- 泄题审计每 (skill, version) 一次、先判定后落账、泄露则该版本作废并消耗一轮（acceptance.py:367-390；末轮泄露直接 `leak_audit` 弃，378-382；非末轮作废后跳过带技能考试直接改写，383-390）。
- 带技能考试 → 判对接受（392-405）→ 末轮失败 `retry_exhausted` 弃（408-420）→ 否则 G6 改写（413-416，`_rewrite_round` 229-276，修复预算 `_REWRITE_ATTEMPTS=2`，109）。
- 驱动层：`model_output_error` 终态弃（methods/acceptance/exam.py:164-169、74）、缺考题跳过不崩（136-139）、infra 失败留 pending + 阶段末抛错（170-172、203-207）。
- C 按配置可为检索或整库：7 份 v3 配置中 delivery 有 retrieval（tb21_split_v2_v3:15、tb21_split_live_run_v3:17、alfworld_skillopt_split_v2_v3:15、tb21_split_reuse_v3:15）也有 whole_bank（tb21_full_v2_v3:13、alfworld_skillopt_split_v3:13、searchqa_skillopt_split_v3:13）。属实。

**live_run 侧，对照 `methods/acceptance/live_run.py` + `composites/live_acceptance.py`（K6）：**

- 轮数钉死两轮（live_acceptance.py:168-169 `_ROUNDS = (0, 1)`，用户裁决 8）→ 序列 B ×2 吻合。
- round 0 全组注入执行、按具名规则对 stage-1 弱臂基线判（live_run.py:517-587；judge_round 573-720）。
- 未提升 → G10 归因 + G11 修订（live_run.py:546-587；attribute_group 783-895、apply_revisions 930-1032），round 1 只测幸存者（595-618）。
- no-op guard 在 round0/round1 接缝：幸存者渲染块与 round 0 冻结审计行字节相同 → 全组 `live_no_improvement` 弃、round 1 不跑（live_acceptance.py:1133-1155；崩溃补全 live_run.py:296-327）。
- injection_audit 先写冻结、重派发前逐字节比对（live_run.py:488-503；audit_row 单一构造器 live_acceptance.py:536-562）。
- accepted shortfall 按轮落盘、判断前生效（live_run.py:806-825）。
- 零执行终态：`weak_already_knows` 入口短路（451-466，仅 strict 族规则，live_acceptance.py:177-178、634-648）、`live_injection_overflow` 派发时短路（504-514、633-641）、`live_undetermined` 全洞轮（live_acceptance.py:684-694）。

**判定：映射本身成立**，但有两点必须记录：

1. exam 现状是**每个技能在各自 worker 里跑完整 K4 循环**（exam.py:142-181 一单元一技能全循环；workers=1 时技能 1 跑完全部轮才轮到技能 2）。方案把它重排成全局轮相位（A1 测所有技能 → L1 改写 → A2…）。我核对过跨技能无耦合：泄题判定键 (skill,version)、作答键 (skill,version,round)、改写只读本技能自己的 attempt 与审计行——重排不改变每技能的调用集与终态，属良性重排；live 本来就是轮屏障（驱动 docstring 的 ruling A7）。但 golden 是**逐字节快照**（tests/golden/harness.py:10-14），重排必然打翻字节快照——见偏移约束 A7。
2. 序列里的 A/B 相位是"上界调度"而非线性执行：A2 只测 A1 失败且被改写的技能，B2 只测幸存组（no-op 组零执行）。方案 §3.1 的"只测变化的部分"承担了这个语义，但该断言本身有问题（见第 3 项与 A1）。

### 2. "验收只测量、决策归 L"：库状态变更动作清单与信息可得性

现有全部"改变技能库状态/内容"的动作：

| 动作 | 位置 |
|---|---|
| 接受（accepted） | acceptance.py:334-337、402-404；live_acceptance.py:1204-1207（finalize_group） |
| 弃（weak_already_knows / leak_audit / retry_exhausted） | acceptance.py:349-351、378-382、408-420 |
| 弃（model_output_error） | exam.py:164-169；live_acceptance.py:857-868（G10 预算耗尽）、1082-1093（G11 预算耗尽） |
| 改写（版本+1、rewrite_history、内容） | acceptance.py:267-275；live_acceptance.py:1101-1108 |
| G10 计划的 keep/delete/revise 落地 | live_acceptance.py:1003-1031（delete→discard 1007-1010） |
| no-op guard 全组弃 | live_acceptance.py:1153-1154；崩溃补全 live_run.py:319-327 |
| 零执行终态落账+提交 | live_run.py:461-466、508-513、635-640 + finalize_group |
| K5 合并提交（sources→merged、merge→accepted / →discarded） | curation.py:360-375（_apply_kept/_apply_reverted）、989-1011（commit） |
| K5 回归不通过撤销合并 | curation.py:1187-1203（kept/reverted 判定） |
| G9 分组标签写入 skill.group | curation.py:1239-1244 |
| 写库（append_bank） | curation.py:1272-1273 |

**决策所需信息是否都能在"验收报告"接口拿到**：能，但前提是报告必须显式承载下列载荷——它们今天分散在 Attempt 字段与各账本里，不是现成的"报告"形态：

- 判卷理由与弱模型答案：`Attempt.judge_reason` / `Attempt.weak_answer`（core/models.py:286-289）——G6 改写必需（acceptance.py:413-415 直接把 `attempt.judge_reason` 当 error_description）；
- 泄题判定与理由：`leak_verdicts.jsonl`（acceptance.py:94-98、214-226 的 `_void_error_description` 还要拼上来源 attempt 的判卷理由）；
- live verdict 行（baseline/live 计数、better、aux、injection_tokens、judged_with_shortfall、零执行终态）：live_acceptance.py:723-749；
- 冻结注入审计行（sha + tokens）：live_acceptance.py:536-562——no-op guard 的字节等价证据就是"当前渲染块 sha vs 冻结行 sha"这个比较结果（1133-1155），必须作为测量进报告；
- G10 归因计划（diagnosis + 每技能 problem + version）：live_acceptance.py:878-891；
- G6/G11 修复预算耗尽标记：`rewrite_failures.jsonl`（acceptance.py:100-103）、`revision_failures.jsonl`（live_acceptance.py:132）——决策规则要靠它区分"改写失败但还有轮"与"改写成功"；
- 失败处置类决策的输入（测量失败本身）：ModelOutputError 终态弃 vs InfrastructureError 留 pending + 阶段末抛错（exam.py:164-172；live_run.py:585-587），这套处置语义在方案里没有出现，必须作为决策模块的失败处置规则原样保留，否则 golden 与续跑契约都翻。

**哪些信息目前根本不是"报告"形态**：K5 的回归考试结果——它只存在于 Attempt 对象（purpose="regression"，curation.py:751-776）与 curation journal 的 kept/reverted 记录里，没有任何"验收报告"载体。但这不构成跨阶段缺口：按方案 §3.2，整理（agentbrew）整体在 L 内部，回归测量与撤销决定同住 L，不需要跨阶段报告——前提是偏移约束 A2 先解决（L 内部要跑答题判卷）。

### 3. "只测变化的部分"（按版本跳过）与现状的对应

- K4 的 `_next_round`（acceptance.py:279-284）：已完成轮 = (version−1) + 耗尽的改写轮。版本提升是"改写成功关轮"的证明；**G6 修复预算耗尽也消耗一轮但不提版本**（acceptance.py:250-276，docstring 26-30"the next round re-audits the unchanged content and tries the rewrite again"）。而带技能作答的复用键是 **(skill_id, version, retry_round)**（acceptance.py:182-193、392-401）：同一版本进入下一轮时**会重新采样一次带技能作答**，不是沿用上一轮的判卷结果。
- K6 的重派发集：非终态 + round store 不完备（skip-aware）（live_run.py:231-240、479-515）——这是续跑语义，与"沿用报告行"等价；轮间接缝上的 no-op guard 是"注入字节相同→不再执行"（live_acceptance.py:1133-1155）。

**判定：方案的"与现有行为一致"不成立，见偏移约束 A1。** live 侧（no-op guard + 轮仓完备性）大体吻合；exam 侧的 G6 耗尽轮是反例，且 live 的 guard 是**字节**判据、方案括号里写的是**版本**判据，两者在一个角落分歧：G11 改写返回逐字节相同文本时版本已 +1（live_acceptance.py:1101-1108）但渲染块不变——现状弃组（guard 命中），按版本判据则应重测。

### 4. 技能身份与段解绑（§5 原则 6）：ID 链载荷点枚举

skill→subtask→trajectory→task 链的全部载荷点：

| 载荷点 | 证据 |
|---|---|
| skill_id = subtask_id + "/skill" | core/ids.py:88-89；确定性契约 ids.py:20-37 |
| "stage-3 候选"判定 = ID 形状 | ids.py:111-117（is_stage3_candidate） |
| exam_id = subtask_id + "/exam"，考题与技能只靠 subtask_id 关联 | ids.py:92-93；core/models.py:255-257（Exam 刻意不带 skill 引用） |
| attempt_id 链自 exam_id | ids.py:96-99、177-185 |
| live 按任务分组：从 subtask_id 反解 trajectory→task | live_acceptance.py:365-372（_task_of_skill）、400-426（pending_groups）；live_run.py:198-210（_group_members） |
| K4 只收 stage-3 候选（ID 形状守卫） | acceptance.py:312-319；skill_status.py:37-45（stage3_candidates） |
| K5 合并产物锚点 = sources[0].subtask_id；回归考题从 merged_from 递归解析原件的 subtask_id 取 | curation.py:1174-1184（锚点）、659-684（_original_sources）、687-709（_source_exams 用 make_exam_id(source.subtask_id)）；合并产物版本恒 1 的守卫 384-393 |
| stage3 候选枚举与 (skill,exam) 配对 | stage3_generation.py:223-225（make_skill_id/make_exam_id）、197-205 |
| 装配复用全链溯源 | assemble_reuse.py:201-268（skill_lineage_tasks：subtask.task_id + merged_from 递归）、1169-1171（kept_exams 按 subtask_id 过滤） |
| 检索/注入记账 | blocks/retrieve.py:100-107（读 skill.group/content/skill_id）；delivery/retrieval.py:84-105（selection 引用） |
| stage6 注入审计 | stage6_test.py:316-321 + delivery/retrieval.py:107-135（retrieval_audit）；whole_bank 记 bank digest（delivery/whole_bank.py:22-27） |

**"技能带来源字段、考题挂技能"覆盖大部分载荷点，但不完整，见偏移约束 A6**。要点：

- **Skill 需要的字段变更**：加来源（轨迹/段指针）；**还要加显式的 origin/kind**——`is_stage3_candidate` 是 ID 形状判定，被 pending_groups、stage3_candidates、K4 守卫三处用作"新写候选 vs 合并产物"的区分器，技能 ID 独立后形状判定失效，必须换成显式字段；merged skill 的锚点语义（K5 写 sources[0] 的 subtask_id 且版本恒 1）与 `_check_merged_versions` 要重新定义。两者 schema 升版。
- **Exam 需要的字段变更**：挂 skill_id（替代 subtask_id 关联）；exam_id 改从 skill_id 派生，attempt_id 链跟着变——K4 的按 ID 续跑（acceptance.py:147-154）与 K5 的 `_stored_regression_attempts`（curation.py:751-776，按 attempt_id 解析 serial）都是 ID 解析消费方。
- **消费方需跟着改**：ids.py（含确定性说明的重申——新 skill_id 方案必须保住"同输入同 ID"的续跑前提）、models.py、exam 驱动的考题解析（exam.py:136）、K4 守卫、K6 分组、K5（_source_exams 改为沿 merged_from 收集各原件挂的考题）、stage3、assemble_reuse（lineage 与 kept_exams）、tests/fixtures/golden 全部重生成。
- **一个方案没回答的语义问题**：live 的 B 阶段"同任务技能打包给"按任务分组；技能与段解绑、来源可能跨多条轨迹后，"这条技能属于哪个任务"不再唯一，分组依据必须另行定义（按来源任务集合？按主来源？）。

### 5. 出题挪进 A（伪执行）：可行性与"exam 行为不变"

现状：`gen_exam` 的输入是 subtask.label / subtask.description / steps_view（blocks/author.py:377-421，变量 392-396），steps_view 由 subtask.step_range 从 Trajectory 切片后经 `render_steps_view(…, plugin.view_rules)` 渲染（stage3_generation.py:246-249）；EXAM_GEN 模板只吃 {label, description, steps_view}（prompts/exam.py:20-30）。出题时看不到技能，考题与技能只通过 subtask_id 关联（models.py:255-257）——方案描述属实。

**可行性：成立**。链路：A 拿到技能 → 技能的来源指针 → subtask（label/description/step_range/trajectory_id）→ 读 Trajectory → render_steps_view → 以相同变量、相同 EXAM_GEN prompt、相同 examiner 角色句柄调 `gen_exam`。模型调用参数可做到逐字节一致（变量都是 subtask+轨迹+view_rules 的纯函数）。前提：来源指针必须能唯一定位 (subtask, step_range)。

**对"exam 方法行为不变"的影响**：

1. **调用时点后移**（stage3 → A）：逐字节的 golden 快照必然翻（llm_requests 落盘顺序、pipeline_log 阶段归属都变）；方案 §6 用"模型调用多重集"口径可以保住语义等价——但现有 golden 是字节快照，语义比较器尚不存在（见 A7）。
2. **出题失败语义变化**已在 §6 披露且结论成立：现状 G3 失败时整单元作废、skill 不落盘（stage3_generation.py:270-279"failed unit leaves nothing behind"、284-287）；新流程 skill 已存在、由决策删除，最终库相同，G2/G3 的调用多重集不变（现状 G3 失败时 G2 的调用也已发生）。附带消失的机制：`skipped_missing_exam` 跳过（exam.py:136-139）与 `exam_missing_advanced`（stage3_generation.py:236-242）两类修补路径——迁移时这两个分支及其测试一并清理即可。
3. **live 侧**：`authoring.exams` 开关删除后 live 不再出题，与 §6 变化①②一致。但注意 **§3.3"live 全程不出题"是目标态而非现状**：正在 TB-dev 续跑的 live 正式配置 `authoring.exams: true` 且 `curation: agentbrew`（configs/experiments/tb21_split_live_run_v3.yaml:9,11），考题在 stage 3 生成、K5 回归在消费。方案把它写成现在时，属表述错误（琐细 S3）。

### 6. stage6 对照臂：D/C 序列记法是否覆盖

现状（stages/stage6_test.py）：

- split 模式：C 自带一条**新跑的裸臂**（`fresh_bare_run`，332-350，独立 store `stage6_test/bare`）——这本身就是一次"不给技能 × 真做题"的执行，发生在 test 集上，而 D 只在 train 上跑过，序列里没有对应它的相位；§4.2 示例的 `compare: D_test` 在 split 模式下无实物可指。
- full 模式：**复用 D（stage 1）的弱臂**（`stage1_reuse`，325-331），且只复用弱臂、连 accepted holes 一起继承（279-284），强臂永不进 stage 6（模块 docstring 3-5）。
- 对照臂的配套落盘：每臂独立 store、每臂 accepted shortfall 记录（112、359-371）、delta 汇总（394-405）。

**判定：序列记法没有覆盖这一差异，见偏移约束 A4。**"对照臂的验收报告去向"也漏了：C 的报告必须同时携带 with-bank 臂与对照臂的测量、两臂的 infra_missing/accepted 记录及 delta，否则 D3 的"同分母两臂"纪律（stage6_test.py:50-57、382-387）无处安放。

### 7. 方案没提但代码里存在的机制（逐个判归宿）

| 机制 | 证据 | 新框架归宿（我的判定） |
|---|---|---|
| aux metrics（D3 aux，预注册次级指标） | stage1_baseline.py:225-229；stage6_test.py:376-391、229-244；live verdict 行 aux 槽 live_acceptance.py:752-776（用户裁决 11）；manifest 冻结名字 stage0_freeze.py:265 | 验收模块的测量输出：报告行/摘要带 aux 计数；manifest 的 `aux_metric` 预注册不变。方案漏提，必须写进报告契约 |
| F 失败任务集推导 + task_select / segment_select / trajectory_select 三条具名规则 | stage1_baseline.py:233-241（F）；stage2_segmentation.py:188、196-202；stage3_generation.py:195、203-205；core/config.py:61、69-73（RULE_KEYS/DEFAULT_RULES）；stage0_freeze.py:181-185（加载期校验） | task_select 属 D 的派生列（对弱臂测量套规则得 F，随报告走）；trajectory_select/segment_select 属 L 的找证据/写作参数。三条规则名必须出现在新配置 schema 与 manifest（见 A3） |
| stage1 shortfall 与 accepted holes（贯穿 K1/K6/stage6） | stage1_baseline.py:217-224（durable 先于 F）；live_acceptance.py:508-529（skip_ids_for 读 stage1 holes）、462-465；live_run.py:806-825（按轮记录）；stage6_test.py:359-371（每臂记录）、279-284（full 复用继承）；cli.py:301-308（运行参数） | 每个执行相位的持久产物（accepted-hole 记录），比较型验收（B、C 的 full 复用）跨相位读取。属"轨迹集"载荷的一部分，方案漏提，见 A5 |
| assemble_reuse.py（装配复用） | 硬编码阶段目录：assemble_reuse.py:148-165（负清单含 stage3_generation/…、stage4_acceptance/…、stage5_curation/…）、471-472、1244-1245、1286-1288；身份白名单引用槽键 `config.methods.delivery`（121-127）；v2 升级路径 1032-1040；lineage 201-268 | 方案只字未提。序列驱动布局 + 配置 schema v4 下它必然失效，而 tb21_split_reuse_v3 是 7 份正式配置之一。必须在 M4 里决定：改写（目录映射 + 身份白名单对 phases+sequence 重述 + lineage 改读来源字段）或声明复用实验钉死在旧布局，见 A5 |
| scripts/audit/audit_live_run.py | LIVE_SUBTREE = stage4_acceptance/live_run（86）；STAGE1_SHORTFALL（93）；failed_units 标签 "stage4_live"（499-500） | 读旧布局与阶段标签，新布局下断。归宿：随 M4 重指向新布局，或按 AGENTS 六.1 冻结给旧 run 用、新 run 配新审计——二选一要写明，见 A5 |
| cli.py until_stage 与 pipeline_log 阶段账本 | pipeline.py:61-69（STAGE_NAMES）、229-232（until_stage 0-6 硬编码）、391-415；cli.py:232-257（summary 按 STAGE_NAMES 打印与完整性检查） | until_stage → until_phase（序列序号）；账本键从固定 stage 名改为相位实例（序列内 A×3 需 (名字, 序号) 键）；summary 的完整性清单按序列生成。方案漏提，见 A5 |
| ROLE_DEFAULTS 角色表与 weak_profile | core/config.py:102-113（10 角色）；pipeline.py:153-189（逐角色建句柄、查预算）；weak_profile 消费点：acceptance.py:255/414、stage3_generation.py:265、live_run.py:568/579、curation.py:198-200（签名统一、实际不用） | 全局 config 字段原样保留，相位配置只引用角色名；manifest 的 model_roles 继续冻结。方案说"模型是执行的参数"，但 10 角色表这个冻结面没提（琐细 S5） |
| token_budgets | core/config.py:125-128、260-321、475-495（fail-closed）；pipeline.py:166-187 | 同上：全局字段保留，按角色名解析。方案未提（并入 A3 的配置表达力清单） |

### 8. 配置表达力：§4.2 能否无损表达 7 份 v3 配置

7 份配置的共同载荷：benchmark、mode、10 角色模型表、model_options（reasoning_effort: max）、n_attempts 3/3、token_budgets（default 65536 + 10 角色条目）、weak_profile、split 三列表（train/test/reserved）、tasks、schema_version 3、rules 三名、methods 五槽。逐份差异只在：acceptance（live_run 仅 tb21_split_live_run_v3:7，其余 exam）、delivery（retrieval vs whole_bank）、规则名组合、mode（full 仅 tb21_full_v2_v3）。

**判定：§4.2 的形状按字面无法无损表达，见偏移约束 A3。** 缺的槽位：

1. **三条具名规则名**（所有 7 份都显式写了，且 manifest 冻结 `rules`，stage0_freeze.py:258）——§4.2 的 D 和 L 配置里都没有位置；
2. **segmentation 参数**（K: 5、retry_limit: 2，7/7 份都有，如 tb21_split_live_run_v3.yaml:20-21）——L 的 `evidence: segment` 不带参数；
3. **curate 参数**（token_budget: 96000、rounds: 3，7/7 份）——示例 L 的 `curate: {name: agentbrew, merge_check: none}` 缺这两个；
4. **token_budgets / model_options / weak_profile / 角色表**（7/7 份）——全局字段在 phases+sequence 里没说存续；
5. **reserved 列表与 mode**——D/C 的 `tasks: train/test` 隐含 split，但冻结划分的第三列表（TB 20 个 reserved 任务）与 full 模式（train==test）没有表达；
6. **D 的双 n_attempts**（n_attempts_strong/weak 各自独立，stage1 两臂深度不同）——示例 D 只有 `models: [strong, weak]`；
7. **C 的对照臂配置**（见 A4）与 **live 的跨约束**（n_attempts ≤ n_attempts_weak，methods/acceptance/__init__.py:73-90，需保留为加载期检查）。

tb21_split_reuse_v3 单独看：它本身是一份普通的 exam 方法配置（assembly 发生在管线之外，cli assemble-reuse），所以"配置能表达"没问题；真正的问题是装配工具本身（A5）。

### 9. §3.2 "只有一种 L"：三种输入形态对照代码

- **第一个 L**（stage2+3）：输入 = 基线轨迹 + F，无报告 → 全部"新增"。对照：stage2 按 trajectory_select 选轨迹切段（stage2_segmentation.py:196-202），stage3 按 segment_select 选段写技能（stage3_generation.py:195-205）。吻合。
- **中间 L**：exam = 读判卷理由/泄题判定改写（G6）；live = 读 verdict 行跑 G10 找证据 + G11 修订 + keep/delete/revise 计划落地。吻合。注意 G10 的模型输出**本身就带 skill_actions（keep/revise/delete，blocks/attribution.py:54）**——"决策"由模型在找证据时一并给出，决策模块的规则实际是"照单采纳归因计划（含版本回放守卫 live_acceptance.py:1013-1023）"，方案应把这点点名，否则"决策（门）读报告定动作"听起来像决策模块自己发明动作。
- **最后 L**：删除 + 整理。exam：末轮失败/末轮泄露/裸答已会/model_output_error 的技能删除（acceptance.py:349-351、378-382、408-420；exam.py:164-169），随后 K5；live：round-1 miss 或 no-op 的幸存者删除（live_acceptance.py:1188-1194、1153-1154），随后 K5。吻合。
- **"最后一轮直接删是同一规则的分支"**：成立——现状 exam 的末轮判定就是 `last_round = retry_round == retry_limit`（acceptance.py:361），live 是"还有没有 round-1 verdict 行"（live_run.py:595-605）。
- **leak-voided 轮怎么表达**：可以表达，不需要第三种 L——A 的报告行是"该 (skill,version) 泄题、无带技能作答、本轮消耗"（相位槽位即轮预算，序列长度替代 retry_limit 自动记账）；L 的决策 = 作废该版本 + 以审计报告（拼上来源 attempt 的判卷理由，acceptance.py:214-226 的既有配方）触发改写。但改写的输入（来源 attempt 的 weak_answer/judge_reason）必须进报告（第 2 项清单）。
- **真正的第三形态风险不在 leak，在 G6 预算耗尽轮**：改写失败、版本不变、内容不变，现状下一轮**重新采样**带技能作答（第 3 项），而"按版本跳过"会把它跳掉——这不是报告形态问题，是 A1 的行为差异。

---

## 二、发现分级

### 偏移约束（不修不能进实施）

**A1. "只测变化的部分＝按技能版本判断，与现有行为一致"不成立。**
现状 exam 的带技能作答复用键是 (skill_id, version, **retry_round**)（acceptance.py:182-193、392-401）：G6 修复预算耗尽的轮不提版本（acceptance.py:250-276、`_next_round` 279-284），同一版本进入下一轮会**重新采样**一次作答——重采样可能翻盘接受，现状真的在重跑。按版本跳过会（a）改变模型调用多重集，（b）改变验收结果（耗尽改写预算的技能永远无法靠重采样翻身）。live 侧判据也拧着：no-op guard 是**字节**等价（live_acceptance.py:1133-1155），G11 返回逐字节相同文本时版本已 +1（1101-1108），按版本判"变了"、按字节判"没变"，现状按字节弃组。
**建议修法**：把"只测变化的部分"改写成按测量类型逐一钉死的跳过键——裸答每考题一次；泄题判定每 (skill, version) 一次；带技能作答每 (skill, version, **轮序号**) 一次；live 以轮仓完备性 + 冻结审计行为准（字节判据）；或者明示两处行为变化（G6 耗尽轮不再重采样、no-op 判据改版本）并列入 §6 与 M4 验收对照表。二选一，不能留"与现有行为一致"这句话。

**A2. 原则 5"学习模块不调用执行"与 §3.3 merge_check: exam 自相矛盾。**
merge_check: exam 的回归检验必须在合并之后、于 L 内部现场跑"弱模型答题 + 判卷"（现状 K5 直接调 exam_once/K3，curation.py:789-809）——这正是方案定义的伪执行（弱模型答题、判卷）。§3.3 的加载期检查只覆盖"序列里有 A（有考题）"，没回答"谁跑答题判卷"；合并产物在 L 里才诞生，A 不可能预先测它。
**建议修法**：把原则 5 改为"L 不发起真执行、不跑 benchmark、不重复交付；K3 答题判卷原语由 A 与整理的 merge_check 共享"，并在 §3.3 写明回归考的执行者是整理模块调用的共享原语（对应现状 composites 调 composites 的既有形态）。

**A3. §4.2 配置形状无法无损表达 7 份正式配置；M4"转换脚本"缺少验收基准。**
缺失槽位（证据见第一节第 8 项）：三条具名规则名（7/7 份都写、manifest 冻结 rules，stage0_freeze.py:258）、segmentation 的 K/retry_limit（tb21_split_live_run_v3.yaml:20-21 等 7/7）、curate 的 token_budget/rounds（7/7）、token_budgets/model_options/weak_profile/角色表、split 的 reserved 列表与 mode、D 的双 n_attempts、C 的对照臂配置、live 的 n_attempts ≤ n_attempts_weak 加载期检查（methods/acceptance/__init__.py:73-90）。
**建议修法**：方案补一节"配置字段映射表"：phases+sequence 之外哪些留在全局 config（角色表、预算、profile、mode/split/reserved）、每类相位配置的完整键清单（D: task_select、n_attempts_strong/weak；L: evidence/author 的全部参数含 segment_select/trajectory_select/K/retry_limit；A/B/C: 各自规则与参数；C: control 子句）。M4 的验收加一条：7 份 v3 配置逐份转换后语义等价（冻结字段逐一对照）。

**A4. C 的对照臂不在序列记法里，对照臂的报告与落盘去向未定义。**
split 模式 C 自带一条新跑裸臂（stage6_test.py:332-350，"不给技能×真做题"在 test 上的一次执行，序列中没有它的相位；`compare: D_test` 在 split 下无实物），full 模式复用 D 的**弱臂**并继承其 accepted holes（325-331、279-284）。对照臂还有自己的 store、每臂 accepted shortfall 记录与 delta（112、359-371、394-405）。
**建议修法**：C 的阶段配置增加对照子句（如 `control: {source: fresh_bare | reuse_d_weak}`），并写明对照臂的两臂同分母纪律（stage6_test.py:50-57）、per-arm shortfall 与 delta 都落 C 的阶段目录、进 C 的报告。

**A5. 方案遗漏一批对旧布局/旧 schema 硬编码的现存机制，未指定归宿。**
(a) assemble_reuse：负清单与拷贝清单硬编码 stage 目录名（assemble_reuse.py:148-165、1244-1245、1286-1288），身份白名单引用槽键 `config.methods.delivery`（121-127），lineage 沿 ID 链（201-268）——序列驱动布局 + v4 配置下全部失效，而 tb21_split_reuse_v3 是正式配置之一；(b) scripts/audit/audit_live_run.py 读 `stage4_acceptance/live_run` 子树与 "stage4_live" 标签（86、93、499-500）；(c) cli 的 until_stage（0-6 硬编码，pipeline.py:229-232）与 pipeline_log/summary 的 STAGE_NAMES 账本（pipeline.py:61-69、cli.py:232-257）；(d) accepted shortfall/holes 的跨相位契约（B 读 D 的洞、C-full 继承、每轮/每臂 durable 记录，live_acceptance.py:508-529、stage6_test.py:359-371）；(e) 失败处置语义（ModelOutputError 终态弃 vs InfrastructureError 留 pending + 阶段末抛错，exam.py:164-172）。
**建议修法**：方案加"现存机制清单与归宿"一节，逐项写明：改写（assemble_reuse 的目录映射 + 身份白名单对 v4 重述、audit 脚本重指向、until_phase/账本键含相位序号、shortfall 记录成为 E 相位标准产物）或冻结（钉在维护分支只服务旧 run），并给每项归宿挂到 M1–M4 的某一步。

**A6. §5 原则 6 一句话低估了"解绑"的改动面。**
除"技能带来源字段、考题挂技能"外，还必须：给 Skill 加显式 origin/kind（`is_stage3_candidate` 是 ID 形状判定，ids.py:111-117，被 pending_groups/stage3_candidates/K4 守卫三处消费，ID 独立后失效）；重新定义合并产物的锚点与"版本恒 1"守卫（curation.py:1174-1184、384-393）；定义多来源技能在 live"按任务打包"下的分组依据（现在 = 从 subtask_id 反解的唯一任务，live_acceptance.py:365-372）；exam→attempt 的 ID 链变更波及 K4 续跑与 K5 回归 attempt 解析（curation.py:751-776）；新 skill_id 方案必须重申 ids.py:20-37 的确定性契约（续跑靠 ID 判完成）；golden 全量重生成；assemble_reuse 的 lineage/kept_exams 改读新字段。
**建议修法**：§5.6 扩成小节：Skill/Exam 的字段级变更清单 + 消费方清单 + 分组语义裁决 + ID 确定性重申，作为 M1 的验收输入。

**A7. M4 验收"exam golden 语义一致"引用了一个尚不存在的仪器。**
现有 golden 是**逐字节**快照（tests/golden/harness.py:10-14："reproduces these snapshots byte-for-byte"），连 pipeline_log、阶段摘要、llm request dumps 都逐字节比对。相位化必然改变目录布局与调用时点（第 1、5 项），字节比较必翻；"按语义（最终库、测试指标、模型调用多重集）核对"的比较器需要先建（多重集可从 llm_requests 落盘构造）。
**建议修法**：M1 或 M2 里加一步"golden 语义比较模式"（最终库、stage6 指标、(role, prompt, variables) 调用多重集、终态计数），作为 M4 的实际验收仪器；字节快照继续服务 M1–M3 中不翻布局的步骤。

### 琐细

- S1. §3.2 整理行的"现有实现"列把 `merge_check: exam 或 none` 写成现成可选——`merge_check` 参数今天不存在，K5 对两种方法一律跑回归考（curation.py:789-813），`merge_check: none` 是 M3 要新造的（方案 §8 自己说了"agentbrew 加 merge_check"）。表述为"目标实现"即可。
- S2. §2 说"考题是某个模块的私有产物、不进阶段间接口"，§5.6 又说"考题挂在技能上"（技能库是跨阶段对象）且 §5.5 承认合并检查跨阶段取考题——三处口径不一。建议统一为"考题由 A 产出、经技能引用跨阶段可见"。
- S3. §3.3"live 全程不出题"是目标态：现状 live 正式配置 `exams: true` + agentbrew（tb21_split_live_run_v3.yaml:9,11），stage3 出题、K5 回归消费。应改写为"live 目标态全程不出题（§6 变化①②）"。
- S4. §7 说"合并 K5 的主体不动"，与 §6②/M3"agentbrew 加 merge_check（回归考变条件）"打架；K4 也被 M2/M3 拆分。措辞收敛为"K1/K2/K3 与 K5 的合并/分组/预算逻辑主体不动"。
- S5. 方案未提 10 角色表（ROLE_DEFAULTS，config.py:102-113）、weak_profile、token_budgets 的存续——应明确这些留在全局 config 并继续进 manifest（model_roles、config 全量）。
- S6. stage0（任务加载、划分推导、manifest 冻结、aux_metric 预注册）在"方法=阶段序列"之外，方案未点名其存续；建议在 §7 补一句"运行装配（冻结/划分/锁/用量）不属于方法序列，原样保留"。
- S7. v3→v4 配置转换要注意现有 v2 拒载与升级路径（config.py:539-596 upgrade_v2_config、stage0_freeze.py:289-302 的 v2 拒载提示）的对应物：v4 的 from_dict/load_config 拒载逻辑与转换脚本要成对出现。

### 赞同（核实成立的关键断言）

- 方法映射计数成立：exam 的 retry_limit=2 → 3 轮（acceptance.py:23-34、360）；live 两轮钉死（live_acceptance.py:168-169，ruling A7 的轮屏障）。
- K4 循环顺序与方案描述逐环吻合（裸答→weak_already_knows→泄题每 (skill,version) 一次、泄露作废消耗一轮→带技能考→G6→retry_exhausted，acceptance.py:340-420）。
- K6 全流程吻合：round0 全组→G10+G11→round1 幸存者→再败弃；no-op guard；injection_audit 先写冻结 + 重派发逐字节比对；accepted shortfall（live_run.py 全文核对）。
- 交付五档（不给/单技能/按任务组/检索/整库）与"两份注入逻辑"的指控属实：delivery 槽只在 stage6 消费（stage6_test.py:316-321），live 自建解析器（live_run.py:368-392）。
- "验收只测量"所需的全部决策信息今天都已持久化（Attempt 字段、leak_verdicts、verdicts/attributions/injection_audit 账本），报告对象可导出；K5 回归结果是唯一"非报告形态"的信息，但它本就住 L 内部。
- 出题链路回溯可行且模型调用参数可保持一致（gen_exam 变量 = subtask+steps_view 纯函数，author.py:377-421、prompts/exam.py:20-30）；出题失败语义变化（§6）的"最终库相同"结论成立。
- "切段写死进技能身份"（ids.py:88-89）、"阶段 0–6 展开一圈没接回去"（stage6 轨迹无任何学习阶段消费）均属实。
- "整理只在最后一个 L"复现现状（K5 单次运行于 stage 5）；merge_check 两版的格式检查与预算硬阀在 K5 均已存在（curation.py:722-748、1134-1158、1256-1267）。
- 配置加载期依赖检查（merge_check: exam 但无 A → 报错）有同构先例且可行（authoring 槽的 exams 冲突检查，methods/authoring/__init__.py:26-45）。
- 续跑契约不变 + 按序列序号组织子目录与"序列进 manifest 冻结"兼容（现状 manifest 冻结全量 config，stage0_freeze.py:251-266）。

---

## 三、结论

方案通过 E/L 重组在语义上能覆盖现有两种方法，可以进入实施；但 A1（跳过判据与现状不等价——它会改结果而不只是改调用）与 A2（原则 5 与 merge_check 自相矛盾）是设计级修正，A3–A6 是实施前必须补齐的规格（配置映射、对照臂、机制归宿、数据对象变更面），A7 是 M4 验收仪器的先行项。修正这些不改变方案方向。
