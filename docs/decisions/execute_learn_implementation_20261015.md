日期：2026-10-15
一句话说明：执行—学习闭环重构的具体实施方案（M0–M4）v2：数据对象字段、模块接口与注册表键集、配置 schema v4 完整形状、报告读取器与决策规则清单、失败处置表、标签与语义等价准则、各步验收标准、跟随者清单与多代理分工；已并入第二轮三路评审（A 7 / B 6 / C 9 条偏移约束，裁决见 §12）。
对应 log.md 条目：2026-10-15 执行—学习闭环实施方案（两轮三路评审后定稿）。
上位文档：docs/decisions/execute_learn_loop_20261015.md（v2，裁决记录 §10）。

---

## 1. 结论与范围

1. 本方案是 M1–M4 的实施正本；与 scope 方案 v2 冲突时以本方案为准并回改。
2. 总原则（用户约束）：**简洁、可扩展、避免 over-engineering**——不加新 hash、不加影响执行流程的硬检查；**完整保留**既有异常分类（reliability.py 四类）与恢复机制（逐单元落盘、按确定 ID 跳过、账本续跑、shortfall、runlock）。
3. golden 语义基线：提交 69904f7 的 tests/fixtures/golden/（对象 schema v1 时代的全产物快照）。
4. 分层纪律（test_layering 全程红绿灯）：composites（5）不得 import methods（6）——决策逻辑落 composites 层、驱动落 methods 层、相位编排在 stages 层；methods 模块互不 import，注册表是唯一缝。

## 2. 数据对象变更（M1）

| 对象 | 变更 | 版本 |
|---|---|---|
| Skill | 加 `source_subtasks: List[str]`。段生技能 = `[自己的 subtask_id]`；合并产物 = 各来源 `source_subtasks` 的并集（**写作时算好存入**，读取方不递归推导）。其余字段不变 | SKILL_SCHEMA_VERSION 1→2 |
| Exam | 加 `skill_id: str`（考题直接挂技能；`exam_id` 派生规则不变，消费方改读字段） | EXAM_SCHEMA_VERSION 1→2 |
| Subtask / Trajectory / Attempt | 字段不变。**Attempt.retry_round 的语义在 v4 改为"A 相位序号"**（见 §3 考键），字段本身不升版 | 不变 |

旧读新写规则（AGENTS 七.3：显式迁移，不静默默认）：

- v1 Skill：段生（`<subtask>/skill`）从 ID 后缀推导 `source_subtasks`；**嵌套合并产物沿 `merged_from` 推导需要兄弟记录，`from_dict` 做不了——from_dict 对这类记录响亮报错**；装配器走**带 skills_by_id 的迁移纯函数**（core 层，输入 v1 行字典 + 同源技能字典，输出 v2 行）。
- v1 Exam：`skill_id` 由 `exam_id` 换后缀推导（`<subtask>/exam` → `<subtask>/skill`）。
- **装配器完整性比对同构化（I1，M1 必做）**：`assemble_reuse.py:1189` 的 `row != stored.to_dict()` 改为 `Skill.from_dict(row).to_dict() != stored.to_dict()`（两侧同构；否则 M1 后对**任何** v1 对象源 run 的每行 bank 误报"the source is corrupt"——今天全部正式 run 都是 v1）；`curation.py:945` 的补账按存储记录原样落盘（M1 实测：该处是补账写入而非比较，v1 前缀 + v1 补行自洽）。

**M1 验收裁决（2026-10-15，A 的 P-A1 与 C 的 P-C1/P-C2 同根因——"只有契约点名的两处接了迁移，全量加载型消费方全部没接"）**：

