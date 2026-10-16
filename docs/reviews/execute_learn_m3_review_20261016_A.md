日期：2026-10-16
一句话说明：执行—学习重构 M3 步骤的验收评审 A——行为等价（决策改调逐 hunk 对照、留原处四条独立复核、读取器与 merge_check 语义核验、硬闸亲验）。
对应 log.md 条目：2026-10-16 12:02 M3 学习侧抽取完成。

评审对象：commit d80d6d3（`git diff 95442b9 d80d6d3`，21 个文件：10 src + 5 tests + AGENTS.md + log.md）。
契约：docs/decisions/execute_learn_implementation_20261015.md §3（决策清单 17 条、读取器表、五槽位处置）与 §9 M3 行。
方法：对 95442b9 的每个改动 hunk 逐行对照原分支的字面动作（kind/reason/后续调用/求值次序）；留原处四条用本人的控制流与续跑窗口分析独立复核（不复读 docstring）；硬闸全部本机亲跑。静态对照之外的行为证据是三件未改动的既有仪器：金矩阵（11 项，字节比对）、两套崩溃续跑套件、test_finalize_semantics.py 的 50 例语义钉（本评审未另做双实现执行比对，等价结论 = 逐 hunk 静态对照 + 这三件仪器全绿）。

---

## 逐项核查

### 1. 决策改调的行为等价（K4 / K6 / 两驱动逐 hunk）

**K4（src/expedistill/composites/acceptance.py），5 个替换 hunk：**

- accept_skill:338（resume 预检，E2 的存量通过）：原 `if accepted is not None:` → `if decision.exam_stored_pass_action(accepted is not None) is not None:`。True→`Action(KEEP)`→非 None，False→`None`，分支体一字未动。等价（decision.py:191-197）。
- accept_skill:349-356（E1 裸答）：原 `if bare.judge_verdict:` + 字面 `"weak_already_knows"` → `exam_bare_answer_action` 返回 `Action(DELETE, WEAK_ALREADY_KNOWS)`，`reason` 赋值同值同位置（decision.py:145,182-188）。合法状态下 `judge_verdict ∈ {True, False, None}`（blocks/exam.py:89-94 经 `_VERDICT_TO_BOOL` 只产真 bool；exam_once.py:298 直接赋值），`is True` 与真值判断逐位一致——【琐细 A】见后。
- accept_skill:382-386（E4/E5 泄露分轮）：原 `if last_round: discarded/"leak_audit"` → `exam_leak_action(last_round)`，DELETE→`action.reason`（="leak_audit"，decision.py:153,207），非 DELETE（REVISE）落空走作废改写——与原 if/else 同分支同序（decision.py:200-208）。
- accept_skill:390-393（泄题改写来源，P7b）：原 `source = last_with_skill if last_with_skill is not None else bare` → `exam_voided_rewrite_source(...)`，函数体逐字相同（decision.py:230）。
- accept_skill:408-427（E2 轮内通过 / E6 答错改写 / E7 答错终态）：原真值通过→accepted；`last_with_skill = attempt`；`if last_round:`→`"retry_exhausted"`；否则 `_rewrite_round` → `exam_answer_action` 的 KEEP/`DELETE(retry_exhausted)`/REVISE 三分支，赋值次序（`last_with_skill = attempt` 在 KEEP 之后、终态判定之前）与原代码逐位相同（decision.py:211-220）。
- **E7 空区间 fall-through**：accept_skill:427 `working.discard_reason = decision.RETRY_EXHAUSTED`——与 `exam_answer_action` DELETE 分支的 reason 是**同一常量对象**（decision.py:154 定义、:219 使用），retry_limit 收缩导致空区间时的终态字符串与轮内终态单一真相。核实成立。

**K6（src/expedistill/composites/live_acceptance.py），5 个替换 hunk：**

