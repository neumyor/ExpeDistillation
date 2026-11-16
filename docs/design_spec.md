# ExpeDistillation

强模型轨迹蒸馏成 Skill 库、教弱模型的实验仓库（开发名 SkillPool）。
不训练模型权重，只维护自然语言文本：技能库、弱模型画像和全局手册（playbook）。
技能和 playbook 进入弱模型的提示词，这是它们影响弱模型的唯一通道。

本文件是系统设计的唯一正本：改设计先改这里，再改代码。项目介绍与使用教程见
[README.md](../README.md)；目录结构、开发规范、测试要求见 [AGENTS.md](../AGENTS.md)。
设计取舍的讨论过程在 `docs/decisions/`，本文对应的方案是
`docs/decisions/method_simplification_20261112.md` 与
`docs/decisions/evidence_driven_evolution_20261113.md`。代码和测试注释里的"README 第 X 节"
指本文件的对应章节。

---

## 一、系统是什么

### 1.1 闭环与三种相位

整个系统是一个闭环：**轨迹 → 技能库 → 弱模型带着技能库和 playbook 在训练题（或验证题）上练习，
得到新的失败 → 根据失败更新画像、playbook 和技能库 → ……**（"轨迹"指一次完整执行留下的逐步记录。）

闭环由两类阶段组成，阶段在配置和磁盘上的正式名字是"相位"：

- **执行相位**：技能库 + playbook + 任务 + 模型 → 运行记录 + 答卷；
- **学习相位**：上一个摸底或练习相位的答卷 + 运行记录 + 旧技能库 → 新技能库 + 新画像 + 新 playbook。

一种方法就是这两类相位排成的一个序列。相位有四种角色：

| 角色 | 类别 | 题 | 注入的技能 | 谁做、做几次 |
|---|---|---|---|---|
| `baseline` 摸底 | 执行 | 每个训练任务 | 不注入，也不显示 playbook；只能是序列第一项 | 强、弱模型各做 `n_attempts_strong` / `n_attempts_weak` 次 |
| `practice` 练习 | 执行 | 每个训练任务或每个验证任务（字段 `tasks: train \| val`） | 技能文件夹注入全部在用的卡，并显示演化的 playbook | 弱模型做 `attempts` 次 |
| `learn` 学习 | 学习 | — | — | 读紧邻的上一个 `baseline` 或 `practice` 相位的答卷 |
| `test` 终测 | 执行 | 每个测试任务 | 在用的卡（整库注入或技能文件夹） | 弱模型做 n 次，与对照比较 |

参考方法的序列是：

```
baseline → learn → practice → learn → practice → learn → test
```

三个学习相位用同一个定义。练习就是终测那条执行路径用在训练题或验证题上；在验证题上练习时，
学习读的失败来自写卡材料之外的题。

### 1.2 模块

```
执行相位：技能库 + playbook → 交付 → 执行 → 验收 → 答卷
学习相位：失败答卷 → 诊断 → 定位到子任务类型 → 分配证据 → 演化画像与 playbook → 写卡 / 改写 / 删除 → 新技能库
```

| 模块 | 职责 | 输入 → 输出 |
|---|---|---|
| 交付 | 决定每道题注入哪些技能，附上 playbook | 技能库 + 本相位的题 → 执行单元清单（每行 = 一道题 + 注入的技能，技能可以为空） |
| 执行 | 适配各 benchmark，把运行结果统一成同一种输出格式 | 执行单元清单 → 运行记录（每次运行一条） |
| 验收 | 对每次运行给出最终判定 | 运行记录 → trial 级 AnswerSheet（execution + coverage + grading） |
| 诊断 | 对弱模型的每张失败答卷说明原因，指出决定性出错的那一步 | 失败答卷 → 诊断记录（`Finding`） |
| 定位 | 切段失败轨迹，把出错那一步所在的段归入子任务类型 | 失败轨迹 + 出错步 + 类型表 → 这条失败的子任务类型 |
| 分配证据 | 按程序事实把每条失败分给缺口、触发、正文或无卡证据 | 定位结果 + 读取记录 + 技能库 → 证据记录 |
| 演化 | 用诊断里的笔记更新画像和 playbook | 上一版文本 + 笔记 + 事实 → 新版文本（`EvolvingText`） |
| 演化技能库 | 缺口写新卡，触发证据只改触发条件，正文证据只改正文，正文证据连续两轮即删除 | 证据 + 材料 + 画像 + playbook → 新技能库 |

执行单元 = 一道题 + 给它注入的技能。单元每被执行一次就留下一条运行记录。
相位之间只传四样东西：**技能库、运行记录、答卷、演化文本**；执行单元清单、诊断记录、
证据记录这些中间产物只在相位内部流转。

验收的落盘输出是 **trial 级 AnswerSheet**：execution（轨迹/答卷/外部
reward + 原样的 correctness）、coverage（这张 trial 该不该有、有没有、为什么
没有）、grading（success / failure / invalid + 依据 + 规则名）。
`UnitSummary`（有效次数、成功次数、status、cause）由 `composites/unit_summary.py`
从 AnswerSheet + UnitSpec 现算，学习相位和终测调用同一组纯函数。

### 1.3 三个演化对象与一份固定协议

| 对象 | 内容 | 谁读它 | 由什么证据驱动 |
|---|---|---|---|
| 技能 Skill | 一个子任务类型一张卡。触发条件：`summary` + `when_to_use`（写这个子任务的起点状态）；正文：`steps` + `not_applicable_when` | 执行者（目录里只看到触发条件，打开后看到全部） | 按子任务类型分配的缺口、触发、正文证据 |
| 画像 Profile | 弱模型会犯什么错、需要怎样的教法 | 写作者（写卡、改卡、写 playbook 时都读） | 全部失败的 `model_note`；"读了卡仍失败"最直接 |
| 手册 Playbook | 每个任务都适用的做法 | 执行者（相位配置 `playbook: evolved` 时每一步都能看到） | 全部失败的 `playbook_note`、摸底事实、触发证据计数 |
| 协议 Protocol（不演化） | 环境事实：执行者每一步能看到什么、可用哪些动作、技能怎样打开 | 写作者 | 由 benchmark 提示词包固定 |

各部分的边界：画像只描述弱模型；只对某个子任务成立的做法写进技能；对所有任务都成立、
又不属于环境事实的做法写进 playbook；环境事实写在协议里。写卡时写作者看到当前 playbook，
卡只写 playbook 没覆盖的部分。执行者看到的环境规则写在 benchmark 自己的执行提示词里
（见 3.7）。ALFWorld 的 playbook 替换提示词按设计要求写入按任务类型的搜索顺序等内容
（为 ALFWorld 的分数），与技能有重合，这是有意的取舍。

### 1.4 关键分工

1. **执行不判定**。执行只把 benchmark 自带判分器的结果原样传下去，没拿到
   判分结果就留空（`correctness = null`），并附上重试次数、报错原文等运行
   指标。运行算不算正常完成，由验收判定。
2. **重试分两层**。执行只在没拿到结果时机械重试（runner 抛出异常、进程
   崩溃、连接失败时重跑）。摸底与练习相位里派发了却什么都没存下的采样（执行的重试耗尽），
   由相位调度重新派给执行，次数上限是配置里的 `invalid_redispatch_limit`。已经落盘、
   但没拿到判分结果（`correctness = null`）的运行由验收判为无效，不重跑（每次采样的轨迹
   编号是确定的，已存即已完成）。无效运行既不算成功也不算失败。
3. **学习中不设统计关卡**。学习相位不判断一张卡"有没有用"：训练集上的失败太少，逐卡或
   整库的对照都没有足够的功效（`docs/decisions/evidence_driven_evolution_20261113.md` 1.6）。
   学习只读失败证据并据此修改；技能是否有用由终测衡量，技能库在 playbook 之上的贡献由之后的消融臂 `no_skills` 按题配对测量（2.5）。
4. **证据类别由程序定**。诊断模型只给失败原因、出错那一步和范围（子任务 / 通用）；
   出错步落在哪个子任务类型、出错前有没有打开过该类型的卡，都是程序事实
   （打开记录从轨迹里的 `cat` 或 `ReadSkill[...]` 动作解析，`composites/skill_use.py`，
   不取模型自述）。
5. **一次改写只改一侧**：触发证据只改触发条件，正文证据只改正文，另一侧逐字节不变。
6. **测试结果不流进学习**。test 相位的答卷只进入最终成绩；学习相位遇到紧邻的执行相位是
   test 时直接报错。

### 1.5 证据分配

学习相位读紧邻的上一个摸底或练习相位里弱模型判为失败的答卷。每条诊断成功、定位成功的失败
按下表分配（`composites/evidence.py`，纯函数）：

| 程序事实 | 证据类别 | 动作 |
|---|---|---|
| 诊断给出 `scope = general` | 无卡证据 | 只有笔记进画像与 playbook |
| 出错段的类型没有在用的卡 | 缺口 `gap` | 写一张新卡（没有同类型强模型成功段时不写，回执 `skipped_no_material`） |
| 有卡，出错那一步之前没有打开过 | 触发证据 `trigger` | 只改触发条件 |
| 有卡，出错那一步之前打开过 | 正文证据 `content` | 只改正文；上一个学习相位也给这张卡正文证据时改为删除（`content_failed_twice`） |
| 该类型这一轮没有失败 | 无证据 | 不动 |

规则说明：

- "之前"是严格的：第一次打开恰好在出错那一步或更晚，算没有打开过（触发证据）；
- "有没有在用的卡"读上一个学习相位发布的库工件（第一个学习相位读 run 根技能库），不读对象存储；
- 一个类型最多一张卡（`skill_id = <type_id>/skill`）。卡已被删除的类型再拿到缺口证据时不写新卡，
  回执 `skipped_discarded`；
- 同一张卡在一个相位里两侧都有证据时，先改正文、再改触发条件，各自一个版本；
- 诊断失败的答卷记进 `failed_units.jsonl`，既没有笔记也没有证据；一步都没有的失败答卷没有可指认的
  出错步，直接记为诊断失败（不调模型）。诊断成功但定位失败（出错步不在任何已归类的段里）的答卷
  记一行错误类别为 `Unlocated` 的 `failed_units.jsonl`，它的笔记照常进画像与 playbook，但不产生卡证据；
- 卡只有两种状态：`active`（交付使用）与 `discarded`。没有待检验、已接受之分；
- 每个学习相位都把本相位全部证据写进 `evidence.jsonl`，触发证据的计数另交给 playbook 写作者。

---

## 二、方法 = 相位序列（配置 schema 9）

一种方法写成配置里的两块内容：`sequence`（相位名单，可重复）和
`phases`（每个名字一份相位定义）。

### 2.1 配置字段

一份实验配置（`configs/experiments/<实验名>.yaml`）的顶层字段：

| 字段 | 说明 |
|---|---|
| `benchmark` | benchmark 名字（`tb` / `searchqa` / `alfworld`） |
| `models` | 模型角色表：角色 → 模型名。必须给出 `executor_strong` 与 `executor_weak` 两个锚点，其余角色按 `ROLE_DEFAULTS` 补齐（全部用强模型）。角色：`executor_strong`、`executor_weak`、`segmenter`、`author`、`rewriter`、`judge`（`judge` 只供 `judge-skills` 分析工具） |
| `n_attempts_strong` / `n_attempts_weak` | 摸底两臂的采样次数；终测按执行者取其中之一；练习缺省取 `n_attempts_weak`，可用执行参数 `attempts` 覆盖 |
| `split` | 显式 train / val / test / reserved 列表（`val` 可缺省为空，四个列表两两不相交），或 `{ratio, seed}` |
| `tasks` | 任务集：不写（插件全集）、任务 ID 列表、或任务文件路径 |
| `sequence` | 相位名单（局部名的列表，可重复） |
| `phases` | 局部名 → 相位定义 |
| `invalid_redispatch_limit` | 摸底与练习相位里，一个单元中派发了却什么都没存下的采样最多再派发几轮（缺省 1）；已存的无效运行不重派 |
| `rules` | 具名规则的选择（`segment_select` / `trajectory_select`），缺项由 `DEFAULT_RULES` 补齐（`strong_success_weak_failed` / `all`） |
| `model_options` | 按模型名给出的额外调用参数（如 `reasoning_effort`）；不得携带 `max_tokens`、`temperature`、`model`、`messages`、`stream` |
| `token_budgets` | 每个角色的 token 上限（`default_max_output_tokens` 必填，可选全局 `max_input_tokens`，`roles.<role>` 逐角色覆盖） |
| `prompts_digests` | prompt 名 → digest；源配置里不写，冻结步填入后随完整配置进 manifest |
| `schema_version` | 9 |

**执行相位的字段**（`kind: execute`）：

