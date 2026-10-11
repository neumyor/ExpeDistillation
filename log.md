# 工作日志

格式与规定见 [AGENTS.md](AGENTS.md) 第二节。按时间顺序追加，旧的在上、新的在下。
本文件建立前（2026-10-10 17:12 之前）的条目根据当日会话记录补记，时间取各阶段完成时刻。

---

## 2026-10-10 15:56 — 评估新系统的改动范围，决定新开仓库

用户提出新系统流程：强模型和弱模型各在任务集上跑一遍，对弱模型没做出来的题，把强、弱两边的轨迹切成一段段 Subtask，每段生成一个 skill 并出一道考题，弱模型靠 skill 做对考题才入库，test 时弱模型读入全部 skill 执行任务。对照 SkillExpand（`ExpeToSkill-merged`）的代码评估改动范围：底座四层（runtime、benchmarks、persistence、reliability）对 L1 / L2 / evaluation 没有反向依赖，L1 执行循环一簇（约 914 行）可以整簇带走，campaign / cli 因为编排的是旧流程必须重写，约一半旧代码用不上。结论是新开仓库、只迁移依赖闭包，用户认可。

工件：`../ExpeToSkill-merged` 分支 `SkillPool`（从 `TB-eval @ 4f436af` 切出，未改代码）

## 2026-10-10 16:00 — 建立 ExpeDistillation 仓库

在 `repos/experiments/` 下新建 `ExpeDistillation` 并 git 初始化（用户要求先建空仓库，不直接拷贝代码），写入初始 README 说明仓库定位与代码来源，设置仓库级 git 身份。

工件：`README.md`、commit `aca16ba`

## 2026-10-10 16:05 — 细化系统流程，确定四个设计决定

把系统流程细化为阶段 0–5 并与用户确认。用户的四个决定：数据划分是实验模式，TB 上 `split`（train / test）和 `full`（全部题一起蒸馏、一起测）两种都做；切段方法参照 AMD 论文 §3.2 Subtask Memory，但成功和失败轨迹都切；考题形式为文本问答题；弱模型没考过的 skill 由强模型改写后重考，重考次数可配置。

工件：`README.md`（系统流程章节）、`../../../papers/agent_memory_distillation_empowering_small_llm_agents_with_hierarchical_teacher_memory.txt`（AMD 参考）

## 2026-10-10 16:24 — 功能封装、数据模型与 prompt 统一管理

按用户要求把"让一个模型在一道题上跑一遍"封装为执行模块：输入（模型、一道任务、skill（可选）），输出（是否成功、执行轨迹（可选）），内部按 benchmark 选 runner，TB 独享一个远程 runner，SearchQA 和 ALFWorld 在本地逐步执行，且直接采用外层 `benchmark/` 下现有的两套 eval 仓库。定义 Trajectory / Subtask / Skill / Exam / Attempt 五个对象，JSON 落盘、以 ID 关联，Subtask 必带是否成功字段。切段加段数上限 K（写进 prompt）和程序侧校验；所有 prompt 集中到一个注册表，digest 写进 manifest 冻结。

工件：`README.md`（功能封装、数据模型章节）

## 2026-10-10 16:45 — 设计合理性 review 与第一轮修订

对 README 做 review，提出 10 条问题。用户决定：验收前先让弱模型裸答，裸答就对的 skill 丢弃；出考题时不给强模型看 skill；改写重考沿用同一道考题，因为改写的目的是让强模型摸清弱模型的理解能力（我原先建议换题，被否决），改为用"改写时看不到参考答案、先归类错因、程序检查泄题"三条约束防止塞答案；split 模式在 test 集上单独跑一组弱模型裸跑做对照，full 模式沿用阶段 1 成绩，不额外加同期对照；阶段 4 与 test 之间新增 skill 库整理，参照 AgentBrew 的 curator；每题采样次数做成参数，失败题集合只算全部采样都失败的题；程序只检查切段的 success 字段存在；补弱模型作答 prompt；弱模型做对的段不生成 skill；Subtask 加来源轨迹是否成功的标记。

工件：`README.md`、`../../pulled/UpSkill/scripts/run_curator.py`（curator 参考实现）、`../../../papers/working/terminal_skill_literature/2607.16851v1.txt`（AgentBrew 论文 §2.3.3、§2.5）

## 2026-10-10 16:53 — 系统拆解为积木

按用户要求把整个系统拆成可跨阶段、跨 benchmark、跨模型复用的积木：基础件 B1–B5、执行积木 E1、程序积木 D1–D10、模型积木 G1–G9、组合积木 K1–K5，并写出积木规则、阶段与积木的对应表、prompt 与积木的对应。新增待定项：一道题有多次采样时阶段 2 切哪几条轨迹。