- :171-176 词表 re-export + :204 `_ALL_SUCCESS_SAFE_RULES = decision.STRICT_SHORT_CIRCUIT_RULES`：值不变、对象同一（见第 3 项）。
- :690（L15 全洞）：`decision.live_all_holes(expected_ids, skip)` ≡ 原 `all(unit in skip for unit in expected_ids)`；入参传的 `skip` 已是 set，函数内再 `set()` 一次幂等（decision.py:319-326）。触发时写 `live_undetermined` 行的代码一字未动。
- :875（L16 账本读取半）：`decision.live_attribution_exhausted(failures, _ATTRIBUTION_ATTEMPTS)` ≡ 原 `any(row.get("exhausted") for row in failures) or len(failures) >= _ATTRIBUTION_ATTEMPTS`，decision.py:338-339 逐字相同；触发后的整组补弃代码一字未动。
- :1271-1274（L12 no-op 判据）：`live_no_op_fires(rendered_block_sha=_block_sha(block), frozen_audit_sha=audit["injection_block_sha256"])` ≡ 原 `_block_sha(block) != audit["injection_block_sha256"]` 取反。**输入逐字节相同**：sha256(utf-8 编码的幸存者当前渲染块) 对 冻结 audit 行的字段，无第二套哈希计算（decision.py:283-292 为纯 `==`）；命中后的逐技能 `discard_skill(LIVE_NO_IMPROVEMENT)` 未动。
- :1308-1321（L1/L9 终态判定）：原 local 元组 `decision = ("discarded", row["verdict"])` 族逻辑 → `decision.live_final_action(rows, rounds)` 返回 `Action`。逐分支对照（95442b9 原文 vs decision.py:247-280）：零执行行（任意轮）→ `Action(DELETE, row["verdict"])` ≡ `("discarded", row["verdict"])`；末轮 better → `Action(KEEP)` ≡ `("accepted", "")`（reason None→`""`→返回体 `reason if reason else None`→None，与原 `""`→None 逐位一致）；末轮 miss → `Action(DELETE, LIVE_NO_IMPROVEMENT)` 同；早轮 better → KEEP 同；早轮 miss → break 后仍 None → pending 同；无行 → None → pending 同。原 local 变量名 `decision` 改 `action`，恰好消除了与模块级 `from ... import decision` 的遮蔽隐患。`direction/reason` 还原与 `_pending_group` 提交循环未动。

**两驱动（methods/acceptance/）**：

- exam.py：`MODEL_OUTPUT_DISCARD`（="model_output_error"）常量删除，两处使用改为 `decision.MODEL_OUTPUT_ERROR`（同值，:129）与 `exam_measurement_exhausted_action().reason`（E3 规则落点，:170）；终态先写、账本行回执的次序（:165-172）未动；`__all__` 同步收缩，全仓 grep 无残留消费者（95442b9 侧的 4 处使用全部内消）。
- live_run.py：:437-438 入口守卫（L13）与 :482、:612 两处注入超限（L14）改调 decision 函数。**L13 与原语义对照**：95442b9 live_run.py:431-435 为 `0 not in _verdict_rows(...) and rule_name in la._ALL_SUCCESS_SAFE_RULES and baseline_counts["success"] == baseline_counts["valid"]`；新代码保留第一个合取项，后两项换成 `live_zero_execution_short_circuit(rule_name=..., baseline_counts=...)` = `rule_name in STRICT_SHORT_CIRCUIT_RULES and baseline_counts["success"] == baseline_counts["valid"]`（decision.py:305-306）——两个操作数的内容与短路次序不变，且 `la._ALL_SUCCESS_SAFE_RULES is decision.STRICT_SHORT_CIRCUIT_RULES`（judge_round :641-644 的强制检查读同一对象），驱动入口与 K6 强制仍是同一集合。L14 两处 ≡ 原 `max_injection_tokens is not None and row["injection_tokens"] > max_injection_tokens`（decision.py:309-316 逐字）。

