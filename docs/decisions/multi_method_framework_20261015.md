日期：2026-10-15
一句话说明：把 ExpeDistillation 升级为"方法按名插拔"框架的最终重构方案——初版方案经三位独立架构师（A 代码事实/可行性、B 扩展性/反过度设计、C 迁移/可复现风险）三轮审计讨论、裁决提案 R1–R7 全票通过后定稿，并附每个重构步骤的三路评审协议（用户裁决）。
对应 log.md 条目：2026-10-15 多方法框架方案定稿，建立 MM 独立副本。

---

## 0. 结论

1. **方向不变**：在"阶段"与"组合积木"之间加一层**方法驱动**（`methods/`），方法按名注册、从配置选择；阶段变薄。
2. **比初版大幅做减法**：删掉通用依赖声明（needs/provides）、`extends` 配置继承、方法基类与类型化摘要、按方法声明 prompt/角色、工作目录迁移；不引入任何新 hash、版本号、审计机制。
3. **可靠性只靠四样已有/极简的东西**：manifest 逐字段比对（已有）、"正式 run 固定在自己的维护分支续跑"运维规则、两条静态测试、只在测试侧的 golden 产物矩阵。
4. **执行地点与隔离（致命风险对策）**：TB-dev 的 `.venv` 是可编辑安装、指向其 `src/`，在跑实验续跑或进入新阶段时会加载改后的代码。因此重构在**本仓库（ExpeDistillation-MM，独立 clone + 独立 venv）**进行：origin = TB-dev 本地路径，基点 `bcb3ffe`（feature/acceptance-live-run 顶点），分支 `feature/multi-method`，本仓库 `.venv`（Python 3.11.16）的 editable `.pth` 只指向 MM 自己的 `src/`。TB-dev 在正式实验期间的任何热修经 `git fetch origin` + cherry-pick 同步进来。
5. **执行时点**：MM 只管自己的重构，不围绕 TB-dev 的状态编排任何等待或同步流程。**2026-10-15 用户裁决："升级为多方法框架 不用等正式 run"**——golden 矩阵已提前完成（§7.1），P0 随即开始；TB-dev 终态 tag 与维护分支归档是 TB-dev 侧的事，等它的 run 完结即可，与 MM 的重构互不阻塞。MM 想反映上游（含热修）时 merge origin 后重生成 golden 即可，无程序化要求。

## 1. 现状问题（经 A 事实核查修正后的版本）

| # | 问题 | 证据 | 核查结论 |
|---|---|---|---|
| ① | 方法选择用 if 分支散落：验收（exam/live_run）四处、注入（whole_bank/retrieval）两处 | `core/config.py:296,233`、`stages/stage0_freeze.py:287,309`、`stages/stage4_acceptance.py:171`、`stages/stage6_test.py:435`、`composites/assemble_reuse.py:305` | 属实 |
| ② | 重复：`_workers` ×7、`_k1_stagger`/`_allow_infra_shortfall` ×3、插件导入 **×5**、stage0 冻结逻辑在 assemble_reuse 复制 5 个函数、stage1 shortfall 读取器 ×4、`_tally` ×2、候选过滤 ×3 | 各 stage 文件；`blocks/tasks.py:65`、`stage1_baseline.py:228` | 属实且初版少算；**根因是分层规则**（composites 不能 import stages，只好复制） |
| ③ | `StageContext` 文档称只读但未 frozen，pipeline 动态挂 workers 等；`getattr(ctx.config,"workers")` 是死分支 | `stages/context.py`、`pipeline.py:363-365` | 属实 |
| ④ | 跨阶段隐式契约：stage3 无条件出考题，因为 K5 合并回归要用 | `composites/curation.py:687` | 属实 |
| ⑤ | 冻结补丁：live 两个 prompt 懒注册、`_MANIFEST_OPTIONAL_FIELDS` 白名单 | `prompts/__init__.py:25`、`config.py:131` | 属实；但 `METHOD_PROMPT_NAMES` 快照本身不是补丁，懒注册才是 |
| ⑥ | 未成文的跨槽位协议：skill 正文结构标记、skill↔subtask 一一对应、bank 行格式 | K5 标记门 `curation.py:712-750`、D11 加权 `retrieve.py:112`、G11 | 初版遗漏 |
| ⑦ | 缺校验：live 的 `n_attempts` 必须 ≤ `n_attempts_weak`，超了会被误报为 infra 错误 | `live_acceptance.py:433-483` | 初版遗漏 |
| — | "上帝模块"说法夸大：三个大文件 docstring+注释占 37–45%，有效代码 630–950 行 | — | 修正 |
| — | distill/diagnose 不是可配置的方法，是按数据来源的两条必经分支 | `stage3_generation.py:283` | 修正 |

