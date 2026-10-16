日期：2026-10-16
一句话说明：Wave 3 四代理（W3-S 切段接线 6b9c93f、W3-C 文档与转换器 59d3dc3、W3-A 装配器迁 v4 2882623、W3-B 审计与运行脚本 b916a6f）加收尾同步（740105e）在 `fd64e40..740105e` 的合并结果的正确性静态评审 A 路：四块工作逐点核对通过、无阻断，跨代理咬合（装配 run 首个学习相位的 add 行 × 切段接线）静态推演成立，但该咬合路径在装配语境下零测试覆盖；另发现 2 条偏移（装配"下游零调用"的表述过强、generation_failures 不拷贝会改变装配 run 的库内容）与 5 条细节。
对应 log.md 条目：2026-10-16 23:16（W3-S）、23:17（W3-C）、23:22（W3-A）、23:16（W3-B）四条 Wave 3 条目。

评审对象：`src/expedistill/`、`scripts/`、`tests/`、`configs/experiments/*_v4.yaml`、`README.md`、`docs/runbooks/live_run_acceptance_runbook_20261014.md`、`log.md` 在 `fd64e40..HEAD`（feature/multi-method，HEAD 740105e）的合并结果。
基准：docs/decisions/retrieval_delivery_tb_split_20261013.md（裁决 1/4/5 与修订三）、docs/decisions/execute_learn_module_boundaries_20261016.md（§2.5/§4.2）、Wave 2 评审报告、旧世界实现 `git show c6a9fb8:src/expedistill/stages/{stage2_segmentation,stage3_generation}.py`、四个提交的说明与 log.md 对应条目。
方法：纯静态评审。不运行任何测试、不执行任何脚本；逐段阅读合并后的 segmentation.py / learn_tasks.py / add_path.py / assemble_reuse.py / phase_learn.py / decision.py / curation.py / phase_bank.py / phase_execute 包 / audit_live_run.py / cli.py / convert_v3_configs.py / upgrade_v3_config，与旧 stage2 逐点对照；两份 *_v4.yaml 逐键对 upgrade_v3_config 的输出；README 抽 5 处对代码。测试绿数以提交说明自报为准，本评审未复跑。

---

## 总体结论

**通过（无阻断）。** 四块工作各自兑现了提交说明的承诺，且自报的偏离清单与代码事实一致：

- **W3-S**：`segment_add_material` 与旧 stage2 在轨迹选择规则调用契约（逐任务、双角色非空集、0 步轨迹在规则之前排除并写一次性审计）、处理顺序（按轨迹 ID 排序）、审计通道（同一文件 `stage2_segments/segmentation_audit.jsonl`）、K2 持有持久化与续跑语义上逐点等价；自报的三处偏离（只切强角色、串行、无强材料记 `skipped_no_material` 回执而不是静默）都有明确的设计理由且是全部偏离。
- **W3-A**：v2/v3/v4 三种源的读取分派、两侧配置经 from_dict→to_dict 规整后的同一性比对、末个学习相位预置库使整理短路的机制、以及身份拒绝面（方法键/模型/规则/画像/benchmark/相位改名/exam↔live 换方法）都有测试钉住，拒绝面没有缩水。
- **W3-B**：schema 分派方向正确（v4 走新路径、旧 run 走原样保留的旧路径），四个新检查逐项对上了相位驱动的写盘事实（phase.json 四字段、units/report/redispatch 三件、学习相位三账本与 learn_summary.json 的 bank 提交点、终测双臂 store 与 complete 键），`--until-phase -1` 的三处帮助文本与实现一致。
- **W3-C**：抽查的两份转换配置逐键对上纯函数输出；README 抽查的 5 处（相位字段表、pNN 标签规则、4.3 目录布局、5.6 的 12 种回执与切段位置、v3→v4 身份变化）与代码一致。

跨代理咬合（评审重点 5）静态推演成立但有缺口：装配 run 的首个学习相位**会**对每个弱臂失败的任务发 add 行（决策的库输入是"上一个学习相位的工件"，首个学习相位恒为空库——`phase_learn._previous_bank`，README 5.6 也如实记载），因此每行都会调用切段接线；零模型调用成立靠的是"装配把 train 侧 subtask 与技能整拷 + K2 按存量跳过 + 段选规则筛后技能已存在 → `skipped_existing` 回执"这条条件链。而 W3-A 的零调用续跑测试里弱臂全部判对（夹具 `_traj` 默认 `correctness=True`），首个学习相位根本没有 add 行——这条咬合路径在装配语境下零测试覆盖（发现 1）。真实 TB 复用实验的源 run 必然有弱失败任务，装配后的 run 一定会走到它。

