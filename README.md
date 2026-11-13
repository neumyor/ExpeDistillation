# ExpeDistillation

把强模型的做题经验提炼成一个自然语言 Skill 库，教会弱模型。全程不训练任何
模型权重：Skill、弱模型画像和全局手册（playbook）都是写给模型读的文字，Skill 与
playbook 注入弱模型的 prompt，是它们影响弱模型的唯一途径。

本仓库是这套方法的实验框架：一条命令跑完"摸底 → 写 Skill → 带库练习、按失败修改 Skill → 终测"
的整个流程，支持 ALFWorld、SearchQA、TerminalBench 三个 benchmark，
也能用同一套执行与评测流程比较其他 Skill 学习方法（ExpeL、GEPA、SkillOpt 等）。

| 想做什么 | 去哪里 |
|---|---|
| 理解设计思路 | 本文第一、二节 |
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

难点在于 Skill 写出来不等于有用。一条 Skill 可能触发条件写错而没人打开、可能正文
没讲到关键步骤、也可能弱模型读了却没照做。所以框架的核心是：弱模型带着技能库在训练题
（或验证题）上真实练习，每一次失败都是证据；程序先判断失败落在哪个子任务、那张卡有没有
被打开，再决定写新卡、只改触发条件，还是只改正文。

方法有四个要点：

1. **一个子任务类型一张卡**：把轨迹切成目标连贯的段，归入一张只增不改的子任务类型表，
   每个类型最多一张 Skill，写卡材料是同类型的强模型成功段与弱模型失败段；
2. **触发条件与正文分开演化**：Skill 的触发条件（一句话摘要 + 什么时候用）是弱模型在
   目录里看到的部分，正文（步骤 + 什么时候别用）是打开后才看到的部分。练习失败时，
   程序根据出错那一步之前弱模型有没有打开这张卡判断该改哪一侧；
3. **两份全局文本一起演化**：弱模型画像（profile）记录弱模型自己的弱点，写作者每次
   写 Skill 都读它；全局手册（playbook）记录所有任务都适用的做法，可以在执行时常驻显示；
4. **每个 benchmark 一套可替换的提示词**：词汇、环境事实、执行提示词、playbook 的写法
   和检查都集中在 benchmark 的提示词包里。

### 1.2 执行—学习闭环

整个系统是一个闭环：

```
          ┌──────── 技能库 + 画像 + playbook ────────┐
          ▼                                          │
   ┌─────────────┐      运行记录 + 答卷        ┌─────────────┐
   │  执行相位    │ ─────────────────────────► │  学习相位    │
   │ 用技能库做题 │                             │ 根据答卷改库 │
   └─────────────┘                             └─────────────┘
```

两类阶段在配置里都叫"相位"（phase）。执行相位有三种：

| 相位 | 做什么 | 题目来源 | 注入的技能 |
|---|---|---|---|
| `baseline` 摸底 | 强、弱模型各自裸跑，留下轨迹；只能是序列第一项 | 训练题 | 无 |
| `practice` 练习 | 弱模型带着当前技能库（技能文件夹）和 playbook 做题，留下新的失败 | 训练题或验证题（`tasks: train \| val`） | 全部在用的卡 |
| `test` 终测 | 带上最终技能库做测试题，与不带技能的成绩比较 | 测试题 | 全部在用的卡 |

学习相位只有一种（`learn`），读紧邻的上一个摸底或练习相位里弱模型的失败答卷，每次按
同样五步进行：

1. **诊断**每张失败答卷：失败原因、决定性出错的那一步、范围（某个子任务 / 通用），
   以及给画像和 playbook 的两条笔记；
2. **定位**：切段失败轨迹，把新段归入子任务类型表，出错那一步所在段的类型就是这条失败的类型；
3. **分配证据**（纯程序）：该类型没有卡 → 缺口；有卡但出错前没打开 → 触发证据；
   出错前打开过 → 正文证据；通用失败只有笔记；
4. **演化**画像和 playbook；
5. **演化技能库**：缺口写新卡，触发证据只改触发条件，正文证据只改正文；同一张卡连续两个
   学习相位拿到正文证据就删除。

