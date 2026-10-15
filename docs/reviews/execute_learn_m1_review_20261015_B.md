日期：2026-10-15
一句话说明：执行—学习重构 M1 步骤的验收评审 B——简洁性与反过度设计。
对应 log.md 条目：2026-10-15 执行—学习闭环实施方案（两轮三路评审后定稿）的 M1 步骤。

---

## 被审对象与证据基础

- 被审提交：32d6c86（diff 84d280e..32d6c86），工作区 HEAD 即该提交、树干净；契约为
  `docs/decisions/execute_learn_implementation_20261015.md` §1（纪律）、§2（数据对象变更）、§9 M1 行。
- 评审人独立复核（全部只读）：pytest 全量 **1945 passed, 3 deselected**；ruff E9/F 通过；
  `tests/test_layering.py` 3 项通过；对 `src/` 做 AST 扫描确认 Skill/Exam 全部构造点都带新字段；
  用 `git archive` 抽取 84d280e 旧树收集测试计数（**1927**，与提交说明一致）；对 8 个 golden
  组合做结构化复核（skill 行剥 `source_subtasks`、exam 行剥 `skill_id`、schema_version 2→1
  回退后与 84d280e 版**逐字节相等**，provenance 仅 base_commit 一处变化）；逐一查了 84d280e 与
  32d6c86 两版 `curation.py` 的全部 `to_dict` 使用点。
- 视角：简洁性与反过度设计（新 hash？影响执行流程的硬检查？多余机制？）。用户核心约束：
  保留异常分类与恢复机制。

## 逐项判断

### 1. M1 有没有做多余的事 —— 结论：没有，逐个候选机制清点后全部对应契约条款

diff 共 38 个文件。超出"两字段+迁移+I1 修复+golden 重生成"的候选机制只有四件，逐一处置：

- **后缀镜像常量 `_SKILL_ID_SUFFIX` / `_EXAM_ID_SUFFIX`**（models.py:326-331）：不是新机制，
  是循环依赖的最小解——`core/ids.py:42` import 了 `core/models.py`（MODEL_ROLES），models 不能
  反向 import ids；镜像常量加单测钉住等价（tests/unit/core/test_models.py 的迁移单测全部用
  `ids.make_skill_id` 生成的真 ID），漂移会被测试抓住。没有更简的合法形状。
- **`merged_source_subtasks` 共享并集函数**（models.py:386-401）：见第 3 项，共用是分层规则下
  的最小形状，不是抽象癖。
- **迁移函数里的防御性报错**（v1 未知 ID 形状、缺兄弟记录、merged_from 成环、v2 来源缺字段，
  models.py:345-463 共 5 个 ValueError 分支）：全部是 AGENTS 七.3"要么正确迁移、要么明确报错，
  不能静默读成默认值"的落实，且方案 §9 M1 行明文要求"迁移函数单测（含推导不出报错）"——
  报错分支与单测是契约点名的交付物。关键：这些检查**全部挂在 v1 读取路径**（`from_dict` 经
  `schema_version == 1` 闸进入，models.py:255/284；v2 记录直接跳过迁移），对现行执行流程零影响，
  不属于用户约束禁止的"影响执行流程的硬检查"。其中成环守卫（models.py:429）我专门审过：没有
  它，损坏 run 会在装配器里以千帧 RecursionError 崩掉；4 行换成点名"twice in one merged_from
  chain"的损坏报告，与仓库"corrupt run 要人说人话"的既有风格一致，是比例恰当的防御。
- **curation v1 前缀续跑测试**（tests/composites/test_curation_resume.py:473，含 `_downgrade_run_to_v1`
  helper 约 15 行）：不在 §9 M1 行的字面清单里，但它钉的是偏差(b)引入的新语义，AGENTS 八.2
  本就要求组合积木测试覆盖中断续跑；不加这个测试，补账行为的变化就无人看守。属应尽的测试义务。

其余改动全部精确落在 §2/§9 清单内：两字段（models.py:235/279）、两版本号（models.py:38-39）、
from_dict 两个 v1 分支（models.py:255/284）、迁移纯函数、全部写入方补字段（src 内经 AST 扫描
仅 3 处构造点：author.py:367 写作、author.py:417 出题、curation.py:1181 合并产物）、
I1 两处修复、golden 8 组合+provenance 重生成、README §2、log.md 条目。没有新配置、没有新 hash、
没有新注册表、没有新账本、没有新增影响执行流程的检查。

