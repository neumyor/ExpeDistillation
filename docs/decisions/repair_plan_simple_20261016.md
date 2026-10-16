日期：2026-10-16
目的：按 execute_learn_module_boundaries_20261016.md 修复代码，建立七模块、实现相位驱动。
上位文档：docs/decisions/execute_learn_module_boundaries_20261016.md（模块边界定稿）

---

## 1. 当前状态

分支 `feature/multi-method`，HEAD `c6a9fb8`，工作区干净，2130 测试通过。

已完成：M1 对象字段、M2 交付驱动、M3 决策函数与报告读取器、M4.1 语义检查工具、M4.2 配置转换函数。

要完成：建立验收模块、相位驱动取代六个阶段、配置改成相位序列。

---

## 2. 代码里的问题

| 编号 | 问题 | 位置 |
|---|---|---|
| G1 | 没有验收模块，判定分散在五处 | `benchmarks/tb/plugin.py`、`batch_execute.py:354`、`exam_once.py`、`acceptance.py:290`、`live_acceptance.py` |
| G2 | 执行模块自己下结论 | `base.py:53` 的三值状态无法表达"对错未知" |
| G3 | 比较写在执行阶段内部 | `stage6_test.py:195` 的 delta 计算 |
| G4 | 执行阶段直接改技能库 | `live_acceptance.py` 五处 `discard_skill` |
| G5 | 五个交付驱动签名不统一 | `methods/delivery/` 下五个文件 |
| G6 | 三种报告形状 | `reports.py` 导出三个函数 |
| G7 | 找证据没有驱动 | `methods/evidence/` 只有参数类 |
| G8 | 写作没有驱动 | `methods/author/` 只有参数类 |
| G9 | 技能库不按相位产生 | 阶段 5 整理、阶段 6 隐含读取 |
| G10 | 相位语义写死、注册表两套键 | `pipeline.py:61`、`registry.py:45` |
| G11 | 配置是五个槽位 | `config.py:82` 的 METHOD_SLOTS |

---

## 3. 修复项

依赖关系：R0 前置，R1/R2/R3/R5/R6/R7 并行，R2b 串行，R8/R9/R10 顺序执行。

```
R0 → R1/R2/R3/R5/R6/R7 → R2b → R8 → R9 → R10
      ↓
      R4（Wave 2 验证）
```

### R0 冻结接口形状

新建 `core/contracts.py`，定义四个数据类：

1. **运行记录**：`unit_id`、`task_ref`、`skills_injected`、`skills_block`、`correctness: Optional[bool]`、`metrics`、`aux`、`artifacts`
2. **验收报告行**：`unit_id`、`phase_label`、`role`、`injected_skills`、`runs`、`valid_count`、`success_count`、`status`、`cause`、`artifacts`
3. **执行单元行**：`unit_id`、`task_ref`、`skills_injected`、`skills_block`、`aux`
4. **任务清单行**：`target_kind`、`target_id`、`action`、`reason`、`basis_unit`

把 `live_acceptance.py`、`exam_once.py`、`acceptance.py` 三个文件按职责机械拆分成九个小文件。

验收：产物逐字节无差异，测试全通过。

### R1 验收模块成立（消除 G1、G6）

新建 `composites/verdicts.py`，统一做判定。产出一份验收报告，形状见 R0。判定规则按 benchmark 具名。

验收：现有的失败、泄题、预算耗尽、内容未变四类场景测试通过；新增丢弃分支场景测试通过。

### R2 执行去判定（消除 G2）

执行只写 `correctness`（benchmark 自带判分器结果）与 `metrics`（重试次数、报错、耗时）。判卷摘出归 R1。

验收：执行模块单测不出现成功失败结论。

### R2b 运行状态词汇迁移

把三值状态词汇改成 `correctness: Optional[bool]` 加运行指标。基础设施失败改为抛出 `InfrastructureError` 异常。

验收：测试全通过，轨迹产物字节差异逐处披露。

### R3 决策统一与比较归位（消除 G3）

`decision.py` 合并成一个入口：`(本轮报告, 对照报告, 当前技能库, 后面还剩几次检验) → 任务清单`。比较规则是参数，默认 `strict_rate_gain`。

实现边界定稿 §4.3 三条配套规则：没变不重测、改写失败原样保留、对照全对跳过带技能运行。

验收：边界定稿 §4 表格逐行有单测；决策入口拒绝 C 相位报告。

### R4 副作用归位（消除 G4）

执行相位不得调用 `discard_skill`。

验收：新增测试断言"一个执行相位结束后技能库不变"。在 Wave 2 统一验收。

### R5 交付接口统一（消除 G5）

五个驱动统一签名：`resolve(bank, tasks, params) -> ExecutionUnitList`。

Wave 1 只冻结新签名、写兼容层，Wave 2 替换调用点。

验收：五个驱动同一签名；golden 重生成后 retrieval_audit 不变。

### R6 找证据与写作驱动（消除 G7、G8）

找证据升为真实驱动，G10 改为只报告事实。写作驱动就位，接收任务清单与证据包。