发现计数：阻断 0、偏移 2、细节 5，共 7 条。

---

## 逐项发现

### 【偏移 P1】W3-A 的"零调用续跑"测试没有覆盖装配 run 首个学习相位的 add 行——W3-S 接线在这条路径上的零调用保证只靠静态推演

- **add 行必然出现**：`stages/phase_learn.py` 的 `_previous_bank`（300–312 行）返回"上一个 learn 相位的工件"，序列里没有更早的 learn 相位时返回空列表。装配只预置**末个** learn 相位的工件（`assemble_reuse.py` 的 `_last_learn_label` + 预置写入），首个 learn 相位看到的库恒为空。`composites/decision.py` 的 `_baseline_rows`（620–656 行）按"弱臂失败 ∧ 库里没有该任务的技能"发 add 行——空库之下，D 报告里每个弱臂失败的 train 任务都发一条。
- **每条 add 行都会调切段**：`learn_tasks._land_add`（302–311 行）先调 `segmentation.segment_add_material` 再收证据，不做任何"技能已存在"的前置短路。
- **零调用为什么仍然成立**：装配按 train 列表整拷了 subtask（`_copy_kind(source_run, run_dir, "subtasks", kept_subtasks, …)`）与技能（`_write_migrated_kind(skills)`），且源 run 是 v3 时旧 stage2 对失败集里每个任务都已把 `trajectory_select` 选中的强轨迹切完（规则被同一性比对钉住、轨迹逐字节拷贝，选择结果相同），所以 K2 的"存量 subtask 即完成"守卫直接命中，零次切段模型调用；随后 `collect_add_evidence` 找到强段、`fresh` 过滤发现技能都已存在 → `skipped_existing` 回执，零次写作调用。K2 耗尽审计与 0 步空轨迹审计整文件拷贝（装配器明文豁免），耗尽与空轨迹也不会重新触发调用。
- **测试缺口**：`tests/unit/stages/test_assemble_reuse.py::test_assembled_run_resumes_under_the_v4_pipeline_with_zero_model_calls`（1096–1163 行）用拒答假客户端从 p00 跑到末个 learn 相位——但夹具的弱臂轨迹 `correctness=True`（`_traj`，333–345 行），D 报告里没有弱失败，首个学习相位的任务清单是空的，add 行、切段接线、`skipped_existing` 回执一条都没执行。W3-S 自己的相位级测试（`test_w3s_segmentation_wiring.py`）覆盖了 add 行，但不在装配语境（无预置库、无拷贝件）。
- **风险**：低——静态推演的每一环都有独立代码与测试支撑（K2 存量跳过、`fresh` 过滤、NoAddMaterial 回执），但"装配 run 首 L 零调用"这一合并性质没有任何一个测试钉住，两条接线的任何一个未来改动（比如 `fresh` 过滤前移、K2 守卫改键）都可能悄悄打破它而不转红。
- **建议**：给零调用续跑测试的夹具加一个弱臂失败的 train 任务（技能已拷贝），断言首 L 落 `skipped_existing` 回执、切段与写作零调用、预置库逐字节不变。

### 【偏移 P2】generation_failures 不拷贝 + README"bank 档下游零调用"表述过强：装配 run 的库可能长出源 run 从未有过的技能

- **裁决本身自洽**：v4 的学习相位对失败 add 的口径是"记 ModelOutputError、下个学习相位重试"（`execute_rows` 的 except 分支），全仓确实没有 generation_failures.jsonl 的 v4 读者；装配器按 train 侧行数在摘要与 ASSEMBLED_FROM 里披露"这些单元将由首个学习相位重新尝试（真实模型调用）"，不是静默丢弃（`assemble_reuse.py` 543–555、911–916 行）。裁决与"装配语义不变"的冲突是**有意为之且留痕的**——但它确实改变了装配 run 的下游行为：
  - v3 世界该账本随装配拷贝、装配 run 的 stage3 按终态跳过这些单元（`git show c6a9fb8:stages/stage3_generation.py` 的账本语义：记入即本 run 终态、列入 `infra_failed` 跳过）；
  - v4 装配 run 会真实重试它们，重试成功则库里多出源 run 从未产出过的技能，C 相位测的库与源 run 不再严格同库——对"同一份库、换交付方式对比"这一复用实验的目的而言是潜在混杂。同理，被全链溯源过滤掉技能且无其他技能覆盖的任务，首个学习相位的 add 行也会触发真实写作调用（旧 v3 装配 run 的 stage3 同样会重写它们，这一半与旧世界行为一致）。
