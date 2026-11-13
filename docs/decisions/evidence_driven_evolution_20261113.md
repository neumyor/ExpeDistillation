# 决定：用带库训练执行的失败证据驱动演化

日期：2026-11-13
这份文档是 `simplified` 分支的第二轮方法修订与代码方案：删除文本考试与库整理，学习相位统一为"诊断带库训练执行里的失败 → 定位到子任务类型 → 程序分配证据 → 演化画像、playbook 与技能库"，技能是否有用交给终测衡量；本轮先实现整体框架并在 ALFWorld 上实测一次。
对应 log.md：2026-11-13 的"证据驱动演化方案"条目。

## 一、背景：现状的问题

以下数字来自参考 run `e1_ours_alfworld_20261030_2325`（不带 playbook，学生模型经网关）与
本分支的两次 ALFWorld 冒烟 run，统计脚本是一次性的 `tmp/open_vs_success.py`、
`tmp/plan_numbers.py`（不进 git）。

### 1.1 打开的卡没有带来收益

参考 run 的终测，带库臂 402 次 trial 按"有没有打开任何卡"分组，每个 trial 与它所在题目
的对照臂成功率配对：

| 带库 trial | 数量 | 带库成功率 | 同题对照成功率 | 差 |
|---|---|---|---|---|
| 打开了卡 | 164 | 0.860 | 0.861 | −0.001 |
| 没打开卡 | 238 | 0.954 | 0.913 | +0.041 |

整体 +2.4 个百分点全部来自没打开卡的 trial。弱模型往往在卡住时才开卡，"打开"一组天然偏难，
所以这张表不是因果结论；但现有数据里找不到"读了卡就做得更好"的证据。对照臂已有 89%，
可提升的空间约 11 个点。

### 1.2 playbook 压低打开率

2026-11-10 的回放实验（日志里的 prompt 原样重放，n = 40，T = 0）：去掉 "Global playbook:"
一块，第一步打开率在 vLLM 上从 0% 升到 83%，在网关上从 5% 升到 82%。带演化 playbook 的
终测打开率是 0%–4%（vLLM）、23%–31%（网关），不带 playbook 时是 42%。ALFWorld 的
playbook 替换提示词强制写入按任务类型的搜索顺序等内容，这是有意的设计（为了 ALFWorld 的
分数），本方案不改它。

### 1.3 文本考试测不到真实瓶颈

考试把"选卡"单独问一次，场景已经整理好，也没有环境动作与之竞争；真实执行里开卡是和环境
动作抢同一个回复。本分支两次冒烟共 6 张失败考卷，被考的卡全部被打开，5 张判为 `model`
（直接接受），没有一张判为 `trigger`：触发条件这一侧在实践中拿不到改写信号。考试判的是
"计划对不对"，不是"读了这张卡是否比不读更好"。

### 1.4 第一次学习按时机组织，整理产出未检验的大卡

- 五个触发时机族（开局、看过一圈后、命令失败时、提交前、任意一步）是"什么时候"，不是
  "哪个子任务"。同一子任务出现在不同位置会进不同的族；触发条件以时机短语开头，例如
  "At task start, when the instruction names a <target object> whose location is
  unknown"，对每道 ALFWorld 题都成立；
- 段筛选规则保留强模型全部成功段，弱模型本来就会的子任务也写成了卡；
- 摸底诊断指出的出错步骤只进画像和 playbook（提示词明写 "never a skill card"）；
- 最后一个学习相位的整理把卡合并成 18 步、触发条件覆盖几乎所有任务的大卡，合并产物直接
  接受，从未被检验。

### 1.5 画像没有接进决策

画像只给写作者读。冒烟里诊断写下"能打开对的卡，但不能把它转成可执行的计划"进了画像，
对应的卡却按 `model` 直接接受，没有任何东西因此改动。

### 1.6 数据量撑不起学习中的统计判定

参考 run 的摸底：弱模型 117 次里失败 20 次，分布在 6 类题上，每类 1–5 次；弱模型失败段
26 个。"B 臂成功次数严格多于 A 臂即通过"这种逐卡规则的误判率（两臂真实成功率均为 0.5）：

| 每臂样本数 | 无用的卡被判通过 | 真提升 0.5→0.7 判通过 | 真提升 0.5→0.8 判通过 |
|---|---|---|---|
| 6 | 39% | 67% | 80% |
| 12 | 42% | 79% | 92% |
| 30 | 45% | 93% | 99% |

整库对照同样不够：ALFWorld 训练集每轮 117 次、成功率 85%–90%，两组差的标准误约 4.3 个
百分点，5 个点的提升只有约 1.2 个标准误。功效足够的测量只有终测（134 题 × 3 次，按题配对）。

## 二、讨论过的方案

| 方案 | 做法 | 不选的原因 |
|---|---|---|
| A 保留文本考试 | 现状 | 1.3：测不到开卡瓶颈；通过不代表有用 |
| B 真实整题考试 + 程序判定 | 考试改为训练题上带库执行，"适用未打开"判触发缺陷 | 带 playbook 时不开卡的原因在 playbook，会把技能误判为触发缺陷并删掉；89% 的成功率下多数题落进"无证据"；单次执行噪声大 |
| C 同一状态三臂对照 | 重放前缀到弱模型卡住的局面，比较"只有 playbook / 直接给卡 / 真实目录" | 逐卡功效不够（1.6）；每类失败状态太少，避开写卡素材后多为 0–1 个；ALFWorld 一个检验相位约 400 次执行（摸底 1.7 倍），TB 无法廉价重放；C 臂前缀没有开卡历史，B 臂呈现方式与终测不同；新增重放、三臂缓存身份等机制，复杂度不降反升 |
| D 整库对照作门槛 | 每个检验相位比较"当前库"与"只有 playbook" | 训练集功效仍不够（1.6） |
| **E 证据驱动演化 + 终测消融** | 本文 | — |

