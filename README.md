# ExpeDistillation

把强模型的做题经验提炼成一个自然语言 Skill 库，教会弱模型。全程不训练任何
模型权重：Skill 是一份份写给弱模型读的文字，注入弱模型的 prompt 是它们影响弱模型
的唯一途径。

本仓库是这套方法的实验框架：一条命令跑完"摸底 → 写 Skill → 检验 Skill → 终测"
的整个流程，支持 ALFWorld、SearchQA、TerminalBench 三个 benchmark，
也能用同一套执行与评测流程比较其他 Skill 学习方法（ExpeL、GEPA、SkillOpt 等）。

| 想做什么 | 去哪里 |
|---|---|
| 理解设计思路 | 本文第一、二节（含全局 Playbook） |
| 了解模块边界、扩展方法 | 本文第三、四节 |
| 跑起来 | 本文第五节 |
| 查完整的设计细节（积木清单、数据模型、每个相位的流程） | [docs/design_spec.md](docs/design_spec.md) |
| 开发规范、目录结构、测试要求 | [AGENTS.md](AGENTS.md) |
| 某个设计决定的来龙去脉 | `docs/decisions/` |

---

## 一、设计理念

### 1.1 问题

强模型做对了一道题，留下一条轨迹。弱模型要从这些轨迹里得到什么，才能在没见过的
题上也做得更好？ExpeDistillation 的回答是一个 Skill 库：每条 Skill 写明
"什么时候用、怎么做、什么时候别用"，弱模型做题时读到它，照着做。

难点在于 Skill 写出来不等于有用。一条 Skill 可能弱模型本来就会、可能读不懂、
可能触发条件写错而没人打开、也可能把弱模型带偏。所以框架的核心是：每条 Skill
都要经过弱模型的实际检验，检验的结果再反过来指导修改。

### 1.2 执行—学习闭环

整个系统是一个闭环：

```
          ┌────────────── 技能库 ──────────────┐
          ▼                                     │
   ┌─────────────┐   运行记录 + 验收报告   ┌─────────────┐
   │  执行阶段    │ ─────────────────────► │  学习阶段    │
   │ 用技能库做题 │                         │ 根据结果改库 │
   └─────────────┘                         └─────────────┘
```

两种阶段在配置里都叫"相位"（phase）。执行相位有四种：

| 相位 | 做什么 | 题目来源 | 注入的技能 |
|---|---|---|---|
| `baseline` 摸底 | 强、弱模型各自裸跑，留下轨迹，找出弱模型做不好的题 | 训练题 | 无 |
| `exam` 泛化考试 | 给每条新技能出一道"场景题"，看弱模型能否找到并用对这条技能 | 从轨迹里复原的场景 | 全库目录，模型自己选 |
| `live` 实战验收 | 弱模型在真实环境里带技能做训练题，与摸底成绩比较 | 训练题 | 该题对应的技能 |
| `test` 终测 | 带上最终技能库做测试题，与不带技能的成绩比较 | 测试题 | 最终技能库 |

学习相位只有一种（`learn`），内部按"决策 → 找证据 → 写作 → 整理"四步进行：
看验收报告决定每条技能保留、修改、删除还是新增，为修改和新增收集轨迹证据，
写出或改写技能，最后对整个库去重合并。

一种方法就是这些相位排成的序列。本仓库的参考方法（配置
`configs/experiments/alfworld_split_paper_v3_e1_ours.yaml`）是：

```
baseline → learn(新增) → exam → learn(修订) → exam → learn(修订) → test
```

先用摸底的失败轨迹写出第一批技能，再用两轮泛化考试检验并修订，最后在测试题上终测。
把 `exam` 换成 `live` 就得到另一条路线：技能直接在真实环境里检验。

### 1.3 四条贯穿全局的原则

1. **判定与比较分开。** 验收只回答"这次运行成功、失败还是无效"。"带技能比不带
   技能是否提升"由决策根据配置里的比较规则计算，规则的名字在看结果之前写进配置。
2. **执行不下结论。** 执行把 benchmark 自带判分器的结果原样传下去；没拿到结果就
   留空（`null`），既不算对也不算错。
3. **测试结果不流进学习。** 终测只产出最终成绩，决策入口收到终测报告会直接报错，
   避免用测试题调参。
4. **可恢复、可审计。** 每完成一个单元（一次执行、一次作答、一次改写）立刻写盘；
   同一条命令重跑即续跑；配置、任务表和全部 prompt 在 run 开始时冻结，之后任何
   不一致都会拒绝启动。

