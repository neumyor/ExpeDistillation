日期：2026-10-15
一句话说明：架构师 C 对多方法框架重构方案的三轮独立审计全文（第一轮独立审计、第二轮交叉讨论、第三轮收敛表决），供 docs/decisions/multi_method_framework_20261015.md 的收敛记录追溯。
对应 log.md 条目：2026-10-15 多方法框架方案定稿，建立 MM 独立副本。

# 架构师 C：三轮审计全文

分工说明：A = 代码事实与可行性，B = 扩展性与反过度设计，C = 迁移路线与可复现风险。评审基于仓库 bcb3ffe，全程只读。

---

## 第一轮：独立审计

# 独立审计 C：多方法框架重构方案 v1 —— 迁移路线、可复现性与风险

审计对象：/tmp/arch_review/plan_v1.md
仓库：ExpeDistillation-TB-dev @ bcb3ffe（feature/acceptance-live-run），只做了静态阅读：没有跑测试，没有碰 runs/，没有改动仓库。
文中路径默认相对 `src/expedistill/`；以 `tests/`、`scripts/`、`configs/`、`docs/` 开头的路径相对仓库根。

---

## 一、总体评价

方向我同意：加一层"方法槽位"、去掉阶段里的 if 分发、拿掉冻结补丁，都对。真正的风险在迁移路线，不在目标架构。

方案说 P0–P2"行为不变"，但这期间 manifest 一个字段都没变。也就是说，在 manifest 改形状（P3）之前，冻结比对拦不住任何**路径或布局的漂移**，所以"路径漂移"恰好最危险。最典型的是 `ctx.workspace() → run_dir/<slot>/<name>/`。旧 run 用新代码续跑时不会报错，而是**悄悄把活重做一遍**：远程 run id 换了命名空间、重试游标归零、append-once 账本"消失"。

此外有三处方案没覆盖：
- schema v3 会让 `assemble_reuse` 无法再复用任何 v2 源 run；
- 冻结的 prompt 集改成"各方法声明的并集"后，会出现一个身份漏洞：方法调用了却没声明的 prompt 不进冻结集；
- 当前 checkout 是 editable 安装，正在跑的 run 和重构共用同一份 `src/`。

"1362 测试 + golden 全绿 + 产物逐字节相同"这套验收，现在**无法照字面执行**：
- 产物里带时间戳和 uuid；
- 仓库里没有产物级 golden，只有 manifest 级的；
- 搬迁 K4/K5/K6 会让一批崩溃注入测试**空转也照样变绿**。

这些都能用测试侧和运维侧的手段解决，**不需要新增任何 hash 或审计机制**。下面是具体清单。

---

## 二、迁移风险清单（按严重度排序）

### R1【致命】在飞 run 和重构共用同一个 editable 安装，新代码会被在飞进程中途加载、或在续跑时被拾取

- **触发条件**：只要在这个工作树里改代码，或者切分支，而此时还有 run 在跑或待续跑。
- **证据**：
  - `.venv/lib/python*/site-packages/__editable__.expedistill-0.1.0.pth` 指向本仓库的 `src`；
  - `scripts/run/launch_tb21_live_run.py:19-27` 直接用 `.venv/bin/expedistill`；
  - 进程内的懒 import 发生在长时间运行的中途：`stages/stage4_acceptance.py:174`（`from expedistill.stages import stage4_live_run`）、`stages/stage0_freeze.py:290`。阶段 1 跑了几个小时后，阶段 4 才加载 live 模块，这时加载的是**磁盘上的新代码**；
  - supervisor 遇到退出码 3 会自动续跑（README:982），续跑必然拾取新代码。
- **缓解**：
  - 在第一次重构提交之前，先给在飞 run 用的提交打 tag（如 `schema-v2-final`）；
  - 在飞或待续跑的 run 迁到一个**独立 clone + 独立 venv**，launcher 指向那个 venv；
  - 重构在另一个 clone 里做。`git worktree` 不够：editable 的 `.pth` 只指向一个路径。
  - 方案第 5 节写的"老 run 用老 checkout"必须落成这套物理隔离，不能只靠纪律。

### R2【致命】P0–P2 期间的布局漂移没有任何冻结面能拦截

- **触发条件**：P1/P2 把 K4/K6（以及 delivery）搬进 `methods/` 时，同时启用 `run_dir/<slot>/<name>/` 工作目录或新的 `work/` 子目录。此时 manifest 还是 v2 形状，旧 run 用新代码续跑**不会被拒绝**。
- **证据**：
  - K1 的远程命名空间里含 store 目录的 realpath：`composites/batch_execute.py:202-225`（`"store_dir": os.path.realpath(...)`）；
  - 重试游标和远程启动记录都放在单元工作目录里：`composites/batch_execute.py:325-327`，README:121-126；
  - live 的轮 store 和账本路径写死为 `stage4_acceptance/live_run`：`composites/live_acceptance.py:125, 224-244`；
  - 两轮的 trajectory id 相同，必须靠分树隔离：runbook §0。
- **后果**：
  - 旧 run 续跑时看到的是空工作目录，于是轮 0 用新命名空间整批重派，远程执行重复、花双倍钱；
  - G10 归因"append-once、绝不重问"的契约被打破，因为账本在旧路径；
  - 如果只挪了 `work/`、没挪 store，游标会归零，已判为 infra 的 token 被重新请求，命中"本地记录缺失"后一律记成 infra_error，可能烧光重试预算（README:124-126）。