## 三、选定的方法

### 3.1 核心

学习中不设任何统计关卡。学习相位只读带库真实执行里弱模型的失败，按程序事实把每条失败分配
给"写新卡 / 改触发条件 / 改正文 / 只进 playbook"，改完再去拿新的证据。技能有没有用，由
终测衡量（消融臂留到做消融时再加，见 3.8）。

三条标准的落点：

| 标准 | 落点 |
|---|---|
| 1 反事实对照 | 终测：带库臂对比对照臂；技能库在 playbook 之上的贡献由之后的消融臂 `no_skills` 测量 |
| 2 触发条件与正文分开检验 | 学习时按"出错前是否打开过这张卡"这一程序事实，把失败分给只改触发条件或只改正文的改写 |
| 3 对准弱模型失败的地方 | 学习只读带库执行里弱模型失败的轨迹，证据就是"带着当前全部知识仍然做不成"的地方 |

### 3.2 相位与序列

执行相位有三种角色：

| 角色 | 题 | 做什么 |
|---|---|---|
| `baseline` 摸底 | 训练集 | 强、弱模型裸跑，留下强模型的成功做法与弱模型的失败；只能是序列第一项 |
| `practice` 带库练习 | 训练集或验证集（相位字段 `tasks: train \| val`） | 只跑弱模型，交付与终测相同：技能文件夹（`folder` + `real_folder`）加演化的 playbook；失败交给下一次学习 |
| `test` 终测 | 测试集 | 不变 |

参考方法：

```
baseline → learn → practice → learn → practice → learn → test
```

三个学习相位用同一个定义 `learn`（序列允许重复名字），行为完全相同：第一次学习只是
"库里还没有卡"的特例。`practice` 就是终测那条执行路径用在训练集或验证集上，插件不需要新能力。

- **验证集**：划分增加第四个列表 `val`（与 `train`、`test`、`reserved` 两两不相交）。
  `practice` 在验证集上练习时，学习读的失败来自写卡材料之外的题，减轻"样本内"风险（风险 4）；
  写卡的正面材料仍只来自摸底里强模型在训练集上的成功轨迹。ALFWorld 的验证集取 `valid_seen`
  里按训练集各任务类型的题数分层抽取的 39 局（固定种子，写进 `scripts/run/paper_splits.py`）；
  测试集仍是 `valid_unseen` 的 134 局；
- **采样次数**：`practice` 的执行参数 `attempts`（ALFWorld 3、TB 2、SearchQA 1），缺省取执行者的
  全局次数。

### 3.3 子任务类型表与技能卡

- **子任务类型表**是 run 级对象：每个类型有程序生成的编号 `type/NNN`、名字、可观察的起点状态
  （例如"手里拿着目标物、任务要求它是热的"）、创建它的学习相位。表只增不改；
- **段 → 类型**：每个学习相位把本相位新切出的段（标签 + 描述）连同现有类型表交给归类调用
  （`SUBTASK_TYPING`，角色 segmenter），每段归入现有类型或新增类型；段数超过
  `typing_chunk_size`（缺省 100）时按段编号分块，每块都带完整的现有类型表；
- **一个类型最多一张卡**：`skill_id = <type_id>/skill`。重复卡在结构上不会产生，不需要整理；
- 卡的结构不变：触发条件（`summary` + `when_to_use`，目录里可见）与正文（`steps` +
  `not_applicable_when`，打开后可见）。`when_to_use` 写这个子任务的起点状态，不写时机；
  单一子任务的卡要短（3–7 步）。

### 3.4 学习相位的五步

每个学习相位按同样五步执行；输入是紧邻的上一个执行相位（必须是 `baseline` 或 `practice`，
是 `test` 时报错，与现在一致）。

**第 1 步：诊断。** 对上一个执行相位里弱模型每张判为失败的答卷调用同一个提示词
`TRIAL_FAILURE_ANALYSIS`（角色 author）。输入：协议、任务说明、完整的执行步骤（含读取动作）、
执行者看到的目录与 playbook、执行者打开过的卡全文（摸底没有这些，填"(none)"）。输出：

- `reason`：为什么失败；
- `failing_step`：决定性出错的那一步（0 起，必须在步数范围内）；
- `scope`：`subtask`（出错属于某个子任务）或 `general`（格式、反复空转这类与子任务无关的问题）；
- `model_note`、`playbook_note`：同现在。

越界或空字段的回复带原因重问，最多 2 次；仍不可用记进 `failed_units.jsonl`，该答卷既没有笔记
也没有证据。诊断不读画像，保持证据独立。

**第 2 步：定位。**

1. 切段（K2，参数 `K`、`retry_limit`）每条失败轨迹；`failing_step` 所在的段是出错段；
2. 写卡的正面材料来自摸底里强模型的轨迹：第一次学习按 `trajectory_select` 规则选出的强模型
   轨迹全部切段（与诊断并行，见 3.9），之后的学习相位直接复用；
3. 归类调用把本相位新切出的全部段归入类型表。

出错段的类型就是这条失败的类型。切段或归类不可用的失败记进 `failed_units.jsonl`，它的笔记照常
进第 4 步，但不产生卡证据。

**第 3 步：分配证据**（纯程序，`composites/evidence.py`）。对每条诊断成功、定位成功的失败：

| 程序事实 | 证据类别 |
|---|---|
| `scope = general` | 无卡证据（笔记照常进画像与 playbook） |
| 该类型没有在用的卡 | 缺口（gap） |
| 有卡，出错那一步之前没有打开过它 | 触发证据 |
| 有卡，出错那一步之前打开过它 | 正文证据 |