1. **装配产物 = v2 原生**（采评审 A 修法）：装配器对 skills/exams/bank 行**迁移后落盘**（其余 KIND 字节复制不变）；ASSEMBLED_FROM 披露对象行已迁移；消费方零改动。装配出的 run 端到端可用（阶段 4 枚举、阶段 5 短路、审计工具）。
2. **审计工具读 v1 run 经迁移**：`scripts/audit/` 是 AGENTS 六.7 标准流程工具，读取点单一；core 加批量包装 `migrate_skill_rows(rows)`（内部建 map、逐行 migrate，v2 恒等穿过）作共享缝。
3. **直接在 v1 run 上用新代码续跑不在支持范围**（AGENTS 六.1/R6：正式 run 固定在维护分支的旧代码上续跑）。对象升版有意不动 CONFIG_SCHEMA_VERSION——stage0 不拒载，**from_dict 对 v1 合并产物的响亮报错就是"走错 checkout"的发现机制**，报错信息同时指向 migrate_skill_record 与 R6 维护分支规则。不建 run 级迁移工具（无消费者，P5 教训；场景真出现时平凡可写）。
4. M1 的 v1 前缀补账测试保留（钉"按存储原样落盘"不变量，该不变量同样服务 v2 run），docstring 注明 v1 续跑场景本身属 R6 范围外。
5. 装配器迁移失败抛 ConfigError（与同处损坏源错误族一致，A/C 琐细）。

不改的判定：`is_stage3_candidate` 仍按 ID 模式；`make_skill_id` / `make_exam_id` / `make_merged_skill_id` / `make_trajectory_id` 不变；live 的多来源（合并产物）分组依据 = 候选 ID 判定本就排除 bank/merge 产物（`live_acceptance.py:400-426`），一句话写进 §3。

## 3. 模块接口与注册表键集（M2/M3 抽取，M4 接线）

**注册表只有一张**（D4）：沿用 methods/registry.py 的纯数据表机制（(family, name) → (驱动, Params, check)，懒 import，build_params 通用校验）。**键集为五个内容族**（B2-1；learn 不入表——相位驱动由序列驱动按 kind 直接 import，注册表只登记"可按名替换的做法"）：

| family | 已注册 name | 驱动内容 | 来源 |
|---|---|---|---|
| delivery | `none` / `whole_bank` / `retrieval` / `per_skill` / `group_by_task` | 输入 (bank 行, tasks, store 或相位上下文) → 注入规格（K1 的标量 `skills_injected`/`skills_block` 或 `skills_for_task` 解析器）+ 可选审计行。**输出形状随执行方式**：per_skill 的规格是 K3 单技能块（`format_skills_block([skill])`），group_by_task/retrieval 要读 store 按任务解析 | whole_bank/retrieval 现有；none/per_skill/group_by_task 从 stage6 空臂、K4 单技能注入、live `_injection_resolver` 抽出 |
| execution | `real` / `exam` | real：K1 批量执行编排（arms、n_attempts 是参数）；exam：出题→裸答→泄题审计→带技能作答的测量编排（**gen_exam/audit_leak 留在 blocks 当模型积木，M4 只挪调用点**——其调用方 K4 在第 5 层，搬进 methods 会撞分层） | K1 不动；exam 编排从 K4/stage3 抽 |
| evidence | `segment` | add 路径的证据：切段（G1+D7，K2），**输出仍是 Subtask 对象**（"不切段"扩展的第一步契约） | 现 segmentation 槽 |
| author | `distill_diagnose` | 新写技能（G2）+ 修订写作的编排（G6/G11 的调用随报告种类分发，见下） | 现 authoring 槽（exams 开关删除） |
| curate | `agentbrew` / `none` | 现有驱动；agentbrew 加 `merge_check: exam\|none`（缺省 exam = 现行为） | 现有 |

**五槽位逐文件处置**（D4）：

- 留：`methods/registry.py`（换族键）、`methods/params.py`、`methods/delivery/{whole_bank,retrieval}.py`、`methods/curation/{agentbrew,none}.py`。
- 改：`methods/acceptance/{exam,live_run}.py` → 测量编排归 execution 族、动作分配归 composites/decision（见下）、轮序/再派发/shortfall 归相位驱动；`methods/authoring/__init__.py` → author 族 Params（exams 开关与 check_authoring 删除）；`methods/segmentation/__init__.py` → evidence 族 Params（K/retry_limit 保留——这是**切段生成**的修复预算，与验收轮数无关，验收轮数才由序列长度表达）。
- 删（M4 原子核内）：`core/config.py` 的 METHOD_DEFAULTS/_validate_methods/METHOD_SLOTS；`methods/acceptance/` 整目录；stage1–6 固定编排（pipeline.py 的 STAGE_NAMES 与 if 链）。

