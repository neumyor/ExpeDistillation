# 决定：方法与代码的整体简化重构

日期：2026-11-12
这份文档是 `simplified` 分支的重构方案：把方法收敛为"分段学习 + 三个演化对象 + 每个 benchmark 一个提示词包"，并删除不再使用的路径。
对应 log.md：2026-11-12 的方法简化方案条目。

## 一、目标与已确认的决定

论文要突出的四项功能：

1. 把参考轨迹切成段，从段里学习和演化技能；
2. 技能分成触发条件（trigger）和正文（content），两部分各自演化；
3. 持有并演化两份全局文本：弱模型画像（profile）记录弱模型自身的弱点，
   全局手册（playbook）记录所有任务通用的做法；
4. 每个 benchmark 有一套可替换的提示词，调整这套提示词就能影响技能、画像、
   手册的撰写和修改，以及执行阶段智能体的行为。

用户在 2026-11-12 确认的四个决定：

| 问题 | 决定 |
|---|---|
| 旧 run 的兼容 | 不兼容。新代码只读新格式，旧 run 不续跑，需要的实验在新代码上重跑 |
| 静态画像文件 | 取消。拆成两部分：环境事实移入 benchmark 的执行协议文本，画像从空文本开始演化 |
| playbook 的生命周期 | 每个学习相位都演化，和 profile 走同一套组件 |
| `REQUIREMENT_SKILL` 新技能路径 | 删除。新技能只从分段产生 |

## 二、现状中与四项功能冲突的地方

以下每一条都在 `submit_version`（`3a6e713`）的代码里核实过，路径相对 `src/expedistill/`。

### 2.1 分段只在第一次学习时使用

- 第一次学习（P02）走"切段 → 触发族分类 → 族内聚类 → 每类写一个技能"
  （`stages/learn_add_cluster.py`、`methods/author/cluster_by_trigger.py`）；
- 之后的学习相位里，新技能来自聚合提示词 `TRIAL_REQUIREMENT_AGGREGATION`
  输出的 `new_skill_requirements`，再由 `REQUIREMENT_SKILL` 写成技能
  （`stages/learn_requirement.py`），完全不经过分段；
- 分段相关代码约 2,000 行，分布在 `blocks/`、`composites/`、`methods/review/`、
  `methods/author/`、`stages/` 五个目录的 10 个文件里，并被包装成 review 族的
  "add 证据"（`methods/review/segmentation.py`、`add_evidence.py`）。

### 2.2 trigger 和 content 只是同一段文本的两个小节

- `core/models.py` 的 `Skill` 只有一个 `content` 字符串和一个 `summary`。
  写作提示词其实已经分字段输出（`when_to_use`、`steps` 列表、`not_applicable_when`，
  见 `blocks/author.py` 的 `SKILL_SECTION_FIELDS`），程序再把它们拼成一段带方括号小节的文本；
  改写提示词 `REWRITE` 和合并提示词 `MERGE` 又输出整段文本，靠 `core/skill_sections.py`
  按方括号切开；
- 改写哪一部分由配置开关 `review.revise_scope`（`both` / `body` / `trigger`）决定，
  对整个相位统一生效；
- 考试诊断提示词 `TRIAL_EXAM_ANALYSIS`（`prompts/trial_review.py`）已经要求模型对被考技能
  判断四种情况：
  1. 没打开，而且它的目录行本应匹配这个场景：触发条件有缺陷；
  2. 没打开，而且它的目录行确实不该匹配：模型选错，或者场景不属于这个技能的阶段；
  3. 打开了，内容也覆盖了判卷指出的问题：模型没照做；
  4. 打开了，但内容没覆盖或者有误导：正文有缺陷。

  这个判断以自由文本 `exam_skill_issue` 输出，代码不解析它，之后由聚合提示词决定
  keep / revise / delete，改写时整张卡片一起重写。

### 2.3 profile、playbook 与执行提示词三者内容交叉

- 画像有两套：
  - 静态文件 `configs/profiles/alfworld_unified_memory_20261103.md`，由 `use_weak_profile`
    开关控制。它的内容其实是"阅读者每一步能看到什么"这一环境事实；
  - 演化画像 `model_profile.json`，只在 `trial_level` 相位由 `PROFILE_UPDATE` 累积。
