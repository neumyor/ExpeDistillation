# ExpeDistillation

强模型轨迹蒸馏成 Skill 库、教弱模型的实验仓库（开发名 SkillPool）。
不训练模型权重，只维护自然语言 Skill；Skill 以规则形式注入弱模型的 prompt，
这是它影响弱模型的唯一通道。

本文件是系统设计的唯一正本：改设计先改这里，再改代码。目录结构、开发规范、
测试要求见 [AGENTS.md](AGENTS.md)。设计取舍的讨论过程在 `docs/decisions/`。

---

## 一、系统是什么：执行—学习闭环与七个模块

（定稿文档：`docs/decisions/execute_learn_module_boundaries_20261016.md`，
本节是它的正文摘要。）

### 1.1 闭环

整个系统是一个闭环：**轨迹 → 技能库 → 把技能库交给模型去做题，得到新轨迹 →
根据新轨迹更新技能库 → ……**（"轨迹"指一次完整执行留下的逐步记录。）

闭环只有两种阶段（"相位"是阶段在配置和磁盘上的正式名字，见第二节）：

- **执行阶段**：技能库 + 任务 + 模型 → 运行记录 + 验收报告；
- **学习阶段**：验收报告 + 运行记录 + 旧技能库 → 新技能库。

一种方法就是这两种阶段排成的一个序列。例如 exam 方法是
`baseline learn exam learn exam learn exam learn test`，live 方法是
`baseline learn live learn live learn test`。相位名就是它的 role：
`learn` 是学习阶段，`baseline`、`exam`、`live`、`test` 是四种执行阶段
（含义见 1.2 的表）。

### 1.2 七个模块

```
执行阶段：技能库 → 交付 → 执行 → 验收 → 验收报告
学习阶段：验收报告 → 决策 → 找证据 → 写作 → 整理 → 新技能库
```

| 模块 | 职责 | 输入 → 输出 |
|---|---|---|
| 交付 | 决定每道题注入哪些技能 | 技能库 + 本阶段的题 → 执行单元清单（每行 = 一道题 + 注入的技能，技能可以为空） |
| 执行 | 适配各 benchmark，把运行结果统一成同一种输出格式 | 执行单元清单 → 运行记录（每次运行一条） |
| 验收 | 对每次运行给出最终判定 | 运行记录（及其指向的轨迹）→ trial 级 AnswerSheet（execution + coverage + grading）；unit/task 级汇总只是共享纯函数的按需结果 |
| 决策 | 决定本轮技能库要做哪些操作 | 本轮验收汇总（UnitSummary）+ 对照汇总 + 当前技能库 + 剩余检验次数 → 任务清单 |
| 找证据 | 收集写作需要的信息，整理成统一格式 | 任务清单 + AnswerSheet/轨迹 → 证据包 |
| 写作 | 按写作规范新建或修改技能 | 任务清单 + 证据包 → 新技能（或修改后的技能，版本号加一） |
| 整理 | 把技能整理成最终形态的技能库 | 本轮全部技能 → 新技能库 |

执行单元 = 一道题 + 给它注入的技能。单元每被执行一次就留下一条运行记录。
阶段之间只传三样东西：**技能库、运行记录、验收报告**；执行单元清单、任务清单、
证据包这些中间产物只在阶段内部流转。

验收的落盘输出是 **trial 级 AnswerSheet**：execution（轨迹/答卷/外部
reward + raw correctness）、coverage（这张 trial 该不该有、有没有、为什么
没有）、grading（success / failure / invalid + basis + rule name）。
`UnitSummary`（有效次数、成功次数、status、cause）不是验收产物，而是
`unit_summary.py` 从 AnswerSheet + UnitSpec 现算的 unit 级视图；决策、
审计、test 都调同一组纯函数。

| 执行阶段 | 题 | 注入的技能 | 谁做、做几次 |
|---|---|---|---|
| baseline 摸底 | 每个训练任务 | 不注入 | 强、弱模型各做若干次 |
| exam 泛化考试 | 每条新建/修改技能版本对应的一道场景题 | 全库技能目录，模型自己选 | 弱模型选技能 + 写计划，LLM judge 判卷 |
| live 实战验收 | 每个有技能的训练任务 | 该任务的整包技能（整块注入或逐技能文件夹） | 弱模型做 n 次 |
| test 最终测试 | 每个测试任务 | 检索出的若干条，或整个技能库，或一个技能文件夹 | 弱模型做 n 次 |

**对照单元**指同一道题、不注入任何技能的单元。它可以现场跑，
也可以借用更早阶段（如 baseline）的结果。

### 1.3 关键分工

1. **判定和比较分开**。验收只给绝对判定：每次运行成功、失败或无效。
   "本轮比对照有没有提升"归决策，比较规则是决策的参数。
2. **执行不判定**。执行只把 benchmark 自带判分器的结果原样传下去，没拿到
   判分结果就留空（`correctness = null`），并附上重试次数、报错原文等运行
   指标。运行算不算正常完成、失败的原因，都由验收判定。
3. **重试分两层**。执行只在没拿到结果时机械重试（runner 抛出异常、进程
   崩溃、连接失败时重跑）。验收把运行判为无效后，由相位调度把单元重新派给
   执行，次数上限是配置里的 `invalid_redispatch_limit`。无效运行既不算成功
   也不算失败。
4. **需要模型来判的东西都归验收**：判卷（G5）、泄题检查（D8），以及没有
   判分器、要靠模型读轨迹打分的场景。
5. **考试也是一种执行**：出题（附参考答案）相当于准备题目，作答时
   "对不对"留空、参考答案作为附带信息传下去，判卷和泄题检查归验收。
6. **报告只记录"注入了哪些技能"**。模型实际按哪条技能做的，要读轨迹才知道；
   live folder 的 `ExecutionRecord.steps` + `artifacts.skill_folder` 是读取
   事实的源；应用与相关判断仍属于找证据的工作。
7. **决策不读轨迹，找证据不定动作，写作不做测量**。三者之间只通过任务清单
   和证据包交接。
8. **实战验收的测量单位是"包"，决策单位是"技能"**。live 相位一个任务的整包
   技能一起上场，组级结论只说明这包有没有提升；P04 的 `review` 策略把组级
   结论（或逐技能 usage 事实）展开成每条技能的 keep / revise / delete，归因
   负责解释，写作只负责改内容。整包是否继续被测由这些逐技能动作共同决定。
9. **测试结果不流进学习**。test 相位的报告只进入最终成绩；"带技能库比裸跑
   提升多少"由 test 相位自己的汇总计算，决策入口收到 test 相位的报告会直接报错。

### 1.4 决策规则

决策拿本轮的单元汇总和对照汇总，按比较规则比较，每个单元得出结论之一：

- **提升**：本轮成绩按比较规则优于对照；
- **没提升**：本轮成绩不优于对照；
- **无提升空间**：对照本来就全对；
- **测不了**：没有有效运行（原因进 `cause` 字段：泄题、注入过长、出题失败、
  无效重派用尽、全部运行未判分等）。

再按结论定动作（任务清单的四种动作：保留 keep / 修改 revise / 删除 delete /
新增 add）：

| 结论 | 后面还有检验 | 后面没有检验 |
|---|---|---|
| 提升 | 保留 | 保留 |
| 没提升 | 修改 | 删除 |
| 无提升空间 | 删除 | 删除 |
| 测不了：泄题 | 修改 | 删除 |
| 测不了：其他原因 | 删除 | 删除 |
| 摸底：弱模型做不好、且还没有技能的任务 | 新增 | 新增 |

"后面还有检验"指序列里这次学习之后是否还有在训练集上做的验收阶段
（exam 或 live）；test 不算。"摸底"那一行读 baseline 相位的报告，按 `task_select` 具名规则
判断哪些任务弱模型做不好。

比较规则是 `blocks/rules.py` 的 `ACCEPTANCE_IMPROVE` 具名注册表的成员，
名字进配置进 manifest，看结果前定死、换规则 = 新实验。当前两条：

- `strict_rate_gain`（缺省）：live 成功率 × 对照有效数 > 对照成功率 × live
  有效数（整数交叉相乘，无浮点），且 live 有效数 > 0。并列判负（进修改），
  变差同样进修改（技能可能有害，归因者应看到）；对照全对时数学上不可能
  更好，触发对照全对守卫（配套规则 3）；
- `first_success_gain`（消融备选，pass@N 语义）：基线全败 → live 至少一次
  成功。

三条配套规则：

1. **没变的单元不重测**。题和注入的技能都没变的单元，沿用上一次的验收结果
   （实现见 5.8）。修改后内容和原来一字不差也算没变。test 相位例外，每次全量
   重跑。
2. **改写失败就原样保留**。修改没有产出新版本就视为没变：按上一条不重测，
   下次学习再试着改；到最后一次学习仍没有提升就按表删除。
3. **对照全对时可以跳过带技能的运行**。比较规则在 strict 家族时，对照已经
   全对的单元不可能得出"提升"，执行侧直接跳过带技能的那次，报告中标注
   `not_run_control_perfect`，决策据此得出"无提升空间"。这只省调用次数，
   不改变任何结论。

---

## 二、方法 = 相位序列（配置 schema v7）

一种方法写成配置里的两块内容：`sequence`（相位名单，可重复）和
`phases`（每个名字一份相位定义）。旧版本（v1/v2/v3/v4）的配置是冻结的历史
记录：v5/v4 由加载器在内存里依次升级，v3 及更早的加载器直接拒收并指向
转换工具（见 2.5）。

### 2.1 配置字段

一份实验配置（`configs/experiments/<实验名>.yaml`）的顶层字段：

| 字段 | 说明 |
|---|---|
| `benchmark` | benchmark 名字（tb / searchqa / alfworld） |
| `mode` | `split`（划分 train/test）或 `full`（全部题目一起蒸馏） |
| `models` | 模型角色表：角色 → 模型名。必须给出 `executor_strong` 与 `executor_weak` 两个锚点，其余角色按 `ROLE_DEFAULTS` 补齐（answerer 用弱模型，其余用强模型） |
| `weak_profile`（或 `weak_profile_file`） | 弱模型画像：一段描述弱模型是谁、已知弱点的文本。画像文本保留在配置里，作为来源记录和 opt-in 素材；两者二选一：`weak_profile_file` 是画像文本文件的路径，相对路径相对配置文件所在目录解析 |
| `use_weak_profile` | 是否把弱模型画像送进写作/改写提示。默认 `false`：新实验默认关闭画像，提示词收到的是“没有画像，按一般能力终端 agent 写”的中性段落。只有显式写 `true` 才启用画像 |
| `n_attempts_strong` / `n_attempts_weak` | 摸底相位两臂各自的采样次数 |
| `split` | `split` 模式的划分：显式 train/test/reserved 列表，或 `{ratio, seed}` |
| `tasks` | 任务集：不写（插件全集）、任务 ID 列表、或任务文件路径 |
| `sequence` | 相位名单（局部名的列表，可重复） |
| `phases` | 局部名 → 相位定义 |
| `invalid_redispatch_limit` | 一个被判无效的单元最多重新派发几次（缺省 1） |
| `rules` | 具名规则的选择（`task_select` / `segment_select` / `trajectory_select`），缺项由 `DEFAULT_RULES` 补齐 |
| `model_options` | 按模型名给出的额外调用参数（如 `reasoning_effort`）；不得携带 `max_tokens`（token 上限只归 `token_budgets` 管） |
| `token_budgets` | 每个角色的 token 上限（`default_max_output_tokens` 必填，`roles.<role>` 可逐角色覆盖） |
| `schema_version` | 当前为 7；v6/v5/v4 文件由 `load_config` 在内存里升级（v6→v7 把 exam 的 `per_skill` 改成 `exam_folder` 并补执行参数），v3/v2 文件先用转换脚本转成新文件 |

**执行相位的字段**（`kind: execute`）：

| 字段 | 说明 |
|---|---|
| `role` | 相位角色，显式声明，加载期核对与字段一致。执行相位四选一：`baseline` / `exam` / `live` / `test`；学习相位恒为 `learn` |
| `tasks` | 跑冻结的哪个列表：`train` 或 `test`（`test` 角色与 `test` 列表是同一件事，两者必须同时成立） |
| `delivery` | 交付方法（一个带 `name` 的映射）：`none` / `whole_bank` / `retrieval` / `group_by_task` / `folder_by_task` / `folder` / `exam_folder`（live 的逐任务文件夹交付，只用 `real` 执行，流程见 5.4）/ `folder`（test 的技能文件夹交付，与 `real_folder` 执行一对一配对，流程见 5.5）；说明见 `src/expedistill/methods/delivery/README.md` |
| `execution` | 执行方法：`real`（真实 benchmark 执行）/ `real_folder`（真实执行，技能以文件夹送进执行环境，与 `folder` 交付一对一配对）/ `exam`（泛化考试：选技能 + 写计划，不跑真实环境）；`real` 带参数（`arms`、`n_attempts`、`max_injection_tokens`），`real_folder` 没有参数，`exam` 带 `max_skill_reads` / `bare_control` / `leak_audit`；说明见 `src/expedistill/methods/execution/README.md` |
| `compare` | 对照来源（可选）：`against` 指向更早的相位名、`fresh_bare`（相位内现场跑一条裸臂）或 `external_campaign`（对照读外部 campaign 的冻结成绩文件，零执行，只有 test 角色相位可用，且必须带 `external` 块钉住文件身份：`path`（相对路径相对配置文件所在目录解析，冻结为绝对路径）+ `sha256`（成绩文件字节的校验和，防漂移）+ `model_key`（campaign 汇总里对应本 run 弱模型的前缀））；对照口径 = 每任务取前 `n_attempts_weak` 次采样的奖励均值（与旧外部对照报告的"前 3 次"对齐列同口径），`rule` 指定比较规则名 |
| `acceptance` | 验收判分规则名（可选；缺省用 benchmark 的默认绑定，TB 是 `tb_scorer`） |

