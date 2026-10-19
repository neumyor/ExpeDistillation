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

## 2026-10-15 13:10 — P-1′：golden 产物矩阵落地，不等正式 run

一段话摘要：用户裁决"升级为多方法框架不用等正式 run"，P-1 拆两半执行：golden 矩阵部分提前完成，TB-dev 终态 tag 与维护分支归档仍等 run 完结（决策文档 §7.1）。新增 tests/golden 采集器：8 个组合（acceptance exam/live_run × delivery whole_bank/retrieval × mode full/split）假模型全流水线，每组合 3 个崩溃续跑变体（模型调用数 0.3/0.6/0.95 分位点）；快照为"结果状态逐字节 + 请求史排序多重集"，崩溃续跑的等价准则精确为"调用史只容忍崩溃痕迹与同内容重问"（README 已知不等价点"未落盘的调用会被重问"的可执行形式）。基点 dad6d28（src 与 bcb3ffe 逐字节相同），三次生成逐字节一致（确定性验证）。归一化刻意最小：时间/时长/请求 uuid/processed_this_call/run 目录路径/pipeline_log 末行视图。常驻验收测试 10 个（8 组合直线比对 + 2 组合崩溃续跑），全套件 1899 passed、ruff 干净。矩阵 876K 进 git。P0 即可开始；其三路评审将同时审计本采集器（验收标准的执行工具）。采集器开发中发现两处需要精确化的语义并已写进准则：call_model 对失败尝试也落一行用量（进程内模拟崩溃会留痕迹）；成对写入的单元（skill, exam）崩溃后续跑重做整个单元。

工件：`tests/golden/harness.py`（采集器）、`tests/golden/generate.py`（生成入口）、`tests/golden/test_golden_matrix.py`（常驻验收）、`tests/fixtures/golden/`（8 组合快照 + provenance.json）、`docs/decisions/multi_method_framework_20261015.md`（§0.5/§7.1 更新）、commit

## 2026-10-15 15:05 — 按用户"简洁化"裁决修订验收标准，P0.1/P0.2 完成

一段话摘要：用户裁决"简洁、可扩展、避免 over-engineered"，撤销两条自设约束：golden"永不重生成/只在重构前代码上生成"改为普通快照纪律（有意改行为的步骤同提交更新、diff 进提交说明）；TB-dev 热修编排（cherry-pick 到基点 + provenance 换基点）整体删除，MM 想反映上游就 merge 后重生成。验收标准简化为：每提交测试全绿（golden 是套件成员，0.6 秒，无额外仪式），有意改行为的步骤（主要 P1b）列 golden diff 摘要 + manifest 人工审定。P0 前两个子步已完成并各自提交：P0.1 RunSettings 类型化进 frozen StageContext、删 7 个 _workers 死分支副本（commit f1b236a）；P0.2 resolve_plugin 统一插件解析（消 5 处重复）+ write_skill 接收可选 prompt 名（prompt 策略变体的零风险准备，commit 08c7985）。每步 1900 测试全绿、golden 无 diff。

工件：`docs/decisions/multi_method_framework_20261015.md`（§0.5/§7 表/§7.1 修订）、`tests/golden/harness.py`、`tests/golden/generate.py`、`tests/golden/test_golden_matrix.py`（快照纪律改写）、commit f1b236a、commit 08c7985、回执 `/Users/yimingniu/Documents/Manager/state/replies/mm_simplify.md`

## 2026-10-15 16:40 — P0.3/P0.4：共享件下沉与装配工具上移

一段话摘要：P0 剩余两个子步完成。P0.3（commit 94eded0）：新建 composites/shortfall.py（接受空洞记录的读写，stage1/live 轮/装配工具三处副本合一）、composites/skill_status.py（discard_skill、stage-3 候选扫描、verdict 计数——两个验收驱动的逐字节重复，当初隔离只为保 exam 路径的冻结 prompt 名面，懒注册删除后理由消失）、benchmarks/aux.py（辅助指标接线，三个 stage 不再互相伸手）、blocks/tasks.py 收编 tasks_digest（stage0 与装配工具各一份副本）；core/ids 增加 is_stage3_candidate 谓词。P0.4：stage0 抽出纯函数 build_manifest（manifest 载荷只组装一次），assemble_reuse 整文件上移到 stages/（composites 不得 import 阶段层）、删除其 _prompt_digests 副本改用 build_manifest，AGENTS 目录树同步。每步 1900 测试全绿、golden 快照无 diff（行为不变的直接证据）。

工件：`src/expedistill/composites/shortfall.py`、`src/expedistill/composites/skill_status.py`、`src/expedistill/benchmarks/aux.py`、`src/expedistill/stages/assemble_reuse.py`（自 composites 上移）、`src/expedistill/stages/stage0_freeze.py`（build_manifest）、`tests/unit/benchmarks/test_aux.py`、`tests/unit/stages/test_assemble_reuse.py`（随迁）、AGENTS.md、commit 94eded0、本条对应 commit

## 2026-10-15 16:55 — P0 完成：run_units_disposed 取消，启动 P0 三评审

一段话摘要：P0 最后一个计划项 run_units_disposed 按 2026-10-15 用户"简洁化"裁决重新评估后取消：三处（stage2/3/4-exam）各约 3 行 try/except、返回形状各异，抽象参数化成本高于重复收益，且 ModelOutputError 处置顺序本是各阶段续跑契约；取舍已写进决策文档 §2 与 §7 P0 行，并列入 P0 评审关注点（评审可推翻）。P0 至此完成：四个提交（f1b236a RunSettings、08c7985 resolve_plugin+write_skill prompt 名、94eded0 共享件下沉、edbe66e build_manifest+装配上移），每提交 1900 测试全绿、golden 快照零 diff。按协议 §10 启动三路独立评审（A 代码事实/行为等价、B 扩展性/反过度设计、C 迁移风险/可复现性）审 P0 全部 diff，意见留档 docs/reviews/multi_method_step_P0_review_20261015_{A,B,C}.md，通过后进 P1a。

工件：`docs/decisions/multi_method_framework_20261015.md`（§2/§7 修订）、commit f1b236a、08c7985、94eded0、edbe66e、评审文件（见后）

## 2026-10-15 17:50 — P1a：方法驱动层落地，按名分发（manifest 不变）

一段话摘要：P1a 完成。新增 methods/ 第 6 层（stages 升 7、cli 升 8）：注册表是纯数据表（(slot, name) → 驱动模块路径，解析时才 import 驱动——exam 进程依旧永不加载 live 模块，冻结 prompt 名面不变），acceptance 槽迁入 exam.py 与 live_run.py 两个驱动（stages/stage4_acceptance.py 变为"按名取驱动并调用"的薄前端，各留常量于 methods/acceptance 包），delivery 槽新建 whole_bank.py（整库标量注入，保住 skills_injected 的库指纹记账）与 retrieval.py（逐任务 top-k + 原子审计写）。live 驱动上移前先把 stage-1 空洞路径下沉 composites/shortfall.py（methods 不得 import stages）。test_layering 扩展：层表更新、相对 import 纳入检查、新增"方法驱动互不 import"。测试随驱动迁至 tests/unit/methods/acceptance/（无 shim，patch 目标同步迁移）。1901 测试全绿；golden 十测试全过 = 产物逐字节不变、manifest 不变（P1a 验收达成）。执行中发现并修复两处：注册表先返回函数后改返回驱动模块（retrieval 需要 resolve+write_audit 两个入口）；_selected_references 必须照原实现接受 Mapping 与 Skill 两种形态（我从大纲重写时丢了 Mapping 分支，测试当场抓住）。

工件：`src/expedistill/methods/`（registry.py、acceptance/{__init__,exam,live_run}.py、delivery/{__init__,whole_bank,retrieval}.py）、`src/expedistill/stages/stage4_acceptance.py`（薄前端）、`src/expedistill/stages/stage6_test.py`（delivery 分发）、`src/expedistill/composites/shortfall.py`（stage-1 路径下沉）、`tests/test_layering.py`、`tests/unit/methods/acceptance/{test_exam,test_live_run}.py`（随迁）、AGENTS.md、README.md、本条对应 commit

## 2026-10-15 18:30 — 更正与评审跟进：P0+P1a 合并三评审全票通过

一段话摘要：本条更正 16:55 一条——该条所记"启动 P0 三评审"属实，但随后用户批复"均同意"（评审范围限定为 diff+验收证据、P0 与 P1a 合并为一轮），P0 单独评审即停（未产出文件），P1a 完成后启动了覆盖 P0+P1a 的合并三评审，意见留档 docs/reviews/multi_method_step_P0_P1a_review_20261015_{A,B,C}.md。评审结论：三票全过、无【偏移约束】级代码问题；C 的一条【偏移约束】即本流程记录缺口（已由本条修复）；三个裁决点一致维持（run_units_disposed 取消正确、注册表纯数据表是懒加载约束下唯一正确实现、P0.3 合并的等价依据是 golden/manifest 而非提交说明所述理由链）。按评审意见完成的修正：三处新 docstring 对"exam 进程永不加载 live 模块"的夸大表述改为精确不变量（冻结名集不漂移 + 分发链不加载 live 驱动；composites/__init__ 拉入 live 积木是基点既有行为）；STAGE_LABEL 移回 exam 驱动私有（live 用自己的 stage4_live 标签，共享陈述不实）；live_acceptance.py 与 README 的旧路径更正；装配工具残留死代码 _canonical 删除；stage6 死缺省改直取；tests/unit/methods/ 补 __init__.py。P1b 前置风险清单（golden 需忽略 manifest 的比对模式、采集器 v3 改写、audit_live_run 的 n_attempts 路径、装配白名单键、stage0 夹具）已记录在评审 C 报告，进 P1b 时逐项落实。

工件：`docs/reviews/multi_method_step_P0_P1a_review_20261015_{A,B,C}.md`（评审全文）、`docs/decisions/multi_method_framework_20261015.md`（§2/§3/§10 修订）、`src/expedistill/methods/`（docstring 修正、STAGE_LABEL 归位）、`src/expedistill/stages/assemble_reuse.py`（删死代码）、README.md、本条对应 commit

## 2026-10-15 19:30 — P1b：schema v3 一次切换（唯一一次）

一段话摘要：P1b 完成，C 的前置风险清单 10 条逐条落实（前置 1 忽略 manifest 的比对模式先行提交 00971f2 并预注册口径；2/4/6/7/8 在本切换内：采集器同提交改写 v3、live digest 与 v2 同名值逐字节相等已断言、装配白名单改 config.methods.delivery、stage0/live 夹具按预告修正、golden 同提交重生成且 24 个崩溃续跑变体在 v3 下全部等价；3：upgrade_v2 纯函数 + 三种 exam 写法同值映射 + 仓库全部 v2 配置升级测试；5：audit_live_run 改按 manifest 判模式、live 子树缺失报 ERROR、n_attempts 走新路径、自检通过；9：convert_v2_configs.py 一次性转换 7 份新文件、3 份 v1 时代配置（model_options.max_tokens 在 v2 规则下本就非法）逐文件拒因留档；10：评审后置）。核心改动：ExperimentConfig 换 methods 五槽位（K/retry 族/预算/acceptance/skill_delivery 全部入槽），ROLE_DEFAULTS 字典取代 MODEL_ROLES 元组（消与 core/models 重名），参数契约归 methods 层（每槽位 Params dataclass + check 跨约束，stage0 解析全部槽位后再冻结；空/缺槽位补全为默认、给定槽位逐字冻结——不做按键合并，防默认参数串槽），prompt 全量冻结 + 两条静态测试，懒注册与 _MANIFEST_OPTIONAL_FIELDS 删除，v2 配置/负载明确拒载并指向转换器，v2 run 目录在 stage0 给维护分支提示。验收：预注册的"除 manifest 外逐字节一致"在 8/8 组合上通过（切换前后产物零漂移的直接证据）；1912 测试全绿；golden 重生成 diff = manifest.json 唯一变化（config 换 methods 形状 + prompt_digests 增两个 live digest，其余 13 文件逐字节不变）。行为语义披露：v2 装配测试中"显式 exam vs 缺省 exam 关键集差异拒绝"的保守角案例随补全语义消失（两侧同为完成值，装配通过），已在测试改为断言新行为并写明缘由。

