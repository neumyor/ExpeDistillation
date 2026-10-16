日期：2026-10-16
一句话说明：执行—学习重构 M4.1 的验收评审 B——简洁性与反过度设计。
对应 log.md 条目：2026-10-16 M4.1 等价仪器先行。

---

## 0. 评审范围与独立复验

- 对象：`git diff aee0031 2ca0632`（+4103/−4，15 个文件），HEAD 2ca0632。
- 契约：docs/decisions/execute_learn_implementation_20261015.md 的 §1 纪律（简洁、避免
  over-engineering、不加新 hash、完整保留异常分类与恢复机制）、§8（E1–E6 预注册准则与
  live 白名单）、§9 的 M4.1 子步行。
- 本评审独立复验的证据（非转述提交说明）：
  1. `pytest` 全量：**2084 passed + 3 deselected**（53s，与 log.md 数字一致）；其中
     `tests/golden/` 55 例（11 既有 + 44 新增）3.1s 全绿；
  2. `ruff check tests/golden/` 通过；`tests/test_layering.py` 3 passed；
  3. diff 范围核实：仅 log.md + tests/ 下 14 个文件；`src/`、`pyproject.toml`、
     `configs/`、既有金矩阵 `tests/fixtures/golden/` **全部零改动**（§8 的承诺核实）；
  4. 三份新代码文件全文逐行读完；基线束 8 份投影逐准则做字段与体积分解（总量
     406,690 B ≈ 416 KiB，与 README 声称一致）；
  5. `v4_layout` 对 exam/live 两条序列手工推导核对（含 p04/p06/p08 的
     rewrite_failures 序号 0/1/2 与 p03/p05_B 的 verdicts 序号 0/1），与 pin 测试
     的字面期望一致。

先说总判断：**M4.1 的"厚度"每一处都能指认为一条契约条款或一个当下消费者，没有发现
提前备料给不存在准则的机器**。仪器的体积大头是数据（8 份投影 + 布局数据表），不是
逻辑；逻辑部分（canonicalize 225 行内、E5 机器约 160 行）无一处是"通用框架"的形状。
发现 6 条琐细、0 条偏移约束。

---

## 1. 基线束形状与提取器位置 ——【赞同】

**投影字段集逐准则对照 §8，无偷加**：

- E1：skill_id / content / final_version / status / discard_reason / group /
  merged_from / rewrite_history 逐条——与 §8 E1 的括号清单**逐字一致**，8 字段一个
  不多；
- E3：with_bank / bare（自 control 改名）/ delta / aux / control_source +
  retrieval_audit 行——与 §8 E3 一致；
- E4：(role, prompt_name, request_text) 三元组——恰是 T9 的"渲染请求文本"口径；
- E6：verdicts + attribution 整行、round 规范化——与 §8 E6 一致；
- E2 的 7 个小节看似超出"逐技能/逐组终态判定与 discard_reason"的一句话，逐节溯源
  都有出处：terminal_states 是那句话本身；unborn 与 generation_failures 原始行是
  §8 E2 映射（"v3 未出生 ↔ v4 discarded(model_output_error)"）的**推导源**，不读
  它映射无从执行；leak_verdicts 是 exam 侧的 E6 对偶（E6 的立法理由"防判据漂移但
  better 布尔不变绕过"对泄露判定同样成立，且 §3 exam_report 读取器明文读它、B2-4
  定它是跨相位单一真相）；rewrite/revision_failures 是"改写预算耗尽→下个 A 重答同版"
  语义（E5 的 C-1 窗口正是这条）的账本。E2.groups.final_round 是一个可从 E6 推导的
  辅助字段，纯为 diff 指认便利——1 字段的便利性开销，接受。

**体积**：416 KiB 中 E4 的 request_text 占 81–87%（逐文件核算），这是 T9 明文要求
  的比较口径，不是备料；整束小于既有金矩阵快照（861 KiB）的一半，且是它的投影。
  提交派生数据与本仓库提交金矩阵 fixtures 的既有惯例一致——预注册束的任何后续改动
  都以 git diff 形式可评审，这是特性不是负担。

**提取器位置**：`tests/golden/extract_baseline.py` 放对了。三个理由：① 仓库先例——
  金矩阵生成器 `tests/golden/generate.py` 就住在这里，提取器与它同性质；② 它不是
  一次性工具：常驻测试 `test_extraction_reproduces_the_committed_bundle` 把"重提取
  逐字节复现已提交束"作为常驻自检，提取器是该测试的**被测对象**，是测试夹具的一
  部分；③ AGENTS §五对 scripts/ 的定位是"调用 CLI 或读取产物的薄脚本"，提取器
  import tests.golden.semantics 共享投影、自身被测试消费，两头都不薄，放 scripts/
  反而错位。