## 2. 目标结构

```
cli (8)
 └─ stages (7)      按名取方法 → 调用 → 写摘要 / 阶段末抛 InfrastructureError；pipeline 固定槽位顺序
     └─ methods/<slot>/<name>.py (6)   方法"驱动"：拿 RunContext、编排、调用组合积木、处置失败
         └─ composites (5)   组合积木本体（K1–K6）原地不动 + 下沉来的共享件
             └─ blocks (4) / benchmarks (3) / llm (2) / infra·prompts·inject (1) / core (0)
```

- **methods 只放驱动，不搬组合积木本体**（R1）：K4/K5/K6 留在 composites，崩溃注入测试的 patch 目标不变（避免"patch 打空、断言没发生的测试空转变绿"）。
- 共享件下沉（S7）：shortfall 读写 → `composites/shortfall.py`；`discard_skill`/`_tally` → `composites/skill_status.py`；候选判定 → `core/ids`；aux 接线 → `benchmarks`；`tasks_digest` → `blocks/tasks`；`get_plugin` 统一负责导入插件包；stage0 冻结抽成纯函数 `build_manifest()`。`assemble_reuse` 上移到 stages 层复用它。
- 失败处置共享原语（S8，收窄）——**2026-10-15 执行时按用户"简洁化"裁决取消**：三处（stage2/3/4-exam）各约 3 行 try/except、返回形状各异（字符串标签 vs 元组），抽象的参数化成本高于重复收益；ModelOutputError 的处置顺序本就是各阶段的续跑契约（stage3 先写终态行、exam 先丢弃 skill、live 先写组终态），保持各阶段本地。此取舍列入 P0 评审的关注点，评审可推翻。
- `test_layering.py` 扩展：加入新层号；检查相对 import；方法驱动之间互不 import。

## 3. 方法接口（函数 + 注册元数据，不建基类）

```python
@method("acceptance", "live_run", Params=LiveRunParams, check=check_live_run)
def live_run(ctx: RunContext, params: LiveRunParams) -> dict: ...
```

- 注册复用 `core/registry.py`，每槽位一个注册表；注册表文档写明该槽位签名与**该槽位允许修改的共享字段**（如验收可改 Skill.status/version）。
- `Params`：dataclass；框架通用校验只覆盖最小集合（未知键拒绝、int/float/str/bool/Optional 类型）+ `__post_init__` 方法内约束。
- `check(params, experiment)`：可选，放**跨参数/跨槽位**约束（例：`n_attempts ≤ n_attempts_weak`；P4 起"exam 验收或 K5 回归 + `exams: false`"冲突）。
- 返回 `dict` 摘要，只约定少量公共键（如 `infra_failed`）；写摘要与阶段末抛异常归 stages。
- **单一 frozen `RunContext`**（含 `RunSettings`：workers、stagger、timeout、allow_infra_shortfall 等运维参数）。方法只拿到"自己的 Params + 实验级视图（benchmark、mode、weak_profile、执行者锚点、n_attempts_weak）"；stages 可读完整 config。

## 4. 槽位清单与时机