**相位驱动**（stages 层，M4）：`stages/phase_execute.py`（一次 E 相位：解析 delivery+execution → 跑 → 测量 → shortfall/审计 → summary + 阶段末抛错）、`stages/phase_learn.py`（一次 L 相位：读报告 → 决策 → 证据/写作/整理 → summary）、`stage0_freeze.py` 沿用（p00，加载/划分/冻结/aux 预注册）。职责一句话（B2-1）：**stages 相位驱动拥有"何时做什么"（目录、次序、shortfall、summary、抛错），methods 注册表拥有"怎么做"（可替换的做法与参数契约）**。

**相位目录与对象布局**（P4 裁决，续跑语义优先）：

- **五个 KIND_DIRS 对象仓保持 run 级、全相位共享、目录名不变**（trajectories→stage1_baseline/ 等，`infra/store.py:29` 零改动）——exam_once 的序号按仓计数、K4/K5 跨相位 attempt 检索、"首判为准"全依赖单仓；v3 源直读因此免费成立。
- **跨相位共享的 exam 测量账本**：`leak_verdicts.jsonl` 留在 attempts 仓（stage4_acceptance/）不变——它的键是 (skill, version)、跨 A 相位恒等，必须单一真相（B2-4）；裸答/带技能作答本就是共享仓里的 Attempt。
- **相位目录** `<run>/phases/pNN_<name>/` 只放：相位 summary、逐相位账本（verdicts/attributions/injection_audit/rewrite_failures——改写失败账本随改写搬进 L 相位目录，键含相位，分片自洽）、B 的轮仓、C 的臂仓、K1 workdir、逐相位 accepted shortfall 记录。
- **A 的作答复用键 = (skill_id, version, retry_round=A 相位序号)**（T6：仓共享，键用相位序号消歧；字段语义升版写进 README §2，字段本身不变）。

**测量核去轮化**（D5，P5 收窄）：K6 的测量函数核签名与轮数、live 目录名解耦（**账本路径作参数传入**——judge_round 拥有 verdicts 的 append-once 与重算比对，s-8）；M2 采用**薄壳中间态**：对外签名与路径原样保留、核内部去轮化，`test_live_acceptance_resume` 原样通过；薄壳在 M4 删除。去轮化后"检索交付+真执行测量"= 0 新模块 + 1 份相位配置 + 至多 1 条新具名规则。

**报告读取器**（D3）：`composites/reports.py`，纯函数、只读、**不落盘不建新对象**。三个读取器（s-2：test_report 删除——C 恒为终评，其测量留相位驱动内联，E3 直接比 summary 文件）：

| 读取器 | 读什么 | 产出（内存 dict 行） |
|---|---|---|
| baseline_report | D 相位的轨迹仓 + shortfall | 每任务 {task_id, 各 role 成功数/总数}；F 集由 task_select 规则派生 |
| exam_report | 共享 attempts 仓 + leak_verdicts + 技能终态 | 每技能 {skill_id, version, bare_verdict, leak_verdict, 本相位 attempt(verdict, judge_reason, weak_answer, skill_version, retry_round, 来源 attempt 指针), 改写结果(exhausted 计数), terminal}（P7b：G6 改写需要 weak_answer 与来源选取规则——泄露轮取最近带技能 attempt，无则裸答） |
| live_report | verdicts / attributions / injection_audit 账本 + 轮仓 | 每任务组 {task_id, members(版本), baseline, 本轮结果, verdict, attribution 计划}。**不含"与上轮冻结行字节等价"字段**（P7a：no-op guard 的比较时点是修订落地后、渲染块对冻结行，由守卫自身在编排时点算，读取器在决策时点算恒为真） |

**决策规则清单**（D10，M3 验收基线；实现在 `composites/decision.py`（同层，I3），按报告种类分发，**不建决策注册表**）：

