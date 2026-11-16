# AGENTS.md

本仓库的目录结构、开发规范与工作记录规范。项目介绍与使用教程见
[README.md](README.md)；系统设计（积木清单、数据模型、阶段流程）见
[docs/design_spec.md](docs/design_spec.md)；外层工作区的通用规范（长任务前的
检查清单、测量与结论规范、远程资源）见 `../../../AGENTS.md`，本文件不重复，
两者冲突时以更严格的为准。

---

## 一、根目录结构

```
ExpeDistillation/
  AGENTS.md          本文件：目录、规范
  README.md          项目介绍与使用教程（面向读者和使用者）
  log.md             工作日志：每个阶段的工作记录（见第二节；只在本地，不进 git）
  pyproject.toml     包定义、依赖、pytest / ruff 配置
  .gitignore
  .env.example       需要的环境变量清单（只有变量名和说明，不含值）
  configs/           实验配置（进 git）
  src/expedistill/   全部方法代码（见第四节）
  tests/             测试（见第八节）
  scripts/           运行、远程、冒烟、审计脚本（见第五节）
  docs/              过程中产出的文档工件（见第三节）
  runs/              本地实验产物（不进 git，见第六节）
  tmp/               临时草稿、一次性调试输出（不进 git，随时可删）
```

根目录下只允许出现上面这些条目。新增顶层目录或文件要先改本节。

## 二、工作日志 log.md

log.md 只留在本地，已列入 `.gitignore`，不进 git、不发布：它记录主机、路径、
账号等部署细节。需要对外留痕的结论写进 `docs/`（决策、报告），不依赖 log.md。

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
  archive/     过时的文档（移到这里保存，文件开头注明过时原因和替代文档）