- **README 表述过强**（936–939 行）："两档位：`bank`（拷贝源 run 的训练侧产物与过滤库，**下游零调用**）、`baseline`（……）"。旧 README（fd64e40:983）写的是"装配**阶段 0–5** 产物与过滤库"，把 stage6 明确排除在外；新表述至少有两处理由不成立：(1) C 相位按设计全新执行（真实模型调用，runbook 0.5 与 ASSEMBLED_FROM 都这么说）；(2) 上面的 generation_failures 重试路径。
- **建议**：README 该句改为与 ASSEMBLED_FROM 相同的口径（"D 与学习相位零调用复用；C 相位全新执行；源 run 的 v3 阶段-3 生成失败行会被重新尝试，见 ASSEMBLED_FROM 披露"）。对复用实验本身：启动前核对源 run 的 generation_failures train 侧行数，非零时在 log.md 条目里显式记录其对库同构性的影响。

### 【细节 D1】终测相位 compare 进白名单是一处有意的扩大，留痕齐全，但"compare 与 mode 不再绑定"这一后果没有成文说明

- `_allowed_diff_prefixes`（`assemble_reuse.py` 422–435 行）把 v3 的"delivery 槽"换成终测相位的 `delivery` 与 `compare` 两个映射。v3 没有独立对照臂旋钮（由 mode 隐式决定，而 mode 本来就在白名单里），v4 的加载期检查清单（core/config.py 499–604 行）只要求 `compare.against` 指向更早的 baseline 相位或 `fresh_bare`，**不强制它与 mode 一致**——所以目标配置现在可以在白名单内把终测对照臂从 `fresh_bare` 换成指向 D（或反之）。这在裁决 4 的意图内（"只作用于终测的旋钮"），且每次装配的 allowed_diffs 在 ASSEMBLED_FROM 里留痕；评审重点问的拒绝面（方法键、模型、规则、benchmark、相位改名）经逐一核对**没有缩水**：`test_identity_diff_rejects_a_changed_method_name/model/rule/weak_profile/benchmark/a_renamed_test_phase` 与 exam↔live 换方法拒绝全部在场，相位改名会同时动 `config.sequence`（不在白名单）而被拒。仅建议在 README 或决策文档补一句"compare 允许随终测相位配置变化，含对照臂取法"。
- 另一个邻接小点：白名单按**目标**配置的终测相位局部名推导前缀，源与目标的终测相位名不同时，`config.phases.<源名>` 的删除差异不在前缀内、必然拒绝——行为正确，无需改动。

### 【细节 D2】W3-B 的模式判型顺序：代码 live 优先，提交说明与 log 写成 exam 优先

- 代码（`scripts/audit/audit_live_run.py` 1231–1238 行）：`if PHASE_ROLE_LIVE in roles: mode = "live_run" elif PHASE_ROLE_EXAM in roles: mode = "exam"`——live 优先。
- 提交说明与 log.md 写的是"序列含 **exam** 相位即 exam 方式，**否则**含 live 相位即 live_run 方式"——exam 优先。两者只在"序列同时含 A 相位与 B 相位"时分岔；升级映射产生的序列不会混合（exam 序列无 B、live 序列无 A），加载期检查清单也不禁止手写混合序列，所以现网无行为风险。`test_w3b_audit_v4` 的模式判型测试只把 live 相位翻转成 exam（原本无 A 相位），没有钉住混合序列的优先级。建议修 log/提交说明的描述（log 条目已写不改，可另追加更正），或在代码里加一行注释说明取向。

### 【细节 D3】W3-S 与旧 stage2 的两个次级差异没有出现在自报偏离清单里（均已核对为可接受）

- **infra 失败的账本粒度**：旧 stage2 以轨迹为单元记 `failed_units.jsonl`（unit id = 轨迹 ID）并在批末抛错；新路径经 `execute_rows` 以任务为单元记录（unit id = task、stage = 相位标签），相位末抛错。中断后续跑语义等价（决策是磁盘报告的纯函数、K2 按存量跳过），但审计口径从"每条轨迹"变成"每个任务"。
- **stage2_summary.json 不再写**：旧 stage2 每次调用写运行级摘要（segmented/skipped/total_subtasks/infra_failed/empty_skipped）；新驱动的返回值在 `_land_add` 里被丢弃，相位世界没有对应产物。v4 审计不读它、README 4.3 布局也不再列它，一致；但"每个 run 有切段摘要"这一旧世界的可读性产物就此消失，值得知情。

