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

## 2026-10-13 17:35 — TB21 full 实验完成：最终 delta≈0，逐任务分析定位失效机制在注入层

一段话摘要：阶段 6 在修复版驱动（r7/r8：32→64 worker、--k1-stagger 20/40、随机退避 15-45s、300s 心跳）下满负荷推进，剩 4 个 4.5h 级长任务时用户决定接受超时缺口结束实验（3 个写入 stage6_test/accepted_infra_shortfall.json：circuit-fibsqrt/01+02、schemelike-metacircular-eval/02，均为裸跑 0/3 的硬任务，circuit-fibsqrt/01 已重试烧号到 a01r10；train-fasttext/02 的远程 result 在收尾时刚好完成、已收回为真实失败记录），远程两个在飞孤儿 runner 已安全清理（pid+RUN_ID 双验证）。最终 stage6_summary：with_bank 264 有效轨迹 avg@N=0.4457/pass@N=0.5955（53/89），control 267/267 avg@N=0.4494/pass@N=0.5955（53/89），**delta avg@N=−0.0037、pass@N=0.0000——skill 库对弱模型在 TB21 上无整体提升**。逐任务分析（应用户要求深挖）：17 任务库更好/16 裸更好/56 打平；6 个任务被库救活（裸全败库做出，如 build-cython-ext、qemu-startup）、6 个被拖死（含 db-wal-recovery 0/3 vs 裸 2/3：agent 读库两遍 65 万字符后 rm 掉 WAL 文件直接 INSERT 猜测答案提前宣布——库内无任何 sqlite 相关内容，纯注意力税；openssl-selfsigned-cert 裸 3 步成功 vs 带库 6 步假验证失败；fix-ocaml-gc 363 步挣扎中 5 遍 cat 全库反复冲刷调试上下文）。机制结论：失效在交付层而非蒸馏层——97 个 skill 以 325k 字符单文件 cat 注入，flash 级弱模型只能 head -200 浅读、检索不到相关内容、库内 "verify before completion" 协议对它零约束力；受益案例恰是库中存在可照抄具体命令序列的任务。另发现环境混杂：hf-model-inference、count-dataset-tokens 的"被害"实为沙箱外网时代漂移（归档对照臂 10-11/12 可直连 HF、带库臂 10-13 Network is unreachable，波及 8 任务），full 模式复用归档轨迹作对照存在时间混杂、delta 在网络敏感任务上低估库效果，不改变定性结论。skill 注入链路经五环证据核实完整（bank 97 成员 325,122 字符→远程 SKILL.md 325,134 字节→config skill_root→TencentSkillTerminus2→job.log 实录 agent cat 读取）。
工件：run `runs/tb21_full_v3_212702`（stage6_test/stage6_summary.json 最终版、accepted_infra_shortfall.json）、`stage6_test/bank/failed_units.jsonl`（1647 行，含 1 条 deadline 超时）。

## 2026-10-13 18:47 — 阶段 2/3 协议 v2 与检索式分发、TB 冻结划分复用的设计定稿（两轮六路评审收敛）

一段话摘要：按用户七条思路起草阶段 2/3 协议 v2（新 task_select 规则 any_weak_failed、新 segment_select 规则 strong_success_weak_failed、G2 双 prompt 重写为四节结构 JSON 契约 + 确定性渲染 + 字段校验 + write_skill 前提守卫、阶段 4/5 三项保结构连带），经两轮各三路独立代码评审定稿；同日整合两条新需求——检索式 skill 分发（纯词法两步检索：类别路由→类内 top-k，阈值下裸跑，零模型确定性，逐任务记录注入）与 TB 冻结划分复用实验（原样采用 AgentBrew seed-42 三划分 evolve41/test28/validation20→train/test/reserved，装配旧 full run 的 train 溯源产物：库 97→42 条全链口径、F=16、246 轨迹零调用复用到阶段 6 的 168 局）——再经两轮各三路独立评审收敛出 8 代理波次并行实施方案（Wave1 A1+D → Wave2 A2/B1/C → Wave3 A3/A4/B2，文件所有权互斥、15 条裁决与 12 条契约钉死）。用户决定：划分与 AgentBrew 冻结三划分一致、validation 为保留集不参与正式实验；节标记常量移址 core/models.py（第二个 block 消费者推翻二轮 placement 裁决）。关键实测预期已预注册：TB 检索是任务名词面匹配（18/48 任务零重叠、min_score 网格 {0.02,0.05,0.10}、42 库 29 组、归并阈值 0.25 路由近似惰性）；评审在协议冻结前实测过分数分布，已在决策文档披露。待办：8 代理实施（等用户启动指令后的波次派发）、集成门、TB 复用实验五道验收门。

工件：`docs/decisions/stage23_protocol_v2_20261013.md`（协议 v2 定稿，含修订三）、`docs/reviews/stage23_protocol_v2_review_20261013.md`（协议 v2 两轮评审）、`docs/decisions/retrieval_delivery_tb_split_20261013.md`（整合方案最终版）、`docs/reviews/retrieval_delivery_tb_split_review_20261013.md`（整合方案两轮评审）、分支 `stage23-protocol-v2`

## 2026-10-13 20:06 — 协议 v2 + 检索式分发 + TB 划分复用的 8 代理并行实施完成（集成门全绿）

