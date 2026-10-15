# ExpeDistillation

强模型轨迹蒸馏成 Skill 库、教弱模型的实验仓库（开发名 SkillPool）。
不训练模型权重，只维护自然语言 Skill；Skill 以规则形式注入弱模型的 prompt，
这是它影响弱模型的唯一通道。

## 一、模块拆解（积木）

整个系统由一组积木拼成。积木分三层：**基础件**（所有积木共用的底座）、
**功能积木**（做一件具体的事）、**组合积木**（把几块功能积木串成一个常用
动作）。每个阶段只是把积木按顺序接起来，再加上逐单元落盘和断点续跑。

### 1.1 积木规则

1. **积木只认对象，不认阶段**：功能积木的输入输出都是数据模型里的对象
   （或对象列表、或纯文本），它不知道自己在第几阶段，也不知道文件存在哪里。
   落盘和断点续跑由组合积木和阶段流程负责。
2. **模型是参数**：凡是调用模型的积木，用哪个模型由调用方传入（从配置的
   角色表里取），积木内部不写死"强模型"或"弱模型"。换判卷模型、换出题模型、
   让弱模型自己切段做消融，都只改配置。
3. **跟 benchmark 有关的东西打包成一个 benchmark 插件**：执行 runner、任务加载、
   轨迹视图的截断规则、执行任务 prompt 四样，放在同一个插件目录里，通过插件
   注册表按名字取用。其余积木只处理统一格式的 Trajectory / Subtask / Skill /
   Exam，对 benchmark 无感知。加一个 benchmark = 加一个插件目录。
4. **所有模型调用走同一个基础件**（B1 调用模型），统一处理输出格式错误的重试、
   用量记账和请求原文落盘；积木自己不直接连模型接口。唯一例外是 E1 里执行
   agent 的模型调用：本地 runner 用 B1 的底层客户端逐步调用；TB runner 的模型
   调用发生在远程 Harbor 里，用量从 Harbor 结果中读回再记账。
5. **每块积木可以单独测试**：模型可以换成返回固定内容的假模型，程序类积木
   直接用构造好的对象测。
6. **ID 由程序按确定规则生成，不由模型给**：子对象 ID = 父对象 ID + 序号
   （如 `subtask_id = <trajectory_id>/seg<i>`），同样的输入重跑得到同样的 ID，
   断点续跑时据此判断"这个单元做过没有"。
7. **可替换的规则做成具名规则**：挑题规则（D4）、段筛选规则（D6）等，在规则
   注册表里按名字登记，配置里写名字，名字进 manifest 冻结。做消融或换定义时
   新增一条具名规则，不改旧规则。

### 1.2 基础件

| 编号 | 名称 | 做什么 |
|---|---|---|
| B1 | 调用模型 | 输入（模型, prompt 名, 变量, 输出格式）→ 解析好的结果。格式不对时按规则重问；基础设施异常重发同一请求（次数由运行设置给出）；每次请求和用量落盘 |
| B2 | Prompt 注册表 | 所有 prompt 的具名常量；计算 digest 供 manifest 冻结（见 1.6） |
| B3 | 对象存储 | 五个对象的保存 / 读取 / 列举；判断某个单元是否已完成（断点续跑用）；JSONL 账本的加锁追加与断尾修复 |
| B4 | Manifest 冻结 | 写一次、之后逐字段比对，不一致即拒绝继续（沿用 SkillExpand 的 `freeze`） |
| B5 | 并行池 | 与业务无关的线程池 / 进程池 |
| B6 | 可靠性 | 异常分类（基础设施 / 模型输出 / 配置 / 代码 bug）、重试策略、单元失败记录（沿用 SkillExpand 的 `reliability`） |
| B7 | 运行锁与用量累计 | run 目录排他锁（防止两个进程写同一个 run）；用量跨进程累加落盘，续跑后不清零 |

**B1 的两种重试**（`docs/decisions/call_infra_retry_20261011.md`）：

- 输出格式不对（解析不出要求的 JSON）→ 带修复说明重问，次数 `max_format_retries`；
- 基础设施异常（连接错误、超时、HTTP 408 / 409 / 429、5xx）→ 原样重发同一个请求，
  次数由运行设置 `--llm-infra-retries` 给出（默认 3），第 n 次重发前等待
  `--llm-infra-backoff`（默认 5 秒）× 2ⁿ，单次等待最多 60 秒；
- 两种预算互相独立：每个请求（首次请求和每一次修复重问）都有完整的基础设施预算，
  所以一次调用最多发 `(1 + 格式重试次数) × (1 + 基础设施重试次数)` 个请求；
- 其余 4xx（key 错、模型名不存在、上下文超长等）不是基础设施异常，而是**服务方拒绝**：
  重发同一个请求没有用，不重试，流程停下等人处理（退出码 1，见第三节"运行规则"）；
- 每一次尝试（包括失败的）都记一行用量、落一份请求原文到
  `<run>/llm_requests/NNNN_<prompt>.json`。序号加锁预留，文件用不覆盖的方式发布，
  多线程同时落盘也不会互相覆盖；本地 benchmark 执行时的请求同样落在这里，
  不散落到各单元的工作目录。API key 永不落盘。

**B1 的输出上限与空回复**（真实端点冒烟发现，2026-10-11）：

- 每次模型调用的输出上限默认 `max_tokens = 5120`（推理模型开满
  `reasoning_effort` 时要几千输出 token，不设上限会让一条失控的推理轨迹
  烧穿整步预算）；按模型覆盖走配置的 `model_options` extra body，例如
  `{"DEEPSEEK_up5zdj": {"max_tokens": 128, "reasoning_effort": "max"}}`——
  客户端把 extra 合进请求体发生在句柄级 `max_tokens` 之后，所以 extra 里的
  `max_tokens` 盖过默认值（合并顺序见 `llm/call.py` 与 `llm/client.py`）；
- 模型返回空内容或全空白回复是 **ModelOutputError**（消息里带 `finish_reason`，
  分得清"预算烧在推理上"（length）和"模型选择沉默"（stop）），不重发。本地
  ReAct 一局里发生时，这一局记为 `failure`（`termination = model_output_error`，
  已走步骤保留），**不重试**——重试等于对被测模型反复重采样直到它答好（见第三节
  运行规则）；
- 请求原文落盘记 `finish_reason`，用量账本每行记 `finish_reason` 与 `cached_tokens`
  （网关侧的缓存模板会把 prompt token 虚高数百倍；按角色比成本时要看
  `cached_tokens`）。

### 1.3 功能积木

**执行类**

| 编号 | 名称 | 输入 → 输出 | benchmark 相关 |
|---|---|---|---|
| E1 | 执行（Executor） | （模型, 一道任务, skill 列表（可选））→ Trajectory（含结果：成功 / 失败 / 基础设施异常） | 是：内部按 benchmark 选 runner |

**基础设施异常不算失败**：沙箱启动超时、评测超时、网络错误等不是模型做错了，
E1 把结果标成 `infra_error`，而不是 `success = false`。K1 按 B6 的策略对这类
单元重试；D3 统计成绩时只算有效执行（成功 / 失败），基础设施异常单独计数。
重试耗尽的单元不落盘（"已存 = 已完成"，存了续跑就不会再试），所以 D3 按调用方
给出的应有单元集合计分：应有而缺失的单元算一次基础设施异常（见阶段 1、6）。

E1 内部的 runner：

| Runner | Benchmark | 执行方式 | 使用的 eval 仓库 |
|---|---|---|---|
| TB runner（独享） | TerminalBench | 直接委托远程执行（Harbor + 腾讯沙箱一次 rollout），结束后从 Harbor trial 目录还原逐步轨迹 | 远程任务镜像 + Harbor（node29 链路） |
| SearchQA runner | SearchQA | 本地一步一步执行到结束 | 外层工作区 `benchmark/searchqa-eval/` |
| ALFWorld runner | ALFWorld | 本地一步一步执行到结束 | 外层工作区 `benchmark/alfworld-eval/` |

SearchQA 和 ALFWorld 的环境、任务表、判分**直接采用外层工作区 `benchmark/`
下现有的两套 eval 仓库**，不在本仓库内重造（ALFWorld 必须用其 venv 并加载
`ALFWORLD_DATA` / `ALFWORLD_CONFIG` / `ALFWORLD_BENCH_SRC`，见外层 AGENTS.md）；
runner 只做一层适配，把逐步交互整理成统一 Trajectory。迁移时以 SkillExpand 的
`BaseEnv`（reset / step / success_fn）+ benchmark 适配器 + 执行 worker 为基础；
TB runner 复用 `scripts/tb21` 那套远程链路（runner 脚本、沙箱环境变量注入）的经验。
两套本地 runner 的每局步数上限都用环境变量配：SearchQA 是
`EXPEDISTILL_SEARCHQA_MAX_STEPS`（默认 6，即原 QAEnv 的默认步数），ALFWorld 是
`EXPEDISTILL_ALFWORLD_MAX_STEPS`（默认 50）；设了但不是 ≥1 的整数即配置错误。

**TB runner 的远程 run id 与续跑**（`docs/decisions/tb_run_namespace_20261011.md`）：

- 远程 run id = `ed_<task>_<命名空间>_<attempt token>`。命名空间由 K1 计算：
  run 目录的真实路径、角色、模型名、注入的 skill 引用、渲染后 skill 文本块的摘要，
  合起来取 sha256 前 10 位。远程 jobs 目录按远程根目录共享、结果按 run id 查找，
  有了命名空间，阶段 1 强 / 弱两臂、阶段 6 与阶段 1、两个共用远程根目录的实验
  都不会撞 id，不会把别的臂的 `result.json` 当成自己的结果；
- **同一个 run id 只提交一次**。提交时记两份启动条件（run id、转发的环境变量摘要、
  SKILL.md 摘要）：本地 `<单元工作目录>/tb_remote/<run_id>.json`（另记 runner pid）
  和远程 `jobs/<run_id>.ed_meta.json`。续跑时远程已经有这个 run id 的任何痕迹
  （结果、jobs 目录、日志、meta），只有两份记录都在、且都与本次请求一致才复用：
  有 `result.json` 就直接取结果，没有结果但 runner 还活着就接着等；其余情况一律
  返回 `infra_error`，由 K1 换下一个 token 重跑，绝不往旧 run id 里再提交一次；
- **核对进程身份**：判断 runner 还活着、判定它已经退出、超时终止它，都先核对
  `/proc/<pid>/environ` 里有 `RUN_ID=<run_id>`。只看 `kill -0` 会把复用了这个 pid
  的无关进程当成自己的 runner，甚至把它杀掉。核对不了（远端没有 `/proc`、没有权限）
  就当作"不是我们的"：宁可换 token 重跑，也不发信号。超时终止只杀记录下的那个
  进程组，从不用 `pkill` / `pgrep -f`。

**数据整理类**（纯程序，不调模型）