| 槽位 | 现有实现 | 何时建 |
|---|---|---|
| acceptance | exam、live_run | P1 |
| delivery | whole_bank、retrieval（复用 K1 现成 `skills_for_task` 接口） | P1 |
| authoring | 仅一种（distill/diagnose 是数据分支） | **用户提出第二种写法时立即建**；P0 先做零风险准备：`write_skill` 接收 prompt 名参数；正文标记集 + skill↔subtask 一一对应写入槽位协议 |
| curation | K5 | P4，与 `none` 同批 |
| segmentation | G1+D7 | 出现第二实现再建 |
| 规则类 / benchmark 插件 | 已是具名注册 | 不动 |
| 模型 provider | 仅 OpenAI 兼容 | 按需（与方法框架正交） |

不抽象：D9 注入格式（"验收与测试同一种注入"靠单一实现保证）、D5 视图、K1。

## 5. 配置（schema v3）

```yaml
benchmark: tb
mode: split
split: {...}
models: {executor_strong: ..., executor_weak: ...}
rules:                       # 不变，不并入 methods
  task_select: any_weak_failed
  segment_select: strong_success_weak_failed
  trajectory_select: strong2_weak2
methods:
  segmentation: {K: 5, retry_limit: 2}          # 单实现槽位：只写参数组，不写 name
  authoring:    {exams: true}                   # P1b 即写入形状；P4 才允许 false
  acceptance:   {name: live_run, n_attempts: 3, improvement: strict_rate_gain, max_injection_tokens: null}
  curation:     {token_budget: 96000, rounds: 3}
  delivery:     {name: retrieval, top_k: 5, max_categories: 3, min_score: 0.02}
token_budgets: {...}
```

- 冻结形式：补全默认值后的 `methods` 明文字典（现有 manifest 机制）。
- **不做 `extends`**：消融矩阵用脚本生成完整 YAML。
- **角色表不开放**（R5）：`core.config.MODEL_ROLES` 改为 `ROLE_DEFAULTS = {role: "strong"|"weak"}`（把写死的"answerer 用弱、其余用强"变成数据、消除与 `core/models.py:42` 重名）；角色名与预算校验不变。`None`（必须显式配置）档位等第一个使用者出现再加。
- **考题依赖**（R2）：`authoring.exams` 缺省 true，所有现有配置语义逐字节不变；否决 `ensure_exam` 懒生成——它会改变候选集（G3 失败今天淘汰整段，`stage3_generation.py:284-300`），且破坏 K5 并行"同一波只读不可变考题"的前提（`curation.py:45`）。
- **prompt 冻结**（D2）：冻结集 = `prompts/` 包注册的全部 prompt + 本配置插件的任务 prompt（即今天 exam run 的公式）。live 两个 prompt 改为在 `prompts/__init__` 正常 import；删除懒注册与 `_MANIFEST_OPTIONAL_FIELDS`。代价只是噪声：新增/修改任何 prompt 会改变之后所有新 run 的 manifest（旧 run 在维护分支续跑不受影响），manifest 会列出本 run 用不到的 digest。
- **两条静态测试**（R4，取代运行时成员检查）：(1) `prompts/` 下每个模块都必须被 `prompts/__init__` import；(2) prompt 只能在 `prompts/` 与 `benchmarks/<name>/` 中注册，且每个插件最多注册一个、就是它的任务 prompt。

## 6. 运维规则（写入 AGENTS 常设规则，R6）

- 每个正式 run 一条**维护分支**（命名惯例如 `run/<run 目录名>`，可 cherry-pick 热修，先例 `scripts/run/prepare_inplace_recovery.py`）+ **独立 venv**；正式 run 只在自己的维护分支上续跑。
- stage0 读到旧 schema manifest：报错"此 run 由 schema v2 冻结，请用它的维护分支 + 独立 venv 续跑"（不为此给 manifest 加字段）。
- 工作目录布局**永久不迁移**（远程 TB run id 命名空间含 store 目录真实路径、重试游标在单元工作目录，搬目录会让旧 run 续跑悄悄重派远程执行）。新方法目录照 `<stage_dir>/<method>/` 惯例，用常量声明。
- 失败账本与 `pipeline_log` 的阶段标签不改（CLI summary、审计脚本、runbook 都在读）。

## 7. 迁移路线

