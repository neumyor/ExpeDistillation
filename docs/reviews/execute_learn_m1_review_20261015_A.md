日期：2026-10-15
一句话说明：执行—学习闭环重构 M1 步骤的验收评审 A——正确性与行为等价（对提交 32d6c86，diff 84d280e..32d6c86，契约 docs/decisions/execute_learn_implementation_20261015.md §2 与 §9 的 M1 行）。
对应 log.md 条目：2026-10-15 执行—学习闭环实施方案（两轮三路评审后定稿）的 M1 步骤。

---

## 结论

**M1 验收：不通过**——1 条【偏移约束】（I1 修复不完整：装配出的 v1 形态 run 在
管线的 stage 4 候选枚举即崩溃），修复并重审后可过。其余全部核查项（迁移函数
正确性、写入方覆盖、golden 真伪、两处对方案字面偏差的判断、测试全绿、v1 源
夹具）均为【赞同】或【琐细】。

核查方式声明：golden 是我自写的深度逐叶比对脚本核的（8 组合全量，非抽查后轻
信提交说明）；崩溃洞是用仓库自己的 I1 夹具机械（`build_source_run` +
`_downgrade_source_records_to_v1`）端到端复现的；测试是我自己跑的
（`tests/golden tests/composites` 516 全绿，全量 1945 全绿，ruff 与
test_layering 通过）。复现脚本在 /tmp/m1_review_A/（未入仓库）。

---

## 一、逐项核查

### 1. 迁移函数正确性 —— 【赞同】

`src/expedistill/core/models.py`：

- **嵌套合并递归正确**。`_migrate_skill_record`（models.py:422-463）沿
  `merged_from` 逐来源递归（models.py:444-445），每个来源取迁移后的
  `source_subtasks`（models.py:446-451）再做并集（models.py:452-454）；
  来源本身已是 v2 时原样透传（models.py:426-427），v2 行缺
  `source_subtasks` 响亮报错（models.py:447-450）。单测覆盖扁平
  （tests/unit/core/test_models.py:372）、嵌套（:384）、v2 透传。
- **成环 / 兄弟缺失 / 未知形状均响亮报错**。环检测用 `seen` 路径元组
  （models.py:429-432）——是路径不是全局访问集，我额外验证了单测没覆盖的
  **菱形**情形（两个来源共享同一子来源）不会误报成环、自环会报、v1 合并挂
  v2 来源可用（/tmp 脚本实测通过）。兄弟缺失 models.py:438-443，未知 v1
  形状 models.py:457-461 与 from_dict 路径 models.py:364-368，单测
  test_models.py:404/410/415 钉住。
- **并集 helper 首见序确定**。`merged_source_subtasks`（models.py:386-400）
  用 dict `setdefault` 保首见序，单测 test_models.py:425 钉住顺序语义。
- **"K5 写入侧与迁移侧共用同一函数"属实**。写入侧
  composites/curation.py:1192 `merged_source_subtasks(source.source_subtasks
  for source in sources)`，迁移侧 models.py:453 同一函数；两侧的来源顺序也
  一致——写入侧 `merged_from=source_ids` 与取值遍历用的是同一个 `sources`
  列表（curation.py:1130、1186-1193），迁移侧按 `merged_from` 顺序取并集，
  因此 v1 记录迁移重建的值与 K5 若在 v2 下写入的值**逐位相同**。
- **from_dict 的 v1 段生判定与 ids.py 无误判窗口**。镜像后缀
  `_SKILL_ID_SUFFIX="/skill"`（models.py:329）对应 ids.make_skill_id
  （core/ids.py:88-89）；合并产物 ID `bank/merge/NNNN`（ids.py:102-108）
  不以 `/skill` 结尾，任何 `subtask_id` 都无法令
  `subtask_id + "/skill" == "bank/merge/NNNN"`，**不存在把 merge 前缀误判
  成段生的窗口**；且 from_dict 的 v1 分支先查 `merged_from`（models.py:355）
  再做段生推导，双保险。镜像常量不用 import 是因为 ids imports models（会
  成环），由构建于 `ids.make_*` 之上的单测钉住等价（注释 models.py:326-328）。
  Exam 同理：`_EXAM_ID_SUFFIX="/exam"` 对应 ids.make_exam_id（ids.py:92-93）。

### 2. 写入方覆盖完备性 —— 【赞同】