学习中不设统计关卡：一张卡有没有用由终测衡量，学习只读失败证据并据此修改。

一种方法就是这些相位排成的序列。参考方法（配置
`configs/experiments/alfworld_paper_e1_ours.yaml`）是：

```
baseline → learn → practice → learn → practice → learn → test
```

第一个学习相位读摸底的失败写出第一批卡，之后两轮练习各自暴露新的失败、驱动下一次学习，
最后一个学习相位的改写直接带进终测。

### 1.3 四条贯穿全局的原则

1. **执行不下结论。** 执行把 benchmark 自带判分器的结果原样传下去；没拿到结果就
   留空（`null`），既不算对也不算错。
2. **证据类别由程序决定。** 诊断模型只给失败原因、出错那一步和范围；出错步落在哪个
   子任务类型、出错前有没有打开过该类型的卡，都是从轨迹里读出的程序事实（打开记录来自
   `cat` 或 `ReadSkill[...]` 动作，不取模型自述）。
3. **测试结果不流进学习。** 终测只产出最终成绩，学习相位读到终测答卷会直接报错，
   避免用测试题调参。
4. **可恢复、可审计。** 每完成一个单元（一次执行、一次诊断、一次改写）立刻写盘；
   同一条命令重跑即续跑；配置、任务表、全部 prompt 和提示词包在 run 开始时冻结，
   之后任何不一致都会拒绝启动。

---

## 二、一次实验长什么样

### 2.1 配置：方法 = 相位序列

一份 YAML 配置就是一次实验的全部身份。核心是两块：`sequence`（相位名单）和
`phases`（每个名字对应的相位定义）。参考方法的精简版如下（省略了 token 预算、
任务表等字段）：

```yaml
schema_version: 9
benchmark: alfworld              # alfworld / searchqa / tb
models:
  executor_strong: <强模型名>
  executor_weak:   <弱模型名>    # 其余角色（segmenter、author、rewriter、judge）缺省用强模型
n_attempts_strong: 3             # 摸底阶段每题采样次数
n_attempts_weak: 3
split: {train: [...], val: [...], test: [...], reserved: [...]}   # val、reserved 可省略

sequence: [baseline, learn, practice, learn, practice, learn, test]
phases:
  baseline:
    kind: execute
    role: baseline
    tasks: train
    delivery:  {name: none}
    execution: {name: real, arms: [executor_strong, executor_weak]}
  learn:                         # 三个学习相位共用这一个定义
    kind: learn
    review:  {name: review, K: 5, retry_limit: 2, write_lessons: true}
    author:  {name: subtask_cards}
  practice:
    kind: execute
    role: practice
    tasks: train                 # 或 val：在验证集上练习
    delivery:  {name: folder, description_mode: summary_then_when_to_use}
    execution: {name: real_folder, attempts: 3}
    playbook:  evolved           # 执行者看到上一个学习相位演化出的 playbook；缺省 "off"
  test:
    kind: execute
    role: test
    tasks: test
    delivery:  {name: folder}
    execution: {name: real_folder}
    playbook:  evolved
    compare:   {against: fresh_bare}   # 对照：同一批测试题不带技能、不带 playbook
```

三个学习相位的定义相同、做同样的五步；第一个学习相位库里还没有卡，所有定位到子任务的
失败都是缺口。`baseline` 只能是序列第一项，有学习或练习相位的序列必须以它开头；只有
`test` 的序列（终测导入的外部技能库、裸跑）不需要摸底。字段含义、加载期检查清单和所有
可选项见 [docs/design_spec.md](docs/design_spec.md) 第二节。

论文用到的配置全部由 `scripts/run/build_paper_configs.py` 生成，文件名是
`<bench>_paper_<exp>_<arm>.yaml`。想做一组新的对照，改生成脚本、给新臂起新名字
（已经跑过正式 run 的配置保持原样）。

### 2.2 技能库怎样交给弱模型

`delivery`（交付）决定每道题看到哪些技能：

