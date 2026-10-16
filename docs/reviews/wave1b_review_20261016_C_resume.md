# Wave 1B（R2b 词汇迁移）续跑语义评审报告

- 评审对象：分支 `feature/multi-method` 提交 `16c3791`（Migrate the run-status vocabulary to correctness and raise infra failures (R2b)）
- 评审代理：Wave 1B 续跑语义（C 路）
- 日期：2026-10-16
- 方式：纯静态评审。只读代码与提交差异，未运行任何测试、未执行任何脚本、未修改被评审对象。

## 总体判断

**通过，未发现阻断项。** 续跑语义的硬约束全部成立：跳过键构造零改动（`src/expedistill/infra/` 在本提交中一行未动，跳过判定仍是"文件存在即完成"，与状态词汇完全无关）；重试游标推进点与"异常先于游标写入、游标先于重试"的顺序保持崩溃安全；重试耗尽的落盘路径（失败账本、不落盘、续跑再试）形状不变；金矩阵重生成流程与既有 `tests/golden` 器器对得上；用量账本与失败账本没有口径漂移。

发现 1 项**偏移**（TB 判分日志抓取失败会把已判分的运行降级为"无判分"且不再重试，基础设施级故障落进了事实通道而不是异常通道）和 4 项**细节**，均不构成阻断。逐条见下。

## 逐条发现

### 偏移-1：verifier 日志抓取失败被吞掉，已判分的 rollout 降级为 correctness=None 且永不重试

**位置**：`src/expedistill/benchmarks/tb/plugin.py:1042-1049`（抓取处）、`:1082`（判分判定处）。

**机制**（先用大白话说：判分证据是 pytest 的输出日志，日志要从远程机器拷回来）：`_finish` 里抓取 verifier 日志的 `link.get` 失败时，`except (InfrastructureError, OSError)` 把传输异常整个吞掉，`verifier_log = None`，于是 `verifier_ran = None`；判分判定 `reward is not None and facts["verifier_ran"] is True` 不成立，轨迹以 `correctness=None` 落盘，用量行记 `unscored`，没有任何重试。因为"已存 = 已完成"，续跑永远不会再看这个单元，这次测量就此丢失。

**为什么算偏移而不是阻断**：这次抓取失败不区分两种情况——"判分器确实没跑"（伪零分，业务上就该记 None）和"判分器跑了、日志拷贝时网络抖了一下"（基础设施故障）。提交的边界主张是"基础设施失败一律抛 `InfrastructureError`"（修复计划 C5 裁决原文："运行是否完整由基础设施失败一律抛出异常这一条契约表达"），而这条路径把一次传输故障留在了事实通道里。模块 docstring 写的规则是"没有判分器证据的 reward 不携带判定"，代码与文字一致，所以语义上自洽；但文字没有区分"证据不存在因为没跑"和"证据没拿到因为没抓到"。迁移前同样的抓取失败下 reward 原样通过（有效判分、用量记 ok），所以这条是本次提交引入的行为变化。修复计划（docs/decisions/execute_learn_repair_plan_20261016.md 的 R2b 节）没有对这一条给出裁决。

**建议**：把 verifier 日志抓取失败（至少 `InfrastructureError` 那一支）改为抛出 `InfrastructureError`，让 K1 走既有重试；"日志存在但 pytest 没收集到任何东西"才留在 `correctness=None` 通道。

### 细节-1：deadline_kill 审计信息进失败账本后会被 1000 字符截断，且无结构化字段

**位置**：`src/expedistill/benchmarks/tb/plugin.py:596-622`（deadline/dead-runner 路径，`deadline_kill` 的 JSON 与 runner log tail 序列化进异常消息）、`src/expedistill/infra/reliability.py:147,167-168`（`UnitFailureRecorder.MESSAGE_LIMIT = 1000`）。

deadline_kill 从 outcome meta 挪到异常消息文本后，落点就是 `failed_units.jsonl` 的 `message` 字段。runner log tail 本身上限 2000 字符，加上 `deadline_kill` 的 JSON，消息几乎必然超过 1000 字符被截断，`deadline_kill` 的 JSON 有可能被切在中间，人工排查时拼不出完整审计信息。账本行没有为它设结构化字段。

**读取方核对**：`scripts/audit/audit_live_run.py` 的 check 4（shortfall vs failed_units，`:463-534`）只用 `unit_id` 与 `stage` 两个字段对账，不读 `message` 内容，也不读 `error_class`——不需要同步，也不会误报。全仓库 grep 确认没有其他 `deadline_kill` 读取方。所以这是一条"读取方无需改、但信息可用性打了折扣"的细节。顺带说明：迁移前 deadline_kill 随被丢弃的 `RunOutcome.meta` 一起消失，账本里根本没有它；这次挪动是净改进，截断是它的上限。

### 细节-2：`final_status_word` 的 None→"unknown" 是 G1/G10 提示词空间里的新词，提交的"逐字节一致"承诺只覆盖 True/False 两个取值

