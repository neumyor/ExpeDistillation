日期：2026-10-16
一句话说明：执行—学习重构 M3 步骤的验收评审 C——续跑契约与 M4 前置。
对应 log.md 条目：2026-10-16 M3 学习侧抽取。

---

## 0. 总评

**结论先行**：M3 是行为保持步骤，我按"续跑契约零漂移 + M4 前置干净"两条线核过。
**续跑侧全部成立**：对 95442b9..d80d6d3 逐 hunk 审计（K4 十三处、K6 九处、K5 四处、
三驱动各一二处），每一处改动都只把布尔/元组判定换成 decision 的具名规则或常量
re-export，**所有携带持久化次序的代码路径一字未动**——K4 的 `_rewrite_round`
逐 try 账本与 `_next_round` 版本锚、K6 的 verdicts append-once / 首判为准 /
`_apply_revisions` 版本重放守卫 / no-op 守卫重算补齐 / G10 耗尽"终态先于回执"次序、
K5 的 `commit()` 四步（journal 先于效果）全部原样；两套崩溃续跑套件零行改动且
**亲跑通过（236 passed）**。**验收硬闸逐项亲跑复现**：全套 2039 passed + 3
deselected（52.8 秒）、金矩阵 11 passed、fixtures 最后改动仍 32d6c86（M3 diff 中
fixture 文件数为 0）、ruff E9/F All checks passed、test_layering 3 passed、工作树
干净。**读取器只读性成立**（唯一潜在写路径被双重豁免，见琐细 C-4）。**词表搬家
全库无断**。唯一的实质缺口在 M4 前置：**exam 报告行的 attempt 无版本标识且读取器
无相位键**（C-1，偏移约束）——它不破坏 M3 的任何行为（新代码、无消费者、金矩阵
零漂移），但 M4.3 的 L 相位若照现状接线，"本版答对 → keep"的判定与"L 相位修订
落库后的崩溃续跑"都无法只从报告行分辨，接线前必须补（一行级改动或对齐 live 侧的
from_version 重放守卫）。值得强调：该缺口在方案 §3 的行字段表原文里同样存在
（attempt 括号里只有 verdict/judge_reason/weak_answer/来源指针，没有 version），
是计划与实现共有的洞，M3 实现还额外偏离了"本相位"的字面（见 §4）。

审阅纪律：只读本仓库 + 只写本文档；未进入 ../ExpeDistillation-TB-dev，未触碰任何
runs/（merge_check 可达性实验的配置副本写在 /tmp，未进仓库）。

**M3 验收：通过**（C-1 为 M4.3 接线前置收口项，不阻塞本步；裁决见 §9）。

---

## 1. 续跑协议的保全（核查项 1）——逐 hunk 审计

对 `git diff 95442b9 d80d6d3` 的全部源码 hunk 逐条核对（`-U0` 列 hunk 后逐个读
上下文）：

**K4（composites/acceptance.py，13 个 hunk）**：docstring 1 处、import 1 处，其余
11 处全在 `accept_skill` 的判定分支：

- `_find_accepted` 命中：`if accepted is not None` → `if decision.exam_stored_pass_action(accepted is not None) is not None`——`exam_stored_pass_action(True)` 恒返 `Action(KEEP)`，双重否定后**逐位等价**；
- 裸答判定：`if bare.judge_verdict` → `exam_bare_answer_action(bare.judge_verdict)`（`True→Action(DELETE, weak_already_knows)`，`False/None→None`），`discard_reason` 取 `action.reason`，值不变；
- 泄题分轮：`if last_round` → `action.kind == decision.DELETE`；来源选取 `last_with_skill if ... else bare` → `exam_voided_rewrite_source(...)`（同函数体逐字平移）；
- 带技能作答：`if attempt.judge_verdict` → `action.kind == KEEP`；`if last_round` → `action.kind == DELETE`，reason 同值；
- 循环落空兜底：`"retry_exhausted"` 字面量 → `decision.RETRY_EXHAUSTED`（同值）。