| 名字 | 做法 | 用在 |
|---|---|---|
| `none` | 不注入，裸跑 | 摸底、终测的现场对照臂 |
| `whole_bank` | 在用的全部技能作为一段规则注入 prompt；`verbatim: true` 时逐字节注入（用于导入的外部技能文档） | 终测 |
| `folder` | 渐进式披露：每条技能是一个 `SKILL.md` 文件，prompt 里只有目录（名字 + 触发描述），模型需要时打开 | 练习（必须）、终测 |

`folder` 形态与 Agent Skills 的使用方式一致：弱模型自己判断哪条技能适用，
所以一条技能的触发条件写得好不好，会直接决定它有没有被打开。

### 2.3 画像与 playbook

每个学习相位都写出新版画像和 playbook：

- **画像**从空文本开始，每次用本相位诊断里关于弱模型弱点的笔记更新；写卡、改卡和写
  playbook 时都读最新的画像；
- **playbook** 从提示词包的初始文本开始（可以为空），第一次学习时还读一份从摸底答卷
  统计出的事实，之后读本相位的触发证据计数（哪类卡在失败里出错前没被打开、几次），
  并用诊断里"所有任务都缺什么做法"的笔记更新；写出的 playbook 必须通过 benchmark 规定的
  必备部分检查。

执行相位用 `playbook: evolved | off` 决定执行者是否看到上一个学习相位的 playbook，
练习和终测分别配置。ALFWorld 和 SearchQA 把 playbook 放在技能目录上方；TB 把它放进
技能文件夹的 skill-read 后缀前面，所以 TB 只在 `folder` 交付下显示 playbook。
论文配置里凡是带学习相位的臂，练习和终测都显示 playbook；对照臂不带。已知 playbook 会
降低技能被打开的比例，所以带库臂与对照的差值同时包含技能库和 playbook 两部分的作用。

### 2.4 一次 run 留下什么

```
runs/<实验名>_<时间>/
  manifest.json            冻结的配置、任务表、prompt 摘要、提示词包
  pipeline_log.jsonl       每个相位完成时写一行
  failed_units.jsonl       失败单元的记录
  phases/pNN_<相位名>/     每个相位自己的答卷、账本、诊断、证据、画像、playbook 与技能库快照
  stage1_baseline/ stage2_segments/ stage3_generation/
                           跨相位共享的对象库（摸底与练习的轨迹、段、子任务类型表、技能）
  llm_requests/            每次模型调用的请求与回复，usage.jsonl 是用量账本
```

最终成绩在最后一个 `test` 相位目录的 `phase_summary.json` 里，用 `expedistill summary`
查看进度与用量。完整布局见设计正本第四节。

---

## 三、模块与接口边界

### 3.1 模块

```
执行相位：技能库 + playbook → 交付 → 执行 → 验收 → 答卷
学习相位：失败答卷 → 诊断 → 定位到子任务类型 → 分配证据 → 演化画像与 playbook → 写卡 / 改写 / 删除 → 新技能库
```

| 模块 | 输入 → 输出 | 配置里怎么选 |
|---|---|---|
| 交付 | 技能库 + 题目 → 执行单元清单（一道题 + 注入的技能） | `delivery`、`playbook` |
| 执行 | 执行单元清单 → 运行记录（每次运行一条） | `execution` |
| 验收 | 运行记录 → 每次运行的判定（success / failure / invalid） | `acceptance`（判分规则名） |
| 学习五步 | 失败答卷 + 当前技能库 → 新技能库、画像、playbook | `review`（切段与写卡材料的参数）、`author`（写卡方法 `subtask_cards` 及其参数） |

### 3.2 交接物

相位与相位之间只传四样东西：**技能库**、**运行记录**、**答卷**、**画像与 playbook**。
执行单元清单、诊断记录、证据记录只在相位内部流转。这条边界让每个模块可以单独替换、
单独测试：换一种交付方式，不影响学习；换一个 benchmark，不影响学习相位。

### 3.3 数据对象

全部以 JSON 落盘，对象之间用外键关联，ID 由程序按确定规则生成（模型输出里的编号
不当 ID 用）：