### 【细节 D4】runbook 0.5 节插入时把"## 0. 适用范围与身份声明"标题写重了两遍

`docs/runbooks/live_run_acceptance_runbook_20261014.md` 44 行与 46 行是同一个二级标题（59d3dc3 插入 0.5 节时把新标题一并插入，旧标题未删）。纯排版问题；0.5 节的换算表本身逐行核对与代码一致（`--until-phase`、verdicts/attribution 账本接替、B 相位 execution/compare 拆分、轮次-相位对应），与 README 无实质矛盾，runbook 交叉引用的"README 第六节"（运行规则）也对得上。

### 【细节 D5】`_assert_phase_tree` 只数文件不数空目录

`assemble_reuse.py` 773–783 行用 `rglob("*") if path.is_file()` 收集 phases/ 下的实际条目，一个多余的**空目录**（比如手误建了 `phases/p03_A/`）不会被断言抓住。现网危害接近零（装配器自己不会建，脏目录通常带文件会先被负清单/树断言抓住），记录备查。

---

## 核对通过清单

**W3-S 切段接线（对旧 stage2 逐点）**

- [x] 规则调用契约：`_task_trajectories` 给规则的是单任务双角色非空轨迹集（排序），与旧 `_failed_trajectories_by_task` 的每任务形状一致；0 步轨迹在规则之前排除；规则选择后的处理顺序按轨迹 ID 排序。
- [x] 一次性空轨迹审计：同一通道（经 `segment_checked.AUDIT_PATH` 导出，替代旧 stage 私有常量复制）、同一条目形状（status=skipped, reason="empty trajectory"）、同样的 already 去重；先记账后调用。
- [x] K2 持有：持久化、按存量 subtask 续跑、D7 校验、重切预算、耗尽 run 内终态审计全部走 `segment_checked`，参数（K/retry_limit）来自 `(evidence, segment)` 注册表条目（`SegmentationParams`，验证齐全）。
- [x] 只切强角色是**唯一**的行为收窄：`collect_add_evidence` 只读 `source == "strong"` 的 subtask（与旧 stage3 的 distill 侧一致），弱半确实无相位世界消费者；规则看到的双角色输入集与旧世界相同。
- [x] `NoAddMaterial` 是 ValueError 子类（旧契约仍可断言）；`_land_add` 记 `skipped_no_material` 回执而不是抛错——K2 耗尽是 run 内终态，抛错会让续跑在同一行上永久卡死，回执方案成立且该行在 outcomes.jsonl 里 skill_id=None，与 W3-B 审计的 learn-ledgers 检查兼容（无技能的行跳过对账）。
- [x] 惰性由结构保证：`_land_add` 只被 add 行触达，add 行只来自摸底缺口决策路径；`test_a_learn_phase_without_add_rows_never_segments` 钉住。
- [x] W2-D 端到端测试去掉手工播种后，下游断言（ID 链、考试、工件）不变；W2-B 三个 add 路径测试加空脚本切段句柄兼作零调用断言。

**W3-A 装配器**

- [x] 源分派：v2→upgrade_v2_config→upgrade_v3_config、v3→upgrade_v3_config、v4 原样；库读取 v4 从末个 learn 相位工件、v3 从 run 根，缺库响亮拒绝；两侧 from_dict→to_dict 规整后比对（升级补全的 v3 配置与手写 v4 配置同构）。
- [x] 预置相位库短路：预置件落在末个 learn 相位标签（与相位循环将用的标签同源），K5 dest 模式"非空库短路一切模型调用"（composites/curation.py 文档句 + 实现），且接受集完整性断言保证 store 的已接受集被预置库覆盖（短路不被 stale 检查否决）；首个 A 相位的交付走 `phase_disk._bank_skills` 的 run 根回退（预置件相位序号在当前相位之后被正确跳过）。phases/ 树精确形状断言（只允许这一个文件）。
- [x] 身份拒绝面：方法键/双 executor 模型/规则名/weak_profile/benchmark/终测相位改名/exam↔live 换目标全部有拒绝测试；tasks_digest、sequence、全部相位定义、预算、model_options 均在比对面内；prompt digest 按混装协议披露不拒绝。
- [x] 装配幂等（重装配不重复、原子整写）、run 根既有 manifest 拒绝、v1 源对象迁移、全链 train 溯源与 bank/skill 双调用一致断言、baseline 档位钉住键（benchmark/双模型/双 n_attempts/train ⊆ 源任务集）——均有测试。
- [x] 46 例测试含 v3 源等价装配、待定成员披露、脏 phases/ 拒绝；主分支 36 残留红清零的口径与提交说明一致（本评审未复跑）。