**未触碰**：`_rewrite_round`（REWRITE_FAILURE_LEDGER 逐 try 追加、预算跨崩溃记账
——方案 §3 明文这条留在 K4，理由与实现一致）、`_next_round`（版本号 − 1 + 耗尽数
= 已关闭轮数，成功改写的续跑锚）、`_find_leak_verdict` 首判语义、leak 账本
append-once、`_exam_attempts`/`_find_round_attempt` 的补齐读取。

**K6（composites/live_acceptance.py，9 个 hunk）**：import、6 个词表常量与
`_ALL_SUCCESS_SAFE_RULES` 改为 decision 的同名 re-export（`is` 同对象，见 §5）、
`_judge_round` 全洞短路（`all(unit in skip ...)` → `live_all_holes`，同谓词）、
`_attribute_group` 耗尽判据（`any(exhausted) or len >= _ATTRIBUTION_ATTEMPTS` →
`live_attribution_exhausted`，同表达式）、`_no_op_guard` 的 sha 比较（`!=` →
`not live_no_op_fires`，守卫的重算补齐与丢弃循环原样）、`_finalize_group` 决策段
（`Optional[Tuple]` → `live_final_action` 返回的 `Action`，方向映射
`KEEP→accepted / DELETE→discarded`、`reason or ""` 与旧元组的 `""` 兜底逐位同；
该核由 M2 的 50 例受控组合测试直驱，全套通过）。**未触碰**：`judge_round` 的
verdicts append-once 与重算比对、`_apply_revisions` 的版本重放守卫
（`from_version` 三态：相等→应用、+1→already_revised、其他→CodeBugError）、
`_revise_one` 的耗尽"仅该技能弃"次序、`_discard_group` 的终态先写。

**K5（composites/curation.py，4 个 hunk）**：签名加 `merge_check: str = "exam"`
（缺省 = 现行为逐字节不变）、docstring、`ConfigError` 值校验、合并单元 worker 内
回归考分支。**`commit()` / `record_merge()` / `_apply_kept` / `_apply_reverted` /
`_recover_pending_merges` 一字未动**——none 模式下单元 worker 只是把
`_regression_passes`（及其 ModelOutputError→regression_error 的改道）换成
`passed, reason = True, ""`，随后 `return {"status": "kept", ...}` 走的是与 exam
模式**同一条** commit 路径：**journal（`record_merge` → `_write_log`）仍然先于
`_apply_kept` 的库效果落盘**，崩溃窗口与补齐语义完全一致（崩溃在 commit 前：
pending 合并 + reuse 路径复活；崩溃在 commit 后：journal 已记，续跑幂等跳过）。
复用的 pending 合并在 none 下同样免 G8 免回归考，与"pending 合并已过 post-check"
的既有裁定一致。G8 marker 闸（pre/post-check）与预算硬阀（commit 后按组重算 token）
均在 none 分支之外，不受影响。

**三驱动**：exam.py 两处 `MODEL_OUTPUT_DISCARD` → `decision.MODEL_OUTPUT_ERROR`
（同值 `"model_output_error"`，崩溃补齐分支"discard 是真相、账本行是回执"的次序
原样，`__all__` 中无消费者的死名删除安全——全库 grep 仅一篇历史评审文档提及）；
live_run.py 入口护栏（strict 短路改调 decision，短路求值次序与 KeyError 行为不变）
与两处注入超限分支改调 `live_injection_overflows`（`max_injection_tokens is not
None` 的 None 语义保持）；agentbrew.py 仅透传 `params.merge_check`。

**亲跑**：`pytest tests/composites/test_acceptance_resume.py
tests/composites/test_live_acceptance_resume.py` → **236 passed**；
`git diff 95442b9 d80d6d3 --stat -- tests/composites/test_acceptance_resume.py
tests/composites/test_live_acceptance_resume.py` → **空输出**（零行改动）。

**核查项 1 结论：续跑契约零漂移，成立。**

---

## 2. 金矩阵硬闸亲验（核查项 2）

