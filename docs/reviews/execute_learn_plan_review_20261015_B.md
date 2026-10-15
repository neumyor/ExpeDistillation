日期：2026-10-15
一句话说明：执行—学习闭环重构方案（scope 级）的第一轮评审 B——扩展性与反过度设计。
对应 log.md 条目：2026-10-15 执行—学习闭环重构方案（scope 级）。

---

## 0. 总评

方向正确：两阶段（E/L）+ 模块组合 + 方法 = 阶段序列，确实比五槽位流水线更贴近
"轨迹→技能库→新轨迹→新技能库"的闭环本质，§1 对五槽位四条病的诊断与代码事实逐条
相符。§9 的"明确不做"清单与用户反过度设计约束完全对齐（无新 hash、无类型化报告类、
无 DAG、无自动发现），异常分类与逐单元续跑模型（§7）被显式保全，两处新增检查都是
加载期约束而非运行时硬门。

但方案有一个未裁决的结构问题（五槽位与 phases/modules 的关系，即"两套抽象并存"的
风险）和三个会把过度设计或承诺落空固化进实施的具体缺口：验收报告的落盘形态、配置
示例的自相矛盾、M2 对 K6 测量去轮化的缺失。四条均为【偏移约束】，修法都是方案文档
里加几句话，不动方向。

证据基线：`methods/` 现状 1733 行（registry 124 + params 66 + 五槽位包与驱动）；
`composites/live_acceptance.py` 1212 行、`curation.py` 1274 行、
`methods/acceptance/live_run.py` 825 行；golden 矩阵 8 组合
（tests/fixtures/golden/provenance.json）。以下 file:line 均相对仓库根。

---

## 1. 偏移约束（不修不能进实施）

### B-1【偏移约束】五槽位与 phases/modules 的关系未裁决——必须声明"一张注册表"，并给出逐文件处置清单

**问题**（对应重点判断 1）。方案 §4.2 只说"模块名查纯数据注册表（沿用现有做法，
按需加载）"，从头到尾没有回答：`methods/` 现有的五槽位注册表
（`methods/registry.py:42` 的 `_ENTRIES`，键为 segmentation/authoring/acceptance/
curation/delivery 五个槽位）与新的 phases/modules 是什么关系。这是用户点名的
"两套并存就是 over-engineering 的最大来源"：如果实施时新建一套 phases 注册表、
又不敢删旧五槽位，仓库将同时存在两套按名分发、两套 Params、两套 check。

**判断**：不需要第二套注册表。现有注册表的**机制**（纯数据表 + 懒 import 驱动 +
Params/check 挂表项，`methods/registry.py:34-38`、`methods/params.py:21`）正是
新方案需要的；要换的只是**键集**：从五个流水线槽位，换成相位内的模块名。五槽位
中三个模块名（delivery、curation、authoring）字面保留，一个改挂（segmentation →
evidence 的 segment 实现），一个重定义（acceptance：从"测+决+改"重定义为
"只测量"），另新增两个键（execution、decision）。

**建议修法**：方案加一节"旧五槽位处置"，逐文件清单如下（对照 `methods/` 目录）：

留（机制与文件逐字节不动）：
- `methods/registry.py`、`methods/params.py`、`methods/__init__.py`——注册表
  机制原样，只改 `_ENTRIES` 的键；
- `methods/delivery/{__init__,whole_bank,retrieval}.py`——交付模块就是原槽位，
  驱动契约 `resolve(ctx, params, bank, tasks) → (K1 kwargs, 审计行)`
  （`methods/delivery/retrieval.py:54`、`whole_bank.py:19`）原样够用；
- `methods/curation/none.py` 原样；`methods/curation/agentbrew.py` 仅加
  `merge_check` 参数透传给 K5；
- `methods/segmentation/__init__.py` 的 `SegmentationParams` 移作 evidence 槽位
  下 "segment" 实现的参数（attribution 另立自己的 Params）。

改造：
- `methods/acceptance/exam.py`（208 行）与 `live_run.py`（825 行）按 M2/M3 拆：
  测量归 acceptance 模块实现；轮序编排、再派发集、shortfall、summary 归 E 相位
  驱动；动作分配归 decision 模块；
- `methods/authoring/__init__.py`：删 `exams` 开关与 `check_authoring`
  （`methods/authoring/__init__.py:25-45`），写作模块只剩 G2/G6/G11 的 prompt 名
  与块接线；