"打开过"取自轨迹里的读取动作（`composites/skill_use.py`，`cat` 与 `ReadSkill[...]` 同等看待）
及其步号，不取模型自述。每条失败的分配结果写进相位目录的 `evidence.jsonl`。

**第 4 步：演化画像，再演化 playbook。** 与现在相同（`PROFILE_UPDATE`、`PLAYBOOK_UPDATE`
及 benchmark 替换提示词）。playbook 写作者另外收到一段程序事实：本相位的触发证据
（哪些类型的卡在失败里出错前没有被打开、各几次）。第一次学习仍读 `playbook_facts`
从摸底答卷算出的事实。

**第 5 步：演化技能库。**

| 证据 | 动作 | 提示词与输入 |
|---|---|---|
| 缺口 | 写一张新卡 | `CARD_WRITE`（角色 author）：同类型的强模型成功段（正面做法）、本相位该类型的弱模型失败段（出错之处）、画像、当前 playbook。写作者只写 playbook 没覆盖的部分。没有任何同类型的强模型成功段时不写卡，回执 `skipped_no_material` |
| 触发证据 | 只改触发条件 | `REVISE_TRIGGER`（角色 rewriter）：卡、执行者看到的目录、失败轨迹从该段起点到出错步的真实步骤、当前 playbook、画像 |
| 正文证据 | 只改正文 | `REVISE_CONTENT`（角色 rewriter）：卡、打开后仍失败的真实步骤、诊断原因、画像、当前 playbook，按画像里的弱点加防护 |
| 无证据 | 不动 | — |

- 一次改写只改一侧，另一侧逐字节不变，版本号加一；同一张卡在一个相位里两侧都有证据时，
  先改正文、再改触发条件，各自一个版本；
- **删除规则**：一张卡在相邻两个学习相位都拿到正文证据（改过一次正文仍不管用），
  第二次不再改写，直接删除，原因 `content_failed_twice`；
- 卡没有"待检验 / 已接受"两种状态，只有 `active` 与 `discarded`。交付只用 `active` 的卡；
- 每个版本照常经 `record_version` 写 Provenance（`cause` = `add` / `revise_trigger` /
  `revise_content`，`sources` 是证据位置），先写来源再存技能。

**提交点与续跑**沿用现在的做法：诊断、切段、归类、写卡、改写都逐单元落盘，`learn_summary.json`
最后写；改写调用前先写起始版本（版本守卫）。

### 3.5 学习参数与消融开关

本轮实现主方法用到的参数：`review.K`、`review.retry_limit`、`review.write_lessons`、
`rules.trajectory_select` / `rules.segment_select`（都已存在），`author` 的 `typing_chunk_size` /
`max_segments_per_card` / `max_chars_per_segment` / `write_retry_limit`，`practice` 的 `tasks`
与 `attempts`，执行相位的 `playbook`。

留到做消融时再实现（设计已定，本轮不写代码、不生成配置）：`review.revise_scope`
（`trigger_only` / `content_only`：范围外的证据只记录、不改写、不计入删除）与 `review.cards`
（`false` 时不写卡、不改卡，即 `no_skills` 臂）。

### 3.6 画像、playbook 与技能库的分工

| 对象 | 由什么证据驱动 | 谁读 | 在本方案里的作用 |
|---|---|---|---|
| 画像 | 全部失败的 `model_note`，其中"读了卡仍失败"的正文证据最直接 | 写卡、改卡、写 playbook 的写作者 | 决定正文怎样改才适合这个学生 |
| playbook | 全部失败的 `playbook_note` + 摸底事实 + 触发证据计数 | 执行者，每一步可见 | 常驻的通用做法；ALFWorld 的强制部分不变 |
| 技能库 | 按子任务类型分配的缺口、触发、正文证据 | 执行者，目录常驻、正文按需打开 | 只写 playbook 没覆盖的子任务做法 |

### 3.7 终测

终测不变：测试题上用技能文件夹、显示演化的 playbook，对照臂不带 playbook。ALFWorld 的对照是
学生模型冻结的裸跑成绩文件。每个 `practice` 相位的摘要另报机制指标（只描述，不做显著性判断）：
成功率、每张卡被打开的次数、打开后成功的次数；学习相位的摘要报各证据类别的计数。

### 3.8 决定记录

**2026-11-13 上午确认的五个决定点**（用户让按建议定）：

| 决定点 | 决定 |
|---|---|
| 1 序列 | 七相位 `baseline → learn → practice → learn → practice → learn → test`；最后一次学习的改写不再执行，直接带进终测（风险 5） |
| 2 `practice` 采样次数 | ALFWorld 3、TB 2、SearchQA 1，写进配置 |
| 3 `no_skills` 消融臂 | 加（随消融一起实现，见下表第 2 条） |
| 4 删除规则 | 只有相邻两个学习相位都拿到正文证据才删 |
| 5 e3/e4/e5 终测是否显示 playbook | 本轮不跑消融，暂不决定 |

**2026-11-13 下午用户的补充**：

| 补充 | 落实 |
|---|---|
| 1 `practice` 可选在训练集或验证集上练习 | 划分增加 `val`，`practice` 相位字段 `tasks: train \| val`（3.2） |
| 2 先不跑消融，实现整体框架后在 ALFWorld 上实测一次完整结果；P01 复用已有结果 | 消融开关与 e2–e5、`no_skills` 配置推迟（3.5）；论文配置生成脚本只保留 e1；新增 `import-baseline` 把已有 run 的摸底轨迹写进新 run（7.6） |
| 3 流程中能并行的环节尽量并行 | 学习相位按依赖关系分组并行，结果按确定顺序提交，与串行逐字节相同（3.9） |

**本方案改变的早先决定**（log.md 13:52 条目里的八个设计点）：