```

规定：

1. **文件名**：`<主题>_<YYYYMMDD>.md`，主题用英文小写加下划线，例如
   `docs/reviews/config_curation_redundancy_review_20261018.md`。同一主题同一天有多份时加
   `_v2`；
2. **文件开头**写三行：日期、一句话说明这份文档是什么、它对应 log.md 的哪一条；
3. `docs/design_spec.md` 是设计正本，docs 下的其他文档是过程记录。决定一旦实现，
   结论要回写设计正本，docs 里保留讨论过程，两者不能矛盾；
4. 每份新文档都要在 log.md 对应条目的"工件"里出现；
5. 文档用中文写；代码标识符、文件名、路径保持原样。

## 四、源码目录 src/expedistill/

```
src/expedistill/
  core/                   第 0 层：只依赖标准库
    models.py             持久对象：Trajectory（schema 3，编号带相位标签）/ Subtask
                          （schema 2，带 phase_label）/ SubtaskType / Skill（schema 5，
                          一个子任务类型一张卡，状态 active / discarded）/ EvolvingText；
                          技能的触发条件字段与正文字段（TRIGGER_FIELDS / CONTENT_FIELDS）
    trace.py              值对象 TraceRef / Provenance（只做形状校验）
    ids.py                确定性 ID 规则：轨迹 traj/<相位>/<任务>/<角色>/<序号>
                          （parse_trajectory_id 先返回相位标签）、段、类型 type/NNN、
                          技能 <type_id>/skill（make_skill_id 只接受类型 id）
    config.py             实验配置结构（schema 9：sequence + phases；相位角色
                          baseline / practice / test / learn；train / val / test /
                          reserved 四个列表与执行相位的 tasks 字段；执行相位的
                          playbook 开关）、模型角色表、加载期检查清单
    contracts.py          交付与学习操作的交接形状（执行单元行及其列表别名 /
                          任务清单行 TaskListRow）；执行→验收的 trial 级记录见
                          answer_sheet.py 的 ExecutionRecord
    answer_sheet.py       trial 级 AnswerSheet / ExecutionRecord / CoverageRecord /
                          GradingRecord，以及 unit 级 UnitSpec / UnitSummary /
                          PhaseReport（只有 sheets 与 units）的接口形状
    vocabulary.py         success/failure/invalid、normal/not_testable 与 cause 词的
                          唯一定义处
    registry.py           通用具名注册表（prompt、规则、benchmark 插件共用）
  infra/                  第 1 层：与业务无关的运行支撑
    store.py              B3 对象存储（原子写、按 ID 读、判断单元是否完成、JSONL 账本）；
                          相位库工件的文件名 BANK_NAME 定义在这里
    manifest.py           B4 冻结
    parallel.py           B5 线程池：run_parallel（按序返回）、run_units（遇到致命错误
                          即停批，各阶段共用）
    reliability.py        B6 异常分类、重试策略、单元失败记录
    runlock.py            B7 run 目录排他锁
    usage.py              B7 用量跨进程累加
  prompts/                第 1 层：B2 prompt 注册表（__init__ 的 import 清单登记全部
                          方法 prompt，manifest 冻结它们的 digest）
    registry.py           登记、取用、计算 digest
    llm.py                B1 自己的协议 prompt（DEFAULT_SYSTEM、REPAIR_INSTRUCTION）
    segment.py            G1 切段（SEGMENT）
    cards.py              G2 归类（SUBTASK_TYPING）、G3 写卡（CARD_WRITE），两者共用的
                          重问说明 CARDS_CORRECTION
    diagnose.py           诊断：TRIAL_FAILURE_ANALYSIS，重问说明 DIAGNOSIS_CORRECTION
    evolve.py             画像与 playbook 的演化：PROFILE_UPDATE、PLAYBOOK_UPDATE，
                          重写说明 EVOLVE_CORRECTION、PLAYBOOK_PARTS_CORRECTION
    revise.py             改写一侧：REVISE_TRIGGER、REVISE_CONTENT，诊断段落
                          REVISION_DIAGNOSIS
    adherence.py          技能适用性与遵循度两个裁判 prompt（judge-skills 用）
    learning_context.py   学习提示词的 benchmark 词汇槽：LearningContext、GENERIC；
                          call_model 按 GENERIC、句柄默认值、调用方变量三层填充
    profile.py            画像段的构造：PROFILE_ROLES 登记各写作 prompt 的读者标题行，
                          画像为空时给中性段落
  inject/                 第 1 层：D9 技能注入（纯函数）
    skills.py             技能块渲染、playbook 块、整库引用与 token 估计
    skill_text.py         技能文本的唯一渲染处：render_card（打开后看到的全文）、
                          description_text（目录行与 SKILL.md 的 description）
  llm/                    第 2 层：B1 调用模型
    client.py             底层客户端（OpenAI 兼容接口）+ 请求 ID 别名池
    providers.py          provider 注册表（纯数据表：名字 → builder；环境变量选择）
    call.py               ModelHandle（含 prompt_defaults / prompt_aliases）与
                          call_model：取 prompt → 请求 → 解析 → 格式错误重试 → 记账落盘
    fake_provider.py      注册进 providers 表的 `fake` provider：按计划文件返回，
                          让整条相位序列在真实 benchmark 插件上零网络运行（smoke）
  benchmarks/             第 3 层：benchmark 插件
    base.py               插件接口：load_tasks / run / view_rules / task_prompt_name；
                          PromptPack 与 PlaybookRequirement；可选能力
                          skill_folder_prompt_name（技能文件夹执行，None=不支持）、
                          verdict_rule（缺省 `scorer`）、aux_metric_name、
                          playbook_in_skills_block
    react_loop.py         本地 benchmark 共用的逐步 ReAct 执行循环
    skill_folder.py       本地 benchmark 的渐进式技能披露：<available_skills>
                          目录与 ReadSkill[<位置>] 读取（循环自己响应，
                          评测环境收不到）
    aux.py                辅助指标的插件接线（声明与打分器包装）
    registry.py           按名字取插件
    tb/                   TerminalBench：plugin.py 插件（远程 Harbor 提交、取回、
                          技能文件夹上传）；remote.py 远程主机与本机两种传输；
                          trajectory.py 从 Harbor 的 trajectory.json 重建逐步轨迹；
                          protocol.py 执行侧全部文本：Terminus 模板副本、
                          读卡规则、工作纪律与后缀拼装（上传为
                          SKILL_READ_SUFFIX.md，playbook 在最前面）；
                          playbook.py 摸底事实提取与 TB_PLAYBOOK_UPDATE；
                          prompt_pack.py 提示词包
    searchqa/             适配外层 benchmark/searchqa-eval/：plugin.py 插件；
                          env.py 评测环境与判分器的加载；prompt_pack.py 提示词包
    alfworld/             适配外层 benchmark/alfworld-eval/：plugin.py 插件；
                          env.py 子进程环境封装；protocol.py 与外层统一 runner 一致
                          的执行协议（系统消息、第一步模板、历史窗口、动作解析、
                          技能目录与 playbook 的拼接）；playbook.py 摸底事实提取与
                          ALFWORLD_PLAYBOOK_UPDATE；prompt_pack.py 提示词包
  blocks/                 第 4 层：功能积木，一个文件一组相关积木
    execute.py            E1：按 benchmark 取插件执行
    tasks.py              D1 任务加载、D2 划分
    metrics.py            D3 成绩统计
    rules.py              D6 段筛选（segment_select）与轨迹挑选（trajectory_select）的
                          具名规则；写卡材料与定位按名字取用
    view.py               D5 轨迹视图、答卷的 Markdown 视图
    segment.py            G1 切段、D7 切段校验
    subtask_types.py      G2 归类：段卡片、类型表渲染、一块段的 SUBTASK_TYPING 调用与
                          校验（新类型按段编号顺序第一次被用到时编号）
    author.py             写作 prompt 返回字段的校验：触发条件两字段、正文两字段、
                          四字段
    adherence.py          技能适用性判定与遵循度判定（judge-skills 用）
  composites/             第 5 层：组合积木（负责落盘与续跑）
    batch_execute.py      K1 批量执行（TB 远程命名空间含相位标签）
    segment_checked.py    K2 切段（带校验）
    locate.py             K3 定位：切一条轨迹（0 步轨迹记审计跳过）、挑摸底强模型轨迹、
                          按块归类本相位的新段；子任务类型表 subtask_types.jsonl 与
                          段归属 segment_types.jsonl（只追加；两次追加之间崩溃时删掉
                          本相位没有段用到的类型）
    evidence.py           证据分配（纯函数）：Evidence 行、route（gap / trigger /
                          content / none）、plan_cards（每个类型或每张卡一个单元）、
                          删除规则 content_failed_twice、trigger_facts、discarded_types
    sheet_facts.py        从一张摸底或练习答卷读出执行者看到的目录与 playbook、打开过的
                          卡（第一次打开的步号与当时交付的版本全文）
    decision.py           终测的纯函数：final_test_delta、control_arm_source
    learn_ledger.py       学习账本：revisions.jsonl / outcomes.jsonl、删除的落盘
                          land_delete、按目标排序提交 commit_order
    evolving_text.py      每个学习相位的 profile.json / playbook.json 存取；读取紧邻
                          上一个学习相位的版本；playbook 显示能力检查
    unit_ids.py           unit id / 注入技能规范形 / 空洞答卷 id 的身份规则
    answer_sheets.py      trial 级 ExecutionRecord 构建、coverage 与 execution
                          装配、逐 trial 判分、answer_sheets/ 的 JSON + Markdown 导出
    coverage.py           应有 trial 与已存 trial 的 coverage 记录
                          （present / missing / terminal + cause）；不判分、不聚合
    verdicts.py           per-trial 判分核：具名判分规则 + grade_sheet；
                          default_verdict_rule 读插件的 verdict_rule
    unit_summary.py       AnswerSheet + UnitSpec → UnitSummary 的纯聚合函数
    phase_report.py       相位报告：读取 unit_specs / answer_sheets / coverage，
                          现算 UnitSummary
    paired_stats.py       配对统计的纯函数：按任务自助法的置信区间、胜负平、
                          McNemar 精确检验、单臂噪声底；只用标准库
    trigger_metrics.py    技能使用的预登记指标：触发召回、误触发、遵循度、
                          按适用/打开分层的成功率；练习摘要的 skill_use
                          （card_use_metrics）
    skill_use.py          判断一次执行打开了哪些技能、在哪些步骤
                          （cat 与 ReadSkill[...] 同等看待）
    shortfall.py          已接受基础设施空洞的落盘记录
    external_control.py   外部冻结成绩文件的加载与校验（sha256、任务覆盖、
                          按采样深度截取奖励）；test 相位 against: external_campaign
                          时对照臂由此取数，零执行
    phase_bank.py         每个学习相位的技能库工件布局、写出与读取（全部已存技能，
                          含 discarded，按技能 id 排序）；相位标签与相位目录名的互相
                          映射只在这里实现（phase_bank_dir、label_of_dir）
    skill_materialize.py  folder 单元清单的幂等物化：按清单的库指纹从相位库读技能，
                          写成 `<skill>/SKILL.md`（带 YAML 头）并写 catalog.md 兜底
    trace.py              来源指针的函数：trace_at（唯一构造 TraceRef，只接受摸底或
                          练习答卷与范围内的步号）、check_ref（边界检查：指针仍指向
                          一条已存执行的某一步）、trace_of_trajectory、render_windows
                          （写作者读到的真实步骤，同一次执行的多个窗口合并后只渲染
                          一次）；task_instruction 按任务 id 取 benchmark 的任务说明
    provenance.py         每个技能版本一条 Provenance 的账本：record_version（唯一
                          写入口，技能与来源一起落盘）、find_provenance、
                          check_provenance（学习相位末的边界检查）
  methods/                第 6 层：方法驱动与参数契约（纯数据表注册，
                          见 methods/registry.py；解析按需 import 驱动）
    params.py             通用参数构建器（未知键/类型/缺键全响亮失败）
    registry.py           (族, 名字) → (驱动, 参数类) 的纯数据表（四族：
                          delivery / execution / review / author）
    execution/            execution 族参数契约：RealExecutionParams /
                          RealFolderExecutionParams（executor、attempts；相位驱动
                          直接使用）+ README.md（参数表）
    delivery/             交付驱动 none.py、whole_bank.py、folder_delivery.py
                          （配置名 `folder`）+ units.py（各驱动共用的执行单元行构造
                          与 playbook 拼接）+ params.py（全部交付 Params，不 import
                          驱动）+ README.md
    review/               ReviewParams（K、retry_limit、write_lessons）
    diagnose/             学习第 1 步：diagnose_trial（TRIAL_FAILURE_ANALYSIS）、Finding
    evolve_text/          学习第 4 步：evolve_text 外壳（画像与 playbook 共用，
                          含 playbook 的长度与必备部分检查）
    revise/               学习第 5 步的改写：revise_trigger、revise_content、
                          diagnosis_text、带版本守卫的 apply_revision
    cards/                author 族 `subtask_cards`：CardAuthoringParams（__init__）；
                          material.py 一个类型的写卡材料（强模型成功段、弱模型失败段）；
                          writer.py write_card（CARD_WRITE，不合格带原因重写）
  stages/                 第 7 层：相位流程与只读工具
    stage0_freeze.py      冻结步（p00）：方法族解析、提示词包检查、任务表、manifest 冻结
    context.py            StageContext / RunSettings（相位驱动的公共上下文）
    phase_execute/        执行相位驱动包：__init__ 分发入口 run()；
                          phase_disk.py 相位目录与磁盘事实（execute_phase_dir、
                          arm_dir、bank_skills、write_units、materialize_folder：
                          库为空也物化空目录）；
                          resolve.py 一次调用 resolve_phase 把配置映射成
                          ResolvedPhase（执行参数、执行者与采样次数、交付驱动与参数、
                          playbook 文本、判分规则、重派预算），另有
                          execution_params、executor_role、bare_delivery_rows；
                          engine.py 派发→重派引擎（Plan、plan_units、
                          task_objects、task_map、dispatch_arm、run_arm_units、
                          arm_trajectories；K1 经 engine.batch_execute 调用，测试在此
                          替换）；baseline.py / practice.py / final_test.py 三种执行
                          角色各一个模块
    phase_learn.py        学习相位驱动：五步（诊断、定位、分配证据、演化画像与
                          playbook、演化技能库），按 A–E 五组并行，账本按确定顺序提交
    fork_run.py           分叉工具：把已完结 run 的前 K 个相位与它们产生的 run 级对象
                          拷进新 run，新配置从第 K+1 个相位接着跑（`fork-run` 子命令）
    import_baseline.py    复用摸底：把已有 run 的摸底轨迹改写编号后写进新 run，新 run 的
                          摸底零执行（`import-baseline` 子命令）
    import_bank.py        把外部方法的产物（markdown / 技能文件夹 / notes）写成
                          测试专用 run 的技能库（`import-bank` 子命令）
    analysis_common.py    只读分析的共用约束：输出目录不得在被读的 run 里，
                          首次设置写进 analysis_config.json，续跑设置不同即拒绝
    judge_skills.py       只读：判定 test 或 practice 相位每个 trial 的技能使用——适用、
                          打开、遵循（`judge-skills` 子命令）
    paper_tables.py       只读：按配对统计量比较已完结的 run，生成论文表格
                          （`paper-tables` 子命令）
    segmentation_metrics.py 只读：分段消融的机制指标——测试成绩、初始/最终库大小、
                          技能锚点不在任务开始的比例、任务中途打开率
                          （`segmentation-metrics` 子命令）
  cli.py                  第 8 层：命令行入口（唯一的方法入口）+ 相位循环
                          （run_experiment 逐个派发 sequence 条目）+ 模型句柄构建
                          （从提示词包填 prompt_defaults / prompt_aliases）；子命令
                          run / summary / fork-run / import-baseline / import-bank /
                          judge-skills / paper-tables / segmentation-metrics