**位置**：`src/expedistill/blocks/view.py:44-51`、消费方 `src/expedistill/composites/live_attribution.py:196-199`、`src/expedistill/methods/evidence/live_group.py:62-70`。

提交说明写"渲染结果与迁移前的表头逐字节一致"。核对后：True→"success"、False→"failure" 两个词确实与迁移前逐字节相同；None→"unknown" 在迁移前的表头里对应不上任何词——迁移前无判分的轨迹根本不落盘、不进提示词。所以严格说"unknown"是 G1（切段）与 G10（找证据）提示词输入空间里的新词，E4 语义仪器钉住的金语义基线束（`tests/fixtures/golden_semantic_baseline/`，本提交未触碰）不覆盖这个词出现的场景。金矩阵里的轨迹只有 True/False 两个取值（`tests/golden/harness.py` 的两个插件只产出这两个值），所以 E1-E6 通过与这个词不冲突。风险很小（新词只在真实 run 出现无判分轨迹时进入提示词），但语义实验的预登记口径应当把它记上。

### 细节-3：provenance.json 的 base_commit 记录的是父提交 `7fadee2`，不是产出矩阵的代码

**位置**：`tests/fixtures/golden/provenance.json`、`tests/golden/generate.py:37-40`（`git rev-parse HEAD` 在工作区生成时取值）。

本次再生成把 base_commit 从 `84d280e` 记为 `7fadee2`（即 `16c3791` 的父提交），generated_from 的代码实际是本提交的工作区版本。核对历次矩阵再生成提交（`32d6c86`、`fea4310`、`78db71f`）全部是同款模式——生成总是发生在代码提交之前，base_commit 恒指向父提交。这是仓库既有惯例而非本次引入的问题；严格讲 base_commit 这个字段名承诺的"这份矩阵是哪个代码生成的"没有兑现，追溯时需要知道这条惯例。

### 细节-4：`_migrate_v1_trajectory_row` 只识别 schema_version 1 与 2，更高版本号会带着默认值静默通过

**位置**：`src/expedistill/core/models.py:166-168`（`data.get("schema_version", 1) == 1` 才走迁移）。

一行 v3 记录（现在不存在这种东西）会跳过迁移直接进 `_known_kwargs`，`correctness` 缺失时吃到默认值 `None`，没有响亮报错。与"v1 的 infra_error 行明确拒载"的严格程度不对称。当前没有 v3，属于前瞻性细节。

## 核对通过清单

以下各项逐条静态核对通过，均为硬约束或提交说明中的可核对声明：

**1. 续跑键与跳过逻辑（硬约束）**
- `git diff 16c3791^ 16c3791 -- src/expedistill/infra/` 为空：store.py、reliability.py、usage.py、runlock.py、manifest.py、parallel.py 全部零改动；`core/ids.py` 同样零改动。
- 跳过判定是 `Store.exists`（`src/expedistill/infra/store.py:379-380`，按百分编码的确定 ID 查文件存在性），与 Trajectory 的任何字段无关；`batch_execute` 的存量单元校验只比对 `model_role` / `model_name` / `skills_injected`，不碰状态字段。
- 提交差异中所有含 "skip"/"exists" 的改动行均为 docstring 措辞，无逻辑改动。

**2. schema 升版与旧 run 续跑**
- 逐行迁移、按行判版本：`Trajectory.from_dict`（`models.py:166-168`）对每行单独看 `schema_version`，v1 行走 `_migrate_v1_trajectory_row`，v2 行直通——同一 run 目录里 v1 与 v2 轨迹混存（迁移中途崩溃再续跑的形态）能各自正确处理；`_migrate_v1_trajectory_row` 不修改传入字典。
- v1 success/failure 行无损映射到 True/False，`status`/`success`/旧 `schema_version` 三个键剥除、新版本号写回（`models.py:331-368`）；v1 行缺 `schema_version` 键按 v1 处理，有测试钉住（`tests/unit/core/test_models.py` 的 `test_v1_row_without_a_version_is_treated_as_v1`）。
- v1 infra_error 行拒载消息核对：报错文本引用"AGENTS.md section 6 rule 1"，仓库 AGENTS.md 第六节第 1 条正是"正式 run 固定在它启动时的 checkout 上续跑……schema 升版等破坏续跑的改动在 stage 0 会被明确拒载并指向该规则"——指路正确。
- `audit_live_run` 读旧 run 兼容：audit 加载 Trajectory 只有两处用途（注入引用集合比对，`scripts/audit/audit_live_run.py:613`；自建假数据），全部经 `from_dict` 走迁移；audit 的五类检查（round-stores、账本、shortfall 对账、注入审计、终态一致性）没有一处依赖状态字段。