| 早先的决定 | 现在 |
|---|---|
| 2 `model` 与 `scene` 判定直接接受 | 不再有这两类：`scene` 由程序事实取代，`model` 并入正文证据 |
| 3 每道考题只作答一次 | 考试删除，不再适用 |
| 4 之后的学习相位不写新技能 | 每个学习相位都可为缺口写卡 |
| 5 最后一次学习时有缺陷的技能直接删除 | 最后一次学习照常改写；删除只看"相邻两轮都有正文证据" |
| 8 终测不加"只带 playbook"的对照臂 | 终测对照臂仍不带 playbook；"只演化 playbook"的作用由之后的消融臂 `no_skills` 测量 |

第 1、6、7 条（只有被打开的卡才可能拿正文证据；论文主结果开 playbook；画像只给写作者）保持。

### 3.9 并行

学习相位的五步按数据依赖分组，组内全部单元放进同一个并行池（并行数 `--workers`），组与组之间
顺序执行：

```
组 A  诊断（每张失败答卷）  ‖  切段（每条失败轨迹）  ‖  切段（摸底强模型轨迹，仅第一次学习）
组 B  归类（按块顺序；通常一块）  ‖  演化画像
组 C  分配证据（纯程序，瞬时）
组 D  演化 playbook（要读本相位的画像与触发证据计数）
组 E  写新卡与改写（每张卡一个单元；同一张卡先改正文再改触发条件）
```

- 切段不等诊断：失败轨迹无论诊断给出 `subtask` 还是 `general` 都先切，`general` 的段只是不被
  用到，换来与诊断同时进行；
- **确定性**：并行只用于计算，落盘的 JSONL 账本按确定顺序（答卷 id、段 id、类型 id、技能 id）
  提交，对象按确定 id 存储，所以并行与串行的产物逐字节相同；golden 矩阵另加一条测试，
  比较 `--workers 1` 与 `--workers 8` 的快照；
- 执行相位的 rollout 已按 `--rollout-workers` 并行（K1）；`practice` 只有弱模型一个臂；
  终测在 ALFWorld 上读冻结的对照文件，零执行。相位之间是数据依赖，不重叠。

## 四、数据模型与配置

| 项 | 变化 |
|---|---|
| 配置 schema | 8 → 9：执行角色 `baseline` / `practice` / `test`（删 `exam`）；划分增加 `val`；`practice` 的 `tasks` 取 `train` 或 `val`；学习相位删掉 `curate` 与 `author` 的时机族参数；`real_folder` 增加 `attempts` |
| `Skill` schema | 4 → 5：增加 `subtask_type`；`status` 取 `active` / `discarded`；删除 `merged_from`；`skill_id = <type_id>/skill`（导入技能仍为 `import/<来源>/<键>`，`subtask_type` 为空） |
| 新对象 | 子任务类型（`type_id = type/NNN`、`name`、`entry_state`、`phase_label`），run 级 `stage2_segments/subtask_types.jsonl`；段的归类结果 `stage2_segments/segment_types.jsonl`；学习相位的 `evidence.jsonl` |
| 删除的对象 | `Exam`、`Attempt` |
| 轨迹编号 | `Trajectory` schema 2 → 3：编号带相位，`traj/<相位标签，冒号换成下划线>/<task_id>/<role>/<attempt>`（如 `traj/p03_practice/...`），同一道题在 `baseline` 与各次 `practice` 里的轨迹、以及由它们切出的段不再撞号 |
| `Subtask` | schema 1 → 2：增加 `phase_label`（切出它的学习相位）。段、子任务类型、段归属都记创建相位，`fork-run` 只拷前缀相位产生的共享对象 |
| Provenance | 去掉 `scene`（考题切点）；`sources` 是证据位置 |
| 模型角色 | 删除 `examiner`、`answerer`、`curator`；保留 `judge`（只供 `judge-skills` 分析工具） |

## 五、删除与改写清单

由两次只读代码普查（2026-11-13）得出，路径相对 `src/expedistill/`，行数是 `wc -l`。

**整文件删除**

| 机制 | 文件 | 行数 |
|---|---|---|
| 文本考试 | `blocks/exam.py`、`stages/phase_execute/exam.py`、`stages/exam_replay.py`、`composites/exam_answering.py`、`exam_grading.py`、`exam_once.py`、`exam_facts.py`、`acceptance_skill_status.py`、`prompts/exam.py`、`methods/delivery/exam_folder.py`、`stages/learn_exam.py` | 2,031 |
| 库整理 | `composites/curation.py`、`curation_waves.py`、`blocks/curate.py`、`prompts/curate.py`、`methods/curation/`（`agentbrew.py`、`none.py`、`__init__.py`、`README.md`、`phase_bank.py`） | 1,803 |
| 时机族与聚类 | `methods/segment_learn/cluster.py`、`stages/learn_segments.py`、`prompts/cluster_author.py`（可复用的部分先移出，见 7.4） | 1,200 |
| 测试 | 考试 7 个文件、整理与聚类 8 个文件 | 约 3,900 |

**大改**

