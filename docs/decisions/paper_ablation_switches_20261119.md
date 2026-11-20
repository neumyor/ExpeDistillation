日期：2026-11-19
一句话：给论文的 ALFWorld 消融实验补上四个学习相位开关（`cards`、`revise_scope`、`evolve_profile`、`trigger_view`）、`judge-skills` 的判定温度和 14 份新配置，使这些运行可以用 `fork-run` 从参考运行 R 复制前缀后接着跑。
对应 log.md：2026-11-19 21:25「实现论文消融开关、配置生成与判定温度」条目

# 论文消融开关

## 背景

论文要在 ALFWorld 上回答六个问题：技能库在 playbook 之上有没有增益（去技能库）、技能只写一次够不够
（Written once）、触发描述和正文哪一侧的修订起作用（Body only / Trigger only）、触发描述用阶段起点
之前的步骤写会不会更好（Prefix authoring）、画像有没有作用（No profile）、切段预算的作用（K=1），
以及上述各臂终测去掉 playbook 以后技能库自己的作用（无 playbook 终测）。运行清单与判读规则写在论文
项目的实验计划（`plans/experiment_plan_20261119.md`）里，本文只记录代码侧的决定。

参考运行 R 的配置是 `alfworld_paper_e1_ours.yaml`，学习相位 p02、p04、p06 共用一个 `learn` 定义。
`fork-run` 要求被复制的相位在目标配置里定义完全相同，所以：

- 在 p02 就改变方法的臂只能复制 p01；
- 只改后两轮学习的臂（Body only、Trigger only）要给后两轮另起定义 `learn_scoped`，p02 才能沿用 R 的 `learn`；
- 新开关取缺省值时不能出现在配置里，R 的定义才不会因为代码增加了字段而变。

决定文档 `evidence_driven_evolution_20261113.md` 3.5 节已经写过 `review.revise_scope` 与 `review.cards`
的设计，本文把它们实现，并加上关闭画像与 prefix authoring。

## 开关定义

| 开关 | 位置 | 取值 | 作用 |
|---|---|---|---|
| `cards` | `learn.review` | `true`（缺省）/ `false` | 学习相位不写、不改、不丢弃任何技能；画像与 playbook 照常演化；证据照常分配、记录；练习与终测看到格式相同的空技能目录 |
| `revise_scope` | `learn.review` | `both`（缺省）/ `trigger_only` / `content_only` | 范围之外的证据照常记录，不触发改写，不计入"相邻两轮正文证据即删除"；缺口证据写新卡不受影响 |
| `evolve_profile` | `learn.review` | `true`（缺省）/ `false` | 不演化画像，画像保持空；写作者读到 `prompts/profile.py` 里的无画像中性段落 |
| `trigger_view` | `learn.author` | `full`（缺省）/ `prefix` | 新卡的触发描述由"阶段起点之前最多 15 步"的视图写出，正文仍由完整段写出 |

配置写法：

```yaml
learn:
  review: {name: review, K: 5, retry_limit: 2, write_lessons: true, cards: false}
learn_scoped:
  review: {name: review, K: 5, retry_limit: 2, write_lessons: true, revise_scope: content_only}
learn:
  author: {name: subtask_cards, ..., trigger_view: prefix}
```

## 实现要点

**配置层。** 相位定义里的 `review` 与 `author` 是原样冻结进 manifest 的映射，参数类（`ReviewParams`、
`CardAuthoringParams`）只在解析时补缺省值，不回写。所以新字段只要不出现在配置里，manifest 就与改动前
逐字节相同，`prompts_digests` 不变（没有新增或修改任何提示词），不需要升 schema 版本。`LearnPhaseSpec`
没有增加字段（`dataclasses.asdict` 会把新字段写进每份冻结的配置）。已有的 46 份可以加载的配置在改动前后的
`ExperimentConfig.to_dict()` 逐一相同（另外两份 `tb21_oldbank_*` 是 schema 7，本来就加载不了）。

校验放在参数类的 `__post_init__` 与冻结步（`stage0_freeze._resolve_phase_methods`）：

- `cards`、`evolve_profile` 必须是布尔值；`revise_scope`、`trigger_view` 必须在取值表里；
- `revise_scope` 用"缺省为空"表示未指定，所以 `cards: false` 与任何 `revise_scope`（包括 `both`）同时出现都报
  ConfigError：关闭技能库后没有改写，范围无从谈起；
- `cards: false` 与 `trigger_view: prefix` 同时出现报 ConfigError（跨 `review` 与 `author` 两个族，所以在冻结步检查）；
- 配置里没有画像文本的字段（画像只由学习相位演化），所以"关闭画像时仍写了画像文本"这类冲突在现有配置结构下
  不可能出现，没有可检查的项。

