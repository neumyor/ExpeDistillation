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
| B1 | 调用模型 | 输入（模型, prompt 名, 变量, 输出格式）→ 解析好的结果。格式不对时按规则重试；每次请求和用量落盘 |
| B2 | Prompt 注册表 | 所有 prompt 的具名常量；计算 digest 供 manifest 冻结（见 1.6） |
| B3 | 对象存储 | 五个对象的保存 / 读取 / 列举；判断某个单元是否已完成（断点续跑用） |
| B4 | Manifest 冻结 | 写一次、之后逐字段比对，不一致即拒绝继续（沿用 SkillExpand 的 `freeze`） |
| B5 | 并行池 | 与业务无关的线程池 / 进程池 |
| B6 | 可靠性 | 异常分类（基础设施 / 模型输出 / 配置 / 代码 bug）、重试策略、单元失败记录（沿用 SkillExpand 的 `reliability`） |
| B7 | 运行锁与用量累计 | run 目录排他锁（防止两个进程写同一个 run）；用量跨进程累加落盘，续跑后不清零 |

### 1.3 功能积木

**执行类**

| 编号 | 名称 | 输入 → 输出 | benchmark 相关 |
|---|---|---|---|
| E1 | 执行（Executor） | （模型, 一道任务, skill 列表（可选））→ Trajectory（含结果：成功 / 失败 / 基础设施异常） | 是：内部按 benchmark 选 runner |

**基础设施异常不算失败**：沙箱启动超时、评测超时、网络错误等不是模型做错了，
E1 把结果标成 `infra_error`，而不是 `success = false`。K1 按 B6 的策略对这类
单元重试；D3 统计成绩时只算有效执行（成功 / 失败），基础设施异常单独计数。

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

**数据整理类**（纯程序，不调模型）

| 编号 | 名称 | 输入 → 输出 | benchmark 相关 |
|---|---|---|---|
| D1 | 任务加载 | （benchmark, 任务表）→ 任务列表 | 是 |
| D2 | 划分 | （任务列表, 模式 split/full, 种子）→ train / test | 否 |
| D3 | 成绩统计 | Trajectory 列表 → 每题有效次数 / 成功次数 / 基础设施异常次数，以及 avg@N、pass@N | 否 |
| D4 | 挑题 | （每题成绩, 具名规则）→ 题目集合。阶段 1 的规则是"有效采样数达到 N 且全部失败" | 否 |
| D5 | 轨迹视图 | （Trajectory 或段内步骤, 视图配置）→ 给模型看的文本；超长观察按规则截断，截断处明确标注 | 是：截断规则按 benchmark 配置 |
| D6 | 段筛选 | （Subtask 列表, 规则）→ 要生成 skill 的 Subtask。当前规则：强模型段全要，弱模型段只要 `success = false` | 否 |
| D7 | 切段校验 | （Trajectory, Subtask 列表, K）→ 通过 / 错误清单 | 否 |
| D8 | 泄题审计 | （Skill, Exam）→ 通过 / 重合报告。skill 与参考答案、考题情境中具体实体的字面重合超过阈值即不通过 | 否 |
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
| K1 | 批量执行 | E1 × 题目 × 采样次数，经 B5 并行 | 每完成一次执行就落盘一条 Trajectory，可续跑；`infra_error` 按 B6 重试 |
| K2 | 切段（带校验） | D5 → G1 → D7；不通过就把错误清单交给 G1 重切 | 重切次数上限 `segment_retry_limit`；耗尽则跳过这条轨迹并记入审计日志 |
| K3 | 考一次 | D9 → G4 → G5 → 记一条 Attempt | skill 列表为空即裸答；Attempt 记录用途（裸答 / 验收 / 回归） |
| K4 | 验收 | K3（裸答）→ 循环 [K3（带 skill）→ 不过则 G6 → D8] | 裸答就对则丢弃；循环上限 `retry_limit`；D8 不通过的版本作废、本轮算用掉一次，回到 G6 重写 |
| K5 | 库整理（Curator） | G7 → G8 → K3 回归考试 → G9 → D10 | 回归考试不过就撤销该次合并；超出 `bank_token_budget` 时回到 G7/G8 继续合并，最多 `curation_rounds` 轮；仍超出则阶段 5 停下报告，不自动删 skill |

组合积木负责落盘和续跑：每一步的产物按确定 ID 存进 B3，再次运行时已完成的
单元直接读盘跳过。功能积木本身不读写磁盘。

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
  discard_reason(weak_already_knows / retry_exhausted / leak_audit / ...),
  group(能力分组，阶段 5 写入),
  rewrite_history[ 旧版正文、错因归类与改写原因 ], merged_from[ skill_id ...](仅库整理产物)
  （task_id / trajectory_id 经 subtask_id 查得，不重复存）