**学习相位的字段**（`kind: learn`）：

| 字段 | 说明 |
|---|---|
| `role` | 恒为 `learn` |
| `review` | 审查方法：把决策和找证据合成一个入口。参数 `decision: task_level|skill_level|trial_level`（P04/P06 的策略，默认 `task_level`；`trial_level` 逐 trial 分析）、`unread_in_won_group: delete|keep`（未读但组赢时的策略）、`before_steps` / `after_steps`（归因窗口，默认 10 / 10）、`K` / `retry_limit` / `write_lessons`（P02 add 的切段预算）；说明见 `src/expedistill/methods/review/README.md` |
| `author` | 写作方法：`distill_diagnose`（默认，无参数，逐 task 逐段写）或 `cluster_by_trigger`（跨 add-authorized task 的 subtask：先分五个 trigger 族，再族内聚类，最后每类写一条 skill；参数 `max_cards_per_prompt` / `max_chars_per_card` / `min_cluster_size` / `classify_retry_limit` / `cluster_retry_limit` / `skill_retry_limit`）；说明见 `src/expedistill/methods/author/README.md` |
| `curate` | 整理方法：`agentbrew`（带 `merge_check` / `token_budget` / `rounds` / `drop_low_value` / `similarity_input` / `grouping_input`）或 `none`（带 `token_budget`）；`merge_check` 默认 `none`（2026-10-19 起，原为 `exam`），`drop_low_value` 默认 `false`（开启后 G7 标出的低价值技能在合并前丢弃，原因 `low_value`），`similarity_input` 默认 `full`（`summary` 时 G7 只看编号 + 摘要，缺摘要直接报错），`grouping_input` 默认 `full`（`summary` 时 G9 只看编号 + 摘要，缺摘要直接报错；与 `similarity_input` 独立），见 `docs/decisions/curation_generalize_20261019.md` 与 `docs/decisions/curation_grouping_input_20261020.md`；参数说明见 `src/expedistill/methods/curation/README.md`；`token_budget` 是防注入块超上下文的安全阀，取正整数，或 `null` = 无护栏（2026-10-16 用户裁决：token 上限退出预登记参数集，v4 世界的正式拼法） |

### 2.2 相位标签与相位目录

每个相位有一个标签 `pNN:<局部名>`：冻结步恒为 `p00:freeze`；序列第 i 项
（i 从 0 数）的标签是 `p{i+1:02d}:<局部名>`，例如第一个序列相位是
`p01:baseline`。标签出现在进度账本（`pipeline_log.jsonl` 的 `stage` 值）、
`failed_units.jsonl` 和 AnswerSheet 的 `phase_label` 里；局部名不得带冒号
（标签按冒号解析）。

每个相位在 run 目录里有自己的目录 `phases/pNN_<局部名>/`（标签里的冒号
换成下划线），存放该相位的账本与产物（完整布局见 4.3）。

### 2.3 加载期检查清单

配置加载时（`core/config.py` 的 `parse_phases`）逐条核对，任何一条不过都
报配置错误，跑都不会开始：

1. `sequence` 是非空的名字列表，字符集限字母、数字、`.`、`_`、`-`（禁
   冒号）；每个名字在 `phases` 里有定义；`phases` 里没有被序列用到的定义
   也拒绝（它会冻结没有任何相位使用的方法身份）；
2. 每个定义的字段集合与它的 `kind` 匹配（多出的键按拼错处理，拒绝）；
3. `role` 是五个词之一，且与字段一致：`test` 角色必须跑 `test` 列表且只有
   它跑；`exam` 角色必须用 `exam` 执行；`baseline` 的执行必须恰好是
   `arms: [executor_strong, executor_weak]` 两臂（深度由全局
   `n_attempts_strong` / `n_attempts_weak` 决定）；单臂真实执行的
   `n_attempts` 不得超过对照的弱臂深度；
4. `compare.against` 必须指向序列里更早出现的名字，或写 `fresh_bare`，
   或写 `external_campaign`（只允许 test 角色相位，且必须带 `external`
   块；stage 0 会校验成绩文件存在且 sha256 匹配，test 相位派发前再校验任务
   覆盖与奖励格式）；前两种情况下被指向的对照必须是 baseline 相位
   （只有摸底相位的弱臂裸跑报告才是裸测量）；
5. `curate.merge_check: exam` 要求序列里存在 exam 相位（合并的回归考题
   由 exam 相位生成）；
6. 序列必须以执行相位开头（学习相位没有可学的测量）；
7. live 相位必须带 `compare` 指向更早的 baseline 相位（它自己的报告行
   全部带注入，没有相位内的对照行）；
8. `folder` 交付只出现在 test 角色的执行相位，且与 `real_folder` 执行一对一配对：
   `folder` 配 plain `real`（会把目录文本当技能块注入，做错实验）或
   `real_folder` 配其他交付（会等一个没人准备的文件夹）都是配置错误。
   `folder_by_task` 交付只出现在 live 角色，使用 plain `real` 执行；live 的采样
   深度和注入 token 预算来自 `real` 的参数。

另外：stage 0（冻结步）在写任何文件之前，把每个相位的每个方法族
（delivery / execution / review / author / curation）都解析一遍并校验
参数（`methods/registry.py` 的 `resolve_params`），配置里的方法名拼错、
参数类型错都在这里停下。

### 2.4 两个例子

exam 方法（v3 配置 `acceptance: {name: exam, retry_limit: R}` 转换而来）：

```
sequence: [baseline, learn, exam, learn, exam, learn, exam, learn, test]  # retry_limit = 2 时
phases:
  baseline: {kind: execute, role: baseline, tasks: train,
       delivery: {name: none},
       execution: {name: real, arms: [executor_strong, executor_weak]}}
  exam: {kind: execute, role: exam,
       delivery: {name: exam_folder, description_mode: summary_then_when_to_use},
       execution: {name: exam, max_skill_reads: 3,
                   bare_control: false, leak_audit: false}}
  learn: {kind: learn, review: {name: review, K: 5, retry_limit: 2},
       author: {name: distill_diagnose},
       curate: {name: agentbrew, merge_check: exam, token_budget: ..., rounds: 3}}
  test: {kind: execute, role: test, tasks: test,
       delivery: <原配置的 delivery>, execution: {name: real},
       compare: {against: fresh_bare}}        # full 模式改为 against: baseline
```

live 方法（v3 配置 `acceptance: {name: live_run, ...}` 转换而来）：

```
sequence: [baseline, learn, live, learn, live, learn, test]
phases:
  live: {kind: execute, role: live, tasks: train,
       delivery: {name: group_by_task},
       execution: {name: real, n_attempts: 3, max_injection_tokens: null},
       compare: {against: baseline, rule: strict_rate_gain}}
  learn: {kind: learn, ..., curate: {name: agentbrew, merge_check: none, ...}}
```

live 的渐进式披露与 usage 感知修订形态：

```
sequence: [baseline, learn, live, learn, live, learn, test]
phases:
  live: {kind: execute, role: live, tasks: train,
       delivery: {name: folder_by_task, description_mode: when_to_use},
       execution: {name: real, n_attempts: 3, max_injection_tokens: null},
       compare: {against: baseline, rule: strict_rate_gain}}
  learn: {kind: learn,
       review: {name: review, decision: task_level,
                unread_in_won_group: delete,
                before_steps: 10, after_steps: 10,
                K: 5, retry_limit: 2, write_lessons: true},
       author: {name: distill_diagnose},
       curate: {name: agentbrew, ...}}
```

`review.decision` 不写时默认 `task_level`；`before_steps` / `after_steps`
不写时默认 10/10。P02 的 add 路径只使用 `K` / `retry_limit` /
`write_lessons`，P04/P06 只使用决策和归因字段。

P02 也可以换成跨 task 的 trigger-族聚类写作（只影响 `add` 的写作落地，
P04/P06 仍走 `revise.py`）：

```yaml
author:
  name: cluster_by_trigger
  max_cards_per_prompt: 40
  max_chars_per_card: 1200
  min_cluster_size: 1
  classify_retry_limit: 2
  cluster_retry_limit: 2
  skill_retry_limit: 2
```

它仍只处理 `add` 行授权的 task，以及 `segment_select` 保留的段；新增
`phases/pNN_learn/trigger_families.jsonl`、`clusters.jsonl`、
`cluster_skills.jsonl`、`cluster_outcomes.jsonl`、`cluster_summary.json`
五个工件。聚类结果以 JSON 为源，`clusters/<family>.md` 只是人读视图。

test 相位的技能文件夹形态（folder 交付 + real_folder 执行，完整可跑的配置见
`configs/experiments/tb21_split_folder_v4.yaml`）：

```
sequence: [baseline, learn, live, learn, live, learn, test]
phases:
  test: {kind: execute, role: test, tasks: test,
       delivery: {name: folder}, execution: {name: real_folder},
       compare: {against: fresh_bare}}
```

（相位名是转换工具发出的缺省名，与 role 同名，只是名字；配置作者可以
自由改名，代码只认 `role`。2026-10-19 之前的缺省名是字母 D / L / A / B / C，
旧 run 的 manifest 与相位目录里仍是字母。仓库里 9 份 v4 配置
`configs/experiments/*_v4.yaml` 覆盖这两类形状与 folder 形态。）

### 2.5 旧版本配置的转换

v4 及更早的配置文件是冻结的实验记录，不修改：v4 由加载器在内存里升级
（`upgrade_v4_config`，`use_weak_profile` 默认 false），v3 及更早用转换工具
写新文件：

```
scripts/run/convert_v3_configs.py   # v3 → v5（写出 <原名>_v5.yaml；
                                    # 显式传入 v2 文件时在内存里链式
                                    # 走 v2→v3→v4→v5→v6）
```

v2 → v3 的一次性转换在 commit `78db71f` 完成，当时的转换脚本
`convert_v2_configs.py` 在当前 v5 代码下已无法转换任何文件，现已删除，需要时从该
提交取回。

转换映射是 `core/config_upgrade.py` 的纯函数 `upgrade_v2_config` /
`upgrade_v3_config` / `upgrade_v4_config`（装配器读旧 run 的 manifest 时走
同一组函数）。v3 → v4 的身份变化：
acceptance 槽位溶进序列（exam 的 `retry_limit = R` 变成 R+1 个 exam 相位；
live_run 的 `n_attempts` / `improvement` / `max_injection_tokens` 移进
live 相位）；live 方法的整理 `merge_check` 写 `none`（live 序列没有 exam 相位，
合并由轮 1 的整组重测证明）；authoring 的 `exams` 开关删除（true 由每条
发出的序列蕴含；false 只在 live_run + curation none 这一对合法消融下可转，
其余拒绝）；delivery 原样移进 test 相位；segmentation 的 `K` / `retry_limit`
移进 learn 相位的 evidence。转换结果带上 v4 新全局 `invalid_redispatch_limit`
的缺省值 1，使转换后的身份不依赖缺省值。

v4 → v5 的身份变化：新增全局 `use_weak_profile`，升级时取 `false`——画像
文本原样保留，但写作、改写、live 归因与 live 修订的提示词收到中性段落；
要复现 v4 的画像行为必须显式写 `true`。

v5 → v6 的身份变化：学习相位的 `evidence` / `attribution` / `decision`
三个槽位合并为 `review`。`K` / `retry_limit` / `write_lessons` 来自原
`evidence`；`before_steps` / `after_steps` 来自原 `attribution`；
`decision` / `unread_in_won_group` 来自原 `decision`。未写出的字段取原有默认值。

### 2.6 方法组合的计数

一次实验必须在四根主轴上各选一项：benchmark（tb / searchqa / alfworld）、
验收方法（exam / live）、模式（split / full）、test 相位交付
（whole_bank / retrieval / folder），主轴组合 3 × 2 × 2 × 3 = **36 种**。
学习相位还有两项选择：整理方法（agentbrew / none）与比较规则
（strict_rate_gain / first_success_gain），各 2 种，配置形态上限
36 × 2 × 2 = **144 种**。test 相位对照来源还有第三种：`external_campaign`
（对照读外部 campaign 冻结成绩文件，零执行，见 5.5；另两种是相位复用
与 `fresh_bare`），只影响对照臂的取数方式，不新增方法族。folder
交付目前只有 tb 的插件声明支持（插件
能力位 `skill_folder_prompt_name`，见 5.5 与 3.6），searchqa / alfworld
配 folder 的配置能通过加载，test 相位派发前被相位级能力检查拒绝——与
技能库空不空无关，逐单元的 E1 守卫保留作第二道。