| 检查 | 命令 | 结果 |
|---|---|---|
| 全套 pytest | `.venv/bin/python -m pytest -q` | **2039 passed, 3 deselected**（52.82s），与声称逐字一致（基线 2008 + 新增 31） |
| 金矩阵 | `pytest tests/golden -q` | **11 passed**（0.73s） |
| fixtures 零改动 | `git log -1 -- tests/fixtures/golden` | **32d6c86（M1 提交）**；M3 diff 的 name-only 里 fixtures 路径计数 **0** |
| ruff | `ruff check --select E9,F src/ tests/ scripts/` | **All checks passed** |
| test_layering | `pytest tests/test_layering.py -q` | **3 passed** |
| 工作树 | `git status` | **nothing to commit, working tree clean**，HEAD = d80d6d3 |

**核查项 2 结论：全部亲验通过。**

---

## 3. 读取器不落盘的证明（核查项 3）

**reports.py 全文核查**：grep 全部写路径关键词（write/append/save/open/tempfile/
mkdir/touch/unlink/rename/os./shutil）——正文命中的 `append` 全部是**内存 list 的
`rows.append`**，无一磁盘写。三读取器只调用只读面：`store.load/ids/exists`、
`read_jsonl`、`shortfall.stage1_accepted_unit_ids`（读 shortfall 记录，缺失返
[]）、K4 的 `_exam_attempts/_find_bare/_find_leak_verdict/_last_with_skill/
_skill_records`、K6 的 `_ledger_path/round_dir/_first_row_from/_audit_row_from/
_trajectories_from/pending_groups`——逐一读了函数体确认只读。路径推导**全部复用
K4/K6 的 helper，零复制**（读取器与写入方对"文件住哪"不可能产生分歧），这正是
方案 D3 的要求。

**唯一的潜在写路径**：`live_report → _trajectories_from → Store(round_store_dir)`
——`Store.__init__` 会 `mkdir(parents=True, exist_ok=True)` 建五个 LAYOUT_DIRS。
但它被**双重豁免**：`_trajectories_from` 先检查 `round_store_dir.exists()`（不
存在即 CodeBugError，不构造 Store），而存在的轮仓必然由派发时的同一个 `Store`
类创建、五个子目录俱全，mkdir 是无操作。零执行终态行（`"verdict" in row`）根本
不触碰轮仓目录。结论：**不可达的写路径，只读性成立**。

**快照断言的真实构造**（tests/composites/test_reports.py::test_readers_write_nothing）：
夹具含轨迹仓、skills、exams、attempts、leak 账本、改写失败账本、verdicts、
attributions（测试内追加一条已落地计划行）、轮仓与冻结审计行——三读取器 +
`live_report(round_=1)`（不存在的轮）全部跑一遍；`_tree()` 快照的是
**run 树内全部文件的（相对路径, 全部字节）有序列表**，前后逐字节比对。范围说明：
**含全部文件字节与路径集合；不含目录节点本身、不含 mtime**——空目录被悄悄建出
的话该断言看不见（对应 C-4，建议 M4.1 仪器把目录清单纳入）。断言本身真实、非
恒真：夹具覆盖了读取器会触碰的每一类文件面。

**核查项 3 结论：只读性成立，快照断言真实（范围见 C-4 的目录盲区）。**

---

## 4. M4 前置干净度（核查项 4）

### 4a. decision 输入形状 ↔ 读取器行形状，逐函数对照