| 编号 | 名称 | 输入 → 输出 | benchmark 相关 |
|---|---|---|---|
| D1 | 任务加载 | （benchmark, 任务表）→ 任务列表 | 是 |
| D2 | 划分 | （任务列表, 模式 split/full, 种子）→ train / test / reserved（reserved 不进任何阶段；显式划分只给两键时恒为空列表） | 否 |
| D3 | 成绩统计 | （Trajectory 列表, 应有单元集合（可选）, 辅助打分器（可选，由 benchmark 插件声明））→ 每题有效次数 / 成功次数 / 基础设施异常次数（应有而缺失的单元计入基础设施异常），以及两种口径的 avg@N、pass@N；插件声明了辅助指标时，同一分母、同两种口径的辅助 avg@N（`aux` 一节，未声明为 null）。live_run 验收的判定同样消费它的单题计数形状（`{attempts, valid, success, infra}`，声明辅助指标时另有 `aux_success`） | 否（辅助指标由插件声明，本积木只收打分器） |
| D4 | 挑题 | （每题成绩, 具名规则）→ 题目集合。阶段 1 由配置的 `task_select` 规则决定，v2 默认 `any_weak_failed`：任一有效尝试失败即入 F；部分 infra + 有失败的题既进 F、也在 shortfall 清单 | 否 |
| D5 | 轨迹视图 | （Trajectory 或段内步骤, 视图配置）→ 给模型看的文本；超长观察按规则截断，截断处明确标注。live_run 验收的归因与修订输入（G10 的 `live_views`、G11 的 `failure_view`）同样经它渲染 | 是：截断规则按 benchmark 配置 |
| D6 | 段筛选 | （Subtask 列表, 规则）→ 要生成 skill 的 Subtask。由配置的 `segment_select` 规则决定，v2 默认 `strong_success_weak_failed`：强模型段只取 `success = true`（做法材料），弱模型段只取 `success = false`（教训材料）。旧名 `strong_all_weak_failed` 为 v1，与 v2 的 G2 prompt 不兼容 | 否 |
| D7 | 切段校验 | （Trajectory, Subtask 列表, K）→ 通过 / 错误清单 | 否 |
| D8 | 泄题审计 | （Skill, Exam, 模型（auditor 角色，默认强模型））→ 泄露 / 不泄露 + 理由。LLM 判官：skill 包含、平凡改写或直接揭示参考答案及考题答案特有实体（名字、数值、文件名）即泄露；通用方法建议、协议词、动作名、环境反馈串不算泄露。理由永不复述参考答案。**exam 模式专用**：live_run 验收没有"考题参考答案"这个保护物，不跑 D8，防退化的三层既有防线见阶段 4 | 否 |
| D9 | skill 注入 | （skill 列表, 分组信息（可选））→ 插入 prompt 的规则文本块。live_run 验收两轮的组注入经同一函数渲染——注入字节与 injection_audit 冻结行的 sha256 逐字节一致由"同一份实现"保证 | 否 |
| D10 | 库长度统计 | skill 列表 → 注入后的 token 数。live_run 验收的注入 token 估算用同一函数（记进 injection_audit 行；超过 `max_injection_tokens` 的组零执行丢弃） | 否 |
| D11 | skill 检索 | （任务文本, skill 库, top_k / max_categories / min_score）→ 选中 skill 子集 + 审计记录（`selected`、`routed` 类别名、`scores` 逐条分数、`threshold_passed`）。词法两步检索纯函数：`derive_categories` 把 G9 细组确定性凝聚成粗类别，`select_skills` 先路由到至多 `max_categories` 个类别、再类内 TF-IDF 余弦取 top-`top_k`（检索文档 = skill 全文，标记节权重 ×3）；`min_score` 只门 top-1，低于阈值返回空集 | 否 |

**模型类**（每块对应注册表里的一个或两个 prompt，模型由调用方传入）

| 编号 | 名称 | 输入 → 输出 |
|---|---|---|
| G1 | 切段 | （轨迹视图, K, 模型）→ 段列表（未校验）。模型只给每段的**起止步号**和 label / description / success，不复述步骤原文；段内步骤由程序按步号从 Trajectory 取 |
| G2 | 写 skill | （Subtask, 写法 = 提炼 / 诊断, 弱模型画像, 模型）→ Skill v1。提炼用于强模型段，写成 procedure（执行步骤）写法；诊断用于弱模型段，写成 failure reference（失败参考：哪一步开始错、正确做法是什么）写法。两种写法都是固定四节结构：模型按 JSON 契约输出，程序做字段校验、再确定性渲染成标记文本；write_skill 带前提守卫——提炼要求段 `success = true`，诊断要求段 `success = false` |
| G3 | 出考题 | （Subtask, 模型）→ Exam。**不接收 skill** |
| G4 | 作答 | （模型, Exam, skill 列表（可选））→ 作答文本。带 skill 与裸答用**同一个 prompt 模板**，唯一差别是 D9 注入的规则块是否为空 |
| G5 | 判卷 | （Exam, 作答, 模型）→ 对 / 错 + 错误描述。错误描述只说哪里不对，不复述正确答案，也不说明或暗示该怎么改（正确做法、缺什么步骤、答案本该说什么） |
| G6 | 改写 skill | （上一版 Skill, 考题, 错误作答, 错误描述, 弱模型画像, 模型）→ Skill 新版本 + 错因归类。不接收参考答案 |
| G7 | 找相似与冲突 | （skill 列表, 模型）→ 候选合并组、冲突对。先用字面相似度粗筛，再由模型确认 |
| G8 | 合并 skill | （一组 skill 或一对冲突 skill, 模型）→ 合并后的 Skill（`merged_from` 记录来源） |
| G9 | 分组 | （skill 列表, 模型）→ 每条 skill 的能力分组标签 |
| G10 | live 归因 | （任务执行 prompt 的渲染产物, 基线逐次状态行摘要, 本轮**全部**尝试的轨迹视图（D5，成败都给）, skill 组列表, 弱模型画像, 模型（author 角色））→ 组级诊断 + 逐 skill 处置计划：`{"diagnosis", "skill_actions": [{"skill_id", "action": keep\|revise\|delete, "problem"}]}`，恰好覆盖组内每条 skill 一次，revise / delete 必带非空 problem。**live_run 验收专用**（阶段 4）：对照的是同一 task 的真实执行而非考题 |
| G11 | live 修订 | （skill 现文本, 归因报告（组诊断 + 本 skill 的 problem）, 失败尝试视图摘录（D5）, 弱模型画像, 模型（rewriter 角色））→ 保节结构的新正文 + 错因归类 + 修订理由，输出契约与 G6 同构（复用 `FAILURE_CATEGORIES` 与四节纪律）。与 G6 的差别在输入：G11 修订的依据是真实执行失败，不是考题作答；修订纪律（不得抄本题具体数值/文件名/实体名）是硬约束，防归因修订把具体值塞回 skill |

"弱模型画像"是配置里的一段文字（弱模型是谁、已知弱点），是 G2 / G6 的输入，
换弱模型时只换这段配置。

### 1.4 组合积木

| 编号 | 名称 | 组成 | 说明 |
|---|---|---|---|
| K1 | 批量执行 | E1 × 题目 × 采样次数，经 B5 并行 | 每完成一次执行就落盘一条 Trajectory，可续跑；`infra_error` 按 B6 重试，重试耗尽的单元不落盘、记入失败账本，续跑时再试 |
| K2 | 切段（带校验） | D5 → G1 → D7；不通过就把错误清单交给 G1 重切 | 重切次数上限 `segment_retry_limit`；耗尽则跳过这条轨迹并记入审计日志，本 run 内不再重切 |
| K3 | 考一次 | D9 → G4 → G5 → 记一条 Attempt | skill 列表为空即裸答；Attempt 记录用途（裸答 / 验收 / 回归）；先存作答、再判卷 |
| K4 | 验收（exam 模式） | K3（裸答）→ 循环 [K3（带 skill）→ 不过则 G6 → D8] | 裸答就对则丢弃；循环上限 `retry_limit`；D8 不通过的版本作废、本轮算用掉一次，回到 G6 重写。文本考试协议，由配置 `acceptance` 缺省（exam）选中 |
| K5 | 库整理（Curator） | G7 → G8 → K3 回归考试 → G9 → D10 | 回归考试覆盖合并所代表的全部原始 skill 的考题，任一不过就撤销该次合并；超出 `bank_token_budget` 时回到 G7/G8 继续合并，最多 `curation_rounds` 轮；仍超出则阶段 5 停下报告，不自动删 skill |
| K6 | live-run 验收 | 逐组阶段函数库：基线加载、注入审计行构造（首写与续跑比对共用一个构造器）、轮判定（具名 `ACCEPTANCE_IMPROVE` 规则 × D3 计数）、G10 归因、G11 修订应用、no-op 守卫、幸存组推导、组终态提交；两轮钉死。轮序编排、K1 派发与重派发集、injection_audit 首写、shortfall 记录、摘要归 `stages/stage4_live_run.py`（裁决 A16 的分工） | live_run 模式的验收（阶段 4，配置 `acceptance` 选中）：验收单位 = 一个 train task 的 pending 候选组，经 K1 真实执行、与阶段 1 裸跑基线按预注册判据比对，两轮不过整组丢弃；状态全部从磁盘推导（verdicts / attributions / 两个失败账本 + injection_audit + skill 版本号），任意崩溃点续跑等价。**编号与阶段 6 无关**：这是组合积木的第 6 号，test 阶段仍由 K1 / D3 承担 |
| — | 装配工具 | `composites/assemble_reuse.py`：从源 run 按 train 溯源过滤，装配出新 run 的阶段 0–5 产物与 skill 库 | 复用已完结 run 的蒸馏产物、只重跑 test 侧实验（如检索式分发对同一冻结库重测）的入口：allowlist 拷贝（未列即不拷）+ 负清单断言 + 与源 manifest 的白名单 diff（仅 `mode` / `split` / `tasks` / `skill_delivery` / `schema_version` 允许不同），库行与拷贝对象逐位一致、零 test / reserved 溯源，run 根写 `ASSEMBLED_FROM.md` 溯源，拒绝已含 manifest 的目录。**不给 K 号**：它是装配工具，不是把积木串成阶段动作的组合件，放 `composites/` 只因它负责落盘与校验。两档位：bank=过滤旧库零调用复用（消融用）；baseline=仅拷阶段 1 轨迹，阶段 2–6 按当前协议全新执行（修订三默认） |

组合积木负责落盘和续跑：每一步的产物按确定 ID 存进 B3，再次运行时已完成的
单元直接读盘跳过。功能积木本身不读写磁盘。总的要求是：**在任何一处崩溃后续跑，
结果都与一次跑完相同**（少数已知例外写在各决定文档的"已知局限"里，例如 K3 作答
的修复预算不跨崩溃保存）。各组合积木为此定下的规则：

**K1 批量执行**

- 派发顺序按轮次：先派所有题的第 0 次采样，再派所有题的第 1 次……，同一道题的
  多次采样不相邻；返回值仍按 trajectory_id 排序；
- 重试游标：每个单元的工作目录里有 `k1_retry_cursor.json`，记着下一个没用过的
  重试序号。续跑从这里接着数，崩溃时正在跑的 token 会被再次请求（可复用远程
  结果或接上存活的 runner），已经判为 `infra_error` 的 token 不再请求；
