日期：2026-10-15
一句话说明：执行—学习闭环重构的具体实施方案（M0–M4）：数据对象字段、模块接口与注册表键集、配置 schema v4 完整形状、报告读取器与决策规则清单、失败处置表、标签与语义等价准则、各步验收标准、跟随者清单与多代理分工。
对应 log.md 条目：2026-10-15 执行—学习闭环实施方案（第一轮三路评审后）。
上位文档：docs/decisions/execute_learn_loop_20261015.md（v2，含裁决记录 §10）。

---

## 1. 结论与范围

1. 本方案把 scope 方案 v2 落到字段与接口级，是 M1–M4 的实施正本。两份文档冲突时以本方案为准并回改。
2. 总原则（用户约束）：**简洁、可扩展、避免 over-engineering**——不加新 hash、不加影响执行流程的硬检查；**完整保留**既有异常分类（reliability.py 四类）与恢复机制（逐单元落盘、按确定 ID 跳过、账本续跑、shortfall、runlock）。
3. golden 基线：提交 69904f7 的 tests/fixtures/golden/（v3 语义基线，M4 验收的对照物）。

## 2. 数据对象变更（M1）

| 对象 | 变更 | 版本 |
|---|---|---|
| Skill | **加 `source_subtasks: List[str]`**。段生技能 = `[自己的 subtask_id]`；合并产物 = 各来源 `source_subtasks` 的并集（写作时算好存入，读取方不再递归推导）。其余字段（version、merged_from、group、rewrite_history…）不变 | SKILL_SCHEMA_VERSION 1→2 |
| Exam | **加 `skill_id: str`**（考题直接挂技能；`exam_id` 派生规则不变，消费方改读字段） | EXAM_SCHEMA_VERSION 1→2 |
| Subtask / Trajectory / Attempt | 不变 | 不变 |

旧读新写规则（AGENTS 七.3：显式迁移，不静默默认）：

- `from_dict` 遇 v1 Skill：`source_subtasks` 由 ID 规则显式推导——段生（`<subtask>/skill`）取后缀去除；合并产物沿 `merged_from` 递归推导（各来源也是 v1 时同法）。推导不出（未知 ID 形状）→ 响亮报错。
- v1 Exam：`skill_id` 由 `exam_id` 换后缀推导（`<subtask>/exam` → `<subtask>/skill`）。
- 装配器读 v3 源 run 时走同一映射（§7）。

不改的判定：`is_stage3_candidate` 仍按 ID 模式（`core/ids.py` 不动判定逻辑）；`make_skill_id` / `make_exam_id` / `make_merged_skill_id` / `make_trajectory_id` 全部不变。

## 3. 模块接口与注册表键集（M2/M3 抽取，M4 接线）

**注册表只有一张**（D4）：沿用 `methods/registry.py` 的纯数据表机制（(family, name) → (驱动, Params, check)，懒 import，`build_params` 通用校验）。键集从五槽位换成**模块族**：

| family | 已注册 name | 驱动内容 | 来源 |
|---|---|---|---|
| delivery | `none` / `whole_bank` / `retrieval` / `per_skill` / `group_by_task` | 输入 (bank, tasks) → 注入规格（K1 的 `skills_injected`/`skills_block` 标量或 `skills_for_task` 解析器）+ 可选审计行 | whole_bank/retrieval 现有；none/per_skill/group_by_task 从 stage6 空臂、K3 单技能注入块、live `_injection_resolver` 抽出 |
| execution | `real` / `exam` | real：K1 批量执行（arms、n_attempts 是参数）；exam：K3 作答判卷环 + 出题 + 泄题审计（**出题/泄题函数从 blocks 搬进驱动编排，M2 只搬函数不搬调用点**） | K1 不动；exam 编排从 K4/stage3 抽 |
| learn | `default`（暂只有一个驱动） | L 相位驱动：决策分发 + 找证据 + 写作 + （末位 L）整理 | 从 stage2/3/5 + K4/K6 的决策半边抽 |
| curate | `agentbrew` / `none` | 现有驱动；agentbrew 加 `merge_check: exam\|none` 参数（默认 exam = 现行为） | 现有 |

