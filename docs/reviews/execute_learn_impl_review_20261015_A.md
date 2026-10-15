日期：2026-10-15
一句话说明：执行—学习实施方案的第二轮评审 A——正确性核查（§2 字段表、§3 接口与规则清单、§4 schema 映射、§5 失败处置、M2/M3 golden 逐字节约束、M4 相位覆盖、E1–E5 可计算性与第一轮 A1–A7 落实，逐项对照 feature/multi-method @ bb0935f 的代码事实）。
对应 log.md 条目：2026-10-15 执行—学习闭环实施方案（第一轮三路评审后）。

---

## 0. 总判

评审基线：feature/multi-method @ bb0935f（docs-only 提交，代码与 69904f7 相同）；`tests/composites/test_acceptance_resume.py + test_live_acceptance_resume.py` 在 HEAD 实测 236 passed；golden fixture 树 69904f7 与 HEAD 无 diff（`git diff --stat 69904f7 HEAD -- tests/fixtures/golden` 为空），"69904f7 基线"指称成立。

方向与主体成立：§2 两个字段对七类消费方覆盖完备（§一.1 逐个验证）；§4 映射表对 7 份 v3 配置的每个 methods 子键都有 v4 归宿；§5 的"现行"列与代码逐行吻合；M2/M3 的逐字节目标除一处门的表述外可行；A1–A7 全部有 v2 §10 裁决且基本落实。但有 **7 条偏移约束**，集中在四处：①§3 的一处定义写错计数对象（"还有后续验证轮"数的是 learn 相位）、②§3/§5 漏收 live 侧 G10/G11 预算耗尽处置（model_output_error 在 live 词汇表里有、清单里没有）、③§4 的 D 双臂采样深度缺省与 stage1 现状冲突（A3 第 6 点只落实一半）、④M4 相位目录没有回答对象仓（Store KIND_DIRS）搬不搬——这是唯一可能造成续跑语义断裂的布局决定；外加 M2 验收门自相矛盾、E2 对 golden 不可达分支是空验证、读取器两处行规格会误导实现。

---

## 一、逐项核查

### 1. §2 字段表充分性（Skill.source_subtasks + Exam.skill_id 是否覆盖每个消费方）

逐个消费方验证（`grep` 全仓 ID 链消费点后逐一读码）：

| 消费方 | 现行依据 | 新字段下 | 判定 |
|---|---|---|---|
| live `pending_groups` 按任务分组 | `live_acceptance.py:365-372`（`_task_of_skill`：subtask→trajectory→task）、400-426（候选过滤） | `is_stage3_candidate` 仍按 ID 模式（ids.py:111-117），进组的恒为单源段生技能，source_subtasks 恒为 `[自己的 subtask_id]`，读字段或读 ID 链等价 | 覆盖；合并产物永不进组 |
| exam 驱动考题解析 | `methods/acceptance/exam.py:136`（`make_exam_id(skill.subtask_id)`） | 改读 `Exam.skill_id`，exam_id 派生不变 | 覆盖 |
| K5 回归考题来源（merged_from 递归） | `composites/curation.py:659-684`（`_original_sources` 沿 merged_from 递归原件）、687-709（`_source_exams` 用 `make_exam_id(source.subtask_id)`） | source_subtasks 并集 ≡ 原 `merged_from` 递归到原件的 subtask_id 集合（合并产物=各来源并集，逐层并集=原件集合），`_source_exams` 可改为按 skill_id 取原件考题 | 覆盖且更简 |
| blocks/retrieve.py | 全文只读 `skill_id`/`content`/`group`（retrieve.py:80-107），无 ID 链依赖 | 不变 | 无需字段 |
| stage3 候选枚举 | `stage3_generation.py:223-225`（make_skill_id/make_exam_id） | M4 后归 L/A；ID 规则不变 | 覆盖 |
| stage6 注入审计 | `stage6_test.py:316-321` + `delivery/{whole_bank,retrieval}.py`（只读 bank） | delivery 不变 | 无需字段 |
| gen_exam 输入（subtask 证据回溯链） | `blocks/author.py:377-421`（变量 = subtask.label/description/steps_view）；steps_view 由 subtask.step_range 切 Trajectory 渲染（stage3_generation.py:246-249） | `Skill.subtask_id` 字段保留（§2"其余字段不变"），A 沿 subtask_id → Subtask → trajectory_id → Trajectory → render_steps_view 重建，逐字节可复现 | 覆盖 |