| 字段 | 说明 |
|---|---|
| `role` | `baseline` / `practice` / `test`，显式声明，加载期核对与字段一致 |
| `tasks` | 跑冻结的哪个列表：`baseline` 只能是 `train`；`practice` 是 `train` 或 `val`；`test` 角色与 `test` 列表是同一件事，两者必须同时成立 |
| `delivery` | 交付方法（带 `name` 的映射）：`none` / `whole_bank`（可选 `verbatim: true`）/ `folder`（可选 `description_mode`）；说明见 `src/expedistill/methods/delivery/README.md` |
| `execution` | 执行方法：`real`（真实 benchmark 执行；摸底写 `arms: [executor_strong, executor_weak]`）/ `real_folder`（真实执行，技能以文件夹送进执行环境，参数 `executor`（缺省 `executor_weak`）与 `attempts`（缺省取该执行者的全局次数））；说明见 `src/expedistill/methods/execution/README.md` |
| `compare` | 对照来源，只有 test 角色可写：`against: fresh_bare`（相位内现场跑一条裸臂）或 `against: external_campaign`（对照读外部冻结成绩文件，零执行，必须带 `external` 块：`path`（相对路径相对配置文件所在目录解析，冻结为绝对路径）+ `sha256`（成绩文件字节的校验和）+ `model_key`（成绩文件里对应本 run 执行者的模型键）） |
| `acceptance` | 验收判分规则名（可选；缺省用 benchmark 的默认规则，TB 是 `tb_scorer`，其余是 `scorer`） |
| `playbook` | `off`（缺省）或 `evolved`：执行者是否看到紧邻的上一个学习相位演化出的 playbook。写了 `evolved` 的执行相位前面必须有学习相位。YAML 里 `off` 要加引号写成 `"off"`，裸写会被读成布尔值而报错 |

**学习相位的字段**（`kind: learn`）：

| 字段 | 说明 |
|---|---|
| `role` | 恒为 `learn` |
| `review` | 学习参数：`K`（每条轨迹最多切几段，缺省 5）、`retry_limit`（切段校验不过时的重切次数，缺省 2）、`write_lessons`（弱模型失败段是否进写卡材料，缺省 `true`） |
| `author` | 写卡方法：`subtask_cards`，参数 `typing_chunk_size`（一次归类调用最多带几个段，100）/ `max_segments_per_card`（一张卡的写作材料最多几个段，8）/ `max_chars_per_segment`（每段渲染步骤的字符上限，1200）/ `write_retry_limit`（写卡输出不合格时的重写次数，2） |

学习流程固定为五步（5.4），每个学习相位都执行同样的五步，配置只决定参数。

### 2.2 相位标签与相位目录

每个相位有一个标签 `pNN:<局部名>`：冻结步恒为 `p00:freeze`；序列第 i 项
（i 从 0 数）的标签是 `p{i+1:02d}:<局部名>`，例如第一个序列相位是
`p01:baseline`。标签出现在进度账本（`pipeline_log.jsonl` 的 `stage` 值）、
`failed_units.jsonl`、AnswerSheet 的 `phase_label` 和摸底、练习的轨迹编号里；局部名只能用
字母、数字、`.`、`_`、`-`（标签按冒号解析）。

每个相位在 run 目录里有自己的目录 `phases/pNN_<局部名>/`（标签里的冒号
换成下划线），存放该相位的账本与产物（完整布局见 4.3）。

### 2.3 加载期检查清单

配置加载时（`core/config.py` 的 `parse_phases`）逐条核对，任何一条不过都
报配置错误，跑都不会开始：

1. `sequence` 是非空的名字列表，每个名字在 `phases` 里有定义；`phases` 里没有被
   序列用到的定义也拒绝（它会冻结没有任何相位使用的方法身份）；
2. 每个定义的字段集合与它的 `kind` 匹配（多出的键按拼错处理，拒绝）；
3. `role` 与字段一致：`test` 角色必须跑 `test` 列表且只有它跑；`baseline` 必须跑 `train` 列表，
   恰好写 `arms: [executor_strong, executor_weak]` 两臂、`none` 交付与 `real` 执行、`playbook: "off"`；
   `practice` 跑 `train` 或 `val` 列表（选 `val` 时该列表必须非空），用 `folder` 交付与
   `real_folder` 执行；其他相位不得写 `arms`；
4. `baseline` 只能是序列的第一项；序列里有 `learn` 或 `practice` 相位时，第一项必须是
   `baseline`（强模型轨迹只来自这里）。只有 `test` 的序列（如 `[test]`，见 2.7）没有摸底，合法；
5. 只有 test 相位带 `compare`，`against` 只能是 `fresh_bare` 或
   `external_campaign`，后者必须带完整的 `external` 块；
6. 每个学习相位紧邻的上一个执行相位必须是 `baseline` 或 `practice`；
7. `folder` 交付与 `real_folder` 执行一对一配对；
8. `playbook` 取 `off` 或 `evolved`；写了 `evolved` 的执行相位前面必须有学习相位；
9. `schema_version` 必须是 9，未知顶层键拒绝。

另外，冻结步在写任何文件前做三项检查：把每个相位的每个方法族
（delivery / execution / review / author）都解析一遍并校验
参数（`methods/registry.py` 的 `resolve_params`）；核对具名规则名已登记；
核对 benchmark 能否在该交付方式下显示 playbook
（`composites/evolving_text.check_playbook_capability`：TB 的技能块不是普通提示词文本，
只能经技能文件夹的 skill-read 后缀显示 playbook，所以 TB 的 `none` / `whole_bank`
相位写 `playbook: evolved` 会被拒绝）。

### 2.4 例子

参考方法（`configs/experiments/alfworld_paper_e1_ours.yaml`，省略模型表、预算与任务列表）：

```yaml
sequence: [baseline, learn, practice, learn, practice, learn, test]
phases:
  baseline:
    kind: execute
    role: baseline
    tasks: train
    delivery:  {name: none}
    execution: {name: real, arms: [executor_strong, executor_weak]}
  learn:
    kind: learn
    review:  {name: review, K: 5, retry_limit: 2, write_lessons: true}
    author:  {name: subtask_cards}
  practice:
    kind: execute
    role: practice
    tasks: train                 # 或 val：在验证集上练习
    delivery:  {name: folder, description_mode: summary_then_when_to_use}
    execution: {name: real_folder, attempts: 3}
    playbook:  evolved
  test:
    kind: execute
    role: test
    tasks: test
    delivery:  {name: folder, description_mode: summary_then_when_to_use}
    execution: {name: real_folder}
    playbook:  evolved
    compare:
      against: external_campaign
      external: {path: ../seed/<冻结的裸跑成绩文件>.json, sha256: <校验和>, model_key: <弱模型名>}
```

三个学习相位共用定义 `learn`。第一个学习相位读裸跑的失败，库里还没有卡，所以所有定位到
子任务的失败都是缺口；之后的学习相位读 `practice` 的失败。最后一个学习相位的改写不再执行，
直接带进终测。

### 2.5 学习参数与消融开关

| 开关 | 取值 | 作用 |
|---|---|---|
| `review.K` | 正整数 | 切段预算。`K: 1` 让每条轨迹只出一段，整条轨迹成为学习单元 |
| `review.write_lessons` | `true` / `false` | 弱模型失败段是否进入写卡材料；关闭时材料只有强模型段 |
| `rules.trajectory_select` | `all` / `strong1_weak1` | 摸底里每个任务挑哪些强模型轨迹切段作写卡材料 |
| `rules.segment_select` | `strong_success_weak_failed` / `strong_all_weak_failed` / `all` | 哪些段进入写卡材料；`all` 与 `trajectory_select: all` 一起构成覆盖消融 |
| 练习相位 `tasks` | `train` / `val` | 在训练集还是验证集上练习 |
| 执行相位 `playbook` | `off` / `evolved` | 练习与终测分别配置；学习相位总是演化 playbook |

设计已定、做消融时再实现的两个开关（`docs/decisions/evidence_driven_evolution_20261113.md` 3.5）：
`review.revise_scope`（`auto` / `trigger_only` / `content_only`：范围之外的证据照常记录，但不改写、
不计入删除）与 `review.cards`（`false` 时不写卡、不改卡，只演化画像与 playbook，即 `no_skills` 臂）。

### 2.6 schema 版本

`CONFIG_SCHEMA_VERSION = 9`。加载器只接受这一个版本，其余版本直接报配置错误并提示用
`scripts/run/build_paper_configs.py` 重新生成配置；没有升级函数。对象记录同样只读
当前版本：`Trajectory`（schema 3：编号带相位标签）、`Subtask`（schema 2：增加 `phase_label`，
`fork-run` 按它过滤）、`SubtaskType`（schema 1）、`Skill`（schema 5：一个子任务类型一张卡，
没有 `subtask_id` 字段）遇到其他 schema 版本直接报错。

### 2.7 论文实验矩阵

论文配置全部由 `scripts/run/build_paper_configs.py` 生成，划分、TB 任务表和外部对照
文件信息在 `scripts/run/paper_splits.py`。文件名是 `<bench>_paper_<exp>_<arm>.yaml`，
`bench` ∈ {`tb21`, `alfworld`, `searchqa`}。`--check` 校验磁盘上的配置与生成结果一致，
`--print-index` 打印每个臂与参照臂相差的键。

| 实验 | 臂 | 与参照的差别 |
|---|---|---|
| e1 | `ours` | 参照方法（2.4），练习在训练集上；`practice` 的 `attempts`：ALFWorld 3、TB 2、SearchQA 1 |
| e1 | `ours_val`（ALFWorld） | 练习在验证集上（`valid_seen` 分层抽取的 39 局） |
| e1 | `<方法>_folder` / `<方法>_whole` | 六个外部方法（skillopt、evoskill、trace2skill、gepa、expel、agentbrew），序列只有 `test`，在 `import-bank` 准备的 run 目录上跑；`_whole` 用 `whole_bank`（`verbatim: true`）+ `real` |
| e1 | `bare`（SearchQA）、`bare_q` / `bare_d` / `bare_x`（ALFWorld） | 空技能库的裸跑；ALFWorld 的裸跑结果由 `scripts/run/build_bare_control.py` 写成冻结成绩文件，其余 ALFWorld 臂用 `external_campaign` 读它 |

消融（e2 教师 → 学生、e3 切段预算、e4 改写范围与 `no_skills`、e5 覆盖）的臂定义见决定文档第六节，
做消融时再加回生成脚本。

凡是带学习相位的臂，`practice` 与终测都是 `playbook: evolved`；只有 `test` 一个相位的臂
（外部方法与裸跑）没有学习相位，`playbook` 用缺省的 `off`。终测对照臂始终不带 playbook，
所以带库臂与对照的差值同时包含技能库和 playbook 的作用。

TB 的终测对照是外部 campaign 成绩文件（`model_key` 跟随学生模型）；ALFWorld 用各学生模型
冻结的裸跑文件；SearchQA 用 `fresh_bare`。已经启动过正式 run 的配置保持原样，
参数要变就给新臂起新名字。

---

## 三、积木清单

整个系统由一组积木拼成。积木分三层：**基础件**（所有积木共用的底座）、
**功能积木**（做一件具体的事）、**组合积木**（把几块功能积木串成一个常用
动作，负责落盘和续跑）。方法驱动和相位流程把积木按顺序接起来。

### 3.1 积木规则

1. **积木只认对象，不认相位**：功能积木的输入输出都是数据模型里的对象
   （或对象列表、或纯文本），它不知道自己在第几相位，也不管文件存在哪里。
   落盘和断点续跑由组合积木和相位流程负责。
2. **模型是参数**：凡是调用模型的积木，用哪个模型由调用方传入（从配置的
   角色表里取），积木内部不写死"强模型"或"弱模型"。
3. **跟 benchmark 有关的东西打包成一个 benchmark 插件**：执行 runner、
   任务加载、轨迹视图的截断规则、执行提示词，以及一个提示词包（3.7），放在同一个
   插件目录里，通过插件注册表按名字取用。其余积木只处理统一格式的对象，对
   benchmark 无感知。加一个 benchmark = 加一个插件目录。
4. **所有模型调用走同一个基础件**（B1 调用模型），统一处理输出格式错误的
   重试、用量记账和请求原文落盘。唯一例外是 E1 里执行 agent 的模型调用：
   本地 runner 经 B1 的客户端逐步调用；TB runner 的模型调用发生在远程
   Harbor 里，用量从 Harbor 结果中读回再记账。
5. **每块积木可以单独测试**：模型可以换成返回固定内容的假模型，程序类积木
   直接用构造好的对象测。
6. **ID 由程序按确定规则生成**：子对象 ID = 父对象 ID + 序号（如
   `subtask_id = <trajectory_id>/seg<i>`），同样的输入重跑得到同样的 ID，
   断点续跑时据此判断"这个单元做过没有"。模型输出里的任何编号都不能当 ID 用。
7. **可替换的规则做成具名规则**：段筛选、轨迹挑选、判分都在注册表里按名字
   登记，配置里写名字，名字进 manifest 冻结。改规则 = 新增一条具名规则，不改已有
   规则的行为。

### 3.2 基础件

| 编号 | 名称 | 做什么 | 文件 |
|---|---|---|---|
| B1 | 调用模型 | 输入（模型句柄, prompt 名, 变量, 输出格式）→ 解析好的结果。格式不对时按规则重问；基础设施异常重发同一请求；每次请求和用量落盘；按句柄的 `prompt_aliases` 把方法提示词换成 benchmark 的替换提示词，按 `prompt_defaults` 填词汇槽 | `llm/call.py`、`llm/client.py`、`llm/providers.py` |
| B2 | Prompt 注册表 | 所有 prompt 的具名常量；计算 digest 供 manifest 冻结 | `prompts/` |
| B3 | 对象存储 | 对象的保存 / 读取 / 列举；判断某个单元是否已完成（断点续跑用）；JSONL 账本的加锁追加与断尾修复 | `infra/store.py` |
| B4 | Manifest 冻结 | 写一次、之后逐字段比对，不一致即拒绝继续 | `infra/manifest.py` |
| B5 | 并行池 | 与业务无关的线程池 | `infra/parallel.py` |
| B6 | 可靠性 | 异常分类（基础设施 / 模型输出 / 服务方拒绝 / 配置 / 代码 bug）、重试策略、单元失败记录 | `infra/reliability.py` |
| B7 | 运行锁与用量累计 | run 目录排他锁；用量跨进程累加落盘，续跑后不清零 | `infra/runlock.py`、`infra/usage.py` |
| — | 接口形状 | 交付与决策的交接形状（执行单元行、任务清单行）；执行→验收的 trial 级记录是 `core/answer_sheet.py` 的 `ExecutionRecord` | `core/contracts.py`、`core/answer_sheet.py` |