**`cards: false`。** 学习相位照常诊断、切失败轨迹、归类、分配证据、演化画像与 playbook，跳过第 5 步。摸底里强模型轨迹的切段
只给写卡提供材料，所以这时不切（省下一批教师调用）。每个缺口类型写一条回执 `skipped_cards_off`：完整性检查
（`scripts/audit/audit_run.py` 第 4 项）要求每个有证据的目标都有回执，没有这条回执，`no_skills` 的运行审计不过。
库工件原样发布（空库）。交付侧不需要改动：`folder` 交付对空库本来就物化空目录、单元仍挂清单、用同一个文件夹执行提示词
（`phase_disk.materialize_folder`、`folder_delivery` 文档字符串已写明），本次用整条七相位序列的测试证明了练习与终测
在空库上能跑完。

**`revise_scope`。** 第 3 步照常写 `evidence.jsonl`。第 5 步规划单元之前，`composites/evidence.split_by_scope`
把范围之外那一类证据（`trigger_only` 时的正文证据，`content_only` 时的触发证据）拿出，余下的才交给 `plan_cards`。
所以范围外的证据既不触发改写，也不进删除规则：删除只在本相位范围内有正文证据时才判断，上一个学习相位的证据照常读。
每张卡每侧写一条 `skipped_out_of_scope` 回执（动作 `revise_content` 或 `revise_trigger`），续跑不重复写。
`learn_ledger.commit_order` 的排序键从目标编号改为（目标编号，动作），使同一张卡的回执顺序固定为删除、正文、触发，
不受回执写入先后影响；已有流程里这个顺序本来就是如此，golden 快照不变。

playbook 写作者读到的触发证据计数不受 `revise_scope` 影响（那是"记录"的一部分）：`content_only` 的运行里 playbook
仍会收到触发证据的计数。

**`evolve_profile: false`。** 第 4 步不调 `PROFILE_UPDATE`，写一份文本为空、版本 1 的 `profile.json`（后续相位的读取方
要求每个学习相位都有这个文件）。诊断本来就不读画像；写卡、两种改写、playbook 的写作者读到空文本，
`profile_section` 返回中性段落。

**`trigger_view: prefix`。** 复用 `CARD_WRITE`、`render_steps_view`、`parse_skill_fields`，不新增提示词。
`methods/cards/writer.write_card_prefix` 的流程：

1. 照常调一次 `CARD_WRITE`（完整素材），保留 `steps`、`not_applicable_when`；
2. 对同一批素材段（强模型段与弱模型段）调 `material.prefix_material`：每段的步骤视图换成该段起点之前最多
   `WRITER_BEFORE_STEPS`（15，触发改写的回看步数，`composites/trace.py`）步，步号保持轨迹自己的步号；
   起点为 0 的段没有前缀，在这次调用里省略；
3. 用换了视图的素材再调一次 `CARD_WRITE`，保留 `summary`、`when_to_use`；
4. 两部分合成一张卡。第二次调用的回复同样要过 3–7 步的检查，不合格带原因重写。

没有任何强模型段有前缀（例如 `K: 1`，每条轨迹只有一段，起点都是 0）时不做第二次调用，触发描述沿用第一次的结果，
回执的原因写 `gap; prefix_unavailable_segments_start_at_step_0`。第二次调用失败（输出不合格耗尽）按写卡失败处理，
重试时两次调用都重问（第一次的结果在卡写盘之前不落盘）。

**`judge-skills` 的温度。** 新增 `--temperature`，缺省 0.95，构造判定句柄时传给 `make_handle`；方法侧的句柄（教师）
不变，仍是 1.0。温度写进输出目录的 `analysis_config.json`：同一个输出目录续跑时温度不同会被拒绝，以免一个文件里混有两种
温度的判定。

## 未选方案

| 方案 | 不选的原因 |
|---|---|
| 在 `LearnPhaseSpec` 上加字段 | `phase_spec_to_raw` 用 `dataclasses.asdict`，新字段会写进每份冻结的配置，R 的定义与新代码解析出的定义不再相同，`fork-run` 的相位定义检查失败 |
| 新开关取缺省值时也写进生成的配置 | 已有配置必须逐字节不变（`--check`），并且已启动 run 的配置保持原样 |
| `revise_scope` 缺省写成 `both` 字符串并只在 `cards: false` 时拒绝非 `both` | "同时指定"无法与"缺省"区分，`revise_scope: both` 与 `cards: false` 并存不报错，配置里留下无意义的键 |
| `cards: false` 时仍切强模型轨迹 | 这些切段只给写卡材料用，`cards: false` 不写卡，白耗教师调用 |
| `cards: false` 不写回执，改动审计脚本 | 审计脚本在本次改动范围之外，回执本身也是有用的记录 |
| prefix authoring 新增一个"写触发描述"的提示词 | 会给 manifest 增加一个提示词摘要，并且让本臂与其他臂的写卡提示词不同，混入第二个变量；复用 `CARD_WRITE` 只改输入视图 |
| prefix authoring 的第二次调用失败时退回第一次的触发描述 | 静默退回会让一部分卡不是 prefix 写的却被计入该臂；只有"没有可用前缀"这一种确定的情形才退回，并在回执里写明 |
| 把无 playbook 终测命名为 `test_nopb` | 现有配置与测试都把最后一个相位叫 `test`；`test` 定义本身可以不同，`fork-run` 不比较被复制范围之外的相位 |
| 关闭画像的开关放在顶层 | 画像由学习相位演化，开关属于学习相位定义；放在 `review` 里与 `cards` 一样，随相位定义进 `fork-run` 的检查 |

