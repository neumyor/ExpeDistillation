# Wave 1B（R2b 词汇迁移）简洁性评审报告（B 路）

- 评审对象：分支 `feature/multi-method` 上的单个提交 `16c3791`（Migrate the
  run-status vocabulary to correctness and raise infra failures (R2b)）
- 评审维度：词汇残留、死代码与新冗余、可读性（初级程序员视角）、文档同步、
  测试迁移质量
- 方式：纯静态阅读，未运行任何测试，未改动任何文件（本报告除外）
- 日期：2026-10-16

## 总体判断

迁移本身做得干净、彻底：`RUN_STATUSES` / `TRAJECTORY_STATUSES` 全仓库零残留，
`RunOutcome.infra_error` 工厂与 `verdict_from_trajectory` / `verdict_from_attempt`
两座桥接删除后无任何调用方，新旧词汇没有在活代码路径上并存，
`correctness=None` 的每个消费点都带了自解释的注释。
约 25 个测试文件的迁移是完整的一次性替换，断言与新词汇一致。

需要处理的问题集中在"边角"：1 处会让 smoke 测试一跑就崩的半迁移断言（阻断级）、
2 处同文件/同管线内新旧两套 `None` 语义并存的契约文字（偏移级）、
若干注释与文档的旧词残留（细节级）。都属于一行到几行就能修完的范畴，
不动摇 R2b 的结构。

## 逐条发现

### B-1（阻断）smoke 测试仍断言 `outcome.status`，一跑就会 AttributeError

- 位置：`tests/unit/benchmarks/test_tb_plugin.py:943`
- 证据：
  ```python
  outcome = plugin.run(task, handle, "", tmp_path / "smoke")
  # The smoke asserts only that the link works end to end; a model failure
  # (reward 0) is still a successful smoke of the plumbing.
  assert outcome.status in ("success", "failure", "infra_error")
  assert outcome.meta["run_id"].startswith("ed_fix-git_smoke1")
  ```
  R2b 之后 `RunOutcome`（`src/expedistill/benchmarks/base.py:79-100`）只有
  `steps / correctness / meta` 三个字段，`.status` 会直接抛 `AttributeError`。
  该测试只在设置 `EXPEDISTILL_TB_SMOKE_MODEL` 时才运行，所以
  "2325 passed + 3 deselected" 的验收门没有拦住它——这正是评审任务书里说的
  "半迁移的角落"：迁移漏掉了一个消费者，而它在默认测试集里是隐形的。
- 直接简化方案：删掉 `assert outcome.status ...` 这一行。smoke 的本意是
  "链路通了就算过"——R2b 之下能从 `run()` 正常返回本身就说明链路通了
  （基础设施失败现在抛异常，根本走不到断言），留下 `run_id` 断言即可。
  若想保留一层语义，用 `assert outcome.correctness in (True, False, None)`
  也可以，但这行不提供任何额外信息。

### B-2（偏移）`RunRecord.correctness` 的 docstring 仍是迁移前的窄语义，与同管线的 `Trajectory` / `RunOutcome` 直接矛盾

- 位置：`src/expedistill/core/contracts.py:91-93`
- 证据：
  ```
  #: The benchmark's own scorer verdict. ``None`` ONLY means the
  #: benchmark has no scorer; True/False is the scorer's answer, never
  #: an acceptance conclusion (that is acceptance's job).
  correctness: Optional[bool]
  ```
  而同一管线上，`Trajectory.correctness`（`core/models.py:137-150`）和
  `RunOutcome.correctness`（`benchmarks/base.py:81-95`）的 docstring 都已改为
  "None = 没拿到判分结果（无判分器的 benchmark，或虽有可能判分但这次没判出来）"，
  并且 `reports._run_record_from_trajectory`（`composites/reports.py:91-107`）
  会把 TB 伪零分 / trial 中途死掉这类"有判分器但没判出来"的 `None` 原样灌进
  `RunRecord`。也就是说，`contracts.py` 作为执行与验收之间的冻结契约文件，
  现在描述的是一个代码里不存在的窄语义。初级读者把两处 docstring 对着读，
  会得出"同一个字段两种定义"的结论。
