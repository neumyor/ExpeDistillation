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

## 2026-10-11 15:07 — 用 TB2.1 真实 rollout 归档验证数据链路四模块，全量对账通过

用上一轮 campaign 收官归档（node29 `/data2/niuyiming/e1_final_archive/`，890 条计入指标，20GB）对 TB 数据链路做了四项验证：轨迹重建、伪 0 判定、指标复算、视图渲染，外加一次真实 G1 切段调用。采样 14 条归档 trial（含最长轨迹 schemelike-metacircular-eval，3.7MB/428 步）+ 12 条伪 0/基础设施 trial 到本地 tmp/，对 730 条 niuyiming 名下 trial 做了远程只读全量扫描（stdin 管道 python，未在远端写任何文件）。结论：reconstruct 的 action/observation 逐字节可独立复现、配对方向正确（observation 属于同一步的调用）、最长观测 10168 字符原样保留；classify_result 与 campaign 审计在全量 730 条上仅 1 条分歧，即人工复核改判的"agent 改坏沙箱自身网络"trial（预注册规则看不见这一点，判 pseudo_zero 重跑，campaign 判 KEEP_MODEL 计 0）——这是已知的规则边界而非 bug；用 blocks/metrics 从 manifest 复算 avg@5/pass@5 与官方完全一致（qwen 0.469663=59/89、dpsk 0.631461=76/89，分母 89、每题恰好 5 条 VALID+KEEP_MODEL，KEEP_MODEL 计 0）；视图截断标记的省略字符数逐一核对无误。发现的问题：(1) ModelHandle 默认 max_tokens=5120 对 DEEPSEEK_up5zdj 的 G1 调用（约 4 万 token 视图）全被推理烧穿（finish_reason=length、空内容、ModelOutputError 不重试），需在 model_options 里给该模型配 ≥32768 才跑得通（实测 32768 成功，4 段、D7 无错、57.5k in + 4.6k out、61 秒）；(2) 空内容失败路径在读取 response.usage 之前抛错，长度烧穿的调用真实消耗（约 4 万 token）在账本里记 null——信息本可用却被丢弃；(3) reconstruct 会丢弃"有 observation 无 tool_calls"的步（样本中 60/1882：43 条解析失败回合 + 17 条上下文摘要 handoff 的 system 步），真实命令与观测没有丢失，但 G1/D5 看不到"模型输出解析失败"和 handoff 边界这两个行为信号；(4) final_metrics 的 token 总是大于全部步级 metrics 之和（handoff 摘要等不在 steps 里），total_usage 取 final_metrics 是正确选择。estimate_tokens（len//4）对终端 dump 类文本低估约 41%（40702 估 vs 57553 实）。267 条 liyishan 名下 canary trial 按约束未做全量扫描（其中 3 条已读核对一致）。远端零写入、无残留；本地采样保留在 tmp/tbval/（18MB，gitignored）。

工件：`scripts/analysis/tb_archive_sample.py`、`scripts/analysis/tb_archive_validate.py`、`scripts/smoke/tb_g1_segment_smoke.py`、tmp `tmp/tbval/`（manifest/audit/summary + 采样 + g1_smoke 请求落盘）

## 2026-10-11 15:30 — TB 远程链路适配收口：交接对齐、两级冒烟、五模块真数据验证

收到 TB2.1 裸 rollout campaign 的交接材料（docs/tb_rollout_handover_20261011.md，参考实现在外层 scripts/tb21/），对它逐项核对（11 项）后发现并修掉两处代码与 campaign 契约不符：classify_result 的 tmux 规则只匹配消息、不查异常类型（campaign 的 audit_trials.py 要求必须 BE RuntimeError 才算模型弄坏 tmux——qemu 两题的 RuntimeError tmux 按键失败正是 KEEP_MODEL 的判例），现加类型门并有四个方向的回归测试；默认 deadline 10800s 低于 campaign 开关下单 trial 自身的最坏情况 15480s（agent 7200 + verifier override 7200 + setup 360×3），会把合法在跑的 trial 中途误杀，改为 16200s。环境接线不改远程代码包（runner 默认的 TBENCH_VENV_DIR=<scripts>/.venv 在 node29 不存在，真实 venv 在 ~/agentbrew-tb21）：插件提交前 source 我们 root 下的 env.sh（venv 指向 + OPENAI_API_KEY 由 TBENCH_LLM_KEY 派生），由 DRY_RUN 冒烟一次性部署。两级冒烟全过：DRY_RUN（runner_env 真实提交环境 + DRY_RUN=1，生成并校验 Harbor job YAML，不起沙箱）后跑单题 live 冒烟（git-leak-recovery，弱模型 qwen3.6-flash-distill，runs/smoke_tb_live_20261011_150323/）：bare 一次 130s 成功、reward=1.0；同一 run id 二次调用复用已完成结果（resumed=result、零重提交）；skills 模式史上首次在新网络下验证通过——SKILL.md 经 scp 上传、注入沙箱 /opt/openclaw-skills/（skill_activation.json 与轨迹 step 0 均指认），进程中途崩溃后同一 run id 重连存活 runner（resumed=alive、零重提交）并跑完拿到 1.0；三行用量账目与 final_metrics 一致。用收官归档（890 条）做真数据验证（15:07 条目详述）：轨迹重建逐字节可复现、伪 0 规则在 730 条全量上仅 1 条分歧（agent 改坏沙箱自身网络，人工改判类，属已知边界）、指标复算与官方完全一致（qwen avg@5=0.469663、dpsk 0.631461）、视图渲染核对无误、真实 G1 切段在 max_tokens=32768 下成功（5120 会被推理烧穿成空内容）——TB 实验配置须给 DEEPSEEK_up5zdj 设 model_options {"reasoning_effort": "max", "max_tokens": 32768}。过程中顺手修掉两处记录缺口：空内容失败路径在读取 response.usage 之前抛错，长度烧穿调用的真实消耗现在随 ModelOutputError 记入账本（不再记 null）；reconstruct 丢弃的"有观测无动作"步新增分类计数 reconstruct_skip_counts（解析失败回合 / handoff 系统步 / 其他），写进 meta，行为信号不再静默丢失。已知边界（有意接受，见决定文档）：伪 0 规则看不见"agent 自毁沙箱网络"，会补跑一次，反复同形态需人工复核；estimate_tokens（len//4）对终端 dump 低估约 41%（40702 估 vs 57553 实），bank_token_budget 实际比名义更宽松；TBENCH_E2B_RELAY_TEMPLATE 是死变量（真开关 HARBOR_E2B_TEMPLATE，默认 code-agent-new，我们未用）；容量指引——K1 workers 即并发冷启动沙箱数，campaign 头号死因是整批冷启动，正式 run 从 ≤2 workers 起步、盯 infra_error 率。自己运行 .venv/bin/python -m pytest -q：1196 个测试全过，3 个 live 冒烟默认免跑。全部改动尚未提交。