---

## 2. 归一化管线 ——【赞同】

**SEQUENCES 数据驱动 vs 硬编码两张表：数据驱动更简，三条论据**：

1. 双消费者：`SEQUENCES` + `phase_dir` 同时被 `v4_layout`（semantics.py:145）和
   测试侧 `_synthetic_v4`（test:177–234）消费。硬编码表的话，测试要么再硬编码一遍
   路径（两份真相），要么反向查表（更绕）；
2. 规律性：exam 表的 4 个 L 是 `[A,L]×(R+1)` 的重复，规则（B 后 L 拥有
   attributions/revision_failures、A 后 L 拥有 rewrite_failures、序号取分片位置）只
   写一次；硬编码要把这些序号（0/1/2）手写四遍，抄错一处即静默错映射；
3. 输出有锚：`test_v4_layout_pins_the_predicted_phase_paths` 用**字面路径**钉住推导
   结果，等于兼得两边的好处——推导不怕抄错、字面期望防"推导与测试同源同错"。

**canonicalize 的分层（快照归一 → 布局映射 → 投影 → 比较）**：每层恰好对应 §8
归一化清单的 1/2/3 条（volatile 键在 harness 既有 snapshot、布局映射在
canonicalize、新字段剔除由"投影显式列字段"天然成立）。把布局映射折进 6 个视图
函数会把同一张表散六处；现在的分法每条归一化规则有一个家。

**control_source 别名表**：结构正确——未命中的 token 逐字透传后在 E3 转红（fail-loud，
逼人改表），所以 3 个 v4 拼法猜测（D / phase_D / p01_D）只是省一轮 M4.3 迭代的便利，
不是必需的机器。5 个条目里确有死重（见 B-4），但量级是 4 行字典项。

---

## 3. 断言模块结构与双层 API ——【赞同】

**文件数与职责**：新增代码只有 2 个文件（semantics.py 仪器本体、extract_baseline.py
提取器）+ 1 个测试文件 + 基线束，加上 harness.py 的两处小改。与既有布局形成对称：
金矩阵 = generate.py + test_golden_matrix.py + harness；基线束 = extract_baseline.py
+ test_semantic_instrument.py + semantics。semantics.py 711 行听着大，但段落结构清晰
（数据表 / canonicalize / 视图 / 比较 / E5 五段），且 E5 机器放这里而非 harness.py
可辩护：E5 是六准则之一，RevisionWindowCrash 是 C-1 窗口的仪器专属插件，不是通用
harness 能力。

**compare_combo 返回逐准则 JSON 报告 + assert_semantically_equal 门——双层 API 的
双消费者当下就存在，不是给 M4.3 的投机设计**：报告结构的现役消费者是 tamper 套件
自身——`_expected_red` 依赖逐准则 pass/fail 与 diff 字段来断言"恰这些准则红、其余
绿、diff 指认正确"（13 处调用）；门消费于全部身份测试。M4.3 只是第二组消费者
（报告进证据文件、门做断言）。若只有门（无报告），tamper 测试就无法证明"无关准则
保持绿"这个精度声明。

---

## 4. E5 的参数化与 RevisionWindowCrash ——【赞同】

**没有造通用崩溃注入框架**（上轮明确否决"通用第 k 次写盘崩溃遍历"）：`resume_variants`
是固定形状——3 个分数派生的 before_call + live 组合追加 1 个 revision_window，共
4 个变体；没有"第 k 次持久化点"参数、没有可插拔崩溃种类枚举、没有变体注册表。

**RevisionWindowCrash 恰好够用且推断有据**：单一谓词（注入块携带 G11 标记）触发
一次性崩溃。"标记在块 ⇒ 修订已落库"的推断依赖"块是 skills store 的纯派生
（survivors_and_block）"这一事实，成立；且被两处验证兜住：`_crash_in_revision_window`
崩溃后检查 store 内确有带标记的技能文件（semantics.py:667–676，"fix the crash
predicate, do not weaken the check"的态度是对的），专项测试再钉"零条重答轨迹"。
按内容而非路径/轮号判定，天然布局无关——M4.3 轮次变相位后不用改。

**installed_plugin 逐段自装是最小解**：由既有 `installed_plugin` 的拒绝嵌套语义强制
（同名注册嵌套会互相拆台），两条出路——改 harness 的安装机制成可嵌套（动既有共享
机器，面更大），或逐段自装（现状）。选了小的，且"callers must NOT wrap"的约束在
两处 docstring 写明。