**W3-B 审计与运行脚本**

- [x] schema 分派方向：`audit_run` 读冻结 manifest 的 `config.schema_version == 4`（或携带 sequence/phases）走 `_audit_v4`，其余走 `_audit_legacy`；旧路径代码原样保留。
- [x] 四个新检查对上写盘事实：phase.json 四字段与 `phase_execute.__init__.run` 的写入逐字段一致（learn 相位不写 phase.json，检查里正确放行）；units.jsonl / acceptance_report.jsonl / redispatch.json / phase_summary.json 的 complete 键 / C 相位不写报告（final_test 明文不写）全部与 phase_execute 的写盘点一致；learn 三账本 + learn_summary.json 带 bank 键的提交点次序与 phase_learn 的写序一致；终测双臂 `<臂>/store` + shortfall + n_attempts_weak 同源（final_test 的 `_expected_units` 用同一全局深度）。
- [x] 轮深度按每个 live 相位自己的 `execution.n_attempts`（与 learn_tasks._n_attempts 同式）；轮级 failed_units.jsonl 的位置与 K1 以轮 store 实例化 UnitFailureRecorder（batch_execute 用 `store.run_dir`）一致；v4 的 shortfall 上下文行按学习相位 pNN 标签报告。
- [x] `--until-phase -1`：cli.py 的边界检查（`-1 <= until_phase < len`）、ConfigError 文案（含 -1 释义）、argparse help、run_experiment docstring、README 运行设置表五处一致；seed_tb_stage1 的冻结步与提示适配正确；launch_tb21_live_run 的透传改名正确；fork_run_stage04 按历史工具注记。
- [x] 模式判型（live/exam 序列、live 子树缺失/多余的错误）与 legacy 行为对齐；`--self-test` 按提交说明覆盖两种形状（本评审未复跑）。

**W3-C 文档与转换器**

- [x] 转换器抽查 2 份（tb21_split_live_run_v3_v4、alfworld_skillopt_split_v3_v4，另扫 searchqa）：序列（live：D L B L B L C；exam retry_limit=2：D L A L A L A L C）、B 相位参数搬家（n_attempts/max_injection_tokens/compare rule）、C 相位 delivery 原样 + compare 按 mode（split→fresh_bare）、L.evidence K/retry_limit、merge_check 跟随 acceptance（exam→exam、live_run→none）、invalid_redispatch_limit=1 补全——逐键对上 `upgrade_v3_config`；输出是解析后规范形（from_dict→save_config），与装配器身份比对的规整口径一致；"已存在的输出不覆盖、v2 有 v3 记录时拒绝"防护在场。
- [x] README 抽 5 处：相位字段表（execution/compare/acceptance 与 ExecutePhaseSpec 一致；evidence/author/curate 与 LearnPhaseSpec 一致）、pNN 标签规则（p{i+1:02d}:name、无冒号字符集、目录冒号换下划线）、4.3 目录布局（run 级五仓历史名、live 轮仓、相位目录逐文件——与各驱动写盘点一致，含 exam 相位的 generation_failures.jsonl）、5.6 的 12 种回执结局（与 learn_tasks 全部 12 个 outcome 字符串逐一核对）与切段位置一节（对 segmentation.py 逐句）、第六节 v3→v4 身份变化（对 upgrade_v3_config 的披露清单）——全部一致。
- [x] 收尾同步 740105e：移除 README 与 runbook 两处"待同步"过渡注，与已合入的代码事实相符。

**跨代理咬合（评审重点 5）**

- [x] 装配 run 首 L 的 add 行 → 切段：静态推演零模型调用成立（见发现 P1 的条件链），缺口仅在测试覆盖。
- [x] 预置库短路的是**整理**（末个 learn 相位），决策照常重跑；README 5.6"第一个学习相位从空库开始"如实记载了决策侧的空库，两代理的说法不冲突。
- [x] W3-S 的 add 行回执（skipped_existing/skipped_no_material）不破坏 W3-A 零调用续跑测试所断言的"预置库逐字节不变"（回执写 outcomes.jsonl，不动库工件）。