```

九个顶层包（`core`、`infra`、`llm`、`inject`、`blocks`、`benchmarks`、`composites`、
`methods`、`stages`）的 `__init__.py` 只有文档字符串，使用方 import 具体子模块。
`prompts/__init__.py` 例外：它按 import 清单登记全部方法 prompt。

积木编号与文件的对应以设计正本第三节为准；新增积木时两处同时更新。

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
   不得出现 `if benchmark == "tb"` 之类的分支，也不得出现按 benchmark 名字
   索引的表，需要的差异做成插件接口的一项（例如 `verdict_rule`）。
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
   请求、不写 prompt 字符串（包括拼进 prompt 的标题行），prompt 一律按名字从
   `prompts/` 取；弱模型画像段的标题行由 `prompts/profile.py` 按角色名提供。
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
  run/        启动实验与准备实验输入：
              run_experiment.sh <配置名>：加载环境变量、脱离终端启动 driver、
                                  写 pidfile 与续跑命令（不含方法参数）
              supervise_tb_run.py：TB 终测相位的 detached supervisor（读 pidfile、
                                  轮询产物文件，driver 退出后按摘要判断是否重跑同一条命令）
              build_paper_configs.py：按论文实验矩阵生成全部配置
                                  （`configs/experiments/<bench>_paper_<exp>_<arm>.yaml`，
                                  `--check` 校验与磁盘一致，`--print-index` 打印
                                  各臂与参照臂的差异）
              paper_splits.py：论文配置共用的划分（含 ALFWorld 的 val 列表）、TB 任务表
                                  与外部对照文件信息
              build_bare_control.py：把一次 ALFWorld 裸跑写成该学生模型的冻结成绩文件
  remote/     在基准主机上运行实验：deploy_bench_host.sh（rsync 部署，一个提交一个目录；未提交的改动加 `--force`，目录名带时间戳、永不覆盖）、
              start_run_bench_host.sh（在基准主机上起/续跑 run）、README.md（运维手册）；
              driver 与 Harbor 同机，run 目录、分析、审计都留在基准主机
  audit/      scan_private.py：提交前扫描已跟踪文件里的私有部署信息
  smoke/      冒烟与启动工具：gen_searchqa_fake_plan.py（SearchQA 假模型冒烟的计划文件）、
              launch_detached.py（start_new_session 启动长命令并写 pidfile）、
              smoke_tb_dryrun.py / smoke_tb_live.py（TB 提交链路的 DRY_RUN 与单题真实冒烟）
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
   的方式（镜像、重试名单、超时参数）与其保持一致，有差异要在脚本开头注明；
6. 正式实验在基准主机上运行：`EXPEDISTILL_TB_HOST=local`，driver 与 Harbor 同机，
   不经过 ssh/scp。代码按提交放在 `<deploy-base>/<commit>/`，run 目录放在
   `<runs-dir>/`，结果不拉回本机，分析也在基准主机上做；具体路径只写在被
   gitignore 的 `.env` 和基准主机上的密钥文件里，不写进仓库。

## 六、配置与产物

### configs/

```
configs/
  experiments/   每个实验一份配置：benchmark、模型角色表、采样次数、划分与任务表、
                 相位序列（sequence + phases，方法参数全部在相位定义里）、
                 无效重派预算、具名规则的名字、token 预算；schema_version 9。
                 论文配置全部由 scripts/run/build_paper_configs.py 生成
  seed/          外部 campaign 与裸跑的冻结成绩文件（外部对照的取数来源，
                 字节级拷贝 + sha256 由实验配置固定；本仓库不修改它，
                 重导出后按新 sha256 换新文件名）