其余 ID 链消费点（`composites/acceptance.py:312-319` 的候选守卫、`skill_status.py:37-45`、`live_run.py:198-210`、`assemble_reuse.py:201-268,1169-1171,1345-1349`）全部因 ID 规则不变（D1）而继续可用。**判定：字段表充分，无消费方需要更多字段**。两点备注见琐细 T1/T10 与偏移约束 P7（§3 读取器行规格，非 §2 问题）。

### 2. §3 决策规则清单完备性（对照 K4 与 K6 的全部终态与 discard_reason）

K4（`composites/acceptance.py`）终态：accepted（334-338、402-404）、weak_already_knows（346-352）、leak_audit（377-382）、retry_exhausted（408-420）、（驱动层）model_output_error（`methods/acceptance/exam.py:164-169`）。K6（`composites/live_acceptance.py:62-65`）词汇表：weak_already_knows / live_no_improvement / live_revision_deleted / **model_output_error** / live_undetermined / live_injection_overflow。

§3 exam 清单 8 条与 acceptance.py:340-420 逐条对应，**含**改写预算耗尽（→版本不变、轮已消耗、下个 A 重答同版）。§3 live 清单 7 条收录了其余 6 个理由中的 5 个 + keep/revise/delete 计划 + no-op guard，**唯独缺 model_output_error**（G10 归因预算耗尽→整组弃，live_acceptance.py:851-874；G11 修订预算耗尽→仅该技能弃、组继续，1081-1093；两者都是"终态先写、exhausted 账本行是回执"的次序）。§5 失败处置表 6 行也没有对应行。→ **偏移约束 P2**。

"还有后续验证轮"的定义（§3 末条）写成"该 L 之后还有几个 **learn 相位**"，语义应为"该 L 之后还有没有**验证类执行相位**（A/B）"——对照 exam 现行末轮判定 `retry_round == retry_limit`（acceptance.py:361）与 live 的 round-1 候选判定（live_run.py:597-605），数的是验证轮不是学习轮；对 schema 未禁止的序列（如 [D,L,A,L,A,C]）两者数值分歧。→ **偏移约束 P1**。

"改写预算耗尽→版本不变→下轮重答同版"的等价性：v3 靠 `_next_round` 的轮账目 `(version-1)+exhausted`（acceptance.py:279-284）在 resume 时推导续跑位置；v4 下相位序号本身即轮号，带技能作答复用键 (skill_id, version, 相位序号) 与 v3 的 (skill_id, version, retry_round) 数值一致（A1/A2/A3 → 0/1/2），泄题判定沿 (skill, version) 跨相位复用（first-verdict-sticks，acceptance.py:363-376 明文含"later round on an unchanged version"场景）。**语义等价成立**，但有两个前提不在方案文本里：attempts 对象仓必须跨相位共享（否则 exam_once 的 serial 按单仓计数（exam_once.py:148-160）跨相位撞号），泄题账本必须跨相位可读——前者即偏移约束 P4。

读取器行规格两处问题（live 的 no-op guard 字段来源、exam 的改写输入字段）→ **偏移约束 P7**。

### 3. §4 upgrade_v3 映射无损性（逐份核对 7 份 *_v3.yaml）

7 份配置（tb21_split_live_run / tb21_split_v2 / tb21_split_reuse / tb21_full_v2 / alfworld_skillopt_split / alfworld_skillopt_split_v2 / searchqa_skillopt_split）的顶层键 = benchmark、methods、mode、model_options、models、n_attempts_strong/weak、prompts_digests、rules、schema_version、split、tasks、token_budgets、weak_profile——§4"全局字段不变"清单逐一含盖，删 methods 后无遗漏键。methods 子键逐份归宿：

| v3 子键 | 实测值（逐份） | §4 归宿 | 判定 |
|---|---|---|---|
| acceptance: exam retry_limit=2（6 份） | 全部 2 | [D,L]+[A,L]×3+[C]，L.curate.merge_check=exam | 吻合 K4 轮账目（rounds 0..2） |
| acceptance: live_run（1 份） | n_attempts=3、improvement=strict_rate_gain、max_injection_tokens=null（tb21_split_live_run_v3.yaml:4-7） | B.execution.n_attempts=3 / B.execution.max_injection_tokens=null / B.compare.rule=strict_rate_gain | 无损；null 在字段表里有位 |
| delivery（retrieval 4 份：top_k=5, max_categories=3, min_score=0.02/0.05；whole_bank 3 份） | | C.delivery 嵌套参数 | 无损（alfworld v2 的 min_score=0.05 也进嵌套） |
| curation（7 份 agentbrew: token_budget=96000, rounds=3） | | L.curate 同参数 + merge_check | 无损 |
| segmentation（7 份 K=5, retry_limit=2） | | L.evidence（K2 修复预算，非轮数） | 无损 |
| authoring.exams: true（7 份） | | 删除（隐含） | 无损 |