- 批弹性（`docs/decisions/k1_batch_resilience_20261013.md`）：每个单元 3 次尝试
  （`infra_retries` 默认 2），重试之间按 uniform 随机等待 15–45 秒——第一次重试落在
  开局连接风暴窗外，随机值防止并行 worker 的重试再次对齐；`--k1-stagger` 让每个单元
  在 worker 领取后、error 门判定后、执行前随机等待 0 到 N 秒（被 error 门跳过的单元
  不等待），错开并行派发的 ssh 连接（TB 远程建议 20，本地执行 benchmark 用 0）；批
  开始向 stderr 打一行 `K1 dispatch[<role>]`（pending / skipped / total、workers、
  stagger、backoff），pending 非空时每 300 秒再打一行 `K1 heartbeat[<role>]`
  （done / failed / skipped / in_flight / elapsed），"正常慢"与"卡死"必须可区分。基础
  设施重试耗尽的单元记失败账本、等续跑；其他异常的停止语义见本节末条；
- 续跑前核对已存单元：已存轨迹记录的角色、模型名、注入的 skill 与本次请求不一致，
  就报配置错误并提示换新 run 目录；已存文件从不覆盖。防的是库重新整理或换了模型
  之后重跑，旧轨迹被原样当成新条件下的结果；
- `skip_unit_ids`：调用方声明为终态的单元（已接受的基础设施空洞，见阶段 1），
  K1 不执行、不记失败；
- 任一单元抛出基础设施以外的错误后，不再启动新单元，已在跑的跑完并落盘；配置错误、
  服务方拒绝按原类型抛出，代码 bug 保留完整调用栈。

**K3 考一次**

- 作答一返回就存成未判卷的 Attempt（`judge_verdict = null`），判卷后用同一个
  attempt id 再存一次。续跑时遇到同一用途、同一 skill 版本、同一轮次的未判卷作答，
  只补判卷，不重问弱模型；
- 判卷失败的次数记在这条未判卷 Attempt 上，崩溃后不会重新给满预算；
- 凡是按已存作答判断"这一步做过没有"的地方（K4、K5），只认已判卷的记录。

**K4 验收**

- 每次改写成功**立即**落盘，然后才审计、才考，所以磁盘上 v 版的作答一定是在
  磁盘上 v 版正文上做的；
- 改写（G6）每失败一次就写一行 `rewrite_failures.jsonl`，预算耗尽的那一次标
  `exhausted`；下一轮 = （版本号 − 1）+ 改写耗尽的轮数，全部从已落盘记录推导，
  Skill 不加轮次字段；
- "已通过"要求那次通过的作答正是磁盘上的当前版本，旧版本上的通过不算数；
- 泄题审计不通过按（版本, 轮次）只记一次，续跑不重复追加。

**K5 库整理**

- `curation_log.json` 是整理日志：每轮的 G7 输出、每个合并的决定、每轮的 G9 输出
  **先写日志、再改 skill 状态**，续跑按日志重放，不重新问模型；
- 一次合并的提交顺序：合并 skill 存为 pending（pending 永远不是库成员）→ 回归考试 →
  决定（保留 / 撤销）写进日志，这是提交点 → 保留则先把各来源标为 `merged`、最后把
  合并 skill 翻成 `accepted`，撤销则合并 skill 存为 `discarded`。续跑时日志里有决定
  而合并 skill 还是 pending，就补完这一步；没有决定的 pending 合并 skill 是下一个
  单元的在途合并，来源一致就复用，否则回滚（`interrupted_merge`）；
- 合并编号 = store 里已有 `bank/merge/NNNN` 的最大编号 + 1，撤销和回滚的也算在内，
  **编号永不复用**，任何已有合并记录都不会被覆盖；
- 回归考试沿 `merged_from` 递归展开到阶段 3 的原始 skill，每个原始来源的考题都要过，
  合并的合并不能只考锚点那一道；
- 输入集快照与过期检查：日志记下整理开始时的输入集（当时所有 accepted skill）。
  之后每次续跑都核对：store 里实际的 accepted 集合必须等于"输入集 − 保留合并的来源
  + 保留的合并 skill"。前面阶段续跑时补出了新的 accepted skill → 报
  "skill bank is stale"；其余对不上 → 报 "corrupted run"。两者都是配置错误，
  绝不把没经过整理的 skill 悄悄收进库。处理方法只有一个：**换新 run 目录重跑**，
  不在原目录删掉库和日志重新整理——原地重整后阶段 6 已存的轨迹对应的是旧库，
  K1 会拒绝续跑，这条路走不通。

**K6 live-run 验收**

- **零新状态对象**：一个组处于流程的哪一步，全部从磁盘推导——轮 r 执行完 = 轮 store
  （`live_run/round{NN}/`）里该组的应有单元（task × `n_attempts` 全量集）全部已存或在
  skip 并集内；轮 r 已判定 = `verdicts.jsonl` 有该 (task, round) 行；已归因 =
  `attributions.jsonl` 有该行（append-once、首判为准，G10 绝不重问）；某条修订已应用 =
  该 skill 的版本号已越过归因行记录的版本（version == from_version → 施加，
  == from_version+1 → 跳过，其余 → 代码 bug）；组终态 = 成员 status；
- **重算一致性守卫**：续跑时判定作为纯函数重算（基线计数 × 轮 store 计数 × 具名规则），
  与账本首行不一致 → 代码 bug 停机——store 被外部改动的防腐层；
- **持久化顺序**：verdict 行先于任何 status 提交落盘（崩溃截断的提交由 finalization
  sweep 幂等补完）；归因行先于任何修订动作原子追加；每条修订成功立即 store.save
  （版本号即完成证明）；injection_audit 行由阶段在派发前首写冻结，此后一切读取
  （no-op 守卫的 sha、verdict 行的 token 数）都回到冻结行，重派发前重推导的内容与
  冻结行逐字节比对，不等 → 代码 bug；
- **已知不等价点**：结果未落盘的模型调用会被重问（K3/K4 的既定豁免同款）——覆盖
  G10 计划在调用与账本追加之间丢失、G11 修订在调用与 save 之间丢失、以及归因预算
  耗尽行（蓄意写在组终态之后——discard 是事实，账本行是它的回执）三种崩溃窗口。

### 1.5 积木复用

**阶段 → 积木组装**

| 阶段 | 用到的积木 |
|---|---|
| 0 准备与冻结 | D1、D2、B4 |
| 1 双模型基线执行 | K1（强模型, 裸跑）、K1（弱模型, 裸跑）、D3、D4 |
| 2 轨迹切段 | K2（对强、弱模型轨迹各调用） |
| 3 skill 与考题生成 | D6、G2（提炼 / 诊断）、G3，G2 与 G3 互相独立可并行 |
| 4 验收 | 由配置的 `acceptance.mode` 决定：exam（缺省）→ K4（内含 K3、G6、D8）；live_run → K6（内含 G10、G11，两轮经 K1 真实执行） |
| 5 库整理 | K5（内含 G7、G8、K3、G9、D10） |
| 6 test | K1（弱模型, 带库：whole_bank 整库注入，或 retrieval 模式先经 D11 逐任务检索、由 D9 的 `format_skills_block` 渲染选中子集）、K1（弱模型, 裸跑，仅 split 模式）、D3 |

**复用最多的几块**

- **D9 skill 注入**：阶段 4、5 的作答（经 G4）和阶段 6 的真实执行（经 E1）
  都通过它把 skill 拼进 prompt。注入格式只有一份实现，"验收时的注入方式和
  test 一致"由代码结构保证，而不是靠约定。
- **K3 考一次**：阶段 4 的裸答、带 skill 作答，阶段 5 的回归考试，都是它。
- **K1 / E1 执行**：阶段 1 强、弱模型各一次，阶段 6 带库、裸跑各一次。
- **D5 轨迹视图**：G1 切段要看整条轨迹，G2 写 skill / 诊断要看段内步骤。
- **D3 成绩统计 + D4 挑题**：阶段 1 求失败题集合 `F`，阶段 6 算成绩；换 `F`
  的定义（比如"成功率低于某值"）只换 D4 的规则。
- **G2 写 skill**：强模型段和弱模型段是同一块积木的两种写法。

**跨 benchmark、跨模型**

- 加一个新 benchmark：写一个 benchmark 插件（runner、任务加载、截断规则、
  执行 prompt 四样）。其余积木不动。
- 换强模型 / 弱模型：改配置里的角色表和弱模型画像。积木不动。
- 做消融（比如不做裸答对照、不做库整理、换切段模型）：在阶段流程里去掉或
  替换某块积木，其余照常。

### 1.6 Prompt 统一管理

所有发给模型的 prompt **集中在 B2 注册表里统一管理**，不在业务代码里散落字符串。

| prompt | 所属积木 | 典型使用者 |
|---|---|---|
| 切段 | G1 | 强模型（阶段 2） |
| skill 提炼（强模型段） | G2 | 强模型（阶段 3） |
| skill 诊断（弱模型段） | G2 | 强模型（阶段 3） |
| 出考题 | G3 | 强模型（阶段 3） |
| 作答（带 skill / 裸答共用） | G4 | 弱模型（阶段 4、5） |
| 判卷 | G5 | 强模型（阶段 4、5） |
| 改写 skill | G6 | 强模型（阶段 4） |
| 找相似与冲突 | G7 | 强模型（阶段 5） |
| 合并 skill | G8 | 强模型（阶段 5） |
| 分组 | G9 | 强模型（阶段 5） |
| 执行任务（每个 benchmark 一份） | E1 的 runner | 强 / 弱模型（阶段 1、6） |
| LIVE_RUN_ATTRIBUTION | G10 | author（阶段 4，仅 live_run 模式） |
| LIVE_SKILL_REVISION | G11 | rewriter（阶段 4，仅 live_run 模式） |

**两个 live prompt 是懒注册的**（`docs/decisions/acceptance_live_run_20261014.md`）：
`prompts/__init__.py` 的 `METHOD_PROMPT_NAMES` 是 import 时的快照，任何新名字出现都会
让在飞 exam run 的 manifest 比对拒绝续跑，所以 `LIVE_RUN_ATTRIBUTION` /
`LIVE_SKILL_REVISION` **不进** `prompts/__init__.py` 的 import 清单，而在两处按需注册：
stage 0 只在 `acceptance.mode == live_run` 时条件 import 它们并把两个 digest 并进
manifest（exam run 的冻结 digest 集逐字节不变）；`blocks/attribution.py` 模块顶部防御性
import 同一模块，绕过 stage 0 的调用方（单测、脚本）不会撞 PromptNotFoundError。
后果：(a) 冻结的 prompt 名集随验收模式不同；(b) 两个 prompt 各有一份 golden 测试钉住
digest——**prompt 文本演化时必须有意识地 bump 对应 golden**，漏 bump 即测试红。

沿用 SkillExpand 的两条规定：

1. 每个 prompt 是注册表里的具名常量，带简短说明；
2. **每个 prompt 的文本摘要（digest）写进 run 的 manifest**——prompt 是协议的
   一部分，run 开始后改动任何 prompt 文本即视为新实验（沿用 `prompt_digests`
   冻结机制）。

### 1.7 代码目录

目录结构、分层规则和开发规范见 [AGENTS.md](AGENTS.md)。积木到目录的对应：
基础件在 `core/`、`llm/`、`prompts/`、`inject/`；benchmark 插件在 `benchmarks/`；
功能积木在 `blocks/`；组合积木在 `composites/`；阶段流程在 `stages/`。

## 二、数据模型（JSON 落盘，对象间以 ID 关联）

五个核心对象，全部 JSON，外键记录关系。每个对象带 `schema_version`，
ID 按 1.1 第 6 条的确定规则生成：