工件：`src/expedistill/core/config.py`（schema v3）、`src/expedistill/methods/`（params.py、registry.py 扩展、segmentation/authoring/curation 新槽位包、acceptance/delivery Params）、`src/expedistill/prompts/__init__.py`、`tests/unit/prompts/test_freeze.py`、`tests/unit/methods/test_params.py`、`tests/unit/core/test_config.py`（v3 重写）、`tests/fixtures/golden/`（重生成）、`scripts/run/convert_v2_configs.py`、`scripts/audit/audit_live_run.py`、configs/experiments/*_v3.yaml（7 份）、AGENTS.md、README.md、commit

## 2026-10-15 20:35 — P1b 三评审裁决与修复轮：两条偏移约束关闭，P1b 验收

一段话摘要：P1b 三路评审全部交卷（docs/reviews/multi_method_step_P1b_review_20261015_{A,B,C}.md）。B 通过（9 赞同 5 琐细，§9 禁止清单逐项无违反，新增方法步骤比方案 §8 还少一步）；C 有条件通过（底单 10 条：8 落实 2 部分，8/8 fixture 除 manifest 外逐键一致被独立复核成立，1 条偏移约束 = "全部 v3 配置人工审定一次"无执行留痕）；A 有条件通过（8/8 逐字节一致、live digest 等值、upgrade 映射完整、两级校验闭环全部独立复核成立，1 条偏移约束 = smoke_stage01_searchqa.py 仍 import 已删除的 MODEL_ROLES 即 ImportError——scripts 同步漏掉一份）。修复轮（本条对应 commit）逐项落实：两条偏移约束（smoke 脚本一行改名 + 7 份 v3 配置逐份人工审定留档 docs/reviews/v3_config_review_20261015.md，7/7 忠实）；琐细项一批清（装配工具 docstring 与 ASSEMBLED_FROM 文案的旧 skill_delivery 拼写、get_method 与 acceptance_mode 两个零调用者函数删除、装配 v2 源升级分支补专属测试、build_skillopt_split_configs.py 改产 v3 且实加载验证、fork_run_stage04.py 历史工具加头注、stage0 v2 拒载补测试、AGENTS 落 R6 维护分支规则、runbook 两处陈旧声明更正、stage0 与 methods 的 docstring 陈旧段更正、方案 §5 补逐字冻结语义披露、§8 与 P1a 纯表裁决对齐）。1914 测试全绿。P1b 关闭，进 P4。

工件：`docs/reviews/v3_config_review_20261015.md`（v3 配置逐份审定）、`docs/reviews/multi_method_step_P1b_review_20261015_{A,B,C}.md`（评审全文）、`scripts/smoke/smoke_stage01_searchqa.py`、`scripts/run/build_skillopt_split_configs.py`、`scripts/run/fork_run_stage04.py`、`src/expedistill/methods/registry.py`（get_method 删除）、`src/expedistill/core/config.py`（acceptance_mode 删除）、`src/expedistill/stages/assemble_reuse.py`（拼写与文案）、AGENTS.md、README.md、runbook、方案 §5/§8、本条对应 commit

## 2026-10-15 21:20 — P4：curation 槽位双驱动与 exams 开关（预注册消融对）

一段话摘要：P4 完成。curation 槽位从"仅参数"升级为双方法驱动：agentbrew（K5 薄壳，composites.curation 一行未动，stage5 变成与 stage4 同形的"解析并调用"前端）与 none（消融：裸 accepted 集即库、零模型调用、无 curation_log；预算仍是硬阀——超限响亮停机；bank 文件原子整写而非追加，幂等由构造保证——初版用 append_bank 有续跑重复行 bug，自查发现改为整写并补幂等测试）；curation 缺省项须点名（双方法槽位），METHOD_DEFAULTS 与 upgrade_v2 同步加 name。authoring.exams 开关落地：stage3 按 resolve_params 的 exams 值跳过 gen_exam（单元完成判据同步），集中冲突校验放在 authoring 槽位的 check（exams:false × exam 验收 或 agentbrew 整理 = ConfigError，消融对 exams:false + curation:none + live_run 可解析）。金矩阵验证"仅 manifest 漂移"（curation 缺省加 name 属身份变化、行为等价）后同提交重生成。1921 测试全绿。

工件：`src/expedistill/methods/curation/`（__init__ 双 Params、agentbrew.py、none.py）、`src/expedistill/stages/stage5_curation.py`（前端化）、`src/expedistill/stages/stage3_generation.py`（exams 门）、`src/expedistill/methods/authoring/__init__.py`（check_authoring）、`tests/unit/methods/test_curation_none.py`、`tests/unit/methods/test_params.py`（+3）、golden 重生成、AGENTS.md、README.md、本条对应 commit

## 2026-10-15 21:55 — P5：provider 接缝与角色表 None 档；两项按简洁化跳过留档

一段话摘要：P5 完成（用户指令明确要求做到 P5）。落地两个有当前价值的最小接缝：llm/providers.py 纯数据表（名字 → 客户端 builder，EXPEDISTILL_LLM_PROVIDER 环境变量选择、缺省 openai_compat，未知名字响亮列出已知——与 methods 注册表同形，无自动发现）；ROLE_DEFAULTS 的 None 档（None 锚位不补默认，pipeline 的缺角色检查负责响亮拒绝——"必须显式配置"的语义由现有检查免费承担，零新代码路径）。两项按用户"避免 over-engineered"裁决明确跳过并留档于方案 P5 行：非 chat 调用（embedding，唯一消费者 embedding delivery 尚无人提出，到时按 §8 清单加）；TB 插件真实查询文本（benchmark 插件层的方法实验决定，会改 tasks_digest 即实验身份，不属框架重构）。1924 测试全绿。

工件：`src/expedistill/llm/providers.py`、`src/expedistill/core/config.py`（None 档）、`src/expedistill/stages/pipeline.py`（default_client 走注册表）、`tests/unit/methods/test_params.py`（+3 provider 测试）、AGENTS.md、方案 §7 P5 行、本条对应 commit

## 2026-10-15 22:50 — P4+P5 三评审全过，修复批落地，最终验收通过

一段话摘要：P4+P5 合并三评审（docs/reviews/multi_method_step_P4_P5_review_20261015_{A,B,C}.md）三票全过、零偏移约束。最有价值的发现：C 的 R1——P4 给 curation 加 name 后，P1b 已转换的 7 份 v3 配置全部拒载（转换器不覆盖既有文件），已逐份补 name 并在审定文档加更正注记；A 的 CLI 缺口——core 的 ConfigError 不在 CLI 捕获族里，方法参数错误会以 traceback 呈现，已显式捕 CoreConfigError（不用裸 ValueError，避免吞代码 bug）；B 对 P5 None 档的批评（四项中唯一无消费者却落地、形状对预期使用者也不适用）按其最低成本路径处理（补行为测试路径由现有 stage5 夹具族承担，方案 §5 已同步语义）。琐细批：store 新增 write_jsonl 原子整写原语（none 的 bank 与检索审计两处手写副本收编）；check_authoring 去死回退、默认名单一真相；none 单次加载 + stale 语义差异披露；.env.example 登记 EXPEDISTILL_LLM_PROVIDER；provider 测试归位 tests/unit/llm；stage3 docstring 同步 exams 语义 + 补 exams=false 两条测试（C 的 R2）；方案 §5/§8 补注（None 档语义、单实现槽位转型的四步清单）。最终验收：1927 测试全绿（金矩阵 10 项在内）；转换器/审计自检/v2 拒载/CLI 干净报错四项实跑验证；方案 §2–§9 约束逐条自查无违反。P0→P5 重构计划全部完成。

工件：configs/experiments/*_v3.yaml（7 份补 name）、`docs/reviews/v3_config_review_20261015.md`（更正注记）、`docs/reviews/multi_method_step_P4_P5_review_20261015_{A,B,C}.md`、`src/expedistill/infra/store.py`（write_jsonl）、`src/expedistill/methods/curation/none.py`、`src/expedistill/methods/authoring/__init__.py`、`src/expedistill/cli.py`、`.env.example`、`tests/unit/llm/test_providers.py`、`tests/unit/stages/test_stage3.py`（+2）、方案 §5/§8、本条对应 commit

## 2026-10-15 19:25 — 执行—学习闭环重构方案（scope 级）

一段话摘要：用户指出五槽位流水线把"某一种做法的内部工序"当成了系统接口，并提出新组织方式：系统只有执行（技能库 + 任务 + 模型 → 新轨迹）与学习（旧库 + 验收结果 + 轨迹 → 新库）两种阶段，阶段由模块组合而成，方法 = 阶段序列（exam = D L A L A L A L C，live_run = D L B L B L C）。用户的两条设计决定：①验收只如实测量、不做决定，保留 / 修改 / 新增 / 删除的门在学习阶段内部；②agentbrew 分两个版本——靠考题回归决定是否合并（exam 用）与直接接受合并（live 用）。据此写成 scope 级方案：跨阶段只传技能库、轨迹集、验收报告；执行 = 交付 + 执行方式 + 验收，学习 = 决策 + 找证据 + 写作 + 整理且只有一种 L；出题与泄题审计归伪执行；技能身份与段解绑（升 schema）；exam 预期行为不变，live 有两处有意变化（不再因出题失败淘汰技能、不再因考题回归撤销合并）。尚未开始实现；本条对应 commit。

工件：`docs/decisions/execute_learn_loop_20261015.md`（方案）

## 2026-10-15 20:10 — 执行—学习方案第一轮三路评审、裁决与实施方案落档

一段话摘要：scope 方案提交后启动三路独立评审（A 代码事实 7 条 / B 扩展性 4 条 / C 迁移 9 条偏移约束，全文 docs/reviews/execute_learn_plan_review_20261015_{A,B,C}.md）。三份高度交叉印证，关键裁决十一条（方案 §10）：D1 技能 ID 派生规则不变、解绑只落 schema 层（Skill.source_subtasks + Exam.skill_id，curate prompt 内嵌 skill_id 使换 ID 的等价验证面爆炸，且不切段需求可走转型四步另立 ID）；D2 v4 装配器支持 v3 源；D3 验收报告=对既有落盘工件的读取器（不落盘不建新对象）；D4 只保留一张注册表、五槽位逐文件处置；D5 跳过语义=现行续跑键、K6 测量核去轮化；D6 迁移重切 M0–M4（M2/M3 golden 逐字节无 diff，M4 语义验收）；D7 标签从冻结序列派生；D8 原则 5 改"学习不发起真执行"（K3 原语由伪执行与 merge_check 共用）；D9 配置统一嵌套 + C 加 control 子句 + 完整字段映射表；D10 no-op guard/weak_already_knows/空洞政策进规则清单（行为保持）；D11 出题调用点挪进 A 发生在 M4。据此修订方案 v2 并写出实施方案（字段表、注册表键集、配置 v4 全形状、报告读取器、决策规则清单、失败处置表、标签映射、预注册语义等价准则 E1–E5、M1–M4 逐步验收与多代理分工）。两项用户级裁决（ID 不变、装配器支持 v3 源）按自主决策授权选定并留痕，用户可推翻。尚未开始实现。

工件：`docs/reviews/execute_learn_plan_review_20261015_{A,B,C}.md`（三份评审）、`docs/decisions/execute_learn_loop_20261015.md`（v2 修订）、`docs/decisions/execute_learn_implementation_20261015.md`（实施方案）、本条对应 commit

## 2026-10-15 21:40 — 实施方案第二轮三路评审、22 条偏移约束裁决、v2 定稿

一段话摘要：实施方案（bb0935f）交三路评审：A 正确性 7 条（P1 后续验证轮数错对象、P2 漏收 G10/G11 耗尽、P3 D 双臂 n_attempts 缺省错、P4 对象仓布局空白、P5 M2 验收门自相矛盾、P6 E2 在 golden 上空验证、P7 读取器两处行规格误导）；B 简洁性 6 条（B2-1 learn 注册键死数据改五内容族、B2-2 compare 的 control 是 mode 复写、B2-3 n_attempts 检查比错对象、B2-4 泄题账本跨 A 单真相、B2-5 对象布局未裁决、B2-6 scripts 跟随者漏四个活工具）；C 迁移 9 条（I1 装配器对全部 v1 源 run 当场破裂、I2/I3 分层红灯、I4/I5 验收仪器缺口、I6 live 白名单矩阵不可观测、I7 装配器五点、I8 与 P1 同、I9 M4 原子核）。全部裁决并入实施方案 v2（§12）：对象仓布局 P4 与 B2-5 正面冲突，采 P4（续跑语义证据胜出：exam_once 序号按仓计数、跨相位 attempt 检索、首判为准）；注册表五内容族、compare 统一 against 形、M2 薄壳中间态、M4 拆 M4.1 仪器先行–M4.6 文档子序列、E1–E6 六准则（新增 E6 比对 live 测量账本）。scope 方案两处措辞同步修正（出题积木留 blocks 只挪调用点；验收轮数与切段修复预算分开表述）。v2 定稿，进入 M1 实施。

工件：`docs/reviews/execute_learn_impl_review_20261015_{A,B,C}.md`（三份）、`docs/decisions/execute_learn_implementation_20261015.md`（v2）、`docs/decisions/execute_learn_loop_20261015.md`（措辞修正）、本条对应 commit

## 2026-10-15 20:45 — 执行—学习闭环 M1：Skill/Exam 对象 schema v2 与 v1→v2 迁移落地

一段话摘要：按实施方案 §2 与 §9 的 M1 行完成数据对象变更：Skill 加必填 `source_subtasks`（段生 = [自己的 subtask_id]；合并产物 = 各来源的并集，K5 写入时算好）、Exam 加必填 `skill_id`，SKILL/EXAM_SCHEMA_VERSION 1→2，两个新字段都无默认值，所有构造点（blocks/author 的写作与出题、curation 的合并产物、src 与 tests 与 scripts 的全部夹具，AST 全量扫描核对）逐一补上。旧读规则按 AGENTS 七.3 显式迁移：`Skill.from_dict` 对 v1 段生记录按 ID 后缀推导、对 v1 合并产物响亮报错并指向新增的 core 迁移纯函数 `migrate_skill_record(record, skills_by_id)`（嵌套合并沿 merged_from 递归取并集，未知形状/缺兄弟/成环均响亮报错）；`Exam.from_dict` 由 exam_id 换后缀推导 skill_id；v2 记录缺新字段一律 TypeError。I1（M1 必做）：装配器 bank 完整性比对改两侧同构（row 经迁移函数再 from_dict——方案里的字面表达式 `Skill.from_dict(row)` 对 v1 合并 bank 行会触发 from_dict 的设计内报错，故经 migrate_skill_record，已在提交说明披露），装配器的源 skill 加载同走迁移函数；curation.py:945 经 grep 确认并无比较处，实际是崩溃续跑的 bank 补账写入，改为按存储记录的原样落盘——补出的行与既有前缀同版本（否则 v1 run 续跑会写出 v1/v2 混版 bank，且 v1 合并成员根本过不了 from_dict），该解读已在提交说明披露。golden 八组合连同 provenance 同提交重生成，结构化审计逐键验证：diff 仅为 skill 记录（含 skill_bank.jsonl 行）加 source_subtasks、exam 记录加 skill_id、两者 schema_version 1→2，别无任何漂移；provenance 仅 base_commit 随 HEAD 变化。测试 1927→1945 全绿（迁移纯函数单测含段生/扁平/嵌套合并/推不出报错；I1 验收——v1 对象源 run 夹具 bank 级装配原样通过且覆盖扁平合并推导；curation v1 前缀续跑补账测试），两条 I1 修复均验证过去掉后测试转红。README §2 已同步字段语义与 v1→v2 读规则。M2（执行侧抽取）未开始。

工件：`src/expedistill/core/models.py`（字段、升版、from_dict 迁移、migrate_skill_record / merged_source_subtasks）、`src/expedistill/blocks/author.py`、`src/expedistill/composites/curation.py`（合并产物写新字段 + 补账原样落盘）、`src/expedistill/stages/assemble_reuse.py`（I1）、`scripts/smoke/smoke_leak_audit_k4.py`、`scripts/audit/audit_live_run.py`、README §2、`tests/fixtures/golden/*`（8 组合 + provenance 重生成）、`tests/unit/core/test_models.py`（迁移单测）、`tests/unit/stages/test_assemble_reuse.py`（I1 验收测试）、`tests/composites/test_curation_resume.py`（补账测试）、其余测试夹具补字段、本条对应 commit

## 2026-10-15 23:05 — M1 三路验收：B 过，A/C 各出偏移约束，裁决后进修复轮

一段话摘要：M1（32d6c86）三路验收：B 简洁性通过（0 偏移约束，"没有做任何方案没要求的事"，golden 零漂移经独立复核）；A 正确性不通过（P-A1：装配出的 v1 形态 run 在阶段 4 第一步就崩——装配器字节复制保持 v1 形态，而 from_dict 按设计拒绝 v1 合并产物，stage3_candidates/pending_groups/审计工具等全量加载点全部没接迁移，用仓库自己的 I1 夹具机械复现）；C 迁移不通过（P-C1 同根因 + P-C2：v1 run 的 curation 续跑在库外有合并产物时崩，专项测试只覆盖无合并形态）。裁决写入实施方案 §2：采 A 修法（装配器迁移后落盘、产物 v2 原生、消费方零改动）+ 吸收 C 两点（审计工具读取经迁移；直接在 v1 run 上用新代码续跑明文裁定 R6 范围外，from_dict 报错加维护分支指向，不建无消费者的 run 级迁移工具）。进入修复轮，修复后 A/C 重审。

工件：`docs/reviews/execute_learn_m1_review_20261015_{A,B,C}.md`（三份验收评审）、`docs/decisions/execute_learn_implementation_20261015.md`（§2 M1 验收裁决）、本条对应 commit

## 2026-10-15 21:29 — M1 修复轮：装配产物 v2 原生 + 审计工具读 v1 经迁移

一段话摘要：按实施方案 §2 末尾的 M1 验收裁决五条完成修复（A 的 P-A1 与 C 的 P-C1/P-C2 同根因：只有契约点名的两处接了 v1→v2 迁移，全量加载型消费方全部没接）。裁决 1：装配器对 skills / exams / bank 行改为**迁移后落盘**（skill 经 migrate_skill_record + from_dict，exam 经 from_dict 的 v1 推导，v2 恒等穿过，trajectories / subtasks / attempts 仍逐字节拷贝），装配产物成为 v2 原生 run，下游消费方零改动即可走普通 from_dict；ASSEMBLED_FROM.md 增加对象行已迁移为 schema v2 的披露行，身份比对逻辑未动（本就两侧同构）。裁决 2：core/models.py 新增批量包装 migrate_skill_rows(rows)（内部建 sibling map、逐行迁移、v2 恒等返回），装配器的源 skill 加载与审计工具的技能全量读取共用它；audit_live_run 的 _candidate_groups 与 check_injection_audit 成员加载改走迁移（AGENTS 六.7 标准流程工具必须能审今天的全部 v1 正式 run，含 round≥1 注入行指向 bank 合并成员的场景）。裁决 3：Skill.from_dict 对 v1 合并产物的报错在指向迁移函数之外追加维护分支规则（直接在新代码上续跑 v1 run 不受支持，AGENTS 六.1），CONFIG_SCHEMA_VERSION 不动、不建 run 级迁移工具。裁决 4：curation v1 前缀补账测试保留，helper 与测试的 docstring 注明 v1 续跑场景本身属 R6 范围外、测试钉的是"按存储原样落盘"不变量。裁决 5：装配器迁移失败包成 ConfigError（与同处损坏源错误族一致）。测试 1945→1950：I1 夹具扩展为含扁平 + 嵌套合并 bank 成员与库外回退合并（正是 stage3_candidates 与 _check_bank_not_stale 的崩溃形态）；新增端到端测试（v1 源 → 装配 → 对产物跑阶段 4 枚举与阶段 5 非空库短路，零模型调用）；新增审计工具 v1 最小夹具读取测试；新增装配器 ConfigError 测试；原 v1 形态断言翻转为 v2，与事实相反的注释按新事实改写。回退转红已验证：装配器回退后端到端测试恰在评审 A 复现的 stage3_candidates→from_dict 崩溃点转红（另有装配 v2 断言、ConfigError 两处转红），审计工具回退后 v1 读取测试恰在评审 C 复现的崩溃点转红。golden 不在装配器路径：金矩阵 11 项全过、tests/fixtures/golden 零改动；全套 1950 全绿，ruff E9/F 与 test_layering 通过。待 A/C 复审本 diff。

工件：`src/expedistill/stages/assemble_reuse.py`（迁移后落盘、批量加载、ConfigError、ASSEMBLED_FROM 披露）、`src/expedistill/core/models.py`（migrate_skill_rows、报错加维护分支指向）、`scripts/audit/audit_live_run.py`（技能全量读取经迁移）、README §2（装配器落盘语义一句同步）、`tests/unit/stages/test_assemble_reuse.py`（夹具扩展、端到端、ConfigError）、`tests/unit/core/test_models.py`（批量迁移单测）、`tests/unit/test_audit_live_run.py`（新增）、`tests/composites/test_curation_resume.py`（docstring R6 注记）、本条对应 commit

## 2026-10-16 10:56 — M2 执行侧抽取：delivery 三新驱动入注册表 + K6 测量核去轮化薄壳

一段话摘要：按实施方案 §9 M2 行完成执行侧抽取，两块工作。第一块：delivery 族新增三个驱动并全部经注册表缝接入——`none`（裸注入规格 skills_injected=None + 空块，从 stage6 裸臂的硬编码抽出，stage6 的 fresh_bare 控制臂改为经注册表解析调用）、`per_skill`（单技能注入块，直接委托 K4 现用的 inject 层 format_skills_block([skill])，不重复实现；其消费者 exam 执行驱动 M4 才出现，本步只注册 + 契约单测，docstring 写明这是方案 §3 定好的 v4 形状）、`group_by_task`（按任务打包的 per-task 注入解析器，从 live_run 的内联 _injection_resolver 逐字抽出，live_run 改为经注册表解析调用，原内联实现删除不留转发 shim）。注册表新增 `resolve_fixed(slot, name)` 入口：按显式名字解析固定驱动 + 空参数构造，与现有 resolve 共用同一张纯数据表、同一 build_params 校验与懒 import（带 check 的条目响亮拒绝——check 需要 config 而固定解析没有），供"语义不是配置选择"的调用点（stage6 裸臂、live 的组注入）使用；三个新驱动的入口形状与各自调用方对齐（none 与 whole_bank 同形 (ctx, params, bank, tasks)；per_skill 为 (ctx, params, skill)；group_by_task 为 (ctx, params, store, benchmark)），返回形状统一为 (method_kwargs, audit_rows)。第二块：composites/live_acceptance.py 测量核去轮化（薄壳中间态，P5）——judge_round / attribute_group / apply_revisions / no_op_guard / audit_row / finalize_group 拆为轮无关核（`_` 前缀私有函数，账本路径 verdicts_path / attributions_path / failures_path / revision_failures_path、轮仓目录 round_store_dir、审计文件路径 audit_path、轮标签全部作显式参数传入；judge_round 核拥有 verdicts 的 append-once 与重算比对，s-8）+ 薄壳（对外签名与落盘路径原样保留，壳只做 _check_round + 算出与今天完全相同的路径再调核）；LIVE_DIR_NAME 与 _ROUNDS=(0,1) 留在壳层，docstring 注明壳在 M4 删除；finalize_group 的轮优先级泛化为 rounds 序列参数（末轮定胜负、更早轮 better 接受），对 rounds=(0,1) 用 821 种账本行组合穷举比对新旧决策逐位相等。验收：全套 1950→1958 全绿（新增 8 个测试：三驱动契约测试 + resolve_fixed 四条）；金矩阵 8 组合逐字节零 diff、tests/fixtures/golden 无任何改动；test_acceptance_resume 与 test_live_acceptance_resume 原样通过、文件零改动（现套件 patch 的 la.append_jsonl、_pending_group、预算常量等内部名全部保留）；ruff E9/F 与 test_layering 通过；无测试需要迁移 patch 目标（没有任何测试引用被移动的 _injection_resolver）。gen_exam/audit_leak 未搬（留 blocks，M4 只挪调用点）；stage3、K4、配置 schema 未动。

工件：`src/expedistill/methods/delivery/{none,per_skill,group_by_task}.py`（新建）、`src/expedistill/methods/delivery/__init__.py`（三个 Params）、`src/expedistill/methods/registry.py`（三行 + resolve_fixed + 共享 _load_params/_load_driver）、`src/expedistill/methods/__init__.py`、`src/expedistill/stages/stage6_test.py`（裸臂改调 none 驱动）、`src/expedistill/methods/acceptance/live_run.py`（经注册表缝解析 group_by_task，删 _injection_resolver）、`src/expedistill/composites/live_acceptance.py`（核/壳拆分）、`tests/unit/methods/delivery/test_drivers.py`（新建契约测试）、`tests/unit/methods/test_params.py`（TestResolveFixed）、本条对应 commit

## 2026-10-16 11:10 — M1 修复轮 A/C 重审通过，M1 关闭（补记）

一段话摘要：修复提交 85bfa4f 经 A/C 重审双双通过（docs/reviews/execute_learn_m1_recheck_20261016_{A,C}.md）。A：P-A1 已解决——自写 33 断言独立脚本验证 v1 源（含嵌套/回退合并）装配产物全部 v2 原生、阶段 4 枚举与阶段 5 短路不崩，回退转红恰在第一轮复现点逐字一致，落盘与 store.save 逐字节同构，消费方零行改动；0 偏移约束。C：P-C1/P-C2 均解决，R6 边界裁定成立——误续跑 v1 run 的崩溃点恰在阶段 4 第一个动作（任何模型调用之前）且零数字产出，字面 stage0 闸反而是方案禁止的影响执行流程硬检查；审计工具两处加载点均走迁移且注入式复核成立；回退转红在 /tmp 副本重放确认（PYTHONPATH 坑：不显式指向副本 src 会因 editable 安装指向原仓库造成假阴性）。两票各留 2-3 条文案级琐细，不阻塞。M1（32d6c86 + 85bfa4f）关闭。补记：本条应记于 M2 条目之前——当时会话工作目录被切到外层工作区，本条误写入外层 log.md 且误提交进外层仓库 main；已撤销外层误提交并将外层完全复原（外层 log.md 本为空文件，已截回空），本条按原时间线补记于此。

工件：`docs/reviews/execute_learn_m1_recheck_20261016_{A,C}.md`（重审全文）、本条对应 commit

## 2026-10-16 11:27 — M2 收尾：_finalize_group 决策语义钉成受控组合测试（C-1）+ resolve_fixed 报错构造去重（F2/T-1）

一段话摘要：落实 M2 三路验收的唯一遗留——评审 C 的偏移约束 C-1（提交 6b86e65 声称的"821 种账本行组合穷举比对"是一次性脚本、未留版本控制，违反"结论依赖的要留档"）。不重证新旧等价（A/C 两路已分别用 13487/7381 种组合独立复验为零失配），而是把当前去轮化核 `_finalize_group` 的决策语义钉成受版本控制的确定性组合测试 tests/composites/test_finalize_semantics.py——它是 M4 删除薄壳时的守卫：50 个测试全部直驱核（显式 verdicts 路径 + 显式 rounds 序列，M4 删壳不需要改一行）。组合集按语义轴分七组：每种行型单独出现 13 例（含越界轮 round=2 行与无 round 键行的两面规则——判定对它们视而不见、终态扫描照收）、轮 0/轮 1 边界对 4 例（评审 A 点名的"轮 0 better+轮 1 miss""轮 0 miss+轮 1 miss"在内）、终态行乱序到达 4 例（append 序首行胜）、决胜轮内首行粘滞 4 例、越界行穿插 9 例（长度至 4）、三轮序列 rounds=(0,1,2) 9 例（评审 B 琐细 2 点名的泛化缺口：末轮定胜负、更早轮 better 接受/miss 保持 pending，含与 (0,1) 表同行不同果的差分对）、单轮序列 rounds=(0,) 4 例；另加两条独立测试：他任务行不可见、commit 只碰 pending 成员。预期值全部从模块 docstring 的决策规则（D1 终态行弃组且 append 序首行胜 / D2 最新有行的轮定胜负 / D3 末轮 better 接受 miss 弃 / D4 更早轮 better 接受 miss 不决策 / D5 决胜轮首行粘滞 / D6 轮集外行不参与判定）手工推导后写死为字面量，每组断言返回 dict、逐成员终态+discard_reason、verdicts 账本只读不变、二次调用零提交零改动。回退转红已验证：三个变异（更早轮 better 改为不决策→6 红；verdict 扫描取末行→2 红；reversed(rounds) 改正序→10 红）。第二件事按评审 A F2/B T-1 去重 resolve_fixed 与 _entry_for 的未知名字报错构造：收敛到共享 `_entry_named`（报错文案唯一构造点）；未照搬两路评审建议的字面形态 `_entry_for(slot, {"name": name})`——该形态在 name=None 且槽位只有一个条目时会从今天的响亮 ConfigError 变成静默解析出该条目（行为漂移），共享查找助手对每个输入（含 None）行为逐位不变，实测四种报错文本与去重前逐字节相同。验收：全套 1958→2008 全绿（+50，新套件 0.2 秒）；ruff E9/F、test_layering 通过；金矩阵 11 项通过且 tests/fixtures/golden 零改动。M2 三份评审文档（A/B/C）仍为未跟踪状态，留给 M2 关闭提交。

工件：`tests/composites/test_finalize_semantics.py`（新建，_finalize_group 决策语义守卫，50 测试）、`src/expedistill/methods/registry.py`（报错构造收敛到 _entry_named）、本条对应 commit

## 2026-10-16 13:40 — M2 三路验收通过，C-1 组合测试落库，M2 关闭

一段话摘要：M2（6b86e65）三路验收全过（docs/reviews/execute_learn_m2_review_20261016_{A,B,C}.md）：A 行为等价 0 偏移约束——除核/壳 AST 级比对外，补做 22 个执行级场景双实现比对（返回值、账本文件字节、库终态、异常类型全等）与 13487 组合独立复验；B 简洁性 0 偏移约束——薄壳合计约 44 行为最小中间态、resolve_fixed 形状干净、per_skill 裁决留（M4 验收须核其获得真实调用方，否则删）；C 续跑契约 1 条不阻塞偏移约束 C-1——"821 组合穷举"为一次性脚本未留档（违反 AGENTS 五.1），C 以 7381 种组合独立复验语义为真。修复提交 dc95421：新增 tests/composites/test_finalize_semantics.py（50 例，直驱去轮化核，六条决策规则 D1–D6 各至少两例钉住，含非 (0,1) 轮序列差分对；预期值从 docstring 手工推导写死，非实现复读；三变异回退转红验证），M4 删薄壳时守卫一行不用改；顺手去重 resolve_fixed/_entry_for 的报错构造（有意偏离评审建议形态——照搬会造成单条目槽位静默解析漂移，已披露）。1958→2008 全绿，金矩阵零改动。M2 关闭，进 M3。

工件：`docs/reviews/execute_learn_m2_review_20261016_{A,B,C}.md`（三份验收评审）、`tests/composites/test_finalize_semantics.py`、`src/expedistill/methods/registry.py`、commit dc95421、本条对应 commit

## 2026-10-16 12:02 — M3 学习侧抽取完成：decision.py 决策规则、reports.py 三读取器、agentbrew merge_check、注册表 evidence/author 两族

一段话摘要：按实施方案（docs/decisions/execute_learn_implementation_20261015.md §3 与 §9 M3 行）完成执行—学习闭环的学习侧抽取，全程行为保持。一、新建 composites/decision.py（纯函数，同层合法）：§3 决策清单 17 条逐条落点——exam 侧 8 条中 7 条抽为具名函数（裸答对、本版答对、泄露分轮次、答错分轮次、泄题改写来源选取 P7b、测量耗尽的词表），K4（accept_skill 签名不变）与 exam 驱动改调；改写预算耗尽一条留在 _rewrite_round（与逐 try 账本追加熔合，拆开会破坏续跑的轮次记账）；live 侧 9 条中 6 条抽为具名函数（终态判定 live_final_action、no-op 判据、strict 族弱已知短路、注入超限、全洞 undetermined、G10 耗尽判据），K6 核与 live 驱动改调；G10 计划应用（版本重放守卫）与 G11 耗尽留原处并在 docstring 注明缘由。discard_reason 词表与 strict 短路规则集的定义处随之移到 decision.py（K6 原 6 个常量与 _ALL_SUCCESS_SAFE_RULES 改为同名 re-export，杜绝两套真相且解开 decision↔K6 的 import 环）。二、新建 composites/reports.py（纯函数只读）：baseline_report（轨迹仓+shortfall→每任务两臂成功/总数+task_select 派生 F 集）、exam_report（共享 attempts 仓+leak_verdicts+改写耗尽→每技能行，attempt 字段即 P7b 来源选取）、live_report（三账本+轮仓→每任务组行，不含 no-op 字节等价字段，P7a）；路径推导全部复用 K4/K6 的 helper，不复制。三、AgentBrewParams 加 merge_check（缺省 exam=现行为逐字节不变；none=合并单元跳过回归考，产物 pending→accepted 零 answerer/judge 调用，格式检查与预算硬阀不动），经薄壳接进 K5 的 curate_bank。四、注册表 _ENTRIES 加 (evidence, segment) 与 (author, distill_diagnose)，SegmentationParams/AuthoringParams 类定义 re-home 到 methods/evidence/ 与 methods/author/（槽位包 re-export，v3 配置路径零改动；author 族保留 exams 开关），新条目无驱动、不带 check，配置接线等 M4。验收：2039 passed + 3 deselected（基线 2008 + 新增 31：族契约 6、决策规则 13、读取器 9、merge_check 3），金矩阵逐字节零 diff（fixtures 无改动），两套崩溃续跑套件对 95442b9 零行改动原样通过，ruff E9+F 与 test_layering 通过。遇到的问题：无行为漂移，仅一次测试期望笔误（G10 耗尽判据的预算边界，测试侧修正）。

工件：`src/expedistill/composites/decision.py`、`src/expedistill/composites/reports.py`、`src/expedistill/methods/evidence/__init__.py`、`src/expedistill/methods/author/__init__.py`、`src/expedistill/methods/segmentation/__init__.py`、`src/expedistill/methods/authoring/__init__.py`、`src/expedistill/methods/registry.py`、`src/expedistill/composites/acceptance.py`、`src/expedistill/composites/live_acceptance.py`、`src/expedistill/composites/curation.py`、`src/expedistill/methods/acceptance/{exam,live_run}.py`、`src/expedistill/methods/curation/{__init__,agentbrew}.py`、`tests/unit/methods/test_family_entries.py`、`tests/unit/composites/test_decision.py`、`tests/composites/test_reports.py`、`tests/composites/test_curation.py`、`tests/unit/methods/test_params.py`、`AGENTS.md`（源码树同步）、本条对应 commit

## 2026-10-16 12:26 — M3 收尾：exam_report 行内 attempt 补 skill_version/retry_round（评审 C-1）

一段话摘要：落实 M3 三路验收的唯一遗留——评审 C 的偏移约束 C-1（docs/reviews/execute_learn_m3_review_20261016_C.md §4a：exam 报告行的 attempt 条目缺 skill_version，“本版答对 → keep”与“L 修订已落库、重答未发生”（attempt 是旧版的）两种状态在行上同形，M4.3 相位驱动照现状接线会在崩溃续跑时对已修订技能二次修订）。修复取评审给出的第一方向（行内补字段，不新增推导）：exam_report 的 attempt dict 从 {attempt_id, verdict, judge_reason, weak_answer} 扩为再加 skill_version、retry_round（Attempt 本就有的两个字段，随行暴露）；行级 version 字段保持现状（行头是当前存储版本）；docstring 一句话写明消费方语义——L 相位按 (skill_id, skill_version) 匹配“本版 attempt”，旧版 attempt 不作为本轮依据。方案同步两处：§3 报告读取器表 exam_report 行的 attempt 括号补上两字段（该缺口是计划与实现的共同缺口，方案原文同样缺 version）；§8 的 E5 行补“崩溃续跑变体必须含'修订已落库、重答未发生'窗口（评审 C-1）”。测试：test_reports.py 新增 test_exam_report_attempt_names_the_version_it_answered——构造“v1 的 attempt 已判 False + 技能已改写为 v2”（照 _rewrite_round 的真实落盘形态：version=2 + rewrite_history 一条）的账本，断言行头 version=2 而 attempt.skill_version=1（两种状态不再同形）；再落一条 v2 判 True 的 attempt，断言 attempt.skill_version=2（“本版答对”从行上可判）；既有 test_exam_report_row_over_a_judged_round 的全 dict 断言随新行形状补两字段。回退转红已验证：stash 掉 reports.py 修复后恰两条测试转红（新测试 + 形状断言），恢复后全绿。验收：全套 2039→2040 passed + 3 deselected；金矩阵 11 passed、tests/fixtures/golden 零改动（仍 32d6c86）；两套崩溃续跑套件零行改动、236 passed；ruff E9/F 与 test_layering 3 passed。评审建议的第二方向（exam 侧修订应用加 from_version 重放守卫，对齐 live 侧 _apply_revisions）不在本次范围，M4.3 接线时按需定。

工件：`src/expedistill/composites/reports.py`（attempt 补两字段 + docstring 消费方语义）、`tests/composites/test_reports.py`（新增 C-1 钉住测试 + 既有形状断言同步）、`docs/decisions/execute_learn_implementation_20261015.md`（§3 字段表、§8 E5 各补一处）、本条对应 commit

## 2026-10-16 16:10 — M3 三路验收通过，C-1 收口，M3 关闭

一段话摘要：M3（d80d6d3）三路验收全过（docs/reviews/execute_learn_m3_review_20261016_{A,B,C}.md）：A 行为等价 0 偏移约束——K4/K6 十个替换 hunk 逐行对照改前原文逐位一致，17 条决策清单全部有落点、无借"留原处"藏匿，词表 identity 测试有效；B 简洁性 0 偏移约束——Action 三 kind 无膨胀，读取器字段与方案表逐字段吻合无偷加，留原处四条均为真实约束（L16 切分线"画得准"），决策测试为手写字面量非实现复读；C 续跑与 M4 前置 1 条不阻塞偏移约束 C-1——exam 报告行 attempt 缺 skill_version，"本版答对"与"修订已落库待重答"两状态同形，M4.3 接线后崩溃续跑会二次修订。修复提交 9ca1ad6：attempt 条目补 skill_version/retry_round（Attempt 既有字段随行暴露），补钉住崩溃窗口的测试（行头 version=2 而 attempt.skill_version=1），方案 §3/§8 同步（E5 补"修订已落库、重答未发生"窗口要求）；exam 侧修订 from_version 重放守卫按 C-1 第二方向留 M4.3 按需定。2040 全绿，金矩阵零改动。M3（d80d6d3 + 9ca1ad6）关闭，进 M4.1（等价仪器先行）。

工件：`docs/reviews/execute_learn_m3_review_20261016_{A,B,C}.md`（三份验收评审）、`src/expedistill/composites/reports.py`（行补版本）、`tests/composites/test_reports.py`、`docs/decisions/execute_learn_implementation_20261015.md`（§3/§8 同步）、commit 9ca1ad6、本条对应 commit

## 2026-10-16 17:55 — M4.1 语义等价仪器先行落地：E1–E6 断言 + 归一化/布局映射 + 69904f7 基线束 + 自检反证

一段话摘要：按实施方案 §8/§9 的 M4.1 子步落地语义等价仪器（M4.3 原子核翻掉逐字节金矩阵后，"行为没变"的唯一判据），不改 src/ 一行、不动金矩阵 fixtures、不改配置。一、基线束 tests/fixtures/golden_semantic_baseline/（8 组合视图 + provenance，共 416 KiB）：由 tests/golden/extract_baseline.py 用 git show 从 69904f7（最后一个 v1 对象、stage 目录布局的金矩阵）提取，只提各准则要读的字段（E1 库行 8 字段、E2 终态/未出生/组终局/泄题判定/三类失败账本、E3 C 相位 summary 五桶 + retrieval_audit 行、E4 (role, prompt, request_text) 三元组、E6 verdicts/attribution 整行、E5 调用数与崩溃点），输出字节确定（排序键、固定缩进、无时间戳），README 记提取命令与来源 commit。二、tests/golden/semantics.py：两条相位序列按 §4 推导成数据表（exam=[D,L]+[A,L]×3+[C] 即 p01–p09、live=[D,L,B,L,B,L,C] 即 p01–p07，retry_limit 缺省 2 核对无误），v4_layout 从序列生成 v4→规范名映射表（C 的 summary/retrieval_audit、逐 B 相位 verdicts 分片、B 后 L 的 attributions/revision_failures、A 后 L 的 rewrite_failures、各 L 的 generation_failures，行内 round/retry_round 字段一律按分片位置改写为规范序号——合成 v4 分片写了相位绝对值也能拉回）；对象仓/bank/leak_verdicts/llm_requests 保持 run 级无映射（P4）；control_source 经别名表规范化（stage1_reuse↔D_weak_arm，v4 实际 token 不一致时改表）；比较入口 compare_combo(combo, v4_root, baseline_root, layout="v4") 返回逐准则 JSON 报告，live 组合的 E4 即白名单①机判（v4 调用多重集恰等于 v3 删光 examiner/EXAM_GEN），每个断言失败的 diff 指认第一个失配的行与字段；E5 机器 resume_variants（0.3/0.6/0.95 三分数 + live 的 revision_window）与 RevisionWindowCrash（在第一个携带 G11 标记的注入块前崩溃——块是 store 的纯派生，标记在块即修订已落库；K1 派发首个 raise 即停，崩溃时零条重答轨迹，实测钉住）+ run_e5 公共入口。三、自检 44 例全绿：提取可复现（重跑逐字节等）；当前金矩阵（v2 对象）8 组合语义视图 == 基线（M1–M3 无行为漂移的独立证据，也实测了新字段剔除归一化）；现跑 v3 两组合经 compare_combo(layout="v3") 全等；合成 v4 布局（预测的 M4.3 产物形状，含 live 删光出题调用）8 组合经 canonicalize 后全等——v4 侧映射在真实数据上端到端验证；11 种定向篡改（任务点名的 6 种 + 快照级 verdict 删除耦合、summary 字段、unborn 注入、组终局翻转 5 种）各自转红、diff 指认正确、无关准则保持绿；E5 全 8 组合全变体续跑等价（每组合 ~0.3 秒）。遇到的问题与处置：(a) E5 的 revision_window 变体在现行代码上抓到真差异——进程内崩溃注入会让 K1 的 error-stop 门在崩溃前给排队兄弟单元写 skip_audit 行（+lock sidecar），真进程死亡不写；与 harness 既有 _is_crash_row（usage 崩溃行）同类，assert_resume_equivalent 扩展为 skip_audit 记账差异容忍（resumed 可多不可缺；无状态消费方，真丢单元由结果态比较兜住）；(b) 白名单①对 v3 live 数据恰好转红（EXAM_GEN 还在）——这是设计使然，白名单是 v3 基线↔v4 产物的关系，身份自检用 layout="v3" 的朴素比较。验收：2040→2084 passed + 3 deselected（+44），金矩阵 fixtures 零改动、src/ 零改动，ruff E9/F 全过，test_layering 3 passed。M4.3 的验收即：v4 代码重生成产物 → compare_combo 全绿 + run_e5 全等。

工件：`tests/golden/semantics.py`（新建，仪器本体：序列/布局数据表、canonicalize、E1–E6 视图与比较、白名单①、E5 机器）、`tests/golden/extract_baseline.py`（新建，可复现提取器）、`tests/fixtures/golden_semantic_baseline/`（新建，8 组合视图 + provenance + README，416 KiB）、`tests/golden/test_semantic_instrument.py`（新建，44 例自检：identity×4 类 + tamper×11 + E5 全 8 组合）、`tests/golden/harness.py`（G11_REVISION_MARKER 常量化 + assert_resume_equivalent 的 skip_audit 记账差异扩展）、本条对应 commit

## 2026-10-16 13:31 — M4.1 收尾：四组评审缺口一次修完（C-1 分片拼接、P-A1/B-5 E2 未出生桥、C-2 E5 期望 layout 感知、文档勘误）

一段话摘要：三路评审（A/B/C）通过 M4.1 后留下的四组不阻塞缺口在 M4.3 原子核之前一次收口，只动 tests/golden/ 仪器与自检、不动 src/ 一行、金矩阵 fixtures 与基线束数据文件零改动（重提取逐字节复现测试保持全绿）。①C-1=B-6：canonicalize 对 round_field=None 的规则原先"后写覆盖"，多 L 相位的 generation_failures 分片只剩最后一片——改为 list 值走分片拼接（dict 值如 C summary 保留 plain-move），无 round 字段规则的序号改记相位索引以定拼接顺序，pin 测试补钉 p02/p08 的 (GENERATION_FAILURES, None, 1/7)，新增双分片用例（p06/p02 各 1/2 行，拼接后 3 行、序号正确）。②P-A1：E2 原判别器基于两个失实前提（"G3 失败但技能已入库"——v3 stage3 是 G2+G3 成对落盘，generation_failures 行永远无已存技能；"v4 弃用进 bank"——bank 只写 accepted，弃用技能的唯一持久宿主是 skills 仓）。改为真实桥接：v3 generation_failures 行（键 subtask_id，经 make_skill_id 对齐）与 skills 仓中 discarded(model_output_error) 的技能投影进同一 unborn 多重集，两侧不同形态的同一事件跨布局可比；注释写明该分支金矩阵恒空（P6 已声明）、由合成测试钉住、其余 discard 理由归 M4 验收场景测试。配套 _compare_e2 增加 layout 参数：跨布局（v4）时不再比较 generation_failures 原始行（每行两侧都已投影进 unborn 一对一比较，原始行比较恰在布局按设计分叉处假红——v3 的 G3 行 vs v4 的弃用技能），同布局（v3）比较保持全强度。③B-5：新增快照级合成桥接对——v3 侧一条 generation_failures 行 + v4 侧对齐的 discarded 仓技能经 compare_views(layout="v4") 全等通过；错位两条（键对不上→E2 红、diff 指认 skill_id；理由不对（retry_exhausted）→E2 红、diff 指认 missing）。测试独立拼 store 键（quote(safe="")），钉住探针 key 拼法。④C-2=S-3：test_e5 的调用数交叉断言改 layout 感知——新公开 expected_model_calls(combo, layout)（v3=provenance 原数；v4 live=原数−该组合基线 E4 的 examiner/EXAM_GEN 数，实测 live full 28−6=22、live split 15−3=12，exam 不变）+ run_layout(run_dir)（按 run 目录是否含 phases/ 判实际布局）+ run_e5 返回值增加 layout 字段；防漂移闸未删除，README 的 M4.3 验收节写明该适配点。⑤文档勘误：篡改计数 9→11（test docstring 与束 README）、"复制到 tmp 目录"的过时描述改"内存深拷贝"（test docstring / semantics.py 注释）、束体积 416→399 KiB（实测 10 文件 408,892 字节=399.3 KiB，修正上一条 log 的 416 口径）、S-1 的 aux 死投影保留并加注释（删了会迫使重生成冻结束；v4 若把 aux 提为顶层键会转红，响亮方向）。回退转红验证 4 条全做：还原覆盖语义→分片用例红；store 探针前缀拼错→桥接正例红（E2.unborn 指认缺失 skill_id）；去掉 _compare_e2 的 v4 跳过→桥接正例在原始账本节假红（证明跳过是承重的）；去掉白名单差量→公式测试红。验收：2084→2090 passed + 3 deselected（+6：分片拼接 1、桥接正例 1、错位 tampers 2、公式 1、run_layout 1），ruff E9/F 过，test_layering 3 passed，tests/golden 61 例 3.1 秒。

工件：`tests/golden/semantics.py`（canonicalize 分片拼接、_discarded_model_output_skills + _e2 桥接、_compare_e2 layout 参数、run_layout/expected_model_calls、run_e5 返回 layout、S-1 aux 注释、docstring 勘误）、`tests/golden/test_semantic_instrument.py`（+6 例：双分片、桥接正例、键/理由错位 tampers、公式钉、run_layout 钉；pin 测试补 generation_failures 序号；test_e5 改 layout 感知期望；docstring 勘误）、`tests/fixtures/golden_semantic_baseline/README.md`（体积 399 KiB、篡改计数 11、M4.3 验收的 C-2 适配点说明）、`docs/reviews/execute_learn_m41_review_20261016_{A,B,C}.md`（三路评审原文，随本提交入库）、本条对应 commit

## 2026-10-16 13:46 — M4.2 upgrade_v3_config 纯函数：v3→v4 映射 + 7 份配置期望形状单测

一段话摘要：按实施方案 §4 与 §9 M4.2 子步落地 schema v4 的纯映射函数 upgrade_v3_config（core/config.py，紧挨 upgrade_v2_config），纯函数 + 单测、不动运行时——CONFIG_SCHEMA_VERSION 仍是 3，load_config/_validate_methods/stage0 一行未改，输出里的 schema_version: 4 只是数据（M4.3 原子核才翻常量并让加载器认识 sequence/phases）。映射按 §4 表逐条：exam(retry_limit=R) → [D,L]+[A,L]×(R+1)+[C] 且 L.curate.merge_check=exam；live_run → [D,L,B,L,B,L,C] 且 merge_check=none（方案披露的身份变更：live 无 A 相位、合并改由轮 1 组重执行证明）；acceptance 的 n_attempts/improvement/max_injection_tokens → B.execution/B.compare.rule（max_injection_tokens 缺省也显式写 null）；delivery 槽原名同参逐字进 C.delivery；curation 槽同参数 + merge_check 进 L.curate（none 方法只带 token_budget）；segmentation 的 K/retry_limit → L.evidence；authoring.exams 开关删除——true 隐含、false 仅 live_run+curation none（唯一合法消融组合，与 v3 check_authoring 同界）可转且结果与 true 完全一致，其余响亮拒绝。相位形状严格按 §4（D 双臂+tasks:train+delivery none；A per_skill+exam 无参数——重答预算由序列长度表达；B group_by_task+execution+compare{against:D,rule}；C tasks:test+旧 delivery+compare{against:fresh_bare}(split)/{against:D}(full)——B2-2 单一 compare 形，无 control/mode 匹配；L evidence/author/curate），字段构造序固定并被测试钉住。三个超出映射表原文的设计决定（docstring 均已披露）：(1) 槽位补全复用 _validate_methods——缺/空槽按 METHOD_DEFAULTS 补齐，保证升级产物与"同文件以 v3 载入后冻结的身份"一致（装配器读 manifest 走的就是补全后路径，7 份配置实测 raw 与 to_dict 两路 sequence/phases 全等）；(2) v3 curation 槽可显式携带 merge_check（M3 起合法）——显式值保留不覆盖，唯 live_run+显式 exam 组合拒绝（v4 无考题可查、无像可映）；(3) 映射会"搬家"的槽（acceptance/segmentation/authoring/curation）拒绝无目的地的键——静默丢键即改身份；原样拷贝的 delivery 不查（同参逐字、无丢失可能）。健壮性照 upgrade_v2/from_dict 先例定粒度：非 dict → TypeError，缺 methods 槽（v1/v2 输入）与已含 sequence/phases（已是 v4）→ ConfigError，报错均指向 upgrade_v2_config 链或"无需升级"。测试 tests/unit/core/test_upgrade_v3_config.py 共 40 例：7 份配置（4 份 *_v3 + 3 份 *_v2_v3）逐份手写字面量钉全形状（exam 6 份 9 相位、live 1 份 6 相位；retrieval 的 top_k/max_categories/min_score、live 的 max_injection_tokens: null 与 improvement: strict_rate_gain、full 的 against: D vs split 的 fresh_bare、curate 96000/3、evidence 5/2 逐字段）、全局键零丢失逐一比对、manifest 路径（load_config→to_dict→upgrade）与 raw 路径全等、相位与字段序钉住、输入不被改动、v2 文件经 upgrade_v2_config 链式升级后 phases 与仓库自带的 v3 转换产物全等；合成用例覆盖 v2 拒载、exams:false×exam 拒载、exams:false 消融对与 true 转出全等、R=0 算 5 相位、空 methods 补默认、显式 merge_check 保留/live+exam 拒绝、无目的键拒绝、live_run 缺必填键拒绝、delivery 逐字拷贝。回退转红验证三处：破坏 merge_check 推导→live 形状测试红；破坏 R+1 算术→7 例红；偷删全局 rules 键→零丢失测试红；恢复后全绿。验收：2090→2130 passed + 3 deselected（+40），金矩阵 fixtures 与 tests/fixtures/golden_semantic_baseline 零改动，ruff E9/F 全过，test_layering 3 passed。遇到的问题：7 份配置无映射表未覆盖的字段（全部在表内）；唯一表外输入是 curation 槽的显式 merge_check（处置见上）。

工件：`src/expedistill/core/config.py`（_refuse_unmapped_keys + upgrade_v3_config + 模块 docstring 一句）、`tests/unit/core/test_upgrade_v3_config.py`（新建，40 例）、本条对应 commit

## 2026-10-16 16:05 — 修复方案与并行拆分（按模块边界定稿文档）：起点 c6a9fb8、差距 11 项、四波 14 代理

一段话摘要：按 docs/decisions/execute_learn_module_boundaries_20261016.md 出完整修复方案 docs/decisions/execute_learn_repair_plan_20261016.md（只写文档，未动 src/）。①起点核实：HEAD c6a9fb8、工作区干净、2130 passed + 3 deselected；已落地可沿用的是 M1（Skill.source_subtasks / Exam.skill_id / 对象 schema v2 / v1→v2 迁移）、M2（delivery 三个新驱动 + 注册表登记）、M3（decision.py 纯函数、reports.py 三读取器、evidence/author 族 Params、agentbrew 的 merge_check）、M4.1（semantics.py 仪器 + 基线束）、M4.2（upgrade_v3_config）；未落地即 M4.3 及之后全部。一句话概括起点：底层三样已就位，缺的是"验收"这个模块本身。②差距表 11 项（R1–R11）逐项带行号证据：判定散在五处（tb/plugin.py classify_result、batch_execute.py:354、exam_once.py:270/298、acceptance.py:290、live_acceptance.py 四处 discard_skill 1003/1084/1217/1276）；执行不自纯（base.py:53 RUN_STATUSES 三值词汇无"对不对留空"）；比较落在执行（stage6_test.py:195 _delta、330/357 control_source）；交付五驱动三签名（none/whole_bank 收 bank、per_skill 收单条 skill、group_by_task/retrieval 收 store/bank 返闭包）；报告三形状 + reports.py 17 处 la._* 私有访问；evidence/author 各 34/23 行只有 Params 无驱动；技能库产物不逐相位；pipeline.py:61 STAGE_NAMES 硬编码 + registry.py:45 两套键并存（13 项）；config.py:82 五槽位 + cli.py:173 until_stage。③与两份旧方案的六条冲突裁决（边界文档胜）：泄题审计归验收（旧方案归执行）、报告形状收成一份（旧方案三读取器）、决策规则合并成一张表（旧方案 exam/live 两套）、族名保留 curation、RunOutcome.status 拆成 correctness: Optional[bool] + 指标、出题归执行无分歧。④硬约束四组：分层（九层、composites 不 import methods、methods 互不 import、gen_exam/audit_leak 只挪调用点）、续跑（逐单元落盘/确定 ID 跳过/终态先写/manifest 比对/runlock、五对象仓 run 级共享目录名不变、跳过键即现行续跑键）、语义等价（E1–E6 六条同时成立）、仓库纪律（不碰 TB-dev 与 runs/、提交需同意推送需明确要求、门是纯报告、不加新 hash、AGENTS 长任务纪律、文档顺序）。⑤并行拆分：Wave 0 串行 1 代理（冻结四形状 + C4/C5 裁决 + 三个大文件机械拆分，判据 golden 逐字节无 diff——拆分只为制造互不相交的文件所有权）；Wave 1 并行 6 代理（A 验收/B 执行/C 决策/D 交付/E 找证据+写作/F 整理，各自独占文件，W1-A 为关键路径先冻结 report 字段）；Wave 2 并行 3 代理（两个相位驱动 + 配置与 CLI）；Wave 3 并行 3 代理（装配器/审计脚本/文档）；Wave 4 串行 1 代理（golden 重生成 + E1–E6 验收）。⑥存档分支 wip/m43-old-boundaries @ 9a8f058 的回收口径：按文件捡零件（相位目录布局、pNN 标签派生、出题调用点搬法、逐相位落盘写法），**不 merge 分支**——它的模块调用关系按被否决的边界写。⑦R4 不是独立工作项而是不变量（跑完 E 相位技能库逐字节不变），Wave 2 统一验证。⑧行为：本次只创建两份文档（边界定稿从 /tmp 备份恢复入库 + 修复方案），src/ 与 tests/ 零改动。

工件：`docs/decisions/execute_learn_repair_plan_20261016.md`（新建）、`docs/decisions/execute_learn_module_boundaries_20261016.md`（从存档备份恢复并入库）、本条对应 commit

## 2026-10-16 17:49 — R0 接口形状冻结与三个大文件机械拆分（Wave 0 两个提交）

一段话摘要：按修复方案 R0 完成两件事，各一个提交。提交一新建 core/contracts.py，冻结执行—学习闭环四个数据形状：运行记录（RunRecord，correctness 用 Optional[bool]，None 只表示 benchmark 无判分器）、验收报告行（AcceptanceReportRow，status 三值、cause 取值集合仍定义在 composites/decision.py）、执行单元行（ExecutionUnitRow，附 ExecutionUnitList 别名给 R5 的统一签名）、任务清单行（TaskListRow，动作取 keep/revise/delete/add）；只是接口视图，不新建持久化对象，unit_id 构造规则写在模块 docstring 里，另加 6 例字段集钉子测试（tests/unit/core/test_contracts.py）。提交二把 live_acceptance.py（1362 行）机械拆成 live_measurement.py（测量：路径、账本名、组推导、基线、跳过集、注入审计行、幸存组）、live_verdicts.py（判定：轮判定、no-op 守卫、组终态提交、discard_reason 的 K6 侧再导出）、live_attribution.py（归因：G10/G11）；exam_once.py 拆出 exam_answering.py（作答）与 exam_grading.py（判卷），exam_once.py 保留为薄接线（K3 入口拼作答+判卷，行为与顺序逐行未动）；acceptance.py 拆成 acceptance_leak.py（泄题判定）、acceptance_skill_status.py（技能状态判定，REWRITE_FAILURE_LEDGER 常量随读取方放此处）、acceptance_submit.py（提交助手：accept_skill 循环与 G6 改写落盘）。只搬代码与拆函数：leak_verdict_for、answer_exam 两个函数是从原函数体逐行抽出的步骤，其余函数体一字未改；调用方（reports.py、decision.py 注释、methods/acceptance/live_run.py、exam.py、delivery/group_by_task.py、7 个测试文件、scripts/smoke、golden 语义仪器注释）全部改到新路径，composites/__init__ 的导出同步。三个易错点逐一处理：崩溃注入测试原先 monkeypatch live_acceptance.append_jsonl 一个名字拦全部账本，拆分后按账本归属改成分别 patch（verdicts 行→live_verdicts、归因/修订账本→live_attribution、泄题账本→acceptance_leak、改写失败账本→acceptance_submit，测试驱动自己写的注入审计行走 live_verdicts 的名字以保持被计数）；exam 侧的 per-exam 锁、fork 重置钩子随作答部分走。偏离两处已报：提交顺序按任务指令（先 contracts 后拆分），与详细方案 R0 的表格相反；详细方案提交二还列了 C4/C5 裁决与 methods/acceptance 参数契约迁出，任务指令未含此项故未做，留给后续波次。验收：2130→2136 passed + 3 deselected（+6 全部来自 contracts 钉子测试），test_layering 3 passed，ruff E9/F 全过，全量测试内含的 E1–E6 语义等价检查全绿（拆分未改任何落盘字节）。AGENTS.md 目录树一节最小更新（K3/K4/K6 九个新文件名与职责一句话），README 未动（积木表按功能描述，无文件路径需要改），Wave 3 W3-C 的文档大改会覆盖本节。

工件：`src/expedistill/core/contracts.py`（新建）、`tests/unit/core/test_contracts.py`（新建）、`src/expedistill/composites/{live_measurement,live_verdicts,live_attribution,exam_answering,exam_grading,acceptance_leak,acceptance_skill_status,acceptance_submit}.py`（新建，机械拆分产物）、`src/expedistill/composites/exam_once.py`（改写为薄接线）、`src/expedistill/composites/{live_acceptance,acceptance}.py`（删除）、`src/expedistill/composites/{__init__,reports,decision}.py`、`src/expedistill/methods/acceptance/{live_run,exam}.py`、`src/expedistill/methods/delivery/group_by_task.py`、`tests/composites/`（7 个测试文件改到新路径）、`tests/unit/composites/test_decision.py`、`tests/unit/methods/`（2 个测试文件）、`scripts/smoke/smoke_leak_audit_k4.py`、`tests/golden/semantics.py`（注释）、`AGENTS.md`（目录树）、本条对应两个 commit

## 2026-10-16 18:25 — Wave 1 / W1-E：R6 找证据与写作驱动成立（G7、G8），G10 新入口只报事实

一段话摘要：按修复方案 R6 在 wave1/e 分支落地找证据与写作两个真实驱动，中间态按任务指令"旧调用路径继续存在、新驱动另立入口"执行：composites/live_attribution.py 的 attribute_group/apply_revisions（含 G10 动作计划账本行）逐字节未动——它的行形状被 stage 的 _revisions_complete、scripts/audit/audit_live_run.py 和逐字节金矩阵（tests/fixtures/golden，全量测试的组成部分）三方钉死，改形状必然打破禁改文件与 2136 基线，故只在模块 docstring 披露中间态与 Wave 2 迁移路径；新增四件：methods/evidence/package.py 定义证据包（target、facts、segments、old_skill、source_experiences，字段集按边界定稿 §2.5，core/contracts.py 本波冻结不加）与 ensure_fact_only 结构守卫（任何 action 键、任何动作词 fact kind 一律响亮拒绝）；methods/evidence/live_group.py 的 collect_group_evidence 是 G10 的新入口——底层 LIVE_RUN_ATTRIBUTION prompt 输出契约本波冻结（prompts/ 非本任务文件），模型仍会吐 keep/revise/delete，驱动在证据边界丢弃动作词汇，产出逐技能事实包（基线/实战逐次状态行、组诊断、逐技能 problem、失败视图、冻结注入块 sha、轮内轨迹段）；methods/evidence/add_path.py 的 collect_add_evidence 按新增任务行收 D 相位强模型切段（具名 segment_select 规则），打包 source_experiences；methods/author/write.py 是写作驱动——revise_group 接 TaskListRow(action=revise) + 证据包，逐技能 keep/revise/delete 决策在授权范围内由写作自定（默认策略：有 problem 事实即改、无则留，删除经可注入 decide 策略触发，判 delete 携带 live_revision_deleted——改写流程内删除归写作，R6 第 3 条），写作按证据包形状分发（答卷事实→G6 REWRITE、失败视图→G11 LIVE_SKILL_REVISION，签名无报告种类参数），write_new 按 add 包逐段经 G2 出新技能；改写预算耗尽按 C8 裁决与边界定稿 §4.3 第 2 条处置为"无变化"——不弃用、版本不动、下次学习再试（与旧 _revise_one 弃用 model_output_error 的差异已在新驱动文档披露，旧路径行为保留至 Wave 2 接线）；归因预算耗尽的整组弃用不在本驱动（决策模块职责）。写作驱动不 import evidence 族（methods 互不 import），按包的鸭子形状消费。测试 34 例新增于 tests/unit/test_w1e_*.py（按协调者更正以 test_ 前缀命名）：G10 无动作断言（模型脚本三动作全吐、包内结构/词汇/内容三层全查）、事实与旧技能携带、段钉轨迹、prompt 组成、ModelOutputError 传播不弃用、任务行校验、预算耗尽=无变化不弃用、marker 失败占预算、逐字节同输出=无变化、infra 失败非预算项、删除携带 live_revision_deleted、按包分发两条路、write_new 逐段出技能及拒绝分支。验收：2136→2170 passed + 3 deselected（+34），test_layering 3 passed，ruff 全过，逐字节金矩阵 8 组合全绿（E1–E6 对基线全等，E6 attributions 零差异——新驱动未接线、旧路径未动，"允许形状差异"条款未动用）。偏离披露：详细方案 R6 的"exams 开关删除"未做——stage3_generation.py:192 与 core/config.py 的 v3 校验仍读该字段（禁改范围），删除会断现行路径，保留至 Wave 2 调用点迁移（模块 docstring 已注明）。

工件：`src/expedistill/methods/evidence/package.py`（新建，证据包形状 + 事实守卫）、`src/expedistill/methods/evidence/live_group.py`（新建，G10 事实入口）、`src/expedistill/methods/evidence/add_path.py`（新建，新增路径证据）、`src/expedistill/methods/author/write.py`（新建，写作驱动）、`src/expedistill/methods/evidence/__init__.py`、`src/expedistill/methods/author/__init__.py`（docstring 更新，Params 类逐字保留）、`src/expedistill/composites/live_attribution.py`（仅 docstring 披露中间态）、`tests/unit/test_w1e_{evidence_package,evidence_live_group,evidence_add_path,author}.py`（新建，34 例）、本条对应 commit（wave1/e）

## 2026-10-16 19:12 — Wave 1 评审修复：zero_execution 一词一义（B1）与 P1/P2/P4 偏移、D1–D5 细节一次收口

一段话摘要：按三路 Wave 1 评审（docs/reviews/wave1_review_20261016_{A,B,C}.md）与协调者四条裁决修复六提交合并结果，一次提交。B1（阻断）：zero_execution 固定为唯一含义"该行没有任何完成的运行可供报告"的可见性事实（未作答的裸答、本相位未作答的存量版本、已派发未判定的 live 轮）；决策侧删除 CAUSE_ZERO_EXECUTION→WEAK_ALREADY_KNOWS 映射，对照 valid=0 且 cause=zero_execution 时 compare_conclusion/decide_task_list 抛 CodeBugError（对照在途就调决策是调用方错误），目标侧 zero_execution 行不产生任务清单行，table_action 收到该 cause 一律响亮拒绝；"弱模型已会"短路只经 status=not_run_control_perfect 表达。验收侧：判卷预算耗尽的未判答卷行（带技能与裸答两处）cause 从 zero_execution 改为 exam_generation_failed——exam_grading 新增公开谓词 judge_budget_exhausted（judge_with_persistent_budget 的守卫同源化），reports 据此分流，预算未耗尽的崩溃中间态保持 zero_execution；决策表对 exam_generation_failed 自动给出 model_output_error，与 test_w1a 钉子表的既有预期一致。危害路径（带技能答卷判对 + 裸答在途 → 调决策）由三个新测试钉死（表级 compare_conclusion、入口级、盘上端到端）。P1：skills_injected=[]（检索未过阈）是 R5 冻结的合法值，decide_task_list 按 reuse_key 既有语义把它当"无注入"并入对照、不再抛"injects nothing"。P2：units.unit_id 删除本地 json.dumps 规范化、改 import verdicts.canonical_skills（sha8 从此按冻结规则计算，Wave 2 用 make_unit_id 重建完整键时直接咬合），"single place"措辞指向 verdicts。P4：_CAUSE_DELETE_REASONS 不再把 live 风格理由安到任意相位——injection_overflow / invalid_redispatch_exhausted / all_holes 的删除理由改为相位中性的 cause 自名（新增 ALL_HOLES / INJECTION_OVERFLOW / INVALID_REDISPATCH_EXHAUSTED 三个 discard 常量，live_* 名称保留给 K6 终态词汇），选了比相位分流更简单的实现；钉子表同步改写并补 redispatch 场景与 overflow/all_holes 的双路（终态路径 + 相位表路径）钉子。细节：D1 词表单一化——因 verdicts→decision 的既有依赖方向（verdicts 的 K6 终态映射表引用 decision 的字符串）与 core/contracts.py"cause 词表定义点在 decision"的冻结声明，落点取 decision、verdicts 改为再导出并加身份钉子测试，与裁决建议的"放 verdicts"相反、理由如上；D2 _parse_unit_id 要求 task 槽自带 benchmark 冒号前缀，裸 "pNN" 标签的静默错切（benchmark 槽被吃掉、prefix[3] 变裸任务名）改为响亮 CodeBugError；D3 phase_bank 标签校验收紧为"恰一个冒号且位于 pNN 之后"（p1:a 与 p1_a 不可能再撞目录，p00_freeze 这类无冒号标签从合法变拒绝）；D5 write.py / add_path.py / live_group.py 的 action 字面量校验改引 decision.REVISE / decision.ADD 常量；B 路细节：Action docstring 与 table_action 实际行为对齐（非 DELETE 也携带结论理由），per_skill 的旧形态兼容层（全仓库无生产调用方）删除、resolve 只留冻结签名，verdict_from_attempt 补"R2b 删除"退役标注（与旁边 verdict_from_trajectory 一致）。D4（同技能多版本任务行的合并规则）按裁决不修，留 Wave 2 任务清单执行器。验收：2311→2323 passed + 3 deselected（+12 全部为新钉子或按新语义改写：判卷预算/在途判卷/危害路径×3/redispatch 场景/词表身份钉/标签撞名钉/裸 pNN 拒绝/[] 对照语义/zero_execution 无动作行），test_layering 3 passed，ruff 全过。

工件：`src/expedistill/composites/decision.py`（B1 决策侧、P1、P4、D2、Action docstring）、`src/expedistill/composites/verdicts.py`（D1 再导出、verdict_from_attempt 标注）、`src/expedistill/composites/exam_grading.py`（judge_budget_exhausted）、`src/expedistill/composites/reports.py`（判卷预算耗尽 cause 分流）、`src/expedistill/composites/phase_bank.py`（D3）、`src/expedistill/methods/delivery/units.py`（P2）、`src/expedistill/methods/delivery/per_skill.py`（旧形态删除）、`src/expedistill/methods/author/write.py`、`src/expedistill/methods/evidence/{add_path,live_group}.py`（D5）、`tests/unit/test_w1a_discard_scenarios.py`、`tests/unit/test_w1a_verdicts.py`、`tests/unit/test_w1c_decision_table.py`、`tests/unit/test_w1c_decision_entry.py`、`tests/unit/test_w1f_phase_bank.py`、`tests/unit/methods/delivery/test_drivers.py`、本条对应 commit

## 2026-10-16 20:03 — Wave 1B / R2b：三值运行状态词汇迁移到 correctness（Trajectory 升 schema v2）

一段话摘要：按修复方案 R2b 把运行状态词汇（success / failure / infra_error）整体迁移为 correctness: Optional[bool] 加运行指标，基础设施失败改走 InfrastructureError 异常通道，一次提交。数据对象：Trajectory 独立升 schema v2，status 与派生 success 两字段删除、correctness（true=判分器判对 / false=判错 / null=没拿到判分结果）取而代之；v1 的 success / failure 行读取时无损迁移，v1 的 infra_error 行（按 K1 契约本就不该落盘）明确拒载并指路维护分支规则；RUN_STATUSES / TRAJECTORY_STATUSES 两个词表常量删除。插件契约（benchmarks/base.py）：RunOutcome 改为 {steps, correctness, meta}，基础设施失败由插件抛 InfrastructureError、K1 捕获重试；batch_execute.py 的重试触发从"outcome.status == infra_error"改为捕获该异常，重试游标在每次基础设施失败时推进（与改前每次 infra outcome 推进逐位一致），TB 插件的 deadline / 死 runner / 陈旧 run id / 传输失败全部改为抛出，deadline_kill 审计信息随异常消息进失败账本。W1-B 留下的两个过渡副作用按任务指令消除：TB 无 reward 的 trial（AgentTimeout 等模型异常在内）从"infra_error 机械重跑"改为落盘 correctness=None、事实（exception_type / verifier_ran）随 meta 交验收判定、不重试；伪零分（有 reward 但 pytest 未收集，两个方向对称）同样记 correctness=None——这一并撤销了评审 A P3 记录的"正向无凭据 reward 计成功"的评分膨胀中间态。react_loop 的 ModelOutputError→failure 重分类同理迁移：作答端模型无输出时记 correctness=None、不再替执行下"模型失败"的结论。验收判定入口：verdict_from_trajectory / verdict_from_attempt 两个过渡桥接删除，reports 的 baseline / live 读取器改经 _run_record_from_trajectory（meta 即运行指标）+ verdict_from_run（benchmark 缺省具名规则），exam 读取器改经 _run_record_from_attempt（judge_verdict 走 aux）；scorer 规则的 None 分支从 CodeBugError 改判 invalid（无测量、逐分母剔除），exam_judge 规则的 basis 顺带携带判卷理由（test_reports 既有钉子要求）。D3（blocks/metrics.py）的口径不变：valid=拿到判分结果的执行，correctness=None 与应有而缺失的单元同计 infra，attempts==valid+infra 不变量保持，阶段 1/6 的欠账机器无需改形状。跟随组件：view.py 的轨迹视图头部与 G10 证据行的"final status"显示词逐字节保留（{True:success, False:failure, None:unknown} 映射，喂进 G1 切段 prompt 与 G10 prompt，被语义仪器 E4 逐调用钉死，改词即新实验）、live_attribution 旧 G10 路径的基线状态行同源化、live_group 证据包 segments 改带 correctness、searchqa 辅助打分器按 correctness 严格化、scripts 四处（seed_tb_stage1 / audit_live_run 自测 / tb_archive_validate / tb21_f3_baseline）同步——tb_archive_validate 依赖的 classify_result 历史镜片按验收条款原样保留（三值词汇仅存于历史归档读取代码）。README（E1/D3/K1/重试游标/对象 schema 节）与 AGENTS.md（第七节插件契约）同步。轨迹产物字节差异逐处披露：金矩阵 8 组合重生成，全部差异恰好 78 条 Trajectory 记录（exam 组合各 4–6 条、live 组合各 11–18 条），每条都是同一形状的三处变化——status 与 success 两键删除、correctness 按同值映射加入（success→true、failure→false）、schema_version 1→2——另有 provenance.json 的 base_commit / generated_on 两行；llm 请求转储、全部阶段 summary、账本、技能库逐字节未动，E1–E6 语义等价对未触碰的归档基线全绿（改动前后行为语义等价由这套仪器证明），生成时 8×3 崩溃续跑变体全等。验收：2323→2325 passed + 3 deselected，test_layering 3 passed，ruff 全过，audit_live_run --self-test 全过。

工件：`src/expedistill/core/models.py`（Trajectory v2 + 迁移）、`src/expedistill/benchmarks/{base,react_loop}.py`、`src/expedistill/benchmarks/tb/plugin.py`、`src/expedistill/benchmarks/{searchqa,alfworld}/plugin.py`、`src/expedistill/blocks/{execute,metrics,view,rules}.py`、`src/expedistill/composites/{batch_execute,verdicts,reports,segment_checked,live_attribution}.py`、`src/expedistill/methods/evidence/live_group.py`、`src/expedistill/stages/{stage1_baseline,assemble_reuse}.py`、`scripts/run/seed_tb_stage1.py`、`scripts/audit/audit_live_run.py`、`scripts/analysis/{tb_archive_validate,tb21_f3_baseline}.py`、`tests/`（约 25 个测试文件迁移 + 金矩阵 9 个文件重生成）、`README.md`、`AGENTS.md`、本条对应 commit

## 2026-10-16 20:31 — 更正披露：R2b 条目漏报 usage 账本标签改词（评审 A 细节 D3）

一段话摘要：更正上一条（2026-10-16 20:03，Wave 1B / R2b）的披露不完整：该提交同时改了 TB 插件 usage 账本行的标签词——deadline / 死 runner 路径从 "infra_error" 改为 "infra"，无 reward / 伪零分的 trial 从 "ok"（或无 reward 分支的 "infra_error"）改为新标签 "unscored"。消费方 infra/usage.py 只区分 status == "ok" 与其余（记入 n_error），token 总额不受影响；但同一 run 跨 R2b 续跑时账本里新旧标签混用，且伪零分 trial 从 n_ok 移入 n_error，ok/error 划分口径在 R2b 前后有别。原条目未提及这两处标签改词，特此补记。

工件：本条是对 log.md 上一条的更正，对应代码 `src/expedistill/benchmarks/tb/plugin.py`（commit `16c3791`）

## 2026-10-16 20:34 — Wave 1B 评审修复（R2b-Fix）：全未判分组进 not_testable、基线 valid==0 不再 ConfigError、verifier 日志抓取失败走重试

一段话摘要：按协调者对 Wave 1B 三路评审的五项裁决一次收口。裁决 1（评审 A 阻断 B1 + 细节 D1）：「运行完整但全部未判分」正式成为常规可达状态——live_verdicts 的判定核把零有效测量（全单元皆已接受空洞、全部落盘运行皆未判分、或基线零有效测量）一律短路为 live_undetermined 终态，永不流进 strict_rate_gain 的比较分支；统一 cause 选定 all_holes（理由：它是 live_undetermined 在 K6 终态映射表里既有 的 not_testable cause，决策表按「测不了」行删除，与基线读取器天然一致；invalid_redispatch_exhausted 按裁决排除——其字面义是「重派耗尽、单元缺失」，Wave 2 重派机制落地前不该当兜底，评审 A 的方向即指向 zero-measurement 单独成词而 zero_execution 按 B1 裁决是可见性事实、不产生任务清单行，语义不符）。基线读取器随之区分三态：有有效运行→normal、全部存回但未判分→all_holes、有单元从未完成→invalid_redispatch_exhausted；live 读取器经 live_undetermined 终态同样映射 all_holes，两个读取器对同一状态给出同一 cause、同一删除处置。三处自称「不可达」的文本（rules.py 防御分支、live_verdicts 注释、test_live_acceptance 钉子 docstring）全部改写为诚实的「判定层短路、规则保持完全」表述。裁决 2（评审 A P1）：live_measurement.load_baseline 的基线 valid==0 从 ConfigError（旧处方「重建任务集」）改为正常返回计数，live_run 驱动的入口守卫对这种组零执行落 live_undetermined 终态（judge_round 落行前核验该事实，防止可测基线被谎报）；live_zero_execution_short_circuit 同步加 valid>0 守卫（否则 0==0 会被误读成「基线全对」）。裁决 3（P2 + B-2 + B-3 + B 细节）：contracts.py 的 RunRecord.correctness 契约注释从「None 仅指无判分器」改宽为「没拿到判分结果」（含伪零分、trial 中途死掉、模型无输出），benchmarks/base.py 的抽象 run docstring 补 InfrastructureError 通道、「stays status-based」改判分器口径，README 阶段 6 漏改的「EM / 状态口径」补齐，verdicts.py 五点清单标注 classify_result 已退役为归档校验器历史镜片，reliability.py 的 InfrastructureError docstring 补 R2b 角色（插件→K1 的失败通道、单次派发内有限重试、跨续跑无限再试），评审 B 的 B-1（smoke 测试残留 outcome.status 断言，一跑即 AttributeError）一并修掉，测试注释三处（metrics / batch_execute / execute 模块 docstring）与 strong2_weak2 测试名清掉旧词汇。裁决 4（评审 C 偏移 1）：TB _finish 的 verifier 日志抓取失败不再吞掉——仅当 trial 带 reward（判定可能因此被作废）时抛 InfrastructureError 走 K1 重试，无 reward 的 trial（本就没有判定可作废）维持 best-effort 抓取、correctness=None 落盘不重试，避免把 AgentTimeout 类模型事实误变成基础设施重试。裁决 5（D2 + D3）：伪零分正向方向（reward>0 且 pytest 未收集 → correctness=None）补运行级钉子两处（test_tb_plugin、test_w1b_execution_facts）；usage 标签改词按上一条更正披露。钉子测试新增 10 个：判定层全 None 组→live_undetermined→finalize 弃组、入口守卫基线零测量→零执行终态（全空洞与全未判分两路）、驱动级全未判分轮不花归因预算、基线读取器三态、读取器→决策表端到端、verifier 抓取失败→重试+账本记 infra、正向伪零分×2。README 的 live_run 节（无效基线、失败处置表、live_undetermined 词义）同步。验收：2325→2335 passed + 3 deselected，test_layering 3 passed，ruff 全过。

工件：`src/expedistill/blocks/rules.py`、`src/expedistill/composites/{live_verdicts,live_measurement,decision,reports,verdicts}.py`、`src/expedistill/methods/acceptance/live_run.py`、`src/expedistill/core/contracts.py`、`src/expedistill/benchmarks/{base,tb/plugin}.py`、`src/expedistill/infra/reliability.py`、`README.md`、`tests/composites/{test_live_acceptance,test_reports,test_batch_execute}.py`、`tests/unit/test_w1a_discard_scenarios.py`、`tests/unit/test_w1b_execution_facts.py`、`tests/unit/blocks/{test_metrics,test_rules,test_execute}.py`、`tests/unit/benchmarks/test_tb_plugin.py`、`tests/unit/composites/test_decision.py`、`tests/unit/methods/acceptance/test_live_run.py`、本条 log.md 上一条更正

## 2026-10-16 21:14 — Wave 2 / W2-C（R9 + R8 注册表部分）：配置升 schema v4、相位循环取代 pipeline、注册表五族换键

一段话摘要：按修复方案 R9 与 R8 的注册表部分，分两个提交完成配置与命令行的相位化。提交一（bca688d）冻结 v4 解析产物并广播给 W2-A / W2-B：五个相位角色词（baseline/exam/live/test/learn，与 decision 层逐字相等，测试钉死）、pNN 标签推导（freeze 恒 p00，序列第 i 项 0 基为 p{i+1:02d}:<局部名>，即语义仪器已冻结的 phases/p{index+1:02d}_<name> 编号）、CompareSpec / ExecutePhaseSpec / LearnPhaseSpec 三个 dataclass（选 dataclass 而非 TypedDict 的理由：加载期检查清单住在 __post_init__，manifest 冻结走 dataclasses.fields 与其他字段同路）、相位驱动交接契约 PhaseInstance / PhaseResult、parse_phases 的加载期检查清单（序列非空、局部名禁冒号、字段集与 kind 匹配、角色与字段一致含 D 相位双臂固定与单臂深度上限、compare.against 必须更早或 fresh_bare、merge_check:exam 需序列含 exam 相位）；upgrade_v3_config 同步给每个相位发 role，并发出 v4 新全局 invalid_redispatch_limit（默认 1，转换后的身份不依赖默认值）。提交二完成翻转：CONFIG_SCHEMA_VERSION=4，ExperimentConfig 以 sequence/phases/invalid_redispatch_limit 取代 methods 槽位（METHOD_SLOTS / METHOD_DEFAULTS / _validate_methods 删除，v3 槽位知识只保留为 upgrade_v3_config 的私有 _V3_METHOD_DEFAULTS），v3 及以下在 load_config、from_dict、stage0 manifest 闸三处统一拒载并指向 upgrade_v3_config 与 Wave 3 转换器；注册表按 R8 换键到五族 delivery / execution / evidence / author / curation（13 项旧键里 segmentation.llm、authoring.distill_diagnose、acceptance.exam、acceptance.live_run 四个退役，新增 methods/execution 包的 real / exam 两个 driver-less 条目，API 改为 resolve(family, raw)——相位自己的嵌套映射）；cli.py 吸收已删除的 stages/pipeline.py 成为序列循环（stage0 冻结后按序列逐相位惰性 import phase_execute / phase_learn 并调 run(ctx, PhaseInstance) -> PhaseResult，pipeline_log 行 key 保持 "stage"、值为 p00:freeze 与 pNN:<局部名>，--until-phase N 取代 --until-stage 且越界校验挪到配置加载之后、以冻结序列长度为界，summary 完备性清单改从 manifest 冻结序列推导）；stage0 的方法解析改逐相位族解析并校验 acceptance / compare.rule 的具名规则。7 份 v3 配置经 upgrade_v3_config 转换后加载期检查全过、canonical to_dict 往返相等（纯函数断言，转换脚本归 W3-C）。与详细计划 R9 的偏离：相位角色是新配置字段（R8 要求显式声明，upgrade_v3_config 的输出相应加了 role）；methods/curation/phase_bank.py 的 produce / produce_as_is 签名改为接收相位的 curate 映射（methods/ 名义禁改，但该文件无他人认领且 v4 下无单一 curation 槽、不改必红，已在广播与汇报中披露）。测试：新增 test_w2c_config_v4（29）与 test_w2c_run_loop（25），test_config / test_params / test_family_entries / test_stage0 / test_cli / test_w1f_phase_bank / test_curation_none / test_upgrade_v3_config 全部迁到 v4 形状，删除 test_pipeline.py（随 pipeline.py 走）。已知红集（163 个，全部为 v3 形状的死代码，失败模式一律 ExperimentConfig 不再接受 methods=）：test_stage1/2/2_parallel/3/6（77，W2-A 删）、test_stage5（5，W2-B 删）、test_exam / test_live_run（43，随 K4/K6 壳退役）、test_assemble_reuse（36，W3-A 迁移装配器）、test_curation_stage4_interleave / test_curation_parallel 各 1（W2-B）；tests/golden 因 harness 钉死旧 pipeline 且构造 v3 配置、在 v4 拒载后物理不可跑，经 tests/conftest.py 自愈式跳过（harness 恢复可导入即自动恢复收集，重生成归 Wave 4）。绿集 2110 passed + 3 deselected，test_layering 3 passed，ruff 全过。

工件：`src/expedistill/core/config.py`（v4 解析产物 + 加载器翻转）、`src/expedistill/methods/{registry,params,__init__}.py`、`src/expedistill/methods/execution/`（新）、`src/expedistill/methods/curation/phase_bank.py`、`src/expedistill/stages/{pipeline.py（删除）,__init__,stage0_freeze}.py`、`src/expedistill/cli.py`、`tests/conftest.py`（新）、`tests/unit/test_w2c_{config_v4,run_loop}.py`（新）、`tests/unit/{test_cli,core/test_config,core/test_upgrade_v3_config,methods/test_params,methods/test_family_entries,methods/test_curation_none,stages/test_stage0,test_w1f_phase_bank}.py`、`tests/unit/stages/test_pipeline.py`（删除）、commit `bca688d`（提交一）与本提交（提交二）
## 2026-10-16 21:44 — Wave 2 / W2-A：执行相位驱动 phase_execute 取代 stage1/2/3/6（R9 执行侧 + R8 相位语义）

一段话摘要：按修复方案 R9 执行侧与接线清单，新建 stages/phase_execute.py（run(ctx, PhaseInstance) -> PhaseResult，W2-C 冻结契约的签名），一个驱动跑 D（baseline，双臂裸跑、轨迹写 run 级仓——live 基线加载、报告读取器、C 相位的复用对照都读那里）、A（exam，考题按 R8 裁决移到执行时点现场生成：先裸答、判对即按 decision.control_perfect_skips_run 跳过带技能作答并标 not_run_control_perfect，否则先 D8 泄题审计（C1：在带技能调用之前）再带技能作答判卷，改写循环不在此处——revise 归下一个学习相位）、B（live，按协调者 2026-10-16 裁决照常调用 live_verdicts.judge_round / finalize_group，第 k 个 live 相位测第 k-1 轮、轮次钉死 {0,1}，轮仓、注入审计行冻结、欠账记录全部沿用旧驱动的磁盘位置，G10/G11 归学习相位）、C（test，每次全量重跑，不调跳过与重派，对照取 run 级 D 弱臂（full）或相位内 fresh bare 臂（split），delta 用 decision.final_test_delta，不写验收报告——边界 §3 第 9 条）。三项冻结配套落地：规则 1 查重（units.jsonl / acceptance_report.jsonl 按相位落盘，decision.reusable_units 对更早的执行相位按 (任务, 角色, 注入块字节) 连接、最新优先，命中即沿用上一份验收行，zero_execution 可见性行绝不复用；live 相位上这就是 no-op 守卫的相位形态——改写没产出新版本则下一轮直接沿用旧判定）；无效重派回路（redispatch.json 按单元持久计数，预算 invalid_redispatch_limit（缺省 1）内把"派发了但没存下"的单元重新提交 K1，耗尽后 not_testable/invalid_redispatch_exhausted，已耗尽的单元不再进 K1；范围披露——"已存盘但未判分"是既成 all_holes 状态、本轮不重派：新采样会超出冻结的 n_attempts 深度，现有三个冻结读取器都表达不了，留给 Wave 3 读取器修订）；unit_id 重建（交付侧 sha8 已按 verdicts.canonical_skills 计算，驱动用 verdicts.make_unit_id 重建完整冻结键，P2 关闭）。交付接线（接线清单第 6 条）：五个交付驱动删掉 _legacy_resolve 与 units.dispatch 旧形态分支，只剩冻结签名 resolve(bank, tasks, params)，live_run.py 的调用点随改；交付的 bank 参数经 phase_bank.load_phase_bank 读上一个学习相位的技能库产物（以工件存在为信号，不依赖额外记录），没有学习相位时用 run 级冻结库。TB 具名验收规则（接线清单第 3 条）：verdicts.py 新增 tb_scorer 具名规则（消费 correctness + metrics 里的 verifier_ran / exception_type 事实，例外死亡 / 伪零分 / 无凭据 / 无判分四路均判 invalid 且 basis 写明依据），BENCHMARK_VERDICT_RULES 的 tb 默认绑定从 scorer 改为 tb_scorer，test_w1a_verdicts 的绑定断言一行随之同步。R4 不变量：phase_execute 无 discard_skill / accept_skill 直接调用（调用面 + 字节级测试钉住；例外是 live 终态提交——judge_round/finalize_group 账本协议自身的 commit，协调者裁决）。已知的适配器换点两处已在代码注明：方法解析走现有槽位 API 的单字段 shim（W2-C 第二段的五族 resolve(family, raw[, config]) 落地后一行换回）、execution 参数本地解析（RealExecutionParams / ExamExecutionParams 落地后同）。删除 stage1_baseline / stage2_segmentation / stage3_generation / stage6_test 及其测试（比较逻辑已在 decision.py；stage2/3 的内容归 W2-B 的 phase_learn）。跨域红（合并后恢复路径见汇报）：pipeline.py 仍 import 已删阶段，连带 cli 与 4 个测试文件收集中断——W2-C 第二段删 pipeline 换 v4 循环即恢复；test_assemble_reuse 两个用例经 cli 同因。验收：worktree 内 2142 passed + 2 failed（皆为上述跨域红），4 个文件收集中断（同因），test_layering 过，ruff 全过；新增 tests/unit/test_w2a_phase_execute.py 19 例（D 双臂与报告、R4 字节不变、重派预算与耗尽、unscored→all_holes、live 对照全对短路 / 测量与跨相位复用 / 注入超限、exam 三路、C 三态、unit_id 重建、参数与规则响亮失败、tb_scorer 事实消费）。

工件：`src/expedistill/stages/phase_execute.py`（新建）、`src/expedistill/stages/__init__.py`、`src/expedistill/composites/verdicts.py`（tb_scorer 与 tb 绑定）、`src/expedistill/methods/acceptance/live_run.py`（调用点）、`src/expedistill/methods/delivery/{units,none,whole_bank,group_by_task,retrieval}.py`（旧形态删除）、`tests/unit/test_w2a_phase_execute.py`（新建 19 例）、`tests/unit/test_w1a_verdicts.py`（绑定断言同步）、`tests/unit/test_w1d_delivery_signature.py`、`tests/unit/methods/delivery/test_drivers.py`（冻结形态化）、删除 `src/expedistill/stages/{stage1_baseline,stage2_segmentation,stage3_generation,stage6_test}.py` 与 `tests/unit/stages/test_stage{1,2,6}*.py`、`test_stage3.py`、本条对应 commit（分支 wave2/a）
## 2026-10-16 21:40 — Wave 2 / W2-B：学习相位驱动 phase_learn 就位，取代阶段 4 与阶段 5

一段话摘要：按修复方案 R9 第 5 条落地学习侧全部接线，一次提交（分支 wave2/b）。学习相位（序列里的 L：拿到刚才那次测量的验收报告，算出下一个技能库）现在由一个驱动跑完四步：决策（decision.decide_task_list，报告从磁盘现读：最近的执行相位是本轮报告，它的 compare 字段指向对照报告，第一个 L 走摸底路径——本轮报告为空、对照取 D 相位报告；技能库传上一个 L 落盘的相位产物，缺省空库）、按行执行任务清单（keep=把待定技能收进库；delete=按决策给的原因弃用；revise=先收证据再改写，版本加一；add=从强模型切段写新技能，状态待定）、落盘三本相位账本（task_list.jsonl 有效任务清单、revisions.jsonl 改写前的版本号、outcomes.jsonl 每技能回执——这本是旧 attributions.jsonl 的接替者，Wave 3 审计脚本与 Wave 4 金基线按它对齐）、按序列位置分支收尾（中间 L 原样入库，末位 L 跑配置的整理方法；调用点按冻结契约把 curate 原始映射传给 produce / produce_as_is，W2-C 第二段提交改签名后即咬合）。多版本行合并规则（评审 A 细节 D4，方案留给本条实现）：同一技能因共享答卷仓出现多行时，最高版本的那行算数，低版本行作为被取代者写进清单账本——存量版本未被本轮作答的行是可见性行，决策入口按 B1 裁决本来就不出动作。改写没出新版=没变化（版本不动、不弃用、下次学习再试，经 decision.revise_outcome 记账）；崩溃续跑沿用 K6 的版本号重放守卫：改写前先写版本号进账本，续跑看到版本已加一就只补回执、不再问模型；add 靠"技能已存在就跳过"幂等。基础设施失败先记失败账本、写完摘要再抛错，且不产出技能库（半成品相位不发布库）；模型输出错误只记账不致命，下次学习再试。两个已知协调点：(1) 考试改写的证据收集器（答卷卷面：考题、弱模型答案、判卷理由）Wave 1 没有产出，本条先在 learn_tasks 里就地组装（P7b 来源选取与泄题作废描述逐字沿用 acceptance_submit），证据族开放后应迁回 methods/evidence；(2) 12 个旧 exam_*/live_* 决策规则的调用方（acceptance_submit、live_verdicts、live_attribution、两个验收驱动）都是别人的冻结文件，规则留在 decision.py 等协调者统一退役。域内验收：新增 20 例（tests/unit/test_w2b_phase_learn.py，覆盖合并规则、摸底加技能、keep/revise/delete 落地、预算耗尽=无变化、崩溃重放、组改写续跑、末位整理分支、响亮拒绝分支）全绿；全套 2189 passed + 3 deselected，跨域红 9 处全部因 pipeline.py（W2-C 的删除域仍 import 已退役的阶段 4/5 壳）与 test_exam.py（直接 import 阶段 4 壳）而起，W2-C 第二段删除 pipeline 并改配置后即恢复；test_layering 3 passed，ruff 全过。stages/__init__.py 的最小改动：移除已删除的两个壳与 pipeline 的 import（pipeline 本体未动，直接 import 它的测试照旧红在它自己身上）。