| 步骤 | 内容 | manifest | 验收 |
|---|---|---|---|
| **P-1 准备**（不改源码） | 当前 TB run 完结后：在 TB-dev 打 tag `schema-v2-final`；在飞/历史 run 建维护分支 + 独立 venv；在 tag 上生成 **golden 产物矩阵**：{exam, live_run} × {whole_bank, retrieval} × {full, split} + 崩溃续跑变体，假模型驱动，永不重新生成 | — | — |
| **P0 纯重构** | frozen `RunContext`+`RunSettings`；S7 下沉、`build_manifest`、`assemble_reuse` 上移；`get_plugin` 统一导入；`write_skill` 接收 prompt 名；只合并真正等价的重复（名字像但语义不同的不合并；K6/K4 刻意隔离的 helper 等 golden 建好后再合）。~~收窄版 `run_units_disposed`~~ 执行时取消（见 §2，简洁化裁决） | 不变 | 测试全绿（golden 快照为套件成员，无 diff） |
| **P1a 按名分发** | 建 `methods/` 层与注册表；迁 acceptance、delivery 驱动；扩展 `test_layering` | **不变** | 测试全绿；golden 无 diff（manifest 不变由既有 manifest golden 测试守住） |
| **P1b 唯一一次 schema 切换** | v3 配置形状 + Params/check（含 `n_attempts ≤ n_attempts_weak`，放这一步因为会让 `test_stage0.py:133/719/744` 夹具失败）；`authoring.exams: true` 写入形状；`ROLE_DEFAULTS`；全量 prompt 冻结 + 两条静态测试；删懒注册与 `_MANIFEST_OPTIONAL_FIELDS`；`upgrade_v2` 纯函数；一次性配置转换脚本（结果写新文件名）；stage0 旧 schema 提示；同步 scripts/ 与 runbook；审计脚本改为按 manifest 判断模式、live 子树缺失报 ERROR 而非 INFO（`audit_live_run.py:605-610`） | 切换 | 除 manifest 外产物与 golden 一致；`configs/experiments/` 全部 v2 配置升级后的 v3 manifest 人工审定一次；prompt 名集与 digest 等于 v2 golden |
| **P4 新协议**（按需、预注册） | `curation` 槽位 + `none`；放开 `authoring.exams: false` + 集中冲突校验；authoring 第二写法（一出现就做） | 新实验 | 每个新方法自带续跑测试 |
| **P5 按需** | 模型 provider 注册表、非 chat 调用（embedding）、插件提供真实"查询文本"、`ROLE_DEFAULTS` 的 None 档位 | — | — |

**`upgrade_v2`**（R3）：纯 stdlib 函数，只服务两处——`assemble_reuse` 读 v2 源 manifest 做身份比对（否则单侧缺键被算作差异，`assemble_reuse.py:360-372`），以及一次性配置转换。stage0/pipeline 永不加载 v2。v2 `acceptance` 的三种写法（缺键、`{}`、`{mode: exam}`）必须映射到同一个 v3 值；golden 测试覆盖全部 v2 配置。近期使用场景：用 v3 才有的新方法（如新 delivery）复用当前 TB v2 正式 run 的冻结库。

**迁移纪律**：模块与其测试同一提交迁移，旧位置不留转发 shim（过时 patch 直接报错）；被改动 patch 目标的"断言没发生"类测试补一个正向前提（证明 patch 确实生效）。

### 7.1 P-1 的提前执行与验收标准（2026-10-15 用户裁决 ×2）

用户裁决 ①"升级为多方法框架 不用等正式 run"：P-1 拆两半——golden 矩阵已提前完成，TB-dev 终态 tag 与维护分支归档仍等它的 run 完结（TB-dev 侧的事，MM 不为其编排任何流程）。

用户裁决 ②"简洁、可扩展、避免 over-engineered；golden 从未要求过不重新生成；每步全量比对太死板"：撤销两条自设约束——

- **"golden 永不重生成/只在重构前代码上生成"撤销**。golden 是普通快照回归测试：有意改行为的步骤在**同一提交**里更新它，diff 摘要写进提交说明，步骤评审读的就是这个 diff；不改行为的步骤不得碰它。
- **"TB-dev 热修 → cherry-pick 到基点 + provenance 换基点"编排删除**。想反映上游变化就 merge origin 再重生成，无程序化要求。