- 同一个 run 里两者混用：进入 `trial_level` 后，诊断、改写读演化画像，
  它的起点是空文本；而第一次学习的写作、最后的整理（`methods/curation/agentbrew.py`）、
  playbook 写作读的仍是静态画像。44 份配置同时打开了两者。
- 全局通用规则出现在三处：
  - `configs/playbooks/*.md`；
  - TB 的 skill-read 后缀 `TB_TASK_PROMPT_READ`（`benchmarks/tb/skill_read.py`），
    里面除了"怎么选技能"，还有 shell 操作规范、阈值留余量、完成前自检。
    不带 playbook 时插件不上传后缀文件，远端智能体用它内置的同一段默认文本
    （`benchmarks/tb/plugin.py` 的 `_read_suffix_of`）；
  - 静态画像里"每个步骤都必须遵守"的规则。
- playbook 只在摸底之后写一次（`stages/learn_playbook.py` 的 `ensure_playbook`），
  之后不再修改；它只进入技能文件夹类的交付（`folder`、`exam_folder`），
  `whole_bank` 和不带技能的试次都看不到它。

### 2.4 benchmark 相关的提示词分散在四类位置

| 位置 | 内容 |
|---|---|
| `benchmarks/<name>/learning.py` | 学习提示词的词汇槽，每个 benchmark 25 个 |
| `benchmarks/<name>/plugin.py`、`alfworld/protocol.py`、`tb/skill_read.py` | 执行提示词 |
| `benchmarks/<name>/playbook.py`（TB 407 行，ALFWorld 827 行，SearchQA 没有） | playbook 写作提示词、事实提取、必备小节检查 |
| `prompts/profile.py` 的 `PROFILE_ROLES` | 各写作提示词里描述读者的标题行 |

方法层的 26 个提示词都是全局的，benchmark 只能替换其中的词语，不能替换整个提示词。

### 2.5 大量代码服务于当前配置都不使用的选项

在 181 份 schema v7 配置中统计：

| 选项 | 实际取值 |
|---|---|
| `mode` | 全部是 `split` |
| 检验相位 | 全部是 `exam`；`live` 只出现在 v3–v6 的旧配置里 |
| test 交付 | `folder` 140 次、`whole_bank` 40 次；`retrieval`、`group_by_task`、`folder_by_task` 为 0 次 |
| review 决策 | 摸底之后全部是 `skill_level`（108 次），考试之后全部是 `trial_level`（170 次）；`task_level` 为 0 次 |
| 写作方法 | 全部是 `cluster_by_trigger` |
| 比较规则 `compare.rule` | 全部是缺省值；`compare.against` 只用 `external_campaign` 和 `fresh_bare`，按相位复用为 0 次 |
| `rules.task_select` | 全部是 `any_weak_failed`（注册表里有两条） |
| 整理开关 `merge_check` / `drop_low_value` / `similarity_input` / `grouping_input` | 各自只有一个取值 |
| 考试开关 `bare_control` / `leak_audit` | 全部是 `false` |
| `before_steps` / `after_steps` | 只被 live 路径读取 |

`GROUP` 提示词写出的 `Skill.group` 字段，被 `group_by_task` 交付、`whole_bank` 的非逐字渲染
和 `blocks/retrieve.py` 读取；论文矩阵里非逐字的 `whole_bank` 只用于空技能库的 bare 臂，
所以这个字段对论文结果没有作用。

## 三、目标方法

### 3.1 一次实验的流程

```
baseline → learn → exam → learn → exam → learn → test
```

- `baseline`：强、弱模型在训练题上各跑 n 次，不注入技能；
- `learn`：第一次学习的输入是摸底答卷，之后每次学习的输入是上一次考试的答卷；
- `exam`：每个待检验的技能版本有一道场景题，从它的来源段截取；弱模型看到
  playbook 和整个技能目录，选卡片、写计划，裁判模型判卷。每道题作答一次；
- `test`：弱模型做测试题，与外部对照或现场跑的不带技能对照比较。