| decision 函数 | 需要的输入 | 行上的来源 | 对齐 |
|---|---|---|---|
| `exam_bare_answer_action(bare_verdict)` | 判过的裸答布尔 | `row["bare_verdict"]` | ✅ |
| `exam_leak_action(last_round)` | 本版首判 + 序列位置 | `row["leak_verdict"]`（K4 同款首判读取）+ 相位驱动持有的位置事实 | ✅ |
| `exam_answer_action(verdict, last_round)` | 最近判定的对错 | `row["attempt"]["verdict"]` + 位置事实 | ✅（无崩溃流程下成立，见 C-1） |
| `exam_stored_pass_action(passed_on_stored_version)` | **"在存量版本上"通过** | **行上无法判定**：attempt dict 不带 `skill_version` | ❌ → **C-1** |
| `exam_voided_rewrite_source` | 最近带技能 attempt / 裸答 | 读取器内部就用这条规则选 attempt（读写两侧不可能漂移，好设计） | ✅ |
| `exam_measurement_exhausted_action()` | 异常即事实 | G4/G5/审计耗尽由 A 相位驱动终态化，行经 `status` 透出；G3 耗尽（untestable）见 C-5 | ⚠️ |
| `live_final_action(rows, rounds)` | **verdicts 账本行**（带 `round` 键，append 序） | `live_report` 的行是**每 (组, 轮) 一行**、`round` 只在报告顶层——两者形状不接 | ⚠️ 非缺口，见注 |
| live 侧其余（`live_no_op_fires` / `live_zero_execution_short_circuit` / `live_injection_overflows` / `live_all_holes`） | 守卫时点的比较 / 判据 | 有意留在 K6/编排（P7a 已按方案免除行上的等价字段）；报告行 `verdict`/`better` 已够 L 相位做 keep/G10/delete 分发 | ✅ |
| baseline 报告侧 | task_select 命中 → add | `baseline_report` 的 `F` 就是用**同一个具名规则注册表 + 同一个 `metrics_block.failed_tasks`** 派生的（与 stage1 的 F 计算逐行对照一致），add 幂等跳过按方案留在 stage3 现语义 | ✅ |

注（live_final_action 与 live_report 不同形）：这不是缺口——`live_final_action` 是
K6 终局扫描（多轮裁决）的规则，现行消费者是 `_finalize_group`；v4 的 L 相位持有序列
位置事实，按行内 `better`/`verdict` 直接分发即可，不需要多轮扫描。M4 接线时**不要**
试图把 live_report 的行喂给 `live_final_action`（`row.get("round")` 恒 None，会静默
返回 None）——这是给 M4.3 的一句明示，不是对 M3 的指控。

baseline_report 的全集推导核过：宇宙 = 存量轨迹 ∪ 已接受空洞（按 unit_id 反解
task_id），D 相位完整结束时每单元要么有轨迹要么有已接受空洞记录，故 store 推导
全集 == 冻结全集；未接受的 infra 空洞会让 D 以退出码 3 停下、L 根本不会跑——
读取时点前提成立。

**live 侧的 L 续跑锚已具备**：`live_report` 的 `attribution` 字段就是"已落地计划
行，续跑的 L 不得重问"（行 docstring 明写），配合 `_apply_revisions` 的
from_version 守卫闭环。**exam 侧没有对应物——这正是 C-1。**

**C-1【偏移约束】exam 报告行的 attempt 缺版本标识，读取器无相位键**
（不阻塞 M3，M4.3 接线前必须收口）：

- 事实一（实现偏离方案字面）：方案 §3 行字段表写"**本相位** attempt"，实现为
  "任意轮最近已判 attempt"（`_ANY_ROUND = inf`），`exam_report(store)` 根本没有
  相位参数。无崩溃流程下两者重合（我逐步推演过：A 相位总答存量版本、修订只在 L
  内发生且决策在前），但字面偏离使"这一相位是否已作答"在行上不可判定。
- 事实二（计划与实现共有的洞）：attempt dict 是
  `{attempt_id, verdict, judge_reason, weak_answer}`，**无 `skill_version`**——而
  方案字段表的括号里同样只有 verdict/judge_reason/weak_answer/来源指针。于是两种
  状态在行上同形：①"存量版本（v）的最近判定为错，待 L 修订"；②"L 的修订已落库
  （v+1 已存、status 仍 pending）、下个 A 未答"。两者都是
  `attempt.verdict == False`。**方案里 `exam_stored_pass_action` 的语义（"在存量
  版本上通过才 keep"）从行上无法执行**；v4 的 L 相位照现状接线，遇状态②续跑会
  **二次修订**（版本 +2、多烧一次 G6、轮次记账漂移，E1 终库比对将失配）。v3 里
  这个窗口由 K4 的 `_next_round`（版本号 − 1 + 耗尽行数 = 已关闭轮数）锚住，方案
  没有把这个锚搬进行字段表。
