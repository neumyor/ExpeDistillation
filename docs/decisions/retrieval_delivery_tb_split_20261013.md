# 检索式 skill 分发（A）+ TB 冻结划分复用（B）+ 8 代理并行实施：最终版方案

日期：2026-10-13（初稿 → 修订一：一轮三方评审采纳 → 修订二/定稿：二轮三方评审采纳 → 修订三：用户裁决，TB 复跑改为 v2 全重蒸馏）
一句话说明：整合阶段 2/3 协议 v2、检索式分发（W2）、TB 冻结划分复用实验（W3）与 8 子代理波次实施的最终方案；两轮六路评审全部收敛，本版为实施依据。
对应 log.md 条目：2026-10-13 设计定稿条目（实施另行记录）。

## 修订三（2026-10-13 用户裁决）：TB 复跑改为 v2 全重蒸馏

**用户决定**：协议 v2 改变了选材（F 口径）与 skill 生成方式，TB 复跑必须按 v2 **完整重新蒸馏**，不再过滤旧库只跑阶段 6。具体为"阶段 1 轨迹复用 + 阶段 2–6 全新跑"：

1. **阶段 1 裸跑轨迹复用**（246 条，协议无关的纯测量）：装配工具新增 **baseline 复用档位**——仅拷贝 train-41 双臂轨迹（+ stage-1 accepted shortfall 若存在），**不拷** subtask/skill/exam/attempt/bank/终态账本；同一性校验相应放宽为仅钉 {benchmark, 双 executor 模型名, 双 n_attempts, train ⊆ 源任务集}（阶段 1 复现性所需的最小集；规则名/prompt digest/skill_delivery 允许不同——它们只作用于全部新跑的下游）。F 在新配置（v2 规则 `any_weak_failed`）下重算 = **train-41 上 28 题**（旧口径 16）。
2. **阶段 2–5 全新**：28 题全部新切（同一 G1 prompt、单次连贯采样，不混用旧段）；阶段 3 按 v2 契约生成（四节结构 skill，×3 检索加权路径**首次真实生效**）；K4/K5 全新；产出 v2 结构化新库（规模待实测，bank_token_budget 安全阀预登记处置不变）。
3. **阶段 6**：168 局（检索新库 + 同期新鲜裸跑对照）；min_score 在**新库**上按 §0 裁决 8 预注册网格重新探测冻结（0.02 占位不沿用）。
4. **验收门更新**：零调用门改为 `--until-stage 1`（仅阶段 1）；数字门 F == 28、库规模以阶段 5 实测记录；其余门不变。
5. **配置**：新建 `configs/experiments/tb21_split_v2.yaml`（v2 规则名 + 冻结三列表 + retrieval；原 `tb21_split_reuse.yaml` 的 v1 规则名设计随原装配实验一并归档为可选消融，不再启动）。
6. 原"过滤 42 条旧库、零调用到阶段 6"的装配实验**作废为后续可选消融**（装配机制与 bank 档位保留，日后若要单独检验检索消费层可零蒸馏成本启动）。

---

## 0. 两轮评审的最终裁决（实施中不得偏离）