Exam（考题）
  exam_id, question_text, reference_answer(判卷参考), subtask_id(→段)

Attempt（作答记录）
  attempt_id, exam_id, skill_id + skill_version(都为 null = 裸答),
  purpose(bare / acceptance / regression), weak_answer,
  judge_verdict, judge_reason, retry_round
```

**关系**：Skill 和 Exam 都只挂在 Subtask 上（`subtask_id`），两者互不引用——
考题出题时不看 skill。每个参与生成的 Subtask 对应一个 skill 和一道考题（1:1:1）；
改写重考不新建对象，skill 版本号 +1；每次作答（含裸答）另记一条 Attempt，
用 `exam_id` + `skill_id/skill_version` 指向当时的考题和 skill 版本，重考全程可追溯。

落盘布局（每个 run 目录）：

```
<run>/
  manifest.json                  冻结的输入与协议
  stage1_baseline/trajectories/  强/弱模型裸跑轨迹
  stage2_segments/subtasks/      切段结果（含校验审计日志）
  stage3_generation/skills/      skill（未验收）
  stage3_generation/exams/       考题
  stage4_acceptance/attempts/    每次作答（裸答 + 带 skill）与判卷记录
  stage5_curation/               合并记录、回归考试记录
  skill_bank.jsonl               整理后的最终库（test 时注入的就是它）
  stage6_test/                   test 轨迹与成绩
  llm_requests/                  B1 记录的每次模型请求与用量
```

## 三、系统流程（2026-10-10 定稿）

每个阶段标出用到的积木，规则写在阶段里，积木本身不带阶段规则。

### 阶段 0：准备与冻结（D1、D2、B4）

- 输入：任务集、强模型 `M_s`、弱模型 `M_w`、弱模型画像。
- 数据划分是**实验模式**，不是固定选择（以 TB 为例两种都做）：
  - 模式一 `split`：任务集划分 train / test，蒸馏只用 train，test 独立评测；
  - 模式二 `full`：全部题目一起蒸馏，test 时弱模型带 skill 重跑，
    直接看"原来做不出来的题现在能不能做出来"。
- 冻结：任务数据、划分、模型角色表、弱模型画像、采样次数、K、
  `segment_retry_limit`、`retry_limit`、`bank_token_budget`、`curation_rounds`、
  泄题阈值、具名规则的名字、全部 prompt digest，写进 manifest；之后再改即视为新实验。

### 阶段 1：双模型基线执行（K1 × 2、D3、D4）

- 强模型在每道蒸馏题上执行 `n_attempts_strong` 次，弱模型执行 `n_attempts_weak`
  次，均为裸跑；两边的 Trajectory 全部落盘（成功和失败都保留）。
- **失败题集合 `F` = 弱模型有效采样全部失败的题**（基础设施异常不算失败，
  重试后有效采样仍不足 `n_attempts_weak` 的题单独列出，不进 `F`）；
  只要有一次做出来，该题就不进入后续流程。
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
- **D8 泄题审计**：每版 skill 都过一次，不通过则该版作废
  （`discard_reason = leak_audit`）并记入审计日志。

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
  3. **合并后回归考试**（K3）：合并出的新 skill 重做每个来源 skill 的考题，
     来源原本能过、合并后过不了 → 撤销这次合并，恢复来源 skill；
  4. **按能力分组**（G9）：每条 skill 归入一个能力分组（AgentBrew 的 skill
     scope），D9 注入时按组排列，减少不相关 skill 互相干扰；
  5. **长度预算**（D10）：整库注入后不超过 `bank_token_budget`；超出时
     继续合并，而不是直接删掉通过验收的 skill；合并最多 `curation_rounds` 轮，
     仍超出则阶段 5 停下报告，由人决定（调预算或改合并策略，均为新实验）。
- 产出：`skill_bank.jsonl`，即 test 时注入的最终库。

### 阶段 6：test（K1、D3）

- 强模型完全退出。
- 弱模型带库回测：K1（弱模型, skill = 整个 `skill_bank.jsonl`）
  （`split` 模式跑 test 集；`full` 模式重跑任务集）。
- 对照组（弱模型裸跑成绩）：
  - `full` 模式：直接使用阶段 1 的弱模型裸跑成绩；
  - `split` 模式：阶段 1 只跑了 train，因此在 test 集上单独跑一组 K1（弱模型, 裸跑）。
- 提升幅度 = 带库成绩 − 对照组成绩（D3 统计）。

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