| 文件 | 改什么 |
|---|---|
| `core/config.py` | 角色、划分 `val`、位置规则（第一项必须是 `baseline`）、`practice` 的任务与交付规则、学习相位字段、`ROLE_DEFAULTS`、schema 9 |
| `core/models.py`、`core/ids.py` | 删 `Exam` / `Attempt` 与其 id；`Skill` schema 5；`Subtask` schema 2；新增 `SubtaskType`；`Trajectory` schema 3 与带相位的轨迹 id；删 `bank/merge` id |
| `composites/decision.py` | 只留 `parse_unit_id`、`final_test_delta`、`control_arm_source` |
| `composites/learn_ledger.py` | 留回执、版本守卫与 `record_version`；删 `land_accept`、`accept_written`、任务清单；`land_delete` 改为删除规则 |
| `stages/phase_learn.py` | 五步流程与 3.9 的分组并行；删 `_check_nothing_pending`、`remaining_checks` 与考试分支 |
| `methods/diagnose/` | 单一诊断 `diagnose_trial`；`Finding` 换成 `reason` / `failing_step` / `scope` / 两条笔记 |
| `methods/revise/` | 输入换成证据的真实步骤；删除 `land_accept` 路径，只写回执 |
| `methods/delivery/units.py` | `accepted_bank` 改为 `active_bank` |
| 相位库工件 | 写入逻辑从 `methods/curation/phase_bank.py` 与 `none.py` 移到 `composites/phase_bank.py` |
| `stages/phase_execute/` | `baseline.py` 只做两臂裸跑；新增 `practice.py`；`exam.py` 删除；`resolve.py` 让 `practice` 也解析执行者角色与文件夹能力；`engine.py` 让空库的文件夹单元仍挂清单 |
| `stages/fork_run.py` | 去掉"后续检验"与 `merged_from`，改按创建相位过滤共享对象 |
| `prompts/` | 见 7.4 |
| `prompts/learning_context.py` 与三个提示词包 | 删 `TRIGGER_FAMILY_KEYS`、`trigger_timing_slots` 及其四张表 |
| `llm/fake_provider.py` | 删考试、整理、分族的处理函数；新增归类、写卡、新诊断的确定性回复 |
| `scripts/run/build_paper_configs.py`、`paper_splits.py` | 只保留 e1（三个 benchmark 的 `ours` 与只测臂）；新序列与相位定义、角色表、schema 9；`paper_splits` 增加 ALFWorld 的 `val` 列表 |

## 六、论文实验矩阵（本轮）

| 实验 | 臂 | 说明 |
|---|---|---|
| e1 | `ours` | 参照方法（3.2）；三个 benchmark 各一份，本轮只在 ALFWorld 上跑 |
| e1 | 外部方法 `_folder` / `_whole`、裸跑臂 | 不变（只有 `test`），随 schema 9 重新生成 |

**推迟的消融臂**（臂定义已定，做消融时加回生成脚本）：

| 实验 | 臂 | 与 `ours` 的差别 |
|---|---|---|
| e2 | 教师 → 学生组合 | 序列 `baseline, learn, practice, test`：写出的卡不演化，`practice` 只做测量；`write_lessons: false` |
| e3 | `k1` / `k3` | `review.K` |
| e4 | `a0` | 序列 `baseline, learn, test` |
| e4 | `a1_body` / `a2_trigger` | `revise_scope: content_only` / `trigger_only` |
| e4 | `joint9` / `staged9` | 九相位：再多一对 `practice, learn`；`staged9` 前两次学习分别只改正文、只改触发条件 |
| e4 | `no_skills` | `review.cards: false` |
| e5 | `allcover_k*` | `trajectory_select: all` + `segment_select: all` |

## 七、代码方案

### 7.1 范围

- **实现**：三种执行角色与 `val` 划分、学习相位五步与分组并行、`import-baseline`、空库文件夹单元的
  呈现修正、相关提示词、配置生成（只 e1）、测试与 golden、README 与 AGENTS.md 同步；
- **推迟**：`review.revise_scope`、`review.cards`、e2–e5 与 `no_skills` 的配置；
- **删除**：第五节整文件删除的全部内容，以及它们的测试与 golden 快照。

每个阶段结束都跑 `pytest tests -q`、`ruff check src tests scripts --select E9,F`、
`pytest tests/test_layering.py`，全绿才进入下一阶段。

### 7.2 阶段 S1：数据模型与配置（串行，先做）

| 文件 | 内容 |
|---|---|
| `core/models.py` | `Trajectory` schema 3；`Subtask` schema 2（`phase_label`）；`SubtaskType`；`Skill` schema 5（`subtask_type`、`SKILL_STATUSES = ("active", "discarded")`，删 `merged_from`、`merged_source_subtasks`）；删 `Exam`、`Attempt` |
| `core/ids.py` | `make_trajectory_id(phase_label, task_id, role, attempt)`；`make_type_id(serial)`；`make_skill_id(type_id)`；删考试、作答、合并 id |
| `core/config.py` | `PHASE_ROLE_BASELINE` / `PHASE_ROLE_PRACTICE` / `PHASE_ROLE_TEST`；划分四键（`val` 可缺省为空）；加载期检查：第一项是 `baseline` 且只有它写 `arms`；`baseline` 用 `none` + `real`、`playbook: "off"`、任务 `train`；`practice` 用 `folder` + `real_folder`、任务 `train` 或 `val`（`val` 必须非空）；学习相位紧邻的上一个执行相位是 `baseline` 或 `practice`；`ROLE_DEFAULTS`；schema 9 |
| `core/vocabulary.py`、`core/answer_sheet.py`、`infra/store.py` | 删考试相关的原因词、`attempt` 记录种类、`exams` / `attempts` 存储种类 |
| `stages/stage0_freeze.py` | 划分四键进 manifest；`curation` 族不再解析 |
| `methods/registry.py`、`methods/execution/`、`methods/delivery/params.py` | 删 `exam`、`exam_folder`、整理族；`RealFolderExecutionParams` 加 `attempts`；`author` 族改为 `subtask_cards` + `CardAuthoringParams` |

### 7.3 阶段 S2：执行相位（S1 之后，可与 S3 并行）