- baseline 报告（首个 L）：task_select 命中的任务 → add；evidence 用 trajectory_select + segment_select 挑轨迹与段。**add 幂等**：按 store.exists(skill_id) 跳过（L 相位 resume 重放不得重复入库，stage3 现语义平移）。
- exam 报告：裸答对 → delete(weak_already_knows)；本版答对 → keep(accepted)；**出题/作答/判卷/泄题审计任一测量预算耗尽** → delete(model_output_error)；泄露且还有后续验证轮 → revise（作废注记入改写描述，本轮已消耗）；泄露且无后续 → delete(leak_audit)；答错且还有后续 → revise(judge_reason + weak_answer)；答错且无后续 → delete(retry_exhausted)；改写预算耗尽 → 版本不变、轮已消耗、下个 A 重答同版。
- live 报告：任务组提升 → keep；未提升且还有后续验证轮 → G10 逐技能计划（keep/revise/delete，delete 落 live_revision_deleted）→ 按计划改写；未提升且无后续 → delete(live_no_improvement)；**注入块与冻结审计行字节相同（修订落地后时点）→ 不跑下一轮直接淘汰（no-op guard）**；基线全成功且规则属 strict 族 → 零执行终态 weak_already_knows；注入超限 → live_injection_overflow；全洞 → live_undetermined；**G10 预算耗尽 → 整组弃（终态先写、exhausted 行是回执）；G11 预算耗尽 → 仅该技能弃、组继续**（P2，discard_reason=model_output_error）。
- **"还有后续验证轮" = 该 L 之后还有几个 A/B 型执行相位**（不数 C、不数 learn 相位——P1/I8：按 learn 相位计数在合法序列 [D,L,L] 下会给出错误动作）。
- 所有技能库变更只经既有提交 helper（discard_skill 等），终态先写、账本行是回执的次序不变；**不新增 actions 账本**。
- 修订写作随报告种类：exam → G6（blocks/author.rewrite_skill）；live → G11（blocks/attribution.revise_skill），归因 G10 的计划即逐技能动作分配（决策定单元级动作，G10 把"修改"细化为逐技能计划——不建协调层）。

## 4. 配置 schema v4（M4）

**全局字段不变**：benchmark、mode、split、tasks、models、model_options、token_budgets、rules{task_select, segment_select, trajectory_select}、n_attempts_strong、n_attempts_weak、weak_profile、prompts_digests、schema_version: **4**。full 模式下 `tasks: test` 与 train 同集（语义注记，T3）。

**删除**：methods 五槽位整块。

**新增**：

```yaml
sequence: [D, L, B, L, B, L, C]
phases:
  D: {kind: execute, tasks: train, delivery: {name: none},
      execution: {name: real, arms: [executor_strong, executor_weak]}}
  A: {kind: execute, delivery: {name: per_skill}, execution: {name: exam}}
  B: {kind: execute, delivery: {name: group_by_task},
      execution: {name: real, n_attempts: 3, max_injection_tokens: null},
      compare: {against: D, rule: strict_rate_gain}}
  C: {kind: execute, tasks: test, delivery: {name: retrieval, top_k: 5},
      execution: {name: real}, compare: {against: fresh_bare}}
  L: {kind: learn, evidence: {name: segment, K: 5, retry_limit: 2},
      author: {name: distill_diagnose},
      curate: {name: agentbrew, merge_check: none, token_budget: 96000, rounds: 3}}
```

字段表：

- 执行相位：`tasks: train|test`（缺省 train）；`delivery`（嵌套参数）；`execution: {name: real|exam}`——real 可带 `arms`（缺省 `[executor_weak]`；**双臂相位（D）禁止携带 n_attempts**：两臂深度恒为全局 n_attempts_strong / n_attempts_weak，P3/B2-3）；单臂相位可带 `n_attempts`（缺省 n_attempts_weak）与 `max_injection_tokens`；`compare` 可选，**只有一种形状**（B2-2）：`{against: <序列中更早出现的相位名 | fresh_bare>, rule: <具名规则，可选>}`——B 形 `{against: D, rule: strict_rate_gain}`；C(full) `{against: D}`（复用 D 弱臂并继承其 accepted holes）；C(split) `{against: fresh_bare}`（本相位自跑裸对照臂；关键字唯一保留，不做"缺 compare 即 fresh"推断）。D、A 无 compare。
- 学习相位：`evidence: {name: segment, K, retry_limit}`（切段生成的修复预算）；`author: {name: distill_diagnose}`；`curate`（agentbrew 带 merge_check/token_budget/rounds，或 none 带 token_budget）——curate 只在末位 L 执行（驱动按序列位置分支）。

