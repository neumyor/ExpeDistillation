日期：2026-10-15
一句话说明：执行—学习实施方案的第二轮评审 C——迁移风险与可复现性。
对应 log.md 条目：2026-10-15 执行—学习闭环实施方案（第一轮三路评审后）。

---

## 0. 总评

**结论先行**：M 步骨架（M1 数据对象 → M2/M3 纯抽取 golden 无 diff → M4 序列驱动
+ schema v4 + 语义验收）经代码核验**方向成立**，第一轮 9 条偏移约束的裁决
（v2 §10 D1–D11）总体落实忠实。但实施方案在**四处会当场翻车的地方**与字面
不符，M4 开工前必须修：

1. **M1 有一个隐藏半接线**：`from_dict` 做 v1→v2 显式推导后，装配器
   `assemble_reuse.py:1189` 的"bank 行逐位等于 store 对象"校验对**任何 v1 对象
   源 run**（今天全部正式 run）当场报"source is corrupt"——M1 验收（pytest 全绿
   + golden diff 披露）**抓不到它**，因为装配测试夹具都是用当前版本对象构造的
   （I1）。
2. **M2/M3 的两处"搬调用"分层不可行**：`audit_leak` 的现行调用方在第 5 层
   （composites/acceptance.py:82,370），把它搬进 methods 层的"execution/exam
   编排"后"调用点不动"= 第 5 层 import 第 6 层 = test_layering 红灯；M3 的
   "K4/K6 决策半边改调 learn 驱动"同理，且连带 `test_acceptance_resume` /
   `test_live_acceptance_resume`（逐调用点/逐写盘点崩溃续跑）在 M3 被迫重写
   （I2、I3）。
3. **M4 的验收仪器 E1–E5 有一处漏比、一处回缩、一处不可执行**：没有任何一条
   比对 live 的 verdicts/attribution 账本内容（测量面的直接证据，M4 恰是测量核
   重接线的一步）；E1 字段集比第一轮 C2 预注册的少了 rewrite_history 与
   status/discard_reason；live 白名单②（merge_check: none 不撤销合并）在金矩阵
   **不可观测**——假模型 G7 恒返回空 similar_groups（harness.py:286-288），
   金组合里根本没有合并、也没有回归考（实测 live 金组合 0 次 answerer/judge/
   auditor 调用），②作为矩阵验收项是空转（I4、I5、I6）。
4. **M4 过大风险的缓解（§11"E1–E5 先行 + 滚动验收"）不够**：schema v4、注册表
   换键、相位驱动、harness 配置改写、golden 重生成是**不可拆的原子核**（五槽位
   配置驱动不了相位驱动器，v4 配置驱动不了 stage1–6），方案没有承认这一点，
   也没给出哪几个子提交必须 E 全等、哪些测试必须原样通过（I9）。

术语（沿用第一轮）：**golden 矩阵** = 假模型跑 8 组合把 run 目录全部产物拍标准照
的回归测试；**续跑契约** = 崩溃后同命令重启、逐单元跳过、结果与一口气跑完相同。

