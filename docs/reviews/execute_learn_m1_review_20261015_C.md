日期：2026-10-15
一句话说明：执行—学习重构 M1 步骤的验收评审 C——迁移风险与可复现性。
对应 log.md 条目：2026-10-15 执行—学习闭环实施方案（两轮三路评审后定稿）的 M1 步骤（被审提交 32d6c86，diff 84d280e..32d6c86，契约 docs/decisions/execute_learn_implementation_20261015.md §2 与 §9 M1 行）。

---

## 0. 总评

**结论先行**：M1 的四个验收项**按字面全部达成**，且达成质量高——迁移纯函数三态
完备、golden 重生成干净且披露逐条属实、测试证据经我独立复验全部真实（含两处
I1 修复的回退转红）。但我在 I1 的**验收实质**（今天全部正式 v1 run 保持可用）
上发现了 M1 自己引入的两处新断裂：`from_dict` 对 v1 合并产物改为响亮报错后，
**只有装配器与补账两处按契约接了迁移函数，其余"全量加载"型消费方全部没接**。
后果具体且已复现：从 v1 源 run 装配出的复用 run（I1 夹具自己产出的那种）在
阶段 4 的技能枚举处**当场 ValueError 崩溃**，复用流程（stages 1–5 零调用 +
stage 6 重跑）根本走不到 stage 6；用当前代码审计含合并产物的 v1 正式 run
（`audit_live_run`，AGENTS 六.7 的标准流程）同样崩在第一步的技能枚举；v1 run
的 curation 续跑在库外存在合并产物（回退合并、嵌套内层合并——正是
test_curation_resume.py 模块 docstring 自己的参考场景）时同样崩溃。M1 之前
这些流程全部可用，M1 之后变成只有报错信息可看、没有任何工具能落地的死胡同。
这与上一轮 I1（装配器对全部 v1 源 run 误报 corrupt）是同一类"验收测试停在
链路半程、后半程当场翻车"的问题，只是这次断在验收测试结束之后的下一步。

判定为两条偏移约束（P-C1、P-C2，同一根因、同一修法），**M1 验收不通过**，
修复后重审。修复是纯读路径改动（把消费方加载与装配器同走迁移函数），不触碰
run 产物，不会扰动 M2 的"golden 逐字节无 diff"门。

---

## 1. 逐项核查

### 1.1 v1 源 run 的全链路可用性（I1 的验收实质）

**装配器内部：全部可用。** 源 skill 加载走迁移函数
（`src/expedistill/stages/assemble_reuse.py:1153-1156`：`skill_rows` 全量读入后
逐条 `Skill.from_dict(migrate_skill_record(record, skill_rows))`）；bank 行比对
两侧同构（`assemble_reuse.py:1207-1213`：row 先过 `migrate_skill_record` 再
`from_dict`，与已迁移的 `stored.to_dict()` 比较）；kept_exams 按契约留在
subtask_id 键（M4 才按 §7 ④ 换 `exam.skill_id`），v1 exam 经 `Exam.from_dict`
本地推导 skill_id（`assemble_reuse.py:1157-1158`）；lineage 沿 `merged_from`
在已迁移对象上走（`skill_lineage_tasks` 未改，`assemble_reuse.py:208`），
v1 合并的溯源经迁移后照常解析。

**I1 夹具覆盖的 v1 形态**：段生记录（全部降级记录）+ 扁平合并（bank 内的 m0，
`tests/unit/stages/test_assemble_reuse.py:608-642`）；curation v1 前缀续跑专项
测试是真崩溃注入（`SecondAppendCrashStore` 在第二次 `append_bank` 时抛
`Crash`，`tests/composites/test_curation_resume.py:483-519`），断言补出行保持
v1 形态且等于存储记录原样、零模型调用——崩溃续跑等价在该场景下成立。

**漏测且实际破裂的形态**：
1. **嵌套合并**在装配器层无端到端夹具（迁移递归只有单测
   `tests/unit/core/test_models.py:384-398` 钉住；静态核对装配器对嵌套合并是可
   用的——同一 `skill_rows` 全量喂给迁移函数）；
2. **库外合并产物**（回退合并、merged-away 的内层合并、pending 回滚）——
   见 P-C2，不只是漏测，是当场崩溃；
3. **装配产物 run 的下游可用性**——见 P-C1，I1 夹具在 `assemble_reuse` 返回
   处停止，装配出的 run 没有任何测试驱动过阶段 4/5 的枚举路径
   （唯一的端到端 smoke `tests/unit/test_cli.py:602-636` 也只调 CLI 的
   assemble-reuse 子命令，不跑后续阶段）；同理，审计工具对 v1 run 的读取路径
   （`scripts/audit/audit_live_run.py:160`）无测试覆盖。

