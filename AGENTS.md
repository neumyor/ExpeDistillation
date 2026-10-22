# AGENTS.md

本仓库的目录结构、开发规范与工作记录规范。系统设计（积木清单、数据模型、
阶段流程）见 [README.md](README.md)；外层工作区的通用规范（长任务前的检查清单、
测量与结论规范、远程资源）见 `../../../AGENTS.md`，本文件不重复，两者冲突时
以更严格的为准。

---

## 一、根目录结构

```
ExpeDistillation/
  AGENTS.md          本文件：目录、规范
  README.md          系统设计（唯一的设计正本，改设计先改这里）
  log.md             工作日志：每个阶段的工作记录（见第二节）
  pyproject.toml     包定义、依赖、pytest / ruff 配置
  .gitignore
  .env.example       需要的环境变量清单（只有变量名和说明，不含值）
  configs/           实验配置（进 git）
  src/expedistill/   全部方法代码（见第四节）
  tests/             测试（见第八节）
  scripts/           运行、远程、分析脚本（见第五节）
  docs/              过程中产出的文档工件（见第三节）
  runs/              本地实验产物（不进 git，见第六节）
  tmp/               临时草稿、一次性调试输出（不进 git，随时可删）
```

根目录下只允许出现上面这些条目。新增顶层目录或文件要先改本节。

## 二、工作日志 log.md

**每完成一个阶段的工作就在 log.md 末尾追加一条**，按时间顺序排列，旧的在上、
新的在下，已写的条目不改（写错了另追加一条更正，注明更正哪一条）。

"一个阶段"指一段有明确产出的工作：一轮设计修订、一次迁移、一块积木实现完、
一次 smoke、一次正式实验启动 / 结束、一次审计、一次 review。纯问答、没有
产出的不记。

每条的格式：

```
## YYYY-MM-DD HH:MM — 标题（一句话说清做了什么）

一段话摘要：做了什么、为什么做、结论或决定是什么、还有什么没做完。
实验类条目要写清 run 目录、配置名、是否通过完整性审计；数字只写审计过的。

工件：`相对路径`（说明）、`相对路径`、commit `abc1234`、run `runs/<name>/`
```

规定：

1. 时间写**本机时间，精确到分钟**，取该阶段完成的时刻；
2. 摘要是一段话，不分点，写给一周后的自己看，不用代号和缩写；
3. **工件必须列出**：这一阶段新建或修改的文件、相关提交、run 目录、docs 下的
   文档。没有工件的工作不算一个阶段；
4. 用户做出的设计决定要写进摘要，并说明决定的是什么（不只写"用户确认"）；
5. 失败、作废、撤回的结论同样记录，写清原因（对应外层 AGENTS.md 第四节）。

## 三、文档工件 docs/

过程中产出的文档都放在 `docs/` 下，按用途分子目录：

```
docs/
  decisions/   设计决定：一个决定一个文件。写清背景、候选方案、选了哪个、理由
  reviews/     设计 review、代码 review、审计报告
  reports/     实验报告、结果分析（必须引用 run 目录和审计结果）
  runbooks/    操作手册：怎么部署、怎么启动、怎么查进度、出问题怎么处理
  notes/       调研笔记、论文阅读笔记、与其他系统的对比
  archive/     过时的文档（移入而不是删除，文件开头注明过时原因和替代文档）
```

规定：

1. **文件名**：`<主题>_<YYYYMMDD>.md`，主题用英文小写加下划线，例如
   `docs/reviews/design_review_20261010.md`。同一主题同一天有多份时加
   `_v2`；
2. **文件开头**写三行：日期、一句话说明这份文档是什么、它对应 log.md 的哪一条；
3. README 是设计正本，docs 下的文档是过程记录。决定一旦落地，结论要回写
   README，docs 里保留讨论过程，两者不能矛盾；
4. 每份新文档都要在 log.md 对应条目的"工件"里出现；
5. 文档用中文写；代码标识符、文件名、路径保持原样。

## 四、源码目录 src/expedistill/