### 2. 必填无默认值 —— 结论：必填是唯一正确形状，构造样板量与收益相称

- 带默认值的方案其实不存在：`source_subtasks` 的正确值对段生是 `[subtask_id]`、对合并是并集，
  两者都不是任何 dataclass 默认能表达的值；`field(default_factory=list)` 会让漏写的构造点静默
  存下语义错误的 `[]`——正是 AGENTS 七.3 明令禁止、也是本步要消灭的"读取方各自推导"问题的镜像。
  Exam.skill_id 同理（默认 None/空串都是静默错值）。所以"必填 vs 带默认"不是简洁性取舍，带默认
  的方案不更简，只是更错。
- 样板量实测：diff 中因补字段而改的构造点约 60 处，分布在 20 个测试文件与 2 个脚本夹具
  （audit_live_run.py:729、smoke_leak_audit_k4.py:126），绝大多数文件经共享 helper
  （`_skill` / `_exam` / `make_pair` / `seed_group` 等）只改 2–8 行，纯补字段的测试改动约 150 行。
  对一次动到夹具中最高频构造的两个对象的 schema 升版，这个量是机械且一次性的。
- 必填设计还把"有没有漏掉写入方"变成机器可查的问题：我复现的 AST 扫描（0 漏点）之所以有意义，
  正因为漏点会以 TypeError 响亮失败；若带默认值，漏点会静默混进 golden。这是用 150 行样板
  买下的完备性证明，划算。

### 3. 迁移设施的最小性 —— 结论：三件套是方案自己规定的形状，两件能力 + 一个 5 行共享函数

- 清点：from_dict 两个 v1 分支（记录内可推导的段生/考题）+ `migrate_skill_record`（带兄弟记录
  的递归迁移）+ `merged_source_subtasks`（并集）。这对应 §2 明文写好的分工："段生从 ID 后缀推导；
  嵌套合并 from_dict 做不了——响亮报错；装配器走带 skills_by_id 的迁移纯函数（core 层）"。
  所以"三件还是两件"的答案是：能力上两件（记录内推导、跨记录迁移），第三个是两处共用的 5 行
  函数——是共享不是第三件能力。
- `merged_source_subtasks` 被 K5 写入侧（curation.py:1192）与迁移侧（models.py:443）共用是否合理：
  合理且必须。分层规则禁止 core import composites，迁移住在 core，所以并集逻辑必须在 core 才能被
  迁移使用；K5（composites 层）可以往下 import。若在 K5 内联，core 的迁移就得再抄一份并集逻辑，
  而"v1 run 迁移出的值 == v2 代码新写的值"这一跨版本等价保证（E1 类比对的语义基础）就会押在
  两份代码不漂移上。共用一个定义是最小且唯一安全的形状。docstring 把这一点写明了
  （models.py:392-395）。
- from_dict 对 v1 合并产物的报错是否清晰无歧义：清晰。原文（models.py:356-360）给出记录 ID、
  为什么推不出（并集需要兄弟记录）、以及完整的替代调用
  `expedistill.core.models.migrate_skill_record(record, skills_by_id)`——模块、函数、参数俱全，
  并被单测 `match="migrate_skill_record"` 钉住（tests/unit/core/test_models.py:332）。
- 更简的等价形状排查过三种，均不更简：(i) from_dict 内部委托 `migrate_skill_record(data, {})`——
  合并行会报"not among the sibling records"，对 from_dict 调用方是误导信息，丢失契约要求的
  "指向纯函数"；(ii) 砍掉 from_dict 分支、所有调用方显式迁移——要动全代码库每一处
  `store.load`，diff 大一个数量级，且 v1 段生行本就零接触；(iii) 装配器比对改原始字典对原始字典
  （不经对象往返）——见第 4 项，偏离方案规定的"同构"语义更多。

### 4. 两个偏差处置是否最小 —— 结论：都是各自前提下能找到的最小修法，无特例化

- **偏差(a) 装配器比对走迁移函数**（assemble_reuse.py:1206-1208）：方案 §2 I1 的字面表达式
  `Skill.from_dict(row)` 对 v1 **合并** bank 行会触发 from_dict 的设计内报错——而 bank 行恰恰
  就是合并产物（I1 验收夹具自己的 bank 就含合并行 m0）。字面式不可执行，偏离不可避免。实现
  选择的修法是在字面式外面加一层 `migrate_skill_record(row, skill_rows)`，复用已加载的
  `skill_rows`（assemble_reuse.py:1153），仅此一处包裹，其余与方案逐字一致。备选的"原始字典
  对原始字典"少一次对象往返，但改动了方案规定的比对语义（对象往返会丢弃未知键，两侧对称；
  原始比对则会对未知键报警），是更大的偏离。选了偏离最小的一种，并在提交说明里如实披露了
  原因。没有为个别路径引入的特例。
