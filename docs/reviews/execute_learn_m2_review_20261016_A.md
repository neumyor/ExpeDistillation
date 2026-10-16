日期：2026-10-16
一句话说明：执行—学习重构 M2 步骤的验收评审 A——行为等价（三路评审之一，只审代码事实与行为等价，与其他两路互不通气）。
对应 log.md 条目：2026-10-16 10:56 — M2 执行侧抽取：delivery 三新驱动入注册表 + K6 测量核去轮化薄壳（commit 6b86e65）。

---

## 0. 评审对象与勘误记录

- 被审提交：**6b86e65**（父提交 85bfa4f）。评审 diff 范围 = `git diff 85bfa4f 6b86e65 -- src tests`。任务书原文写的 `git diff 9c8bf49 6b86e65` 方向反了：9c8bf49 是 M2 **之后**的纯文档提交（docs: close M1），该命令实际返回**空 diff**（两提交的 src/tests 树逐字节相同）。本评审全程使用正确的 85bfa4f→6b86e65 范围，评审对象与验收标准不受影响。
- 硬闸与全套测试在当前 HEAD（= 9c8bf49 = 6b86e65 + 纯文档）上运行，其 src/tests 内容与被审提交逐字节一致。
- diff 全集（`git diff 85bfa4f 6b86e65 --name-only`）恰为 13 个文件：log.md、`src/expedistill/composites/live_acceptance.py`、`src/expedistill/methods/{__init__.py,registry.py,acceptance/live_run.py,delivery/{__init__,none,per_skill,group_by_task}.py}`、`src/expedistill/stages/stage6_test.py`、`tests/unit/methods/{test_params.py,delivery/{__init__,test_drivers}.py}`。无其他路径被触碰。

## 1. 结论（先说判定）

**M2 验收通过。** 契约 §9 M2 行的三条硬闸（金矩阵逐字节零 diff、两套 resume 套件原样通过、pytest 全绿）全部由本评审独立重跑确认；核/壳拆分的行为等价不仅经逐行比对，还经本评审自己的**新旧双实现执行级比对**（旧实现从 85bfa4f 的 git blob 加载、与 HEAD 实现在相同种子上并跑）证实：13487 种 finalize 账本组合 + 22 个 judge/guard/attribute/apply 场景，返回值、落盘账本字节、终态与异常（类型+消息）全部相等。发现两条【琐细】，无【偏移约束】。

## 2. 核/壳拆分的行为等价（评审项 1）

### 2.1 对外签名逐字段不变

AST 签名比对（旧 = 85bfa4f，新 = HEAD）：六个公开函数 `judge_round` / `attribute_group` / `apply_revisions` / `no_op_guard` / `audit_row` / `finalize_group` 的形参名、顺序、keyword-only 标记、默认值**全部相同**（judge_round: live_acceptance.py:729-741；attribute_group:943-953；apply_revisions:1113-1123；no_op_guard:1279；audit_row:575；finalize_group:1348）。`__all__`（:116-141）未变，未导出任何私有核。

### 2.2 壳算出的路径与今天逐字节相同

各壳的路径推导与旧实现的内部推导一一对应（`_ledger_path`/`round_dir` 本身零改动，:249-263）：

- `judge_round` 壳（:757-763）：`verdicts_path=_ledger_path(store, VERDICTS_LEDGER)`、`round_store_dir=round_dir(store, round_)`、`audit_path=round_dir(...)/INJECTION_AUDIT_NAME` —— 与旧版读写的三个位置完全一致（旧 `_audit_row_of` 读 `round_dir/INJECTION_AUDIT_NAME`，旧 `_first_row`/`append_jsonl` 走 `_ledger_path`，旧 `_round_trajectories` 走 `round_dir`）。
- `attribute_group` 壳（:969-975）：attributions/failures 两账本 + 轮仓 + 审计四处路径同上。
- `apply_revisions` 壳（:1156-1163）：attributions + revision_failures + 轮仓。
- `no_op_guard` 壳（:1293-1296）：审计路径同旧 `_require_audit_row`。
- `finalize_group` 壳（:1376-1379）：verdicts 路径 + `rounds=_ROUNDS`。

私有核不引用任何壳层常量：AST 扫描 12 个 `_` 前缀核函数（`_judge_round`/`_attribute_group`/`_apply_revisions`/`_no_op_guard`/`_audit_row`/`_finalize_group`/`_revise_one` 及各 reader），**零处**出现 `LIVE_DIR_NAME`/`_ROUNDS`/`round_dir`/`_ledger_path`/`_live_dir`/`_check_round`。调用图：每个核只被自己的壳调用（reader 例外），无第二真相。`_check_round`（:266-270）与两个常量（:148、:195）留在壳层，docstring 注明 M4 随壳删除。