- **缓解（推荐的根本做法）**：**永久不搬布局**。把 `workspace()` 定义成"给现有目录起名字"，用一张固定映射表：exam → `stage4_acceptance/`，live_run → `stage4_acceptance/live_run/`，K1 → `work/stage4_live/roundNN`、`work/stage6_bank|bare`，curation → `stage5_curation/`。新方法照 live_run 已有的惯例放到 `<stage_dir>/<method>/` 下。
  - 搬布局本身没有收益，却要改动 `scripts/` 和 runbook，审计脚本还会静默失效（见 R10）。
  - 如果一定要搬，只能放在 P3 的 schema 切换里，让 v3 manifest 负责拒绝旧 run。

### R3【高】schema v3（包括 runbook §7 的单项退役）会让 `assemble_reuse` 无法复用任何 v2 源 run

- **触发条件**：v3 代码拿 v2 源 run 做 `assemble-reuse --level bank`。复用已完结 run 正是这个工具存在的理由。
- **证据**：
  - 允许差异的键只有 `{mode, split, tasks, skill_delivery, schema_version}`：`composites/assemble_reuse.py:118-119`；
  - `_diff_paths` 把"只在一侧出现的键"也算作差异：`:360-372`；
  - `_identity_view` 只排除了 prompt digest：`:394-406`；
  - 待比对的新 manifest 由当前代码现场推导：`:1121-1137`。
- **后果**：
  - 只做 runbook §7 一项（`acceptance` 恒写入、None 显式在场），v2 exam 源 run 就缺 `config.acceptance` 这个键，产生不在白名单里的差异，直接拒绝。runbook §7 没有提到这一点；
  - 换成 `methods:` 之后，`rules / K / retry_limit / bank_token_budget / skill_delivery` 全部挪位，`model_roles` 会少掉 live_run 用不到的 `auditor`，整份 diff 全红；
  - `_path_allowed` 只看第二段路径（`:380-389`），表达不了 `config.methods.delivery` 这样的三段路径。
- **缓解**：
  - 写一个**纯函数升级器** `upgrade_v2(dict) -> v3 dict`，放在 core，只依赖标准库，用 golden 测试固定。它有三个用途：
    - (a) YAML 配置转换；
    - (b) `assemble_reuse` 在比对之前先把源 manifest 提升到 v3 视图；
    - (c) 测试用的 oracle。
  - v2 里 `acceptance` 的三种写法（缺键 / `{}` / `{mode: exam}`）必须映射到同一个 v3 值，否则会出现假差异。
  - 白名单改成按路径前缀匹配。
  - `assemble_reuse` 上移到 stages 层（见 R6）。

### R4【高】"产物逐字节相同"无法照字面执行，仓库里也没有产物级 golden

- **证据**：
  - 落盘内容里有易变字段：
    - `infra/usage.py:196` `"time"`；
    - `infra/reliability.py:175` `"time"`；
    - `composites/batch_execute.py:527` skip_audit `"time"`；
    - `llm/call.py:574,587` `request_id`（uuid）和 `duration_ms`；
    - `infra/runlock.py:92-94` pid 和时间；
  - workers > 1 时账本行序不固定：`stages/stage3_generation.py:26-28`；
  - P1 以后 manifest 本身的形状会变；
  - 现有 golden 只固定了 manifest、`to_dict` 和 prompt digest：`tests/unit/stages/test_stage0.py:599-683`，`tests/unit/core/test_config.py:877-990`；
  - e2e 只覆盖 full+exam 和 full+live_run 两种组合：`tests/unit/stages/test_pipeline.py:304, 1185`。
- **缓解（全部是测试侧，不进框架）**：
  - 在 R1 的 tag 提交上，用 `FakePipelinePlugin` 以 workers=1 跑一个矩阵：{exam, live_run} × {whole_bank, retrieval} × {full, split}；
  - 把产物树存成 `tests/fixtures/golden_runs/`，**永不重新生成**（沿用 `test_stage0.py:599-603` 的纪律）；
  - 比对时用一张**写死的易变键清单**做归一化，只在测试 helper 里；
  - 比对范围：Store 对象、全部 `*.jsonl` 账本、各阶段 summary、`llm_requests` 的 prompt 名序列，manifest 在 P3 前也比；
  - 每个组合再加一个"中途 kill 再续跑"的变体。

### R5【高】搬迁 K4/K5/K6 会让崩溃注入测试静默空转

- **触发条件**：搬迁时在旧位置留一个 re-export shim，或者测试继续 patch 旧模块。`monkeypatch.setattr` 在 shim 上能成功，但新代码绑定的是自己模块里的名字。
- **证据**：
  - `tests/composites/test_live_acceptance_resume.py:710-722`：patch `la.append_jsonl` 之后 `assert audit_writes == []`。patch 落空时这条断言**照样成立**；
  - 同类写法还有：同文件 `:508, :544, :593, :702, :745`，`tests/composites/test_curation_resume.py:313`，`tests/composites/test_curation_parallel.py:548, 630, 734`，`tests/composites/test_acceptance_resume.py:355, 397, 553`，`tests/unit/stages/test_stage4.py:773`。
- **缓解**：
  - 模块和对应测试在**同一个提交**里一起迁；
  - **不留 shim**，直接删掉旧模块，这样过时的 patch 会抛 AttributeError；
  - 断言"没发生"的测试补一个正向前提，证明 patch 确实生效过（例如同一个 spy 在参照 run 里被调用过）。

### R6【高】冻结 prompt 集改为"选中方法声明的并集"后会出现身份漏洞