**简化后的验收标准**：

1. 每个提交测试全绿。golden 快照测试只是套件成员（10 个测试约 0.6 秒），不构成额外仪式；行为不变的步骤它必须无 diff。
2. 有意改行为的步骤（主要是 P1b）在提交说明里列 golden diff 摘要；P1b 另做一次性认真检查（每份 v2 配置升级后的 manifest 人工审定，见 §7 表）。
3. 崩溃续跑等价是既有契约，在 golden 生成时验证一次；套件常驻 2 个组合的续跑测试。

**采集器现状**：8 组合 × 假模型全流水线，每组合 3 个崩溃续跑变体在生成时全部等价；等价准则为"结果状态逐字节相同 + 请求史多重集只容忍崩溃痕迹与同内容重问"（README 已知续跑语义"未落盘的调用会被重问"的可执行形式）。基点 commit 记录在 `provenance.json`（信息记录，不是纪律）。覆盖面限制如实披露：假模型、无远程 TB 链路、无真实端点——远程与端点路径由在跑正式 run 与 live 冒烟验证。

**测试体系**：golden 产物矩阵为主验收；每槽位只保留两条廉价参数化契约测试（假模型跑通、未知参数拒绝）；各方法现有专属续跑/不变量测试全部保留；不建通用"第 k 次写盘崩溃"遍历框架（等第一个新方法落地再评估）。

## 8. 新增一种方法的清单（写入 AGENTS 第七节）

1. 在 `methods/<slot>/<name>.py` 写驱动函数并用 `@method(slot, name, Params=..., check=...)` 注册；在 `methods/__init__.py` 显式 import。
2. 需要新 prompt：在 `prompts/` 新建模块并加进 `prompts/__init__` 的 import 清单（静态测试会检查）。
3. 改已有方法的行为 = 注册新名字，不改旧名字的行为。
4. 驱动只读自己的 Params 与实验级视图；落盘目录用常量声明在 `<stage_dir>/<method>/`。
5. 写续跑测试 + 槽位契约测试自动覆盖。

**思想实验（B）**：新 prompt 策略的 authoring（结构不变）只改 3 个文件；`curation: none` 完全隔离；embedding delivery 方法本身隔离，但需 P5 的一次性基础设施（非 chat 调用、新角色、TB 插件提供真实查询文本——今天 `Task.instruction` 只是任务名，`tb/plugin.py:450`）；新 provider 与方法框架正交。若新 authoring 改变 skill 结构或"一段一条"粒度，会触及跨槽位协议（ID、考题关联、live 分组、策展溯源），须走设计变更。

## 9. 明确不做

needs/provides；`extends` 深合并；方法基类、`slot` 冗余字段、类型化 Summary；按方法声明 prompts/roles；`call_model` 运行时成员检查；任何新 hash/指纹/版本号/审计机制（审计留在 `scripts/audit/` 外挂）；工作目录迁移；搬 K4/K5/K6 本体；角色表开放（v3 内）；通用崩溃遍历测试框架；插件自动发现；DAG 引擎；DI 容器。docstring 瘦身（事故史移入 `docs/decisions/`）单独立项。

## 10. 每步三路评审协议（用户裁决，2026-10-15）

重构的每个步骤（P0、P1a、P1b、P4……）完成后，**必须**启动三个独立评审对本步骤的改动做审核，判断其是否偏移了本方案定下的约束，通过后才进入下一步：