---

## 三、积木清单

整个系统由一组积木拼成。积木分三层：**基础件**（所有积木共用的底座）、
**功能积木**（做一件具体的事）、**组合积木**（把几块功能积木串成一个常用
动作）。相位流程只是把积木按顺序接起来，再加上逐单元落盘和断点续跑。

### 3.1 积木规则

1. **积木只认对象，不认相位**：功能积木的输入输出都是数据模型里的对象
   （或对象列表、或纯文本），它不知道自己在第几相位，也不管文件存在哪里。
   落盘和断点续跑由组合积木和相位流程负责。
2. **模型是参数**：凡是调用模型的积木，用哪个模型由调用方传入（从配置的
   角色表里取），积木内部不写死"强模型"或"弱模型"。
3. **跟 benchmark 有关的东西打包成一个 benchmark 插件**：执行 runner、
   任务加载、轨迹视图的截断规则、执行任务 prompt 四样，放在同一个插件目录
   里，通过插件注册表按名字取用。其余积木只处理统一格式的对象，对
   benchmark 无感知。加一个 benchmark = 加一个插件目录。
4. **所有模型调用走同一个基础件**（B1 调用模型），统一处理输出格式错误的
   重试、用量记账和请求原文落盘。唯一例外是 E1 里执行 agent 的模型调用：
   本地 runner 用 B1 的底层客户端逐步调用；TB runner 的模型调用发生在远程
   Harbor 里，用量从 Harbor 结果中读回再记账。
5. **每块积木可以单独测试**：模型可以换成返回固定内容的假模型，程序类积木
   直接用构造好的对象测。
6. **ID 由程序按确定规则生成**：子对象 ID = 父对象 ID + 序号（如
   `subtask_id = <trajectory_id>/seg<i>`），同样的输入重跑得到同样的 ID，
   断点续跑时据此判断"这个单元做过没有"。模型输出里的任何编号都不能当
   ID 用。
7. **可替换的规则做成具名规则**：挑题、段筛选、轨迹挑选、判分、比较规则
   都在注册表里按名字登记，配置里写名字，名字进 manifest 冻结。改规则 =
   新增一条具名规则，不改已有规则的行为。

### 3.2 基础件

| 编号 | 名称 | 做什么 | 文件 |
|---|---|---|---|
| B1 | 调用模型 | 输入（模型, prompt 名, 变量, 输出格式）→ 解析好的结果。格式不对时按规则重问；基础设施异常重发同一请求；每次请求和用量落盘 | `llm/call.py`、`llm/client.py`、`llm/providers.py` |
| B2 | Prompt 注册表 | 所有 prompt 的具名常量；计算 digest 供 manifest 冻结 | `prompts/` |
| B3 | 对象存储 | 五个对象的保存 / 读取 / 列举；判断某个单元是否已完成（断点续跑用）；JSONL 账本的加锁追加与断尾修复 | `infra/store.py` |
| B4 | Manifest 冻结 | 写一次、之后逐字段比对，不一致即拒绝继续 | `infra/manifest.py` |
| B5 | 并行池 | 与业务无关的线程池 | `infra/parallel.py` |
| B6 | 可靠性 | 异常分类（基础设施 / 模型输出 / 配置 / 代码 bug）、重试策略、单元失败记录 | `infra/reliability.py` |
| B7 | 运行锁与用量累计 | run 目录排他锁（防止两个进程写同一个 run）；用量跨进程累加落盘，续跑后不清零 | `infra/runlock.py`、`infra/usage.py` |
| — | 接口形状 | 交付与决策的冻结交接形状（执行单元行及其列表别名、任务清单行），见 `core/contracts.py` 的 docstring；执行→验收的 trial 级记录是 `core/answer_sheet.py` 的 `ExecutionRecord` | `core/contracts.py`、`core/answer_sheet.py` |

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
  `default_max_output_tokens`），没有配置 `token_budgets` 是硬配置错误
  （不回退到任何内置缺省）。按模型覆盖的采样参数走 `model_options`；
- 模型返回空内容或全空白回复是 **ModelOutputError**（消息里带
  `finish_reason`，分得清"预算烧在推理上"（length）和"模型选择沉默"
  （stop）），不重发。本地 ReAct 一局里发生时，这一局以
  `correctness = null`、`meta.termination = "model_output_error"` 收尾
  （已走步骤保留），不重试——重试等于对被测模型反复重采样直到它答好，
  判定交验收；
- 请求原文落盘记 `finish_reason`，用量账本每行记 `finish_reason` 与
  `cached_tokens`（网关侧的缓存模板会把 prompt token 虚高数百倍；按角色
  比成本时看 `cached_tokens`）。

**B1 的采样温度**：

- 所有模型调用固定使用 `temperature = 1.0`；配置文件不能覆盖
  （`model_options` 里写 `temperature` 会被保留键检查拒绝）；
- 思考型模型（会先输出一大段推理再给答案）在 `temperature = 0` 下容易
  陷入复读：反复输出同一段推理、迟迟不收敛到答案，甚至把输出预算耗尽后
  返回空内容。固定 `1.0` 可以避开这一退化，代价是同一提示的回复不再逐字
  确定；
- 温度是解码设置，不属于实验身份，不进入配置与 manifest；它不改变 prompt
  digest，也不影响续跑校验。

### 3.3 功能积木

**执行类**

| 编号 | 名称 | 输入 → 输出 | 文件 |
|---|---|---|---|
| E1 | 执行 | （模型, 一道任务, skill 列表（可选））→ Trajectory（含判分器结果 `correctness`） | `blocks/execute.py` + benchmark 插件 |

**执行只记录事实**：E1 把 benchmark 自带判分器的结果原样写进
`correctness`（`true` = 对、`false` = 错、`null` = 没拿到判分结果——没有
判分器的 benchmark、判分器没跑、或运行没走到可判分的状态），附上运行指标
（重试次数、报错原文、耗时），不做任何判定。基础设施失败（沙箱超时、断网、
进程死亡）不进结果词汇：插件抛出 `InfrastructureError`，K1 按 B6 的策略
重试这类单元；重试耗尽的单元不落盘（"已存 = 已完成"）。统一结果对象
`RunOutcome` 三字段：`steps`（逐步原文）、`correctness`、`meta`（插件自由
数据）。

E1 内部的 runner：

| Runner | Benchmark | 执行方式 |
|---|---|---|
| TB runner | TerminalBench | 委托 Harbor + 腾讯沙箱执行一次 rollout，结束后从 Harbor trial 目录还原逐步轨迹。driver 与 Harbor 同机（`EXPEDISTILL_TB_HOST=local`）时命令走本机 `bash`、文件走本机复制；driver 在别的机器时经 ssh/scp |
| SearchQA runner | SearchQA | 本地一步一步执行到结束（外层 `benchmark/searchqa-eval/`） |
| ALFWorld runner | ALFWorld | 本地一步一步执行到结束（外层 `benchmark/alfworld-eval/`，须用其 venv 并加载 `ALFWORLD_DATA` / `ALFWORLD_CONFIG` / `ALFWORLD_BENCH_SRC`） |

两套本地 runner 的每局步数上限用环境变量配：SearchQA 是
`EXPEDISTILL_SEARCHQA_MAX_STEPS`（默认 6），ALFWorld 是
`EXPEDISTILL_ALFWORLD_MAX_STEPS`（默认 50）；设了但不是 ≥1 的整数即配置错误。

**TB runner 的远程 run id 与续跑**（`docs/decisions/tb_run_namespace_20261011.md`）：

- 远程 run id = `ed_<task>_<命名空间>_<attempt token>`。命名空间由 K1 计算：
  run 目录的真实路径、角色、模型名、注入的 skill 引用、渲染后 skill 文本块的
  摘要，合起来取 sha256 前 10 位。有了命名空间，不同臂、不同相位的执行不会
  撞 id。run 目录的真实路径是命名空间的一部分，所以把 run 搬到另一台机器
  （路径变了）不能续跑，要在新位置新建 run；
- **同一个 run id 只提交一次**。提交时记两份启动条件：本地
  `<单元工作目录>/tb_remote/<run_id>.json`（另记 runner pid）和远程
  `jobs/<run_id>.ed_meta.json`。续跑时只有两份记录都在、且都与本次请求一致
  才复用：有 `result.json` 就直接取结果，没有结果但 runner 还活着就接着等；
  其余情况抛 `InfrastructureError`，换下一个 token 重跑；
- **核对进程身份**：判断 runner 还活着、判定它已退出、超时终止它，都先核对
  `/proc/<pid>/environ` 里有 `RUN_ID=<run_id>`。核对不了就当作"不是我们
  的"：宁可换 token 重跑。超时终止只杀记录下的那个进程组，从不用
  `pkill` / `pgrep -f`。

**数据整理类**（纯程序，不调模型）

| 编号 | 名称 | 输入 → 输出 | 文件 |
|---|---|---|---|
| D1 | 任务加载 | （benchmark, 任务表）→ 任务列表 | `blocks/tasks.py` |
| D2 | 划分 | （任务列表, 模式, 种子）→ train / test / reserved | `blocks/tasks.py` |
| D3 | 成绩统计 | （Trajectory 列表, 应有单元集合, 辅助打分器）→ 每题有效次数 / 成功次数 / 基础设施异常次数，两种口径的 avg@N、pass@N（声明辅助指标时另有同分母的辅助 avg@N） | `blocks/metrics.py` |
| D4 | 挑题 | （每题成绩, 具名规则）→ 题目集合（决策模块用） | `blocks/rules.py` |
| D5 | 轨迹视图 | （Trajectory 或段内步骤, 视图配置）→ 给模型看的文本；超长观察按规则截断，截断处明确标注 | `blocks/view.py` |
| D6 | 段筛选 | （Subtask 列表, 具名规则）→ 要生成 skill 的 Subtask（add 路径的材料筛选） | `blocks/rules.py` |
| D7 | 切段校验 | （Trajectory, Subtask 列表, K）→ 通过 / 错误清单 | `blocks/segment.py` |
| D8 | 泄题审计 | （Skill, Exam, auditor 模型）→ 泄露 / 不泄露 + 理由（判官理由永不复述参考答案） | `blocks/audit.py` |
| D9 | skill 注入 | （skill 列表, 分组信息）→ 插入 prompt 的规则文本块 | `inject/skills.py` |
| D10 | 库长度统计 | skill 列表 → 注入后的 token 数 | `inject/skills.py` |
| D11 | skill 检索 | （任务文本, skill 库, top_k / max_categories / min_score）→ 选中 skill 子集 + 审计记录。词法两步检索：粗类别路由 + 类内 TF-IDF 余弦取 top-k | `blocks/retrieve.py` |

**模型类**（每块对应注册表里的 prompt，模型由调用方传入）

| 编号 | 名称 | 输入 → 输出 |
|---|---|---|
| G1 | 切段 | （轨迹视图, K, 模型）→ 段列表（未校验）。模型只给每段的起止步号和 label / description / success |
| G2 | 写 skill | （Subtask, 写法 = 提炼 / 诊断, 弱模型画像, 段位置, 模型）→ Skill v1。两种写法产出同一种三节结构 `[When to use]`/`[Steps]`/`[Do not use when]`（2026-10-19 起统一，技能是做法还是教训看来源段的 `Subtask.source`）；前提守卫：提炼要求段成功、诊断要求段失败。2026-10-23 起：`[When to use]` 以 5 种触发时机之一的固定短语开头（`At task start,` / `After the first environment check,` / `When a command fails, hangs, or repeats,` / `Before submitting,` / `At any step,`），summary 只写“教什么”、`[When to use]` 只写“什么时候打开”，两者合计最好 ≤400、最长 ≤1,000 字符；段位置（第 i/n 段、原轨迹步号范围）由 add 路径证据采集器算出，作为选时机的提示（`docs/decisions/p02_trigger_timing_and_length_20261023.md`） |
| G3 | 复原考场 | （来源轨迹前缀、任务说明、技能正文、修改原因, examiner 模型）→ `scene_text` / `expected_behavior` / `reference_answer`。场景里不出现技能名 |
| G4 | 选技能与写计划 | （场景、候选目录、已打开技能正文, answerer 模型）→ 打开了哪些技能、理由、计划 |
| G5 | 判卷 | （场景、期望行为、参考答案、候选目录、程序记录的已打开技能、理由、计划, judge 模型）→ success / failure + 自然语言理由 |
| G6 | 改写 skill | （上一版 Skill, 考题, 错误作答, 错误描述, 弱模型画像, 模型）→ 新版本 + 错因归类。不接收参考答案 |
| G7 | 找相似、冲突与低价值 | （skill 列表, 模型, 输入方式 全文/摘要）→ 候选合并组、冲突对、低价值技能列表 |
| G8 | 合并 skill | （一组 skill 或一对冲突 skill, 模型）→ 合并后的 Skill 正文 + 一句话摘要（JSON；泛化优先，冲突先析因） |
| G9 | 分组 | （skill 列表, 模型）→ 每条 skill 的能力分组标签 |
| G11 | live 修订 | （skill 现文本, 本次 skill 的所有 attempt 窗口证据, 弱模型画像, rewriter 模型）→ 保节结构的新正文 + 修订理由 + 新摘要。多个窗口冲突时由写作模型自己综合 |
| G12 | attempt 归因 | （任务 instruction, 本次 attempt 的 success/failure, 一段轨迹窗口, 窗口内所有目标 skill 的内容, 弱模型画像, author 模型）→ 每条 skill 的触发上下文 / 触发后行为 / 可改进点，不带动作；同一窗口的多条 skill 合并一次调用 |