- **证据**：
  - `DEFAULT_SYSTEM` 和 `REPAIR_INSTRUCTION` 是 B1 的协议 prompt，不属于任何方法（`prompts/llm.py`），但它们在现行 golden 冻结集里（`test_stage0.py:611-626`）；
  - EXAM_GEN 要不要用，取决于下游方法的 needs，方案里的 `prompts: ClassVar[tuple]` 表达不了这种条件依赖；
  - `assemble_reuse` 在第 5 层，无法 import 第 6 层的方法声明（`tests/test_layering.py:15-26`）。它今天已经因为这个分层限制复制了 stage0 的推导（`composites/assemble_reuse.py:291-312, 315-344`）。
- **后果**：方法调用了却没声明的 prompt，digest 不进 manifest。以后改这个 prompt 的文本，续跑不会被拒绝。这直接违反"改一个字即新实验"（AGENTS §7）。
- **缓解（不加任何 hash 机制）**：
  - (a) 框架级 prompt 恒冻结；
  - (b) 声明改成实例方法 `prompts(params, resolved_needs)`；
  - (c) `call_model` 碰到不在本 run 冻结集里的 prompt 名，直接 CodeBugError。这只是一行成员检查；
  - (d) 升级等价性测试：v2 exam 配置和 v2 live_run 配置升级、冻结之后，prompt 名集和 digest 必须分别等于现有两份 golden；
  - (e) `assemble_reuse` 上移到 stages 层，复用唯一一份冻结推导，顺带消掉重复代码。

### R7【高】角色表开放后，预算和校验会漂移

- **证据**：
  - `token_budgets.roles` 按 MODEL_ROLES 校验：`core/config.py:416-421`；
  - handle 按 MODEL_ROLES 全量构建：`stages/pipeline.py:151-155, 187`；
  - `configs/experiments/tb21_split_live_run.yaml:211` 给 `auditor` 配了预算，但 live_run 根本用不到 auditor。按方案的规则"合法角色 = 选中方法 roles 并集"，转换后这份配置直接 ConfigError；
  - core 在第 0 层，不能 import 方法注册表，所以角色校验必须挪出 `load_config`，报错的时点会变；
  - 如果给 live_run 起新的角色名，它的 max_tokens 会回落到 `default_max_output_tokens`。runbook §2.5（`:79-86`）的包络核算是按 author/rewriter = 65536 做的。
- **缓解**：
  - 第一次切换时**不改角色名**，live_run 继续用 author/rewriter；
  - 转换器删掉用不到的角色预算，并在输出里列出删了哪些；
  - 角色集校验放在 pipeline 里、取锁和建 client 之前，维持现在的报错时点，沿用 `test_pipeline.py:586` 的测试模式。

### R8【中高】"考题按需生成"会改变候选集和续跑判据，而且对现有所有配置都是死代码

- **证据**：
  - 阶段 3 把 G2 和 G3 放在同一个单元里，G3 模型输出失败会让整个段进入终态：`stages/stage3_generation.py:42-54, 284-300`；
  - 续跑判据是"skill 和 exam 都已存"：`:249`。
- **后果**：一旦按需不出题，G3 失败就不再淘汰 skill，候选集变了，"已完成"的判据也跟着变。
- **现状**：对 `configs/experiments/` 里的每一份配置，exam 都有消费者：exam 验收要用；live_run + K5 的回归考试也要用（`composites/curation.py:687-711`）。所以 P3 加上按需生成，**一行行为都不会变**。
- **缓解**：
  - 把它挪到 P4，和第一个不需要考题的实现（`curation: none`）一起上，符合方案自己"第二实现出现才建接口"的原则；
  - needs/provides 解析的**结果**要补全写进冻结配置（例如 `methods.authoring.with_exam: true`），让实验身份由数据决定，不由解析代码决定。这和 `rules` 的"补全、不拒绝"是同一条纪律（`core/config.py:15-20`）。

### R9【中】v3 代码碰到 v2 run 目录时的报错会误导人

- **证据**：未知键会 fail-loud（`core/config.py:748-754`）。但如果拿转换后的配置去续跑旧 run 目录，报的是一长串 FrozenProtocolChanged，提示语是"use a new run directory"（`infra/manifest.py:66-69`）。操作者照做的话，就等于放弃了旧 run 的续跑。
- **缓解**：阶段 0 先读已存 manifest 的 `config.schema_version`；小于当前版本时报 ConfigError，明确写出"此 run 由 schema v2 冻结，请用 tag `schema-v2-final` 的 checkout（独立 venv）续跑"。这只是一条明确的提示，不是新的审计机制。

### R10【中】scripts/ 和 runbook 写死了布局和 manifest 形状，其中审计脚本会静默放行

- **证据**：
  - `scripts/audit/audit_live_run.py:86` 写死 `LIVE_SUBTREE`；
  - 同文件 `:605-610`：子树不存在时只记一条 INFO "nothing to audit" 就返回，**一个 live run 换了布局后审计照样通过**；
  - 同文件 `:613-616` 读的是 `config.acceptance.n_attempts`，v3 的形状不一样；
  - 写死布局的还有：`scripts/run/fork_run_stage04.py:48-59`、`scripts/run/prepare_inplace_recovery.py`、`scripts/run/seed_tb_stage1.py`，runbook §4（`:131-142`）；
  - `assemble_reuse` 的负清单是逐个列路径的（`:140-157`），布局一变就成了永真断言。
- **缓解**：
  - 审计脚本按 manifest 判断模式：manifest 说是 live_run 而子树缺失，就报 ERROR；
  - 负清单改成按方法工作目录的根目录整体禁止；
  - 每个阶段的验收清单里加一项"scripts/ 和 runbook 引用同步"。

### R11【中】P0 的"消重复"不全是机械操作