- `methods/acceptance/__init__.py` 的 `STAGE4_DIR`/`SUMMARY_FILENAME` 布局常量
  随 §7 的新目录约定走（审计脚本与 CLI summary 同步，见琐细 T-8）。

删（在 M2–M4 内完成，不留兼容层）：
- `core/config.py:85` 的 `METHOD_DEFAULTS`、`:200` 的 `_validate_methods` 五槽位
  校验与配置里 `methods:` 五键——M4 一次性换成 `phases + sequence`（P1b"唯一一次
  切换 + 转换脚本写新文件名"的先例，照抄）；
- `stages/` 的 stage1–stage6 固定顺序编排（`stages/pipeline.py:61-69` 的
  `STAGE_NAMES`、`:387-416` 的直线调用）——各 stage 的单元逻辑被相位驱动吸收后，
  `pipeline.py` 变成序列驱动（冻结、runlock、用量记账、pipeline_log 全保留）。

新增（唯一真正的新代码面）：E 相位驱动、L 相位驱动、decision 模块的具名规则
（从 K4/K6 的分支逻辑平移）。

### B-2【偏移约束】验收报告必须是"对既有落盘工件的读取器/视图"，不是新落盘对象

**问题**（对应重点判断 2）。方案 §2 把验收报告列为三个跨阶段对象之一但未说形态；
§8 M1 写"技能与段解绑、考题挂技能、**定义验收报告**；schema 升版"——把"定义报告"
与"schema 升版"并列，会诱导实施者把它做成新的持久化数据对象（第二个 schema 对象）。

**判断**：报告的每一项内容今天都已经按单元落盘，且已经是审计与 golden 的唯一真相：
- exam 侧：bare/with-skill 各轮 Attempt（judge_verdict、judge_reason）+
  `leak_verdicts.jsonl`（`composites/acceptance.py:98`）+
  `rewrite_failures.jsonl`（`:103`）+ Skill 终态；
- live 侧：`verdicts.jsonl`（基线/live 计数、better、injection_tokens、aux、
  终结判定，`composites/live_acceptance.py:129`）、`attributions.jsonl`
  （诊断 + 逐技能计划，`:130`）、`injection_audit.jsonl`（字节等价审计，
  `:138`、`:536-562`）、各轮 shortfall 记录、Skill 终态；
- 审计工具已经直接从这些账本推导一切状态（`scripts/audit/audit_live_run.py:86-91`
  自述"derives ALL of its state from disk"）。

若报告是**新落盘对象**：与上述工件构成两套真相；测量与报告写之间出现新崩溃窗口
（判卷是不确定的，"首判为准"要求重问被拒——续跑语义必须为报告自身另立规则）；
§3.1"只测变化的部分（按技能版本判断）"需要新的"报告行有效性"机制。
若报告是**读取器**（每个 acceptance 实现注册一个纯函数：按单元聚合既有工件 →
内存 dict）：续跑语义免费继承（账本本来就是续跑契约）、零新 schema、零新崩溃窗口、
golden 与审计照旧。方案 §2 说证据是"判卷说明 / **失败轨迹引用**"——引用而非副本，
说明方案本意就是视图，只是 M1 的措辞没说死。