- src + scripts 全部构造点（新字段均无默认值，漏补=构造时 TypeError）：
  - `Skill(`：blocks/author.py:367（write_skill，author.py:371
    `source_subtasks=[subtask.subtask_id]`）、composites/curation.py:1181
    （K5 合并产物，curation.py:1190-1192 并集）、
    scripts/smoke/smoke_leak_audit_k4.py:129、
    scripts/audit/audit_live_run.py:726。
  - `Exam(`：blocks/author.py:417（gen_exam，author.py:422
    `skill_id=make_skill_id(subtask.subtask_id)`）、
    scripts/smoke/smoke_leak_audit_k4.py:124。
- 无 `Skill(**...)` / `dataclasses.replace` 之类的旁路构造（grep 核过）。
  tests 侧全部夹具改动均为机械补字段（逐 diff 过目），1945 全绿兜底。
- **取值语义与 §2 一致**：段生 = [自己的 subtask_id]（author.py:371，单测
  tests/unit/blocks/test_author.py:189 断言）；合并 = 各来源并集
  （curation.py:1190-1192）；exam.skill_id = make_skill_id(subtask_id)
  （author.py:422，test_author.py:610 断言）。

### 3. golden diff 真伪 —— 【赞同】（自证，非采信提交说明）

自写脚本对 **8 个组合全量**做两版（84d280e vs 32d6c86）整树逐叶比对：

- 全部叶子差异恰为三类：`stage3_generation/skills/*.json` 加
  `source_subtasks` + schema 1→2（8 组合共 30 处）、`skill_bank.jsonl` 行同
  （30 行）、`stage3_generation/exams/*.json` 加 `skill_id` + schema 1→2
  （30 处）。**零处其他漂移**：各 stage summary、curation_log、leak/
  rewrite 账本、llm dumps 与 usage、pipeline_log、manifest、stage6 目录全部
  逐叶相同。
- 抽 2 个组合（exam-retrieval-full、live_run-whole_bank-split）核**取值**：
  每条 skill 的 `source_subtasks == [自己的 subtask_id]` 且 schema=2；每道
  exam 的 `skill_id == subtask_id+"/skill"` 且指向已存技能；每行 bank 行与
  同 ID 的 skill 记录**整 dict 相等**；curation_log 不泄漏新字段。
- `provenance.json` 仅 `base_commit` 350b40d→84d280e 一行（提交说明声称的
  "调用数与崩溃点不变"由此成立）。
- 测试数 1927→1945 的算术自洽：diff 净增 19 个测试函数、删 1 个
  （`test_no_skill_reference_field`，其语义被新必填字段取代），1945 全量通过
  （3 个 live 标记按惯例 deselect）。

### 4. 两处与方案字面的偏差 —— 【赞同，两处判断均正确】

**(a) 装配器比对走 `migrate_skill_record` + `from_dict` 而非方案原字
`Skill.from_dict(row)`**（assemble_reuse.py:1206-1209）：

- 方案原字在本步自己的设计下必炸：`from_dict` 对 v1 **合并** bank 行按
  models.py:355-361 响亮报错，而 I1 针对的"今天的全部 v1 源 run"恰恰在 bank
  里带合并产物——方案的字面表达式达成不了它自己声明的意图。
- 实现达成"两侧同构"且无语义损失：`stored` 侧加载已走同一迁移
  （assemble_reuse.py:1150-1156），bank 行侧经同一迁移后 `to_dict` 比较
  （:1206-1210）；v2 行 `migrate_skill_record` 原样透传（models.py:426-427），
  对 v2 源与方案字面**逐位等价**。加载兼作 bank 行 can-load 检查（顺带满足
  评审 C I1 修法 1 的附加要求）。`skill_lineage_tasks` 拿同一对象（:1213-1214），
  与技能拷贝集共用同一判定，硬断言（:1216-1222）保持。

**(b) curation.py:945 实为补账写入而非比较**：

- 核实评审 C 的 I1 原诊断（docs/reviews/execute_learn_impl_review_20261015_C.md
  §1.4 第二点）：该处是崩溃续跑的 bank 补账 `store.append_bank(store.load(...,
  Skill).to_dict())`——对 v1 记录会补写出 v2 行混入 v1 前缀，且 v1 合并成员
  根本过不了 from_dict。诊断与实际代码对应准确（方案 v2 §2 里"补账比对"的
  "比对"二字是方案措辞失准，实现者 grep 后按写入处理是对的，log.md 已披露）。
