日期：2026-10-16
一句话说明：执行—学习重构 M2 步骤的验收评审 C——续跑契约与可复现性。
对应 log.md 条目：2026-10-16 M2 执行侧抽取。

---

## 0. 总评

**结论先行**：M2 是本仓库续跑语义最重的一步（K6 的 verdicts append-once、首判
为准、注入审计首写冻结都住在被拆的模块里），我按"续跑契约零漂移 + 证据可复现"
两条线逐项核过：**账本/轮仓路径逐函数对照与 85bfa4f 完全一致**（路径助手逐字节
相同、六个函数体在机械改名后 diff 为空）；**两套崩溃续跑套件零行改动且亲跑通过**，
套件 patch 的内部名全部保留且 patch 语义对拆分后的核依然生效；live 注入解析器的
迁移**未改变任何读写时点与崩溃窗口**；核**确实零轮概念**（全部 round 相关引用
都在壳层）；全套验收证据（1958 passed + 3 deselected、layering、ruff、金矩阵
11 项、fixtures 零改动、工作树干净）**逐项亲跑复现**。唯一实质缺口在证据留档：
提交说明与 log 引用的"821 种账本行组合穷举比对"**没有任何留档工件**（无测试、
无脚本），我按 AGENTS 五.1 记一条偏移约束（C-1）——但它不阻塞验收：我已用
**7381 种行组合**（该空间的超集，含钉死轮集之外的行与多行/乱序文件）独立复验
新旧决策**逐位相等，零失配**，该声称本身为真。

审阅范围说明：任务给出的 `git diff 9c8bf49 6b86e65 -- src tests` 为空——9c8bf49
是 HEAD、仅比 6b86e65 多一个 docs 提交；本评审的实际被审 diff 为
`85bfa4f..6b86e65`（src+tests 共 12 文件，+688/−241）。

**M2 验收：通过**（C-1 需在 M3 开工前或随 M3 补上，不阻塞本步）。

---

## 1. 账本路径与字节等价（核查项 1）

**逐字节对照路径助手**：`_live_dir` / `_ledger_path` / `round_dir` 三个函数与
`LIVE_DIR_NAME = "stage4_acceptance/live_run"`、四个账本名常量、
`INJECTION_AUDIT_NAME` 在 85bfa4f 与 6b86e65 之间逐字节相同（从两个提交各自
提取后 diff 为空；新文件仅行号平移 + `_round_store` 助手被内联进
`_trajectories_from` 的 `Store(round_store_dir)`，等价）。

**逐函数路径对照**（壳传入 vs 旧单体内部自算）：

| 函数 | 壳传入 | 旧版内部计算 | 相同 |
|---|---|---|---|
| judge_round | verdicts `_ledger_path(store, VERDICTS_LEDGER)`；round_store `round_dir(store, r_)`；audit `round_dir(store, r_)/INJECTION_AUDIT_NAME` | 三者分别经 `_first_row`、`_round_trajectories`、`_require_audit_row` 内部同一表达式 | 是 |
| attribute_group | attributions / attribution_failures 两条 `_ledger_path` + round_store + audit | 同左 | 是 |
| apply_revisions | attributions / revision_failures 两条 `_ledger_path` + round_store | 同左（`_revise_one` 的读写路径经位置参数正确传入） | 是 |
| no_op_guard | `round_dir(store, r_)/INJECTION_AUDIT_NAME` | 同 | 是 |
| audit_row | 无路径（纯构造） | 同 | 是 |
| finalize_group | verdicts `_ledger_path` + `rounds=_ROUNDS` | 同 | 是 |

**函数体机械 diff**：我把六个旧单体函数体与新核函数体在剥离 docstring、应用
机械改名（`_rows_of`→`_rows_from` 等）后逐行 diff——除签名参数线程化外**无任何
差异**，即核是逐字搬运，不是重写。

**金矩阵亲验**：`.venv/bin/python -m pytest tests/golden -q` → **11 passed**；
`git diff 85bfa4f 6b86e65 --stat -- tests/fixtures/ tests/golden/` 为**空**；
`git log --oneline -1 -- tests/fixtures/golden` → **32d6c86（M1 提交）**——
金夹具与比对仪在 M2 零改动，8 组合重生成对比逐字节无 diff。再生机制
（harness 的 volatile 键归一 + 其余逐字节断言 + tamper 反证）未被触碰。

## 2. 续跑等价的硬闸（核查项 2）

- `git diff 85bfa4f 6b86e65 --stat -- tests/composites/test_acceptance_resume.py
  tests/composites/test_live_acceptance_resume.py` → **空**（零行改动）。
- 两套件亲跑通过（与 layering、golden 合跑 250 passed；亦含于全套 1958 内）。
- **patch 目标语义仍然生效**，这是本次拆分最容易翻车的一点：崩溃注入靠
  `monkeypatch.setattr(la, "append_jsonl", ...)` 拦截写账本——拆分后的核仍住在
  同一模块、调用同一模块级全局 `append_jsonl`，所以 patch 依旧拦住**核**的每一次
  落盘（若把 append 挪进别的模块，注入会静默失效、崩溃套件变成假绿）。同理
  `la._pending_group`（:397 仍在）、`_ATTRIBUTION_ATTEMPTS`（:184）、
  `_REVISION_ATTEMPTS`（:187）等预算常量都是模块级且核经同一全局读取，
  monkeypatch 语义不变。test_acceptance_resume patch 的
  `acceptance_mod.append_jsonl` 在 K4（本次未动）一侧，无涉。

