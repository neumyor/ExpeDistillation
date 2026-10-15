# stage-4 验收重构：exam / live_run 双模式（最终实施方案）

日期：2026-10-14（修订二：用户裁决 11 项开放问题；修订三：三方定稿评审收敛——final_review_{1,2,3}.md 合计 0 blocker、合并去重 5 major 与约 20 minor，全部文本级修正已并入正文，三方一致判断"可实施"）
一句话说明：把 stage 4 验收从"单 skill 文本考试"改造为可配置切换的验收模式体系，新增 live_run 模式（同 task 的 skill 组注入学生、真实执行 n 次、与 stage1 裸跑比对、强模型归因修订、两轮不过整组丢弃）——本文是三案设计（tmp/acceptance_redesign/designer_{1,2,3}.md）→ 收敛（converged_plan.md）→ 三路独立审查（reviewer_{1,2,3}.md，共 52 条发现）修正 → 用户裁决（§3）→ 三方定稿评审收敛后的最终实现方案，为实施依据。
对应 log.md 条目：设计定稿条目（实施另行记录）。

---

## 0. 设计沿革与审查修正摘要

设计流程：同一简报三个独立设计者并行产出三份完整方案 → 交叉收敛（17 项分歧逐一裁决）→ 三个独立分析者对照代码逐条核验（9 项代码事实全部带行号验证）→ 按审查修正。审查抓到的**收敛稿错误**（本定稿已全部修正，编号可追溯）：

| 来源 | 错误 | 修正 |
|---|---|---|
| R2-P1 | A10 把 D3 的"首写冻结"错改成"覆盖式"，与轮栅栏续跑组合出 blocker 破洞（宽集重派 → 已修订组撞 `_check_stored` ConfigError 判死整个 run；窄集重派 → 冲掉 no-op 守卫数据源） | §2.4：append-once 首写冻结 + 重派发集限定 |
| R1-P3 / R2-P6 | shortfall 记录无轮次粒度；接受后空洞未回灌 skip 集（D2 原文有、收敛时丢失） | §2.5：按轮存 + skip 集合并集 |
| R1-P1 / R2-P3 / R3-M2 | A15 的 assemble_reuse 修法与代码相反（`_ALLOWED_DIFF_KEYS` 只被 bank 档消费，加键 = 放开 bank 档混装） | §1 A15 重写：零改白名单 + 双向拒绝已由 identity walk 保证 |
| R1-P2 | None-drop 若实现成通用"None 删键"会删掉 full 模式的 `split: null`/`tasks: null` → full run 续跑被拒 | §2.1：字段白名单式删键 |
| R2-P2 | "既有 1636 测试零修改全绿"为假（`test_to_dict_covers_every_field` 断言全字段键集，与 None-drop 正面冲突，已核验 tests/unit/core/test_config.py:644） | §2.7：口径改为"唯一修改的既有测试是序列化形状测试" |
| R3-M6 / R2-P5 / R1-P4 | A7 的"cursor 竞态"是收敛稿新造的假代码事实（`k1_retry_cursor.json` 每 trajectory_id 一份，per-task 形态无竞态） | §1 A7 论据重写（结论保留） |
| R3-M3 / R2-P4 / R1-P5 | K6 形状三处矛盾（轮栅栏 vs 单 task 状态机 vs "或"） | §2.6：K6 定形为逐组阶段函数库 |
| R3-M5 | TB 的 `Task.instruction` 就是任务名，G10 归因输入退化为一个词 | §2.3：改用 `task_prompt` 渲染产物 |
| R3-M4 | 真实 TB pilot 干跑门槛被丢（外层 AGENTS §1 硬性要求） | §2.9 合并门追加 |

三案独立收敛、三路审查核验后**确认成立**的地基（不再重复论证）：manifest 三个"续跑炸弹"（`ExperimentConfig.to_dict()` 全字段进 manifest、`METHOD_PROMPT_NAMES` import 时快照、`MODEL_ROLES` 角色表补全——任何新字段/新 prompt 名/新角色都让在飞 TB v2 run 的续跑被 `FrozenProtocolChanged` 拒绝）及其"exam 视图逐字节不变 + golden 回归测试"拆法；K1 零改动复用（参数表全部承接，`_run_namespace` 天然区分轮次）；五对象零 schema 变更（`discard_reason` 自由字符串的设计口子）；判据签名消费 `compute_task_metrics` 的 `{attempts, valid, success, infra}` 计数形状；`discard_skill` 的 composites 复用先例；A17 不可达论证（两个已注册 task_select 规则都要求 `valid > 0`，rules.py:111,127）。

## 1. 裁决表（修正后定稿）