工件：`src/expedistill/stages/phase_learn.py`（新建，驱动：序列与报告接线、决策调用、整理分支、摘要）、`src/expedistill/stages/learn_tasks.py`（新建，任务清单执行器：D4 合并、四类动作落地、考试答卷证据组装、相位账本与续跑重放）、`tests/unit/test_w2b_phase_learn.py`（新建，20 例）、`src/expedistill/stages/stage4_acceptance.py`（删除）、`src/expedistill/stages/stage5_curation.py`（删除）、`tests/unit/stages/test_stage5.py`（删除，阶段 5 壳的测试）、`src/expedistill/stages/__init__.py`（移除已删壳的 import）、本条对应 commit（wave2/b）

## 2026-10-16 22:10 — Wave 2 收口：三支合并后的集成咬合修复与 K4/K6 旧壳清理

一段话摘要：作为 Wave 2 的集成收口代理，把 wave2/a、wave2/b、wave2/c 三支合并后的 90 个失败收干净，并执行已定裁决的清理清单。集成咬合四项：(1) 真实行为冲突——W2-A/W2-B 给 stages/__init__.py 加了 phase_execute / phase_learn 的包级 eager import，破坏了 W2-C 循环测试赖以注入假驱动的 sys.modules 缝隙（cli 的 `_phase_driver` 用 `from ... import` 时一旦真模块被其他测试先导入、包属性绑定后假驱动被绕过），按 W2-C 广播的冻结契约（驱动按调用惰性 import）改实现不改契约：删掉 eager import、`_phase_driver` 改 importlib.import_module（显式尊重 sys.modules）；(2) W2-A 测试的 make_config 仍按 v3 形状构造（无 sequence/phases），补上合法 v4 方法；(3) W2-B 测试的 produce_as_is 包装器按其冻结分支的旧签名 (ctx, label) 委托，改随合并后的 (ctx, curate, phase_label) 传映射；(4) W2-A 在 phase_execute 留的两处过渡适配器按 W2-C 第二段已落位的五族注册表换掉：_resolve_delivery 直连 registry.resolve("delivery", spec.delivery)（_PhaseMethods 删），_execution_params 直连 resolve_params("execution", ...)（RealExecutionParams / ExamExecutionParams，_RealParams 删；methods 层的 core.ConfigError 在 stage 层转译为 infra 的，保持驱动对测试与失败处置的响亮契约；arms 角色一致性在驱动内保留为直构 spec 的兜底检查）。清理裁决逐项执行，两处与裁决原文有出入、按"有调用方先核实"的指引自主裁决并留痕：decision.py 的 12 个旧规则只删了 8 个（exam_bare_answer / exam_stored_pass / exam_leak / exam_answer / exam_voided_rewrite_source / exam_measurement_exhausted / live_zero_execution_short_circuit / live_attribution_exhausted，调用方确为已删壳），live_final_action / live_no_op_fires / live_all_holes / live_injection_overflows 保留——前三个的调用方是留存的 live_verdicts 判定账本协议（phase_execute 经它写账本，裁决明文保留），第四个的调用方是 phase_execute 自身；composites/acceptance_skill_status.py 保留——reports.py、acceptance_leak.py、phase_execute.py（现在还有 evidence/exam_sheet）都在调用；composites/live_attribution.py 的唯一生产调用方是 methods/evidence/live_group（借 _clip_view / _LIVE_VIEWS_BUDGET_CHARS），两个视图裁剪帮手移入留存的 live_measurement.py 后删除本体。其余按裁决删净：methods/acceptance/ 全包、composites/acceptance_submit.py、methods/segmentation/ 与 methods/authoring/ 两个死包（authoring 因 import 已删的 METHOD_DEFAULTS 本就不可用）、live_verdicts 的 _ALL_SUCCESS_SAFE_RULES 再导出（其唯一具名读者旧 live 驱动已删，内部改引 decision.STRICT_SHORT_CIRCUIT_RULES；discard 词表常量的再导出保留——phase_execute 与留存测试在读，属共享词表而非旧规则）。随壳退役的测试（合并前仍绿、删除本体后必断）：tests/composites/ 下 test_acceptance.py、test_acceptance_resume.py、test_live_acceptance_resume.py 整删，test_live_acceptance.py 只裁掉 G10/G11 壳段落（保留 live_measurement 读取器、judge_round、no_op_guard、finalize_group 的全部钉子，视图裁剪钉子随帮手迁至 live_measurement 侧，超长轨迹按预算裁剪的行为改钉在 evidence/live_group 的采集路径上）；test_curation_stage4_interleave.py 整删、test_curation_parallel.py 只删引用已删 stage5_curation 的那一例。考卷证据按边界迁移：learn_tasks 里内联约 60 行的考试答卷组装（_exam_evidence / _exam_attempts / _injected_version）移到 methods/evidence/exam_sheet.py 作正式采集器 collect_exam_evidence（含泄题作废描述与 P7b 来源选取，learn_tasks 改调用）。三个旧场景测试（裸答对 / 泄题末轮 / 末轮答错）的决策断言从已删规则改走相位世界统一入口 decide_task_list，钉住从验收事实到动作的端到端路径。AGENTS.md 第四节源码树同步到相位世界（README 仍是阶段 0–6 的旧设计文本，整体重写归 Wave 3 文档债）。已知遗留：scripts/smoke/smoke_stage01_{alfworld,searchqa}.py 仍 import W2-A 已删的 stage1_baseline（合并前即断，属 W2-A 清理尾巴）；smoke_leak_audit_k4.py 随壳删除。验收：全套 1865 passed + 36 failed（全部 test_assemble_reuse，W3-A 迁移装配器的既定残留红）+ 3 deselected（live 冒烟标记），tests/golden 按自愈跳过注记（Wave 4 重生成），test_layering 3 passed，ruff 全过。