### 1.2 旧对象读规则的三态完备

三态全部实现且有测试：v2 记录缺新字段 → TypeError
（`tests/unit/core/test_models.py:190-195, 214-220`，必填无默认值的构造侧同钉
`:174、:208`）；v1 段生 → 推导（`:322-331`）；v1 合并 → 报错指向迁移函数
（`:332-337`，`src/expedistill/core/models.py:354-361`）。推导不出的三类报错
齐备：未知 ID 形状（skill `models.py:362-369` / exam `models.py:378-384`，
测试 `:338-343`、`:356-361`）、缺兄弟（`models.py:439-446`，测试
`:410-414`）、成环（`models.py:428-434`，测试 `:415-419`，含自合并；`seen`
是逐路径祖先链，菱形共用不误报）。并集首见序有测试（`:425-427`），K5 写侧
与迁移共用 `merged_source_subtasks`（`curation.py:1186-1192` 与
`models.py:386-401`），读写一致由同一函数保证。`from_dict` 的 v1 分支不修改
入参（返回新 dict，`models.py:255-257`），`migrate_skill_record` 对 v2 行恒等
返回（测试钉 `is row`，`:400-402`）。

**老代码读 v2 对象**：`_known_kwargs` 丢未知键（`models.py:103-109`），
维护分支的旧代码读 v2 记录只丢 `source_subtasks`/`skill_id`，不 brick；
宽松读有前置测试钉住（`test_models.py:186-189` future_field 用例）。
"旧 run 在维护分支上不受影响"的论证成立——注意其方向是**旧 run + 旧代码**；
若把旧代码指向新 run，旧代码回写会静默把记录写回 v1 形状（其
`SKILL_SCHEMA_VERSION` 还是 1），这不是维护分支的使用场景，不构成问题。

### 1.3 golden 重生成的完整性与披露质量

8 个组合 + provenance 全部在同提交重生成（提交文件清单核对）。我**逐行复核
了全部 8 份 golden diff**：删除行只有 `"schema_version": 1,`，新增行只有
`"schema_version": 2,`、`"source_subtasks": [本段 subtask_id]`（skill 记录与
bank 行）、`"skill_id": <subtask>/skill`（exam 记录），别无任何漂移；30 skills
+ 30 exams + 30 bank rows 的计数与提交说明一致；provenance 仅 base_commit
350b40d→84d280e（调用数与崩溃点未动）。提交说明的 diff 分类**与实际一致**。
金矩阵 11 项（8 fresh + 2 crash-resume + 1 ignore-keys 元测试）是常备套件成员
——fixture 即当前代码输出，重生成的真实性由常备测试机器钉住，不依赖人工审计。

"结构化审计（回退新字段后逐键全等）"没有留可复跑脚本（git 无、tmp/ 无 M1 时
代产物；`tmp/golden_check3/` 是 10-13 P1b 时代的旧物，内容为更早的 v2 配置
形态，与 M1 无关）——见琐细 1。

### 1.4 schema 升版的周边同步

`SKILL/EXAM_SCHEMA_VERSION` 常量的消费方全在 `core/models.py`（from_dict /
migrate）与测试，src 内没有任何读对象 `schema_version` 做分支的漏改点（grep
全量核对；`stage0_freeze.py:294`、`assemble_reuse.py:1039`、`config.py` 的
版本闸全是 **config** schema，按契约不动，仍 v3）。README §2 同步完整：字段
语义、v1→v2 读规则、以及 live_run 技术债已偿还的注记（README diff
:363-376, :386-419）。构造点 AST 扫描：src 与 scripts 全净；tests 的 3 处命中里 2 处是故意的
负测试（`test_models.py:179,210`），1 处是 `Skill(**base)` 经字典传字段
（`test_models.py:66`）。store 无对象版本闸，无需改。

### 1.5 测试与验收证据的真实性（独立复验）

- 全套 `pytest -q`：**1945 passed, 3 deselected**（3 项 live 标记的插件测试），
  exit 0——与声明一致；基线数独立核对：`git archive 84d280e` 抽取树
  collect-only = **1927**，1927 + 19 新增 − 1 删除（`test_no_skill_reference_field`）
  = 1945，算术自洽。