| # | 分歧 | 裁决 |
|---|---|---|
| A1 | 配置键的家 | 顶层 `acceptance: Optional[Dict] = None` + **`to_dict()` 字段白名单式删键**（`_MANIFEST_OPTIONAL_FIELDS = frozenset({"acceptance"})`，仅此字段 None 时缺键；禁止通用 None 过滤——`split`/`tasks` 在 full 模式冻结为 null 必须保留）。`CONFIG_SCHEMA_VERSION` 不升（9/9 配置不显式写 schema_version，升版会让冻结值 2→3 同样拒续跑；技术债登记实施收尾清单——§2.9 T5、裁决 7） |
| A2 | 判据粒度 | 两层：`mode: live_run` + `improvement: <规则名>`（必填无默认）+ `n_attempts`（必填，**≥2**——1v1 的 H0 假通过率≈真实成功率本身，D1 守卫采纳）+ `max_injection_tokens`（可选，null=只测量）。`acceptance: {}` 与键不写都 ≡ exam |
| A3 | 归因+修订形态 | **两段式**：G10 组级归因计划 → G11 逐 skill 修订（修复预算/落盘单位独立；单条修订失败不连坐全组）。G10 输出契约不含版本号——版本由 K6 在账本 append 时从 store 读入补记（模型输出不可信） |
| A4 | 归因全 keep | **不跑轮 1**：no-op 守卫（幸存组渲染块与轮 0 注入块逐字节相同 → 全组 `live_no_improvement` 终态）。数据源 = round00 injection_audit 冻结行的块 sha256。防"重采样直到运气好"（两次 3v3 严格提升假阳性近乎翻倍） |
| A5 | 修订纪律 | **保持"不抄本题具体值"**。决定性理由（审查后明确）：**retrieval 交付会把库成员检索注入到其他 task 上**，任务局部化 skill（具体数值/文件名）对别的 task 是主动有害的；G11 放水 = 在 G2 已冻结的可迁移纪律上开后门，伤害外部化到 stage6。措辞折衷：允许通用模式与命令族、禁逐字抄具体值。该取舍与"任务知识是被测处理"（§0.9）的张力如实写进 README |
| A6 | 判据口径 | `strict_rate_gain`：`live.success × baseline.valid > baseline.success × live.valid`（整数交叉乘法）。等深且 skip 对齐时与计数口径等价；分叉场景（n 不等或 live 侧接受 shortfall）率口径是唯一无偏推广（例：live 3/5 vs 基线 2/3——计数判"更好"、率判"未更好"，率是对的） |
| A7 | 并行结构 | **每轮一次 K1 大批（轮栅栏）**。论据修正（原"cursor 竞态"不成立，如实记录）：(a) per-task 形态下同 task 的 n 次尝试串行相邻，丢掉 K1 attempt-major 派发的"同题采样不相邻"去相关；(b) 大批复用 K1 的波次/心跳/错峰/游标机器一次而非 N 套；(c) **墙钟代价如实入账：轮 1 执行要等最慢的轮 0 组完成（轮间串行化，D1 估 10–14h vs per-task 流水线估 2–3h），换来相位化续跑推导的简单性与单套观测面**；(d) D2 形态无正确性缺陷，本裁决是工程偏好 |
| A8 | live 空洞政策 | `--allow-infra-shortfall` 扩展到 stage4-live（live 让 stage4 首次成为执行阶段，与 stage1/6 同故障面；TB 永久坏沙箱真实存在，无接受机制 = 续跑死锁）。durable 记录**按轮**存 |
| A9 | stage1 空洞继承 | `skip_unit_ids` 继承 stage1 已接受弱臂空洞（**过滤 weak 臂**——该记录是双臂的；**限本 task**） |
| A10 | 注入审计 | **首写冻结**（非覆盖式）：`round{NN}/injection_audit.jsonl` 每 task 一行（成员 [skill_id, version] 列表 + D9 块 sha256 + D10 估算 token 数），append-once；重派发前重推导内容与已冻结行逐字节比对，不等 → CodeBugError。**重派发集 = 非终态 ∧ 轮 store 不完整的组**（"非终态"限定不可省：零执行终态组——weak_already_knows / live_injection_overflow——的轮 store 为空即"不完整"，不加限定会在每次续跑时把它们重派进 K1 白烧 n_attempts 次执行；"终态"= 组内全部成员已有终态 status 或组已落零执行终态 verdict 行） |
| A11 | 状态载体 | 零新状态对象，全部从磁盘推导（轮 store 完整性 + 三个账本 + 版本号）；finalization sweep 收尾。账本行 schema 见 §2.4（审查指出推导表隐式需要归因时版本与注入快照，显式化） |
| A12 | 归因者角色 | G10 = `author`（诊断族），G11 = `rewriter`（修订族）。非阻塞，README 词汇表注明 |
| A13 | discard_reason 词汇 | `live_no_improvement` / `live_revision_deleted` / `model_output_error` / `weak_already_knows` / `live_undetermined` / `live_injection_overflow`。`weak_already_knows` 跨模式复用（exam=裸答对，live=基线全成功），README 词汇表注明证据基础差异；`live_no_improvement` 双语义（no-op vs 轮 1 失败）靠 verdicts 行的 round 区分 |
| A14 | K 编号 | K6（README 注明与 stage 6 无关） |
| A15 | assemble_reuse | **白名单零改动**：baseline 档的 pinned-minimum 检查天然放行 acceptance 差异（预期用法"stage1 复用 + live_run 重蒸馏"）；bank 档的 identity walk 天然**双向拒绝**任何 acceptance 差异（exam 源→live 目标、live 源→exam 目标都被拒——库的验收协议身份不可换）；同 acceptance 参数的显式 `{mode: exam}` 复用配置被拒（manifest 键集不同）是保守可接受的行为，文档注明。回归测试钉死这四种组合。`assemble_reuse._prompt_digests`（stage0 同名函数的手工复制品，assemble_reuse.py:290）在 live_run 目标时并入两个新 prompt 名——其作用面经定稿评审修正：装配**从不写 manifest**（MANIFEST_NAME 仅作守卫），digest 副本只被 bank 档消费且不参与身份拒绝，同步与否只影响 bank 档 live→live 复用时 drift 披露的准确性（不同步会把"prompt 集"误报为配置漂移），仍是必要修改但理由如上 |
| A16 | stage 分工 | `stage4_live_run.py`：轮序编排、K1 调用、shortfall 记录、摘要、阶段末 raise。K6（composites）：逐组阶段函数库（§2.6 接口清单）。无"或" |
| A17 | 基线 valid==0 | fail-loud（ConfigError，数据与冻结规则矛盾）；判据内防御分支保底。README 注明该守卫的不可达性依赖 task_select 规则要求 valid>0——换规则名时守卫从不可达变可达 |