相位种类只剩四种：`baseline`、`learn`、`exam`、`test`。
论文矩阵里还有两种较短的序列，保持可用：`baseline → learn → exam → test`（e2）
和 `baseline → learn → test`（e4 的 a0 臂）。

### 3.2 三个演化对象与一份固定协议

| 对象 | 内容 | 谁读它 | 何时更新 |
|---|---|---|---|
| 技能 Skill | trigger：`summary` + `when_to_use`；content：`steps` + `not_applicable_when` | 执行者（目录里只看到 trigger，打开后看到全部） | 每个学习相位 |
| 画像 Profile | 弱模型会犯什么错、需要怎样的教法 | 写作者（写技能、改技能、写 playbook 时都读） | 每个学习相位 |
| 手册 Playbook | 每个任务都适用的做法 | 执行者（每一步都能看到） | 每个学习相位 |
| 协议 Protocol（不演化） | 环境事实：执行者每一步能看到什么、可用哪些动作、必须遵守的操作规范 | 写作者和执行者 | 由 benchmark 提示词包固定 |

各部分的边界：

- 协议是环境的一部分，所有执行臂都带，包括摸底、不带技能的对照、不带 playbook 的臂。
  TB skill-read 后缀里的 shell 操作规范、阈值留余量、完成前自检属于协议；
  ALFWorld 静态画像里"阅读者只看到当前观察和最近两次交互"属于协议；
- 画像只描述弱模型；只对某一类情境成立的做法写进技能；对所有任务都成立、
  又不属于环境事实的做法写进 playbook；
- 执行提示词中关于技能目录的部分只说明"如何查看和打开技能"。

### 3.3 学习相位的统一流程

每个学习相位按以下六步执行。第一次学习与之后的学习用同一个流程，
差别写在每一步里。

**第 1 步：诊断。** 对输入答卷中的每条失败记录调用一次诊断提示词，三个提示词分别对应三种答卷：

| 答卷 | 提示词 | 说明 |
|---|---|---|
| 摸底的弱模型失败试次 | `BASELINE_FAILURE_ANALYSIS`（新增） | 没有技能，只产出画像和 playbook 笔记 |
| 考试失败 | `TRIAL_EXAM_ANALYSIS` | 对被考技能给出结构化判定 |
| 终测 | 不诊断 | 终测结果不进入学习 |

考试诊断的输出改成结构化字段：

```json
{
  "reason": "失败原因",
  "exam_skill_defect": "trigger|content|model|scene",
  "exam_skill_issue": "具体问题和证据",
  "model_note": "这次失败说明了弱模型的什么弱点，可为空",
  "playbook_note": "这次失败说明所有任务都缺少的什么做法，可为空"
}
```

`exam_skill_defect` 的四个取值对应 2.2 列出的四种情况：
`trigger` 对应情况 1，`scene` 对应情况 2，`model` 对应情况 3，`content` 对应情况 4。
程序用读取记录约束模型输出：被考技能没被打开时只接受 `trigger` 或 `scene`，
被打开时只接受 `content` 或 `model`，否则按格式错误重试（读取记录由
`composites/skill_use.py` 的 `skill_files_read` 给出）。
只有被考技能自己那场考试的诊断能决定它的动作；诊断里提到的其他技能只作为证据记录。

摸底诊断是新增的调用，数量等于弱模型失败试次数，ALFWorld 训练集约为几十到一百多次。

**第 2 步：更新画像和 playbook。** 两者共用一个外壳 `evolve_text`：

```
evolve_text(kind, old_text, notes, facts, handle) -> new_text
```

| 参数 | 画像 | playbook |
|---|---|---|
| `kind` 选用的提示词 | `PROFILE_UPDATE` | `PLAYBOOK_UPDATE`（新增，由 benchmark 提示词包提供） |
| `old_text` | 上一个学习相位的画像；第一次学习为空 | 上一个学习相位的 playbook；第一次学习为提示词包的 `playbook_seed`（可以为空） |
| `notes` | 本相位所有诊断的 `model_note` | 本相位所有诊断的 `playbook_note` |
| `facts` | 不用 | 只有第一次学习传入：提示词包的 `playbook_facts` 从摸底答卷计算；之后的学习相位为空，因为考试答卷没有执行步骤 |
| 输出检查 | 非空 | 长度上下限 + 提示词包的 `playbook_checks` 必备小节，未通过时带着缺失项重写，最多两次 |

