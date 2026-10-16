日期：2026-10-16
一句话说明：Wave 1B（R2b 词汇迁移，单个提交 `16c3791`，65 文件）的正确性评审 A 路：机械迁移本身（correctness 映射、InfrastructureError 通道、重试游标、v1 迁移、金矩阵披露）逐项核对通过，但迁移使"已落盘、未判分"成为常规可达状态，live 判定路径仍按改前不变量处理它，构成 1 条须在 Wave 2 接线前裁决的阻断与 2 条偏移、3 条细节。
对应 log.md 条目：2026-10-16 20:03 — Wave 1B / R2b：三值运行状态词汇迁移到 correctness（Trajectory 升 schema v2）。

评审对象：提交 `16c3791`（分支 `feature/multi-method`）相对 `7fadee2` 的全量 diff，以及合并后 HEAD 的九个咬合面文件（core/models、benchmarks/base、benchmarks/tb/plugin、benchmarks/react_loop、composites/batch_execute、composites/verdicts、composites/reports、blocks/metrics、blocks/view）加上 live 判定三件（live_measurement / live_verdicts / methods/acceptance/live_run）。
基准：docs/decisions/repair_plan_simple_20261016.md R2b 节与硬约束、docs/reviews/wave1_review_20261016_A_correctness.md 接线清单第 4 条、src/expedistill/infra/reliability.py 异常分类契约。
方法：纯静态评审。只读 diff 与代码，未运行任何测试或脚本；金矩阵核对用只读脚本对 fixture 做 JSON 形状全检（不改文件）。全量测试绿色（2323→2325 + 3 deselected）与 8×3 崩溃续跑等价按提交说明引用，本评审未复跑。

---

## 总体判断

**有条件通过：R2b 的机械迁移层全部核对通过——success→true / failure→false 无例外路径、InfrastructureError 的抛出点与改前 infra_error 集合逐一对应、重试游标推进逐位等价、v1 迁移无损且 infra_error 响亮拒载、78 条金矩阵记录的披露与 fixture 实际形状吻合（程序化全检零出入）。问题集中在迁移的语义余波：R2b 把"已落盘但没拿到判分结果"从不可能状态变成常规状态（TB 无 reward / 伪零分 / react_loop model_output_error 都会落盘 correctness=None），而下游 live 判定路径的两处守卫、三处 docstring 和一处测试注释仍写着改前的不变量"K1 不会存未判分轨迹"，其中判定核心会把零测量的完整组静默判成"无改进"、最终丢弃技能。这正是三值词汇当初要防的"把没测到当测败"，从异常通道绕了回来。该路径既没有测试钉住，也没有在提交说明或 log.md 披露。**

发现计数：阻断 1、偏移 2、细节 3，共 6 条。

---

## 逐条发现

### 【阻断 B1】live 判定核心把"完整但全部未判分"的组静默判成"无改进"：改前不可达、改后常规可达、未披露

- **可达性是本提交打开的**。改前（`7fadee2` 的 `plugin.py`）：无 reward 的 trial 返回 `infra_error` outcome，K1 机械重试，重试耗尽则单元缺失，`judge_round` 的完整性守卫（`live_verdicts.py:192-198`）对缺失单元抛 CodeBugError、由阶段重派——所以"完整组 ⇒ 全部有判分"恒成立。改后（`tb/plugin.py:1082-1088`）：无 reward / 伪零分（双向）的 trial **落盘** correctness=None、不重试；react_loop 的 model_output_error 同样落盘 None（`react_loop.py:147-156`）。一个 TB 组的全部采样都撞上 AgentTimeout 或伪零分时，组完整、valid=0、全部无测量，直接流进 improve 规则。
- **处置路径**：`strict_rate_gain` 的防御分支 `if live["valid"] == 0: return False`（`blocks/rules.py:259-261`）把零测量读成 not better → 该轮 miss → 花一次 G10 归因 + G11 修订（对根本没有测量的"失败"做归因）→ 第二轮再 miss 就 `discard(live_no_improvement)`（`methods/acceptance/live_run.py:10-11`、`live_verdicts.py:442-443`）。技能在零次有效测量的情况下被删除。
- **三处自证过时的文本**（都写于"该状态不可达"的前提，本提交未同步）：
  - `blocks/rules.py:253-258`：防御分支 docstring 自称 "Both defensive branches are **unreachable** in the live_run stage as designed -- it short-circuits an all-infra live arm (live_undetermined)"——改后可达；
  - `live_verdicts.py:204-208`：注释 "K1 never stores an infra-exhausted unit, so this is **also exactly the live_counts['valid'] == 0 case**"——改后 valid==0 不再等价于全空洞，存在第三种来源；
  - `tests/composites/test_live_acceptance.py:628-631`：`test_judge_stored_infra_trajectory_is_not_undetermined` 的 docstring 仍说 "(impossible through K1, which never stores infra-exhausted units)"——本提交只把它的输入从 `("infra_error", ...)` 改成 `(None, False, False)`，没改这句已为假的断言。
