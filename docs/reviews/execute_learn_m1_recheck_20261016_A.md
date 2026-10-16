日期：2026-10-16
一句话说明：M1 修复轮的重审 A——P-A1 复核（对提交 85bfa4f，diff 786fda9..85bfa4f，契约 docs/decisions/execute_learn_implementation_20261015.md §2 末尾"M1 验收裁决"五条）。
对应 log.md 条目：2026-10-15 M1 三路验收裁决后的修复轮。

---

## 结论

**P-A1 已解决；M1 验收：通过。** 0 条【偏移约束】，3 条【琐细】（文案与
对称性，均不阻塞），其余全部核查项【赞同】。

核查方式声明：端到端是我自写的独立脚本跑的（/tmp/m1_recheck_A/
recheck_e2e.py，33 项断言全过，非重跑仓库测试）——v1 源（扁平合并 m0 +
嵌套合并 m_nested 两个 bank 成员 + 库外回退合并 m_revert）装配后，对产物
run 实跑 `stage3_candidates` 与 `curate_bank` 非空库短路；忠实性（迁移只加
字段与版本号、与 store.save 字节同构、v2 恒等穿过）逐项字节级验证。回退
转红是我独立复验的：把装配器（和审计工具）换回 786fda9 版本在 /tmp 副本
里跑新测试，端到端测试恰在评审 A 复现的 stage3_candidates → from_dict 崩溃
点转红（错误对象 `bank/merge/0000`），审计 v1 测试在同一拒绝处转红。测试是
我自己跑的（全量 1950 passed + 3 deselected、test_layering 3 passed、ruff
全过）。

---

## 一、逐项复核（对照裁决五条 + 第一轮 P-A1/琐细）

### 1. P-A1 是否真正解决 —— 【赞同，已解决】

**端到端亲测（自写脚本，33/33 过）：**

- **装配不崩**：v1 源（含 bank 内扁平合并 m0、嵌套合并 m_nested——m_nested
  合并自 m_flat2（其自身又是 s_f+s_g 的合并）与 s_h——以及库外 discarded
  回退合并 m_revert，全部记录与 bank 行降级 schema_version=1）经修复后的
  `assemble_reuse` 装配成功，无异常。
- **产物 v2 原生且字段在**：12 个 skill 记录、8 个 exam 记录、3 行 bank 行
  **全部** schema_version=2，skill 行带 `source_subtasks`、exam 行带
  `skill_id`；每一行都能被消费方用的裸 `Skill.from_dict` / `Exam.from_dict`
  直接加载。嵌套合并的迁移值正确：`[st1w3, st1s4, st1w4]`（沿 merged_from
  的传递首见序），回退合并 `[st1s, st1w3]`，扁平 m0 `[st1w, st1s3]`，段生
  行恒 `[自己的 subtask_id]`。
- **阶段 4 第一步可用**：对产物 run 实跑 `stage3_candidates(Store(产物))`，
  候选集**恰为** 8 个段生技能（按 ID 形状排除全部 merge 产物，排序精确
  断言），全部 schema v2 对象——正是 P-A1 里 `skill_status.py:45` 的崩溃
  点，现在通过。
- **阶段 5 非空库短路可用**：实跑 `curate_bank`（bank 非空走
  `_check_bank_not_stale`，它逐个 `store.load` 全部**非 bank** 存储技能——
  回退合并 m_revert 正在其中），返回 bank 不变（`[m0, m_nested, s_a]`）、
  **零模型调用**（三个 idle handle 的 calls 全空）。
- **审计工具（同根因消费方）**：v1 run（含 bank 合并成员 + round≥1 注入行
  指向合并成员）经迁移可审，专项最小夹具测试通过，且我回退转红复验。
- **库外回退合并确实被拷进产物且在 bank 外**——P-A1 事实链第 4 点点名的
  "被 K5 回退的 discarded 合并"形态，端到端覆盖。