外壳负责：调用模型、解析输出、检查与重写、写盘。`notes` 全部为空且 `facts` 为空时，
不调用模型，原样复制上一版本。每个学习相位都必须写出这两个工件，版本号加一，
写盘在本相位的提交点之前。读取方只读紧邻的上一个学习相位的工件，缺失时报错。

这一步放在改写和写作之前，所以本相位的改写和写作读的是本相位更新后的画像。

**第 3 步：决定技能动作。** 由程序按下表决定，不再调用聚合提示词。
"后面还有考试"指序列里本相位之后还有 `exam` 相位。

| 被考技能本次的结果 | 后面还有考试 | 后面没有考试 |
|---|---|---|
| 考试通过 | 接受 | 接受 |
| 失败，`trigger` | 改 trigger | 删除 |
| 失败，`content` | 改 content | 删除 |
| 失败，`model` | 接受，证据已进入画像 | 接受 |
| 失败，`scene` | 接受，考题标记为无效 | 接受 |
| 诊断两次格式错误或基础设施失败用尽 | 保留待检验，下次续跑重试 | 保留待检验，下次续跑重试 |

规则说明：

- "考试通过"指这道题唯一一次作答被判为成功；
- "接受"把技能状态从 pending 改为 accepted，终测只注入 accepted 的技能；
- 同一相位里同一技能有多个版本被考时，以最高版本那场考试为准（沿用 `merge_exam_rows`）；
- 改写没有产生变化（输出与原字段逐字相同）时，技能按"接受"处理，
  原因记为 `unchanged_after_revision`；
- 这张表与现在聚合提示词的行为一致：通过即接受，技能本身没问题时保留；
  不同之处是 trigger 和 content 分开处理。

`revise_scope` 保留为消融开关，取值 `auto`（缺省，按诊断改）、`trigger_only`、
`content_only`。范围之外的诊断按 `model` 处理，即接受。

**第 4 步：改写技能。** 改 trigger 与改 content 各用一个提示词，每个只输出自己负责的字段：

| 提示词 | 输入 | 输出 |
|---|---|---|
| `REVISE_TRIGGER` | 当前 trigger、考生看到的技能目录、考试场景、考生打开了哪些卡片、诊断、画像 | `summary`、`when_to_use` |
| `REVISE_CONTENT` | 当前全部字段、来源段的真实轨迹、考试场景、考生的计划、判卷理由、诊断、画像 | `steps`、`not_applicable_when` |

一次改写只修改一侧字段，版本号加一，`rewrite_history` 记录被改的旧字段。
改 trigger 后，下一次考试沿用上一版本的场景和期望行为，只重新作答，
因为考题检验的是"这个场景下能不能找到它"，场景本身没有变化的理由；
改 content 后重新出题。

**第 5 步：从段中写新技能。** 只在第一次学习执行。

- 待学习段：按 `trajectory_select` 和 `segment_select` 规则从摸底轨迹中选出的段，
  `write_lessons` 决定弱模型轨迹的失败段是否也进入；
- 待学习段走同一条线：分到触发族、族内聚类、每类写一个技能（`CLUSTER_SKILL`）；
- 写出的技能状态为待检验，由下一次考试检验；后面没有考试时直接接受
  （这取代现在的 `accept_new` 开关，e2 和 e4_a0 两种序列因此仍能得到技能库）。

之后的学习相位不写新技能。原因：新技能只从分段产生（用户决定），
而能切段的轨迹只有摸底轨迹，它们在第一次学习时已经全部切过；
考试答卷是计划，没有执行轨迹，终测结果不进入学习。
之后的学习相位只演化已有技能、画像和 playbook。

这一条改变论文矩阵 e4 的改写范围消融：现在的 `a3_joint` 臂与 `e1_ours` 的唯一区别是
`new_skills: false`，新方法下两者相同，`a3_joint` 臂取消，`a1_body`、`a2_trigger`
直接以 `e1_ours` 为参照，三者只在 `revise_scope` 上不同。