工件：`docs/tb_rollout_handover_20261011.md`（收到的交接材料）、`docs/decisions/tb_adaptation_20261011.md`（新建，九项决定）、`docs/decisions/tb_run_namespace_20261011.md`、`scripts/smoke/smoke_tb_dryrun.py`、`scripts/smoke/smoke_tb_live.py`、`scripts/analysis/tb_archive_sample.py`、`scripts/analysis/tb_archive_validate.py`、`scripts/smoke/tb_g1_segment_smoke.py`、run `runs/smoke_tb_live_20261011_150323/`、`src/expedistill/benchmarks/tb/plugin.py`、`src/expedistill/benchmarks/tb/trajectory.py`、`src/expedistill/llm/client.py`、`src/expedistill/llm/call.py`、`src/expedistill/infra/reliability.py`

## 2026-10-11 16:35 — 蒸馏链路（阶段 0–4）用真实归档轨迹整链跑通，含续跑零调用验证

新脚本 scripts/smoke/smoke_tb_distill_chain.py 选题 adaptive-rejection-sampler（TB2.1 campaign：qwen 0/5 全败、dpsk 3/5 过，真实的蒸馏场景），把归档中强模型最小成功 trial（dpsk 04，reward 1.0，22 交互步）与弱模型有效失败 trial（qwen 03，reward 0.0，223 交互步）经 trajectory.py 重建后按 ID 规则播种进新 run 目录（n_attempts 各 1，K=5，full 模式，model_options 给 DEEPSEEK_up5zdj 配 reasoning_effort=max + max_tokens=32768），再经 CLI 跑 --until-stage 4 --workers 2。结果：阶段 1 因两个单元都已存而零模型调用跳过（work/ 无产物、无 failed_units、usage 首行即 SEGMENT）；阶段 2 两条轨迹各切 5 段共 10 个 subtask，D7 覆盖无缺口；阶段 3 选中 9 段（weak/seg01 因 success=True 被 strong_all_weak_failed 正确排除）、9 对 skill+考题全部生成、零失败；阶段 4 处理 9 个候选：1 个接受（strong/seg03，v1 判错→G6 重写"misread"→v2 答对）、7 个 weak_already_knows（弱模型裸答即对）、1 个 retry_exhausted（weak/seg00，三轮全错：弱模型反复建议把无界支撑截断成有限区间——恰是它在真实 rollout 里犯的同一个错）。判卷质量高（引例：weak/seg00 的 judge 精确区分"需要有限初始锚点"与"截断支撑"）；考题换皮到位（slugkit 测试格式化题完全脱离 R/ARS 表面）。发现的问题：(1) 判卷的 error_description 有时会泄露修法（strong/seg03 v1 的理由直接写出"唯一修法是让 detail 只在失败时打印"，G6 重写拿到的就是答案）；(2) 弱模型裸答正确率 7/9 偏高，考题对文本 QA 型弱模型偏容易，接受率被压低；(3) qwen 的一次 ANSWER 调用 300 秒撞上客户端默认 timeout_sec=300 报 InfrastructureError，靠句柄 infra_retries=3 重发成功（重试机制按设计工作，但 300s 上限对弱模型长答案偏紧）。全链 52 次模型调用（上限 60 内），in 285,071 / out 146,089 token，模型调用跨度 34.2 分钟、全程约 39.6 分钟；同命令重跑续跑验证零模型调用、processed_this_call=0、各阶段摘要一致。run 目录 runs/smoke_tb_distill_20261011_155311/。

工件：`scripts/smoke/smoke_tb_distill_chain.py`、run `runs/smoke_tb_distill_20261011_155311/`（config.yaml、manifest、stage1–4 摘要与全部对象、52 条 llm_requests 落盘与 usage 账本）

## 2026-10-11 16:41 — 蒸馏链路（阶段 2–4）真实数据整链验证通过，收口小修

用归档 TB 轨迹播种（qwen 全失败、dpsk 成功的真实能力缺口题 adaptive-rejection-sampler），
`--until-stage 4` 整链真实模型跑通：切段 D7 零缺口、9 对 skill/考题、验收 1 接受 /
7 weak_already_knows / 1 retry_exhausted（弱模型在考题上复现 rollout 同款错误，判卷公正不泄答案），
52 次调用 / in 285k / out 146k / 39.6 分钟，整链重跑零模型调用。修复：JUDGE 模板禁止
错误描述携带修法（README 同步）；`EXPEDISTILL_LLM_TIMEOUT_SECONDS` 超时开关。
裸答 7/9 的设计层问题按用户决定暂不处理，记入 tb_adaptation 决策文档待多题分布再定。

工件：`scripts/smoke/smoke_tb_distill_chain.py`、`runs/smoke_tb_distill_20261011_155311/`、
`src/expedistill/prompts/exam.py`、`src/expedistill/llm/client.py`、
`docs/decisions/tb_adaptation_20261011.md`（新增"已知观察"节）、`.env.example`。
pytest 1202 passed，3 个 live 冒烟默认免跑。
## 2026-10-11 16:42 — 修复试点发现的两个问题：SearchQA 预登记辅助指标 sub_EM；K5 无合并轮也跑 G9 分组

试点（pilot）在 SearchQA 上发现主指标 EM 过严（答案 "lumbar" 对金标 "lumbar vertebrae" 记 0），按子串匹配 sub_EM 结论会翻转。用户决定把 sub_EM 预登记为辅助指标：benchmark 插件契约新增可选声明（`benchmarks/base.py` 的 `aux_metric_name` 属性与 `aux_success(traj)` 方法，默认 None），SearchQA 插件声明 `sub_em` 并读轨迹 meta 里评测仓库算好的子串分；阶段 0 把声明的名字冻结进 manifest 顶层 `aux_metric`（不是配置字段），改声明即拒绝旧 run 续跑；D3 在同一套应有单元分母上另算辅助 avg@N（与主指标同一对口径，缺失/基础设施单元不可能算辅助成功），阶段 1 两臂的 summary 与阶段 6 的两臂及 delta 各增加 `aux` 一节，未声明时为 null、零额外工作。缺失 meta 的取舍已写进决定文档：从未提交答案的失败轨迹 sub_EM 记 0（真实的 0），成功或已提交答案却没有评测 meta 的轨迹按数据损坏停下。已知局限：打分器是代码不是配置，改它不改变 prompt digest，旧 run 不会因此拒绝续跑。第二个问题：README 阶段 5 第 4 步说 skill 总会分组，但 K5 在"无合并候选且已在预算内"时提前跳出、跳过了 G9，库里成员 group 全是 None。修复为只要库里有 ≥ 2 条成员就跑 G9（无合并轮也跑，输出照常写进整理日志、续跑零次模型调用原样重放），只有 1 条成员或空库才跳过。测试先行：metrics / 插件契约 / 阶段 0 / 阶段 1 / 阶段 6 / 整理的新测试先失败再实现。运行 `.venv/bin/python -m pytest -q tests/unit tests/test_layering.py`：本会话负责的测试全过；剩余 18 个失败全部集中在 `tests/unit/stages/test_pipeline.py`，由并行的泄题审计改造（另一批文件）造成，不属本次改动。全部改动未提交。

工件：`src/expedistill/benchmarks/base.py`（辅助指标契约）、`src/expedistill/benchmarks/searchqa/plugin.py`、`src/expedistill/blocks/metrics.py`、`src/expedistill/stages/stage0_freeze.py`、`src/expedistill/stages/stage1_baseline.py`（`aux_wiring` 共享接线）、`src/expedistill/stages/stage6_test.py`、`src/expedistill/composites/curation.py`、`tests/unit/blocks/test_metrics.py`、`tests/unit/benchmarks/{test_base,test_searchqa_plugin}.py`、`tests/unit/stages/test_stage{0,1,6}.py`、`tests/composites/{test_curation,test_curation_resume}.py`、`README.md`（D3、阶段 0 冻结、阶段 1 / 6 辅助指标合同、阶段 5 第 4 步）、`docs/decisions/aux_metric_preregistration_20261011.md`