- 修复方向（一行级，二选一或都做）：行内 attempt dict 补 `skill_version`（或
  `retry_round` 相位键）+ 对应单测；或 M4.3 给 exam 侧修订应用加 from_version
  重放守卫（对齐 live 侧 `_apply_revisions` 的三态守卫）。**E5 的崩溃变体必须
  含"L 相位修订落库后崩溃"窗口，否则该缺口对所有仪器不可观测。** 建议随 M4.1
  仪器先行一并落地。

### 4b. evidence / author 两族 Params ↔ §4 的 v4 字段表

- `evidence: {name: segment, K: 5, retry_limit: 2}` ↔
  `SegmentationParams(K, retry_limit=2)`：K 必填、retry_limit 缺省 2、越界
  ConfigError——**逐字段吻合**，且错误文案仍是 `methods.segmentation.*`（类是
  同一个对象，v3 配置路径报错不变）。
- `author: {name: distill_diagnose}` ↔ `AuthoringParams(exams=True)`：v4 字段表
  里 author 槽没有 exams，`{name: distill_diagnose}` 经 build_params 用缺省
  `exams=True` 构造，无未知键冲突。**exams 开关按方案保留到 M4、未被提前删**
  （方案 §3 五槽处置"exams 开关与 check_authoring 删除"是 M4 终态；M3 保留 +
  author 族条目 `check=None` 与"开关删除后 check 无所检查"一致）。M4.3 删 exams
  时注意：`check_authoring` 留在 authoring 槽包里随槽一起死，路径正确。
- `curate: {name: agentbrew, merge_check, token_budget, rounds}` ↔
  `AgentBrewParams(token_budget, rounds, merge_check)` **逐字段吻合**；
  `curation: none 带 token_budget` ↔ `NoneParams(token_budget)` 吻合。
- 注册表键集与 §3 表一致：`(evidence, segment)`、`(author, distill_diagnose)`
  名字逐字对上；两族条目 driver=None、无 check——"配置接线等 M4"与方案 §9 M3
  行一致；execution 族（real/exam）未加，属 M4 范围（exam 编排驱动 M4 才出现），
  正确。族条目测试经**真注册表路径**（resolve_params + SimpleNamespace）解析，
  槽/族两类条目产出 `==` 相等、槽包 re-export `is` 同一类——单一真相成立。

### 4c. merge_check 的 none 模式与 upgrade_v3

- **upgrade_v3 能表达**：§4 表明文 `live_run → L.curate.merge_check = none`、
  `exam → merge_check = exam`；加载期检查 #4（"merge_check: exam 要求序列含 exam
  执行相位"）覆盖了 exam 侧的交叉约束。live 侧无第二个前提。
- **第三个隐藏前提：没有找到**。K5 侧 marker 闸、预算阀、"bank is stale"检查、
  audit 工具（audit_live_run 查组终态与库迁移，不假设回归 attempt 存在）、
  stage5 摘要、装配器——均不依赖回归考必须发生。`_regression_passes` 的
  存量去重只在被调用时生效，none 模式下根本不进该函数。
- **修正任务书一个前提**：none 并非"无配置入口"。亲测：在 v3 配置
  （schema_version 3）的 `methods.curation` 下写 `merge_check: none`，
  `load_config` 通过（`_validate_methods` 只查五槽形状、非空 mapping 逐字冻结），
  `resolve_params("curation")` 解析出 `AgentBrewParams(merge_check='none')`——
  **该开关今天就已是 v3 可载入、可生效的配置面**（缺省 exam 不变，行为保持
  成立）。这把 README 的不同步从"未来问题"变成"现在就欠的账"，见 C-2。

**核查项 4 结论**：4b/4c 干净；4a 的 exam 侧行形状有实质缺口（C-1），live/baseline
两侧干净。

---

## 5. 词表搬家的影响面（核查项 5）