**第 6 步：整理。** 序列里最后一个学习相位做一次 agentbrew 合并（`curate: agentbrew`），
或者不整理（`curate: none`，e2 和 e4_a0 使用）。`MERGE` 提示词改为按四个字段输出。

### 3.4 playbook 的开关

playbook 是一个独立于交付方式的开关，写在执行相位的配置里：

```yaml
playbook: evolved   # 或 off
```

- `evolved`：读紧邻的上一个学习相位的 playbook，注入方式由 benchmark 决定
  （ALFWorld 放在技能目录上方，TB 放在 skill-read 后缀前面）。
  所有交付方式都支持，包括 `whole_bank` 和不带技能的对照；
- `off`：不注入；
- 学习相位总是演化 playbook，开关只控制执行相位是否看到它；
- 考试相位与终测相位分别配置。已知 playbook 会降低技能打开率，
  所以 K 和改写范围的消融把考试和终测都设为 `off`。

终测的四个标准臂：不带技能不带 playbook、只带 playbook、只带技能、技能加 playbook。
外部对照（`external_campaign`）只能对应"不带技能不带 playbook"。

### 3.5 benchmark 提示词包

每个 benchmark 一个文件 `benchmarks/<name>/prompt_pack.py`，导出一个对象：

| 字段 | 内容 |
|---|---|
| `vocabulary` | 学习提示词的词汇槽（现在的 `LearningContext.slots`） |
| `protocol` | 3.2 的协议文本 |
| `executor_prompts` | 执行提示词的注册名：system、首步、后续步、技能目录说明 |
| `playbook_seed` | playbook 的初始文本，可以为空 |
| `playbook_facts` | 从摸底答卷提取统计事实的函数 |
| `playbook_checks` | playbook 必须包含的小节 |
| `overrides` | 方法提示词名到本 benchmark 替换提示词注册名的映射，可以为空 |

接线方式，保持现有分层（第 2 层的 `llm/` 不读第 3 层的对象）：

- 替换提示词和执行提示词都在 `benchmarks/<name>/` 里注册成具名提示词；
- `ModelHandle` 增加一个普通字典字段 `prompt_aliases`；
- `cli.py` 构建模型句柄时（现在有两处），从提示词包取值，填进
  `prompt_defaults`（`vocabulary` 与 `protocol`）和 `prompt_aliases`（`overrides`）；
- `call_model` 取提示词时先查 `prompt_aliases`：`prompts.get(aliases.get(name, name))`；
- stage 0 检查：替换名必须已注册，替换提示词的占位符集合必须与被替换的提示词相同。

冻结沿用现有机制，不新增任何校验值：具名提示词进入现有的 `prompt_digests`；
`vocabulary`、`protocol`、`playbook_seed` 的原文按现在 `learning_context.to_dict()`
的方式写进 manifest；`playbook_facts` 和 `playbook_checks` 是代码，由正式 run 固定的提交覆盖。

### 3.6 删除的内容

