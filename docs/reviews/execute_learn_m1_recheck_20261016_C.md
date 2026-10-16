日期：2026-10-16
一句话说明：M1 修复轮的重审 C——P-C1/P-C2 复核与 R6 边界裁定评估。
对应 log.md 条目：2026-10-15 M1 三路验收裁决后的修复轮（被审提交 85bfa4f，diff 786fda9..85bfa4f，叠加在 M1 主体 32d6c86 之上；裁决契约 docs/decisions/execute_learn_implementation_20261015.md §2 末尾"M1 验收裁决"五条）。

---

## 0. 总评

**结论先行**：修复提交 85bfa4f 按裁决五条逐条落地，两条偏移约束 P-C1/P-C2
均已解决，且解决质量高于裁决的字面要求（端到端测试驱动的是真实的崩溃形态，
不是装配成功即停的半程验收）。两处"回退转红"我都在 /tmp 副本（PYTHONPATH
指向副本 src）独立重放，**双双确认**：装配器回退到 786fda9 后，端到端测试恰在
我第一轮复现的崩溃点（`stage3_candidates` → `store.load` → `Skill.from_dict`
→ `bank/merge/0000` 的 v1 合并产物报错）转红；审计工具回退后，v1 审计测试恰在
`_candidate_groups` 的同型加载点转红。全套 pytest **1950 passed, 3 deselected**
（与声明一致，1945 + 5 个新测试算术自洽）、test_layering 3 项过、ruff E9/F 过、
金矩阵 11 项过且 tests/fixtures/golden 在 786fda9..85bfa4f **零改动**、工作树干净。
P-C2 的 R6 边界裁定本身**成立**（理由见 §2.1），我不要求额外机制。
**M1 验收：通过**，保留两条琐细（均不阻塞）。

---

## 1. P-C1 复核：装配产物 v2 原生 + 审计工具读 v1 经迁移

### 1.1 装配产物 v2 原生（裁决 1）

**实现核对**（`src/expedistill/stages/assemble_reuse.py`）：

- 源加载走批量迁移：`:1199-1206` `migrate_skill_rows(list(skill_rows.values()))`
  后逐行 `Skill.from_dict`（zip 的键序 = 值序，dict 插入序保证），exam 经
  `Exam.from_dict` 的 v1 推导；整块包 `except ValueError → ConfigError`
  （`:1207-1211`），与同处损坏源错误族一致；
- skills/exams 落盘改走 `_write_migrated_kind`（`:480-500`、调用点
  `:1292-1300`）：`write_json(path, obj.to_dict(), indent=2)` 与
  `Store.save`（`infra/store.py:373-377`）**同一序列化路径**，装配出的
  记录与 v2 原生 run 的 store 落盘逐位一致；
- bank 行写迁移孪生：`kept_bank_rows.append(row_obj.to_dict())`（`:1285`），
  注释明说"与拷贝的 skill 记录逐位一致"——测试确实断言
  `read_json(path) == row`（test_assemble_reuse.py:704-706）；
- 身份比对未动（`:1266-1270`，两侧同构，本就与 M1 主体一致）；
- ASSEMBLED_FROM.md 披露行已加（`:703-707`）——我在 /tmp 实跑一次 v1 源
  装配，确认渲染出的披露行内容准确（"对象行已迁移为 schema v2……其余对象类
  逐字节拷贝"）；
- trajectories/subtasks/attempts 仍走 `_copy_kind` 字节复制（`:1288-1291`），
  符合"其余 KIND 不动"的裁决边界。

**夹具与端到端测试核对**（`tests/unit/stages/test_assemble_reuse.py`）：

- `_add_nested_and_reverted_merges`（`:611-667`）补齐了我第一轮琐细 2 点名
  的嵌套合并形态，并额外造了**库外回退合并**（`regression_failed` 落盘、
  sources 保持 accepted、不进 bank）——这正是 `_check_bank_not_stale` 只加载
  "不在 bank 内"技能时撞上的那类记录，夹具形状与真实崩溃形态对齐，不是摆设；
- `test_bank_level_assembly_of_a_v1_object_source_run`（`:676-743`）断言翻转
  为 v2 原生：bank 行、全部 skills 记录、全部 exams 记录 `schema_version == 2`
  且新字段已推导（嵌套合并的并集按首见序 = 三个 subtask，回退合并自己的二元
  并集），每条都能过普通 `Skill.from_dict` / `Exam.from_dict`；