```

1. 一份配置对应一个实验，文件名即实验名：`<bench>_paper_<exp>_<arm>.yaml`
   （`bench` ∈ {`tb21`, `alfworld`, `searchqa`}）；改配置先改生成脚本，再重新生成；
2. 已经启动过正式 run 的配置**保持原样**，参数要变就给新臂起新名字；
3. 配置只写方法参数和模型名，**端点地址、密钥从环境变量读**，不写进仓库、
   配置、日志或 manifest。需要的环境变量列在 `.env.example`。

### runs/

1. 本地 run 目录放 `runs/<实验名>_<YYYYMMDD_HHMM>/`，不进 git；远程 run 放
   `<runs-dir>/` 下，同样的命名。**正式 run 固定在
   它启动时的 checkout 上续跑**（多方法方案 §6，R6）：每个正式 run 一条
   维护分支（命名惯例 `run/<run 目录名>`，热修 cherry-pick 到它上面）+
   独立 venv（editable `.pth` 只指向该 venv 自己的 src）——schema 升版等
   破坏续跑的改动在 stage 0 会被明确拒载并指向该规则；
2. run 目录内部布局见设计正本第四节；run 开始时写 manifest，之后每次启动都与
   manifest 逐字段比对，不一致直接拒绝（`infra/manifest.py`）；
3. **逐单元落盘、可续跑**：一次执行、一次切段、一次诊断都是一个单元，完成
   一个写一个（原子写：先写临时文件再改名）。再次启动时按确定 ID 跳过已完成单元；
4. **run 目录加排他锁**（`infra/runlock.py`），第二个进程启动直接报错并给出
   持有者 PID；
5. **用量跨进程累加落盘**，续跑后不清零；拿不到用量记 unknown，不记 0；
6. **失败分类处置**（`infra/reliability.py`）：基础设施异常重试、模型输出格式
   错误按次数修复、配置错误立即停止、代码 bug 立即停止整个流程。失败单元单独
   记录，不得混进成功结果，也不得悄悄丢掉；
7. **每个 run 跑完先做完整性检查，再使用它的数字**：`expedistill summary`
   加设计正本第七节"run 跑完后的完整性检查"列出的人工核对项（`scripts/audit/`
   只有提交前的私有信息扫描）。有问题的 run 一律作废（在 run 目录写 `INVALID.md`
   说明原因），不进结果表，并在 log.md 记录；
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
   `task_prompt_name`（执行 prompt 的注册名）；
2. 写 `benchmarks/<名字>/prompt_pack.py`，导出 `PROMPT_PACK`（`PromptPack`），
   插件用 `prompt_pack` 属性声明。七个字段：`vocabulary`（学习提示词的词汇槽）、
   `protocol`（给写作者的环境事实）、`executor_prompts`（执行提示词的注册名，
   digest 进 manifest）、`playbook_seed`（playbook 初始文本，可为空）、
   `playbook_facts`（从摸底答卷计算事实的函数，可为 None）、`playbook_checks`
   （playbook 必备部分）、`overrides`（方法提示词名 → 本 benchmark 替换提示词名，
   替换提示词的占位符必须与原提示词相同）。替换提示词和执行提示词都在
   `benchmarks/<名字>/` 里注册；
3. 可选能力：支持技能文件夹的插件声明 `skill_folder_prompt_name`（folder 模式
   执行 prompt 的注册名；folder 相位落到不声明的插件时，test 相位驱动在派发前
   拒绝，E1 的逐单元守卫作第二道）；技能块不是普通提示词文本的插件把
   `playbook_in_skills_block` 设为 `False`；判分规则不是 `scorer` 的插件声明
   `verdict_rule`；有辅助指标的插件声明 `aux_metric_name` 并实现 `aux_success`；
4. 在 `benchmarks/registry.py` 登记名字；
5. 在 `tests/unit/benchmarks/` 加测试：用录好的原始输出测轨迹转换；另加 1 道题的
   真实执行冒烟（标 `live`）；
6. `blocks/` 及以上不改一行代码。如果非改不可，说明插件接口不够用，先改
   接口和设计正本，再实现。

### 新增一块积木

1. 先在设计正本第三节的积木表里登记编号、输入输出；
2. 放进对应目录的文件；调模型的话同时在 `prompts/` 登记 prompt；
3. 写单元测试（调模型的用 `tests/helpers/fake_llm.py` 的 `FakeClient`）；
4. 在设计正本 3.6 的"相位 → 积木组装"表里写明哪个相位用它。

### 新增或修改 prompt

1. prompt 是 `prompts/` 下的具名常量，带一句话说明用途和所属积木；
2. 每个 prompt 的 digest 写进 manifest。**改一个字也是新实验**，必须新建 run
   目录，不得在旧 run 上续跑；
3. 输出格式（JSON 字段）与 prompt 放在一起定义，两者一起改；
4. benchmark 专用的提示词（执行提示词、替换提示词）登记在 `benchmarks/<名字>/`，
   并写进该 benchmark 提示词包的 `executor_prompts` 或 `overrides`。

### 修改数据对象

1. 持久对象的字段只在 `core/models.py` 定义，其他地方不得自造字段；
2. 改字段含义、删除字段、或改变已有字段的类型/必填性/语义都要升
   `schema_version`，并在设计正本第四节同步。新增可选字段且带默认值时，
   **如果旧代码读取新数据不会出错**（旧代码会忽略未知键，新代码会补全缺省键），
   则不必升版本号；
3. 升版本即拒绝旧版本的记录，不写升级函数；读到旧版本对象明确报错，不能静默
   读成默认值。

## 八、测试：开发过程中如何测试和新增测试

### 8.1 三层测试架构

```
tests/
  unit/          每块积木的单元测试，目录与 src/expedistill/ 对应
  composites/    组合积木测试（假模型 + 临时 run 目录，含中断续跑）
  golden/        回归测试（2 组合 golden 矩阵 + 崩溃续跑验证 + 修订窗口探针）：
                 generate.py 重新生成快照，test_golden_matrix.py 核对
  fixtures/      benchmark 原始输出录像（tb / alfworld / searchqa）、学习提示词
                 渲染样例（prompts/）、golden 矩阵的 2 组合快照（golden/）
  helpers/       多个测试文件共用的测试工具，一处定义，契约变了只改这里：
                 fake_llm.py（假模型 FakeClient）、fake_plugin.py（经导入机制
                 提供假 benchmark 插件的唯一实现）、stub_plugin.py、plugins.py、
                 golden_harness.py 与 golden_revision_window.py（golden 的剧本、
                 快照与崩溃工具）、tb_fake_remote.py / tb_folder.py（TB 远程与
                 技能文件夹）、final_test_folder.py、alfworld_adapter.py、
                 searchqa_stub.py、skill_folder.py、skill_use_runs.py、
                 learn_runs.py、paper_runs.py、traces.py、skills.py、configs.py、
                 prompts.py
  conftest.py    共用的 pytest fixture（TB runner 环境变量、插件注册表清理、
                 golden 假插件）
  test_layering.py