- **证据**：
  - live 侧的 `_candidates` 是有意不和 exam 共享的（`stages/stage4_live_run.py:192-198`，README:720-722 已记录这个决定）；
  - `_expected_units`（返回单元 id 列表，`stage4_live_run.py:224`）和 `expected_units`（返回给 D3 的映射，`composites/live_acceptance.py:495`）语义不同，只是名字像；
  - 三个插件解析函数的报错文本各不相同（`stages/stage3_generation.py:121-136` 等）。
- **缓解**：P0 只合并真正等价的重复：
  - `_workers`、`_k1_stagger`、`_allow_infra_shortfall`；
  - `_canonical`、`_tasks_digest`、`_prompt_digests`（通过把 `assemble_reuse` 上移来合并）。
  - 名字像而语义不同的保持原样，在 docs/decisions 里记一笔。

### R12【中】`test_layering.py` 不足以守住新的 methods 层

- **证据**：
  - 相对 import 被直接跳过（`tests/test_layering.py:32`，条件 `not node.level`）；
  - 同层互相 import 目前"靠 review 检查"（`:14-15`）。方案说"方法互不 import"，但没有任何机器检查。
- **缓解**：
  - RANK 改为：methods=6、stages=7、cli=8、`__main__`=9；
  - 相对 import 要么一律拒绝，要么解析后再检查；
  - 新增两条规则：`methods/<slot>/<name>.py` 不得 import 其他方法文件（`methods/base` 除外）；`blocks/*.py` 不得互相 import。

### R13【中】Params 校验上移后，失败时点和 fail-loud 合同会变

- **证据**：
  - 配置现在是构造时整体校验（`core/config.py:59-65, 520-617`）；
  - pipeline 在取锁之前统一把配置错误转成 ConfigError（`stages/pipeline.py:330-337`）。
- **缓解**：分两段校验。
  - core 只校验形状：`methods` 是"槽位 → 字符串，或带 `name` 的映射"；
  - pipeline 在取锁和建 handle 之前解析 Params；
  - 用测试固定"配置错误发生在任何 client 构建之前"。

### R14【低中】统一 `AcceptanceSummary` 会改变摘要形状

- **证据**：
  - 两种模式的 summary 形状本来就不同（README:824-833）；
  - pipeline 读的是 dict 键 `infra_shortfall_accepted`（`stages/pipeline.py:370`）。
- **缓解**：summary 保持由方法自己产出 dict，框架只读事先约定的几个公共键。

### R15【低】确定性 ID 和槽位命名不能耦合

- **说明**：live 的轮 store 和阶段 1 用的是同一套 trajectory id，靠分树隔离（runbook §0）。
- **缓解**：ID 规则只在 `core/ids.py`，不准把方法名拼进 ID。多个轮 store 也不能合并到同一个"方法工作目录 store"里。

---

## 三、对 P0–P5 的修订建议（重新切分）

**P-1 准备（零源码改动）**
- 打 tag `schema-v2-final`；
- 在飞 run 迁到独立 clone + 独立 venv（R1）；
- 在 tag 提交上生成 golden 产物矩阵和崩溃续跑变体，写死易变键清单（R4）；
- 定下"布局永久不变"的政策（R2）。

**P0 纯重构（配置、manifest、布局全不变）**
- RunSettings 进 StageContext 字段，删掉动态挂属性；
- 只合并真正等价的重复（R11）。
- 验收：全量测试 + golden 矩阵归一化后相同 + 崩溃续跑变体相同。注意：用到 `ctx.workers = …` 的测试要随 P0 一起改，所以"测试全绿"不能当作唯一的不变量证据。

**P1 方法注册表 + 迁 acceptance（仍然读 v2 配置）**
- 由内部适配器把 v2 字段映射成方法 Params；
- `workspace()` 走旧路径映射表；
- K4、K6 连同测试同一提交迁移，不留 shim（R5）；
- 扩展 `test_layering.py`（R12）。
- 验收同 P0。

**P2 迁 delivery**
- 额外验收：whole_bank 走统一接口后，每个单元的命名空间、`Trajectory.skills_injected`、`bank_reference` 都和 golden 相同（`composites/batch_execute.py:202-225` 的标量路径）。

**P3 schema 切换（一组提交，在所有旧 run 都已迁到 tag 之后）**
- `methods:` 配置 + v3 manifest；
- 纯函数升级器 + 配置转换 CLI。转换结果**写成新文件名**，因为已启动过正式 run 的配置不得修改（AGENTS §6）；
- `assemble_reuse` 接入升级器并上移到 stages 层；
- prompt 和角色的声明；删掉懒注册、`METHOD_PROMPT_NAMES`、`_MANIFEST_OPTIONAL_FIELDS`；
- `call_model` 加 prompt 名成员检查；阶段 0 拒绝 v2 run 并给出明确提示（R9）；
- 同步 scripts/ 和 runbook（R10）。
- 验收：
  - `configs/experiments/` 下每份配置：升级 → 冻结 → 等于一份人工审定过的 v3 manifest 字面量；
  - prompt 名集和 digest 与 v2 golden 完全相同；
  - manifest 以外的产物与 golden 矩阵相同；
  - 用一个 v2 fixture 源 run 跑通 `assemble_reuse`。

**P4 有第二实现时再做**
- 考题按需生成和 `curation: none` 一起上，needs 的解析结果写进冻结配置（R8）；
- authoring、segmentation 等真出现第二个实现再迁。

**P5 不变**：需要时再加模型 provider 注册表。