| 对象 | 含义 |
|---|---|
| `Trajectory` | 一次完整执行的逐步记录（动作、观察、判分器结果）；编号带产生它的摸底或练习相位 |
| `Subtask` | 轨迹切出的一段，写卡与定位的基本单位 |
| `SubtaskType` | 子任务类型表的一行（名字 + 可观察的起点状态），表只增不改 |
| `Skill` | 一个子任务类型的一张卡：触发条件 `summary` + `when_to_use`，正文 `steps`（步骤列表）+ `not_applicable_when`，带版本号与状态（`active` / `discarded`）；导入的外部技能另存原文 `verbatim_text` |
| `EvolvingText` | 一个学习相位写出的一版画像或 playbook |

每个技能版本都带来源记录，指向它背后的证据位置（写卡材料段的起点、失败里出错的那一步）。

### 3.4 方法驱动：四族可替换件

学习与执行的每个槽位都是一个"族"，每族有若干具名方法，登记在
`src/expedistill/methods/registry.py` 的一张纯数据表里：

| 族 | 现有方法 |
|---|---|
| `delivery` | `none` `whole_bank` `folder` |
| `execution` | `real` `real_folder`（参数 `executor`、`attempts`） |
| `review` | `review`（参数 `K`、`retry_limit`、`write_lessons`） |
| `author` | `subtask_cards`（参数 `typing_chunk_size`、`max_segments_per_card`、`max_chars_per_segment`、`write_retry_limit`） |

加载配置时，每个方法名与参数都会被校验：名字拼错、参数类型错误会在任何文件被写入
前停下。参数说明在 `src/expedistill/methods/delivery/README.md`、
`src/expedistill/methods/execution/README.md` 与设计正本第二节。

**新增一种方法**：在对应族里写一个驱动和它的参数类，在注册表加一行，配置里写名字。

### 3.5 Benchmark 插件与提示词包

所有与具体 benchmark 有关的代码都在 `src/expedistill/benchmarks/<名字>/`，
上层代码对 benchmark 一无所知。一个插件实现四件事：

| 接口 | 作用 |
|---|---|
| `load_tasks(spec)` | 加载题目，返回 `Task`（ID、说明、插件自用的 `meta`） |
| `run(task, handle, skills_block, workdir)` | 执行一道题一次，返回 `RunOutcome{steps, correctness, meta}`；`correctness` 取 `True` / `False` / `None`；沙箱超时、断网等基础设施故障抛 `InfrastructureError`，由框架重试 |
| `view_rules` | 轨迹给模型看时的截断规则（截断必须显式标注） |
| `task_prompt_name` | 执行 prompt 在注册表里的名字 |

另外每个插件带一个**提示词包**（`benchmarks/<名字>/prompt_pack.py` 的 `PROMPT_PACK`）：

| 字段 | 作用 |
|---|---|
| `vocabulary` | 学习提示词里的 benchmark 词汇：智能体是谁、能力族、最终动作、可观察的信号、段标签示例等 |
| `protocol` | 给写作者的环境事实：执行者每一步看到什么、技能怎样打开 |
| `executor_prompts` | 执行提示词的注册名 |
| `playbook_seed` | playbook 的初始文本 |
| `playbook_facts` | 从摸底答卷计算统计事实的函数（可以没有） |
| `playbook_checks` | playbook 必须包含的部分 |
| `overrides` | 用本 benchmark 自己的提示词替换某个方法提示词（例如 `PLAYBOOK_UPDATE`） |

调整提示词包，就能改变技能、画像、playbook 的写法，以及执行者看到的内容。

可选能力：声明 `skill_folder_prompt_name` 即支持技能文件夹交付；声明 `aux_metric_name`
提供一个并列报告的辅助指标；`verdict_rule` 指定缺省判分规则。新增 benchmark 不需要修改
`blocks/` 及以上的任何代码。步骤见 [AGENTS.md](AGENTS.md) 第七节。

### 3.6 模型与 Prompt

- 调用模型的代码只接收一个"模型句柄"，不自己选模型；哪个角色用哪个模型由配置里的
  `models` 角色表决定（`executor_strong`、`executor_weak` 必填）。
- 所有方法 prompt 登记在 `src/expedistill/prompts/`，benchmark 的执行提示词和替换提示词
  登记在各自的插件目录，全部按名字取用，摘要写进 manifest。改一个字就是新实验，必须
  新建 run 目录。
