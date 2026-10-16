# live_run 验收操作手册（runbook）

日期：2026-10-14
一句话说明：阶段 4 live_run 验收模式（`docs/decisions/acceptance_live_run_20261014.md`）的
操作手册——启动前检查、shortfall 预注册正本、pilot 检查表、进度查看、故障处置与
ALFWorld 义务清单；供首个 live_run 实验（候选配置
`configs/experiments/tb21_split_live_run.yaml`）启动时按节执行。
对应 log.md 条目：2026-10-14 live_run 实施完成条目（T5 文档波次）。

---

## 0.5 相位世界对应说明（2026-10-16 补注，R10 文档同步）

本手册写在"阶段 0–6"的旧世界里。2026-10-16 的执行—学习重构（schema v4，
`docs/decisions/execute_learn_module_boundaries_20261016.md` 与 README）把
"阶段 4 的 live_run 模式"换成了**相位序列** `D L B L B L C`（D 摸底、B 实战
验收、L 学习、C 终测；v4 配置见
`configs/experiments/tb21_split_live_run_v3_v4.yaml`）。阅读本手册时按
下表换算，其余内容（运维经验、检查纪律）继续有效：

| 手册里的旧说法 | 相位世界的对应 |
|---|---|
| stage-1 弱臂裸跑（基线） | 序列第 0 项的 baseline 相位（D），轨迹与已接受空洞仍在 `stage1_baseline/` 下 |
| 阶段 4 live_run 模式 | 序列里的 B 相位（live 角色），轮次固定为 {0, 1}：第 k 个 B 相位测第 k-1 轮 |
| `methods.acceptance` 槽位（n_attempts / improvement / max_injection_tokens） | B 相位的 `execution`（n_attempts、max_injection_tokens）与 `compare`（against: D、rule: improvement 名） |
| G10 归因 / G11 修订发生在阶段 4 | 发生在 B 相位**之后**的学习相位（L）：找证据（`methods/evidence/live_group`）→ 写作（`methods/author` 的组修订），回执写进该 L 相位目录的 `outcomes.jsonl`（旧 `attributions.jsonl` 已由它接替） |
| `stage4_summary.json` 的 live 形状 | B 相位目录的 `phase_summary.json`（逐组明细字段名沿用旧形状） |
| 重跑"阶段 1"补空洞 | 同一条命令续跑即可：D 相位按磁盘上已有的轨迹跳过、只补缺失单元 |
| `--until-stage` | `--until-phase N`（N 从 0 数，含义见 README 第六节） |

**继续原样有效的节**：§2 shortfall 预注册正本（接受标准与按轮记录）、
§2.5 G10/G11 视图输入预算、§3 的 pilot 检查方法（逐项核对路径按上表换算）、
§5 的停机语义与处置、§6 ALFWorld 义务清单、§8 的审计纪律
（`scripts/audit/audit_live_run.py` 的账本读取在 Wave 3 同步到相位目录）。
**已过时的节**：§1 第 3 条的"methods 五槽位校验"（v4 起是逐相位的加载期
检查清单）、§4 的进度查看命令里的 `stage4_summary.json` 路径（改读
`phases/` 下各相位目录的摘要文件）、§7 的"升 v3"遗留事项（已于 2026-10-16
随 v4 完成并超越）。`scripts/audit/audit_live_run.py` 已随修复项 R10 迁移：
读取路径按冻结 manifest 的 schema 版本分派（阶段世界的旧 run 走原路径，
v4 相位世界走相位目录与学习相位账本），`--self-test` 覆盖两种形状。

---

## 0. 适用范围与身份声明

本手册服务 stage-4 live_run 模式的正式 run 与 pilot。方法的完整定义在
`docs/decisions/acceptance_live_run_20261014.md`（最终实施方案修订三）与 README；
本文件只写"怎么跑、怎么查、出问题怎么办"，不重复设计论证。冲突时以决策文档为准。

两个 mode 事实先钉死：

- live_run 与 exam 共用 `stage4_summary.json` 文件名，但 live 的 `infra_failed` 元素是
  **task id**（不是 skill id），且没有 `skipped_missing_exam`（形状差异表见 README 阶段 4）；