"弱模型画像"是配置里的一段文字，换弱模型时只换这段配置。`use_weak_profile` 默认关闭：画像文本留在配置里，但写作、改写、live 归因和 live 修订的提示词收到的是中性段落；只有显式打开时才把画像正文送进提示词。

### 3.4 组合积木

| 编号 | 名称 | 做什么 | 文件 |
|---|---|---|---|
| K1 | 批量执行 | E1 × 题目 × 采样次数，经 B5 并行；每完成一次执行就落盘一条 Trajectory，可续跑；`InfrastructureError` 按 B6 重试，耗尽的单元记失败账本 | `composites/batch_execute.py` |
| K2 | 切段（带校验） | D5 → G1 → D7；不通过就把错误清单交给 G1 重切，次数上限 `retry_limit`；耗尽则记审计日志、本 run 内终态 | `composites/segment_checked.py` |
| K3 | 考一次 | EXAM_PLAN → EXAM_JUDGE → 记一条 Attempt；先存作答、再判卷；判卷预算跨崩溃持续 | `composites/exam_once.py`、`exam_answering.py`、`exam_grading.py` |
| K4 | 泄题判定 | D8 的判定与 leak_verdicts 账本：每个 (skill, 版本) 恰一行、首判为准，续跑不重问 | `composites/acceptance_leak.py` |
| K5 | 库整理 | G7 →（可选）丢弃低价值 → G8 →（可选）K3 回归考试 → G9 → D10，合并与丢弃日志先写后改、续跑按日志重放 | `composites/curation.py` |
| K6 | live 判定账本 | 轮判定（judge_round，append-once、首判为准、续跑重算逐字节核对）、组终态提交（finalize_group）、基线与轮仓读取、注入审计行 | `composites/live_verdicts.py`、`live_measurement.py` |
| — | trial 级 AnswerSheet | `ExecutionRecord`（轨迹/答卷/外部 reward + raw facts）、`CoverageRecord`（present/missing/terminal + cause）、`GradingRecord`（verdict/basis/rule）组成的 trial 级 envelope；JSON 是源，Markdown 是派生视图 | `core/answer_sheet.py`、`composites/answer_sheets.py`、`blocks/view.py::render_answer_sheet` |
| — | 验收判定核 | 只对一张 AnswerSheet 判 success / failure / invalid；具名判分规则注册表（`scorer`、`exam_judge`、`tb_scorer`、`external_reward`） | `composites/verdicts.py` |
| — | coverage | expected trial vs stored trial：present / missing / terminal + cause（accepted hole、leak、injection overflow、exam generation failed、control perfect、zero execution）；不判分、不聚合 | `composites/coverage.py` |
| — | unit 汇总 | `summarize_unit` / `summarize_phase` / `task_metrics`：由 AnswerSheet + UnitSpec 纯函数算出 UnitSummary；决策/审计/test 共用 | `composites/unit_summary.py` |
| — | 相位报告 envelope | 读取 `unit_specs.jsonl` + `answer_sheets/` + `coverage.jsonl`，现算 UnitSummary；不是持久 report card | `composites/phase_report.py` |
| — | unit/sheet 身份 | `make_unit_id` / `canonical_skills` / gap sheet id | `composites/unit_ids.py` |
| — | 审查 | `methods/review`：`decide_task_list`（P02 baseline-gap / exam）、`strategies`（P04/P06 的 `task_level` / `skill_level` 与 exam 多版本合并）、归因窗口、P02 切段与 add 证据、exam 证据 | `methods/review/`；动作表、cause 词表、`final_test_delta` 仍在 `composites/decision.py` |
| — | 相位库 | 相位目录布局、相位技能库工件的读取侧 | `composites/phase_bank.py` |
| — | 技能文件夹物化 | `materialize_skill_folder(清单, bank, 目录)`：按清单的库指纹核对相位库、从库里读每条技能的正文，把文件夹（每技能一个 `<skill>/SKILL.md`，带 YAML 头，另加 catalog.md 兜底）幂等写进目标目录（内容不变不重写，永不删除别的文件）；test 相位派发前物化一次，全部单元共享 | `composites/skill_folder.py` |
| — | 技能状态提交 | `discard_skill`（丢弃的唯一入口）与候选扫描 | `composites/skill_status.py` |
| — | 空洞记录 | 已接受基础设施空洞（什么叫空洞见 5.9）的读取与写入（摸底相位与各臂共用） | `composites/shortfall.py` |
| — | 考卷状态读取 | 从存量记录读出考题的已判作答与裸答 | `composites/acceptance_skill_status.py` |
| — | 装配工具 | 从源 run 按 train 溯源装配新 run（`assemble-reuse` 子命令；读旧版本源 manifest 时经 `core/config_upgrade.py` 的升级纯函数做身份核对） | `stages/assemble_reuse.py` |

组合积木负责落盘和续跑：每一步的产物按确定 ID 存进 B3，再次运行时已完成的
单元直接读盘跳过。功能积木本身不读写磁盘。总要求：**在任何一处崩溃后续跑，
结果都与一次跑完相同**（少数已知例外写在各决定文档的"已知局限"里）。

**K1 批量执行**的要点：

- 派发顺序按轮次（先所有题的第 0 次采样，再第 1 次……，同题采样不相邻）；
- 每个单元的工作目录里有 `k1_retry_cursor.json` 重试游标，续跑接着数；
  批弹性：每个单元 3 次尝试，重试之间随机等待 15–45 秒；`--k1-stagger`
  让单元启动前随机等待错开连接；pending 非空时每 300 秒打一行心跳
  （done / failed / skipped / in_flight），"正常慢"与"卡死"必须可区分；
- 续跑前核对已存单元的角色、模型名、注入 skill 与本次请求一致，不一致报
  配置错误；已存文件从不覆盖；
- `skip_unit_ids`：调用方声明为终态的单元（已接受的基础设施空洞）不执行、
  不记失败；
- 任一单元抛出基础设施以外的错误后不再启动新单元；配置错误、服务方拒绝按
  原类型抛出，代码 bug 保留完整的报错回溯（traceback）。

**K5 库整理**的要点（整理发生在序列最末的学习相位，见 5.6）：

- 整理日志（`curation_log.json`，在相位目录里）先写、再改技能状态，续跑按
  日志重放，不重新问模型；合并编号永不复用；
- 一次合并的提交顺序：合并 skill 存为 pending（pending 永远不是库成员）→
  回归考试 → 决定写进日志（提交点）→ 保留则来源标 `merged`、合并 skill 翻成
  `accepted`，撤销则存为 `discarded`；
- 回归考试沿 `merged_from` 递归展开到原始 skill，每个原始来源的考题都要过；
- 输入集快照与过期检查：store 的 accepted 集合必须等于"输入集 − 保留合并的
  来源 + 保留的合并 skill"，对不上即配置错误，只能换新 run 目录重跑。

**K6 live 判定账本**的要点：

- 组处于哪一步全部从磁盘推导，没有内存状态对象；判定作为纯函数重算，与
  账本首行不一致 → 代码 bug 停机；
- 持久化顺序：verdict 行先于任何状态提交落盘；归因行先于任何修订动作；
  injection_audit 行在派发前首写冻结，此后一切读取回到冻结行。

### 3.5 相位 → 积木组装

| 相位 | 用到的积木 |
|---|---|
| 冻结步（p00） | D1、D2、B4、`methods/registry.py` 的逐族解析 |
| baseline | 交付 `none`、E1 × K1（强、弱两臂）、验收判定核（benchmark 默认判分规则）、D3（摘要）、辅助指标 |
| exam | 交付 `exam_folder`、`EXAM_SCENE`（现场复原场景）、`EXAM_TRIGGER` / `EXAM_PLAN`（选技能与写计划）、`EXAM_JUDGE`（LLM 判卷）、可选裸答 / 泄题审计、K3 记账、规则 1 复用 |
| live | 交付 `group_by_task` 或 `folder_by_task`、E1 × K1（整组或逐任务文件夹、`n_attempts` 次）、K6 判定账本（`ACCEPTANCE_IMPROVE` 比较规则）、D10（注入 token 估算）、D5（视图，预算内裁剪）、规则 1 复用；folder 交付的读取事实由 AnswerSheet 的 `ExecutionRecord.steps` + `artifacts.skill_folder` 按需提取 |
| test | 配置的交付方法（`whole_bank` / `retrieval` + D11 / `folder` + 技能文件夹物化）、E1 × K1（带库臂 + 对照臂）、D3、`decision.final_test_delta` |
| learn | 统一走 `methods/review`：P02 baseline-gap → add 证据 + `learn_add`（G2）；P04/P06 → per-skill 决策 + 归因证据 + `learn_apply`；`trial_level` → 逐 trial 分析 + 聚合动作 / 新需求 + `learn_requirement`；exam → 多版本 merge + 考卷证据 + `learn_exam`；整理（最末相位）：K5（G7、G8、K3、G9、D10）或 none；相位库工件 |

**复用最多的几块**：

- **D9 skill 注入**：live 相位的真实执行、test 相位的带库执行通过它把 skill 拼进
  prompt；exam 相位改为把全库目录交给模型自己选择，再读取被选中技能的正文；
- **K1 / E1 执行**：四种执行相位全部是它；
- **D5 轨迹视图**：G1 切段看整条轨迹，找证据看段内步骤与失败视图；
- **D3 成绩统计**：各执行相位的摘要与 test 相位的 delta 都是它。

**跨 benchmark、跨模型**：加一个新 benchmark = 写一个插件（runner、任务加载、
截断规则、执行 prompt 四样），其余积木不动；换强 / 弱模型 = 改配置的角色表
与弱模型画像；做消融 = 在序列里增删相位、换交付 / 整理方法，其余照常。

### 3.6 Prompt 统一管理

所有发给模型的 prompt 集中在 B2 注册表里统一管理，不在业务代码里散落字符串。

| prompt | 所属积木 | 典型使用者（角色，相位） |
|---|---|---|
| 切段 | G1 | segmenter（学习相位 add） |
| skill 提炼（强模型段） | G2 | author（学习相位 add） |
| skill 诊断（弱模型段） | G2 | author（学习相位 add） |
| 复原场景 | EXAM_SCENE | examiner（exam 相位） |
| 选技能 / 写计划 | EXAM_TRIGGER / EXAM_PLAN | answerer（exam 相位、整理的回归考试） |
| 判卷 | EXAM_JUDGE | judge（exam 相位、整理的回归考试） |
| 改写 skill | G6 | rewriter（学习相位 revise，exam 证据路径） |
| 找相似、冲突与低价值 | G7 | curator（最末学习相位） |
| 合并 skill | G8 | curator（最末学习相位） |
| 分组 | G9 | curator（最末学习相位） |
| 执行任务（每个 benchmark 一份） | E1 的 runner | executor_strong / executor_weak（各执行相位） |
| 执行任务·技能文件夹（`TB_SKILL_FOLDER_GUIDANCE` 作为任务级 `SKILL.md`；每个技能是 `<skill>/SKILL.md`，由 Terminus 自己发现） | E1 的 TB runner（folder 单元） | executor_weak（test 相位 real_folder） |
| SKILL_ATTEMPT_ATTRIBUTION | G12 | author（学习相位 revise，每窗口一次） |
| SKILL_ATTEMPT_REVISION | G11 | rewriter（学习相位 revise，逐 skill 整条重写） |
| TRIAL_FAILURE_ANALYSIS | trial_level review（live 相位） | author（逐失败 trial 并发） |
| TRIAL_EXAM_ANALYSIS | trial_level review（exam 相位，按裁判判词分析纸面答卷） | author（逐失败答卷并发） |
| TRIAL_REQUIREMENT_AGGREGATION | trial_level review | author（每相位一次） |
| PROFILE_UPDATE | trial_level review | author（每相位一次） |
| REQUIREMENT_SKILL | trial_level review | author（每个新需求一次） |

**全部方法 prompt 全量冻结**：`prompts/__init__.py` 的 import 清单登记
每个方法 prompt，**每个 run 的 manifest 都冻结全部已注册方法 prompt 的
digest**（外加本 benchmark 的任务 prompt；插件声明了技能文件夹能力时，
能力位指向的 folder prompt 一并冻结，名字未注册是配置错误）。两条静态
测试守住冻结面：
`prompts/*.py` 每个模块必须进 import 清单；prompt 注册只允许出现在
`prompts/` 与 `benchmarks/<名字>/`。改一个字的 prompt 文本即视为新实验，
必须新建 run 目录。

### 3.7 代码目录