## 2. 统一方案

### 2.1 配置与切换

```yaml
# 缺省（整键不写）≡ exam，与现状逐字节等价；{mode: exam} 显式亦合法；{} 亦合法
acceptance:
  mode: live_run            # ∈ ("exam", "live_run")
  n_attempts: 3             # 必填，int >= 2；建议 = n_attempts_weak（用户协议"跑 3 次"）
  improvement: strict_rate_gain   # 必填，指向 blocks/rules.py 的 ACCEPTANCE_IMPROVE，无默认
  max_injection_tokens: null     # 可选；null = measure-only
```

- `core/config.py`：+`acceptance` 字段 + `_validate_acceptance`（None/`{}` 直通；mode 词表；未知键拒；exam 下多余键 ConfigError；live_run 下 n_attempts≥2 与 improvement 必填）+ to_dict 的 `_MANIFEST_OPTIONAL_FIELDS` 白名单删键 + `ACCEPTANCE_MODES` + `acceptance_mode(config)`。
- `blocks/rules.py`：+第 4 个具名注册表 `ACCEPTANCE_IMPROVE`，签名 `(baseline, live, n_attempts) -> bool`。预注册两条（精确定义，T1 据此实现）：
  - `strict_rate_gain`（**裁决选名**）：`live["valid"] > 0 且 live["success"] × baseline["valid"] > baseline["success"] × live["valid"]`（整数交叉乘法；防御分支：baseline["valid"] == 0 时返回 False——正常流程在入口已被 ConfigError 拦截）；
  - `first_success_gain`（消融备选）：`live["success"] >= 1 且 baseline["success"] == 0`（pass@N 语义：基线全败 → 至少一次做成）。
  规则只读自己需要的键（aux_scorer 存在时 mapping 多 `aux_success` 键，严格键集断言会被 aux benchmark 打破）。规则名 stage 0 解析校验。
- 分发：`stage4_acceptance.run(ctx)` 顶部 4 行懒 import（exam 进程不加载 live 模块，import 图不变）。
- 第三模式加入路径：mode 名 + prompt 组 + stage 驱动模块 + 分发行 + **stage0 条件 digest 分支一行 + bank 档守卫认知一行**（如实：不是纯加法）。

### 2.2 K6 数据流（轮栅栏，每轮一次 K1 大批）

**验收单位（组的定义，实施正本）**：live_run 模式下，一个验收单元 = 一个 train task 的**组** = 该 task 的全部 **pending** stage-3 候选 skill（候选判定沿用 exam 的 `_candidates` 过滤器语义：`skill.skill_id == make_skill_id(skill.subtask_id)`，因此 `bank/merge/NNNN` 天然排除；live 侧在新文件就地重写该过滤 + 按 task 分组，不共享 exam 文件的 helper）。分组键由 ID 链确定性推导：`skill_id → parse_subtask_id → trajectory_id → parse_trajectory_id → task_id`；task 的 Task 对象经 `core/ids.py` 的 `raw_task_id` 反解析后由 D1 `load_tasks` 重建。组的宇宙从 store 现存 pending 候选推导（不假设 F=28 或候选覆盖全部 train task），无候选的 task 不构成单元。