一段话摘要：按最终版整合方案波次派发 8 个实施代理（Wave1 A1+D → Wave2 A2/B1/C → Wave3 A3/A4/B2，文件所有权互斥、契约逐字钉死），全部完成且契约偏离为零：阶段 2/3 协议 v2 落地（any_weak_failed / strong_success_weak_failed 两条新规则、G2 双 prompt 重写为四节结构 JSON 契约 + write_skill 前提守卫 + 确定性渲染 + 字段校验、REWRITE/SIMILARITY/MERGE 保结构连带 + K5 预检后检、DEFAULT_RULES 切 v2、ALFWorld v2 配置与生成器 CONFIG_PATHS 切 _v2）；检索式分发落地（D11 blocks/retrieve.py 纯词法两步检索——TF-IDF 余弦 + 触发节 ×3 加权 + 类别路由 + top-k、K1 skills_for_task 逐单元注入 + namespace 下沉、stage6 检索分支 + stage6_test/retrieval_audit.jsonl、skill_delivery 配置 schema）；TB 冻结划分复用落地（split_tasks 三元组 + reserved 保留集、stage0 恒三键、assemble_reuse 装配工具——白名单 diff + allowlist/负清单 + 全链口径单一溯源函数，CLI assemble-reuse 子命令、tb21_split_reuse.yaml 冻结三列表（AgentBrew seed-42 原文 41/28/20）、freeze_tb_split.py 漂移哨兵）。装配模块已对真实源 run 干跑对账通过（246 轨迹 / 299 subtask / 42 库行 / 6 条终态账 / merged 原件 16=11 消费+5 悬空且 5 条点名与裁决一致）。编排者集成：两个 __init__ 统一注册新导出、README CLI 清单补 assemble-reuse。集成门全绿：全量 1613 passed / 3 deselected（live）、ruff E9,F 全过、test_layering 过、三个既有 namespace 语义测试零删改保持绿。两处经编排者确认的解释性裁定：C 的 prompt digest 不进白名单 diff 改为逐名披露（严格逐位一致会使 prompt 变更后的复用数学上不可行）、B1 的 ×3 加权只作用于 [When to use]/[Trigger] 节正文（全文 ×3 在余弦下可证为恒等变换）。知情记录：旧 run 续跑将被 manifest 拒绝（prompt digest + skill_delivery + split 三键原因链）；tb21_full.yaml 与两个 pilot 配置在新代码下 load_config 报 model_options 保留键错误属预先存在的旧冻结配置 fail-loud，与本轮无关。待办：TB 复用实验（装配 → 零调用门 → 数字门 → 确定性门 → 检索预检门（min_score 按预注册网格冻结）→ 首题干跑门 → 168 局）待用户确认启动；本分支提交待用户同意。

工件：分支 `stage23-protocol-v2` 上约 40 个文件的未提交改动（`src/expedistill/` 的 blocks/author.py、blocks/retrieve.py、blocks/rules.py、blocks/tasks.py、composites/assemble_reuse.py、composites/batch_execute.py、composites/curation.py、core/config.py、core/models.py、prompts/author.py、prompts/curate.py、stages/stage0_freeze.py、stages/stage6_test.py、cli.py 等；`configs/experiments/alfworld_skillopt_split_v2.yaml`、`configs/experiments/tb21_split_reuse.yaml`；`scripts/run/freeze_tb_split.py`、`scripts/run/build_skillopt_split_configs.py`；对应 tests/；README.md、AGENTS.md；`docs/decisions/retrieval_delivery_tb_split_20261013.md`、`docs/reviews/retrieval_delivery_tb_split_review_20261013.md`）

## 2026-10-13 20:28 — 修订三：TB 复跑改为 v2 全重蒸馏（baseline 装配档位落地）

一段话摘要：用户裁决 TB 复跑不作"过滤旧库只跑阶段 6"的装配实验（其作废为后续可选消融），改为按协议 v2 完整重新蒸馏——阶段 1 的 246 条裸跑轨迹为协议无关纯测量予以复用（与 v1 结果同基线、省下最贵的 246 次执行），阶段 2–6 全部按 v2 新跑（F 口径 any_weak_failed 在 train-41 上扩到 28 题、四节结构 skill 生成、×3 检索加权首次真实生效、检索新库 168 局）。实施代理落地：assemble_reuse 增 baseline 档位（仅拷 train 双臂轨迹 + stage1 shortfall，同一性放宽为只钉 benchmark/双 executor 模型名/双 n_attempts/train ⊆ 源任务集，负清单加钉下游产物必须为空）、CLI --level 参数、新建 configs/experiments/tb21_split_v2.yaml（v2 规则名 + AgentBrew 冻结三列表原文 + retrieval min_score 0.02 占位；load_config 与 freeze_tb_split.py 校验均通过）、README 两处同步。启动序列中 min_score 澄清为"确认门"而非"调参门"：选择规则先验地选定 0.02（排除零重叠任务的最小网格值），阶段 5 出库后零执行探测仅确认分布并按预注册唯一出口（维持选定值 + 如实记录）处置，配置在 run 中途不变、manifest 全程一致。集成门：全量 1622 passed / 3 deselected（live）、ruff E9,F 全过、test_layering 过。待办：TB v2 复跑启动（装配 → --until-stage 1 门 → --until-stage 5 → 探测确认 → 168 局）待用户确认；分支提交待用户同意。

工件：`src/expedistill/composites/assemble_reuse.py`（双档位）、`src/expedistill/cli.py`（--level）、`configs/experiments/tb21_split_v2.yaml`（新建）、`tests/composites/test_assemble_reuse.py`（+9 用例）、`README.md`、`docs/decisions/retrieval_delivery_tb_split_20261013.md`（修订三）

## 2026-10-14 09:08 — G2 多行值合法化 + extract_json 围栏优先级修复（2026-10-13 run 阶段 3 事故 72/315 误终态），配原地恢复脚本