工件：`README.md`（第一节"模块拆解"）

## 2026-10-10 17:03 — 可扩展性 review 与目录结构规划

对积木方案再做一次 review，修了 7 个问题：skill 注入下沉到底层以消除同层依赖；跟 benchmark 有关的四样东西打包成插件；执行结果区分成功、失败、基础设施异常，异常不计入成败；Subtask 只存起止步号、不重复存原文；ID 由程序按确定规则生成；库整理加最大轮数 `curation_rounds`；补可靠性与运行锁两个基础件。据此规划分层的源码目录并写入 AGENTS.md。

工件：`README.md`、`AGENTS.md`（新建）

## 2026-10-10 17:12 — 补充仓库管理规范，建立工作日志

按用户要求在 AGENTS.md 中规定：根目录用 log.md 记录每个阶段的工作（日期时分、一段话摘要、相关工件）；过程文档放 `docs/`，按 decisions / reviews / reports / runbooks / notes / archive 分类；脚本放 `scripts/`，按 run / remote / analysis / audit 分类，只做薄调用，方法逻辑只在 `src/`。另补充了根目录白名单、`configs/` 与 `runs/` 的规范、`tmp/` 的用途、测试与 git 的提交前检查。建立本日志并补记今天此前的各阶段。

工件：`AGENTS.md`、`log.md`（新建）

## 2026-10-10 17:19 — 明确迁移原则：按需迁移，老仓库作参考实现

按用户要求把迁移原则写进 AGENTS.md 第十节，拆成两条：一是按需迁移——迁移清单从 README 的积木表推导，设计不需要的（L2 进化闭环、family 发现、冷启动、PPI/claim 等）不迁，避免无用代码扰乱新架构；二是老仓库同时是参考实现——写新代码前凡老仓库有对应实现（异常分类与处置、重试、冻结、原子写、目录锁、用量累计等踩坑产物）必须先看老实现，优先复用或改造，决定不用要写理由进 docs/decisions/，并同步翻 ARCHITECTURE.md、ERROR_HANDLING.md 和外层 AGENTS.md 的事故记录，不重犯已记录的错。

工件：`AGENTS.md`（第十节重写）

## 2026-10-11 09:53 — 实现 README 蓝图：全部七层代码 + 测试落盘

用 17 个子 Agent 分七波并行实现了设计文档描绘的全部系统：core（五个对象、确定性 ID、配置、注册表）、infra（对象存储、manifest 冻结、并行池、异常分类、运行锁、用量累加）、prompts（12 个 prompt 进注册表）、inject（skill 注入）、llm（模型调用单入口、JSON 抽取与格式重试、假模型）、benchmarks（插件底座 + TB/SearchQA/ALFWorld 三插件）、blocks（11 个功能积木文件）、composites（K1–K5）、stages（阶段 0–6）与 pipeline/cli（直线串联、run/summary 两个命令、逐阶段账本）。每个组合积木都有"中途中断再续跑，结果与一次跑完相同"的测试。过程中发现并修复：Store 按首匹配取 ID 会拿错外键字段（回归测试锁死）、我漏排了 blocks/curate.py 导致 K5 干等（补一个 Agent 按其冻结签名实现）、阶段摘要携带 Task 对象撑爆 JSONL 账本（改为 id 列表 + 账本防御性序列化）、composites/__init__ 并发追加互相覆盖（重写补齐五个导出）。最终验证：670 个测试全过、3 个 live 冒烟默认免跑、ruff 与分层检查全绿。收尾整合补了 avg@N 保守口径（与 tb_stats.py 分母一致，条件口径并存）、DEFAULT_SYSTEM/REPAIR_INSTRUCTION 进注册表受冻结、三份设计决定文档。

工件：`src/expedistill/`（全部七层）、`tests/`（单元 + composites + fixtures）、`pyproject.toml`、`.env.example`、`.gitignore`、`docs/decisions/{openai_sdk_v1,stage6_arm_stores,handle_attached_accounting}_20261011.md`、`README.md`（布局补 pipeline_log.jsonl 与 leak_audits.jsonl）

## 2026-10-11 11:44 — 对照设计 review 全部实现，分波修复并经三次独立对抗式复审