**加载期检查**（全部 ConfigError，加载即拒，不进运行时）：

1. sequence 非空且名字都在 phases；相位局部名字符集受限（进目录名与标签，s-5）；相位键集与 kind 匹配（unknown-key 响亮拒绝是仓库纪律，s-5）。
2. compare.against 引用序列中更早出现的相位名，或恰为 fresh_bare。
3. 单臂 real 相位的 `n_attempts ≤ compare.against 所指相位的弱臂深度`（该相位未带 n_attempts 时为全局 n_attempts_weak）——check_live_run 的推广（B2-3）。
4. `merge_check: exam` 要求序列含 exam 执行相位。
5. roles 有效（现行检查沿用）。

**upgrade_v3_config**（纯函数；消费者 = 转换器 + 装配器身份比对；v2→v3→v4 链式，拒载提示**三处**泛化：load_config、ExperimentConfig.from_dict 版本闸、stage0 manifest 闸，T1/T4）：

| v3 | v4 |
|---|---|
| acceptance: exam, retry_limit=R | sequence = [D, L] + [A, L]×(R+1) + [C]；L.curate.merge_check = exam |
| acceptance: live_run | sequence = [D, L, B, L, B, L, C]；L.curate.merge_check = none |
| acceptance 的 n_attempts / improvement / max_injection_tokens | B.execution.n_attempts / B.compare.rule / B.execution.max_injection_tokens |
| delivery 槽 | C.delivery（同名同参） |
| curation 槽 | L.curate（同参数 + merge_check 按验收方式） |
| segmentation 的 K / retry_limit | L.evidence（切段修复预算，保留） |
| authoring.exams: true | 删除（隐含）；**exams: false 仅当 acceptance=live_run 且 curation=none 可转**（唯一合法消融组合，T2），live 序列本就无 A，结果一致 |
| rules / 预算 / 角色表 / weak_profile / mode / split | 原样 |

转换器 `scripts/run/convert_v3_configs.py`：4 份 `*_v3.yaml` + 3 份 `*_v2_v3.yaml`（s-6，别漏后者）→ `*_v4.yaml` 新文件名；v1/v2/v3 的 load_config 拒载均指向对应转换器与维护分支规则。

## 5. 失败处置表（C5+P2+T5，语义逐条保持）

| 情形 | 现行 | 新框架 |
|---|---|---|
| 写技能（G2）格式预算耗尽 | stage3 单元终态失败，技能不入库 | L 的写作单元终态失败，不入库，resume 不再试（语义平移） |
| 出题（G3）/ 作答（G4）/ 判卷（G5）/ 泄题审计任一耗尽 | G3：stage3 单元终态失败技能不入库；其余：K4 内 discarded(model_output_error) | G3：A 的出题单元终态失败（键 = skill_id，不重试），报告行 untestable → 决策 delete(model_output_error)（最终库相同，E2 有映射）；其余同左 |
| 泄题判定 | 每 (skill, version) 一次，先落盘后动作，首判为准，resume 永不重问 | 不变；**账本留 attempts 仓共享**（跨 A 相位单一真相，B2-4） |
| 改写（G6）预算耗尽 | REWRITE_FAILURE_LEDGER 记 exhausted 行，版本不变，本轮已消耗，下轮重答同版 | 同一账本随改写进 L 相位目录（键含相位，分片自洽）；下个 A 以 (skill, version, 新相位序号) 重答同版 |
| G10 归因耗尽 | 整组弃，终态先写、exhausted 行是回执 | 不变（决策清单已收） |
| G11 修订耗尽 | 仅该技能弃（model_output_error），组继续 | 不变 |
| 基础设施错误 | 单元留 pending，阶段末抛错（exit 3 可续跑） | 不变（相位末抛错） |
| shortfall | 逐相位 durable 记录；B 读 D 弱臂洞；live 轮 1 不继承轮 0 洞；full 的 C 对照臂洞在 D 侧接受 | 不变；空洞继承是**相位编排的再派发语义**（不是决策清单项，s-1/T6） |
| 代码 bug | 立即停整批 | 不变 |