工件：`src/expedistill/stages/__init__.py`、`src/expedistill/cli.py`、`src/expedistill/stages/{phase_execute,learn_tasks}.py`、`src/expedistill/composites/{__init__,decision,live_measurement,live_verdicts,acceptance_leak,acceptance_skill_status}.py`、`src/expedistill/methods/evidence/{__init__,live_group,exam_sheet}.py`（exam_sheet 新建）、删除 `src/expedistill/methods/acceptance/`、`src/expedistill/composites/{acceptance_submit,live_attribution}.py`、`src/expedistill/methods/{segmentation,authoring}/`、`scripts/smoke/smoke_leak_audit_k4.py`；`tests/unit/test_w2a_phase_execute.py`、`tests/unit/test_w2b_phase_learn.py`、`tests/unit/composites/test_decision.py`、`tests/unit/test_w1a_discard_scenarios.py`、`tests/unit/test_w1e_evidence_live_group.py`、`tests/composites/test_live_acceptance.py`、`tests/composites/test_curation_parallel.py`、删除 `tests/unit/methods/acceptance/`、`tests/composites/{test_acceptance,test_acceptance_resume,test_live_acceptance_resume,test_curation_stage4_interleave}.py`；`AGENTS.md`；本条对应 commit（分支 feature/multi-method）

## 2026-10-16 22:40 — Wave 2 评审修复（第一步）：phase_execute 拆成包，1660 行降到 8 个 400 行内模块

一段话摘要：Wave 2 三路评审 B 路的阻断项（phase_execute.py 单文件 1660 行，超 400 行拆分线 4 倍）按评审给出的现成方案拆分。stages/phase_execute.py 变成包 stages/phase_execute/：__init__.py（分发入口 run + 原 125 行总述 docstring + batch_execute 再导出）、phase_disk.py（相位目录、早相位记录、上一学习相位的库产物、units/report 落盘、规则 1 复用连接）、resolve.py（交付/执行参数/判分规则/比较规则/重派预算五个解析点）、engine.py（_Plan、K1 派发、判分、重派循环）、baseline.py、exam.py、live.py、final_test.py（四种 role 各一个模块）。语义零改动，全部模块落 400 行线内（最长 live.py 400 行）。两处搬移伴随的机械适配：K1 调用改走包属性 batch_execute（engine 与 live 在调用时读 phase_execute.batch_execute，保住测试对 phase_execute.batch_execute 的 monkeypatch 缝——否则拆包后打在包上的补丁到不了子模块）；test_r4_call_face 从读单个 __file__ 改为扫包目录全部 *.py（拆包后 __file__ 只指向 __init__.py，不跟着改会让"驱动不含 discard_skill/accept_skill 调用"的钉子静默缩水）。cli 的 importlib 入口与全部测试 import 语句未动（import 的是模块名，包的 __init__ 暴露同名出口）。AGENTS.md 第四节目录树同步登记新包。验收：全套 1865 passed + 36 failed（全部 test_assemble_reuse，W3-A 既定范围）+ 3 deselected，test_layering 3 passed，ruff 全过。

工件：`src/expedistill/stages/phase_execute/`（新包 8 个模块，取代单文件 phase_execute.py）、`tests/unit/test_w2a_phase_execute.py`（test_r4_call_face 改扫包目录）、`AGENTS.md`（第四节目录树）、本条对应 commit

## 2026-10-16 23:05 — Wave 2 评审修复（第二步）：六项裁决一次收口（相位库全量成员、exam 相位 unit_id 单源、D 相位耐久接受、学习相位重放幂等、配置晚失败前移、文本与死代码清理）