- `test_assembled_v1_run_feeds_stage4_enumeration_and_stage5_short_circuit`
  （`:745-790`）是裁决点名的端到端：v1 源 → 装配 → `stage3_candidates` 枚举
  出全部 8 个段生候选（合并产物按 ID 形状排除）→ `curate_bank` 非空库短路
  返回 bank，**零模型调用由 idle 假句柄断言**（`not any(handle.client.calls)`）。
  这正是我第一轮 P-C1 复现的链路，现在走通；
- `test_refuses_a_source_whose_v1_lineage_cannot_migrate`（`:792-812`）钉
  ConfigError（裁决 5），破坏 merged_from 指向不存在 ID。

**四个消费方逐一核对我第一轮的清单**：

1. `composites/skill_status.stage3_candidates`（`skill_status.py:36-47`）——
   **零改动**（diff 确认 composites/ 与 methods/ 全目录无改动，符合"消费方
   零改动"的裁决设计），对装配产物拿到 v2 数据，端到端测试直接覆盖；
2. `composites/live_acceptance.pending_groups`（`live_acceptance.py:413-417`）
   ——零改动，同型 `store.ids + store.load` 纯 from_dict 枚举，对 v2 原生
   产物按构造成立；无独立测试，但机制与已测的 stage3_candidates 逐行同型，
   补测属近重复覆盖，不要求；
3. `methods/curation/none.run`（`none.py:24-26`）——零改动，同上按构造成立；
   agentbrew 路径（`curate_bank` 短路）由端到端测试覆盖；
4. `scripts/audit/audit_live_run.py`——v1 run 直读经迁移，见 §1.2。

### 1.2 审计工具读 v1 run 经迁移（裁决 2）

**两处加载点亲自核对，都走 `_load_skills`**：

- `audit_run` 只做一次 `_load_skills(store)`（`:678`），`_candidate_groups`
  的全量枚举改为吃该 dict（`:173-190`，docstring 注明按 sorted-id 序，与改前
  `store.ids` 的迭代序一致）；
- `check_injection_audit` 的冻结行成员加载改为 `skills.get(str(skill_id))`
  （`:561-570`），命中失败仍报 "unknown skill"，重建失败仍报
  "cannot reconstruct"——**语义零漂移**，只是换数据源；
- 我逐一 grep 了 `store.load("skills", ...)` 的残余调用点：`check_group_terminals`
  `:219` 只加载 group 成员（候选 ID 判定已排除 bank/merge 产物，段生 v1 行
  from_dict 可本地推导，不崩）；`:848` 起全在 `self_test` 的自建 v2 夹具里。
  **没有漏网的 v1 合并产物加载点。**

**round≥1 冻结注入行指向 bank 合并成员的场景（我第一轮说"真 TB21 v1 run
必命中"）**：新测试 `tests/unit/test_audit_live_run.py` 的最小夹具
（`_build_v1_live_run`，`:35-86`）恰好构造了这个形状——v1 store 里一个候选 +
一个 v1 合并产物（bank 成员），round 1 的 injection_audit 冻结行 members 指名
该合并成员，sha/tokens 按其内容预先算好。**我为排除"行没被处理导致空过"的假绿
做了注入式复核**（/tmp 副本）：(a) 夹具原样跑 audit，injection-audit 检查
**零 findings**——合并成员经迁移加载、内容重建、sha 逐位吻合，路径真实走通；
(b) 删掉合并成员的存储文件后重跑，"unknown skill" 立即出现——证明 round-1 行
确实被处理、成员加载是活的；(c) `_load_skills` 返回的两行在内存里均为
schema v2（磁盘 v1 → 内存 v2，不落盘）。回退审计工具后该测试恰在
`_candidate_groups`（786fda9 版 `:160` 的 `store.load`）转红，报错文本与我
第一轮复现的完全一致。审计工具自带 `--self-test` 亦通过。

---

## 2. P-C2 复核：R6 边界裁定的落实与可否性

### 2.1 裁决 3/4 的落实核对