## 2026-10-11 16:50 — D8 泄题审计换成 LLM 判官：判决按（skill, version）落盘、首判为准

机械审计（子串重合 + `leak_threshold`）在真实 pilot 上系统性误报（无词边界的缩写匹配、引号里的协议 token（动作名 "Finish"、环境反馈 "answer is incorrect."）被当成受保护实体），杀掉了合法 skill。改为语义判官：`blocks/audit.py` 的 `audit_leak` 一次调用、prompt `LEAK_AUDIT_JUDGE`，泄露判定标准（含/平凡改写/直接揭示参考答案与考题答案特有实体）与非泄露豁免（通用方法建议、协议词、动作名、环境反馈串）写进 prompt；新角色 `auditor` 加入 MODEL_ROLES、默认取强模型；`leaked` 闭集校验 + 非空 `reason`，违反即 ModelOutputError，沿阶段 4 既有处置终态丢弃（model_output_error）。判官不可复现，续跑等式改为落盘回放：每个 (skill_id, skill_version) 的判决恰一行写入 `stage4_acceptance/leak_verdicts.jsonl`、落盘先于本轮行动，续跑查表复用、绝不重问（首判为准）；旧 `leak_audits.jsonl` 并入该账本（`leaked=true` 行即作废簿记），避免双账本漂移。理由永不复述参考答案：它是 G6 改写的错误描述输入，而 G6 不得看到答案——这是 prompt 的硬约束。`leak_threshold` 字段与校验删除，from_dict 丢弃未知键故旧配置仍可加载。两个设计取舍：账本合并（单一事实来源）；判官格式错误传播给阶段 4 单元处置（与 K3 修复预算耗尽同类，无组合级重试）。已知局限：auditor 默认与写 skill 同为强模型（判官自审），换配置角色表即可打破，README 已标注。测试先行：判官积木 / 判决落盘与复用 / 崩溃注入续跑（含判决落盘与轮次推进之间的崩溃）/ 角色默认 / 字段删除的新测试先失败（188 个）再实现。运行 `.venv/bin/python -m pytest -q`：1235 过、18 败，失败全部在 `tests/unit/stages/test_pipeline.py`（其 `_script` 尚未识别新 prompt，属并行改动的文件），另有 1 个 ruff F841 预先存在于 `tests/unit/stages/test_stage1.py`（基线即有）。全部改动未提交。

工件：`src/expedistill/blocks/audit.py`、`src/expedistill/prompts/audit.py`、`src/expedistill/prompts/__init__.py`、`src/expedistill/core/config.py`、`src/expedistill/composites/acceptance.py`、`src/expedistill/stages/stage4_acceptance.py`（最小改动：auditor 句柄接线）、`src/expedistill/stages/stage5_curation.py`（最小改动：透传占位）、`src/expedistill/blocks/__init__.py`、`tests/unit/blocks/test_audit.py`、`tests/composites/{test_acceptance,test_acceptance_resume,test_curation_stage4_interleave}.py`、`tests/unit/core/test_config.py`、`tests/unit/prompts/test_templates.py`、`tests/unit/stages/test_stage4.py`、`docs/decisions/leak_audit_llm_judge_20261011.md`、`README.md`（1.3 D8 行、阶段 4 审计与单元失败两条）

## 2026-10-11 16:55 — K5 签名清理：删除从未使用的 leak_threshold 形参

泄题审计改为 LLM 判官（`docs/decisions/leak_audit_llm_judge_20261011.md`）后，`leak_threshold` 配置字段已删除，`curate_bank` 里那个从未被使用的同名必填形参成了遗留：D8 那波改动让阶段 5 传占位值 0.0 顶住签名。本次把它彻底删掉：`composites/curation.py` 的 `curate_bank` 签名、模块与函数 docstring 里的说明、`stages/stage5_curation.py` 的占位传参一并去除，grep 确认仓库内无其他调用方（`tests/unit/core/test_config.py` 里只剩 D8 自己关于配置字段已删的字符串）。README 阶段 0 的冻结清单同步删去已不存在的"泄题阈值"一项。运行 `.venv/bin/python -m pytest -q`：1253 个测试全过，3 个 live 冒烟默认免跑。全部改动未提交。

工件：`src/expedistill/composites/curation.py`、`src/expedistill/stages/stage5_curation.py`、`tests/composites/{test_curation,test_curation_resume}.py`、`README.md`

## 2026-10-11 16:50 — 两个 benchmark 全链路试点（阶段 0–6，真实模型）跑通

用真实 CLI `expedistill run` 与真实端点（强 DEEPSEEK_up5zdj / 弱 qwen3.6-flash-distill）完成 SearchQA 与 ALFWorld 的全流程试点，均逐段 `--until-stage 1..6` 推进、跑完原样续跑零成本验证。SearchQA（`configs/experiments/pilot_searchqa.yaml`，6 题）：F=2、库 1 条 skill（236 token）、delta=0（1 题转好 1 题转差抵消；EM 主口径下近失算全错，催生 sub_EM 预注册辅助口径）；ALFWorld（`configs/experiments/pilot_alfworld.yaml`，4 题）：F=1、库 1 条（263 token）、delta=+0.25（F 题 7 步由败转胜，n=1 无统计意义）。成本：SearchQA 37 分钟、75 请求、in 11.6 万 / out 5.9 万；ALFWorld 111 分钟（约 80 分钟耗于端点对弱模型请求挂起、每次烧满 300s 超时）、165 请求、in 31.1 万 / out 15 万。

试点发现并随后修复（各条由子代理测试先行落地，见当日 16:42 / 16:55 等条目）：① 客户端超时不可配 → 新增 `--llm-timeout` 运行参数；② 错误路径落盘丢 finish_reason → 客户端异常携带该值；③ summary 缺 cached_tokens → 按角色折叠输出；④ 机械泄题审计误杀（缩写无词边界、协议词当保护实体，两试点共误杀 1-2 个 skill）→ 按用户决定整体替换为 LLM 判官（`auditor` 角色，判决按（skill, 版本）落盘、首判为准，理由不复述参考答案，`leak_threshold` 配置项删除，`docs/decisions/leak_audit_llm_judge_20261011.md`）；⑤ EM 近失问题 → sub_EM 预注册为辅助口径（插件声明、manifest 冻结、与主口径并列报告永不事后挑选，`docs/decisions/aux_metric_preregistration_20261011.md`）；⑥ K5 无合并轮跳过分组与 README 不符 → ≥2 条成员必跑 G9。待办：阶段 6 曾发生一次驱动进程无痕死亡（无 traceback / 崩溃报告，续跑兜住，未定位）；考题难度（6/9 候选因弱模型裸答即对被弃）按用户决定暂不改，待更大试点量化。运行 `.venv/bin/python -m pytest -q`：1253 个测试全过，3 个 live 冒烟默认免跑。