目录结构、分层规则和开发规范见 [AGENTS.md](AGENTS.md)。积木到目录的对应：
基础件在 `core/`、`llm/`、`prompts/`、`infra/`、`inject/`；benchmark 插件
在 `benchmarks/`；功能积木在 `blocks/`；组合积木在 `composites/`；
方法驱动与参数契约在 `methods/`；相位流程在 `stages/`。

---

## 四、数据模型与对象仓布局

### 4.1 五个对象

五个核心对象，全部 JSON 落盘，外键记录关系，ID 按 3.1 第 6 条的确定规则
生成（对象定义在 `core/models.py`）：

```
Trajectory（轨迹，schema v2）
  trajectory_id, task_id, benchmark, model_role(强/弱), model_name,
  attempt_index(第几次采样), skills_injected(null=裸跑；否则为
  skill_id+version 列表或库的 digest),
  steps[ {action, observation} ... ],
  correctness(判分器结果：true / false / null), meta(终止原因等自由数据)

Subtask（段）
  subtask_id, task_id, trajectory_id(→轨迹), source(强模型段/弱模型段),
  trajectory_success(来源轨迹本身是否成功), segment_index,
  step_range[start, end), label, description,
  success(该段目标是否达成), context_summary
  （段内逐步原文不另存，按 step_range 从 Trajectory 取，只有一份来源）

Skill（schema v3）
  skill_id, content(自然语言正文), subtask_id(→段),
  source_subtasks[ subtask_id ... ](素材来源段；合并产物 = 各来源的并集),
  summary(一句话摘要：这条技能是什么；目录行用，不进 content；
    G2/G6/G11 产出，K5 合并产物的摘要由 G8 写；非空、单行、无长度上限；
    v2 旧记录迁移时置空，M2 目录渲染对空摘要回退取 [When to use] 节),
  version(改写次数), status(pending/accepted/discarded/merged),
  discard_reason(自由字符串), group(能力分组，整理写入),
  rewrite_history[ 旧版正文、旧摘要、错因归类与改写原因 ],
  merged_from[ skill_id ... ](仅整理产物)

Exam（泛化考题，schema v4）
  exam_id(skill_id + version), skill_id, skill_version,
  scene_text(自然语言场景), expected_behavior(自然语言期望行为),
  reference_answer(判卷参考),
  scene(TraceRef：来源相位 + 一张答卷 id + 场景切点步号；出题时从该技能
        版本的 Provenance.scene 复制)

TraceRef（精确位置，值对象，core/trace.py）
  phase_label, sheet_id(恰好一张执行答卷), step(该答卷 steps 的 0 起下标)
  —— 不存 unit / task / attempt：这些都能从答卷推出，存了就会有歧义

Provenance（技能版本的来源，值对象，每个版本一条）
  skill_id, version, phase_label(产出该版本的 learn 相位),
  cause(add / revise / requirement / merge),
  scene(TraceRef | null：该版本的考题从哪里切；合并产物为 null),
  sources[ TraceRef ](背后全部证据位置，审计用), note(写作 / 改写原因)

Attempt（作答记录）
  attempt_id, exam_id, skill_id + skill_version(都为 null = 裸答),
  purpose(bare / acceptance / regression), weak_answer,
  steps(选技能与计划的作答轮次),
  judge_verdict(null = 已作答、尚未判卷), judge_reason, retry_round
```

**对象 schema 的历史**：v1→v2（2026-10-15）Skill 升 `source_subtasks`、
Exam 升 `skill_id`；Trajectory 于 2026-10-16（R2b）升 v2：三值运行状态改为
`correctness: true / false / null`，基础设施失败改走 `InfrastructureError`
异常通道；Skill 于 2026-10-17（技能文件夹 M1）升 v3：加一句话摘要
`summary`，v2 旧记录迁移时显式置空。旧记录的读取迁移显式做在
`core/models.py`（v1 的 success /
failure 行读取时迁移，v1 的 infra_error 行按 K1 契约本就不该存在、明确拒载；
v1 Skill / Exam 走同一文件里的迁移纯函数），不静默读成默认值。

**discard_reason 的词汇**：字段是自由字符串（schema 不设闭合枚举），系统
实际写入的词：`weak_already_knows` / `retry_exhausted` / `leak_audit` /
`model_output_error` / `regression_failed` / `regression_error` /
`interrupted_merge` / `live_no_improvement` / `live_revision_deleted` /
`live_undetermined` / `live_injection_overflow`。两个跨模式复用词的证据
基础不同：`weak_already_knows` 在 exam 路径 = 裸答即对，在 live 路径 =
基线全成功（零执行推断）；`model_output_error` 在 exam 路径 = 作答 / 判卷 /
改写预算耗尽，在 live 路径 = 归因预算耗尽（整组）或修订预算耗尽（单条）。

### 4.2 correctness 词汇与运行指标

R2b 之后，"这次运行结果如何"由三样东西分开表达：

- **`correctness: Optional[bool]`**：benchmark 自带判分器的原样结论。
  `null` 表示没拿到判分结果——没有判分器的 benchmark、判分器没跑（TB 的
  伪零分、trial 中途死掉）、或模型没有产出可用回复。`null` 运行永远不是
  任何一方的测量成绩；
- **运行指标**（`metrics`）：重试次数、每次报错的原文、耗时、退出状态，
  随运行记录传给验收；
- **`InfrastructureError` 异常通道**：沙箱超时、断网、进程死亡这类失败
  从结果词汇里移出，插件抛异常、K1 按重试策略重试。这个通道与
  `correctness = null` 的分工：异常 = 根本没拿到可交付的结果（重试）；
  `null` = 拿到了结果但判分器没给结论（不重试，交验收处理）。

### 4.3 run 目录布局

五个对象仓保持 run 级共享（目录名沿用历史名字，五个相位的执行、切段、
写作全部读写同一份）：

```
<run>/
  manifest.json                  冻结的输入与协议
  ASSEMBLED_FROM.md              装配溯源（仅装配产出的 run 有）
  run.lock                       run 目录排他锁（B7），记持有者 pid
  pipeline_log.jsonl             逐步完成的摘要账本（key 恒为 "stage"，
                                 值为 p00:freeze 与各相位的 pNN:<名>；
                                 续跑追加新行，读取取每个标签的最后一行）
  failed_units.jsonl             单元失败账本（单元 ID、相位标签、错误
                                 类别、消息），只追加
  stage0_summary.json            冻结步摘要

  —— run 级五仓（全部相位共享；目录名沿用历史名字）——
  stage1_baseline/trajectories/  轨迹仓：摸底相位两臂的裸跑轨迹（test 相位
                                 full 模式的对照也从这里读）
  stage1_baseline/accepted_infra_shortfall.json
                                 摸底相位已接受的基础设施空洞（本 run 的
                                 持久决定，见 5.9）
  stage2_segments/subtasks/      段仓（学习相位 add 路径的切段结果）
  stage2_segments/segmentation_audit.jsonl
                                 切段审计：重切耗尽、被跳过与 0 步空轨迹，
                                 本 run 内不再重切
  stage3_generation/skills/       技能仓（写作产出的技能；整理的合并技能
                                 bank/merge/NNNN 也在）
  stage3_generation/exams/        考题仓（exam 相位执行时点生成的考题）
  stage4_acceptance/attempts/    作答仓（裸答、带技能作答、回归考试共用）
  stage4_acceptance/leak_verdicts.jsonl
                                 泄题判官的每次判决，每个 (skill, 版本)
                                 恰一行、首判为准，续跑不重问

  —— 实战相位的账本与轮仓（沿用历史位置；live 相位专用）——
  stage4_acceptance/live_run/
    verdicts.jsonl                逐 (task, round) 判定行，append-once、
                                 首判为准；零执行终态（weak_already_knows /
                                 live_injection_overflow / live_undetermined）
                                 同样写行：计数与 better 记 null、终态名进
                                 verdict 字段
    round00/ round01/            两个轮仓（本轮带注入的 live 轨迹，内部
                                 同轨迹仓布局；injection_audit.jsonl 派发
                                 前首写冻结；accepted_infra_shortfall.json
                                 按轮记录；folder_by_task 的读取/未读事实
                                 由相位 AnswerSheet 的 execution steps 提取）

  —— 相位目录（每个相位一份）——
  phases/pNN_<局部名>/
    phase.json                   这个目录里跑过什么（标签、序号、角色）——
                                 后续相位从磁盘推导事实的依据
    units.jsonl                  执行相位：交付解析出的执行单元清单（带
                                 完整冻结 unit_id；每次相位启动整文件重写，
                                 exam 相位读覆写前的内容当同相位重放标记）
    unit_specs.jsonl             执行相位：每个预期 trial/unit 的 UnitSpec
                                 （unit_id / task / role / arm / expected）
    answer_sheets/               执行相位：trial 级 AnswerSheet 的 JSON + Markdown
                                 （execution + coverage + grading）；test 相位
                                 两臂都写在这里
    coverage.jsonl               执行相位：每个预期 trial 的 CoverageRecord
                                 （present / missing / terminal + cause）
    carried_units.jsonl          执行相位：规则 1 复用单元的上一份 UnitSummary
                                 （新 unit_id + 原计数 + reused_from）；只在
                                 有复用单元时写，学习相位据此仍能决策该单元
    reuse_index.jsonl            执行相位：规则 1 的 execution-side 缓存
                                 （unit_id / status / cause）；不是验收报告，
                                 test 相位不写
    redispatch.json              执行相位：按单元持久记录的无效重派计数
    <臂>/store/                  执行相位自己的轨迹仓（test 相位的两臂、
                                 split 模式 test 相位的现场裸臂；摸底相位
                                 与 live 轮仓不在这里，见上）
    <臂>/accepted_infra_shortfall.json   本臂已接受的空洞
    skill_folder/                  folder 交付物化出的技能文件夹：
                                   test 相位（folder）一份，全部单元共享；
                                   live 相位（folder_by_task）每个任务一个
                                   子目录（每条技能一个 <skill>/SKILL.md +
                                   catalog.md 兜底，幂等物化）
    generation_failures.jsonl    exam 相位：出题失败的终态记录（每道考题
                                 首行即定）
    phase_summary.json           执行相位摘要（也是返回给相位循环的值；
                                 baseline/live/test 相位 complete=true、
                                 exam 相位 infra_failed 为空即该相位的
                                 完成信号——已完成的执行相位重放时整个
                                 跳过，工件保持自己那一轮的落盘字节）
    task_list.jsonl              学习相位：生效的任务清单（每次整文件重写；
                                 被取代的多版本行单独记一行）
    revisions.jsonl              学习相位：改写前的版本号（改写调用前先写，
                                 每条技能只写一行，续跑按版本号重放）
    outcomes.jsonl               学习相位：逐技能回执（见 5.6 的结局清单；
                                 同一目标每条技能至多一条回执，重放不补写）
    provenance.jsonl             学习相位：每个技能版本一条 Provenance
                                 （scene / sources 均为 TraceRef；唯一的
                                 写入口是 ledger.record_version）
    skill_bank.jsonl             学习相位：本相位的技能库工件（全体成员，
                                 含待定；下一个执行相位的交付从这里读）
    bank_summary.json            工件摘要（成员 / 大小 / 整库 digest）
    curation_log.json             整理日志（agentbrew 方法，最末学习相位）
    learn_summary.json           学习相位摘要（最后写；带 bank 键即该相位
                                 的完成信号）
    trial_findings.jsonl         trial_level：逐失败 trial 的自由文本诊断
    new_skill_requirements.jsonl trial_level：聚合后的跨 task 新 skill 需求
    requirement_outcomes.jsonl   trial_level：新需求的逐条回执（added；最后
                                 一个 learn 相位记 deferred_no_later_check）
    model_profile.json           trial_level：累积的弱模型画像（初始为空）
    trigger_families.jsonl       cluster_by_trigger：每个 subtask 的五个
                                 trigger 族之一及理由
    clusters.jsonl               cluster_by_trigger：族内聚类结果（theme、
                                 subtask_ids、reason、确定性 cluster_id）
    cluster_skills.jsonl         cluster_by_trigger：每类写出的 skill
                                 （cluster_id、skill_id、source_subtasks）
    cluster_outcomes.jsonl       cluster_by_trigger：逐 cluster 回执
                                 （added / skipped_existing / failed /
                                 infra_failed）
    cluster_summary.json         cluster_by_trigger：跨 task 写作摘要（族、
                                 类、skill、回执计数，以及
                                 uncovered_add_tasks：授权 add 但无锚点
                                 skill 的任务、基线里是否至少一次强模型
                                 成功且一次弱模型失败、被哪些 cluster
                                 吸收；只读报告，不改分组）

  —— 其他 ——
  skill_bank.jsonl               run 根整库（装配产出的 run 由装配器写入；
                                 没有相位工件时交付的回退来源）
  work/                          执行单元的工作目录（K1 重试游标、TB 提交
                                 记录与取回的原始文件），按相位与臂分树
  llm_requests/                  B1 记录的每次模型请求与响应，不覆盖、
                                 不含 key
  llm_requests/usage.jsonl       用量账本，跨进程累加
  *.lock / *.interrupted-tail    JSONL 账本旁的加锁文件 / 被打断的半行
```

失败类账本都是**第一次记失败时才创建**：一个干净跑完的 run 目录里没有这些
文件，不能拿"文件在不在"当判据。