```
stage1 弱臂裸跑（主 store 只读；已接受弱臂空洞继承为 skip）
   │  入口预检：基线逐确定性 ID 加载；未接受缺失单元 → InfrastructureError（不可达守卫，
   │  指路先续跑 stage1）；valid==0 → ConfigError；全成功 → weak_already_knows 零执行终态
   ▼
轮 0：一次 K1 大批【派发集 = 轮 store 不完整的组】（× n_attempts，逐 task 组注入，
   │   store = stage4_acceptance/live_run/round00/，injection_audit 派发前首写冻结）
   ▼
逐组判定（纯函数）——先短路：live 全单元皆已接受空洞 → live_undetermined 终态（不入归因）
   ├─ better → verdicts.jsonl 落盘 → 组内全员 accepted（幂等提交）→ 终态
   └─ not better → verdicts 落盘 → 归因
        ├─ G10（author，组级一次）→ 契约校验 → attributions.jsonl append（首判为准）
        ├─ 应用：delete → 立即 discarded(live_revision_deleted)；revise → G11（rewriter，逐条）
        │        → marker 集合校验 → version+1 + rewrite_history 立即 store.save
        ├─ no-op 守卫：幸存组渲染块 sha == round00 audit 冻结行 → 全组 live_no_improvement，不跑轮 1
        ▼
轮 1（仅幸存且注入块有变化的组）：K1 大批（store = round01/，injection_audit 首写冻结）
   ├─ better → 幸存成员 accepted
   └─ not better → 幸存成员 discarded(live_no_improvement)
```