工件：`configs/experiments/pilot_searchqa.yaml`、`configs/experiments/pilot_alfworld.yaml`、`runs/pilot_searchqa_20261011/`、`runs/pilot_alfworld_20261011_150000/`；修复涉及 `llm/client.py`、`llm/call.py`、`blocks/audit.py`、`prompts/audit.py`、`composites/acceptance.py`、`composites/curation.py`、`blocks/metrics.py`、`benchmarks/base.py`、`benchmarks/searchqa/plugin.py`、`stages/{pipeline,stage0_freeze,stage1_baseline,stage4_acceptance,stage5_curation,stage6_test}.py`、`cli.py`、`infra/usage.py`。

## 2026-10-11 17:05 — 合并主线（3db8447 试点修复）到 dev，统一超时开关

把主线新提交 3db8447（SearchQA/ALFWorld 全链路试点后的六项修复：LLM 判官泄题审计、
sub_EM 预注册辅助指标、--llm-timeout、finish_reason/cached_tokens 记账、K5 ≥2 成员必跑
G9、试点配置）合入 dev。冲突四处：llm/call.py 与 client.py 取两侧并集（TB 的 usage 随异常
携带 + 主线的 finish_reason 记录与超时参数化）；test_client.py 保留 TB 的超集测试、删主线
被覆盖的两条；log.md 双方条目按时间全保留。一处取捨：TB 侧临时的
EXPEDISTILL_LLM_TIMEOUT_SECONDS 环境变量删除，统一走主线 --llm-timeout 运行参数——
pipeline 总是显式传超时，环境变量会被默认值静默盖掉，留双开关是陷阱（决定文档已加更正注）。

## 2026-10-11 18:35 — 三 benchmark 合并后验证：链路全通，配置回填 max_tokens 32768

合并主线（35d2c24）后三个子代理并行验证。TB（2 快题 × 强弱臂，--until-stage 1，真实远程，
runs/tb_full_stage01_smoke_20261011_1701/）：4 trial 全成功、5.3 分钟、零 infra 重试零熔断，
命名空间互异、attempt-major 派发、每 trial 一行记账与 final_metrics 逐位一致、远程无孤儿、
续跑 1 秒零调用。SearchQA（3 次完整 0–6 run + K4 泄题审计直驱冒烟，runs/smoke_searchqa_full_*
与 smoke_leak_audit_20261011_175552/）：LLM 判官审计判泄露/不泄露及 G6 作废重写闭环验证通过、
理由不复述答案（程序化断言）；sub_EM 冻结与并列报告确认并实测动机场景（Lumbar vs lumbar
vertebrae）；cached_tokens 按角色汇总确认；4 次续跑零调用。ALFWorld（2 次完整 0–6 run，
runs/smoke_alfworld_merge*_20261011/）：照抄旧 pilot 配置的 run 1 切段六发六空（5120 被推理
烧穿）整库为空——实锤配置缺口；run 2 配 max_tokens 32768 后全链通（库 1 条、阶段 6 两臂 2/3、
delta 0），子进程全收割零残留，空 content 记 failure（termination=model_output_error）不断流程。
端点侧 300s 服务端掉线仍在（两 run 共 5 次、约 25 分钟），infra 重试全部兜住，--llm-timeout
未触发（服务端先掉线，参数对这类挂起不省时间）。修复：两个 pilot 配置回填
model_options.DEEPSEEK_up5zdj.max_tokens=32768（照抄旧配置会得到空库）。三条观察（无代码
bug）：n_attempts=1 时 F 不稳定（同题两次 run 一次 F=2 一次 F=∅）；weak_already_knows 主导
验收漏斗（SearchQA 4/4、TB 7/9、ALFWorld 4/5），考题难度问题待多题分布；严格 EM 对强模型
同样严苛，佐证 sub_EM 预注册。

工件：`configs/experiments/pilot_{searchqa,alfworld}.yaml`（max_tokens 回填）、
`scripts/smoke/launch_detached.py`、`scripts/smoke/smoke_leak_audit_k4.py`、
run `runs/tb_full_stage01_smoke_20261011_1701/`、`runs/smoke_searchqa_full_*`、
`runs/smoke_leak_audit_20261011_175552/`、`runs/smoke_alfworld_merge*_20261011/`。

## 2026-10-11 22:31 — TB21 全量实验：stage-1 归档播种脚本、实验配置与 F@3 基线对照全过

为 TB21 全量 campaign 完成三件事并把 stage 1 播种跑到底。（1）离线基线：预注册
"每臂取归档 rank 1..3 作为 attempt 0..2"口径，写 `scripts/analysis/tb21_f3_baseline.py`
从 campaign manifest（445+445 行，与审计逐行对上：reward 在则 VALID 同值、缺失则必为
KEEP_MODEL）算出 F=36 题、弱臂 avg@3=40/89≈0.4494 / pass@3=53/89、强臂 avg@3=169/267≈0.6330 /
pass@3=73/89、F 中强臂 rank1-3 有成功的 20 题；脚本用精确分数手算与仓库 D3/D4 积木各算一遍，
两套逐项一致才落盘 `docs/tb21_f3_baseline_20261011.md`（后附播种 run 的对照结果）。
（2）新脚本 `scripts/run/seed_tb_stage1.py`：走真实 CLI 冻结 manifest（--until-stage 0），
归档里缺的两条轨迹（qwen extract-moves-from-video rank01、qwen path-tracing rank02，均为
KEEP_MODEL 的 tmux 未装好即死 trial，agent 目录为空）探测一次记进缓存 absent_units.json，
其余 532 条 trajectory.json 单条 ssh tar 流拉进 `tmp/tb_seed_cache/`（原子落盘、可续传、
远端只读），reconstruct/reconstruct_skip_counts 重建后按 traj/tb:<task>/<role>/<NN> 的 ID
规则存入 store，model_name/skills_injected 与 K1 的已存单元校验逐字段一致，meta 记
rank/来源/skip 计数与 result.json 的 timing；幂等：重跑全跳过零下载。（3）新配置
`configs/experiments/tb21_full.yaml`（89 题、n_attempts 3/3、K5、strong2_weak2 轨迹规则、
DEEPSEEK_up5zdj max_tokens 32768、qwen 弱模型画像取材审计：11/13 次 KEEP_MODEL 死在
tmux 按键层、2 次 AgentTimeout、avg@5 0.47 对 0.63）。先跑 3 题干跑
（runs/tb21_seed_dry_20261011_1/，覆盖成功/失败/KEEP_MODEL/缺轨迹四条路径，复跑零下载，
stage 1 零调用 0.9 秒，summary 与手算逐位一致），再全量
run `runs/tb21_full_20261011_2242/`：534 单元全播种（515 文件 231.6MB、189 秒下载），
`--until-stage 1 --workers 8` 零模型调用（llm_requests/ 空、无 work/、无 failed_units），
stage-1 summary 与基线逐项一致：F 36 题清单逐项相同、弱 120/强 169 次成功、pass 53/73、
每题 3 valid 0 infra；avg@3 浮点在第 16 位有 1 ulp 差（D3 先求和后除 vs 单次除法的舍入
顺序，整数不变量完全相同），判为一致。远端全程只读（归档在 22:00 后零改动、无残留）。