**每个槽位的参数化契约测试**：可行，但只能补充、不能替代现有的方法专属不变量测试（K6 的重算守卫、K5 的 stale 检查等）。
- "假模型跑通"和"Params 未知键拒绝"很简单；
- "中断续跑等价"建议在 `infra/store` 的写入点（`write_json` / `append_jsonl` / `Store.save`）按"第 k 次写入时崩溃"遍历 k，比较归一化后的最终状态；
- 只比较状态，不比较调用次数。README:263-265 已经登记了"结果未落盘会重问"这类已知不等价。

---

## 四、遗漏点

1. **needs/provides 只列了 exam**。实际的跨槽依赖还有：
   - live_run 依赖阶段 1 弱臂轨迹和 `stage1_baseline/accepted_infra_shortfall.json`（runbook §1.2，`n_attempts` 建议等于 `n_attempts_weak`）；
   - full 模式下阶段 6 的对照组直接复用阶段 1（`stages/stage6_test.py:449-455`）；
   - K5 回归考试依赖 answerer 和 judge 两个角色。
2. **MethodContext 不应暴露整个 config**。否则方法会去读别的槽位的参数，形成新的隐式耦合。只给方法自己的 Params 加少量公共字段（`weak_profile`、`n_attempts_weak`）。
3. **方法会改共享对象，续跑契约不只是"在自己工作目录上幂等"**：
   - acceptance 改 Skill 的 status 和 version：K6 用版本号作为完成证明（README:250-255）；
   - curation 写合并 skill，并依赖全局 accepted 集合做 stale 检查（README:240-246）。
   - 方案应该给每个槽位写明"允许修改的共享对象和字段"。
4. **单元失败账本里的 stage 标签不能改**：`failed_units.jsonl` 里的 `stage4_live` / `stage3_generation` 标签、`pipeline_log` 的阶段名，都被 `cli summary`、审计脚本和 runbook 读取（`cli.py:231, 251`；runbook:142）。
5. **转换时会丢掉不用的字段，属于身份变化，要写进文档**。例如 live_run 配置里的 `retry_limit`、`auditor` 角色。这和 runbook §7 说的"升版 = 新实验身份"一致。
6. **可选（按用户偏好可以拒绝）**：在 `stage0_summary` 里记录当时的 git 提交号，只记不比对。它不是 hash 校验，只是帮"老 run 用哪个 checkout 续跑"留一条可追溯的记录。

---

## 五、对其他视角的简评

- **方法层放在 composites 之上是对的**。但是 `assemble_reuse` 必须跟着上移，否则在分层约束下，"消除它复制的 stage0 逻辑"做不到。
- **注册复用 `core/registry.py` 合理**。但方法注册必须是 import 时的显式注册，不做自动发现，方案已经写了。否则同样会遇到"进程里碰巧 import 了什么"的问题，这正是 `stages/stage0_freeze.py:58-61` 已经踩过的坑。
- **整个方案没有引入 hash 或审计，符合用户偏好**。本审计的全部缓解措施也都是三类东西：纯函数升级器、成员检查断言、测试 fixture，外加 git tag 这种运维手段。

---

## 第二轮：交叉讨论（提纲见决策文档附录 A）

# 第二轮 · 架构师 C 表态（主视角：迁移、可复现与风险）

我读了 round2_brief.md、review_A.md 和 review_B.md。仓库只读，没有运行任何东西。
文中路径默认相对 `src/expedistill/`；以 `tests/`、`scripts/`、`configs/` 开头的路径相对仓库根。

本轮我有两处**撤回或修正**第一轮的意见：

- 第一轮 R6(c) 建议在 `call_model` 里加"冻结集外 prompt 名即报错"的成员检查。在 S6 成立的前提下这一检查是多余的，我撤回，改为支持 B 的"冻结全部方法 prompt"，但要加一处必要的限定，见 D2。
- 第一轮我建议 P1/P2 在内部继续读 v2 配置。这个说法不准确，会被误读成"代码里要做双格式适配"。我改为同意 A 的"无双格式"，但主张把切换拆成两次提交，见 D6。

---

## 一、对 S1–S8 的意见

### S1：部分反对一处措辞

方向我同意。但"删掉 `METHOD_PROMPT_NAMES` 快照"这一句不对，**快照本身不是补丁，懒注册才是**。

`prompts/__init__.py:20-25` 的快照 = "prompts 包里注册的全部方法 prompt"。它是确定的，与进程里还 import 过什么无关。它唯一的毛病是 live 的两个 prompt 被刻意排除在 import 清单之外。

修法只要两步：
- 把 `attribution` 加进 `:20` 的 import 清单；
- 删掉 `stage0_freeze.py:287-291` 和 `assemble_reuse.py:305-307` 两处条件分支。

快照（改不改名都行）应该保留，它正是 D2 公式里"方法 prompt"那一项。

### S2：无反对

去掉 needs/provides 之后，考题依赖在 P4 之前仍是隐式的。但 stage3 无条件出题（`stage3_generation.py:288-306`），所以这不构成风险。

### S3：无反对

这是我第一轮的 R2。

### S4：无反对

### S5：无反对

### S6：同意，但要补两点细节

1. **"固定 checkout"应该是每个 run 一条维护分支，而不是不可变的 tag。**
   - 证据：历史上有过在飞 run 必须拿到 bug 修复的情况。`scripts/run/prepare_inplace_recovery.py` 的 docstring 写明，用户豁免了 AGENTS §6.8，在原 run 上用修复后的代码续跑；`scripts/run/fork_run_stage04.py` 也属同类。
   - 规则：热修复 cherry-pick 到该 run 的分支，不从 main 拉。
