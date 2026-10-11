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
| D2 | 划分 | （任务列表, 模式 split/full, 种子）→ train / test | 否 |
| D3 | 成绩统计 | （Trajectory 列表, 应有单元集合（可选）, 辅助打分器（可选，由 benchmark 插件声明））→ 每题有效次数 / 成功次数 / 基础设施异常次数（应有而缺失的单元计入基础设施异常），以及两种口径的 avg@N、pass@N；插件声明了辅助指标时，同一分母、同两种口径的辅助 avg@N（`aux` 一节，未声明为 null） | 否（辅助指标由插件声明，本积木只收打分器） |
| D4 | 挑题 | （每题成绩, 具名规则）→ 题目集合。阶段 1 的规则是"有效采样数达到 N 且全部失败" | 否 |
| D5 | 轨迹视图 | （Trajectory 或段内步骤, 视图配置）→ 给模型看的文本；超长观察按规则截断，截断处明确标注 | 是：截断规则按 benchmark 配置 |
| D6 | 段筛选 | （Subtask 列表, 规则）→ 要生成 skill 的 Subtask。当前规则：强模型段全要，弱模型段只要 `success = false` | 否 |
| D7 | 切段校验 | （Trajectory, Subtask 列表, K）→ 通过 / 错误清单 | 否 |
| D8 | 泄题审计 | （Skill, Exam, 模型（auditor 角色，默认强模型））→ 泄露 / 不泄露 + 理由。LLM 判官：skill 包含、平凡改写或直接揭示参考答案及考题答案特有实体（名字、数值、文件名）即泄露；通用方法建议、协议词、动作名、环境反馈串不算泄露。理由永不复述参考答案 | 否 |
| D9 | skill 注入 | （skill 列表, 分组信息（可选））→ 插入 prompt 的规则文本块 | 否 |
| D10 | 库长度统计 | skill 列表 → 注入后的 token 数 | 否 |

**模型类**（每块对应注册表里的一个或两个 prompt，模型由调用方传入）

| 编号 | 名称 | 输入 → 输出 |
|---|---|---|
| G1 | 切段 | （轨迹视图, K, 模型）→ 段列表（未校验）。模型只给每段的**起止步号**和 label / description / success，不复述步骤原文；段内步骤由程序按步号从 Trajectory 取 |
| G2 | 写 skill | （Subtask, 写法 = 提炼 / 诊断, 弱模型画像, 模型）→ Skill v1。提炼用于强模型段（成功段提炼做法、失败段提炼教训）；诊断用于弱模型段（哪一步开始错、正确做法是什么） |
| G3 | 出考题 | （Subtask, 模型）→ Exam。**不接收 skill** |
| G4 | 作答 | （模型, Exam, skill 列表（可选））→ 作答文本。带 skill 与裸答用**同一个 prompt 模板**，唯一差别是 D9 注入的规则块是否为空 |
| G5 | 判卷 | （Exam, 作答, 模型）→ 对 / 错 + 错误描述。错误描述只说哪里不对，不复述正确答案 |
| G6 | 改写 skill | （上一版 Skill, 考题, 错误作答, 错误描述, 弱模型画像, 模型）→ Skill 新版本 + 错因归类。不接收参考答案 |
| G7 | 找相似与冲突 | （skill 列表, 模型）→ 候选合并组、冲突对。先用字面相似度粗筛，再由模型确认 |
| G8 | 合并 skill | （一组 skill 或一对冲突 skill, 模型）→ 合并后的 Skill（`merged_from` 记录来源） |
| G9 | 分组 | （skill 列表, 模型）→ 每条 skill 的能力分组标签 |

"弱模型画像"是配置里的一段文字（弱模型是谁、已知弱点），是 G2 / G6 的输入，
换弱模型时只换这段配置。

### 1.4 组合积木

| 编号 | 名称 | 组成 | 说明 |
|---|---|---|---|
| K1 | 批量执行 | E1 × 题目 × 采样次数，经 B5 并行 | 每完成一次执行就落盘一条 Trajectory，可续跑；`infra_error` 按 B6 重试，重试耗尽的单元不落盘、记入失败账本，续跑时再试 |
| K2 | 切段（带校验） | D5 → G1 → D7；不通过就把错误清单交给 G1 重切 | 重切次数上限 `segment_retry_limit`；耗尽则跳过这条轨迹并记入审计日志，本 run 内不再重切 |
| K3 | 考一次 | D9 → G4 → G5 → 记一条 Attempt | skill 列表为空即裸答；Attempt 记录用途（裸答 / 验收 / 回归）；先存作答、再判卷 |
| K4 | 验收 | K3（裸答）→ 循环 [K3（带 skill）→ 不过则 G6 → D8] | 裸答就对则丢弃；循环上限 `retry_limit`；D8 不通过的版本作废、本轮算用掉一次，回到 G6 重写 |
| K5 | 库整理（Curator） | G7 → G8 → K3 回归考试 → G9 → D10 | 回归考试覆盖合并所代表的全部原始 skill 的考题，任一不过就撤销该次合并；超出 `bank_token_budget` 时回到 G7/G8 继续合并，最多 `curation_rounds` 轮；仍超出则阶段 5 停下报告，不自动删 skill |

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
- 熔断（运行设置 `--infra-breaker`，默认 5，0 关闭）：连续耗尽重试的单元覆盖到
  T 道**不同的题**才判定端点挂了，T = min(`--infra-breaker`, 本次待跑单元里不同的
  题数)，所以题少的任务集也能熔断；待跑单元只剩一道题时不熔断（阈值为 1 会让一道
  坏题直接跳过它自己剩下的采样）。同一道坏题反复失败只说明这道题有问题，不计数；
  任何一个单元正常结束（成功或失败）都清零。熔断后剩下的单元不再尝试，逐个记入
  失败账本，函数正常返回，续跑时再试；
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