**建议修法**：M1 行改为"定义**报告读取器**（纯函数：按单元聚合 attempts / verdicts /
leak_verdicts / attributions / 技能终态），输出内存 dict；不做类型化类（§9 已定），
不落盘"。schema v4 只含技能身份变更（B-4）。最小行形状：`{unit_id, with_result,
control_result, improved: Optional[bool], evidence: [引用], 未测定标记}`——
`improved=None` 覆盖泄题作废与全洞未测定；除这五项外不为跨实现对齐字段
（exam 的 judge_verdict 与 live 的任务计数本来就是 decision 各自规则要读的）。

### B-3【偏移约束】§4.2 配置示例内部不一致：两套参数约定、未定义引用、死字段——schema 定形前必须统一

**问题**（对应重点判断 3）。同一 YAML 块里出现两套约定：
`C: {delivery: retrieval, ..., top_k: 5}`（模块名是字符串、参数漂在相位顶层）vs
`L: {..., curate: {name: agentbrew, merge_check: none}}`（参数嵌套在模块键下）。
前者要么迫使 phase 层另写一套参数校验（与 `methods/params.py:21` 的 build_params
"未知键拒绝/类型检查"重复成两套机制——正是过度设计），要么把所有模块参数摊平进
phase 顶层造成命名冲突（两个模块同有 top_k 时无解）。

附带三处：
- `compare: D_test` 引用了 phases 里不存在的键，示例自己不可解析。C 在 split 模式
  下需要测试集的裸对照（今天 stage6 split 模式跑 fresh bare arm，
  `stages/stage6_test.py:324-350`），这个对照在示例里没有表达位置；
- `models: [strong, weak]`（D 行）：D 的定义就是"强、弱各一遍"（§4.1），
  无任何单臂 D 消费者，是常量字段，删；
- `A: {compare: bare_exam}`：bare 对照是 exam 协议内生（K4 规则：裸答错 + 带技能
  答对才收），不是相位引用，魔数关键字可省略；compare 的词汇表要么是相位名、
  要么缺省。

**建议修法**：模块参数一律嵌套（`delivery: {name: retrieval, top_k: 5,
max_categories: 3, min_score: 0.02}`，与 v3 methods 形状同构，build_params 免费生效）；
compare 只允许引用 `sequence` 中出现过的相位名，C 的裸对照显式表达（C 配置自带
delivery:none 的对照臂，或序列里加一个 D_test 相位）；删 `models` 字段。跨相位
约束进 check：真执行相位的 `n_attempts` ≤ 其 compare 目标弱臂深度（现
`methods/acceptance/__init__.py:70-85` 的 `check_live_run` 的推广）。

`phases`（命名 + `sequence` 引用）vs 序列内联：**保留方案的命名引用**。理由：
L 在 exam 序列里出现 4 次，内联会把同一份配置抄 4 遍，"抄漏/抄错一处"恰是
§3.2"只有一种 L"要禁的失败模式；命名映射天然保证同名同配置，且 manifest 冻结
`phases+sequence` 就是"序列即实验身份"（§4.2）的最小载体。`kind: execute|learn`
保留（一个字段的显式早验，比按键集猜类型稳）。

### B-4【偏移约束】M2 未包含 K6 测量与"两轮协议/live 目录布局"的解耦，而 §3.1/§4.1 的"空格组合"承诺依赖它

**问题**（对应重点判断 5b）。方案承诺"交付与执行方式正交，空格都是可直接拼出的
新组合"（§4.1）、"检索 + 真做题做验收无需新代码"（§3.1）。但今天的真执行测量
内核被两轮协议钉死：`composites/live_acceptance.py:169` 的 `_ROUNDS = (0, 1)`
（"rounds are pinned to two"，`_check_round` 对 0/1 之外的轮直接 CodeBugError）、
`:125` 的 `LIVE_DIR_NAME = "stage4_acceptance/live_run"` 烧进每条账本路径。
§8 M2 只写"从 K4/K6 抽出'测量'部分作为验收模块"——如果"测量部分"连着轮号与
live 子目录布局一起搬走，那么"C 形态做中间轮"这类空格组合每拼一次都要再动一次
K6，承诺落空。

**建议修法**：M2 的内容栏加一句——**轮序与 live 子目录布局是 live 方法的私有序列
编排（归 E 相位驱动），验收测量核心的签名与轮数/目录无关**（输入 = 一个轨迹
store + 基线计数 + 具名规则 + 冻结的注入审计行，输出 = 报告行）。这样
"检索交付 + 真执行验收"的拼装成本就是：0 个新模块、1 份相位配置、至多 1 条新
具名决策规则（检索按任务命中后"任务提升→保留哪些技能"是新的归因语义，属方法
内容而非框架成本）。

---

## 2. 琐细

- **T-1**（判断 4）§1 的动机言过其实：不切段在旧框架下也能表达（切段方法输出
  "每轨迹一个整段 subtask"，`SegmentationParams` 槽位本来就预留了第二实现）；
  ID 解绑的真实需求是**多轨迹合成技能**（"新增"动作）。M1 可做成加法：保留
  `make_skill_id`（`core/ids.py:88`，两现存方法全部不变），只为合成技能新增
  `skill/<serial>` ID 模式 + 来源字段——比全量换键更小。若坚持全量换键也可行
  （§6 已承诺 golden 按语义核对），但改动更大。
- **T-2**（判断 4）来源字段最小集：`source_subtasks: List[str]`（切段法=单元素；
  整轨迹法=整段 subtask；合成=多个）+ 现有 `merged_from`（`core/models.py:235`）
  + 现有 version/status/discard_reason/group。考题挂技能 = `exam_id` 从
  `subtask_id` 改键到 `skill_id` 的纯重标（两现存方法 1:1）。**不要**加时间戳、
  内容 hash、逐技能 prompt digest、模型名来源——manifest 已全局冻结 prompt
  digest，逐技能溯源是通用审计系统的苗头。候选判定（`core/ids.py:111`
  `is_stage3_candidate`）由 ID 模式区分（`make_skill_id` / `skill/<serial>` /
  `bank/merge/`），零新字段。
- **T-3**（判断 6）决策/证据边界要写一句话：G10 的输出**本身就是逐技能动作分配**
  （`composites/live_acceptance.py:883-888` 的 `skill_actions`），方案应写明
  "决策定单元级动作；'修改'单元由证据模块（归因）细化为逐技能计划"，避免实现时
  两处推导动作或建协调层。
- **T-4**（判断 7）决策模块改库只经既有提交 helper（`composites/skill_status.py:20`
  `discard_skill`、finalize 式提交），沿用"终态先写、账本行为回执"次序；**不新增
  actions ledger**——技能对象的终态是唯一真相，任何计划行只是回执。方案未要求
  新账本，M3 实施时守住即可。
- **T-5**（判断 6）M2/M3 是行为保持步骤，验收应加"golden 无 diff"（方案只写了
  单阶段测试 + 续跑测试；golden 矩阵现成、0.6 秒，是更强的免费验收——P0/P1a 的
  先例就是这么用的）。
- **T-6**（判断 6/判断 5d）"决策规则知道后面还有没有能验证的执行"的输入要定义：
  由序列派生的"剩余反馈执行轮数"（对本单元任务集有报告、且其后还有 L 的执行
  相位数；C 这类终评相位不算反馈）。L 配置的单份 `evidence`/`author`/`curate`
  字段含义是"需要时用哪个方法"（首 L 才分段、中 L 无证据步、尾 L 才整理），
  这层按门与位置的分支要在 §3.2 写明，否则实现会为不同位置造不同 L——恰是方案
  自己禁止的。
- **T-7**（判断 3）`A` 行的 `compare: bare_exam` 省略即可（exam 协议内生对照）。
- **T-8**（判断 1/迁移）M4 清单漏三处跟随者：`stages/assemble_reuse.py:119-126`
  装配白名单读 `config.methods.delivery`（schema v4 下需同批改，P1b 的
  `upgrade_v2` 先例）；`scripts/audit/audit_live_run.py:86-91` 与 CLI summary
  读 `stage4_acceptance/live_run` 子树和 `STAGE_NAMES`（新目录约定 + 序列化
  阶段标签要同步）；`pipeline_log` 的阶段标签被 runbook/审计读（multi_method
  方案 §6 明确"不改阶段标签"）——新布局用新标签时旧 run 不受影响，但要写进 M4。
- **T-9**（判断 5b）§4.1"任意组合都是合法的执行阶段"对**管件**成立、对**语义**
  不成立（"同任务打包 + 伪执行"的组级判卷/归因是新协议，不是零代码）；组合表
  加一句"新格子的测量/决策语义可能需要新具名规则与决策规则"。

---

## 3. 赞同

- 两阶段 + 模块组合 + 方法 = 序列的核心抽象；§1 的四条病诊断与代码事实逐条相符
  （技能 ID 段绑定 `core/ids.py:88`；live 验收内嵌小闭环 `methods/acceptance/
  live_run.py` 全文；注入两份实现——delivery 驱动 vs live 的
  `_injection_resolver`；阶段 0–6 不回环 `stages/pipeline.py:387-416`）。
- §9 不做清单与反过度设计约束逐项对齐：无新 hash/审计机制、无类型化报告类层次、
  无 DAG、无插件自动发现、无跨 run 调度。
- 验收只测量不做决定（§0.4/§5.2）：正确修掉"一个槽位里塞一圈小闭环"，
  且泄题降格为测量注记（本次不算数）而非验收内直接丢技能，语义等价性已由 §6
  golden 承诺兜住。
- `merge_check` 用参数而非注册两个名字（§3.3）：两变体共享 K5 主体（合并环、
  日志、预算硬阀），一个 if + 一个加载期检查最简；与 `authoring.exams` 开关的
  Params 先例同形。
- 出题/泄题归伪执行、`authoring.exams` 开关随之删除（§3.1）：live 的消融对
  （无 A 相位 + curation:none）改由序列本身表达，一个开关消亡，净减机制。
- `retry_limit` 由序列长度表达（§4.2）：消掉一个参数及其校验。
- 三件套接口 + "段/考题/归因是模块私有产物"（§2/§5.1）：中间产物退出阶段间接口，
  今天的痛点（Subtask/Exam 作为全局对象在 stage 间传递）确实存在。
- §6 如实披露 live 两处行为变化及其与 TB-dev 在跑正式 run 的不可比性——符合
  外层 AGENTS 第四节的诚实性规范。
- §7 不动清单点名 K1/K2/K3/K5 主体、续跑契约、旧 run 维护分支规则；迁移面收窄
  正确，且与 multi_method 方案 §6 的运维规则（run 固定维护分支续跑）衔接一致。
- 基础设施类异常四分类与处置未被触碰（`infra/reliability.py:42-86`）；
  方案全文扫描未发现任何新增运行时硬检查/校验门——两处"配置加载时检查"
  （§3.3 merge_check×伪执行、§5.5）是 `check_authoring` 同族的加载期约束，
  正当；manifest 冻结复用 B4，序列进 manifest 不是新 hash。

---

## 4. 七项重点判断的逐项结论

### 4.1 双抽象风险（B-1）

结论：**一张注册表，五槽位键改名，不建第二套**。机制（纯数据表、懒 import 驱动、
Params/check、光参数包）原样保留；delivery/curation/authoring 名字沿用，
segmentation 改挂 evidence，acceptance 重定义为只测量，新增 execution/decision
两键；core/config.py 的五槽位校验随 M4 一次性删除。逐文件清单见 B-1。

### 4.2 验收报告的形状（B-2）

结论：**读取器/视图，不是新落盘对象**。判卷理由、泄题判定、各轮 attempt、归因
计划、字节等价审计今天全部已按单元落盘且被审计/golden 直接消费；读取器形态让
续跑语义免费继承（账本即续跑契约）、"只测变化的部分"免费（K4 现行为：同版本
存档 attempt 复用）。新对象 = 两套真相 + 新崩溃窗口 + 报告有效性机制，三项都是
纯增重。最小行形状：五字段 dict（§1 B-2），证据字段只放引用。

### 4.3 配置形状（B-3）

结论：**保留 phases+sequence 命名引用**（L×4 重复是决定性理由），但统一为
"参数嵌套在模块键下"，修复 `compare: D_test` 未定义引用、删 `models` 死字段、
省 `bare_exam` 魔数；跨相位约束（n_attempts ≤ 对照弱臂深度）进 check。
示例里字段没有多余项是"嵌套后"的结论：`tasks`、`compare`、`improvement`、
`n_attempts`、`top_k`（归入 delivery 参数）各有所指。

### 4.4 技能来源字段（T-1/T-2）

结论：最小集 = `source_subtasks: List[str]` + 现有 `merged_from` + 考题改键到
`skill_id`，仅此而已。真实消费方核对：`blocks/retrieve.py:72-106` 只要
skill_id/content/group（不解析 ID）；`live_acceptance.pending_groups`
（`:400-426`）要任务归属（subtask 链可解）；`curation._original_sources/
_source_exams`（`:659-709`）要 merged_from + 原技能 subtask（考题查找）；
`blocks/author.py:377` 出题吃 Subtask 的视图（经来源 subtask 可得）。全部被
上述最小集覆盖。不建通用溯源系统（无时间戳/hash/逐技能 prompt/模型名）。
另建议（T-1）：M1 做成加法——保留 `make_skill_id`，只为合成技能新增
`skill/<serial>`，比全量换键更小且 §6 的 exam 行为不变核对更容易。

### 4.5 扩展性思想实验

- **a. 不切段学习**：新方案下约 3 个文件（evidence 新驱动整轨迹→单 subtask +
  注册表一行 + Params/测试），零新机制——前提是约定 **evidence 的输出仍是
  Subtask 对象**（写作/出题/判卷全不动）；建议把这条约定写进 §3.2。注意 §1 说
  旧框架"无法表达"言过其实（T-1），此格的真实受益者是合成技能。
- **b. 检索交付 + 真执行验收**：M2 完成去轮化（B-4）后 = 0 新模块 + 1 份相位
  配置 + 至多 1 条新具名决策规则。不做 B-4 则此格不兑现。
- **c. embedding 检索交付**：交付契约 `resolve(ctx, params, bank, tasks)` 容纳它
  （向量在 resolve 内算、审计行照写）；增量 = llm 层 1 个非 chat 调用 + providers
  表 1 行 + `delivery/embedding.py` 1 驱动 + embedder 角色（None 档现成）+ 插件
  真实查询文本（P5 ④，本就是方法自带依赖、会改 tasks_digest 即新实验）。约
  3 文件 + 2 注册行，方案零新机制；P5 当初跳过仍正确。
- **d. 多轮闭环**：序列写长即得，0 代码；前提是 B-3 修掉 compare 引用的良定义
  问题、T-6 定义"剩余反馈轮数"。"序列可写长、不做自动收敛"（§9）是对的边界。
- **e. 新 benchmark 插件**：AGENTS §7 四件套**原样成立**——E 的真执行经 K1/E1
  调插件，插件接口（load_tasks/run/view_rules/task_prompt）零改动，方案 §7 的
  不动清单包含 benchmarks 层。0 框架文件。

总评：五格中 b 依赖 B-4，d 依赖 B-3/T-6，a 依赖一条输出约定，c/e 已经兑现。
"组合即方法"的承诺总体成立，但兑现清单如上，不是无条件成立。

### 4.6 方案自身的过度设计苗头（判断 6）

- E 三模块（交付/执行方式/验收）：必要——§4.1 组合表的两根轴就是它们，缺一
  格子就塌。保留。
- L 四模块（决策/找证据/写作/整理）：保留，但两条边界要钉死：G10 计划本身是
  动作分配，决策只定单元级动作（T-3）；evidence 是可选模块（exam 不需要），
  "需要时才跑"的分支归 L 驱动（T-6）。
- "决策（门）"的抽象层级：**不高，是对的**——它是 §5.2 测量/决策分离的落点，
  修掉的就是 §1 的"验收里塞一圈小闭环"。但它的实现形状应是具名规则 + 复用既有
  提交 helper（T-4），不是新的交易/账本子系统；方案没有要求后者，实施守住即可。
- `merge_check` 参数 vs 两个注册名：**参数更简**，维持方案选择（§3.3）。
- §8 M1–M4：顺序正确（数据 → E → L → 序列），无多余中间态；两处补强——M2/M3
  验收加 golden 无 diff（T-5）、M2 补去轮化（B-4）。
- §3 模块表本身没有发现多余模块或多余字段；真正的过度设计风险全部集中在
  B-1（双注册表）、B-2（报告落盘）两处，均已在方案层面可修。

### 4.7 异常分类与恢复机制的保全（判断 7）

- **新增硬检查扫描**：方案全文未发现任何新 hash、新运行时校验门。仅有的两处
  检查（§3.3 merge_check 依赖、§5.5 考题由伪执行相位提供）都是**加载期** ConfigError，
  与现存 `check_authoring`/`check_live_run` 同族，正当且必要（否则错误配置会
  跑到半途才炸）。序列进 manifest 冻结复用 B4，不是新机制。维持 §9 的"不做"
  清单即可。
- **两套真相风险**：验收变只测量后，测量账本（verdicts/leak/audit）归 acceptance
  模块、技能终态写入归 decision 模块——每类写者唯一，无重叠。风险点是 decision
  若自建"动作账本"并与技能终态并存；按 T-4 处理（终态是真相，行是回执，不新增
  actions ledger）。
- **续跑语义挂哪**：原则——(1) 单元级状态只存在于模块拥有的 store 对象与账本
  （K1 的轨迹、K3 的 attempt、K5 的 merge 记录、K6 的 verdict 行，全部
  "终态/行先落盘，回执跟随"）；(2) 三个跨阶段对象中技能库与报告是**视图**
  （报告=读取器，B-2），轨迹集是 store 对象——跨阶段接口不携带任何独立持久
  状态，崩溃窗口零新增；(3) 相位驱动只拥有相位目录 + summary（今天的
  stage 目录 + stageX_summary.json 模式），序列驱动只拥有 manifest 冻结 +
  pipeline_log 追加，永不解释模块内部；(4) 决策的写入幂等可从盘上重推导
  （finalize_group 的先例：从 verdict 行重推 commit）。§7 的续跑契约声明与此
  一致，保留即可。
