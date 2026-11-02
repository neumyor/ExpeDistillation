日期：2026-10-30
一句话：用模型裁判事后度量技能卡的触发召回、误触发和步骤遵循度，并用 `exam-replay` 让别的答题模型在同一批考题上重答，两者都只读已完结的 run。
对应 log.md：2026-10-30 20:12「论文实验代码」条目

## 背景

论文的第二个主张是：渐进式披露下，强模型写的触发条件在弱模型上常常失效。要支持它，需要两种此前产不出的证据：

1. 一个 run 里弱模型怎么用卡片清单：该用的卡有没有打开（触发召回），无关的卡有没有被打开（误触发），打开之后卡里的步骤做了没有（遵循度）。
2. 不同答题模型在同一份技能库、同一批考题上的表现（实验二的纸面考试矩阵、实验四 a）。

两者都在 run 结束之后做，不改任何已有执行路径，不写 run 目录。

## 指标定义（事先登记，不再增加备选口径）

输入是逐 trial 的记录：交付了哪些卡、实际打开了哪些卡、适用性裁判对每张已交付卡的判断、遵循度裁判对每张已打开卡的逐步状态、trial 的成败。实现和定义的唯一出处是 `composites/trigger_metrics.py` 的 docstring，本文只复述。

| 指标 | 定义 |
|---|---|
| 触发召回 `trigger_recall` | 已打开且适用的（trial, 卡）对数 / 适用的（trial, 卡）对数。按对池化，不先在 trial 内求比再平均；没有适用卡的 trial 自然不进分子分母 |
| 误触发率 `misfire_rate` | 已打开且不适用的（trial, 卡）对数 / 已打开的（trial, 卡）对数。同样按对池化 |
| 打开率 `open_rate` | 至少打开了一张卡的 trial 数 / trial 数（按 trial，不按卡） |
| 遵循度 `adherence_rate` | 对每个已打开的（trial, 卡）对，卡内逐步打分后取均值：followed = 1，partial = 0.5，skipped = 0，not_reached 不计入该卡的均值；再对所有有可计分步骤的卡求均值。全部步骤都是 not_reached（或卡没有编号步骤）的卡不计分，`cards_opened` 与 `cards_scored` 分别给出打开数和计分数 |
| 分组成功率 `success_by_group` | trial 分三组：`applicable_opened`（至少一张适用卡被打开）、`applicable_not_opened`（有卡适用但适用的卡一张都没打开）、`nothing_applicable`（没有卡适用）。各组给出 `n`、`n_known`（成败已知的 trial 数）、`successes`、`rate`；成败未知的 trial 计入 `n`，不计入 rate 的分母 |

每个指标同时返回分子和分母，分母为零时值为 None，不写 0。

两个口径选择写在这里，因为任务描述的措辞可以有不同读法：

- 召回与误触发按（trial, 卡）对池化，而不是先在每个 trial 内求比再平均。池化让适用卡多的 trial 权重更大。分子分母都在输出里，想换口径重算不需要重新调裁判。
- 打开率取"至少打开一张"的 trial 占比，而不是"打开数 / 交付数"：库有几十张卡、一次最多打开 3 张时，后者天然很小，不好读。

`exam-replay` 的聚合（`replay_metrics`）：

| 指标 | 定义 |
|---|---|
| `recall` | 目标技能被打开的考题数 / 作答成功的考题数 |
| `misfire` | 每道考题平均打开的非目标卡数 |
| `plan_pass_rate` | 计划被判通过的考题数 / 作答成功的考题数 |
| `pass_given_target_opened` / `pass_given_target_not_opened` | 在"目标被打开 / 没被打开"的考题里的通过率 |

## 裁判看到什么

**适用性裁判**（`SKILL_APPLICABILITY_JUDGE`，`blocks/adherence.py::judge_applicability`），每个 with-bank trial 一次调用：

- 任务说明：benchmark 插件对该任务的提示词（与学习阶段取任务说明是同一个函数，`attribution.task_instruction_for`）；
- 卡片清单：每张已交付卡的技能编号和描述。描述用 `inject.skill_text.description_text`，与物化出的 `SKILL.md` 头里的 `description` 是同一函数、同一描述模式，所以与执行者在 `<available_skills>` 里看到的描述一致；
- 整条轨迹：D5 渲染（`blocks.view.render_steps_view`），观察按该 benchmark 的截断规则截断；
- 不给 trial 的成败，也不给技能正文。提示词要求"从轨迹判断触发条件是否出现过，不看是否打开、是否遵循、结局如何"，并让裁判对每张卡给出 `applicable`、`first_step`（只记录，不进指标）和一句理由。