工件：`scripts/run/seed_tb_stage1.py`、`scripts/analysis/tb21_f3_baseline.py`、
`configs/experiments/tb21_full.yaml`、`docs/tb21_f3_baseline_20261011.md`、
run `runs/tb21_full_20261011_2242/`（全量播种 + stage 1）、`runs/tb21_seed_dry_20261011_1/`
（3 题干跑）、缓存 `tmp/tb_seed_cache/`（532 条轨迹 + absent_units.json）。

## 2026-10-12 02:15 — TB 正式实验启动；首夜崩溃（网关空响应体）根源修复

TB21 阶段 2–5 于 22:35 开始（阶段 0–1 播种零调用、F@3 基线对账一致，见 docs/tb21_f3_baseline_20261011.md）。
02:01 进程死亡：网关对一次 200 返回了空响应体，openai SDK 在 create() 内部抛出裸
JSONDecodeError，而 llm/client.py 的异常边界只翻译 APIStatusError/APIConnectionError 两类，
协议层异常穿透到顶层。根源修复：客户端边界补全翻译——SDK 调用期间抛出的协议类异常
（json.JSONDecodeError、APIResponseValidationError）一律转 InfrastructureError 交给重试预算，
不裸抛（该边界本来就拥有全部翻译职责，任何 benchmark 的端点协议故障都不该打死进程）。
先写两个复现测试（裸 JSONDecodeError 与 schema-invalid 响应）确认失败再修。当晚端点很不稳
（3.5 小时内 17 次 300s 超时，infra 重试全部兜住），崩溃前已存 415 个 subtask、审计 2 条
（两条 0 步轨迹按预期跳过）。同命令续跑，已完成单元零重跑。

工件：`src/expedistill/llm/client.py`、`tests/unit/llm/test_client.py`、
run `runs/tb21_full_20261011_2242/`。pytest 1277 passed。

## 2026-10-12 09:40 — 强模型端点重路由（用户授权的等价替换）

DEEPSEEK_up5zdj 部署自 02:01 起故障（对一切请求返回空响应体，小时级仅约 4 次成功窗口；
弱模型全程健康）。用户确认 deepseek-v4-flash-0731--ali 与 DEEPSEEK_up5zdj 指向同一模型
（deepseek-v4-flash-0731）并授权替换。因模型 id 冻结在 manifest、也写在 267 条已播种强臂
轨迹的 model_name 里，做了一次外科手术式等价重路由（零代码改动）：manifest 的 model_roles/
config.models/config.model_options 键、267 条轨迹记录、三份实验配置全部替换；run 内活跃
记录 grep 确认无旧 id 残留（历史账本行按历史保留）。stage0 冻结比对通过，v3 驱动续跑，
首个 SEGMENT 调用即成功。设计缺口记录："实验身份"与"端点路由串"耦合——provider 换路由
就要动冻结记录；后续可考虑把路由映射做成运维层机制（配置保留规范模型名，发送时翻译）。
此前一夜：22:35–02:01 切段 103 条轨迹（415 subtask）后崩溃一次（已修，见 02:15 条目）；
02:01–09:33 端点故障期间重试机制消化全部故障、失败单元 22 条零损失待补。

工件：`configs/experiments/{tb21_full,alfworld_skillopt_split,searchqa_skillopt_split}.yaml`、
run `runs/tb21_full_20261011_2242/`（manifest 与轨迹记录重路由）。

## 2026-10-12 10:25 — 阶段 2–4 并行化（B5 run_units），TB 实验暂停待续跑

TB 实验暂停（09:48，589 subtask 已落盘）后按用户决定做并行化改造：暂停时独立审查
（runs/tb21_full_20261011_2242）结论"可原样续跑"——534 轨迹/589 subtask 逐文件校验通过，
三次驱动重启对账闭合（87+29+9 条轨迹），重路由零残留；剩余阶段 2 = 19 条轨迹/约 20 次调用，
阶段 3 候选 ≈477。改造（三个代理并行 + 一个统一代理）：B5 新增共享单元池 run_units
（有界线程池、输入序聚合、致命错误停止派发、在飞单元跑完、**按单元序解包第一个致命异常、
ParallelError 全量上下文链为 cause**——该浮出规则统一适用于所有异常类型，三阶段行为一致；
K1 的 _reraise 保留旧分裂，统一是后续工作）；阶段 2（单元=轨迹）、阶段 3（单元=subtask）、
阶段 4（单元=skill 对）逐单元循环改为池派发，单元粒度/落盘/续跑语义/summary 聚合不变，
workers 取 run 设置（默认 1 = 原串行行为，既有测试零改动全过）。每个阶段配 workers 1 vs 4
等价性测试（store/summary 逐字段一致）、Barrier 并发证明、单元失败隔离、部分落盘续跑等价。
预计阶段 3+4 从串行约 42 小时缩到 8 并发约 5–6 小时。
更正（应审查要求）：09:40 条目"22:35–02:01 切段 103 条轨迹"应为 87 条（415 subtask 不变）。
播种脚本强臂模型 id 同步为重路由后的新 id。

工件：`src/expedistill/infra/parallel.py`、`src/expedistill/stages/{stage2_segmentation,stage3_generation,stage4_acceptance}.py`、
`tests/unit/infra/test_parallel.py`、`tests/unit/stages/test_stage2_parallel.py`、
`tests/unit/stages/test_stage3.py`、`tests/unit/stages/test_stage4.py`、
`scripts/run/seed_tb_stage1.py`。pytest 1304 passed。

## 2026-10-12 17:25 — 网关 300 秒非流式切断线：客户端改内部流式传输（根源修复）

TB 阶段 3 收尾时发现顽固单元 write-compressor/strong/00/seg00 死循环：请求本身正常大小
（44k 字符），但该内容令强模型满预算思考（流式实测 414 秒、13387/32768 token、内容为空、
finish_reason=length），而网关对非流式请求在 300.3 秒服务端切断（v8 在客户端超时 900s 下
仍精确 300 秒报 APIConnectionError 证实）。300 秒超时把"慢思考"误分类为基础设施错误 →
无限重试。决定性实验：同一请求流式重放 414 秒完成。修复（系统层）：OpenAICompatClient
的 complete() 改为内部流式 + 手工聚合（组装纪律复用老仓库 GPTWrapper：delta.content/refusal
跨 chunk 累积、usage 取自 include_usage 的末 chunk），公开契约不变；所有 reasoning-max
调用从此不受网关 300 秒天花板约束。该单元修复后首次调用即按设计得到模型输出终态失败
（与同批 18 个同类一致，损失 1/470≈0.2%），阶段 3 收尾。附带运维决定：--llm-timeout 900
（满预算思考约 600 秒 + 余量）。测试：流式决策回归 + 多 chunk 组装 + usage 捕获 + refusal
累积 + 中途断流，1309 passed；stub 层机械改造，43 个既有调用点零改动。

工件：`src/expedistill/llm/client.py`、`tests/unit/llm/test_client.py`、
`tests/unit/stages/test_pipeline.py`（stub 机械适配）、
run `runs/tb21_full_20261011_2242/`（诊断证据 dumps 2946-2953）。

## 2026-10-12 20:36 — 预算决策（4096→96000）、阶段 5 波次并行化、TB 换新 run 续跑