```
Trajectory（轨迹）
  trajectory_id, task_id, benchmark, model_role(强/弱), model_name,
  attempt_index(第几次采样), skills_injected(null=裸跑；否则为 skill_id+version 列表或库的 digest),
  steps[ {action, observation} ... ],
  status(success / failure / infra_error), success(status 为 success 时为 true), meta

Subtask（段）
  subtask_id, task_id, trajectory_id(→轨迹), source(强模型段/弱模型段),
  trajectory_success(来源轨迹本身是否成功), segment_index(段在轨迹中的序号),
  step_range[start, end)(段在轨迹中的起止步号，左闭右开),
  label(短名), description(这段在做什么),
  success(该段目标是否达成), context_summary(上文各段目标摘要)
  （段内逐步原文不另存，按 step_range 从 Trajectory 取，只有一份来源）

Skill
  skill_id, content(自然语言正文), subtask_id(→段),
  version(改写次数), status(pending/accepted/discarded/merged),
  discard_reason(weak_already_knows / retry_exhausted / leak_audit / model_output_error
                 / regression_failed / regression_error / interrupted_merge
                 / live_no_improvement / live_revision_deleted / live_undetermined
                 / live_injection_overflow),
  group(能力分组，阶段 5 写入),
  rewrite_history[ 旧版正文、错因归类与改写原因 ], merged_from[ skill_id ...](仅库整理产物)
  （task_id / trajectory_id 经 subtask_id 查得，不重复存）

Exam（考题）
  exam_id, question_text, reference_answer(判卷参考), subtask_id(→段)

Attempt（作答记录）
  attempt_id, exam_id, skill_id + skill_version(都为 null = 裸答),
  purpose(bare / acceptance / regression), weak_answer,
  judge_verdict(null = 已作答、尚未判卷), judge_reason, retry_round
```

**关系**：Skill 和 Exam 都只挂在 Subtask 上（`subtask_id`），两者互不引用——
考题出题时不看 skill。每个参与生成的 Subtask 对应一个 skill 和一道考题（1:1:1）；
改写重考不新建对象，skill 版本号 +1；每次作答（含裸答）另记一条 Attempt，
用 `exam_id` + `skill_id/skill_version` 指向当时的考题和 skill 版本，重考全程可追溯。

**discard_reason 的词汇与证据基础**（`docs/decisions/acceptance_live_run_20261014.md`
裁决 A13）：字段本身是自由字符串（schema 不设闭合枚举），上面括号里的清单是系统实际
会写入的词。live_run 验收新增四个词：`live_no_improvement`（两轮未更好 / no-op 守卫，
双语义靠 verdicts 行的 round 字段区分）、`live_revision_deleted`（G10 判 delete）、
`live_undetermined`（live 侧全单元皆为已接受空洞，无判定分母）、
`live_injection_overflow`（注入块超过 `max_injection_tokens`，零执行终态）。两个跨模式
复用词的**证据基础不同**：`weak_already_knows` 在 exam = 裸答即对，在 live = 阶段 1
基线全成功（零执行推断）；`model_output_error` 在 exam = 作答/判卷/改写预算耗尽，
在 live = G10 归因预算耗尽（整组）或 G11 修订预算耗尽（单 skill）。**零 schema 升版**：
五个数据对象不加字段、不改字段含义，live 模式全部新词走自由字符串口子，
`CONFIG_SCHEMA_VERSION` 不动（在飞 run 的续跑兼容性优先；升版清理登记为在飞 run
完结后的一次性技术债，见 `docs/runbooks/live_run_acceptance_runbook.md` 的遗留事项）。

落盘布局（每个 run 目录）：

```
<run>/
  manifest.json                  冻结的输入与协议
  ASSEMBLED_FROM.md              装配溯源（仅装配产出的 run 有，见 1.4 装配工具）：源 run、
                                 划分 provenance、拷贝口径与逐类计数、校验和、未拷贝清单
  run.lock                       run 目录排他锁（B7），记持有者 pid
  pipeline_log.jsonl             逐阶段完成的摘要账本（续跑时追加新行，读取时取每阶段最后一行）
  failed_units.jsonl             单元失败账本（单元 ID、阶段、错误类别、消息），只追加；
                                 当前还缺哪些单元以各阶段摘要的 infra_failed / infra_missing 为准
  stage0_summary.json            阶段 0 摘要（阶段 1、2 的摘要同样在根目录，3–6 的在各自目录下）
  stage1_summary.json
  stage2_summary.json
  stage1_baseline/trajectories/  强/弱模型裸跑轨迹（基础设施重试耗尽的单元不落盘）
  stage1_baseline/accepted_infra_shortfall.json
                                 用 --allow-infra-shortfall 接受的空洞单元，本 run 的持久决定
  stage2_segments/subtasks/      切段结果
  stage2_segments/segmentation_audit.jsonl
                                 重切耗尽、被跳过的轨迹，本 run 内不再重切
  stage3_generation/skills/      skill（阶段 3 的候选，以及阶段 5 的合并 skill bank/merge/NNNN）
  stage3_generation/exams/       考题
  stage3_generation/generation_failures.jsonl
                                 写 skill / 出题的模型输出失败的段，本 run 内不再重试
  stage3_generation/stage3_summary.json
  stage4_acceptance/attempts/    每次作答（裸答、带 skill、阶段 5 回归考试）与判卷记录
  stage4_acceptance/leak_verdicts.jsonl
                                 泄题判官（auditor 角色）的每次判决，每个
                                 （skill, 版本）恰一行、首判为准，续跑不重问
  stage4_acceptance/rewrite_failures.jsonl
                                 改写每失败一次一行，耗尽的那次标 exhausted；续跑据此推导轮次
  stage4_acceptance/stage4_summary.json
  stage4_acceptance/live_run/      live_run 模式专有子树（exam run 没有它；三账本 + 两个
                                 修复失败账本 + 按轮子目录都在这里）
    verdicts.jsonl                逐（task, round）判定行，append-once、首判为准：
                                 {schema, task_id, round, rule, baseline{valid,success},
                                  live{valid,success}, better, injection_tokens,
                                  judged_with_shortfall, aux}——aux 是裁决 11 的并列
                                 辅助计数槽（未声明辅助指标为 null，不参与判据）；
                                 judged_with_shortfall 的空洞两种来源（stage1 继承的
                                 与本轮自接受的）在行上不区分；零执行终态
                                 （weak_already_knows / live_injection_overflow /
                                 live_undetermined）同样落行：计数与 better 记 null、
                                 终态名进 verdict 字段（键的存在即行种类的自描述）
    attributions.jsonl            G10 归因行，append-once、首判为准：
                                 {schema, task_id, round, diagnosis, skill_actions[
                                  {skill_id, version, action, problem}],
                                  injection_block_sha256}——skill 版本由程序在追加时
                                 从 store 读入补记，模型输出不可信
    attribution_failures.jsonl   G10 每次修复重试失败一行，耗尽的那次标 exhausted；
                                 预算跨崩溃持久（K4 rewrite_failures 先例）
    revision_failures.jsonl      G11 逐 skill 的同款失败账本（该 skill 的预算独立）
    round00/                      轮 0 的独立 store（内部同 stage1_baseline/trajectories/
                                 布局——注意该目录名只是 store 布局复用，**内容是本轮
                                 带 skill 注入的 live 轨迹**，裸基线在主 store；
                                 K1 的 failed_units.jsonl 也在这里），加两个轮内文件：
                                   injection_audit.jsonl          每 task 一行（成员
                                                                 [skill_id, version]
                                                                 列表 + D9 块 sha256 +
                                                                 D10 估算 token 数），
                                                                 派发前首写冻结，
                                                                 重派发前重推导逐字节比对
                                   accepted_infra_shortfall.json  本轮已接受的空洞
                                                                 （--allow-infra-shortfall，
                                                                  按轮记录：轮 1 的远程
                                                                  沙箱是新实例，轮 0 的
                                                                  空洞不自动继承）
    round01/                      轮 1 同构（仅幸存且注入块有变化的组）
  stage5_curation/curation_log.json
                                 库整理日志：输入集快照、每轮 G7 / G9 输出、每个合并的决定（续跑依据）
  stage5_curation/stage5_summary.json
  skill_bank.jsonl               整理后的最终库（test 时注入的就是它）
  stage6_test/bank/              带库臂的独立 store：内部重复 stage1_baseline/trajectories/ 等布局，
                                 本臂执行的 failed_units.jsonl 也在这里
  stage6_test/bare/              对照臂（split 模式的弱模型裸跑）的独立 store，布局同上
  stage6_test/accepted_infra_shortfall.json
                                 阶段 6 接受的空洞，按臂（with_bank / control）记录
  stage6_test/retrieval_audit.jsonl
                                 skill_delivery = retrieval 时的逐任务检索审计（选中的
                                 skill、路由类别、逐条分数、阈值判定），dispatch 前
                                 覆盖式写出一次
  stage6_test/stage6_summary.json
                                 两臂成绩与 delta
  work/                          执行单元的工作目录：work/stage1/、work/stage4_live/
                                 round00/ 与 round01/（live_run 模式按轮分开——重试游标按
                                 workdir × trajectory 存，两轮的 trajectory id 相同，共用
                                 一棵树会让轮 1 继承轮 0 的游标）、work/stage6_bank/、
                                 work/stage6_bare/ 下每个单元一个子目录，内含
                                   k1_retry_cursor.json        K1 重试游标（下一个没用过的重试序号）
                                   tb_remote/<run_id>.json     TB 提交记录（启动条件摘要 + runner pid），
                                                               远程复用的前提
                                   以及 TB 取回的 result.json、轨迹等原始文件
  llm_requests/                  B1 记录的每次模型请求与响应，NNNN_<prompt>.json，不覆盖、不含 key
  llm_requests/usage.jsonl       用量账本，跨进程累加
  *.lock                         JSONL 账本旁的加锁文件（如 usage.jsonl.lock），
                                 让多个线程 / 进程的追加排队进行
  *.interrupted-tail             账本末尾被打断的半行：下次追加时从账本截掉，原样留在这里作证据
```

失败类账本（`failed_units.jsonl` 及 K4 / K5 的同类账本）都是**第一次记失败时才创建**：
一个干净跑完的 run 目录里没有这些文件，不能拿"文件在不在"当判据。

阶段 6 两臂各用独立 store，是因为轨迹 ID `traj/<task>/<role>/<attempt>` 不带
实验臂信息：共用一个 store 时，第二臂会按"ID 已存在 = 已完成"直接继承第一臂的轨迹，
算出假的零提升（`docs/decisions/stage6_arm_stores_20261011.md`）。

## 三、系统流程（2026-10-10 定稿）

每个阶段标出用到的积木，规则写在阶段里，积木本身不带阶段规则。

### 阶段 0：准备与冻结（D1、D2、B4）

- 输入：任务集（配置的 `tasks` 字段）、强模型 `M_s`、弱模型 `M_w`、弱模型画像。
- 数据划分是**实验模式**，不是固定选择（以 TB 为例两种都做）：
  - 模式一 `split`：任务集划分 train / test，蒸馏只用 train，test 独立评测；
  - 模式二 `full`：全部题目一起蒸馏，test 时弱模型带 skill 重跑，
    直接看"原来做不出来的题现在能不能做出来"；
  - 划分是**三列表**：train / test / reserved。`reserved` 不进任何阶段
    （既不蒸馏也不评测），留作开发冒烟与后续校准切片；显式划分只给
    train / test 两键时 `reserved` 恒为空列表，stage 0 摘要与 manifest 的
    `split` 恒含三键。