**仓库端到端测试的夹具与断言强度**（tests/unit/stages/
test_assemble_reuse.py）——【赞同，足够强】：

- `_add_nested_and_reverted_merges` 真覆盖嵌套合并（m_nested 经 m_flat2
  两级）与回退合并（discarded、库外、来源保持 accepted 在 bank）；注释如实
  说明"回退合并正是字节复制产物在阶段 5 崩溃的那条记录"。
- 断言逐条够硬：装配 bank 行全部 v2 且 `source_subtasks` 非空；bank 行与
  存储记录 `read_json(path) == row` 逐位相等；产物 skills/exams 目录**全量
  glob** 逐行断言 v2 + 新字段 + 裸 from_dict 可载；嵌套并集按首见序精确
  断言；候选集恰为 8 个段生 ID 的排序断言；短路断言零调用（`handle.client.calls`）。
- ConfigError 测试（`test_refuses_a_source_whose_v1_lineage_cannot_migrate`）
  钉住裁决 5。

**回退转红独立复验**：/tmp 副本中把 `src/expedistill/stages/assemble_reuse.py`
换回 786fda9 版 → 3 个新测试转红（v1 形态断言、端到端、ConfigError），
其中端到端测试的失败点正是
`stage3_candidates → store.load → Skill.from_dict → _migrate_v1_skill_row`
对 `bank/merge/0000` 的 v1 合并拒绝（models.py:356）——与第一轮评审 A 的
复现崩溃点逐字一致；恢复装配器、把 `scripts/audit/audit_live_run.py` 换回
旧版 → 审计 v1 测试在同一拒绝处转红，装配器侧 43 测试全过。提交说明的
"revert-to-red verified"声明属实。

### 2. 迁移后落盘的忠实性 —— 【赞同】

- **只加新字段与版本号，不改既有值**：我逐行断言"产物行 == 源行 + 新字段
  + schema_version=2，其余键值逐位相等"，skills/exams 全部通过（不采信
  `{**data, ...}` 的代码读法，直接对产物验证）。
- **与 store.save 同构（缩进、键序）**：`_write_migrated_kind` 写
  `write_json(path, obj.to_dict(), indent=2)`，文件名 `quote(obj_id, safe="")`
  与 `store.path_for` 同式；我把每个产物对象用 `store.save` 写进对照仓
  byte-for-byte 比对，**全部逐字节相同**。bank 行序列化
  `_write_jsonl_atomic` 的 `json.dumps(ensure_ascii=False, sort_keys=True)`
  与 `append_jsonl` 参数一致。
- **v2 源恒等穿过（逐字节不变）**：v2 源装配后，保留的 skills/exams 文件与
  源文件逐字节相同；bank 保留行（源行序内子集，被过滤的是恰为
  test/reserved 血统的 s_t/s_r/m_mix 三行）逐字节相同；嵌套合并行原样。
- **bank kept 行 append 的实现**：`kept_bank_rows.append(row_obj.to_dict())`
  ——迁移孪生而非原始行；"kept bank row 与拷贝技能逐位一致"的完整性断言
  （`read_json(stored_path) != row`）原样成立（两侧同源）。
- **身份比对逻辑零改动**：`row_obj.to_dict() != stored.to_dict()` 比较与
  最终完整性断言在 diff 中原样未动；`skill_lineage_tasks`、keep 集判定、
  `_merged_original_groups`、negative list 均未触碰。

### 3. 修复没有引入新的行为变化 —— 【赞同】

- **allowlist 语义原样**（not listed means not copied）：copy list、
  `_ALLOWED_DIFF_PREFIXES`、`_assert_negative_list`、shortfall 拷贝、
  `_assemble_baseline`（只拷 stage 1，不经迁移）全部未动。
- **其余 KIND 仍字节复制**：trajectories/subtasks/attempts 我逐字节比对
  源与产物，全部相同。