- **为什么标阻断而不是细节**：混合场景（部分 None、valid>0）被判 not better 是 `test_judge_stored_infra_trajectory_is_not_undetermined` 钉住的既有策略，可以辩护；但**全 None 组 = 零测量 → 无改进 → 弃技能**这个新可达行为没有测试、没有披露，且与边界定稿自己的纪律（"a run without a scorer verdict is not a measurement of anything"，`benchmarks/base.py` aux scorer 契约原话）正面冲突。提交说明只声称"D3 口径不变、欠账机器形状不变"，对 live 判定核心的这条变化一字未提。Wave 2 的相位驱动接上 `acceptance_report`/`judge_round` 之前、以及任何正式 run 之前必须裁决。
- **裁决方向（二选一）**：(a) 全未判分的完整组按 `live_undetermined` 语义处理（重派或挂起，不判 not better），或在 cause 词表补一个显式的 unscored 条目；(b) 维持"零测量 = not better"为有意策略，则必须更新上述三处文本、在提交/log 披露该语义变化、并补一条全 None 组的钉子测试。按 (a) 或 (b) 落地前，本条不关闭。

### 【偏移 P1】live 基线读取的 valid==0 守卫诊断过时：改前意味着数据矛盾，改后是自然可达状态

- `live_measurement.py:347-353`：`counts["valid"] == 0 → ConfigError("...every registered task_select rule requires valid > 0, so **the frozen split and the store disagree -- the run's task set must be rebuilt in a new run directory**")`。改前 stored ⟹ scored，这个 ConfigError 只能是切分与库矛盾的强信号；改后一个全部弱基线试验都未判分的任务（AgentTimeout 风暴下完全现实）就会命中，而给出的处方"重建任务集"是错的——真实问题是试验未判分，不是任务集错了。守卫本身响亮（好），但错误指引会把一次可重试/可接受的欠账误导成作废整个 run。与 B1 同根：守卫的语义前提没随 R2b 重推导。

### 【偏移 P2】Wave 0 冻结契约的字段注释与 R2b 语义冲突：contracts.py 仍写 "None ONLY means the benchmark has no scorer"

- `core/contracts.py:91-93`（RunRecord.correctness 字段注释）："``None`` ONLY means the benchmark has no scorer"。R2b 之后 None 还包括"**有**判分器但没拿到结果"（伪零分、AgentTimeout、model_output_error）——`benchmarks/base.py` RunOutcome 的 docstring 已写成新语义，contracts.py 这份"冻结契约"没同步（它不在本提交改动清单里）。contracts.py 是 Wave 0 冻结、Wave 2 三个代理的接线参照文本；按旧注释理解，Wave 2 完全可能把 TB 的 None 运行当"无判分器 benchmark"路由到 model-judging 规则。一行注释的修复，但必须在 Wave 2 开工广播前落。

### 【细节 D1】报告 cause 词表没有"已落盘、未判分"的条目，两个读取器把它折进语义不符的现有 cause

- 基线读取器：全部单元已存但全 None 的行拿到 cause `invalid_redispatch_exhausted`（`reports.py:249-251` 的 else 分支），而该 cause 的定义是"派发预算耗尽、单元缺失"（`decision.py:455`）——这些单元明明都回来了。live 读取器把同一状态标成 `zero_execution`（`reports.py:455-457` 附近），而 B1 裁决里 zero_execution 的含义是"该行没有任何**完成的**运行可供报告"——存了的未判分运行是"完成了但没有判定"，不是"没完成"。
- 后果：同一个新状态在两个读取器下走相反的决策处置——前者经 `_CAUSE_DELETE_REASONS` 给 delete(INVALID_REDISPATCH_EXHAUSTED)，后者按 B1 裁决是"可见性行、不产生任务清单行"。哪个对取决于裁决；至少应在 Wave 2 前明确归属（与 B1 的裁决合并处理最自然）。

