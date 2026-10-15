日期：2026-10-15
一句话说明：架构师 A 对多方法框架重构方案的三轮独立审计全文（第一轮独立审计、第二轮交叉讨论、第三轮收敛表决），供 docs/decisions/multi_method_framework_20261015.md 的收敛记录追溯。
对应 log.md 条目：2026-10-15 多方法框架方案定稿，建立 MM 独立副本。

# 架构师 A：三轮审计全文

分工说明：A = 代码事实与可行性，B = 扩展性与反过度设计，C = 迁移路线与可复现风险。评审基于仓库 bcb3ffe，全程只读。

---

## 第一轮：独立审计

# 架构审计 A：多方法框架重构方案 v1

审计人：独立架构师 A（主视角：代码事实核查与可行性）
对象：/tmp/arch_review/plan_v1.md；仓库 ExpeDistillation-TB-dev @ bcb3ffe（feature/acceptance-live-run），只做了静态阅读
标尺：用户偏好"不引入复杂 hash、审计等过度设计；整洁与可扩展第一"

---

## 0. 总体评价

方向对。"在阶段与积木之间加一层粗粒度方法槽位；阶段变薄；冻结集合按选中方法来算"这个判断，和代码里的真实痛点吻合。第 1 节大部分出处准确（行号基本都对得上）。

但方案有两类系统性问题：

1. **重复被低估了，而且没说清根因。** 多数重复不是"缺公共上下文层"造成的，而是分层规则逼出来的：composites 不许 import stages，于是 stage0 的逻辑在 assemble_reuse 里复制了 5 个函数，stage1 的 shortfall 读取器复制了 4 份。
2. **第 2、4 节的接口没有在现有代码上验证过。** 主要有四处：
   - live_run 套不进 `run(ctx, candidates)`；
   - methods 放第 6 层后会反向 import stage1、K4；
   - "冻结 prompt 集 = 选中方法的并集"漏掉了不是方法的阶段；
   - `needs/provides` 是一个只有一个使用者的泛化机制，而且它是自动推断出来的，考题依赖在配置里照样看不见，恰好没解决 ④ 自己指出的问题。

结论：**可以做，但要先修正 4 个高/中高严重度问题（见第 2 节），并删掉 3 处过度设计。**

---

## 1. 事实核查表