```
src/expedistill/
  core/                   第 0 层：只依赖标准库
    models.py             五个对象：Trajectory / Subtask / Skill / Exam / Attempt
    ids.py                确定性 ID 规则
    config.py             实验配置结构（schema v5：sequence + phases + use_weak_profile 开关）、模型角色表
    config_upgrade.py     v2→v3→v4→v5 升级纯函数（config.py 重新导出）
    contracts.py          执行—学习循环的四个交接数据类（执行单元行 / 运行记录 /
                          验收报告行 / 任务清单行，R0 冻结的接口形状）
    registry.py           通用具名注册表（prompt、规则、benchmark 插件共用）
  infra/                  第 1 层：与业务无关的运行支撑
    store.py              B3 对象存储（原子写、按 ID 读、判断单元是否完成）
    manifest.py           B4 冻结
    parallel.py           B5 并行池
    reliability.py        B6 异常分类、重试策略、单元失败记录
    runlock.py            B7 run 目录排他锁
    usage.py              B7 用量跨进程累加
  prompts/                第 1 层：B2 prompt 注册表
    registry.py           登记、取用、计算 digest
    llm.py                 B1 自己的协议 prompt（默认 system 消息等）
    segment.py            G1 切段
    author.py             G2 提炼 / 诊断、G6 改写
    exam.py               G3 出考题、G4 作答、G5 判卷
    curate.py             G7 找相似、G8 合并、G9 分组
    attribution.py        G10 live 归因、G11 live 修订（v3 起进
                          __init__ 的 import 清单——全量冻结，manifest 冻结全部
                          已注册方法 prompt 的 digest）
  inject/                 第 1 层：D9 skill 注入（纯函数，执行和作答共用）
  llm/                    第 2 层：B1 调用模型
    client.py             底层客户端（OpenAI 兼容接口）
    providers.py          provider 注册表（纯数据表：名字 → builder；环境变量选择）
    call.py               call_model：取 prompt → 请求 → 解析 → 格式错误重试 → 记账落盘
    fake.py               测试用假模型（按脚本返回固定内容）
  benchmarks/             第 3 层：benchmark 插件
    base.py               插件接口：load_tasks / run / view_rules / task_prompt；
                          可选能力声明 skill_folder_prompt_name（技能文件夹
                          执行 real_folder，M3；None=不支持）
    react_loop.py         本地 benchmark 共用的逐步 ReAct 执行循环
    aux.py                辅助指标的插件接线（声明与打分器包装）
    registry.py           按名字取插件
    tb/                   TB runner（远程 Harbor）+ 任务加载 + 截断规则 + 执行 prompt
    searchqa/             适配外层 benchmark/searchqa-eval/
    alfworld/             适配外层 benchmark/alfworld-eval/
  blocks/                 第 4 层：功能积木，一个文件一组相关积木
    execute.py            E1：按 benchmark 取插件执行
    tasks.py              D1 任务加载、D2 划分
    metrics.py            D3 成绩统计、D4 挑题
    rules.py              D4 / D6 / 轨迹挑选的具名规则
    view.py               D5 轨迹视图
    segment.py            G1 切段、D7 切段校验
    select.py             D6 段筛选
    author.py             G2 写 skill、G3 出考题、G6 改写 skill
    exam.py               G4 作答、G5 判卷
    audit.py              D8 泄题审计
    curate.py             G7 找相似、G8 合并、G9 分组（D10 库长度统计在 inject/skills.py）
    retrieve.py           D11 skill 检索：词法两步检索纯函数（类别路由 + 类内 top-k）
    attribution.py        G10 live 归因、G11 live 修订（live_run 验收的模型积木；
                          prompt 注册由 prompts/__init__ 全量负责，无需防御性 import）
  composites/             第 5 层：组合积木（负责落盘与续跑）
    batch_execute.py      K1
    segment_checked.py    K2
    exam_answering.py     K3 作答部分（R0 拆分：序列号、同题锁、作答落盘）
    exam_grading.py       K3 判卷部分（R0 拆分：跨崩溃持续的判卷修复预算）
    exam_once.py          K3 薄接线：作答 + 判卷拼成一条 Attempt
    acceptance_leak.py    K4 泄题判定（R0 拆分：D8 判定与 leak_verdicts 账本；
                          exam 相位驱动 phase_execute 调用）
    acceptance_skill_status.py  从存量记录读出 skill 状态、考题的已判作答
                          序列与裸答（报告读取器与 exam 相位驱动共用）
    curation.py           K5
    curation_waves.py     K5 每轮的单元规划（纯函数：分波、去重、派发编号）
    live_measurement.py   K6 测量部分（R0 拆分：live_run 路径与账本名、组推导、
                          基线读取、注入审计行、幸存组推导、轨迹视图的
                          预算裁剪）
    live_verdicts.py      K6 判定部分（R0 拆分：轮判定、no-op 守卫、组终态提交；
                          discard_reason 词表的 K6 侧再导出在此；live 相位驱动
                          phase_execute 经它写判定账本）
    decision.py           决策规则（M3 起的纯函数）：相位世界统一决策入口
                          decide_task_list 与 4.2/4.3 规则表；discard_reason
                          词表与 strict 短路规则集的定义处
    reports.py            报告读取器（M3 起的纯函数，只读）：baseline/exam/live 三件
    shortfall.py          接受空洞的落盘记录（摸底相位的记录在 run 根的
                          stage1_baseline/ 下，live 基线加载与 test 相位的
                          复用对照都读它）
    skill_status.py       skill 状态提交（accept/discard）的唯一入口
    external_control.py   外部 campaign 对照的加载与校验（sha256 钉住、任务
                          覆盖、对齐深度的奖励切出）；test 相位
                          against: external_campaign 时对照臂由此取数，零执行
    skill_reads.py        folder 单元的事后读取统计（M4 纯函数）：从存储
                          轨迹解析实际 cat 过的技能文件，benchmark 无关
    skill_usage.py        live folder 轮次的逐尝试使用事实：可用技能、首轮
                          <available_skills> 是否与清单一致、读取/未读、
                          结果；从存储轨迹重算，不拦截执行
    verdicts.py           R1 起的验收判定核：状态/原因词表、具名判分规则、
                          统一报告行
    phase_bank.py         R7 每个 learn 相位结束时的库工件（写与跨相位加载）
    skill_folder.py       folder 单元清单的幂等物化（M2/M3）：按清单的库指纹
                          从相位库读正文，把每个技能写成 `<skill>/SKILL.md`
                          （带 YAML 头）并写 catalog.md 兜底；test 相位派发前
                          物化一次全部单元共享，live 的 `folder_by_task` 按
                          任务各物化一份
  methods/                第 6 层：方法驱动与参数契约（纯数据表注册，
                          见 methods/registry.py；解析按需 import 驱动）
    params.py             通用参数构建器（未知键/类型/缺键全响亮失败）
    registry.py           (族, 名字) → (驱动, 参数类) 的纯数据表（R8 五族：
                          delivery / execution / evidence / author / curation）
    execution/            execution 族参数契约：RealExecutionParams /
                          ExamExecutionParams / RealFolderExecutionParams
                          （无驱动，相位驱动直接消费）+ README.md（模块说明与参数表）
    evidence/             evidence 族：SegmentationParams / LiveGroupParams /
                          UsageGroupParams + 采集器 add_path（加分路径）、
                          live_group（G10 事实）、usage_group（读取与
                          应用事实）、exam_sheet（考卷答卷材料）+ segmentation.py
                          （segment 方法驱动：add 行的 K2 切段接线，切强角色
                          轨迹，write_lessons 开着时连弱角色一起切）+ README.md
                          （模块说明与参数表）+ package.py
                          （证据包形状与只报事实守卫）
    author/               author 族：AuthoringParams / UsageReviseParams +
                          write 驱动（写新 skill / 组修订 revise_group）+
                          usage_revise 驱动（按使用事实 keep / retrigger /
                          revise / delete / observe）+ README.md
                          （模块说明与参数表）
    curation/             库整理驱动 agentbrew.py、none.py（只作用于已接受集，
                          预算硬阀）+ phase_bank.py（R7 相位库入口：工件携带全部
                          成员，待定成员原样透传）+ Params + README.md（模块说明与参数表）
    delivery/             注入驱动 whole_bank.py、retrieval.py、per_skill.py、
                          group_by_task.py、none.py、folder.py（test 技能文件夹
                          交付）、folder_by_task.py（live 逐任务技能文件夹）+
                          各自的 Params + README.md（模块说明与参数表）
  stages/                 第 7 层：相位世界的流程（薄分发 + 写摘要/相位末抛错）
    stage0_freeze.py      冻结步（p00）：配置、任务表、manifest 冻结
    phase_execute/        执行相位驱动包（R9/R8）：__init__ 分发入口 run()；
                          phase_disk 相位目录/早相位/库产物/复用连接；resolve
                          配置映射到驱动与参数；engine 派发→判分→重派引擎；
                          baseline / exam / live / final_test 四种 role 各一
                          个模块（baseline 摸底 / exam 纸面验收 / live 实战验收 / test 终测）
    phase_learn.py        learn 相位驱动（R9）：决策 → 证据 → 写作 → 整理
    learn_tasks.py        learn 相位的逐行落地（keep/delete/revise/add 与
                          断点续跑账本；add 行先经 evidence 族的 segment 驱动
                          切段——相位世界没有独立的切段相位）
    context.py            StageContext / RunSettings（相位驱动的公共上下文）
    assemble_reuse.py      装配工具（不占 K 号）：按 train 溯源从源 run 装配新 run；
                          上移到本层是为了复用 stage0 的 build_manifest（组合层
                          不得 import 阶段层）
  cli.py                  第 8 层：命令行入口（唯一的方法入口）+ 相位循环
                          （run_experiment 逐个派发 sequence 条目）
```