- 金矩阵 11 项：随全套通过（collect 单独核对 11 项）。
- **两处 I1 修复的回退转红，我在 /tmp 副本（PYTHONPATH 指向副本 src）独立复验，
  双双转红**：(a) bank 比对回退为 `row != stored.to_dict()` 后，I1 测试复现
  `ConfigError: bank row 'bank/merge/0000' differs ... the source is corrupt`
  ——正是 I1 要修的误报；(b) 补账回退为 from_dict roundtrip 后，v1 前缀续跑
  测试断言失败（补出行变 v2）。注意 (a) 需同时保留加载侧的迁移路由才会走到
  误报——若连加载路由一起回退会更早炸在 `from_dict`，两处修复各自独立承重。
- ruff E9/F 通过（独立跑）。
- 提交说明的两个"披露"均属实：契约字面的 `Skill.from_dict(row)` 对 v1 合并
  bank 行会触发 from_dict 设计内报错，改经 `migrate_skill_record` 是必要且
  已披露的偏差；`curation.py:945` 处经核确实没有比较、只有补账写入。

### 1.6 对 M2 的影响面

M2 的前置本身干净：工作树在 32d6c86 干净（除评审文档），金矩阵基线即
32d6c86；P-C1/P-C2 的修复是消费方读路径改动，不写任何 run 产物，不会扰动
M2 的"golden 逐字节无 diff"门。按流程应先落 P-C1/P-C2 修复并重审 M1，再开
M2（M2 不依赖这两处，但不应在带伤的 v1 读路径上叠新层）。

---

## 2. 发现

### 2.1 偏移约束

#### P-C1 装配出来的 v1 复用 run 在阶段 4 当场崩溃——I1 的验收实质（全链路可用）未达成

**现象（已复现）**。用 M1 自己的 I1 夹具端到端跑：`build_source_run` →
`_downgrade_source_records_to_v1` → `assemble_reuse`（成功，与 M1 测试完全相同
的产物：6 skills 含 bank 内扁平合并 m0、bank_rows=2）→ 对装配出的 store 调
`stage3_candidates`（每个复用 run 阶段 4 的第一个枚举动作）：

```
ValueError: skill 'bank/merge/0000' is a schema-v1 merge product: its
source_subtasks is the union of its sources' lists and cannot be derived
from the record alone; migrate the run's records with
expedistill.core.models.migrate_skill_record(record, skills_by_id) instead of from_dict
```

**根因**。契约 §2 只给 v1 合并产物规定了两条出路：`from_dict` 响亮报错、装配
器走迁移纯函数。M1 忠实实现了这两条，但"先加载全部技能、再按 ID 过滤"的全量
枚举型消费方没有一个接迁移函数，全部变成响亮死胡同：

- `src/expedistill/composites/skill_status.py:44-45`——`stage3_candidates` 对
  `store.ids("skills")` 逐个 `store.load(..., Skill)` 后才做
  `is_stage3_candidate` 过滤；阶段 4 的 exam 驱动入口
  `src/expedistill/methods/acceptance/exam.py:123` 无条件先调它；
- `src/expedistill/composites/live_acceptance.py:413-417`——`pending_groups`
  同型（live 验收的宇宙枚举）；
- `src/expedistill/methods/curation/none.py:25-26`——curation=none 加载全部技能
  写 bank；
- `scripts/audit/audit_live_run.py:160-162`——审计工具的 `_candidate_groups`
  同型"先全量 load 再按 ID 过滤"：用当前代码审计任何含合并产物的 **v1 正式
  run**（AGENTS 六.7"跑完先审计再使用数字"的标准流程）当场同错；
- `src/expedistill/stages/pipeline.py` 固定按序跑 stage 0→6，`--until-stage N`
  只设上限不设起点（`cli.py:8`、`pipeline.py:229-230`），复用 run 想到 stage 6
  必须先活着穿过 stage 4 的枚举。

装配器对 bank 级 v1 源修好了（I1 字面验收过），但 byte-copy 装配把 v1 记录
**原样**搬进新 store（I1 测试自己断言 `read_json(path) == row`、
`schema_version == 1`，`tests/unit/stages/test_assemble_reuse.py:634-642`），
于是下一个读它的全量枚举就在合并产物上炸掉。**任何 bank 含合并产物的 v1 源
run**（今天全部正式 run 的常态——TB 库 102 members 含合并）装配出的 run 都
不可用：stages 1–5 零调用复用走不通，复用实验根本到不了 stage 6。M1 之前该
流程端到端可用（旧 `from_dict` 无此要求）；这是 M1 的 diff 引入的回归，且
M1 的验收测试在 `assemble_reuse` 返回处停止，抓不到它——与上一轮 I1 是同一
类"半程验收"问题。测试注释
`tests/unit/stages/test_assemble_reuse.py:634`（"every consumer loads them
through from_dict"）与事实相反：`from_dict` 对 m0 恰恰是抛错，说明下游断裂
未被意识到，不是有意划定的边界。