| # | 方案中的判断 | 结论 | 证据与说明 |
|---|---|---|---|
| ①a | 验收模式分支在 config.py:296-362（校验） | 属实 | `core/config.py:296` `_validate_acceptance`，到 362 结束 |
| ①b | stage0_freeze.py:287/309 | 属实 | `stages/stage0_freeze.py:287`（`_prompt_digests` 里的 live 分支，条件 import prompts.attribution）；`:309`（live 才校验 improvement 规则名） |
| ①c | stage4_acceptance.py:171 分发 | 属实 | `stages/stage4_acceptance.py:171-175` 懒 import stage4_live_run |
| ①d | assemble_reuse.py:305 | 属实，但性质不是"分发" | `composites/assemble_reuse.py:305` 是复制过来的 prompt 名集分支，不是方法分发 |
| ①e | 注入方式在 config.py:233、stage6_test.py:435 | 属实 | `config.py:233` `_validate_skill_delivery`；`stages/stage6_test.py:435` |
| ①f | skill 写法 distill/diagnose 在 stage3:283 与 author.py:320，归为"方法选择散落" | 部分属实：出处对，定性错 | `stage3_generation.py:283` 是 `mode = "distill" if subtask.source == "strong" else "diagnose"`，按每段数据来源分派，两种写法每个 run 都会用到，**不是配置选出来的方法**。`author.py:179/319/340` 是同一个 mode 对应的渲染和前提守卫 |
| ①g | "每种方法参数校验约 70 行" | 部分属实 | 按**槽位**算，不是按方法：`_validate_skill_delivery` 233-294 约 62 行，`_validate_acceptance` 296-362 约 67 行，各自同时覆盖两种模式 |
| ①h | "每加一种方法改四处" | 属实，还少算了 | acceptance 实际还要改：`prompts/__init__` 的懒注册排除、`blocks/attribution.py` 顶部的防御性 import、golden 测试、README 1.6 |
| ②a | `_workers()` ×7 | 属实 | stage1:134、stage2:105、stage3:192、stage4:108、stage4_live:361、stage5:45、stage6:248 |
| ②b | `_k1_stagger`/`_allow_infra_shortfall` ×3 | 属实 | stage1:146/152、stage4_live:370/376、stage6:257/263 |
| ②c | 插件导入 ×3 | 不实（少算） | 实际是 **×5**：`blocks/execute.py:49`、`blocks/tasks.py:65`、`stages/stage1_baseline.py:228`（aux_wiring 里）、`stage2:114`、`stage3:121`。根因：`benchmarks/registry.get_plugin` 故意不负责 import（registry.py:6-11 的 docstring）。最省事的修法是在 get_plugin 里按需 import 插件包，一处解决 |
| ②d | `_prompt_digests` 在 stage0 与 assemble_reuse 近乎逐字重复 | 属实，但只说了 1/5 | assemble_reuse 还复制了 `_canonical`(315)、`_tasks_digest`(348)、`_explicit_split_union`(267)、`_load_spec`(283)，另有 `_diff_paths`(360) 复制自 infra.manifest。docstring 自己写明原因："composites may not import stages"（:49、:318） |
| ②e | stage4_live_run 与 live_acceptance 各有 `_ledger_path`/`_candidates`/`_expected_units` | 部分属实 | `_ledger_path`：两边都有（live_run:147，live_acceptance:227），而且 K4 `acceptance.py:116` 还有第三个。`_expected_units`(live_run) 与 `_unit_ids`(live_acceptance:332) 同体异名。`_candidates` 在 live_acceptance 里叫 `pending_groups`(400)。**漏了的**：`_frozen_audit_row`≈`_audit_row_of`(277)、`_verdict_rows`≈`_first_row`(264)、`_tally` ×2（stage4:134 / live_run:451）、候选过滤 ×3（stage4:117、live_run:192、la:400）、accepted-shortfall 读取器 ×4（stage1:110、la `_read_stage1_accepted`:313、live_run `_read_round_shortfall`:254、stage6 `_read_accepted`:126）。另外，候选过滤重写三遍是**刻意的**（决定文档 2.2："live side shares no helper with the exam stage's file"），目的是保住 exam 的冻结面不动。方案删掉懒注册之后这个理由就没了，应当明说 |
| ③a | StageContext 文档称只读/frozen，但没有 frozen | 属实（措辞稍有出入） | `stages/context.py:3` 写的是"never mutate it"；"frozen"这个说法出现在 `pipeline.py:361` 的注释；`context.py:13` 是普通 `@dataclass` |
| ③b | pipeline.py:363-365 动态挂属性 | 属实 | 原样在 363-365。另外，测试也依赖这种动态挂法：7 个测试文件写 `ctx.workers = …`，14 处直接构造 `StageContext(` |
| ③c | `getattr(ctx.config,"workers")` 是死分支 | 属实 | `ExperimentConfig`（config.py:451 起）没有 workers 字段；7 处 `_workers` 都先查 config |
| ④ | stage3 无条件出考题；K5 回归依赖原始 skill 的考题（curation.py:687） | 属实 | `stage3_generation.py:288-306`：skill 和 exam 成对生成、成对保存，续跑判定也按"成对是否齐全"（:249）。`composites/curation.py:687` `_source_exams`。**漏了一层语义耦合**：live_run 验收下 skill 从来没考过自己的考题，经 G11 修订后的正文却要在 K5 回归里通过"原始考题"，这比 exam 模式更严格，而且口径和 exam 模式不一样。这不是"产物有没有"能表达的 |
| ⑤ | curation 1274 / live_acceptance 1212 / assemble_reuse 1425 行，算法本体占小部分 | 部分属实 | 行数准确。按 AST 统计，docstring+注释约占 37%-45%：curation 有效代码约 656 行，live_acceptance 约 632 行，assemble_reuse 约 945 行。剩下的"持久化/防腐"主要是 README 1.4 规定的续跑等价契约，属于本质复杂度，不是"侵入"。称为"上帝模块"有夸大 |
| ⑥a | `prompts/__init__.py:25` METHOD_PROMPT_NAMES 是 import 时快照，live prompt 懒注册 | 属实 | `prompts/__init__.py:25`；`prompts/attribution.py:12`；README 1.6 |
| ⑥b | config.py:131 `_MANIFEST_OPTIONAL_FIELDS` 白名单 | 属实 | 已列在 runbook §7 的 v3 退役清单里 |
| ⑥c | 根源是"冻结名集 = 全部已注册" | 部分属实 | 决定文档 acceptance_live_run_20261014.md:25 写的是**三个"续跑炸弹"**：to_dict 全字段进 manifest、prompt 快照、MODEL_ROLES 补全。三者共同的根因是"正式 run 续跑时用的是会演进的代码"。方案只改冻结集合的算法，没处理这个根因（见第 2 节 R5） |
| ⑦a | MODEL_ROLES 写死（config.py:83），live 因此复用 author/rewriter | 部分属实 | 写死属实。复用的原因是决定文档要求"零新角色"（:166），A12 裁决本身又认可了这种复用（"诊断族/修订族"），并不纯粹是被迫。另有**命名陷阱**：`core/models.py:42` 也有一个 `MODEL_ROLES=("strong","weak")` |
| ⑦b | prompt 注册表没用 core/registry.py | 属实 | `prompts/registry.py:50` 自带 dict；core/registry.py 的 docstring 却声称三类注册表共用它 |
| ⑦c | pipeline 写死直线、阶段签名不一 | 属实 | `pipeline.py:386-412`；stage1(ctx, train_tasks, test_tasks)、stage2(ctx, failed_task_ids)、stage6(ctx, test_tasks)，其余都是 run(ctx) |
| 漏项 | 阶段之间互相 import | 方案未提 | `stage4_live_run.py:132-133` import 了 stage1_baseline（aux_wiring、accepted_infra_shortfall）和 stage4_acceptance 的常量；`stage6_test.py:107` import 了 stage1_baseline |
| 漏项 | K6 调用 K4 的 `discard_skill` | 方案未提 | `composites/live_acceptance.py:77`、`stage4_live_run.py:120` 都从 `composites.acceptance` import。方法迁入 methods 后会直接违反"方法互不 import" |
| 漏项 | skill 正文标记是跨槽位协议 | 方案未提 | 方案说"槽位间协议是 Skill.status"，但正文标记同样是协议：`core/models.py` 的 PROCEDURE/FAILURE_REFERENCE_MARKERS 被 K5 标记门（curation.py:722）、D11 检索加权（retrieve.py:34/55）、G11 保节修订（la:73）共同依赖 |
| 漏项 | 跨参数约束没有校验 | 方案未提 | live 的 `acceptance.n_attempts` 必须 ≤ `n_attempts_weak`，否则 `load_baseline`(la:433) 会把缺的单元报成误导性的 InfrastructureError；目前只在决定文档 :57 里写了"建议相等"，代码不校验 |