| 文件 | 内容 |
|---|---|
| `stages/phase_execute/baseline.py` | 两臂裸跑，轨迹 id 带相位，其余不变 |
| `stages/phase_execute/practice.py`（新） | 弱模型一臂；任务取 `train` 或 `val`；物化技能文件夹（复用 `final_test.py` 的 `_folder_manifest` / `_materialize_folder`，提到 `phase_disk.py` 共用）；轨迹写进 run 级存储；无效重派；摘要报机制指标 |
| `stages/phase_execute/final_test.py` | 只取 `active` 的卡；空库呈现修正 |
| `stages/phase_execute/engine.py`、`resolve.py`、`__init__.py` | 空库的文件夹单元仍挂清单（带 `playbook_text`）；`practice` 解析执行者与文件夹能力；角色分发 |
| `benchmarks/alfworld/`、`searchqa/`、`tb/plugin.py`、`benchmarks/skill_folder.py` | 空清单时仍用文件夹执行提示词、显示 playbook、目录为空；TB 空库时仍上传 skill-read 后缀 |
| `methods/delivery/units.py`、`whole_bank.py`、`folder_delivery.py` | `active_bank`；删 `accepted_only=False` 路径 |
| 删除 | `stages/phase_execute/exam.py`、`stages/exam_replay.py`、`composites/exam_*`、`acceptance_skill_status.py`、`blocks/exam.py`、`prompts/exam.py`、`methods/delivery/exam_folder.py`、`cli.py` 的 `exam-replay` |

### 7.4 阶段 S3：学习相位（S1 之后，可与 S2 并行）

**提示词**

| 提示词 | 来源 | 要点 |
|---|---|---|
| `TRIAL_FAILURE_ANALYSIS` | 合并 `BASELINE_FAILURE_ANALYSIS` 与 `TRIAL_EXAM_ANALYSIS` | 输出 `reason` / `failing_step` / `scope` / 两条笔记；目录、playbook、打开过的卡作为输入 |
| `SUBTASK_TYPING` | 由 `CLUSTER_SUBTASKS` 改出，放进新文件 `prompts/cards.py` | 输入现有类型表与一批段卡片；每段归入现有类型或新增类型（名字 + 起点状态）；每个段 id 恰好用一次 |
| `CARD_WRITE` | 由 `CLUSTER_SKILL` 改出，放进 `prompts/cards.py` | 去掉时机族与开头短语；输入类型、强模型成功段、弱模型失败段、画像、playbook；3–7 步 |
| `REVISE_TRIGGER` / `REVISE_CONTENT` / `REVISION_DIAGNOSIS` | 原有 | 去掉开头短语规则与 `timing_menu`；考试场景与计划换成证据的真实步骤 |
| `PLAYBOOK_UPDATE`、`ALFWORLD_PLAYBOOK_UPDATE`、`TB_PLAYBOOK_UPDATE` | 原有 | `{facts}` 的说明改为"第一次学习是摸底事实，之后是触发证据计数" |
| 删除 | — | `CLUSTER_TRIGGER_FAMILY`、`SIMILARITY`、`MERGE`、`EXAM_*`、`learning_context` 的时机槽 |

**代码**

| 文件 | 内容 |
|---|---|
| `methods/diagnose/` | `diagnose_trial(sheet_facts, handle)` → `Finding`；越界重问 |
| `composites/sheet_facts.py`（新） | 从一张执行答卷读出目录、playbook、打开过的卡及其首次读取步号（复用 `skill_use.skill_read_events`） |
| `blocks/subtask_types.py`（新） | G2 的回复校验（复用原聚类回复的 id 唯一性校验）与类型编号分配 |
| `composites/locate.py`（新，K3） | 切段（复用 `segment_checked`）、归类分块、类型表与段归属只追加、续跑只归类还没有归属的段 |
| `composites/evidence.py`（新） | 证据分配、删除规则、触发证据计数；纯函数 |
| `methods/cards/`（新） | `CardAuthoringParams`；`material`（由 `segment_learn/material.py` 改出：按类型取强模型成功段与弱模型失败段并渲染）；`write_card`（由 `cluster.write_cluster_skill` 改出，复用 `build_card`） |
| `methods/revise/` | 新输入；删 `land_accept` 路径 |
| `composites/learn_ledger.py`、`composites/phase_bank.py` | 见第五节 |
| `stages/phase_learn.py` | 五步、3.9 的分组并行与确定顺序提交、摘要 |
| `llm/fake_provider.py` | 新提示词的确定性回复；删旧处理函数 |
| 删除 | `stages/learn_segments.py`、`stages/learn_exam.py`、`methods/segment_learn/`、`prompts/cluster_author.py`、整理全部、`composites/decision.py` 的动作表部分 |

### 7.5 阶段 S4：工具、配置与复用摸底（S2、S3 之后）

| 文件 | 内容 |
|---|---|
| `stages/import_baseline.py`（新）+ `cli.py` 子命令 `import-baseline` | 见 7.6 |
| `stages/fork_run.py` | 按创建相位过滤共享对象 |
| `stages/judge_skills.py` | 也接受 `practice` 相位 |
| `stages/segmentation_metrics.py`、`cli.py` 的 `summary` | 文案与新摘要键 |
| `scripts/run/paper_splits.py` | 增加 `ALFWORLD_VAL`（`valid_seen` 分层抽样 39 局，固定种子，列表写死） |
| `scripts/run/build_paper_configs.py` | 只生成 e1：三个 benchmark 的 `ours` 与只测臂；`ours` 的 `practice` 默认 `tasks: train`，另生成一份 `tasks: val` 的 ALFWorld 臂 `ours_val`；角色表与 schema 9 |

### 7.6 复用摸底：`import-baseline`

```
expedistill import-baseline --source-run <已有 run> --config <新配置> --run-dir <新 run 目录>
```

- 在新 run 第一次 `run` 之前执行（与 `import-bank` 一样准备 run 目录）。读源 run 的
  `manifest.json` 与 `stage1_baseline/trajectories/`，把轨迹按新规则改写 `trajectory_id`
  （相位标签取新配置序列第一项的标签，如 `p01_baseline`）后写进新 run 的轨迹存储，其余字段原样；