专项核对：live 的 max_injection_tokens 与 improvement 有归宿（B）；reuse 配置本身是普通 exam 配置、delivery retrieval 参数无损（装配工具归 §7）；alfworld retrieval 参数进 C.delivery 嵌套；full 模式 C 对照 = reuse_d_weak（与 stage6_test.py:325-331 的 stage1_reuse 分支一致，A4 落实）；exams:false 消融对的可转换性见琐细 T2（转换条件少抄了 check_authoring 的一半）。**一处失真**：D 双臂采样深度——§4 字段表只写"`n_attempts`（缺省 n_attempts_weak）"，而现行 D 的强臂/弱臂各取全局 n_attempts_strong / n_attempts_weak（stage1_baseline.py:210-215）；按字面 D 的强臂会跑 n_attempts_weak 深度，任何两值不等的配置转换后行为改变（7 份现配置恰好都是 3/3，故转换无损，但 schema 语义错——第一轮 A3 第 6 点只落实一半）。→ **偏移约束 P3**。

### 4. §5 失败处置表与代码路径吻合

逐行核对：

- 行 1（G3 出题耗尽）：现行 = "单元终态失败，技能不入库，resume 不再试"（stage3_generation.py:49-51"failed unit leaves nothing behind"、270-279 终态行先写、56-57 resume 不再试）✓；新框架列与 E2 映射一致 ✓。**缺行**：G2（write_skill）预算耗尽的另一半（现行同路：单元终态、不入库），新框架归 L 单元，表里无行 → 琐细 T5。
- 行 2（泄题审计耗尽）：现行 = audit_leak 的 ModelOutputError 穿透 accept_skill → exam.py:164-169 discard(model_output_error) ✓。
- 行 3（G6 耗尽）：现行 = REWRITE_FAILURE_LEDGER 记 exhausted 行、版本不变、本轮消耗、下轮重答同版（acceptance.py:100-109、238-276）✓；新列"A 重答键含相位序号"等价性见 §一.2。
- 行 4（基础设施错误）：现行 = 单元留 pending、阶段末抛 InfrastructureError、exit 3（exam.py:170-172/203-207、live_run.py:585-587/756-763、stage6_test.py:407-412、cli.py:81 EXIT_INFRA_RESUMABLE=3）✓。
- 行 5（shortfall）：四条政策逐条与现状一致（逐相位 durable：live_run.py:806-825；B 读 D 弱臂洞：skip_ids_for 过滤 role=="weak"，live_acceptance.py:508-529；轮 1 不继承轮 0 洞：live_run.py:609-610 的 skip1_for 只含 accepted1；full 的 C 对照臂洞在 D 侧接受：stage6_test.py:279-284）✓。括注"（§3 决策清单已含）"指向不对（§3 清单不含空洞政策，它们是相位编排的再派发语义）→ 琐细 T6。
- 行 6（代码 bug）：run_units 规则，立即停整批 ✓（exam.py:41-43 模块 docstring）。

**缺行**：G10/G11 预算耗尽（见 §一.2 与 P2）——这是 §5 表与 §3 清单共同漏掉的唯一现行失败处置。

### 5. M2/M3 "golden 逐字节无 diff" 可行性

逐项判断翻字节风险：

| M2/M3 改动 | 翻字节风险 | 依据 |
|---|---|---|
| delivery 三新驱动接线（stage6 裸臂、live 解析器） | 无——只要 kwargs 值不变 | stage6 裸臂现值 skills_injected=None/skills_block=""（stage6_test.py:333-349）；live 块经 survivors_and_block（live_run.py:380-392），audit_row 构造留在 K6 不动 |
| gen_exam/audit_leak 搬函数（调用点不动） | 无 | llm dump 只含 prompt_name/role/model/渲染 messages（llm/call.py:459-463），prompt 注册在 prompts 包导入期，与函数所在模块无关 |
| K6 核去轮化 | 落盘字节无风险（账本行含 round 字段由调用方传同值），**但验收门表述自相矛盾** | test_live_acceptance_resume 以现行签名直调 judge_round/audit_row/no_op_guard/round_dir 并硬编码 live 布局路径（test_live_acceptance_resume.py:29-36、188、233、245、264、329），"签名与轮数、live 目录名解耦"后不可能"原样通过" → **偏移约束 P5** |
| M3 learn 驱动抽取（决策半边改调它） | 无——写盘次序与内容保持即可；注意 write_json 不排序键 | infra/store.py:111-127 `json.dump` 原序写出，summary dict 构造序必须保持（琐细 T8）；append_jsonl 键排序（store.py:285），golden workers=1 下行序确定 |