## 6. 标签、账本与 CLI（C9/D7）

- pipeline_log 行：**key 仍为 "stage"**（消费者兼容），值 = `p00:freeze`、之后 `pNN:<局部名>`。旧 stage 标签消费者同提交更新：cli.summary 完备性从 manifest 冻结序列推导；audit_live_run 改读相位目录与 pNN 标签、**manifest 模式分派从 config.methods.acceptance 改为冻结序列判型**（T9）；runbook 标签对照表同步；映射表写入 M4.3 提交说明。
- `--until-phase N` 取代 `--until-stage`：N 是 sequence 的 0 基索引（freeze 恒跑）；越界校验在 load_config 之后做（T3：`_check_run_settings` 现在跑在配置可见之前，要挪）；每次启动从 p00 重放、按确定 ID 逐单元跳过。
- exit code 与阶段末抛错语义不变。

## 7. 跟随者清单（M4 内完成）

| 跟随者 | 改动 |
|---|---|
| `stages/assemble_reuse.py` | **v3 源支持**（D2+I7 逐点）：① 身份白名单 v4 拼法 `config.phases.<C>.delivery`（转换器固定局部名 D/L/A/B/C 使其可写死；`_section()` 同步）；② 版本链 v2→v3→v4（`==2` 泛化为 `< 当前版`，逐级 upgrade）；③ 读/写两侧路径映射清单（负清单、shortfall、generation_failures/segmentation_audit → 相位目录；对象仓不动）；④ kept_exams 过滤键改 `exam.skill_id`；⑤ **M1 的同构比对修复使 v1 源自 M1 起可用**（不等 M4） |
| `scripts/audit/audit_live_run.py` | 相位目录 + pNN 标签 + D 相位 shortfall 路径 + manifest 序列分派；自检通过 |
| `cli.py` | until_phase、summary 完备性从冻结序列推导 |
| `scripts/` 逐文件处置（B2-6） | 活工具随相位化改写：smoke_stage01_searchqa.py / smoke_stage01_alfworld.py（import 面）、build_skillopt_split_configs.py（产 v4）、fork_run_stage04.py（flag 改名）；旧 run 专用工具（tb21_f3_baseline.py、seed_tb_stage1.py）标注"v3 布局专用"保持可运行 |
| README §1–§3、AGENTS §四/§五/§六/§七、runbook | 模块表、目录树、配置节、标签对照、Attempt.retry_round 语义升版同步（README→docs→代码→log 顺序） |
| golden | 全量重生成（v4 布局）；v3 fixture 树留在 git 历史（69904f7）作语义基线 |
| 假模型脚本 | golden 重生成时按新调用顺序重写（E 准则用多重集断言，不依赖顺序） |

## 8. 语义等价准则（预注册，M4 验收仪器；M4.1 先行落地 + 自检）

**归一化**：现行 4 类 volatile 键 + run_dir 路径 + 布局映射（**仅逐相位账本/summary 的目录与标签**；对象仓不动故无映射）+ 对象新字段剔除。**不比**（文件级枚举，T12）：各相位 summary 的分桶与键名、中间落盘点、调用顺序、pipeline_log 的标签替换、workdir 树。