- **偏差(b) curation 补账按存储原样落盘**（curation.py:945-953）：方案的"补账比对同 helper 修"
  前提有误——我核对了两版 curation.py，`to_dict` 全部使用点只有两处且都是 append（84d280e 的
  946/1273 行），**没有任何比较处**。实现者经 grep 发现、在提交说明与 log 里披露了这一解读，
  然后修了真问题：补账的承诺是"补出中断的 append 循环本来会写的行"。三种做法里——
  from_dict 往返（v1 记录被迁成 v2，写进 v1 bank 成混版，且 v1 合并成员直接过不了 from_dict）、
  带兄弟迁移后落盘（同样混版）、按存储原样 `read_json`——只有第三种能跨 schema 版本匹配前缀，
  而且它还比旧代码少一次对象构造（一次 `read_json` 替代 `load().to_dict()` 两步）。这是把
  "迁就个别路径"降到零的统一修法：v1/v2 run 走同一条代码路径。配套测试
  （tests/composites/test_curation_resume.py:473）精确钉住该语义：v1 run 中断后补出的行必须是
  v1 形态且等于存储记录。

### 5. 无消费者落地检查（P5 教训）—— 结论：只写不读恰如其分，未提前建任何读取设施

- `source_subtasks` 在 src 内的全部出现（grep 复核）：写入方 author.py:371、写入方 curation.py:1192
  （合并时的并集计算，即 §2 的"写作时算好存入"本身）、迁移函数。**零个读取方**。
- `Exam.skill_id` 在 src 内：写入方 author.py:419 一处，**零个读取方**。装配器的 kept_exams
  过滤仍按 `exam_id`（assemble_reuse.py:1186），切到 `exam.skill_id` 按方案 §7 ④ 留给 M4.5。
- `skill_lineage_tasks` 仍沿 `merged_from` 递归（assemble_reuse.py:208-237 未动）——读取方切换
  是 M4 的事，M1 没有抢跑。from_dict 的 v1 迁移不是"读取设施"，是 §2 明文规定的旧读规则。
- 所以 M1 的"只写不读"不是 P5 批评的那种"无消费者落地"（那是指建了没人用的机制）；这里是
  方案明文的中间态：字段先行、消费方 M3/M4 到位。没有为此多建一行设施。

### 6. 异常与恢复机制保全 —— 结论：完好，未被触碰

- reliability.py、store.py、manifest.py、runlock.py 均不在 diff 中；四类异常的处置路径零改动。
- 补账修复的语义次序核对（curation.py:930-957）：仍位于 `_read_final_member_ids` 校验之后、
  `_check_bank_not_stale` 之前；迭代次序不变（final_ids 顺序）；不写任何终态、不新增
  try/except、不新增失败分类。"终态先写、行是回执"不受影响——补账本来就是给已终态的存储记录
  补回执，修的只是回执行的**形态**（存储原样），不是次序或账本语义。
- K5 提交步次序（store.save = commit step 1 → 回归 → bank append，curation.py:1196-1210）不变，
  新字段只是多了一项载荷。
- 新增的 5 个 ValueError 全部在 v1 读取路径上、全部针对损坏数据、全部"响亮报错不静默"，不处于
  任何重试/分类管线中。装配器不是续跑路径，其改动是纯读取侧。
- 实现者自己披露过一处边界（curation 测试 docstring，tests/composites/test_curation_resume.py:
  480-483）：仓里存有 v1 合并产物（bank 之外）时 from_dict 响亮报错——这是方案 §2 的规定行为，
  不是 M1 新引入的语义。

### 7. 文档与提交质量 —— 结论：准确、如实、合式

- README §2（README.md:363-412）：字段语义（段生=[自己的 subtask_id]、合并=并集、写入时算好、
  读取方不推导；Exam.skill_id=make_skill_id(subtask_id)、出题仍不看 skill）与 v1→v2 读规则
  （段生 from_dict 后缀推导、合并响亮报错指向 migrate_skill_record、exam 换后缀推导、v2 缺字段
  TypeError）与代码逐条对得上；旧设计（"Exam 不携带 skill 引用"）的撤销已改写"关系"段并保留
  语义要点（出题仍然盲写）；CONFIG_SCHEMA_VERSION 不动的技术债注记准确；grep 确认无残留矛盾
  表述。AGENTS 七.2 的"README 第二节同步"义务在 M1 当步即完成（不必等 M4.6）。