## 3. "821 组合"等价性验证的可信度（核查项 3）

**留档状态**：全仓 grep "821" 只命中 log.md:649 与提交说明——**没有测试、没有
scripts/ 脚本、tmp/ 里也没有**（tmp/ 仅存早前步骤的 golden_check3）。即该声称目前
对第三方不可复现。按 AGENTS 五.1（"结论依赖它的脚本要移入 scripts/"），提交说明
与 log 都引用了它的结论 → 应留档。

**覆盖面独立复核**：我在 /tmp 用旧版（85bfa4f 的 `finalize_group` 决策逻辑逐字
副本）对现版 `_finalize_group`（直接 import 运行）做穷举比对——枚举长度 0..4 的
**全部 verdicts 行序列**（9 种行类型：轮 0/1/2 × better 真假/verdict 终态，
共 1+9+81+729+6561 = **7381** 种），每种写真实 verdicts 文件 + 双成员 store，
比对返回 dict 三字段（outcome/reason/committed）**逐位相等：0 失配**。覆盖了
"轮 0/1 × better/miss × 终态 × 多行 × 首行选取"的全部组合，并额外覆盖了
**钉死轮集之外的行（round=2，两版都正确忽略）**与终态行乱序出现的位置序——
严格超集于声称的 821。两版唯一逻辑差（轮优先级的序列化泛化）已被证伪不了；
提交块代码 diff 为空（我另做了机械 diff）。

**既有测试钉住的路径**：test_live_acceptance.py:1354-1433 钉了 6 条主路径
（轮0 better 幂等接受 / 轮1 miss 弃 / 轮1 过接受 / 零执行行按其理由弃 /
轮0 miss 不决策 / 无行 pending）。

**比例恰当的裁决（C-1，偏移约束，不阻塞）**：M2 是行为保持步骤、金矩阵 + 两套
续跑套件是主验收且都过硬；821 脚本是提交者对唯一一处泛化做的额外自证，其
**结论经我独立复验为真**，故不翻验收。但"结论依赖的验证不留档"违反五.1，且这
类穷举正是 M4.1 仪器（E1–E6 断言）该吸收的东西。要求：**M3 开工前（最迟随
M4.1）把组合穷举落成受版本控制的测试**（一个小单测枚举 16 个主组合 + 多行/乱序
若干即可，~20 行），或整理进 scripts/。我的复验脚本可直接作为种子。

## 4. live 迁移的崩溃窗口（核查项 4）

**调用时点不变**：旧版 `_injection_resolver(store, benchmark)` 在
`_dispatch_round` 内、`batch_execute(...)` 调用处内联构造闭包；新版
`resolve_fixed("delivery", "group_by_task")` + `delivery.resolve(ctx, params,
store, benchmark)` 在同一函数内、仅早几行、仍在 `batch_execute` 之前——两次都只是
**构造闭包，不读 store**；`skills_for_task` 对 store 的读取仍发生在 K1 逐任务/
逐 attempt 调用时。读时点、读对象（run 级 store 而非轮仓——新旧都对）、读函数
（`survivors_and_block`）全部不变。

**崩溃窗口不变**：注入审计的"首写冻结"协议完整住在 live_run.py（轮 0 循环
:462-476、轮 1 :594-605：派发**前** `la.audit_row` 重导 → 有冻结行则
`_canonical_line` 逐字节比对、无则首写）。M2 对 live_run.py 的 40 行改动只含
import、解析器删除与调用点替换，**冻结/比对代码一行未动**。因此"解析器已调用、
审计行未写"这一窗口经 stage 在新旧两版中**同样不可达**（首写先于任何解析器
调用）；派发中途崩溃后的 resume 亦相同——无 verdict → 无归因 → 无修订落盘，
重导 `audit_row` 必与冻结行逐字节相等。K1 侧 `_check_stored` 的续跑校验（逐单元
重解析 `skills_injected`）读的是同一个惰性解析器，重导值不变。

**无新崩溃面**：`resolve_fixed` 只做表查找 + 空参数构造（GroupByTaskParams 无
字段，空 raw 不可能失败）+ importlib 一个依赖链（composites.live_acceptance）
已被加载的模块——冻结写与派发之间没有新增的可崩环节。

**逐字性**：被删的 `_injection_resolver` 与新驱动 `skills_for_task` 逐行相同
（`ids.make_task_id` 前缀、`survivors_and_block`、空组 CodeBugError 的报错文本、
references 构造、`(references, block)` 返回）；差异仅 `la.` 前缀改直接 import。

## 5. M4 删除薄壳的准备度（核查项 5）