- 背景：这不是实现走样。修复方案 C5（`docs/decisions/execute_learn_repair_plan_20261016.md:161`）
  原文就是"`correctness` 取 None 只表示该 benchmark 没有判分器"，本次提交
  有意把 None 扩成了"没拿到判分结果"（log.md 摘要里如实披露了），但没有同步
  契约文字。属于"实现先走、文档留旧"的欠账。
- 直接简化方案：把 `contracts.py` 这两行注释改成与 `models.py` 一致的一句话
  （"``None`` = no scorer verdict came back: a benchmark without a scorer,
  or a run that completed without one"）。若还想保住 C5 原文的权威性，
  应在修复方案文档里补一行"R2b 实施时 None 语义扩大，理由见 log.md"，
  二选一，不能两处各说各话。

### B-3（偏移）`benchmarks/base.py` 同一个文件里，抽象方法 `run` 的 docstring 还是旧词汇

- 位置：`src/expedistill/benchmarks/base.py:303` 与 `base.py:284`
- 证据：
  ```python
  @abc.abstractmethod
  def run(self, task, handle, skills_block, workdir) -> RunOutcome:
      """Execute one task once; infra failures come back as status."""
  ```
  同一个文件 200 行之前，`RunOutcome` 的 docstring 已经写明
  "An infrastructure failure ... never lands here: the runner raises
  ``InfrastructureError`` and K1 retries the unit"。这是本次提交重写过的文件
  里新旧两种说法并存。另有一处 `base.py:284`
  "the primary one (which stays status-based)"，"status-based" 也是旧词，
  现在主指标是判分器口径（`correctness`）。
- 直接简化方案：两处各改一词——
  `run` 的 docstring 改为 "Execute one task once; infra failures raise
  ``InfrastructureError``"；"stays status-based" 改为
  "stays the scorer verdict (`correctness`)"。

### B-4（细节）README 阶段 6 一处"EM / 状态口径"漏改，与阶段 1 的孪生段落不一致

- 位置：`README.md:943`（阶段 6 辅助指标段）
- 证据："主指标（EM / 状态口径）始终是主指标"。阶段 1 的同一段话
  （`README.md:625`）本次已改成"主指标恒为判分器口径（`correctness`）"，
  阶段 6 这一处被漏掉了。
- 直接简化方案：把"EM / 状态口径"改成"判分器口径"，与 625 行同词。

### B-5（细节）测试注释里的 "infra_error trajectory" 说法残留（3 处 + 2 个测试名）

- 证据：
  - `tests/unit/blocks/test_metrics.py:177`：注释 "A stored infra_error
    trajectory (another producer than K1) fills its unit..."——该轨迹现在
    是 `correctness=None` 的记录，"infra_error trajectory" 读起来像一种
    已经不存在的落盘状态。测试名
    `test_expected_counts_stored_infra_trajectory_once` 同理。
  - `tests/composites/test_live_acceptance.py:628`：docstring "A round store
    holding an infra_error trajectory (impossible through K1...)"。
  - `tests/composites/test_batch_execute.py:1053`：注释 "K1 must get a stale
    infra_error and run a fresh trial..."——R2b 之下 K1 不是"收到"一个
    infra_error，而是捕获插件抛出的 `InfrastructureError`。
  - 测试名残留：`tests/unit/blocks/test_rules.py:124`
    `test_strong2_weak2_takes_two_per_role_with_status_preference`（偏好
    现在挂在 `correctness` 上）、`tests/unit/blocks/test_execute.py:1`
    模块 docstring "status mapping"。