2. **每个 checkout 必须有自己的 venv。**
   - 证据：`.venv` 的 editable `.pth` 只指向一个 `src`。launcher 也写死了这个 venv：`scripts/run/launch_tb21_live_run.py:19-27` 用的是 `.venv/bin/expedistill`。

### S7：同意，附两点提醒

1. A 指出候选过滤三份重写的理由（决定文档 2.2）会随懒注册一起消失，我同意。但合并它会改动 exam 和 live 两条路径，必须放在 golden 比对能覆盖的那一步里做（见 D7）。
2. 第一轮我说 `_expected_units` 和 `expected_units` 语义不同。这个对比本身没错，但 A 给出的配对更准确：`_expected_units`（`stage4_live_run.py:224`）与 `_unit_ids`（`live_acceptance.py:332`）才是同一个函数换了名字。我接受 A 的配对。

### S8：有条件同意

`run_units_disposed` 只能抽取**两个共同部分**：
- 基础设施错误：记账本、收集、在阶段末统一抛出；
- 致命错误：不再启动新单元（这一点 `run_units` 已经做了）。

**模型输出错误的终态处理不能统一。** 各处的持久化顺序本身就是续跑契约的一部分，而它们各不相同：

| 位置 | 模型输出错误时的处理顺序 |
|---|---|
| `stage3_generation.py:291-300` | 先写终态行，再写账本，续跑时补账本 |
| `stage4_acceptance.py:235-240` | 先丢弃 skill，再写账本 |
| live（README:800-801） | 先写组终态，再写账本行 |
| stage5 G7/G9（README:932） | 整个阶段停下 |

原语应当只接收方法传入的 `on_model_output(unit, exc)` 回调，不替方法决定先写什么后写什么。

---

## 二、对 D1–D7 逐条表态

### D1：选 (c)，P4 时演进为 (b)；(b) 不算硬伤，但它是协议变更，不能放进"行为不变"的那一步

1. 今天 G3 失败会让整段进入终态：`stage3_generation.py:284-300`，续跑判据是"成对已存"（`:249`）。
   改成懒生成后，候选集合、stage3/stage4 的 summary 键、丢弃原因都会变：exam 模式需要新增"出题失败"这种 discard_reason；K5 中途出题失败会让合并被撤销。这对**所有**现有配置都是新实验。
2. 但 (b) 的设计本身最干净：考题只依赖 subtask（`blocks/author.py:369`），没有解析器，身份由方法名决定。在 S6 成立的前提下，不需要我第一轮提的"把解析结果写进冻结配置"，这一条我撤回。
3. A 的 `authoring.exams` 开关在 (b) 之下是多余的，在 (c) 之下没有使用者。

结论：v3 首切不动 stage3；(b) 和 `curation: none` 一起在 P4 作为预注册的新协议上线。

### D2：在 S6 成立的前提下，选 B 的"冻结全部方法 prompt"，但"全部已注册"必须收窄为"prompts 包注册的全部 + 本配置插件的任务 prompt"

1. 不能字面理解为"PROMPTS 里所有已注册的"。插件在 import 时注册自己的任务 prompt：`benchmarks/tb/plugin.py:133`、`searchqa/plugin.py:89`、`alfworld/plugin.py:103`。进程里 import 了哪些插件，取决于测试或 `assemble_reuse` 怎么跑，结果会不确定。`stage0_freeze.py:58-61` 已经为此踩过一次坑。
2. 收窄后的公式 = `METHOD_PROMPT_NAMES`（attribution 改为在 import 时注册）∪ 配置插件的任务 prompt。这就是今天 exam run 的公式，**代码改动最小，而且没有漏洞**：B1 的 `DEFAULT_SYSTEM`/`REPAIR_INSTRUCTION` 自动包含在内，也不存在"声明与实际调用不一致"的问题。
3. 硬性前提：所有 prompt 只能写在 `prompts/` 里，并在 import 时注册。方法文件里不得 `register`。可以给 `test_layering` 加一条约 5 行的检查。
4. 代价：
   - 任何 prompt 的增改都会改变**新 run** 的 manifest，旧 run 由 S6 保护；
   - manifest 会列出本 run 从不调用的 prompt；
   - `assemble_reuse` 的 digest 漂移披露（`assemble_reuse.py:120-133`）会多出无关条目。

   三者都只是"噪声"，不影响正确性。
5. 这样我第一轮提的成员检查、B 提的"实际调用 ⊆ 声明"契约测试、A 的 `BASE_PROMPTS` 都不需要了。

### D3：不新增 methods 层；按名分发的注册表放在 stages 层，方法驱动沿用 `stages/<stage>_<method>.py` 的现有惯例，本体留在 composites

1. S7 把共享件下沉之后，A 指出的反向依赖就消失了：
   - `stage4_live_run.py:132-133` 对 stage1 的 import 改成指向 `composites/shortfall`；
   - `discard_skill` 移到 `composites/skill_status`。
2. 迁移风险主要来自"代码搬家"。搬 K4/K5/K6 会让崩溃注入测试的 monkeypatch 目标失效，而这些测试会**空转也变绿**。证据：`tests/composites/test_live_acceptance_resume.py:710-722` 的 `assert audit_writes == []`；同类还有 `test_curation_resume.py:313`、`test_acceptance_resume.py:355,397,553`。**不搬就没有这类风险。**
3. `stage4_live_run.py` 本来就是"阶段目录 + 方法名"的驱动形态，exam 驱动从 `stage4_acceptance.py` 拆成 `stage4_exam.py` 即可。不必重排层号。
4. 需要做的是在 `tests/test_layering.py` 加一条检查：`stages/stage*_<method>.py` 之间不得互相 import，同时把相对 import 也纳入检查（目前 `:32` 跳过了相对 import）。