**五槽位逐文件处置**（D4，B-1）：

- 留：`methods/registry.py`（换键集）、`methods/params.py`、`methods/delivery/{whole_bank,retrieval}.py`、`methods/curation/{agentbrew,none}.py`。
- 改：`methods/acceptance/{exam,live_run}.py` → 测量归 `execution/exam` 与 `execution/real` 的测量核，轮序/再派发/shortfall 归相位驱动，动作分配归 `learn`；`methods/authoring/__init__.py` 的 exams 开关与 `check_authoring` 删除（考题归 A，序列长度表达轮数）；`methods/segmentation/__init__.py` 的 Params 并入 `learn` 的 evidence 参数。
- 删（M4 一次性）：`core/config.py` 的 `METHOD_DEFAULTS`、`_validate_methods`、`METHOD_SLOTS`；`methods/acceptance/` 整目录；stage1–6 的固定编排（`pipeline.py` 的 STAGE_NAMES 与 if 链）。

**相位驱动**（stages 层，M4）：`phases/execute.py`（一次 E 相位实例：解析 delivery+execution → 跑 → 测量 → shortfall/审计 → summary）、`phases/learn.py`（一次 L 相位实例：读报告 → 决策 → 证据/写作/整理 → summary）、`phases/freeze.py`（现 stage0：加载/划分/冻结/aux 预注册，独立于序列，永远是 p00）。**相位目录**：`<run>/phases/pNN_<name>/`；K1 工作目录、轮仓、账本都在相位目录下，用常量声明。

**测量核去轮化**（D5，B-4）：K6 的测量函数签名与轮数、live 目录名解耦——输入 = 轮仓 store + 基线计数 + 具名规则 + 冻结审计行；轮序、再派发集、`rounds=(0,1)`、`LIVE_DIR_NAME` 归 live 序列的相位编排。M2 完成后，"检索交付 + 真执行测量"= 0 新模块 + 1 份相位配置。

**报告读取器**（D3，B-2）：`composites/reports.py`，纯函数、只读、**不落盘不建新对象**：

| 读取器 | 读什么 | 产出（内存 dict 行） |
|---|---|---|
| baseline_report | D 相位的轨迹仓 + shortfall | 每任务 {task_id, 各 role 成功数/总数}；F 集由 task_select 规则派生 |
| exam_report | attempts（K3 仓）+ leak_verdicts 账本 + 技能终态 | 每技能 {skill_id, version, bare_verdict, leak_verdict, 本相位 attempt(verdict, judge_reason), 改写结果(exhausted 计数), terminal} |
| live_report | verdicts / attributions / injection_audit 账本 + 轮仓 | 每任务组 {task_id, members(版本), baseline, 本轮结果, verdict, attribution 计划, 注入块 sha, 与上轮冻结行字节等价?} |
| test_report | C 相位双臂仓 + shortfall | 测试指标 + delta（+ aux） |

**决策规则清单**（D10，行为保持的完备清单；实现在 learn 驱动内，按报告种类分发，**不建决策注册表**——只有用户可选的规则才具名）：

- baseline 报告（首个 L）：task_select 命中的任务 → 动作=add；evidence 用 trajectory_select + segment_select 挑轨迹与段。
- exam 报告：裸答对 → delete(weak_already_knows)；本版答对 → keep(accepted)；出题/审计不可用 → delete(model_output_error)；泄露且还有后续 A → revise（作废注记入改写描述，本轮已消耗）；泄露且最终 A → delete(leak_audit)；答错且还有后续 A → revise(judge_reason)；答错且最终 A → delete(retry_exhausted)；改写预算耗尽 → 版本不变、轮已消耗、下个 A 重答同版。
- live 报告：任务组提升 → keep；未提升且还有后续 B → G10 逐技能计划（keep/revise/delete，delete 落 live_revision_deleted）→ 按计划改写；未提升且最终 B → delete(live_no_improvement)；注入块与冻结审计行字节相同 → 不跑下一轮直接淘汰（no-op guard）；基线全成功 → 零执行终态 weak_already_knows；注入超限 → live_injection_overflow；全洞 → live_undetermined。
- "还有后续验证轮" = 冻结序列中该 L 之后**还有几个 learn 相位**（末位 L = 0）；不数 C（终评不给学习反馈）。
- 所有技能库变更只经既有提交 helper（`discard_skill` 等），终态先写、账本行是回执的次序不变；**不新增 actions 账本**。