---

## 二、一次实验长什么样

### 2.1 配置：方法 = 相位序列

一份 YAML 配置就是一次实验的全部身份。核心是两块：`sequence`（相位名单）和
`phases`（每个名字对应的相位定义）。下面是一个精简的 exam 方法（省略了模型表、
token 预算等字段）：

```yaml
benchmark: alfworld              # alfworld / searchqa / tb
mode: split                      # split：划分训练/测试；full：全部题目一起蒸馏
models:
  executor_strong: <强模型名>
  executor_weak:   <弱模型名>    # 其余角色（写作、判卷等）按默认补齐
n_attempts_strong: 3             # 摸底阶段每题采样次数
n_attempts_weak: 3

sequence: [baseline, learn_add, exam, learn, test]
phases:
  baseline:
    kind: execute
    role: baseline
    tasks: train
    delivery:  {name: none}
    execution: {name: real, arms: [executor_strong, executor_weak]}
  learn_add:                     # 对弱模型做不好的题，从轨迹里写出第一批技能
    kind: learn
    role: learn
    review:  {name: review, decision: skill_level, K: 5, retry_limit: 2}
    author:  {name: cluster_by_trigger}
    curate:  {name: none, token_budget: null}
  exam:
    kind: execute
    role: exam
    tasks: train
    delivery:  {name: exam_folder, description_mode: summary_then_when_to_use}
    execution: {name: exam, max_skill_reads: 3}
  learn:                         # 根据考试结果保留 / 修改 / 删除技能，最后整理成库
    kind: learn
    role: learn
    review:  {name: review, decision: trial_level, K: 5, retry_limit: 2}
    author:  {name: cluster_by_trigger}
    curate:  {name: agentbrew, rounds: 3, token_budget: null}
  test:
    kind: execute
    role: test
    tasks: test
    delivery:  {name: folder}
    execution: {name: real_folder}
    compare:   {against: fresh_bare}   # 对照：同一批测试题不带技能
```

字段含义、加载期检查清单和所有可选项见 [docs/design_spec.md](docs/design_spec.md)
第二节。想改变方法，改这份配置即可；想做对照实验，复制一份新名字再改（已经跑过
正式 run 的配置不再修改）。

### 2.2 技能库怎样交给弱模型

`delivery`（交付）决定每道题看到哪些技能：

| 名字 | 做法 |
|---|---|
| `none` | 不注入，裸跑 |
| `whole_bank` | 整个技能库作为一段规则注入 prompt |
| `retrieval` | 先按类别、再按相关度检索出若干条注入 |
| `folder` / `folder_by_task` | 渐进式披露：prompt 里只有一份技能目录（名字 + 触发描述），模型需要时用 `ReadSkill[位置]` 打开某条技能 |
| `exam_folder` | 泛化考试专用的目录交付 |

`folder` 与 `exam_folder` 还可以附带一份常驻的全局 Playbook，见 2.3。

`folder` 形态与 Agent Skills 的使用方式一致：弱模型自己判断哪条技能适用，
所以一条技能的"触发描述"写得好不好，会直接决定它有没有被打开。

### 2.3 全局 Playbook：常驻的整体策略

Skill 按需打开，一局里可能一条都没被读到。有些规则（任务类型的通用配方、搜索循环
恢复规则）需要每一步都可见，这类内容放进全局 Playbook：一段自然语言文本，在技能目录
之前常驻渲染在 prompt 里。它属于交付层，和技能库互相独立，默认关闭，只在
`folder` / `exam_folder` 交付里开启，有两种来源：

| 来源 | 配置 | 说明 |
|---|---|---|
| 手写文件 | `playbook_file: <路径>` 加 `playbook_sha256: <文件哈希>` | 读取文件正文；文件内容变化会因哈希不符被拒绝。ALFWorld 的版本在 `configs/playbooks/` |
| 从摸底轨迹学习 | `playbook_source: learned` | 与 `playbook_file` 互斥。`baseline` 之后的第一个 `learn` 相位，从 baseline 答卷里程序提取事实（各任务类型的成败、失败局的重复搜索统计等），再调用一次写作模型写出 `playbook.md`，之后的执行相位读取最近一份 |

```yaml
phases:
  exam:
    delivery: {name: exam_folder, playbook_source: learned}
  test:
    delivery: {name: folder, playbook_source: learned}
```