- **from_dict 报错双指针**：`core/models.py:354-364` 的报错在指向
  `migrate_skill_record(record, skills_by_id)` 之外追加"Resuming a v1 run
  directly on this code is not supported: a formal run stays on the old code
  of its own maintenance branch (AGENTS.md section 6 rule 1)"；单测
  `test_models.py:333-343` 两个 match（`migrate_skill_record` 与
  `maintenance branch`）分别钉住两段；
- **不建 run 级迁移工具**：修复提交的 scripts/ 只改了 audit_live_run.py，
  无任何迁移工具——**按裁决这个缺失是正确的**（无消费者，P5 教训）；
- **测试 docstring 注记**：`tests/composites/test_curation_resume.py:453-461`
  （helper）与 `:476-490`（测试）都注明"v1 续跑场景本身属 R6 范围外（AGENTS
  6.1），测试钉的是按存储原样落盘的不变量，该不变量同样服务 v2 run"；
- **curation v1 前缀补账测试语义未动**：diff 逐行核对，该文件**只有 docstring
  改动，零断言/正文改动**——"按存储原样落盘"（含崩溃注入 `SecondAppendCrashStore`
  与零模型调用断言）原样保留；
- CONFIG_SCHEMA_VERSION 未动（config 闸仍 v3），符合裁决。

### 2.2 边界裁定可否性——我的判断：**成立，无需额外机制**

我第一轮的担忧是"过程性隔离不是闸门"：对象升版不动 CONFIG_SCHEMA_VERSION，
stage 0 的配置闸对对象 schema 失明，误用主工作树续跑 v1 run 会一直走到阶段 4/5
才炸。复核后我认为裁决的缓解是充分的，理由如下：

1. **发现点前置且零成本**。含合并产物的 v1 run 被误续跑时，第一个全量加载
   恰是阶段 4 驱动的第一个动作（exam 驱动 `methods/acceptance/exam.py:123`
   的 `stage3_candidates(store)` 在任何单元派发与模型调用**之前**无条件执行；
   live 驱动的 `pending_groups` 同型），阶段 5 的 `_check_bank_not_stale`、
   审计工具的首个技能加载同理。即误用在**零模型开销**处响亮死亡，而阶段 6
   （唯一产出可用数字的阶段）在固定按序的 pipeline 里对这类 run **不可达**。
   我第一轮说"会一路走到阶段 4/5 才炸"在字面上仍真，但炸点是阶段 4/5 的
   第一步而非深腹——闸门效果与 stage 0 拒载几乎等价，成本为零；
2. **报错自含完整出路**：双指针让撞上的人同时知道"该用哪个函数修"与"违反了
   哪条流程"（AGENTS §6.1 的维护分支规则原文在案）；
3. **stage 0 字面闸不可行且被方案自己禁止**：对象版本不在 config/manifest 里，
   stage 0 拒载只能靠全 store 对象行扫描——这正是方案 §1.2 总原则明文禁止的
   "影响执行流程的硬检查"，为拦截一个流程明文禁止的场景给每次启动加一遍
   全量读，得不偿失；from_dict 的检查就是同一个闸，放在版本第一次真正要紧
   的地方；
4. **残余场景不产生错误数字**：无合并产物的 v1 run 误续跑不会炸（v1 段生行
   from_dict 本地推导），若阶段 3 有未完单元会写出 v2 行形成混版 store——
   但 bank 不被短路路径改写、注入内容与 v1 行逐位同内容（差异仅簿记字段），
   数字不致错，且该场景同样流程禁止。留痕即可，不构成机制理由；
5. **不建工具的裁定与 P5 教训一致**：支持范围内的三条路径（v1 run 回维护
   分支续跑、装配产物 v2 原生、审计工具内存迁移读取）都不需要 run 级迁移；
   真出现消费者时 `migrate_skill_rows(rows)` 让工具平凡可写。

结论：**"过程性隔离 + from_dict 响亮报错（双指针）"的组合已把误用场景压到
"零成本、零数字产出、信息完整"的失败形态，我不再要求 stage 0 拒载或任何
额外机制**（也符合用户反对过度设计的约束）。

---

## 3. 共享缝与批量包装

- `migrate_skill_rows` 落 `core/models.py:425-436`：模块只 import 标准库
  （`dataclasses`/`typing`，`:31-32`），core 不依赖 infra，**分层合规**
  （test_layering 3 项过亦证）；