---

## 2. 缺陷与风险（按严重度排序）

### R1【高】分层放置不可落地：methods 放第 6 层后会出现反向依赖和同层互引

- live_run 迁到 `methods/acceptance/` 后仍然需要：
  - `stage1_baseline.aux_wiring` 和 `accepted_infra_shortfall`（stage 会变成第 7 层）；
  - `stage4_acceptance.STAGE4_DIR` 和 `SUMMARY_FILENAME`；
  - K4 的 `discard_skill`（违反"方法互不 import"）。
- assemble_reuse（第 5 层）要算 prompt 集，就得知道"选中的方法"，即需要方法注册表（第 6 层）。core 的 `ExperimentConfig.__post_init__`（第 0 层）也无法再校验方法参数。
- **修改建议**：迁移前先把共享件**下沉**：

| 共享件 | 去处 |
|---|---|
| accepted-shortfall 读/写/skip 集（4 份合成 1 份） | `composites/shortfall.py`（第 5 层） |
| stage-3 候选判定 | `core/ids.is_stage3_candidate(skill)` |
| `discard_skill` 与 `_tally` | `composites/skill_status.py` |
| `aux_wiring` | `benchmarks/registry.aux_scorer(name)`（第 3 层） |
| `tasks_digest` 与 `_canonical` | `blocks/tasks.py`（第 4 层） |

  然后把 assemble_reuse **上移到第 7 层**（`stages/assemble.py`），直接调用 stage0 新拆出的 `build_manifest(config, tasks)`，5 个复制函数一并删除。
- 方法参数校验从 core 移到"方法解析"这一步：`methods.resolve(config)` 由 pipeline 和 assemble 在 stage 0 之前调用。core 只校验 `{slot: str | {name:…, …}}` 这个形状。

### R2【高】exam 和 live_run 套不进 `AcceptanceMethod.run(ctx, candidates) -> AcceptanceSummary`

两者在以下几方面不一样：

| 维度 | exam | live_run |
|---|---|---|
| 执行单位 | (skill, exam) 对，经 `run_units` 并行 | task 组 |
| 执行方式 | 逐单元 | 轮栅栏 + 每轮一次 K1 大批 |
| 依赖 | — | stage1 基线、workdir_root、executor_weak 的模型名 |
| 状态来源 | — | 全部从磁盘推导：`pending_groups(store)` 在一次 run 里重算了 4 次，因为每一步之后状态都会变（stage4_live_run.py:321-472） |

由此带来三个问题：

- 传入一份 `candidates: list[Skill]` 快照，对 live 没用，而且会误导：G11 修订后版本号就变了，快照里的对象随即过期。
- 摘要结构也不同：exam 有 `skipped_missing_exam`；live 有 `group_details`、`infra_missing`，并且 `infra_failed` 里放的是 task id。
- 两边都是"先写摘要，再抛 InfrastructureError"。

**修改建议**：签名改为 `run(ctx) -> dict`，由方法自己从 store 枚举（共用 `is_stage3_candidate`）。dict 约定 4 个公共键：`accepted`、`discarded`、`infra_failed`、`processed_this_call`；其余键由方法自由添加。stage 负责写 `stage4_summary.json`，并在 `infra_failed` 非空时抛异常。这样"薄阶段"才真正成立。不要定义 AcceptanceSummary 类型。

### R3【高】"冻结 prompt/角色集 = 选中方法声明的并集"漏掉了不是方法的阶段

在 P3 的时间点，segmentation、authoring、curation 还不是方法（P4 才迁）。按方案的公式，SEGMENT、SKILL_DISTILL、SKILL_DIAGNOSE、EXAM_GEN、G7-G9 这些 prompt，以及 segmenter、examiner、curator 等角色，会从冻结集里消失。要补上，就得让所有阶段都实现方法接口，这又和方案自己的"单实现不建接口"冲突。另外 exam 验收和 K5 回归共用 ANSWER/JUDGE prompt 以及 answerer/judge 角色。