积木编号与文件的对应以 README 第三节为准；新增积木时两处同时更新。

### 分层规则

| 层 | 目录 | 可以依赖 |
|---|---|---|
| 0 | `core` | 只有标准库（唯一例外：`config.py` 的 YAML 读写，函数内按需 import pyyaml） |
| 1 | `infra`、`prompts`、`inject` | 第 0 层 |
| 2 | `llm` | 第 0–1 层 |
| 3 | `benchmarks` | 第 0–2 层 |
| 4 | `blocks` | 第 0–3 层 |
| 5 | `composites` | 第 0–4 层 |
| 6 | `methods` | 第 0–5 层 |
| 7 | `stages` | 第 0–6 层 |
| 8 | `cli` | 全部 |

1. **只能向下依赖**，函数内的 import 也算。`tests/test_layering.py` 静态检查，
   不通过不得提交（沿用 SkillExpand 的同名测试）。
2. **同层之间**：第 1 层的三个目录互不依赖；`blocks/` 下的文件互不 import——
   两块积木要接在一起，就在 `composites/` 里接；`benchmarks/` 下的各插件互不依赖。
3. **跟 benchmark 有关的代码只能在 `benchmarks/<名字>/` 里**。`blocks/` 及以上
   不得出现 `if benchmark == "tb"` 之类的分支，需要的差异做成插件接口的一项。