- 同一性检查，任何一条不过即报配置错误、什么都不写：benchmark 相同；两个执行模型名相同；
  两臂采样次数相同；新配置的 `train` 列表 ⊆ 源 run 的任务集；每个（任务、角色、序号）恰好一条；
  源轨迹 schema 为 2（与现在的字段一致，只是编号不带相位）；
- 写 `BASELINE_ORIGIN.md`：源 run 路径、核对结果、逐角色条数与成功数、全部轨迹文件的 sha256 清单摘要；
- 之后正常 `run`：p01 的执行单元全部"已存即已完成"，零模型调用，照常产出答卷、判定与摘要；
  无效轨迹照常按 `invalid_redispatch_limit` 重派（会真实执行）。

这是只为一种产物（摸底轨迹）服务的窄工具，不读源 run 的任何其他产物。

### 7.7 阶段 S5：测试与 golden

- 删除第五节列出的测试文件；改写 `test_config*`、`test_models`、`test_ids`、`test_phase_execute`、
  `test_phase_learn`、`test_fork_run`、`test_stage0`、`test_templates`、`test_fake_provider*`、
  `test_build_paper_configs` 等（清单见代码普查）；
- 新增单元测试：`evidence`（分配表每一格、删除规则、"出错前打开"的边界：同一步与之后一步）、
  `subtask_types`、`locate`（续跑只归类缺的段）、`cards`、`diagnose_trial`、`practice`、
  `import_baseline`（每条同一性检查各一个反例）、配置的位置规则与 `val`；
- 组合测试：学习相位在组 A、组 E 中途崩溃再续跑，与一次跑完相同；并行与串行逐字节相同；
- golden 两个组合（`whole_bank`、`folder`）跑新七相位，崩溃点"两次诊断之间、改写调用前、
  两张新卡之间"，`revision_window.py` 改为"改写已落盘、下一个 practice 未执行"；重新生成快照。

### 7.8 阶段 S6：文档

`README.md`、`AGENTS.md` 第四节（模块）与第八节（golden）按设计正本同步；
`src/expedistill.egg-info/SOURCES.txt` 随重新安装生成。

### 7.9 分工

S1 由一个执行者完成并合入后，S2 与 S3 由两个执行者并行（文件不重叠：S2 只动
`stages/phase_execute/`、`methods/delivery/`、`benchmarks/`；S3 只动 `prompts/`、`blocks/`、
`composites/` 的学习件、`methods/` 的学习件、`stages/phase_learn.py`、`llm/fake_provider.py`）；
`cli.py`、`core/` 与 `__init__.py` 的导出由编排者统一合并。S4–S6 串行。

### 7.10 ALFWorld 实测计划

1. **复用的摸底**：`runs/exp3_k5_v4_20261103_0957`（在 `ExpeDistillation-MM-submit` 下）的
   p01：39 道训练题、强 D / 弱 Q、各 3 次，共 234 条轨迹（强 88/117 成功，弱 68/117 成功），
   协议已与统一 runner 对齐、输出上限 65536、没有一次因长度截断（核对见第九节第 13–15 条）；
   导入前先抽样把源 run 里第二步以后（带历史）的请求按当前模板重新渲染，逐字节比较，不同即停下；
2. **对照**：终测用 `configs/seed/alfworld_bare_q_20261102.json`（同一协议；Q 的输出上限 4096，
   12,402 次调用里 24 次因长度截断，约 0.2%）；
3. **端点不变量**：Q 在摸底与对照里的回复长度中位数是 375 与 386 token，同一服务栈；正式 run
   前先用 1–2 道题跑一次 practice，Q 的回复长度中位数必须落在同一范围，否则说明端点变了，停下；
4. **小样本干跑**：2 道训练题、1 道测试题跑完七相位，核对 7.7 的不变量与第九节第 1 条的空库呈现；
5. **正式 run**：`practice` 在训练集上（或用户选的验证集），每次 117 局（39 × 3）；终测 134 × 3 = 402 局；
6. **完整性检查**：设计正本第七节清单全部通过后再使用数字，结果写进 `docs/reports/`。

## 八、风险

1. **证据稀疏**：ALFWorld 上带 playbook 的弱模型失败得少，每轮可读的失败有限，演化慢；本地
   执行便宜，可加大 `practice` 采样次数；
2. **打开率被 playbook 压住**：触发证据会大量堆积，改触发条件可能扭不动；在 `no_skills` 臂加上之前，
   技能库在 playbook 之上的贡献无法与 playbook 分开；
3. **出错步由大模型定位**：定位错了，证据会分给错的卡；`findings.jsonl` 与 `evidence.jsonl`
   保存每条判断，可事后复核；
4. **样本内**：`practice` 在训练集上时，学习读的就是写卡的题；在验证集上练习可以减轻；
5. **最后一次学习的改写不再执行**：直接带进终测，效果只在终测里测得出；
6. **类型表漂移**：归类可能把同一子任务分成两个类型，从而写出两张相近的卡；没有合并机制，
   靠归类调用每次都看到完整类型表来抑制；
7. **类型在 TB / SearchQA 上很少重复**：TB 的题彼此差异大，SearchQA 的轨迹很短，类型多为一类
   一个实例，卡更像单题教训；能否迁移只看终测；
8. **缺正面材料**：没有同类型强模型成功段的缺口不写卡，只进 playbook 笔记；
9. **复用的摸底与对照的输出上限不同**：摸底 65536、对照 4096，Q 在对照里约 0.2% 的回复被截断。

## 九、对照代码复核

2026-11-13 对照工作区代码（`simplified` 分支，未提交的第一轮重构之上）做了两次只读普查，
逐条核对本方案依赖的代码事实；下午又为复用摸底核对了已有的 ALFWorld run（第 13–16 条）。
结论：方案可以实现，下列各条已据此写进第三至七节；其中第 1–4 条是方案原稿没想到、复核后补上的。