- **消费方零改动字面成立**：`git diff 786fda9..85bfa4f --stat` 显示
  `curation.py`、`skill_status.py`、`methods/`、其余 stages 均**零行改动**
  ——产物 v2 原生使它们零改动即工作。
- **全套测试我实跑**：全量 1950 passed + 3 deselected（live 标记，与第一轮
  口径一致）、test_layering 3 passed、ruff 全过。1945→1950 算术自洽
  （+5 个新测试函数，无删除；`test_from_dict_rejects_v1_merge_product` 为
  原测试扩充断言）。
- **golden 不在装配器路径**：tests/fixtures/golden 在本提交零改动
  （diff --stat 无 golden 文件），金矩阵 11 项在全量内通过。

### 4. 次要裁决点 —— 【赞同，全部落实】

- **ConfigError 不误吞**：`ConfigError(ReliabilityError)`、
  `ReliabilityError(Exception)`（reliability.py:32/77）——运行时
  `issubclass(ConfigError, ValueError) == False` 亲测；两处
  `except ValueError` 只可能捕到迁移/from_dict 的 ValueError，装配器自身
  其余 ConfigError（身份比对、lineage、"does not store"）在 try 块外或先于
  包装抛出，不会被二次包装。
- **迁移失败抛 ConfigError**：技能行与 bank 行两条路径都有（错误链带
  `from exc` 保留原因），专项测试钉住 + 回退转红验证。
- **from_dict 报错的 R6 指向文案清晰**（裁决 3）：报错同时给出 (a) 该用的
  函数（`migrate_skill_record(record, skills_by_id)`）、(b) 不支持场景
  （"Resuming a v1 run directly on this code is not supported"）、(c) 依据
  （维护分支规则，AGENTS.md 六.1——与 AGENTS 正文"正式 run 固定在它启动时的
  checkout 上续跑（R6）"吻合）；单测断言 "maintenance branch" 文案。
  CONFIG_SCHEMA_VERSION 仍为 3（运行时验证），无 run 级迁移工具。
- **migrate_skill_rows 批量语义**（裁决 2）：map 内建一次
  （`{str(row["skill_id"]): row}`）、逐行按输入序迁移、v2 恒等返回**同一
  对象**（单测 `migrated[5] is rows[5]` 钉住）、兄弟缺失错误原样传播
  （专项单测）；被装配器与审计工具共用同一缝。

### 5. 第一轮琐细逐条核对 —— 全部处理或按裁决豁免

1. **嵌套源夹具**：已补（m_nested 两级嵌套 + m_revert 回退，端到端断言并集
   首见序）。
2. **ValueError/ConfigError 一致性**：已修（ConfigError 族，测试钉住）。
3. **方案 §2 "补账比对"措辞失准**：留备查，无需动作（维持第一轮判定）。
4. **v1 形态断言与错误注释**：断言已翻转为 v2（schema_version==2 +
   source_subtasks 在）；"every consumer loads them through from_dict"的
   与事实相反注释已按新事实改写（"v2-native ... plain from_dict load
   works"）。
5. **curation v1 前缀补账测试**（裁决 4）：保留，helper 与测试 docstring 均
   注明 v1 续跑场景属 R6 范围外、测试钉的是"按存储原样落盘"不变量——与
   裁决 4 文字一致。

---

## 二、发现分级

### 【偏移约束】

无。

### 【琐细】

1. 装配器技能加载的 try 块同时包住了 exams 目录的 `_source_objects` 与
   `Exam.from_dict`（assemble_reuse.py:1204-1215）：源 exams 目录里一个无法
   解析的 JSON（`json.JSONDecodeError` 是 ValueError 子类）会被包成
   "cannot migrate to schema v2" 的 ConfigError——处置与家族正确（损坏源、
   响亮、可定位），但文案把"JSON 坏"说成"迁移失败"；且 skills 目录的
   `_source_objects` 在 try 外，同类错误仍是裸 JSONDecodeError，两目录
   不对称。纯文案问题，行为不变。