对照 README 把 09:53 落盘的实现逐阶段 review 了一遍，列出 3 条严重问题和若干条中等问题，涉及：TB 远程 run id 不含角色和实验臂（阶段 1 弱模型那一臂会直接读到强模型同题同次的结果，阶段 6 与阶段 1 互相撞 id）、验收循环续跑可能把从没考过的旧版本 skill 判为通过、库整理每次调用都从 0 开始给合并编号而覆盖已保留的合并、真实端点下默认客户端第一次运行就报错、配置里没有任务集字段导致 TB 的 full 模式无法配置、阶段 1 和 6 只数返回的轨迹等。修复由并行子 Agent 分几波完成：配置新增任务集字段，规则默认值集中补齐并在阶段 0 校验，manifest 冻结任务内容摘要和确定的 prompt 摘要集合；模型调用加按请求的基础设施重试，非瞬时的 4xx 改判为服务方拒绝，请求落盘加锁且不覆盖；命令行定下退出码 0 / 1 / 3；批量执行加命名空间、远程续跑、重试游标、已存单元核对、熔断和跳过名单；考一次改为先存作答再判卷、判卷失败次数随作答落盘；验收每次改写立即落盘、轮次从已落盘记录推导，只处理阶段 3 的候选；库整理改为先写日志再改状态、编号永不复用、回归考试覆盖全部原始来源；阶段 2–4 对单元失败按类别处置；阶段 1 和 6 按应有单元集合计分、空洞不得静默通过；JSONL 账本加锁追加并修复被打断的末行。之后做了三次互相独立的对抗式 review，每次修完再复审，它们找出了作者自己的测试没有发现的问题：阶段 6 保守口径的测试是往 store 里直接写基础设施异常轨迹，而批量执行实际从不存这种轨迹，测试通过的理由是错的；库整理在合并途中崩溃后，续跑的验收阶段会把还在途中的合并 skill 当成候选、只凭一道考题就验收通过，库里多出一个没经过回归考试的合并 skill；为 TB 坏题加的"接受基础设施空洞"开关最初只对单次调用生效，不带开关续跑会补上空洞、改变失败题集合和库，run 目录随即卡在 "skill bank is stale" 上报废，这个陷阱是修复本身引入的，现改为本 run 的持久决定；熔断按单元计数时，一道永远起不来的题就能触发熔断，其余题一次都不跑且每次续跑都重复，现改为按不同的题计数并按轮次派发；判活和超时终止只看 kill -0，会把复用了同一 pid 的无关进程当成自己的 runner 甚至杀掉，现改为先核对进程环境里的 RUN_ID。据此撤回两条此前的说法：一是 09:53 条目里"补了 avg@N 保守口径"——修复前保守口径在真实数据上与条件口径分母相同，两者的 delta 没有区别，这个口径实际等于没做，按应有单元集合计分后才真正生效；二是"运行设置只影响多努力地去跑、不影响测的是什么"——对接受空洞这一项是错的，其余几项（并发数、重试次数与退避、熔断阈值）仍按运维参数处理、不冻结。最终状态：自己运行 `.venv/bin/python -m pytest -q`，1122 个测试全过，3 个 live 冒烟默认免跑；README 已按代码同步 run 目录布局、阶段 0 的任务集与冻结内容、各阶段单元失败处置、阶段 6 的共同分母与两种口径、各组合积木的续跑规则、模型调用的重试、命令行运行设置与退出码。没做完的：阶段 2"一道题切哪几条轨迹"仍待定；本地执行中模型返回空内容、库整理里找相似和分组的模型输出失败目前会带调用栈退出，命令行没有单独处置；全部改动尚未提交。

工件：`docs/decisions/{stage0_task_set_freeze,call_infra_retry,cli_exit_codes_and_default_client,tb_run_namespace,acceptance_resume,curation_resume,stage6_metrics_denominator}_20261011.md`（新建）、`src/expedistill/core/{config,ids}.py`、`src/expedistill/infra/{store,reliability,usage}.py`、`src/expedistill/llm/{call,client}.py`、`src/expedistill/prompts/__init__.py`、`src/expedistill/benchmarks/{tb,searchqa,alfworld}/plugin.py`、`src/expedistill/blocks/metrics.py`、`src/expedistill/composites/{batch_execute,segment_checked,exam_once,acceptance,curation}.py`、`src/expedistill/stages/{pipeline,stage0_freeze,stage1_baseline,stage2_segmentation,stage3_generation,stage4_acceptance,stage5_curation,stage6_test}.py`、`src/expedistill/cli.py`、`tests/composites/{test_acceptance_resume,test_curation_resume,test_curation_stage4_interleave,test_batch_execute}.py`、`tests/unit/benchmarks/{tb_fake_remote,test_tb_plugin}.py`、`README.md`（第一、二、三节同步）