| # | 必须相等 | 说明 |
|---|---|---|
| E1 | 最终技能库行：(skill_id, content, final_version, status, discard_reason, group, merged_from, **rewrite_history 逐条**) 多重集 | I5：恢复第一轮 C2 预注册的全字段 |
| E2 | 逐技能/逐组终态判定与 discard_reason | 映射：v3"未出生"↔ v4 discarded(model_output_error)；K4 内部丢弃 ↔ L 相位丢弃（理由同名）。**覆盖面声明（P6）**：金夹具全为"全接受零弃"，全部弃用分支在 golden 上恒空真——分支钉住由 M4 验收承担：以 tests/composites/test_acceptance.py 与 test_live_acceptance.py 的现有失败/泄题/耗尽/no-op 场景为基准，v4 代码在同样场景断言逐技能终态、discard_reason、账本行与 v3 语义一致 |
| E3 | C 相位测试 summary 逐字段（with_bank/bare/delta/aux/control_source）+ **retrieval_audit.jsonl 审计行**（I4） | 对旧 stage6 summary |
| E4 | 模型调用多重集：**(role, prompt 名, 渲染请求文本)**（T9：fixture 里只有渲染 messages） | exam 相等；live 见白名单 |
| E5 | v4 内 8 组合 × 3 崩溃续跑变体全部等价 | 沿用 assert_resume_equivalent 语义；崩溃续跑变体必须含"修订已落库、重答未发生"窗口（评审 C-1） |
| E6 | **live 测量账本逐行**（I4）：verdicts 行多重集（round 字段 → 相位序号映射）+ attribution 计划行 | 防"判据计数漂移但 better 布尔不变"绕过 |

**live 白名单**（可自动判定，I6/s-4）：① v4 live 调用多重集**恰等于** v3 live 删光全部 (examiner, EXAM_GEN) 调用（金夹具实测 live v3 组合有 6/3 次该调用），其余任何差 = 失败；② merge_check: none 不撤销合并在金矩阵**不可观测**（假模型 G7 恒空、无合并无回归），由 M3 的 merge_check:none 单测 + 真实 run 披露承担。D 相位 shortfall 在矩阵不可达，由续跑套件与真实 run 审计钉住。

## 9. 迁移步骤（每步一个实现代理，步后三路验收评审）

| 步骤 | 内容 | 测试与验收 |
|---|---|---|
| **M1** | §2 全部：字段、v1→v2 迁移纯函数（含嵌套合并的 skills_by_id 版）、**装配器/curation 同构比对修复（I1）**、schema 升版、全消费方写新字段、golden 同提交重生成 | pytest 全绿；golden diff 逐处披露（新字段 + 对象 schema 1→2，T13）；**v1 对象源 run 的 bank 级装配夹具测试通过**（I1 验收）；迁移函数单测（含推导不出报错） |
| **M2** | 执行侧抽取：delivery 三新驱动（none/per_skill/group_by_task）入注册表并被 stage6/live 经注册表缝调用（methods 互不 import，T8）；K6 测量核去轮化**薄壳中间态**（对外签名与路径原样，P5）；测量核签名带账本路径（s-8）。**gen_exam/audit_leak 不搬**（留 blocks，M4 只挪调用点，I2/s-3） | pytest 全绿；**golden 逐字节无 diff**（注意 summary dict 构造序保持——write_json 不排序键，T8-A）；test_acceptance_resume / test_live_acceptance_resume **原样通过** |
| **M3** | 学习侧抽取：`composites/decision.py`（决策分发，同层合法，I3；accept_skill / K6 函数对外签名不变）；报告读取器三件（§3 表）；agentbrew 加 merge_check（缺省 exam）；evidence/author 族的 Params 就位（注册表加族，配置接线等 M4） | pytest 全绿；**golden 逐字节无 diff**；**两套崩溃续跑套件原样通过**（T11）；merge_check: none 单测（现造）；读取器对既有账本夹具的单测 |
| **M4** | 子序列（I9，原子核不可拆）：**M4.1** 仪器先行（E1–E6 断言函数 + 归一化表 + 69904f7 基线加载器 + 自检：基线对自身全等 + tamper 反证）；**M4.2** upgrade_v3_config 纯函数 + 7 份配置期望形状单测（不翻版本号）；**M4.3** 原子核（schema v4 + 注册表换族键 + 相位驱动取代 stage1–6 + 相位目录/标签 + 出题调用点挪 A + harness write_config v4 + golden 重生成——E1–E6 在此对基线全等验收）；**M4.4** cli + audit_live_run；**M4.5** 装配器 v3 源；**M4.6** 文档 + 转换器实录 + scripts/ 处置 | 每子块提交测试全绿；M4.3 后金矩阵持续无 diff（M4.4–M4.6 不改 run 产物）；E1–E6 全等 + live 白名单①机判；E2 分支钉住场景测试全过；7 份 v4 配置加载 + 转换语义逐份人工审定留档 |