阶段世界的历史目录（`stage1_summary.json` 等）不再产生；装配器（`assemble-reuse`）
从旧 run 装配时按白名单拷贝历史产物，见 `stages/assemble_reuse.py`。

---

## 五、每个相位的流程

命令行循环（`cli.py`）按冻结序列逐相位派发：执行相位进
`stages/phase_execute`（一个包，四种角色各一个模块），学习相位进
`stages/phase_learn`（P02 add 在 `stages/learn_add.py`，P04/P06 per-skill apply 在
`stages/learn_apply.py`，exam 在 `stages/learn_exam.py`，共享账本在
`stages/learn_ledger.py`）。每完成一个相位
打印一行摘要并写进度账本。**续跑 = 同一条命令重跑**：每个相位按磁盘上已有
的产物跳过已完成的单元。

### 5.1 冻结步（p00）

- 加载任务（D1）、推导划分（D2）、逐相位解析全部方法族并校验参数（拼错的
  方法名在这里停下）、核对全部具名规则名已登记；
- 冻结进 manifest：benchmark、模式、三键划分列表、完整配置（含
  `sequence` / `phases` / 生效规则名 / `model_options` / `token_budgets`）、
  全部方法 prompt、本 benchmark 任务 prompt（及插件声明的 folder prompt）的
  digest、模型角色表、任务
  内容摘要 `tasks_digest`、辅助指标名 `aux_metric`（插件声明，预登记）；
- 之后每次启动逐字段比对，再改即视为新实验（B4）。运行设置（并发、重试
  次数）不冻结。

### 5.2 摸底相位（baseline）

- 强模型与弱模型在每道训练题上各裸跑 `n_attempts_strong` / `n_attempts_weak`
  次，全部轨迹（成功和失败都保留）写进 run 级轨迹仓；
- 验收报告每个 (任务, 角色) 一行（判定用 benchmark 默认判分规则；未判分
  的运行按 all_holes 语义处理，见 5.7）；
- **失败题的挑选不在这里**：决策模块在学习相位按 `task_select` 具名规则从
  弱臂行推导（缺省 `any_weak_failed`：任一有效尝试失败即入）；
- 有未接受的基础设施空洞时写完摘要后以基础设施错误停下（退出码 3），续跑
  只补空洞；接受的空洞是本 run 的持久决定（见 5.9）；
- 摘要含两臂的 D3 成绩与辅助指标。

### 5.3 泛化考试相位（exam）

泛化考试检查弱模型能否在场景中**找到并正确使用**技能，不执行真实终端环境。
每个新建或版本升高的技能版本生成一道考题，`exam_id = skill_id + version`；
curation 的合并产物不考（整理阶段的合并回归考试复用各源技能已有的考题）。
考题的场景从技能版本的 `Provenance.scene`（一个 `TraceRef`）复原：
`resolve(scene)` 取回来源执行的真实前缀和后续，examiner 用 `EXAM_SCENE`
写成自然语言场景、期望行为和参考答案；`Exam.scene` 保存这个 `TraceRef`。

每个考题单元内的顺序：

1. **选技能**（`EXAM_TRIGGER`）：把当前库全部成员（accepted + pending）的
   目录交给弱模型，目录描述与 test/live 的 folder 一致；模型可以打开 0 到
   `max_skill_reads` 条技能，并给出理由；
2. **写计划**（`EXAM_PLAN`）：把被打开技能的正文交给模型，要求它写出接下来
   的预期计划；没有打开技能时也写计划；
3. **判卷**（`EXAM_JUDGE`）：judge 读取场景、期望行为、参考答案、候选目录、
   程序记录的已打开技能、选择理由和计划，判 success / failure；读取了哪条
   技能是程序事实，不由模型自述；
4. **可选步骤**（默认关闭）：裸答对照（不给目录）和泄题审计（D8）。旧配置
   升级到 v7 时保留这两项；新配置默认关闭。

作答轮次写进 `Attempt.steps`，AnswerSheet 的 `ExecutionRecord` 带这些 steps
和技能目录清单，所以 `skill_usage`、`trial_level` 审查和 live 路径共享同一套
读取事实。重放保护沿用 K3：同一考题已判卷的 attempt 直接复用；同一相位实例
已规划过的单元从覆写前的 `units.jsonl` 识别。

**来源与上下文（一条主线）**：来源指针和写作上下文共用同一个 `TraceRef`，
细节与取舍见 `docs/decisions/provenance_trace_context_20261029.md`。

- `TraceRef` 只能由 `trace_at(sheet, step)` 构造：参数必须是一张**执行**答卷
  对象，校验 `0 <= step < len(steps)`，并拒绝考试答卷；因此“只有 unit 的
  模糊指针”和“考试的考试”都写不出来。
- `resolve(run, ref)` 是读取来源上下文的唯一出口，返回 `TraceContext`
  （任务说明、切点前后的渲染步骤、源执行自己的判分）。出题用的前缀和写作类
  prompt 里的轨迹文本都由它读取同一张答卷，找不到答卷或越界直接报错，不扫
  目录、不排优先级。出题（`EXAM_SCENE`）取完整前缀和切点后 30 步，所以
  P03 与 P05 对同一道题的出题文本相同。
- `render_scenes(run, refs)` 是写作者（`REWRITE`、live 改写、需求）读到的
  真实步骤。前缀只保留切点前最近 `WRITER_BEFORE_STEPS`（15）步，切点后
  30 步；同一次执行上的多个切点（同一条轨迹的几个技能各有一道考题，被
  同一个需求引用）合并成步号的并集，每个步骤只出现一次，窗口之间的空隙用
  `[steps a-b are not shown]` 标出，任务说明和这次执行的结果也只写一次。
  对 2053 run 的 37 个 P02 场景、22 条需求实测：需求 prompt 最大从 444K 字符
  降到 256K，REWRITE 场景最大从 304K 降到 226K，都低于该 run 里成功发送过的
  最大输入（约 302K 字符）。
- `scene_of(exam)` 返回 `exam.scene`：考试答卷（纸面）想回到真实场景，只能
  经由它的考题。
- 五类写作点统一经 `ledger.record_version(skill, provenance)` 落盘；
  `scene` 的取值：P02 = 锚点轨迹答卷在段起点；live 改写 = 窗口自己的
  `ref`；exam 改写 = `scene_of(被考的 exam)`（原切点，保证 P03→P05 逐题
  配对）；需求 = 它第一个来源的真实位置（来源是考试答卷时经 `scene_of`
  回溯）；合并 = null。
- 边界检查：每个 learn 相位结束与每个 exam 相位开考前，都校验每个版本有
  Provenance、`scene` 可解析、`scene` 不是考试答卷；P02 版本还要求
  `scene` 的轨迹等于技能锚点轨迹。审计脚本做同一项检查。

### 5.4 实战验收相位（live）

- 验收单元 = 一个训练任务的**组** = 该任务的全部待定成员，弱模型真实执行
  `n_attempts` 次。交付可选整块注入（`group_by_task`）或逐技能目录
  （`folder_by_task`）：后者把每个技能写成 `<task>/<skill>/SKILL.md`，模型只
  先看到名字和描述，按需读取全文；派发前按任务物化到相位目录的
  `skill_folder/<task>/`，执行后由该轮 AnswerSheet 的
  `ExecutionRecord.steps` + `artifacts.skill_folder` 提取读取/未读事实。
  首轮提示词仍可见时比较 `<available_skills>` 与清单；
  TB 的本地还原从第一条模型命令开始，列表通常不可见，此时有读取即视为投递
  可用，没有读取则记 `unverified`，不误判为投递失败；
- 轮次规则：序列里第 k 个 live 相位测量第 k-1 轮，轮号只有 {0, 1}
  （序列至多承载两个 live 相位）；
- 派发前首写冻结注入审计行（成员 + 注入块 sha256 + D10 估算 token）；
  注入超过 `max_injection_tokens` → 该组 `live_injection_overflow` 零执行
  终态；基线全成功 → `weak_already_knows` 零执行终态；零有效测量 →
  `live_undetermined` 零执行终态；
- 执行完毕逐组判定（`ACCEPTANCE_IMPROVE` 具名规则 × D3 计数，verdicts 账本
  append-once、首判为准、续跑重算逐字节核对）；判定、终态提交
  （finalize_group）都在相位内完成；
- **相位宇宙会随自己的进度缩小**（判定提交后组不再是待定），所以重入的
  live 相位把覆写前 `units.jsonl` 里已有、本轮没再规划的单元**并回**单元
  清单与摘要——相位的单元记录是它所有 pass 解析过的并集；摘要的
  units / dispatched / skipped_* 全部从并集与轮内磁盘事实（冻结审计行、
  终态、规则 1 复用表）推导，不读本 pass 的计划清单，重放落在一次跑完
  的同一些字节上；
- **归因与修订不在这个相位**：没提升的组由下一个学习相位按
  `review.decision` 先产出 per-skill 的 keep / revise / delete；`review`
  按 `skill_use` 找到触发点：folder 按每个 cat 调用切前后窗口，group/whole
  bank 每个 attempt 只取开头和结尾窗口；同一窗口内多条 skill 合并一次
  `SKILL_ATTEMPT_ATTRIBUTION`，然后逐 skill 写 `SKILL_ATTEMPT_REVISION`。
  改写没产出新版本时，注入块逐字节不变，下一个 live 相位按规则 1 直接沿用
  旧判定。

### 5.5 终测相位（test）

- 在 test 列表（split 模式）或全部任务（full 模式）上跑带库臂：交付方法
  由配置给出（`whole_bank` 整库注入 / `retrieval` 逐任务检索注入 /
  `folder` 目录清单交付 / …），`whole_bank`、`retrieval` 与 `folder` 都
  只用**已接受**成员；`exam_folder` 面向考试的全库候选，`group_by_task`
  面向 live 的待定组；
- **`folder` 交付（技能文件夹）**：其余交付把技能文本拼进提示词，folder
  把技能放进执行环境里的一个文件夹。每个已接受技能写成自己的目录
  `<skill>/SKILL.md`，文件开头是 YAML 头（`name` 和 `description`），
  后面是三段正文；任务级 `SKILL.md` 只放一段使用说明，`catalog.md` 是
  兜底目录。交付解析出的每个单元在 `aux["skill_folder"]` 里带一份清单：
  目录文本、每条已接受技能的相对路径映射、库指纹、描述拼法——不带技能
  正文（正文的唯一来源是相位技能库，渐进读取是这个玩法的核心）。
  `FolderParams.description_mode` 控制 YAML 头里 `description` 的拼法：
  `summary` 只放摘要，`when_to_use` 只放触发条件，
  `summary_then_when_to_use`（默认）两者都放。第一轮任务提示词由
  Harbor/Terminus 自己拼；远端部署的 `Terminus2._build_skills_section`
  会扫描 `skills_dir` 下深度 2 的 `SKILL.md`，解析 YAML 头，把技能名、
  描述和完整路径生成 `<available_skills>` 放进首轮提示词。因此
  `TencentSkillTerminus2` 在运行前把 `skills_dir` 设成当前任务目录
  `/opt/openclaw-skills/<task>`，插件不再把 catalog 注入 `SKILL.md`，也不
  再要求模型先读某一份固定文件。catalog.md 每行是
  "技能编号 | 要 cat 的相对路径 | 一句话摘要"（2026-10-23 起描述完整保留、
  不截断），只在 `<available_skills>` 缺失时作为兜底。派发前相位驱动把清单物化成真实目录（相位目录下的
  `skill_folder/`，`materialize_skill_folder` 幂等：内容不变不重写；
  库指纹与相位库不符是代码 bug 停机）。支持技能文件夹的插件把目录送进
  执行环境（TB：目录随既有 skills 上传链进沙箱 `/opt/openclaw-skills/`；
  上传缓存递归打包，缓存键是清单列出的每个文件的字节摘要）。插件用能力位
  `skill_folder_prompt_name` 声明支持（目前只有 tb 声明，默认指向
  `TB_TASK_PROMPT_FOLDER`）；实际上传的任务级 `SKILL.md` 是
  `TB_SKILL_FOLDER_GUIDANCE`，它指向 `<available_skills>` 和挂载路径。
  folder 相位落到不支持的插件，test 相位驱动在派发前就报配置错误停机——
  与技能库空不空无关，逐单元的 E1 守卫保留作第二道。空已接受集不物化、
  单元裸跑（空注入是空串：不渲染一个只剩表头的空目录文件）。
  `real_folder` 执行没有自己的执行参数，test 相位的采样深度沿用全局
  `n_attempts_weak`，与旧 test 相位行为一致；执行过程零拦截零预算，模型
  实际读了哪些技能文件，记录在轨迹的 cat 命令里，事后由纯函数解析
  （`composites/skill_use.py`：匹配完整相对路径 `<skill>/SKILL.md`，
  或同一行里同时出现唯一的技能目录名和 `SKILL.md`，首读序去重，只读轨迹
  零落盘）：每单元的读取集写进 test 相位摘要的 `skill_files_read` 键
  （`{轨迹 ID: [技能编号]}`）。键的口径要读全：它只盖带技能臂——对照臂
  不进键，且轨迹 ID 与对照臂同形，审计拿 ID 对账必须配合臂目录使用。
  三态分辨：**键缺** = 旧执行相位，或该单元没跑成 / 已接受为空洞（要与
  "跑了没读"区分，必须交叉摘要的 `infra_missing` / `infra_accepted`
  计数）；**空列表** = 跑了但一个技能文件都没读；非空列表 = 实际读过的
  技能（首读序去重）。已知局限：别的目录下同名相对路径的 cat 会误记、
  只有 cat 算（grep/head 不算，catalog.md 永不进读取集）、只有
  `cat SKILL.md` 而没有技能目录名时无法判断是哪一个技能，不计入。
  test 相位不写验收报告，读取集不进任何决策输入，供事后审计与技能使用
  分析；泄题审计对 test 相位沿用既有覆盖（exam 相位考试管道的 D8 判定）；
