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