- **任务集 `tasks`**（`docs/decisions/stage0_task_set_freeze_20261011.md`）有三种写法：
  不写（插件的全集；插件可以拒绝，TB 就拒绝，必须显式给出）、任务 ID 列表、
  任务文件路径（格式由插件解释）。列表不得为空、不得有重复 ID。
  - 配置里的相对路径（`tasks`、`weak_profile_file`）一律按**真实**配置文件所在目录
    解析：经符号链接加载与直接加载等价。任务文件路径存成绝对路径，所以从哪个
    目录启动或续跑，冻结的都是同一个值；
  - 插件加载任务后先查重复 ID（例如任务文件把同一道题写了两次），有重复即配置
    错误，在任何划分之前就停下；
  - `split` 模式给了显式划分列表（train / test，或再加 reserved）时：同时给了
    `tasks` 的，两者必须完全一致（报出两边各自多出的 ID；并集含 reserved）；
    只给列表的，加载列表的并集。按比例 + 种子划分时，从加载到的任务集派生。
- **具名规则**：配置里没写的规则由 `DEFAULT_RULES`（`core/config.py`）补齐——
  `task_select = any_weak_failed`、`segment_select = strong_success_weak_failed`、
  `trajectory_select = all`，所以冻结的是实际生效的名字。阶段 0 在加载任务、
  写任何文件之前就逐个核对规则名是否已登记，名字拼错在这里就停下，不会等到
  昂贵的阶段 1 跑完才报错；后面各阶段直接读 `config.rules`，不再各自带默认值。
- **验收模式键 `acceptance`**（`docs/decisions/acceptance_live_run_20261014.md`）选择
  阶段 4 的验收协议：整键不写、`{}`、`{mode: exam}` 都 ≡ exam（文本考试，与原行为
  逐字节等价）；`{mode: live_run, n_attempts: <int≥2>, improvement: <规则名>,
  max_injection_tokens: <上限|null>}` 切到 live_run（键的语义见阶段 4）。live_run 下
  阶段 0 会：(a) 校验 `improvement` 规则名已登记进 `ACCEPTANCE_IMPROVE`（拼错在这里
  停下）；(b) 条件 import 两个 live prompt 并把它们的 digest 并进 manifest——exam run
  的冻结 digest 集逐字节不变。manifest 的配置快照里，`acceptance` 为 None 时**该键
  缺席**（`to_dict` 的字段白名单式删键，且仅此一个字段走此通道——通用的"None 就删"
  会把 full 模式冻结为 null 的 `split`/`tasks` 也删掉，旧 run 的续跑会被改动的键集
  拒绝）。这一条与在飞 run 的兼容规则：新代码 + 旧（exam）配置 ⇒ manifest 逐字节不变，
  在飞 run 照常续跑。
- 冻结：benchmark、模式、显式的 train / test / reserved 列表、完整配置（任务集、划分、
  模型角色表、`model_options`（按模型名给出的额外 chat 调用参数，如
  `reasoning_effort`，随完整配置冻结——它改变模型产出，不是运行设置，见 B1）、
  弱模型画像、采样次数、K、`segment_retry_limit`、`retry_limit`、
  `bank_token_budget`、`curation_rounds`、生效的规则名）、prompt digest、
  任务内容摘要 `tasks_digest`、辅助指标名 `aux_metric`，写进 manifest；之后每次
  启动逐字段比对，再改即视为新实验。
  - `aux_metric`：benchmark 插件声明的**辅助指标**的名字（插件没声明就是 null；
    SearchQA 声明 `sub_em`）。它是实验身份的一部分，在任何结果存在之前就冻结
    （预登记，见 D3）。注意辅助指标的**打分器是代码不是配置**：改打分器不改变
    任何 prompt digest，旧 run 目录不会因此拒绝续跑——跨打分器版本比较 run 时
    以代码历史为准，改打分器要在 log.md 记录。
  - `tasks_digest`：按 task_id 排序，对每道题的 ID、指令、meta 做规范 JSON 后取
    sha256。任务文件里的指令改了而 ID 没变，续跑同样会被拒绝。它只覆盖插件放进
    Task 的内容：TB 的指令在远程任务目录里，远程那边的改动不在摘要内；ALFWorld 的
    meta 存相对 `ALFWORLD_DATA` 的路径，数据目录搬家（内容不变）不会让续跑被拒；
  - prompt digest 的集合是确定的：全部方法 prompt，加上当前 benchmark 插件的执行
    prompt，与进程里碰巧导入过哪些插件无关。
- 运行设置（并发数、重试次数等）不在配置里，也不冻结，见本节末"运行规则"。

### 阶段 1：双模型基线执行（K1 × 2、D3、D4）

- 强模型在每道蒸馏题上执行 `n_attempts_strong` 次，弱模型执行 `n_attempts_weak`
  次，均为裸跑；两边的 Trajectory 全部落盘（成功和失败都保留）。
- **失败题集合 `F` 由配置的 `task_select` 规则决定**。v2 默认
  `any_weak_failed`：弱模型**任一有效尝试失败**（success < valid）该题即入
  `F`；全部有效尝试都成功的题不入（没有可修的），一次有效尝试都没有的题
  也不入——基础设施异常不算模型失败，只进 infra shortfall 清单。部分采样
  耗尽基础设施重试（0 < 有效数 < `n_attempts_weak`）但确有有效失败的题
  **既进 `F`、也仍在 shortfall 清单**，两张表不再构造性不相交。
- **基础设施空洞按应有单元计分**：K1 不保存耗尽基础设施重试的单元，所以空洞在
  磁盘上表现为"轨迹缺失"。D3 按每个臂的应有单元集合（每道题 × 冻结的采样次数）
  计分，缺失的单元算一次基础设施异常；所有采样都缺的题仍留在分母里，不会消失。
- **空洞不得静默通过**：两臂跑完后先写摘要（`infra_missing`、`complete`、
  `infra_shortfall_accepted`），只要有未接受的空洞就以基础设施错误停下（退出码 3），
  同一条命令重跑只补这些空洞。
- **接受空洞是本 run 的持久决定**（`docs/decisions/stage6_metrics_denominator_20261011.md`
  的"更正"一节）：TB 里永久坏掉的题可以用 `--allow-infra-shortfall` 接受。被接受的
  单元 ID **先**写进 `stage1_baseline/accepted_infra_shortfall.json`，再推导 `F`；之后每次
  调用（带不带这个开关都一样）都把它们交给 K1 跳过、按空洞计分，即使后来出现了
  轨迹也不算。原因：`F` 是从有洞的样本推出来的，库是从 `F` 建出来的；如果续跑时
  补上了空洞，`F` 和库都会变，而阶段 6 已存的轨迹对应旧库，这个 run 目录就废了。
  开关只决定"新出现的空洞接不接受"。
- **辅助指标与主指标并列报告**（预登记，见 D3 与阶段 0 的 `aux_metric`）：
  主指标恒为 EM / 状态口径；benchmark 插件声明了辅助指标时（SearchQA 的
  `sub_em`，子串匹配——EM 里 "lumbar" 对金标 "lumbar vertebrae" 记 0，sub_EM 记 1），
  两臂的 summary 各加一节 `aux`（指标名 + 同两种口径的辅助 avg@N），
  分母与主指标完全一致（缺失 / 基础设施单元不可能算辅助成功）。辅助指标
  **永远只并列**，不因为结果好看就替换主指标、也不看结果后再挑。
  插件未声明时 `aux` 为 null，不打分。SearchQA 的打分器对数据严格：成功或
  已提交答案的轨迹必须有评测 meta，缺失即数据损坏（报错停下）；从未提交答案的
  失败轨迹 sub_EM 记 0（没有答案可匹配，是真实的 0，不是缺数据）。
- 产出：强模型轨迹库、弱模型轨迹库、失败题清单 `F`。

### 阶段 2：轨迹切段（K2，方法参照 AMD）

对 `F` 中每道题，强模型和弱模型的轨迹各自切段。
切法参照 AMD（*Agent Memory Distillation*, §3.2 Subtask Memory）：

- 一段 Subtask = 轨迹中一个**语义连贯的行为单元**——完成一个目标的完整过程
  （如"向某服务完成认证"）；AMD 只切成功轨迹，我们**成功和失败轨迹都切**。
- 切段由强模型执行；可选用基于规则的启发式给候选断点提示（AMD 同款）。
- **段数上限 K**（可配置）写进切段 prompt（"最多 K 段，每段应是一个完整目标"）。
- **D7 校验**：段边界不重不漏地覆盖全轨迹（相邻段首尾相接、总步数对得上）；
  段数 ≤ K 且每段非空；每段 `label` / `description` / `success` 字段齐备
  （只检查字段存在，不判断 `success` 的值）。不通过就重切，见 K2。
- **每个 Subtask 带两个成败字段**：
  - `success`：该段目标是否达成，阶段 3 据此区别对待；
  - `trajectory_success`：来源轨迹本身是否成功。v2 下 `F` 含弱模型"既有成功
    也有失败"的混合题，弱成功轨迹可在失败轨迹不足配额时补位入切（轨迹挑选
    规则 `strong2_weak2`），所以弱段的 `trajectory_success` 不再恒为
    false；强模型轨迹两种都有，便于事后单独统计"强模型自己也没做出来的题"
    产出的 skill 的入库率与效果。
- **切段失败**：G1 输出不合格算一次失败的切段尝试，重切耗尽后跳过这条轨迹、写进
  `stage2_segments/segmentation_audit.jsonl`，本 run 内不再重切（换一次采样重试就是
  新实验，用新 run 目录）。切段调用遇到基础设施错误不算切段失败：这条轨迹记入失败
  账本、什么都不存，其余轨迹照常切，阶段末以基础设施错误停下，续跑只重做这些轨迹。
- **轨迹挑选（已由 `strong2_weak2` 落地）**：一道题有多次采样时切哪几条轨迹
  （全部，还是每个模型取若干条）由具名的"轨迹挑选规则"（与 D4、D6 同一个
  规则注册表）决定。当前配置用 `strong2_weak2`：每臂至多取两条，强臂成功
  优先、弱臂失败优先——恰是 v2 的材料序，不足配额时用另一类补位；换策略
  只新增规则、不改流程。

### 阶段 3：skill 与考题生成（D6、G2、G3）

- D6 筛段：由配置的 `segment_select` 规则决定（v2 默认
  `strong_success_weak_failed`）——强模型段只取 `success = true` 的段
  （做法材料），弱模型段只取 `success = false` 的段（教训材料）。v1 规则
  `strong_all_weak_failed` 与 v2 的 G2 prompt 不兼容（失败的强段会撞
  write_skill 的前提守卫）。
- G2 写 skill：强模型段用"提炼"写法（procedure，执行步骤），弱模型段用
  "诊断"写法（failure reference，失败参考）。**四节结构契约**：模型按 JSON
  契约输出——提炼是 `when_to_use` / `steps` / `verify` / `not_applicable_when`，
  诊断是 `trigger` / `why_it_fails` / `do_instead` / `not_applicable_when`；
  程序校验字段（缺字段、空清单、条目自带编号等不合格直接终态），再确定性
  渲染成标记文本（[When to use] / [Steps] / [Verify] / [Do not use when]，
  或 [Trigger] / [Why it fails] / [Do instead] / [Do not use when]）存进
  `Skill.content`，步骤编号由渲染器统一添加。