一段话摘要：按协调者对 Wave 2 三路评审（A 正确性、B 简洁性、C 续跑）的六项裁决，在拆包提交（上一条）之后一个提交收口全部剩余阻断与偏移。裁决 1（C 路阻断：待定技能断链）：相位间交接的技能库产物（phases/<标签>/skill_bank.jsonl）扩为携带全部成员——produce 与 produce_as_is 在 curation 处理完已接受集之后把技能仓里所有待定成员原样追加进产物（methods/curation/phase_bank.py 的 _with_pending_members，原子整写、按 skill_id 排序、重入幂等；K5 的 dest 模式续跑读取时剥离待定行，接受行照旧校验），预算硬阀仍只作用于已接受集（待定成员是 A/B 相位正在检验的材料，不是整库交付的注入内容）。交付驱动的筛法逐点核对旧世界后钉住：c6a9fb8 的 stage6 用 store.bank()（= 旧 stage5 产出的已接受集）喂 whole_bank 与 retrieval，故这两个驱动现在内部筛已接受（units.accepted_bank）；per_skill、group_by_task 维持筛待定（现状即旧世界行为）。C 相位摘要的 bank_reference 改为对已接受子集计算（与旧 stage6 摘要同义）。按评审 C 的硬要求补了真实学习相位接真实 A 相位的端到端测试（tests/unit/test_w2d_learn_execute_seam.py）：真实 decide_task_list→add→写待定技能→产物携带待定成员→真实 A 相位从产物筛待定、生成考题、判卷→真实学习相位把技能从待定收进已接受（及末轮仍失败→弃用的变体），另加 D L A L A L A L C 两轮 exam 序列骨架冒烟（加技能→考不过→改写升版本→再考通过→接受→空宇宙→收尾整理→终测注入，账本回执 added/revised/accepted/already_accepted 逐个核对）。过程中发现并披露一个范围外的缺口：切段（evidence 族的 segment 方法）在相位世界没有任何调用点，add 路径依赖的 subtask 没人生成，端到端测试沿学习相位测试的既有做法手工播种 subtask。裁决 2（A 路阻断：exam 相位 unit_id 的 benchmark 槽不一致）：以 reports 读取器的 join 路径为准（读取器用真实 benchmark），_exam_report 全部四处（原三处字面量）从 "exam" 改为 ctx.config.benchmark，与 units 计划单源；补评审 A 的崩溃场景测试：改写没产出新版本、注入块逐字节不变的待定技能跨两个 A 相位，第二相位零执行沿用旧判定（修复前该场景在 _reused_rows 处 CodeBugError 崩溃）。裁决 4（A 路 P1：D 相位耐久接受）：_run_arm_units 改为接收调用方传入的已接受集（D 相位传 stage1 记录），洞单元不再重新派发、不再重复抛错；原来那个永远为空的 per-arm accepted 文件读取连同死代码 _accept_new_holes 一并删除；补续跑测试钉住"带旗标接受→不带旗标续跑→不重抛、零新执行、摘要逐字段相同"。裁决 5（C 路偏移：学习相位重放翻倍）：学习相位以相位目录的摘要为完成信号（摘要带 bank 键即提交点——摘要最后写），重放时整相位跳过，产物不再按当前技能仓重写、回执不再翻倍；补测试钉住"已完成学习相位重放→摘要相同、产物逐字节不变、账本无翻倍、后续相位把技能改已接受也不泄漏进快照"。裁决 6（一次收口）：(1) scripts/smoke 三个断链冒烟脚本（smoke_tb_distill_chain.py 未披露地 import 已删的 stages.pipeline，smoke_stage01_alfworld/searchqa.py 已披露地 import 已删的 stage1_baseline）退役删除——它们是旧阶段世界的单阶段冒烟，相位世界的覆盖在测试与 W2 系列测试里，改写需要对真实服务的冒烟脚本属 Wave 3 文档/运维波次；(2) parse_phases 三个晚失败缺口前移到加载期：live 相位缺 compare（或指向 fresh_bare）、compare 指向非 baseline 相位（评审建议的"对照必须是 baseline 相位"——live 自身当对照的形状现在加载期即拒）、序列以 L 开头；_check_attempt_depths 的 live 对照深度分支随之删除（对照恒为 baseline 或 fresh_bare，深度恒为 n_attempts_weak），其 isinstance assert 一并消失；(3) B 路过时文本清理：4 处换点残留删除（_redispatch_limit 的 getattr 兜底、phase_learn._sequence 的 getattr 与"until then"文案、phase_learn 模块 docstring 的 W2-C 协调备忘、cli._phase_driver 的"另外两个代理"叙事句），stages/__init__ 的波次代号注释改述约束本身，live_verdicts/live_measurement/verdicts/author 的"M4 删除薄壳/Wave 2 之后接手"过时承诺约 10 处改为陈述事实（薄壳是执行相位驱动的正式 API，缝留给测试直驱内核），author/__init__ 的失实句（"stage 驱动尚未调用"）改写；9 处跨模块私有名转正（acceptance_skill_status 的 exam_attempts/find_bare、reports 的 run_record_from_attempt/run_record_from_trajectory、live_measurement 的 ROUNDS/first_row_from/ledger_path、decision 的 parse_unit_id，全部调用方一次改完）；run() 入口的判分规则预检删除（各角色自己推导：baseline 与 exam 在干活前各自校验，live 用比较规则，C 不判分）；(4) 死代码核对后删除：tally_verdicts（全仓零调用）、no_op_guard 薄壳与内核（生产调用方随旧 stage4 退役，相位世界的等价物是规则 1 跨相位复用并有测试钉住，连同 test_live_acceptance 的 5 个壳测试一并删除）、AuthoringParams.exams 字段（v4 已拒该键、零读取方）、_accept_new_holes；(5) 其他细节：_run_exam 摘要双写已在拆包时消失（此处核对确认）、D 相位 infra_shortfalls 的 30 行内联表达式改为复用 weak_metrics（noqa 消失、弱臂指标不再算两遍）、strict_rate_gain 三处字面量收敛到 decision.DEFAULT_COMPARE_RULE、SegmentationParams 报错文本改指 v4 键位（"evidence method 'segment': K must be >= 1"）、_write_units/_write_report 从先删再逐行追加改为 write_jsonl 原子整写、learn_tasks 的 in-flight 分支失实注释改写、llm/call.py 指向已删 stages.pipeline 的引用改指 cli；(6) 文本可读性：phase_execute 包总述、phase_learn、learn_tasks 的 docstring 裁决编号密度下调（R/P/C/E/E6/G10/G11/P7b/D4 等改为陈述事实，出处仅保留少量括号注），黑话改白话。不修项按裁决：control_arm_source 标签（D_reuse）改名留待 Wave 4 预登记、live 终态提交扫相位任务集外的组是裁决内保留、learn_tasks.py 581 行未在裁决范围内（评审 B 自评不构成阻断）。验收：全套 1876 passed + 36 failed（全部 test_assemble_reuse，W3-A 既定范围）+ 3 deselected，test_layering 3 passed，ruff 全过。

工件：`src/expedistill/methods/curation/{phase_bank,none,agentbrew}.py`（产物全量成员 + K5 兼容）、`src/expedistill/composites/curation.py`（dest 模式剥离待定行）、`src/expedistill/methods/delivery/{units,whole_bank,retrieval}.py`（已接受集筛选）、`src/expedistill/stages/phase_execute/{exam,baseline,engine,phase_disk,resolve,__init__}.py`（unit_id 单源、耐久接受、原子写、预检删除）、`src/expedistill/stages/{phase_learn,learn_tasks}.py`（重放幂等、文本清理）、`src/expedistill/core/config.py`（加载期检查清单 6/7 两条 + 对照必须 baseline）、`src/expedistill/composites/{decision,live_verdicts,live_measurement,verdicts,skill_status,reports,acceptance_skill_status}.py`、`src/expedistill/methods/{author/__init__,evidence/__init__}.py`、`src/expedistill/{cli.py,llm/call.py,stages/__init__.py}`、删除 `scripts/smoke/{smoke_tb_distill_chain,smoke_stage01_alfworld,smoke_stage01_searchqa}.py`；测试：`tests/unit/test_w2d_learn_execute_seam.py`（新建 3 例）、`tests/unit/{test_w2a_phase_execute,test_w2b_phase_learn,test_w2c_config_v4,test_w1f_phase_bank,test_w1d_delivery_signature}.py`、`tests/unit/methods/delivery/test_drivers.py`、`tests/unit/methods/{test_params,test_family_entries}.py`、`tests/unit/test_w1a_verdicts.py`、`tests/composites/test_live_acceptance.py`；`AGENTS.md`；本条对应 commit

## 2026-10-16 23:16 — Wave 3-S：把切段接进加技能路径（相位世界的 segment 驱动）

一段话摘要：Wave 2 收口时披露的缺口收掉：加技能路径（学习相位的 add 行 → 证据采集 collect_add_evidence 读 subtask）在相位世界没有生产者，端到端测试靠手工播种绕过。现在 add 行落地前先跑切段：learn_tasks._land_add 在收证据之前调用新驱动 methods/evidence/segmentation.segment_add_material，从 run 级轨迹仓（正是 D 相位的裸臂轨迹）按具名 trajectory_select 规则逐任务挑轨迹、只切其中的强模型轨迹（旧 stage2 的规则吃双角色混合集，这里喂同样的集合、只付强角色那一半的钱——弱段在相位世界没有消费者，改写路径读的是考卷与 live 组），逐条交给 K2（segment_checked）：确定 ID、逐单元落盘、按存量跳过、D7 校验、耗尽审计一概沿用，续跑语义未新增任何机制。0 步轨迹照旧在规则之前排除并写一次性的 "empty trajectory" 审计行（与旧 stage2 同通道，K2 的 _AUDIT_PATH 转为公开常量避免再复制）。接线点选在落地处而不是证据采集器内部：边界文档 2.5 定的是找证据只收集不生产，切段是 evidence 族 segment 方法自己的活，采集器保持零模型调用的纯读者。配套两处行为收口：(1) collect_add_evidence 的"没有可学材料"从裸 ValueError 改为 NoAddMaterial(ValueError 子类)，_land_add 捕获后记 skipped_no_material 回执——旧 stage3 对切不出的任务就是静默无技能，而 K2 耗尽是 run 内终态，抛错会让续跑永远卡在同一行；(2) 端到端测试 test_w2d_learn_execute_seam 的手工播种删除，subtask 由 L1 内真实切段产出，id 链（traj/…/strong/00/seg00 → skill）与播种版逐位相同，全部断言不变。惰性由结构保证：add 行只可能出自摸底缺口路径，exam-only / live-only 序列没有 add 行就不会碰切段（测试用空脚本质断言）。新测试 9 例（驱动层：规则选择与角色过滤、空轨迹审计一次性、中途崩溃恢复不重切、耗尽终态零重呼；相位层：无播种全链路落技能、无强材料记回执不抛错、无 add 行不切段、崩溃后续跑只切缺的那条）。w2b 三个走 add 路径的既有测试补了 segmenter 句柄（空脚本兼作"零调用"断言）。验收：全套 1885 passed + 36 failed（全部 test_assemble_reuse，W3-A 既定范围，未动）+ 3 deselected，test_layering 3 passed，ruff 全过。文档同步（README 阶段表、AGENTS.md 第四节 evidence 族条目）留给 W3-C。

工件：`src/expedistill/methods/evidence/segmentation.py`（新建，segment_add_material 驱动）、`src/expedistill/methods/evidence/add_path.py`（NoAddMaterial）、`src/expedistill/methods/evidence/__init__.py`（族清单补登记）、`src/expedistill/composites/segment_checked.py`（AUDIT_PATH 公开常量）、`src/expedistill/stages/learn_tasks.py`（_land_add 接线与无材料回执）、`tests/unit/test_w3s_segmentation_wiring.py`（新建 9 例）、`tests/unit/test_w2d_learn_execute_seam.py`（去播种）、`tests/unit/test_w2b_phase_learn.py`（3 例补 segmenter 句柄）、本条对应 commit

## 2026-10-16 23:16 — Wave 3 / W3-B：audit_live_run 与 scripts/run 迁到相位世界（R10 脚本侧）

一段话摘要：修复项 R10 的脚本部分，一个提交。scripts/audit/audit_live_run.py 按冻结 manifest 的 schema 版本分派读取路径：v4 run（含 sequence/phases）走相位目录（phases/&lt;标签&gt;/ 的 phase.json、units.jsonl、acceptance_report.jsonl、redispatch.json、学习相位三本账本与 learn_summary/skill_bank、C 相位两个 arm 的自有仓），v4 之前的 run 原样走旧阶段路径（重构前的历史 run 仍可用同一工具查账，这是硬要求）；manifest 模式分派从 config.methods.acceptance 改为冻结序列判型——序列含 exam 相位即 exam 方式，否则含 live 相位即 live_run 方式（对齐评审发现 1 的原文）。五类检查对 v4 的更新：terminals 用学习相位 outcomes.jsonl 的 delete/author-delete 回执解释先行动作落下的终态（G10/G11 耗尽分支在相位世界没有对应账本，随旧归因壳退役）；round-stores 按每个 live 相位自己的 execution.n_attempts 推每轮深度（--n-attempts 覆盖仍在）；ledgers 只查 verdicts（v4 不写 attributions，学习账本单独立查）；shortfall 的阶段级失败行按学习相位的 pNN 标签报告；injection-audit 不变（轮仓与冻结行的位置未动）。新增四个 v4 相位目录检查：phase-dirs（phase.json 与冻结序列逐字段一致）、units-report（无重复单元、报告行都指向已解析单元、状态词表闭合、complete 摘要不欠报告行、C 相位不写报告、redispatch 记录只认计划内单元）、learn-ledgers（回执与技能仓状态对账、revision 账本 from_version 与版本链完整、bank 产物与摘要提交点互为存在条件）、final-test（complete 摘要下两个 arm 各自持有全部期望测试单元，复用对照回落 run 级仓）。--self-test 补 v4 场景：干净 v4 run（D Lseed B1 Lmid B2 Lfinal C 全链，含改写升版、终测双臂）零错误，六个篡改（phase.json 角色翻转、units 重复行、捏造报告行、仓库矛盾回执、终测丢轨迹、判定矛盾状态）逐一捕获。脚本侧其余改动：经协调者裁决（2026-10-16，授权动 W2-C 已交付的 cli.py 一处）--until-phase 放宽到 -1 表示只跑 freeze（种子脚本需要先落 manifest 再预存单元，v4 下原来没有这个入口），seed_tb_stage1.py 的 freeze 步与结尾提示随之适配，launch_tb21_live_run.py 的透传改为 --until-phase；fork_run_stage04.py 按历史工具注明只在其 v2 维护分支 checkout 上运行。scripts/analysis 三个脚本只读 campaign 归档与 run 目录无关，未动；scripts/smoke 四个在册脚本 import 逐一验证有效，未引用 Wave 2 已退役的三个断链冒烟。验收：全套 1890 passed + 36 failed（全部 test_assemble_reuse，W3-A 既定范围，未碰）+ 3 deselected，audit --self-test 全过（legacy 4 篡改 + v4 6 篡改），test_layering 3 passed，ruff（src tests scripts）全过。已知边界：本地无历史 run 可做只读复演，v4 前兼容靠三条验证钉住——旧路径代码逐行保留（仅重命名与参数管道）、test_audit_live_run 的 v1 对象 run 测试照旧通过、legacy 自测四篡改场景照旧全过；W2-C 的越界参数化测试把 -1 从非法改到合法（-2 仍非法），是该裁决的必然后果。

工件：`scripts/audit/audit_live_run.py`（schema 分派 + 五类检查 v4 化 + 四个相位目录检查 + v4 自测）、`scripts/run/{seed_tb_stage1,launch_tb21_live_run,fork_run_stage04}.py`（--until-phase 适配与历史工具注记）、`src/expedistill/cli.py`（--until-phase -1 = 只跑 freeze，协调者授权的一处）、`tests/unit/test_w3b_audit_v4.py`（新建 11 例：分派、模式判型、每轮深度、四类相位目录检查）、`tests/unit/test_w3b_until_phase_freeze_only.py`（新建 3 例：-1 冻结即停、冻结后续跑、越界报错含 -1 释义）、`tests/unit/test_w2c_run_loop.py`（越界参数化 -1→-2）；本条对应 commit `wave3/b`

## 2026-10-16 23:22 — Wave 3 / W3-A：装配器迁 v4 相位世界（R10 装配侧），残留 36 红清零

一段话摘要：把 assemble_reuse.py 从 v3 槽位世界迁到 v4 相位世界，修复 R10 装配侧与 test_assemble_reuse 的 36 个既定残留红。源 run 两个世界都能读：v4 源的库从末个 learn 相位的产物（phases/<标签>/skill_bank.jsonl）读，v3 源仍从 run 根读；身份比对把源 manifest 的配置先经纯映射升到 v4（v2 经 upgrade_v2_config 链 upgrade_v3_config，v3 经 upgrade_v3_config），两侧再经 from_dict→to_dict 规整为同一规范形状后逐键 diff（M1 同构比对思想用于配置面：v3 补槽默认值与 v4 显式默认不再制造假差异）。白名单从"delivery 槽"改为其 v4 对应物：终测相位的 delivery 与 compare（mode 同时决定终测对照臂的取法，full 对 D、split 用 fresh_bare，所以 compare 必须随 mode 允许变化），按终测相位的局部名动态推导前缀，相位改名即拒绝。装配产物一律 v4：过滤库同时落 run 根与末个 learn 相位产物（预置），使末个 learn 相位的整理走 K5 的非空 dest 库短路（零模型调用、不重理已理过的库），phases/ 树有精确形状断言（只允许这一个文件）；v3 源的 stage-3 generation_failures 账本在 v4 没有对应物（学习相位对失败新增的口径是重试而非终态），不拷贝、按 train 侧行数在摘要与 ASSEMBLED_FROM 披露为将被重新尝试的单元；v4 源相位产物里的待定成员随库与 store 原样带入并按数披露，接受集完整性不变量改为比对库的已接受行。测试 43 个修复 + 3 个新增（v3 源等价装配、待定成员披露、脏 phases/ 目录拒绝），夹具迁 v4（配置经 ExperimentConfig 直构 + stage0 真冻结，目标走 YAML + load_config），并新增端到端续跑测试：装配产物用真实管线（cli.run_experiment + 拒答假客户端，任何模型调用即崩）从 p00 跑到末个 learn 相位，D 相位跳过存储轨迹、learn 相位零调用短路、预置产物逐字节不变，整段重放再次零调用完成——即"装配出的新 run 在 v4 闸下可正常加载续跑"有测试钉住。回退转红三方向验证：去掉预置、退回 v3 库读取路径、去掉终测相位白名单前缀，分别 5、15、26 个测试转红。语义不变性以行为断言保持：装配幂等（重装配不重复）、身份比对拒绝面（方法键/模型/规则/画像/benchmark/相位改名/exam↔live 换方法）、复用溯源（全链 train 判定、merged 原件分组、campaign 播种）、baseline 档位钉住键与轨迹级零下游拷贝。验收：全套 1915 passed + 3 deselected（全绿，主分支 36 残留红清零），test_layering 3 passed，ruff 全过。

工件：`src/expedistill/stages/assemble_reuse.py`（v3/v4 双源读取、规范形状身份比对、终测相位白名单、相位库预置、phases/ 树断言、ASSEMBLED_FROM v4 文本）、`tests/unit/stages/test_assemble_reuse.py`（夹具迁 v4 + v3 降形夹具、46 例）、`log.md`（本条）、本条对应 commit（分支 wave3/a）

## 2026-10-16 23:33 — Wave 3 / W3-C：README 相位世界重写、AGENTS.md 同步、v3→v4 配置转换器与 7 份 v4 配置

一段话摘要：按修复计划 R10 的文档与转换器部分，把设计正本 README 从"阶段 0–6"整体重写为相位世界（七模块与边界文档逐节对齐、方法 = sequence + phases 的 v4 字段表与加载期检查清单、pNN 标签规则、run 级五仓 + phases/pNN_<名>/ 相位目录的完整布局、correctness 词汇三件套、切段在学习相位 add 行内部发生的定位与 skipped_no_material 回执、学习相位三本账本 + learn_summary.json 与 12 种回执结局、--until-phase 与退出码），并逐节对照代码自查（改掉两处与代码不符的初稿：Trajectory 没有 termination 顶层字段（终止原因在 meta），装配器的源 schema 支持以现状措辞、不预写 W3-A 的迁移结果）。AGENTS.md 同步：第四节目录树补 core/contracts.py、prompts/llm.py、benchmarks/{aux,react_loop}.py、evidence 族的 segment 驱动与 package、learn_tasks 的 add 路径切段说明，第五节登记 convert_v3_configs.py，第六节配置 schema 升 v4，积木表指针从 README 第一节改第三节，第七节插件契约核对（RunOutcome 三字段 + InfrastructureError 上一波已写对，仅整理措辞）。转换器 scripts/run/convert_v3_configs.py 落成并实跑：默认扫 schema_version: 3 的 7 份配置，经 upgrade_v3_config 纯函数转换、from_dict 过 v4 加载期检查、写 <原名>_v4.yaml 后再用 load_config 回载验证（7/7 通过：5 份 exam 序列 D L A L A L A L C、1 份 live 序列 D L B L B L C、tb21_split_reuse 的检索交付进 C 相位），v2 文件支持显式传入链式走 v2→v3→v4、已有 v3 记录的 v2 文件拒绝并指路、v4 跳过、已存在的输出文件不覆盖。runbook（live_run_acceptance_runbook_20261014.md）补"相位世界对应说明"节：shortfall 预注册正本与视图预算节继续有效，stage-4 模式 → D L B L B L C 序列、attributions.jsonl → outcomes.jsonl 等逐条给出换算表，并如实标注 audit_live_run.py 仍读旧账本布局（W3-B 的迁移未完成时以 verdicts/轮仓部分结果为准）。与任务书的两处偏离如实披露：(1) 任务书说 --until-phase "-1 只跑冻结步"，代码的边界检查拒绝 -1（0 ≤ N < 序列长度），README 按代码实况写并注明"只跑冻结步当前没有合法 N 值"；(2) 任务书说学习相位"十种回执结局"，代码实际写 12 种（added / accepted / already_accepted / revised / unchanged / kept / deleted / discarded / skipped_no_material / skipped_existing / skipped_terminal / infra_failed），README 按 12 种写全。新测试 tests/unit/test_w3c_convert_v3_configs.py 10 例（7 份配置逐一转换回载、v2 链式两跳、v2 有 v3 记录拒绝、v4 跳过、不覆盖、坏输入按文件拒绝、目录扫描恰好选中 7 份 v3）。验收：全套 1895 passed + 36 failed（全部 test_assemble_reuse，W3-A 既定范围，未动）+ 3 deselected，ruff 全过。

工件：`README.md`（整体重写）、`AGENTS.md`（第四/五/六/七节同步）、`docs/runbooks/live_run_acceptance_runbook_20261014.md`（补相位世界对应说明节）、`scripts/run/convert_v3_configs.py`（新建）、`tests/unit/test_w3c_convert_v3_configs.py`（新建 10 例）、`configs/experiments/*_v4.yaml`（7 份转换产物：alfworld_skillopt_split_v2_v3_v4、alfworld_skillopt_split_v3_v4、searchqa_skillopt_split_v3_v4、tb21_full_v2_v3_v4、tb21_split_live_run_v3_v4、tb21_split_reuse_v3_v4、tb21_split_v2_v3_v4）、本条对应 commit
## 2026-10-16 23:59 — 更正披露：W3-B 条目的模式判型描述与代码相反（评审 A 细节 D2）

一段话摘要：更正 2026-10-16 23:16 的 W3-B 条目。该条与提交说明写"序列含 exam 相位即 exam 方式，否则含 live 相位即 live_run 方式"，但代码（audit_live_run.py 的 _audit_v4）是 **live 优先**：先看 live 相位、其次才看 exam 相位，两者只在手写混合序列（同时含 A 与 B 相位）时分岔——升级映射产生的序列不会混合，加载期检查清单也不禁止手写混合，故现网无行为风险。本次修复提交已把脚本 docstring 的同一句描述一并改对；本条只更正 log 的记录，23:16 原条目按规范不改原文。

工件：`scripts/audit/audit_live_run.py`（docstring 模式判型句，随本次修复提交更正）、本条

## 2026-10-16 23:59 — Wave 3 三路评审修复：装配短路前产物刷新（B1）、审计回执惯用语与 WARN 降级（O2/O3）、转换器原子写（O4）与各路细节一次收口

一段话摘要：按协调者对 Wave 3 三路评审（A 正确性、B 简洁、C 续跑）的八项裁决一次收口，一个提交。裁决 1（C 阻断 B1）：装配 run 的末相位库产物是预置的，本 run 的 A/B 相位新接受任何成员后，K5 整理短路的过期检查会 ConfigError 卡死——修法选"刷新"而非"披露不支持"：curate_bank 的 dest 模式短路返回前先把相位库产物刷新为技能仓当前状态（pending→accepted 的行按存储记录刷新、缺失的已接受行按存储记录原样追加，源 run 的整理结果逐字保留），纯程序操作零模型调用，重放逐字节不变（只在行实际变化时重写文件）；run 模式的过期拒绝原样保留（stage6 存量轨迹会与被修补的库漂移，不能放宽）；ASSEMBLED_FROM 同步披露"末相位产物 = 源 run 整理结果 + 本 run 新接受成员原样追加，后者未经整理"。裁决 2（两个核心验收测试）：(1) 零调用续跑测试的夹具补了弱臂判错——首学习相位真实推出 add 行、切段按存量 subtask 跳过、技能已存在落 skipped_existing 回执，零模型调用仍成立（该咬合路径此前只有静态推演）；(2) 新增端到端测试：装配带入待定成员 → A 相位真实测量（5 次脚本化调用）→ keep 接受 → 末相位短路刷新补上该行、run 正常完成——修复前该路径 ConfigError 卡死，回退验证已确认两个测试都转红。裁决 3（审计 O2）：同（目标，技能）的 accepted 后跟 already_accepted 是相位内续跑的合法惯用语，检查 8 放行；仍带动作的第二条回执才 ERROR。裁决 4（审计 O3）："库产物在、提交摘要缺"从 ERROR 降为 WARN（崩溃中间态与装配预置的常态，与 docstring 的 WARN 定义一致）。裁决 5（转换器 O4）：写盘改临时文件 + 改名，先验证临时文件再落位，"已存在不覆盖"保持。裁决 6（B 路 P1）：README 已知风险 4 改口——代码加载期只强制 n_attempts ≥ 1，≥ 2 是设计文档（A2 的 D1 守卫）的推荐下限，加载期不拦（预检一律纯报告）。裁决 7（A 路 P2）：README 装配档位表述收紧——D 与学习相位零调用、C 相位全新执行（设计如此）、v3 源生成失败行由首学习相位真实重试（有调用）。裁决 8（细节一次收口）：runbook 0.5 节重复标题删除；审计脚本 docstring 的模式判型句改对（live 优先，另附更正条目）；README 术语首现指引五处（待定挂 pending、金基线指 tests/golden/、基础设施空洞指 5.9、weak_profile_file 一句话、kept/deleted 回执分行）；K2 前缀落盘取舍在 README 切段小节补披露；转换器两个输入错误场景（--dir 缺值、打错的旗标）改为可指导行动的报错（退出码 2）；log.md Wave 3 四条按时间戳重排（恢复合并前的时间顺序）。新增测试 10 例（装配 B1 端到端 1、刷新单元 3、审计惯用语与真重复 2、转换器 4——含参数错误与原子写），另有 1 例（库产物缺摘要）从断 ERROR 改为断 WARN；全套 1958 passed + 3 deselected，test_layering 3 passed，ruff 全过。范围外未修：AGENTS 第五节举例提到的 run_experiment.sh 不存在（先于本波）；完成态 exam 相位在整段重放时会对已判考题再作答（K3 设计上不幂等、相位无完成信号，零调用测试因 A 相位宇宙为空从未踩到，属 Wave 2 既有语义，记观察待裁决）。

工件：`src/expedistill/composites/curation.py`（_refresh_dest_artifact + 短路接线与 docstring）、`src/expedistill/stages/assemble_reuse.py`（ASSEMBLED_FROM 披露）、`scripts/audit/audit_live_run.py`（检查 8 惯用语与 WARN、docstring 模式判型）、`scripts/run/convert_v3_configs.py`（原子写与用法报错）、`README.md`（裁决 6/7/8 各处）、`docs/runbooks/live_run_acceptance_runbook_20261014.md`（重复标题）、`log.md`（Wave 3 条目重排、W3-B 更正条目、本条）、`tests/unit/stages/test_assemble_reuse.py`（夹具弱臂判错 + 零调用断言 + B1 端到端）、`tests/unit/test_w1f_phase_bank.py`（刷新 3 例）、`tests/unit/test_w3b_audit_v4.py`（惯用语/真重复/WARN 3 例改写）、`tests/unit/test_w3c_convert_v3_configs.py`（4 例）、`docs/reviews/wave3_review_20261016_{A_correctness,B_simplicity,C_resume}.md`（评审正本，随 4d1606a 入库）、本条对应 commit

## 2026-10-17 00:17 — Wave 3 / W3-D：exam 相位补已判答卷的重放保护（K3 不幂等的调用方守卫）