两种来源只改变 prompt 里的常驻文本，技能的学习、检验、整理流程不变。不写这两个键时，
manifest 与 prompt 逐字节不变。学习来源只读训练集的摸底轨迹，不使用测试集信息。
对应配置：`alfworld_split_paper_v3_e6_playbook_k*`（手写）与
`alfworld_split_paper_v3_e7_learnedpb_k*`（学习）。设计与取舍见
`docs/decisions/global_playbook_p0_20261104.md` 与
`docs/decisions/learned_playbook_20261105.md`。

### 2.4 一次 run 留下什么

```
runs/<实验名>_<时间>/
  manifest.json            冻结的配置、任务表、prompt 摘要
  pipeline_log.jsonl       每个相位完成时写一行
  failed_units.jsonl       失败单元的记录
  phases/pNN_<相位名>/     每个相位自己的账本、答卷与技能库快照
  stage1_baseline/ stage2_segments/ stage3_generation/ stage4_acceptance/
                           跨相位共享的对象库（轨迹、段、技能、考题、作答）
  llm_requests/            每次模型调用的请求与回复，usage.jsonl 是用量账本
```

最终成绩在最后一个 `test` 相位目录里，用 `expedistill summary` 查看进度与用量。
完整布局见设计正本第四节。

---

## 三、模块与接口边界

### 3.1 七个模块

```
执行阶段：技能库 → 交付 → 执行 → 验收 → 验收报告
学习阶段：验收报告 → 决策 → 找证据 → 写作 → 整理 → 新技能库
```

| 模块 | 输入 → 输出 | 配置里怎么选 |
|---|---|---|
| 交付 | 技能库 + 题目 → 执行单元清单（一道题 + 注入的技能） | `delivery` |
| 执行 | 执行单元清单 → 运行记录（每次运行一条） | `execution` |
| 验收 | 运行记录 → 每次运行的判定（success / failure / invalid） | `acceptance`（判分规则名） |
| 决策 + 找证据 | 验收报告 + 当前技能库 → 每条技能的动作及证据 | `review` |
| 写作 | 动作 + 证据 → 新技能或改写后的技能 | `author` |
| 整理 | 本轮全部技能 → 新技能库 | `curate` |

### 3.2 交接物

相位与相位之间只传三样东西：**技能库**、**运行记录**、**验收报告**。执行单元清单、
动作清单、证据包只在相位内部流转。这条边界让每个模块可以单独替换、单独测试：
换一种写作方法，不影响交付与验收；换一个 benchmark，不影响学习相位。

### 3.3 五个数据对象

全部以 JSON 落盘，对象之间用外键关联，ID 由程序按确定规则生成（模型输出里的编号
不当 ID 用）：

| 对象 | 含义 |
|---|---|
| `Trajectory` | 一次完整执行的逐步记录（动作、观察、判分器结果） |
| `Subtask` | 轨迹切出的一段，写作的基本素材 |
| `Skill` | 一条技能：一句话摘要 + `[When to use]` / `[Steps]` / `[Do not use when]` 三节正文，带版本号与状态 |
| `Exam` | 为某个技能版本从真实轨迹场景复原出的考题 |
| `Attempt` | 弱模型对一道考题的一次作答与判卷结果 |

每个技能版本都带来源记录，指向它是从哪条轨迹的哪一步学出来的。考题的场景就是从这个
指针回到真实轨迹中复原的。

### 3.4 方法驱动：五族可替换件

学习与执行的每个槽位都是一个"族"，每族有若干具名驱动，登记在
`src/expedistill/methods/registry.py` 的一张纯数据表里：

| 族 | 现有驱动 |
|---|---|
| `delivery` | `none` `whole_bank` `retrieval` `group_by_task` `folder_by_task` `folder` `exam_folder` |
| `execution` | `real` `real_folder` `exam` |
| `review` | `review`（决策 `task_level` / `skill_level` / `trial_level`） |
| `author` | `distill_diagnose` `cluster_by_trigger` |
| `curation` | `agentbrew` `none` |

加载配置时，每个方法名与参数都会被校验：名字拼错、参数类型错误会在任何文件被写入
之前停下。每个族的参数说明在 `src/expedistill/methods/<族>/README.md`。

**新增一种方法**：在对应族里写一个驱动和它的参数类，在注册表加一行，配置里写名字。

### 3.5 Benchmark 插件接口

所有与具体 benchmark 有关的代码都在 `src/expedistill/benchmarks/<名字>/`，
上层代码对 benchmark 一无所知。一个插件实现四件事：