**B1 的两种重试**：

- 输出格式不对（解析不出要求的 JSON）→ 带修复说明重问，次数
  `max_format_retries`；
- 基础设施异常（连接错误、超时、HTTP 408 / 409 / 429、5xx）→ 原样重发同
  一个请求，次数由运行设置 `--llm-infra-retries` 给出（默认 3），第 n 次
  重发前等待 `--llm-infra-backoff`（默认 5 秒）× 2ⁿ，单次等待最多 60 秒；
- 两种预算互相独立：一次调用最多发
  `(1 + 格式重试次数) × (1 + 基础设施重试次数)` 个请求；
- 其余 4xx（key 错、模型名不存在、上下文超长等）是**服务方拒绝**：重发没有
  用，不重试，流程停下等人处理（退出码 1）；
- 每一次尝试（包括失败的）都记一行用量、落一份请求原文到
  `<run>/llm_requests/NNNN_<prompt>.json`；API key 永不落盘。

**B1 的输出上限与空回复**：

- 每次模型调用的输出上限来自配置的 `token_budgets`（按角色；缺省取
  `default_max_output_tokens`），没有配置 `token_budgets` 是硬配置错误。
  按模型覆盖的采样参数走 `model_options`；
- 模型返回空内容或全空白回复是 **ModelOutputError**（消息里带
  `finish_reason`，分得清"预算烧在推理上"（length）和"模型选择沉默"
  （stop）），不重发。本地 ReAct 一局里发生时，这一局以
  `correctness = null`、`meta.termination = "model_output_error"` 收尾
  （已走步骤保留），不重试，判定交验收；
- 请求原文落盘记 `finish_reason`，用量账本每行记 `finish_reason` 与
  `cached_tokens`。

**B1 的采样温度**：所有方法侧模型调用固定使用 `temperature = 1.0`，配置文件不能覆盖。
思考型模型在 `temperature = 0` 下容易反复输出同一段推理、把输出预算耗尽后返回空内容；
固定 `1.0` 避开这一退化。温度是解码设置，不属于实验身份，不进入配置与 manifest。
ALFWorld 的执行协议用 `temperature 0.95`（外层统一 runner 用 `temperature 0`）：自建 vLLM 上的学生在贪心解码下，20 条复放的摸底提示词里有 2 条思考陷入重复、写到 65536 上限，采样解码避开了这一退化（见 3.3 的 runner 表）。

### 3.3 功能积木

**执行类**

| 编号 | 名称 | 输入 → 输出 | 文件 |
|---|---|---|---|
| E1 | 执行 | （模型, 一道任务, 技能块（可为空））→ Trajectory（含判分器结果 `correctness`） | `blocks/execute.py` + benchmark 插件 |

E1 把 benchmark 自带判分器的结果原样写进 `correctness`（`true` = 对、`false` = 错、
`null` = 没拿到判分结果），附上运行指标（重试次数、报错原文、耗时），不做任何判定。
基础设施失败（沙箱超时、断网、进程死亡）由插件抛出 `InfrastructureError`，K1 按 B6
的策略重试；重试耗尽的单元不落盘（"已存 = 已完成"）。插件的统一结果对象 `RunOutcome`
三字段：`steps`（逐步原文）、`correctness`、`meta`（插件自由数据）。

| Runner | Benchmark | 执行方式 |
|---|---|---|
| TB runner | TerminalBench | 委托 Harbor + 沙箱执行一次 rollout，结束后从 Harbor trial 目录还原逐步轨迹（`benchmarks/tb/trajectory.py`）。driver 与 Harbor 同机（`EXPEDISTILL_TB_HOST=local`）时命令走本机 `bash`、文件走本机复制；driver 在别的机器时经 ssh/scp |
| SearchQA runner | SearchQA | 本地共用 ReAct 循环（`benchmarks/react_loop.py`）一步一步执行到结束，环境与判分来自外层 `benchmark/searchqa-eval/` |
| ALFWorld runner | ALFWorld | 本地执行，协议与外层 `benchmark/alfworld-eval` 的统一 runner 一致（`benchmarks/alfworld/protocol.py`：相同的 system 消息与 user 模板、最近 2 步历史、环境给出的可行动作列表、`<think>…</think><action>…</action>` 严格解析、一次纠错、temperature 0.95、固定的输出上限 65536、50 步预算）；环境在外层 venv 的子进程里运行，需要 `ALFWORLD_DATA` / `ALFWORLD_CONFIG` / `ALFWORLD_BENCH_SRC` |

两套本地 runner 的每局步数上限用环境变量配：SearchQA 是
`EXPEDISTILL_SEARCHQA_MAX_STEPS`（默认 6），ALFWorld 是
`EXPEDISTILL_ALFWORLD_MAX_STEPS`（默认 50）；设了但不是 ≥1 的整数即配置错误。

**TB runner 的远程 run id 与续跑**（`docs/decisions/tb_run_namespace_20261011.md`）：

- 远程 run id = `ed_<task>_<命名空间>_<attempt token>`。命名空间由 K1 计算：
  run 目录的真实路径、相位标签、角色、模型名、注入的技能引用、渲染后技能块的
  摘要，合起来取 sha256 前 10 位。不同臂、不同相位的执行因此不会撞 id；相位标签必须在内：
  两个 `practice` 共用 run 级轨迹存储，交付的库也可能相同，没有它就会复用对方的 Harbor 结果。
  run 目录的真实路径是命名空间的一部分，把 run 搬到另一台机器（路径变了）
  不能续跑，要在新位置新建 run；
- **同一个 run id 只提交一次**。提交时记两份启动条件：本地
  `<单元工作目录>/tb_remote/<run_id>.json`（另记 runner pid）和远程
  `jobs/<run_id>.ed_meta.json`。续跑时只有两份记录都在、且都与本次请求一致
  才复用：有 `result.json` 就直接取结果，没有结果但 runner 还活着就接着等；
  其余情况抛 `InfrastructureError`，换下一个 token 重跑；
- **核对进程身份**：判断 runner 还活着、判定它已退出、超时终止它，都先核对
  `/proc/<pid>/environ` 里有 `RUN_ID=<run_id>`。核对不了就当作"不是我们
  的"，换 token 重跑。超时终止只杀记录下的那个进程组，从不用
  `pkill` / `pgrep -f`。

**数据整理类**（纯程序，不调模型）

| 编号 | 名称 | 输入 → 输出 | 文件 |
|---|---|---|---|
| D1 | 任务加载 | （benchmark, 任务表）→ 任务列表 | `blocks/tasks.py` |
| D2 | 划分 | （任务列表, 冻结的划分）→ train / val / test / reserved | `blocks/tasks.py` |
| D3 | 成绩统计 | （Trajectory 列表, 应有单元集合, 辅助打分器）→ 每题有效次数 / 成功次数，两种口径的 avg@N、pass@N（声明辅助指标时另有同分母的辅助 avg@N） | `blocks/metrics.py` |
| D5 | 轨迹视图 | （Trajectory 或段内步骤, 视图配置）→ 给模型看的文本；超长观察按规则截断，截断处明确标注 | `blocks/view.py` |
| D6 | 段筛选 | （Subtask 列表, 段筛选规则）→ 进入写卡材料的 Subtask；规则名在 `blocks/rules.py` 的注册表里取用，写卡材料（`methods/cards/material.py`）调用 | `blocks/rules.py` |
| D7 | 切段校验 | （Trajectory, Subtask 列表, K）→ 通过 / 错误清单 | `blocks/segment.py` |
| D9 | 技能注入 | （技能列表）→ 插入 prompt 的技能块；每条技能的文本由 `render_card` 渲染 | `inject/skills.py`、`inject/skill_text.py` |

`inject/skill_text.py` 是技能文本的唯一渲染处：`render_card(skill)` 输出执行者打开技能时
看到的全文（导入技能输出 `verbatim_text`，其余输出
`[When to use] …` / `[Steps]` 编号列表 / `[Do not use when] …`）；
`description_text(skill, mode)` 输出目录行与 `SKILL.md` 的 `description`。执行、诊断、
改写和 `SKILL.md` 物化都调用这两个函数。

**模型类**（每块对应注册表里的 prompt，模型由调用方传入）

| 编号 | 名称 | 输入 → 输出 | prompt | 文件 |
|---|---|---|---|---|
| G1 | 切段 | （轨迹视图, K）→ 段列表（未校验）。模型只给每段的起止步号和 label / description / success | `SEGMENT` | `blocks/segment.py` |
| G2 | 归类 | （现有类型表, 一批段的卡片：label / description / 截断的步骤）→ 每段归入的类型（现有或新增：名字 + 起点状态） | `SUBTASK_TYPING` | `blocks/subtask_types.py` |
| G3 | 写卡 | 一个子任务类型 + 同类型强模型成功段 + 弱模型失败段 + 画像 + playbook → 一张卡的四个字段 | `CARD_WRITE` | `methods/cards/writer.py`（材料在 `methods/cards/material.py`，字段校验在 `blocks/author.py`） |
| G4 | 改写 | 卡 + 证据的真实步骤 + 诊断 + 画像 + playbook → 触发条件两个字段，或正文两个字段 | `REVISE_TRIGGER`、`REVISE_CONTENT` | `methods/revise/` |
| — | 诊断 | 一张失败的摸底或练习答卷（含目录、playbook、打开过的卡）→ 失败原因、出错步、范围、画像笔记、playbook 笔记 | `TRIAL_FAILURE_ANALYSIS` | `methods/diagnose/` |
| — | 演化文本 | 上一版文本 + 笔记 + 事实 + 画像 → 新版画像或 playbook | `PROFILE_UPDATE`、`PLAYBOOK_UPDATE` | `methods/evolve_text/` |
| — | 技能使用判定 | 一张终测答卷 + 展示过的技能目录 → 每张卡是否适用；被打开的卡的每一步是否被执行 | `SKILL_APPLICABILITY_JUDGE`、`SKILL_ADHERENCE_JUDGE` | `blocks/adherence.py` |

写卡、改写、写 playbook 的提示词都带一段画像（`prompts/profile.py` 的
`profile_section`）：画像为空时给出中性段落（"没有画像，按一般能力的该类 agent 写"），
画像非空时在正文后追加一条绑定行：画像里陈述的一切都是对所写内容的约束。各提示词的
读者标题行按角色名取（`skill_reader` / `playbook_reader`）。写卡与两种改写还带当前 playbook，
卡只写 playbook 没覆盖的部分。

### 3.4 组合积木

| 编号 | 名称 | 做什么 | 文件 |
|---|---|---|---|
| K1 | 批量执行 | E1 × 题目 × 采样次数，经 B5 并行；每完成一次执行就落盘一条 Trajectory，可续跑；`InfrastructureError` 按 B6 重试，耗尽的单元记失败账本 | `composites/batch_execute.py` |
| K2 | 切段（带校验） | D5 → G1 → D7；不通过就把错误清单交给 G1 重切，次数上限 `retry_limit`；耗尽则记审计日志、本 run 内终态 | `composites/segment_checked.py` |
| K3 | 定位 | 失败轨迹与摸底强模型轨迹的 K2 切段（0 步轨迹不切，记一条 "empty trajectory" 审计）→ G2 归类（按 `typing_chunk_size` 分块）；类型表与段归属只追加，续跑只归类还没有归属的段 | `composites/locate.py` |

其余组合积木：