两项用户批准的决策落地。(1) 预算：TB 阶段 5 实测 102 条成员 ≈80k 估算 token vs 冻结预算
4096，而 MERGE 合同"一条建议不丢"（压缩率 ~0.98），数学上不可能达标——阶段 5 注定
ConfigError。bank_token_budget 的角色重定义为"上下文溢出安全阀"（docs/decisions/
bank_token_budget_20261012.md），TB 开新 run（tb21_full_v2_202544，manifest 与旧 run 仅
预算一处不同），ALFWorld/SearchQA 在各自实验启动前统一预注册 96000。(2) 阶段 5 并行化：
五份同题独立评审一致否决"都保留+批末推导"（G7 冲突对实际大量重叠、同考题 attempt 竞态、
批末推导破坏续跑等价、跨 benchmark 方法混杂），收敛到"并行计算+有序提交"：波内单元互不
共享成员、run_units 并行，波间按序提交，产出与串行逐位一致（实现代理并修正了设计稿的
朴素贪心不保 first-wins 的缺陷——受约束贪心 wave(j)=1+max(更早重叠单元)）；调度期序号
预分配+烧号、重复源组去重（两项 v2 语义变更已记录）、dispatch map 落盘作在途识别、
journal 单写者+定序、schema v3；单元内回归考题保持串行（保早停）。等价性：5 场景
workers 1 vs 8 全量逐位相等；多在途崩溃注入 4 点，续跑与一次跑完一致且调用数精确；
既有 61 个串行测试零改动通过。附带加固：exam_once 按考题粒度互斥（修复前实测竞态
——两线程同 serial 互相覆盖、零报错）。TB fork：scripts/run/fork_run_stage04.py 复制
阶段 0–4 产物到新 run（含阶段 5 已保留合并，新阶段 5 从 102 成员继续），干跑验证
零调用、各阶段复算与旧 run 逐项一致。期间两个 fork 脚本 bug 被发现修复（argv 空格、
Store 空目录骨架误判"已复制"），以及本条记录的干跑漏复制 generation_failures.jsonl
导致 18 个终态单元被误重生成（2 个产物已清理，usage 5 行真实消耗保留不篡改）。

工件：`src/expedistill/composites/curation.py`、`src/expedistill/composites/exam_once.py`、
`src/expedistill/stages/stage5_curation.py`、`tests/composites/test_curation_parallel.py`（新，28 测试）、
`tests/composites/test_exam_once.py`、`configs/experiments/tb21_full_v2.yaml`、
`configs/experiments/{alfworld_skillopt_split,searchqa_skillopt_split}.yaml`、
`scripts/run/fork_run_stage04.py`、`docs/decisions/bank_token_budget_20261012.md`、
run `runs/tb21_full_v2_202544/`。pytest 1339 passed。

## 2026-10-12 21:40 — 空内容与坏 JSON 同类化：输出预算内重问（G7 烧穿事故的根源修复之一）

TB v2 新 run 的首次 G7 调用（输入 74.5k 真实 token）把 32768 输出预算全部烧在思考上、
空内容返回（finish_reason=length），而 call_model 对"格式坏"有 3 次有界重问、对"空内容"
却是零重试直接判死——不对称是实现顺序的意外而非设计。修复：output_json 调用中，客户端
携带 usage/finish_reason 附件的 ModelOutputError（空内容路径的精确指纹）消耗同一个
max_format_retries 预算、以相同模式重问（原文 + 空结果说明 + 修复指令）；纯文本调用与裸
ModelOutputError 保持立即穿透（阶段 2–4 的单元处置语义不变）。实测同调用此前成功过——
非确定性烧穿靠重问吸收。与输出预算 32768→65536（三份配置已预注册）互补：预算翻倍让思考
装得下，重问兜住偶发烧穿。测试先行：新增 9 个（6 个修前失败），全量 1348 passed。
规划中（等用户批复）：token_budgets 统一管理方案（按角色的输入/输出预算、单一生效路径、
调用前输入测量与超支证据落盘——依据 4872 次真实调用的 in/out 分布实测，含一次 54 万
token 输入与一次弱模型 87k 输出超上限未被端点执行的证据）。

工件：`src/expedistill/llm/call.py`、`tests/unit/llm/test_call.py`、
`configs/experiments/*.yaml`（max_tokens 65536 预注册）。

## 2026-10-12 21:22 — token_budgets 按角色预算表迁移落地；补记已删除的 v2 run；旧 run 标记作废

按已批复的 token_budgets 设计把三份实验配置（tb21_full_v2、alfworld_skillopt_split、
searchqa_skillopt_split）迁移到统一预算段（评审收敛值，各实验启动前预注册：
default_max_output_tokens 65536、max_input_tokens null——v1 输入只测量不设阈值；
强模型八个角色 65536，qwen 的 executor_weak/answerer 5120），deepseek-v4-flash-0731--ali 的
model_options 只保留 reasoning_effort: max（max_tokens 移出，行为保持不变：qwen 角色此前即走
客户端 5120 默认值）。生成器 build_skillopt_split_configs.py 同步产出新形状——此前它还停在
DEEPSEEK_up5zdj / 预算 4096 / max_tokens 32768 的旧形状，重跑会悄悄覆盖手工预注册，顺手修正——
并给 --check 新增字节一致性校验（配置是生成文件，手工漂移必须当场报错；故意加杂行验证过确实
拦截），两份 split 配置重生成 round-trip 逐字节一致，任务解析与 load_config 校验全部通过；
smoke_tb_distill_chain.py 的内联配置同步迁移；fork_run_stage04.py 的 manifest 差异白名单从精确
路径改为前缀匹配（.config.bank_token_budget、.config.model_options.deepseek-v4-flash-0731--ali.
max_tokens 的移除、.config.token_budgets 三处，"." 边界防止 bank_token_budget_x 之类兄弟键误
匹配）——用旧 run 真实 manifest 加模拟 v3 新 manifest 验证：恰好三条允许路径通过，改 K、加
兄弟键、token_budgets 内部叶差异均按预期处置。记账（补录）：runs/tb21_full_v2_202544 已于约
21:15 删除——该 run 唯一一次阶段 5 G7 调用（74.5k token 输入）把 32768 输出预算全部烧在思考上、
空内容返回，零已提交轮次、无保留价值；其 usage 台账 6 行（fork 脚本缺口导致的 5 次阶段 3 误
重生成 + 1 次 G7 尝试）即评审发现的 4,872 对 4,866 差额的记账缺口，特此记录在案（旧 run
usage.jsonl 实测 4,866 行，为 TB 链路调用计数的权威记录）。旧 run runs/tb21_full_20261011_2242
写入 INVALID.md 标记作废：19:46 停止于阶段 5 第 1 轮（22/29 单元，11 kept / 9 reverted /
2 skipped），被 v3 fork（预算 96000 + 输出 65536 + token_budgets 表）取代，其阶段 0–4 产物为
fork 复制源，已知缺陷（content 截断的 bank/merge/0008 被判 kept 入库）将在 fork 中修复，阶段 5
产物不得直接消费。全量 pytest：1435 通过、3 失败——失败均在 call/pipeline 侧的测试夹具（构造
ExperimentConfig 未带 token_budgets，新的 max_output_tokens_for 拒绝空预算），文件属并行代理，
与本迁移无关。更正：上一条目标题时间 21:40 有误，该条实际写于约 20:54（log.md 修改时刻；本机
时钟已对照外部时间源核对），内容不受影响。