4. `src/` 不得 import `scripts/` 下的任何东西。

### 积木规范

1. **签名**：一块积木是一个函数（或一个只有一个公开方法的类）。输入输出是
   `core/models.py` 的对象、对象列表或纯文本；配置以显式参数传入，不读全局状态。
2. **模型是参数**：调模型的积木接收一个模型句柄，不在内部选模型，也不出现
   "strong" / "weak" 字样的分支。用哪个模型由 `stages/` 从角色表里取。
3. **不读写磁盘**：功能积木只做计算。落盘、续跑、加锁由 `composites/` 和
   `stages/` 负责。唯一例外是 `benchmarks/` 插件的 runner（执行本身就要碰
   环境和远程目录），它产生的文件必须放在调用方传入的目录里。
4. **模型调用只走 `llm.call.call_model`**：积木里不直接 import 客户端、不拼 HTTP
   请求、不写 prompt 字符串，prompt 一律按名字从 `prompts/` 取。
5. **ID 由程序生成**：按 `core/ids.py` 的规则，子对象 ID = 父对象 ID + 序号。
   模型输出里的任何编号都不能当 ID 用。
6. **模型输出要校验**：调模型的积木声明输出格式，`call_model` 负责解析和格式
   重试；格式之外的业务校验（如切段覆盖全轨迹）写成独立的程序积木（如 D7），
   不混在模型积木里。
7. **可替换的规则做成具名规则**：放进 `blocks/rules.py` 的注册表，配置里只写
   名字。改规则 = 新增一条具名规则，不改已有规则的行为。

### 代码风格

1. Python 版本与 SkillExpand 一致（`>=3.9,<3.12`），依赖在 `pyproject.toml` 锁定版本；
2. 标识符、注释、docstring、提交说明用英文；注释密度与周边代码一致，只解释
   "为什么"，不复述代码；
3. 不留注释掉的代码、不留无主 TODO（要做的事记进 log.md 或 docs/decisions）。

## 五、脚本 scripts/

**方法逻辑只在 `src/` 里，入口只有 `cli.py`。** `scripts/` 只放调用 CLI 或
读取产物的薄脚本，不实现任何积木、不复制 `src/` 里的逻辑。

```
scripts/
  run/        启动实验：按配置调用 cli，例如 run_experiment.sh <配置名>
              （负责加载环境变量、起 supervisor、写 pidfile，不含方法参数）
              freeze_tb_split.py：从 AgentBrew 冻结 JSON 生成并校验配置三列表
              convert_v3_configs.py：一次性 schema v3→v5 配置转换（写新文件名；
                                  显式传入 v2 文件时在内存里链式走 v2→v3→v4→v5）
              launch_tb21_live_run.py：正式实验的 detached 启动器
  remote/     远程（node29）相关：部署代码、同步产物、查看进度、停止任务
  analysis/   读 run 目录产出统计和图表，只读不写 run 目录，结果写到 docs/reports/ 或 tmp/
  audit/      run 完整性审计（重复执行、对象一致性、统计口径），见第六节
```

规定：

1. **一次性的调试脚本放 `tmp/`**，不进 git；用过两次以上、或结论依赖它的，
   整理后移入 `scripts/` 对应子目录；
2. 脚本文件名用英文小写加下划线，开头注释写清用途、输入、输出、示例命令；
3. 方法参数（模型、K、`retry_limit` 等）只写在 `configs/` 里，脚本不得硬编码
   或覆盖方法参数；脚本只管运维参数（并发、超时、路径）；
4. 长任务的启动方式按外层 AGENTS.md 第一节：用 `os.setsid()` /
   `start_new_session=True` 起 supervisor，追踪自己 spawn 的 child PID，
   **不用 `pgrep -f`** 判活；从新的 shell 确认任务还活着；