| 名称 | 做什么 | 文件 |
|---|---|---|
| trial 级答卷 | `ExecutionRecord` / `CoverageRecord` / `GradingRecord` 组成的 AnswerSheet；JSON 是源，Markdown 是派生视图 | `composites/answer_sheets.py`、`blocks/view.py` |
| 判定核 | 对一张 AnswerSheet 判 success / failure / invalid；具名判分规则（`scorer`、`tb_scorer`、`external_reward`） | `composites/verdicts.py` |
| coverage | 应有 trial 与已存 trial 的对照：present / missing / terminal + cause | `composites/coverage.py` |
| 单元汇总 | `summarize_unit` / `summarize_phase` / `task_metrics` | `composites/unit_summary.py` |
| 相位报告 | 读 `unit_specs.jsonl` + `answer_sheets/` + `coverage.jsonl`，现算 UnitSummary | `composites/phase_report.py` |
| 单元身份 | `make_unit_id` / 注入技能的规范形 / 空洞答卷 id | `composites/unit_ids.py` |
| 证据分配 | 按 1.5 的表把每条定位成功的失败分给缺口 / 触发 / 正文 / 无卡证据（`route`）；每个类型或每张卡一个单元（`plan_cards`）；删除规则（`content_failed_twice`）；触发证据计数（`trigger_facts`） | `composites/evidence.py` |
| 终测差值 | `final_test_delta`、`control_arm_source` | `composites/decision.py` |
| 学习账本 | `revisions.jsonl` / `outcomes.jsonl`、删除的落盘（`land_delete`）、按目标排序提交（`commit_order`） | `composites/learn_ledger.py` |
| 演化文本存取 | 每个学习相位的 `profile.json` / `playbook.json`；读取紧邻上一个学习相位的版本；playbook 显示能力检查 | `composites/evolving_text.py` |
| 执行事实 | 从一张摸底或练习答卷读出执行者看到的目录与 playbook、打开过的卡及其步号 | `composites/sheet_facts.py` |
| 来源指针 | `trace_at`（唯一构造 `TraceRef`）/ `check_ref`（边界检查）/ `trace_of_trajectory` / `render_windows`（改写者读到的显式窗口）；`task_instruction`（按任务 id 取 benchmark 的任务说明） | `composites/trace.py` |
| 来源账本 | 每个技能版本一条 Provenance：`record_version`（唯一写入口）、`find_provenance`、`check_provenance` | `composites/provenance.py` |
| 相位库 | 相位技能库工件的布局、写出与读取：工件列出对象存储里的全部技能（含 `discarded`），按技能 id 排序；相位标签与相位目录名的互相映射（`phase_bank_dir`、`label_of_dir`）只在这里实现 | `composites/phase_bank.py` |
| 技能文件夹物化 | 按清单的库指纹核对相位库，把每条技能写成 `<skill>/SKILL.md`（YAML 头 + `render_card` 全文），另写 `catalog.md` 兜底；内容不变不重写 | `composites/skill_materialize.py` |
| 技能读取解析 | 从轨迹解析打开了哪些技能、在哪些步骤 | `composites/skill_use.py` |
| 空洞记录 | 已接受基础设施空洞的读取与写入 | `composites/shortfall.py` |
| 外部对照 | 外部冻结成绩文件的加载与校验 | `composites/external_control.py` |
| 配对统计 | 按任务自助法的置信区间、胜负平、McNemar 精确检验、单臂噪声底 | `composites/paired_stats.py` |
| 触发指标 | 触发召回、误触发、遵循度、分层成功率；练习摘要的 `skill_use`（`card_use_metrics`） | `composites/trigger_metrics.py` |

组合积木负责落盘和续跑：每一步的产物按确定 ID 存进 B3，再次运行时已完成的
单元直接读盘跳过。功能积木本身不读写磁盘。总要求：**在任何一处崩溃后续跑，
结果都与一次跑完相同**。

**K1 批量执行**的要点：

- 派发顺序按轮次（先所有题的第 0 次采样，再第 1 次……，同题采样不相邻）；
- 每个单元的工作目录里有 `k1_retry_cursor.json` 重试游标，续跑接着数；
  每个单元 3 次尝试，重试之间随机等待 15–45 秒；`--k1-stagger`
  让单元启动前随机等待错开连接；还有未完成单元时每 300 秒打一行心跳
  （done / failed / skipped / in_flight），"正常慢"与"卡死"可以区分；
- 续跑前核对已存单元的角色、模型名、注入技能与本次请求一致，不一致报
  配置错误；已存文件从不覆盖；
- `skip_unit_ids`：调用方声明为终态的单元（已接受的基础设施空洞）不执行、
  不记失败；
- 任一单元抛出基础设施以外的错误后不启动新单元；配置错误、服务方拒绝按
  原类型抛出，代码 bug 保留完整的报错回溯。

### 3.5 方法驱动

`methods/registry.py` 是一张纯数据表：（族, 名字）→（驱动模块, 参数类）。

| 族 | 名字 | 驱动 | 参数类 |
|---|---|---|---|
| `delivery` | `none` / `whole_bank` / `folder` | `methods/delivery/` 下各一个模块 | `NoneParams` / `WholeBankParams` / `FolderParams`（都在 `methods/delivery/params.py`） |
| `execution` | `real` / `real_folder` | 相位驱动直接使用参数 | `RealExecutionParams` / `RealFolderExecutionParams` |
| `review` | `review` | 学习相位驱动直接使用参数 | `ReviewParams`（`methods/review/`） |
| `author` | `subtask_cards` | `stages/phase_learn.py` 编排 `methods/cards/` | `CardAuthoringParams` |

`resolve_params` 只导入参数类所在的轻量包并严格校验（未知键、缺键、类型错都报错），
冻结步用它校验每个相位；`resolve` 另外按需导入驱动；`resolve_fixed` 解析不由配置选择的
名字（终测现场裸臂的 `none` 交付）。

学习流程的方法包：

| 包 | 内容 |
|---|---|
| `methods/diagnose/` | `diagnose_trial`（`TRIAL_FAILURE_ANALYSIS`）；`failing_step` 或 `scope` 越界、`reason` 为空的回复带原因重问（`DIAGNOSIS_CORRECTION`），最多 `REPAIRS` = 2 次；`Finding` 记录 |
| `methods/evolve_text/` | `evolve_text(kind, old_text, notes, facts, handle, profile, checks)` 外壳；不合格回复的重写说明取 `EVOLVE_CORRECTION`（形状不对）或 `PLAYBOOK_PARTS_CORRECTION`（缺必备部分） |
| `methods/revise/` | `revise_trigger`、`revise_content`、带版本守卫的 `apply_revision`；两个改写提示词收到的诊断段落由 `REVISION_DIAGNOSIS` 拼出 |
| `methods/cards/` | `CardAuthoringParams`（`__init__.py`）；`material.py` 的 `collect_material`（同类型的强模型成功段与弱模型失败段，筛段并渲染步骤，两种材料各最多 `max_segments_per_card` 段）；`writer.py` 的 `write_card`（`CARD_WRITE`；不合格回复带原因重写，说明取 `CARDS_CORRECTION`，最多 `write_retry_limit` 次） |
| `methods/review/` | `ReviewParams` |

### 3.6 相位 → 积木组装

| 相位 | 用到的积木 |
|---|---|
| 冻结步（p00） | D1、D2、B4、`methods/registry.py` 的逐族解析、提示词包检查 |
| baseline | 交付 `none`、E1 × K1（强、弱两臂）、判定核、D3、辅助指标、无效重派 |
| practice | 交付 `folder` + 技能文件夹物化（+ playbook）、E1 × K1（弱模型）、判定核、D3、技能读取解析、无效重派 |
| test | 配置的交付（`whole_bank` / `folder` + 技能文件夹物化，均可带 playbook）、E1 × K1（带库臂 + 对照臂或外部对照）、D3、技能读取解析、`decision.final_test_delta` |
| learn | 诊断 → K3 定位（K2 + G2）→ 证据分配 → 演化画像与 playbook → G3 写卡（D6 筛材料）/ G4 改写 / 删除 → 相位库工件 |

**跨 benchmark、跨模型**：加一个新 benchmark = 写一个插件（runner、任务加载、
截断规则、执行提示词、提示词包），其余积木不动；换强 / 弱模型 = 改配置的角色表；
做消融 = 在序列里增删相位、换交付方法、改 2.5 的开关。

### 3.7 Prompt 与 benchmark 提示词包

所有发给模型的 prompt 集中在 B2 注册表里统一管理，不在业务代码里散落字符串；
重问与重写说明这类拼进提示词的固定文本同样是注册的模板，代码按名字取用，冻结步一并冻结。

| prompt | 角色 | 用在哪里 |
|---|---|---|
| `SEGMENT` | segmenter | 学习相位的切段（失败轨迹与强模型轨迹） |
| `SUBTASK_TYPING` | segmenter | 学习相位把新段归入子任务类型 |
| `CARDS_CORRECTION` | segmenter / author | 归类或写卡回复不合格时填进 `correction_note` 的重问说明 |
| `TRIAL_FAILURE_ANALYSIS` | author | 学习相位诊断一张失败的摸底或练习答卷 |
| `DIAGNOSIS_CORRECTION` | author | 诊断回复不可用时填进 `correction_note` 的重问说明 |
| `PROFILE_UPDATE` / `PLAYBOOK_UPDATE` | author | 每个学习相位的文本演化 |
| `EVOLVE_CORRECTION` / `PLAYBOOK_PARTS_CORRECTION` | author | 文本演化回复不合格时填进 `correction_note` 的重写说明 |
| `CARD_WRITE` | author | 缺口写新卡 |
| `REVISE_TRIGGER` / `REVISE_CONTENT` | rewriter | 触发证据与正文证据的改写 |
| `REVISION_DIAGNOSIS` | rewriter | 两个改写提示词的 `diagnosis` 段落（每条证据的诊断原因） |
| `SKILL_APPLICABILITY_JUDGE` / `SKILL_ADHERENCE_JUDGE` | judge | `judge-skills` 分析工具 |
| `DEFAULT_SYSTEM` / `REPAIR_INSTRUCTION` | — | B1 自己的协议文本 |
| 各 benchmark 的执行提示词 | executor_strong / executor_weak | E1 的 runner |

**提示词包**：每个 benchmark 一个文件 `benchmarks/<name>/prompt_pack.py`，导出
`PROMPT_PACK`（`benchmarks/base.py` 的 `PromptPack`），插件用 `prompt_pack` 属性声明：

| 字段 | 内容 | TB | ALFWorld | SearchQA |
|---|---|---|---|---|
| `vocabulary` | 学习提示词的词汇槽（`LearningContext`：智能体是谁、能力族、最终动作、可观察的信号、段标签示例等） | 有 | 有 | 有 |
| `protocol` | 给写作者的环境事实（执行者每一步看到什么、技能怎样打开） | 有 | 有 | 有 |
| `executor_prompts` | 执行提示词的注册名 | `TB_TERMINUS_TEMPLATE`、`TB_SKILL_READ`、`TB_DISCIPLINE` | `ALFWORLD_TASK_PROMPT_SYSTEM`、`ALFWORLD_TASK_PROMPT_NO_HISTORY`、`ALFWORLD_TASK_PROMPT`、`ALFWORLD_TASK_PROMPT_NO_HISTORY_FOLDER`、`ALFWORLD_TASK_PROMPT_FOLDER`、`ALFWORLD_TASK_PROMPT_CORRECTION` | `SEARCHQA_TASK_PROMPT`、`SEARCHQA_TASK_PROMPT_FOLDER` |
| `playbook_seed` | playbook 的初始文本 | 空 | 空 | 空 |
| `playbook_facts` | 从摸底答卷计算统计事实的函数 | `tb/playbook.py` | `alfworld/playbook.py` | 无 |
| `playbook_checks` | playbook 必须包含的部分（`PlaybookRequirement`） | 5 项 | 6 项 | 无 |
| `overrides` | 方法提示词名 → 本 benchmark 的替换提示词名 | `PLAYBOOK_UPDATE` → `TB_PLAYBOOK_UPDATE` | `PLAYBOOK_UPDATE` → `ALFWORLD_PLAYBOOK_UPDATE` | 无 |

`ALFWORLD_PLAYBOOK_UPDATE` 的搜索规则里有一条两物体任务的规则（2026-11-15 起，"P1"）：
两物体任务算两次搜索。交付第一件、目的地只显示一件之后，第二次搜索从顺序的第一个种类重来，
单向、容器已搜完、种类已搜完三条规则随之重置，第一次搜索经过的地方重新允许访问。学生只看得到
最近两步，"记住看到过的副本再回去拿"无法执行，所以用"重来"代替。依据与 5 次 run 的结果见
`docs/reports/alfworld_e1_prompt_improvement_plan_20261115.md`。

接线：`cli.py` 构建模型句柄时（`run` 与分析子命令两处），把 `vocabulary` 的词汇与
`protocol` 填进句柄的 `prompt_defaults`，把 `overrides` 填进 `prompt_aliases`；
`call_model` 取提示词时先查 `prompt_aliases`，再按"`GENERIC` 缺省 < 句柄 `prompt_defaults`
< 调用方变量"的优先级填槽。冻结步检查：每个执行提示词与替换提示词都已注册，替换提示词的
占位符集合与被替换的提示词相同。

**冻结面**：manifest 冻结全部已注册方法 prompt 的 digest（`prompts.METHOD_PROMPT_NAMES`），
外加本 benchmark 的任务提示词、技能文件夹提示词、提示词包的执行提示词与替换提示词；
提示词包的 `vocabulary`、`protocol`、`playbook_seed`、`overrides` 原文写进 manifest 的
`prompt_pack`；`playbook_facts` 与 `playbook_checks` 是代码，由正式 run 固定的提交覆盖。
两条静态测试守住冻结面：`prompts/*.py` 每个模块必须进 `prompts/__init__.py` 的 import
清单；prompt 注册只允许出现在 `prompts/` 与 `benchmarks/<名字>/`。改一个字的 prompt
文本即视为新实验，必须新建 run 目录。

### 3.8 代码目录

目录结构、分层规则和开发规范见 [AGENTS.md](../AGENTS.md) 第四节。积木到目录的对应：
基础件在 `core/`、`llm/`、`prompts/`、`infra/`、`inject/`；benchmark 插件
在 `benchmarks/`；功能积木在 `blocks/`；组合积木在 `composites/`；
方法驱动与参数契约在 `methods/`；相位流程与分析工具在 `stages/`；入口是 `cli.py`。
顶层包的 `__init__.py` 只有文档字符串，使用方 import 具体子模块；`prompts/__init__.py`
是例外，它按 import 清单登记全部方法 prompt。