| 接口 | 作用 |
|---|---|
| `load_tasks(spec)` | 加载题目，返回 `Task`（ID、说明、插件自用的 `meta`） |
| `run(task, handle, skills_block, ...)` | 执行一道题一次，返回 `RunOutcome{steps, correctness, meta}`；`correctness` 取 `True` / `False` / `None`；沙箱超时、断网等基础设施故障抛 `InfrastructureError`，由框架重试 |
| `view_rules()` | 轨迹给模型看时的截断规则（截断必须显式标注） |
| `task_prompt_name()` | 执行 prompt 在注册表里的名字 |

可选能力：声明 `skill_folder_prompt_name` 即支持技能文件夹交付；声明
`learning_context` 提供该 benchmark 的学习提示词词汇；声明 `playbook_prompt_name`
与 `playbook_facts(sheets)` 即支持学习全局 Playbook（前者是写作 prompt 的注册名，
后者从摸底答卷提取事实文本，不调模型；两者缺省表示不支持，配置却要求学习
Playbook 时 stage 0 直接报错）。新增 benchmark 不需要修改
`blocks/` 及以上的任何代码。步骤见 [AGENTS.md](AGENTS.md) 第七节。

### 3.6 模型与 Prompt

- 调用模型的代码只接收一个"模型句柄"，不自己选模型；哪个角色用哪个模型由配置里的
  `models` 角色表决定（`executor_strong`、`executor_weak` 必填）。
- 所有 prompt 登记在 `src/expedistill/prompts/`，按名字取用，摘要写进 manifest。
  改一个字就是新实验，必须新建 run 目录。
- 模型服务通过环境变量接入，兼容 OpenAI 接口；`EXPEDISTILL_LLM_PROVIDER=fake`
  可切换到按脚本回复的假模型，用于零网络冒烟。

### 3.7 代码分层

```
core → infra / prompts / inject → llm → benchmarks → blocks → composites → methods → stages → cli
```

只能向下依赖，`tests/test_layering.py` 静态检查。`blocks/` 是做一件事的功能积木，
`composites/` 负责落盘与续跑，`stages/` 把积木串成相位，`cli.py` 是唯一入口。
详见 [AGENTS.md](AGENTS.md) 第四节。

---

## 四、可靠性约定

| 约定 | 内容 |
|---|---|
| 冻结 | 配置、任务表、prompt 摘要在 stage 0 写入 `manifest.json`；之后每次启动逐字段比对，不一致即拒绝 |
| 逐单元落盘 | 一次执行、一次作答、一次改写完成就写盘（原子写）；已完成的单元续跑时跳过 |
| 排他锁 | 一个 run 目录同时只允许一个进程，第二个进程报错并给出持有者 PID |
| 失败分类 | 基础设施故障按策略重试；模型输出格式错误按次数修复；配置错误与代码缺陷立即停止 |
| 无效重派 | 被判无效的单元重新派发，次数上限为配置项 `invalid_redispatch_limit` |
| 没变的单元不重测 | 题和注入的技能都没变时，沿用上一次的验收结果 |
| 用量累加 | token 用量跨进程累加落盘；拿不到用量记 unknown，不记 0 |

---

## 五、使用教程

### 5.1 安装

要求 Python ≥ 3.9 且 < 3.12。

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

安装后得到命令 `expedistill`（等价于 `python -m expedistill.cli`）。

### 5.2 确认环境正常

```bash
.venv/bin/pytest tests -q
```

默认不连任何真实服务，全部用假模型，约 1 分钟。标记为 `live` 的测试需要真实端点，
默认跳过。

### 5.3 配置环境变量

复制 `.env.example` 为 `.env` 并填写（`.env` 不进 git，密钥只进进程环境）：

```bash
cp .env.example .env
```

| 变量 | 用途 |
|---|---|
| `EXPEDISTILL_LLM_ENDPOINT` / `EXPEDISTILL_LLM_API_KEY` | OpenAI 兼容的模型端点与密钥，强弱模型共用 |
| `EXPEDISTILL_LLM_ENDPOINTS` / `EXPEDISTILL_LLM_API_KEYS` | 可选，让个别模型走另一个端点 |
| `EXPEDISTILL_ALFWORLD_EVAL_DIR`、`EXPEDISTILL_ALFWORLD_VENV`、`ALFWORLD_DATA`、`ALFWORLD_CONFIG` | 跑 ALFWorld 时需要 |
| `EXPEDISTILL_SEARCHQA_EVAL_DIR` | 跑 SearchQA 时需要 |
| `EXPEDISTILL_TB_*` | 跑 TerminalBench 时需要，见 [scripts/remote/README.md](scripts/remote/README.md) |