结论：除 P5 的门表述外，逐字节目标可行。

### 6. M4 相位驱动对 stage1–6 现有产出的覆盖

逐 stage 列出现有落盘物与相位归宿：

- stage0：manifest + stage0_summary → phases/freeze（p00）✓（§3）。
- stage1：summary（metrics/F/infra_*) + accepted_infra_shortfall.json + 轨迹仓 → D 相位。baseline_report 读"D 相位的轨迹仓 + shortfall"✓。**但 stage1 洞记录与轨迹仓的路径常量（composites/shortfall.py:21-22 硬编码 `stage1_baseline`；KIND_DIRS 硬编码 `stage1_baseline/trajectories`，infra/store.py:29-38）搬不搬进相位目录，方案没说** → 偏移约束 P4。
- stage2：summary + K2 审计（segmentation_audit.jsonl，segment_checked.py:68 硬编码 `stage2_segments`）+ subtasks 仓 → L（add 路径）。subtasks 只由首个 L 写、后续相位跨读——同受 P4 影响。
- stage3：generation_failures.jsonl + summary → G3 半边随出题挪 A、G2 半边留 L（T5）；`skipped_missing_exam`/`exam_missing_advanced` 两分支随 v4 消亡（第一轮 A 已预告清理）。
- stage4：exam 的 leak_verdicts（→A）/rewrite_failures（→L，§5 行 3 明说）+ attempts 仓；live 的 verdicts/attributions/attribution_failures/revision_failures（→B/L）+ 轮仓（§3"轮仓都在相位目录下"）✓。
- stage5：curation_log + summary → 末位 L ✓。
- stage6：双臂仓（bank/bare stores）+ retrieval_audit + 每臂 accepted shortfall + summary/delta → C 相位（test_report 读"C 相位双臂仓 + shortfall"）✓。

**判定**：相位目录对"账本、轮仓/臂仓、K1 workdir、summary、shortfall 记录"都有归宿；唯独**五个对象仓（KIND_DIRS）**没有归宿声明，而它是最不能含糊的一项——若随相位目录搬动，K3 的 attempt serial 按单仓计数（exam_once.py:148-160）、K4/K5 的 attempt 检索（acceptance.py:147-154、curation.py:751-776）跨相位失联、attempt_id 跨 A 相位撞号、"first verdict sticks" 续跑语义断裂；若不搬，§8 的"布局映射（旧 stage 目录名 → 相位目录）"就必须收窄为只映射账本/轮仓/summary。→ **偏移约束 P4**。

### 7. E1–E5 可计算性（对照 69904f7 的 golden fixtures，实测）

对 8 份 fixture 逐份探测的结果：**全部技能终态均为 accepted，零 discard**——exam 组合 leak_verdicts 全部 leaked=false、rewrite_failures 0 行、调用多重集里没有 REWRITE；live 组合 better=[False,True]（round0 败→G10/G11→round1 过）、无 no-op 命中。原因在假模型脚本：泄题审计恒 `{"leaked": False}`（harness.py:271）、带技能作答恒判对（272-277）、live 插件只在注入块带 REVISED-BY-G11 标记时成功（134）而 G11 恒追加该标记（242）→ no-op guard 恒不命中；出题脚本恒返回合法 JSON → G3 失败不可达；golden 未设 max_injection_tokens、无 infra 洞 → live_injection_overflow / live_undetermined 不可达；K5 的 similar_groups 恒 [] → 无合并、无回归考。

因此：E1（bank 行多重集）、E3（stage6 指标+aux，字段清单正确地不含 bank_reference——M1 新字段必然翻 bank digest）、E4 exam 侧（调用多重集相等）、E4 live 披露项①（v3 live golden 实测含 EXAM_GEN 调用，v4 为 0，非空真）都可计算 ✓。**但 E2 的全部映射行（未出生↔model_output_error、leak_audit、泄露改写、retry_exhausted、live_no_improvement、live_revision_deleted、live_undetermined、live_injection_overflow、weak_already_knows、no-op guard、G6/G10/G11 耗尽）在 golden 上恒真**；E4 披露项②（无回归考）在 golden 上空真（v3 live golden 本就无合并、无回归调用）。§11 只给了一条专项单测（改写耗尽→同版重答）。若 L 驱动把这些分支写错，M4 的 E 门照样全过。→ **偏移约束 P6**（补法：以 tests/composites/ 现有场景为基准的分支专项等价测试，逐条列在 P6）。

