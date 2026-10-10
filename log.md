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