- 提交说明：两处偏差均如实披露且给了原因；golden diff 按类披露（skill 记录+bank 行加
  source_subtasks、exam 记录加 skill_id、schema 1→2、provenance 仅 base_commit）——此分类我
  已独立结构化复核为真（见文首证据基础）；"1927→1945 全绿、ruff、test_layering"全部复核为真；
  "两条 I1 修复去掉后测试转红"我未重放（评审人只读），但两条测试的断言本身使该声明逻辑闭合：
  旧比对式对 v1 行必报 corrupt、from_dict 往返补账必产出 v2 行且对 v1 合并成员必抛错，去掉修复
  断言不可能通过。
- log.md 条目（log.md:629-634）：符合 AGENTS 第二节格式——时间到分钟、一段话摘要、工件全列
  （含 README、golden、三组新测试、commit）、两处偏差及其披露去处写清、如实声明 M2 未开始。

## 发现分级

**【偏移约束】**：无。

**【琐细】**（均无需修改，记录备查）：

1. `_migrate_v1_skill_row`（models.py:362-371）与 `_migrate_skill_record`（models.py:452-463）
   结尾各有约 8 行近似的"推导或报未知形状"尾巴，报错措辞刻意区分了 from_dict 与迁移两种上下文；
   抽共享 helper 会以间接层换这 8 行，两可，维持现状可接受。
2. `merged_source_subtasks` 函数体（models.py:396-401）6 行，可用
   `list(dict.fromkeys(chain.from_iterable(source_lists)))` 一行表达；具名共享点本身是对的，
   仅函数体略繁，不值得动。

**【赞同】**：

1. 必填无默认值是唯一正确形状（默认值即静默错值），约 60 处构造点样板是买完备性证明的一次性机械成本。
2. from_dict 的 v1 分支经 `schema_version == 1` 闸进入，v2 记录零开销，现行执行流程不受任何新检查影响。
3. 后缀镜像常量是循环依赖（ids import models）下的最小解，且有单测钉住与 ids 的等价。
4. `merged_source_subtasks` 由 K5 写入侧与迁移侧共用：core 不能 import composites，共用保证迁移值==新写值，内联反而要两份。
5. from_dict 对 v1 合并产物的报错含函数全名与参数签名，指向无歧义，被单测钉住。
6. 偏差(a)：装配器比对在方案字面式外加一层 `migrate_skill_record(row, skill_rows)`，是对"字面式必崩"的最小修正，复用已加载的 skill_rows，无比对语义改动。
7. 偏差(b)：补账按存储原样落盘是唯一能跨版本匹配中断前缀的形式，v1/v2 统一路径、还少一次对象构造；方案"945 有比对"的前提有误，处置诚实披露。
8. 两处偏差的配套测试（v1 源 bank 级装配、v1 前缀补账）把各自语义钉死，属应尽的测试义务而非多余设施。
9. source_subtasks / Exam.skill_id 在 src 内除写入与迁移外零读取方；kept_exams 仍按 exam_id、lineage 仍走 merged_from，读取方切换全部留给 M4，无抢跑。
10. reliability / store / manifest / runlock 零改动；补账位置、次序、无新增 try/except，"终态先写、行是回执"次序原样。
11. 迁移的成环守卫（4 行）把损坏 run 的无限递归变成点名错误，仅挂 v1 读取路径，比例恰当。
12. golden diff 分类声明经独立结构化复核为真：剥新字段、版本回退后 8 组合与 84d280e 逐字节相等，provenance 仅 base_commit。
13. 测试计数 1927→1945、ruff、test_layering 的提交声明全部复核为真。
14. README §2 同步准确无残留矛盾；log.md 条目合式，偏差与 M2 未开始均如实记录。

## 验收结论

**M1 验收通过。** 偏移约束 0 条；琐细 2 条（均建议不改）；从简洁性与反过度设计视角，diff 的每一
行都能在 §2/§9 或 AGENTS 七.3 中找到出处，两处对方案的偏离都是对方案错误的必要修正且以最小
方式落地并如实披露，用户约束（不加新 hash、不加影响执行流程的硬检查、完整保留异常分类与
恢复机制）逐条核对无违反。