### 8. 第一轮偏移约束 A1–A7 落实核对

| # | v2 §10 裁决 | 实施方案落实 | 判定 |
|---|---|---|---|
| A1 跳过判据 | D5（跳过键=现行续跑键 + K6 去轮化） | §3 按测量类型逐一钉死（裸答/泄题/带技能/轮仓+冻结行） | ✓（去轮化的门表述见 P5） |
| A2 原则 5 矛盾 | D8（"学习不发起真执行"） | loop v2 §5.5 已改 | ✓ |
| A3 配置表达力 | D9（完整字段映射表） | §4 表 + 加载检查 + M4"逐份人工审定留档" | 大部分 ✓；D 双 n_attempts 半点未落实 → P3；exams:false 转换条件少半条 → T2 |
| A4 C 对照臂 | D9（control 子句） | §4 compare.control + E3 含 control_source | ✓ |
| A5 外围机制 | D2/D7 | §7 跟随者全表（assemble_reuse/audit_live_run/cli/文档/golden/假模型）+ §5 shortfall 行 | ✓（对象仓归属是新增空白 → P4） |
| A6 解绑改动面 | D1（ID 不变、解绑落 schema 层、不加 kind 字段，理由记录在案） | §2 + "make_* 全部不变" + M1 golden 重生成 | ✓（多来源分组依据隐式成立，建议一句话写明 → T10） |
| A7 验收仪器 | D6（M4 语义准则） | §8 E1–E5 + §11"E1–E5 先行实放在 M4 第一提交" | ✓ 落实；可计算性缺口 → P6 |

无漏网的 A 项；新增发现全部出自本轮自己的代码核查。

---

## 二、发现分级

### 偏移约束（不修不能开工）

**P1. §3"还有后续验证轮"的定义数错了对象。**
实施方案 §3（execute_learn_implementation_20261015.md:65）："还有后续验证轮 = 冻结序列中该 L 之后**还有几个 learn 相位**（末位 L = 0）；不数 C"。语义应为"该 L 之后还有没有**验证类执行相位**（A/B）"：exam 的现行末轮判定是 `retry_round == retry_limit`（composites/acceptance.py:361），live 是"有无 round-1 verdict 行"（methods/acceptance/live_run.py:597-605），数的是验证轮不是学习轮；括注"不数 C"只有在按执行相位计数时才有意义，恰好暴露本意。对 schema 并不禁止的序列（如 [D,L,A,L,A,C]——L@3 之后无 learn 相位但 A@4 仍在），按字面实现会把中间 L 误判成末位（答错直接 delete 而非 revise），方向性错误。
**修法**：改为"'还有后续验证轮' = 该 L 之后还有没有验证类执行相位（A/B），不数 C；之后验证相位数 = 0 的 L 即末位"。

**P2. §3 live 决策清单与 §5 失败处置表都漏收 G10/G11 修复预算耗尽的处置。**
live 的 discard_reason 词汇表共 6 个（composites/live_acceptance.py:62-65、145-150），§3 live 清单（impl:64）收录了其中 5 个，唯独缺 model_output_error：现行 G10 归因预算耗尽 → **整组**弃 model_output_error（live_acceptance.py:851-874，先 `_discard_group` 落终态、后落 exhausted 行）；G11 修订预算耗尽 → **仅该技能**弃、组继续（1081-1093，discard_skill 后落 exhausted 行）。两者"整组 vs 单技能"的差别与"终态先写、账本行是回执"的次序都不在 §3/§5 任何清单里，而 M3 以 §3 为验收基线（impl:182"以 §3 清单为验收基线"）——漏收即可能漏实现，且 E2 在 golden 上抓不到（见 P6）。
**修法**：§3 live 清单加"G10 预算耗尽 → 整组 delete(model_output_error)；G11 预算耗尽 → 仅该技能 delete(model_output_error)、组继续"；§5 表加对应行并注明次序与现行一致。顺带把 §3 exam 清单的"出题/审计不可用"扩为"出题/作答/判卷/泄题审计任一测量预算耗尽"（现行 G4/G5 耗尽与 G3/D8 同路弃：exam_once 的 ModelOutputError 经 exam.py:164-169 同处置）。