- **定义处搬家**：6 个 discard_reason 常量 + `STRICT_SHORT_CIRCUIT_RULES`
  （原 `_ALL_SUCCESS_SAFE_RULES`）从 live_acceptance 移到 decision.py，K6 原名
  re-export（`WEAK_ALREADY_KNOWS = decision.WEAK_ALREADY_KNOWS` 等 6 条 +
  `_ALL_SUCCESS_SAFE_RULES = decision.STRICT_SHORT_CIRCUIT_RULES`），且仍在
  K6 的 `__all__` 里——**所有 `la.X` 旧 import 路径原样可用**。
- **全库引用清点**（src/tests/scripts 全量 grep，含 scripts/audit）：
  - 代码侧全部经 `la.*` 或 `decision.*` 引用，无一自造第二定义；
  - `judge_round` 的 strict 族强制（live_acceptance.py:641-644）与 live 驱动的
    入口护栏经 `live_zero_execution_short_circuit` **读同一个 frozenset 对象**
    ——短路判据与判卷强制单一真相，方案 §3"解开 import 环"的目的达成；
  - `scripts/audit/audit_live_run.py` 用的是裸字符串
    （`"weak_already_knows"`、`"live_injection_overflow"` 等），不依赖常量名，
    搬家零影响；其裸串值与常量的同一性由
    `test_discard_reason_vocabulary_is_pinned`（值级）间接钉住；
  - README/runbook/decisions 里的词表全是裸字符串文档，不 import 常量，无断链；
  - exam.py 的 `MODEL_OUTPUT_DISCARD` 死名删除：全库无外部使用者（仅一篇历史
    评审文档提及），删除安全。
- **同一性断言亲验**：`test_vocabulary_is_the_one_definition` 对 6 常量 +
  frozenset 全部用 **`is`**（对象同一，非值相等）——是真同一性断言，不是弱化的
  `==`。

**核查项 5 结论：无一处断裂，单一定义成立。**

---

## 6. 测试证据真实性（核查项 6）

新增 31 条清点：test_family_entries 6 + test_decision 13 + test_reports 9 +
test_curation 2 + test_params 1 = 31（新文件 28 条实收 + 修改文件 3 条），与
log 声称一致。

**test_decision.py 预期值抽 4 条对照 M3 前原语义**（对照 diff 里的旧分支体）：

1. `exam_answer_action`：旧代码 `if attempt.judge_verdict: accepted`（对 →
   KEEP，不分轮）；`if last_round: discarded/retry_exhausted`；否则 `_rewrite_round`。
   测试四格（对/错 × 末轮/非末轮）预期 KEEP / DELETE("retry_exhausted") /
   REVISE / KEEP——**与旧分支逐格一致**，值是手写字面量。
2. `exam_leak_action`：旧代码 `if last_round: leak_audit 弃`，否则 voided 改写。
   测试两格一致。
3. `live_final_action` 优先级：零执行行任意位置即弃且取 append 序首行；最新有行
   轮定胜负（末轮 better→keep / miss→live_no_improvement；更早轮 better→keep、
   miss→None）。七格预期与 M2 已落库的 D1–D6 决策规则及 `_finalize_group` 旧实现
   逐格一致——且这 13 条之外还有 50 例 `test_finalize_semantics` 直驱核兜底
   （M3 后原样通过）。
4. `live_attribution_exhausted`：旧表达式 `any(exhausted) or len >= 预算`，测试
   三态（未满 False / 满额 True / 有耗尽行 True）一致。

**merge_check:none 的"空脚本句柄零调用"**：真·一调即炸——`FakeClient` 对耗尽
脚本 `raise AssertionError("FakeClient script exhausted")`，测试把
`answer_script=[]`、`judge_script=[]` 传入完整 G7/G8/G9 脚本的整理流程并跑通
全程，**任何一次回归调用都会炸掉测试**；另有正向断言
`handles[*].client.calls == []`、`store.ids("attempts") == []`（无 regression
attempt 落盘）、journal `merges == {attempted: 1, kept: 1, reverted: 0,
skipped: 0}`（同一 kept 记录）——强度充分。未知值 `"regression"` 在
`curate_bank` 与 `AgentBrewParams` 两层都被 ConfigError 拒绝（各有一条测试）。