- 基线加载：stage6 `_bare_stage1_trajectories` 同款形态直读主 store，绝不重跑。
- 基线全成功守卫（`success == valid` → 零执行终态）限定：仅当注册规则在率 1.0 基线下数学上不可能返回 True 时触发（当前两条规则均满足）；README 注明仅 strict 族适用，防注册表扩展时守卫成为暗坑。
- 归因/修订经 `run_units(ctx.workers)` 并行（单元 = 组/skill，各写各的账本行，`append_jsonl` 已线程+flock 串行化）。
- summary：`stage4_summary.json` 同名，顶层保留 `accepted`/`discarded`(reason→count)/`infra_failed`/`processed_this_call`，新增 `mode`/`improvement_rule`/逐组明细（双轮计数 + verdict + injection_tokens）/**`infra_missing`/`infra_accepted`/`infra_shortfall_accepted`**（前两个按轮分组；pipeline.py:367-376 的 ledger 旗标链路依赖它们）。README 写明双模式形状差异（live 的 `infra_failed` 元素是 task id 而非 skill id；无 `skipped_missing_exam`）。

### 2.3 归因（G10）与修订（G11）

- `prompts/attribution.py`（不进 `prompts/__init__.py` import 列表；`blocks/attribution.py` 模块顶部防御性 `import expedistill.prompts.attribution`——绕过 stage0 的调用方（单测/脚本）不撞 PromptNotFoundError）：
  - `LIVE_RUN_ATTRIBUTION`（G10）输入：`task_instruction`（**取 `task_prompt(task)` 渲染产物**——学生实际看到的执行 prompt；TB 下 Task.instruction 就是任务名，指令经 live_views 首步上下文间接可见，此事实如实写进 prompt 设计说明与 README；开放问题挂"TB 是否回捞远端指令全文"）、`baseline_summary`（逐次 status 行）、`live_views`（本轮**全部**尝试的 D5 视图，成败都给）、`skills_listing`、`weak_profile`。输出：`{"diagnosis", "skill_actions": [{"skill_id", "action": "keep|revise|delete", "problem"}]}`。
  - `LIVE_SKILL_REVISION`（G11）输入：`skill_text` / `attribution_report` / `failure_view`（轮 0 失败尝试 D5 摘录）/ `weak_profile`。输出与 G6 同构：`{"failure_category", "skill_text", "revision_reason"}`（复用 `FAILURE_CATEGORIES`）。两条硬约束：四节头逐字复现独占一行；不得把本题轨迹中的具体数值/文件名/实体名抄进 skill（A5）。
- 契约校验（block 内，ModelOutputError）：skill_actions 恰好覆盖组内每条 skill_id 一次、action 词表闭合、revise/delete 必带非空 problem；G11 词表闭合、skill_text 非空。marker 保结构校验在组合层（`skill_markers(new) == skill_markers(old)`，K5 先例）。
- 修改落点与 G6/K4 同形（version+1 + rewrite_history + 立即 save）；delete 复用 `discard_skill`。
- 组合级修复预算 2 次/调用；token 预算沿用角色表。

### 2.4 持久化与续跑

状态全部从磁盘推导：

| 状态 | 推导来源 |
|---|---|
| 轮 r 某组是否执行完 | 轮 store 中该组应有单元（task × n_attempts 全量）是否全部已存**或**在 skip 并集内（skip 单元不执行、不落轨迹，由 expected 口径自然计为 infra） |
| 轮 r 判定 | 基线计数 × 轮 store 计数 × 具名规则——纯函数；**重算与账本首行不一致 → CodeBugError**（store 被外部改动的防腐层） |
| 归因是否已发生 | `live_run/attributions.jsonl` 有无该 (task, round) 行（append-once 首判为准） |
| 某修订是否已应用 | `skill.version > 归因行记录的该 skill 版本`（或 delete 已落盘）；重放守卫：版本 == from_version → 施加；== from_version+1 → 跳过（该版已落盘即为事实，版本号即完成证明）；**其余 → CodeBugError** |
| 组终态 | 成员 status；先 finalization sweep（有 verdict/终态决定的组幂等补完），再枚举 pending 组 |

**账本行 schema**（审查 R1-P6/R2-P10/R3-M10 的显式化）：

- `live_run/verdicts.jsonl` 行：`{schema, task_id, round, rule, baseline: {valid, success}, live: {valid, success}, better, injection_tokens, judged_with_shortfall, aux}`，**零执行终态行多一个 `verdict` 字段**（取 weak_already_knows / live_injection_overflow / live_undetermined，键的存在即行种类的判别式）——`judged_with_shortfall` 标记该轮是否带已接受空洞判定（shortfall 判定的事后可辨识性；含 stage1 继承的空洞与本轮自接受的空洞两种来源，行上不区分）；`aux` = 该 task 的 aux 计数（**裁决 11 的落点**：声明了 aux_metric 的 benchmark 并列记录 aux_scorer 计数、不参与判据，未声明时缺省 null）。**零执行终态同样落 verdict 行**（计数侧记 null）——审计的完整事实源；
- `live_run/attributions.jsonl` 行：`{schema, task_id, round, diagnosis, skill_actions: [{skill_id, version, action, problem}], injection_block_sha}`——`version` 由 K6 append 时从 store 读入补记（模型不提供）；
- `live_run/attribution_failures.jsonl` / `revision_failures.jsonl`：每次修复重试失败一行、耗尽标 exhausted，预算跨崩溃持久（K4 `rewrite_failures` 先例）。

**expected 口径**（定稿评审修正）：live 侧与 baseline 侧**都传全量应有单元集**给 `compute_task_metrics`（stage6 先例：`_expected_units` / `_bare_stage1_trajectories` 的读取语义）——skip 的单元不执行、无轨迹，D3 的 expected 口径自然把它们计为 infra（不进 valid 分母）；两侧同减同一批单元，率比较的分子分母自动对齐。禁止给 D3 传"已减 skip"的 expected（那会让 skip 单元从计数中整体消失，verdict 行失去"哪些单元是空洞"的自描述性）。

崩溃点与恢复（审查三份的并集，全部由上表推导）：轮内 K1 执行中（K1 逐单元续跑，**重派发只含轮 store 不完整的组**）→ 判定后提交前（重算一致断言 + sweep 幂等补齐）→ 归因调用中未落盘（重问——未持久化模型调用的既定豁免）→ 归因落盘后修订中（版本号守卫跳过已施加项）→ 修订后轮 1 前（audit 存在 ⇒ 只读并断言一致；不存在 ⇒ 纯函数推导并首写冻结）→ 轮 1 同构 → 摘要后 raise 前（续跑重写累计摘要）。已知不等价点如实登记于 docstring：结果未落盘的模型调用会被重问。

### 2.5 失败处置与 shortfall 全链路（修正后）

| 情形 | 处置 |
|---|---|
| G10 预算耗尽（含其契约校验失败计入；marker 保结构校验不在此列——它属 G11 修订路径，见下） | 全组 discarded(model_output_error)，先终态后账本行 |
| G11 单 skill 预算耗尽（含 marker 集合校验失败计入——结构校验 raise 必须落在 ModelOutputError 修复预算的作用域内，K5 教训） | 该 skill discarded(model_output_error)，组继续 |
| InfrastructureError（归因/修订/执行） | 组保持 pending、记账本、其余继续、阶段末 raise（退出码 3）、续跑重试 |
| live 单元 infra 耗尽 | 默认不接受（续跑补全）；`--allow-infra-shortfall` 时写入 **`live_run/round{NN}/accepted_infra_shortfall.json`**（`{"unit_ids": [...]}`，**按轮**——轮 1 的远程 run id 因注入块变化而不同于轮 0，新沙箱实例，轮 0 坏 ≠ 轮 1 必坏；"轮 0 坏则轮 1 同跳"若将来要作为政策须显式预注册，不得作为实现副作用）；判定在剩余有效样本上按率进行，verdict 行带 `judged_with_shortfall`；**全单元皆空洞 → live_undetermined 终态（先于归因短路）** |
| 每轮 K1 的 skip_unit_ids | **stage1 已接受弱臂空洞（过滤 role==weak、限本 task）∪ 该轮自己的已接受空洞**（并集） |
| 基线全成功 | weak_already_knows 零执行（strict 族限定，见 §2.2） |
| 基线 valid==0 / 未接受缺失 | ConfigError / InfrastructureError（不可达守卫） |
| 归因全 delete / 幸存组空 | 不跑轮 1，组终态即各成员已落的 discarded |
| no-op（块 sha 不变） | 全组 live_no_improvement，不跑轮 1 |
| 注入超长 | injection_audit 首写时估算 token；`max_injection_tokens` 非 null 且超限 → **该组 discarded(live_injection_overflow)（不进 K1 派发，零执行浪费；证据在 audit 行）**，不烧整 run 的 ConfigError 停机 |

### 2.6 侵入面（修正后）

**K6 公开面**（T3 交付物，T4 依赖此接口）：`composites/live_acceptance.py` = 逐组阶段函数库——`load_baseline(store, task)` / `expected_units(task, skip_ids)`（全量应有单元集的推导助手） / `audit_row(group)`（injection_audit 行内容的构造与重推导——首写与续跑比对共用同一函数，杜绝"写的人和比的人算得不一样"） / `judge_round(...)` / `attribute_group(...)` / `apply_revisions(...)` / `no_op_guard(...)` / `finalize_group(...)` / `survivors_and_block(...)`，各自从磁盘推导、幂等落盘；三个账本、skip 集推导与修复预算归它。`stages/stage4_live_run.py` 拥有：轮序推进、K1 调用与重派发集判定（§1 A10 的"非终态 ∧ 不完整"）、injection_audit 首写（内容经 `audit_row` 构造）、shortfall 记录、summary、阶段末 raise。

| 文件 | 动作 |
|---|---|
| `core/config.py` | 改：acceptance 字段 + 白名单删键 + 校验（§2.1） |
| `core/ids.py` | 改：+`raw_task_id`（**必做**——TB 的 `_TASK_NAME_RE` 拒绝含冒号的带前缀 id，tb/plugin.py:200） |
| `blocks/rules.py` | 改：+ACCEPTANCE_IMPROVE + 两规则（纯增量） |
| `blocks/attribution.py`、`prompts/attribution.py` | 新增（§2.3；含防御性注册 import） |
| `composites/live_acceptance.py`、`stages/stage4_live_run.py` | 新增（§2.6 分工） |
| `stages/stage0_freeze.py` | 改：live_run 时规则名校验 + `_prompt_digests` 签名携带 mode、条件 import 并入两个新 digest 名（exam 时两分支 no-op） |
| `stages/stage4_acceptance.py` | 改：run() 顶部 4 行懒 import 分发 + docstring（exam 本体一行不动） |
| `composites/assemble_reuse.py` | 改：仅 `_prompt_digests` 复制品的 live 目标条件同步（A15）；白名单零改动 |
| `cli.py` | 改：仅 `--allow-infra-shortfall` 的 help 文本加 stage4-live（行为零变化） |
| `tests/unit/core/test_config.py` | 改：**`test_to_dict_covers_every_field` 是唯一修改的既有测试**（改为断言 None 时缺键、非 None 时在——它测序列化形状不测行为）；新增 golden 与校验矩阵 |
| `README.md`、`AGENTS.md`、`log.md`、`docs/`、示例配置 | 文档回写（§2.8） |

**不许碰**：K1/K3/K4/K5、blocks/exam|audit|author|metrics|view|execute、`prompts/__init__.py` 与全部既有 prompt、`core/models.py`、`infra/*`、`stages/pipeline.py`、`stages/stage1|2|3|5|6.py`。其余既有测试零修改。

**exam 零行为变更证据链**：① 冻结面（白名单删键 + 条件 digest + 零新角色 → golden 逐字节；fixture **必须覆盖 full 模式（split=None）与 split 模式各一份**——只测 split 会让通用 None 过滤的陷阱存活）；② import 图（懒 import）；③ 行为（K1–K5 与 exam blocks 零 diff；**行为类测试**零修改全绿——唯一例外是序列化形状测试，见上表）；④ 在飞 run 续跑路径（freeze 比对经 ①）。

### 2.7 测试计划（fake model 全程）

- golden 兼容：full + split 两形态的旧式配置在新代码下 `to_dict()` 与 freeze 映射逐字节等于固定基准；live_run 配置的 manifest 含两个新 digest 与 acceptance 键；未知规则名/mode 名 ConfigError；`{}` ≡ exam。
- 单测：`_validate_acceptance` 全矩阵（含 n_attempts=1 拒、exam 多余键拒）；strict_rate_gain（升/平/降、交叉乘法、n 不等场景、防御分支）、first_success_gain；G10/G11 契约全分支。
- 组合测试：快乐路径全分支（轮 0 过 / 两轮败丢 / 修订后过 / 全 keep / 全 delete / no-op / weak_already_knows / 归因耗尽 / 修订失败单条终态 / **shortfall 接受后判定 + 接受空洞后续跑不再执行该单元** / live_undetermined / live_injection_overflow 派发前拦截）；**A9 继承空洞用例**（含 stage1 双臂 shortfall fixture——验证只继承 weak 臂、限本 task）；**入口守卫用例**（基线 valid==0 → ConfigError、未接受缺失单元 → InfrastructureError、基线全成功 → 零执行终态且不进 K1）；**重算一致性守卫用例**（外部改 verdict 行后续跑 → CodeBugError）；**懒 import 断言**（exam 配置驱动的 stage4 进程不加载 `blocks.attribution`/`prompts.attribution`/`stage4_live_run`——sys.modules 检查）。
- **中断续跑矩阵**：一次跑完作参照，每次模型调用与写盘前后注入崩溃并续跑，断言成员状态、三账本、轮 store、summary 与参照完全相同；含"归因落盘后崩溃不重问"（FakeClient 计数）、"预算跨崩溃续用"（账本行数）、"宽集重派不发生"（修订中崩溃后续跑不撞 `_check_stored`）、"零执行终态组在续跑时不再进 K1"（Major-1 回归）。
- 模式切换回归：同 fixture 无键 / `{mode: exam}` / live_run——前两者产物逐字节相同；**assemble_reuse 四组合测试归 T4**（baseline×{exam,live} 目标放行、bank×{exam→live, live→exam} 拒绝、显式 `{mode: exam}` 复用被拒的保守行为）。

### 2.8 README 与文档回写（含审查遗漏项）

G 表 +G10/G11、K 表 +K6、阶段→积木表 stage 4 双模式、prompt 表 +两条（条件冻结说明）；§2 落盘布局 +`stage4_acceptance/live_run/` 子树与 `work/stage4_live/`、discard_reason 新词与证据基础差异；§3 阶段 0 补 acceptance 键语义与白名单删键规则；§3 阶段 4 双模式成文（判据预注册与噪声数字按 n 写、no-op/天花板守卫及其 strict 族限定、D8 裁决、失败处置表、shortfall 按轮语义）；运行规则表阶段 4 行按模式分写（**正文"阶段 2–4 空洞不能接受"须改为按模式分述**，`--allow-infra-shortfall` 行加 stage4-live）；§4 已知风险 +4 条（3v3 功效局限、live 通过≠迁移、K5 回归仍文本考、**逐 skill 修订可能互相重叠**）；**full 模式语义声明**（live 验收与 stage6 同 task，任务定制 skill 抬高 test 是 full 模式的定义）；AGENTS 目录树 +4 文件；live run 完整性审计模板（组终态↔账本一致、轮 store 轨迹数=应有减 skip、账本口径自洽、shortfall 与 failed_units 对账）列为 T5 交付物。

### 2.9 实施拆分（修订三；文件所有权互斥，依赖序 T1 → T2 → T3 → T4 → T5）

| # | 子任务 | 独占文件 | 依赖 |
|---|---|---|---|
| T1 | 配置底座（含 `test_to_dict_covers_every_field` 的形状修改 + config golden——full/split 两形态 fixture） | `core/config.py`、`core/ids.py`、`blocks/rules.py`、`tests/unit/core/`、`tests/unit/blocks/test_rules.py` | 无 |
| T2 | prompt 与归因积木 | `prompts/attribution.py`、`blocks/attribution.py`、`tests/unit/blocks/test_attribution.py` | T1（接口约定） |
| T3 | K6 阶段函数库（§2.6 接口即 T3/T4 契约；skip 集推导函数归 K6，stage4_live_run 传入 shortfall/stage1 记录调用它——避免接缝重复实现） | `composites/live_acceptance.py`、`tests/composites/test_live_acceptance*.py` | T1+T2 |
| T4 | 阶段接线与冻结面（stage0 golden fixture + assemble_reuse 四组合测试归此） | `stages/stage4_live_run.py`、`stages/stage4_acceptance.py`、`stages/stage0_freeze.py`、`composites/assemble_reuse.py`、`cli.py`（help 一行）、`tests/unit/stages/*` | T3 |
| T5 | 文档与审计（README 的积木表登记**随对应代码任务落地**，AGENTS §11.5 顺序：README → decisions → 代码 → log.md）；**裁决托付的交付物在此挂账**：live_run runbook 模板（引用 §3 裁决 5 的 shortfall 预注册正本 + ALFWorld 三项义务——裁决 5/10）与实施收尾清单（含 schema_version 升版清理 v3——裁决 7） | `README.md`、`AGENTS.md`、`log.md`、`docs/`、示例配置、审计脚本模板、runbook 模板 | T1–T4 |

每任务收尾：全量 pytest + `ruff check --select E9,F` + `test_layering.py`。**合并门**（全部合入后、放行到在飞 run 所在 checkout 前）：
1. 老配置 `--until-stage 0` 干跑，manifest 与合入前产物逐字节比对；
2. **真实 TB pilot**：1–2 个 task 全两轮干跑（含一次故意归因失败路径），检查表——远程 run id 不撞（轮 0/轮 1/stage1/stage6 四方）、injection_audit token 实测、verdict 行 schema、注入块逐字节 = audit sha、pilot 中途 kill 一次再续跑验证等价、对照真的是对照（基线轨迹确来自弱臂裸跑、`skills_injected` 确为组引用）；
3. pilot 数字（归因输入 token 量级等）回填 README 披露，不当作已知数先写。

## 3. 用户裁决记录（2026-10-14，经管理者代传；原开放问题 11 项全部闭卷）

用户裁决："归因者可以删 skill，其他均按 agent 推荐设置。"逐项落地：

| # | 原开放问题 | 裁决与落地 |
|---|---|---|
| 1 | 判据选名 | **`strict_rate_gain`**。首个 live_run 实验配置写此名（配置仍必填显式写，无默认——预注册纪律不变）；`first_success_gain` 留注册表作消融备选，不进配置 |
| 2 | K5 混搭缺口 | **保留 exam 回归复考，K5 零改动**（§0 共识第 10 条）；"live regression"升级路径不启动，留档 |
| 3 | 归因者角色 | **author 归因 / rewriter 修订**（方案原设计确认） |
| 4 | 归因输入 | **v1 按原设计**（基线状态行摘要 + live 全视图 + task_prompt 渲染产物）；pilot 后再议增强 |
| 5 | shortfall 接受标准 | **按预注册建议执行，标准为**：live_run 正式 run 默认**不开** `--allow-infra-shortfall`（与 TB v2 阶段 6 同纪律——重试耗尽当场同命令续跑；不靠开关兜底）；仅当用户明示允许时，接受标准 = 单单元经一次续跑补跑后仍 infra 耗尽（即至少两轮完整重试），且总量 > 15% 的组停下诊断；双轮同 policy；接受必须先落 durable 记录再判定（§2.5）。此文本即预注册正本，runbook 引用之 |
| 6 | n_attempts 定标 | **TB = 3**（= n_attempts_weak）；ALFWorld/SearchQA 启动前按其画像单独预注册（本地执行便宜可升 5，噪声数字按 n 重算后写入其 runbook） |
| 7 | schema_version 技术债 | **在飞 run 完结后统一升版清理（v3）**，届时把白名单删键退役为普通 complete-don't-reject；登记在实施收尾清单 |
| 8 | 轮数 | **钉死两轮**（用户协议原文），不实现 `max_revision_rounds` 配置面 |
| 9 | delete / live_undetermined | **归因者（G10）明确允许 delete**——skill_actions 词表 keep\|revise\|delete 维持（§2.3 契约不变，delete 即终态 `live_revision_deleted`）；**live_undetermined 全空洞组丢弃**确认 |
| 10 | ALFWorld 适配 | 结构性改动为零（K6/E1 benchmark 盲）；**串行 runner 瓶颈 + 32k 上限 + 判据深度**三项写入其启动前 runbook 义务清单（32k：`max_injection_tokens` 设非 null） |
| 11 | aux 指标 | **采纳"verdict 行并列记录 aux、不参与判据"**（aux_metric 预注册纪律） |

裁决后方案无剩余开放问题。三方定稿评审（final_review_{1,2,3}.md：0 blocker，合并去重 5 major + 约 20 minor）的收敛修正已于修订三全部并入正文各节——其中四处由两路以上独立确认（裁决 11 的 aux 槽位、expected 口径自相矛盾、A15 理由句错置、G10 预算行 marker 错置），评审 3 独有的重派发集"非终态"限定（Major-1）已修入 §1 A10 并配回归用例（§2.7）。方案可实施。