### 5.4 启动一次实验

`configs/experiments/` 里有现成的配置。例如 ALFWorld 上的参考方法：

```bash
set -a; . ./.env; set +a
.venv/bin/expedistill run \
  --config configs/experiments/alfworld_split_paper_v3_e1_ours.yaml \
  --run-dir runs/e1_ours_$(date +%Y%m%d_%H%M) \
  --workers 16
```

常用运行设置（只影响怎么跑，不进配置、不进 manifest）：

| 参数 | 作用 |
|---|---|
| `--workers` | 只调用模型端点的步骤（写作、审阅、整理、考试）的并行数 |
| `--rollout-workers` | 真实环境执行的并行数，缺省与 `--workers` 相同 |
| `--until-phase N` | 只跑到序列第 N 项（从 0 数）后停下，用于试跑；`-1` 只做冻结 |

**先小后大**：正式实验之前，先把一份配置的任务表缩到 1–2 道题，用 `--until-phase`
逐相位跑通，确认输出结构正确、对照组确实是对照组，再启动全量。

长时间实验建议用 `scripts/run/run_experiment.sh <配置名>`：它加载 `.env`，把 driver
脱离当前终端启动，并写出 pidfile 与续跑命令。判断任务是否还活着请读 pidfile，
不要用 `pgrep -f`。

### 5.5 续跑与查看

```bash
# 中断后，用同一条命令续跑
.venv/bin/expedistill run --config <同一份配置> --run-dir <同一个 run 目录> --workers 16

# 查看进度与用量
.venv/bin/expedistill summary --run-dir <run 目录>
```

退出码：`0` 正常完成；`1` 需要人处理（配置错误、manifest 不一致、目录被锁、模型输出
错误）；`3` 基础设施耗尽，直接续跑即可；`2` 命令行用法错误。

跑完之后先用 `scripts/audit/` 下的脚本做完整性审计（重复执行、对象一致性、统计口径），
通过之后再使用其中的数字。

### 5.6 分析与对比

这些子命令只读已完结的 run，结果写到 run 目录之外：

| 命令 | 用途 |
|---|---|
| `paper-tables` | 多个 run 之间的配对比较：按任务自助法置信区间、胜负平、McNemar 检验 |
| `judge-skills` | 终测或实战里每次运行的技能使用情况：该用的有没有打开、打开后有没有照做 |
| `exam-replay` | 同一技能库、同一批考题，换一个答题模型重答 |
| `segmentation-metrics` | 切段粒度消融的机制指标 |
| `import-bank` | 把其他方法产出的技能（markdown、技能文件夹、笔记）导入为一个测试专用 run，在同一终测流程下比较 |
| `fork-run` / `assemble-reuse` | 复用已完结 run 的前若干相位，从某个相位开始换配置接着跑 |

各命令的参数见 `expedistill <命令> --help`，用途与统计口径见设计正本 3.8 与第七节。

### 5.7 设计自己的方法

1. 复制一份相近的配置，改文件名；
2. 调整 `sequence` 与 `phases`：增删相位、换交付方式、换 `review.decision`、
   改 `curate`、开关全局 Playbook（2.3）；
3. 需要新的行为时，按 3.4 在对应族里新增驱动；需要新 benchmark 时，按 3.5 写插件；
4. 小样本干跑，再全量。

`scripts/run/build_paper_configs.py` 展示了怎样用脚本批量生成一组只相差一个开关的
对照配置，`--print-index` 会打印每个对照臂与参照臂的差异。

---

## 六、已知局限

1. **考题质量。** 考题太像原题，弱模型靠记忆也能通过；考题太泛，检验就放水。
   裸答对照能筛掉前一种，筛不掉后一种。
2. **"答得对"与"做得到"的距离。** 泛化考试检验的是文本场景里的选择与计划，
   终测检验的是真实执行，两者的差距只能从终测成绩里看出来。`live` 路线直接在真实
   环境里检验，消除了这段差距。
3. **统计功效。** `live` 相位每题采样次数较少时，单题的二值比较噪声很大，
   推荐每题至少 2 次；技能库整体有没有用，以 `test` 相位在测试集上的配对比较为准。
4. **`full` 模式的可迁移性。** `full` 模式下训练题与测试题相同，任务定制的内容会
   同时抬高验收与终测成绩；主张可迁移性时使用 `split` 模式。

更多讨论见设计正本"已知风险"一节。