5. TB 远程链路以外层 `scripts/tb21/` 为参考实现，`scripts/remote/` 调 Harbor
   的方式（镜像、重试名单、超时参数）与其保持一致，有差异要在脚本开头注明。

## 六、配置与产物

### configs/

```
configs/
  experiments/   每个实验一份配置：benchmark、模式（split / full）、模型角色表、
                 采样次数、相位序列（sequence + phases，方法参数全部在相位
                 定义里）、无效重派预算、具名规则的名字；
                 schema_version 5（v1/v2/v3/v4 文件是冻结的历史记录，转换走
                 scripts/run/convert_v3_configs.py，结果写新文件名）
  profiles/      弱模型画像（一个弱模型一份文本）
  seed/          外部 campaign 的冻结成绩文件（外部对照的取数来源，
                 字节级拷贝 + sha256 由实验配置钉住；本仓库不修改它，
                 campaign 侧重导出后按新 sha256 换新文件名）
```

1. 一份配置对应一个实验，文件名即实验名：`<benchmark>_<mode>_<要点>.yaml`；
2. 已经启动过正式 run 的配置**不再修改**，要改就复制一份新名字；
3. 配置只写方法参数和模型名，**端点地址、密钥从环境变量读**，不写进仓库、
   配置、日志或 manifest。需要的环境变量列在 `.env.example`。

### runs/

1. 本地 run 目录放 `runs/<实验名>_<YYYYMMDD_HHMM>/`，不进 git；远程 run 放
   `/data2/niuyiming/expedistill/runs/` 下，同样的命名。**正式 run 固定在
   它启动时的 checkout 上续跑**（多方法方案 §6，R6）：每个正式 run 一条
   维护分支（命名惯例 `run/<run 目录名>`，热修 cherry-pick 到它上面）+
   独立 venv（editable `.pth` 只指向该 venv 自己的 src）——schema 升版等
   破坏续跑的改动在 stage 0 会被明确拒载并指向该规则；
2. run 目录内部布局见 README 第四节；run 开始时写 manifest，之后每次启动都与
   manifest 逐字段比对，不一致直接拒绝（`infra/manifest.py`）；
3. **逐单元落盘、可续跑**：一次执行、一次切段、一次作答都是一个单元，完成
   一个写一个（原子写：先写临时文件再改名）。再次启动时按确定 ID 跳过已完成单元；
4. **run 目录加排他锁**（`infra/runlock.py`），第二个进程启动直接报错并给出
   持有者 PID；
5. **用量跨进程累加落盘**，续跑后不清零；拿不到用量记 unknown，不记 0；
6. **失败分类处置**（`infra/reliability.py`）：基础设施异常重试、模型输出格式
   错误按次数修复、配置错误立即停止、代码 bug 立即停止整个流程。失败单元单独
   记录，不得混进成功结果，也不得悄悄丢掉；
7. **每个 run 跑完先用 `scripts/audit/` 做完整性审计，再使用它的数字**。
   有问题的 run 一律作废（在 run 目录写 `INVALID.md` 说明原因），不进结果表，
   并在 log.md 记录；
8. **修复只在新 run 目录里重建**，不在有问题的 run 上原地修补；
9. 长任务前的检查按外层 AGENTS.md 第一节：先 1–2 道题跑通全链路、检查不变量、
   核对对照组，再上全量。

## 七、扩展清单

### 新增一个 benchmark

1. 在 `benchmarks/<名字>/` 实现插件接口的四项：`load_tasks`、`run`（单题单次
   执行，返回统一结果 `RunOutcome{steps, correctness, meta}`——`correctness`
   为 `True`（判分器判对）/ `False`（判错）/ `None`（没拿到判分结果）；
   基础设施失败（沙箱超时、断网等）抛 `InfrastructureError`，由 K1 重试；
   Trajectory 的 ID、角色等簿记字段由 E1 组装）、`view_rules`（截断规则）、
   `task_prompt`（执行 prompt，登记进 prompt 注册表）；支持技能文件夹的插件
   再声明 `skill_folder_prompt_name`（指向 folder 模式执行 prompt 的注册名，
   digest 进 manifest；目前只有 tb 声明，folder / folder_by_task 相位落到
   不声明的插件时，test / live 相位驱动在派发前响亮拒绝，E1 的逐单元守卫
   保留作第二道）；
2. 在 `benchmarks/registry.py` 登记名字；
3. 在 `tests/unit/benchmarks/` 加测试：用录好的原始输出测轨迹转换；另加 1 道题的
   真实执行冒烟（标 `live`）；
4. `blocks/` 及以上不改一行代码。如果非改不可，说明插件接口不够用，先改
   接口和 README，再实现。

### 新增一块积木