输出严格校验：卡片编号必须在清单内、不重复、不遗漏；`applicable` 必须是布尔值；`first_step` 是 null 或轨迹内的步号；不适用的卡 `first_step` 一律记 null。不合格抛 `ModelOutputError`。

**遵循度裁判**（`SKILL_ADHERENCE_JUDGE`，`judge_adherence`），每张被打开的卡一次调用：

- 卡片全文（库里该技能的正文）；
- 从该卡首次被读取的那一步起的轨迹（步号保持整条轨迹里的编号）；
- 对卡的 `[Steps]` 里每个编号项给 `followed` / `partial` / `skipped` / `not_reached` 之一和一行证据。步骤数由程序从卡正文解析（`card_steps`），回复必须恰好覆盖每个编号一次。没有编号步骤的卡不调用裁判，记空列表，不计入遵循度均值。

读取事实来自 `skill_use.skill_read_events`（TB 的 `cat <skill>/SKILL.md`，本地 benchmark 的 `ReadSkill[...]`），与系统其余部分同一套，只取每张卡的首次读取。

两个 prompt 的措辞与 benchmark 无关（"the agent / actions / observations"），所有基准共用。

## 数据来源与只读保证

`judge_phase(run_dir, phase_label, out_dir, judge_handle, ...)` 的输入：

- trial：该相位 `answer_sheets/` 里 `arm == "with_bank"` 的轨迹类答卷；
- 交付清单：答卷 `artifacts["skill_folder"]`（live 相位有）；test 相位的答卷不带清单，就取同相位 `units.jsonl` 里同一 `unit_id` 那一行的 `aux["skill_folder"]`。后者在真实 TB test 相位目录上核对过；
- 卡片正文：该相位收到的库，即最近一个更早的、留有 `skill_bank.jsonl` 的相位的产物（与执行相位自己找库的规则一致），也可用 `bank_phase_label` 指定。清单里的版本号必须与库里一致，否则报配置错误。

`replay(source_run_dir, out_dir, answerer_handle, judge_handle, ...)` 的输入是源 run 的 manifest（配置）、库产物和 `stage3_generation/exams/`。

两个入口都不写 run 目录：输出目录在 run 目录之内会被拒绝；`replay` 直接读考题文件而不是构造 `Store`（`Store` 会建目录）；新对象（拷入的考题与技能、答卷）写进新建的 `Store(out_dir/"store")`。测试逐字节比对调用前后的 run 目录。

两个入口都在输出目录写 `analysis_config.json`，固定 run、相位 / 库、答题或裁判模型名等；换设置续跑会被拒绝，避免两个模型的答卷混进同一个文件。

## 续跑

- `judge_phase`：每个 trial 的所有调用都成功后才追加一行到 `judgments.jsonl`；续跑跳过已有行的 trial。`judge-skills` 的聚合写入 `trigger_metrics.json`。
- `replay`：每道考题完成就追加一行到 `replay.jsonl`；已判卷的作答在 store 里按完整键（考题、目的、技能、版本）复用，所以"答完、判卷前崩溃"的续跑只重新判卷，不重新问答题模型。聚合写入 `replay_metrics.json`。
- 回复格式错误：裁判回复不合格时重采一次，仍不合格则该 trial 记入返回值的 `failed_trials`、不落盘，续跑时再试。提供方拒绝（典型情形是轨迹超过裁判窗口）同样只让该 trial 失败。基础设施错误不影响其他 trial，调用结束时抛 `InfrastructureError`，续跑接着做。`replay` 里格式错误对该考题是终态（行里 `status: model_output_error`，计入 `failed`，不进比率），与 exam 相位的做法一致。

## 成本