工件：`configs/experiments/tb21_full_v2.yaml`、`configs/experiments/alfworld_skillopt_split.yaml`、
`configs/experiments/searchqa_skillopt_split.yaml`、`scripts/run/build_skillopt_split_configs.py`、
`scripts/smoke/smoke_tb_distill_chain.py`、`scripts/run/fork_run_stage04.py`、
`runs/tb21_full_20261011_2242/INVALID.md`（新）；run `runs/tb21_full_v2_202544/`（已删除，记账见上）。

## 2026-10-12 21:26 — token_budgets 统一管理（三评审收敛方案实施）

三份同题独立评审（A/B/C）对初版方案全部判定"有条件合理"，收敛修订后由四个并行代理实施：
(1) 配置 schema：token_budgets 冻结节（按角色 max_output_tokens 必填、default 65536——5120 已被
实证烧穿证伪、max_input_tokens 只测不罚）、model_options 保留键五连禁（max_tokens/temperature/
model/messages/stream——temperature 与 max_tokens 同属要消灭的双源模式）、CONFIG_SCHEMA_VERSION→2、
未知顶层键拒绝（堵死"新配置+旧代码静默回落 5120"，三评审中的降级陷阱）；(2) pipeline+call：
handle_for 按角色接线预算（5120 死值从主流水线路径消失）、每行 usage meta 记 requested_max_tokens/
estimated_in（每次尝试重估，len//4 偏差与 in_tokens 的比值即校准仪器）/cap_violation（端点无视
上限的证据——qwen 实测 99/727 次）/input_violation（只标记不中断）、纯文本 finish_reason=length
截断从"静默接受"改为有界重问（起因：merge/0008 以 32,768/length 被判 kept、截断文本已入库）；
(3) usage/cli：by_role_prompt 预注册口径聚合（ok 行中位、全行成本、unknown 不折算、cap 违规与
length 截断对照）；(4) 迁移：三份配置按行为保持表迁移（强模型 8 角色 65536、qwen 两角色 5120）、
生成器修复（此前停在旧形状，重跑会覆盖手工预注册）+ 字节一致性校验、fork 白名单前缀化、
旧 run INVALID.md、v2 run 删除补记（6 行台账缺口落账）。不做（收敛结论）：per-prompt 覆盖表、
输入执法 v1、客户端截断。pytest 1438 passed。

工件：`src/expedistill/core/config.py`、`src/expedistill/stages/pipeline.py`、`src/expedistill/llm/call.py`、
`src/expedistill/infra/usage.py`、`src/expedistill/cli.py`、`configs/experiments/*.yaml`（3 份）、
`scripts/run/{build_skillopt_split_configs,fork_run_stage04}.py`、`scripts/smoke/smoke_tb_distill_chain.py`、
`runs/tb21_full_20261011_2242/INVALID.md`。

## 2026-10-12 22:35 — TB v3 阶段 5 单轮收官（波次并行+新预算首跑）；id 后缀归一化

v3 run（runs/tb21_full_v3_212702）阶段 5 首跑暴露混合格式清单的系统性失败：清单首次同时含
长 id（traj/.../segNN/skill）与短 id（bank/merge/NNNN），模型把格式统一成短的——两次 G7 输出
全部 68 个 id 缺 /skill 后缀（v1 从未触发因当时清单格式单一）。根源修复在验证层：_normalize_ids
对未知 id 补 /skill 后若唯一匹配即采纳（模型的分组意图恢复而非重掷；歧义仍报错）。修复后首跑：
G7 通过（65536 预算生效、requested_max_tokens/estimated_in 观测字段全上线）、23 单元波次派发
（5 分钟 52 次调用全 ok）、单轮收官：6 kept / 15 回归撤销 / 2 skipped，97 条成员、62 能力分组、
81,280 估算 token ≤ 96,000。merge/0008 截断修复生效（3 来源恢复、作为候选重入本轮）。
fork 白名单补 .config.schema_version。阶段 6 以爬坡方案启动（8→16→24→32，纠正首启 32 的
偏离；在飞 trial 由提交记录+远程 meta 重连零损失）。pytest 1440 passed。

工件：`src/expedistill/blocks/curate.py`、`tests/unit/blocks/test_curate.py`、
`scripts/run/fork_run_stage04.py`、run `runs/tb21_full_v3_212702/`。

## 2026-10-13 04:50 — TB skills 模式的点号任务目录 bug（install-windows-3.11 修复）

TB v3 阶段 6 首批 skills 模式 trial 中，install-windows-3.11 的三个 token（a00/r1/r2）
全部死于 env setup："task skill directory is unavailable:
.../skills/<run_id>/install-windows-3-11"。机制：tb21 捆绑包的任务包目录名带点
（install-windows-3.11），但 E2B skill 环境按 environment_name 查找 skill 目录，其
授权导出把点规范成横线——捆绑包自己有 _TASK_SKILL_DIRECTORY_OVERRIDES
（"install-windows-3.11"→"install-windows-3-11"）。我们的插件只上传了带点拼法。
修复（benchmark 代码）：_skill_plan 的 meta 记录 dashed 别名（含点时），
_upload_launch_files 对别名 mkdir+上传同一 SKILL.md——两种拼法都在，无论环境按
哪个找。89 题里只有这一题带点。该单元卡在重试链（cursor=3、暂存无 failed_units 行），
修复生效后由下次派发自然恢复。测试先行（双拼法上传 + 无点名单文件）。pytest 1441。

工件：`src/expedistill/benchmarks/tb/plugin.py`、`tests/unit/benchmarks/test_tb_plugin.py`。

## 2026-10-13 09:40 — 阶段 6 卡滞诊断与恢复；启动器无缓冲化

阶段 6 在 04:50 重启后的驱动上停摆 3.5 小时：0% CPU、30 线程锁等待、零新提交、零存储增长，
只有 4 个旧 trial 的重连轮询在跑（进程样本 sample 证实）。三个假说逐一排除（磁盘双侧充足、
66 条已存轨迹的续跑输入校验手工重放全过、classify 对坏 result 结构安全）。真凶线索最终来自
两处：(a) console 日志因 stdout 块缓冲"说谎"——04:50 重启的 stage5 摘要一直躺在缓冲区里，
日志尾部显示的 stage 行全部来自更早的重启，误导诊断近一小时；(b) 杀掉后以 PYTHONUNBUFFERED=1
重启，前 90 秒即恢复 15 个新提交、stage5 摘要即时落盘——卡滞未复现（最可能为 04:50 恢复时
32 worker 同时 ssh 探测触发的端点侧瞬时故障，致命异常被池排空机制扣住未及打印即被杀）。
修复：launch_detached.py 默认给子进程注入 PYTHONUNBUFFERED=1（console 日志必须实时）。
当前驱动（driver_stage6_dbg）健康运转：72/267 已存、派发与存储稳定增长。教训入档：
诊断 hang 时第一步先确认日志缓冲，而不是相信日志尾部。

工件：`scripts/smoke/launch_detached.py`、run `runs/tb21_full_v3_212702/`（driver_stage6_dbg）。

## 2026-10-13 12:10 — 阶段 6 卡滞根因诊断（faulthandler 线程转储）+ 致命异常即时打印