1. 先在 README 第三节的积木表里登记编号、输入输出；
2. 放进对应目录的文件；调模型的话同时在 `prompts/` 登记 prompt；
3. 写单元测试（调模型的用 `llm/fake.py`）；
4. 在 README 3.5 的"相位 → 积木组装"表里写明哪个相位用它。

### 新增或修改 prompt

1. prompt 是 `prompts/` 下的具名常量，带一句话说明用途和所属积木；
2. 每个 prompt 的 digest 写进 manifest。**改一个字也是新实验**，必须新建 run
   目录，不得在旧 run 上续跑；
3. 输出格式（JSON 字段）与 prompt 放在一起定义，两者一起改。

### 修改数据对象

1. 五个对象的字段只在 `core/models.py` 定义，其他地方不得自造字段；
2. 改字段含义、删除字段、或改变已有字段的类型/必填性/语义都要升
   `schema_version`，并在 README 第四节同步。新增可选字段且带默认值时，
   **如果旧代码读取新数据不会出错**（旧代码会忽略未知键，新代码会补全缺省键），
   则不必升版本号——这减少了每次小改动的连带测试同步成本；
3. 读旧版本对象时要么能正确迁移，要么明确报错，不能静默读成默认值。

## 八、测试：开发过程中如何测试和新增测试

### 8.1 三层测试架构

```
tests/
  unit/          每块积木的单元测试，目录与 src/expedistill/ 对应
  composites/    组合积木测试（假模型 + 临时 run 目录，含中断续跑）
  golden/        回归测试（4 组合 golden 矩阵 + 崩溃续跑验证 + 修订窗口探针）
  fixtures/      构造好的轨迹、段、skill、考题样例；benchmark 原始输出录像；
                 golden 矩阵的 4 组合快照（exam/live × whole_bank/retrieval）
  helpers/       多个测试文件共用的测试工具（插件重新注册、模型回复样例、
                 造 skill），一处定义，契约变了只改这里
  test_layering.py
```

### 8.2 单元测试规范

1. **覆盖要求**：
   - 每块积木至少一个单元测试文件
   - 测试文件按它测的源码放进对应目录、按测试内容命名
   - 不按开发批次命名（不用 `test_w1a_*`、`test_folder_m3_*` 这类名字）

2. **测试实现**：
   - 调模型的用 `llm/fake.py`，不发真实请求
   - 不连远程服务
   - 测试数据从 `tests/fixtures/` 读取，不得从 `runs/` 里直接读

3. **共用代码**：
   - 两个以上测试文件要用的辅助代码放进 `tests/helpers/`
   - 不在各文件里各复制一份

### 8.3 组合测试规范

1. **必须包含续跑验证**：
   - 组合积木的测试必须包含"中途中断再续跑，结果与一次跑完相同"
   - 用假模型 + 临时 run 目录验证多模块协作

2. **覆盖关键路径**：
   - baseline → exam → live → test 全流程
   - 各相位的崩溃点与恢复

### 8.4 Golden 矩阵回归测试

**4 个组合**（从 8 个精简）：
- `exam-whole_bank-full`
- `exam-retrieval-split`
- `live_run-whole_bank-split`
- `live_run-retrieval-full`

**覆盖策略**：
- 每个轴（acceptance/delivery/mode）的两个取值各出现 2 次
- 全部 4 个组合都跑崩溃续跑验证（3 档进度：0.3/0.6/0.95）
- `revision_window.py`：保留"修订已落盘、重答未开始"的特殊崩溃点覆盖

**何时重新生成**：
- 修改提示词后
- 修改配置字段影响端到端行为后
- 修改相位流程后

**如何生成**：
```bash
python tests/golden/generate.py
```

生成后检查：
- 4 个组合的 JSON 文件
- `provenance.json`（记录 base commit 和调用数）
- 运行 `pytest tests/golden -q` 确认通过

### 8.5 契约测试原则

**断言契约，而非捕获字面量**：
- 测试应该重新调用函数比较结果，而不是硬编码字面量
- 只有真正需要冻结的协议表面（如 golden 矩阵的 prompt 名称集合、
  run 目录的顶层键集合）才写成字面量

**实例**（`tests/unit/stages/test_stage0.py`）：
```python
# ❌ 不推荐：硬编码 14 个 sha256 字面量
_GOLDEN_PROMPT_DIGESTS = {
    "ANSWER": "a1b2c3...",
    "SEGMENT": "d4e5f6...",
    ...
}

# ✅ 推荐：固定名称集合，digest 值重新计算
FROZEN_PROMPT_NAMES = frozenset({
    "ANSWER", "EXAM_GEN", "SEGMENT", ...
})

def _expected_prompt_digests():
    return {name: prompts.digest(name) 
            for name in FROZEN_PROMPT_NAMES}

# 断言
assert manifest["prompt_digests"] == _expected_prompt_digests()
```