**修法**（读路径改动，零产物影响，不触 M2 金门）：
1. 把全量枚举型加载与装配器同走迁移：一次读入全部技能行成 rows map，逐条
   `Skill.from_dict(migrate_skill_record(row, rows))`（v2 行恒等穿过，行为零
   变化）。落点：`skill_status.stage3_candidates`、
   `live_acceptance.pending_groups`、`methods/curation/none.run`、以及 P-C2
   列出的 curation 各加载点。建议抽一个 store 级 helper（如
   `Store.load_skills()`）收口，避免五个调用点各写一遍。
2. 补端到端测试：v1 源（含 bank 内合并）→ 装配 → 阶段 4 枚举 + 阶段 5 早退
   分支全链路可用（顺带把嵌套合并形态纳入装配夹具，见琐细 2）。
3. 改正 `test_assemble_reuse.py:634` 的错误注释。

#### P-C2 v1 run 的 curation 续跑在库外有合并产物时崩溃；M1 的专项测试只覆盖无合并形态

**现象（已复现）**。构造 test_curation_resume.py 模块 docstring 自己的参考
场景终态（bank=[M2 合并套合并, s3]；M0 merged-away；M1 回退 discarded；日志含
final_member_ids），把全部技能记录与 bank 行降为 v1（与 M1 测试的
`_downgrade_run_to_v1` 同法），调 `curate_bank` 早退分支（bank 已写完的重跑/
续跑路径）：v2 形态正常返回 bank，**v1 形态在 `_check_bank_not_stale` 处
ValueError**（`bank/merge/0000` 是库外 v1 合并产物）。同根因的其余触发点：

- `src/expedistill/composites/curation.py:886`——`_check_bank_not_stale` 只加载
  **不在 bank 内**的技能，而回退合并（regression 失败后被
  `_apply_reverted` 落盘为 discarded，`curation.py:372-376`）、嵌套内层合并、
  pending 回滚恰都在库外；
- `curation.py:390`（`_check_merged_versions`）、`curation.py:427`（`_recover_pending_merges`）、
  `curation.py:332`（`_accepted_members`）——bank 未写完的 v1 run 崩溃续跑
  （`_load_log` 后第一步就是 `_check_merged_versions`）在这些点炸，即
  **curation 中途崩溃的 v1 run 用 M1 后的代码一律无法续跑**（一旦 G8 已落盘
  任一合并产物）。

M1 的 curation v1 专项测试（`tests/composites/test_curation_resume.py:473-519`）
真实做了崩溃注入且补账等价成立（赞同），但其 docstring 自述"no-merge scenario
on purpose"（:477-482），把"库外 v1 合并产物"推给 from_dict 的报错边界——
from_dict 报错是契约设计，但报错信息指向的 `migrate_skill_record` 没有任何
调用方/工具对整个 run 落地，维护分支时代的 v1 run（"今天全部正式 run"）从
"可续跑"变成"只能人工手写迁移脚本"。这与 I1 的动机（M1 后 v1 run 必须可用，
不能等 M4）同一口径。

公平记录一个减损情节（AGENTS 六.1 / R6）：正式 run 固定在启动时 checkout 的
维护分支 + 独立 venv 上续跑，"用 M1 后代码续跑 v1 正式 run"在流程上本就不该
发生。但该保护的机制是 per-run venv 的**过程性**隔离，不是闸门——本次对象
升版不动 CONFIG_SCHEMA_VERSION，stage 0 的配置闸不会拒载，误用主工作树续跑
v1 run 会一路走到阶段 4/5 才炸（R6 自己的措辞"schema 升版等破坏续跑的改动
在 stage 0 会被明确拒载"对**对象** schema 升版并不成立）；且 M1 自己新增了
v1 前缀续跑专项测试，说明实现方把"当前代码续跑 v1 run"当作支持场景。装配
run（P-C1）与审计读取则完全不在 R6 保护范围内。

**修法**：与 P-C1 同一补丁——curation 的四个加载点
（`_accepted_members`、`_check_merged_versions`、`_recover_pending_merges`、
`_check_bank_not_stale`）改走同一迁移 helper；并把 curation v1 续跑测试从
"无合并"扩到模块 docstring 的参考场景（回退合并 + 库内合并套合并）。
若确有不想接迁移的站点，必须改报错文案并补一个 run 级迁移工具
（scripts/，对既有 v1 run 原地升 v2 含 bank 行），让响亮报错有出路。