**test_reports.py**：行内容逐字段断言（含 P7b 来源指针、首判 leak 行、零执行行
members 回退到当前 pending 组、已落地 attribution 计划逐字透传）、未知规则名
ConfigError、只读快照（§3）。夹具按现有组合测试的形态构造，非摆设。

**test_family_entries.py**：经真注册表解析；族/槽产出 `==`、re-export `is`、
未知名列出已知名、必填缺失响亮失败——把"搬家不是复制"钉成了契约。

**核查项 6 结论：测试证据真实，断言强度充分。**

---

## 7. AGENTS / README 同步（核查项 7）

- **AGENTS §四已同步**：树里新增 `decision.py`（"决策规则（M3 起的纯函数）……
  discard_reason 词表与 strict 短路规则集的定义处"）与 `reports.py`（"报告读取器
  （M3 起的纯函数，只读）：baseline/exam/live 三件"）；registry 行改
  "（槽位/族, 名字）"；segmentation/authoring 两行注明 re-export + M3；新增
  evidence/、author/ 两行（含"exams 开关随 M4 调用点迁移删除"）；curation 行
  注明 merge_check 开关 M3。与实际目录逐一对照，**无漏项**（composites 下
  shortfall.py、skill_status.py 本来就不在树上——M3 之前就漏，见 C-6）。
- **README 有一处该同步而未同步**：README §1.4 的 K5 行与阶段 5 段落把"回归考试
  覆盖合并所代表的全部原始 skill 的考题，任一不过就撤销"写成无条件行为，未提
  merge_check 开关——而该开关**今天就 v3 可载入可生效**（§4c）。方案 §7 把
  README §1–§3 的系统同步排在 M4.6，但那针对的是相位化重构；这个开关是 M3 落进
  代码的增量。→ C-2。
- README §1.7 把目录结构显式委托给 AGENTS.md，模块树本身不欠账；decision/reports
  未占 K 号，与"组合积木负责落盘和续跑"的 K 编号口径一致，不进积木表是对的。

**C-2【琐细】README K5 段落未提 merge_check（且开关已 v3 可达）**：建议要么现在
给 K5 行补一句（"`merge_check: none`（M3 起，live 序列用）跳过回归考试，marker 闸
与预算阀不变"），要么把 K5 段落列进 M4.6 文档同步的显式清单，防漏。

---

## 8. 发现清单

**【偏移约束】**

- **C-1**：exam_report 行的 attempt 无 `skill_version` 且读取器无相位键（§4a）。
  "本版答对 → keep"无法从行上判定；L 相位修订落库后的崩溃续跑在行上与"待修订"
  同形，M4.3 照现状接线会二次修订。计划字段表同样缺 version（共有缺口），M3 实现
  另偏离"本相位"字面。修复一行级（行补 `skill_version`/相位键，或 exam 侧修订加
  from_version 守卫）；E5 崩溃变体必须含修订落库后窗口。**不阻塞 M3**（新代码、
  无消费者、行为保持成立），**必须在 M4.3 接线前收口**（建议随 M4.1 仪器先行）。

**【琐细】**

- **C-2**：README K5 段落未同步 merge_check，而开关今天已 v3 可载入可生效（亲测
  load_config + resolve_params 通过；`_validate_methods` 非空 mapping 逐字冻结、
  build_params 认识新字段）。M4.6 前存在"配置能写、文档没说"的窗口。
- **C-3**：`AgentBrewParams` docstring 把 none 的理由写成"merges are proven by
  the round-1 group re-execution"——不准确：v3/v4 的 live 流程里整理都在全部
  live 轮之后，经再执行验证的是**来源候选**，合并产物本身在 C 之前只有 marker
  结构闸。方案对 none 的真实依据是 E8 白名单②（金矩阵不可观测 + 单测 + 真实 run
  披露），建议措辞改成"来源技能已经过 live 轮验证；产物只受结构闸与最终 C 检验，
  观测性由 E8 白名单②承担"。