- 模型服务通过环境变量接入，兼容 OpenAI 接口；`EXPEDISTILL_LLM_PROVIDER=fake`
  可切换到按脚本回复的假模型，用于零网络冒烟。

### 3.7 代码分层

```
core → infra / prompts / inject → llm → benchmarks → blocks → composites → methods → stages → cli
```

只能向下依赖，`tests/test_layering.py` 静态检查。`blocks/` 是做一件事的功能积木，
`composites/` 负责落盘与续跑，`methods/` 是可替换的方法与学习步骤，`stages/` 把它们
串成相位，`cli.py` 是唯一入口。详见 [AGENTS.md](AGENTS.md) 第四节。

---

## 四、可靠性约定

| 约定 | 内容 |
|---|---|
| 冻结 | 配置、任务表、prompt 摘要、提示词包在冻结步写入 `manifest.json`；之后每次启动逐字段比对，不一致即拒绝 |
| 逐单元落盘 | 一次执行、一次诊断、一次切段、一次写卡或改写完成就写盘（原子写）；已完成的单元续跑时跳过 |
| 排他锁 | 一个 run 目录同时只允许一个进程，第二个进程报错并给出持有者 PID |
| 失败分类 | 基础设施故障按策略重试；模型输出格式错误按次数修复；配置错误与代码缺陷立即停止 |
| 无效重派 | 摸底与练习相位里派发了却什么都没存下的采样重新派发，次数上限为配置项 `invalid_redispatch_limit`；已存但没拿到判分结果的运行判为无效，不重跑 |
| 执行相位不互相复用 | 每个练习都在当时的技能库与 playbook 下重新执行全部题，终测每次全量重跑 |
| 并行不改结果 | 学习相位的模型调用并行执行，账本按确定顺序提交，`--workers 1` 与 `--workers 8` 的产物逐字节相同 |
| 用量累加 | token 用量跨进程累加落盘；拿不到用量记 unknown，不记 0 |
| 只读当前版本 | 配置只接受 schema 9；不兼容的旧配置用生成脚本重新生成 |

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
| `EXPEDISTILL_ALFWORLD_EVAL_DIR`、`EXPEDISTILL_ALFWORLD_VENV`、`ALFWORLD_DATA`、`ALFWORLD_CONFIG`、`ALFWORLD_BENCH_SRC` | 跑 ALFWorld 时需要 |
| `EXPEDISTILL_SEARCHQA_EVAL_DIR` | 跑 SearchQA 时需要 |
| `EXPEDISTILL_TB_*` | 跑 TerminalBench 时需要，见 [scripts/remote/README.md](scripts/remote/README.md) |

### 5.4 启动一次实验

`configs/experiments/` 里有现成的配置。例如 ALFWorld 上的参考方法：

```bash
set -a; . ./.env; set +a
.venv/bin/expedistill run \
  --config configs/experiments/alfworld_paper_e1_ours.yaml \
  --run-dir runs/alfworld_paper_e1_ours_$(date +%Y%m%d_%H%M) \
  --workers 16
```

常用运行设置（只影响怎么跑，不进配置、不进 manifest）：

| 参数 | 作用 |
|---|---|
| `--workers` | 只调用模型端点的步骤（诊断、切段、归类、写卡、改写）的并行数 |
| `--rollout-workers` | 真实环境执行的并行数，缺省与 `--workers` 相同 |
| `--until-phase N` | 只跑到序列第 N 项（从 0 数）后停下，用于试跑；`-1` 只做冻结 |
| `--allow-infra-shortfall` | 接受耗尽重试的基础设施空洞，继续往下跑 |

**先小后大**：正式实验前，先把一份配置的任务表缩到 1–2 道题，用 `--until-phase`
逐相位跑通，确认输出结构正确、对照组确实是对照组，再启动全量。

**复用已有的摸底**：摸底（强、弱两臂的裸跑）最贵，同一组模型与训练题不必重跑。在新 run
第一次 `run` 之前执行：

```bash
.venv/bin/expedistill import-baseline \
  --source-run <已有 run 目录> \
  --config configs/experiments/alfworld_paper_e1_ours.yaml \
  --run-dir <新 run 目录>
```

