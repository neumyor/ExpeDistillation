日期：2026-10-16
一句话说明：执行—学习重构 Wave 1（含 Wave 0 拆分）的续跑语义评审——崩溃续跑契约在合并结果上是否原样保持。
对应 log.md 条目：2026-10-16 Wave 0 / Wave 1 各合并（c5aa94d、4246621、5d401ef、4c36ede、87a047b）。

## 总体判断

**通过。** `c6a9fb8..HEAD` 的合并结果没有破坏崩溃续跑契约：跳过键的构造规则一处未改，
run 根目录的账本名与目录名逐字节相同，K5 的 run 模式仍走 `store.bank()` /
`store.append_bank()`（崩溃注入测试的挂钩点完好），终态与账本行的写入顺序在所有被改文件里
都原样保留。发现 0 个阻断、2 个偏移（都是计划内且被测试钉住的过渡期差异，但要在启动正式
run 前记入决策）、5 个细节。

两点偏移都来自 W1-B（TB 插件摘除判定）：AgentTimeout 超时从"记 failure 不重试"变成
"落 infra_error 触发 K1 机械重试"；伪零（verifier 声称给了分但 pytest 根本没跑）从
"判 infra_error"变成"分数原样通过、只把 `verifier_ran=False` 记为事实"。第一条被
`tests/unit/test_w1b_execution_facts.py` 和 `tests/unit/benchmarks/test_tb_plugin.py` 钉住，
且只存在于 TB 插件路径（AgentTimeoutError 在 src 里只出现在 tb/plugin.py，searchqa /
alfworld 插件从来没有过异常重分类）。第二条的 `verifier_ran` 目前全仓库没有任何读者，
要等 R2b 的具名规则——在此之前，TB 上一次正式 run 的基线/验收计数里伪零会被当成真分数。
这与"修复全部完成前不启动正式 run"的门是自洽的，但建议把这个门显式记到启动检查单里。

评审方法说明：按更正后的指令，本评审以静态阅读与 grep 为准，没有为评审跑任何测试。
（更正指令到达前我已经跑过一轮：`-k "resume or crash or recovery"` 345 全过、全量
2311 通过 + 3 deselected，与各合并提交自报的数字一致；该结果只作背景参考，不作为
任何一条发现的依据。）

## 逐条发现

### 偏移 1：TB 超时重试语义变化，测试已钉住，仅 TB 路径（W1-B，计划内）

- **改动**：`src/expedistill/benchmarks/tb/plugin.py` 的 `run()` 不再调用
  `classify_result`。拿不到 verifier 分数的 trial 一律 `RunOutcome.infra_error`，
  例外类型（含 AgentTimeoutError）原样进 meta 的 `exception_type`。
  infra_error 会被 K1 按 `retry_limit` 机械重试，所以超时 trial 从"记一次 failure"
  变成"最多重试 retry_limit 次"。旧分类只保留在 `classify_result`，唯一调用方是
  `scripts/analysis/tb_archive_validate.py`（历史口径复算），`run()` 不再调它。
- **钉住的测试**：`test_w1b_execution_facts.py::
  test_reward_absent_trial_reports_no_verdict_and_raw_exception_facts`（AgentTimeoutError
  → infra_error + 原始事实）、`test_only_a_run_without_a_scorer_verdict_is_retried`
  （K1 只重试无判定的 run）、`test_a_suspicious_looking_result_is_executed_exactly_once`
  （拿到判定的 run 绝不重试）；`test_tb_plugin.py::
  test_run_model_timeout_result_is_no_scorer_verdict`。
- **范围确认**：`grep AgentTimeout src/` 只命中 tb/plugin.py；searchqa / alfworld
  的 `run()` 从不做模型/基础设施异常重分类（grep 无 status 重分类逻辑），K1 的重试机制
  本身（`composites/batch_execute.py`、`infra/reliability.py`）在本区间一行未动。
  差异只在 TB 插件路径。R2b 引入 `correctness=None` 后此过渡消失。

### 偏移 2：伪零分数原样通过，`verifier_ran` 事实暂无读者（W1-B，计划内但要记门）

- **改动**：同一提交。以前"有 reward 但 verifier 日志缺失或 pytest 没收集到任何条目 →
  infra_error"；现在 reward>0 就是 success、reward==0 就是 failure，`verifier_ran`
  （True/False/None）只作为事实记进 meta。