### 2.3 append-once、重算比对、"终态先写、行是回执"次序

- judge 核的 append-once 与重算比对（:715-726）：先算 `row`，再 `_first_row_from` 取存量首行，相等原样返回、不等抛 CodeBugError、无则 append——与旧版逐字相同。执行级验证：同输入连续调用两次，两次返回相等且账本恰 1 行；手工篡改存量行后重判，新旧抛出**相同消息**的 CodeBugError。
- 归因耗尽次序（:902-912）：先 `_discard_group`（终态先写）后 append `exhausted: true` 行（回执）；修订耗尽次序（:1213-1222）：先 `discard_skill` 后 append。均与旧版相同。
- `_revise_one` 的预算账本读写改经 `revision_failures_path` 参数（:1166-1171、:1174-1182），值即旧版 `_ledger_path(store, REVISION_FAILURES_LEDGER)`。

### 2.4 `_finalize_group` 轮判定泛化与 821 组合声称的核实

旧语义（85bfa4f：verdict 行扫描取首个带 `verdict` 键的行 → 否则轮 1 有行则轮 1 定胜负（better→accepted，miss→discarded(live_no_improvement)）→ 否则轮 0 有行且 better→accepted → 否则 pending）。新核（:1303-1345）泛化为 `rounds` 序列：`reversed(rounds)` 找最新有行的轮，末轮（`rounds[-1]`）定胜负、更早轮 better 接受 / miss 保持 pending。对 `rounds=(0,1)` 逐分支对照：三个分支（轮 1 有行 / 轮 1 空轮 0 有行 / 两轮皆空）与旧代码完全同构，`of_round[0]`/首行粘滞、verdict 扫描的 append 序敏感、`break` 时机均一致。

**821 比对的实现核实**：提交 6b86e65 与 log.md 均声称"用 821 种账本行组合穷举比对新旧决策逐位相等"，但该比对**不是提交内的测试，也没有留档脚本**——提交内新增测试只有 4 条驱动契约测试（tests/unit/methods/delivery/test_drivers.py）与 4 条 resolve_fixed 测试（tests/unit/methods/test_params.py:174-204），仓库与 tmp/ 内均无 821 相关工件（tmp/golden_check3 是 10-15 的 golden 校验输出，早于 M2）。即该数字是一次性脚本的结果，不可从仓库复现。

**本评审的独立复核**（取代性地验证该声称的实质）：把 85bfa4f 的 live_acceptance.py 整体加载为独立模块（依赖解析到 HEAD 安装包——M2 未改其任何依赖面），与 HEAD 实现在相同种子库上并跑 `finalize_group`，比对返回值 **和** 落盘终态（逐技能 to_dict）：

- 枚举空间：每轮行序列取自 {better=True, better=False, 零执行 verdict 行} 的长度 0–2 全排列（13×13 对），全保留序 interleaving（691 种基础组合），再在每种组合的每个位置插入越界变体行（round=2 的 better 行 / verdict 行、无 round 键的 better 行 / verdict 行）——共 **13487** 种组合，全部新旧相等（含异常路径）。
- 任务书点名的边界组合显式验证（新旧返回值逐位相同）：轮 0 better + 轮 1 miss → `discarded/live_no_improvement`（两技能提交）；轮 0 miss + 轮 1 miss → `discarded/live_no_improvement`；轮 0 miss 单独 → pending；轮 0 better 单独 → accepted；轮 0 miss + 轮 1 better → accepted；verdict 行与 append 序的先后（r0-V+r1-better 与 r1-better+r0-V）→ 均按首行 verdict 弃组——顺序敏感性与旧实现一致。

结论：821 声称的**实质为真**（本评审用更大的枚举独立证实），但证据本身未按外层 AGENTS"保存原始单元级证据"的规范留档——见发现 F1。

### 2.5 其余函数的执行级比对（本评审补做）

除 finalize 外，对同一双实现并跑了 22 个场景，比对返回值、账本文件字节、库终态、异常类型+消息，全部相等：