```

### 8.2 单元测试规范

1. **覆盖要求**：
   - 每块积木至少一个单元测试文件
   - 测试文件按它测的源码放进对应目录、按测试内容命名
   - 不按开发批次命名（不用 `test_w1a_*`、`test_folder_m3_*` 这类名字）

2. **测试实现**：
   - 调模型的用 `tests/helpers/fake_llm.py` 的 `FakeClient`，不发真实请求
   - 不连远程服务
   - 测试数据从 `tests/fixtures/` 读取，不得从 `runs/` 里直接读

3. **共用代码**：
   - 两个以上测试文件要用的辅助代码放进 `tests/helpers/`
   - 不在各文件里各复制一份，测试文件之间不互相 import

### 8.3 组合测试规范

1. **必须包含续跑验证**：
   - 组合积木的测试必须包含"中途中断再续跑，结果与一次跑完相同"
   - 用假模型 + 临时 run 目录验证多模块协作

2. **覆盖关键路径**：
   - baseline → learn → practice → learn → test 全流程
   - 各相位的崩溃点与恢复

### 8.4 Golden 矩阵回归测试

**2 个组合**（按 test 相位的交付命名，快照是 `tests/fixtures/golden/<组合>.json`）：
- `whole_bank`：test 用 `whole_bank` + `real`，playbook 关闭
- `folder`：test 用 `folder` + `real_folder`，显示演化的 playbook

两个组合的 practice 都用 `folder` + `real_folder` 并显示演化的 playbook。

**覆盖策略**：
- 两个组合都跑论文的七相位序列 `baseline, learn, practice, learn, practice, learn, test`
  （一个 `learn` 定义复用三次），每个组合一次跑完 35 次模型调用
- 三个学习相位依次覆盖：两张缺口新卡；一次正文改写与一次触发改写；删除规则与一次触发改写
- 两个组合都在三个崩溃点上做崩溃续跑验证（`golden_harness.CRASH_POINTS`：`diagnosis` 两次诊断
  之间、`revise` 改写调用前、`new_cards` 两张新卡之间）
- `--workers 1` 与 `--workers 8` 的快照逐字节相同
  （`test_eight_workers_write_the_same_snapshot_as_one`，`generate.py` 生成时也核对）
- `tests/helpers/golden_revision_window.py`：覆盖"改写已落盘、下一个 practice 还没执行"的崩溃点

**何时重新生成**：
- 修改提示词后
- 修改配置字段影响端到端行为后
- 修改相位流程后

**如何生成**：
```bash
python tests/golden/generate.py
```

生成后检查：
- 2 个组合的 JSON 文件（`whole_bank.json`、`folder.json`）
- `provenance.json`（记录每个组合的模型调用数与崩溃点的调用序号）
- 运行 `pytest tests/golden -q` 确认通过

### 8.5 契约测试原则

**断言契约，不捕获字面量**：
- 测试重新调用函数比较结果，不硬编码字面量
- 只有真正需要冻结的协议表面（如 golden 矩阵的 prompt 名称集合、
  run 目录的顶层键集合）才写成字面量

**实例**（`tests/unit/stages/test_stage0.py`）：
```python
# ❌ 不推荐：硬编码 14 个 sha256 字面量
_GOLDEN_PROMPT_DIGESTS = {
    "CARD_WRITE": "a1b2c3...",
    "SEGMENT": "d4e5f6...",
    ...
}