- **风险**：`grep verifier_ran src/ scripts/` 确认除 tb/plugin.py 自身与测试外没有
  任何消费者。R2b 的验收具名规则落地之前，伪零会混进 D3 的成功计数，进而影响
  strict_rate_gain 之类规则的判定。这正是"不启动正式 run"这道门要拦的东西——建议在
  启动检查单里显式写一条"R2b 未落地前 TB 不开正式 run"。
- **钉住的测试**：`test_tb_plugin.py::test_run_pseudo_zero_keeps_the_scorer_verdict_
  and_reports_the_fact`、`test_result_facts_report_pseudo_zero_evidence_without_
  invalidating`。

### 细节 1：生产驱动里的 injection_audit 追加不在崩溃注入覆盖内（先前即如此，非本区间引入）

- 崩溃注入测试（`tests/composites/test_live_acceptance_resume.py`）的 `_dispatch_round`
  刻意通过 `lv.append_jsonl` 写冻结行，让 `lv`/`lat` 两个 patch 点把审计行计入写序号
  （测试里的注释明说了这一点）。但生产驱动 `methods/acceptance/live_run.py` 第 484/614
  行用的是自己 import 的 `append_jsonl`，驱动整体没有崩溃注入测试，只有"构造崩溃等价状态"
  的续跑测试。
- 与基线 `c6a9fb8` 核对：当时审计行同样写在 live_run.py 里、同样不被注入，本区间只是
  `la.` 改 `lm.`。续跑正确性由 A10 契约兜底（续跑重派发时对冻结行做逐字节重推导比对，
  不一致直接 `CodeBugError`），所以这不是回归，记录在案供 Wave 2 相位驱动接管派发时补
  崩溃注入。

### 细节 2：failed_units.jsonl 与用量账本绕过崩溃注入（先前即如此）

- `live_attribution.apply_revisions` 经 `UnitFailureRecorder`（`infra/reliability.py`
  自己的 `append_jsonl` import）写 failed_units.jsonl，llm 调用记用量账本同理。
  崩溃注入只拦 `lat.append_jsonl` / `lv.append_jsonl` / `store.save`，拦不到这两条。
- 与基线核对：c6a9fb8 的 live_acceptance.py 有同样三处 recorder 用法，
  `infra/reliability.py` 本区间未动。失败行是追加式且幂等信息，不构成续跑正确性风险；
  记录在案即可。

### 细节 3：相位模式的 K5 落盘不走 store 子类挂钩，Wave 2 需要新的崩溃覆盖（W1-F）

- run 模式（`dest=None`）在 `_read_bank` / `_append_bank_row` 里显式走
  `store.bank()` / `store.append_bank()`，`tests/composites/test_curation_resume.py`
  的 `AppendCrashStore` / `SecondAppendCrashStore` 子类化这两个访问器注入崩溃——
  这正是 W1-F 踩坑后改回来的形状，已核实。
- 相位模式（`dest` 给定）的库行改走裸 `append_jsonl(_bank_path(...))`，Store 子类挂钩
  失效。当前相位模式没有生产调用方（见核对清单第 4 条），且 `test_w1f_phase_bank.py`
  有幂等二调用的单测；Wave 2 相位驱动接上真实调用方时应为相位模式补崩溃续跑测试。

### 细节 4：相位目录名可能因冒号映射碰撞（W1-F，低危）

- `composites/phase_bank.py` 把 `pNN:<name>` 的冒号映射成 `_`。合法字符集含 `_`，
  所以 `p01:a_b` 与 `p01_a:b`…… 更直接地：`p01:learn` 与字面 `p01_learn` 两个不同
  标签会落进同一个目录。标签由 Wave 2 相位驱动生成（格式固定 `pNN:<name>`），实际
  撞名需要名字本身以 `_` 占掉冒号位，概率极低；记录在案，Wave 2 固定标签格式时顺手
  收紧即可（例如禁止标签内出现 `_` 紧随冒号位，或直接拒绝含 `_` 的裸段）。
- 越权防护是对的：`..` 与任何非安全字符都会被 `ConfigError` 拒绝，标签无法逃出
  `phases/`。