**修改建议**：

- 在 prompts 里放一个静态 `BASE_PROMPTS`（固定阶段用到的 prompt，取代 METHOD_PROMPT_NAMES 快照），在 core 里放 `BASE_ROLES`。
- 冻结集 = `BASE ∪ 选中方法.prompts ∪ 插件任务 prompt`，角色同理。
- 所有 prompt 模块一律在 `prompts/__init__` 里急切注册，删掉懒注册和 `blocks/attribution.py` 的防御性 import。
- 顺手把 `core.config.MODEL_ROLES` 改名为 `BASE_ROLES`，避免和 `core.models.MODEL_ROLES` 混淆。

### R4【中高】needs/provides 是过早泛化，也没解决 ④

- 全仓库只有一对生产者/消费者（考题），这不满足方案自己"第二个实现出现才建接口"的原则。
- 自动推断"有人 needs exam 才出题"意味着配置里依然看不到"本 run 出不出考题"，和 ④ 的诊断相反。
- 产物集合检查表达不了语义约束（live 下 K5 回归的口径差异），也表达不了跨参数约束（`n_attempts ≤ n_attempts_weak`）。
- 按需出题会改动 stage3 的成对续跑判定（:249）和 `exam_missing_advanced` 分支，代价不小。

**修改建议**：

- 删掉 needs/provides。
- 改为显式参数 `authoring: {exams: true}`（缺省 true），落进冻结配置。
- 给方法加一个可选类方法 `check(params, config)`，启动时调用一次，集中写跨槽位约束，例如：
  - `exam` 验收或 curation 的回归开启时，要求 `authoring.exams`；
  - live 要求 `n_attempts ≤ n_attempts_weak`。
- K5 在 live 下的口径差异写进决定文档；是否给 curation 加 `regression: exam|none`，留给第二个 curation 实现出现时再定。

### R5【中】删掉 `_MANIFEST_OPTIONAL_FIELDS` 却没处理它的根因

方案冻结的是"补全默认值后的 methods dict"。那么给已有方法**新增一个带默认值的参数**，补全后的 dict 就变了，在飞 run 续跑会被拒绝，这正是白名单当初要解决的问题。"改行为 = 注册新名字"覆盖不了"加一个可选参数"这种常见演进。

**修改建议**：把"在飞的正式 run 固定在启动时的 checkout（独立 git worktree）上续跑"写成 AGENTS 的**常设规则**，而不只是 v3 过渡期的一次性安排。这样代码里永远不需要兼容补丁，manifest 也不需要新增字段：冻结只做比对，不一致就拒绝。这是零代码、零 hash 的方案，最符合用户偏好。

### R6【中】`ctx.workspace() → run_dir/<slot>/<name>/` 的目录迁移收益低、破坏面大

- K4/K6 的路径是从 `store.run_dir` 推出来的：
  - `acceptance.py:116`；
  - `la.LIVE_DIR_NAME="stage4_acceptance/live_run"`(:125)，几乎每个 K6 函数都经过它。
- 迁移要改 K6 几十个函数签名，还会连带打破：
  - assemble_reuse 的负清单（:140-173）；
  - `scripts/audit/audit_live_run.py`、`scripts/run/fork_run_stage04.py` 等脚本；
  - runbook；
  - P1 承诺的"产物逐字节相同"（路径一变，账本位置就不同了）。

**修改建议**：沿用现有布局。每个方法用一个常量声明自己的目录，比如 exam 用 `stage4_acceptance/`，live 用 `stage4_acceptance/live_run/`，retrieval 用 `stage6_test/`。`workspace()` 只返回这个常量路径。

### R7【中】authoring 槽位的诊断和协议定义有误

- distill/diagnose 是**数据驱动的必经双分支**，不是可替换的方法（见核查 ①f）。
- 新的写法会碰到 K5 标记门、D11 加权、G11 保节修订这三处对正文标记的依赖。

**修改建议**：在槽位协议里明确写入"Skill.status + 正文标记集（core.models 的两组 MARKERS）"。新写法要么产出既有的标记结构，要么同时声明自己的标记集，并让三个消费方按集合工作。authoring 的优先级从"高"降到"中"。

### R8【中】迁移路线里有不必要的过渡复杂度，也有对测试工作量的低估

- P0 说"纯重构、1362 测试全绿"，但要改测试夹具：7 个文件里动态挂 workers，14 处构造 StageContext。这部分工作量应当计入。
- P1 要求"旧配置与转换后新配置"都能跑，意味着一段时间内要双格式加载。可是迁移本来就安排在正式实验结束之后，老 run 用老 checkout，**不需要在代码里兼容 v2**。
- **修改建议**：一次性切到 v3，用一次性转换脚本改写 10 份 configs。等价性改为"老 checkout + 老配置"对比"新 checkout + 新配置"，在假模型上比较 store 对象和账本（布局不变，见 R6），不比较 manifest。