| 删除项 | 原因 |
|---|---|
| `live` 相位、`live_measurement`、`live_verdicts`、`skill_usage`、`group_by_task`、`folder_by_task`、`methods/review/attribution.py` 中只服务 live 的部分 | 当前配置不使用 |
| `mode: full` | 当前配置不使用 |
| `retrieval` 交付、`blocks/retrieve.py` | 当前配置不使用；`none`、`whole_bank`、`folder`、`exam_folder` 保留 |
| `task_level`、`skill_level`、`trial_level` 三种决策策略，`TRIAL_REQUIREMENT_AGGREGATION` | 由 3.3 的统一流程替代 |
| `REQUIREMENT_SKILL`、`learn_requirement.py`、`methods/author/requirement.py` | 新技能只从分段产生（用户决定） |
| `SKILL_DISTILL`、`SKILL_DIAGNOSE`、`REWRITE`、`REVISION_SCOPE_BODY`、`REVISION_SCOPE_TRIGGER`、`SKILL_ATTEMPT_ATTRIBUTION`、`SKILL_ATTEMPT_REVISION`、`core/skill_sections.py` | 写作只用 `CLUSTER_SKILL`；改写用 `REVISE_TRIGGER` 和 `REVISE_CONTENT` |
| 静态画像、`use_weak_profile`、`weak_profile`、`weak_profile_file`、`configs/profiles/` | 用户决定改成"协议 + 演化画像" |
| `ensure_playbook`、`playbook_source`、`playbook_file`、`playbook_sha256`、`configs/playbooks/` | playbook 改为逐相位演化；手写文本通过提示词包的 `playbook_seed` 提供 |
| `accept_new` | 由 3.3 第 5 步"后面没有考试时直接接受"替代 |
| `new_skills` | 之后的学习相位不再写新技能，开关没有作用对象 |
| 比较规则注册表、`first_success_gain`、`compare.rule`、按相位复用的对照 | 只用缺省规则；`compare.against` 只保留 `external_campaign` 与 `fresh_bare` |
| `task_select` 注册表 | 只用 `any_weak_failed`，直接写成函数 |
| 整理的四个开关、`GROUP` 提示词、`Skill.group` | 只有一个取值；字段对论文结果没有作用 |
| `bare_control`、`leak_audit`、`LEAK_AUDIT_JUDGE`、`blocks/audit.py`、`composites/acceptance_leak.py` | 当前配置全部关闭；考试固定为不带对照 |
| `before_steps`、`after_steps` | 只被 live 路径读取 |
| `core/config_upgrade.py`、`scripts/run/convert_v3_configs.py`、全部旧配置 | 不兼容旧版本（用户决定）；配置由 `build_paper_configs.py` 重新生成 |
| `assemble-reuse`、`remerge-bank` 子命令 | 方法主体与论文实验都不需要 |
| 只服务 live 或旧流程的脚本：`launch_tb21_live_run.py`、`build_local_live_config.py`、`fork_run_stage04.py`、`seed_tb_stage1.py`、`prepare_inplace_recovery.py`、`audit_live_run.py` | 对应的流程已删除 |

保留并改写的内容：

| 保留项 | 原因 | 需要的改动 |
|---|---|---|
| `fork-run` | 多个臂共用同一个摸底（TB 的摸底很贵），以及"同一个技能库换终测条件"的实验 | 无 |
| `import-bank`、`whole_bank` 的逐字注入 | 论文的六个对比方法通过它们导入 | 导入的外部文档没有四个字段，给 `Skill` 增加 `verbatim_text` 可选字段专门存放导入原文，只由 `import-bank` 写、只由逐字注入读 |
| `paper-tables`、`judge-skills`、`exam-replay`、`segmentation-metrics` 及其依赖（`blocks/adherence.py`、`prompts/adherence.py`、`composites/trigger_metrics.py`、`composites/paired_stats.py`） | 论文的表 3、触发召回与遵循度分析依赖它们 | 读技能文本的地方改为读字段 |
| `trajectory_select`、`segment_select` 具名规则 | `all` 是覆盖度消融的开关 | 无 |
| `write_lessons`、`K` | e2、K 消融使用 | 作为第 5 步的参数直接传入 |
| `external_campaign`、`fresh_bare`、`configs/seed/`、`build_bare_control.py`、`build_pooled_control.py` | 终测对照 | 协议进入执行提示词后，ALFWorld 与 SearchQA 的 bare 对照要在新代码上重跑，种子文件重建 |
| `--allow-infra-shortfall` 与 `composites/shortfall.py`、`composites/external_control.py`、`composites/skill_use.py`、`composites/acceptance_skill_status.py`、`methods/review/attribution.py` 的 `task_instruction_for` | 考试与终测路径使用 | 删除对已删模块的导入 |

在跑的 `tb21_oldbank_nopb_20261112` 与 `tb21_oldbank_pb_20261112` 按 R6 固定在
`submit_version` 的提交上跑完，不迁移到新代码。

## 四、代码结构