**好处**：
- 改提示词文本，测试不需要人工同步
- 加带默认值的配置字段，测试自动通过
- 测试意图清晰

### 8.6 如何为新功能添加测试

**步骤 1：单元测试（必做）**

1. 在 `tests/unit/` 对应目录创建 `test_<模块名>.py`
2. 用 `tests/helpers/` 的工具构造测试数据
3. 覆盖正常路径 + 边界情况 + 错误处理

示例：
```python
from tests.helpers.skills import make_skill
from expedistill.llm.fake import FakeClient

def test_author_write_with_profile():
    client = FakeClient(replies=[
        '```python\ndef solution():\n    pass\n```'
    ])
    skill = write_skill(
        client, task="...", profile="...", ...
    )
    assert skill.name == "..."
    assert "def solution" in skill.code
```

**步骤 2：组合测试（如果改了多模块协作）**

1. 在 `tests/composites/` 创建测试
2. 必须包含崩溃续跑用例：
```python
def test_baseline_exam_crash_resume(tmp_path):
    # 第一趟：跑到一半崩溃
    ctx = run_baseline_until_crash(tmp_path, crash_at=0.5)
    
    # 第二趟：续跑完成
    ctx_resumed = resume_run(tmp_path)
    
    # 验证：结果与一次跑完相同
    assert ctx_resumed.result == run_baseline_once(tmp_path)
```

**步骤 3：Golden 矩阵（如果改了端到端行为）**

改了提示词、配置、相位流程后，重新生成：
```bash
python tests/golden/generate.py
pytest tests/golden -q
```

检查 git diff：
- 4 个 JSON 文件的变化是否符合预期
- `provenance.json` 的调用数变化

**步骤 4：更新文档（如果升了 schema 版本）**

1. 在 README.md 第四节补充 schema 历史
2. 更新 `config_upgrade.py` 的升级函数
3. 添加升级函数的测试用例

### 8.7 测试数据管理

**测试夹具**（`tests/fixtures/`）：
- `golden/`：4 个组合的完整快照（588 KB）
- `benchmark/`：原始输出录像（TB、ALFWorld、SearchQA）
- 其他：构造好的轨迹、段、skill、考题样例

**不要做的事**：
- ❌ 从 `runs/` 里直接读真实运行数据
- ❌ 在多个测试文件里各复制一份辅助代码
- ❌ 测试文件按开发批次命名
- ❌ 把真实用户数据或敏感信息放进 fixtures

**应该做的事**：
- ✅ 需要真实样例时，复制一份脱敏后放进 `fixtures/`
- ✅ 共用代码放进 `tests/helpers/`，一处定义
- ✅ 测试文件按它测的源码放进对应目录、按测试内容命名

### 8.8 运行与验收

**本地开发**：
```bash
# 全量测试（约 45 秒，2211 个用例）
pytest tests -q

# 只跑单元测试
pytest tests/unit -q

# 只跑某个模块的测试
pytest tests/unit/blocks/test_author.py -v

# 只跑 golden 矩阵
pytest tests/golden -q

# 静态检查
ruff check src tests scripts --select E9,F
```

**提交前检查清单**：
- [ ] `pytest tests` 必须全绿
- [ ] `ruff check src tests scripts` 通过（`E9` 和 `F` 类错误）
- [ ] `pytest tests/test_layering.py` 通过（分层检查）
- [ ] 新功能有对应的单元测试
- [ ] 改了多模块协作有组合测试
- [ ] 改了端到端行为重新生成了 golden 矩阵
- [ ] 升级了 schema 版本时更新了 README 和升级函数

**需要真实服务的测试**：
- 标记 `@pytest.mark.live`，默认不跑
- 例如：真实模型请求、远程沙箱、外部 benchmark

### 8.9 Schema 版本规则

**什么时候升 `CONFIG_SCHEMA_VERSION`**：
- ✅ 改字段含义、删除字段、改类型/必填性/语义
- ❌ 新增可选字段且带默认值，旧代码能忽略未知键、新代码能补全缺省键

**判断标准**：
- **元数据字段**（如 `description: str = ""`）：不影响运行行为，不升版本
- **行为开关**（如 `use_weak_profile: bool = False`）：影响运行结果，升版本
- **可选参数**（如 `max_retries: int = 3`）：旧代码能用默认值正确运行，不升版本

**升级函数要求**：
- 每个版本的升级函数是纯函数：`upgrade_vN_config(old: dict) -> dict`
- 放在 `src/expedistill/core/config_upgrade.py`
- 必须有测试覆盖：仓库全部历史配置的升级加载往返

**示例**：
```python
def upgrade_v4_config(config: dict) -> dict:
    """v4 → v5: 新增 use_weak_profile 开关，默认 False."""
    if config.get("schema_version") != 4:
        raise ConfigError("...")
    
    return {
        **config,
        "schema_version": 5,
        "use_weak_profile": False,
    }
```