---

## 5. tamper 自检 ——【赞同】（附 1 条重复，见 B-3）

**实现没有绕路**：`_tampered` 是 json round-trip 的**内存**深拷贝（test:259–262），
比文档描述的"复制到 tmp 目录"还简单，已提交束零接触。文档陈述过时见 B-1。

**11 种的独立价值逐个核过**：E1 content（E1 转红指认字段）、discard_reason 双准则
（E1+E6 之外的第一处跨准则红）、E6 删非终轮行（钉"E2 组视图用终轮"的分离）、
E6 删终轮行（见 B-3）、快照级删终轮 verdict（E2+E6 耦合——bundle 层 E2 是冻结小节、
快照层 E2 是派生视图，两层行为不同这一点只有此测试能证）、E4 request_text、
live 白名单负例（v4 侧多一条出题调用必须转红——白名单不是免检牌的直接证据）、
E3 audit 行内容变化（顺带钉"纯重排不算差异"）、E3 标量字段（走 `_diff` 而非多重集
的另一条比较路径）、E2 unborn 注入（v3↔v4 映射小节的负例）、E2 组终局翻转。
六准则每个至少一个红测试 + 白名单负例 + 快照级耦合，"每个断言都能抓住它声称抓的
差异"的模块 docstring 声明在比较器层面成立（派生探针的缺口见 B-5）。

---

## 6. 44 条测试的密度 ——【赞同】

构成：2 布局钉 + 20 身份（重提取复现 1、当前矩阵 8、现跑 2、合成 v4 8、v3 恒等 1）
+ 11 tamper + 11 E5（形状 1、provenance 公式 1、全组合变体 8、窗口专项 1）。
`tests/golden/` 全套 3.1 秒——常驻套件的体格，不是装饰。

**无空洞凑数**：身份四类各证一个方向（束可复现、M1–M3 无行为漂移、run 目录入口
端到端、v4 侧映射端到端）；8 组合参数化是矩阵自身的轴，不可收；现跑只测 2 组合
不是缺口——既有 `test_fresh_run_matches_golden`（8 组合字节等）× 本套"当前矩阵 ==
束"（8 组合）复合传递 closure，2 个组合只需钉 compare_combo 入口机制本身。E5 测试
顺带把"今天代码的调用数 == 69904f7 调用数"钉住（任何漂移都会移动所有崩溃点），
这不是凑数断言。可合并的只有 tamper 一对（B-3）。

---

## 7. skip_audit 容差扩展 ——【赞同】

这是对既有 harness 的唯一实质修改，按"是否最小修复"核了三个替代方案：

1. **"让注入器模拟真死亡"在本 harness 不可达**：skip_audit 行是 K1 的 error-stop
   门在**异常面升起时**给排队兄弟单元写的——进程内无论怎么注入（任何异常类型），
   门都会在异常传播前执行；唯一真模拟是每变体起子进程（插件安装、脚本假模型、
   调用计数全要搬进子进程协议），harness 重写量远超 20 行容差。且 E5 的窗口崩溃
   恰在 K1 派发内触发，是既有三个分数崩溃点没走过的路径——问题是这次才暴露的
   原因，成立；
2. **改 src/ 的门**在 M4.1 被冻结，不可能；
3. **整文件忽略（3 行）vs 单边容差（约 20 行）**：选了单边（resumed 可多不可缺），
   与既有 `_is_crash_row` 对 usage/请求行的同类容差同构，与文件"真差异是发现不是
   噪声"的哲学一致。多出的 8 行买到的是：skip_audit 在比较器里永远不是自由变量。

**弱化评估**：skip_audit 是进度簿记（heartbeat 只数行数），无状态消费者，E1–E6
无一读它；真被跳过的单元会因轨迹缺失在结果态比较里抓红（docstring 写明）。对既有
2 个 crash-resume 组合零影响（改前已绿）。结论：这是约束结构下的最小修复，且方向
保守。

---

## 8. 无 src/ 改动与新依赖 ——【赞同】（核实）

- `git diff --name-only aee0031 2ca0632`：log.md + tests/ 下 14 文件，**`src/` 零
  文件**、pyproject.toml 零改动、configs/ 零改动、既有金矩阵 `tests/fixtures/golden/`
  零改动——M4.1"不改 src/ 一行、不动金矩阵"的承诺逐项属实；
- 新代码 import 面全部是 stdlib + 既有 expedistill 模块 + pytest；无新第三方依赖。

---

## 9. 发现汇总