- **write_skill 前提守卫**：提炼要求该段 `success = true`、诊断要求该段
  `success = false`，不满足直接报错停批——规则与写法配错时响亮失败，
  不静默失真。
- **写 skill 面向弱模型**：prompt 中带上弱模型画像，要求用弱模型能执行的粒度
  和措辞来写（短句、明确顺序、具体触发条件），不写弱模型无法操作的抽象要求
  （参照 AgentBrew §2.4.2 student-aware prompting）。
- G3 出考题：**文本问答题**，给定一段情境（如某步的观察），问该做什么、为什么；
  考的是该 Subtask 对应的能力，不是原题本身。**出题时只看 Subtask、不看 skill**，
  避免 skill 里直接写着考题答案。
- 一个段是一个单元：G2、G3 都成功后才把 skill 和考题一起落盘。模型输出失败（积木
  自己的修复预算耗尽）在本 run 内是终态：先写一行
  `stage3_generation/generation_failures.jsonl` 再继续，续跑不再重试这个段；
  基础设施错误则记入失败账本、继续其余段，阶段末停下待续跑，续跑会重做这些段。

### 阶段 4：验收（exam → K4；live_run → K6）

阶段 4 有两种验收协议，由配置的 `acceptance` 键决定（缺省 exam，见阶段 0）。分发在
`stage4_acceptance.run` 顶部以懒 import 完成：exam 进程不加载任何 live 模块
（`blocks.attribution` / `prompts.attribution` / `stage4_live_run` 都不进 import 图），
live 进程才加载它们。两种模式写同名摘要文件 `stage4_summary.json`，形状差异见本节末的
对照表。

**exam 模式（缺省，K4）**——对每个候选 skill 及其 Subtask 的考题：

1. **裸答**（K3，skill 列表为空）：**裸答就做对 → 弱模型本来就会，这个 skill
   没用，直接丢弃**（`discard_reason = weak_already_knows`）。
2. **带 skill 作答**（K3，只注入这一个 skill；多个 skill 放在一起的问题由
   阶段 5 处理）：**只有"裸答错、带 skill 答对"的 skill 才通过验收。**
3. **做错 → G6 改写 → D8 审计 → 再考**，上限 `retry_limit`；耗尽仍不过 →
   丢弃（`discard_reason = retry_exhausted`），作答记录留档。

改写重考的目的是**让强模型逐步摸清弱模型的理解能力**：弱模型答错，多半不是
skill 的道理错了，而是表述它读不懂、照着做不了（缺了中间步骤、触发条件太抽象、
用词超出它的理解）。每轮改写都在把 skill 调成弱模型能用的写法，因此重考沿用
同一道考题（参照 AgentBrew 的 Ralph Loop）。为了让改写改的是"写法"而不是
"往 skill 里塞这道题的答案"，有三条约束：

- **G6 看不到参考答案**；G5 的错误描述只说哪里不对，不复述正确答案，也不说明或暗示该怎么改——改写只能凭"该说什么"自己想出来，否则"改写只改写法、不改道理"就名存实亡。
- **G6 先归类弱模型为什么没做对**（没读懂 / 漏了步骤 / 误判触发条件 / 其他），
  再针对原因改写法；不得写入考题情境里的具体数值、文件名、实体名。归类结果
  存进 `rewrite_history`，它本身就是关于弱模型能力局限的数据。
- **D8 泄题审计**：LLM 判官（auditor 角色，默认强模型，可在角色表里换模型），
  每版 skill 问一次。判泄露（skill 包含、平凡改写或直接揭示参考答案及考题答案
  特有实体）则该版作废、本轮算用掉一次，判官理由进改写的错误描述；通用方法
  建议、协议词、动作名、环境反馈串不算泄露。判决按 (skill, version) 落盘进
  `leak_verdicts.jsonl`、**首判为准**：续跑查表复用，绝不重问（判官不可复现，
  续跑必须等于一次跑完）。理由永不复述参考答案——它正是 G6 的改写上下文，而
  G6 不得看到答案。最后一轮仍判泄露则丢弃（`discard_reason = leak_audit`）。
  已知局限：auditor 默认与写 skill 的是同一个强模型（判官自审），换配置里
  `auditor` 角色的模型即可打破。

**G6 改写保持节结构**：skill 是固定四节的标记文本（见阶段 3），改写产物必须
保持与原文相同的四个节——只改写各节内容、不并成散文，节头逐字复现、独占
一行；程序校验改写产物的节标记集合与原文一致，不一致按模型输出失败处置
（K4 既有路径）。

验收的范围与单元失败：

- **只处理阶段 3 产出的候选 skill**（`skill_id` 由 `subtask_id` 按规则生成的那些）。
  阶段 5 的合并 skill（`bank/merge/NNNN`）不进验收：K5 在合并途中崩溃时，它以
  pending 状态留在 store 里，修复前续跑的阶段 4 会把它当成候选、只凭锚点那一道考题
  就验收通过，K5 再合并一次，库里就多出一个没经过回归考试的合并 skill；
- 作答、判卷或 D8 判官的模型输出失败耗尽修复预算 → 该 skill 丢弃
  （`discard_reason = model_output_error`），本 run 内终态；
- 基础设施错误 → skill 保持 pending、记入失败账本，继续其他 skill；全部处理完、
  摘要写下后以基础设施错误停下，流水线不会带着缺了 pending skill 的库进入阶段 5；
- 摘要按磁盘上全部候选的状态累计，续跑或重跑不会把它覆盖成部分计数。

**live_run 模式（K6 + `stages/stage4_live_run.py`）**——验收单位从"单 skill 的文本
考试"改为"task 级 skill 组的真实执行比对"（`docs/decisions/acceptance_live_run_20261014.md`）：

- **验收单位（组的定义）**：一个验收单元 = 一个 train task 的**组** = 该 task 的全部
  **pending** stage-3 候选 skill。候选过滤沿用 exam 的语义（`skill_id ==
  make_skill_id(subtask_id)`，因此 `bank/merge/NNNN` 天然排除——它们是 K5 的、已由回归
  考试证明，永不回到验收），live 侧在新文件里就地重写该过滤、按 task 分组，不共享 exam
  文件的 helper。分组键由 ID 链确定性推导：`skill_id → parse_subtask_id →
  trajectory_id → parse_trajectory_id → task_id`；task 对象经 `raw_task_id` 反解析后由
  D1 从冻结配置重建。组的宇宙从 store 现存 pending 候选推导（不假设候选覆盖全部
  train task），无候选的 task 不构成单元。
- **配置键**：`acceptance: {mode: live_run, n_attempts: <int≥2>,
  improvement: <ACCEPTANCE_IMPROVE 规则名，必填无默认>, max_injection_tokens:
  <上限|null>}`（`n_attempts` 建议 = `n_attempts_weak`；1v1 二值比较的 H0 假通过率
  ≈ 真实成功率本身，故 ≥2 是硬约束）。`max_injection_tokens` 为 null 时只测量不执法。
- **判据预注册**：判定规则是 `blocks/rules.py` 第四个具名注册表 `ACCEPTANCE_IMPROVE`
  的成员，签名 `(baseline_counts, live_counts, n_attempts) -> bool`，名字进配置进
  manifest，看结果前定死、换规则 = 新实验。当前两条：
  - `strict_rate_gain`（裁决选名）：`live.success × baseline.valid >
    baseline.success × live.valid`（整数交叉乘法，无浮点），且 `live.valid > 0`。
    等深且 skip 对齐时与计数口径等价；live 侧接受空洞后样本变浅时，率口径是唯一
    无偏推广（live 3/5 vs 基线 2/3：计数判"更好"、率判"未更好"——率是对的）。
    **并列判负**（进归因修订），变差同样进归因（skill 可能有害，归因者应看到）；
  - `first_success_gain`（消融备选，pass@N 语义）：基线全败 → live 至少一次成功。
  - **噪声数字如实披露**：H0（skill 无效果、真实成功率 p）下单 task 假通过率 =
    P(live 严格更好)。n=3 时 p=1/6 → ≈27%、p=0.5 → ≈34%——`strict_rate_gain` 是
    弱证据的入门过滤器，不是统计检验；数字按 n 重算（n=1 时约 50%）。可辩护性：
    验收是库的入门门而非最终主张，最终对照在阶段 6 test 集；逐组 baseline/live
    计数全落盘，事后可用符号检验复核。
  - **天花板守卫（strict 族限定）**：基线全成功（success == valid）且规则在率 1.0
    基线下数学上不可能返回 True 时 → 全组 `weak_already_knows` 零执行终态。守卫
    限定在 strict 族规则集内硬编码（当前两条均满足）；注册新规则必须有意识地复核
    该集合，否则守卫会短路掉 pass@N 型规则本可执行的组。
  - **无效基线**：基线 valid == 0 → ConfigError。该守卫的不可达性依赖两个已注册
    task_select 规则都要求 valid > 0（这种 task 不入 F、无候选、无组）；换
    task_select 规则时守卫从不可达变可达，fail-loud 而非静默跳过。
- **两轮循环（轮数钉死为 2，用户裁决 8；无 `max_revision_rounds` 配置面）**：
  轮栅栏（每轮一次 K1 大批，attempt-major 派发保持"同题采样不相邻"）——
  1. **入口**：finalization sweep（幂等补完崩溃截断的提交）→ 逐组加载基线
     （未接受缺失 → InfrastructureError 指路先续跑阶段 1；基线全成功 → 零执行终态）；
  2. **轮 0**：派发前首写冻结 injection_audit 行（成员 + 注入块 sha256 + 估算
     token；超 `max_injection_tokens` → `live_injection_overflow` 零执行终态，不进
     K1）；K1 一次大批（store = `live_run/round00/`，弱模型，整组注入，`n_attempts`
     次真实执行）→ 逐组判定 → better 则 verdicts 落盘、全员 accepted；
  3. not better → verdicts 落盘 → **G10 归因**（author 角色，组级一次）→ 应用：
     delete 立即 `live_revision_deleted`；revise 逐 skill **G11 修订**（rewriter
     角色，保节结构，version+1 立即落盘）；
  4. **no-op 守卫**：幸存组渲染块 sha == 轮 0 冻结行 → 全组 `live_no_improvement`，
     不跑轮 1（防"重采样直到运气好"——两次 3v3 严格提升的假阳性近乎翻倍）；
  5. **轮 1**（仅幸存且注入块有变化的组）：同构执行（store = `round01/`）→
     better 则幸存成员 accepted，否则幸存成员 `live_no_improvement`。
  归因/修订经 `run_units` 并行（单元 = 组/skill，各写各的账本行，append_jsonl 已
  线程+flock 串行化）。