**同分支同序替换**声称逐 hunk 核实成立：所有 decision 调用都发生在原判定的同一时点（同一磁盘事实之后、同一副作用之前），无任何调用次序重排。

### 2. 留原处的四条（E8 / L10 / L16 / L17）——独立复核

- **E8（改写预算耗尽，acceptance.py:_rewrite_round:237-283）**：判定就是循环边界 `range(len(failed_tries), _REWRITE_ATTEMPTS)`，其中每次失败先追加 REWRITE_FAILURE_LEDGER 再试下一次——耗尽时的"处置"是 fall-through 返回 False（版本不变、轮已消耗），**没有独立的状态副作用可抽**。若把逐 try 追加逻辑一并拆出确会破坏续跑的预算续算；仅抽一个"预算已耗尽"谓词技术上可行且无害，但 `_rewrite_round` 的入口本身就是账本读取（没有 G10 那样的独立恢复入口分支可抽），抽出的谓词只是复述循环边界。**结论（留原处）正确**；docstring"拆开会破坏续跑"对谓词-only 拆法略有过述——【琐细 B】。读者侧需要的计数（耗尽轮数）已由 exam_report 的 `rewrite_exhausted` 承接。
- **L10（G10 计划应用，live_acceptance.py:1029-1121）**：`_apply_revisions` 的版本重放守卫（stored.version == from_version+1→already_revised；== from_version→应用；否则 CodeBugError，:1083-1092）就是半应用计划的续跑协议——每个条目的状态由 store 现值即时判定，纯函数拿不到"caller 已读好的事实"以外的输入形态；且按契约 §3"G10 把修改细化为逐技能计划——不建协调层"，计划应用是执行不是决策。成立。delete 落 `live_revision_deleted`（:1088）在该执行内。
- **L16（G10 循环内耗尽，:899-912）**：终态先写（`_discard_group`）在 exhausted 回执行之前——该次序是崩溃窗口内唯一的持久真相；账本读取半已抽出（live_attribution_exhausted）。拆法与 G10 的"读取半抽、写入半留"一致。成立。
- **L17（G11 耗尽，_revise_one:1210-1222）**：与 E8 同构（逐 try 追加 + 仅该技能弃、终态先写回执在后），组继续由 `_apply_revisions` 的 outcomes 流表达。成立。

**"该抽没抽"排查**：扫了 K4/K6 其余判定形状的逻辑——`_find_accepted`/`_find_bare`/`_find_round_attempt`/`_next_round` 是读取器不是动作；K6 `judge_round` 的两类 CodeBugError 断言是强制检查不是决策；`survivors_and_block`/`_audit_row` 是派生/冻结；live 驱动的轮序派发是编排（M4 相位驱动）。未发现借"留原处"之名藏匿的清单项——**§3 的 17 条全部有落点**（decision.py 模块 docstring 的逐条映射与代码一致，本评审逐条对过）：exam 8 条中 7 条抽（E8 留）、live 9 条中 6 条抽（L10/L16 循环内半/L17 留）。

### 3. 词表搬家（单一真相）

- 定义处：decision.py:145-150（六个 discard_reason）+ :153-154（LEAK_AUDIT/RETRY_EXHAUSTED）+ :162-163（STRICT_SHORT_CIRCUIT_RULES）。K6 侧 :171-176、:204 全部为赋值 re-export——两处引用解析到同一对象。
- `test_vocabulary_is_the_one_definition`（tests/unit/composites/test_decision.py:160-167）用 **`is`** 断言六个常量与 frozenset 的同一性：字符串常量的 `is` 在赋值 re-export 下平凡成立，但 frozenset 的 `is` 是承重断言；测试的真实效力是**回归钉**——今后任何人在 K6 侧"重新本地化"任一常量即红。有效。
- audit 脚本路径：scripts/audit/audit_live_run.py 读 `skill.discard_reason`（对象字段）并与字符串字面量比对（:265,280,291,321,341），不 import K6 常量；词表值未变，不受影响。K6 常量的既有消费者（live_run.py 的 `la.LIVE_NO_IMPROVEMENT` 等、tests 的 `from ...live_acceptance import (...)`）经 re-export 全部照常。