2. `Skill.from_dict` 经 `_known_kwargs` 静默丢未知键（既有宽松读取语义，
   M1 之前就如此）：若某源行带多余键，迁移落盘会把它归一化掉（旧字节复制
   会保留）。真实 run 的行全部由 `to_dict` 写出、键集恒等于已知字段，实际
   不可达；且归一化后的产物正是所有消费方经 from_dict 看到的形态。仅
   记录，无需动作。
3. `zip(skill_rows, migrated_skill_rows)` 依赖 dict 插入序与批量迁移输出序
   对齐（assemble_reuse.py:1208-1210，审计工具 `_load_skills` 同式）——
   正确（Python 3.7+ 保序，两序同源，我端到端逐行配对验证），但可读性
   不如直接 zip keys/values 再建 dict；全测试覆盖之下无风险，可读性
   改进留待顺路。

### 【赞同】

1. **修法甲完整落地**（裁决 1）：产物 v2 原生、消费方零改动（消费方代码本
   提交零行改动，实核）、ASSEMBLED_FROM.md 披露对象行已迁移、其余 KIND
   字节复制；我 33 项独立检查全过，含端到端两条崩溃路径实测通过。
2. **忠实性四件套**：迁移只加新字段与版本号（既有值逐位相等）、产物与
   store.save 字节同构、v2 源逐字节恒等穿过、身份比对零改动——均我逐字节
   验证，非代码读法推断。
3. `migrate_skill_rows` 批量缝（裁决 2）设计干净：装配器与审计工具共用，
   v2 恒等返回同一对象，单测覆盖混合序列与兄弟错误。
4. 审计工具读 v1 run 经迁移（裁决 2）：`_candidate_groups` 与
   `check_injection_audit` 两处全量加载均改走迁移，v1 最小夹具（bank 合并
   成员 + round-1 注入行指向它）测试通过，回退转红复验。
5. from_dict 的 R6 维护分支指向（裁决 3）、curation 测试 docstring 注记
   （裁决 4）、装配器 ConfigError（裁决 5）逐条落实；CONFIG_SCHEMA_VERSION
   未动、无 run 级迁移工具。
6. 测试 1945→1950 全绿（我实跑），+5 无删自洽；test_layering 与 ruff 过；
   golden fixtures 零改动。
7. 回退转红声明属实（两处我独立复验，崩溃点与评审 A 第一轮复现逐字一致：
   stage3_candidates → from_dict 对 `bank/merge/0000` 的 v1 合并拒绝）。
8. 第一轮的 4 条琐细全部处理或按原判定豁免；README §2 与 log.md 同步
   完整。

---

## 三、复核证据（评审 A 自持）

- /tmp/m1_recheck_A/recheck_e2e.py：独立端到端 + 忠实性 33 项断言全过
  （v1 源装配 → 产物全 v2 → stage3_candidates / curate_bank 短路实测 →
  store.save 字节同构 → v2 恒等穿过 → 反事实 from_dict 拒绝）。
- /tmp/m1_recheck_A/repo_red：仓库 /tmp 副本回退转红——装配器回退 →
  端到端测试红于 stage3_candidates → models.py:356（`bank/merge/0000`，
  与第一轮复现一致）；审计工具回退 → 审计 v1 测试红于同一拒绝。
- 测试实跑：`pytest tests/` → 1950 passed, 3 deselected；
  `pytest tests/test_layering.py` → 3 passed；`ruff check` → 全过；
  目标四文件（assemble_reuse / models / audit / curation_resume）129 passed。
- 运行时断言：`issubclass(ConfigError, ValueError) == False`；
  CONFIG_SCHEMA_VERSION == 3；SKILL/EXAM_SCHEMA_VERSION == 2。

---

## 四、最终裁决

**P-A1：已解决。**
**M1 验收：通过。**