# ✅ 推荐：固定名称集合，digest 值重新计算
FROZEN_PROMPT_NAMES = frozenset({
    "SEGMENT", "SUBTASK_TYPING", "TRIAL_FAILURE_ANALYSIS", "CARD_WRITE", ...
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
from expedistill.inject.skill_text import render_card

def test_render_card_numbers_the_steps():
    skill = make_skill("type/001", "when the drawer is closed",
                       steps=["go to the drawer", "open the drawer"])
    card = render_card(skill)
    assert "1. go to the drawer" in card
    assert "2. open the drawer" in card
```

**步骤 2：组合测试（如果改了多模块协作）**

1. 在 `tests/composites/` 创建测试
2. 必须包含崩溃续跑用例：在某次模型调用前模拟崩溃，用同一条命令续跑，比较续跑结果与
   一次跑完的结果。端到端的写法用 `tests/helpers/golden_harness.py` 的现成工具：
```python
from tests.helpers.golden_harness import (
    GoldenPlugin, assert_resume_equivalent, installed_plugin,
    run_crash_and_resume, run_straight, snapshot, write_config)

def test_a_crash_resumes_to_the_straight_run(tmp_path):
    combo = {"delivery": "whole_bank"}
    config = write_config(tmp_path / "config.yaml", combo)
    with installed_plugin(GoldenPlugin()):
        run_straight(config, tmp_path / "straight")
        run_crash_and_resume(config, tmp_path / "crashed", crash_at=10)
    assert_resume_equivalent(snapshot(tmp_path / "straight"),
                             snapshot(tmp_path / "crashed"))
```
   单个组合积木的续跑用例在临时 run 目录上直接调用它两次：第一次在中途抛出异常
   （例如 monkeypatch 某个落盘函数），第二次正常跑完，再与一次跑完的产物比较
   （参考 `tests/composites/test_learn_phase_resume.py`）。

**步骤 3：Golden 矩阵（如果改了端到端行为）**

改了提示词、配置、相位流程后，重新生成：
```bash
python tests/golden/generate.py
pytest tests/golden -q
```

检查 git diff：
- 2 个 JSON 文件的变化是否符合预期
- `provenance.json` 的调用数变化

**步骤 4：更新文档（如果升了 schema 版本）**

1. 在设计正本第二、四节写明新的 schema 版本与字段
2. 用 `scripts/run/build_paper_configs.py` 重新生成配置

### 8.7 测试数据管理

**测试夹具**（`tests/fixtures/`）：
- `golden/`：2 个组合的完整快照（约 680 KB）
- `tb/`、`alfworld/`、`searchqa/`：原始输出录像
- `prompts/`：学习提示词的渲染样例

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
# 全量测试（约 16 秒，约 2050 个用例）
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
- [ ] 升级了 schema 版本时更新了设计正本并重新生成了配置

**需要真实服务的测试**：
- 标记 `@pytest.mark.live`，默认不跑
- 例如：真实模型请求、远程沙箱、外部 benchmark

### 8.9 Schema 版本规则

**什么时候升 `CONFIG_SCHEMA_VERSION`**（当前为 9）：
- ✅ 改字段含义、删除字段、改类型/必填性/语义
- ❌ 新增可选字段且带默认值，旧代码能忽略未知键、新代码能补全缺省键

**判断标准**：
- **元数据字段**（如 `description: str = ""`）：不影响运行行为，不升版本
- **行为开关**（如执行相位的 `playbook`）：影响运行结果，升版本
- **可选参数**（如 `max_retries: int = 3`）：旧代码能用默认值正确运行，不升版本

**升版本即拒绝旧版本**：加载器只接受当前版本，其他版本报配置错误并提示用
`scripts/run/build_paper_configs.py` 重新生成；不写升级函数。对象的 schema 版本
（`core/models.py` 的 `*_SCHEMA_VERSION`）同样处理。

### 8.10 测试覆盖维护

**新增一个 block/method/stage 时**：
1. 在 `tests/unit/` 对应目录创建测试文件
2. 至少覆盖：
   - 正常路径（正确输入 → 正确输出）
   - 边界情况（空输入、极大/极小值）
   - 错误处理（非法输入 → 明确报错）
3. 调模型的用 `tests/helpers/fake_llm.py` 的 `FakeClient`

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
3. 远程基准主机上只在分配给自己的目录下工作（可用 `EXPEDISTILL_TB_ALLOWED_ROOTS`
   强制）；他人目录只读，不碰他人的进程和沙箱；不起 docker；
4. 密钥只加载进进程环境，不得打印，不得写进仓库、配置、日志、manifest。

## 十、迁移规范

迁移对象是姊妹仓库 `../ExpeToSkill-merged @ TB-eval 4f436af`（SkillExpand 系统）。
它同时扮演两个角色：**迁移来源**（按需拷代码）和**参考实现**（踩过的坑都写在里面）。

### 10.1 按需迁移：设计文档说了算

1. **迁移清单从设计正本第三节的积木表推导**：只有设计需要的积木对应的源码
   才迁移（对应关系见设计正本第九节）。清单里没有的，即使看起来有用也不迁，
   避免无用代码扰乱新架构；
2. 迁移前先核对：这个模块在老仓库里依赖了什么、被什么依赖，只带依赖闭包，
   不整体拷贝；
3. 设计不需要的机制（如 SkillExpand 的 L2 进化闭环、family 发现、冷启动、
   PPI / claim 等验收协议）不迁移。将来设计要用时，按"新增一块积木"的流程
   先改设计正本，再回老仓库找实现；
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
   AGENTS.md、`pyproject.toml`、`.env.example`。不进 git 的：`log.md`、`runs/`、
   `tmp/`、`.env`、虚拟环境、缓存（见 `.gitignore`）；
   仓库内的文档、代码、测试、配置不得出现真实的主机名、地址、账号、个人目录和
   服务端点，一律用占位符或环境变量；
4. 每次提交前检查：测试全绿、分层检查通过、本地 log.md 已追加本阶段条目、
   `python scripts/audit/scan_private.py` 无命中（模式见脚本说明，部署专属的名字放在
   被 gitignore 的 `tmp/private_patterns.txt`）、
   相关文档已同步（README / 设计正本 / AGENTS.md / docs）；
5. 改设计的顺序：设计正本 → `docs/decisions/`（如有取舍）→ 代码 → log.md。