- live 的执行单元是**组**（一个 train task 的全部 pending 候选），两轮钉死，
  轮 store 分别在 `stage4_acceptance/live_run/round00|round01/`，
  K1 工作目录在 `work/stage4_live/round00|round01/`（游标按 workdir × trajectory 存，
  两轮的 trajectory id 相同，必须分树）。

## 1. 启动前检查（全部通过才许开跑）

1. **远程可达 + 真实请求**：node29 ssh 通；按外层 AGENTS 的健康检查纪律，不能只看
   "目录在不在"——TB 走 Harbor 链路，先跑一次 `scripts/smoke/smoke_tb_dryrun.py`
   （生成并校验 job YAML，不起沙箱），再跑单题 live 冒烟确认端到端可用；
2. **基线完整性**：live_run 的判定对照是 stage-1 弱臂裸跑轨迹，入口守卫会逐确定性 ID
   加载。启动前自查（零模型调用）：
   - 每个有候选组的 train task，`traj/<task>/weak/00..NN`（NN = `n_attempts-1`）要么在
     主 store `stage1_baseline/trajectories/` 里，要么在
     `stage1_baseline/accepted_infra_shortfall.json` 里（弱臂、限本 task）；
   - 任何"未接受缺失"会在入口 InfrastructureError 停下并指路先续跑 stage 1——不要
     用 `--allow-infra-shortfall` 掩盖基线缺失，stage 1 的空洞只能在 stage 1 接受；
   - 基线全成功的组会被 `weak_already_knows` 零执行短路（这是特性不是 bug）；基线
     valid == 0 的组是 ConfigError（数据与冻结规则矛盾，须人工诊断）；
3. **acceptance 配置校验**：`load_config` + stage 0 会校验 `methods` 五槽位形状、
   各方法参数契约、`improvement` 规则名已注册、未知键拒绝。
   启动前手动复核配置里的 `improvement` 名字
   与预注册文档一致（拼写错会在 stage 0 停下，但核对是免费的）。注意
   live 深度代码只强制 `n_attempts >= 1`，`>= 2` 是设计文档的推荐下限
   （README 七.4），配 1 不会被加载期拒绝；
4. **在飞 run 兼容**：同一 run 目录不得在两版代码间混跑——老 run 用它自己的
   维护分支 + 独立 venv 续跑（AGENTS 六.1）；schema v2 的 run 目录在 v3 代码
   的 stage 0 会被明确拒载（config.methods 形状不同）；
5. **运行设置**：TB 远程沿用 K1 批弹性经验——`--k1-stagger 20`（≥20，配合随机退避
   `--llm-infra-backoff`）、workers 按 campaign 修复后口径可到 64（2026-10-14 更新：
   旧口径"16 安全、32 必爆"是退避机制修复**前**的实测；修复后 32→64 workers + stagger
   20/40 已有 168 局跑通先例，log.md 2026-10-12 条目）、`--llm-timeout 1500`。

## 2. shortfall 预注册正本（裁决 5，启动前定死、看结果前不许改）

> **live_run 正式 run 默认不开 `--allow-infra-shortfall`**（与 TB v2 阶段 6 同纪律——
> 重试耗尽当场同命令续跑；不靠开关兜底）。
>
> 仅当**用户明示允许**时，接受标准为：
> 1. **单单元经一次续跑补跑后仍 infra 耗尽**（即至少两轮完整重试）才可接受；
> 2. **总量 > 15% 的组**（单元口径）出现 infra 耗尽即停下诊断，不带病继续；
> 3. **双轮同 policy**：轮 0 与轮 1 用同一条接受标准（按轮记录、轮 1 不自动继承
>    轮 0 的空洞——远程沙箱是新实例，"轮 0 坏"对轮 1 无信息量）；
> 4. **先落盘后判定**：接受的空洞先写进 `live_run/roundNN/accepted_infra_shortfall.json`
>    （durable、本 run 持久），判定才在剩余有效样本上按率进行，verdict 行带
>    `judged_with_shortfall: true`。
>
> 此文本即预注册正本（决策文档 §3 裁决 5 的引用落点）；启动实验的 log.md 条目须
> 原样引用本节。

补充事实（不是政策、是代码语义）：`--allow-infra-shortfall` 接受下来的空洞**没有
撤回通道**，之后每次续跑都跳过、按 infra 计分；开关只决定"新出现的空洞接不接受"。