每步完成后启动三路评审（A 正确性/行为等价、B 简洁/反过度设计、C 迁移/续跑），只审该步 diff + 验收证据；【偏移约束】修复后重审。

## 10. 多代理分工与纪律

- 实现代理按 M1→M2→M3→M4 串行（M4 内按 M4.1–M4.6 子序列）；每步一个主代理，测试随代码同提交。
- 代理硬约束：只在 ExpeDistillation-MM 工作；绝不触碰 ../ExpeDistillation-TB-dev 与任何 runs/ 目录；提交不推送；test_layering 与 ruff 通过才提交；每步完成写 log.md。
- 评审代理只读（只写自己的评审文档），独立互不通气。

## 11. 风险清单

| 风险 | 缓解 |
|---|---|
| M4.3 原子核过大（schema+键+驱动+harness+golden 互锁不可拆） | I9 子序列：仪器与 upgrade_v3 先行独立提交；M4.4–M4.6 不改产物、矩阵持续无 diff 兜底 |
| exam 轮账目换相位序号后语义漂移 | E2 场景钉住 + 改写耗尽专项单测（同版重答） |
| 假模型脚本按位消费，M4.3 重写量大 | E4 用多重集断言；脚本重写列入 M4.3 工作量 |
| K4/K6 拆分引入两套真相 | 决策只经既有提交 helper；读取器不落盘；M2/M3 golden 无 diff 硬闸 |
| 对象仓布局误改导致续跑断裂 | §3 布局裁决明文（对象仓 run 级共享不动）+ test_layering/续跑套件把关 |
| 决策规则清单有遗漏 | §3 清单是 M3 验收基线，三路评审对照代码逐条核（第二轮已核：P2 补 G10/G11 耗尽） |

## 12. 第二轮三路评审裁决记录（v2）

评审文档：docs/reviews/execute_learn_impl_review_20261015_{A,B,C}.md（A 7 条 P1–P7 / B 6 条 B2-1–B2-6 / C 9 条 I1–I9）。逐条落实：

- **P1=I8**："后续验证轮"改数 A/B 型执行相位（§3）。
- **P2**：决策清单补 G10 整组弃 / G11 单技能弃 + 次序；exam"任一测量预算耗尽"（§3、§5）。
- **P3=B2-3**：双臂相位禁携 n_attempts、检查对照所引相位弱臂深度（§4）。
- **P4 与 B2-5 冲突，采 P4**：对象仓 run 级共享、目录名不变（B 的相位级仓会断 exam_once 序号、跨相位 attempt 检索与首判语义——A 的续跑证据胜出；B 的"必须裁决"诉求由本裁决满足）；B2-4 泄题账本随之为"留在 attempts 仓共享"。
- **P5**：M2 去轮化改薄壳中间态，测试原样通过，薄壳 M4 删。
- **P6=I6**：E2 加覆盖面声明 + 场景钉住进 M4 验收；live 白名单①机判化、②转单测。
- **P7**：live_report 删字节等价字段（守卫时点注记）；exam_report 补 weak_answer/来源指针。
- **I1**：装配器同构比对修复进 M1 + v1 源夹具测试。
- **I2=s-3**：gen_exam/audit_leak 留 blocks，M4 只挪调用点。
- **I3**：决策落 composites/decision.py，methods 薄壳，accept_skill/K6 签名不变。
- **I4=I5**：E6 新增、E1/E3/E4 补全（rewrite_history/status/discard_reason、retrieval_audit、渲染请求文本）。
- **I7**：装配器五点逐项进 §7。
- **I8**：见 P1。
- **I9**：M4 子序列 M4.1–M4.6。
- **B2-1**：注册表五内容族，learn 不入表，相位驱动按 kind import。
- **B2-2**：compare 统一 against 形，删 mode 复写与匹配检查。
- **B2-6**：scripts/ 逐文件处置进 §7。
- 琐细全部并入对应小节：s-1/s-2/s-4/s-5/s-6/s-7/s-8/s-9/s-10、T1–T13（文中已逐处标注）。