| # | 级别 | 内容 |
|---|---|---|
| B-1 | 【琐细】 | 陈旧文档计数/描述 3 处：test 模块 docstring 与束 README 写"9 种定向篡改"（实际 11，log.md 反而写对了）；且三处（test docstring:13、semantics.py:91–92 的 BASELINE_DIR 注释、README）把篡改描述为"复制到 tmp 目录再改"，实现是纯内存深拷贝。改注释即可 |
| B-2 | 【琐细】 | `CRASH_FRACTIONS` 与崩溃点公式现在双份定义：generate.py:28 + 73–74（内联）与 semantics.py:562 + crash_points。E5 边界契约的两处真相。有兜底：`test_baseline_provenance_matches_the_variant_formula` 用 69904f7 的 provenance 对公式，静默漂移会转红——但 M4.3 重写 generate.py 时应收敛到单一出处（harness.py） |
| B-3 | 【琐细】 | tamper 第 4 例（删终轮 verdict 行，test:311）与第 3 例（删非终轮行，test:297）在 bundle 层同覆盖：同一比较器、同一期望结果（仅 E6 红），bundle 层 E2 是冻结小节，删哪轮都不影响它——两例的真正分野只由第 5 例（快照级耦合）证明。第 4 例可并入第 3 例的注释叙事 |
| B-4 | 【琐细】 | 小件死重：CONTROL_SOURCE_ALIASES 的恒等项 "fresh_bare_run"→"fresh_bare_run" 在 `.get(key, key)` 缺省下是死条目；`_e2(snapshot_data, acceptance)` 的 `acceptance` 参数未被使用（semantics.py:258）。各 1 行，顺手可清 |
| B-5 | 【琐细】 | E2 未出生映射的 store 探针（`_skill_in_store`，semantics.py:252–255）与映射循环体（:268–275）**没有任何测试真正执行过**：8 组合的 generation_failures 全空（P6 已声明的恒空真），unborn tamper 只改视图不动快照。金矩阵上该分支的任何 v4 失败行仍会经原始行多重集转红（不会假绿），但探针的 key 拼法（复刻 store.path_for 的 quote 约定）写错时无人能抓。建议补一条快照级 tamper：注入一行 generation_failures（store 有/无该技能各一），或注释声明该探针的钉住归 M4 场景测试 |
| B-6 | 【琐细】 | canonicalize 的 `round_field=None` plain-move 分支（semantics.py:212–214）是"后写覆盖"语义，而 `generation_failures` 在 v4_layout 里是**逐 L 多源条目**（:181–182）——若 v4 在多个 L 相位都写了失败行，只有最后一个分片存活。金矩阵不可达（基线恒空、任何 v4 失败行仍转红，只可能让 diff 指错行数、不可能假绿），且合成 v4 测试也只把账本放进首个 L。修法很小：让 None 字段的 list 值也走分片拼接（dict 值保留 plain-move），或在 docstring 声明 generation_failures 的单源假设 |

无【偏移约束】。

---

## 10. 结论

**M4.1 验收通过。** 0 条偏移约束、6 条琐细（B-1 文档陈旧、B-2 契约常量双份、
B-3 一对可合并的 tamper、B-4 两处 1 行死重、B-5 恒空分支的探针零覆盖、B-6
plain-move 的多源覆盖——全部不阻塞，且无一条指向"为不存在的需求造机器"）。

本步的简洁性成色：仪器的每一层厚度都有出处——投影字段逐条对得上 §8 的括号清单，
布局表是数据不是代码路径，E5 是 4 个固定变体而非框架，双层 API 的两个消费者今天
就坐在测试套件里，对既有 harness 的唯一修改（skip_audit 单边容差）是"进程内注入
+ src 冻结"约束结构下的最小解且比替代方案更保守。6 条琐细里最值钱的是 B-5/B-6
这对：都源于同一个事实（金矩阵恒空真，P6 已声明），一个让探针零覆盖、一个让
多源合并语义悬空——它们是"仪器在金矩阵上永远够用"与"仪器代码在金矩阵外可能
说谎"之间的缝，修法都是个位数行。

给 M4.3 的三个核验收口（本评审新增的观察，非本步义务）：① v4 实际布局与预测
不符时，兑现"只改 SEQUENCES/v4_layout 数据表"的承诺（断言函数无路径硬编码，
本次已核）；② 重写 generate.py 时把 CRASH_FRACTIONS/crash_points 收敛单一出处
（B-2）；③ 若 v4 真在多个 L 相位写 generation_failures 账本，先修 B-6 再跑
compare_combo，否则 diff 会少报行数。