- **归因与修订（G10 / G11）**：G10 的 `task_instruction` 取 `task_prompt(task)` 渲染
  产物——学生实际看到的执行 prompt（TB 下 Task.instruction 只是任务名，指令经
  live_views 首步上下文间接可见，这一限制如实记录在 prompt 设计说明里）；G10 输出
  契约不含版本号——版本由 K6 在账本追加时从 store 读入补记（模型输出不可信）。
  **视图输入预算（2026-10-14 三评审修复）**：`live_views`（组内全部轨迹视图拼接）
  与 G11 的 `failure_view` 受 K6 的硬字符预算约束（`_LIVE_VIEWS_BUDGET_CHARS`/
  `_FAILURE_VIEW_BUDGET_CHARS`，值预注册于 runbook）——超预算的单条视图保留头尾
  （尾部是 TB 失败证据所在）、中间以省略标记折叠。没有预算时上下文超限返回
  400/413（永久拒绝，不归修复预算也不归 infra），首个带长轨迹的失败组会把整个
  阶段硬停且续跑原地再撞（pilot 实测一组三视图 ≈562k 字符 ≈14 万估算 token）。
  **修订纪律**（硬约束，写进两条 prompt）：修订必须是可迁移的 skill——通用模式与
  命令族合法，逐字抄本题的具体数值/文件名/实体名禁止。决定性理由：retrieval 交付
  会把库成员检索注入到**其他** task 上，任务局部化的 skill 对别的 task 是主动有害的；
  在 G11 放水等于在 G2 已冻结的可迁移纪律上开后门、伤害外部化到阶段 6。该纪律与
  "任务知识是被测处理"之间的张力（修订当然要用任务信息诊断问题）如实存在：纪律
  只约束**写进 skill 正文的**内容，不约束诊断。
- **D8 不适用**：live 验收没有考题参考答案这个保护物，不跑 D8 也不加替代判官。
  防"skill 退化为任务答案复制粘贴"的三层既有防线：(1) 写作层——G2 v2 prompt 的
  硬性纪律；(2) 修订层——G11 prompt 继承同一条纪律（见上）；(3) 测量层——split
  模式的阶段 6 test 集与验收任务不相交，可迁移性由 test 集端到端回答。full 模式下
  live 验收与阶段 6 同 task：任务定制 skill 同时抬高验收与 test——这是 full 模式的
  **定义**（"直接看原来做不出来的题现在能不能做出来"），不是缺陷（见第四节已知风险）。
- **expected 全量口径**：live 侧与基线侧都把**全量**应有单元集（task × n_attempts）
  交给 D3——skip 的单元不执行、无轨迹，按 expected 口径自然计为 infra（不进 valid
  分母）；两侧同减同一批单元，率比较的分子分母自动对齐。禁止给 D3 传"已减 skip"的
  expected（那会让 skip 单元从计数中整体消失，verdict 行失去"哪些单元是空洞"的
  自描述性）。
- **失败处置**（照方案 §2.5）：

  | 情形 | 处置 |
  |---|---|
  | G10 预算耗尽（含其契约校验失败计入） | 全组 discarded(`model_output_error`)，先终态后账本行 |
  | G11 单 skill 预算耗尽（含 marker 保结构校验失败计入——结构校验 raise 必须落在修复预算作用域内，K5 教训） | 该 skill discarded(`model_output_error`)，组继续 |
  | InfrastructureError（归因/修订/执行） | 组保持 pending、记账本、其余继续、阶段末 raise（退出码 3）、续跑重试 |
  | live 单元 infra 耗尽 | 默认不接受（续跑补全）；`--allow-infra-shortfall` 时按轮写入 `round{NN}/accepted_infra_shortfall.json`，判定在剩余有效样本上按率进行，verdict 行带 `judged_with_shortfall`；**全单元皆空洞 → `live_undetermined` 终态（先于归因短路）** |
  | 每轮 K1 的 skip_unit_ids | 阶段 1 已接受弱臂空洞（过滤 role==weak、限本 task）∪ 该轮自己的已接受空洞 |
  | 基线全成功 | `weak_already_knows` 零执行（strict 族限定，见判据预注册） |
  | 基线 valid==0 / 未接受缺失 | ConfigError / InfrastructureError（不可达守卫） |
  | 归因全 delete / 幸存组空 | 不跑轮 1，组终态即各成员已落的 discarded |
  | no-op（块 sha 不变） | 全组 `live_no_improvement`，不跑轮 1 |
  | 注入超长 | audit 首写时估算；超限 → 该组 `live_injection_overflow`（不进 K1，零执行浪费；证据在 audit 行），不烧整 run 的停机 |

- **shortfall 按轮语义与政策**：live 让阶段 4 首次成为执行阶段（与阶段 1、6 同故障
  面），`--allow-infra-shortfall` 扩展到 stage4-live。接受记录**按轮**存（轮 1 的
  远程 run id 因注入块变化而不同、沙箱是新实例，轮 0 坏 ≠ 轮 1 必坏；"轮 0 坏则
  轮 1 同跳"若要作为政策须显式预注册，不得作为实现副作用）。**正式 run 默认不开**
  该开关（预注册正本见 `docs/runbooks/live_run_acceptance_runbook.md`）：重试耗尽
  当场同命令续跑，不靠开关兜底；仅当用户明示允许时，接受标准 = 单单元经一次续跑
  补跑后仍 infra 耗尽（至少两轮完整重试）、且总量 > 15% 的组停下诊断、双轮同
  policy、接受必须先落 durable 记录再判定。
- **状态推导与续跑**：见 1.4 节 K6 的落盘规则；驱动（`stages/stage4_live_run.py`）
  拥有轮序推进、重派发集判定（**非终态 ∧ 轮 store 不完整**的组——"非终态"限定不可
  省，否则零执行终态组的空 store 会在每次续跑时被重派进 K1 白烧执行）、injection_audit
  首写、shortfall 记录、摘要与阶段末 raise。

**双模式 summary 形状差异**（同名文件 `stage4_acceptance/stage4_summary.json`）：

| 字段 | exam | live_run |
|---|---|---|
| `accepted` / `discarded`（reason→count）/ `processed_this_call` | 有（按盘上候选累计） | 有（同语义；live 的"accepted"含阶段 5 已并入合并的成员） |
| `infra_failed` | 有（元素是 **skill id**） | 有（元素是 **task id**——live 的单元是组不是 skill） |
| `skipped_missing_exam` | 有 | **无** |
| `mode` / `improvement_rule` | 无 | 有（`live_run` / 具名规则名） |
| `group_details`（逐组双轮判定 + 注入 token + 逐成员终态与理由） | 无 | 有 |
| `infra_missing`（按轮列出仍缺的单元）/ `infra_accepted`（按轮）/ `infra_shortfall_accepted` | 无 | 有（pipeline 的账本旗标链路依赖后两者） |

### 阶段 5：skill 库整理（K5，参照 AgentBrew Curator）

通过验收的 skill 都只是**单独**被证明有用；test 时弱模型要读入 skill 库
（`whole_bank` 交付模式一次读入整个库，`retrieval` 模式逐任务注入选中子集，
见阶段 6），多个 skill 放在一起可能互相冲突、互相稀释，总长度也可能超出
prompt 容量。做法参照 AgentBrew §2.3.3 Note Curator 与 §2.5 Lifelong Memory：

- **只读取通过验收的 skill**，没过验收的版本不得进入库（UpSkill 仓库实现里
  先 curate 后验证、导致未验证笔记入库的顺序问题，见外层
  `docs/design/agentbrew_upskill_porting_plan_20261008.md`）。
- **不重新判断对错**，只做库的维护：
  1. **去重合并**（G7 → G8）：意思相近的 skill 合并成一条新 Skill，来源 skill
     状态改为 `merged`，原文留档。G7 只在同类 skill 之间给候选——procedure
     与 procedure、failure reference 与 failure reference，`similar_groups` 与
     `conflicts` 两组字段都受此约束；G8 逐节合并，产物保持与来源相同的四个
     标记节、节头逐字复现独占一行。混合写法的合并组在调 G8 前被程序预检
     拦下、合并产物缺节在落盘前被后检拦下，都记因跳过该次合并；
  2. **冲突处理**（G7 → G8）：同一情境下给出相反做法的 skill，合并为带条件
     区分的一条，或保留证据更强的一条（记录理由）；配对同样只在同类 skill
     之间进行；
  3. **合并后回归考试**（K3）：合并出的新 skill 重做它所代表的每个原始 skill
     （沿 `merged_from` 递归展开到阶段 3 的 skill）的考题，任一道过不了 → 撤销这次
     合并，来源 skill 保持 accepted；
  4. **按能力分组**（G9）：每条 skill 归入一个能力分组（AgentBrew 的 skill
     scope），D9 注入时按组排列，减少不相关 skill 互相干扰。只要库里有
     ≥ 2 条成员就跑 G9——**包括整轮没有任何合并候选、且已在 token 预算内的
     轮次**（这一轮的 G9 输出照常写进整理日志、续照原样重放）；只有 1 条
     成员（或空库）的轮次跳过分组，给单条 skill 分组没有意义；
  5. **长度预算**（D10）：整库注入后不超过 `bank_token_budget`；超出时
     继续合并，而不是直接删掉通过验收的 skill；合并最多 `curation_rounds` 轮，
     仍超出则阶段 5 停下报告，由人决定（调预算或改合并策略，均为新实验）。
- 产出：`skill_bank.jsonl`，即 test 时注入的最终库。
- 续跑：按 `curation_log.json` 逐个合并单元续上，提交顺序、编号规则与过期检查见
  1.4 节 K5。整理开始之后，前面阶段的续跑若又产出了新的 accepted skill，K5 拒绝
  把它不经整理直接收进库（"skill bank is stale"），只能换新 run 目录重跑。
- 单元失败：基础设施错误一律记入失败账本后停下（不允许因为网络问题跳过一次合并，
  否则库的内容取决于运气）；G8 的模型输出失败 → 该合并跳过；某个合并的回归考试
  模型输出失败 → 该合并撤销（`discard_reason = regression_error`）；G7 / G9 面向整个库，
  它们的模型输出失败停下整个阶段（退出码 1，先看一眼落盘的响应再重跑）。跳过和
  撤销的合并续跑时不重试。

### 阶段 6：test（K1、D3；skill_delivery = retrieval 时另有 D11）

- 强模型完全退出。
- **skill 交付由配置 `skill_delivery` 决定**，两种模式：
  - `whole_bank`（缺省）：整库注入——K1（弱模型, skill = 整个
    `skill_bank.jsonl`）；
  - `retrieval`：逐任务两步检索（D11）。开局对全库 `derive_categories`
    一次，每道任务以 `Task.instruction` 为检索文本路由到至多
    `max_categories` 个类别、再类内取 top-`top_k` 条 skill，由 D9 渲染
    选中子集注入；`min_score` 只门 top-1，最高分低于阈值的任务不注入
    任何 skill、等价裸跑。逐任务的选中清单、路由类别、逐条分数与阈值
    判定在 dispatch 前覆盖式写进 `stage6_test/retrieval_audit.jsonl`；
  - 两种模式注入的 skill 引用清单都记在 `Trajectory.skills_injected`
    （retrieval 记选中条目的 skill_id + version，低于阈值记空列表、与
    裸跑的 null 可区分），summary 的 `bank_reference` 仍记整库 digest；
  - 执行范围：`split` 模式跑 test 集；`full` 模式重跑任务集。
- 对照组（弱模型裸跑成绩）：
  - `full` 模式：直接使用阶段 1 的弱模型裸跑成绩；
  - `split` 模式：阶段 1 只跑了 train，因此在 test 集上单独跑一组 K1（弱模型, 裸跑）。