### 3.9 论文分析与准备工具

这些工具读已完结的 run，结果写到 run 之外的目录，从不写被读的 run。`judge-skills`
的输出目录首次设置写进 `analysis_config.json`，续跑时设置不同即拒绝
（`stages/analysis_common.py`）。

| 工具 | 回答什么 | 模型调用 |
|---|---|---|
| `import-bank`（`stages/import_bank.py`） | 把其他方法产出的技能（markdown 文档、`<名字>/SKILL.md` 文件夹、notes 行）写成测试专用 run 的 run 根技能库（状态 `active`），技能编号 `import/<来源>/<键>`，原文存进 `Skill.verbatim_text` | 无 |
| `import-baseline`（`stages/import_baseline.py`） | 在新 run 第一次 `run` 之前，把已有 run 的摸底轨迹（schema 2，编号不带相位）改写编号（相位标签取新配置序列第一项）后以 schema 3 写进新 run 的轨迹存储，只导入新配置的 train 任务，其余字段原样；新 run 的摸底相位零执行，照常产出答卷、判定与摘要；导入的轨迹都已存，没拿到判分结果的照常判为无效、不重跑（只有什么都没存下的采样才按 `invalid_redispatch_limit` 重派）。核对 benchmark、两个执行模型名、两臂采样次数、新配置的 train 列表 ⊆ 源任务集、每个（任务、角色、序号）恰好一条、源轨迹 schema 为 2，任何一条不过即配置错误、什么都不写；新 run 目录已有 manifest 或轨迹时拒绝。写 `BASELINE_ORIGIN.md`（源 run 路径、核对结果、逐角色条数与成功数、全部导入文件 sha256 清单的摘要） | 无 |
| `fork-run`（`stages/fork_run.py`） | 把已完结 run 的前 K 个相位拷进新 run，让新配置从第 K+1 个相位接着跑 | 无 |
| `judge-skills`（`stages/judge_skills.py`，`blocks/adherence.py`，`composites/trigger_metrics.py`） | 一个 test 或 practice 相位里，技能目录展示后：该适用的卡有没有被打开（触发召回）、不适用的卡有没有被打开（误触发）、打开之后步骤有没有被执行（遵循度） | 每个 trial 一次适用性判定，每张被打开的卡一次遵循度判定 |
| `paper-tables`（`stages/paper_tables.py`，`composites/paired_stats.py`） | 已完结的 run 之间的配对比较：按任务自助法的置信区间、胜负平、McNemar 精确检验、单臂噪声底；行列顺序跟随 spec，不按结果排序 | 无 |
| `segmentation-metrics`（`stages/segmentation_metrics.py`） | 分段消融的机制指标：测试 avg@3 / pass@3、初始与最终技能库大小、技能构造锚点不在任务开始的比例、任务中途打开率 | 无 |

预登记指标（看结果前固定，不选择口径）：触发召回 = 打开且适用的（trial, 卡）对 / 适用的对；
误触发率 = 打开且不适用的对 / 打开的对（两者都在 (trial, 卡) 对上汇总，不按 trial 平均）；
遵循度 = 被打开的卡的步骤得分均值（followed 记 1、partial 记 0.5、skipped 记 0，
not_reached 不计）；成功率按"适用且打开 / 适用未打开 / 无适用卡"分层报告。
`paper-tables` 的主指标是条件口径的 avg@N，保守口径与 pass@N 同时写进 JSON；无效、缺失、
空洞和未判分的 trial 不当作 0，列在 `meta["unscored"]`。

---

## 四、数据模型与 run 目录布局

### 4.1 对象

全部 JSON 落盘，外键记录关系，ID 按 3.1 第 6 条的确定规则生成（对象定义在
`core/models.py`，ID 规则在 `core/ids.py`）：

```
Trajectory（轨迹，schema 3）
  trajectory_id = traj/<phase_tag>/<task_id>/<role>/<attempt:02d>
                  （phase_tag 是产生它的摸底或练习相位标签，冒号换成下划线，如 p01_baseline、p03_practice；
                  `ids.parse_trajectory_id` 返回 (phase_label, task_id, role, attempt)，相位标签在最前）
  task_id, benchmark, model_role(strong/weak), model_name, attempt_index,
  skills_injected(null = 裸跑；否则为整库引用 `bank:<12 位十六进制>`，由整库每张卡的 id、版本和文本算出),
  steps[ {action, observation} ... ],
  correctness(判分器结果：true / false / null), meta(终止原因等自由数据)

Subtask（段，schema 2）
  subtask_id = <trajectory_id>/seg<i:02d>
  phase_label(切出它的学习相位), task_id, trajectory_id, source(strong/weak), trajectory_success,
  segment_index, step_range[start, end), label, description,
  success(该段目标是否达成), context_summary
  （段内逐步原文不另存，按 step_range 从 Trajectory 取）

SubtaskType（子任务类型，schema 1；stage2_segments/subtask_types.jsonl 的一行）
  type_id = type/<serial:03d>（按出现顺序由程序生成，表只增不改）
  name(简短的子任务名), entry_state(可观察的起点状态), phase_label(新增它的学习相位)

段归属（stage2_segments/segment_types.jsonl 的一行）
  subtask_id, type_id, phase_label(归类它的学习相位)

Skill（技能，schema 5；没有 subtask_id 字段）
  skill_id = <type_id>/skill（`ids.make_skill_id` 只接受类型 id）；导入技能 import/<来源>/<键>
  subtask_type(type_id；导入技能为空),
  source_subtasks[ subtask_id ... ](写卡材料段),
  summary(一句话：这条技能是什么；单行),
  when_to_use(什么时候打开：这个子任务的起点状态),
  steps[ str ... ](不带编号的步骤列表),
  not_applicable_when(什么时候不用),
  verbatim_text(只有导入技能有：外部方法的原文；此时四个结构字段除 summary 外可为空),
  version(从 1 起，每次改写加一), status(active/discarded),
  discard_reason(自由字符串),
  rewrite_history[ {side: trigger|content, old: 被替换的字段及旧值, reason} ... ]

EvolvingText（演化文本）
  kind(profile/playbook), text, version(从 1 起), phase_label(写出它的学习相位),
  source_sheet_ids[ 提供了非空笔记的答卷 id ... ]

Finding（诊断记录，学习相位的 findings.jsonl）
  sheet_id, reason, failing_step, scope(subtask/general), model_note, playbook_note

Evidence（证据记录，学习相位的 evidence.jsonl）
  sheet_id, failing_step, subtask_id(出错段), type_id,
  skill_id(该类型在用的卡，可为空), opened_before(出错那一步之前是否打开过),
  opened_step(这张卡第一次被打开的步号，可为空，可能不早于出错步),
  category(gap / trigger / content / none)
  （每条诊断成功且定位成功的失败一行，`scope = general` 的记 none）

回执（学习相位的 outcomes.jsonl，schema 标记 learn-outcome-v3）
  phase_label, target_kind(type / skill), target_id(类型 id 或技能 id),
  action(add / revise_content / revise_trigger / delete), skill_id(可为空),
  outcome(见 5.4), version(可为空), reason, basis_unit(第一条证据的执行单元)
```

两个值对象定义在 `core/trace.py`：

```
TraceRef（精确位置）
  phase_label, sheet_id(恰好一张摸底或练习答卷), step(该答卷 steps 的 0 起下标)

Provenance（技能版本的来源，每个版本一条）
  skill_id, version, phase_label(产出该版本的学习相位),
  cause(add / revise_trigger / revise_content),
  sources[ TraceRef ](背后全部证据位置), note(写作或改写说明)
```

技能的触发条件（`TRIGGER_FIELDS` = `summary`、`when_to_use`）是执行者在目录里看到的部分，
正文（`CONTENT_FIELDS` = `steps`、`not_applicable_when`）是打开后才看到的部分。一次改写
只改一侧，另一侧逐字节不变。

**discard_reason 的词汇**：字段是自由字符串，系统写入的词：`content_failed_twice`（相邻两个
学习相位都拿到正文证据）。

### 4.2 correctness 词汇与运行指标

"这次运行结果如何"由三样东西分开表达：

- **`correctness: Optional[bool]`**：benchmark 自带判分器的原样结论。
  `null` 表示没拿到判分结果（没有判分器、判分器没跑、或模型没有产出可用回复）。
  `null` 运行永远不是任何一方的测量成绩；
- **运行指标**：重试次数、每次报错的原文、耗时、退出状态，随运行记录传给验收；
- **`InfrastructureError` 异常通道**：沙箱超时、断网、进程死亡这类失败
  不进结果词汇，插件抛异常、K1 按重试策略重试。异常 = 根本没拿到可交付的结果（重试）；
  `null` = 拿到了结果但判分器没给结论（不重试，交验收处理）。

验收的判定词汇（`core/vocabulary.py`）：success / failure / invalid；单元状态 normal /
not_testable；not_testable 的原因 `invalid_redispatch_exhausted`、`all_holes`、`zero_execution`。

### 4.3 run 目录布局

对象存储在 run 级共享，全部相位读写同一份：

```
<run>/
  manifest.json                  冻结的输入与协议（含 prompt_digests 与 prompt_pack）
  run.lock                       run 目录排他锁（B7），记持有者 pid
  pipeline_log.jsonl             逐步完成的摘要账本（key 恒为 "stage"，
                                 值为 p00:freeze 与各相位的 pNN:<名>；
                                 续跑追加新行，读取取每个标签的最后一行）
  failed_units.jsonl             单元失败账本（单元 ID、相位标签、错误类别、消息），只追加
  stage0_summary.json            冻结步摘要
  FORKED_FROM.md / BANK_ORIGIN.md / BASELINE_ORIGIN.md
                                 fork-run / import-bank / import-baseline 准备的 run 才有

  —— run 级对象存储（全部相位共享）——
  stage1_baseline/trajectories/  轨迹：摸底与全部练习相位的轨迹（编号带相位标签）
  stage1_baseline/accepted_infra_shortfall.json   摸底相位已接受的空洞（两臂共用一份）
  stage2_segments/subtasks/      段（失败轨迹与强模型轨迹切出的段）
  stage2_segments/segmentation_audit.jsonl
                                 切段审计：重切耗尽、0 步空轨迹，本 run 内终态
  stage2_segments/subtask_types.jsonl   子任务类型表（只追加）
  stage2_segments/segment_types.jsonl   段 → 类型的归属（只追加）
  stage3_generation/skills/      技能（含导入技能）
  skill_bank.jsonl               run 根技能库：import-bank 写入；没有学习相位工件时交付读它

  —— 相位目录（每个相位一份）——
  phases/pNN_<局部名>/
    phase.json                   执行相位：这个目录里跑过什么（标签、序号、局部名、角色），
                                 供人查阅
    units.jsonl                  执行相位：交付解析出的执行单元清单（带完整 unit_id；
                                 每次相位启动整文件重写）
    unit_specs.jsonl             执行相位：每个预期单元的 UnitSpec
    answer_sheets/               执行相位：trial 级 AnswerSheet 的 JSON + Markdown；
                                 test 相位两臂都写在这里
    coverage.jsonl               执行相位：每个预期 trial 的 CoverageRecord
    <臂>/redispatch.json         摸底与练习相位：每个臂按单元持久记录的无效重派计数
    <臂>/store/                  test 相位自己的轨迹存储（带库臂与现场裸臂分开）
    <臂>/accepted_infra_shortfall.json   练习与 test 相位：本臂已接受的空洞
    skill_folder/                folder 交付物化出的技能文件夹，全部单元共享；库为空时
                                 也物化一个空目录
    external_control.json        test 相位：外部对照成绩文件的字节拷贝
    phase_summary.json           执行相位摘要（完成信号：已完成的执行相位重放时整个跳过）

    findings.jsonl               学习相位：逐张失败答卷的诊断（Finding）
    evidence.jsonl               学习相位：逐条失败的证据分配（Evidence）
    profile.json / playbook.json 学习相位：本相位的画像与 playbook（EvolvingText）
    revisions.jsonl              学习相位：改写前的版本号（改写调用前先写）
    outcomes.jsonl               学习相位：逐卡回执（见 5.4）
    provenance.jsonl             学习相位：每个技能版本一条 Provenance
    skill_bank.jsonl             学习相位：本相位的技能库工件（全体成员，含 discarded；
                                 下一个执行相位的交付从这里读）
    bank_summary.json            工件摘要（成员 / 大小 / 整库 digest）
    learn_summary.json           学习相位摘要（最后写；带 bank 键即该相位的完成信号；
                                 键见 5.4）

  —— 其他 ——
  work/                          执行单元的工作目录（K1 重试游标、TB 提交记录与取回的原始文件）
  llm_requests/                  B1 记录的每次模型请求与响应，不覆盖、不含 key
  llm_requests/usage.jsonl       用量账本，跨进程累加
  *.lock / *.interrupted-tail    JSONL 账本旁的加锁文件 / 被打断的半行
```

失败类账本都是**第一次记失败时才创建**：一个干净跑完的 run 目录里没有这些
文件，不能拿"文件在不在"当判据。

---

## 五、每个相位的流程