它核对 benchmark、两个执行模型名、两臂采样次数一致，新配置的训练题都在源 run 里且每个
（任务、角色、序号）恰好一条，不过任何一条就报配置错误、什么都不写；通过后把源 run 的摸底
轨迹改写编号写进新 run，并写 `BASELINE_ORIGIN.md` 记录来源与核对结果。之后照常 `run`，
摸底相位零模型调用。

长时间实验建议用 `scripts/run/run_experiment.sh <配置名>`：它加载 `.env`，把 driver
脱离当前终端启动，并写出 pidfile 与续跑命令。判断任务是否还活着请读 pidfile，
不要用 `pgrep -f`。TerminalBench 的正式实验在基准主机上运行，步骤见
[scripts/remote/README.md](scripts/remote/README.md)。

### 5.5 续跑与查看

```bash
# 中断后，用同一条命令续跑
.venv/bin/expedistill run --config <同一份配置> --run-dir <同一个 run 目录> --workers 16

# 查看进度与用量
.venv/bin/expedistill summary --run-dir <run 目录>
```

退出码：`0` 正常完成；`1` 需要人处理（配置错误、manifest 不一致、目录被锁、模型输出
错误）；`3` 基础设施耗尽，直接续跑即可；`2` 命令行用法错误。

跑完后先做完整性检查：`expedistill summary` 加设计正本第七节列出的人工核对项，全部通过后
再使用其中的数字。

### 5.6 分析、对比与准备

| 命令 | 用途 |
|---|---|
| `paper-tables` | 多个 run 之间的配对比较：按任务自助法置信区间、胜负平、McNemar 检验 |
| `judge-skills` | 终测或练习里每次运行的技能使用情况：该用的有没有打开、打开后有没有照做 |
| `segmentation-metrics` | 切段粒度消融的机制指标 |
| `import-baseline` | `--source-run <已有 run> --config <新配置> --run-dir <新 run 目录>`：把已有 run 的摸底轨迹导入新 run（见 5.4） |
| `import-bank` | 把其他方法产出的技能（markdown、技能文件夹、笔记）导入为一个测试专用 run，在同一终测流程下比较 |
| `fork-run` | 复用已完结 run 的前若干相位，从下一个相位开始换配置接着跑 |

分析命令只读已完结的 run，结果写到 run 目录之外。各命令的参数见
`expedistill <命令> --help`，用途与统计口径见设计正本 3.9 与第七节。

### 5.7 设计自己的方法

1. 在 `scripts/run/build_paper_configs.py` 里照着现有的臂加一个新臂，或复制一份相近的
   配置并改文件名；
2. 调整 `sequence` 与 `phases`：增删练习与学习相位、练习用 `train` 还是 `val`、
   换终测的交付方式、改 `review` 的 `K` / `write_lessons`、改 `author` 的参数、
   开关练习和终测的 `playbook`、改 `rules` 的段筛选与轨迹挑选；
3. 需要新的行为时，按 3.4 在对应族里新增方法；需要新 benchmark 时，按 3.5 写插件和
   提示词包；
4. 小样本干跑，再全量。

`build_paper_configs.py --print-index` 会打印每个对照臂与参照臂相差的配置键，
`--check` 确认磁盘上的配置与生成结果一致。

---

## 六、已知局限

1. **playbook 降低技能打开率。** 带 playbook 时弱模型打开技能的比例明显下降，
   playbook 逐相位演化后可能更长；触发证据会因此堆积，改触发条件未必扭得动。
2. **证据稀疏。** 带 playbook 的弱模型在训练题上失败不多，每轮可读的失败有限，演化慢；
   本地 benchmark 可以加大练习的 `attempts`。
3. **出错步由大模型定位。** 定位错了，证据会分给错的卡；每条诊断（`findings.jsonl`）与
   证据（`evidence.jsonl`）都落盘，可以事后复核。
4. **样本内。** 在训练题上练习时，学习读的就是写卡用的题，泛化只在终测体现；在验证题上
   练习可以减轻。
5. **最后一次学习的改写不再执行**，直接带进终测，效果只在终测里测得出。

更多讨论见设计正本"已知风险"一节。