### R9【低】其余

- "方法互不 import"在 `tests/test_layering.py` 里不会被检查（它只查包和包之间的方向），需要加一个约 10 行的检查。
- `slot` ClassVar 与注册表名重复：二选一，保留注册表即可。
- 配置示例里 segmentation/curation 也带了 `name:`，和 P4"单实现不建接口"矛盾。单实现槽位只放参数组，不要 name。等出现第二个实现时再加 `name`，缺省值就是现有实现。在 R5 的固定 checkout 规则下，这不需要再升 schema。

---

## 3. 应删减的过度设计，与应补充的遗漏

**应删减**

1. `needs/provides` 集合机制，改为显式 `authoring.exams` 加 `check()`（R4）。
2. `MethodContext` 作为第二种上下文类型：把 StageContext 改成 frozen，加上 `settings` 和 `plugin`，全仓库只用一个 `RunContext`。
3. `AcceptanceSummary` 类型：用 dict 加 4 个公共键即可（R2）。
4. 方法工作目录迁移（R6）。
5. `extends:` 深合并：目前只有 10 份配置，深合并的列表/None 语义本身就需要设计，可以后置。
6. "每槽位一个参数化契约测试（中断续跑等价）"作为通用测试框架：保留各方法现有的续跑测试；通用部分只做"未知参数键被拒绝"和"假模型能跑通"两项。
7. `slot` ClassVar（R9）。
8. K5 迁入 methods：curation 只有一个实现，留在 composites，由 stage5 直接调用。

**应补充**

1. 共享件下沉清单，以及 assemble_reuse 上移到第 7 层（R1）。
2. 固定阶段的 `BASE_PROMPTS` / `BASE_ROLES`（R3）。
3. "在飞 run 固定 checkout"作为常设规则（R5）。
4. `get_plugin` 统一负责 import 插件包，消除 5 处重复（核查 ②c）。
5. 跨参数校验钩子 `check()`（R4）。
6. 槽位协议里写入正文标记集（R7）。
7. 删掉懒注册之后，"live 不与 exam 共享 helper"（决定文档 2.2）的理由随之消失。应当明确合并 3 份候选过滤、2 份 `_tally`、2 对 live 账本读取器。
8. docstring 瘦身单独立项：live/curation 的事故史迁到 docs/decisions，按 37%-45% 的注释占比估算，可减重约 1/3。

---

## 4. 建议的修订

### 4.1 接口（methods/base.py，第 6 层）

```python
@dataclass(frozen=True)
class RunSettings:                 # 运行设置，不冻结进 manifest
    workers: int = 1
    k1_stagger: float = 0.0
    allow_infra_shortfall: bool = False

@dataclass(frozen=True)            # 取代 StageContext + MethodContext，全仓库只此一个上下文
class RunContext:
    run_dir: Path; store: Store; config: ExperimentConfig; usage: UsageRecorder
    handles: Mapping[str, ModelHandle]; workdir_root: Path
    settings: RunSettings; plugin: BenchmarkPlugin
    def handle(self, role: str) -> ModelHandle: ...

class Method:
    Params: ClassVar[type] = NoParams       # dataclass；未知键拒绝，在 __post_init__ 里校验
    prompts: ClassVar[tuple[str, ...]] = () # BASE_PROMPTS 之外额外要冻结的 prompt
    roles: ClassVar[tuple[str, ...]] = ()   # BASE_ROLES 之外额外需要的角色
    workdir: ClassVar[str] = ""             # 沿用现有布局的相对路径
    def __init__(self, params): self.params = params
    @classmethod
    def check(cls, params, config) -> None: # 可选：跨槽位/跨参数约束，启动时调用一次
        pass

class AcceptanceMethod(Method):
    def run(self, ctx: RunContext) -> dict: ...
    # 返回的 dict 必须含 accepted / discarded / infra_failed / processed_this_call；
    # 阶段负责写 stage4_summary.json，infra_failed 非空时抛 InfrastructureError

class DeliveryMethod(Method):
    def resolve(self, bank, tasks) -> tuple[Callable[[Task], tuple[Any, str]], list[dict] | None]: ...
    # whole_bank 返回常量 resolver（refs="bank:<12hex>"）和 None；
    # retrieval 返回逐任务 resolver 和审计行。stage6 一律走 skills_for_task 路径

ACCEPTANCE = make_registry("acceptance method")   # 复用 core/registry
DELIVERY   = make_registry("delivery method")
```

### 4.2 配置（schema v3，一次切换）

```yaml
schema_version: 3
rules: {task_select: any_weak_failed, trajectory_select: all, segment_select: strong_success_weak_failed}
segmentation: {K: 5, retry_limit: 2}          # 单实现：只放参数，不写 name
authoring:    {exams: true}                   # 显式写出，进冻结配置
acceptance:   {name: live_run, n_attempts: 3, improvement: strict_rate_gain}   # 多实现：写 name
curation:     {token_budget: 96000, rounds: 3}
delivery:     {name: retrieval, top_k: 5, max_categories: 3, min_score: 0.02}
```

