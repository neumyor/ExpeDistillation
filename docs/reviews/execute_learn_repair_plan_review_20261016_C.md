日期：2026-10-16
一句话说明：对 execute_learn_repair_plan_20261016.md 的第三路评审（C：跨文档一致性与执行可行性），只读代码核实起点事实、逐条检验冲突裁决完备性、并行拆分的文件所有权与波内判据可达性。
对应 log.md 条目：2026-10-16 执行—学习闭环修复方案与并行拆分（三路评审之 C）。

---

## 0. 结论先行

1. **起点事实基本可靠**：方案 §1"已落地"五项全部核实无误（M1 字段、M2 三交付驱动、M3 决策纯函数与读取器、M4.1 语义仪器、M4.2 upgrade_v3_config），HEAD `c6a9fb8`、`pytest` 2130 passed / 3 deselected 在本机 `.venv` 下精确复现（55.9 秒）。但 §2 差距表里若干"现状证据"的**数字**有错（详见 F10），其中 `discard_skill` 少数了一处，直接影响 R4 的完备性。
2. **方案在三个地方自相矛盾，按现状执行 Wave 1 不可能"全绿"**：① C5/R2 的 `status` 词汇迁移在风险表里说"放 Wave 0 单独做"，但 §4 依赖图和 §5 波次表都归 W1-B（Wave 1），且 Wave 0 的工作清单没包含它（F2）；② 字节级 golden 矩阵测试是 `pytest` 的一部分，R2/R6 的改动必然改产物字节，而方案把 golden 重生成只放在 Wave 4，波内"pytest 全绿"判据不可达（F2/F4）；③ R3 要求"stage6_test.py 不再计算 delta"，与 E3"summary 逐字段含 delta"判据直接冲突，也与边界文档 §3.9"测试结果不流进学习"冲突（F1）。
3. **Wave 1 的文件所有权矩阵不完备**：R1/R2/R3/R5 各自的判据都要求改动**不在任何代理独占清单里**的文件（stage6_test.py、methods/acceptance/*、live_acceptance 的测量半边、blocks/{execute,metrics,view}.py、benchmarks/{react_loop,alfworld/plugin}.py 等），而风险表又规定"所有权清单里不存在的文件任何人都不能新建"——对已存在但无主的文件没有任何规则，六个代理会僵在"谁都无权改、不改就不绿"上（F3/F6）。
4. **冲突裁决 C1–C6 不完备**：两份旧方案里至少还有五处与边界文档的实质冲突没进裁决表，最重的是 scope §3.1/§2 把"和什么比、怎么算有提升"放在验收里（边界说比较归决策）、impl §3 把"逐技能动作分配"交给 G10（边界 §2.5/§3.8 说找证据不定动作、实战结论只到包）（F5）。

建议：方案需一版修订（波次归属、所有权补全、判据改写）后才可放代理开工；修订量集中在 §4/§5/§7，不需要推翻骨架。

---

## 1. 逐条发现

### F1（阻断）R3 与边界文档 §3.9、E3 判据三方冲突，且 stage6_test.py 无主

**问题**：R3 写"把'带技能 vs 裸跑'的计算从 `stages/stage6_test.py` 搬进决策"，判据"stage6_test.py 不再计算 delta"。但：

- 边界文档 §3.9 明文："测试结果不流进学习。C 阶段的报告只进入最终成绩，'带技能库比裸跑提升多少'**由最终汇总计算，不经过决策**"。stage6 的 `_delta`（`stage6_test.py:195`）算的正是 C 相位的最终成绩 delta——把它搬进**决策**模块，恰好违反了方案自称的最高权威。
- E3 判据（方案 §3.3 第 10 条）要求"C 相位 summary 逐字段"对基线等价，而基线 summary 含 `delta` 字段（`stage6_test.py:407`）。若 stage6 不再计算 delta，E3 必红。
- W1-C 的独占文件是 `composites/decision.py`、比较函数、`composites/skill_status.py`，**不含 stage6_test.py**；stage6_test.py 在 Wave 2 的三张表里也无人认领。R3 的判据在 Wave 1 无法由任何代理合法达成。

**证据**：`stages/stage6_test.py:195 _delta`、`:330/:357 control_source`、`:407 "delta": _delta(...)`；边界文档 §3.9；方案 §3.3 E3、§4 R3、§5 Wave 1 表。

**建议**：R3 收窄为"A/B 相位的训练集比较归决策"（这部分今天已在 `live_acceptance` 经 `decision.live_*` 实现，只差统一入口），C 相位的 delta **留在最终汇总**（按边界文档原话）；"stage6_test.py 不再计算 delta"判据删除，改为"决策不消费 C 相位报告"。相应地 R3 对 stage6 的唯一动作（如有）归入 Wave 2 的 W2-A。

### F2（阻断）C5/R2 词汇迁移的波次归属自相矛盾；无论放哪一波，现行判据组合都不可满足

**问题**：三处文本互相打架：

- §2.1 C5 裁决："`infra_error` 从记录词汇里移出……**R2 承担**"（R2 = W1-B，Wave 1）；
- §7 风险表："`RunOutcome.status` 词汇变更波及 24 处……**放 Wave 0 单独做、单独提交**"；
- §5 Wave 0 工作清单："R0 全部：四个形状、**C4/C5 裁决**、三个大文件的机械拆分"——只写"裁决"，没有词汇迁移这个动作。

更要害的是两个判据层面的死结：

1. **Wave 0 内做不可行**：Wave 0 判据是"golden **逐字节无 diff**（只搬不改）"。把 `Trajectory.status` 的三值词汇换成 `correctness: Optional[bool]` + 指标，会改变所有落盘轨迹行的字节（`tests/golden/test_golden_matrix.py:47 test_fresh_run_matches_golden` 对全 run 快照做逐字节断言）——Wave 0 的"任何 diff 都是错"会直接否掉它。
2. **Wave 1 内做也不可行**：词汇消费点在 src 有 26 处、12 个文件（我用比方案更宽的模式数到 26，方案的 24 基本属实），其中 W1-B 只拥有 3 个（batch_execute、tb/plugin、searchqa/plugin）；`benchmarks/alfworld/plugin.py`、`benchmarks/react_loop.py`、`blocks/execute.py:106`、`blocks/metrics.py:105-107`、`blocks/view.py`、`live_acceptance.py:1053`（判定半边，归 W1-A）、`reports.py:237`（归 W1-A）都不在 W1-B 手里。W1-B 单独改完必红，"六个代理并行、各自提交全绿"不成立。tests 里另有 77 处消费，所有权同样没有着落。

**证据**：方案 §2.1 C5、§4 R2、§5 Wave 0/Wave 1 表、§7 风险表；`benchmarks/base.py:53`、`blocks/execute.py:106`、`blocks/metrics.py:105,107`、`composites/batch_execute.py:354`、`benchmarks/react_loop.py:130`、`benchmarks/alfworld/plugin.py:273`；grep 统计 26 处 / 12 文件（src）、77 处（tests）。

**建议**：把词汇迁移从 R2 拆出来，作为**独立的 R2b**，明确排在 Wave 1 结束后的一个串行窗口（或 Wave 2 开头），一次提交完成 src+tests 全量迁移；Wave 1 期间 R2 只做"执行不再下结论"中**不碰词汇**的部分（classify_result 的摘除走tb插件内部重构，暂以兼容层输出旧词汇）。同时把 W1-B 的所有权扩到 `blocks/execute.py`、`blocks/metrics.py`、`benchmarks/react_loop.py`、`benchmarks/alfworld/plugin.py`，或明确这些文件的临时持有人。

### F3（阻断）Wave 1 存在成批"无主"文件，与"所有权清单外不得新建"规则构成僵局

**问题**：逐项核对 §5 Wave 1 六个代理的独占文件后，下列**必须在本波内被修改**的文件不在任何代理清单里：

| 文件 | 为什么 Wave 1 必须改 |
|---|---|
| `stages/stage6_test.py` | R3（F1）；R5 改 delivery 签名后 `:317/:353` 的 `delivery.resolve` 调用点必须跟随 |
| `methods/acceptance/exam.py`、`live_run.py` | R1 说"methods/acceptance/ 换为新契约"，但 W1-A 清单只写"新建验收模块、verdicts.py、判定半边、reports.py"；且 `live_run.py:772-783` 是 group_by_task 的消费点（R5） |
| `stages/stage4_acceptance.py` | 验收驱动的调用点，R1 换契约必改 |
| `composites/live_acceptance.py` 的**测量半边**（`load_baseline`、`judge_round`、`survivors_and_block:1249`、`audit_row`、`pending_groups`） | Wave 0 拆三半，W1-A 只领了"判定半边"、W1-E 领了"归因半边"，测量半边无人认领；而 W1-D 的 `group_by_task` import 它 |
| `composites/acceptance.py` 拆出的三份（泄题判定/技能状态判定/提交助手） | Wave 0 拆了，Wave 1 没人领；`accept_skill`（:290）在提交助手里，R4 验收侧要动它 |
| `composites/shortfall.py` | R1 的"无效单元重派"与 B 相位跳过语义都读它 |
| `blocks/metrics.py`、`blocks/execute.py`、`blocks/view.py` | R2（F2） |
| `benchmarks/react_loop.py`、`benchmarks/alfworld/plugin.py` | R2（F2） |
| `tests/golden/semantics.py`、`tests/golden/harness.py` | 若"测不了"等新终态或 R6 的归因行变化需要调整归一化/视图，仪器本身要改；方案 §6 说"只审该波 diff"，但仪器是全波共用判据，无主即无人能改 |
| `methods/registry.py`、`methods/params.py` | 归 W2-C（Wave 2）；但 W1-A 若给 methods/acceptance 换 Params 契约、W1-D 若动驱动参数类，注册表条目的引用同一波就得改 |

方案 §7 风险表规定"Wave 0 的所有权清单里不存在的文件，任何人都不能新建"——这条规则只管了"新建"，对"已存在但无主"的文件没有出口。六个代理并行时会出现：谁改谁越权，不改判据不绿。

**证据**：方案 §5 各表与 §7 风险表；上列文件均经 grep/阅读确认被 R1/R2/R3/R5 的判据牵连。

**建议**：出一份补全的 Wave 1 所有权附录，把上表逐文件指派（测量半边建议归 W1-A 或单独 W1-G；stage6_test.py 的改动建议全部推迟到 Wave 2，见 F1/F6 的替代做法）；把"清单外不得新建"改成"清单外文件改动需在该波评审中逐条报备"。

### F4（阻断）R6 与 E6/Wave 1 "E1–E6 等价"判据正面冲突；LIVE_REVISION_DELETED 路径未裁决

**问题**：G10 今天把**含 `action`（keep/revise/delete）的 `skill_actions` 整体写进 attributions 账本（`live_acceptance.py:922-929`，schema `live-attribution-v1`）。R6 判据要求"G10 的输出里不再有 keep/revise/delete"。两种走法都和方案冲突：

- 若账本行随 R6 改形（去掉 action），E6"live 测量账本逐行（verdicts + attribution 计划行）对基线等价"必红——Wave 1 全波判据"E1–E6 对基线等价"不成立；
- 若为了保 E6 而让决策层照原样写行，方案没有这么说，且"事实行由找证据写、计划行由决策写、两者合成同一字节"是个需要明确设计的新机制，不能靠沉默通过。

另外边界 §3.8 说"实战验收的结论只到包，决策只能对整包定动作；具体重写哪条由写作根据证据决定"，而现行 `_revise_one`（`live_acceptance.py:1217`，`LIVE_REVISION_DELETED`）和 `_attribute_group` 内的 `_discard_group`（`:875/:887`，G10 耗尽整组弃）都是**逐技能**的库变更、发生在找证据/改写流里。R6 只裁决了"G10 不定动作"，`LIVE_REVISION_DELETED` 与"G10 耗尽整组弃"的归属（决策 or 写作 or 整理）**没有任何条款裁决**。

**证据**：`live_acceptance.py:922-929`（attribution 行含 skill_actions.action）、`:875/:887`、`:1217`；方案 §4 R6、§5 Wave 1 判据；边界文档 §2.5/§2.6/§3.8。

**建议**：在方案里明确三选一并写死：① R6 保留账本行字节（决策从事实行推导并回写同形计划行），E6 继续硬等价；② R6 改行形，E6 对 attributions 部分转为"披露差异"（Wave 1 判据相应改写为"E1–E5 + E6-verdicts 等价，E6-attribution 披露"）；③ R6 推迟到 Wave 2 与相位编排一起做。同时补一条 C7 裁决：`LIVE_REVISION_DELETED` 与归因耗尽的库变更，按边界文档要么升为决策对整包的动作，要么显式声明为"写作阶段依据证据包的删除"并写进边界文档例外。

### F5（偏移）冲突裁决 C1–C6 不完备：至少五条旧方案条款与边界文档矛盾而未裁决

逐节扫过两份旧方案后，未进 C1–C6 的实质冲突：

1. **scope §3.1 + §2**：验收模块被定义为回答"**和什么比、怎么算'有提升'**"，验收报告字段含"**对照结果、是否提升**"。边界文档 §2.3/§3.1 明文"验收是绝对判定，不和对照比较""比较归决策"。这是对验收职责的根本性分歧，比 C1–C6 里任何一条都重，方案没列。实现代理照 scope §3.1 做就会把比较做进验收。
2. **impl §3 决策规则清单**："G10 逐技能计划（keep/revise/delete）""归因 G10 的计划即逐技能动作分配"——与边界 §2.5（找证据只报事实）、§3.8（实战结论只到包）冲突。R6 修了一半（G10 出事实），但"决策对整包定'修改'之后，包内逐技能的删/改谁定"没有裁决（见 F4）。
3. **impl §3 注册表 delivery 族**："输出形状**随执行方式**（per_skill 是 K3 单技能块，group_by_task/retrieval 按 store 解析）"——与边界 §2.1"交付的输出统一是执行单元清单"直接矛盾。R5 修了代码方向，但 §2.1 冲突表没把它列为旧文档需回改处，Wave 3-C 回改旧文档时可能漏掉。
4. **impl §3 写作**："修订写作随报告种类分发：exam → G6；live → G11"——边界 §2.6"写作只依据证据包落笔，**不管证据来自考试还是实战**"。方案未裁决（R6 只说"author 驱动就位"，没裁决分发依据）。
5. **impl §4 配置 schema v4 无验收槽位**：`phases.<name>` 只有 `delivery`/`execution`/`compare`/`evidence`/`author`/`curate`，而边界 §2.3 说"判定规则按 benchmark 区分、**做成具名规则**"、§2.3 还有"无效判定的具名规则"——具名规则需要配置里能写名字，v4 schema 没给验收留位置。R9 照抄 impl §4 就会漏。

**证据**：scope_20261015 §2 表、§3.1 表；implementation_20261015 §3 五族表、决策规则清单、§4 schema；边界文档 §2.1/§2.3/§2.6/§3.1/§3.8。

**建议**：把上述五条补进 §2.1 作 C7–C11（或合并为三条），各给一行裁决；特别是第 1 条必须写，否则"实现代理会照着旧方案做"这个 §2.1 自己声明的动机就落空了。第 5 条需要 R9 增补"acceptance 具名规则的配置键"设计。

### F6（偏移）R5 交付统一签名在 Wave 1 没有合法的消费点改法

**问题**：R5 把五个驱动统一为 `resolve(bank, tasks, params) -> ExecutionUnitList`。消费点现状：

- `stages/stage6_test.py:317/353`（whole_bank/retrieval，含 `write_audit`）——无主（F3）；
- `methods/acceptance/live_run.py:772-783`（group_by_task，取 `skills_for_task` 闭包）——无主；
- per_skill 按 M2 的设计**当前没有任何调用点**（`methods/delivery/per_skill.py` docstring 明说"M2 deliberately wires no caller"）；
- `group_by_task.py` 直接 import `composites.live_acceptance.survivors_and_block`（`:16`）——这个函数落在 Wave 0 拆出的**测量半边**，Wave 1 无人认领（F3），W1-D 的驱动与它有一条跨所有权的依赖线，改签名时两头要同时动。

W1-D 在 Wave 1 只改 `methods/delivery/*` 本体，改完 stage6/live 立刻红；不改则 R5 判据（同一签名、同一返回类型）在本波无法验收。

**证据**：`stage6_test.py:317,320,353`；`methods/acceptance/live_run.py:772-783`；`methods/delivery/per_skill.py` docstring；`methods/delivery/group_by_task.py:16,25`。

**建议**：二选一并写明：① R5 在 Wave 1 只冻结新签名并给旧调用点加**兼容适配层**（适配层文件的所有权指派给 W1-D 或推迟），消费点换接随 Wave 2 的 phase_execute；② R5 整体推迟到 Wave 2，与 W2-A 合并。同时把 `survivors_and_block` 所在半边指派给与 W1-D 协调的单一 owner。

### F7（偏移）R7 与 R1 的部分判据在 Wave 1 结构上不可验收

**问题**：

- R7（W1-F）判据"逐相位技能库产物落盘**且可被下一相位直接加载**"——逐相位产物由 L 相位落盘、下一 E 相位消费，而 phase_learn/phase_execute 在 Wave 2 才诞生。Wave 1 期间没有任何"下一相位"可以加载它，判据在本波不可执行，只能测半个（落盘形状）。
- R1 说"无效单元的重派归阶段调度"——阶段调度同样是 Wave 2 的产物。Wave 1 期间 R1 只能交付"验收模块 + 标注无效与原因"，重派语义无法接线，判据里也没有区分"本波交付"与"Wave 2 接线后验收"。

**证据**：方案 §4 R1/R7、§5 Wave 1/Wave 2 表；`stages/` 现状仍是 stage1–6。

**建议**：把 R1/R7 的判据拆成"Wave 1 可测部分"（模块单测、场景测试、落盘形状）与"Wave 2 接线后验收部分"（重派发生、跨相位加载），避免代理对着不可达判据工作或自行发明临时接线。

### F8（偏移）Wave 2 的所有权缝隙与波内接口依赖

**问题**：

- "相位驱动取代 stage1–stage6"意味着删除 `stages/stage1_baseline.py` … `stage6_test.py`，但这六个文件不在 W2 任何代理的清单里（W2-C 只有 `pipeline.py`（删））。谁来删、删除提交归谁，未指派；stage0_freeze 沿用与删除的边界也没写。
- W2-A/W2-B 写相位驱动必须读 W2-C 的 v4 配置形状（`config.phases.<name>`），"文件互不相交"成立但**接口依赖**存在：W2-C 的 schema 定稿前，W2-A/B 只能猜。方案对 Wave 1 给了"W1-A 先广播字段"的机制，对 Wave 2 没有对应机制。

**证据**：方案 §5 Wave 2 表；impl §4 schema；`stages/` 目录清单。

**建议**：Wave 2 开工前由 W2-C 先冻结 v4 配置的解析产物（dataclass/TypedDict）并广播；删除 stage1–6 的动作明确归 W2-C（与 pipeline.py 删除同一提交）。

### F9（偏移）E1–E6 对 Wave 1 的覆盖面声明缺失：对 R2 是"测不到"，对 R6 是"必不相等"

**问题**：方案把"E1–E6 对基线等价"当作 Wave 1 全波判据，但没有继承 impl §8 的两条关键限定：

1. **P6 覆盖面声明**：金夹具全部是"全接受零弃"，E2 的弃用分支在 golden 上恒空真。R1 判据里写"E2 全覆盖"容易被读成 E2 能钉住 R1 的 discard 分支——不能，必须靠场景测试（R1 自己也列了，但两处判据并列容易让人以为 E2 也是证据）。
2. **对 R2 的盲区**：E1–E6 比的是技能库行、终态/账本、summary、模型调用多重集、续跑等价、live 账本——**没有一条比对 Trajectory.status**。所以 R2 改词汇后"E1–E6 等价"很可能照样成立，但这只说明仪器看不见这个字段，不说明行为等价；真正的闸（byte-golden）又会红（F2）。方案应写明：R2 的等价主张走"披露 + 定点重生成轨迹夹具"，而不是笼统的"E1–E6 等价"。

**证据**：impl §8 P6/I6；`tests/golden/semantics.py` 视图函数清单（_e1–_e4、_e6、run_e5 均不读 trajectory.status）；`test_golden_matrix.py:47`。

**建议**：在 Wave 1 判据下加一行覆盖面声明：E1–E6 不覆盖运行记录字段本身；R2 的验收 = 场景测试 + 字节 diff 逐处披露；R6 见 F4。

### F10（偏移）起点"现状证据"的数字错误（含一处影响 R4 完备性）

逐项核实 §1/§2 的事实断言：

| 方案断言 | 实际 | 影响 |
|---|---|---|
| `live_acceptance.py` 1375 行 | **1362 行** | 琐细 |
| `live_acceptance.py` **四处** `discard_skill`（1003/1084/1217/1276） | **五处**：还有 `:1325`（`_finalize_group` 终局删除扫） | **偏移**：R4"执行阶段不得调用 discard_skill"的靶点清单少了一个；虽然 1325 经 `decision.live_final_action` 驱动、方向正确，但 R4 的审计测试若按方案列的四处写断言会漏检 |
| `reports.py` **17 处** `la._*` 私有访问 | **6 处** `la._*`（`:211,212,222,230,237,246`；含公开名共 9 处 `la.*`，另有 6 个从 acceptance 导入的私有名） | 琐细（高估了 R6 的工作量画像） |
| `decision.py` 13 个纯函数 | **12 个**（`^def` 公开函数计数） | 琐细 |
| `upgrade_v3_config` 在 `core/config.py:641-742` | 函数在 **:619–861** | 琐细 |
| R9/§8"**两份** v3 配置人工审定" | impl §4 与 configs/ 实际是 **7 份**要转（4 份 `*_v3.yaml` + 3 份 `*_v2_v3.yaml`） | **偏移**：验收面被砍掉 5 份，转换器（W3-C）漏测风险 |
| 其余（registry 13 项、:45/:82-90、exam_once 301 行 :270/298、reports :44、evidence 34 行、author 23 行、METHOD_SLOTS :82、METHOD_DEFAULTS :89、_validate_methods :204、cli until_stage :173、STAGE_NAMES pipeline:61、stage6 :195/:330/:357、models.py:235、`pytest` 2130/3） | **全部属实** | — |

**建议**：修订数字；R4 靶点补 `:1325`；R9/W3-C 的审定清单改为 7 份并与 impl §4 对齐（或明确写"两份方法形态 × 七份配置"）。

### F11（偏移）"每代理一个提交、每个提交全绿"与波内并行在合并时序上矛盾

**问题**：§6 说"Wave 1 每个代理各一次（六个提交，便于回退）"且"test_layering 与 ruff 通过才提交"。但六个代理的改动互相消费：W1-A 收敛 `reports.py` 为单一报告后，W1-C 的决策入口、W1-E 的证据包读取若未同步适配即红。六个独立提交若按到达顺序合并，中间态几乎必然有红提交；若要求每个合并点全绿，就要求代理间串行等待，"并行"名存实亡。方案只给了"W1-A 先广播字段"这一个缓解，没有给**合并顺序**与"允许的临时红区间"。

**证据**：方案 §5/§6；reports.py 被 decision（`live_acceptance.py:169-174` 经 decision 常量）与测量半边共享的现状。

**建议**：明确 Wave 1 的合并策略：要么(a)六个代理在共享集成分支上按 W1-A → 其他 的顺序串行合并、每合并一个跑一次全量；要么(b)接受"分支内可红、合并到主线前统一绿"并修改 §6.3 的措辞。同时规定 `log.md` 由各代理追加自己的段落（并发追加的冲突在所难免，需 rebase 规则）。

### F12（琐细）W1-A"先冻结 AcceptanceReport 字段"与 R0 冻结重复；R0 的落点二选一未定

**问题**：R0 已声称把四个形状"落进 `core/models.py`（或新增 `core/contracts.py`）"冻结；§5 又说 W1-A 的第一件事是"把 AcceptanceReport 的具体字段冻结并发给其余五个代理"。若 R0 真冻结了，W1-A 的广播是重复动作；若没冻结，则 R0 没完成自己的判据。另外"models.py **或** contracts.py"的二选一悬而未决，六个代理的 import 面都悬空。

**建议**：R0 定死落点（建议 `core/contracts.py`，避免 models.py 再膨胀）；W1-A 的"第一件事"改为"复核 R0 形状与验收场景的贴合度，差异回报 R0 owner"。

### F13（琐细）其他执行性备注

- 评审人首次用系统 Python（anaconda 3.14）收集测试即 `ModuleNotFoundError: expedistill`；全绿判据隐含"必须用 `.venv`"。方案 §6.3 的"pytest 全绿"应写明 venv，避免并行代理在错误解释器下白跑一轮（对应外层 AGENTS.md 第一节"改一次跑一次"的教训）。
- golden 矩阵 8 组合全量重跑在 Wave 1 会成为六个代理共同的验收成本（~1 分钟/次），可行但建议波末统一跑，而非每代理自行跑（方案已是这个意图，明确写出来即可）。
- 方案 §1 存档分支引用 `wip/m43-old-boundaries @ 9a8f058`，本仓库未验证其存在与可捡零件（超出只读评审范围，仅提示 Wave 1 开工前核对）。

---

## 2. 已核实为正确的起点事实（供其余两路评审复用）

- HEAD = `c6a9fb8`（"Add upgrade_v3_config"），工作区除 log.md 与两份新文档外干净；
- `.venv/bin/python -m pytest -m "not live"` → **2130 passed / 3 deselected，55.87s**，与方案 §1 完全一致；
- `methods/registry.py:45 _ENTRIES` 恰 13 项，v3 槽位键与 M3 族键并存属实（`:46/:49/:58/:61/:63/:66`）；
- `tests/golden/semantics.py` E1–E6 齐备（`_e1.._e4, _e6, run_e5`，共 793 行），基线夹具 `tests/fixtures/golden_semantic_baseline/` 8 组合在位；
- `tests/test_layering.py` 存在（97 行）且当前绿；
- `composites/exam_once.py` 301 行，作答（:276 起）与判卷（:297 起）确在同一函数 `exam_once` 内；
- `methods/evidence/__init__.py` 34 行（仅 SegmentationParams）、`methods/author/__init__.py` 23 行（仅 AuthoringParams）属实；
- `benchmarks/base.py:53 RUN_STATUSES = ("success","failure","infra_error")` 属实；
- 五个交付驱动三种签名属实（none/whole_bank `(ctx,params,...)`、per_skill `(ctx,params,skill)`、group_by_task `(ctx,params,store,benchmark)` 返回闭包、retrieval 另带 `write_audit`）。

## 3. 总体判断

骨架（差距表 R1–R10 的诊断、R0 先冻结、四波结构、逐文件所有权原则、E 仪器作判据）是健全的，起点事实核查通过率高。但方案在**判据可达性**上有三处硬伤（F1/F2/F4），在**所有权完备性**上有一处系统性遗漏（F3/F6），在**冲突裁决完备性**上漏了至少五条（F5）。这些都属于方案文本层面的修订，不动骨架；修订完成后方可放 Wave 0。