### 【细节 D2】伪零分的"正向"方向（reward>0 且 pytest 未收集）没有运行级测试钉住

- 代码是对称的：`tb/plugin.py:1082` `if reward is not None and facts["verifier_ran"] is True`——reward 为正、verifier 未跑同样落 correctness=None，这正确撤销了 Wave 1 评审 A 记录的 P3 评分膨胀。但运行级测试只钉了 reward==0 + 未收集的方向（`test_tb_plugin.py:475-491`、`test_w1b_execution_facts.py:110-131`，都用 `result_failure.json`）；`result_success.json` + `verifier_not_collected.txt` → `correctness is None` 这一行钉子缺失。膨胀方向恰恰是历史上出过事故的方向，值得一行测试防回退。

### 【细节 D3】usage 账本行标签改词未在提交说明或 log.md 披露

- deadline / 死 runner 的账本行标签 `"infra_error"` → `"infra"`（`tb/plugin.py:596-600, 611-615`）；无 reward / 伪零分试验从改前的 `"ok"`（scored 分支）或 `"infra_error"`（无 reward 分支）改为 `"unscored"`（`tb/plugin.py:1082-1088`）。消费方只区分 `== "ok"`（`infra/usage.py:77`），token 总额不受影响，但 (a) 伪零分试验从 n_ok 移入 n_error，同一 run 的 ok/error 划分口径改变；(b) 跨 R2b 续跑的 run 账本里新旧标签混用。代码注释里"pre-R2b rows say infra_error"算自认，但两条披露文本（提交说明、log.md）都没提。建议在 log.md 追一条更正/补充披露。

---

## 核对通过清单

以下各项逐条核对通过（证据均为静态读码/读 diff；金矩阵形状用只读脚本全检）：