## 2.5 G10/G11 视图输入预算预注册（2026-10-14 三评审修复，启动前定死；2026-10-15 按 miss 链路三评审的包络核算改锚）

> `live_views`（组内全部轨迹视图拼接）与 G11 `failure_view` 受 K6 硬字符预算约束：
> **`_LIVE_VIEWS_BUDGET_CHARS = 140_000`**（组级、逐视图均分）、
> **`_FAILURE_VIEW_BUDGET_CHARS = 80_000`**（修订调用的失败视图块）。
> 超预算的单条视图保留头部 60% + 尾部 40%（TB 失败证据集中在尾部），中间以
> `[... N chars elided ...]` 标记折叠。
>
> **选值依据（2026-10-15 改锚，评审 C 的包络核算）**：预算按**请求总包络**锚定，
> 不只按输入。140k 字符 ≈3.5 万估算 token，按该端点实测的低估系数（run3 真实
> SEGMENT 请求校准 real/est = 1.28–1.50、中位 1.38）≈4.8 万真实输入 token；
> author/rewriter 角色的 `max_tokens=65536` 一并计入后**总包络 ≈12 万 token**，
> 在该端点已证实的最大接受包络（≈11.9 万，v3 run G7 全库 listing + 65.5k cap）
> 之内——无论端点是否把 max_tokens 计入窗口校验都安全。初版 200k 按输入-only
> 口径核算（宣称对 128k 窗留 13% 余量），未计 max_tokens，评审 C 判定为
> 条件性硬停风险（若端点按和校验，首个大视图 miss 组必 400 → ProviderRefusal
> → 整阶段硬停且续跑原地再撞）。
>
> 预算存在的理由（pilot 实测背景）：无预算时一组三条视图实测 562,037 字符
> （≈140,509 估算 token，单条 208 步轨迹 ≈89,776）；上下文超限从端点回来是
> 400/413 **永久拒绝**——既不算修复预算项也不是 InfrastructureError，首个带长
> 轨迹的失败组会把整个阶段硬停且续跑原地再撞。
>
> 这是护栏不是实验变量（与 benchmark 插件里 ViewRules 常量同一纪律），因此**不做
> 成配置键**；改值须改代码并过全量回归，且要在本节记录新的实测依据。若正式 run
> 中仍出现 400 超限（包络假设被推翻），应急通道：下调本常量一行 + 同命令续跑
> （它不在 manifest 冻结面）。

## 3. pilot 检查表（合并门 2：1–2 个 task 全两轮真实干跑，含一次故意归因失败路径）

| # | 检查项 | 怎么查 |
|---|---|---|
| 1 | 远程 run id 不撞（轮 0 / 轮 1 / stage 1 / stage 6 四方） | 远程 jobs 目录里 `ed_<task>_<ns>_<token>`：轮 0 与轮 1 的命名空间不同（workdir 真实路径 + 注入块 digest 都进了 ns 计算）；与同 task 的 stage-1、stage-6 历史 id 逐一比对无重叠 |
| 2 | audit token 实测 | `injection_audit.jsonl` 行的 `injection_tokens`（len//4 估算）与 `llm_requests/usage.jsonl` 里该轮实际请求的 prompt token 同量级；偏差 > 50% 时记录（估算对终端 dump 类文本低估约 41% 是已知特性） |
| 3 | verdict 行 schema | `verdicts.jsonl` 行含全十字段（schema/task_id/round/rule/baseline/live/better/injection_tokens/judged_with_shortfall/aux）；零执行终态行计数与 better 为 null、verdict 字段承载终态名 |
| 4 | 注入块 sha 一致 | audit 行的 `injection_block_sha256` == 本地 `format_skills_block` 重渲染的 sha256 == 远程 `jobs/<run_id>.ed_meta.json` 记录的 SKILL.md 摘要（三方一致才算注入链路闭合） |
| 5 | kill-续跑等价 | pilot 中途 kill 一次（选轮 0 执行中）再同命令续跑：成员状态、三账本行数与内容、轮 store、`stage4_summary.json` 与参照 run 完全一致；归因落盘后崩溃不重问（FakeClient 语义在真实端点下以 llm_requests 落盘计数验证） |
| 6 | 对照真的是对照 | 基线轨迹 `model_name` 是弱模型、`skills_injected` 为 null 且来自 `stage1_baseline/`（不是轮 store）；live 轮 store 轨迹的 `skills_injected` 恰为 audit 行的成员 [skill_id, version] 列表 |
| 7 | 归因失败路径 | 故意让一次 G10 输出违约（或用 pilot 自然发生的）：验证预算账本（`attribution_failures.jsonl`）逐次记录、耗尽行 `exhausted: true`、全组 `model_output_error` 终态先于耗尽行落盘 |
| 8 | 归因输入量级 | pilot 后把 G10/G11 的输入 token 量级（`live_views` 的体积随轨迹长度的伸缩）**回填 README 披露**——不当作已知数先写 |