- 直接简化方案：注释改成 "a stored unscored trajectory
  (`correctness` None, another producer than K1)" / "K1 must catch a
  stale-run `InfrastructureError`..."。测试名可以顺手改，也可以留着——
  metrics 的 `infra` 计数名是刻意保留的口径词，测试名沿用它说得通；
  但注释里那种"落了一条 infra_error 轨迹"的旧状态说法应当清掉。
  `test_tb_plugin.py` 里 `test_run_truncated_result_json_is_infra_error`
  这类名字不算残留：它们断言的是 `pytest.raises(InfrastructureError)`，
  "infra error" 在这里是普通英语，语义没漂。

### B-6（细节）`verdicts.py` 模块 docstring 的"五处判定点"清单第 1 条已过时

- 位置：`src/expedistill/composites/verdicts.py:24-26`
- 证据：清单第 1 条仍写 "``benchmarks/tb/plugin.py``'s ``classify_result`` --
  W1-B (R2): the plugin reports facts ... this module's named rules draw the
  conclusion"。R2b 之后 `classify_result` 已与执行路径完全脱钩，只剩
  归档校验器一个用户；把它继续列为"G1 的在途判定点"会误导读者去执行路径上找它。
- 直接简化方案：该条补一句 "（R2b 后 classify_result 已退役为归档校验器的
  历史镜片，执行路径只报事实）"，或把五点清单整体改成历史注记。

### B-7（细节，可不改）`InfrastructureError` 的 docstring 没提它的新角色

- 位置：`src/expedistill/infra/reliability.py:42-44`
- 证据：docstring 仍是 "Sandbox, network or timeout failure: the work is
  valid, the machine or service was not. Retried without bound from the
  caller's side."。R2b 给这个异常加了一层新身份——它是插件与 K1 之间的
  基础设施失败通道（替代了落盘的 `infra_error` 状态），这一点目前只写在
  `benchmarks/base.py` 与 `batch_execute.py` 的 docstring 里。
- 方案：可在此加一句 "R2b: benchmark plugins raise this instead of storing
  an infra status; K1 catches it and retries the unit"。也可以不改——
  infra 层不引用 benchmark 契约是合理的分层考虑；但"Retried without bound"
  与 batch_execute 的 `infra_retries`（默认 2 次，耗尽记失败账本）连读时
  值得一句话把"单次调用内有限重试、跨续跑无限再试"讲清。

### B-8（细节，记录一个有意为之的重复，不要求改）

- 位置：`src/expedistill/benchmarks/tb/plugin.py`
- 观察：`classify_result`（292-329 行，历史镜片）与 `result_facts`
  （259-289 行，活路径）各有一段几乎相同的 reward 解析 + exception 摘取
  代码（约 12 行）。让 `classify_result` 内部调用 `result_facts` 可以消掉
  这段重复；但历史镜片的要义是冻结——它复现的必须是归档当天的解析行为，
  挂到会演进的活函数上反而危险。现状加上 `classify_result` docstring 的
  "kept ONLY as the archive validator's historical lens" 说明，是合理的取舍，
  评审不要求改。唯一建议：这两段重复目前没有任何注释点破，初级读者容易
  以为是漏改。在 `classify_result` 的 reward 解析处加一行
  "frozen copy of result_facts' parsing, on purpose" 即可。

## 核对通过清单

以下各项逐一查过，全部通过：

1. `RUN_STATUSES` / `TRAJECTORY_STATUSES`：src/、scripts/、tests/ 全仓库
   零引用，是删除而非弃用。
2. `RunOutcome.infra_error` 工厂：全仓库零调用，确已删除。
3. `verdict_from_trajectory` / `verdict_from_attempt` 桥接：删除后无调用方，
   `__all__` 同步更新；`reports.py` 的 baseline/live/exam 读取器全部改走
   `verdict_from_run`，单一判定入口成立。