| # | 裁决 | 依据 |
|---|---|---|
| 1 | **库 = 42 条，全链口径，单一溯源函数**。锚点口径（44）会漏入 2 条含 validation 溯源的 accepted merge（`bank/merge/0004`、`0011`），触发 K5 stale 拒绝或库内混入 reserved 溯源内容。train 侧 16 条 merged 原件分两组：**11 条被 42 库内 merge 正常消费；5 条真悬空**（消费 merge 被过滤排除：dna-insert/strong/00/seg01、extract-moves-from-video/strong/01/seg00、path-tracing-reverse/weak/00/seg03、path-tracing-reverse/weak/01/seg02、write-compressor/weak/00/seg04）——ASSEMBLED_FROM 分两组记录并点名 5 条清单，装配断言不得硬编码 16 | 一轮三路 blocker；二轮三路实测复核 |
| 2 | **检索文本 = `Task.instruction`**（TB=任务名）。如实声明：本 run 检索是任务名词面匹配，**×3 节加权全程走 fallback（库全是 v1 散文）**；结论不外推 ALFWorld。二轮实测：48 任务对 42 库的 top-1 分数双峰，**18/48 零词面重叠**（top-1=0；qemu 系 skill 因 test 溯源被滤光——防泄漏与检索信号天然对撞），median 0.07、max 0.44 | 一轮三路 blocker；二轮 R3 实测 |
| 3 | **`_run_namespace` 现为每 batch 一次（`if pending:` 内），per-task 注入必须下沉逐单元**；stage6 一次性解析出全部任务的 (skills_injected, block) 映射，`_check_stored` 比对与执行两路都读该映射。既有 `test_run_namespace_is_deterministic_and_shared_by_retries` / `differs_per_arm` / `covers_the_rendered_skills_block` 三个测试**不改一字且保持绿** | 一轮 major；二轮三路点名 |
| 4 | **装配同一性校验 = 白名单 diff**：对照源 manifest 逐键比对，仅允许 {mode, split, tasks, skill_delivery, schema_version} 差异；tasks_digest 对照 + "远端内容漂移不在 digest 覆盖范围（AgentBrew revision 2fd12b8）"知情声明进 ASSEMBLED_FROM | 二轮 R1 裁决 4 升级 |
| 5 | **装配 = allowlist 拷贝 + "未列即不拷" + 负清单断言**。负清单增补：leak_verdicts.jsonl、rewrite_failures.jsonl、curation_log.json（拷入必炸——`_read_final_member_ids` 读 97 成员 load 缺失）、manifest、pipeline_log、llm_requests/、failed_units、skip_audit、各 stage summary、stage6_test/、work/。完整性不变式适用范围 = **store 五类对象与库行**（segmentation_audit 整文件拷贝含 1 行 validation 侧 empty 记录属惰性审计记录，不违不变式） | 一轮 major；二轮 R2-#3/R3-#13 |
| 6 | **复用配置：除 mode/split_spec/skill_delivery 外全部方法键逐字复制**（含 segment_retry_limit、model_options、weak_profile、K、retry 族、token_budgets、bank_token_budget、curation_rounds——一轮清单漏了前三者） | 二轮 R2-#4/R3-#11 |
| 7 | **stage0/manifest/summary 恒含三键**（两键 spec 时 reserved 恒为 []）——test_stage0 的既有断言改法以此为准；`_explicit_split_ids` 并集必须含 reserved | 一轮 blocker；二轮 R2-#9 |
| 8 | **min_score 校准协议（预注册，本节即协议本体）**：网格 {0.02, 0.05, 0.10}（0 被排除：会给 18 个零重叠任务注入纯噪声）；选定规则 = **满足"排除全部零重叠任务"的最小网格值**；非退化判据 = test-28 注入率 ≥ 50% 且 1 < 归并类别数 < 29；判据不满足时的唯一出口 = **维持选定值、如实记录"检索覆盖受限、部分任务等价裸跑"**（设计本就容忍裸跑为主，这是结果不是失败）。全部探测为**纯 select_skills 计算零执行**，产物进 tmp/ 不进 run 目录；选定值与探测分布只进 log.md 不进报告表。**披露**：二轮评审已在协议冻结前实测分布（18/48 零重叠；0.02→30/48 注入 62.5%），本协议的判据设定知悉该数字；B1 落地后以正式实现重跑探测，以重跑数字按上述规则执行 | 二轮三路裁决合并（R1-N3/R2 裁决5/R3-Q5） |
| 9 | **ALFWorld v2 的 min_score 标注"占位未冻结"**，其全量启动前用同一协议在 ALFWorld 自身开发切片（真实 instruction 文本）校准冻结——TB 校准值不外推（IDF 刻度随库变） | 二轮 R1-N4/R2-#5 |
| 10 | **类别归并在本库近似惰性**（二轮实测：全文拼接画像 + 共享技术词表 → 巨型类不可避免，阈值 0.15→1 类、0.20→6 类、0.30→18 类）：阈值常量钉 0.25，预期 8–18 类；预登记"两步路由在本 run 为惰性机制，其判别价值预期在 v2 结构化库（ALFWorld）"；B1 测试 fixture 用 **29 组**真实形状（42 库实际组数；62 是 97 库口径），另加 62 组压力口径用例 | 二轮 R2-Q6/R3-Q6 实测 |
| 11 | **波次派发**（并发隐含依赖链：A2/B1 依赖 A1 常量、A3 依赖 A2、A4 依赖 A1+A2、B2 依赖 A1+B1、C 测试依赖 A1）：Wave 1 = A1 + D → Wave 2 = A2、B1、C → Wave 3 = A3、A4、B2。通用义务加一条：**上游契约未落盘时自测红于 ImportError 属预期，不得把缺失常量写进不属于自己的文件，报告等待集成** | 二轮 R3-#3 |
| 12 | **生成器 render_config 必须产出与手写 v2 配置逐字节一致**（含 skill_delivery 块、协议头、注释修正），否则 --check 必 FATAL / 默认模式静默覆写手写件；默认模式同时生成 searchqa_v2（无人手写，生成器即正本） | 二轮 R1-N4/R3-#4 |
| 13 | **blocks/__init__.py 与 composites/__init__.py 均由编排者独占**（新导出统一注册；顺带改写 blocks/__init__.py 的 "several agents append here" 陈旧注释）；`skills_injected` 的 dict 列表是**新形状**（字段本为 Any，勿称"既有形状"） | 二轮 R2-#2/R3-#5/#9/#10 |
| 14 | **旧 run 全部不可续跑的知情记录**：原因链 = prompt digest 变更 + skill_delivery 新键 + split 三键，三者叠加（非仅 skill_delivery 一项） | 二轮 R2/R3 |
| 15 | 契约补钉：`routed` = 类别名列表；"检索文档" = skill 全文（标记节 ×3、无标记全文 ×1）；组画像 = 成员检索文档拼接；**无组 skill 自成一类**（42 库实测零条，规格钉死）；retrieval_audit.jsonl 路径 = `stage6_test/retrieval_audit.jsonl`、**覆盖式**写出（dispatch 前一次）；低于阈值的 `skills_injected` 记 **[]**（与裸跑 None 可区分） | 二轮 R2-#8/#12/R3-#6 |