验收：G10 输出不含保留、修改、删除动作；改写预算耗尽场景测试通过。

### R7 整理与逐相位技能库（消除 G9）

每个学习相位结束产生一份技能库产物。整理有两种：`agentbrew`（带 merge_check）与 `none`（原样入库）。

验收：逐相位技能库落盘；E1 的最终技能库行多重集不变。

### R8 相位语义与注册表换键（消除 G10）

相位角色由配置显式声明。注册表只保留五族：delivery、execution、evidence、author、curation。

验收：注册表条目从 13 项降到五族；`test_layering` 通过。

### R9 配置 v4、相位驱动、命令行界面（消除 G11）

配置用 `sequence` 与 `phases`。执行相位字段：`kind: execute`、`tasks`、`delivery`、`execution`、`compare`、`acceptance`。学习相位字段：`kind: learn`、`evidence`、`author`、`curate`。

新建 `stages/phase_execute.py` 与 `stages/phase_learn.py` 取代 `stage1` 到 `stage6`。

`--until-phase N` 取代 `--until-stage`。

验收：E5 的 8 组合乘以 3 种续跑变体全部等价；7 份旧配置转换后人工审定通过。

### R10 跟随者与 golden 重新生成

`assemble_reuse.py` 支持 v3 源；`audit_live_run.py` 读相位目录；README、AGENTS.md、runbook 同步。

golden 全量重生成，E1–E6 对归档基线全等。

验收：E1–E6 全等，差异逐处披露。

---

## 4. 多智能体并行拆分

### Wave 0 冻结（串行，1 个代理）

R0 的两个提交。判据见 R0。

### Wave 1 七模块成立（并行，6 个代理）

| 代理 | 修复项 | 独占文件 |
|---|---|---|
| W1-A | R1、R4 验收侧 | 新建 `verdicts.py`、判定部分、判卷部分、`reports.py`、`shortfall.py`、`semantics.py` |
| W1-B | R2 | 作答部分、测量部分、`batch_execute.py`、三个 benchmark 插件 |
| W1-C | R3、R4 决策侧 | `decision.py`、比较函数、`skill_status.py`、技能状态判定、提交助手、`stage6_test.py` |
| W1-D | R5 | `methods/delivery/` 五个驱动、兼容层 |
| W1-E | R6 | `methods/evidence/`、`methods/author/`、归因部分、`attribution.py` |
| W1-F | R7 | `curation.py`、技能库产物写入 |

合并顺序：W1-A 先合并，其余按完成顺序串行合并。

判据：测试全通过，E1–E6 对基线等价（E6 的 attributions 部分披露差异）。

### Wave 1B 词汇迁移（串行，1 个代理）

R2b 全部内容，一次提交。

判据：测试全通过，轨迹产物字节差异逐处披露。

### Wave 2 接线（并行，3 个代理）

| 代理 | 修复项 | 独占文件 |
|---|---|---|
| W2-A | R8 相位语义、`phase_execute` | `phase_execute.py`、删除 `stage6_test.py`、`stage1_baseline.py`、`stage2_segmentation.py`、`stage3_generation.py` |
| W2-B | `phase_learn` | `phase_learn.py`、`stage4_acceptance.py`、`stage5_curation.py` |
| W2-C | R9 | `config.py`、`registry.py`、`params.py`、`cli.py`、删除 `pipeline.py` |

开工前 W2-C 冻结 v4 配置解析产物并广播。

统一验证 R4 不变量、R1 重派、R7 跨相位加载。

判据：E5 的 8 组合乘以 3 种续跑变体全等；7 份配置转换结果人工审定。

### Wave 3 跟随者（并行，3 个代理）

| 代理 | 独占文件 |
|---|---|
| W3-A | `assemble_reuse.py` |
| W3-B | `scripts/audit/`、`scripts/run/` |
| W3-C | README、AGENTS.md、runbook、`convert_v3_configs.py` |

### Wave 4 收尾（串行，1 个代理）

golden 全量重生成，E1–E6 对归档基线全等验收，追加 `log.md` 条目。

---

## 5. 硬约束

1. 九层向下依赖，`test_layering` 全程通过。
2. 五个对象仓保持 run 级共享、目录名不变。
3. 跳过键与现行续跑键一致。
4. 异常分类与恢复机制语义不变。
5. E1–E6 对归档基线保持等价，或逐处披露差异。
6. 绝不触碰 `../ExpeDistillation-TB-dev` 和任何 `runs/` 目录。
7. 提交需要用户同意，推送需要用户明确要求。
8. 所有预检是纯报告，不阻塞流程。
9. 不引入依赖图、调度引擎、插件自动发现、决策规则注册表。

---

## 6. 评审与提交纪律

1. Wave 0 两次提交；Wave 1 每代理一次（六个提交）；Wave 1B 一次；Wave 2 三次；Wave 3 三次；Wave 4 一次。
2. 每波次结束后三路评审（A 正确性、B 简洁、C 续跑）。
3. `test_layering` 与 ruff 通过后才提交。
4. 修复全部完成前不启动任何正式 run。