评审方法：通读两份方案（实施方案 + loop v2 §10 裁决），逐文件核对
tests/golden/harness.py、core/{config,models,ids}.py、infra/manifest.py、
stages/{pipeline,stage0_freeze,stage3_generation,stage6_test,assemble_reuse}.py、
methods/{registry,params,acceptance/*,curation/*,delivery/*}、
composites/{acceptance,live_acceptance,curation}.py、cli.py、
scripts/{run/convert_v2_configs,audit/audit_live_run}.py、tests/test_layering.py、
7 份 *_v3.yaml、金矩阵 fixture 内容（dump 的 role/prompt 计数）；跑通
test_layering + 金矩阵 10 项（14 passed）。只读仓库；未触碰 TB-dev 与任何 runs/。

---

## 1. 重点 1：M1–M3 自洽性与"golden 逐字节无 diff"的真实比较范围

### 1.1 harness 到底比什么（先立尺子）

`snapshot()`（tests/golden/harness.py:457-498）拍下 **run 目录下除 run.lock 外
的每一个文件**：JSON/JSONL 解析后归一化，其余文件按 sha256；`pipeline_log.jsonl`
按"每 stage 最后一行"规范化（:483-488，key 是 `row["stage"]`）；usage 与全部
llm 请求 dump 按排序多重集（:494-497，dump 剔 `seq`）。归一化只有四类 volatile
键 + run 目录路径擦除（:421-423, :440-454）。`assert_equal_ignoring`
（:506-521）= 除命名 key 外**全量相等**。所以"golden 逐字节无 diff"实际含义是：
**文件集合、每个文件内容（含 manifest、各 stage summary、全部对象 JSON、各账本、
skill_bank、注入审计）、调用多重集，一个都不能动**——能动的只有 time/
duration_ms/request_id/processed_this_call 与 run 目录路径。锁 sidecar（
`*.jsonl.lock`）是空文件（store.py:164-181 的 append 创建），sha 恒定，不构成
volatile。

在此尺子下逐项判断 M2/M3 的改动：

### 1.2 M2 三件事逐一核验

- **delivery 三新驱动、stage6 与 live 改调它们**：stage6 的 with_bank 臂本来就走
  `resolve("delivery", ...)`（stage6_test.py:316-322），控制臂是直连
  batch_execute 的裸调用（:332-349）；live 的注入是 `_injection_resolver` 包
  `la.survivors_and_block`（live_run.py:368-392）。抽成 `none` / `group_by_task`
  驱动并回接，只要产出相同的 K1 kwargs / (references, block)，golden 不动
  ——**成立**。一个前提要写进方案：live 驱动（methods 层）调新 delivery 驱动
  必须走注册表缝（tests/test_layering.py:77-97 禁 methods 模块互相 import；
  registry 是唯一合法缝，动态 import 不在 AST 检查内但符合"缝在注册表"的规
  矩）——§9 M2 未写机制（T8）。
- **K6 去轮化**：现行 judge_round 等签名带 `round_`，目录经 `la.round_dir` 推导；
  把轮序留在 live_run.py（methods 调 composites，6→5 合法）、测量核收"轮仓
  store + 基线计数 + 具名规则 + 冻结审计行"——纯签名重构，golden 不动。**成立**。
- **gen_exam/audit_leak 搬函数、调用点不动**：gen_exam 的调用方是 stage3
  （stage3_generation.py:78,267，第 7 层）——搬进 methods（第 6 层）后 7→6 合法，
  **成立**。audit_leak 的调用方是 **composites/acceptance.py:82,370（第 5 层
  K4）**——搬进 methods 后 5→6 是向上依赖，test_layering.py:62-74 红灯；在
  blocks 里留 re-export（blocks→methods）同样向上。**字面不可行（I2）**。
  修法见 I2。

结论：M2 的"golden 逐字节无 diff"在 I2 修掉后可成立；其余改动都是真纯重构。

### 1.3 M3 与"读取器不落盘"

- 报告读取器（§3 表）是纯函数、只读既有账本——**成立**，且它读的字段我在代码里
  逐一核对过：exam_report 的三源 = K3 attempts + `LEAK_VERDICTS_LEDGER`
  （acceptance.py:98，stage4_acceptance/ 下）+ 技能终态；live_report 的账本
  名与 `la.VERDICTS_LEDGER` 等一致；baseline/test_report 与 stage1/stage6 落盘
  对得上。不落盘 ⇒ 无写次序问题。
- **"决策经新模块调用"会不会改写盘次序**：只要决策逻辑的**位置**安对（见 I3），
  写盘次序由原循环骨架决定，golden 不动。但 §3 按字面把决策清单"实现在 learn
  驱动内"（methods 层），而 K4 的决策半边长在 `composites/acceptance.py:
  accept_skill`（第 5 层）、K6 的在 finalize_group/apply_revisions——第 5 层调
  第 6 层不可行（I3）。
- 改写账本（REWRITE_FAILURE_LEDGER）在 M3 **不搬家**（相位目录 M4 才有），
  §9 M3 只写"语义保持"——与 §5 第 3 行（搬进 L 相位目录）不矛盾，顺序正确。
  **赞同**。

### 1.4 M1 的自洽性：一个隐藏半接线（I1）

M1 内**run 内**是自洽的：所有写方（blocks/author.py:367 write_skill、
curation.py:1174 的 merge 产物、author.py:416 gen_exam）写新字段，run 内读方
不依赖新字段；golden 同提交重生成、diff 仅为新字段 + 对象 schema_version——验收
设计正确。跨版本读方只有一个常驻路径：**装配器**。它有两处"原始 v1 行 vs
from_dict→to_dict 产物"的比较：

- `assemble_reuse.py:1189`：`if row != stored.to_dict()`——row 是 read_jsonl 读
  的原始 v1 bank 行，`stored` 是 `Skill.from_dict(v1 记录)`（:1143）。M1 后
  from_dict 推导出 `source_subtasks`、to_dict 带 schema_version=2，与 v1 原始行
  **恒不等** ⇒ 每一行 bank 都抛 "bank row … differs from the stored skill …
  the source is corrupt"。今天**所有**源 run 的 Skill/Exam 都是 v1 对象
  （SKILL/EXAM_SCHEMA_VERSION=1，models.py:33-34），包括 tb21_split_reuse 正在
  用的 v2 冻结源——即 M1 落地瞬间，装配器对全部现存源 run 不可用，D2（v4 装配
  器支持 v3 源）的目标在 M1–M3 窗口内反而倒退。
- `composites/curation.py:945-946`：续跑补账 `store.append_bank(store.load(...,
  Skill).to_dict())`——对"从 v1 源装配出的 run"崩在最终 append 循环时，补写
  的是 v2 行，与既有 v1 行混在一个 bank 文件里（同根因，顺带修）。另注
  ：1273 的正常终写在 baseline 档位装配（store 是 v1 对象、无 bank）后全新跑
  stage5 时会产出"v1 对象文件 + v2 bank 行"的混版 run——能靠"消费方一律
  from_dict"工作，但要写进 README §2 契约（见修法 2）。

**为什么 M1 验收抓不到**：tests/unit/stages/test_assemble_reuse.py 的源 run 都是
用**当前版本**对象构造的（:158,168 的 Skill 工厂），M1 后即 v2——测试全绿。
这正是外层 AGENTS 一.3"对照是否真的是对照"的事故类型：夹具没有 v1 记录。

**修法（并入 §9 M1 行）**：
1. `:1189` 改为两侧同构比较（`Skill.from_dict(row).to_dict() != stored.to_dict()`
   ——顺带多验一行"bank 行可加载"）；`:1319` 的逐字节复检（拷贝文件 vs 原始行）
   不受影响，保持原样。
2. curation 补账路径用同一 helper（或明确"装配 run 的 bank 允许混版、消费方一律
   from_dict"并写进 README §2 契约）。
3. M1 验收加一条：**造一个 v1 对象的源 run 夹具，装配（bank 级）必须原样通过**
   （同时覆盖映射函数对扁平 merge 的推导）。

另记两笔 M1 细账（不阻塞，进琐细）：v1 **嵌套合并**（merged_from 含
`bank/merge/NNNN`）的递归推导在 from_dict 内做不了（需要兄弟记录），要么
from_dict 对此"推导不出"响亮报错（AGENTS 七.3 合规）+ 装配器走带 `skills_by_id`
上下文的显式迁移纯函数，要么直接规定映射函数签名（T5）；`_known_kwargs`
（models.py:98-104）现在的语义是"缺键 → TypeError"，from_dict 分支按
`schema_version` 判 v1 是**新行为**，要与"lenient about unknown keys"的 docstring
同步改写（琐细）。

### 1.5 M1 验收里"golden diff 逐处披露（仅新字段）"的完整口径

金矩阵快照里受 M1 影响的 key：`stage3_generation/skills/*`、`.../exams/*`、
`skill_bank.jsonl`、`stage6_test/bank/...`（C 相位冻结库里的对象，若有）、以及
对象 schema_version 数值。披露口径应写明"新字段 + schema_version 1→2"，避免
评审时把版本号当意外 diff（琐细级措辞）。

---

## 2. 重点 2：M4 的子提交序列（I9）

§11 的缓解（"每个子块独立提交；E1–E5 先行实现在 M4 内第一个提交落地，后续提交
滚动验收"）方向对但不够，缺三个承认：

1. **原子核不可拆**。schema v4（删 methods 五槽位、METHOD_SLOTS/DEFAULTS、
   _validate_methods——config.py:78-92,200-236）、注册表换键（§3）、相位驱动取
   代 stage1–6（pipeline.py:61-69 的 STAGE_NAMES 与 if 链）、相位目录/pNN 标签、
   harness 的 `write_config` 改 v4（harness.py:359-387 现在构造的是 methods 槽位
   配置）、golden 同提交重生成——**互相锁死**：v4 配置喂不进 stage1–6（stage0
   会 resolve 不存在的五槽位），五槽位配置喂不进相位驱动器。任何中间提交要么
   测试全红、要么 golden 纪律被破坏（"改产物必同提交重生成"）。§11"每个子块
   独立提交"必须先划出这个核。
2. **E1–E5 先行提交的可运行性**。比对器第一个提交落地时 v4 布局还不存在，
   E1–E5 无从跑起；它只能带"自检"（v3 基线对自身相等 + 类似
   test_golden_matrix.py:68-81 的 tamper 反证）与 69904f7 基线加载器（`git show
   69904f7:tests/fixtures/golden/…` 或固定检出）。"滚动验收"要写明**从核提交起**
   每个后续提交都重跑 E1–E5。
3. **upgrade_v3_config 可以先行暗落**。它是纯函数：先落函数 + 对 7 份 v3 配置的
   期望形状单测（逐份断言生成的 sequence/phases 字典），不翻
   CONFIG_SCHEMA_VERSION；核提交后再跑转换器实录。

**建议的 M4 子提交序列**（替换 §11 那一行）：

| # | 内容 | 验收 |
|---|---|---|
| M4.1 | E1–E6 比对器 + 归一化表（路径映射、标签映射、对象新字段剔除）+ 69904f7 基线加载器 + 自检/tamper 测试 | 比对器自检绿；不动任何 src |
| M4.2 | `upgrade_v3_config` 纯函数 + 7 份配置期望形状单测（不接线） | 单测绿；CONFIG_SCHEMA_VERSION 仍 3 |
| M4.3 | **原子核**：schema v4 + 加载期检查 + 注册表换键 + phases/{freeze,execute,learn} + pipeline 序列驱动（pNN 标签、相位目录、until_phase）+ 删 stage1–6 与五槽位 + harness write_config v4 + golden 全量重生成 | pytest 全绿；**E1–E6 对 69904f7 全等（exam 全等、live 恰①）**；M2/M3 崩溃续跑套件原样通过 |
| M4.4 | cli（until_phase、summary 从冻结序列推导、resume hint）+ audit_live_run（相位目录/标签/D 相位 shortfall/**manifest 模式分派**）| pytest 全绿 + audit 自检；E1–E6 重跑不变 |
| M4.5 | assemble_reuse：v4 布局 + v3/v2 源支持（§4 缺项补齐后） | 装配测试（含 v1 对象源夹具）绿 |
| M4.6 | README/AGENTS/runbook 同步 + 转换器实录 7 份 *_v4.yaml + 逐份人工审定留档 + log | 文档与代码一致；7 份 v4 配置加载通过 |

M4.4/M4.5 不改 run 产物 ⇒ 矩阵在 M4.3 后必须一直无 diff——这是"滚动验收"的
落点。

---

## 3. 重点 3：upgrade_v3_config 与转换器（对 7 份配置逐份核对）

### 3.1 映射表无漏项（赞同，附核对记录）

逐份核对 configs/experiments/ 下 7 份 `*_v3.yaml`（alfworld_skillopt_split_v2_v3/
alfworld_skillopt_split_v3/searchqa_skillopt_split_v3/tb21_full_v2_v3/
tb21_split_live_run_v3/tb21_split_reuse_v3/tb21_split_v2_v3）：

- exam 类 5 份：`acceptance: {name: exam, retry_limit: 2}` → §4 第一行
  （sequence=[D,L]+[A,L]×3+[C]，merge_check=exam）✓；delivery whole_bank×3、
  retrieval×2（top_k 5/max_categories 3/min_score 0.05 或 0.02）→ C.delivery ✓；
- live 类 1 份：`{name: live_run, n_attempts: 3, improvement: strict_rate_gain,
  max_injection_tokens: null}` → §4 第二行 + B.execution/B.compare.rule ✓；
- 全部 `authoring: {exams: true}` → 删除（隐含）✓（"exams:false 仅 live 可转"
  的规则在这 7 份里不触发，规则无害）；
- `curation: {agentbrew, rounds 3, token_budget 96000}` → L.curate 同参数 ✓
  （与 §4 示例数值一致）；
- `segmentation: {K: 5, retry_limit: 2}` → L.evidence ✓；
- 全局字段（mode/split（含 reserved）/tasks/models/rules/token_budgets/
  model_options/weak_profile/prompts_digests）原样 ✓；full 模式 1 份 → C.control
  = reuse_d_weak ✓；live 是 split → fresh_bare ✓。

**结论：§4 映射表覆盖 7 份配置无漏。** 补一点（琐细 T10 相邻）：转换产物的
"补全后形状"要与 `ExperimentConfig.__post_init__` 的补全一致（v2 先例里补全发
生在 from_dict，upgrade_v2_config 只搬键），§9 M4 已有"逐份人工审定留档"兜着。

### 3.2 拒载提示：方案说的"两处"实际是三处

- `load_config`（config.py:629 读版本、:638-643 抛错并指向 convert_v2_configs.py）
  ——方案点名 ✓，泛化成 convert_v3_configs.py 即可；
- `stage0_freeze.py:297` 的 `if frozen_version == 2:` 硬编码——方案点名 ✓，泛化
  为"version < 当前版即拒 + 指向维护分支规则（AGENTS 六.1）"，与现行文案
  （:298-302）同型；
- **漏了 `ExperimentConfig.from_dict` 的版本闸（config.py:519-530）**：报错文案
  同样硬编码 "convert the config with scripts/run/convert_v2_configs.py … resume
  runs frozen under v2"。v4 代码里它对 v2/v3 都是错话。该闸虽少被走到（manifest
  比对走 dict 直比），但它是防御层，文案必须一并泛化（T1）。
- 装配器 `assemble_reuse.py:1032` 的 `== 2` 硬编码属第一轮 C6 点名的同一族，
  方案把它归进 §7 一行——见 I7(b)。

---

## 4. 重点 4：装配器 v3 源支持——§7 一行不够（I7）

对照 `stages/assemble_reuse.py` 的硬编码，§7"身份比对经 upgrade_v3_config +
相位目录直读 + lineage 兜底 + v2 路径不动"缺五件（前四件是实现指导缺失，第五件
是 I1 的连带）：

- **(a) 白名单的 v4 拼法**。`_ALLOWED_DIFF_PREFIXES`（:121-127）里的
  `config.methods.delivery` 在 v4 不存在——delivery 是逐相位参数，允许重选的是
  **C 相位**的 delivery。因转换器生成的局部名固定为 D/L/B/C（§4），前缀可写死为
  `config.phases.C.delivery`（及其子键）；但要写明"局部名即身份，改名=新实验，
  白名单按转换器规范名拼写"。连带 `_section()`（:1108-1115）的 collapse 规则要
  同步改。
- **(b) v2 源的升级链歧义**。"v2 源维持现行 upgrade_v2 路径不动"有两种读法：
  (i) 继续用 upgrade_v2_config（函数不动）**再链** upgrade_v3_config 到 v4 形状
  ——正确；(ii) v2 源在 v3 形状下比对——错误（v4 prospect 是 v4 形状，全线误拒）。
  必须写明 (i)：`frozen < 4 ⇒ 逐级升到 4 再比对`，:1032 的 `== 2` 判断泛化为
  `version < CONFIG_SCHEMA_VERSION`。
- **(c) 读/写两侧的布局映射清单**。负清单（:148-188）与拷贝目标是 **v4 相位
  目录**，源是旧 KIND_DIRS + `stage1_baseline/accepted_infra_shortfall.json`
  （:471-484、:492-499）+ `stage2_segments/segmentation_audit.jsonl` +
  `stage3_generation/generation_failures.jsonl`（:1225-1245）。§7 的"拷贝清单/
  负清单改相位目录（v3 源按旧 KIND_DIRS 直读）"应展开成一张"硬编码点 → v4 处置"
  逐行小表，含 shortfall 在 D 相位目录的新路径（audit 行已提"D 相位 shortfall
  路径"，装配器行没提）。
- **(d) kept_exams 的过滤键**。:1170-1171 按 `exam.subtask_id in kept_subtasks`
  过滤考题；v4 的 Exam 带 `skill_id` 字段后按哪个键过滤（v4 源用 skill_id、v3
  源兜底 subtask_id）要写一句。
- **(e) M1 破裂的连带**（I1）：v3/v2 源的对象是 v1 记录，bank 行校验与补账路径
  在 M1 就坏——I1 的修法要在 M1 落，不能等 M4.5；装配拷贝保持逐字节（`shutil.
  copy2`，:453），消费方一律 from_dict，这条契约要写进 README §2。

另外 baseline 档位受影响面小（pinned 键全是全局字段，:770-818），只有 shortfall
路径随相位目录变——列入 (c) 的表即可。

---

## 5. 重点 5：标签与 CLI 的消费者清单

逐一核对 §6/§7 的消费者覆盖：

- `cli.py:232-257`：summary 按 `STAGE_NAMES`（pipeline.py:61-69）逐项打印 +
  完备性检查——§6 已列"改从 manifest 冻结序列推导" ✓（且比硬编码严，赞同）。
  **但** `_resume_command`/`_resume_hint`（cli.py:160-179）把 `--until-stage`
  写进续跑提示——`--until-phase` 要同步，方案未点名（顺带项，归入 M4.4）。
- `scripts/audit/audit_live_run.py`：:86 `LIVE_SUBTREE = stage4_acceptance/
  live_run`、:93 `STAGE1_SHORTFALL = stage1_baseline/...`、:499-504
  `row.get("stage") == "stage4_live"`——§7 audit 行（相位目录 + pNN 标签 + D
  相位 shortfall 路径）已覆盖 ✓。**漏一处**：:605-651 的**模式分派**直接读
  `manifest["config"]["methods"]["acceptance"]["name"/"n_attempts"]`——v4 manifest
  里没有 `config.methods`。audit 的"这是不是 live run、每臂几次"必须改从
  sequence/phases 推导（n_attempts 在 phases.<B>.execution.n_attempts）。audit 的
  `--self-test`（:763 起自建最小 manifest）会抓住它，但应写进 §7 行（T9）。
- runbook（docs/runbooks/live_run_acceptance_runbook_20261014.md:23-36,135-144）
  的目录树与 `grep stage4_live` 等命令——§7 已列 ✓。
- failed_units 的标签**写方**（stage2_segmentation.py:87 一类、methods/
  acceptance/exam.py:78 的 `stage4_acceptance`、live 的 `stage4_live`、
  batch_execute.py:151 的 `k1`）随驱动在 M4.3 一起换——§6"旧 stage 标签的消费
  者全部同提交更新"覆盖了读方，写方落在核提交里，成立。
- scripts/analysis/ 不读新 run 的 stage 布局（读的是 campaign 归档）✓；
  `scripts/run/fork_run_stage04.py` 是一次性恢复脚本，属维护分支范畴，不进跟随
  者——可在 M4.6 顺手注明。
- harness 的 ledger 规范化按 `row["stage"]` 取行（harness.py:483-488）——v4 行
  的 key 名是否仍叫 `stage`、标签形如 `p01:D`，§6 未写（T4）。

**结论**：消费者清单除 audit 的 manifest 模式分派与 cli 的 resume hint 外齐全
（两处都归琐细/顺带），§6 的"映射表（旧→新）写入 M4 提交说明"落实了第一轮 C9
的显式修订要求。

---

## 6. 重点 6：E1–E5 的可执行性与充分性

### 6.1 需要新写的断言函数（对照 harness 现有能力）

harness 现有：volatile 归一化 + 路径擦除（:440-454）、全量快照（:457-498）、
整键豁免比较（:506-521）、续跑多重集比较（:534-569）。E1–E5 还需要（§8 只给了
"归一化"一句，函数清单要落进 M4.1）：

1. **跨布局路径/标签映射表**（旧 stage 目录 → 相位目录；旧标签 → pNN）——第一轮
   C2 的 N3，§8 只写了半句"布局映射"；
2. **对象新字段剔除器**（source_subtasks/skill_id + 对象 schema_version 差）；
3. **E1**：bank 行多重集比较（按 §8 字段投影——见 I5 要扩字段）；
4. **E2**：v3 侧"未出生"重建器：选中 subtask 集 = 存储的 subtasks 过 manifest
   `rules.segment_select` 规则；有 generation_failures 行而无 skill 对象 →
   未出生(model_output_error)（stage3_generation.py:270-284 的现行语义）。
   这个口径 §8 没写（T10）；
5. **E3**：旧 `stage6_summary.json` ↔ v4 C 相位 summary 的改名直比（字段集我核对
   过：mode/bank_reference/control_source/with_bank/control/delta（含 aux）/
   infra_missing/infra_accepted/complete/infra_shortfall_accepted，
   stage6_test.py:394-405）——**另应把 `stage6_test/retrieval_audit.jsonl`
   纳入 E3**（delivery 的审计行是逐字节冻结的 run 产物，不是 summary 分桶，
   交付重接线正是 M4 动的东西，漏比可惜）；
6. **E4**：(role, prompt_name, request_text 归一化) 多重集差集比较（dump 字段
   已带 role/prompt_name/request_text，harness 快照里现成）；
7. **E6（新增，见 I4）**：live verdicts/attribution 账本内容比对。

### 6.2 豁免范围写得够不够

§8"不比各阶段 summary 分桶、中间落盘点、调用顺序"——**分桶豁免**的文件级清单
没有枚举：stage0–5 的 summary、stage4_summary 的 live group_details、
curation_log、segmentation_audit、generation_failures、failed_units、
skip_audit 各自"比/不比"要一行表说清，否则评审 M4.3 时每一条 diff 都要临场辩论
口径（对照外层 AGENTS 二.1"指标预先固定"）。**赞同**豁免本身（分桶按阶段重组
必然变），但清单要预注册。

### 6.3 漏比的（I4）与白名单可执行性（I6）

- **verdicts/attribution 账本**（`stage4_acceptance/live_run/verdicts.jsonl`、
  attributions.jsonl、attribution_failures.jsonl）是 live 测量的**直接落盘证据**
  （live_run.py:157-177 的读取器、`_round_detail` :395-409 的 summary 只是其投
  影）。M4 恰是"测量核签名重接线"的一步：若 judge 的 baseline/live 计数漂移而
  better 布尔恰好不变（金夹具里弱基线全败、修订后全过，余量大），E1–E5 全绿也
  放行。修法：加 **E6 = 逐任务组比对 verdict 行多重集（baseline/live/better/
  verdict/aux，modulo round→相位序号映射）+ attribution 计划行**。
- **D 相位 shortfall 记录**：金矩阵假模型不产生 infra 失败，shortfall 记录在矩阵
  里不可达——应在 §8 写明"矩阵不可达，由崩溃续跑套件与真实 run 审计钉"，而不是
  留成沉默缺口（任务书点名问的这项，答案是：**矩阵测不到，不算 E 的漏比，但要
  显式声明**）。
- **stage6 的 aux**：E3 已含 ✓；**D 相位（stage1）summary 的 aux 与 live verdict
  行的 aux** 无任何 E 覆盖——建议并入 E6（verdict 行带 aux），stage1 的 aux 写进
  豁免声明（琐细）。
- **白名单**：①"无出题/泄题/回归考调用"可机判，但要把"族"定义成 (role,
  prompt_name) 集合。实测金矩阵：live v3 组合有 (examiner, EXAM_GEN) 调用
  （whole_bank-full 6 次、retrieval-split 3 次），**零** answerer/judge/auditor
  调用（假模型 G7 恒返回空 similar_groups，harness.py:286-288 → 无合并 → 无回归
  考）。所以矩阵上①的机判形式 = **v4 live 多重集恰等于 v3 live 多重集删光
  (examiner, EXAM_GEN)**，其余任何差 = 失败。②"merge_check: none 下合并不被
  撤销"在矩阵**不可观测**（无合并、无回归考），必须改写为"② 由 M3 的
  merge_check: none 单测 + 真实 run 披露承担，不进矩阵验收"——否则 M4 验收项
  "live 差异恰为白名单两项"里②是空转，给人"已验"的错觉。

### 6.4 E1 字段回缩（I5）

第一轮 C2 预注册的 E1 = {正文, version, status, group, **rewrite_history 逐条**}；
实施方案 §8 E1 = (skill_id, content, final_version, group, merged_from)——丢了
rewrite_history 与 status/discard_reason。rewrite_history 记录每次 G6 改写的
error category 与 reason（models.py:230-234），恰是"A 重答键换相位序号 + 改写
账本搬家"要钉的数据；E2 只比终态，中间改写史漂移无人管。修法：E1 恢复
rewrite_history 逐条 + status/discard_reason（bank 行本就带）。

---

## 7. 重点 7：测试体系影响

**M2/M3 必须原样通过（方案已写或应补写）**：

- 金矩阵 10 项（test_golden_matrix.py:46-65，8 fresh + 2 crash-resume）——
  M2/M3 的"golden 逐字节无 diff"硬闸 ✓；
- `test_acceptance_resume`（逐模型调用点 + 逐写盘点崩溃续跑，
  tests/composites/test_acceptance_resume.py:278,387）与
  `test_live_acceptance_resume`（同型 + 归因/改写/no-op 窗口，:422,534,607-682）
  ——驱动面是 `accept_skill` / K6 函数（布局无关），M2 已显式点名 ✓；
  **M3 只靠"pytest 全绿"隐含——应显式点名**（M3 动的正是这些测试钉的决策半
  边），并接受 I3 的安家方案使它们在 M3 不需要重写（T11）；
- `test_acceptance.py:531` 的改写预算耗尽测试是 §11 第二行点名的专项单测的现行
  底版 ✓。

**M4 必须重写（核提交 M4.3 内）**：test_pipeline（STAGE_NAMES/until）、
test_stage0（v2 拒载文案、METHOD_SLOTS 解析）、test_stage1..6（路径/标签）、
test_cli（summary/until-stage/resume hint）、test_config（METHOD_DEFAULTS/
upgrade_v2/拒载文案）、tests/unit/methods/acceptance/*（Params 与 check 的新
家）、test_assemble_reuse（夹具布局；v4 布局在 M4.5）、golden harness 的
write_config（harness.py:359-387）与金矩阵再生、audit 自检的 manifest 构造
（audit_live_run.py:763-765）、prompts 冻结面测试（METHOD_PROMPT_NAMES 若随
authoring 删除而变化）。

**M4 必须原样通过（行为保持的证据面）**：tests/unit/{blocks,core(除 config),
infra,inject,llm,prompts}/*、tests/unit/benchmarks/*、tests/composites/ 里直驱
K 积木的套件（test_acceptance*、test_live_acceptance*、test_curation*、
test_exam_once、test_batch_execute、test_segment_checked、test_curation_resume）
——**前提是 I3 按建议安家（accept_skill/K6 对外签名保持）**；test_layering 一直
绿。

方案没有这份清单（§9 M4 只写"pytest 全绿 + E1–E5 + 7 份配置"）——把它并进 I9
的核提交验收：**"哪些测试原样通过"本身就是 M4 行为保持证据的一部分**，滚动评审
要按它核对。

---

## 8. 重点 8：第一轮 C1–C9 的落实核对（v2 §10 D1–D11 对照）

| 第一轮约束 | 裁决 | 实施方案落点 | 核验结论 |
|---|---|---|---|
| C1 golden 处置 | D6 | §1.3 基线钉 69904f7；M1 同提交重生成+披露；M2/M3 无 diff；M4 语义准则 | **忠实** |
| C2 等价准则 + ID 备选 | D1 | ID 不变（core/ids.py:88-117 核实未动）；E1–E5 | 忠实，**但 E1 字段回缩（I5）**；C2 的"golden 测不到的短路语义用单元级断言钉"未进 §8——由 M2/M3"原套件原样通过"隐含承担，建议在 §8 加一句点名 |
| C3 跳过键 | D5 | §3.1 现行续跑键逐条（裸答/泄题/带技能/轮仓完备+冻结行） | **忠实**（核对了 acceptance.py:127-193 的 (skill,version,round) 键） |
| C4 三项机制 | D10 | §3 决策清单 + §5 shortfall 行 | 落实，**weak_already_knows 漏了 strict 族限定**（live_run.py:459 `rule_name in la._ALL_SUCCESS_SAFE_RULES`；T2） |
| C5 处置表 | — | §5 六行 + §3 读取器表 | **逐条与代码相符**（stage3_generation.py:270-284、exam.py:164-169、REWRITE_FAILURE_LEDGER 语义） |
| C6 schema 清单 | D9 | §4 全套 + §7 文档同步 | 基本忠实；**from_dict 第三处版本闸漏泛化（T1）**；Subtask/Trajectory/Attempt 去留已定（§2 不变）✓ |
| C7 装配器 | D2 | §7 一行 | 裁决正确（选 v3 源支持），**行文不足以指导实现（I7）** |
| C8 重切 | D6 | §9 M0–M4 + 硬闸 | **忠实** |
| C9 标签/CLI | D7 | §6 + §7 | 忠实；until_phase 索引基准未定义（T3）、audit 模式分派漏列（T9） |

无漏网的大项；走样的都是上面已分级的小项。

---

## 9. 分级汇总

### 9.1 【偏移约束】（不修不能进实施；正文含证据与修法）

- **I1** M1 半接线：`from_dict` 的 v1→v2 推导使 `assemble_reuse.py:1189` 的
  bank 行校验对全部现存源 run（对象均为 v1）抛 "source is corrupt"，
  `curation.py:945-946` 补账同根因；现有装配测试夹具无 v1 记录，M1 验收抓不到。
  修法：§1.4（同构比较 + v1 源夹具进 M1 验收）。
- **I2** M2 "audit_leak 搬进 execution/exam 编排（调用点不动）"分层不可行：
  调用方 composites/acceptance.py:82,370 是第 5 层，搬进 methods 后 5→6 违反
  test_layering.py:62-74。修法：audit_leak（及一切被 composites 调用的原语）留
  blocks，"驱动编排"只新增编排代码；gen_exam 的搬迁（stage3 第 7 层调用）合法。
- **I3** M3 "K4/K6 决策半边改调 learn 驱动"同样不可行（决策半边在第 5 层，
  learn 驱动在 methods 第 6 层；methods 互相 import 另被
  tests/test_layering.py:77-97 禁止，注册表是唯一缝），且字面实施将解体
  accept_skill、迫使崩溃续跑套件在 M3 重写。修法：决策逻辑落 composites 层新模
  块，methods/learn 是注册表薄壳；accept_skill/K6 签名不变；M3 验收显式点名
  两套崩溃续跑测试原样通过。
- **I4** E 准则漏比 live 测量账本：verdicts/attributions（及 retrieval_audit，
  应入 E3）无任何 E 覆盖，M4 测量核重接线的漂移可绕过验收。修法：新增 E6
  （§6.3）。
- **I5** E1 字段集较第一轮 C2 预注册回缩：丢 rewrite_history 与
  status/discard_reason。修法：恢复（§6.4）。
- **I6** live 白名单②矩阵不可观测（假模型无合并、无回归考；live 组合 0 次
  answerer/judge/auditor 调用），①未机判化。修法：矩阵验收改写为"恰删光
  (examiner, EXAM_GEN)"；②改由 M3 单测 + 真实 run 披露承担（§6.3）。
- **I7** 装配器 §7 一行不足以指导实现，缺五件：白名单 v4 拼法、v2 升级链歧义、
  读写两侧路径映射表、kept_exams 过滤键、I1 连带。修法：§4 的 (a)–(e)。
- **I8** §3 决策清单"'还有后续验证轮' = 该 L 之后还有几个 learn 相位"数错了对
  象：验证轮是 A/B 执行相位；两个规范序列因严格交替而数值巧合相等，但 v4 加载
  检查不禁止连续 L，合法序列 [D,L,L] 下该判据给出错误动作（该删的改写）。修法：
  改为"该 L 之后还有几个 A/B 型执行相位"，或加载期强制 L 与执行相位严格交替。
- **I9** M4 子提交序列未承认原子核（schema v4 ↔ 注册表键 ↔ 相位驱动 ↔
  harness/golden 互锁），E1–E5 先行提交无可比对象，"滚动验收"无落点，测试存活
  面清单缺失。修法：§2 的 M4.1–M4.6 序列 + §7 的测试清单。

### 9.2 【琐细】（不阻塞，实施时顺手处理）

- T1 `ExperimentConfig.from_dict` 的版本闸（config.py:519-530）文案同样硬编码
  v2 转换器，"两处泛化"应改三处。
- T2 §3 weak_already_knows 短路漏 strict 族规则限定（live_run.py:459）。
- T3 `--until-phase` 的索引基准（p00:freeze 占不占 0）未定义；且现行
  `_check_run_settings`（pipeline.py:229-232）在 load_config 之前跑，越界校验
  要挪到冻结序列可见之后。
- T4 pipeline_log 行的 key 名（"stage"）与标签格式（`p01:D`）在 v4 的确切形状
  未写（harness.py:483-488、cli.py:227 都按 row["stage"] 读）。
- T5 v1 嵌套合并的 source_subtasks 推导需要兄弟记录，from_dict 内做不了：from_dict
  对"推导不出"响亮报错（合规）+ 装配器走带 skills_by_id 的显式迁移纯函数；
  from_dict 的 docstring（lenient-unknown-keys）要随分支行为改写。
- T6 "A 重答键含相位序号"与 §2"Attempt 不变"的口径要写明：建议键 = 相位 store
  作用域 + (skill_id, version)，retry_round 保留原义（相位内恒 0）或披露升版。
- T7 裸答/泄题"全局一次"与"账本在相位目录下"的共享落点未写：多个 A 相位要能
  看到首个 A 的裸答与泄题判定（跨相位只读），否则 E4 exam"相等"会在验收时才
  爆（方案应预答）。
- T8 M2 live 驱动改调新 delivery 驱动须走注册表缝（methods 互 import 被禁）。
- T9 audit_live_run 的 manifest 模式分派（:605-651 读 config.methods.acceptance）
  未列入 §7 跟随者行。
- T10 E2 的 v3 侧"未出生"重建口径（subtasks × segment_select 规则 +
  generation_failures）未写。
- T11 M3 验收应显式点名 test_acceptance_resume / test_live_acceptance_resume
  （M2 点了）。
- T12 manifest.json 留 run 根（装配器/审计/CLI 都按根读）与 freeze 相位 summary
  的位置要钉一句；§8"不比"的文件级清单要枚举（§6.2）。
- T13 M1 golden 披露口径写明"新字段 + 对象 schema_version 1→2"。

### 9.3 【赞同】

- A1 D1 采纳"ID 派生规则不变"备选：迁移风险最大单点的消除，E4 直接可比
  （core/ids.py:88-117 核实 ID 规则零改动）。
- A2 M0–M4 重切与 M2/M3"golden 逐字节无 diff"硬闸（C1/C8 落实忠实；harness
  的比较范围经核验确是全产物、全字段）。
- A3 D3 读取器纯函数不落盘：续跑语义免费继承，无第二套真相。
- A4 §5 失败处置表与代码现状逐条相符（stage3 出题失败=单元终态不落库、
  exam 驱动的 model_output_error 终态先写、改写 exhausted 行=轮次消耗证明）。
- A5 §3 决策规则清单覆盖第一轮 C4 全部三项机制（no-op guard、零执行终态、
  空洞继承政策），live/exam 规则与 K4/K6 现行为逐条对得上。
- A6 D11 出题调用点挪 A 限定在 M4、M2/M3 只搬函数——保住了两道 golden 硬闸。
- A7 §4 映射表对 7 份 *_v3.yaml 逐份核对无漏项；加载期检查承接了
  check_live_run/check_authoring 的全部跨约束。
- A8 D2 装配器选"v3 源支持"而非响亮拒绝——保住 tb21 复用场景。
- A9 E5 沿用 assert_resume_equivalent 语义；M2 验收显式点名崩溃续跑套件。
- A10 每步三路评审、【偏移约束】修复后重审、评审只读纪律——与第一轮协议一致。