## 1. B：TB 冻结划分复用

### 1.1 冻结划分（用户裁决）

train = AgentBrew evolve(41)、test = AgentBrew test(28)、reserved = AgentBrew validation(20)；三列表原样抄 `benchmark/terminalbench-eval/data/splits/*.json`（seed 42、revision 2fd12b8、两两不交、并集 89）；只映射不重跑划分。reserved 开发冒烟保留，正式 run 不执行。

### 1.2 执行机制（两轮六路逐环实测核验通过）

装配 → 管线全跑：阶段 1 K1 store 命中 246/246（`_check_stored` 只比 role/model_name/skills_injected，不含 namespace；全命中时 namespace 根本不计算）；阶段 2 存储命中（audit 整拷保 skip 终态）；阶段 3 存储命中 + generation_failures.jsonl 的 6 行 train 溯源终态；阶段 4 全终态（511 条 skill 无 pending，`status != "pending"` 直接短路，**不需要任何 K4 账本**）；阶段 5 库非空短路 + `_check_bank_not_stale` 相容（accepted∩纯train == 过滤库 == 42，逐 id 相等）；阶段 6 = **168 局全新**（检索注入臂 + 同期新鲜裸跑对照）。v1 规则名 + v2 prompt digest 同 manifest：零 G2 调用；装配若有洞，write_skill 守卫 ValueError 响亮停批（零调用门天然自检）。

### 1.3 装配规格（`composites/assemble_reuse.py`）

- **allowlist**：train-41 双臂轨迹 246 条；train 溯源 subtask/skill/exam/attempt；`stage2_segments/segmentation_audit.jsonl`（整文件）；`stage3_generation/generation_failures.jsonl`（train 溯源行）；过滤后 `skill_bank.jsonl`（**42 条**，契约 10 溯源函数）；源 run 的 stage1 accepted 文件（本源不存在，代码按缺省处理）。其余未列文件一律不拷。
- **负清单断言**：见裁决 5。
- **同一性校验**：白名单 diff（裁决 4）。
- **完整性不变量**（store 五类对象与库行）：库行 content 与新 store 同名 skill 逐位一致；新 store accepted 集 == 库行 id 集；被拷对象零 test/reserved 溯源。
- **ASSEMBLED_FROM.md**：源 run、划分 provenance（含 revision 知情声明）、全链口径、逐类计数与校验和（246 轨迹 / 299 subtask / 42 库 / 29 组）、merged 原件 11+5 两组及 5 条点名、阶段 1 播种溯源（campaign meta）、未拷贝清单。
- RunLock；拒绝已含 manifest 的 run_dir。

### 1.4 复用配置 `tb21_split_reuse.yaml`

mode=split、三列表 split_spec、**除 mode/split_spec/skill_delivery 外全部方法键逐字复制**、规则名 v1 三条、skill_delivery（retrieval, top_k 5, max_categories 3, **min_score 0.02 占位——按裁决 8 协议在门 4 后冻结**）、头部协议注记与混装声明。