**P3. §4 字段表 execution.n_attempts 的缺省与 D 相位双臂现状冲突。**
§4 字段表（impl:94）只写"real 可带 arms（缺省 [executor_weak]；D 显式写双臂）、n_attempts（缺省 n_attempts_weak）"；而现行 D 的两臂各取自己的全局深度——强臂 n_attempts_strong、弱臂 n_attempts_weak（stages/stage1_baseline.py:210-215）。按字面，D 的强臂会以 n_attempts_weak 深度执行：任何 n_attempts_strong ≠ n_attempts_weak 的配置在 v4 下行为改变（7 份现配置均为 3/3，转换无损，但这是第一轮 A3 第 6 点的半点残留，schema 语义本身错）。
**修法**：字段表补一句："D 的 arms 各取全局 n_attempts_strong / n_attempts_weak（角色→全局深度），D 不设 n_attempts；execution.n_attempts 仅单臂执行相位（B/C）适用，缺省 n_attempts_weak"。

**P4. M4 相位目录没有回答对象仓（Store KIND_DIRS）与 run 级工件的归宿。**
infra/store.py:29-38 把五种对象的目录硬编码为 stage 名（trajectories→`stage1_baseline/`、subtasks→`stage2_segments/`、skills/exams→`stage3_generation/`、attempts→`stage4_acceptance/`）；K1 的 skip_audit 随 store.run_dir 落位（composites/batch_execute.py:517）；stage1 洞记录路径硬编码 `stage1_baseline`（composites/shortfall.py:21-22），K6/stage6/审计脚本都按它读。§3 只说"K1 工作目录、轮仓、账本都在相位目录下"（impl:47），§8 又说"布局映射（旧 stage 目录名 → 相位目录）"（impl:145）——两者都没有回答 KIND_DIRS 搬不搬。若搬：exam_once 的 attempt serial 按单仓计数（composites/exam_once.py:148-160），A1/A2 分仓后 serial 重启、attempt_id 跨相位撞号；K4/K5 的 attempt 检索（composites/acceptance.py:147-154、composites/curation.py:751-776）看不到别的相位写的记录；"first verdict sticks"与 E5 的续跑等价全断。若不搬：§8 的布局映射与 §7 装配器行（"拷贝清单/负清单改相位目录"）必须收窄为"仅账本/轮仓/summary/workdir"。
**修法**：M4 明文规定"五个 KIND_DIRS 对象仓保持 run 级、全相位共享（与现行为一致）；相位目录只放相位本地产物（相位 summary、账本、轮仓/臂仓、K1 workdir、相位 shortfall 记录）"；§8 布局映射与 §7 装配器行相应改写（对象 KIND_DIRS 直读，stage 命名的 summary/账本路径映射到相位目录）。

**P5. M2 的验收门自相矛盾：K6 去轮化 vs "test_live_acceptance_resume 原样通过"。**
§3 D5（impl:49）要求"K6 的测量函数**签名**与轮数、live 目录名解耦"；M2 门（impl:162）同时要求"test_live_acceptance_resume **原样**通过"。而该测试以现行签名直调 K6 函数并硬编码 live 布局：import judge_round/audit_row/no_op_guard/survivors_and_block（tests/composites/test_live_acceptance_resume.py:29-36）、`judge_round(..., round_=0, ...)`（264 附近）、audit_row(store, TASK_ID, 0)（233、245）、round_dir 路径串 `"stage4_acceptance"/"live_run"/round{NN}`（188、329）。签名与路径推导解耦后该文件不可能原样通过；按门施工的代理会在"改测试"与"不去做轮化"之间无所适从。
**修法**：M2 写明中间态："K6 保留轮次薄壳（round_ 参数、round_dir/LIVE_DIR_NAME 的现有签名与路径），**核**（judge_round/audit_row/no_op_guard 的内部实现）去轮化——test_live_acceptance_resume 原样通过；薄壳在 M4 相位编排落地后删除"。或把去轮化整体挪入 M4（golden 与测试在 M4 本就全量重生成）。