- 冻结内容：
  - 补全默认值后的明文配置；
  - `prompt_digests` 覆盖 `BASE_PROMPTS ∪ 选中方法.prompts ∪ 插件任务 prompt`；
  - `model_roles` 覆盖 `BASE_ROLES ∪ 选中方法.roles`。
- 删除：`_MANIFEST_OPTIONAL_FIELDS`、`METHOD_PROMPT_NAMES`、懒注册、`acceptance_mode()`。
- assemble 的白名单路径改为 `config.delivery`（原来是 `config.skill_delivery`）。

### 4.3 分层与放置

| 层 | 内容 |
|---|---|
| 0 core | 增加 `ids.is_stage3_candidate`、`BASE_ROLES` |
| 1 prompts | 增加 `BASE_PROMPTS`；所有 prompt 模块急切 import |
| 3 benchmarks | `get_plugin` 按需 import 插件包；增加 `aux_scorer(name)` |
| 4 blocks | `tasks.tasks_digest`（从 stage0 和 assemble 合并而来） |
| 5 composites | K1、K2、K3、K5 原样保留；新增 `shortfall.py`（4 份读取器合一）、`skill_status.py`（discard_skill、tally） |
| 6 methods | `acceptance/exam.py`（stage4_acceptance 的本体 + K4）、`acceptance/live_run/`（driver.py + phases.py，合并重复的读取器）、`delivery/{whole_bank,retrieval}.py` |
| 7 stages | stage0..6 变薄；`stage0.build_manifest()`；`assemble.py`（原 composites/assemble_reuse） |
| 8 cli | 不变 |

stage1、2、6 与 K1 的关系：

- stage1、stage6 仍是固定阶段，不设槽位。三处 K1 调用方（stage1、stage6、live 方法）共用 `composites/shortfall.py` 和 `RunSettings`。
- stage2 直接调用 K2；第二种切段实现出现之前，不建 segmentation 槽位。

### 4.4 迁移步骤

- **P0（行为不变）**：
  - 改为 frozen RunContext + RunSettings，删掉 7 处 `_workers` 及其同类；
  - get_plugin 负责 import；
  - 下沉共享件（4.3 的第 0-5 层）；
  - assemble 上移到第 7 层，改用 build_manifest；
  - 同步更新测试夹具；
  - 全绿后提交。
- **P1（一次切到 schema v3）**：
  - 建 methods 层，注册 acceptance/delivery 两个注册表，加 Params 和 check；
  - 改为 BASE_PROMPTS/BASE_ROLES 并集冻结；
  - 删掉三处冻结补丁；
  - 用转换脚本改写 configs；
  - 等价性验证：老 checkout 与新 checkout 在假模型上比较 store 对象和账本，不比较 manifest；
  - layering 测试加上"methods 同层互不 import"。
- **P2**：
  - 落地 `authoring.exams` 和各方法的 `check()`；
  - live 方法内部合并重复的读取器；
  - 瘦身 docstring，事故史迁到 docs/decisions。
- **P3 起**：某个槽位出现第二个实现时，才给它建注册表并在配置里加 `name`。
- **常设规则（写入 AGENTS）**：正式 run 在启动时的 worktree/commit 上续跑；代码演进不为在飞 run 加兼容补丁。

---

## 第二轮：交叉讨论（提纲见决策文档附录 A）

# 第二轮表态：架构师 A

已通读 review_B.md 和 review_C.md，并回到仓库复核了几处关键证据。全程只读，没有碰 runs/。下文路径相对 `src/expedistill/`。

## 对 S1–S8 的反对

- **S1、S2、S3、S7：无反对。**
- **S6：无反对，补一条证据。** `.venv/.../__editable__.expedistill-0.1.0.pth` 确实存在。C 的说法成立：editable 安装只能指向一个路径，所以用 git worktree 隔离不够，必须是独立 clone 加独立 venv。
- **S4：无反对，补一句精确化。** 全仓库只保留一种上下文 `RunContext`。它不带 `config`，只带一个实验级视图 `experiment`：benchmark、mode、weak_profile、两个执行者锚点、n_attempts_weak。阶段需要的完整配置由 pipeline 显式传给阶段，不放进 ctx。
- **S5：无反对。** 补充 C 的 R13：解析 Params 必须发生在取锁和建 client 之前，以保持现在的报错时点（`stages/pipeline.py:330-337`）。
- **S8：有条件同意。** 原语只负责三件事：分桶、记录 infra 失败、汇总 `infra_failed`。**它不负责抛异常。**
  - 理由：现在四个阶段都是先写 summary，再抛 InfrastructureError：
    - `stages/stage2_segmentation.py:274-276`
    - `stages/stage3_generation.py:327-329`
    - `stages/stage4_acceptance.py:273-278`
    - `stages/stage4_live_run.py:631-639`
  - 如果原语在内部抛异常，summary 就写不出来。所以原语只返回分桶结果，由阶段写完 summary 后再抛。
  - ModelOutputError 的终结处理不进原语，各方法保留自己的回调。各处的写入顺序不一样：stage3 是"先写终态账本，再写失败行"（`:291-300`），exam 是"先 discard，再写失败行"（`:235-240`）。