- judge_round 16 案：better=True/False、部分 shortfall（judged_with_shortfall 行）、append-once 幂等、篡改重算拒绝、全洞 short-circuit（live_undetermined）、不完整轮拒绝、weak_already_knows 合法/非法基线、overflow 有/无上限、未知 terminal、live_undetermined 不可由调用方断言、未知规则、缺审计行、越界轮（壳 `_check_round` 的 pinned 消息）、**round=1 路径**（round01 目录）、**round=1 审计缺失时错误消息中的轮标签**、带 aux_scorer 的行（aux 槽）。
- audit_row：正常行逐字段相等；空组 CodeBugError；越界轮拒绝。
- no_op_guard：同块→True+两技能 discarded(live_no_improvement)（终态相同）；块变化→False 且零改动；缺审计行→CodeBugError；round=1 路径；错误轮的审计文件→按轮标签报缺失。
- attribute_group：首判粘滞（存量行原样返回、零模型调用）；无组 CodeBugError；有组无行→按旧次序走到基线读取（InfrastructureError 消息相同，证实 `_first_row`→`_require_group` 的次序保持）。
- apply_revisions：无 attribution 行 CodeBugError；keep/delete 计划（动作次序、`live_revision_deleted` 终态）；终态技能 skipped_terminal；already_revised（版本 +1 跳过）；版本漂移 CodeBugError；计划指向不存在技能 CodeBugError；后置库状态相等。

## 3. resolve_fixed 正确性（评审项 2）

`src/expedistill/methods/registry.py:151-174`：

- **同表**：直接查 `_ENTRIES[(slot, name)]`（:71-78 新增三行 delivery 条目，`_Entry` 结构与现有条目一致）；未知名字的 ConfigError 消息格式与 `_entry_for`（:82-96）逐字相同且同样列出已知名字。
- **同 build_params 校验**：经 `_load_params`（:99-103）传空 raw 映射——带必填字段的条目照样响亮失败（测试 `test_required_params_still_fail_loudly` 用 retrieval 钉住，test_params.py:190-193）。
- **同懒 import**：`_load_params` 先 import params 模块、`_load_driver`（:106-112）再 import 驱动，与 `resolve` 的既有次序一致；`resolve` 本身的重构（改走 `_load_params`/`_load_driver`）操作序不变（params 模块 import → build_params → check → driver import）。
- **带 check 条目响亮拒绝**：:165-168，`resolve_fixed("acceptance", "live_run")` 抛 "carries a config cross-constraint check"（测试 test_params.py:196-199）。
- **stage6 裸臂 kwargs 与改前逐位相同**：旧版（85bfa4f stage6_test.py:330-350）向 batch_execute 传 15 个 kwargs，其中 `skills_injected=None, skills_block=""` 硬编码；新版（stage6_test.py:337-356）dict 装载同样的 13 项 + `resolve_fixed("delivery","none")` 解出的 `{"skills_injected": None, "skills_block": ""}`（none.py:20-26，`**kwargs` 绑定与键序无关）。`none` 驱动返回的 `skills_injected` 是 `None` 不是 `[]`（`[]` 是 retrieval 低于阈值任务的保留语义，retrieval.py:61），与旧裸臂语义一致——契约测试 test_drivers.py:43-52 钉住。解析点位于 `_arm_store` 之后、`batch_execute` 之前，`none.resolve` 不读 bank/tasks，无行为差异。

## 4. group_by_task 迁移的逐字等价（评审项 3）

- **闭包逐行对照**：新驱动 `group_by_task.py:30-45` 的 `skills_for_task` 与被删的 `_injection_resolver`（85bfa4f live_run.py:368-393）逐行相同——`ids.make_task_id(benchmark, task.task_id)` 前缀、`survivors_and_block(store, prefixed)` 取成员与块（"成员取 survivors_and_block"即当前存储版本，✓）、空组 CodeBugError 文案逐字相同（"has no pending skill group to inject; it must not have been dispatched"）、references 由成员的当前 skill_id/version 构造。`la.survivors_and_block` 改为直接 import 同一函数对象。原 docstring 的"冻结审计行字节等价跨 resume"语义完整保留在驱动模块 docstring（group_by_task.py:1-13）。原实现删除且不留转发 shim。
- **live_run 接法**：`live_run.py:138` `from expedistill.methods import resolve_fixed`；`_dispatch_round`（:767-799）其余部分与 85bfa4f 逐字相同（skip 集计算 → batch_execute 的全部 kwargs），解析器由 `resolve_fixed("delivery","group_by_task")` + `delivery.resolve(ctx, delivery_params, store, benchmark)` 获得（:766-767），解析在 batch_execute 之前、不读 store（闭包在 K1 内逐任务调用时才读）——与旧闭包的求值时点一致。异常类型不变（空组仍从 K1 内抛 CodeBugError）。85bfa4f 的测试零引用 `_injection_resolver`（`git grep` tests 无命中），"无 patch 目标需迁移"的声称属实。
- 分层：驱动（第 6 层）import composites（第 5 层）为向下依赖；live_acceptance 不 import methods（grep 零命中）；test_layering 通过。

## 5. per_skill（评审项 4）