**3. 异常通道的崩溃安全**
- 半写窗口核对：`run_one`（`blocks/execute.py`）只构造 Trajectory 并返回，从不写 store；落盘唯一发生在 `batch_execute._execute_unit` 的 `with_retries` 成功返回之后（`batch_execute.py:383-397`）。异常路径上轨迹不落盘，"轨迹已落盘但游标没推进"的窗口不存在。
- 游标推进点顺序核对（`batch_execute.py:344-369`）：`except InfrastructureError` 分支先原子写 `k1_retry_cursor.json`（`write_json`，临时文件加改名，`store.py:111-126`）再 `raise`；反向窗口（游标推了但轨迹没存）成立且无害——续跑用新 token，远程旧 run id 若有 result.json 会被复用、runner 活着会被接上、死掉无结果按 stale 换 token，与迁移前的推进语义一致。token 序列 `a00, a00r1, ...` 由 `tests/composites/test_batch_execute.py` 的 `test_resume_continues_the_retry_sequence` 等测试钉住。
- 重试耗尽路径核对：`on_give_up` 先写失败账本再 raise，`_execute_unit` 捕获后返回 None，单元不落盘、从返回值消失、下轮续跑重试——与迁移前完全同形，有 `test_infra_exhausted_recorded_absent_and_resumed` 钉住。
- 游标推进的触发集合核对：迁移前所有会返回 `infra_error` outcome 的插件路径（TB 传输失败、deadline、死 runner、stale run id；ALFWorld reset；react_loop 的模型调用与 env.step、环境超时包装的重分类）现在全部改为抛 `InfrastructureError`，唯一不再触发游标推进的是"无 reward 的 trial"——这是 W1-B 过渡行为的刻意移除（改为落盘 correctness=None、由验收具名规则判定），提交说明明示。

**4. 金矩阵重生成与续跑验证**
- 流程对得上：`tests/golden/generate.py` 对每个组合跑 3 个崩溃点（0.3 / 0.6 / 0.95 乘以直线运行模型调用数），`assert_resume_equivalent` 不等价时以 `SystemExit` 中止生成——"8 组合 × 3 崩溃续跑变体全等"的说法与该流程的产物条件一致；常驻测试 `tests/golden/test_golden_matrix.py` 另在两个代表组合上每次测试重放一个崩溃点。
- 字节差异核对：金矩阵 fixture 差异经逐行过滤，除 `provenance.json` 的两行外全部是披露过的三段式（`status`/`success` 删除、`correctness` 加入、`schema_version` 1→2），`"correctness": true/false` 的新增恰好 78 处，与提交说明的 78 条一致。
- 语义仪器未触碰：`tests/golden/semantics.py`、`test_semantic_instrument.py`、`tests/fixtures/golden_semantic_baseline/` 均不在本提交差异内，与"E1-E6 对未触碰语义基线束通过"的声明一致（静态核对，未运行）。
- harness 插件改为产出 correctness True/False，其余流程（崩溃注入客户端、续跑命令同构）未动。

**5. 用量与账本口径**
- 用量账本：`_record_usage` 的标签从 `infra_error` 改为 `infra` / 新增 `unscored`，但唯一的状态消费方 `infra/usage.py:77` 只区分 `status == "ok"` 与其余（记入 `n_error`），TB 用量行的 meta 不含 `error` 键、落 UNKNOWN_ERROR_TYPE，迁移前后相同——记账口径零漂移。全仓库无按 `infra_error` 字符串过滤用量行的读取方。
- 无 reward trial 记 correctness=None 的记账：这类轨迹现在是存量单元，D3（`blocks/metrics.py:100-107`）把它计入 `infra` 计数、排除出 `valid`，`attempts == valid + infra` 不变量保持；缺失单元仍按"一次 infra attempt"计入，阶段 1/6 的 shortfall 机器形状不变（`stages/stage1_baseline.py` docstring 同步改写）。SearchQA 的辅助指标分母与主指标一致地排除无判分轨迹。
- 失败账本读取方：见细节-1，check 4 只按 `unit_id`/`stage` 对账，deadline_kill 挪位不需要同步。
- 复审 P3 临时行为的移除：reward>0 但无判分器证据现在记 None（迁移前临时算成功），提交说明明示为复原生移除。

**6. 跟随者与删除符号**
- `verdict_from_trajectory` / `verdict_from_attempt` / `RUN_STATUSES` / `TRAJECTORY_STATUSES` / `RunOutcome.infra_error` 在 src、scripts、tests 里已无残留调用（只剩 docstring 里的退役说明与"确认已删除"的钉子测试）。
- `core/contracts.py` 零改动（`RunRecord.correctness` 由前置项 R0 落下），reports 的两个映射函数只读既有字段。
- `_strong2_weak2` 的排序键换成 `correctness is not prefer_success` 后对已存数据行为不变（None 排在最后；迁移前 None 的等价物从不落盘）。
- 归档验证器保留 `classify_result` 的历史视角是刻意设计（`scripts/analysis/tb_archive_validate.py` 只读历史归档），提交说明明示。

## 附注

- 本报告为纯静态评审：提交说明里"2323 → 2325 passed"、ruff 全过等运行结果未复跑，只核对了与这些声明对应的代码与测试结构。
- tb21_f3_baseline.py 第 308 行注释仍写着"没有 infra_error"字样，指的是 F3 归档数据的性质（该归档确无无判分单元），措辞停留在旧词汇，不影响行为。