阶段 6 的驱动卡滞（两次复现：运行 0.5–4 小时后停止派发、0% CPU、静默跳过全部队列单元）
最终用 PYTHONFAULTHANDLER=1 + SIGABRT 拿到全线程栈：主线程在 run_parallel._collect 等
future、3 个 worker 在 _poll、29 个 worker 空闲且队列已空——即除初始 32 个单元外，
其余单元全部被"error 门"静默跳过（某单元早期抛出致命异常 → control.raised() 置位 →
后续单元 gate 判 error 直接返回 None 且不留任何账目），而该异常被线程池扣住，
要等全部在飞 future 排空（最长达 4.5h 轮询期限）才浮现——静默本身是缺陷。
修复（batch_execute）：worker 捕获致命异常时立即打印单元 ID + 全部 traceback 到 stderr
（flush），卡滞从"数小时不可诊断"变成"发生即可见"。重启后 r4 正常运转中
（13 新提交、118/267）。若致命异常复发，console 日志即刻给出根因；faulthandler
武装保留（SIGABRT 随时可取全栈）。pytest 1442 passed。

工件：`src/expedistill/composites/batch_execute.py`。

## 2026-10-13 11:55 — 阶段 6 卡滞根因（五路诊断收敛）与 K1 批弹性修复（删断路器/随机退避/错峰/心跳）

一段话摘要：阶段 6 带库臂 10 轮 pass 反复"队列静默清空"，五个独立诊断子代理（并发层/K1 层/TB 插件链/数据取证/观测审计）交叉收敛出根因：每轮 pass 启动时 32 worker 同时发起约 150-200 个 ssh/scp 连接（每个 fresh 单元 6-7 个），触发 node29 sshd 并发限流（kex_exchange_identification/rc=255），单元重试仅 2 次且零退避，全部落在 5 秒风暴窗内耗尽，15 个弃单恰跨 5 个不同任务、断路器（threshold=5）在派发后约 3 秒打开，剩余 108 个单元经 circuit 门跳过（每条记账），队列秒清后 pass 挂在 4.5h 长尾轮询上，操作者 30-90 分钟 kill 重启、风暴重现成循环；"无痕迹"是观测假象——失败账本在 arm 目录 stage6_test/bank/failed_units.jsonl（1646 行中 1505 行 "circuit open"，91%）而误盯 run 根的旧账本，且爆发全部发生在启动 8 秒内。实测 ramp-8/ramp-16 零失败、24/32 必爆（16 worker ≈ 100 连接是 sshd 容忍度内的安全锚点）。用户确认四条修复：①重试间 uniform 随机等待（默认 15-45s，第一次重试移出风暴窗且随机值防重试二次对齐）②彻底删除断路器（时间尺度错配：亚秒失败判小时级成功队列死刑、单向不复位；单元级 with_retries+账本+续跑已足够）③每单元错峰（--k1-stagger，worker 领取后、error 门后、执行前随机 sleep U(0,N)，TB 传 20；默认 0）④300 秒心跳日志（磁盘快照口径，done+failed+skipped+in_flight≡N，in_flight 含 raise 后未终结单元）。三方独立评审收敛（分歧 2:1 裁决：心跳磁盘快照、raise 留 in_flight、stagger=20）后四个子代理实施（A 核心文件/B 参数链/C 测试/D 文档），另有 dispatch 行正式化（K1 dispatch[role] 含 stagger/backoff 字段、skipped 用实际交集数）与 error 门 skip_audit 落桩转正。验收：全量 pytest 1451 passed, 3 deselected in 46s（基线 1441/~45s，净增 10 项、耗时持平）、ruff 全绿、test_layering 绿、src/scripts grep 断路器零残留、端到端干跑三场景全过（退避真实 sleep、3 token 烧号 a00/a00r1/a00r2、心跳恒等式、错误停止+skip_audit、stagger 时序）。r5 于实施前按用户批准停止（135/267，在飞 trial 由 reconnect 回收），r6 以同命令 + --k1-stagger 20 续跑。删除的代价（知情记录于决策文档）：node29 硬宕 4h 场景全批探尽 60-90 分钟、exit 3 可续跑、不烧模型 token；慢失败场景（trial 跑满 4.5h deadline 才报错）失去断路器保险，最坏 144 单元×3 尝试约 60 小时沙箱算力，缓解为心跳 failed 持续增长即可见、人工介入随时可停。

工件：`src/expedistill/composites/batch_execute.py`、`src/expedistill/infra/parallel.py`、`src/expedistill/stages/pipeline.py`、`src/expedistill/stages/stage1_baseline.py`、`src/expedistill/stages/stage6_test.py`、`src/expedistill/cli.py`、`scripts/smoke/smoke_stage01_searchqa.py`、`scripts/smoke/smoke_stage01_alfworld.py`、`README.md`、`docs/decisions/k1_batch_resilience_20261013.md`、`tests/composites/test_batch_execute.py`、`tests/unit/test_cli.py`、`tests/unit/stages/test_pipeline.py`、`tests/unit/stages/test_stage1.py`、`tests/unit/stages/test_stage6.py`、run `runs/tb21_full_v3_212702`（r6 续跑）。

## 2026-10-13 15:21 — milestone 前置批次 A（部分）：批弹性入库、result.json 防护、播种输入归档脱敏

一段话摘要：TB 阶段 6 收尾期间（r8 运行中，未受任何影响），按三轮 review（9 检查 + 3 防幻觉验证 + 3 方案评审）收敛的批次 A 执行可立即落地的前置项。安全：外层 scripts/tb21/.gitignore 的 launch_q*.json 模式漏掉了含两把明文 key 的 launch_dpsk.json，改为 launch_*.json（已核实该文件从未进过 git 历史）。入库：K1 批弹性修复 16 文件（3cfdc90，即 r6-r8 实际运行的代码）与 TB 插件 result.json 解析防护（be0962d：截断/非对象文件从"停整个 pass 的代码 bug"改为可重试的 infra，附 2 个新单测，全量 1453 passed）。归档：阶段 1 播种的三份对账输入（manifest_qwen.tsv 89 题 445 行、manifest_dpsk.tsv 同构、audit_20261011.json）从 gitignored 的 tmp/tbval/ 移入 configs/seed/，其中同事机器路径（canary run 的 trial_dir/original_path，两文件按同一规则替换为 canary-archive:/ 前缀）已脱敏——验证：KEEP_MODEL 匹配 13 条逐位不变、liyishan 残留 0，且 tb21_f3_baseline.py 用新文件重算的预注册数字（F=36、弱臂 avg@3=40/89、pass@3=53/89、强臂 169/267、73/89）与 docs/tb21_f3_baseline_20261011.md 逐位一致，脱敏无损。seed_tb_stage1.py 与基线脚本的默认路径同步改为 configs/seed/（--manifest-dir 仍可覆盖）。批次 A 其余项（审计脚本暂缓——用户决定当前 repo 暂不需要；milestone 报告与 tag 等 r8 完成）。

工件：`configs/seed/`（4 文件，约 470KB）、`scripts/run/seed_tb_stage1.py`、`scripts/analysis/tb21_f3_baseline.py`、commit `3cfdc90`、`be0962d`、外层 `scripts/tb21/.gitignore`。