### 细节 5：W1-E 的 `produce` 入口假设驱动有 `produce_phase_bank` 属性

- `methods/curation/phase_bank.py::produce` 用 `driver.produce_phase_bank(ctx, params,
  phase_label)`，若将来注册一个没有该属性的方法会 AttributeError 而非 ConfigError。
  当前注册表里 agentbrew / none 都有，且无生产调用方；Wave 2 接调用方时建议改成
  响亮的 ConfigError。

## 核对通过清单

1. **账本覆盖完整性（评审重点 1）**。全仓库 grep `append_jsonl` 落盘点，K4/K6 流程内的
   写路径与崩溃注入 patch 点一一对应：
   - `leak_verdicts.jsonl`（acceptance_leak）、`rewrite_failures.jsonl`
     （acceptance_submit）→ `test_acceptance_resume.py` 同时 patch 这两个模块的
     `append_jsonl`（第 358–359、401–402 行），与拆分前 patch 单一 `acceptance_mod`
     覆盖的两个追加点（c6a9fb8 acceptance.py 第 259、380 行）集合相同；
   - `verdicts.jsonl`（live_verdicts）、`attributions.jsonl` /
     `attribution_failures.jsonl` / `revision_failures.jsonl`（live_attribution）→
     `test_live_acceptance_resume.py` patch `lv` + `lat`（第 521–522、559–560 行），
     与拆分前 `la` 覆盖的 5 个追加点（c6a9fb8 live_acceptance.py 第 725、907、913、
     935、1218、1225 行）集合相同；
   - `injection_audit.jsonl`：测试驱动侧经 `lv.append_jsonl` 计入写序号（生产侧见细节 1）；
   - 五对象存储：两套崩溃测试都用 `crasher.save` 包住 `store.save`；
   - `skill_bank.jsonl`（run 级）：`test_curation_resume.py` 子类化 `append_bank`
     注入崩溃，`test_stage5.py` 用 `append_bank` 伪造续跑库，W1-F 保留了这条路；
   - `curation_log.json`：`test_curation_resume.py` patch `curation._write_log`；
   - live_measurement 无任何落盘点（grep 确认），acceptance_skill_status、
     verdicts、decision、contracts、evidence 族、author 族均零落盘（grep 无
     write_json/write_jsonl/append_jsonl/save/open）。
   两条绕过路径（细节 1、2）均为基线已有的现状。
2. **W1-F 相位目录模式（评审重点 2）**。
   - run 根目录模式逐位不变：`curate_bank` 的 `dest=None` 分支经 `_read_bank` /
     `_append_bank_row` 原样走 `store.bank()` / `store.append_bank()`；
     `_log_path`、UnitFailureRecorder 路径在 `dest is None` 时与旧代码逐行相同
     （AST 比对 `_initial_next_serial`、`_check_bank_not_stale`、
     `_read_final_member_ids`、`_check_store_matches_log` 全部 IDENTICAL，
     `_log_path` 仅多一个 dest 分支）；`none.py::run` 经 `write_as_is` 落同一
     `run_dir / "skill_bank.jsonl"`；`infra/store.py` 本区间零改动，
     `KIND_DIRS` 与五个对象目录名未变；
   - 相位模式落盘原子性：库行走 `append_jsonl`（与 run 级库同一撕裂尾修复语义），
     `curation_log.json` / `bank_summary.json` 走 `write_json`（临时文件 + fsync +
     改名），`none` 的相位库走 `write_jsonl`（整文件原子替换），空集工件也是
     `write_jsonl`。逐条读码确认，没有直接 open(...,"w") 的裸写。
3. **W1-B 重试语义（评审重点 3）**：见偏移 1、2。差异只在 TB 插件路径，测试钉住，
   searchqa/alfworld 不受影响。
4. **W1-E 旧路径（评审重点 4）**：`live_attribution.py` 在 09a3496 只加了 docstring
   （AST 比对 `attribute_group`、`apply_revisions` 与拆分前 IDENTICAL）；生产调用方
   `live_run.py` 仍调 `lat.attribute_group` / `lat.apply_revisions`；新驱动
   （evidence 族三个文件、author/write.py）零落盘；`produce` / `produce_as_is` /
   `produce_phase_bank` / `collect_group_evidence` / `revise_group` 在 stages/、cli、
   acceptance 驱动里没有任何调用（grep 只命中 docstring 提及）。