### D4：槽位清单与优先级

| 优先级 | 槽位 / 事项 | 时点 |
|---|---|---|
| 1 | acceptance（exam / live_run 已有两个实现） | P1 |
| 1 | delivery（whole_bank / retrieval 已有两个实现）。whole_bank 改走 `skills_for_task` 时，验证命名空间、`skills_injected` 与 golden 相同（`composites/batch_execute.py:202-225`） | P1 |
| 2 | authoring：先做零风险的准备，让 `write_skill` 接收 prompt 名作为参数（`blocks/author.py:319-336`），行为不变 | 准备可放 P0 |
| 2 | authoring 槽位本身：等第二个写法真正被写出来时再建。distill/diagnose 是按数据来源分派、每个 run 都会用到的两个分支，不是可替换的方法（A 的核查 ①f） | 第二写法出现时 |
| 3 | curation：留在 composites；与 `none` 和 `ensure_exam` 一起建槽位 | P4 |
| — | segmentation：不建槽位 | — |
| — | provider：同意 P5 | P5 |

### D5：v3 不开放角色表

1. 在 S6 下，封闭元组唯一的代价（新增角色会破坏在飞 run 续跑）已经不存在。新增角色 = 在 core 里加一项，只影响新 run。
2. 吸收 B 的默认值语义，但放在 core、不放在方法声明里：把 `MODEL_ROLES` 改成 `ROLE_DEFAULTS = {role: "strong" | "weak" | None}`（None 表示必须显式配置，供将来 embedder 之类使用）。顺带按 A 的建议消除它和 `core/models.py:42` 的重名。
3. 这样角色名不变（live 继续复用 author/rewriter），`token_budgets.roles` 的校验（`core/config.py:416-421`）不变，`tb21_split_live_run.yaml:211` 里 auditor 的预算也不必删。我第一轮 R7 关心的漂移因此完全消失，转换器也不需要删除角色。

### D6：同意 A 的"无双格式过渡"，但切换拆成两次提交；upgrade_v2 现在不必做

1. 代码中任何一个提交都只接受一种配置格式。这一点我同意 A，并撤回第一轮"内部读 v2"的说法。
2. 但我主张拆分：
   - **P1a**：建注册表并按名分发，键用 v2 已有的 `acceptance.mode` / `skill_delivery.mode`。manifest 完全不变，所以现有的 golden（`tests/unit/stages/test_stage0.py:599-683`、`tests/unit/core/test_config.py:877-990`）**原样保持绿**，这是最强的"行为不变"证据；
   - **P1b**：纯形状切换到 v3，包括冻结公式、删掉 `_MANIFEST_OPTIONAL_FIELDS`、一次性转换脚本，不夹带任何行为重构。

   出问题时可以二分定位，而 A 的一步到位做不到。
3. upgrade_v2：用"v2 源 run 用 v2 checkout 装配、v2 checkout 跑完"的政策就能覆盖已知的复用需求，因为 `assemble_reuse` 的产物是一个可直接 run 的新目录（README:946-953）。只有在"v3 才有的方法要复用 v2 源 run"真的出现时，才写一个只用于 `_identity_view` 的纯函数。现在先在文档里登记这条政策。
4. 转换后的配置用新文件名（AGENTS §6 规定，启动过正式 run 的配置不得修改）；v2 配置留在旧分支上。

### D7：验收用 golden 产物矩阵；每槽位只保留最轻的两项契约测试

1. 在 P0 之前、在 tag 上生成 golden 产物矩阵：{exam, live_run} × {whole_bank, retrieval} × {full, split}，workers=1，加上崩溃续跑变体。易变字段（`time`、`request_id`、`duration_ms`、锁文件）的归一化只写在测试 helper 里。
2. A 提的"老 checkout 对比新 checkout"等价于现场生成 golden，但需要两个 venv 同时可用；固定 fixture 是一次性成本，之后永久生效。
3. 每槽位的通用契约测试只要两项：未知参数键被拒绝、假模型能跑通。我同意 A：不建通用的中断续跑框架，各方法保留现有的续跑测试。在 D2 下，prompt 子集测试也不需要了。
4. 附加要求：凡是改动了 monkeypatch 目标模块的提交，被改的"断言没发生"类测试都必须补一个正向前提，证明 patch 确实生效过。

---

## 三、我可以接受的最终折中

- **运维前提**：S6 写入 AGENTS 常设规则，形式是"每个正式 run 一条维护分支 + 独立 venv"；重构在独立 clone 中进行。
- **P0（行为不变、manifest 不变）**：
  - RunSettings 进单一的 frozen 上下文；
  - S7 下沉共享件，`assemble_reuse` 上移；
  - `get_plugin` 统一负责 import 插件包；
  - `run_units_disposed` 按 S8 的限定范围抽取；
  - `write_skill` 接收 prompt 名作为参数；
  - 验收：现有测试 + 上述 golden 矩阵。