- 对照臂：full 模式复用 baseline 相位的弱臂裸跑（连同其已接受空洞）；split 模式
  在相位内现场跑一条裸臂（`fresh_bare`）；或 `external_campaign`——对照
  读外部 campaign 的冻结成绩文件（AgentBrew 裸跑归档），**零执行**：test 相位
  驱动在派发前加载并校验（文件存在、sha256 与冻结配置一致、`model_key`
  覆盖全部 test 任务、每任务奖励数 ≥ 对齐深度），把文件字节拷进相位目录
  （`external_control.json`），摘要带 `external_control` 审计记录（钉住
  的路径 / sha256 / model_key / 对齐深度 / 每任务实际用到的奖励向量）。
  对照口径 = 每任务前 `n_attempts_weak` 次采样奖励的均值（campaign 归档
  每任务 5 次，实验对齐深度 3，与旧外部对照报告的"前 3 次"对齐列同口径），
  归档尝试都是已判分尝试，所以对照臂没有 infra 概念、摘要的对照
  `infra_missing` 恒为空。两臂轨迹仓的隔离只对前两种对照有意义
  （轨迹 ID 不带臂信息，共用会互相继承轨迹算出假的零提升）；
- **每次全量重跑**：不调规则 1 的跳过、不调无效重派；
- **不写验收报告**：delta 由 `decision.final_test_delta` 直接进摘要，
  测试结果不流进学习。摘要并列报告三种口径（事先登记，不看结果再挑）：
  `avg_at_n`（条件口径：先在每道题的有效执行里求成功率，再对至少有一次有效
  执行的题求平均）、`avg_at_n_conservative`（保守口径：所有题都进分母，
  没有有效执行的题记 0）、pass@N（至少一次成功的题占比）；delta 对三种
  口径各算一次，任一臂没有有效执行时条件口径的 delta 记 null（不当成 0）；
- **辅助指标同口径并列**（插件声明了 `aux_metric` 时，如 SearchQA 的
  `sub_em` 子串匹配）：两臂摘要各带 `aux` 一节，delta 对辅助 avg@N 同样
  两种口径各算一次；辅助指标**永远只并列**，不因为结果好看就替换主指标；
  未声明时相关字段全为 null。

### 5.6 学习相位（learn）

一个学习相位把四步跑完：**决策 → 找证据 → 写作 → 整理**。

**决策**：读最近的执行相位的验收报告（本轮测量），对照来自那个相位
`compare` 指向的报告（没有 compare 或 fresh_bare 时，相位内自己的无注入行
就是对照）；技能库 = 上一个学习相位的工件（第一个学习相位没有上一工件，
回退读 run 根的冻结库——装配 run 的预置库，从零跑此时尚无 run 根库、
等价于空库开始；回退覆盖的任务不再重发 add，预置库因此保持冻结）；
剩余检验次数由序列位置算出。紧跟摸底相位的第一个学习相位走
**摸底缺口路径**（`decision.decide_task_list`）：本轮报告为空、baseline
相位报告当对照，对每个弱模型做不好且还没有技能的训练任务发一条 add 行。
后续 P04/P06 走 `methods/review/strategies.py` 的 `task_level` 或 `skill_level`，
统一产出 per-skill 的 keep / revise / delete。也可以选择 `trial_level`：逐条失败
trial 并发分析，聚合 agent 产出 per-skill 动作和跨 task 的新 skill 需求，profile
agent 累积弱模型画像。exam 相位的 `trial_level` 按纸面答卷分析（裁判判词 +
期望行为），每个技能的动作、改写证据和下一次考题的切点都只取自它自己的考题
（见 `src/expedistill/methods/review/README.md`）。`trial_level` 同样遵守 4.2
动作表的“后面没有检查”这一列：`checks_left == 0` 时 revise 转为
delete（`retry_exhausted`），新需求只记 `deferred_no_later_check`、不写作，
因为没有后续执行相位能测它们，而终测只交付 accepted 集
（`docs/decisions/trial_level_last_check_20261029.md`）。

**多版本行合并（仅 exam 路径）**：作答仓共享，一条技能可能带好几行报告行
（不同版本各一行，结论可能互相冲突）。合并规则：**最高版本的那行算数**，
低版本行作为被取代者写进清单账本。P04/P06 的 live 决策已是 per-skill 一行，
不做版本合并。

**逐行动作**（动作是幂等的，续跑重放安全）：

- **keep**：待定成员收进库（状态翻成 accepted；已是 accepted 记
  already_accepted）；
- **delete**：待定与已接受成员都按决策给的原因弃用（`discard_skill` 是
  唯一入口）；已终态的成员保持自己的既有事实（skipped_terminal）；
- **revise**：先收证据再改写，成功则版本加一。exam 路径的证据是考卷
  （考题、弱模型答案、判卷理由）；live 路径按 `skill_use` 找触发点，
  folder 按 cat 调用切前后窗口，group/whole bank 每个 attempt 取开头和
  结尾窗口，同一窗口内多条 skill 合并一次归因，再逐 skill 整条重写
  （记 `revised`）。改写没产出新版本 = 没变化（版本不动，记 unchanged，
  下次学习再试）；
- **add**：把该任务的强模型成功段提炼成新的待定技能；review 的
  `write_lessons` 开着（默认）时，弱模型失败段也用诊断写法写成技能，
  结构与提炼产物相同（一段一技能，`make_skill_id(subtask_id)` 编号）。

**切段在相位世界里的位置**（关键设计，2026-10-16 Wave 3-S）：
**切段发生在学习相位内部、add 行执行的时候**。流程世界没有独立的切段
相位：`learn_add` 的 add 行先调 review 的 segment 方法
（`methods/review/segmentation.py`），它从 **run 级轨迹仓**（正是摸底
相位两臂的裸跑轨迹）按 `trajectory_select` 具名规则逐任务挑轨迹、
**切其中的强角色轨迹，`write_lessons` 开着时连弱角色轨迹一起切**
（关掉时弱臂段落没有消费者，弱臂的钱不花），逐条交给
K2（确定 ID、逐单元落盘、按存量跳过、D7 校验、重切预算、耗尽审计全部
沿用 K2 的既有机制）。参数（`K`、`retry_limit`、`write_lessons`）来自学习相位的 `review`
槽位。0 步轨迹在规则之前排除并写一次性的 "empty trajectory" 审计行。
切不出可学材料（没有轨迹、全被段筛选规则筛掉、或 K2 重切耗尽——本 run
内终态）时记 `skipped_no_material` 回执并继续，**不抛错**（抛错会让续跑
永远卡在同一行）；审计通道 `stage2_segments/segmentation_audit.jsonl`
沿用不变。惰性由结构保证：只有 add 行才付切段的钱，exam-only / live-only
的序列永远不碰切段。随接线进入 add 路径的既有取舍（K2 的 PARTIAL SAVE）：
一条轨迹**内部**是逐段落盘的，写作中断在两段之间时续跑按"任何已存
subtask 即算该单元完成"返回已存**前缀**——前缀即最终结果，不会补切，
续跑结果可能与一次跑完不同（K2 既有设计，非本次引入）。

**三本相位账本**（都在 `phases/pNN_<名>/`）+ 摘要：

- `task_list.jsonl`：生效任务清单，每次整文件重写（决策是磁盘报告的纯
  函数，续跑推导出同样的内容）；
- `revisions.jsonl`：每条技能改写调用**之前**先写版本号（from_version），
  续跑看到版本已加一就只补回执、不再问模型（K6 版本号重放守卫的沿用）；
- `outcomes.jsonl`：逐技能回执（`learn-outcome-v1`：phase_label /
  target_kind / target_id / action / skill_id / outcome / version），
  旧世界 attributions.jsonl 的接替者，审计脚本与 `tests/golden/` 的金基线
  测试以它为准；
- `learn_summary.json`：摘要最后写，**带 bank 键即该相位的完成信号**——
  已完成的学习相位重放时整个跳过（回执不翻倍、工件不按当前技能仓重写）。

回执的**结局清单**（11 种）：

| 结局 | 含义 |
|---|---|
| `added` | add 写出了新技能 |
| `accepted` | keep 把待定成员收进库 |
| `already_accepted` | keep 遇到已接受成员（复测又通过） |
| `revised` | revise 产出新版本 |
| `unchanged` | revise 没产出新版本（没变化，下次再试） |
| `discarded` | delete 按决策原因弃用 |
| `skipped_no_material` | add 切不出可学材料（见上） |
| `skipped_existing` | add 遇到技能已存在的经历（续跑的另一重保障） |
| `skipped_terminal` | 行指向已终态的成员（可见性行，不动作） |
| `infra_failed` | 基础设施失败（记失败账本，相位末抛错，续跑重试） |

**整理与相位库工件**：每个学习相位结束都产出自己的技能库工件
（`phases/pNN_<名>/skill_bank.jsonl`，**全体成员**，已接受 + 待定——工件
是相位之间唯一的交接物，下一个执行相位的待定宇宙从这里来；整理只作用于
已接受集，token 预算硬阀也只算已接受集）。序列**最末**的学习相位跑配置的
整理方法（agentbrew：去重合并 + 冲突处理 + 回归考试 + 分组 + 长度预算；
none：原样入库，预算仍是硬阀），中间的学习相位写原样工件（零模型调用）。
基础设施失败的相位不产出工件（半成品相位不发布库），摘要写完后抛错退出，
续跑重进补完。

### 5.7 判定与无效重派

- 每张 trial 的判定走 `verdicts.grade_sheet`，规则按相位
  `acceptance` 字段选（缺省用 benchmark 默认绑定：tb → `tb_scorer`，
  searchqa / alfworld → `scorer`）。判定词汇：success / failure / invalid
  （无效 = 运行没有正常完成，不能算模型成绩：泄题、注入过长、判分器没跑、
  例外死亡）；
- 单元的全部运行都无效（或派发了但什么都没存下）时，相位调度把单元重新派
  给执行，预算是配置的 `invalid_redispatch_limit`（按单元持久计数
  `redispatch.json`，续跑接着同一预算）；耗尽后该单元报 not_testable /
  `invalid_redispatch_exhausted`，不再进 K1；
- 全部落盘运行都未判分的单元是既成的 all_holes 状态，本轮不重派（重新
  采样会超出冻结的采样深度），按空洞处理；
- exam 相位没有重派回路：作答要么被判定、要么是出题 / 修复预算耗尽的
  终态。

### 5.8 没变的单元不重测（规则 1）

执行相位把解析出的单元清单（`units.jsonl`）与 execution-side 的复用缓存
（`reuse_index.jsonl`：unit_id、status、cause）写进自己的相位目录；
`decision.reusable_units` 把本次的单元与**更早的每个执行相位**按
（任务, 角色, 注入块字节）连接、最新优先：命中即不重新执行，新相位的状态行
沿用旧判定。复用单元不产生新的 trial sheet，因此执行相位把它上一份
UnitSummary 写成 `carried_units.jsonl`（新 unit_id、原计数、`reused_from`）；
学习相位读 `answer_sheets/` + `coverage.jsonl` + `carried_units.jsonl`，
用 `unit_summary.py` 现算 UnitSummary（见 5.7）。这样规则 1 不会把“改写
没产出新版本、下次还要再试”的单元从决策输入里隐藏掉。`zero_execution`
可见性行（工作还没做的记录）永远不参与复用。在 live 相位上这就是 no-op
守卫；在 exam 相位上它让"改写没变 → 下轮免考"自动成立。

### 5.9 基础设施空洞

- 执行单元耗尽基础设施重试后不落盘，磁盘上表现为"轨迹缺失"；摘要按应有
  单元集合计数（`infra_missing`）；
- 有未接受的空洞 → 写完摘要后以基础设施错误停下（退出码 3），同一条命令
  重跑只补空洞；
- `--allow-infra-shortfall` 接受空洞：被接受的单元 ID 先写进持久记录再继续，
  之后每次启动都跳过、按空洞计分（没有撤回通道；开关只决定新出现的空洞
  接不接受）。摸底相位的记录在 `stage1_baseline/accepted_infra_shortfall.json`
  （live 基线加载、test 相位对照都读它）；其他执行相位按臂记录在相位目录里；
  live 相位按轮记录（轮 1 的远程沙箱是新实例，轮 0 的空洞不自动继承）。

---

## 六、测试系统

### 6.1 三层测试架构