- 修复"按存储记录原样落盘"（curation.py:952-953 `read_json(store.path_for(...))`）
  判定正确：
  - **v2 run**：前缀行由 `store.append_bank(skill.to_dict())` 写入
    （curation.py:1284），而存储文件就是同一对象经 `store.save` 的
    `to_dict()`（store.py:376-379），`read_json` 取回的 dict 与
    `from_dict→to_dict` 往返**逐位相同**（to_dict 严格全字段、键序为字段声明
    序，两路径一致）——崩溃续跑等价无变化，既有测试
    `test_resume_before_bank_append_completes_without_model_calls`
    （对照一次跑完的 snapshot）全绿佐证。
  - **v1 run**：补出的行保持 v1 形态与前缀自洽，不再混版、不再在 v1 合并
    成员上炸；新测试 test_curation_resume.py:473
    `test_resume_completes_a_v1_bank_prefix_in_the_v1_form` 钉住（断言补行
    schema=1、无 source_subtasks、等于存储记录原样、零模型调用）。
  - 注意方案原字"同 helper 修"反而会复现评审 C 警告的混版 bank——实现者
    的偏差方向是对的。
  - 该测试 docstring 明说"no-merge scenario on purpose"，把 v1 合并的
    from_dict 边界单独钉在 test_models.py——这个自我限定是诚实的，但它
    划出去的范围正是下面【偏移约束】的洞。

### 5. 行为等价 —— 除一条【偏移约束】外【赞同】

- **新（v2）run 无任何行为变化**：golden 8 组合全树逐叶比对，模型调用
  （llm dumps 逐字节同）、判定与账本（leak/rewrite/verdicts、
  curation_log）、summary、pipeline_log 全部不变；`tests/golden
  tests/composites -x -q` 516 全绿（含两套崩溃续跑套件），全量 1945 全绿，
  ruff E9/F 与 test_layering 通过。
- **v1 读路径的行为变化全部是方案明文设计**（迁移或响亮报错），但有一处
  设计内的变化落在了一个方案没点名、却在 I1 意图之内的消费方上——见
  【偏移约束】。
- 提交说明"两条 I1 修复去掉后测试转红"无法直接复验（需改码），但按代码
  推理成立：去掉装配器迁移，v1 原始行 vs v2 `to_dict` 恒不等→每行
  "the source is corrupt"；去掉补账原样落盘，v1 记录经 from_dict 产 v2 行
  （混版）/v1 合并成员直接 ValueError。

### 6. v1 源夹具测试（I1 验收）—— 【赞同，但见偏移约束】

- 夹具是真 v1 对象：`_downgrade_source_records_to_v1`
  （tests/unit/stages/test_assemble_reuse.py:585-603）对源 run 的**全部**
  skill 记录（含合并产物 m0 的存储文件）、exam 记录、bank 行删
  `source_subtasks`/`skill_id` 并置 `schema_version: 1`。
- 覆盖扁平合并：源里有 m0（`_merged_skill`，merged_from 两个段生来源），
  装配时其 bank 行与存储记录都需经 `migrate_skill_record`（from_dict 会拒）
  ——正是评审 C I1 修法 3 点名的"扁平 merge 推导"覆盖。嵌套合并在迁移
  纯函数单测层覆盖（test_models.py:384），装配器夹具未含嵌套（【琐细 1】）。
- 装配器对 v1 源的**装配链路本身**M1 后全部可用，且 keep 集与 v2 happy
  path 完全一致（counts 逐项断言，test_assemble_reuse.py:620-632）：源
  skill 加载走迁移（assemble_reuse.py:1150-1156）、bank 比对两侧同构
  （:1206-1210）、exam 经 from_dict 的 v1 推导加载、kept_exams/kept_attempts
  过滤不变（:1181-1189）、拷贝为字节复制（`_copy_kind`，:447-467
  `shutil.copy2`）故装配产物内部自洽（bank 行与存储文件逐字节一致的不变式
  在 v1 形态下依然成立）。
- **但链路的产物（装配出的 run）不能被管线继续消费**——装配器的模块契约
  明说它是给管线续跑用的（assemble_reuse.py:74-76 "stages 1-5 short-circuit
  on the stored units, and stage 6 runs fresh"），而 stage 4 起就崩。见
  【偏移约束】。

---

## 二、发现分级

### 【偏移约束】P-A1：I1 修复不完整——装配出的 v1 形态 run 在管线 stage 4
候选枚举即崩溃，"今天的全部 v1 源 run"装出来还是不可用

**事实链（全部有代码与复现证据）：**

1. I1 的动机是"M1 后对任何 v1 对象源 run……今天全部正式 run 都是 v1"
   （方案 §2），M1 提交说明也自述"the reuse assembler keeps working on the
   all-v1 source runs of today"。装配器的产物按其模块契约（assemble_reuse.py:74-76）
   是**要被管线继续跑的 run**：stage 0 冻结自己的 manifest，stages 1-5 靠
   已存对象短路，stage 6 fresh。