- **P1a（manifest 不变）**：在 stages 层建 acceptance/delivery 的按名注册表，方法驱动沿用 `stages/<stage>_<method>.py`，不新增层；扩展 `test_layering`。验收同 P0，外加现有 manifest golden 原样保持绿。
- **P1b（唯一一次 schema 切换）**：
  - 配置改为 v3 形状，方法参数用 Params dataclass，在 pipeline 的解析步骤里校验；
  - 冻结公式 = prompts 包的全部 prompt + 本配置插件的任务 prompt；
  - 删掉懒注册和 `_MANIFEST_OPTIONAL_FIELDS`；
  - `ROLE_DEFAULTS` 取代 `MODEL_ROLES`，角色名不变；
  - 一次性转换脚本，结果写成新文件名；
  - 阶段 0 遇到 v2 manifest 时，提示"请用该 run 的维护分支续跑"；
  - 同步 scripts/ 和 runbook，其中审计脚本改为按 manifest 判断模式（`scripts/audit/audit_live_run.py:605-616`）；
  - 验收：manifest 以外的产物与 golden 一致；每份配置的 v3 manifest 由人工审定一次。
- **P4（预注册的新协议）**：`ensure_exam` + `curation: none` + curation 槽位；authoring 槽位等第二个写法出现时再建。
- **不做**：needs/provides、`extends`、类型化 Summary、methods 新层、目录迁移、upgrade_v2（等真有需求再说）、任何 hash 或审计机制。

---

## 第三轮：收敛表决（提纲见决策文档附录 B）

# 第三轮 · 架构师 C：R1–R7 表态

已读 round3_brief.md、round2_A.md、round2_B.md。仓库只读，没有运行任何东西。路径相对 `src/expedistill/`。

**结论：R1–R7 全部接受，没有否决。** R1、R2、R3 与我第二轮的立场不同，下面专门说明我为什么改投。

- **R1 接受。**
  - 我第二轮反对新增一层，唯一的理由是"搬 K4/K5/K6 的本体会让崩溃注入测试空转也变绿"。这些测试 patch 的是 `la` / `curation` / `acceptance_mod` 模块里的名字（例如 `tests/composites/test_live_acceptance_resume.py:710-722`）。R1 规定组合积木本体留在 composites，这个风险就消除了；A 的职责边界论证（方法放进 composites 会违反"配置以显式参数传入"）也成立。
  - 补充：驱动搬家同样会移动一部分 patch 目标，例如 `tests/unit/stages/test_stage4.py:773` patch 了 `stage4_acceptance.accept_skill`，`tests/unit/stages/test_stage6.py:226` patch 了 `stage6_test.batch_execute`，共 4 处。按已收敛的 D7 处理即可：不留 shim、模块和测试同一提交迁移。

- **R2 接受。**
  - 我第二轮主张 P4 演进为 `ensure_exam`，现在撤回。B 的复核成立：`ensure_exam` 在 live_run 配置下也会改变候选集；K5 遇到源考题缺失会跳过合并（`composites/curation.py:705-707`）。A 指出的 K5 并行前提也成立：同一波内只读不可变的 exams（`composites/curation.py:45`）。所以显式写 `authoring.exams`、缺省为 true，对现有配置零语义变化，是更好的选择。
  - 补充：这个键应在 **P1b 的 schema 切换里**就写进 v3 形状（缺省 true、补全后冻结），P4 只是放开 `false` 并加上集中校验。原因：如果 P4 才往冻结配置里加这个键，P1b 到 P4 之间启动的 v3 run 与之后的 run 会多出一个"只在一侧出现的键"，`assemble_reuse` 的身份比对会拒绝（`composites/assemble_reuse.py:360-372`）。B 的第二轮折中也把它放在 schema 切换时。

- **R3 接受。**
  - 我第二轮认为"v2 源 run 用 v2 checkout 装配"就够了。但主持人给的场景确实走不通：用一个只在 v3 才有的新 delivery 去复用当前在飞 TB v2 run 的冻结库，旧 checkout 里根本没有这个方法。所以 `upgrade_v2` 是必要的。
  - 补充三点限定：
    - 它只服务两处：`assemble_reuse` 的 `_identity_view`，以及一次性配置转换。stage0 和 pipeline 永不加载 v2，保证不出现第二条兼容路径；
    - 必须把 `acceptance` 的三种 v2 写法（缺键 / `{}` / `{mode: exam}`）映射成同一个值；
    - golden 测试覆盖 `configs/experiments/` 下的全部 v2 配置。`--level baseline` 不需要它（B 已指出）。

- **R4 接受。**
  - 静态测试建议写成两条：
    - `src/` 里的 prompt 注册调用只能出现在 `prompts/` 和 `benchmarks/<name>/` 下；
    - `prompts/` 下的每个模块都必须被 `prompts/__init__.py` 的 import 清单引入（A 的版本）。

    两条合在一起，才能保证"全量冻结 = 实际可调用的全部 prompt"。
  - 我同意不再加 `call_model` 成员检查，有了这两条测试它是多余的。

- **R5 接受。** `None` 档位目前没有使用者，可以等第一个需要显式配置的角色出现时再启用。改名本身可以在 P1b 一并做。

- **R6 接受。** 这与我第二轮对 S6 的补充一致。证据是历史上确有在飞 run 需要热修，修复后在原 run 上续跑：`scripts/run/prepare_inplace_recovery.py` 的 docstring 写明了对 AGENTS §6.8 的豁免。

- **R7 接受。**
  - 补充：`n_attempts ≤ n_attempts_weak` 这条新校验会让现有测试夹具失败：
    - `tests/unit/stages/test_stage0.py:133` 的夹具 `n_attempts_weak=2`；
    - 同文件 `:719` 与 `:744` 的 live 配置写的是 `n_attempts: 3`。
  - 因此这条校验必须放在 P1b，不能放 P1a，否则会打破"P1a 现有 golden 原样保持绿"的承诺。正式配置不受影响：`configs/experiments/tb21_split_live_run.yaml:147` 的 `n_attempts_weak` 是 3，`:186` 的 `n_attempts` 是 3。