| 位置 | 内容 | 来源 |
|---|---|---|
| `core/models.py` | `Skill` 的文本字段改为 `summary: str`、`when_to_use: str`、`steps: List[str]`（不带编号）、`not_applicable_when: str`，四个都必填；可选的 `verbatim_text`；删除 `content`、`group` | 原 `Skill` |
| `core/models.py` | `EvolvingText`：`kind`、`text`、`version`、`phase_label`、`source_sheet_ids` | 原 `model_profile.json` |
| `inject/skill_text.py` | `render_catalog_line(skill)`、`render_card(skill)`：唯一的渲染函数，执行、考试、判卷、`SKILL.md`、整段注入、规则 1 的复用判定都调用它们 | 原 `blocks/author.py` 的 `render_skill_content` 与现有 `skill_text.py` |
| `benchmarks/<name>/prompt_pack.py` | 3.5 的提示词包 | `learning.py`、`skill_read.py`、`playbook.py`、执行提示词 |
| `methods/segment_learn/` | 切段与校验、段筛选、触发族分类、聚类、写技能、待学习段的选取 | `blocks/segment.py`、`blocks/select.py`、`composites/segment_checked.py`、`methods/review/segmentation.py`、`methods/review/add_evidence.py`、`methods/author/cluster_by_trigger.py`、`stages/learn_add_cluster.py` |
| `methods/diagnose/` | 摸底与考试的逐条诊断、读取记录对判定的约束 | `methods/review/trial.py`、`trial_evidence.py`、`exam_evidence.py` |
| `methods/evolve_text/` | `evolve_text` 外壳与画像、playbook 两种配置 | `methods/review/profile.py`、`blocks/playbook.py`、`composites/playbook_store.py`、`stages/model_profile.py` |
| `methods/revise/` | `REVISE_TRIGGER`、`REVISE_CONTENT` 与改写落盘、版本守卫 | `methods/author/revise.py`、`blocks/author.py` 的改写部分、`stages/learn_exam.py` 的 `_land_revision` |
| `composites/decision.py` | 3.3 第 3 步的动作表（纯函数） | 原 `decision.py` 中摸底与考试的部分 |
| `stages/phase_learn.py` | 3.3 的六个步骤，按顺序调用上面几个包 | 原 `phase_learn.py`、`learn_add.py`、`learn_apply.py`、`learn_exam.py`、`learn_ledger.py`、`learn_playbook.py` |

执行相位的引擎（`stages/phase_execute/engine.py`）、答卷与覆盖记录
（`core/answer_sheet.py`、`composites/answer_sheets.py`、`coverage.py`）、
对象存储、manifest 冻结、run 目录锁、用量累加、失败分类和重试都保留，
只删除其中服务于已删除选项的分支。

## 五、实施阶段

每个阶段结束时：全量测试通过、`tests/test_layering.py` 通过、`ruff check` 通过、
设计正本同步更新，主智能体做一次整体静态评审，再开始下一个阶段。

**阶段 1：删除不使用的执行侧路径（1 个子智能体）**

- 范围：只删除与学习流程无关的项目：`live` 相位及其专属模块、`mode: full`、
  `retrieval` 交付、`group_by_task` 与 `folder_by_task`、比较规则注册表与按相位复用的对照、
  `bare_control`、`leak_audit`、`before_steps`/`after_steps`、`assemble-reuse`、`remerge-bank`、
  只服务这些路径的脚本；共用模块按 3.6 的"保留并改写"表处理，先删除保留文件中对被删模块的导入，
  再删除模块；
- 同时：`build_paper_configs.py` 的三份来源配置改为把划分、任务表、外部对照信息写进脚本本身；
  用新文件名重新生成论文矩阵；删除 `config_upgrade.py` 与全部旧配置；
- 测试：golden 矩阵改为直接写 v7 配置，组合换成 `exam-whole_bank-split` 与
  `exam-folder-split` 两个，走 `cluster_by_trigger` 与论文的决策路径，并做崩溃续跑验证；
  golden 用的测试插件声明 `skill_folder_prompt_name`；
- 完成标准：重新生成的论文配置全部通过 stage 0 冻结；golden 两个组合通过。

**阶段 2：数据模型与提示词包（2 个子智能体并行）**

两者的接口在阶段开始前由主智能体写入 `core/models.py` 与
`benchmarks/base.py`：`Skill` 的五个文本字段，`EvolvingText` 的字段，提示词包的七个字段，
`ModelHandle.prompt_aliases`。并行期间不修改这些接口。