**修订路径随报告种类**：exam → G6（blocks/author.rewrite_skill）；live → G10 归因 + G11 修订（blocks/attribution）。不新增配置项。

## 4. 配置 schema v4（M4）

**全局字段不变**：benchmark、mode、split、tasks、models、model_options、token_budgets、rules{task_select, segment_select, trajectory_select}、n_attempts_strong、n_attempts_weak、weak_profile、prompts_digests、schema_version: **4**。

**删除**：methods 五槽位整块（含 authoring.exams、acceptance、delivery、curation、segmentation）。

**新增**：

```yaml
sequence: [D, L, B, L, B, L, C]     # 相位局部名列表；序列即身份，进 manifest
phases:
  D: {kind: execute, tasks: train, delivery: {name: none},
      execution: {name: real, arms: [executor_strong, executor_weak]}}
  A: {kind: execute, delivery: {name: per_skill}, execution: {name: exam}}
  B: {kind: execute, delivery: {name: group_by_task},
      execution: {name: real, n_attempts: 3, max_injection_tokens: null},
      compare: {against: D, rule: strict_rate_gain}}
  C: {kind: execute, tasks: test, delivery: {name: retrieval, top_k: 5, ...},
      execution: {name: real}, compare: {control: fresh_bare}}
  L: {kind: learn, evidence: {name: segment, K: 5, retry_limit: 2},
      author: {name: distill_diagnose},
      curate: {name: agentbrew, merge_check: none, token_budget: 96000, rounds: 3}}
```

字段表（执行相位）：`tasks: train|test`（缺省 train）；`delivery`（嵌套参数）；`execution: {name: real|exam}`——real 可带 `arms`（缺省 `[executor_weak]`；D 显式写双臂）、`n_attempts`（缺省 n_attempts_weak）、`max_injection_tokens`；exam 无额外参数；`compare` 可选——`{against: <序列中的早期相位名>, rule: <具名规则>}`（B 形）或 `{control: fresh_bare|reuse_d_weak}`（C 形，split→fresh_bare、full→reuse_d_weak，与现行 mode 决定一致，加载期校验）。D、A 无 compare（D 是基线；A 内生裸对照）。

字段表（学习相位）：`evidence: {name: segment, K, retry_limit}`（add 路径用；修订路径的证据是 G10，随报告种类走）；`author: {name: distill_diagnose}`；`curate`（agentbrew 带 `merge_check`/`token_budget`/`rounds`，或 none 带 `token_budget`）——curate 只在末位 L 执行（驱动按序列位置分支，配置不重复）。

**加载期检查**（沿用集中校验先例，全部 ConfigError）：sequence 非空且名字都在 phases；compare.against 引用序列中更早出现的相位；real+compare 的 `n_attempts ≤ n_attempts_weak`（check_live_run 的推广）；`merge_check: exam` 要求序列含 exam 执行相位；control 与 mode 匹配；roles 有效。

**upgrade_v3_config**（纯函数，照 upgrade_v2 先例；消费者 = 转换器 + 装配器身份比对）：

| v3 | v4 |
|---|---|
| acceptance: exam, retry_limit=R | sequence = [D, L] + [A, L]×(R+1) + [C]；L.curate.merge_check = exam |
| acceptance: live_run | sequence = [D, L, B, L, B, L, C]；L.curate.merge_check = none |
| acceptance 的 n_attempts / improvement / max_injection_tokens | B.execution / B.compare.rule |
| delivery 槽（whole_bank/retrieval + 参数） | C.delivery |
| curation 槽（name/参数） | L.curate（同参数） |
| segmentation 的 K / retry_limit | L.evidence |
| authoring.exams: true | 删除（隐含）；exams:false 仅当 acceptance=live_run 可转（否则报错），live 序列本就无 A，结果一致 |
| rules / 预算 / 角色表 / weak_profile / mode / split | 原样 |