### 4. 读取器三件（composites/reports.py）

- **exam_report（:122-175）**：attempt 来源 = `_last_with_skill(attempts, skill_id, inf)` + `exam_voided_rewrite_source`（:152-155）。与 K4 语义的一致性逐崩溃窗口核过：K4 恢复路径的 `last_with_skill` 以 `start_round` 为界（排除未闭合轮自己的 attempt，因为循环经 `_find_round_attempt` 重找它）；读取器用 `_ANY_ROUND=inf` 把"未闭合轮已判 attempt"也纳入——而那正是 K4 恢复后实际会改写的来源（泄露判定先于 attempt，故泄露窗口内不存在本轮 attempt；答错窗口内 K4 重找的正是该 attempt）。裸答回退与 K4 相同。`leak_verdict` 走 K4 `_find_leak_verdict`（首判为准），`rewrite_exhausted` 与 `_next_round` 同口径（exhausted 行计数）。universe = `stage3_candidates`（含终态，`status` 字段承载 terminal 信息，符合契约行的 terminal 槽）。
- **live_report（:183-244）**：members 首选取冻结 audit 行的 `{"skill_id","version"}`（与 `_audit_row` 写入形状逐字段一致，live_acceptance.py:568），回退用 `pending_groups`——与 `survivors_and_block` 的成员派生是**同一个函数**；版本取存储现值。verdicts/attributions 经 `la._ledger_path`/`_first_row_from`（首行粘滞）读取；`result` 仅对已判行读轮仓（零执行终态恒空）、`better`/`verdict` 与 `_verdict_row` 的字段一致；不含 no-op 字节等价字段（P7a，docstring 明确）。
- **baseline_report（:59-115）**：F 集与 stage1 现行推导一致——同一 `metrics_block.failed_tasks(weak_metrics, get_task_select(rule), n_attempts=n_attempts_weak)`（reports.py:112-115 对 stage1_baseline.py:232-234），同一 `compute_task_metrics` 的 expected={task: n_attempts} 口径（metrics.py:64-116：缺失单元记 infra）；**accepted 洞的口径**：从轨迹集中剔除但任务保留在 expected → 每洞记一次 infra → `total` 恒为冻结采样深度（test_reports.py:176-178 实测 t2/t4 全洞行 total=3/3、t4 强臂 total=2），与 stage1"a durable hole stays a hole and counts as infra"逐位一致。task_select 规则只读 valid/success（rules.py:150-159），F 与 aux 无关。universe 由仓派生（存储轨迹 ∪ accepted 洞），未达成的任务不出现——读取器语义，docstring 已披露，不影响 F（无 valid 即不命中任何已注册规则）。
- **路径 helper 零复制**：grep 全 composites 无第二套路径字符串——reports.py 的 verdicts/attributions/audit/轮仓路径全部经 `la._ledger_path`/`la.round_dir`/`la.INJECTION_AUDIT_NAME`，exam 侧经 K4 的 `_skill_records`（内部 `_ledger_path`），baseline 经 `shortfall.stage1_accepted_unit_ids`。仓内既有的其他路径字符串（store.py KIND_DIRS、assemble_reuse 映射表、驱动各自的 `_ledger_path`——live_run.py:158 一处，95442b9 既有且都锚定 `la.LIVE_DIR_NAME`）非本次引入。测试侧一处路径重建见【琐细 E】。
- 只读性有专测钉住（test_reports.py:356-372 全树字节快照前后不变）。

### 5. merge_check（K5）