## 对 D1–D7 的表态

**D1 考题依赖：选 (a) 的形式 + (c) 的时机，否决 (b)。**
- v3 首切时 stage3 照旧无条件出题。到 P4 和 `curation: none` 一起，加显式字段 `authoring.exams`：缺省 true，补全后写进冻结配置。跨槽位约束集中在 `check()` 里校验。
- **(b) 有硬伤，是协议变更，不是重构。** 理由有两条：
  - 现在 G3 失败会淘汰整段（`stage3_generation.py:284-300`），候选集就是"G2 和 G3 都成功的段"。改成懒生成后候选集会变大。
  - K5 并行等价性的前提是"同一波里各单元只读共享的不可变数据（包括 exams）"（`composites/curation.py:45`）。在波内懒写考题，会破坏这个前提。

**D2 冻结 prompt 集：改投"冻结全部已注册方法 prompt + 本 benchmark 的任务 prompt"，放弃我上一轮的 BASE ∪ 声明。**
- 理由：S6 成立后，懒注册存在的唯一理由（用新代码续跑旧 run）没了。全量冻结不需要任何声明，所以不会出现"声明和实际调用不一致"，也就没有漏洞。
- 唯一的前提：`prompts/__init__` 急切 import 全部 prompt 模块。用一条静态测试守住："prompts/ 下每个模块都被 `__init__` import"。
- 任务 prompt 仍然只取本 benchmark 的，否则冻结集会取决于进程里碰巧 import 了哪些插件（`stage0_freeze.py:58-61`）。
- 代价：
  - 新增或修改任何 prompt，都会改变之后**新 run** 的 manifest，旧 run 不受影响；
  - manifest 不再能告诉你"这个 run 实际用了哪些 prompt"，要看的话读 `llm_requests/` 即可。
- 这样一来，C 的"call_model 成员检查"和 B 的"调用 ⊆ 声明契约测试"都不需要了。

**D3 方法层：保留新增 methods/ 层（第 6 层），理由是职责边界，不是依赖关系。**
- S7 把共享件下沉之后，methods 只会 import composites 及以下各层，确实不再需要反向 import。
- 方法文件要做的事是"拿 RunContext、编排、处置失败"，这正是今天 `stage4_acceptance.run` 和 `stage4_live_run.run` 的内容。
- 如果把它们塞进 composites，有两条路，都不好：
  - composites 改用 ctx，违反积木规范"配置以显式参数传入"；
  - 每个方法拆成"composites 实现 + stages 适配器"两个文件。
- 新增一层的代价是一次性的：改 RANK 表和 AGENTS 分层表。同时采纳 C 的 R12：相对 import 也要检查，并加上"方法之间互不 import"的机器检查。

**D4 槽位清单与优先级。**
- P1 acceptance：现在就有 exam 和 live_run 两个实现。
- P2 delivery：现在就有两个实现。签名直接复用 K1 的 `skills_for_task`（B 的建议）。
- P3 authoring：前提是用户确有第二个 prompt 策略排期，有就按 B 的方案前移。最小改法是让 `write_skill` 接收 prompt 名参数；skill 与 subtask 一一对应写成槽位协议。
- P4 curation：新增 `none`。K5 代码留在 composites，方法文件只是一层薄包装，和 `authoring.exams` 同批上线。
- segmentation：不建槽位。

**D5 角色表：v3 首切不开放（同意 C）。**
- 现有和已规划的方法都不需要新角色。保持封闭，还能完全避开 C 的 R7：`tb21_split_live_run.yaml:211` 的 auditor 预算仍然合法，转换器不用删任何东西。
- 第一个真正需要新角色的方法（比如 embedder）出现时，再采用 B 的 `{role: "strong"|"weak"|None}` 映射。
- 只做一件事：把 `core.config.MODEL_ROLES` 这个常量改个名，避免和 `core.models.MODEL_ROLES` 混淆。角色名本身不变。

**D6 v2→v3 过渡：改投 C，条件是"适配器 = upgrade_v2 本身"。**
- `upgrade_v2` 是必要的。bank 级复用用新代码的阶段 6 去测已完结 v2 run 的冻结库，这正是这个工具存在的理由。身份比对前必须先把源 manifest 提升到 v3 视图：`_diff_paths` 会把单侧缺键算作差异（`assemble_reuse.py:360-372`）。
- P1/P2 在方法解析时调用同一个 `upgrade_v2`，manifest 仍按 v2 冻结。这样 golden 可以连 manifest 一起比对，到 P3 再一次性切换。
- 不允许出现第二条兼容代码路径。