## 2026-10-11 11:51 — review 收尾：补齐两处失败处置、小任务集熔断阈值，订正过时文档

接着 11:44 那一条做完收尾。最后一轮 reviewer 复查通过：在每个崩溃点中断再续跑（split 与 full 两种模式）、并行数 1 和 4、已接受空洞在不带开关续跑时保持不变、跑完的 run 重跑零次模型调用，都与一次跑完相同。补了 11:44 条目里列为没做完的两处：本地执行中途模型给不出可用回复（如返回空内容）时，这一局记为失败（终止原因 model_output_error，已走的步骤保留），不重试，因为重试等于对被测模型反复重采样直到它答好；模型输出错误逃出某个阶段（库整理里找相似和分组的修复预算耗尽）时，命令行给出明确提示、以退出码 1 停下，已完成的单元都已落盘，重跑同一条命令即重试这次调用。熔断阈值改为取设置值与本次待跑的不同题数中的较小者，只剩一道题时不熔断，否则题少的任务集永远熔不断，而阈值为 1 又会让一道坏题跳过它自己剩下的采样。同时订正了过时的文档：各 20261011 决定文档的"对应 log.md 条目"改为实际的 09:53 / 11:44 条目，stage6_arm_stores、call_infra_retry、acceptance_resume、cli_exit_codes_and_default_client 四份追加了更正节（原文保留），插件契约补了"请求落盘跟随句柄的 run 目录、不属于 runner 文件"，README 同步了上述处置、熔断阈值与退出码。自己运行 `.venv/bin/python -m pytest -q`：1131 个测试全过，3 个 live 冒烟默认免跑。全部改动仍未提交。

工件：`README.md`（第一、三节）、`docs/decisions/*_20261011.md`（条目指向与更正节）、`src/expedistill/benchmarks/base.py`（插件契约 docstring）、`src/expedistill/benchmarks/react_loop.py`、`src/expedistill/cli.py`、`src/expedistill/composites/batch_execute.py`

## 2026-10-11 12:50 — 两套真实端点冒烟（阶段 0–1）跑通，发现五处问题并修复

用真实模型端点对 SearchQA 和 ALFWorld 各跑了一次阶段 0–1 冒烟并复核了续跑。SearchQA（2 题 × 1 采样 × 强弱两臂）4/4 成功，新 run 65.6 秒，用同一 run 目录续跑结果逐字节一致；ALFWorld（2 题 × 1 采样 × 强弱两臂）强、弱两臂各 1/2 成功，59.1 秒，episode 子进程全部回收，续跑零次模型调用。冒烟发现五处问题：模型返回空内容被当 ok 记账；模型调用没有输出 token 上限（推理模型烧穿步预算）；请求落盘和用量账本没记 finish_reason 与 cached_tokens（网关缓存模板把 prompt token 虚高约 300 倍，按角色比成本会失真）；SearchQA 的每局步数写死、不可配置；命令行没有"跑到第 N 阶段就停"的开关。修复：空或全空白回复改判 ModelOutputError（消息带 finish_reason），本地 ReAct 一局中发生时记为失败（终止原因 model_output_error，已走步骤保留）、不重试，因为重试等于对被测模型反复重采样直到它答好；`max_tokens` 默认 5120，可在配置的 `model_options` 里按模型覆盖（extra 合并顺序在后，能盖过默认值）；finish_reason 记进每次请求落盘、finish_reason 与 cached_tokens 记进用量账本；新增 `EXPEDISTILL_SEARCHQA_MAX_STEPS`（默认 6，与 `EXPEDISTILL_ALFWORLD_MAX_STEPS` 对应）；新增 `--until-stage N`（运行设置、不冻结，跑完阶段 0..N 就停，已完成的阶段续跑零次模型调用）。修复后自己运行 `.venv/bin/python -m pytest -q`：1184 个测试全过，3 个 live 冒烟默认免跑。README 与 `.env.example` 已同步（B1 输出上限与空回复、失败账本懒创建、`model_options` 冻结、`--until-stage`、步数环境变量、`ALFWORLD_BENCH_SRC` 目前对桥接环境是可选的）。

工件：`scripts/smoke/smoke_stage01_searchqa.py`、`scripts/smoke/smoke_stage01_alfworld.py`、run `runs/smoke_searchqa_real_20261011_122243/`、run `runs/smoke_alfworld_real_20261011_122515/`、`src/expedistill/llm/{call,client}.py`、`src/expedistill/core/config.py`、`src/expedistill/stages/pipeline.py`、`src/expedistill/cli.py`、`src/expedistill/benchmarks/searchqa/plugin.py`、`README.md`、`.env.example`