命令行循环（`cli.py` 的 `run_experiment`）按冻结序列逐相位派发：执行相位进
`stages/phase_execute`（`baseline.py` / `practice.py` / `final_test.py` 三个角色模块，
`engine.py` 是派发→重派引擎，`phase_disk.py` 管相位目录，`resolve.py` 的
`resolve_phase` 一次把配置映射成驱动与参数），学习相位进 `stages/phase_learn.py`。每完成一个相位打印一行摘要并写
进度账本。**续跑 = 同一条命令重跑**：每个相位按磁盘上已有的产物跳过已完成的单元。

### 5.1 冻结步（p00）

- 核对具名规则名、逐相位解析全部方法族并校验参数、核对 playbook 显示能力、校验外部对照
  文件存在且 sha256 匹配；
- 加载任务（D1）、推导划分（D2）；显式划分时四个列表必须恰好覆盖加载的任务集；
- 冻结进 manifest：`benchmark`、四键划分列表（train / val / test / reserved）、完整配置（含 `sequence` / `phases` / 生效
  规则名 / `model_options` / `token_budgets`）、`prompt_digests`、`prompt_pack`、
  `rules`、`model_roles`、任务内容摘要 `tasks_digest`、辅助指标名 `aux_metric`；
- 之后每次启动逐字段比对，再改即视为新实验（B4）。运行设置（并发、重试次数）不冻结。

### 5.2 摸底相位（baseline）与练习相位（practice）

**摸底（baseline）**：

- 强模型与弱模型在每道训练题上各裸跑 `n_attempts_strong` / `n_attempts_weak`
  次，全部轨迹（成功和失败都保留）写进 run 级轨迹存储，轨迹编号带相位标签；
- 每次运行写一张 AnswerSheet，按 benchmark 默认判分规则判定；
- 摘要含两臂的 D3 成绩与辅助指标；
- 摸底可以复用已有 run 的结果：`import-baseline`（3.9）在新 run 第一次启动前把源 run 的摸底轨迹
  改写编号后写进轨迹存储，摸底相位于是全部"已存即已完成"，零模型调用，照常产出答卷与摘要。

**练习（practice）**：

- 在 `tasks` 指定的训练集或验证集上执行；只跑弱模型，交付与终测相同：`folder` 交付把紧邻的上一个学习相位的技能库里全部 `active`
  的卡物化成技能文件夹，`playbook: evolved` 时显示同一个学习相位的 playbook；库为空（例如
  第一个学习相位一张卡都没写，或 `no_skills` 臂）时仍物化一个空的 `skill_folder/`，单元仍挂
  文件夹清单（没有文件）、用同一个文件夹执行提示词，playbook 的位置与带库时相同，只是技能目录
  为空（TB 照常上传 skill-read 后缀）；
- 采样次数取执行参数 `attempts`（缺省为执行者的全局次数）；轨迹写进 run 级轨迹存储，编号带
  相位标签，与摸底及别的 `practice` 不撞号；
- 每次运行写一张 AnswerSheet，判定与摸底相同（相位的 `acceptance`，缺省 benchmark 默认规则）；
  答卷的 `execution.artifacts` 带轨迹 id 与该单元的文件夹清单（`trajectory_id`、`skill_folder`），
  学习相位据此还原执行者看到的目录与 playbook；读取了哪些卡、在哪一步由
  `composites/skill_use.py` 从轨迹解析；
- 摘要键：`role`、`tasks`、`executor`、`n_attempts`、`bank_reference`、`playbook_shown`、
  `metrics`（D3 成绩）、`skill_use`、`skill_files_read`（`{轨迹 ID: [技能编号]}`）、
  `infra_missing`、`infra_accepted`、`complete`、`infra_shortfall_accepted`。`skill_use` 是机制指标
  （只描述，不做显著性判断，`trigger_metrics.card_use_metrics`）：`trials`、`valid`、`success`、
  `success_rate`、`opened_any`（打开过至少一张卡的有效 trial）、`opened_any_success`，以及
  `cards`（交付过的每张卡：`opened`、`opened_success`）；`practice` 后面有学习相位时，各证据类别的
  计数由该学习相位的摘要给出。

两种相位共同的规则：

- 派发了却什么都没存下的采样按 `invalid_redispatch_limit` 重新派发；已存的无效运行不重跑（5.5）；
- 有未接受的基础设施空洞时写完摘要后以基础设施错误停下（退出码 3），续跑
  只补空洞；接受的空洞是本 run 的持久决定（见 5.7）。

### 5.3 终测相位（test）

- 在 test 列表上跑带库臂：交付方法由配置给出（`whole_bank` 整库注入 / `folder`
  技能文件夹），两者都只用 **`active`** 的卡；相位配置 `playbook: evolved` 时把紧邻的上一个
  学习相位的 playbook 放在技能块最前面（folder 单元另把它写进清单的 `playbook_text`）；
- **`whole_bank` 交付**：缺省把每条技能渲染成固定的库版式注入；`verbatim: true` 让每条
  技能的 `render_card` 文本（导入技能即原文）逐字节原样注入，条目之间空一行；
- **`folder` 交付（技能文件夹）**：每张在用的卡写成自己的目录 `<skill>/SKILL.md`，
  文件开头是 YAML 头（`name` 和 `description`），后面是 `render_card` 全文；`catalog.md`
  是兜底目录。交付解析出的每个单元在 `aux["skill_folder"]` 里带一份清单：目录文本、
  每条技能的相对路径映射、库指纹、描述拼法，不带技能正文（正文的唯一来源是相位技能库）。
  `description_mode` 控制 `description` 的拼法：`summary` / `when_to_use` /
  `summary_then_when_to_use`（缺省）。派发前相位驱动把清单物化成相位目录下的
  `skill_folder/`（`materialize_skill_folder` 幂等；库指纹与相位库不符是代码 bug）。
  插件用能力位 `skill_folder_prompt_name` 声明支持，不支持的插件在派发前被拒绝；
  库为空时与 5.2 相同：物化一个空的 `skill_folder/`，单元仍挂清单（没有文件）、用文件夹执行
  提示词，playbook 照常显示；
- **TB 的技能文件夹**：TB 的首轮提示词由 Harbor 组装，依次是 Terminus 自己的模板
  （`TB_TERMINUS_TEMPLATE` 是它的逐字节副本，只用于冻结 digest 和给诊断渲染任务文本）
  与任务描述、本插件上传的 `SKILL_READ_SUFFIX.md`、Terminus 自己生成的
  `<available_skills>`。技能目录随 skills 上传进沙箱 `/opt/openclaw-skills/<task>/`；
  Terminus 只列出 `<task>/<skill>/SKILL.md`（带 YAML 头），任务级 `SKILL.md` 只是挂载
  检查要求存在的占位文件，智能体看不到。所以执行侧能控制的只有后缀，全部文本在
  `benchmarks/tb/protocol.py`：后缀依次是 playbook（标题与 ALFWorld 相同）、
  `whole_bank` 的技能块（只在整库交付时有）、读卡规则 `TB_SKILL_READ`（只在 folder
  单元有，与 ALFWorld 的 ReadSkill 规则同义：卡可选，用 `cat <位置>` 打开，最多 3 张，
  描述与当前情况相符才打开）、工作纪律 `TB_DISCIPLINE`（shell 卫生、阈值留余量、完成前
  自检）。不带技能的单元不上传后缀（插件属性 `playbook_in_skills_block = False`）；
- **本地 benchmark（SearchQA、ALFWorld）的渐进式披露**（`benchmarks/skill_folder.py`）：
  提示词里只有 `<available_skills>` 目录（名称、描述、位置，XML 形状与 Terminus 一致，
  内容读自物化文件的 YAML 头）；模型回复读取动作时，循环自己返回整份 `SKILL.md`，
  评测环境收不到这个动作。SearchQA 的读取动作是整行 `ReadSkill[<位置>]`：每次尝试的前
  3 次读取不占环境步数，读到的卡片作为该步的观察留在历史里，之后的读取被拒绝并占一步。
  ALFWorld 的读取动作写在 `<action>ReadSkill[<位置>]</action>` 里，循环在交给环境前拦下，
  读取不占统一协议的两个记忆槽位：每次尝试最多打开 3 张**不同的**卡，超过上限的新卡请求
  得到拒绝，位置写错得到报错；已打开的卡一直留在提示词的 "Opened skill cards" 段落里直到
  该次尝试结束；重复读取、被拒绝和位置写错的读取前 10 次不占环境步数，之后每次占一步，
  保证病态循环会终止；
- `real_folder` 执行的参数是 `executor`（缺省 `executor_weak`）与 `attempts`（终测同样遵守：
  设了就是每道题的采样次数，缺省取执行者的全局次数）；模型实际读了
  哪些技能文件由 `composites/skill_use.py` 从轨迹解析（TB 的 `cat` 命令与本地的
  `ReadSkill[...]` 同等看待），写进摘要的 `skill_files_read`（`{轨迹 ID: [技能编号]}`，
  只盖带库臂）；
- **对照臂**：`fresh_bare` 在相位内现场跑一条裸臂（`none` 交付，不带 playbook），两臂的
  轨迹存储分开；`external_campaign` 零执行，派发前加载并校验成绩文件（文件存在、sha256
  与冻结配置一致、`model_key` 覆盖全部 test 任务、每任务奖励数不少于采样深度），把文件
  字节拷进相位目录，摘要带 `external_control` 记录。外部对照的每任务成绩 = 前 n 次采样
  奖励的均值（n = 执行者的采样深度）；对照检查先于带库臂的任何花费；
- **每次全量重跑**：不调无效重派；已完成的 test 相位重放时整个跳过；
- **不进学习**：delta 由 `decision.final_test_delta` 直接进摘要。摘要并列报告三种口径
  （事先登记）：`avg_at_n`（条件口径：先在每道题的有效执行里求成功率，再对至少有一次
  有效执行的题求平均）、`avg_at_n_conservative`（保守口径：所有题都进分母，没有有效
  执行的题记 0）、pass@N；任一臂没有有效执行时条件口径的 delta 记 null；
- **辅助指标同口径并列**（插件声明了 `aux_metric_name` 时，如 SearchQA 的 `sub_em`）：
  两臂摘要各带 `aux` 一节，辅助指标永远只并列，不替换主指标。

### 5.4 学习相位（learn）

学习相位的输入是紧邻的上一个摸底或练习相位里**弱模型**判为失败的答卷；技能库 = 上一个学习相位
的工件（第一个学习相位读 run 根技能库，从零跑时为空）。每个学习相位执行五步；没有数据依赖的
单元放进同一个并行池（并行数 `--workers`），按下面的分组执行，组与组之间顺序进行：

```
组 A  诊断（每张失败答卷） ‖ 切段（每条失败轨迹） ‖ 切段（摸底强模型轨迹，仅第一次学习）
组 B  归类（按块顺序，通常一块） ‖ 演化画像
组 C  分配证据（纯程序）
组 D  演化 playbook（读本相位的画像与触发证据计数）
组 E  写新卡与改写（每张卡一个单元；同一张卡先改正文再改触发条件）
```

失败轨迹不等诊断结果就先切段（`scope = general` 的段只是不被用到）。并行只用于计算：JSONL
账本按确定顺序（答卷 id、段 id、类型 id、技能 id）提交，对象按确定 id 存储，所以产物与
`--workers 1` 逐字节相同。下面按逻辑顺序说明五步：

**第 1 步：诊断**（`methods/diagnose`，角色 author）。

- 对每张失败答卷调用 `TRIAL_FAILURE_ANALYSIS`。输入：协议、任务说明、完整的执行步骤（含读取
  动作）、执行者看到的目录与 playbook、执行者打开过的卡全文（裸跑时填 "(none)"）。诊断不读画像；
- 输出 `reason`（失败原因）、`failing_step`（决定性出错的那一步，0 起）、`scope`
  （`subtask` / `general`）、`model_note`、`playbook_note`；
- `failing_step` 越界、`scope` 越界、`reason` 为空的回复带原因重问（`DIAGNOSIS_CORRECTION`），
  最多 2 次（在 `call_model` 的 JSON 格式重试之外）；
- 一步都没有的失败答卷没有可指认的出错步：不调模型，直接按模型输出错误处理（终态，同下）；
- 诊断逐条写进 `findings.jsonl`，续跑只问缺的。重问后仍不可用的诊断（模型输出错误）记进
  `failed_units.jsonl`，该答卷既没有笔记也没有证据，这条记录是终态，续跑跳过。基础设施错误
  同样记账，全部答卷问完后相位在这一步停下（退出码 3），续跑重试。

**第 2 步：定位**（`composites/locate.py`，角色 segmenter）。

1. 对每条失败轨迹切段（K2，参数 `K`、`retry_limit`），`failing_step` 所在的段是出错段；
2. 写卡需要正面材料：第一次学习按 `trajectory_select` 规则选出摸底里全部任务的强模型轨迹
   切段，之后的学习相位直接复用（练习在验证集上时，正面材料同样只来自训练集的摸底）；