pilot 数字回填前，README 与本手册都不写"归因一次调用多少 token"之类的预估结论。

### pilot run3 实测记录（runs/tb21_live_pilot_run3，2026-10-14）

- 项 1/3/4/5/6 **通过**：轮 0 命名空间（f3d007c7c3 / e01f17992b）与 stage1（462f5460ba）
  不撞且远程零重复 token；verdict 行十字段齐全；注入块 sha 三方一致（本地重渲染 ==
  远程 ed_meta）；kill-续跑七项等价证据全过（manifest/audit 字节不变、stage0–3 零重跑、
  六远程 job rejoin）；对照确为裸弱臂（verdict baseline 与 stage1 weak metrics 逐字段
  一致、轮 store 轨迹 skills_injected 恰为成员列表）。
- 项 2 **通过**（audit tokens 6727/4737 与 usage.jsonl 同量级）。
- 项 7/8 **部分**：两组轮 0 直通，G10/G11/轮 1 零触发（llm_requests 中 LIVE_* 请求 0
  条）；量级实测已得（一组三视图 562,037 字符 ≈140,509 估算 token——由此促发视图
  预算修复 §2.5）；miss 链路（归因失败路径、修订、轮 1、no-op 接缝）留给 **miss pilot**
  （chess-best-move + write-compressor，两组 weak 0/3 且 strong 1/3 的 train 任务，
  2026-10-14 三评审 R2/P3 裁定）——它是 §2.5 预算修复的验收载体。

## 4. 进度查看（全部零模型调用、只读）

```bash
RUN=runs/<实验名>
# 摘要（每完成一个阶段更新；live 的逐组明细在 group_details）
cat $RUN/stage4_acceptance/stage4_summary.json
# 判定进度：每行一个 (task, round)；行数 / 组数 ≈ 阶段进度
wc -l $RUN/stage4_acceptance/live_run/verdicts.jsonl
# 归因/修订账本
wc -l $RUN/stage4_acceptance/live_run/{attributions,attribution_failures,revision_failures}.jsonl
# 轮 store 落盘速度（K1 的 stderr 心跳行每 300s 一条：done/failed/skipped/in_flight）
ls $RUN/stage4_acceptance/live_run/round00/stage1_baseline/trajectories | wc -l
ls $RUN/stage4_acceptance/live_run/round01/stage1_baseline/trajectories | wc -l
# 阶段级 infra 失败（组保持 pending 的原因）
grep stage4_live $RUN/failed_units.jsonl | tail
```

判定规则速查：轮 0 全组判定完成后才进归因/修订；轮 1 只跑"幸存且注入块有变化"的
组（no-op 守卫拦下注入块未变的）。**轮 1 慢是结构性的**（轮栅栏：轮 1 要等最慢的
轮 0 组），不是卡死；区分"正常慢"与"卡死"用心跳行，不用目测。

## 5. 出问题怎么处理

- **infra 停机语义（退出码 3）**：某组的执行/归因/修订单元耗尽基础设施重试时，组保持
  pending、其余组继续，阶段末写完摘要后以退出码 3 停下。处置 = **同一条命令原样续跑**
  （CLI 停机时会打印可粘贴的续跑命令）；已完成单元零重跑。supervisor 可对 3 做有限次
  自动续跑，对 1 必须停下；
- **shortfall 政策触发**：见第 2 节——默认续跑补全；用户明示 + 预注册标准满足时才加
  `--allow-infra-shortfall`，且必须先核对"单单元两轮完整重试"与"总量 ≤ 15%"两条；