- **C-4**：`test_readers_write_nothing` 的 `_tree` 只快照文件（路径 + 全部字节），
  不含目录节点与 mtime；读取路径唯一潜在写路径（`Store.__init__` 的 mkdir）被
  `exists()` 守卫 + 轮仓子目录俱全双重豁免，实际不可达，但断言看不见"空目录被建"。
  建议 M4.1 的仪器把目录清单纳入快照。
- **C-5**：§5 的"报告行 untestable → delete(model_output_error)"在行上只能以
  "无考题内容"（bare_verdict 与 attempt 皆 None）隐式表达；在"L 只在 A 完成后跑"
  的时点前提下属实可行（A 相位内 G4/G5/审计耗尽由驱动终态化、status 透出），但
  前提藏在流程里。建议 M4.3 把 untestable 判定写成显式谓词并钉测试。
- **C-6**：AGENTS §四树漏列 `composites/shortfall.py` 与 `skill_status.py`——
  M3 之前就漏、M3 恰好编辑同一节却没顺手补上。与 M3 无因果，记档待补。

**【赞同】（要点）**

- 逐 hunk 审计下的续跑协议保全：M3 对 K4/K6/K5 的每一刀都精确落在"无持久化次序
  的判定谓词"上，携带次序的逻辑（改写账本、版本重放守卫、终态先写、commit 四步）
  一处未碰；两套续跑套件零改动亲跑通过，是本步最硬的证据。
- "续跑语义留在调用方"的抽取纪律：改写预算耗尽（K4）、G10 计划应用（版本重放
  守卫即续跑协议）、G11 耗尽（丢弃先于回执）三条**留原处并写明理由**，与方案
  "拆开会破坏续跑记账"的判断一致——这是对"决策抽取"边界最正确的画法。
- `_finalize_group` 的决策改走 `live_final_action` 后，方向映射逐位等价（KEEP→
  accepted、DELETE+reason→(discarded, reason)、`reason or ""` 兜底），并有 M2 的
  50 例组合测试直驱核兜底。
- 读取器路径全复用 K4/K6 helper、exam 侧来源选取直接调用 decision 的同名规则
  ——读写两侧不可能各自漂移；live_report 按方案 P7a 不带字节等价字段、按
  B2-4 带共享 leak 账本，与方案逐条对上。
- merge_check:none 的 commit 次序（journal 先于效果）与零调用测试设计正确；
  缺省 exam 逐字节保持现行为（金矩阵零 diff 是硬证据）。
- 词表单一化用 `is` 断言钉住，K6 re-export 保住全部旧 import 面；
  `_ALL_SUCCESS_SAFE_RULES` 与决策规则共享同一 frozenset，判卷强制与入口护栏
  不可能分叉。
- evidence/author 两族 Params 经真注册表解析、与槽条目 `==`、re-export `is`，
  "搬家不是复制"成为契约；author 族保留 exams 到 M4 与方案一致，未被提前删。

---

## 9. 裁决

**M3 验收：通过。**

- 续跑契约零漂移在本步的主验收仪器上全部成立：金矩阵 11 项逐字节（fixtures 零
  改动）、两套崩溃续跑套件零行改动且亲跑通过、全部携带持久化次序的代码路径
  逐 hunk 核对未触碰、K5 none 模式的 commit 次序保持。
- M4 前置总体干净：4b（Params 形状）、4c（merge_check 的 upgrade 表达、无第三
  隐藏前提）通过；4a 的 baseline/live 两侧干净，exam 侧有 C-1 一处实质缺口。
- **C-1 是偏移约束但不阻塞本步**：它位于尚无消费者的新读取器里、不影响任何
  现行为；但它决定 M4.3 的 exam 侧"读报告 → 决策"能否接线，必须在 M4.3 开工前
  收口（最小修复一行级），且 E5 的崩溃变体必须覆盖"L 相位修订落库后崩溃"窗口。
  建议：随 M4.1（仪器先行）落地修复并附回退转红，届时按方案 §9 的"偏移约束修复
  后重审"只重审该点。
- 琐细五条（C-2~C-6）不阻塞，建议 C-2 在下次文档提交顺手补上。