3. 一次归类调用 `SUBTASK_TYPING` 把本相位新切出的全部段（段卡片：标签、描述、成败、截断到
   `max_chars_per_segment` 的步骤）连同现有类型表归入类型：每段归入现有类型或新增类型（名字 +
   可观察的起点状态）；段数超过 `typing_chunk_size` 时按段编号分块，每块都带完整的现有类型表与
   前面块新增的类型。回复漏段、重复、编造段，或引用既不在表里也没声明的类型时，带原因重问
   （`CARDS_CORRECTION`），最多 2 次（`TYPING_REPAIRS`，在 JSON 格式重试之外）。新类型的编号由程序
   生成（`type/NNN`）：按段编号顺序第一次被用到的先编号，与回复里的顺序无关；声明了却没有段用到的
   类型丢弃。类型表写进 `stage2_segments/subtask_types.jsonl`，段的归属写进
   `stage2_segments/segment_types.jsonl`，两者只追加。一块的新类型先于它的归属写盘；两次追加之间
   崩溃会留下没有段用到的类型，下一次归类前删掉本相位这样的类型再继续——这是"只追加"的唯一例外，
   更早相位的行从不改动；
4. 定位不了的失败不产生卡证据，笔记照常进第 4 步：K2 重切耗尽的轨迹记在
   `segmentation_audit.jsonl`（本 run 内终态）；重问后仍不可用的归类块把其中每段记进
   `failed_units.jsonl`（终态，续跑不再归类）；出错步不在任何已归类段里的答卷记一行错误类别为
   `Unlocated` 的 `failed_units.jsonl`。

**第 3 步：分配证据**（`composites/evidence.py`，纯函数）。按 1.5 的表为每条定位成功的失败
写一行 `evidence.jsonl`：答卷 id、出错步、出错段、类型、该类型在用的卡（可为空）、出错前是否
打开过它及打开的步号、证据类别。"在用的卡"取本相位起点的技能库，即上一个学习相位发布的库工件
（第一个学习相位读 run 根技能库）。

**第 4 步：演化画像，再演化 playbook**（`methods/evolve_text`，角色 author）。

| 参数 | 画像 | playbook |
|---|---|---|
| 提示词 | `PROFILE_UPDATE` | `PLAYBOOK_UPDATE`（提示词包可替换） |
| 起点 | 上一个学习相位的画像；第一次学习为空 | 上一个学习相位的 playbook；第一次学习为提示词包的 `playbook_seed` |
| 笔记 | 本相位全部诊断的 `model_note` | 本相位全部诊断的 `playbook_note` |
| 事实 | 无 | 第一次学习时由提示词包的 `playbook_facts` 从摸底答卷计算；之后的学习相位是第 3 步得出的触发证据计数（哪类卡在失败里出错前没有被打开、几次） |
| 画像段 | 无 | 本相位刚写出的画像 |
| 输出检查 | 非空的 `profile_text` | 回复末尾 `<playbook>…</playbook>` 之间的文本，长度在 300–20000 字符之间，并通过提示词包的 `playbook_checks`；不合格时带着缺失项重写，最多 2 次 |

笔记全部为空且没有事实时不调模型，原样复制上一版文本。每个学习相位都写出
`profile.json` 和 `playbook.json`，版本号加一，写盘先于本相位的提交点。读取方只读紧邻的
上一个学习相位的版本，缺失即报错。本相位的写卡与改写都读这一步写出的画像和 playbook。

**第 5 步：演化技能库**（`methods/cards`，写卡角色 author，改写角色 rewriter）。

| 证据 | 提示词 | 输入 | 输出 |
|---|---|---|---|
| 缺口 | `CARD_WRITE` | 类型（名字、起点状态）；同类型的强模型成功段（正面做法，按 `segment_select` 筛，缺口失败所在任务的段在前，最多 `max_segments_per_card` 段）；本相位该类型的弱模型失败段（`write_lessons` 开着时，缺口的出错段在前，同样最多 `max_segments_per_card` 段）；画像；当前 playbook | 四个字段：`summary`、`when_to_use`（写起点状态）、`steps`（3–7 步）、`not_applicable_when` |
| 触发证据 | `REVISE_TRIGGER` | 卡；执行者看到的目录；每条触发证据的真实步骤（窗口 `max(出错段起点, 出错步 − 15)` 到出错步，含出错步，`render_windows`）；当前 playbook；画像 | `summary`、`when_to_use`、`revision_reason` |
| 正文证据 | `REVISE_CONTENT` | 卡；每条正文证据的真实步骤（窗口 `max(第一次打开这张卡, 出错步 − 15)` 到出错步之后 5 步）与诊断原因；画像；当前 playbook | `steps`、`not_applicable_when`、`revision_reason` |

- 新卡 `skill_id = <type_id>/skill`，版本 1，状态 `active`；同类型没有任何强模型成功段时不写，
  回执 `skipped_no_material`；类型的卡已被删除（`discarded`，编号被占）时不写，回执
  `skipped_discarded`；
- 写卡回复不合格（四个字段、3–7 步）时带原因重写（`CARDS_CORRECTION`），最多 `write_retry_limit`
  次；仍不合格回执 `write_failed`。`write_failed` 与改写的 `revision_failed` 另各记一行
  `failed_units.jsonl`；
- 一次改写只修改一侧字段，版本号加一，`rewrite_history` 记录被改的旧字段；一个相位里两侧
  都有证据时先改正文、再改触发条件；
- 上一个学习相位也给这张卡正文证据时不再改写，状态设为 `discarded`，原因
  `content_failed_twice`；
- 版本守卫：改写调用前先把起始版本写进 `revisions.jsonl`，新版本经 `record_version` 落盘，
  再写回执；续跑跳过有回执的卡，版本已经加一的卡只补回执、不调用模型。

**来源与上下文**（`docs/decisions/provenance_trace_context_20261029.md`）：

- `TraceRef` 只能由 `trace_at(sheet, step)` 构造：参数必须是一张摸底或练习答卷，
  校验 `0 <= step < len(steps)`；
- `check_ref(run, ref)` 是边界检查：指针必须指向一张已存答卷的某一步，答卷按
  `phases/<相位>/answer_sheets/<答卷 id>.json` 精确查找，找不到或越界直接报错；
- 改写读到的真实步骤由 `render_windows(run, windows)` 渲染，每个切点带显式窗口（见上表，
  窗口起点至多在出错步之前 `WRITER_BEFORE_STEPS`（15）步）。同一次执行上的多个窗口合并成
  步号的并集，空隙用 `[steps a-b are not shown]` 标出；
- 技能版本统一经 `provenance.record_version(store, phase_dir, run_dir, skill, provenance)` 落盘，先写 Provenance 行
  再存技能：两步之间崩溃时续跑重做这一版本，`record_version` 找到已有行后只补存技能，
  不会出现没有来源的已存版本。`sources` 是证据位置：新卡 = 材料段起点与缺口失败的出错步；
  改写 = 各条证据的出错步；
- 边界检查：每个学习相位结束时校验本相位每个新版本都有 Provenance、`sources` 可解析。

**提交点与续跑**：每个学习相位结束都产出自己的技能库工件（`skill_bank.jsonl`，全体成员，
含 `discarded`）与 `bank_summary.json`。`learn_summary.json` 最后写，带 `bank` 键即该相位的
完成信号，已完成的学习相位重放时整个跳过。写卡与改写遇到基础设施失败的卡记进
`failed_units.jsonl`；有待重试的目标时相位写完摘要（带 `infra_failed` 列表）后在产出工件前抛错
（退出码 3），续跑补完没有回执的行。

`learn_summary.json` 的键：`role`、`input_phase`（读的执行相位标签）、`first_learn`、
`failed_sheets`、`diagnosed`、`diagnosis_failed`、`scopes`（`subtask` / `general` 计数）、
`segments_cut`、`segments_untyped`、`types`（`table` 总数、`new` 本相位新增）、`located`、
`unlocated`、`evidence`（四个类别的计数）、`profile_version`、`playbook_version`、`card_units`、
`outcomes`（各回执的计数），完成时另有 `bank`（`members` / `size` / `bank_reference`）。

**回执**（`outcomes.jsonl`，每个操作一条 `outcome`：一个缺口类型的写卡、一张卡一侧的改写、一次删除；
同一张卡在一个相位里正文与触发两次改写是两个操作）：

| 回执 | 含义 |
|---|---|
| `written` | 缺口写出新卡 |
| `skipped_no_material` | 缺口没有同类型的强模型成功段，不写卡 |
| `skipped_discarded` | 缺口类型的卡已被删除，不写新卡 |
| `write_failed` | 写卡输出在 `write_retry_limit` 次重写后仍不合格，不写卡（终态） |
| `content_revised` / `trigger_revised` | 改写产出新版本 |
| `unchanged_after_revision` | 改写结果与原字段相同，不产生新版本 |
| `revision_failed` | 改写输出两次都不合格，卡保持原版本 |
| `discarded` | 卡被删除，原因 `content_failed_twice` |

### 5.5 判定与无效重派

- 每张 trial 的判定走 `verdicts.grade_sheet`，规则按相位 `acceptance` 字段选（缺省用
  benchmark 默认规则：tb → `tb_scorer`，searchqa / alfworld → `scorer`）。判定词汇：
  success / failure / invalid（无效 = 运行没有正常完成，不能算模型成绩：判分器没跑、例外死亡）；
- 摸底与练习相位里，只有派发了却什么都没存下的采样（执行的重试耗尽，`engine.run_arm_units`
  按应有轨迹编号核对）会被相位调度重新派给执行，预算是 `invalid_redispatch_limit`（按臂、按单元
  持久计数 `<臂>/redispatch.json`，续跑接着同一预算）；预算耗尽仍缺的采样是基础设施空洞（5.7），
  单元没有一次有效运行、且有采样缺失时报 not_testable / `invalid_redispatch_exhausted`；
- 已经落盘的运行从不重跑：没拿到判分结果（`correctness = null`）的运行判为 invalid，每次采样的
  轨迹编号是确定的，重新采样会超出冻结的采样深度；全部落盘运行都未判分的单元是 `all_holes`
  状态，按空洞处理。

### 5.6 执行相位之间不复用

每个执行相位只测量自己的单元，不沿用更早相位的结果：每个 `practice` 都在当时的技能库与
playbook 下重新执行全部训练题，test 相位每次全量重跑。同一相位内的续跑仍按磁盘跳过已完成的
单元（K1 的已存轨迹）。

### 5.7 基础设施空洞

- 执行单元耗尽基础设施重试后不落盘，磁盘上表现为"轨迹缺失"；摘要按应有
  单元集合计数（`infra_missing`）；
- 有未接受的空洞 → 写完摘要后以基础设施错误停下（退出码 3），同一条命令
  重跑只补空洞；
- `--allow-infra-shortfall` 接受空洞：被接受的单元 ID 先写进持久记录再继续，
  之后每次启动都跳过、按空洞计分（开关只决定新出现的空洞接不接受，已接受的无法撤回）。
  摸底相位的记录在 run 级的 `stage1_baseline/accepted_infra_shortfall.json`（两臂共用）；练习与
  test 相位按臂记录在相位目录的 `<臂>/accepted_infra_shortfall.json`。

---

## 六、测试系统

### 6.1 三层测试架构

```
tests/
  unit/          单元测试：每个积木的行为契约，目录与 src/expedistill/ 对应
  composites/    组合测试：多模块协作与崩溃续跑验证
  golden/        回归测试：2 组合端到端矩阵 + 修订窗口探针
  fixtures/      测试数据：benchmark 原始输出录像、提示词渲染样例、golden 矩阵快照
  helpers/       共用工具：假模型 `fake_llm.py`、假插件的唯一安装器 `fake_plugin.py`、
                 golden 的剧本与崩溃工具、TB 远程与技能文件夹的假环境、技能构造器、来源指针
  conftest.py    共用的 pytest fixture
  test_layering.py   分层静态检查
```

全量约 105 个测试文件、约 2050 个用例，约 16 秒；标记 `live` 的用例需要真实服务，默认不跑。
测试文件之间不互相 import，共用代码都在 `tests/helpers/`。

**单元测试**（`tests/unit/`）：每个积木（block、composite、method、stage、prompt）至少
一个测试文件；调模型的用 `tests/helpers/fake_llm.py` 的 `FakeClient`，不发真实请求；测试数据从 `tests/fixtures/` 读取，
不直接读 `runs/`。

**组合测试**（`tests/composites/`）：用假模型 + 临时 run 目录验证多模块协作，必须包含
"中途中断再续跑，结果与一次跑完相同"的用例（学习相位的续跑见 `test_learn_phase_resume.py`）。

**Golden 矩阵**（`tests/golden/`）：

- 两个组合，按 test 相位的交付命名（也是快照文件名 `tests/fixtures/golden/<组合>.json`）：
  `whole_bank`（test 用 `whole_bank` + `real`，playbook 关闭）与 `folder`（test 用 `folder` +
  `real_folder`，显示演化的 playbook）；两个组合的 practice 都用 `folder` + `real_folder` 并显示
  演化的 playbook；
- 都跑论文的七相位序列 `baseline, learn, practice, learn, practice, learn, test`，
  模型侧是 `llm/fake_provider.py` 的确定性假模型，benchmark 是 split 的测试插件；
- 快照 run 目录的全部持久产物（manifest、相位摘要、对象存储、账本、技能库、答卷、
  请求原文），只对时间戳、请求 id 和 run 目录绝对路径做归一化；
- 剧本固定：三个学习相位依次覆盖两张缺口新卡、一次正文改写与一次触发改写、删除规则与一次
  触发改写（`test_golden_matrix.py` 从回执账本核对）；每个组合一次跑完共 35 次模型调用
  （`tests/fixtures/golden/provenance.json` 记录调用数与崩溃点的调用序号）；