### 8.10 测试覆盖维护

**新增一个 block/method/stage 时**：
1. 在 `tests/unit/` 对应目录创建测试文件
2. 至少覆盖：
   - 正常路径（正确输入 → 正确输出）
   - 边界情况（空输入、极大/极小值）
   - 错误处理（非法输入 → 明确报错）
3. 调模型的用 `FakeClient`

**改动已有模块时**：
1. 先跑该模块的测试：`pytest tests/unit/<path> -v`
2. 如果测试转红，先理解为什么红
3. 如果是行为变化（不是 bug），更新测试
4. 如果是新增功能，增加新测试

**删除模块时**：
1. 删除对应的测试文件
2. `grep` 确认没有其他测试还在引用该模块
3. 跑全量测试确认没有遗漏



## 九、外部依赖与远程

1. 外部依赖路径（外层 `benchmark/searchqa-eval/`、`benchmark/alfworld-eval/`、
   TB 远程工作区）通过环境变量传入，代码里不写死绝对路径；
2. ALFWorld 插件运行在 `benchmark/alfworld-eval` 的 venv 下，需要
   `ALFWORLD_DATA` / `ALFWORLD_CONFIG` / `ALFWORLD_BENCH_SRC` 三个环境变量；
3. 远程（node29）只在 `~/niuyiming/` 与 `/data2/niuyiming/` 下工作；他人目录
   只读，不碰他人的进程和沙箱；不起 docker；
4. 密钥只加载进进程环境，不得打印，不得写进仓库、配置、日志、manifest。

## 十、迁移规范

迁移对象是姊妹仓库 `../ExpeToSkill-merged @ TB-eval 4f436af`（SkillExpand 系统）。
它同时扮演两个角色：**迁移来源**（按需拷代码）和**参考实现**（踩过的坑都写在里面）。

### 10.1 按需迁移：设计文档说了算

1. **迁移清单从 README 第一节的积木表推导**：只有设计需要的积木对应的源码
   才迁移（对应关系见 README 第五节）。清单里没有的，即使看起来有用也不迁，
   避免无用代码扰乱新架构；
2. 迁移前先核对：这个模块在老仓库里依赖了什么、被什么依赖，只带依赖闭包，
   不整体拷贝；
3. 设计不需要的机制（如 SkillExpand 的 L2 进化闭环、family 发现、冷启动、
   PPI / claim 等验收协议）不迁移。将来设计要用时，按"新增一块积木"的流程
   先改 README，再回老仓库找实现；
4. 每次迁移一个提交，提交说明列出"源文件 → 新位置"的对应表和改动点；
5. 迁移来的模块连同其测试一起带过来，先让测试在新目录结构下全绿，再接新代码；
6. 迁移代码同样要服从本文件的分层与积木规范，不符合的在迁移提交里改掉，
   不留"以后再改"。

### 10.2 参考实现：写新代码前先看老仓库

老仓库里的很多机制是踩坑之后才长成现在这样的（异常分类与处置、重试与修复
策略、冻结、原子写、目录锁、用量累计、分层检查……）。**凡是新系统要用、
老仓库又有对应实现的东西，动手写之前必须先看老仓库怎么做的**：

1. 优先复用或改造老实现，不从零重写；改了什么、为什么改，写在提交说明里；
2. 若决定不用老实现，要能说清理由（设计不同 / 已知缺陷 / 依赖过重），
   记入 `docs/decisions/`；
3. 写新机制时同步翻老仓库的经验文档：`docs/ARCHITECTURE.md`、
   `docs/ERROR_HANDLING.md`（异常体系与扩展方法）、`tests/test_layering.py`
   （分层检查），以及外层 AGENTS.md 里记录的事故；新代码不重犯已记录的错；
4. 老仓库的公开接口名、异常名、落盘格式可以沿用，减少认知成本；但沿用的是
   "行为和教训"，不是连同 bug 一起搬——发现老实现的问题要修掉并记录，
   不默诵"原来就这样"。

## 十一、git 与协作

1. **提交前需用户同意，推送需用户明确要求**；
2. 提交说明用英文、祈使句，第一行写清改了什么；一个提交只做一件事；
3. 进 git 的：`src/`、`tests/`、`scripts/`、`configs/`、`docs/`、README、
   AGENTS.md、log.md、`pyproject.toml`、`.env.example`。不进 git 的：`runs/`、
   `tmp/`、`.env`、虚拟环境、缓存（见 `.gitignore`）；
4. 每次提交前检查：测试全绿、分层检查通过、log.md 已追加本阶段条目、
   相关文档已同步（README / AGENTS.md / docs）；
5. 改设计的顺序：README → `docs/decisions/`（如有取舍）→ 代码 → log.md。