**P6. E2 对 golden 不可达分支是空验证，须把"分支钉住"列为 M4 验收项。**
对 69904f7 基线 fixtures 实测（本评审第一节第 7 项）：8 份 golden 全部"全接受、零 discard"——leak_verdicts 全部 leaked=false（harness.py:271 脚本恒 False）、无 REWRITE 调用、带技能作答一次判对（harness.py:272-277）、live round-1 恒过（harness.py:134+242，REVISED-BY-G11 标记使 no-op guard 恒不命中）、G3 恒成功、无 max_injection_tokens、无 infra 洞、无合并无回归。于是 E2 的全部映射行（未出生↔model_output_error、leak_audit、泄露改写、retry_exhausted、live_no_improvement、live_revision_deleted、live_undetermined、live_injection_overflow、weak_already_knows、no-op guard 命中、G6/G10/G11 耗尽）在 M4 对 golden 的比较里恒真；E4 披露项②（无回归考）在 golden 上空真（v3 live golden 本就无回归调用，实测调用多重集无 MERGE 族）。§11 只钉了"改写预算耗尽→同版重答"一条。
**修法**：§8 E2 加覆盖面声明（"golden 覆盖面 = 接受路径 + K5 无合并回归；其余分支由专项测试钉住"），并把下列专项等价测试列为 M4 验收：以 tests/composites/test_acceptance.py 的 leak 路径（243-304）、retry_exhausted（208）、改写耗尽（531）与 tests/composites/test_live_acceptance.py 的 no-op guard（1258-1317）、overflow（698）、undetermined（588）、G10 耗尽（929）、G11 耗尽（1140）、weak_already_knows（641-696）场景为基准，驱动 v4 的相位/learn 驱动代码，断言逐技能终态、discard_reason 与账本行和 v3 语义一致；E4 披露项②同理由 merge_check 单测钉住（M3 已列"merge_check: none 单测（现造）"，补上"exam 版回归考在合并产物上的等价"即可）。

**P7. §3 读取器两处行规格会误导 M3 实现。**
(a) live_report 行（impl:57）含"与上轮冻结行字节等价?"，但 no-op guard 的现行判据是**修订落地之后**的渲染块 sha 对比冻结行（composites/live_acceptance.py:1148-1155 的 survivors_and_block 在 apply_revisions 之后取当前 store；接缝在 methods/acceptance/live_run.py:612-618）。一个"对上一轮工件 + 当前 store"的纯读取器在 L 决策时点算出的等价性**恒为真**（round-0 冻结行本来就是同一批成员渲染的）——按字面实现等于守卫全灭（每个组 round-0 后整组弃、round-1 永不跑）。
(b) exam_report 行（impl:56）缺改写输入：G6 改写需要 weak_answer 与来源 attempt 的选取规则（泄露轮 source = 最近一次带技能 attempt 或裸答，composites/acceptance.py:383-390、214-226；答错轮 source = 本轮 attempt，413-416），行字段"本相位 attempt(verdict, judge_reason)"不含 weak_answer，也不含跨相位的来源选取。
**修法**：(a) 从读取器行删去该字段，§3 live 清单的 no-op guard 条注明"比较在修订落地后（或下个 B 进入时）以 survivors_and_block 对冻结行计算，不是报告字段"；(b) 行加 weak_answer 与 source_attempt 指针（读取器输入本就含 K3 仓），或注明改写步按报告引用回读 Attempt 仓。

### 琐细

- T1. §3 delivery 注册表行的"输入 (bank, tasks)"对 per_skill/group_by_task 不成立：group_by_task 要读 store 的 pending 组与当前版本（live_run.py:368-392），per_skill 的输入是单元技能；接口草图按 M2 实现时自行修正即可，建议改写为"输入 = 技能源（bank 或 store pending 集）+ 任务集"。
- T2. §4 exams:false 的转换条件少抄半条：v3 的 check_authoring 还拒绝 exams:false + curation: agentbrew（methods/authoring/__init__.py:34-41），合法组合只有 acceptance: live_run **且** curation: none；转换器应照抄整条（"exams:false 仅当 live_run 且 curation: none 可转，否则报错"）。7 份现配置均 exams:true，不碍转换。
- T3. §4 未说 full 模式下 `tasks: test` 的语义（full 时 train==test，全局 mode 决定），一句话补齐。
- T4. §4 拒载只提 v3；v1/v2 配置在 v4 代码下的指向需保留（现行 <3 → convert_v2_configs 提示在 core/config.py:638-643；v4 应为 ==3 指向 convert_v3_configs、≤2 沿用现行）。
- T5. §5 行 1 只写了 G3（出题）半边；G2（write_skill）预算耗尽的现行处置（stage3 单元终态、技能不入库、resume 不再试，stage3_generation.py:49-57,270-279）在新框架归 L 单元，表里应有对应行（行为不变）。
- T6. §5 shortfall 行的"（§3 决策清单已含）"指向不对：§3 清单不含空洞继承政策，它们是相位编排的再派发语义（live_run.py:609-610 的 skip1_for 不含 accepted0 即"轮 1 不继承轮 0 洞"）；引用改指 §5 本行或相位驱动。
- T7. loop 方案 v2 §4.2"retry_limit 之类的轮数参数由序列长度表达，不再是某个模块的私有参数"与实施 §4 保留 L.evidence.retry_limit（K2 修复预算，非轮数）冲突；按 §1.1"以本方案为准并回改"，v2 该句应回改。
- T8. M2/M3 逐字节无 diff 的实现约束未提示：write_json 不排序键（infra/store.py:111-127），所有 stage summary 的 dict 构造序必须保持；建议在 M2/M3 验收里写一句"summary dict 键序不变"。
- T9. E4 的"归一化请求变量"在 fixture 里没有变量字典，只有渲染后的 messages（llm/call.py:459-463 的 dump 记录 + harness 的 multiset 快照）；E4 应写"以 (role, prompt_name, 渲染请求文本) 多重集比较"——与 fixture 现物一致且更强。
- T10. A6 的"多来源技能在 live 按任务打包下的分组依据"未明写；由"is_stage3_candidate 仍按 ID 模式 + pending_groups 只收段生候选"（live_acceptance.py:400-426）隐式保证合并产物永不进组，建议一句话写明。