- **壳 docstring 写清删除范围**：六个公开函数的 docstring 尾部均注明
  "Thin shell over ``_X`` … deleted in M4"；模块 docstring 说明去轮化设计；
  `LIVE_DIR_NAME`（:141-146）与 `_ROUNDS`（:187-191）都带"壳层常量、随壳 M4 删"
  的注记。
- **核零轮概念、机检**：用 awk 把每个 `LIVE_DIR_NAME` / `_ROUNDS` /
  `_check_round` / `round_dir(` / `_ledger_path(` / `_live_dir(` 引用映射到其
  所在函数——**全部落在**模块 docstring、常量定义、四个路径/检查助手、六个壳
  之内；任何 `_` 核函数零引用。去轮化是真的，不是注释里说说。
- **stage6 裸臂换轨点干净**：`resolve_fixed("delivery", "none")` 驱动返回恰为
  被替换的两个硬编码标量 `{"skills_injected": None, "skills_block": ""}`，
  kwargs 合并后单点 `batch_execute(**bare_kwargs)`。M4 相位驱动把这里换成按相位
  配置的 `resolve` 即可，不留转发 shim（旧内联解析器是删干净了的）。

## 6. 测试与验收证据（核查项 6）

全部亲跑复现：全套 `.venv/bin/python -m pytest -q` → **1958 passed, 3
deselected**（52.7s；deselected 为 3 个 `live` 标记的 benchmark 冒烟，与
`-m 'not live'` 默认一致；新增 8 个测试 = 4 驱动契约 + 4 resolve_fixed，
1950→1958 算术自洽）；`test_layering` **3 passed**；`ruff check src tests` →
**All checks passed**；`tests/golden` **11 passed** 且 fixtures 零改动（见 §1）；
`git status` 干净、无 stash。log.md M2 条目的每一项证据声称都与实测一致。

## 7. 对 M3 的前置（核查项 7）

干净。理由：(a) M3 的契约前提是 **K6 对外签名不变**（I3）——六个公开函数签名
与 85bfa4f 逐参相同，已核；(b) M3 要抽的决策逻辑没有被埋深——轮优先级孤立在
`_finalize_group`、no-op 判定孤立在 `_no_op_guard`，都是显式路径入参的核，
`composites/decision.py` 在 M3 可直接调不变的公开壳，M4 相位驱动再直调核；
(c) 报告读取器需要的账本路径助手（`_ledger_path` / `round_dir`）与账本布局
原样未动；(d) 注册表加三个条目 + `resolve_fixed` 未触碰表机制，M3 的
evidence/author 族加条目是同一机械操作；(e) K4（composites/acceptance.py）、
stage3、authoring/segmentation 全未动，M3 的抽取面与 85bfa4f 时无异。

---

## 8. 发现清单

**【偏移约束】**

- **C-1（证据留档）**："821 种账本行组合穷举比对"仅存在于 log.md:649 与提交
  说明，无任何受版本控制的工件（测试 / scripts/ / tmp 均无）。AGENTS 五.1：
  结论依赖的脚本要进 scripts/。经我独立复验（7381 组合、含钉死轮集外行与
  乱序终态行，新旧决策逐位相等零失配），**声称为真、不翻验收**；但该结论
  目前不可第三方复现，且穷举恰是 M4.1 仪器应吸收的内容。要求：M3 开工前
  （最迟随 M4.1）落成受版本控制的组合测试或脚本。**不阻塞 M2。**

**【琐细】**

- **T-1**：`resolve_fixed` 的 unknown-name 报错分支（registry.py）复制了
  `_entry_for` 的报错文案构造而不是共用，3 行重复，未来文案漂移的小风险。
  可在 M3 顺手合并，不要求。
- **T-2**：契约 §9 M2 行"三新驱动……**并被 stage6/live 经注册表缝调用**"字面
  上读作三个都有调用方，实际 per_skill 按 §3 表本就没有现调用方（消费者 exam
  执行驱动 M4 才出现），驱动 docstring 已写明缘由（K4 在第 5 层不能调第 6 层
  驱动）并以契约测试钉住形状。判定**非偏移**（§3 表已固定 v4 形状），记录在案
  免得后续评审重议。

**【赞同】**

- 路径等价的实现方式（壳复用未动的路径助手、核显式收参）使"逐字节相同"成为
  可机械核验的事实而非声称——我两路核验（助手 diff + 函数体机械 diff）均零差。
- 崩溃套件的 patch 目标经拆分后语义不变（核与壳同模块、同全局），这套件对
  未来 M4 的进一步搬迁仍是有效的红灯仪器。
- 解析器迁移保持惰性读取时点、冻结协议零触碰、无新崩溃面；`resolve_fixed`
  拒绝带 check 条目的设计与四条单测（含 required-params 响亮失败）都是对的。
- 壳层注记（常量/函数级"deleted in M4"）让 M4 的删除范围可 grep 清点。

---

## 9. 裁决

**M2 验收：通过。**续跑契约与可复现性零漂移在主验收仪器（金矩阵逐字节、
两套崩溃续跑套件零改动、路径逐函数等价）上全部成立；唯一缺口 C-1 是证据
留档而非代码行为，已独立复验其结论为真，随 M3 前置补上即可。