### 1.5 积木复用

**阶段 → 积木组装**

| 阶段 | 用到的积木 |
|---|---|
| 0 准备与冻结 | D1、D2、B4 |
| 1 双模型基线执行 | K1（强模型, 裸跑）、K1（弱模型, 裸跑）、D3、D4 |
| 2 轨迹切段 | K2（对强、弱模型轨迹各调用） |
| 3 skill 与考题生成 | D6、G2（提炼 / 诊断）、G3，G2 与 G3 互相独立可并行 |
| 4 验收 | K4（内含 K3、G6、D8） |
| 5 库整理 | K5（内含 G7、G8、K3、G9、D10） |
| 6 test | K1（弱模型, 带整个库）、K1（弱模型, 裸跑，仅 split 模式）、D3 |

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
                 / regression_failed / regression_error / interrupted_merge),
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

落盘布局（每个 run 目录）：

```
<run>/
  manifest.json                  冻结的输入与协议
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
  stage5_curation/curation_log.json
                                 库整理日志：输入集快照、每轮 G7 / G9 输出、每个合并的决定（续跑依据）
  stage5_curation/stage5_summary.json
  skill_bank.jsonl               整理后的最终库（test 时注入的就是它）
  stage6_test/bank/              带库臂的独立 store：内部重复 stage1_baseline/trajectories/ 等布局，
                                 本臂执行的 failed_units.jsonl 也在这里
  stage6_test/bare/              对照臂（split 模式的弱模型裸跑）的独立 store，布局同上
  stage6_test/accepted_infra_shortfall.json
                                 阶段 6 接受的空洞，按臂（with_bank / control）记录
  stage6_test/stage6_summary.json
                                 两臂成绩与 delta
  work/                          执行单元的工作目录：work/stage1/、work/stage6_bank/、
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
    直接看"原来做不出来的题现在能不能做出来"。
- **任务集 `tasks`**（`docs/decisions/stage0_task_set_freeze_20261011.md`）有三种写法：
  不写（插件的全集；插件可以拒绝，TB 就拒绝，必须显式给出）、任务 ID 列表、
  任务文件路径（格式由插件解释）。列表不得为空、不得有重复 ID。
  - 配置里的相对路径（`tasks`、`weak_profile_file`）一律按**真实**配置文件所在目录
    解析：经符号链接加载与直接加载等价。任务文件路径存成绝对路径，所以从哪个
    目录启动或续跑，冻结的都是同一个值；
  - 插件加载任务后先查重复 ID（例如任务文件把同一道题写了两次），有重复即配置
    错误，在任何划分之前就停下；
  - `split` 模式给了显式 train / test 列表时：同时给了 `tasks` 的，两者必须完全一致
    （报出两边各自多出的 ID）；只给列表的，加载列表的并集。按比例 + 种子划分时，
    从加载到的任务集派生。
- **具名规则**：配置里没写的规则由 `DEFAULT_RULES`（`core/config.py`）补齐——
  `task_select = all_valid_failed`、`segment_select = strong_all_weak_failed`、
  `trajectory_select = all`，所以冻结的是实际生效的名字。阶段 0 在加载任务、
  写任何文件之前就逐个核对规则名是否已登记，名字拼错在这里就停下，不会等到
  昂贵的阶段 1 跑完才报错；后面各阶段直接读 `config.rules`，不再各自带默认值。
- 冻结：benchmark、模式、显式的 train / test 列表、完整配置（任务集、划分、
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
- **失败题集合 `F` = 弱模型有效采样全部失败的题**（基础设施异常不算失败，
  重试后有效采样仍不足 `n_attempts_weak` 的题单独列出，不进 `F`）；
  只要有一次做出来，该题就不进入后续流程。
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
  - `trajectory_success`：来源轨迹本身是否成功。弱模型轨迹都来自 `F`，恒为
    false；强模型轨迹两种都有，便于事后单独统计"强模型自己也没做出来的题"
    产出的 skill 的入库率与效果。
- **切段失败**：G1 输出不合格算一次失败的切段尝试，重切耗尽后跳过这条轨迹、写进
  `stage2_segments/segmentation_audit.jsonl`，本 run 内不再重切（换一次采样重试就是
  新实验，用新 run 目录）。切段调用遇到基础设施错误不算切段失败：这条轨迹记入失败
  账本、什么都不存，其余轨迹照常切，阶段末以基础设施错误停下，续跑只重做这些轨迹。
- **待定**：一道题有多次采样时，切哪几条轨迹（全部，还是每个模型取若干条）。
  无论怎么定，都做成一条具名的"轨迹挑选规则"（与 D4、D6 同一个规则注册表），
  以后换策略只新增规则、不改流程。

### 阶段 3：skill 与考题生成（D6、G2、G3）

- D6 筛段：强模型段全部生成；弱模型段只处理 `success = false` 的段。
- G2 写 skill：强模型段用"提炼"写法，弱模型段用"诊断"写法。
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

### 阶段 4：验收（K4）

对每个候选 skill 及其 Subtask 的考题：

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

- **G6 看不到参考答案**；G5 的错误描述只说哪里不对，不复述正确答案。
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

### 阶段 5：skill 库整理（K5，参照 AgentBrew Curator）

通过验收的 skill 都只是**单独**被证明有用；test 时弱模型要一次读入整个库，
多个 skill 放在一起可能互相冲突、互相稀释，总长度也可能超出 prompt 容量。
做法参照 AgentBrew §2.3.3 Note Curator 与 §2.5 Lifelong Memory：

- **只读取通过验收的 skill**，没过验收的版本不得进入库（UpSkill 仓库实现里
  先 curate 后验证、导致未验证笔记入库的顺序问题，见外层
  `docs/design/agentbrew_upskill_porting_plan_20261008.md`）。
- **不重新判断对错**，只做库的维护：
  1. **去重合并**（G7 → G8）：意思相近的 skill 合并成一条新 Skill，来源 skill
     状态改为 `merged`，原文留档；
  2. **冲突处理**（G7 → G8）：同一情境下给出相反做法的 skill，合并为带条件
     区分的一条，或保留证据更强的一条（记录理由）；
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

### 阶段 6：test（K1、D3）

- 强模型完全退出。
- 弱模型带库回测：K1（弱模型, skill = 整个 `skill_bank.jsonl`）
  （`split` 模式跑 test 集；`full` 模式重跑任务集）。
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
| 4 验收 | 同上（单元是 skill，保持 pending） | 丢弃（`discard_reason = model_output_error`），本 run 内终态 |
| 5 整理 | 记入失败账本后立即停下（退出码 3），续跑按日志接着做 | G8 → 跳过该合并；回归考试 → 撤销该合并；G7 / G9 → 停下整个阶段（退出码 1，已完成的单元都已落盘，重跑同一条命令会重试这次调用） |

阶段 2–4 的基础设施空洞不能被接受，只能续跑补上。配置错误、服务方拒绝在任何
阶段都立即停下（退出码 1）；代码 bug 立即停下并保留调用栈。"本 run 内终态"的意思
是：一个 run 是一次冻结的实验，同一个 run 目录里不再重试；想换一次采样再试，就开
新 run 目录。

**命令行**

```
expedistill run --config <配置文件> --run-dir <run 目录> [运行设置]
expedistill summary --run-dir <run 目录>
```

- `run` 每完成一个阶段打印一行摘要；用同一条命令重跑即续跑，各阶段按确定 ID
  跳过已完成的单元；
- `summary` 只读进度账本和用量账本，不重算任何东西；账本里缺阶段时退出码 1。

**运行设置**（只影响"怎么跑"，不进配置、不进 manifest）：

| 参数 | 默认 | 作用 |
|---|---|---|
| `--workers` | 1 | 执行单元的并行数 |
| `--llm-infra-retries` | 3 | 单个模型请求遇到基础设施错误时的重发次数 |
| `--llm-infra-backoff` | 5.0 | 重发的指数退避基数（秒），单次等待最多 60 秒 |
| `--infra-breaker` | 5 | K1 熔断：连续耗尽重试的单元覆盖到这么多道不同的题（不超过待跑的题数；只剩一道题时不熔断）后，本批剩余单元不再尝试、等续跑；0 关闭 |
| `--allow-infra-shortfall` | 关 | 让阶段 1、6 带着空洞继续；空洞仍计为基础设施异常，阶段摘要和账本行都标出，并向 stderr 打警告 |
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
   "答得对"不等于"做得到"，这个差距只能在 test 成绩里看出来。

## 五、代码来源（provenance）

底座代码（runtime / benchmarks / persistence / reliability / 执行循环 /
test 骨架）从姊妹仓库迁移而来：

- 源仓库：`../ExpeToSkill-merged`（SkillExpand 系统）
- 源分支与提交：`TB-eval @ 4f436af`
- 迁移时按"仅带依赖闭包"原则拷贝，详见首次迁移提交的说明
- 迁移来源与积木的对应：B1/B2/B4/B5 ← `runtime` + `persistence`；B3 ← `persistence/store`；
  B6 ← `reliability`；E1 与三个 runner ← `benchmarks` + `l1` 执行循环簇 + `evaluation/workers`；
  D2 ← `evaluation/splits`；其余积木为新写。迁移后的落位以 AGENTS.md 的目录结构为准