2. 装配是字节复制（`_copy_kind` assemble_reuse.py:460 `shutil.copy2`），
   v1 源装出的 run 保持 v1 形态——I1 测试自己也把这钉为预期
   （test_assemble_reuse.py:638-644 断言装配 bank 行 `schema_version == 1`）。
3. M1 的设计内行为：`Skill.from_dict` 对 v1 合并产物响亮报错
   （models.py:355-361）——方案 §2 明文如此，本是对的。
4. 但 stage 4 的**两个**验收驱动都以 `stage3_candidates(store)` 起步
   （methods/acceptance/exam.py:123、methods/acceptance/live_run.py:203/683），
   而 `stage3_candidates` 对 `store.ids("skills")` 里**每一个**存储技能做
   `store.load(..., Skill)`（composites/skill_status.py:44-46）——包括 v1
   合并行。于是**只要源 run 的 train 侧存过任何合并产物（bank 内成员或被
   K5 回退的 discarded 合并，curation.py:372-377 证明回退件留盘），装配出的
   run 在 stage 4 第一步就抛 ValueError**。stage 5 的短路路径同样会炸
   （`_check_bank_not_stale` 对所有非 bank 存储技能 `store.load`
   （curation.py:883-886）——回退的合并产物在此命中）。
5. **端到端复现**（/tmp/m1_review_A/demo_v1_source_e2e.py，用仓库自己的 I1
   夹具机械 + 一个被回退的 train 侧合并）：v1 源装配成功（7 技能 2 bank 行），
   随后 `stage3_candidates(Store(装配目录))` 与 `curate_bank(...)`（bank
   非空短路）双双抛
   `ValueError: skill 'bank/merge/0000' is a schema-v1 merge product: ... use migrate_skill_record`
   ——而 `bank/merge/0000` 正是 I1 夹具自己装配进 bank 的 m0。也就是说：
   **I1 验收测试装出的那个 run，本身就是这个洞的活体样本**，只是测试在
   `assemble_reuse` 返回处就停了，没有走出装配链路的最后一步。
6. 行为等价判定：M1 之前，同一操作流（v1 源装配→管线续跑）端到端可用
   （v1 是当时的现行 schema）；M1 之后同一流在 stage 4 崩溃。这是 M1
   引入的行为变化，且报错文案（"migrate the run's records ...
   instead of from_dict"、"a human must look at this run"）把一个
   设计产物说成疑似损坏的 run，会误导真实故障排查。
7. 夹具测试的注释陈述与事实相反：test_assemble_reuse.py:633-634
   "every consumer loads them through from_dict"——对 v1 合并行，from_dict
   恰恰按设计**拒绝**加载；消费方"一律 from_dict"的断言对 I1 针对的那类
   记录不成立。

**为什么必须修（而不能推给 M2–M4）：** 方案后续步骤没有一个会碰它——M2 是
执行侧抽取，M3 学习侧，M4.5 的"装配器 v3 源"是配置/布局支持，不是 v1 记录
读取。而 tb21 复用实验（retrieval_delivery_tb_split_20261013，装配器的存在
理由）随时会对今天的 v1 源 run 发起装配；源 run 经过 stage 5 整理、bank 由
合并产物构成是其常态。不修则 I1 的承诺（"v1 源自 M1 起可用"）只兑现了装配
动作本身，没兑现它服务的实验流。

**修法（二选一，推荐甲）：**

- **甲（推荐，装配器本地、一处改动）**：装配器对 skills/exams 两类改"迁移
  后落盘"——拷贝时写 `Skill.from_dict(migrate_skill_record(row, rows)).to_dict()`
  （装配器内存里本就有迁移后的对象与 `skill_rows`，:1150-1156），bank 行用
  同一迁移行；"kept bank row 与拷贝技能逐字节一致"的不变式改为"与**迁移
  孪生**逐字节一致"（两侧同源，断言依旧成立）；trajectories/subtasks/
  attempts schema 未变，仍字节拷贝。装配产物成为 v2 原生 run，**所有消费方
  零改动**，from_dict 的设计内拒绝不再被踩到。I1 测试相应把"装配行保持
  v1"的断言改为 v2，并**加一步续跑断言**（对装配目录跑 `stage3_candidates`
  + `curate_bank` 短路——当前必红，即回归钉）。副作用：ASSEMBLED_FROM.md
  的 checksum 按新字节计算（本就是现算，无碍）；"byte-exact copy"的审计
  语义对 skills/exams 弱化为"经确定性迁移的拷贝"，在装配器 docstring 注明。