- **装配器与审计工具真共用**：装配器 `assemble_reuse.py:1200` 与审计工具
  `audit_live_run.py:170` 都调 `migrate_skill_rows`，单一实现，无复制；
  装配器 bank 行比对另用单条 `migrate_skill_record`（`row` + 既有
  `skill_rows` map，`:1259-1261`）——与批量版同构（批量版只是把 map 构造
  收口），语义一致；
- **v2 恒等穿过的逐位等价**：`migrate_skill_record` 对 v2 行返回同一对象
  （单测钉 `is row`），批量版保序且 `migrated[5] is rows[5]`
  （`test_models.py:409-432`）。对 v2 源：skill/exam 记录经 from_dict→to_dict
  往返后由与 `Store.save` 相同的 `write_json(indent=2)` 落盘、bank 行经
  `sort_keys=True` 的 `_write_jsonl_atomic` 落盘——与 K5 的规范写盘逐位
  一致，**v2 源装配行为零变化**（往返对 to_dict 写出的行是恒等）。手工多键
  的行会被 from_dict 规范化丢弃——但该行在身份比对处与存储记录的往返同构
  比对，一致源照常通过，不构成新拒绝面；
- `except ValueError` 不误吞：`ConfigError` 继承 `ReliabilityError(Exception)`
  （`reliability.py:32,77`），不是 ValueError 子类；try 块内（迁移 + 两个
  from_dict）也不会抛 ConfigError。v2 行缺新字段的 TypeError 保持原样外抛
  （与 M1 主体行为一致，非本次回归）。

---

## 4. 验收证据独立复验（全部亲手跑）

- **全套 pytest**：`.venv/bin/python -m pytest -q` → **1950 passed,
  3 deselected**，exit 0。基线核对：1945 + 5 新增（2 装配 + 1 审计 + 2 批量
  迁移单测，`git diff` 逐个点数）= 1950，算术自洽；
- **test_layering**：3 passed；**ruff**（E9,F，src+tests+scripts）：All checks
  passed；
- **金矩阵零改动**：`git status --porcelain` 干净（复核前后各查一次）；
  `git diff 786fda9..85bfa4f -- tests/fixtures/golden` **零 diff**；金矩阵
  11 项随全套通过（`tests/golden/test_golden_matrix.py` 单独跑 11 passed）；
- **回退转红，两处都亲手重放**（/tmp 副本 `git archive 85bfa4f` 抽取，
  `PYTHONPATH=<副本>/src` 压过 venv 的 editable 指向——注意 PYTHONPATH 必须
  显式设，否则 import 会落到原仓库 src，这是我第一次重放时踩到的假阴性）：
  1. 装配器回退（`git show 786fda9:...assemble_reuse.py` 覆盖副本）→ 端到端
     测试红，traceback 恰为 `skill_status.py:45 stage3_candidates` →
     `store.py:387 load` → `models.py:256 from_dict` → ValueError
     `bank/merge/0000 ... schema-v1 merge product`——**与我第一轮 P-C1
     复现的崩溃点逐帧相同**；
  2. 审计工具回退 → v1 审计测试红，恰在 786fda9 版 `_candidate_groups`
     `:160` 的 `store.load` → from_dict 同错；
  3. 恢复后两测试各自回绿（副本基线 44 passed）。
  修复声称的另两处转红（装配 v2 断言、ConfigError 两处）由同一回退机械蕴含
  （回退文件即含这两处），未单独重放；
- **审计工具 `--self-test`**：all checks passed。

---

## 5. 我第一轮琐细的处置核对

1. **golden 结构化审计脚本未留档**：裁决五条未采纳，修复提交与 log.md 均无
   交代。**我同意不采纳**：M1 型"预期 diff 的逐键分类"是一次性迁移审计
   （结论我第一轮已独立复核成立）；M2/M3 的金门是"逐字节无 diff + 常备 11
   项矩阵"，由测试机器钉住，不需要人工脚本；真到 M4.3 golden 全量重生成时
   再写即是第二次使用（届时按 AGENTS 五.1 入 scripts/）。不算未清债务；
2. **I1 夹具嵌套合并**：已补（`_add_nested_and_reverted_merges`，且超出
   要求地补了库外回退合并——正是 `_check_bank_not_stale` 的崩溃形态）；
3. **`:634` 与事实相反的注释**：已按新事实改写（原"every consumer loads
   them through from_dict"注释随 v1 形态断言一并翻转为 v2 原生表述）；