- **CodeBugError（重算不一致 / 冻结行不符）**：stage 被设计成 fail-loud。这不是可续跑
  状态：停下、保全现场（拷贝 run 目录）、按外层 AGENTS 诚实性规范记录，修复须在新
  run 目录重建（AGENTS §6.8）；
- **归因/修订的模型输出失败**：预算内重试自动进行；预算耗尽是**本 run 内终态**
  （G10 耗尽 → 全组 `model_output_error`；G11 耗尽 → 仅该 skill），不是故障，续跑不
  重试——想再试就开新 run 目录；
- **run 目录完整性存疑**：跑只读审计
  `.venv/bin/python scripts/audit/audit_live_run.py --run-dir $RUN`（五项对账：组终态 ↔
  账本、轮 store = 应有减 skip、账本自洽、shortfall ↔ failed_units、冻结行 ↔ 注入集；
  exit 1 = 有 ERROR，数字不得使用）；
- **退出码 1**：manifest 不一致 / 配置错误 / 服务方拒绝 / 模型输出错误逃出阶段——都要
  人改了东西才能继续，supervisor 不得自动循环。

## 6. ALFWorld 义务清单（裁决 10：启动其 live_run 实验前必须完成）

结构性改动为零（K6 与 E1 对 benchmark 盲），但三项义务写入本节，逐项勾销：

1. **串行 runner 瓶颈的分片方案**：ALFWorld 是本地逐步执行（单局 = 一个子进程，步数
   上限 `EXPEDISTILL_ALFWORLD_MAX_STEPS`，默认 50），throughput 受单机并发约束——
   外层工作区的经验是串行全量约 50 小时级、瓶颈在 runner 本身。义务：启动前先在
   pilot 实测单任务两轮的墙钟，外推全量工期，写出并发方案（K1 的 `--workers` 即
   本地执行并发数，须与 eval 环境的资源占用一起定），并评估是否需要外层那套
   多沙箱分片；方案写进本节的 ALFWorld 附页后才许开正式 run；
2. **`max_injection_tokens` 设非 null**：ALFWorld 的上下文上限 32k（外层 vLLM 服务
   `--max-model-len >= 32768` 是硬约束），live 注入块 + 任务 prompt + 轨迹必须装得下。
   配置里 `max_injection_tokens` 按其画像预注册一个非 null 上限，超限组零执行丢弃
   （`live_injection_overflow`）而不是烧穿上下文；
3. **判据采样深度单独预注册**：`n_attempts` 不沿用 TB 的 3——ALFWorld 本地执行便宜
   （无远程沙箱成本），可升 5；**H0 假通过率数字必须按新 n 重算**（n=3 时 27–34%，
   n=5 时更低；推导见决策文档 §0 与 README 已知风险 4）并写进本节的 ALFWorld 附页，
   不得直接引用 TB 的数字。

## 7. 遗留事项（实施收尾清单，裁决 7）

一次性技术债，**在飞 run 全部完结后**统一清理（现在做会拒绝在飞 run 的续跑）：

- [ ] 把 `CONFIG_SCHEMA_VERSION` 从 2 升到 3（升版动作本身 = 新实验身份，届时所有冻结
      值 2 的 run 已终结）；
- [ ] 把 `core/config.py` 的 `_MANIFEST_OPTIONAL_FIELDS` 白名单删键机制退役为普通字段
      （"complete-don't-reject"：schema v3 起 `acceptance` 恒写进 manifest，None 显式
      在场而非缺键），删除为此写的例外注释；
- [ ] 升版后跑一遍老配置 manifest 干跑（合并门 1 的复用），确认预期内、且仅预期内的
      键集差异出现；
- [ ] 在 log.md 记录升版与退役（对应 AGENTS §7"修改数据对象"条款）。

## 8. 文档回写义务

- pilot 的归因输入 token 量级等实测数字 → 回填 README（第 3 节检查表第 8 项）；
- 正式 run 启动 / 结束 → log.md 各一条（含 run 目录、配置名、是否通过完整性审计）；
- run 结束后先跑 `scripts/audit/audit_live_run.py`，数字过了审计才进结果表
  （AGENTS §6.7）。