1. **独立性**：三个评审各自从本方案出发独立审阅，互不通气；全程只读 MM 仓库，绝不触碰 TB-dev 与任何 `runs/` 目录。
2. **分工**（沿用三轮定稿审计的视角）：A = 代码事实与行为等价（改动是否真的行为不变 / 与方案一致，逐条对照本方案 §2–§9）；B = 扩展性与反过度设计（是否引入了 §9 禁止的元素、抽象是否越界）；C = 迁移风险与可复现性（manifest/golden/续跑语义、该步验收标准是否真正被执行）。
3. **审核基准**：本方案文档 + 该步骤的验收标准（P0/P1a 的"manifest 不变"、golden 一致、测试全绿）。
4. **结论分级**：每条发现标注为【偏移约束】（引用本方案条目）/【琐细】（不影响推进）/【赞同】。出现任何【偏移约束】级发现 → 修复后对该步重审，通过才继续。
5. **留痕**：每步评审意见与修复记录写入 `docs/reviews/multi_method_step_<step>_review_<YYYYMMDD>.md`，log.md 补记。

## 11. 三方审计收敛记录（方案定稿过程）

| 议题 | 初版 | 最终 | 依据 |
|---|---|---|---|
| 依赖表达 | needs/provides | 删；`authoring.exams` 缺省 true | B 提出只服务一条依赖；A/C 证明懒生成改候选集、破坏 K5 并行前提 |
| prompt 冻结 | 选中方法声明的并集 | 全量冻结（prompts 包 + 本插件任务 prompt）+ 两条静态测试 | B：声明会失真且漏框架 prompt；A/C：固定 checkout 后全量最简；B 补"import 快照"漏洞 |
| 方法层 | 新层并迁入 K4/K5/K6 | 新层只放驱动，本体不搬 | C：搬本体使崩溃注入测试空转变绿；A：方法需要上下文与编排 |
| 工作目录 | `run_dir/<slot>/<name>/` | 永久不迁移 | C：远程 run id/游标绑定路径 |
| 过渡 | P1 产物逐字节相同 | P1a manifest 不变 + P1b 单次切换 + `upgrade_v2` | C：逐字节相同不可执行（时间戳/uuid），改 golden 矩阵 + 归一化 |
| 角色表 | 开放 | 不开放，`ROLE_DEFAULTS` 改名 | C：开放导致预算漂移、live 包络核算失效 |
| 隔离 | 未提 | 独立 clone + venv、每 run 维护分支 | C：editable `.pth` 致命风险 |
| 第三轮 R1–R7 | — | **A、B、C 全票接受**，补充已并入上文 | 见 `docs/reviews/multi_method_review_{A,B,C}_20261015.md` |

三位评审的完整意见（三轮全文）存档于 `docs/reviews/multi_method_review_{A,B,C}_20261015.md`；两轮讨论提纲见下。

---

### 附录 A：第二轮讨论提纲（主持人整理）

三份独立审计到齐后的分歧点 D1–D7：考题依赖表达（A 显式开关 / B ensure_exam 懒生成 / C 首切不动）、冻结 prompt 集公式（A 基础集∪方法声明 / B 积木导出常量或全量冻结 / C 成员检查）、方法层放哪（新层 vs 留 composites）、槽位优先级、角色表是否开放、v2→v3 过渡方式（一次切 vs 分阶段）、验收标准（golden 矩阵 vs 契约测试）。共识 S1–S8：方向保留；删 needs/provides、extends、类型化 Summary；布局不迁；MethodContext 不暴露整份 config；参数校验不进 core；固定 checkout 续跑 + 独立 clone；共享件先下沉；失败处置抽共享原语。

### 附录 B：第三轮收敛提纲（主持人裁决 R1–R7）

R1 methods 只放驱动、组合积木本体不搬；R2 `authoring.exams` 缺省 true、否决 ensure_exam；R3 两步切换（P1a manifest 不变 / P1b 单次 schema 切换）+ `upgrade_v2` 纯函数；R4 不加运行时成员检查、改两条静态测试；R5 `ROLE_DEFAULTS` 改名不开放；R6 每 run 维护分支 + 独立 venv 写入 AGENTS；R7 单一 frozen RunContext + 补 `n_attempts ≤ n_attempts_weak` 校验。结果：A、B、C 全票接受，各自补充（驱动迁移同守三纪律、R4 两条测试的精确形式、`authoring.exams` 须在 P1b 进形状、`upgrade_v2` 的三种 v2 acceptance 写法同值映射、校验放 P1b 避免夹具早红、stage0 报错不给 manifest 加字段）均已并入本文。