- `judge-skills`：每个 with-bank trial 一次适用性调用，外加每张被打开的卡一次遵循度调用。适用性调用读整条轨迹，是成本大头。对一个现成的 TB test 相位做的只读干跑（脚本裁判，用来估调用数，不是实验结果）：116 个 with-bank trial，共 238 次调用，即 116 次适用性加 122 次遵循度。
- `exam-replay`：每道考题一次选卡、一次写计划、一次判卷，共 3 次调用，输入很短。

## exam-replay 的选题规则

- 库是 `bank_phase_label` 的产物，默认最后一个学习相位的产物。考题只在 `(skill_id, version)` 恰是该库成员时才重答。同一技能的老版本考题描述的是库里已不存在的文字，不重答；不在库里的技能也不重答。两类跳过各自计数，写进返回值的 `skipped`。
- 论文矩阵里要比较答题模型时，建议显式指定考试当时所对着的那个库（例如 `p04:learn`）。默认的"最后一个学习相位"往往是最末的 `learn_trial`，它之后没有考试，而且末相位整理会合并技能，合并产物没有考题，可重答的考题会很少。
- 卡片清单与 exam 相位一致：`exam_folder.resolve` 作用于整个产物（待定成员也在内，`accepted_only=False`），描述模式取源配置里紧跟该库之后的那个 exam 相位的 `delivery.description_mode`（没有则取序列里最后一个 exam 相位）。
- `max_skill_reads` 是参数，默认 3，不自动取源配置的值。要与原考试可比，调用方应传源 exam 相位用的值；实际用的值写进返回值。
- 源 run 的考题必须是当前代码读得懂的 schema（v4，provenance 之后产生）。旧 run 的 v3 考题会明确报错，不静默读成默认值。本机现有的 run 都是 v3 考题，所以这条路径只用测试夹具验证过，没有在真实 run 上跑过。

## 已知局限

- 裁判是模型。适用性从轨迹判断，不是从"技能有没有被用"判断，但裁判看得到轨迹里的读取步骤：读取的观察里含卡片正文，可能让它倾向于把这张卡判为适用。提示词要求忽略读取步骤本身，不能保证做到。论文引用这些指标之前，应抽样人工核对裁判，这一步还没有做。
- 一次最多打开 3 张卡（本地 benchmark 的协议常量）：一个 trial 里适用卡多于 3 张时，召回的上限小于 1。TB 的读取没有次数上限，读取占一步，本地的读取免费。所以不跨 benchmark 比较绝对召回，只在同一 benchmark 内比较各臂（与 `local_skill_folder_20261030.md` 同一结论）。
- 超出裁判窗口的轨迹会让该 trial 失败（记入 `failed_trials`），不会被自动截短。`failed_trials` 非空时，返回的聚合只覆盖已判的 trial，使用数字前先看 `expected_trials` 与 `judged_trials` 是否相等。
- TB 存储的轨迹不含初始提示，任务说明来自插件；插件在分析机器上不可用时，调用方必须显式传 `view_rules` 与 `instruction_for`。
- 遵循度只评每张卡的首次读取；重复读取不单独评。裁判只看动作与结果，不记录没有说出口的意图。
- 两个 prompt 是新增的名字，会改变所有新 run 的 manifest 名称集合；一旦用于正式分析，措辞不再修改。

## 新增 prompt 名

`SKILL_APPLICABILITY_JUDGE`、`SKILL_ADHERENCE_JUDGE`（`prompts/adherence.py`）。

## 文件与验证

- 代码：`prompts/adherence.py`、`blocks/adherence.py`、`composites/trigger_metrics.py`、`stages/judge_skills.py`、`stages/exam_replay.py`、`stages/analysis_common.py`（输出目录检查与固定设置、库产物查找，两个入口共用）。
- 测试：`tests/unit/prompts/test_adherence_prompts.py`、`tests/unit/blocks/test_adherence.py`、`tests/unit/composites/test_trigger_metrics.py`、`tests/unit/stages/test_judge_skills.py`、`tests/unit/stages/test_exam_replay.py`，夹具 `tests/helpers/skill_use_runs.py`。覆盖校验分支、手算的指标、续跑（中途崩溃后与一次跑完逐行相同）、run 目录只读、设置变更被拒。
- 干跑：`judge_phase` 在一个现成的 TB test 相位目录上用脚本裁判跑通（读答卷、`units.jsonl` 清单、库产物），目录的文件列表、修改时间和大小前后一致。