### 赞同（核实成立的关键断言）

- §2 字段表对七类消费方覆盖完备，不需要更多字段；source_subtasks 并集 ≡ merged_from 递归原件集（§一.1 逐项验证）。
- exam 序列 [D,L]+[A,L]×(R+1)+[C] 与 K4 轮账目吻合（acceptance.py:23-34、360-361）；live 两轮钉死（live_acceptance.py:168-169）与 [D,L,B,L,B,L,C] 吻合。
- §3 exam 决策 8 条与 acceptance.py:340-420 逐条对应；"改写预算耗尽→版本不变→下轮重答同版"在相位序号键下等价保持（前提=对象仓共享，P4）。
- §4 全局字段清单与 7 份 v3 配置逐键吻合，methods 之外无遗漏；upgrade_v3 映射表给 7 份配置每个 methods 子键都找到 v4 归宿（live 三参数、retrieval 三参数、curation 两参数、segmentation 两参数、exams 开关）。
- C 的 compare.control（split→fresh_bare、full→reuse_d_weak）与 stage6 现行 mode 分支一致（stage6_test.py:278-290、325-331）——A4 落实。
- §5 行 1/2/3/4/6 的"现行"列与代码逐行一致；shortfall 四条政策逐条与现状一致。
- §6 标签/CLI/until-phase/exit code 与 pipeline.py:61-69、229-232、387-391 和 cli.py:232-252 现状对应可行；两处拒载提示位次（config.py:629-643、stage0_freeze.py:289-302）准确。
- §7 跟随者清单覆盖第一轮 A5 的全部外围机制（assemble_reuse 的白名单/负清单硬编码 assemble_reuse.py:116-127、145-165；audit_live_run.py:86、93、499-500；cli；文档；golden；假模型脚本）。
- E1 的"最终技能库行"= skill_bank.jsonl（K5 提交产物，infra/store.py:404-407），"未出生↔discarded"库级等价成立；E3 字段清单正确地排除 bank_reference（M1 新字段必翻 digest）。
- M2/M3 逐字节无 diff 除 P5 外可行：搬函数不改 dump 字节；delivery 接线 kwargs 值不变；K6 核去轮化不动落盘内容（§一.5 逐项验证）。
- golden 基线指称成立：tests/fixtures/golden 在 69904f7 与 HEAD bb0935f 之间无 diff（实测）；E5 的 8×3 崩溃变体与 generate.py CRASH_FRACTIONS=(0.3,0.6,0.95) 一致；HEAD 上两份 resume 测试实测 236 passed。
- A1–A7 全部有 v2 §10 裁决（D1–D11）且实施方案落实（除 A3 的 D 双 n_attempts 半点 → P3）；A6 的"不加 kind 字段"是被 D1 有理由记录在案的裁决，合并产物锚点与版本守卫因 ID/merged_from 不变自动保持。

---

## 三、结论

实施方案的字段表、映射表与失败处置表与代码事实总体吻合，A1–A7 的裁决全部落实，M1–M4 的步骤划分与验收门设计是成立的。开工前须先修 7 条偏移约束：P1/P2/P7 是 §3 清单本身的错误与遗漏（清单是 M3 的验收基线，错一行就漏一个行为）；P3 是 §4 唯一的语义失真；P4 是 M4 唯一可能造成续跑语义断裂的布局空白；P5 是 M2 门的内部矛盾；P6 决定 M4 验收是不是空转。全部修法都是局部的（每条一到三句话 + 一列测试名），不动方案方向。