## 与 fork-run 的关系

`fork-run` 的检查（`stages/fork_run._check_same_prefix`）要求：目标序列以源序列的前 N 个相位开头，这些相位的定义
逐项相同，其余影响它们的键（模型表、采样次数、规则、token 预算、train 与被复制相位用到的 val 划分、模型选项）也相同。
测试（`tests/unit/scripts/test_build_paper_configs.py` 的 `test_fork_run_accepts_every_planned_prefix`）对下表每一行
用磁盘上的两份配置调用同一个检查；另有反向测试，证明多复制一个相位时检查会拒绝。

## 新增配置对照表

均为 ALFWorld，文件名 `alfworld_paper_<实验>_<臂>.yaml`，与 `e1_ours` 的差别由 `--print-index` 计算。

| 实验 | 文件（`alfworld_paper_` 之后） | 用途 | 与 `e1_ours` 的差别 | `fork-run` 来源与相位数 | 要跑的相位 |
|---|---|---|---|---|---|
| E1 | `e1_no_skills` | 去技能库（profile + playbook only） | `learn.review.cards: false` | R，p01 | p02–p07 |
| E2 | `e2_written_once` | 技能只写一次 | 序列 `baseline, learn, test` | R，p01–p02 | 终测 |
| E3 | `e3_body_only` | 只改正文 | 后两轮学习用 `learn_scoped`（`content_only`） | R，p01–p03 | p04–p07 |
| E4 | `e4_trigger_only` | 只改触发描述 | 后两轮学习用 `learn_scoped`（`trigger_only`） | R，p01–p03 | p04–p07 |
| E5 | `e5_prefix_authoring` | 触发描述按起点之前的步骤写 | 序列 `baseline, learn, test`；`author.trigger_view: prefix` | R，p01 | p02、终测 |
| E6 | `e6_no_profile` | 不演化画像 | `learn.review.evolve_profile: false` | R，p01 | p02–p07 |
| E7 | `e7_k1` | 切段预算 K=1 | `learn.review.K: 1` | R，p01 | p02–p07 |
| E8 | `e8_ours_nopb` | R 的无 playbook 终测 | 终测 `playbook` 为 `off` | R，p01–p06 | 终测 |
| E8 | `e8_written_once_nopb` | E2 的无 playbook 终测 | 同上，序列同 E2 | E2 的 run，p01–p02 | 终测 |
| E8 | `e8_body_only_nopb` | E3 的无 playbook 终测 | 同上，序列同 E3 | E3 的 run，p01–p06 | 终测 |
| E8 | `e8_trigger_only_nopb` | E4 的无 playbook 终测 | 同上，序列同 E4 | E4 的 run，p01–p06 | 终测 |
| E8 | `e8_prefix_authoring_nopb` | E5 的无 playbook 终测 | 同上，序列同 E5 | E5 的 run，p01–p02 | 终测 |
| E8 | `e8_no_profile_nopb` | E6 的无 playbook 终测 | 同上，序列同 E6 | E6 的 run，p01–p06 | 终测 |
| E8 | `e8_k1_nopb` | E7 的无 playbook 终测 | 同上，序列同 E7 | E7 的 run，p01–p06 | 终测 |

E1 没有对应的无 playbook 终测（技能库为空，终测去掉 playbook 后与裸跑相同）。

## 风险与读法

- `fork-run` 的检查读源 run 冻结的配置。新配置必须用与 R 相同的生成选项（模型 id、`--explicit-cache-model` 等）
  生成，否则 `model_options`、`models` 等键与 R 不同，检查会拒绝并在复制任何文件之前报错，目标目录保持为空。
  本仓库的测试用 `load_config` 读到的磁盘配置代替 R 冻结的配置，R 的清单与磁盘配置是否一致要在
  R 所在主机上用一次真实的 `fork-run` 确认。
- prefix authoring 的第二次调用里，`CARD_WRITE` 的模板仍把素材称为"强模型怎么做的成功段"，而内容是段之前的步骤；
  段卡片里的标签与描述仍是段本身的。这是复用现有提示词的代价，不改提示词是本次的约束。
- `content_only` 臂的 playbook 仍读触发证据计数：计数属于"记录"，不属于改写范围。
- 判定温度改为 0.95 之前已经用 1.0 写过的判定输出目录不能续跑，要用新目录。