转换器 `scripts/run/convert_v3_configs.py`：7 份 `*_v3.yaml` → `*_v4.yaml`（新文件名，不覆盖）；`load_config` 拒 v3 并指向转换器；stage0 拒 v3 manifest 并指向维护分支规则（两处拒载提示泛化，`config.py:629` 与 `stage0_freeze.py:297` 一并改）。

## 5. 失败处置表（C5，语义逐条保持）

| 情形 | 现行 | 新框架 |
|---|---|---|
| 出题（G3）格式预算耗尽 | stage3 单元终态失败，技能不入库，resume 不再试 | L 正常入库技能；A 的出题单元终态失败（单元键 = skill_id，不重试），报告行 untestable → 决策 delete(model_output_error)。**最终库相同**（E2 有映射） |
| 泄题审计预算耗尽 | 技能 discarded(model_output_error) | 测量行 untestable → 决策 delete(model_output_error) |
| 改写（G6）预算耗尽 | REWRITE_FAILURE_LEDGER 记 exhausted 行，版本不变，本轮已消耗，下轮重答同版 | 同一账本随改写搬进 L 相位目录；A 重答键含相位序号，语义等价 |
| 基础设施错误 | 单元留 pending，阶段末抛错（exit 3 可续跑） | 不变（相位末抛错） |
| shortfall | 逐相位 durable 记录；B 读 D 弱臂洞；live 轮 1 不继承轮 0 洞；full 的 C 对照臂洞在 D 侧接受 | 不变（§3 决策清单已含） |
| 代码 bug | 立即停整批 | 不变 |

## 6. 标签、账本与 CLI（C9，D7）

- pipeline_log 行标签：`p00:freeze`，之后 `pNN:<局部名>`（如 p01:D）。**旧 stage 标签的消费者全部同提交更新**：cli.summary 完备性检查改从 manifest 冻结序列推导（更严：比硬编码 STAGE_NAMES 少一个漂移源）；audit_live_run 改读相位目录与 `pNN` 标签；runbook 的标签对照表同步。映射表（旧→新）写入 M4 提交说明。
- `--until-phase N`（相位索引）取代 `--until-stage`；每次启动仍从 p00 重放、按确定 ID 逐单元跳过（语义同现行：stage0 恒跑 + until 上界）。
- exit code 语义（0/3 等）与阶段末抛错语义不变。

## 7. 跟随者清单（M4 内完成，不留尾巴）

| 跟随者 | 改动 |
|---|---|
| `stages/assemble_reuse.py` | **支持 v3 源**（D2）：身份比对经 upgrade_v3_config；拷贝清单/负清单改相位目录（v3 源按旧 KIND_DIRS 直读）；lineage 走 subtask_id 兜底（ID 未变，可用）；v2 源维持现行 upgrade_v2 路径不动 |
| `scripts/audit/audit_live_run.py` | 相位目录 + pNN 标签 + D 相位 shortfall 路径；自检通过 |
| `cli.py` | until_phase、summary 完备性从冻结序列推导 |
| README §1–§3、AGENTS §四/§五/§六/§七、runbook | 模块表、目录树、配置节、标签对照同步（AGENTS 五：改设计顺序 README→docs→代码→log） |
| golden | 全量重生成（v4 布局）；v3 fixture 树留在 git 历史（69904f7）作语义基线 |
| 假模型脚本 | M4 的语义等价用调用多重集断言，golden 重生成时按新调用顺序重写脚本（披露） |

## 8. 语义等价准则（预注册，M4 验收仪器）

**归一化**：现行 4 类 volatile 键 + run_dir 路径 + 布局映射（旧 stage 目录名 → 相位目录；pipeline_log 旧标签 → pNN）+ 对象新字段剔除。**不比**各阶段 summary 分桶、中间落盘点、调用顺序。