4. `classify_result`（三值词汇的最后栖息地）：生产代码里仅剩
   `scripts/analysis/tb_archive_validate.py` 一个用户；docstring、
   其专用常量（`_MODEL_EXCEPTIONS`、`_TMUX_ERROR_*`、`_PYTEST_COLLECTED_RE`
   的分工注释）、以及钉住它的测试注释三处都写明了"历史镜片"身份。
   src/ 里其余 "infra_error" 字样全部属于 v1 读取迁移（`core/models.py`
   `_migrate_v1_trajectory_row`、`assemble_reuse` 的 docstring）与
   使用账本的披露性注释，均属正当历史读取。
5. scripts/ 单列：`scripts/analysis/tb21_f3_baseline.py:308` 的
   "没有 infra_error" 是对历史 campaign 报告文本的复述，
   `tb_archive_validate.py` 依赖 classify_result 属披露过的设计，无问题。
6. meta 双字段检查：TB 插件写进 meta 的是单一事实集
   （`reward` / `verifier_ran` / `exception_type` / `exception_message`）
   加运行簿记，没有新旧两套并存。
7. `correctness=None` 的消费点全部自解释：`blocks/metrics.py`（valid 判据
   注释）、`blocks/rules.py:213-222`（未判分轨迹排序靠后，注释已改写）、
   `composites/segment_checked.py:174-180`（trajectory_success 判据注释）、
   `benchmarks/searchqa/plugin.py` aux 守卫、`composites/live_attribution.py`
   失败视图过滤、`blocks/view.py` `final_status_word` 的冻结映射（None ->
   "unknown"，且 docstring 写清了 E4 逐调用钉死、改词即新实验的理由）。
8. `RunOutcome` 三字段形状：没有过度包装；`__post_init__` 的布尔守卫
   （非 True/False/None 即 `CodeBugError`）是恰当的最小校验。
   `reports.py` 的两个 `_run_record_from_*` 适配器是抵达单一判定入口的
   最短桥，它们填充的 `skills_block=""`、`dispatch_index=0` 等字段具名规则
   根本不读，属于 RunRecord 既有形状的要求，不算本次引入的包装。
9. `batch_execute` 的重试触发：从"检查 outcome.status"改为捕获
   `InfrastructureError`，游标推进逻辑（token 花掉即推进）语义逐位保持，
   docstring 的三条相关段落（重试、游标、Stopping）都已同步。
10. scorer 规则的 None 分支从 `CodeBugError` 改判 `RUN_INVALID`，
    basis 文本 "no scorer verdict came back for this run" 自解释；
    `attempts == valid + infra` 不变量在 `metrics.py` 的 docstring 里
    重新讲了一遍，D3 形状未动。
11. v1 读取迁移：success/failure 无损映射、infra_error 响亮拒载、错误消息
    指明"K1 契约下本不该存 + 续跑需留在原维护分支"，与 AGENTS"修改数据对象"
    规则一致；测试（`test_models.py:119-155`）把三条路径都钉住了。
12. README / AGENTS.md 同步：E1 表、K1 表、重试游标、对象 schema、
    Trajectory v2 迁移注记、AGENTS 第七节插件契约均准确
    （唯一漏网是 B-4 的阶段 6 一处）。
13. 测试迁移整体质量：抽查 metrics / rules / view / execute / base /
    tb_plugin / react_loop / live_acceptance / reports / models 等文件的
    diff，断言全部随词汇走（`"success"` -> `True`、`"infra_error"` -> `None`
    或 `pytest.raises(InfrastructureError)`），函数签名参数名、fixture
    命名同步更新，没有"名字叫 test_status_xxx、断言 correctness"这类
    挂羊头卖狗肉的半迁移（测试名层面的轻微残留见 B-5，属措辞而非语义）。

## 结论

R2b 的迁移是一次完整、自洽的词汇替换，执行路径上没有留下任何会改变行为
的旧词汇分支。需要动手的清单很短：B-1 必须修（smoke 测试一跑就崩），
B-2、B-3 应当修（契约文字与代码矛盾，各一两行），B-4 至 B-8 属于
顺手即可的措辞清理。修完 B-1 至 B-4 后，本提交即可视为通过简洁性评审。