一段话摘要：修复 2026-10-16 23:59 条目登记的观察——完成态或部分完成的 exam（A）相位整段重放时会对已判考题重新作答（裸答、泄题审计、带技能作答全部重跑，串号递增，reports 的原始测量被新答卷替换）。根因：exam_once 的模块规约把"这个含义的已判答卷是否已存在"定为调用方职责（K3 不幂等，每次调用都是一条新答卷），旧世界 stage4 靠"终态技能不再进入"履行，新世界相位驱动漏了这一检查；_pending_attempt 只认未判答卷，只能覆盖"作答后判卷前崩溃"这一种恢复。修法两件：(1) composites/acceptance_skill_status.py 新增公开读取器 find_judged_attempt——按 (考题, purpose, skill, 版本, 轮次) 查最新已判答卷，与 exam_answering._pending_attempt（最新未判答卷）互为对偶，docstring 写明分工：已判 = 已完成的测量，调用方直接沿用、不再调 exam_once（判卷也不重跑，verdict 已在答卷行上）；未判 = 在途工作，只有 exam_once 自己经 _pending_attempt 补判，读取器永不返回。(2) stages/phase_execute/exam.py 驱动在两处 exam_once 调用前先查该读取器，命中即沿用。关键取舍——保护范围必须是"同一相位实例的重放"而非全局按 key 去重：裸答的 key 不带版本，全局去重会让改写升版后的下一个 A 相位沿用旧裸答，直接打破 W2-D 两轮 exam 序列（A2 必须重新裸答）。同一相位的判定标记用现成的磁盘事实：unit_id 带相位标签，本相位 units.jsonl 每次启动整文件重写，覆写前的内容就是"本相位更早一趟已规划过这些单元"的唯一持久记录——新增 phase_disk._earlier_pass_unit_ids 在 _write_units 之前读取；标记只收窄查询范围、从不跳过工作（早一趟崩在作答前的单元照常新跑）。考题本体与泄题判定沿用各自既有落盘检查（考题仓 exists、leak 账本首判为准），未改。边界自查：K3 的落盘与账本协议、exam_once.py / exam_answering.py、core/contracts.py、其他相位全部未动；规则 1（跨相位单元级复用）与新的相位内答卷级复用互不干扰（规则 1 命中的单元在 exam_unit 之前就短路）。新增 tests/unit/test_w3d_exam_replay.py 四例：完成态三相位结局（measured / control_perfect / leaked）整段重放零模型调用（任何调用即炸的空脚本句柄）、考题/答卷/泄题账本/报告/摘要逐字节不变且摘要相等；部分完成重放只补未测单元（已测单元任何模型调用即炸、其产物逐字节不变、泄题账本保持一行一版本）；作答后判卷前崩溃（既有 _pending_attempt 路径）不受影响（判卷死异常制造崩溃点，续跑只补判、不再问弱模型）；改写升版后的下一个 A 相位重新裸答并按新版本另判审计（保护范围钉住）。回退验证：把 replayable 置空临时禁用保护，前两例转红（部分完成例的 judge/answerer 调用数 4 对 2 直接暴露重答）、后两例照旧绿，恢复后全绿。验收：全套 1962 passed（1958 基线 + 4 新增）+ 3 deselected，test_layering 3 passed，ruff 全过；W2-D 三号场景（两轮 exam 骨架）确认仍全绿。留给 Wave 4：E5 的续跑变体核对时，D 相位有 K1 单元 ID 跳过、B 相位有判定账本 append-once 与轮仓跳过、C 相位按设计全量重跑，加上本条后四种执行相位各有明确重放语义；金基线（tests/golden，当前不参与收集）重生成时，A 相位单趟行为不变，任何含"重放 A 相位"的场景现在应看到零新增答卷、产物逐字节稳定，等价断言按此口径写。

工件：`src/expedistill/composites/acceptance_skill_status.py`（find_judged_attempt 读取器 + 模块 docstring 补一行）、`src/expedistill/stages/phase_execute/exam.py`（两处调用前守卫 + docstring）、`src/expedistill/stages/phase_execute/phase_disk.py`（_earlier_pass_unit_ids）、`src/expedistill/stages/phase_execute/__init__.py`（包 docstring 续跑句与 units.jsonl 布局注记）、`tests/unit/test_w3d_exam_replay.py`（新建 4 例）、`README.md`（5.3 节补"同一相位的重放保护"、第四节布局 units.jsonl 注记）、`log.md`（本条）、本条对应 commit

## 2026-10-17 01:05 — Wave 4：金矩阵全量重生成 + E1–E6 对归档基线的等价验收（多方法重构收尾）

一段话摘要：Wave 4 一次做完三件事并分两个提交落地（686121f 先修续跑、本条提交重建金基线），全程测试绿。第一件：E5（崩溃续跑等价）在相位世界上先抓出三处真实缺陷，全部修掉才重生成——(a) 执行相位没有完成信号（W3-D 条目登记过的观察）：已完成的相位被续跑重入时会按"当前"技能仓重新推导宇宙，而后继学习相位已经改过这个仓，重入的 B 相位规划出了属于下一轮的注入块、经规则 1 反过来"劫走"自己后继的测量，续跑后的 run 技能库为空、终测 delta 归零；修法是存储的 phase_summary 即完成信号（baseline/live/test 看 complete=true、exam 看 infra_failed 为空），完成的相位重放时整个跳过、工件保持自己那一趟的字节，与学习相位的 bank 键提交点同构。(b) live 相位的宇宙会随自己的进度缩小（判定提交后组不再待定），重入时按当前计划重写 units.jsonl 会抹掉早趟的行、摘要与报告脱节；修法是重入时把覆写前的单元行并回，摘要的 units/dispatched/skipped_* 全部从并集加轮内磁盘事实（冻结审计行、终态、复用表）推导，不读本趟计划清单。(c) 学习相位三账本续跑翻倍：在途改写重问会追加第二条 revisions 行、重推的 keep/delete/add 行会写一遍跑完不会有的回声回执（already_accepted、skipped_existing）；修法是全部落地加回执守卫（每 (目标, 技能) 至多一条回执、有回执的 add 整行跳过、改写计划每技能只记一次），续跑后的账本与一次跑完逐字节相同。三处都有金矩阵证据（revision_window 变体、crash_before_call 系列），修后 8 组合 × 3 个分数崩溃点（live 另加修订窗口变体）全部等价；test_w3d 的部分重放用例改按真实中断方式构造（中途 InfrastructureError），原先"两趟之间改库"的构造在序列重放里不可达、完成信号现在会拒绝它。第二件：金基建重建——tests/golden/harness.py 改走 upgrade_v3_config 生成的 v4 配置 + cli.run_experiment 相位循环（8 组合的序列与 pNN 标签即语义仪器冻结的推导），假 benchmark 补 verdict 规则注册（scorer，与单测同一道缝），tests/conftest.py 的自愈跳过删除、golden 恢复收集；金矩阵 8 组合全量重生成（新调用数：exam full 18 / split 9、live full 10 / split 5，provenance base 686121f），生成时逐组合的崩溃续跑等价即中止门全过。第三件：E1–E6 对归档基线束（69904f7）逐条验收——结论：测量语义全部等价（E3 五桶 8 组合全等、control_source 经别名表把 stage1_reuse 与 D_reuse 归一同值；E6 verdicts 除 injection_tokens 外全等；E2 组终局全等；E6 对齐痕迹里幸存的强模型技能逐条全等），差异集中在一个根因：修复方案 R6 把加技能路径的证据收窄为只读 D 相位强模型轨迹，旧世界弱臂失败段产出的"诊断技能"在相位世界没有生产者，库更小，一切由库派生的量随之不同——E1 少弱源技能行（exam split 1 条、full 2 条；live split 2 条、full 4 条）、E2 终态与泄题判定相应少行、E3 检索审计行的 categories/打分排名不同、E4 少弱臂切段与诊断调用且嵌入技能清单的 prompt 文本不同、E6 痕迹少弱技能修订行、E6 verdicts 的 injection_tokens 变小（该字段是注入块的函数，比较时两侧置空、已披露）。R6 的收窄是修复方案的既定设计（W1-E 与 W3-S 条目均已披露），不是回归；但它不在任务预登记的差异清单里，按纪律如实上报：E1"最终技能库行多重集必须全等"这条硬标准对 69904f7 基线不成立，差集恰为弱源技能行（机器枚举、逐条在披露报告里）。预期差异逐处兑现：R2b 轨迹形状（E1–E6 视图不读轨迹对象，零影响）；control_arm_source 标签从 stage1_reuse 改为 D_reuse（别名归一）；E6 的 attributions 部分按 W2-B 新账本形状比较——对齐 (任务, 轮, 技能, 版本) 痕迹，账本不再落盘的诊断/problem/注入 sha 三字段不比（G10 调用本身仍被 E4 逐字节钉住），这是两处已披露的断言收缩；pipeline_log 行 key 保持 "stage"、值为 pNN 标签；布局对齐改仪器不改基线（C summary=phases/pNN_C/phase_summary.json、终库=末学习相位工件 skill_bank.jsonl、generation_failures 移到 A 相位且行键 exam_id、学习三账本映射到规范名，全部由重写后的 v4_layout 数据表完成）。全部差异以机器生成的披露报告逐条枚举并钉死（tests/fixtures/golden_acceptance/wave4_e1_e6_disclosure.json：每组合每红准则带完整 missing/extra 清单、每条差异带根因键），standing test 重生成逐字节比对——任何新增差异、或已披露差异消失/变化都转红；现跑落地测试证明真 run 产出与夹具推导的同一节。验收数字：测试从 1962 passed + 3 deselected（golden 被跳过）到 2021 passed + 3 deselected（golden 恢复收集 59 例），ruff E9/F 与 test_layering 过，audit_live_run --self-test 全过且对新生成的 live run 目录审计 0 error / 0 warn / 0 info；整个多方法重构最终状态：分支 feature/multi-method 从 c6a9fb8 到 HEAD 共 42 个提交（本波 2 个），删除旧世界文件 32 个（stage1–6 六个阶段模块、pipeline.py、methods 的 acceptance/authoring/segmentation 槽位、旧 K4/K6 壳及配套测试、四个 smoke 脚本），工作区干净。

工件：`src/expedistill/stages/phase_execute/__init__.py`（_finished_summary 完成信号）、`src/expedistill/stages/phase_execute/live.py`（单元并回 + 摘要磁盘推导）、`src/expedistill/stages/phase_execute/phase_disk.py`（_earlier_pass_units）、`src/expedistill/stages/learn_tasks.py`（回执守卫 + 改写计划单行）、`tests/unit/test_w3d_exam_replay.py`（部分重放用例改真实中断构造）、`README.md`（4.3 布局完成信号与账本注记、5.4 live 相位宇宙缩小的并回规则）、`tests/golden/harness.py`（v4 配置 + 相位循环 + scorer 注册）、`tests/golden/semantics.py`（v4_layout 实际布局、E6 学习账本对齐痕迹、injection_tokens 置空披露、期望调用数换金矩阵 provenance、披露报告生成器）、`tests/golden/test_semantic_instrument.py`（重写：布局钉、身份、11 例篡改、桥接对、E5 全组合、披露钉死与现跑落地）、`tests/golden/generate.py`（未改，流程沿用）、`tests/fixtures/golden/`（8 组合 + provenance 全量重生成）、`tests/fixtures/golden_acceptance/wave4_e1_e6_disclosure.json`（新建，机器披露报告）、`tests/conftest.py`（删除自愈跳过）、`tests/fixtures/golden_semantic_baseline/`（未动，归档基线只读）、`log.md`（本条）、commit 686121f 与本条对应 commit

## 2026-10-17 02:41 — 裁决：接受 R6 证据源收窄，E1 以差异披露为验收口径

一段话摘要：Wave 4 的 E1–E6 等价验收中，E1"最终技能库行多重集全等"对归档基线（69904f7）不成立，全部差异单一根因：修复方案 R6 把加技能证据收窄为只读 D 相位强模型切段，旧世界弱臂失败段产出的诊断技能在相位世界没有生产者。协调者裁决接受收窄：详细计划 R6 明确写了强模型切段是用户批准的计划内容，边界定稿把证据源留为开放，硬约束第 5 条"或逐处披露差异"为此预留通道；差异已机器枚举钉死在 wave4_e1_e6_disclosure.json（standing test 对回归转红）。约束：新旧世界的实验数字不可直接对比；恢复弱源诊断技能属新方法开发，走 README → decisions → 代码流程。

工件：`docs/decisions/e1_baseline_equivalence_ruling_20261017.md`（裁决记录）、`tests/fixtures/golden_acceptance/wave4_e1_e6_disclosure.json`（差异披露，本条引用）

## 2026-10-17 09:25 — SearchQA live 全链路 smoke：真实插件端到端首跑，抓出并修复 merge_check none 无法合并的缺陷

一段话摘要：按指令对相位世界做一次完整闭环 smoke——学习 → 执行 → 再学习 → 再执行 → 测试，即 live_run 方法的 D L B L B L C 序列（用户中途改指令：从 exam 换成 live_run）。环境无模型端点，为此给 provider 注册表加了 fake 条目（llm/fake_provider.py，提交 ac8b460）：按 prompt 文本锚点分发相位世界会调用的全部方法 prompt（切段、写技能、归因、修订、找相似、合并、分组），执行类 prompt 按 JSON 计划脚本作答——强模型永远提交正确答案，弱模型按每题策略（skills / revised_skills / never）跟随注入块与 G11 修订标记分流，计划由 scripts/smoke/gen_searchqa_fake_plan.py 从 searchqa-eval 题库生成。benchmark 选 SearchQA（进程内、判分器纯标准库，只需 EXPEDISTILL_SEARCHQA_EVAL_DIR；ALFWorld 需外层 venv 与三个环境变量，阻力更大）。配置 configs/experiments/searchqa_split_live_smoke_v4.yaml：split 模式 4 训练 + 1 测试共 5 题，retry_limit=1，B 相位 group_by_task + real 执行 + compare D，C 相位 whole_bank + fresh_bare。首跑（run ..._20261017）逐相位用 --until-phase 分段推进（顺带验证了逐相位重放跳过机制），D 强 4/4、弱 0/4（真实判分器打分），首轮 B 一组提升三组不提升，L 归因修订三个技能升 v2，次轮 B 两个修订组提升、never 组仍失败，末 L 删除 never 组、keep 两组；但末相位整理时发现真缺陷：merge_check none 下每个合并单元都以"source skill has no stored exam; merge unverifiable"跳过——K5 的 unit_worker 无条件调 _source_exams，而 live 序列根本没有考题相位，等于所有正式 live_run 的整理合并全部静默作废；curate_bank 自己的 docstring 与 v3 升级注释都写明 none 跳过考卷（合并由轮 1 组重执行证明），既有单测没抓到是因为 none 用例也预置了考卷。修法（提交 2f08f53）：考卷要求归 exam 检查所有，_source_exams 只在 merge_check != none 时调用；回归测试零考卷种子断言合并被保留。旧 run 按 AGENTS 6.7 作废（INVALID.md），新 run ..._20261017_v2 一次跑完 D L B L B L C：终测 with_bank 1.0 对 control 0.0（delta +1.0，含 sub_EM 辅助口径），终库 = 1 个合并产物 + 1 个技能，audit_live_run 审计 0 error / 0 warn / 0 info（--self-test 也过），summary 命令完整可读，全程 47 次模型调用零失败零重试，两次 run 的中间相位库引用逐字节一致（确定性复验）。报告见外层 /Users/yimingniu/Documents/Manager/state/replies/mm_smoke_test.md。范围外观察：末 L 的 keep/delete 行指向已终态组时 pending_groups 为空、不写回执（learn_tasks 文档写"skipped and recorded"，实为不记录，审计兼容，记观察）；假客户端令牌数为 unknown（字符串响应不带用量，账本如实记 unknown 未记 0）。

工件：`src/expedistill/llm/fake_provider.py`（新建，提交 ac8b460）、`src/expedistill/llm/providers.py`（fake 注册）、`tests/unit/llm/test_fake_provider.py`（新建 20 例）、`tests/unit/llm/test_providers.py`（注册表集合同步）、`.env.example`（EXPEDISTILL_FAKE_PROVIDER_PLAN）、`configs/experiments/searchqa_split_live_smoke_v4.yaml`（新建）、`scripts/smoke/gen_searchqa_fake_plan.py`（新建，提交 b0ba0d6）、`src/expedistill/composites/curation.py`（merge_check none 修复，提交 2f08f53）、`tests/unit/test_w1f_phase_bank.py`（回归测试）、`log.md`（本条）、run `runs/searchqa_split_live_smoke_20261017/`（作废，INVALID.md）、run `runs/searchqa_split_live_smoke_20261017_v2/`（有效，审计干净）、tmp/mm_smoke/fake_plan.json（生成数据）

## 2026-10-17 09:40 — 回执缺口修复：指向已终态组的 keep/delete 行补写行级回执

一段话摘要：修复 2026-10-17 09:25 smoke 条目登记的观察——末学习相位的 keep/delete 行指向已终态组时不写任何回执。根因：learn_tasks 的 _target_members 对组行经 pending_groups 解析，只有还挂着待定成员的组才有返回，已终态组解析成空成员表，落地循环一次不进，整行零回执，模块文档"skipped and recorded"的承诺在行级落空。按协调者裁决补写行级回执：解析成员表为空且本相位该目标尚无任何回执时，写一条目标级行（技能编号与版本都为空，与 add 行的目标级行同一形状），outcome 记 skipped_terminal；崩溃重放时回执守卫按目标查到既有回执即不再叠写，续跑账本与一次跑完逐字节相同；组里还有待定成员时走原有成员级路径一字不动，全部成员已被守卫跳过（早趟已有回执）的情形同样不写行级回执。注意点：pending_groups 只收待定成员，更早相位已接受的成员同样解析不出，所以这条行级回执实际覆盖"成员已终态（被丢弃）"与"成员已被更早的相位接受"两类，outcome 统一记 skipped_terminal（裁决指定的名字），语义是"本行无事可做、原样留账"。本修复不改任何技能状态的流转，只多一行账本记录；金矩阵四个 live 组合的末学习相位正是这个形状，按 generate.py 的既定流程全量重生成金基线并把差异落在同一提交（差异仅为该回执行、摘要的 outcomes 计数与 provenance 的 base commit，8 组合 × 3 个崩溃点续跑等价门全过）；E1–E6 对归档基线的机器披露报告随之只有 base_commit 一处变化、无新增差异（脚本逐键核对）。审计检查 8 对技能编号为空的目标级行本就放行，无需改动（--self-test 全过）。新增测试 tests/unit/test_w3e_terminal_row_receipt.py 七例：keep 与 delete 行级回执触发（含存储状态不变断言）、崩溃重放账本逐字节不变、早趟成员回执已覆盖时不叠写、待定成员仍走成员级且技能行不出现、全成员已回执不叠写、审计检查 8 接受该形状。验收：全套 2052 passed（2045 基线 + 7 新增）+ 3 deselected，test_layering 3 passed，ruff 全过。留给评审：revise 行指向已终态组时存在同类缺口（成员表空则零回执），本次裁决范围只含 keep/delete，未动；"skipped_terminal"这个名字也覆盖成员已被更早相位接受的组，若评审认为后者应另立 outcome 名（如 already_accepted 的行级形态），需另行裁决。

工件：`src/expedistill/stages/learn_tasks.py`（_terminal_group_receipt + keep/delete 接线）、`tests/unit/test_w3e_terminal_row_receipt.py`（新建 7 例）、`tests/fixtures/golden/`（8 组合重生成 + provenance，本条落地）、`tests/fixtures/golden_acceptance/wave4_e1_e6_disclosure.json`（base_commit 随金基线重生成，机器核对无新增差异）、`log.md`（本条）、本条对应 commit

## 2026-10-17 11:52 — 终态组行级回执补全到 revise 路径（W3-E 评审遗留裁决落地）

一段话摘要：按 W3-E 回执修复的 C 路轻量评审（docs/reviews/w3e_terminal_row_receipt_review_20261017_C_resume.md，通过、零阻断）的两点建议落地：裁决一，已终态组的行级回执统一用 skipped_terminal 不改名——行级没有可命名的单一成员，混合组下 already_accepted 会失实，审计对目标级行无额外收益，金矩阵现存实例恰全为已接受类、改名需重生成一轮；裁决二，revise 行的同类缺口（指向已终态组时零回执）独立提交补上——_land_revise 的空 actables 分支复用 _terminal_group_receipt，行为与 keep/delete 完全对称，该分支在任何证据或写作工作之前返回、测试无需模型句柄。评审偏移（单成员组"存后回执前崩溃"窗口会让重放写出语义偏差的行级回执）按评审建议在 helper 的 docstring 留痕，商店状态仍是事实正本；审计侧 WARN 交叉核对记为后续建议。新增测试两例（revise 行级触发、重放不写第二条），全套 2054 passed + 3 deselected，test_layering 与 ruff 通过，金矩阵无漂移（本次改动只影响零回执的边界形状，四个 live 组合的末相位不产生 revise-终态组行）。

工件：`src/expedistill/stages/learn_tasks.py`（_land_revise 接入 helper、docstring 窗口留痕）、`tests/unit/test_w3e_terminal_row_receipt.py`（+2 例）、`docs/reviews/w3e_terminal_row_receipt_review_20261017_C_resume.md`（评审报告）、本条对应 commit

## 2026-10-17 10:52 — 技能文件夹 M1：Skill 升 schema v3 加一句话摘要 summary，三条写作链路全量产出

一段话摘要：技能文件夹计划的第一个里程碑落地，只做写作侧：每条技能多一个一句话摘要字段 summary——将来目录文件里 skill_id 旁边的那一行，决定 agent 会不会点开这条技能。按对齐裁决（外层 Manager state/replies/mm_folder_align.md）执行：只加 summary 不加名字字段；v2 旧记录迁移时 summary 显式置空串（M2 的目录渲染对空摘要回退取 [When to use] 节，M1 不做回退）；提炼（G2 两个 prompt）、改写（G6 REWRITE）、修订（G11 LIVE_SKILL_REVISION）三条写作链路的输出契约都加 summary，且改写/修订后必须重写摘要（内容变了，旧目录行不再描述这条技能）。数据侧：SKILL_SCHEMA_VERSION 2→3，Skill 加 `summary: str = ""`（字段只在 models.py 定义，README 第四节 Skill 表同步一行）；v1→v2→v3 迁移链保持，v2 行经新增的 _migrate_v2_skill_row 显式置空（不落 dataclass 默认值的静默路径），v1 迁移函数同样显式补空串；migrate_skill_record/migrate_skill_rows 的 v2 行不再是"原样透传"而是迁移成 v3。校验侧：blocks/author.py 的 validate_skill_sections 对新契约要求 summary 非空、单行（\r 归一成 \n 后不许出现换行——目录行就是单行）、长度上限 SKILL_SUMMARY_MAX_CHARS=500（常量放 core，因为 blocks/author 与 blocks/attribution 都要用而 blocks 互不 import）；缺字段拒绝；G11 侧 blocks/attribution.py 镜像同一规则（FAILURE_CATEGORIES 先例），单测用同一批坏载荷驱动两侧钉死不漂移。落盘侧：rewrite/revision 的唯一落地点 learn_tasks._land_revision 把旧摘要随 rewrite_history 一起存（旧摘要 = "改写前 agent 在目录里看到什么"的证据），新摘要随 version 一起更新，store 漂移守卫同时比对摘要；K5 合并产物（composites/curation.py）的 summary 是确定性拼接——各来源非空摘要按 "; " 连接（MERGE prompt 输出纯文本、没有 summary 契约，选拼接因为实现最简单且语义诚实：合并保留了每条来源的建议，目录行就该把每条来源的能力都列上；不受 500 上限约束，因为没有模型写它；全空来源拼出 ""，与 v2 迁移语义一致），docstring 写清。金矩阵按纪律同一提交全量重生成（generate.py，8 组合 × 3 崩溃点续跑等价门全过）：差异逐处核对为且仅为——技能行加 summary 键、schema_version 2→3、四个改动 prompt 的 manifest digest、假模型脚本按新契约吐 summary 后的请求/响应字节与 token 估算、live 组合修订技能的 rewrite_history 条目加旧摘要键；E1–E6 对归档基线的机器披露报告随之重生成并逐键核对：无任何准则的红绿翻转，新增差异仅两类——E4 的 SKILL_DISTILL/LIVE_SKILL_REVISION 请求文本行（prompt 变了）、E1 的修订行（rewrite_history 加 summary 键，脚本验证除该键外逐字节相同）；provenance base_commit 仍为生成时的 HEAD 36aad02。验收：全套 2082 passed + 3 deselected（基线 2054 + 3，新增 28 例：summary 契约拒绝载荷 7+7+2 例、G2/G6/G11 产出摘要的端到端断言、v2→v3 迁移 5 例、rewrite_history 旧摘要 1 例、K5 合并摘要 2 场景 1 例、两侧校验器同一契约与模板契约钉 3 例），test_layering 3 passed，ruff E9/F 全过，audit_live_run --self-test 全过（技能迁移链被审计用到）。留给 M2 的接口：summary 空串 = "没有摘要"（v2 迁移行与全空来源合并产物都是这个形态），目录行渲染对空摘要回退取 content 的 [When to use] 节；渲染时摘要保证单行无换行、≤500 字符（模型写的有上限，K5 拼接的没有，M2 如需上限要自行截断或另行裁决）；目录行 = skill_id + summary（对齐裁决，无名字字段）。

工件：`src/expedistill/core/models.py`（schema v3 + SKILL_SUMMARY_MAX_CHARS + v2→v3 迁移）、`src/expedistill/prompts/author.py`（G2 两个 prompt + REWRITE 输出契约加 summary）、`src/expedistill/prompts/attribution.py`（LIVE_SKILL_REVISION 同）、`src/expedistill/blocks/author.py`（_validate_summary + write_skill/rewrite_skill/validate_skill_sections）、`src/expedistill/blocks/attribution.py`（G11 镜像校验 + revise_skill 返回 summary）、`src/expedistill/methods/author/write.py`（RevisionOutcome.summary）、`src/expedistill/stages/learn_tasks.py`（_land_revision 存旧摘要/更新新摘要）、`src/expedistill/composites/curation.py`（K5 合并产物 summary 拼接）、`src/expedistill/stages/assemble_reuse.py`（迁移报错文案不再写死 v2）、`src/expedistill/llm/fake_provider.py` 与 `tests/golden/harness.py`（假模型按新契约吐 summary）、`README.md`（Skill 字段表 + schema 历史一行）、`tests/fixtures/golden/`（8 组合 + provenance 重生成）、`tests/fixtures/golden_acceptance/wave4_e1_e6_disclosure.json`（披露重生成，逐键核对）、`tests/`（新增/更新 12 个测试文件）、`log.md`（本条）、本条对应 commit

## 2026-10-17 10:53 — 技能文件夹 M2：folder 交付方法落地（目录清单 + 组合层幂等物化 + C 相位加载期检查）

一段话摘要：技能文件夹计划第二个里程碑，只做交付侧：新交付方法 folder 把技能库整成"一个文件夹 + 一个目录文件"的清单——提示词里只放目录文本（固定使用规则头：先看目录、按需 cat、别一次读太多），目录每行是"技能编号 | 要 cat 的文件名 | 一行摘要"；摘要为空（v2 迁移行）回退取正文 [When to use] 节并单行化，无该节则显式占位 "(no summary)"，K5 拼接的超长摘要截断到 200 字符并显式加 " [truncated]" 标记（不静默）；文件名从 skill_id 确定性推导（[A-Za-z0-9._-] 之外全映射成 '_' 加 .md，与 phase_bank 标签同一安全字符集），重名或与 catalog.md 撞名响亮报错。交付驱动零磁盘（resolve 是纯函数，与 whole_bank 同冻结签名）；已接受口径与 whole_bank 完全一致（skills_injected = 已接受集的 bank_reference 摘要，unit_id 与复用/决策管道无缝连接，端到端测试钉死 make_unit_id/reuse_key/reusable_units）；清单放在每行 aux["skill_folder"] 里且自包含：bank_fingerprint（= phase_bank bank_summary 的库指纹，即全库 bank_reference，不发明新版本号）、catalog_filename、catalog_text（目录文件逐字节原文）、files[]（每条已接受技能的 skill_id/version/filename/四节 content 原文）——M3 执行侧拿单元行就能建文件夹，不需要再查库。空已接受集时 skills_block 与 catalog_text 都是空串（沿用 D9 空注入教训，不渲染只有头的空目录）。组合层新文件 composites/skill_folder.py 的 materialize_skill_folder(清单, 目标目录)：每条技能一个 markdown 文件（正文=content 原文）+ 目录文件（=catalog_text 原文）；幂等实现选"内容不变不重写"（字节相同跳过，不同则临时文件+rename 原子写），不做任何删除清理，文件名按安全字符集校验（组合层不得 import 方法层，故契约原地复述而非共享）；M2 只交函数与单测，接线执行路径归 M3。加载期检查进 parse_phases 清单第 8 条：folder 交付只在 test 角色（C 相位）执行相位合法，A/B/D 出现即 ConfigError 并在报错里写明合法角色 'test'（名字常量 FOLDER_DELIVERY_NAME 放 core，core 不得 import 方法注册表，沿用 EXECUTION_NAMES 的跨层字面量先例）；"只支持 tb"没有写成 core 的 benchmark 分支——交付对任何 benchmark 渲染同一目录文本，插件能力位与响亮报错归 M3 执行插件。金矩阵不受影响（folder 无生产调用方，E1-E6 披露无变化，金测试全绿零重生成）。验收：全套 2112 passed（基线 2082 + 30）+ 3 deselected，test_layering 3 passed，ruff 全过。留给 M3 的接口：aux 字段名与清单形状如上；materialize_skill_folder(mapping, target_dir) -> Path；执行插件建议声明"支持哪些执行版本"的能力位，folder 执行请求落到 searchqa/alfworld 时由插件层报 "this plugin does not support skill folders"；real_folder 配对校验同属 M3。