- **exam 模式逐字节一致**：diff 里 exam 路径唯一的改动是 `if merge_check == "none":` 分支包裹——原 `_regression_passes` try/except 连缩进带注释原样在 `else:` 里（curation.py:1216-1227）；`curate_bank` 新参缺省 `"exam"`（:904），AgentBrewParams 缺省 `"exam"`（curation/__init__.py），既有 v3 配置不带该键 → 走原路径。金夹具的 G7 恒空（harness.py:288 `similar_groups: []`）故金矩阵本就不覆盖合并回归——与 §8 白名单②"金矩阵不可观测、由 M3 单测承担"的预注册口径一致；exam 路径的行为等价由未改动的 test_curation.py 既有合并用例（kept/reverted/耗尽）全绿承担。
- **none 模式**：单元内 `passed, reason = True, ""`（:1212-1215）→ outcome `{"status":"kept", "reason":""}`，与 exam 模式全过时 `_regression_passes` 的 `(True, "")` 同形状；`commit` → `_apply_kept`（curation.py:363-372）→ 源 merged、产物 **pending→accepted**。marker 门（K5 前置 kind_conflict 与后置四段检查，:1152-1172）与预算阀（:1285 `count_bank_tokens <= bank_token_budget`）在两种模式下同一代码路径，未动。单测（test_curation.py:159-197）以**空 answerer/judge 脚本**证明零调用（脚本耗尽即抛），并断言 attempts 仓为空、库终态、merges 账本 `{attempted:1, kept:1, reverted:0, skipped:0}`——与声称相符。非法值在 Params（`__post_init__`）与 `curate_bank`（:934-936）两处响亮拒绝，与仓库 fail-loud 纪律一致。

### 6. 硬闸亲验（全部本机重跑）

| 闸 | 结果 |
|---|---|
| 全套 pytest | **2039 passed + 3 deselected**（53.2s）——与声称的计数逐位一致（基线 2008 + 新增 31 = 6 族契约 + 13 决策 + 9 读取器 + 3 merge_check） |
| 金矩阵 11 项 | tests/golden 收集恰 11 项（8 fresh 字节比对 + 2 crash-resume + 1 ignore-keys 防伪），**全过** |
| fixtures 零改动 | `git log -1 -- tests/fixtures/golden` = **32d6c86**（M1）；`git diff 95442b9 d80d6d3 -- tests/fixtures/golden` 为空 |
| 两套续跑套件 | `git diff 95442b9 d80d6d3` 对 test_acceptance_resume.py / test_live_acceptance_resume.py **零行改动**；显式重跑含 test_finalize_semantics.py 共 **286 passed**（50 例终态语义钉在新的 live_final_action 实现下原样通过——终态判定等价的第二重证据） |
| ruff（E9+F） | All checks passed |
| test_layering | 3 passed（decision.py 只依赖 core；reports.py 依赖 blocks/composites/core/infra，第 5 层内合法） |

既有测试仅 1 行删除（`_curate` 助手签名加缺省参），**零既有断言改动**。

### 7. 注册表两新条（evidence / author）

- **同一类对象（re-home 非复制）**：`test_rehomed_params_are_one_class_not_a_copy`（test_family_entries.py:54-57）用 `is` 断言槽位包的名字就是族包的类；本人另实测两份 v3 配置（tb21_split_live_run_v3.yaml、searchqa_skillopt_split_v3.yaml）`load_config` + `resolve_params("segmentation"/"authoring")`：解析出的对象即 `expedistill.methods.evidence.SegmentationParams(K=5, retry_limit=2)` / `expedistill.methods.author.AuthoringParams(exams=True)`，族解析（合成 methods 映射）与槽位解析逐值相等；校验报错文案原样随类搬移（"methods.segmentation.K ..."）——v3 路径字节不变。
- 新条目 driver=None、check=None，与注释一致：`_load_driver` 对它们响亮报错（"has no driver yet"）；v3 配置经 `_validate_methods` 的 METHOD_SLOTS unknown-key 检查（core/config.py:214）根本带不进 evidence/author 键——新条目在 v3 路径是惰性数据，接线确如声称留给 M4。
- **author 族保留 exams 开关**（author/__init__.py:22-23）：与 M3 行"配置接线等 M4"一致；§3"exams 开关删除"是五槽位处置的 M4 终态（调用点迁移时删），v3 消融对在 M4 前仍需该字段（stage3_generation.py:192 仍在读）。check 仍只在槽位条目上（族条目 None）——v4 接线在 M4 补上跨约束的落点，届时按 §4 检查 5 复核。