| # | 必须相等 | 说明 |
|---|---|---|
| E1 | 最终技能库行多重集：(skill_id, content, final_version, group, merged_from) | ID 未变，近逐字节 |
| E2 | 逐技能/逐组终态判定与 discard_reason | 映射：v3"未出生"（stage3 出题失败整段作废）↔ v4 discarded(model_output_error)；K4 内部丢弃 ↔ L 相位丢弃（理由同名） |
| E3 | C 相位测试指标 + aux 逐字段（对旧 stage6 summary） | 含 delta、control_source、两臂口径 |
| E4 | 模型调用多重集：(role, prompt 名, 归一化请求变量) | exam 相等；live 恰少"出题"族调用（披露项①）+ 无回归考（披露项②） |
| E5 | v4 内 8 组合 × 3 崩溃续跑变体全部等价 | 沿用 harness 的 assert_resume_equivalent 语义 |

**live 差异白名单**（除此之外任何差异 = 验收失败）：① 无出题/泄题/回归考调用；② merge_check: none 下合并不被撤销。

## 9. 迁移步骤（每步一个代理实现，步后三路验收评审）

| 步骤 | 内容 | 测试与验收 |
|---|---|---|
| **M1** | §2 全部：Skill/Exam 加字段、v1→v2 显式映射纯函数、schema 升版、全消费方写新字段、golden 同提交重生成 | pytest 全绿；golden diff 逐处披露（仅新字段）；映射函数单测（含推导不出报错） |
| **M2** | 执行侧抽取：delivery 三新驱动（none/per_skill/group_by_task，stage6 与 live 改调它们）；K6 测量核去轮化（live_run.py 持轮序）；gen_exam/audit_leak 函数搬进 execution/exam 编排（stage3 调用点不动）；测量核签名定型 | pytest 全绿；**golden 逐字节无 diff**；test_acceptance_resume / test_live_acceptance_resume 原样通过 |
| **M3** | 学习侧抽取：learn 驱动（决策分发 + 证据/写作/整理接线，stage2/3/5 与 K4/K6 决策半边改调它）；报告读取器（§3 表）；agentbrew 加 merge_check（缺省 exam = 现行为）；改写账本语义保持 | pytest 全绿；**golden 逐字节无 diff**；读取器单测（对既有账本夹具）；merge_check: none 单测（现造） |
| **M4** | §4 schema v4 + upgrade_v3 + 转换器与拒载；相位驱动取代 stage1–6（stage0→phases/freeze）；出题调用点挪进 A；相位目录布局；§6 标签/CLI；§7 跟随者全表；golden 重生成；E1–E5 验收 | pytest 全绿；E1–E5 对 69904f7 基线全等；live 差异恰为白名单两项；7 份 v4 配置加载 + 转换语义逐份人工审定留档 |

每步完成后启动三路评审（A 代码事实/行为等价、B 简洁与反过度设计、C 迁移/续跑），只审该步 diff + 验收证据；【偏移约束】修复后重审。

## 10. 多代理分工与纪律

- 实施代理按 M1→M2→M3→M4 串行（每步依赖前步）；每步一个主代理，测试随代码同提交。
- 代理硬约束：只在 ExpeDistillation-MM 工作；绝不触碰 ../ExpeDistillation-TB-dev 与任何 runs/ 目录；提交不推送；分层检查（test_layering）与 ruff 通过才提交；每步完成即写 log.md 条目。
- 评审代理只读（只写自己的评审文档），独立互不通气。

## 11. 风险清单

| 风险 | 缓解 |
|---|---|
| M4 过大（schema+驱动+跟随者+golden 一次换） | 每个子块独立提交；E1–E5 先行实现在 M4 内第一个提交落地，后续提交滚动验收 |
| exam 轮账目换相位序号后语义漂移 | E2 逐技能终态 + 专项单测（改写预算耗尽 → 同版重答）钉住 |
| 假模型脚本按位消费，调用顺序变化导致 M4 重写量大 | 脚本重写列入 M4 工作量；E4 用多重集断言不依赖顺序 |
| K4/K6 拆分引入两套真相 | 决策只经既有提交 helper；读取器不落盘；M2/M3 的 golden 无 diff 是硬闸 |
| 决策规则清单有遗漏（评审 A 的排查面） | M3 实现时以 §3 清单为验收基线，三路评审对照代码逐条核 |