- 子智能体 A（技能数据模型）：
  - 负责：`core/`（除接口外）、`inject/`、`blocks/author.py`、`blocks/curate.py`、`blocks/exam.py`、
    `blocks/attribution.py`、`methods/author/`、`methods/delivery/`、`composites/`
    （`skill_materialize`、`curation`、`provenance`、`exam_answering`、`decision.reuse_key`）、
    `stages/learn_exam.py`、`stages/learn_apply.py`、`stages/phase_execute/exam.py`、
    `stages/import_bank.py`、`prompts/curate.py`、`prompts/author.py`、`llm/fake_provider.py`；
  - 目标：所有读写技能文本的地方改读四个字段，经 `inject/skill_text.py` 渲染；
    `MERGE` 与 `REWRITE` 改为按字段输出；版本守卫比较四个字段；
    `rewrite_history` 记录四个旧字段；`fake_provider` 按字段返回。
- 子智能体 B（提示词包）：
  - 负责：`benchmarks/`、`prompts/registry.py`、`prompts/learning_context.py`、`llm/call.py`、
    `cli.py` 中构建模型句柄的两处、`stages/stage0_freeze.py`；
  - 目标：三个 benchmark 各写一个 `prompt_pack.py`；按 3.5 接线；
    TB 的 folder 单元总是上传 skill-read 后缀，后缀由"技能目录说明 + 协议"组成，
    带 playbook 时 playbook 放在前面；不带技能的 TB 试次也注入协议；
    ALFWorld 的协议进入所有执行提示词。
- 完成标准：用假模型跑通现有学习流程（此时三种决策策略还在），
  `REWRITE` 等旧提示词按字段输出后仍能工作。

**阶段 3：统一学习流程（1 个子智能体）**

- 目标：实现 3.3 的六个步骤、3.4 的 playbook 开关、第四节的四个方法包；
  删除三种决策策略、`TRIAL_REQUIREMENT_AGGREGATION`、`REQUIREMENT_SKILL`、
  `REWRITE` 等旧写作提示词、`skill_sections.py`、静态画像、一次性 playbook、`accept_new`；
  `GROUP` 与 `Skill.group` 也在这一阶段删除；
- 完成标准：用假模型跑通论文矩阵中三种序列（7 相位、e2、e4_a0）；
  学习相位的工件包括诊断、动作表、改写记录、新技能、画像、playbook；
  三个崩溃点（诊断中、改写中、写新技能中）续跑后的结果与一次跑完逐位相同。

**阶段 4：真实模型小样本检查（主智能体）**

- 在 ALFWorld 上用 2 道训练题、1 道测试题跑完整的 7 相位序列，考试与终测各跑
  `playbook: off` 和 `playbook: evolved` 两次；
- 检查项：
  - 考试诊断的四个取值都可能出现，并且与读取记录一致；
  - 改 trigger 后 `steps` 与 `not_applicable_when` 逐字节不变，改 content 后
    `summary` 与 `when_to_use` 逐字节不变；
  - 画像和 playbook 每个学习相位都产生新版本；
  - playbook 与协议、技能目录说明之间没有重复的规则；
  - 两种 playbook 设置下的技能打开率；
- 全部通过后，才能在新代码上启动正式实验。

## 六、风险

1. **playbook 降低技能打开率。** 已有测量：带 playbook 时 ALFWorld 学生打开技能的比例
   从 42% 降到 1%–31%，TB 上从约 42% 降到约 5%。playbook 逐相位演化后会变得更长、
   更具体，这个问题可能更严重。`PLAYBOOK_UPDATE` 要求 playbook 只写所有任务通用的做法、
   不写具体步骤；阶段 4 必须测量打开率；
2. **考试受 playbook 影响。** 考试带 playbook 时，诊断要区分技能的问题与 playbook 的问题，
   所以 `TRIAL_EXAM_ANALYSIS` 的输入要包含考生看到的 playbook；
3. **协议进入所有执行臂会改变对照。** ALFWorld、SearchQA 的 bare 对照要重跑。
   TB 的外部对照来自历史 campaign，不带新协议，只能作为参考；
   TB 的论文结论需要现场跑的不带技能对照；
4. **结果不可比。** 新代码的所有结果都不能与旧 run 直接比较，
   论文表格中的数字都要在新代码上重新测量。