---

## 发现清单

**【偏移约束】**：无。

**【琐细】**（均不阻塞，无需重审；M4 顺手处理即可）：

- **A**：`exam_bare_answer_action` 用 `is True`（decision.py:186），原 K4 裸答分支是真值判断。合法状态（judge_verdict 恒为 bool/None）逐位等价；仅在损坏存储（非 bool 真值）下新代码更保守（不弃用、继续走轮）。与同文件 `_find_accepted` 既有的 `is True` 风格一致；行为无偏离，如实记录。
- **B**：E8 留原处的 docstring 理由略有过述："拆开会破坏续跑的轮次记账"只对连账本追加逻辑一起拆出成立；仅抽"预算耗尽"谓词不会破坏续跑，只是没有可抽的独立入口分支（循环边界即检查）。结论（留原处）与披露实质成立，措辞可在 M4 顺带收准。
- **C**：live_report 对零执行终态组的 members 回退：该组被 finalize_group 提交（全员终态）后 `pending_groups` 回退为空列表，历史成员集不可恢复——磁盘上本无此记录（从未派发、无冻结 audit 行），读取器无从编造；终态组不需要任何动作，无消费方损害。docstring 的回退语义描述准确，此边界可在 M4 接线时注记。
- **D**：baseline_report 不接 aux_scorer：F 与行内字段均不读 aux（契约行只要成功数/总数；规则只读 valid/success），当前无影响；若将来注册读 aux 的 task_select 规则需同步补接线。
- **E**：test_reports.py:232 测试侧重建了 K4 的账本目录路径（`Path(store.dir_for("attempts")).parent`）——src 侧零复制成立，但测试复制了一处路径知识，布局变动时需跟改。
- **F**：计数口径的小出入：实际为 12 个规则纯函数 + Action dataclass（log.md"7 条抽为具名函数"是 7 个清单条目对应 6 个函数、"13 个纯函数"的说法偏松）；17 条清单全部有落点，无实质问题。`_ANY_ROUND = float("inf")` 传给 int 注解参数（reports.py:52）、`exam_stored_pass_action(...) is not None` 的布尔包装——均有注释理由、行为等价，风格层面记录在案。

**【赞同】**：

- 决策改调逐 hunk 行为等价（第 1 项全部核实，含 E7 同常量、L13 strict 族同集合同次序、L12 同输入同哈希）；
- 四条留原处的判定经独立控制流/续跑窗口分析成立，无藏匿（第 2 项）；
- 词表单一真相 + identity 回归钉有效（第 3 项）；
- 读取器三件与 K4/K6/stage1 现行语义逐点一致、路径零复制、只读有专测（第 4 项）；
- merge_check exam 逐字节不变、none 语义与单测相符（第 5 项）；
- 硬闸六项亲验全过、计数与声称逐位一致（第 6 项）；
- re-home 非复制、v3 实测不变、author 族 exams 开关的保留与 M3/M4 分工一致（第 7 项）。

---

## 结论

**M3 验收通过。** 无偏移约束；6 条琐细均不改变行为等价结论，留 M4 顺手处理。M4 验收时建议重点盯三处与本步的接缝：① decision 函数被相位驱动真正调用（L5 的"还有后续验证轮"按 A/B 相位数传入，P1/I8）；② author 族条目接上跨约束 check（§4 检查 5）与 exams 开关删除；③ 读取器与相位目录布局的路径 helper 跟随（对象仓不动、逐相位账本迁移时的映射）。