- 提升幅度 = 带库成绩 − 对照组成绩（D3 统计）。
- **两臂共用一个分母**（`docs/decisions/stage6_metrics_denominator_20261011.md`）：
  应有单元集合 = 每道 test 题 × 冻结的 `n_attempts_weak`，两臂用同一个集合；
  缺失的单元算基础设施异常，两臂覆盖的题集必须相同（不同即代码 bug）。修复前，
  某臂一道题的采样全部耗尽重试时，这道题会从该臂的分母里消失，两臂在不同的题集上
  相减，照样报 delta、退出码 0。
- **两种 avg@N 口径并列报告**（事先登记，不看结果后再挑）：
  - `avg_at_n`（条件口径）：先在每道题的有效执行里求成功率，再对至少有一次有效
    执行的题求平均；
  - `avg_at_n_conservative`（保守口径，与 TB21 的 `tb_stats.py` 分母一致）：所有题
    都进分母，没有有效执行的题记 0；
  - 另有 pass@N（至少一次成功的题占比，全部为基础设施异常的题在分母里、不可能通过）。
  - delta 对这三项各算一次（带库 − 对照）；任一臂没有有效执行时条件口径的 delta
    记 null，不当成 0。
- **辅助指标同口径并列**（与阶段 1 同一份预登记合同）：插件声明了辅助指标时
  （阶段 0 冻结的 `aux_metric`），两臂的 summary 各带 `aux` 一节，`delta` 对辅助
  avg@N 的两种口径各算一次，null 规则与主指标相同；未声明时相关字段全为 null。
  主指标（EM / 状态口径）始终是主指标，两套数字并列报告，不得互换或事后挑选。
- **空洞处置同阶段 1**：有未接受的空洞时先写摘要（`infra_missing` 按臂分开列出），
  再以基础设施错误停下；带 `--allow-infra-shortfall` 时按臂写进
  `stage6_test/accepted_infra_shortfall.json`，之后持久跳过、按空洞计分。`full` 模式的
  对照空洞就是阶段 1 的空洞，**只能在阶段 1 接受**；对照有未接受的缺失时，在执行
  带库臂**之前**就停下——重跑会先续跑阶段 1 补洞，补上后 `F` 和库都可能变，先跑的
  带库轨迹可能对应了错误的库。

### 运行规则：单元失败处置、运行设置与退出码

**单元失败按类别处置**（AGENTS.md 第六节第 6 条）。失败的单元都记进
`failed_units.jsonl`，不混进成功结果，也不悄悄丢掉：

| 阶段 | 基础设施错误（调用级重试和单元级重试都耗尽） | 模型输出错误（修复预算耗尽） |
|---|---|---|
| 1、6 执行 | 单元不落盘、记入失败账本，其余单元照跑；有未接受的空洞，就写完摘要后停下（退出码 3）；可用 `--allow-infra-shortfall` 持久接受 | 本地执行中途模型给不出可用回复（如返回空内容）→ 这一局记为 `failure`（`termination = model_output_error`，已走的步骤保留），不重试：重试等于对被测模型反复重采样直到它答好 |
| 2 切段 | 记入失败账本，继续其他轨迹；阶段末停下（退出码 3），续跑只重做这些轨迹 | 算一次切段尝试；重切耗尽 → 跳过并写审计日志，本 run 内终态 |
| 3 生成 | 同上（单元是段） | 写 `generation_failures.jsonl`、跳过这个段，本 run 内终态 |
| 4 验收（exam） | 同上（单元是 skill，保持 pending） | 丢弃（`discard_reason = model_output_error`），本 run 内终态 |
| 4 验收（live_run） | 组保持 pending、记入失败账本，其余组继续；摘要写下后以基础设施错误停下（退出码 3），续跑重试；`--allow-infra-shortfall` 时按轮接受空洞（语义与政策见阶段 4） | G10 预算耗尽 → 全组丢弃（`model_output_error`）；G11 预算耗尽 → 仅该 skill 丢弃，组继续；均本 run 内终态 |
| 5 整理 | 记入失败账本后立即停下（退出码 3），续跑按日志接着做 | G8 → 跳过该合并；回归考试 → 撤销该合并；G7 / G9 → 停下整个阶段（退出码 1，已完成的单元都已落盘，重跑同一条命令会重试这次调用） |

阶段 2、3 与阶段 4 的 exam 模式的 infrastructure 空洞不能被接受，只能续跑补上。
阶段 4 的 live_run 模式与阶段 1、6 同故障面（真实执行、远程沙箱会永久坏），在
`--allow-infra-shortfall` 下可按轮接受（语义、按轮记录与预注册政策见阶段 4）。
配置错误、服务方拒绝在任何阶段都立即停下（退出码 1）；代码 bug 立即停下并保留
调用栈。"本 run 内终态"的意思是：一个 run 是一次冻结的实验，同一个 run 目录里
不再重试；想换一次采样再试，就开新 run 目录。

**命令行**

```
expedistill run --config <配置文件> --run-dir <run 目录> [运行设置]
expedistill summary --run-dir <run 目录>
expedistill assemble-reuse --source-run <源 run 目录> --config <复用配置> --run-dir <新 run 目录> [--level bank|baseline]
```

- `run` 每完成一个阶段打印一行摘要；用同一条命令重跑即续跑，各阶段按确定 ID
  跳过已完成的单元；
- `summary` 只读进度账本和用量账本，不重算任何东西；账本里缺阶段时退出码 1；
- `assemble-reuse` 按复用配置的 train 列表从源 run 装配阶段 0–5 产物与过滤库
  （见 1.4 装配工具行），产出可直接 `run` 的新 run 目录。

**运行设置**（只影响"怎么跑"，不进配置、不进 manifest）：

| 参数 | 默认 | 作用 |
|---|---|---|
| `--workers` | 1 | 执行单元的并行数 |
| `--llm-infra-retries` | 3 | 单个模型请求遇到基础设施错误时的重发次数 |
| `--llm-infra-backoff` | 5.0 | 重发的指数退避基数（秒），单次等待最多 60 秒 |
| `--k1-stagger` | 0 | 每个单元启动前的随机抖动秒数上限，错开并行派发的 ssh 连接；TB 远程建议 20，本地执行 benchmark 用 0 |
| `--allow-infra-shortfall` | 关 | 让阶段 1、6 与阶段 4 的 live_run 模式（按轮）带着空洞继续；空洞仍计为基础设施异常，阶段摘要和账本行都标出，并向 stderr 打警告。live_run 的预注册政策：正式 run 默认不开（见阶段 4 与 runbook） |
| `--until-stage` | 无（跑到阶段 6） | 跑完阶段 0..N（含 N，N 取 0–6）就停，冒烟与试跑用；属运行设置、不进 manifest，停下后用同一个 run 目录、更大的 N 重跑同一条命令即继续，已完成的阶段不再发模型调用 |

- 运行设置续跑时可以改（例如端点不稳时调大重试次数）；在取锁、建客户端之前统一
  校验，非法值报配置错误；
- **例外**：`--allow-infra-shortfall` 这个开关本身不冻结，但它接受下来的空洞会写进
  run 目录，之后每次续跑都生效，没有撤回的办法——它改变了测的是什么（见阶段 1）。
  开关只决定"新出现的空洞接不接受"。

**退出码**（`docs/decisions/cli_exit_codes_and_default_client_20261011.md`）：

- `0`：正常完成；
- `1`：要人改了东西才能继续——manifest 不一致、run 目录被另一个进程锁着、配置
  错误、服务方拒绝（重复同一个请求没有用，检查模型名、凭据、被拒的内容）、
  模型输出错误逃出了阶段（如阶段 5 的 G7 / G9 修复预算耗尽：已完成的单元都已落盘，
  先查看落盘的响应，再用同一条命令重跑即重试这次调用；supervisor 不得对它自动循环
  续跑）；`summary` 发现 run 不完整也是 1；
- `3`：基础设施耗尽、可以直接续跑——某阶段跑完其余单元后，仍有单元耗尽了基础设施
  重试。已完成的单元都已落盘，CLI 打印带全部运行设置的续跑命令，可直接粘贴。
  supervisor 可以对 3 做有限次自动续跑，对 1 直接停下；
- `2`：命令行用法错误。

代码 bug 和未归类的异常不捕获，保留调用栈退出（这时进程退出码也是 1，所以
supervisor 不能只凭退出码 1 判断"是配置问题"，要看 stderr）。

## 四、已知风险（实现前要想清楚）

1. **skill 数量**：一道题的轨迹最多切出 K 段，几十道失败题仍可能产出成百上千
   个候选 skill；验收阶段的工作量（尤其改写重考是循环）会随之膨胀。
   库的大小由阶段 5 的长度预算约束，但验收成本需要在 pilot 上实测。
2. **考题质量**：考题出得太像原题 → 弱模型靠记忆也能过；出得太泛 → 验收放水。
   裸答对照能筛掉"太简单"的考题，但筛不掉"太泛"的；判卷需要看到弱模型的
   完整作答过程，而不只是结论。
3. **考题与任务的距离**：skill 过的是文本问答题，test 考的是真实执行。
   "答得对"不等于"做得到"，这个差距只能在 test 成绩里看出来。live_run 验收消掉了
   验收环节的这一差距（判的就是真实执行），但 K5 的合并回归考试仍是文本考——
   "live 通过 ≠ 做得到"在库整理环节仍然成立。
4. **live 验收的统计功效（3v3 局限）**：`n_attempts = 3` 的二值比较在 H0（skill 无
   效果）下单 task 假通过率 ≈ 27–34%（p=1/6 → ≈27%，p=0.5 → ≈34%；数字按 n 重算，
   n=1 时约 50%，故 n ≥ 2 是硬约束）。验收是库的入门过滤器而非统计证明；库的总体
   主张由阶段 6 test 集承载，逐组计数全落盘可事后复核。
5. **live 通过 ≠ 迁移**：live 验收的组来自 train task，可迁移性看 split 模式的 test 集。
   full 模式下 live 验收与阶段 6 同 task——任务定制 skill（具体命令带具体值）会同时
   抬高验收与 test。**这不是缺陷而是 full 模式的定义**（"直接看原来做不出来的题现在
   能不能做出来"）；主张可迁移性时请用 split 模式。
6. **逐 skill 修订可能互相重叠**：G11 按归因计划逐条独立修订，同一组的两条 skill
   可能被改成近义正文。重叠不在验收层去重，由阶段 5 的 G7/G8 相似度合并兜底。

## 五、代码来源（provenance）

底座代码（runtime / benchmarks / persistence / reliability / 执行循环 /
test 骨架）从姊妹仓库迁移而来：

- 源仓库：`../ExpeToSkill-merged`（SkillExpand 系统）
- 源分支与提交：`TB-eval @ 4f436af`
- 迁移时按"仅带依赖闭包"原则拷贝，详见首次迁移提交的说明
- 迁移来源与积木的对应：B1/B2/B4/B5 ← `runtime` + `persistence`；B3 ← `persistence/store`；
  B6 ← `reliability`；E1 与三个 runner ← `benchmarks` + `l1` 执行循环簇 + `evaluation/workers`；
  D2 ← `evaluation/splits`；其余积木为新写。迁移后的落位以 AGENTS.md 的目录结构为准