### 1.5 验收门（顺序执行，全过才放 168 局）

1. **零调用门**：装配后 `--until-stage 5`，usage 账本不存在或零增量（双态可过）；需带 LLM 环境变量（handle 构建先于 stage 0）；
2. **数字门**：F == 16、库 == 42、划分两两不交并集 == 89、ASSEMBLED_FROM 对账；
3. **确定性门**：同任务两次 select_skills 逐位同；同库两次 derive_categories 逐位同；
4. **检索预检门（零执行）**：按裁决 8 协议对 48 个 test+validation 任务跑 select_skills 探测（产物进 tmp/），按预注册规则选定/确认 min_score 并冻结进配置；核对 1 < 类别数 < 29；
5. **首题干跑门**：对照臂首题全链路通过后放全量（workers/stagger 沿用既有运维参数；infra 短板政策预注册：重试耗尽单元的续跑/接受标准、双臂等 policy，在看结果前写进 runbook）。

## 2. A：检索式分发

### 2.1 D11 `blocks/retrieve.py`（纯函数，仅依赖 core + 标准库）

- `derive_categories(skills) -> Dict[str, str]`：G9 细组 → 粗类别，确定性词法凝聚归并（组画像 = 成员检索文档拼接、平均链余弦、**阈值常量 0.25**、并列按组名字典序；无组 skill 自成一类）。本库预期 8–18 类、路由近似惰性（裁决 10 预期管理句写入 docstring）。
- `select_skills(task_text, skills, categories, *, top_k, max_categories, min_score) -> {"selected": [...], "routed": [类别名...], "scores": [{"skill_id","score"}...], "threshold_passed": bool}`：路由 top-`max_categories` 类别 → 池内 top-k；TF-IDF 余弦（词元小写 [a-z0-9_]+、IDF 自库、OOV 丢弃）；标记节 ×3 / 无标记全文 ×1；并列 (分数降, skill_id 升)；**阈值只门 top-1**；低于阈值 → selected 空 → 注入 ""。

### 2.2 配置（A1）

`skill_delivery: {mode: retrieval|whole_bank, top_k ≥1, max_categories ≥1, 0≤min_score<1}`，缺省 whole_bank。mode=retrieval 时三参数必填。

### 2.3 接线（B2）

- `batch_execute` 增 `skills_for_task: Optional[Callable[[Task], Tuple[Any, str]]] = None`；namespace 下沉逐单元（裁决 3）；标量路径原样（whole_bank 回归）。
- `stage6_test` retrieval 分支：开局 derive_categories 一次 → 逐任务 select_skills(task.instruction) → (injected 列表, block) 映射（**新形状**：`[{"skill_id","version"}...]`；低于阈值记 `[]`）；`retrieval_audit.jsonl` = `stage6_test/retrieval_audit.jsonl`，dispatch 前覆盖式一次写出；summary `bank_reference` 仍记整库 digest、键只增不改。

## 3. 8 代理波次分工（文件所有权互斥；契约见 §3.9）

> 通用义务：只改独占文件；**不碰 blocks/__init__.py 与 composites/__init__.py**（导出名报编排者）；上游契约未落盘时自测红于 ImportError 属预期，**不得代写**，报告等待；新函数 docstring 英文解释为什么；完成后自跑 `pytest <相关测试> + ruff check --select E9,F src/`；prompt/模板改动以协议 v2 文档 §3.2/§3.4 逐字文本为唯一正本。
> 波次（裁决 11）：Wave 1 = A1 + D；Wave 2 = A2、B1、C；Wave 3 = A3、A4、B2。

各代理的文件 × 函数级改动表**以本版 §0–§2 的裁决与契约为准**，逐条如下（改动内容与修订一版一致处不重复，仅列差异与补充）：