1. **correctness 逐值映射保真**：全仓 grep 确认三值词在 src 的判定与数据路径零残留——只剩 `classify_result` 历史镜片（`tb/plugin.py:318-329`，唯一消费方 `scripts/analysis/tb_archive_validate.py`，符合提交披露）、`_migrate_v1_trajectory_row` 迁移代码与注释；skill/usage/账本的 `"status"` 是各自独立词表，与运行状态无关。没有任何分支仍按旧三值判断，没有 meta 里遗留的 status 读取。
2. **显示词只是渲染层**：`final_status_word`（`blocks/view.py:47-54`）的全部消费方是 `render_trajectory_view`、`live_attribution._attribute_group`、`live_group._measurement_facts` 三处纯渲染/证据文本，不回流任何数据字段或判定路径；{True: success, False: failure, None: unknown} 的逐字节冻结有 docstring 说明动机（E4 语义仪器）。
3. **InfrastructureError 分类契约一致**（reliability.py 本提交未动，四类处置不变）：新抛出点——transport（`tb/plugin.py:586-600` 的统一 except 重抛）、deadline / 死 runner（`tb/plugin.py:602-626`，移出 except 块避免了 usage 双记，`_kill_runner`/`_augment_partial` 内部自吞异常故抛出路径稳健）、陈旧 run id（`_stale`，`tb/plugin.py:879-897`）、截断/非对象 result.json、ALFWorld reset——与改前 infra_error 的集合逐一对应；模型输出问题没有升格为基础设施异常（ModelOutputError → correctness=None；verifier 未跑 → None），也没有基础设施失败被降格（transport/deadline 仍走 K1 重试）。
4. **重试游标推进逐位等价**：`_execute_unit.once` 在捕获 InfrastructureError 时写 `next_retry = retry + 1` 再重抛（`batch_execute.py:344-396`），与改前 infra outcome 分支（`16c3791^` 同函数）逐语句对应——每次失败推进一次、成功/非 infra 异常不推进、耗尽后 `on_give_up` 记账本且不落盘、续跑从游标续号。交错边界（连续异常、异常后成功、耗尽后 resume）均有改写后的测试钉住（`test_batch_execute.py` 的 resume/rejoin 序列测试全部改为 raise 形态）。改前插件不存在绕过 outcome 直接 raise 的 infra 路径，故"改前每次 infra outcome 推进 = 改后每次异常推进"的等价声明成立。
5. **v1 迁移正确**：success→True、failure→False，其余键原样保留（`models.py:341-367`）；infra_error 与缺 status 均响亮拒载（ValueError），拒载消息指路"AGENTS.md section 6 rule 1"——与该仓库 AGENTS.md 第六节第 1 条的维护分支规则原文核对一致；缺 schema_version 的行按 v1 处理（有测试）；读路径 `store.load → from_dict` 全覆盖；`assemble_reuse` 对 v1 行保持字节原样复制、读时迁移，docstring 已同步。
6. **scorer None→invalid 的下游一致性**（B1 所在的 live 判定核心除外）：`summarize_unit` 的 valid_count 排除 invalid（`verdicts.py:357-360`）；D3 的 valid 排除 None、infra 计入 None 与缺失单元，attempts==valid+infra 保持（`metrics.py:103-117`）；stage1/6 欠账机器按 unit_ids 工作与词汇无关（`shortfall.py`）；decision.compare 的 success/valid 交叉相乘口径与 D3 同源；`_strict2_weak2` 的排序键在 None 上确定且永不进入 preferred 组（`blocks/rules.py:225-232`）。
7. **金矩阵披露与实际吻合**：8 个 fixture 恰好 78 条轨迹记录（程序化全数：exam 组合 4–6 条、live 组合 11–18 条），全部 v2 形状——无 status/success 键、有 correctness、schema_version==2，零例外；抽样核对 correctness 值与改前 status 同值映射。diff 中被删除的行仅 status / success / schema_version / base_commit / generated_on 五类，请求转储、阶段 summary、账本、技能库零改动；`golden_semantic_baseline` 未触碰；请求转储中 "final status: success/failure" 逐字节保留（14 处、无 "unknown"——金矩阵的假插件不产生 None 记录，故"请求转储未动"的声明静态可信）。provenance.json 恰好只有 base_commit / generated_on 两行变化。
8. **W1-B 两个过渡副作用的消除正确**：AgentTimeout 等无 reward trial → correctness=None 落盘、exception_type/exception_message 随 meta、不重试（`tb/plugin.py:1082-1088` + `test_w1b_execution_facts.py:139-155`）；伪零分双向对称 → None（代码对称；测试只钉负向，见 D2）；verifier_ran / exception_type 的产方（`tb/plugin.py:279-287` → outcome.meta → Trajectory.meta）与消费方核对：当前 src 内无消费方（scorer 规则只读 correctness），具名规则接线确属 Wave 2 缺口——与 Wave 1 评审 A 的 P3 判断一致，本提交的 docstring 也如实写明"acceptance's named-rule decision"。
9. **两个过渡桥接删除干净**：`verdict_from_trajectory` / `verdict_from_attempt` 全仓无调用方（只剩 docstring 提及与 `hasattr` 反向钉子测试）；reports 的 baseline/live 读取器统一走 `_run_record_from_trajectory` + `verdict_from_run`（benchmark 缺省具名规则），exam 读取器走 `_run_record_from_attempt`（judge_verdict 经 aux）；exam_judge 的 basis 携带判卷理由（`verdicts.py:252-268`，有测试钉住）。
10. **跟随者同步完整**：四个脚本（seed_tb_stage1 的落盘与 S/F 标记、audit_live_run 自测、tb_archive_validate 的 v3 构造、tb21_f3_baseline）、live_attribution 的基线状态行（与 final_status_word 同源）、live_group 证据段（`segments` 键 status→correctness，纯结构指针、无 prompt 消费方）、searchqa 辅助打分器（correctness is True / termination 双条件与改前等价，None 轨迹经 D3 不会到达打分器）、README 五处、AGENTS.md 第七节、log.md 条目——与代码逐一核对一致。

## 附注

- 本评审为纯静态评审，未运行任何测试；"2323→2325 passed + 3 deselected、test_layering 3 passed、ruff 全过、8×3 崩溃续跑等价"均引自提交说明与 log.md，未独立复验。
- 金矩阵的 78 条形状核对用一次性只读脚本完成（读 fixture、打印统计），未写入任何文件。
- 本报告是本次评审的唯一产出文件，未改动任何代码。