5. **跳过键（评审重点 5）**：六个 Wave 1 提交没有改动任何键构造规则。证据：
   - `core/ids.py`、`infra/store.py`、`composites/batch_execute.py`、
     `composites/segment_checked.py`、`stages/stage1_baseline.py`、
     `stages/stage2_segmentation.py`、`stages/stage3_generation.py`、
     `infra/reliability.py`、`infra/runlock.py`、`infra/usage.py`、
     `infra/manifest.py` 在 `c6a9fb8..HEAD` 零提交；
   - AST 比对拆分搬移的键构造函数：`_next_serial`、`_pending_attempt`
     （exam_answering，仅 docstring 措辞变化）、`skip_ids_for`、`round_dir`、
     `pending_groups`、`_pending_group`、`audit_row`、`load_baseline`、
     `survivors_and_block`（live_measurement）、`judge_round`、`finalize_group`、
     `no_op_guard`（live_verdicts）全部与拆分前 AST 相同；
   - `live_run.py` 的 `_stored_units` / `_round_holes` / `_expected_units` 只是
     `la.` → `lm.` 改名（diff 逐行核对）；W1-D 的 delivery 单元 id
     （task_ref + 8 位十六进制摘要）是新增构造，但没有生产调用方走它——
     stage6 与 live 驱动都还走 legacy 形参位置（`units.dispatch` 的结构判别，
     stage6_test.py 第 275 行、live_run.py 第 777 行均为 4 参 legacy 调用）。
6. **终态先于账本行的顺序（评审重点 6）**：无变化。
   - `reports.py` 拆分前后都是纯读取器（旧版 grep 无任何写调用；新版同为只读，
     `acceptance_report` 只经 `read_jsonl` 读）；
   - `stage6_test.py`（W1-C）的 diff 只删掉 `_delta` / `_aux_delta` /
     `control_source` 赋值并改调 `decision.final_test_delta` /
     `decision.control_arm_source`（后者返回值与旧字符串逐字节相同，
     `"stage1_reuse"` / `"fresh_bare_run"`）；仅有的两处 `write_json`
     （`_accepted_path` 的已接受空洞记录、阶段 summary）原样未动；
   - `judge_round` AST 相同（先追加 verdicts 行、终态由 `finalize_group` 之后提交）；
   - `accept_skill`（W1-A 经手的唯一 K4 写路径改动）只是把内联的"查已存判定 →
     没有则审计 → 追加"三步提成 `leak_verdict_for`，顺序逐句相同；
   - `decision.py` 零磁盘访问，`verdicts.py` 零落盘。
7. **目录名与账本名不变（硬约束 2、3）**：`KIND_DIRS` 未动；`LIVE_DIR_NAME` /
   `VERDICTS_LEDGER` / `ATTRIBUTIONS_LEDGER` / `ATTRIBUTION_FAILURES_LEDGER` /
   `REVISION_FAILURES_LEDGER` / `INJECTION_AUDIT_NAME`（live_measurement 与拆分前
   live_acceptance 逐值相同）、`LEAK_VERDICTS_LEDGER` / `REWRITE_FAILURE_LEDGER`
   （与拆分前 acceptance.py 逐值相同）、`curation.py` 的 `LOG_DIR` / `LOG_NAME` /
   `BANK_NAME` 均未变；新增的只有 `phases/` 这棵相位工件树，run 级布局原样。
8. **五个合并提交干净性**：对 c5aa94d / 4246621 / 5d401ef / 4c36ede / 87a047b 逐一
   核对 `git diff --name-status <merge>^2 <merge>`，相对各 wave 分支只出现新增与
   先序 wave 的修改，没有任何文件被合并过程删改；`tests/fixtures/golden/` 本区间
   零改动（金矩阵基线原封）。

## 给启动正式 run 之前的一条建议

把"R2b（`correctness=None` 与验收具名规则）落地前，TB 基准不开正式 run"写进启动
检查单。理由：偏移 2 之外，Wave 2 相位驱动还要引入新的目录结构（`phases/`），那是
manifest 拒载规则与配置 schema 的又一次变更，两道门应该都有显式的勾选项，而不是靠
记忆。

工件：本文件。