- **委托同一渲染函数**：per_skill.py:27 `format_skills_block([skill])`，import 自 `expedistill.inject`（第 1 层 re-export）；K4 现用同一对象（composites/acceptance.py:89 `from expedistill.inject.skills import format_skills_block`、:398 `format_skills_block([working])`）。契约测试 test_drivers.py:55-63 直接断言与 `format_skills_block([skill])` 相等。
- **docstring 如实**：per_skill.py:6-12 写明消费者（exam 执行驱动）M4 才出现、本步只按方案 §3 定好的 v4 形状注册、并给出 K4（第 5 层）不能调第 6 层驱动的理由。grep 证实 src 内除注册表条目与驱动自身外无任何生产消费方——"只注册不接消费方"属实。

## 6. 硬闸亲验（评审项 5）

| 闸 | 命令 | 结果 |
|---|---|---|
| 金矩阵 | `.venv/bin/python -m pytest tests/golden -q` | **11 passed**（8 组合 fresh 快照等 + 2 组合崩溃续跑 + 1 tamper 反证） |
| fixture 未动 | `git log --oneline -2 -- tests/fixtures/golden` | 最后一次改动 = **32d6c86（M1）**，M2 零触碰 |
| resume 套件零改动 | `git diff 85bfa4f 6b86e65 --stat -- tests/composites/test_acceptance_resume.py tests/composites/test_live_acceptance_resume.py` | **空**（零行改动） |
| resume 套件运行 | `pytest tests/composites/test_acceptance_resume.py tests/composites/test_live_acceptance_resume.py -q` | **236 passed** |
| 全套 | `pytest -q` | **1958 passed, 3 deselected**（与声称的 1958 一致；1950→1958 = 新增 8 测试） |
| 分层 | `pytest tests/test_layering.py -q` | 3 passed |
| ruff | `ruff check src tests` | All checks passed |

金矩阵机制说明：snapshot（tests/golden/harness.py:457-500）对非 JSON 文件按 sha256 逐字节、JSON/JSONL 按解析后规范化结构比对——这是 M1 之前预注册的仪器，M2 未改。T8-A（summary dict 构造序）经 diff 逐行核实满足：M2 未触碰任何产物 dict 的构造点（stage6 的改动仅在裸臂 kwargs 装配，live_run 的改动仅在解析器来源），构造序无从漂移。

## 7. 范围纪律（评审项 6）

`git diff 85bfa4f 6b86e65 --stat` 限定 blocks/、K4（composites/acceptance.py）、K3（exam_once.py）、K5（curation.py）、core/（含配置 schema）、stage0–5（含 stage3_generation.py、stage4_acceptance.py）、pipeline.py、configs/、inject/、prompts/、llm/、infra/、benchmarks/、scripts/ —— **全部空 diff**。`gen_exam`（blocks/author.py:378）与 `audit_leak`（blocks/audit.py:37）原位未动，与"留 blocks、M4 只挪调用点"的声称一致。

## 8. 发现清单

- **F1【琐细】** 821 组合穷举比对无留档工件：log.md 与提交说明引用的验证数字不可从仓库复现（提交内无对应测试，tmp/ 无脚本）。外层 AGENTS 第一节/第二节要求昂贵验证保存原始证据。实质已由本评审的 13487 组合独立比对证实为真，不影响本步验收；建议 M3 起将此类"新旧语义比对"的关键边界（至少轮判定三分支）落成提交内测试或把一次性脚本归档进 tmp/ 并在 log 里给出路径。
- **F2【琐细】** `resolve_fixed` 的未知名字报错（registry.py:158-163）与 `_entry_for`（:90-96）重复实现同一消息格式，可复用 `_entry_for(slot, {"name": name})`；当前两条路径消息逐字一致，仅为后续漂移留了缝隙。
- **【赞同】** 其余全部：六个壳签名逐字段不变；核不引用任何壳层常量/路径助手（AST 零命中）；append-once、重算比对、终态先写次序原样（含执行级验证）；`_finalize_group` 泛化对 (0,1) 逐位等价；resolve_fixed 同表同校验同懒 import、check 条目响亮拒绝、空 raw 仍校验必填；stage6 裸臂 kwargs 逐位相同且 None/[] 语义区分保留；group_by_task 逐字迁移、空组文案不变、无 shim 残留；per_skill 委托 K4 同一渲染函数、docstring 如实、无生产消费方；gen_exam/audit_leak/stage3/K4/配置 schema 零改动；三硬闸亲验全过。

## 9. 验收判定

**M2 验收通过。** 硬闸三项（金矩阵逐字节零 diff、两套 resume 套件原样零改动通过、全套 1958 全绿 + test_layering + ruff）由本评审独立重跑确认；行为等价经逐行 diff 比对 + 新旧双实现执行级比对（13487 组合 + 22 场景）双重证实。两条【琐细】不构成验收障碍，留 M3 顺手处理即可。