```
tests/
  unit/          单元测试：每个积木的行为契约（106 个文件，1794 个函数）
  composites/    组合测试：多模块协作与崩溃续跑验证
  golden/        回归测试：4 组合端到端矩阵 + 修订窗口探针
  fixtures/      测试数据：轨迹、段、skill、考题样例；golden 矩阵快照
  helpers/       共用工具：插件注册、模型回复样例、skill 构造器
```

**单元测试**（`tests/unit/`）：
- 目录结构与 `src/expedistill/` 对应
- 每个积木（block、method、stage、prompt）至少一个测试文件
- 调模型的用 `llm/fake.py`，不发真实请求
- 测试数据从 `tests/fixtures/` 读取，不直接读 `runs/`

**组合测试**（`tests/composites/`）：
- 用假模型 + 临时 run 目录验证多模块协作
- 必须包含"中途中断再续跑，结果与一次跑完相同"的用例
- 覆盖关键路径：baseline → exam → live → test 全流程

**Golden 矩阵**（`tests/golden/`）：
- **4 个组合**（从 8 个精简）：
  - `exam-whole_bank-full`
  - `exam-retrieval-split`
  - `live_run-whole_bank-split`
  - `live_run-retrieval-full`
- 每个轴（acceptance/delivery/mode）的两个取值各出现 2 次
- 全部 4 个组合都跑崩溃续跑验证（3 档进度：0.3/0.6/0.95）
- `revision_window.py`：保留"修订已落盘、重答未开始"的特殊崩溃点覆盖

### 6.2 契约测试原则

**断言契约，而非捕获字面量**：
- ❌ **不推荐**：硬编码 14 个 sha256 字面量，每次改提示词都要人工同步
- ✅ **推荐**：固定名称集合，digest 值从 `prompts.digest(name)` 重新计算
- **冻结协议表面**：只有真正需要固定的接口（manifest 顶层键集合、prompt 名称集合）才写字面量

**实例**（`tests/unit/stages/test_stage0.py`）：
```python
# 名称集合固定（协议表面）
FROZEN_PROMPT_NAMES = frozenset({
    "EXAM_SCENE", "EXAM_TRIGGER", "EXAM_PLAN", "EXAM_JUDGE", "SEGMENT", ...
})

# digest 值重新计算（不捕获字面量）
def _expected_prompt_digests():
    return {name: prompts.digest(name) for name in FROZEN_PROMPT_NAMES}

# 断言
assert manifest["prompt_digests"] == _expected_prompt_digests()
```

**好处**：
- 改提示词文本，测试不需要人工同步
- 加带默认值的配置字段，测试自动通过
- 测试意图清晰：在测"名称集合固定，值与注册函数一致"

### 6.3 Schema 版本规则

**什么时候升 `CONFIG_SCHEMA_VERSION`**：
- ✅ 改字段含义、删除字段、改类型/必填性/语义
- ❌ 新增可选字段且带默认值，旧代码能忽略未知键、新代码能补全缺省键

**判断标准**：
- **元数据字段**（如 `description: str = ""`）：不影响运行行为，不升版本
- **行为开关**（如 `use_weak_profile: bool = False`）：影响运行结果，升版本
- **可选参数**（如 `max_retries: int = 3`）：旧代码能用默认值正确运行，不升版本

**升级函数**：
- 每个版本的升级函数是纯函数：`upgrade_vN_config(old: dict) -> dict`
- 放在 `src/expedistill/core/config_upgrade.py`
- 必须有测试覆盖：仓库全部历史配置的升级加载往返

### 6.4 如何为新功能添加测试

**步骤 1：单元测试**
1. 在 `tests/unit/` 对应目录创建 `test_<模块名>.py`
2. 用 `llm/fake.py` 构造模型回复，不发真实请求
3. 覆盖正常路径 + 边界情况 + 错误处理

**步骤 2：组合测试**（如果改了多模块协作）
1. 在 `tests/composites/` 创建测试
2. 用 `tests/helpers/` 的工具构造 skill、轨迹、考题
3. 必须包含崩溃续跑用例

**步骤 3：Golden 矩阵**（如果改了端到端行为）
1. 修改提示词或配置后，重新生成 golden 矩阵：
   ```bash
   python tests/golden/generate.py
   ```
2. 检查生成的 4 个组合 + `provenance.json`
3. 运行回归测试确认通过：
   ```bash
   pytest tests/golden -q
   ```

**步骤 4：更新 schema 历史**（如果升了版本）
1. 在 README.md 第四节补充 schema 历史
2. 更新 `config_upgrade.py` 的升级函数
3. 添加升级函数的测试用例

### 6.5 测试数据管理

**测试夹具**（`tests/fixtures/`）：
- `golden/`：4 个组合的完整快照（588 KB）
- `benchmark/`：原始输出录像（TB、ALFWorld、SearchQA）
- 其他：构造好的轨迹、段、skill、考题样例

**不要做的事**：
- ❌ 从 `runs/` 里直接读真实运行数据
- ❌ 在多个测试文件里各复制一份辅助代码
- ❌ 测试文件按开发批次命名（如 `test_w1a_*`）

**应该做的事**：
- ✅ 需要真实样例时，复制一份脱敏后放进 `fixtures/`
- ✅ 共用代码放进 `tests/helpers/`，一处定义
- ✅ 测试文件按它测的源码放进对应目录、按测试内容命名

### 6.6 运行与验收

**本地开发**：
```bash
# 全量测试（约 45 秒）
pytest tests -q

# 只跑单元测试
pytest tests/unit -q

# 只跑 golden 矩阵
pytest tests/golden -q

# 静态检查
ruff check src tests scripts
```

**提交前检查清单**：
- [ ] `pytest tests` 全绿
- [ ] `ruff check src tests scripts` 通过（`E9` 和 `F` 类错误）
- [ ] `pytest tests/test_layering.py` 通过（分层检查）
- [ ] 新功能有对应的单元测试
- [ ] 改了多模块协作有组合测试
- [ ] 改了端到端行为重新生成了 golden 矩阵

---

## 七、运行规则：命令行、运行设置与退出码

### 命令行

```
expedistill run --config <配置文件> --run-dir <run 目录> [运行设置]
expedistill summary --run-dir <run 目录>
expedistill assemble-reuse --source-run <源 run 目录> --config <复用配置> --run-dir <新 run 目录> [--level bank|baseline]
```

- `run` 每完成一个相位打印一行摘要；同一条命令重跑即续跑；
- `summary` 只读进度账本和用量账本（含逐角色逐 prompt 的 token 统计），
  完备性清单从 manifest 冻结的序列推导，不重算任何东西；账本缺相位时
  退出码 1；
- `assemble-reuse` 从已完结的源 run 装配新 run（读旧版本源 manifest 时经
  升级纯函数做身份核对），零模型调用，产出可直接 `run` 的新 run 目录；
  两档位：`bank`（拷贝源 run 的训练侧产物与过滤库：baseline 相位按存储轨迹跳过、
  学习相位零调用复用——末相位的整理在预置库上短路，短路前把产物刷新为
  技能仓当前状态；test 相位全新执行，设计如此；v3 源的生成失败行不拷贝，
  新 run 的首个学习相位会真实重试它们——有模型调用，逐项披露见
  ASSEMBLED_FROM）、`baseline`（只拷摸底轨迹，其余按当前协议全新执行）。

### 运行设置（只影响"怎么跑"，不进配置、不进 manifest）

| 参数 | 默认 | 作用 |
|---|---|---|
| `--workers` | 1 | 只调用 LLM 端点的步骤的并行数：写作、审阅、整理、exam 单元 |
| `--rollout-workers` | 同 `--workers` | 真实 rollout（基准主机上的实际执行）的并行数；两种负载吃的资源不同，所以分开设置 |
| `--llm-infra-retries` | 3 | 单个模型请求遇到基础设施错误时的重发次数 |
| `--llm-infra-backoff` | 5.0 | 重发的指数退避基数（秒），单次等待最多 60 秒 |
| `--llm-timeout` | 300 | 单个请求的客户端超时（秒） |
| `--k1-stagger` | 0 | 每个单元启动前的随机抖动秒数上限（TB 远程建议 20，本地执行用 0） |
| `--allow-infra-shortfall` | 关 | 接受基础设施空洞继续（见 5.9） |
| `--until-phase N` | 无（跑完整个序列） | 跑完冻结步 + 序列第 0..N 项（含 N，N 从 0 数，上界是序列长度减一）就停，冒烟与试跑用。`-1` 只跑冻结步（任务表与 manifest 落盘即停，种子脚本靠它在第一个执行相位派发前预存轨迹）。属运行设置：停下后用同一个 run 目录、更大的 N 重跑同一条命令即继续（已完成的相位零额外成本） |

运行设置续跑时可以改（例如端点不稳时调大重试次数）；非法值在建客户端之前
统一校验。例外：`--allow-infra-shortfall` 接受下来的空洞写进 run 目录，
之后每次续跑都生效。

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
不能只凭退出码 1 判断"是配置问题"，要看 stderr）。

"本 run 内终态"的意思：一个 run 是一次冻结的实验，同一个 run 目录里不再
重试；想换一次采样再试，就开新 run 目录。

### 在基准主机上运行（TB 正式实验）

driver 与 Harbor 放在同一台基准主机上：命令走本机 `bash`、文件走本机复制，不再经过
ssh/scp，driver 所在机器到基准主机的网络波动和 ssh 并发限制就不再影响实验。
run 目录、审计和分析都留在基准主机上，结果不拉回。

- 切换方式：基准主机上的密钥文件里设 `EXPEDISTILL_TB_HOST=local`（空值仍是配置错误）；
- 部署：`scripts/remote/deploy_bench_host.sh`（rsync，一个提交一个目录，从不覆盖）；
- 启动与续跑：`scripts/remote/start_run_bench_host.sh`，`WORKERS` 和 `ROLLOUT_WORKERS`
  分别对应上表两个并行度；
- 完整步骤、目录约定、环境变量和查看方式见 `scripts/remote/README.md`；
  设计取舍见 `docs/decisions/run_on_benchmark_host_20261029.md`。

### 多个请求 ID 指向同一模型

网关按请求 ID 限流时，同一个模型可以用多个请求 ID 访问。环境变量
`EXPEDISTILL_LLM_MODEL_ALIASES=逻辑名=别名1,别名2;…` 让 client 把发往逻辑名的请求
在这些别名之间轮转；某个别名收到 429 后休息 20 秒再参与。只有线上的 `model` 字段变，
逻辑名仍是 manifest、用量账本和已存轨迹里的模型身份。别名是网关的接入信息，只放环境
变量。它只作用于 driver 自己发出的调用；Harbor 在基准主机上的模型请求不经过这个
client，不受影响。

---

## 七、已知风险

1. **技能数量**：一道题的轨迹最多切出 K 段，几十道失败题仍可能产出成百上
   千个候选技能；纸面验收的工作量随之上膨胀。库的大小由整理相位的长度
   预算约束，验收成本需要在 pilot 上实测。
2. **考题质量**：考题出得太像原题 → 弱模型靠记忆也能过；出得太泛 → 验收
   放水。裸答对照能筛掉"太简单"的考题，筛不掉"太泛"的。
3. **考题与任务的距离**：技能过的是文本问答题，终测考的是真实执行。
   "答得对"与"做得到"的差距只能在 test 相位的成绩里看出来。live 方法消掉了
   验收环节的这一差距，但整理的合并回归考试仍是文本考。
4. **live 验收的统计功效**：`n_attempts = 3` 的二值比较在"技能无效果"的
   假设下单任务假通过率约 27–34%（数字按 n 重算，n=1 时约 50%，故设计
   文档把 live 相位的 `n_attempts ≥ 2` 定为推荐下限——总体设计 A2 的
   D1 守卫；代码在加载期只强制 ≥ 1，配 1 不会被拒绝——预检一律纯报告、
   不阻塞流程，统计功效由实验设计纪律保证）。验收是库的入门过滤器，库的
   总体主张由 test 相位的 test 集承载；逐组计数全部落盘，可事后复核。
5. **live 通过 ≠ 可迁移**：live 验收的组来自训练任务，可迁移性看 split
   模式的 test 集。full 模式下 live 验收与终测同任务——任务定制的内容会
   同时抬高验收与终测，这是 full 模式的定义，主张可迁移性时用 split 模式。
6. **逐条修订可能互相重叠**：同组两条技能的独立修订可能产出近义正文；
   重叠由整理相位的相似度合并兜底。

---

## 八、代码来源（provenance）

底座代码（runtime / benchmarks / persistence / reliability / 执行循环）从
姊妹仓库迁移而来：

- 源仓库：`../ExpeToSkill-merged`（SkillExpand 系统）
- 源分支与提交：`TB-eval @ 4f436af`
- 迁移按"仅带依赖闭包"原则拷贝，详见首次迁移提交的说明
- 迁移对应：B1/B2/B4/B5 ← `runtime` + `persistence`；B3 ← `persistence/store`；
  B6 ← `reliability`；E1 与三个 runner ← `benchmarks` + 执行循环簇；
  D2 ← `evaluation/splits`；其余积木为新写。迁移后的落位以 AGENTS.md 的
  目录结构为准