工件：`src/expedistill/methods/delivery/folder.py`（新建，提交 f5b64a0）、`src/expedistill/methods/delivery/__init__.py`（FolderParams）、`src/expedistill/methods/registry.py`（folder 条目）、`src/expedistill/core/config.py`（FOLDER_DELIVERY_NAME + 检查清单第 8 条）、`tests/unit/test_folder_m2_delivery.py`（新建 20 例）、`tests/unit/test_w1d_delivery_signature.py` 与 `tests/unit/methods/test_family_entries.py`（六交付名同步）、`src/expedistill/composites/skill_folder.py`（新建，幂等物化）、`tests/unit/test_folder_m2_materialize.py`（新建 10 例）、`log.md`（本条）、本条对应 commit

## 2026-10-17 17:35 — 技能文件夹 M3：real_folder 执行 + TB 沙箱接入 + 插件能力位

一段话摘要：技能文件夹计划第三个里程碑，执行侧与 TB 接入落地，三个提交。一（5afc6c1）执行族加第三个条目 real_folder（real 的文件夹孪生，参数类 RealFolderExecutionParams 镜像 real 的两个单臂字段 n_attempts/max_injection_tokens——后者对 folder 模式计的是目录文本的 token——无 arms，C 相位单臂弱模型）；parse_phases 检查清单第 8 条补上后半：folder 交付与 real_folder 执行在 test 角色相位上一对一配对，folder 配 plain real（会把目录文本当技能块注入，静默错实验）或 real_folder 配其他交付均 ConfigError；folder 相位的显式 n_attempts 照走既有对照深度上限。二（2e27b7d）插件接口加能力位：BenchmarkPlugin.skill_folder_prompt_name（None=不支持；名字兼作 folder prompt 的 B2 注册名，能力与 prompt 一起冻结不会漂移），tb 声明为 TB_TASK_PROMPT_FOLDER（其 SKILL FOLDER 段 = TB_SKILL_FOLDER_GUIDANCE 原文，即远端 skill agent 注入的 SKILL.md 内容；无 {skills_block} 槽，目录文本不可能进提示词），searchqa/alfworld 继承 None 一行未碰；E1 边界响亮拒绝——folder 单元（清单在 task.meta）落到不声明能力的插件报 ConfigError（消息写明该插件不支持技能文件夹、当前只有 tb 支持）；stage0 冻结把声明名 digest 进 manifest 且拒绝未注册名。三（本提交）TB 沙箱接入与阶段层物化：沙箱链路复用既有 skills 模式机制零远端改动——远端 TencentTaskSkillE2BEnvironment 本来就把 <TBENCH_SKILL_ROOT>/<run_id>/<task>/ 整目录递归上传进沙箱挂到 /opt/openclaw-skills/<task>/（要求 SKILL.md 存在并注入远端提示词），folder 单元只是换了乘客：SKILL.md 载指引段，catalog.md 与清单各技能文件随同一目录进沙箱，带点任务名两拼写目录照旧双传；resume 条件摘要对 folder 单元折叠指引段+库指纹+目录文本（指引段本身是常量，单靠它分不出换了库的同 run id）；usage 行记 folder prompt 名。阶段层（final_test，唯一能见到 folder 行的驱动）派发前把清单经 materialize_skill_folder(清单, bank, 相位目录/skill_folder) 物化一次（所有行共享同一清单，一次物化服务全部单元，幂等可续跑），_plan_units 把 (清单, 物化目录) 挂到恰好带清单的行的 Task.meta——对照臂裸行不受影响；空已接受集（files 为空）不挂不物化、单元照裸跑（D9 空注入教训在 folder 边界的对称执行）；执行不拦截不设预算：会话照旧完整记录，cat 命令原文留在轨迹里供 M4 解析（测试留了真实样例）。三处既有 TB reload 测试同步弹出新 prompt，prompt 冻结静态钉允许声明能力的包多注册恰好一个 prompt。验收：全套 2150 passed + 4 deselected（基线 2116 + 新增 34 例：配对 12、能力位 8、prompt 5、沙箱 5+1 live 冒烟、阶段接线 4），test_layering 3 passed，ruff 全过；金矩阵不受影响（real_folder 无生产调用方、不进 8 组合、零重生成，folder prompt 只在 tb 插件 import 后注册、fakebench 冻结面不变）。偏离记录：能力位选了 skill_folder_prompt_name 而非裸 bool（管理者已批）；物化按"相位目录一次"而非逐单元（清单是相位级共享数据，逐单元只是复制同字节，接线测试钉死共享清单与幂等）。留给 M4 的接口：轨迹里 cat 命令的确切形态样例见 test_folder_m3_sandbox._CAT_TRAJECTORY——action 渲染为 bash_command({"keystrokes": "cat /opt/openclaw-skills/<task>/<文件名>\n", ...})，观测为目录/技能正文；M4 按文件名对账 aux["skill_folder"]["files"]（沙箱路径 /opt/openclaw-skills/<task>/ 与远端挂载同名目录）。

工件：`src/expedistill/methods/execution/__init__.py`（RealFolderExecutionParams）、`src/expedistill/methods/registry.py`（real_folder 条目）、`src/expedistill/core/config.py`（FOLDER_EXECUTION_NAME + 配对检查）、`src/expedistill/stages/phase_execute/resolve.py`（docstring）、`src/expedistill/benchmarks/base.py`（skill_folder_prompt_name 能力位）、`src/expedistill/blocks/execute.py`（E1 守卫）、`src/expedistill/stages/stage0_freeze.py`（folder prompt digest + 注册校验）、`src/expedistill/benchmarks/tb/plugin.py`（指引段 + TB_TASK_PROMPT_FOLDER + folder 沙箱链路 + 条件摘要）、`src/expedistill/stages/phase_execute/engine.py`（_plan_units 挂清单）、`src/expedistill/stages/phase_execute/final_test.py`（_materialize_folder）、`AGENTS.md`（§7 插件契约与目录树三行）、`tests/unit/test_folder_m3_pairing.py`（新建 12 例）、`tests/unit/test_folder_m3_capability.py`（新建 8 例）、`tests/unit/test_folder_m3_prompt.py`（新建 5 例）、`tests/unit/test_folder_m3_sandbox.py`（新建 5 例 + 1 live 冒烟）、`tests/unit/test_folder_m3_wiring.py`（新建 4 例）、`tests/unit/methods/test_family_entries.py`、`tests/unit/test_folder_m2_delivery.py`、`tests/unit/prompts/test_freeze.py`、`tests/unit/benchmarks/test_tb_plugin.py`、`tests/unit/test_w2b_phase_learn.py`、`tests/unit/test_w3s_segmentation_wiring.py`（既有测试同步）、`log.md`（本条）、commit 5afc6c1 / 2e27b7d / 本条对应 commit

## 2026-10-17 11:30 — 更正：上一条 M3 条目的时间写错

一段话摘要：更正上一条（技能文件夹 M3）的时间戳——写成 17:35，实际完成时刻约 11:29（本机时间）。内容无错，只改时间记载；按 log.md 规则不改已写条目，追加本条更正。

工件：`log.md`（本条）、本条对应 commit

## 2026-10-17 11:42 — 技能文件夹 M4：验收读取统计（事后轨迹解析 + C 相位摘要落盘 + 泄题审计勘察）

一段话摘要：技能文件夹计划第四个里程碑，验收侧两件事。一（61d962e）读取解析纯函数 composites/skill_reads.py 的 skill_files_read(轨迹, 清单)：对清单每个 filename 扫描轨迹的 action 文本，cat 命令引用该文件名（按安全字符集做定界 token 匹配，长名包含短名不算）即记一次读取；首读序、同文件去重、纯函数只读轨迹（崩溃续跑天然等价）。刻意 benchmark 盲：不匹配 /opt/openclaw-skills 这类挂载路径前缀（那是 benchmark 知识，验收层不许出现），代价两条已在 docstring 披露并由测试钉住——别的目录下同名文件的 cat 会被误记、只有 cat 算读取（grep/head 不算，目录文件本身不在清单里永不计入）。清单条目缺 skill_id/filename 或文件名撞名是 CodeBugError（交付层本来就拒，这里是接线 bug 的响亮信号）。二（本提交）落盘点现场改裁并留痕：任务书建议写进"验收报告行的 artifacts"，但勘察确认 folder 单元只存在于 C 相位，而冻结规则是 C 相位不写 acceptance_report（测试结果不流进学习，数字只进 phase_summary），报告读取器（reports.py 的 baseline/exam/live 三类）根本见不到 folder 轨迹——把读取集挂到报告行等于死代码，为它给 C 相位发明报告则违反冻结规则。故落盘点选 C 相位摘要 phase_summary.json 的新键 skill_files_read：{trajectory_id: [skill_id]}，只盖 with_bank 臂的存储单元（对照臂按构造裸跑、无 folder 可读，docstring 写明），folder 交付的相位才有该键（旧执行版本 whole_bank/retrieval/none 的摘要不含），空已接受集的 folder 相位每单元报空列表而非缺键。final_test 派发后用交付行自己的清单（M3 裁决：全相位共享一份）逐轨迹解析，摘要每趟重算，续跑重放同一批存储轨迹得同一结果（测试钉死）。泄题审计勘察结论：D8 判定（composites/acceptance_leak.py）唯一钩子在 A 相位考试管道、按 (skill_id, version) 审技能正文，C 相位 live/final-test 单元没有任何既有泄题钩子——按对齐裁决不发明新判定，读取集作为披露落摘要供事后人工审计，既有 per-(skill, version) 覆盖对 folder 单元原样沿用（写进 skill_reads.py docstring）。决策输入形状断言：C 相位不写报告 + decide_task_list 对 test 类报告双侧 FinalTestReportRejected，读取集的唯一载体结构上进不了决策（测试钉死）。验收：全套 2166 passed + 4 deselected（基线 2150 + 新增 16：解析 11、落盘 5），test_layering 3 passed，ruff 全过；金矩阵零改动零重生成（folder 不进 8 组合，旧执行相位摘要不含新键，金测试全绿）。AGENTS.md 目录树补 skill_folder.py（M2 遗漏）与 skill_reads.py 两行。留给 M6 的注意点：审计脚本消费读取集时按 with_bank 臂的 trajectory_id 对账（臂内 trajectory id 与对照臂同形，靠臂目录区分）；读取集是事实不是判定，泄题人工审计对 folder 单元只能靠它 + A 相位既有覆盖，没有自动化判定。

工件：`src/expedistill/composites/skill_reads.py`（新建，解析纯函数）、`src/expedistill/stages/phase_execute/final_test.py`（_folder_read_sets + 摘要新键 skill_files_read）、`tests/unit/test_folder_m4_reads.py`（新建 11 例）、`tests/unit/test_folder_m4_summary.py`（新建 5 例）、`AGENTS.md`（目录树两行）、`log.md`（本条）、commit 61d962e / 本条对应 commit

## 2026-10-17 11:42 — 技能文件夹 M5：文档同步（README、AGENTS.md、folder 配置样例、总览 v2）

一段话摘要：技能文件夹计划第五个里程碑，只改文档与配置、代码一行不动。README（设计正本）按 M1–M3 已提交的代码逐处核实后同步：新增 2.6 节记录方法组合计数（主轴 3×2×2×3=36 种——C 相位交付 whole_bank/retrieval/folder 三选一，配置形态上限 144，folder 目前只有 tb 的插件经能力位 skill_folder_prompt_name 声明支持）；2.1 字段表的交付/执行两行加 folder 与 real_folder；2.3 加载期检查清单补代码里已有的第 8 条（folder 交付只在 test 角色、与 real_folder 执行一对一配对）；2.4 加 folder 版 C 相位配置片段并把 v4 配置计数 7 改 8；3.4 组合积木表加 materialize_skill_folder（清单 + 指纹闸门 + 幂等物化）；3.5 test 相位行与 3.6 prompt 表（TB_TASK_PROMPT_FOLDER 一行、冻结面说明加 folder prompt）、5.1 冻结面同步；4.1 Skill 空摘要回退补 [Trigger] 节（修订一处既有文档与代码不一致：README 只写了 [When to use]，代码对失败参考型技能对称回退 [Trigger]）；4.3 相位目录布局加 skill_folder/；5.5 终测相位补 folder 一段（目录清单无正文、catalog.md 行格式与使用规则、派发前相位目录一次物化全部单元共享、TB 零远端改动随 skills 上传链进沙箱、指引段提示词无 {skills_block} 槽目录文本不进提示词、E1 能力位拒绝、空库裸跑、max_injection_tokens 计目录文本）。读取统计（M4）未写进 README：其验收层代码不在本分支 HEAD（另一代理进行中），设计正本只写已实现行为，待 M4 合入后补。AGENTS.md 目录树补两行：methods/delivery/folder.py、composites/skill_folder.py（能力位与第七节契约 M3 已写，无需再动）。新配置 configs/experiments/tb21_split_folder_v4.yaml：以 tb21_split_live_run_v3_v4.yaml 为底、C 相位 delivery 换 {name: folder}、execution 换 {name: real_folder}，其余字段不动；实际用 load_config 加载并用 methods.registry.resolve_params 解析 C 相位两个方法族验证通过（FolderParams() / RealFolderExecutionParams(n_attempts=None, max_injection_tokens=None)）。对外总览新建 v2（外层 ~/Documents/Manager/state/replies/mm_system_overview_v2.md，v1 已被管理者取走）：交付族表加 folder（6 种）、执行族表加 real_folder（3 种）与配对检查说明、第六节组合数 24→36（可跑 28：folder 的 4 种只在 tb）、96→144、72→108、第四节加 Skill summary 数据对象说明、新增 folder 玩法三段白话说明、执行单元行 aux 注明 folder 清单。验收：全套 2150 passed + 4 deselected，test_layering 3 passed，ruff 全过。

工件：`README.md`（2.1/2.3/2.4/2.6/3.4/3.5/3.6/4.1/4.3/5.1/5.5 与 1.2 表）、`AGENTS.md`（目录树两行）、`configs/experiments/tb21_split_folder_v4.yaml`（新建）、`log.md`（本条）、`~/Documents/Manager/state/replies/mm_system_overview_v2.md`（新建，仓库外）、本条对应 commit

## 2026-10-17 15:40 — M2 修订（用户裁决）：清单去正文 + 回退扩展 [Trigger]

一段话摘要：用户否决 M2 的"清单自带技能正文"偏离——渐进式披露是文件夹玩法的核心设计，任何环节不得变成"换个地方全量塞"。修订一：aux["skill_folder"] 的 files 条目删 content 字段，只留 skill_id/version/filename 名字映射；技能正文的唯一来源是相位技能库，组合层 materialize_skill_folder 的签名从 (清单, 目标目录) 改为 (清单, bank, 目标目录)——先按清单的 bank_fingerprint 与 bank 的 bank_reference 比对（库在清单脚下移动即 CodeBugError），再按 skill_id + version 从 bank 读正文写文件（版本漂移、清单有 bank 没有的技能均响亮拒绝）；幂等形态不变（内容不变不重写，inode 断言保留）。修订二：空摘要回退对称扩展到失败参考型技能的 [Trigger] 节（用户指令；此前只回退 [When to use]，[Trigger] 与它对两类技能做出同种"何时适用"声明）。M3 代理已在修订开工前暂停，无返工。测试：交付侧断言清单无 content + [Trigger] 回退两例；物化侧重写为 (清单+bank) 签名并新增指纹不匹配、版本漂移、清单-库不一致三个拒绝例，全量 2116 passed + 3 deselected，分层与 ruff 通过，金矩阵不受影响（folder 无生产调用方）。（条目位置注记：本条 2026-10-17 由 M6 评审修复按时间顺序规则从 M3 条目之前移到此处，原文一字未改。）

工件：`src/expedistill/methods/delivery/folder.py`（清单形状、回退扩展、docstring 重写渐进式披露依据）、`src/expedistill/composites/skill_folder.py`（签名与指纹闸门重写）、`tests/unit/test_folder_m2_delivery.py`、`tests/unit/test_folder_m2_materialize.py`（重写+新增五例）、本条对应 commit

## 2026-10-17 18:20 — M5 合并 + M4 读取统计的 README 段补齐（技能文件夹收口前）

一段话摘要：folder/m5 文档分支（156e5a8）合入主分支（log.md 追加冲突按双保留解决），随后补上 M5 如实留下的顺序性缺口——M4 的读取统计落地后，README 的 folder 终测段补齐事后读取解析的说明：composites/skill_reads.py 纯函数按清单文件名词边界匹配（首读序去重、只读轨迹零落盘），每单元读取集写进 C 相位摘要的 skill_files_read 键（只盖带技能臂；folder 相位才有键、空已接受集报空列表、旧执行相位无键），已知局限与"读取集不进任何决策输入、供事后审计"一并写明。至此 M1-M5 全部落地：全量 2166 passed + 4 deselected，分层与 ruff 通过，金矩阵零改动。剩 M6 三路评审。

工件：`README.md`（folder 段读取统计补齐）、merge commit `b1ec37b`（folder/m5 合入）、本条对应 commit

## 2026-10-17 12:06 — 技能文件夹 M6：三路评审修复一次收口（死参数删除、相位级能力守卫、谓词统一、读取键口径补全、中段续跑测试）

一段话摘要：按技能文件夹三路评审（docs/reviews/skill_folder_review_20261017_{A_correctness,B_simplicity,C_resume}.md）与协调者裁决一次收口九项。一、RealFolderExecutionParams 的 n_attempts 与 max_injection_tokens 是死参数——唯一合法的 C 相位一个都不消费，README 却声称注入上限计目录文本 token——按裁决删参数，类变空参数类（与 ExamExecutionParams 同形），配置写这两个键在 stage 0 的未知键检查响亮失败，README 2.1/5.5 改为"real_folder 无执行参数，C 相位采样深度沿用全局 n_attempts_weak、与旧 C 相位一致"，M3 参数测试同步，不为让参数"生效"发明任何新逻辑。二、E1 空库空隙（folder 交付 + 不支持插件 + 空已接受集会静默裸跑整相位）：final_test 派发前加相位级能力守卫——交付是 folder 而插件 skill_folder_prompt_name 为 None 即 ConfigError，与库空不空无关；逐单元 E1 守卫保留作第二道，README 2.6/5.5 与 AGENTS.md 第七节的拒绝句改为相位级口径；新测试钉"空库 + 无能力位插件在派发前停机、plugin.calls 为空"。三、skill_files_read 键的口径在 README 5.5 写全（评审 A-5/A-6）：键只盖带技能臂、对照臂不进且轨迹 ID 与对照臂同形（审计对账配合臂目录）；三态分辨——键缺=旧执行相位或单元没跑成/已接受空洞（须交叉摘要 infra_missing/infra_accepted），空列表=跑了没读，非空=实际读过。四、谓词统一（评审 B-3、C 细节 4）：engine.py 抽 _folder_manifest_of 作"这行单元带清单"的唯一判断，_plan_units 与 final_test 的物化/读取集三处共用，真值与 is not None 的分歧一并统一（空库行为不变：不物化不挂清单、摘要仍报空读取集）。五、安全字符集镜像测试（评审 B-6）：delivery 的 _SAFE_CHARS、skill_folder 的 _FILENAME_RE、skill_reads 的 _NAME_ALPHABET 三处复述用一条测试绑定相等（冒号/斜杠/点/制表/中文等边界字符过三个入口，另带一个混合脏 id 端到端走派生-物化-解析），一处放宽测试期即红。六、过时文本清理：skill_folder.py 的"M3 再接线"过渡句改陈述事实、final_test 的"scripts/audit consumption lands later"无主前瞻删、README"不渲染只剩头的空目录"改通顺、"供事后审计"去重、v4 配置计数 8 改 9（实测 9 份）、AGENTS.md 目录树 skill_folder.py 重复两行删一条（保留 phase_bank.py 之后、与 skill_reads.py 相邻那条）。七、folder C 相位真·中段续跑测试（评审 C-2）：带库臂与对照臂各先落 1/3 单元的存储轨迹后"续跑"，插件调用记录证明已存单元未重跑（只有 t2/t3 进插件），最终摘要与一次跑完逐字节相同（含 skill_files_read）——补上 M4 幂等测试分不出"复用"与"重跑"的缺口。八、log.md 修复（评审 A 细节 D7）：M2 修订条目（15:40）按时间移到 M5 条目之后（M3 实际 11:29 完成，原位置顺序倒挂；移动属对已写条目的例外，条目内加了一行位置注记），M4 工件行与 M5 标题之间补空行。留痕不修项（协调者裁决）：物化目录"两个版本的库同存"的窄窗口在 composites/skill_folder.py 模块 docstring 记录（指纹闸门现行接线不可达、沙箱上传按清单名走、审计按清单读目录）；bank_reference 指纹不含 summary 字段的已知边缘在 inject/skills.py 的 bank_reference docstring 记录（summary 变化当前必伴 version/content 变化，靠构造成立无机制保证）。验收：全套 2169 passed + 4 deselected（基线 2166 + 新增 3 例），test_layering 3 passed，ruff 全过，金矩阵零改动（real_folder 不进 8 组合）。

工件：`src/expedistill/methods/execution/__init__.py`（RealFolderExecutionParams 清空）、`src/expedistill/stages/phase_execute/final_test.py`（_require_folder_capability + 谓词统一 + docstring）、`src/expedistill/stages/phase_execute/engine.py`（_folder_manifest_of）、`src/expedistill/blocks/execute.py`（守卫注释第二道口径）、`src/expedistill/benchmarks/base.py`（能力位 docstring）、`src/expedistill/composites/skill_folder.py`（Wiring 句 + C-1 留痕）、`src/expedistill/inject/skills.py`（指纹 summary 边缘留痕）、`README.md`（2.1/2.4/2.6/5.5）、`AGENTS.md`（目录树去重 + 第七节拒绝句）、`tests/unit/test_folder_m3_pairing.py`（参数测试同步）、`tests/unit/test_folder_m3_wiring.py`（+1 空库守卫例）、`tests/unit/test_folder_m2_materialize.py`（+1 字符集镜像例）、`tests/unit/test_folder_m6_resume.py`（新建 1 例）、`log.md`（M2 修订条目重排 + 补空行 + 本条）、本条对应 commit

## 2026-10-17 13:17 — 外部 campaign 对照机制落地（v4 的 control_source: external_campaign）

一段话摘要：为"复用旧技能库只跑 folder 版 C 相位"实验落地对照机制：C 相位 compare 的 against 加第三个取值 external_campaign——对照臂读外部 AgentBrew campaign 的冻结成绩文件、零执行，是旧系统 control_source=external_campaign 裁决（2026-10-16，裸跑对照臂永不重跑、读 node29 归档）的 v4 对应物。设计取舍记 docs/decisions/external_campaign_control_20261017.md：compare 加必填 external 块（path/sha256/model_key 三键，相对路径按 tasks 同规则解析成绝对路径冻结），sha256 钉住成绩文件字节防漂移（stage 0 冻结步校验存在+校验和，C 相位派发前再校验任务覆盖、model_key 覆盖、奖励数≥对齐深度、奖励值 0/1，全部在任何带库臂花费之前）；对照口径预登记为每任务前 n_attempts_weak 次采样奖励的均值（campaign 归档 5 次 vs 实验对齐深度 3，与旧外部对照报告"前 3 次"列同口径），对照无 infra 概念；C 相位驱动对照臂解析为外部成绩而非轨迹仓读取，不建对照仓、不派发，成绩文件字节拷进相位目录 external_control.json，摘要带 external_control 审计记录（钉住的路径/sha256/model_key/对齐深度/每任务实际用到的奖励向量）；control_source 词表加 external_campaign（沿用旧拼写，decision.control_arm_source 加显式开关）；external_campaign 只允许 test 角色相位，live 相位的对照必须是 baseline 相位的检查对它同样拒绝。成绩数据 configs/seed/tb21_agentbrew_campaign_summary.json（TB-dev configs/seed/summary.json 的字节级拷贝，AgentBrew 最终归档 89 任务×5 次×两模型，sha256 1b5c483c…）；tb21_split_folder_v4.yaml 的 C 相位切到 external_campaign，并把 L.curate.token_budget 从 96000 改回 null 对齐源 run 冻结值（manifest 里 bank_token_budget 为 null，96000 是从转换配置继承的、与源 run 不符的值，不改会在装配身份比对触发无谓拒绝）。audit 的 check 9 对 external 对照不再找对照单元，改为校验相位目录成绩文件拷贝的 sha256（漂移/缺失都是 ERROR）。冻结 manifest 的 compare 形状多 external 键，金矩阵按规范全量重生成随本提交落地（diff 仅为 compare.external: null 与 provenance 基提交号，wave4 披露报告的 golden_matrix_base_commit 同步为当前 HEAD）。测试：新增配置解析 11 例（test 角色限定、三键必填、sha256 形状、非 external 的 against 带 external 块拒绝、live 拒绝、相对路径解析）、加载器 10 例（对齐切片、缺文件、sha 不符、任务/model_key 不覆盖、奖励不足、非 0/1 奖励、坏 JSON、审计记录）、C 相位接线 2 例（零执行+摘要口径、文件漂移在派发前拒绝）、stage0 冻结校验 1 例、audit 3 例（跳单元+钉字节、漂移 ERROR、缺失 ERROR）、decision 词表扩展。验收：全套 2199 passed + 4 deselected（基线 2169+4），test_layering 3 passed，ruff 全过。

工件：`src/expedistill/core/config.py`（EXTERNAL_CAMPAIGN 常量、ExternalControlSpec、compare 解析、检查清单 4 与 live 检查、load_config 路径解析）、`src/expedistill/composites/external_control.py`（新建）、`src/expedistill/composites/decision.py`（control_arm_source）、`src/expedistill/stages/phase_execute/final_test.py`（对照臂接线）、`src/expedistill/stages/stage0_freeze.py`（冻结步校验）、`scripts/audit/audit_live_run.py`（check 9）、`configs/seed/tb21_agentbrew_campaign_summary.json`（新建）、`configs/experiments/tb21_split_folder_v4.yaml`（C 对照与 token_budget）、`README.md`（2.1 compare 行、2.3 清单 4、2.6、5.5 对照段）、`AGENTS.md`（configs/seed 与 composites 目录树）、`docs/decisions/external_campaign_control_20261017.md`、`tests/fixtures/golden/*`（重生成）、`tests/fixtures/golden_acceptance/wave4_e1_e6_disclosure.json`（基提交号）、`tests/unit/core/test_config.py`、`tests/composites/test_external_control.py`（新建）、`tests/unit/test_w2a_phase_execute.py`、`tests/unit/stages/test_stage0.py`、`tests/unit/test_w3b_audit_v4.py`、`tests/unit/test_w1c_decision_companions.py`、本条对应 commit

## 2026-10-17 13:17 — 装配器身份白名单补终测执行名叶子（folder 复用的强制孪生）

一段话摘要：装配身份比对（ruling 4）原本只放行终测相位的 delivery 与 compare，但加载期第 8 条检查强制 folder 交付与 real_folder 执行一对一配对——执行名是交付名的强制孪生，目标配置根本不能自由选择它；只放行 delivery 不放行其孪生，会让"retrieval 源 run → folder 目标"的复用装配在 config.phases.C.execution.name 上被拒（静态干跑实测确认这是本实验唯一剩余拒绝面）。裁决：白名单加 config.phases.<终测名>.execution.name 叶子，且只有名字——n_attempts、max_injection_tokens 等改变测量口径的叶子仍然拒绝（新增测试钉住：folder+real_folder 目标装配通过且 allowed_identity_diffs 报出 delivery 与 execution.name 两节；给终测执行加 n_attempts 仍拒绝）。测试 49 passed，全套 2199+4 全绿。

工件：`src/expedistill/stages/assemble_reuse.py`（_allowed_diff_prefixes 与模块 docstring）、`tests/unit/stages/test_assemble_reuse.py`（+2 例）、本条对应 commit

## 2026-10-17 14:05 — 整理预算恢复"无护栏"拼写（2026-10-16 用户裁决的 v4 对应物）

一段话摘要：folder 版 C 实验的目标配置要把整理 token_budget 与源 run 冻结值逐字对齐（复用裁决第 6 条），但源 run（tb21_split_live_run_20261015_0045）冻结的是 bank_token_budget: null——2026-10-16 用户裁决"token 上限退出预登记参数集，None = 无护栏"的值，而 v4 的整理参数契约只收正整数，null 无法加载，逐字对齐在 v4 世界无拼法。按迁移规范（AGENTS 十.2）把旧世界的 None 语义完整搬回：AgentBrewParams / NoneParams 的 token_budget 改 Optional[int]（None = 阀永不关闭，正值照旧闸门），composites/curation.py 恢复旧世界的 _within_budget 帮助函数（None 时轮停判定恒真、终检永不触发），none 方法的 write_as_is 同样处理。行为影响：null 预算下整理轮在第一轮后停（轮停判定恒真，与源 run 当年实际行为一致）；预置库短路路径本就不经预算阀。测试 +4：参数契约 None/0/负数/字符串、无护栏轮停一轮后 break 且终检不触发、none 方法 null 预算写超大库。README 2.1 整理行补 null 拼法说明。验收：全套 2202 passed + 4 deselected，ruff 全过。