- **A1**：修订一版简报 + ①生成器 render_config 产出与手写 v2 配置逐字节一致（含 skill_delivery 块），默认模式同时生成 searchqa_v2；②`_SPLIT_LIST_KEYS` 校验收紧为"恰为两个键集之一"。
- **A2**：修订一版简报不变（常量 import 自 core.models；协议 v2 §3.2/§3.3 逐字）。
- **A3**：修订一版简报不变（SIMILARITY/MERGE 增句以协议 v2 §3.4 逐字为正本）。
- **A4**：修订一版简报不变（`_script` 锚点、G2 JSON fake、SEGMENT 强臂 success=true、注入标记存活例）。
- **B1**：修订一版简报 + ①测试 fixture 用 29 组真实形状（另加 62 组压力口径）；②无组 skill 自成一类用例；③docstring 含裁决 10 预期管理句与裁决 8 披露句。
- **B2**：修订一版简报 + ①stage6 一次性解析映射、`_check_stored` 与执行两路共读（裁决 3）；②补"per-task 续跑 stored 单元比对"用例；③三个既有 namespace 测试不改一字保持绿；④空注入 `skills_injected=[]`、audit 覆盖式写出。
- **C**：修订一版简报 + ①同一性校验 = 白名单 diff（裁决 4）；②配置复制 = 全键（裁决 6）；③stage0/manifest/summary 恒含三键（裁决 7）；④ASSEMBLED_FROM 按 11+5 两组与 5 条点名（裁决 1）；⑤负清单增补（裁决 5）。
- **D**：修订一版简报 + README 第二节 run 目录布局补 `retrieval_audit.jsonl` 与 `ASSEMBLED_FROM.md`；落地后对照代码复核为集成门显式步骤。

### 3.9 跨代理契约（逐字钉死，最终版）

1. `core/models.py`：`PROCEDURE_MARKERS = ("[When to use]", "[Steps]", "[Verify]", "[Do not use when]")`；`FAILURE_REFERENCE_MARKERS = ("[Trigger]", "[Why it fails]", "[Do instead]", "[Do not use when]")`。
2. `skill_delivery`：`{mode: "retrieval"|"whole_bank", top_k: int≥1, max_categories: int≥1, min_score: 0≤float<1}`，缺省 whole_bank；retrieval 时三参数必填。
3. `split_tasks(tasks, mode, split_spec) -> (train, test, reserved)`；键集恰为 `{"train","test"}` 或 `{"train","test","reserved"}`；**两键时 reserved 恒为 []；stage0 summary 与 manifest split 恒含三键**；调用点 = stage0_freeze.py（C）+ build_skillopt_split_configs.py（A1）。
4. `select_skills` / `derive_categories` 签名与返回形状（§2.1）；`routed` = 类别名列表；检索文档 = skill 全文（标记节 ×3 / 无标记 ×1）；组画像 = 成员检索文档拼接；无组 skill 自成一类。
5. `batch_execute(..., skills_for_task: Optional[Callable[[Task], Tuple[Any, str]]] = None)`。
6. `assemble_reuse(source_run: Path, config_path: Path, run_dir: Path) -> Dict[str, Any]`；CLI `expedistill assemble-reuse`。
7. 冻结三列表 = AgentBrew JSON 原文（41/28/20，seed 42，revision 2fd12b8）；TB 配置规则名 v1 三条，ALFWorld v2 配置规则名 v2 两条。
8. 检索文本 = `Task.instruction`。
9. `blocks/__init__.py` 与 `composites/__init__.py` 由编排者独占（A2 报 skill_markers/render_skill_content/validate_skill_sections，B1 报 select_skills/derive_categories，C 报 assemble_reuse）。
10. 库过滤 = 全链口径、**42 条**；`skill_lineage_tasks(skill, subtasks_by_id)` 单一溯源函数（skill.subtask_id + merged_from 递归），库过滤与 skill 拷贝共用，C 以硬断言实现。
11. 阈值只门 top-1；IDF 刻度随库变——写入 select_skills docstring。
12. min_score 校准协议本体 = §0 裁决 8（编排者执行，不占代理）。

## 4. 编排者职责与启动序列

1. ~~W1 文档回写（修订三）~~ 已完成；~~二轮评审~~ 已收敛（本版即终稿）；log.md 设计定稿条目（派发前）；
2. **Wave 1**：A1 + D → **Wave 2**：A2、B1、C → **Wave 3**：A3、A4、B2（每波启动指令 = 两份决策文档 + 简报 + 通用义务 + 契约）；
3. 集成门：全量 pytest + ruff E9,F + test_layering；两个 __init__ 统一注册导出并改写陈旧注释；三个 namespace 语义测试核验未动；D 对照代码复核 README/AGENTS；log.md 实施条目；
4. TB 复用实验序列：装配 → §1.5 五道门（1→5）→ 168 局全量（infra 短板政策预注册）；
5. 后续可选：whole_bank 消融 run（蒸馏零成本）；
6. 知情记录：旧 run 拒续跑原因链 = prompt digest + skill_delivery + split 三键（裁决 14）。