| # | 复核结果 | 对方案的影响 |
|---|---|---|
| 1 | **空库的文件夹单元会换一种呈现方式。** `stages/phase_execute/engine.py` 的 `plan_units`：清单里没有文件时不挂清单（"the unit runs bare"），插件于是走非文件夹路径：ALFWorld / SearchQA 改用不带读取规则的普通执行提示词，playbook 经 `with_playbook` 拼进的 `skills_block` 出现在技能槽里；TB 把这段文本当任务级 `SKILL.md` 走技能模式上传，而 `playbook_in_skills_block = False` 说明 TB 不该这样显示 playbook | `no_skills` 臂的 playbook 位置与提示词会和带库臂不同，两臂的差就混进了呈现方式的差。实施第 3 阶段让空库的文件夹单元仍挂上带 `playbook_text` 的清单、用同一个文件夹提示词，TB 在空库时仍上传 skill-read 后缀；冒烟核对这一条（第七节第 7 步）。现有终测遇到空库 + `playbook: evolved` 时也是这样 |
| 2 | **重复的训练执行会撞轨迹编号。** 轨迹 id 是 `traj/<task>/<role>/<attempt>`（`core/ids.py`），而 K2 按轨迹 id 续跑（`composites/segment_checked.py`），会把 `practice` 的失败轨迹当成已切过的摸底轨迹，悄悄复用旧段 | 轨迹 id 带相位（`Trajectory` schema 3），`Subtask` 记 `phase_label`（schema 2） |
| 3 | **相位库工件的写入在整理代码里。** `methods/curation/phase_bank.py`（`produce`、`_with_pending_members`）与 `methods/curation/none.py` 的 `write_as_is` | 删整理前先把写入逻辑移到 `composites/phase_bank.py` |
| 4 | **`fork-run` 依赖 `merged_from` 与"后续检验"**（`stages/fork_run.py` 的 `_later_products`、`_checks_left`） | 改为按创建相位过滤共享对象 |
| 5 | 诊断今天不输出出错步：`BASELINE_FAILURE_ANALYSIS` 只在文字里要求指出步骤，`Finding` 没有步号字段。诊断看到的步骤视图从 0 编号（`render_steps_view`），与 `Subtask.step_range` 一致 | 新诊断提示词的 `failing_step` 可直接映射到段 |
| 6 | 打开记录带步号：`composites/skill_use.skill_read_events` 返回每张卡第一次读取的 `step_index`，TB 的 `cat` 与本地的 `ReadSkill[...]` 都覆盖 | "出错前是否打开过"可由程序判定，不需要新解析 |
| 7 | 可复用件：K2 `segment_checked`、`collect_material`、`build_card`、聚类回复的 id 唯一性校验、`record_version` 与 `trace_of_trajectory`、`learn_segments.py` 里按 infra / 模型错误分流的失败记账 | 第 4 阶段在这些件上搭，不从零写 |
| 8 | 交付只认 `accepted`：`methods/delivery/units.accepted_bank` 被 `whole_bank`、`folder` 与终测共用 | 改为只取 `active` |
| 9 | `judge-skills` 写死只读 test 相位（`stages/judge_skills.py` 的 `phase_kind="test"`） | 设计正本说它也可读 `practice`，第 5 阶段放开 |
| 10 | 删掉 `examiner` / `answerer` / `curator` 角色会让 86 份配置全部加载失败（`core/config.py` 拒绝未知角色） | 与 schema 9 一起由生成脚本重新生成 |
| 11 | 三个 playbook 提示词（`PLAYBOOK_UPDATE` 与 ALFWorld、TB 的替换提示词）把 `{facts}` 写成"第一轮之后为空" | 改提示词文字；按 AGENTS 第七节这是新实验，旧 run 不续跑（本来就不续跑） |
| 12 | `revise_scope` 的旧消费者在 `stages/learn_exam.py`（删除）与 `phase_learn.py` | 本轮推迟实现（3.5）；做消融时按"范围外的证据只记录"放进证据分配 |
| 13 | **参考 run 的摸底不能复用。** `e1_ours_alfworld_20261030_2325` 早于 2026-11-02 的 `956a50f`（ALFWorld 执行协议与统一 runner 对齐），协议不同：它的摸底强 111/117、弱 96/117 成功，对齐后的摸底是强 88/117、弱 68/117 | 复用对齐之后的摸底 |
| 14 | **可复用的摸底是 `exp3_k5_v4_20261103_0957` 的 p01。** 之后的 ALFWorld run 都装配了这份摸底（各自的 `ASSEMBLED_FROM.md`）；39 道题与 `ALFWORLD_TRAIN` 相同，强 D / 弱 Q 各 3 次；18,562 次执行调用的输出上限都是 65536，全部正常结束（`stop`）；第一步的请求按当前模板重新渲染后逐字节相同（manifest 里的模板摘要不同，是模板写法变了，渲染结果没变）。第二步以后带历史的请求还没逐字节核对 | 7.10 第 1 步；导入前补做第二步以后请求的抽样核对 |
| 15 | **对照文件。** 生成脚本用的 `alfworld_bare_q_20261102.json` 来自 `e1_bare_q_20261102_1735`：协议已对齐（抽 20 个第一步请求，逐字节相同），输出上限 4096，12,402 次调用里 24 次因长度截断；Q 的回复长度中位数 386 token，与摸底的 375 相近，说明是同一服务栈（后来 vLLM 上的 run 中位数是 116）。另一份 `alfworld_bare_q_pooled_20261102.json` 汇总的是对齐前的 run，不能用 | 7.10 第 2、3 步 |
| 16 | 本地 ALFWorld 数据有 `valid_seen` 140 局（各类型 13–35 局），`train` 池 3,553 局 | 验证集从 `valid_seen` 分层抽 39 局可行 |