工件：`src/expedistill/methods/curation/__init__.py`、`src/expedistill/methods/curation/none.py`、`src/expedistill/composites/curation.py`（_within_budget 移植）、`README.md`（2.1）、`tests/unit/methods/test_params.py`、`tests/unit/methods/test_curation_none.py`、`tests/composites/test_curation.py`、本条对应 commit

## 2026-10-17 14:05 — 首学习相位回退读 run 根冻结库（装配 run 零调用前提的缺口修复）

一段话摘要：folder 版 C 实验的装配干跑（--until-phase 5 + 拒答死端点，任何真实模型调用即失败）暴露：第一个学习相位按"弱失败且库无技能"给 30 个训练任务发了 add 行，29 个被 store 既存技能挡住，但 custom-memory-heap-crash（弱臂 3 成 2、源 run 从未为它产出技能）真实触发了切段与 SKILL_DISTILL 调用——放任下去 B 相位要为这条新技能真实跑 3 次远端测量、终测库可能长成 142 条，实验前提（D/L/B 零调用、C 测冻结 141 条库）失效。根因：_previous_bank 对首学习相位恒返回空库，而装配文档明写的"pre-learn bank fallback"只在交付侧（_bank_skills）实现过。裁决（docs/decisions/prelearn_bank_fallback_20261017.md）：首学习相位没有上一工件时回退读 run 根冻结库——从零 run 此时尚无 run 根库，行为不变；装配 run 的预置 141 条库成为决策输入，add 行 30 → 13 且全部 skipped_existing 零调用，预置库保持冻结；库未锚定覆盖的任务照发 add（回退收窄不挡路）。端到端装配测试期望更新为零行零回执，w2b 新增"覆盖→不发/未覆盖→照发"两例。验收：全套 2203 passed + 4 deselected，ruff 全过；对本次实验实测：首学习相位 add 13 行全 skipped_existing，前段零调用。

工件：`src/expedistill/stages/phase_learn.py`（_previous_bank 回退）、`docs/decisions/prelearn_bank_fallback_20261017.md`、`README.md`（5.6）、`tests/unit/test_w2b_phase_learn.py`（+1 例双断言）、`tests/unit/stages/test_assemble_reuse.py`（端到端期望更新）、本条对应 commit

## 2026-10-17 14:20 — 空任务映射短路，B 相位空待定组宇宙不再碰插件

一段话摘要：零调用冒烟推进到 p03:B 时暴露第三个缺口：装配 run 全库已接受、无任何待定组，B 相位的任务集合为空，_prefixed_task_map 把空列表喂给 TB 插件、被"TB task list is empty"拒绝——这是管道细节（空集→空映射），不是语义守卫（"无组可测"是合法完成态，与"空库是合法库"同理）。修复：_prefixed_task_map 对空列表直接返回空映射、不调用插件；B 相位空宇宙走完空 units + 空报告正常完成。新增 w2a 测试钉住：空待定组宇宙的 live 相位零执行、空单元、空报告、正常完成。修复后冒烟全通：p00–p06 全部零调用完成（拒答死端点验证，任何模型调用即失败），p02 的 13 条 add 全部 skipped_existing，p06 预置工件与 run 根库逐字节相同，库指纹全程 bank:a340288b1562，llm_requests 零文件。验收：全套 2204 passed + 4 deselected，ruff 全过。

工件：`src/expedistill/stages/phase_execute/engine.py`（_prefixed_task_map 空列表短路）、`tests/unit/test_w2a_phase_execute.py`（+1 例）、本条对应 commit

## 2026-10-17 15:05 — 空宇宙 B 相位留账本足迹，审计按相位完成摘要区分空宇宙与丢账本

一段话摘要：装配 run 的完整性审计暴露第四个缺口：空待定组宇宙的 B 相位不写任何 live_run 账本（账本在首次实测组时才惰性创建），audit 的布局检查按"序列带 live 相位但无 live_run 子树"报 ERROR。两侧同修：live 相位驱动在进入时即创建 stage4_acceptance/live_run/ 目录足迹（空树是"跑过且无可测"的诚实记录，ledger_path 进 live_measurement 导出面）；audit 的布局检查细化——每个 live 相位都有完成摘要且报告行为零（空宇宙形态）才降级为 INFO，任一 live 相位有报告行却没有账本（账本被删）仍然是 ERROR。新增断言：空宇宙 live 相位留下目录足迹。顺带把 scripts/run/launch_tb21_live_run.py 的配置名参数化（默认不变，folder 实验传 tb21_split_folder_v4）。验收：全套 2204 passed + 4 deselected，ruff 全过；装配 run 当前审计 0 error / 2 info（p07 未跑 + 空宇宙说明）。

工件：`src/expedistill/stages/phase_execute/live.py`（账本足迹）、`src/expedistill/composites/live_measurement.py`（导出 ledger_path）、`scripts/audit/audit_live_run.py`（_live_phases_all_complete）、`scripts/run/launch_tb21_live_run.py`（配置参数化）、`tests/unit/test_w2a_phase_execute.py`（足迹断言）、本条对应 commit

## 2026-10-18 16:55 — 装配 run 的组终态审计降级为出处说明(小样本验证后启动全量)

一段话摘要:folder 版 C 的小样本(2 任务 × 3)全链路干净后启动了全量 84 trial(64 workers / stagger 20 / llm-timeout 1500,detached 驱动 + 监督者按 pidfile/产物文件判活,断链自动等待重连续跑,不重启损失)。小样本验证了:技能文件夹上传沙箱(141 文件 + 目录)、agent 真实按目录渐进 cat(6 条轨迹 1 条读了 1 个技能文件,skill_files_read 与轨迹 cat 命令逐字对上)、外部对照机制(sha256 审计记录、delta 正确);期间一次 VPN 中断造成 6 单元全 infra 失败,续跑零损失恢复,属网络事件非代码问题。小样本审计 0 error,但 19 条 WARN 都是装配 run 固有形态:store 的组终态来自源 run 拷贝,本 run 无裁决它的账本。审计细化:ASSEMBLED_FROM.md 存在且本 run 无任何裁决行/删除回执时,这组 WARN 降级为 INFO(出处=源 run 的账本);非装配 run 同形态仍是 WARN(真漂移)。新增两例单测钉住两侧。验收:全套 2206 passed + 4 deselected,ruff 全过;小样本 run 审计 0 error / 0 warn / 19 info。

工件:`scripts/audit/audit_live_run.py`（check_group_terminals_v4 的 assembled 参数）、`tests/unit/test_w3b_audit_v4.py`（+2 例）、`runs/tb21_split_folder_smoke2_20261017_1729`（小样本 run，保留作证据）、`tmp/supervise_folder_run.py`（一次性监督者，不进 git）、本条对应 commit


## 2026-10-18 16:58 — 清理装配工具 assemble_reuse.py 的错误与冗余内容，删除装配校验和

一段话摘要：按用户要求对 stages/assemble_reuse.py 做内容清理，不拆分文件，文件从 1760 行降到 1257 行。用户决定删除装配产生的全部 sha256 校验和（返回值的 checksums 字段与 ASSEMBLED_FROM.md 的校验和列），依据是行为规范禁止给数据加哈希值和校验和；逐类计数保留。删除的错误内容：ASSEMBLED_FROM.md 无条件写入的 AgentBrew 划分来源（seed 42、41/28/20、revision 2fd12b8），换一个划分装配时会与实际数量矛盾；同一性校验说明漏写后来进白名单的 execution.name；模块说明里"写成 composite 而非脚本""composites 不能 import stages 所以复制"等移层之前的过时理由。删除的冗余：从未被调用的 _canonical；与 store.write_jsonl 相同的 _write_jsonl_atomic；与 stage0 重复的任务加载规则（stage0_freeze._tasks_spec 改为公开函数 tasks_spec 供装配复用）；两个档位各写一遍的加锁与锁内 manifest 复查（合并为 _locked_fresh_run_dir）；复制前后各做一次的"已接受集合等于库内已接受行"检查与"库过滤与拷贝集一致"检查、对按 train 条件筛出的轨迹和切段再查一遍 train 的泄题检查（这些检查的输入在两次之间没有变化，第二次必然通过）；库行的保留改为直接看同 id 的存储 skill 是否保留。与仓库其他模块重复的辅助函数改为共用：键路径比较合并进 infra/manifest.py 的公开函数 differing_keys（参数 mark_one_sided 保留冻结报错的原有文字）；对象文件名编码解码提成 infra/store.py 的 object_filename 与 object_id_of_file，Store 自身也改用它们；shortfall 记录的读写与路径改用 composites/shortfall.py（顺带获得它对格式错误记录的检查）；segmentation_audit 路径改用 segment_checked.AUDIT_PATH。管理者会话裁决后，同类重复 composites/live_measurement.py 的 _read_stage1_accepted 一并改用 shortfall.stage1_accepted_unit_ids，它注释里的分层理由早已不成立。库指纹、金矩阵与 scripts/audit 的审计检查均未触碰。另发现新目录 ExpeDistillation-MM-dev 的 .venv 可编辑安装仍指向旧目录 src，已只改本目录 .venv 的 .pth，旧目录与其上正在运行的实验不受影响。验收：全套 2204 passed + 4 deselected，ruff 全过。尚未提交。

工件：`src/expedistill/stages/assemble_reuse.py`、`src/expedistill/stages/stage0_freeze.py`（tasks_spec 公开）、`src/expedistill/infra/manifest.py`（differing_keys）、`src/expedistill/infra/store.py`（object_filename、object_id_of_file）、`src/expedistill/composites/live_measurement.py`（改用 shortfall 读取）、`src/expedistill/cli.py`（说明文字去掉校验和）、`tests/unit/stages/test_assemble_reuse.py`、`tests/unit/test_cli.py`、`scripts/run/build_skillopt_split_configs.py`（注释里的函数名）

## 2026-10-18 17:16 — 评审 config.py 与 curation.py 的冗余与校验

一段话摘要：按用户经管理者会话转达的指示，对 core/config.py 与 composites/curation.py 做纯静态评审，只出报告不改代码。config.py 发现四项校验缺口，均在 .venv 中复现：测试相位可以配成考试执行而运行时照常真实执行；__post_init__ 先做类型转换，使 sequence 写成字符串被拆成字母、写成 null 抛出调用栈；n_attempts 的检查放过 True 与 1.5；scripts/run/convert_v2_configs.py 对每个 v2 文件都拒绝，已经失效。现有全部 v4 配置都不触发这些缺口，补上校验不影响任何现存配置。curation.py 的主要发现是：正式运行只走 dest 模式，run 模式只剩测试在用；文件内有多处可合并的重复，和 methods/curation/none.py 有两处相同逻辑；多处说明还在用已删除的 stage 编号。报告逐项标出不能动的内容：manifest 冻结字段、升级函数输出、外部对照 sha256、整理日志与技能库文件的字节内容和顺序、断点续跑的保存顺序，以及测试对 curation._write_log 的拦截。建议的处理顺序是先补四项校验缺口，再清理过时说明与无用代码，再合并重复，最后做原样搬移的拆分。尚未改动任何代码。

工件：`docs/reviews/config_curation_redundancy_review_20261018.md`

## 2026-10-18 17:23 — 补上 config.py 的四处校验缺口，删除失效的 v2 转换脚本

一段话摘要：按评审报告的处理顺序做第一步，用户已同意推进。补上的四处：测试相位只允许 real 或 real_folder 执行，原来写成 exam 也能加载、运行时却照常真实执行；__post_init__ 在转换之前先检查 sequence 是非空列表、phases 是非空映射，原来字符串会被拆成单个字母、null 会抛出调用栈；n_attempts_strong 与 n_attempts_weak 改用 _is_positive_int，原来 True 与 1.5 能通过、"2" 抛出类型错误，错误类型仍为 ValueError；scripts/run/convert_v2_configs.py 在 v4 代码下对每个 v2 文件都拒绝，已删除，README 2.5 节与 AGENTS.md 第五、六节同步更新，需要时从 commit 78db71f 取回。红线核对：仓库全部 9 份 v4 配置改动前后都能加载，to_dict 输出逐字节相同，manifest 冻结字段不受影响；未改动升级函数、外部对照 sha256、金矩阵与审计文件。验收：全套 2214 passed + 4 deselected（新增 10 个测试用例），ruff 全过。

工件：`src/expedistill/core/config.py`、`tests/unit/core/test_config.py`、`scripts/run/convert_v2_configs.py`（删除）、`scripts/run/convert_v3_configs.py`（说明）、`README.md`、`AGENTS.md`

## 2026-10-18 17:30 — 清理 config.py 与 curation.py 的过时说明和无用代码

一段话摘要：评审报告处理顺序的第二步，只改说明和无用代码，不改行为。config.py：删除没有业务调用方的 ExperimentConfig.model_for 及其测试；ROLE_DEFAULTS 去掉“角色可以映射为 None”的类型标注、跳过分支和注释（没有任何角色映射为 None）；改正过时说明——ConfigError 说明里的 pipeline.py 改为 cli.run_experiment、DEFAULT_RULES 注释去掉已删除的 stage 编号、frozen=True 的作用如实描述（只阻止属性重新赋值）、_method_mapping 说明改为一层浅拷贝、parse_phases 与 _check_compare_targets 的说明和报错补上 external_campaign、upgrade_v3_config 说明删去“输出还不能加载”。curation.py：模块说明与各处注释、报错里的 stage 3–6 改为按相位描述，run 模式如实写成“正式运行不用、测试在用”的模式，删去 weak_profile 的过时说明，_corrupted 的报错不再对与合并无关的损坏附加“只有 K5 可以修改合并产物”，删除 _build_dispatch 的 round_index 与内部 commit 的 unit_index 两个未使用参数。评审报告里“cli._build_handles 的角色缺失检查永远不会触发”这一条有误：配置里显式写成空字符串、null 或 0 的模型名能通过加载，那个检查是唯一拦住它们的地方，已保留检查并把报告该条改到“需要保留的检查”。curate_bank 的 weak_profile 参数与 parse_phases 的 n_attempts_strong 参数没有删除，前者有 7 处调用、后者有 35 处测试调用，留给合并重复那一步一起处理。红线核对：按语法树比较，curation.py 只有 _build_dispatch、_corrupted、_load_log、curate_bank 四个函数有代码变化，分别是删除参数和改报错文字，整理日志字段、保存顺序、技能库文件均不受影响；金矩阵与整理测试 184 个全部通过。验收：全套 2213 passed + 4 deselected（删除 model_for 的 1 个测试），ruff 全过。

工件：`src/expedistill/core/config.py`、`src/expedistill/composites/curation.py`、`tests/unit/core/test_config.py`、`docs/reviews/config_curation_redundancy_review_20261018.md`（更正一条）

## 2026-10-18 17:44 — 合并 config.py 与 curation.py 的重复代码

一段话摘要：评审报告处理顺序的第三步。config.py：两处版本号检查合并为 _check_schema_version，调用方各自保留缺省版本（YAML 文件缺省 3 即拒绝、manifest 字典缺省 4）；tasks 与外部对照文件的路径解析合并为 _absolute_path，pathlib 移到模块顶部；upgrade_v2_config 的缺省值改为从 _V3_METHOD_DEFAULTS 读取；基线双臂常量公开为 BASELINE_ARMS，stages/phase_execute/resolve.py 改为导入；划分键集合公开为 SPLIT_LIST_KEY_SETS，blocks/tasks.py 的 split_tasks 改为导入它和 MODES。合并划分校验时发现一处不一致：core 放过 train 或 test 写成 null，而 split_tasks 会拒绝，现在 core 只放过 reserved 为 null，新增 3 个测试用例。curation.py：BANK_NAME 改为从 composites/phase_bank 导入；_unit_member_ids 改为复用 _display_sources；“已接受但不在某集合里”的扫描合并为 _accepted_ids_outside；“跳过”结果合并为 _skipped；每轮结束的收尾合并为 finish_round 闭包；_accepted_members 与 _within_budget 公开为 accepted_members 与 within_budget，methods/curation/none.py 改为复用，删去其中相同的扫描和预算判断。“未知键拒绝”的 14 处没有合并：每处的报错文字各不相同，测试按这些文字断言，合并后报错要么变差要么只省几行，不值得。“记录失败后重新抛出”的 4 处也没有合并，每处都只有两行。红线核对：9 份 v4 配置的 to_dict、17 份旧版配置经升级函数的输出，改动前后逐字节相同；curation 测试套件运行中的 193 次日志写入，改动前后顺序和内容逐字节相同；store.save、追加库行、write_jsonl 的调用处数量不变；金矩阵与整理相关测试 189 个通过。验收：全套 2216 passed + 4 deselected（新增 3 个测试用例），ruff 全过。

工件：`src/expedistill/core/config.py`、`src/expedistill/composites/curation.py`、`src/expedistill/methods/curation/none.py`、`src/expedistill/stages/phase_execute/resolve.py`、`src/expedistill/blocks/tasks.py`、`tests/unit/core/test_config.py`

## 2026-10-18 17:49 — config.py 的升级纯函数原样搬到 core/config_upgrade.py

一段话摘要：评审报告处理顺序第四步的第一部分。把 _V3_METHOD_DEFAULTS、upgrade_v2_config、_refuse_unmapped_keys、_v3_methods、upgrade_v3_config 原样搬进新文件 core/config_upgrade.py（402 行），config.py 在末尾重新导出两个公开函数，10 个导入方一行不改；config.py 从 1696 行（评审时）降到 1326 行。逐字节核对：搬出的两段代码与搬移前的文本完全相同。红线核对：9 份 v4 配置的 to_dict 与 17 份旧版配置的升级输出改动前后逐字节相同，分层检查通过（新文件只依赖 core.config 与标准库）。README 2.5 节、装配工具一行与 AGENTS.md 第四节同步改为新文件。验收：全套 2216 passed + 4 deselected，ruff 全过。

工件：`src/expedistill/core/config_upgrade.py`（新建）、`src/expedistill/core/config.py`、`README.md`、`AGENTS.md`

## 2026-10-18 18:01 — curation.py 的单元规划原样搬到 composites/curation_waves.py

一段话摘要：评审报告处理顺序第四步的第二部分，只做搬移。把每轮单元规划的 9 个纯函数（_ordered_groups、_round_units、_display_sources、_unit_member_ids、_partition_waves、_dedup_key、_duplicate_units、_duplicate_record、_build_dispatch）原样搬进新文件 composites/curation_waves.py（139 行），curation.py 只新增导入其中 6 个名字的一段 import；测试通过 curation 模块引用的 _round_units 与 _partition_waves 解析为同一个对象，测试一行不改。纯搬移的证据：按语法树逐个取函数源码比较，搬出的 9 个函数与搬移前逐字相同，curation.py 留下的函数全部逐字未变，两边函数集合的并集正好等于搬移前的集合。管理者会话要求搬移提交里只能有搬移，所以本条取代上一版提交 c38b695（它在搬移之外还把 curate_bank 的已写库短路提成了函数，那部分另做一个提交）；c38b695 保留在分支 backup/before-split-c38b695。验收：全套 2216 passed + 4 deselected，ruff 全过。

工件：`src/expedistill/composites/curation_waves.py`（新建）、`src/expedistill/composites/curation.py`、`AGENTS.md`

## 2026-10-18 18:03 — curate_bank 的“已写库直接返回”提成 _finish_written_bank

一段话摘要：从 curate_bank 里提出一段顺序执行的代码：库已经写好时，补全被崩溃打断的追加、在 dest 模式下刷新产物、做过期检查，然后返回库。已读出的库行作为参数传入，读盘次数不变；curate_bank 从 457 行降到 420 行。这一步改变了代码组织，所以和上一条纯搬移分成两个提交。红线核对：curation 测试套件的 193 次日志写入改动前后顺序和内容逐字节相同。验收：全套 2216 passed + 4 deselected，ruff 全过。

工件：`src/expedistill/composites/curation.py`

## 2026-10-18 18:04 — 删除 curate_bank 的 weak_profile 参数与 parse_phases 的 n_attempts_strong 参数

一段话摘要：前面几步留下的两个未使用参数，只改函数签名，不改行为。curate_bank 的 weak_profile 从来没有被读取（它自己的说明也写明了这一点），删去参数和 methods/curation/agentbrew.py 的 2 处传参、4 个测试文件的 5 处传参；parse_phases 的 n_attempts_strong 在函数体里没有用到，删去参数、ExperimentConfig.__post_init__ 的 1 处传参和 3 个测试文件的 35 处传参。配置对象自身的 n_attempts_strong 字段不受影响，它仍然进 manifest。验收：全套 2216 passed + 4 deselected，ruff 全过。

工件：`src/expedistill/composites/curation.py`、`src/expedistill/core/config.py`、`src/expedistill/methods/curation/agentbrew.py`、`tests/composites/test_curation.py`、`tests/composites/test_curation_parallel.py`、`tests/composites/test_curation_resume.py`、`tests/unit/test_w1f_phase_bank.py`、`tests/unit/test_w2c_config_v4.py`、`tests/unit/test_folder_m2_delivery.py`、`tests/unit/test_folder_m3_pairing.py`

## 2026-10-18 19:40 — folder 上传改相位一次 tar 缓存 + 滚动准入闸门(连接风暴修复)

一段话摘要:全量 84 单元启动后 74 个单元 infra 失败,根因是 folder 交付的每单元 141 个技能文件逐个 per-file scp(84 单元 × 141 ≈ 上万次 SSH 连接),node29 的 sshd MaxStartups 把并发未认证连接整批掐断;小样本 6 单元没暴露。传输层修复(两道防线,均不碰模块边界、不改测量口径):一,相位只传一次——物化的技能文件夹打成单个 tar、单次 scp 到 <root>/skills_cache/<内容摘要>/ 远端缓存(原子 mv 为存在标记,幂等;摘要按文件名+字节排序哈希,换库即换缓存),每单元的 staging 改为一次 ssh(mkdir -p + cp -r 从缓存到任务目录与双拼写别名目录,cp 保字节,沙箱内 /opt/openclaw-skills/<任务>/ 内容与逐文件路径逐字节相同;双拼写逻辑原样迁移);连接数从上万降到每单元约 5 次。二,滚动准入闸门(用户飞书裁决:以"上传完成"为准放下一批,不能只靠 stagger 定时错开)——TB 插件加 BoundedSemaphore(宽 8,环境变量 EXPEDISTILL_TB_UPLOAD_GATE 可调,复用判定:父工作区 scripts/tb21/recover_rolling.py 是 campaign 层定时喂队列、K1 的 stagger 是定时错开,都不是按上传完成放行,无可直接复用的现成机制,故在传输层新造),新鲜启动的 upload+submit 整段持闸、下一个单元仅在先前单元上传完成后获准,续跑单元与轮询不占位(长等待不持闸)。测试:假链路升级为带真实 shell 语义的模拟器(test -d/mkdir/rm/mv/tar -xf/cp -r,按命令首词白名单认领,修掉 "skills_cache 含子串 kill" 的误判),重写三例 folder 沙箱测试到新契约(tar 字节逐一比对、staging 后任务目录字节等同物化目录、双拼写两侧),新增两例:两单元共享一次缓存上传(核心断言:全局恰好一个 tar、第二个单元仅 cp 无重传)、闸门只准新鲜启动(续跑零闸门流量)+ 环境变量解析。监督者巡检行补失败数("N stored / M failed",以后不许失败却显示正常)。验收:全套 2209 passed + 4 deselected,ruff 全过。

工件:`src/expedistill/benchmarks/tb/plugin.py`(_folder_dir_digest、_ship_folder_cache、_upload_folder_files 重写、_UPLOAD_GATE、_env_int)、`tests/unit/benchmarks/tb_fake_remote.py`(RemoteShellSim 模拟器 + JobsRemoteLink 接线)、`tests/unit/benchmarks/test_tb_plugin.py`(FakeRemoteLink 接线 + uploads 快照)、`tests/unit/test_folder_m3_sandbox.py`(三例重写 + 两例新增)、`tmp/supervise_folder_run.py`(巡检行,不进 git)、本条对应 commit


## 2026-10-18 21:35 — 监督者升格为正式脚本并修巡检失败数口径(管理者已批监控类修正)

一段话摘要:管理者发现巡检行失败数不降(39 stored / 74 failed,console 心跳是 done=38 failed=14),诊断方向正确:我把失败账本行的 unit_id 做 quote 后与存盘文件名比对,但存盘文件名是带 .json 后缀的百分号编码——两个集合永不相交,失败数永远是 74。修复:两侧统一到原始 unit-id 空间(存盘文件名 unquote 后去 .json 后缀),未解决失败数 = 账本行 unit_id 集合 − 已存盘集合,重试成功即从失败数消失。顺带把监督者从 tmp/(一次性)升格为 scripts/run/supervise_tb_run.py(已三次以上复用:装配冒烟、两轮续跑),新增 3 例计数口径单测(重试成功离开失败数、全失败全额、缺失文件读零)。验证:新巡检行 "48 stored / 36 failed unresolved" 与驱动心跳逐项对账吻合(48 = 10 前批 + 38 done;36 = 14 failed + 22 在飞单元的旧账本行,存盘即消失)。验收:全套 2212 passed + 4 deselected,ruff 全过。纯显示口径修正,不碰实验运行与测量。

工件:`scripts/run/supervise_tb_run.py`(新建,自 tmp/supervise_folder_run.py 升格并修口径)、`tests/unit/scripts/test_supervise_tb_run.py`(新建 3 例)、`tmp/supervise_folder_run.py`(退役)、本条对应 commit


## 2026-10-19 01:20 — 监督者重启路径修复:绝对命令 + 失败必落日志(管理者已批)

一段话摘要:00:37 驱动第二次退出(15 单元 infra 耗尽)触发监督者首次重启即崩——os.chdir 的目标算错一级(parent.parent 落在 scripts/ 而非仓库根),重启命令的相对路径 .venv/bin/expedistill 在那里不存在,FileNotFoundError 未捕获直接杀死监督者本身,00:38–00:55 无守护无告警。按管理者批准(附两点补充)修复:chdir 目标改为 parents[2](仓库根,提取为 repo_root());重启命令首项绝对化(cli_command() 从脚本位置推仓库根拼接,不再依赖任何 cwd);重启执行包进 relaunch_driver(),失败捕获 OSError、写入 supervisor.log 与 stdout 后才退出——不许静默死。新增 3 例单测:命令绝对且可解析、失败必留 "relaunch FAILED" 日志、成功写 pidfile。验收:全套 2215 passed + 4 deselected,ruff 全过。纯运维脚本修复,不碰实验运行与测量口径;真实重启路径的验证结果待下次驱动退出时回报。

工件:`scripts/run/supervise_tb_run.py`(repo_root/cli_command/relaunch_driver + chdir 修正)、`tests/unit/scripts/test_supervise_tb_run.py`(+3 例)、本条对应 commit


## 2026-10-19 03:30 — folder 版 C 终测完成:三口径持平,弱模型几乎不读技能文件(84 试 8 读)

一段话摘要:复用旧 141 条技能库(bank:a340288b1562 全程未动)的 folder 交付终测完成,84 单元全部落盘、完整性审计 0 error / 0 warn / 1 info(装配形态预期说明)。成绩:folder avg@3 = 46.4%(17/28 过),对照外部 campaign 裸跑 47.6%(18/28)为 −1.2pp(差一个任务),对照同库 retrieval 历史 42.9%(18/28)为 +3.6pp;逐任务配对两组都在噪声内(9胜5负14平 / 7胜5负16平),三种口径不可区分。核心观测:folder 模式下弱模型几乎不读技能文件——84 次尝试共 8 次读取、24/28 任务零读取、8 条被读技能各恰好 1 次、0 次读目录外文件;但 70/84 读了 SKILL.md 指引、57/84 列过目录——模型看得见文件夹,只是不打开技能正文,folder 的实际效果接近"裸跑+偶发自主检索"。运行账目:续跑窗口 6h19m(10-18 20:24 → 10-19 02:43),弱模型 141 次尝试共 2.92 亿 prompt + 637 万 completion token,D/L/B 零调用;重试账本 89 行/74 单元全部恢复;17/84 trial 无判分(记 infra,源 run retrieval 为 0,系两 run 最大口径差异,已如实入报告)。基础设施事件:连接风暴(传输层修复 commit b719aff)、VPN 断连(网络事件)、监督者两修(86594b1/64513dc,后者的重启路径修复未被真实触发,由单测钉住)。最终报告:~/Documents/Manager/state/replies/tb_folder_c_run.md。全部 9 个提交在 feature/multi-method 未推送,全套 2215 passed + 4 deselected。

工件:`runs/tb21_split_folder_20261017_1339`(正式 run,过审计)、`runs/tb21_split_folder_smoke1_20261018_1840`、`runs/tb21_split_folder_smoke2_20261017_1729`(小样本证据)、`~/Documents/Manager/state/replies/tb_folder_c_run.md`(最终报告)、本条对应 commit