**D7 验收标准：同意 C 的 golden 产物矩阵 + 易变字段归一化（只写在测试代码里），并且每个组合加一个"中途 kill 再续跑"的变体。**
- 每槽位契约测试只保留两项廉价的：假模型能跑通、Params 未知键被拒绝。
- 否决"按第 k 次写入崩溃、遍历所有 k"的通用框架。理由：现有方法专属的续跑测试已经覆盖了这一点，比如 `tests/composites/test_live_acceptance_resume.py`、`test_curation_resume.py`；而通用框架在矩阵上的成本是乘法级的。
- 同意 C 的 R5：迁移模块时不留 shim，模块和对应测试在同一个提交里迁。

## 我可以接受的最终折中

- **起步：**
  - 打 tag，在飞 run 迁到独立 clone + 独立 venv；
  - 在 tag 提交上生成 golden 矩阵；
  - 目录布局永久不变。
- **P0：**
  - 改用 frozen 的 RunContext + RunSettings；
  - 只合并真正等价的重复，加上 S7 的下沉，以及"不负责抛异常"的分桶原语；
  - assemble_reuse 上移到 stages 层，复用 `build_manifest`；
  - `get_plugin` 统一负责 import。
- **P1：**
  - 建 methods/ 层，用"函数 + 元数据"注册：Params、check，不带 prompts、roles、needs 字段；
  - 迁 acceptance，不留 shim；
  - v2 配置在解析时经 `upgrade_v2` 转换；
  - 扩展 layering 测试。
- **P2：** 迁 delivery。
- **P3：**
  - schema v3 一次切换；
  - prompt 一律急切注册并全量冻结；删掉懒注册、`METHOD_PROMPT_NAMES`、`_MANIFEST_OPTIONAL_FIELDS`；
  - 角色表不开放；
  - assemble_reuse 接入 `upgrade_v2`；
  - 阶段 0 遇到 v2 run 时报错，并明确指向 tag；
  - 同步 scripts/ 和 runbook，审计脚本在子树缺失时报 ERROR。
- **P4 及以后：**
  - authoring（有第二个策略时）、`curation: none` 加 `authoring.exams`；
  - 新角色和 provider 按需再做。
- **全程不引入** hash、版本号、needs/provides、extends、类型化 Summary、通用崩溃枚举框架。

---

## 第三轮：收敛表决（提纲见决策文档附录 B）

# 第三轮表态：架构师 A

R1–R7 全部接受。

- **R1 接受。** 只把驱动搬进 methods/，K4/K5/K6 本体留在 composites，C 担心的"patch 目标失效、测试空转也变绿"随之消失（`tests/composites/test_live_acceptance_resume.py:710-722`）。新的一层又保住了"方法拿上下文、积木收显式参数"的分界。
  - 补充：驱动本身也是 patch 目标（`tests/unit/stages/test_stage4.py:773`），所以迁驱动同样要守"同一提交迁测试、不留 shim、给断言'没发生'的测试补正向前提"。
- **R2 接受。** `exams` 缺省 true，现有配置的语义逐字节不变。`ensure_exam` 会改变候选集：今天 G3 失败会淘汰整段（`stages/stage3_generation.py:284-300`）。它还会让 K5 波内出现写入，打破"波内只读不可变数据"的前提（`composites/curation.py:45`）。
- **R3 接受。** P1a 期间 manifest 不变，现有 golden 能原样保持绿，出问题时可以二分定位。`upgrade_v2` 有一个可以预见的使用者：用 v3 才有的新 delivery 去复用在飞 v2 run 的冻结库。不升级的话，`_diff_paths` 会把单侧缺键算作差异，比对直接被拒（`composites/assemble_reuse.py:360-372`）。
- **R4 接受。** 在全量冻结下，唯一的绕过路径是动态注册，静态测试足以兜住。今天的注册点只有 `prompts/*` 和三个插件（`benchmarks/tb/plugin.py:133`、`searchqa/plugin.py:89`、`alfworld/plugin.py:103`）。
- **R5 接受。** `ROLE_DEFAULTS` 只是把 `core/config.py:567-568` 里写死的默认规则改成数据，角色名和预算校验都不变，同时消掉与 `core/models.py:42` 的重名。
  - 补充：None 档暂时没有使用者，只保留类型，不写分支逻辑，等第一个使用者出现再实现。
- **R6 接受。** 已有在飞 run 需要热修的先例（`scripts/run/prepare_inplace_recovery.py`），所以用维护分支比用不可变 tag 合适。
  - 补充：分支名要定成固定惯例，比如 `run/<run_dir 名>`，这样 stage0 的报错能直接给出确切的分支名。
- **R7 接受。** 补上 `n_attempts ≤ n_attempts_weak` 这条校验：它目前只写在决定文档 `acceptance_live_run_20261014.md:57`，代码里没有。超出时 `load_baseline` 会把缺的单元误报成 InfrastructureError（`composites/live_acceptance.py:433-483`）。实验级视图里已经含 `n_attempts_weak`，所以这条可以放在 live_run 方法的 check 里做。