### 2.2 琐细

1. golden 结构化审计脚本未留档：AGENTS 五.1"用过两次以上、或结论依赖它的，
   移入 scripts/"——M1 的"回退新字段逐键全等"结论依赖该脚本，git/tmp 均无；
   本评审已独立复核结论成立（逐行分类 8 份 diff），但 M2/M3 的
   "golden 逐字节无 diff"门还会反复用同类审计，建议整理进 scripts/audit/。
2. I1 装配夹具无嵌套合并形态（迁移递归只有单测）；P-C1 修复合一并补。
3. `tests/unit/stages/test_assemble_reuse.py:634` 注释与事实相反（见 P-C1 修法 3）。
4. 装配器语境下 v1 记录的迁移失败抛裸 ValueError（如同处其它损坏源都是
   ConfigError，`assemble_reuse.py:1196-1200` 等），错误类不一致，退出码表现
   不同；可在装配器侧包一层 ConfigError。

### 2.3 赞同

1. I1 装配器修复比契约字面更好且披露诚实：字面表达式 `Skill.from_dict(row)`
   对 v1 合并 bank 行会触发 §2 自己设计的报错，实现改经 `migrate_skill_record`
   再 from_dict（`assemble_reuse.py:1207-1209`），偏差在提交说明与 log.md 双披露。
2. `curation.py:945` 的解读诚实：grep 证实该处无比较、只有补账写入，改为按
   存储记录原样落盘（`curation.py:942-953`），保证补出行与中断前缀跨代码版本
   同形态；崩溃注入测试真实且断言到位（零模型调用、v1 形态、与存储记录逐位相等）。
3. 旧读规则三态与推不出报错（未知形状/缺兄弟/成环）实现与测试完备（§1.2），
   K5 写侧与迁移共用 `merged_source_subtasks`，读写一致有钉。
4. golden 8 组合 + provenance 同提交重生成，diff 披露逐条属实（独立逐行复核），
   金矩阵 11 项常备在套——fixture 即当前代码输出，无需信任人工审计。
5. 测试证据全部真实：1945 全绿与基线 1927 均独立复验，回退转红双确认，ruff 过。
6. schema 升版周边同步干净：对象版本分支只在 models.py，config 版本闸按契约
   不动，README §2 含技术债偿还注记，构造点 AST 全净。
7. 老代码读 v2 记录按宽松策略丢未知键，"旧 run 在维护分支不受影响"论证成立。
8. M2 前置干净：32d6c86 即金基线；P-C1/P-C2 修复不触产物、不扰动 M2 验收门。

---

## 3. 最终结论

**M1 验收：不通过。**

- **偏移约束 2 条**（同一根因：v1 合并产物的 from_dict 报错只有契约点名的两
  处接了迁移函数，全量加载型消费方全部裸奔）：
  - **P-C1**：v1 源装配出的复用 run 在阶段 4 枚举（`stage3_candidates`，
    `skill_status.py:44-45` ← `exam.py:123`；live 侧 `pending_groups`，
    `live_acceptance.py:417`；`none.py:25`；审计工具
    `scripts/audit/audit_live_run.py:160` 同型）当场 ValueError——I1 要保护
    的"今天的全部 v1 正式 run 可复用/可审计"在链路下一步断裂，且被 M1
    验收测试的半程覆盖掩盖（装配成功即停）；
  - **P-C2**：v1 run 的 curation 续跑在库外有合并产物时崩溃
    （`curation.py:886/390/427/332`），M1 专项测试自述只测无合并形态；
- **修法**：消费方加载统一走迁移 helper（v2 恒等穿过、行为零变化、不触产物、
  不扰 M2 金门）+ 端到端 v1 复用流程测试 + curation 参考场景续跑测试；修复后
  重审 M1（只需复审该 diff 与新测试，其余验收项不需重跑）。
- 其余验收项（pytest 全绿、golden diff 披露、迁移单测、金矩阵、回退转红、
  schema 同步、M2 基线）经独立复验**全部成立且质量高**；除上述两条外无保留。

评审方法留痕：本评审的复现与验证脚本在 `/tmp/m1_review_c/`（v1 curation 续跑
复现、装配后 stage4 枚举复现、I1 夹具端到端复现、回退转红副本、AST 扫描），
均为只读目的，未改动仓库任何文件（除本文档）。