- **乙（面大，不推荐）**：保持 v1 形态拷贝，把装配 run 会走到的每个
  `store.load("skills", ...)` 点改为经 `migrate_skill_record` 读取
  （skill_status.py:28/45/61，curation.py:332/355/390/427/439/684/886，
  methods/curation/none.py:25，methods/acceptance/live_run.py:280 等
  ~20 处），并在 README §2 写明"装配产物保持源版本形态"契约。每处都要
  兄弟表、每个未来消费方都要记得，是永久税。

  无论选哪个：修正 test_assemble_reuse.py:633-634 的错误注释；修复落在
  32d6c86 之上的跟进提交，重审本条。

### 【琐细】

1. 装配器 v1 源夹具只含扁平合并（m0），无嵌套合并源；嵌套在迁移纯函数
   单测层已覆盖（test_models.py:384），I1 验收按评审 C 修法 3 也只要求
   扁平。随 P-A1 的回归测试顺手补一个嵌套源更好。
2. 装配器对"v1 合并产物引用了源 run 未存储的来源"（真损坏源）现在抛
   models 层 ValueError，而装配器对源损坏的惯例是 ConfigError（如
   `skill_lineage_tasks` 的 "does not store"）。两者都响亮、文案都可定位，
   仅异常类型不统一。
3. 方案 §2 "curation.py:945 的补账**比对**同 helper 修"措辞失准（该处是
   写入，且"同 helper"会复现混版问题）；实现者的正确解读已在提交说明与
   log.md 披露，方案文档不必回改，留此备查。
4. I1 测试断言装配 bank 行保持 v1 形态（test_assemble_reuse.py:638-644），
   该"设计决定"正是 P-A1 的根；P-A1 修复（甲案）时此断言一并改。

### 【赞同】

1. 嵌套合并递归、成环（路径式检测、菱形不误报——我独立验证）、兄弟缺失、
   未知形状全部正确且响亮（models.py:422-463，test_models.py:332-428）。
2. 并集 helper 首见序确定，K5 写入侧与迁移侧共用同一函数、同一来源顺序，
   v1 迁移重建值与 K5 写入值逐位相同（curation.py:1190-1193、models.py:453）。
3. from_dict 的 v1 段生判定镜像 ids.py 无 bank/merge 误判窗口，镜像常量被
   基于 ids.make_* 的单测钉住（models.py:326-342）。
4. 写入方覆盖完备（src+scripts 全部 6 个构造点逐一补齐，必填无默认使漏补
   即炸），取值语义与 §2 一致（author.py:371/422、curation.py:1190-1192）。
5. golden 重生成为真：8 组合全量逐叶比对仅三类预期差异、取值正确、
   provenance 仅 base_commit、别无任何漂移（我自证，非采信提交说明）。
6. 偏差 (a)（比对经迁移函数）必要且无语义损失，对 v2 源与方案字面等价。
7. 偏差 (b)（补账按存储原样落盘）判断正确：v2 run 逐位无差、崩溃续跑等价
   保持（既有 snapshot 对照测试全绿），v1 run 前缀自洽，并有专项测试钉住
   （test_curation_resume.py:473）。
8. 测试 1927→1945 全绿（我实跑：golden+composites 516、全量 1945、ruff、
   test_layering），净增 19 删 1 与数字自洽。
9. v1 源夹具为真 v1 对象（含合并产物与 bank 行降级），装配链路（加载、
   比对、kept_exams/attempts）对 v1 源完全可用且 keep 集与 v2 路径一致。
10. README §2 同步完整（字段语义、v1→v2 读规则、技术债偿还注记），docstring
    与 AGENTS 七.3 的合规性说明到位；curation resume 测试 docstring 诚实
    声明了自身的 no-merge 限定。

---

## 三、复现与核查记录（评审 A 自持证据）

- /tmp/m1_review_A/demo_v1_source_e2e.py：I1 夹具源 + 回退合并 → 降级 v1 →
  装配成功 → `stage3_candidates` 与 `curate_bank` 短路均抛 ValueError
  （崩溃点 skill_status.py:45 / curation.py:886，均经 store.py:387 →
  models.py:256 → models.py:356）。
- /tmp/m1_review_A/golden_deepdiff.py：8 组合 84d280e vs 32d6c86 全树逐叶
  diff，UNEXPECTED=0；预期类计数 12–36 叶/组合。
- /tmp/m1_review_A/golden_values.py：2 组合新字段取值断言全过。
- /tmp/m1_review_A/diamond_check.py：菱形不误报、自环报错、v1 合并挂 v2
  来源可用。
- 测试实跑：`pytest tests/golden tests/composites -x -q` → 516 passed；
  `pytest tests/` → 1945 passed, 3 deselected；`ruff` 全过；
  `pytest tests/test_layering.py` → 3 passed。