一段话摘要：run `runs/tb21_split_v2_20261013_2125` 阶段 3 有 72/315（22.9%）单元 model_output_failed 终态，触发协议 v2 预注册的 >5% 条款后三方独立诊断定性为**全部己方代码误杀、零模型违约**：67 条是 validate_skill_sections 的"无换行"子句误杀 prompt 明确要求的 heredoc 形状 steps/do_instead（"Write the exact command FORM"的正确答案），5 条是未 fence 的完整 JSON 对象内嵌 ``` 代码围栏时 extract_json 围栏优先反向失效、代码里的 `[]`/`[0]` 碎片 span 抢先解析把回复劫持为顶层数组（dumps 0178/0363/0382/0383/0500 实证）。修复只放宽不收紧：author.py 删换行拒绝、防线移到逐行行首节标记检查（8 标记 startswith，消息含字段位置/行号/标记名），校验与渲染共用新 _normalize_newlines（CRLF/孤 \r 归一）保证"校验的字节==渲染的字节"，渲染对单行输入逐字节不变（243 对已存 pair 为锚点）；call.py 新增位置版 _balanced_json_span_ranges，_json_candidates 改四段候选序——整文本 → span 外围栏（真包裹/推理后围栏保优先）→ 全文 span 按出现序 → span 内围栏降级为最后手段，extract_json/_load_json_candidate 签名语义不变。被否方案与理由（带错误重问、改 prompt 禁多行、压平换行、彻底取消围栏优先、预摘字符串内围栏、调用方二次打捞、保留 pending merge 让 K5 复用等）与 AGENTS §六.8 原地恢复豁免论证（用户明令 + manifest 全字段不动 + 560 dump 重放 0 回归证据链 + append-only/归档手术）见决策文档。验收：全量 1636 passed / 3 deselected（基线 1622，净增 14 例）、ruff E9,F 全过、test_layering 过；**离线重放门全过**——560 个可解析 ok stage-3 dump 用修复后代码全量重放：72 条事故 payload 全部通过（67 过新校验且渲染 markers 正确、5 过新提取得 dict，其中 3 条 G2 同时过校验+渲染，2 条 EXAM 的字段契约亦通过），其余 488 条 0 回归（解析结果与事故时逐位相同），243 个已存 (skill, exam) 对 content 过 skill_markers 全部正确、208 个 version-1 skill 重渲染逐字节一致；恢复脚本对真实 run 目录 dry-run 前置全过，--apply 与幂等性在 tmp 副本验证（仅 status/discard_reason 变化）。附带核验（记录性）：K4 候选枚举以 skill_id == make_skill_id(subtask_id) 过滤，bank/merge/* 不是 K4 待验收候选，复位仍按序排在续跑前。残余闸门：恢复续跑后阶段 3 若 model_output_failed > 15/315 再现即停下诊断、不带病继续。

工件：`src/expedistill/blocks/author.py`、`src/expedistill/llm/call.py`、`tests/unit/blocks/test_author.py`、`tests/unit/llm/test_json.py`、`tests/unit/llm/test_call.py`、`tests/unit/stages/test_stage3.py`、`tests/unit/stages/test_pipeline.py`、`tests/unit/inject/test_skills.py`、`scripts/run/prepare_inplace_recovery.py`（新建）、`docs/decisions/g2_multiline_and_json_extraction_fix_20261014.md`（新建）、`tmp/replay_g2_fix_20261014.py`（一次性重放门脚本）、run `runs/tb21_split_v2_20261013_2125`（待原地恢复）

## 2026-10-14 10:42 — AgentBrew / SkillOpt / 本系统方法论对比分析（论文阅读笔记）

一段话摘要：按用户的方法论框架（原始轨迹→memory→skill→库→inference 闭环，外加 Gate 模块）通读 AgentBrew（arXiv:2607.16851，含其 Terminal-Bench 25/64 部署案例：Flash 学生+brewed skills 51.6% 超越 Pro 教师 50.0%）与 SkillOpt（arXiv:2605.23904，52/52 组合最优、held-out 验证门 + 文本学习率 + rejected buffer 的单 skill 优化器），与本系统逐段对比。核心结论：三系统把 Gate 放在同一拓扑位（写库之前）但证书各异——AgentBrew 用同题环境恢复（最强最贵）、SkillOpt 用 held-out 提分（统计性泛化证明）、本系统用考试通过（最便宜、逐条归因、证书最弱）；本系统首跑 K4 拒率 78% 与 AgentBrew"无 Ralph 的 curator 拒率 61–73%"同构，说明考试门在结构上替代着 Ralph 的位置。本系统的结构性空白是库级环境效用门与拒绝信号反哺回路；A5 逐 skill 归因记录已为补齐铺好数据管道。识别出可借鉴项：mini-Ralph 库级验收、检索重排引入实证质量先验 ρ 与频率抑制 φ、K4 discard_reason 作 rejected buffer 反哺生成、G8 合并引入文本学习率。纯分析，未改任何代码。另记录一次操作失误：本条目曾误追加到外层工作区的空白 log.md（cwd 漂移），已恢复外层文件原状并改追加到本文件。

工件：`docs/notes/agentbrew_skillopt_system_comparison_20261014.md`（完整对比，含逐段表格）

## 2026-10-14 11:36 — 三系统结构对比图（PPT）：Trajectory / Memory / Skill Library / Gate 四段框架

一段话摘要：应用户要求把 AgentBrew、SkillOpt 与本系统按简化四段框架（轨迹、从轨迹提取经验的 memory、经验变成可复用 skill 的 skill library、Gate）画成可直接看出结构差异的图，用 PPT 绘制、全英文。共两页：第一页为 3 系统 x 4 段矩阵（memory 层缺失以虚线空框表示，Gate 列统一橙色以便对比证书类型），第二页为回路拓扑图（同一组节点上画出各自的 Gate 回路：Ralph 同题重跑与训练期检索回路、SkillOpt 的 rejected buffer 与文档版本回路、本系统考试改写回路及“库冻结不回写”的断开回路）。内容全部取自两篇论文方法章节与本仓库 README，未改任何代码。操作上：生成脚本放 tmp/（一次性，不进 git）；中文曾因只设拉丁字体在 LibreOffice 渲染为空白，已按用户“用英文”的决定改为全英文；用独立 LibreOffice profile 渲染 PDF 目视检查两页后定稿。同时按用户指示关闭了本会话的整点巡检 cron（TB v2 复跑仍在后台运行，阶段 4 仅剩 1 条 pending，之后需人工查看）。

工件：`docs/figures/system_comparison_20261014.pptx`（可编辑 PPT）、`docs/figures/system_comparison_20261014.pdf`（渲染版）

## 2026-10-14 11:53 — 对比图加入 SkillExpand（第四行）

一段话摘要：应用户要求把 repos/pulled/SkillExpand 并入三系统结构对比图。依据其 README 与 docs/ARCHITECTURE.md：冷启动无 skill 的 train L1 → 带证据 ID 的经验卡与 family 归属 → 每个 family 一份可版本化 skill（description 冻结、body 演化）→ L2 planner 候选 → val 面板严格更高才接受（predicted/empirical/JEV 三模式）→ 下一轮用当前 skill 重跑 train 取新卡，形成 skill 在环的回路；测试期 selector 仅凭 description 选一条 skill 载入。图上标注其 val 面板会被反复使用而带选择偏差。两页同步改为四行（矩阵与回路拓扑）；纯文档，未改代码。

工件：`docs/figures/system_comparison_20261014.pptx`、`docs/figures/system_comparison_20261014.pdf`（均已更新为四系统版）

## 2026-10-14 12:02 — TB v2 复跑：阶段 2–5 完成与检索预检门通过，infra 短板政策预注册

一段话摘要：TB v2 复跑（runs/tb21_split_v2_20261013_2125）完成阶段 2–5 全部蒸馏：112 轨迹 527 段、阶段 3 生成 314/315（恢复手术救回 71/72，仅 1 条 EXAM_GEN 空字段诚实终态）、阶段 4 验收 73 accepted / 241 discarded（恢复的 71 新 pair 过验收 20 条，28% 通过率）、阶段 5 一轮 curation 产出 66 成员库（bank:87090ad230a5，7 次合并通过回归考试、6 次 regression_failed 丢弃）。期间两次事故均已按纪律处置：机器电池睡眠挂死驱动（caffeinate+插电压制，v3 同命令续跑零损失）；病理性长尾单元（polyglot-rust-c 的 87k token 失控答案）按协议走 retry_exhausted 诚实丢弃。检索预检门（零执行、48 任务探测新库）：47 G9 细组归并 23 类（1<23<47 通过）、min_score=0.02 下 test-28 注入率 86%（预注册判据 ≥50% 通过）、零重叠任务仅 8/48（旧 42 散文库为 18/48——v2 结构化 [When to use] ×3 加权首次实战，检索质量结构性改善）、top-1 中位 0.108/max 0.472；按预注册规则确认 min_score=0.02（配置即 0.02，manifest 无改动）。infra 短板政策（阶段 6 启动前预注册）：重试耗尽单元当场同命令续跑；单单元二次耗尽或总 infra > 15/168 则停下诊断；4.5h deadline 长尾自然终结为 infra 随续跑重试、不中途干预；不用 --allow-infra-shortfall（除非用户明示）；双臂同 policy。阶段 6（168 局：检索注入臂 + 同期新鲜裸跑对照）待远端检查后启动。

工件：run `runs/tb21_split_v2_20261013_2125/`（stage3_generation/stage3_summary.json、stage4_acceptance/stage4_summary.json、stage5_curation/、skill_bank.jsonl 66 行）、`docs/decisions/g2_multiline_and_json_extraction_fix_20261014.md`、`scripts/run/prepare_inplace_recovery.py`

## 2026-10-14 12:35 — stage-4 验收重构设计定稿：exam / live_run 双模式（三案设计 → 收敛 → 三路代码级审查 → 修正）

一段话摘要：按用户"3 个独立方案设计者 → 讨论收敛 → 3 个独立分析者审查 → 修正出最终方案"的流程完成 stage 4 验收机制重构设计：新增 live_run 模式（验收单位从单 skill 文本考试改为 task 级 skill 组——组注入弱模型、真实执行 n_attempts 次、与 stage1 裸跑基线按预注册具名判据 strict_rate_gain（整数交叉乘法成功率比较、并列判负、H0 假通过率 27–34% 如实披露）比对、未更好则强模型 G10 归因（author 角色）+ G11 逐 skill 修订（rewriter，保持"不抄本题具体值"纪律防库污染）、再跑一轮仍未更好整组丢弃，通过了进库），由新配置键 acceptance{mode, n_attempts, improvement, max_injection_tokens} 触发，缺省逐字节等价现状 exam 模式（在飞 TB v2 run 续跑不受影响）。三案独立同收的核心地基：manifest 三个"续跑炸弹"（ExperimentConfig.to_dict 全字段快照 / METHOD_PROMPT_NAMES import 快照 / 角色表补全循环——任何新字段、新 prompt 名、新角色都让在飞 run 续跑被 FrozenProtocolChanged 拒）及"exam 视图逐字节不变 + golden 回归测试"拆法、K1 零改动（逐轮独立 arm store + skills_for_task + run_namespace 天然区分轮次）、五对象零 schema 变更、live 不跑 D8（没有考题答案这个保护物）、K5 零改动（回归复考是维护性检查非重新验收）。收敛 17 项分歧后三路审查共 52 条发现抓出 4 个收敛稿错误（全部已修正并溯源）：A10 覆盖式 injection_audit 应为首写冻结（否则"infra 洞+修订中崩溃"常规续跑路径撞 _check_stored 判死整个 run）、shortfall 按轮存且接受空洞回灌 skip 集、A15 的 assemble_reuse 白名单修法与代码相反（identity walk 已双向拒绝、唯一真实修改是 _prompt_digests 复制品同步）、A7 的"cursor 竞态"是收敛时新造的假代码事实（游标每单元一份，论据已换真）；另修正 None-drop 须字段白名单式（防 full 模式 split:null 被删）、"1636 零修改"口径（test_to_dict_covers_every_field 是唯一必改的形状测试，经核验 test_config.py:644 确存）、K6 形状定形为逐组阶段函数库、G10 的 task_instruction 改用 task_prompt 渲染产物（TB 的 Task.instruction 就是任务名）。实施拆分 T1–T5（文件所有权互斥）+ 两道合并门（老配置 manifest 逐字节干跑、1–2 task 真实 TB pilot 含 kill-续跑等价验证）；11 项开放问题待用户/评审裁决（判据选名、shortfall 政策预注册、ALFWorld 串行瓶颈等）。纯设计，未改任何代码；实施待用户指令。

工件：`docs/decisions/acceptance_live_run_20261014.md`（最终实施方案定稿）、`tmp/acceptance_redesign/designer_{1,2,3}.md`（三份独立设计稿）、`tmp/acceptance_redesign/converged_plan.md`（收敛稿）、`tmp/acceptance_redesign/reviewer_{1,2,3}.md`（三份审查报告，52 条发现全带行号证据）

## 2026-10-14 14:10 — live_run 验收方案闭卷裁决与三方定稿评审收敛（修订三 = 最终实现方案）

一段话摘要：用户经管理者代传裁决 11 项开放问题："归因者可以删 skill，其他均按 agent 推荐设置"——G10 的 keep|revise|delete 词表定格（delete 即终态 live_revision_deleted）、判据名 strict_rate_gain、shortfall 接受标准按预注册建议（正式 run 默认不开 --allow-infra-shortfall，仅用户明示时按"续跑补跑后仍耗尽才可接受 + 总量>15% 停诊断"）、n_attempts TB=3、其余 8 项按推荐。裁决落进方案后启动三方定稿评审（对"纳入裁决后的最终实现方案"做落地核验+可实施性终审+剩余缺陷分级），合计 0 blocker / 合并去重 5 major / 约 20 minor，三方一致"需小修后可实施"：上轮 52 条修正逐条抽查无一走样；抓到的主要缺陷全部文本级修正进修订三——裁决 11 的 aux 槽位缺失（verdicts.jsonl 行补 aux 字段，三路独立同报）、重派发集缺"非终态"限定（零执行终态组的空 store 会被当"不完整"在每次续跑时重派进 K1 白烧执行——评审 3 独有发现，恰好藏在上轮 blocker 的修正 A10 里，已加限定词并配回归用例）、expected 口径自相矛盾（改为两侧都传全量应有单元集、skip 单元由 D3 自然记 infra，禁止传"已减 skip"的 expected）、A15 理由句仍是假代码事实（装配从不写 manifest、digest 副本只被 bank 档消费且不参与身份拒绝，同步的真实作用面是 bank 档 live→live 的 drift 披露）、验收单位定义在正文丢失（补回 §2.2 组定义正本）；minor 含 first_success_gain 精确定义补进方案、G10 预算行去掉错置的 marker、零执行终态落 verdict 行、重放守卫去 content 子句（版本号即完成证明）、K6 公开面补 expected_units/audit_row 两助手、T1-T5 加依赖列与裁决托付交付物挂账（runbook 模板含 shortfall 预注册正本、实施收尾清单含 schema_version v3 清理）、测试计划补 A9 继承空洞/入口守卫/重算一致性/懒 import 四类用例、pipeline 行号引用修正（367-376）。最终实现方案 = docs/decisions/acceptance_live_run_20261014.md 修订三；实施 T1-T5 待用户另行指令（本次只到最终实现方案）。

工件：`docs/decisions/acceptance_live_run_20261014.md`（修订三，最终实现方案）、`tmp/acceptance_redesign/final_review_{1,2,3}.md`（三方定稿评审报告）

## 2026-10-14 19:09 — live_run 验收模式按定稿方案实施完成（T1–T5 五波），文档回写与交付物收尾

一段话摘要：stage-4 exam / live_run 双模式验收按最终实施方案修订三（docs/decisions/acceptance_live_run_20261014.md）分五个所有权互斥的波次实施完成：T1 配置底座（顶层 acceptance 键 + to_dict 字段白名单式删键——None 时缺键、在飞 exam run 的 manifest 逐字节不变；n_attempts≥2 与 improvement 必填校验、ACCEPTANCE_IMPROVE 注册表与 strict_rate_gain/first_success_gain 两规则、core/ids 的 raw_task_id——TB 带冒号 id 反解析必做），T2 G10/G11 归因修订积木（LIVE_RUN_ATTRIBUTION/LIVE_SKILL_REVISION 两 prompt 懒注册——不进 prompts/__init__ 的 import 快照，digest 仅在 live_run run 的 manifest 冻结，blocks/attribution 模块顶部防御性注册 import），T3 K6（composites/live_acceptance.py 逐组阶段函数库：pending_groups/load_baseline/skip_ids_for/expected_units/audit_row/judge_round/attribute_group/apply_revisions/no_op_guard/survivors_and_block/finalize_group/round_dir；三账本 verdicts/attributions/{attribution,revision}_failures 与 injection_audit 首写冻结全在 stage4_acceptance/live_run/ 下，重算一致性守卫 + append-once + 版本号重放守卫），T4 阶段接线（stages/stage4_live_run.py 轮栅栏驱动——每轮一次 K1 大批、重派发集 = 非终态 ∧ 轮 store 不完整、按轮 shortfall 记录、summary 双模式同名文件含 mode/improvement_rule/group_details/infra_missing/infra_accepted（按轮）；stage4_acceptance 顶部 4 行懒 import 分发；stage0 条件 digest 分支；assemble_reuse 白名单零改动、仅 _prompt_digests 复制品按 live 目标同步——bank 档 identity walk 天然双向拒绝 acceptance 差异；cli --allow-infra-shortfall help 加 stage4-live）。本轮 T5 完成文档回写与交付物：README 按 §2.8 清单逐项回写（G 表 +G10/G11、D8 行注 exam 专用、D3/D5/D9/D10 注 live 亦用；K 表 +K6 注明与 stage 6 无关、K4 注 exam 专用；1.5 阶段→积木表 stage 4 按模式分写；1.6 prompt 表 +两条及懒注册说明；§2 discard_reason 四个新词与跨模式词证据基础注记、live_run/ 落盘子树与 work/stage4_live/round00|round01、零 schema 升版声明；阶段 0 acceptance 键语义与白名单删键；阶段 4 双模式成文——组定义/判据预注册含 27–34% 噪声数字按 n 重算/两轮循环/no-op 守卫/D8 不适用三层防线/失败处置表/shortfall 按轮与默认不开政策/expected 全量口径；运行规则表阶段 4 行按模式分写且"阶段 2–4 空洞不能接受"改为按模式分述；已知风险 +4 条含 full 模式语义声明；双模式 summary 形状差异表）；AGENTS §四目录树 +4 新文件；新建示例配置 configs/experiments/tb21_split_live_run.yaml（tb21_split_v2.yaml 方法键逐字复制 + 顶层 acceptance{mode: live_run, n_attempts: 3, improvement: strict_rate_gain}，候选配置不启动）、操作手册 docs/runbooks/live_run_acceptance_runbook_20261014.md（shortfall 预注册正本引用、pilot 检查表、进度查看、故障处置、ALFWorld 三项义务、遗留事项——在飞 run 完结后升 CONFIG_SCHEMA_VERSION v3 并退役白名单删键）、只读审计脚本 scripts/audit/audit_live_run.py（五项对账：组终态↔账本、轮 store=应有减 skip、账本自洽、shortfall↔failed_units、冻结行↔注入集 sha；--self-test 对手工构造的 fake run 目录跑通含三次篡改全被抓住）；定稿方案文档入库。测试：全量 1885 passed / 3 deselected（基线 1636 + 新增 249），ruff E9,F 全绿，示例配置 load_config 验证通过。两道合并门（老配置 --until-stage 0 manifest 逐字节干跑、1–2 task 真实 TB pilot 含 kill-续跑等价）进行中，结果留待编排者补记。commit 列表：T1 c793215、T2 eb7ff90 + 02e4df9、T3 afa0975 + 1abbe12 + 4c741b7、T4 0b7d47e、T5 本条目所在提交。

工件：`README.md`（设计正本回写）、`AGENTS.md`（§四目录树）、`docs/decisions/acceptance_live_run_20261014.md`（定稿方案入库）、`configs/experiments/tb21_split_live_run.yaml`（新建）、`docs/runbooks/live_run_acceptance_runbook_20261014.md`（新建）、`scripts/audit/audit_live_run.py`（新建）、`src/expedistill/{core/config,core/ids,blocks/rules,blocks/attribution,prompts/attribution,composites/live_acceptance,composites/assemble_reuse,stages/stage0_freeze,stages/stage4_acceptance,stages/stage4_live_run,cli}.py`（T1–T4 产物，见 commit 列表）、commit `c793215`、`eb7ff90`、`02e4df9`、`afa0975`、`1abbe12`、`4c741b7`、`0b7d47e`

## 2026-10-14 23:25 — 合并门 2 通过：live_run 验收 pilot 全链路跑通 + kill-续跑等价验证 + 审计零发现

一段话摘要：合并门 2（1–2 task 真实 TB pilot）在 runs/tb21_live_pilot_run3 跑通：配置 tmp/gate1/tb21_live_run_pilot.yaml（train=mteb-retrieve+sanitize-git-repo、test=query-optimize、reserved 空，方法键与 tb21_split_live_run.yaml 同源），驱动 `expedistill run --until-stage 4 --workers 8 --k1-stagger 20 --llm-timeout 1500`（start_new_session + pidfile）。stage1 十二局（strong 4/6、weak 1/6，两任务均为 any_weak_failed 入 F 集；mteb-retrieve weak a02 局陷已知 tmux 空键病态、由 4.5h deadline 自然兜底落轨迹），stage2 八条轨迹切 39 段，stage3 生成 22 条候选 skill，stage4 live_run 轮 0：两组注入后弱模型真实跑 3 次，判据 strict_rate_gain 整数交叉乘法——tb:mteb-retrieve 裸臂 0/3 → 注入臂 1/3（injection_tokens 6727，13 成员组）、tb:sanitize-git-repo 1/3 → 2/3（4737 tokens，9 成员组），两组均判 better=true，无弃置、无轮 1，summary accepted=22 与组员数、stage3 生成数、审计行 members 三角对账一致；verdict 行 schema live-verdict-v1 与定稿逐字段一致，baseline 计数与 stage1 weak metrics 逐字段一致（对照确为裸弱臂）。kill-续跑等价验证（合并门 2 预注册项）：轮 0 K1 大批在飞时 kill -9 驱动（6 单元均已 submit、record 齐全）→ 同命令续跑，七项等价证据全过——stale run.lock 自动接管（warning 印证）、manifest 逐字节不变（无 FrozenProtocolChanged）、stage0–3 零重跑（strong/weak 大批 0 pending、12 条轨迹原样）、injection_audit 首写冻结（重推导与冻结行逐字节比对通过、零追加、sha 不变）、六个远程 job 全部 rejoin（远程无新 run_id/attempt token，TB 插件 resume probe 的 alive 路径实测成立）、K1 大批从断点续推进（5 分钟内 done=3）、重派发集判定正确（6 pending = store 不完整单元）。scripts/audit/audit_live_run.py --self-test 三次篡改全被抓、正式审计 0 error/0 warn/0 info（报告 tmp/gate1/pilot_audit_report.json）。如实披露 pilot 边界：G10 归因、G11 修订、轮 1、no-op 守卫、shortfall、discard 与 delete 全部未触发（两组轮 0 直接通过），这些路径仅有测试套件覆盖（249 例），首次真实触发将在正式评测的 28 个 F 任务组中发生；三路独立评审（判据账本/流程状态机/边界与规模）已并行启动，通过后修复一轮即启动 TB 2.1 正式评测（train 41 / test 28，64 并发）。

工件：run `runs/tb21_live_pilot_run3/`（console.log、pipeline_log.jsonl、stage4_acceptance/live_run/{verdicts.jsonl,round00/injection_audit.jsonl}、work/stage4_live/round00/ 提交记录）、`tmp/gate1/tb21_live_run_pilot.yaml`（pilot 配置）、`tmp/gate1/pilot_driver.py`（驱动启动器）、`tmp/gate1/pilot_audit_report.json`（审计机读报告）

## 2026-10-15 00:40 — 三方独立评审通过 + 最后修复一轮（视图预算 / 审计豁免 / 文档对齐），全量回归 1888 绿

一段话摘要：按用户指令对 pilot 结果启动三个独立评审（视角分工：判据与账本重算 / 流程状态机与未触发路径走查 / 可扩展性与模块边界），三份报告一致 READY_WITH_FIXES：判据重算（含注入块 sha 独立重建）与账本零矛盾；状态机已触发路径 9/9 对齐、边界合规 12 项全过（七层/互不 import/exam 零侵入/懒注册/切换校验）。评审 2 抓到唯一高严重度真问题：**G10/G11 归因输入无总量预算**——live_views 全量拼接，pilot 实测一组三视图 562,037 字符 ≈14 万估算 token（估算对终端文本低估 ~41%），上下文超限返回 400/413 永久拒绝，既不归修复预算也不归 infra，首个带长轨迹的失败组会把整个阶段硬停且续跑原地再撞。修复（九项）：(F1·代码) K6 拼 live_views/failure_view 时按预注册硬预算 head60%+tail40% 截断 + 省略标记（_LIVE_VIEWS_BUDGET_CHARS=200k / _FAILURE_VIEW_BUDGET_CHARS=80k，runbook §2.5 预注册正本；composites 层，不碰 D5 与 prompt 模板，manifest 冻结面安全）+ 3 个新测试；(F2·代码) 审计脚本对零执行终态（weak_already_knows/live_injection_overflow/live_undetermined）豁免 round-store 完整性检查（修复前合法 overflow 组被误报 store drift），self-test 加 overflow 组场景（clean 审计 0 findings + 三次篡改仍全被抓）；(F3–F8·文档) 定稿 §2.4 verdict 行补零执行终态 verdict 字段、§2.2 summary 补 infra_accepted、README 轮 store 目录名语义加注（stage1_baseline 仅布局复用、内容是 live 轨迹）与 judged_with_shortfall 空洞两来源披露、两处 docstring 措辞对齐实际保证（保住的是 METHOD_PROMPT_NAMES 快照而非模块图——composites/__init__ 的模块导出会让 import exam 路径加载 live 模块，评审 3 实测戳破原声称）、runbook 并发数字更新到修复后口径（64 workers + stagger 有 168 局先例，旧"32 必爆"是退避修复前实测）。全量回归 1888 passed / 3 deselected + ruff 全绿。遗留：三评审一致的流程裁定——正式评测前须跑 miss pilot（chess-best-move + write-compressor，TB v2 实测 weak 0/3 且 strong 1/3 的 train 任务）真实触发一次 G10/G11/轮 1/no-op 接缝，作为 F1 修复的验收与 runbook 检查表 7/8 项的补课；正式评测启动序列还含 stage-5 后的检索预检门（min_score 0.02 占位需在新 bank 上重探）。修复未提交（提交待用户授权），工作区与测试全绿一致。

工件：`src/expedistill/composites/live_acceptance.py`（F1：预算常量 + _clip_view + 两处拼接点）、`scripts/audit/audit_live_run.py`（F2：零执行终态豁免 + overflow self-test 场景）、`tests/composites/test_live_acceptance.py`（+3 用例）、`docs/decisions/acceptance_live_run_20261014.md`（F3/F4）、`README.md`（F5/F6 + F1 披露）、`docs/runbooks/live_run_acceptance_runbook_20261014.md`（F8 + §2.5 预算预注册 + pilot run3 实测记录）、`src/expedistill/stages/stage4_acceptance.py` 与 `src/expedistill/blocks/attribution.py`（F7 docstring）

## 2026-10-15 00:50 — miss 链路三路静态评审通过并修复，按用户裁决跳过 miss pilot，TB 2.1 正式实验启动

一段话摘要：用户指令"用三路独立评审对 miss pilot 执行路径上的代码做静态检查找真 bug（繁琐不影响推进的不算），若基本修复了问题则直接跑正式实验、跳过 miss pilot"。三路静态评审（A 归因-修订数据流 / B 轮 1 状态机与接缝 / C 端到端真实运行）对 miss 失败链路（轮 0 判负 → G10 归因 → G11 修订 → 轮 1 重跑 → 再判定）逐行走查：A 确认输出契约三处一致、plan 匹配全走 ModelOutputError、重放守卫完备（PASS_AFTER_FIX，1 审计层 bug）；B 走查 22 个崩溃窗口全部正确收敛 + 两个实测探针（混合终态、跨轮混合 resume；PASS，1 low bug）；C 端到端 14 环节走查 + run3 真实数据仿真全链重演（PASS_AFTER_FIX 弱，1 条件性硬停风险）。三个真 bug 全部修复：①G10 请求包络风险——author 的 max_tokens=65536 与 200k 视图预算合计 ≈135k token 超出端点一切已证实接受范围（≈119k），若端点按和校验窗口首个大视图 miss 组必 400 硬停，修复为预算降到 140k 字符（总包络 ≈120k 两种窗口解释下都安全，评审 C 用 run3 真实请求校准低估系数 1.28–1.50 中位 1.38），记入 runbook §2.5 并保留应急通道（该常量非 manifest 冻结面，400 实爆时一行下调 + 同命令续跑）；②审计 exhausted_g10 按首行取值恒 False（耗尽标记在末行）→ G10 耗尽组专项审计分支死代码（合法耗尽组被误报 WARN、篡改成员可带病过审——评审 A 实证），修复为 any 语义 + self-test 补 G10 耗尽组与第四篡改场景（死代码分支复活验证被抓）；③summary 的 group_details.outcome 把"一成员 delete/G11 耗尽弃置 + 其余成员轮 1 通过"的混合终态组误报 pending（B/C 同报），修复新增 mixed 分支 + 形状测试。全量回归 1889 passed + ruff 全绿 + self-test 四篡改全被抓 + pilot run3 复审 0 findings。按用户裁决：miss pilot（runs/tb21_live_miss_pilot，停在 stage1 strong 4/6）主动中止（driver 已杀、6 个远程孤儿 job 已在 node29 清理、run 目录写 ABORTED.md），**TB 2.1 正式实验启动**：runs/tb21_split_live_run_20261015_0045，配置 configs/experiments/tb21_split_live_run.yaml（train 41 / test 28 / reserved 20），第一段 --until-stage 5（246 局 stage1 → 蒸馏 → live 验收 → bank），64 workers + stagger 20 + llm-timeout 1500，driver pid 87308，stage1 strong 大批 123 单元已派发；stage6（168 局）待第一段完成 + 检索预检门（新 bank 重探 min_score）后启动。每小时巡检（:23）与 driver 退出监视在位，问题处置流程（确认可续跑 → 三评审 → 不破坏边界修复 → 续跑）写入巡检指令。风险如实披露：G10/G11 首次真实模型调用将在正式实验中发生，静态评审已压包络风险与契约风险，真实失效率与端点接受度由巡检盯防。

工件：`src/expedistill/composites/live_acceptance.py`（预算 140k 改锚）、`scripts/audit/audit_live_run.py`（any 语义 + self-test 耗尽组/篡改 4）、`src/expedistill/stages/stage4_live_run.py`（outcome mixed 分支）、`tests/unit/stages/test_stage4_live_run.py`（+1 形状测试）、`docs/runbooks/live_run_acceptance_runbook_20261014.md`（§2.5 改锚）、`scripts/run/launch_tb21_live_run.py`（新建启动器）、`runs/tb21_live_miss_pilot/ABORTED.md`（中止说明）、run `runs/tb21_split_live_run_20261015_0045/`（正式实验第一段，进行中）、评审回执 `/Users/yimingniu/Documents/Manager/state/replies/gate_miss_review.md`

## 2026-10-15 12:20 — 多方法框架方案定稿，建立 MM 独立副本

一段话摘要：把仓库升级为"方法按名插拔"框架的重构方案定稿。初版方案由我起草后，启动三个独立架构师评审（A 代码事实与可行性、B 扩展性与反过度设计、C 迁移路线与可复现风险），经三轮审计讨论（独立审计 → 交叉讨论七个分歧点 → 裁决提案 R1–R7 收敛表决，三票全通过）定稿。要点：在 stages 与 composites 之间加 methods 方法驱动层（只放驱动，K4/K5/K6 本体不搬）；方法 = 函数 + 注册元数据，参数用 dataclass，校验不进 core；删掉懒注册、_MANIFEST_OPTIONAL_FIELDS、按方法声明 prompt/角色、needs/provides、extends，改为全量冻结 prompt + 两条静态测试；工作目录永久不迁移；P-1（tag + golden 产物矩阵）→ P0 纯重构 → P1a 按名分发（manifest 不变）→ P1b 唯一一次 schema v3 切换（含 upgrade_v2）。用户裁决两条执行纪律：重构在本副本（ExpeDistillation-MM，origin = TB-dev 本地路径，基点 bcb3ffe，分支 feature/multi-method，独立 venv、editable 只指向本副本 src，Python 3.11.16，全量测试 1889 passed）进行，TB-dev 在正式实验期间不动；每个重构步骤完成后必须再过一轮三路独立评审、判断是否偏移方案约束（协议见决策文档第 10 节），通过才进下一步。P-1 及之后的步骤等 TB 正式 run 完结后进行。外层工作区的方案过程稿已删除（内容迁入本仓库决策文档）。

工件：`docs/decisions/multi_method_framework_20261015.md`（最终方案与每步评审协议）、`docs/reviews/multi_method_review_A_20261015.md`（A 三轮审计全文）、`docs/reviews/multi_method_review_B_20261015.md`（B 三轮）、`docs/reviews/multi_method_review_C_20261015.md`（C 三轮）、本仓库 clone（origin `../ExpeDistillation-TB-dev`，分支 `feature/multi-method`）与 `.venv`