- 每个组合在三个崩溃点上做崩溃续跑验证（`tests/helpers/golden_harness.py` 的 `CRASH_POINTS`：`diagnosis` 两次诊断之间、
  `revise` 改写调用前、`new_cards` 两张新卡之间），续跑结果必须与一次跑完的快照相同；
- `--workers 1` 与 `--workers 8` 的快照逐字节相同（`test_eight_workers_write_the_same_snapshot_as_one`，
  `generate.py` 生成时也核对）；
- `tests/helpers/golden_revision_window.py`：在"改写已落盘、下一个 practice 还没执行"处崩溃再续跑。

### 6.2 契约测试原则

断言契约，不捕获字面量：测试重新调用函数比较结果，只有真正需要固定的协议表面
（manifest 顶层键集合、prompt 名称集合）才写字面量。例如 `tests/unit/stages/test_stage0.py`
固定 prompt 名称集合，digest 值由 `prompts.digest(name)` 重新计算：

```python
FROZEN_PROMPT_NAMES = frozenset({
    "SEGMENT", "SUBTASK_TYPING", "TRIAL_FAILURE_ANALYSIS", "CARD_WRITE", ...
})

def _expected_prompt_digests():
    return {name: prompts.digest(name) for name in FROZEN_PROMPT_NAMES}

assert manifest["prompt_digests"] == _expected_prompt_digests()
```

### 6.3 Schema 版本规则

- 改字段含义、删除字段、改类型 / 必填性 / 语义：升 schema 版本（配置的
  `CONFIG_SCHEMA_VERSION`，或对象的 `*_SCHEMA_VERSION`），同步本文件第二、四节；
- 新增带缺省值、且旧代码读取新数据不会出错的可选字段：不升版本；
- 升版本即拒绝旧版本的记录，不写升级函数；需要的配置用生成脚本重新生成，需要的实验在新代码上重跑。

### 6.4 如何为新功能添加测试

1. 单元测试：在 `tests/unit/` 对应目录创建 `test_<模块名>.py`，用假模型构造回复，覆盖
   正常路径、边界情况、错误处理；
2. 组合测试（改了多模块协作时）：在 `tests/composites/` 创建测试，用 `tests/helpers/` 的工具
   构造对象，必须包含崩溃续跑用例；
3. Golden 矩阵（改了提示词、配置字段或相位流程时）：`python tests/golden/generate.py`
   重新生成，检查两个组合的 JSON（`whole_bank.json`、`folder.json`）与 `provenance.json` 的变化，
   再跑 `pytest tests/golden -q`。

### 6.5 运行与验收

```bash
pytest tests -q                       # 全量
pytest tests/unit -q                  # 只跑单元测试
pytest tests/golden -q                # 只跑 golden 矩阵
ruff check src tests scripts --select E9,F
```

提交前：全量测试全绿、`tests/test_layering.py` 通过、ruff 通过、新功能有单元测试、改了多模块
协作有组合测试、改了端到端行为重新生成了 golden 矩阵。

---

## 七、运行规则：命令行、运行设置与退出码

### 命令行

```
expedistill run --config <配置文件> --run-dir <run 目录> [运行设置]
expedistill summary --run-dir <run 目录>
expedistill fork-run --source-run <源 run 目录> --config <新配置> --run-dir <新 run 目录> --through pNN
expedistill import-baseline --source-run <已有 run> --config <新配置> --run-dir <新 run 目录>
expedistill import-bank --source <路径> --source-format markdown_file|skill_folder|notes_jsonl --run-dir <新 run 目录> --origin <方法名>
expedistill judge-skills --run-dir <run 目录> --out-dir <输出目录> --phase pNN:<名> [--arm with_bank] [--bank-phase 标签] [--judge-model 模型]
expedistill paper-tables --spec <JSON> [--out-md 路径] [--out-json 路径] [--n-boot 10000] [--seed 0] [--metric avg_at_n|avg_at_n_conservative|pass_at_n]
expedistill segmentation-metrics --arm LABEL=RUN_DIR [--arm LABEL=RUN_DIR ...] [--out-md 路径] [--out-json 路径]
```

- `run` 每完成一个相位打印一行摘要；同一条命令重跑即续跑；
- `summary` 只读进度账本和用量账本（含逐角色逐 prompt 的 token 统计），完备性清单从
  manifest 冻结的序列推导；账本缺相位时退出码 1；
- `import-baseline`、`import-bank`、`judge-skills`、`paper-tables`、`segmentation-metrics`
  的用途见 3.9。输出目录不能在被读的 run 里。`judge-skills` 缺省用被读 run
  冻结的 judge 模型，并带着该 run 冻结的 token 预算、模型参数和提示词包构造句柄；
  `paper-tables` 与 `segmentation-metrics` 的数值错误退出码为 2；
- `fork-run` 把已完结、空闲的源 run 的前 K 个相位（`--through p05` 是 p01 到 p05）
  连同运行级对象存储拷进新 run 目录，零模型调用。新配置必须以同样的 K 个相位开头、
  定义相同，其余影响这些相位的键也要一致，后面的相位、test 划分和任务清单可以不同。
  run 级共享对象只拷由前缀相位产生的部分：轨迹按编号里的相位标签，段、子任务类型与段归属按
  各自记录的 `phase_label`，技能存储按前缀最后一个学习相位的库工件重建。
  新 run 首次启动时自己冻结 manifest，被拷贝的相位走普通续跑路径，前缀之后的第一个相位
  全新执行。拷贝了什么写在新 run 根目录的 `FORKED_FROM.md`。

### 运行设置（只影响"怎么跑"，不进配置、不进 manifest）

| 参数 | 默认 | 作用 |
|---|---|---|
| `--workers` | 1 | 只调用 LLM 端点的步骤的并行数：诊断、切段、归类、写卡、改写 |
| `--rollout-workers` | 同 `--workers` | 真实 rollout 的并行数 |
| `--llm-infra-retries` | 3 | 单个模型请求遇到基础设施错误时的重发次数 |
| `--llm-infra-backoff` | 5.0 | 重发的指数退避基数（秒），单次等待最多 60 秒 |
| `--llm-timeout` | 300 | 单个请求的客户端超时（秒） |
| `--k1-stagger` | 0 | 每个单元启动前的随机抖动秒数上限（TB 远程建议 20，本地执行用 0） |
| `--allow-infra-shortfall` | 关 | 接受基础设施空洞继续（见 5.7） |
| `--until-phase N` | 无 | 跑完冻结步 + 序列第 0..N 项就停（N 从 0 数），冒烟与试跑用；`-1` 只跑冻结步。停下后用同一个 run 目录、更大的 N 重跑同一条命令即继续 |

运行设置续跑时可以改（例如端点不稳时调大重试次数）；非法值在建客户端前统一校验。
例外：`--allow-infra-shortfall` 接受下来的空洞写进 run 目录，之后每次续跑都生效。

### 退出码

- `0`：正常完成；
- `1`：要人改了东西才能继续——manifest 不一致、run 目录被另一个进程锁着、
  配置错误、服务方拒绝、模型输出错误逃出相位（已完成的单元都已落盘，先查看
  落盘的响应再重跑；supervisor 不得对它自动循环续跑）；`summary` 发现 run
  不完整也是 1；
- `3`：基础设施耗尽、可以直接续跑——相位跑完其余单元后仍有单元耗尽重试，
  已完成的单元都已落盘，CLI 打印带全部运行设置的续跑命令；supervisor 可以
  对 3 做有限次自动续跑，对 1 直接停下；
- `2`：命令行用法错误。

代码 bug 和未归类的异常不捕获，保留完整的报错回溯退出（进程退出码也是 1，supervisor
要看 stderr 区分）。

"本 run 内终态"的意思：一个 run 是一次冻结的实验，同一个 run 目录里不重复尝试；
想换一次采样再试，就开新 run 目录。

### run 跑完后的完整性检查

仓库没有专门的审计脚本。一个 run 的数字进结果表之前，先跑 `expedistill summary`，再逐项
人工核对下面的记录；任何一项不过，run 作废（在 run 目录写 `INVALID.md` 说明原因）：

1. `expedistill summary --run-dir <run>` 退出码为 0：进度账本覆盖冻结序列的每个相位；
2. 每个执行相位的 `phase_summary.json` 里 `infra_missing` 为空；接受过的空洞
   （`accepted_infra_shortfall.json`）逐条列进报告；
3. run 根的 `failed_units.jsonl` 逐条看过：每个学习相位，`findings.jsonl` 的行数加上诊断失败的
   记录数，等于紧邻的摸底或练习相位里弱模型的失败答卷数；
4. 每个学习相位 `evidence.jsonl` 的行数加上本相位 `Unlocated` 记录数，等于 `findings.jsonl` 的行数
   （`scope = general` 的失败定位成功时记为 `none` 类）；每个有缺口证据的类型、每张拿到触发或
   正文证据的卡在 `outcomes.jsonl` 里都有回执；
5. 每个学习相位的 `learn_summary.json` 带 `bank` 键，`bank_summary.json` 的成员与
   `skill_bank.jsonl` 一致；
6. test 相位摘要两臂的题数等于冻结的 test 列表长度。

### 在基准主机上运行（TB 正式实验）

driver 与 Harbor 放在同一台基准主机上：命令走本机 `bash`、文件走本机复制，不经过
ssh/scp。run 目录、审计和分析都留在基准主机上。

- 切换方式：基准主机上的密钥文件里设 `EXPEDISTILL_TB_HOST=local`；
- 部署：`scripts/remote/deploy_bench_host.sh`（rsync，一个提交一个目录，从不覆盖）；
- 启动与续跑：`scripts/remote/start_run_bench_host.sh`，`WORKERS` 和 `ROLLOUT_WORKERS`
  分别对应上表两个并行度；
- 完整步骤、目录约定、环境变量和查看方式见 `scripts/remote/README.md`；
  设计取舍见 `docs/decisions/run_on_benchmark_host_20261029.md`。

### 多个请求 ID 指向同一模型

网关按请求 ID 限流时，同一个模型可以用多个请求 ID 访问。环境变量
`EXPEDISTILL_LLM_MODEL_ALIASES=逻辑名=别名1,别名2;…` 让 client 把发往逻辑名的请求
在这些别名之间轮转；某个别名收到 429 后休息 20 秒再参与。只有线上的 `model` 字段变，
逻辑名仍是 manifest、用量账本和已存轨迹里的模型身份。别名只放环境变量。它只作用于
driver 自己发出的调用；Harbor 在基准主机上的模型请求不经过这个 client。

---

## 八、已知风险

1. **证据稀疏**：ALFWorld 上带 playbook 的弱模型在训练题上大约只失败一成（参考 run 的裸跑
   摸底是 117 次里失败 20 次），每轮只有十几条失败可读，演化慢。本地执行便宜，可以加大
   `practice` 的 `attempts`。
2. **playbook 降低技能打开率**：已有测量，带全局 playbook 时 ALFWorld 学生打开技能的比例从约
   42% 降到 1%–31%，去掉 playbook 一块后第一步打开率从 0% 回到 83%（回放实验）。论文的方法臂
   在 `practice` 与终测都显示 playbook，触发证据会大量堆积，改触发条件可能扭不动；在 `no_skills`
   臂加上之前，技能库在 playbook 之上的贡献无法与 playbook 分开。
3. **打开的卡未必有用**：参考 run 的终测里，打开了卡的 trial 与同题对照成功率相同（0.860 对
   0.861）。正文证据与画像驱动的改写是针对这一点的，效果只在终测里看得出。
4. **出错步由大模型定位**：定位错了，证据会分给错的卡；`findings.jsonl` 与 `evidence.jsonl`
   保存每条判断，可事后复核。
5. **样本内**：在训练集上练习时，学习读的就是写卡的题，泛化只在终测体现；在验证集上练习可以减轻。
6. **最后一次学习的改写不再执行**：直接带进终测，效果只在终测里测得出。
7. **类型表漂移**：归类可能把同一子任务分成两个类型，写出两张相近的卡；没有合并机制，靠归类
   调用每次都看到完整类型表来抑制。
8. **类型在 TB / SearchQA 上很少重复**：TB 的题彼此差异大，SearchQA 的轨迹很短，类型多为一类
   一个实例，卡更像单题教训；能否迁移只看终测。
9. **缺正面材料**：没有同类型强模型成功段的缺口不写卡，只有笔记进画像与 playbook。
10. **复用的摸底与对照的输出上限不同**：ALFWorld 复用的摸底（`exp3_k5_v4_20261103_0957`）输出上限
    65536，对照文件 `alfworld_bare_q_20261102.json` 是 4096，Q 在对照里约 0.2% 的回复因长度被截断。

---

## 九、代码来源（provenance）

底座代码（runtime / benchmarks / persistence / reliability / 执行循环）从
姊妹仓库迁移而来：

- 源仓库：`../ExpeToSkill-merged`（SkillExpand 系统）
- 源分支与提交：`TB-eval @ 4f436af`
- 迁移按"仅带依赖闭包"原则拷贝，详见首次迁移提交的说明
- 迁移对应：B1/B2/B4/B5 ← `runtime` + `persistence`；B3 ← `persistence/store`；
  B6 ← `reliability`；E1 与三个 runner ← `benchmarks` + 执行循环簇；
  D2 ← `evaluation/splits`；其余积木为新写。迁移后的落位以 AGENTS.md 的
  目录结构为准