4. **错误类一致性（ConfigError）**：已实现并配测试（裁决 5）；
   "不误吞"经类层次核对（§3）。

---

## 6. 发现

### 6.1 偏移约束

无。P-C1、P-C2 均按裁决解决（见 §1、§2）。

### 6.2 琐细

1. **README 未落 R6 边界句**。任务清单核对三处文档：方案 §2（裁决 3 原文
   在案）✓、测试 docstring（helper 与测试双处）✓、README——修复只同步了
   装配器 v2 原生语义（README.md:402-406），"v1 run 直接在新代码上续跑
   不受支持、报错指向维护分支规则"这一支持范围声明未写进 README §2 的
   v1→v2 小节。缓解：报错信息自带 AGENTS §6.1 指针、AGENTS §6.1 规则原文
   在案、README 该小节已写"v1 合并产物在 from_dict 响亮报错"。建议下次
   README 同步时（M2/M3 均有 README 跟随者改动）补一句，不单独为此提交。
2. **pending_groups 与 methods/curation/none 对装配产物无直接测试**（§1.1
   第 2/3 项）。两处与已测的 stage3_candidates 逐行同型（纯 from_dict 全量
   枚举），对 v2 原生产物按构造成立，补测属近重复覆盖——**记录在案，不
   要求行动**；若将来任一处改加载方式（如加缓存），端到端测试不会替它报警。

### 6.3 赞同

1. **裁决 1 的落法比"消费方逐个接迁移"更好**：v2 原生产物让全部现在与未来
   的 from_dict 消费方免费可用，迁移逻辑收在装配器一处；写侧迁移 + 读侧
   纯函数的对称性干净（`migrate_skill_rows` 恒等穿过保证 v2 源零行为变化）；
2. **测试形状与真实崩溃形态对齐**：夹具的库外回退合并、bank 内扁平+嵌套
   合并、round≥1 冻结行指名合并成员，全部是我第一轮点名的真实命中形状；
   idle 假句柄断言零模型调用，防"测试自己调了模型"的假绿；
3. **审计工具改动最小且语义零漂移**：一处全量加载、一处成员查找，报错
   分支与文案不动；`check_group_terminals` 的成员重载保持原样是正确的
   （候选 ID 判定使然，非漏改）；
4. **P-C2 边界裁定成立且文档自洽**（§2.2 五点理由）；报错双指针让"响亮
   死胡同"变成"带完整出路的拦截"；
5. **log.md 修复轮条目完整诚实**：五条裁决逐条对账、测试增量与回退转红
   均如实记录，包括"golden 不在装配器路径"的说明；
6. 全套验收证据（1950/layering/ruff/金矩阵 11 项/双回退转红）经我独立
   复验全部属实。

---

## 7. 最终结论

- **P-C1：已解决**——装配产物 v2 原生，端到端测试覆盖阶段 4 枚举与阶段 5
  短路（零模型调用）；第一轮点名的四个消费方中装配路径覆盖的三个全部拿到
  v2 数据（两个有直接测试、两个按构造成立），审计工具对 v1 run 直读经
  迁移（两处加载点逐一核对，round≥1 冻结行指名合并成员的场景经注入式
  复核证明真实走通）；
- **P-C2：已解决**——按裁决以"R6 范围外 + from_dict 双指针报错 + 测试
  docstring 注记"处理；未建 run 级迁移工具（正确）；curation v1 前缀补账
  测试原样保留、语义未动；**边界裁定本身成立**（§2.2），无需 stage 0 拒载
  等额外机制；
- 琐细 2 条（README 边界句、同型消费方无直接测试），均不阻塞、不要求
  本轮行动。

**M1 验收：通过。**可以进入 M2（修复不触 run 产物、金矩阵零改动，M2 的
"golden 逐字节无 diff"门基线即 85bfa4f）。

评审方法留痕：复验与回退转红在 `/tmp/m1_recheck_c.PMBgf1/`（85bfa4f 的
git archive 副本，PYTHONPATH 指向副本 src；回退 = 用 `git show 786fda9:`
覆盖副本内单文件后单测），外加注入式审计探针脚本；均为只读目的，仓库
工作树除本文档外零改动（复核前后 `git status --porcelain` 两次确认为净）。
