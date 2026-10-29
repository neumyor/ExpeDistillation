# 决定：来源指针与写作上下文统一为 TraceRef

日期：2026-10-29
一句话：用一个精确的 `TraceRef`（一张执行答卷 + 一个步号）记录“技能版本从哪里来、考题从哪里切”，并让所有写作类 prompt 的轨迹文本都经 `resolve()` 从同一个指针还原。
对应 log.md：2026-10-29 的“来源指针与写作上下文重构”条目。

## 背景

两次事故暴露了同一类缺陷：

1. **指针有歧义**：来源里存 `unit_id`，而同一 task+role 的三个 attempt 共用一个
   unit，读取端靠优先级猜；旧实现取 `matches[-1]`，37 个技能的场景全部取自
   attempt 02，锚点却在 attempt 00/01（0/37 命中），另有 4 道题切点越界。
2. **写作拿不到真实上下文**：
   - exam 路径的改写（`REWRITE`）只拿到题目、答卷、判词，看不到真实轨迹；
   - live 路径的窗口（`WindowEvidence`）只有分析模型的转述，没有原始步骤；
   - exam 模式下的需求技能把**考试答卷**当来源，下一次考试就是“考试的考试”：
     切点前为空，任务说明只能退回答卷记录的初始提示。

只修读取端的优先级（上一次的修复）能解决 P02 的 0/37，但不能保证写作时有上下文，
也不能避免需求技能指向纸面答卷。

## 候选方案

1. 继续在读取端加规则（锚点优先、sheet 优先……）：规则越叠越多，每个写入点
   仍各自拼字段，一致性靠读者猜。**不采用**。
2. 在每个写入点补 `sheet_id`：能修 P02，但不改变“上下文仍是各自拼的摘要”这一
   事实，也防不住考试答卷被当来源。**不采用**。
3. 统一指针类型 + 唯一构造函数 + 唯一还原函数 + 唯一写入口（**采用**）。

## 决定

### 数据

- `TraceRef(phase_label, sheet_id, step)`：`sheet_id` 必填，不存 unit/task/attempt。
- `Provenance(skill_id, version, phase_label, cause, scene, sources, note)`：
  每个技能版本一条；`scene` 为 `TraceRef | None`（合并产物为 None），
  `sources` 是全部证据位置（审计用）。
- `TraceContext(ref, task_ref, task_instruction, before, after, outcome)`：只由
  `resolve()` 产出。
- `Exam.source_ref` → `Exam.scene: TraceRef`，schema v3 → v4；旧记录不迁移，`from_dict`
  明确拒载。

### 函数（`composites/trace.py`）

- `trace_at(sheet, step)`：唯一构造函数；参数是执行答卷对象；校验步号范围；拒绝考试答卷。
- `resolve(run_dir, ref)`：唯一还原函数；精确查找，缺失或越界报错。
- `scene_of(exam)`：返回 `exam.scene`。
- `ledger.record_version(skill, provenance)`：唯一写入口，保存技能并追加 provenance，
  拒绝重复。

### 写入点取值

| 写入者 | scene |
|---|---|
| P02 逐段 / 按簇 | 锚点轨迹答卷在段起点 |
| live 改写 | 窗口自己的 `ref`（同一个 attempt） |
| exam 改写 | `scene_of(被考的 exam)`（原切点，P03→P05 逐题配对） |
| 需求 | 第一个来源的真实位置；来源是考试答卷时经 `scene_of` 回溯 |
| 合并 | None |

### 还原点（写作 prompt 的输入）

| 调用 | 变化 |
|---|---|
| `EXAM_SCENE` | 前缀/后续来自 `resolve(Provenance.scene)` |
| `REWRITE`（exam 改写） | 新增 `SOURCE CONTEXT` = `render_scenes([exam.scene])` |
| `SKILL_ATTEMPT_REVISION`（live 改写） | 每个窗口附 `render_scenes([window.ref])` 的真实步骤 |
| `REQUIREMENT_SKILL` | 来源用 `render_scenes(全部来源)` 的结果，不再是纸面答卷 |
| `EXAM_TRIGGER/PLAN/JUDGE`、`TRIAL_EXAM_ANALYSIS` | 不变（纸面考试不给轨迹） |

这三个 prompt 的改动会改变 prompt digest，属于新实验身份。

### 检查

每个 learn 相位结束、每个 exam 相位开考前：每个版本有 Provenance；`scene` 可解析；
`scene` 不是考试答卷；P02 版本的 `scene` 轨迹等于锚点轨迹。审计脚本做同一项检查。

## 删除

- `_source_sheet` 的三级优先级与锚点轨迹规则；
- `_fallback_origin` 及其扫描 phases 目录；
- `source_ref()` / `source_ref_for_unit()` 与 `skill_origins.jsonl`；
- `learn_apply`/`learn_exam` 里“unit 的第一张答卷 + 另一窗口步号”的拼装。

## 兼容

不保留任何旧版本兼容：

- `Exam` schema v3 → v4，配置 schema 同步升版；`Exam.from_dict` 只读当前版本，stage 0 的
  manifest 比对拒绝旧 run。旧 run 按仓库规则固定在原 checkout 上。
- `skill_origins.jsonl`、`source_ref()`、`source_ref_for_unit()`、`find_origin()`、
  `_source_sheet`、`_fallback_origin` 全部删除，不提供迁移脚本。需要复用旧 run 的前缀
  （如 P01/P02）时，在新代码下重跑 P02，让它写出 `provenance.jsonl`，而不是转换旧文件。
- 未决项（已采纳默认值，实施时可调整）：live 窗口的 `TraceRef.step` 取触发步，渲染区间
  取窗口范围；需求有多个来源时 `scene` 取第一个。

## 写作端的长度控制（2026-10-29 补充）

首次实测（2053 run 的 37 个 P02 场景、22 条需求）发现：写作 prompt 比重构前大
约 9 倍，最大一条需求 444K 字符（约 127K tokens），高于该 run 里成功发送过的
最大输入（86K tokens）。原因有两个：一条轨迹被切成多段、各写成一个技能、各有
一道考题，同一个需求引用这些考题时，同一条轨迹的重叠窗口被渲染多次（22 条
需求里 10 条重复，51 个来源只对应 39 条不同轨迹）；前缀没有上限。

采用两项修改，都在写作端，出题路径不变：

- `render_scenes` 按 `sheet_id` 合并窗口，同一次执行只渲染一次；
- 写作端前缀限 `WRITER_BEFORE_STEPS = 15` 步；`resolve` 的 `before_steps` 默认
  `None`，`EXAM_SCENE` 不传，仍取完整前缀，P03 → P05 的逐题配对不受影响
  （37/37 个场景的出题文本与修改前逐字一致）。

结果：需求 prompt 中位 225K → 129K、最大 444K → 256K；REWRITE 场景最大
304K → 226K；没有任何一条超过已验证体量。前缀 15 步这个值来自对 5–30 步的
对比，选它是因为再缩短只减少前缀，对剩下的长尾（编译日志）没有帮助。
未验证：完整上下文对改写质量的提升，以及端点的实际输入上限。

## 后果

- 之后不再有“不知道这条技能/考题来自哪里”的问题，P02 的 0/37 类错误会在 P02 结束时
  被拦住，零模型调用。
- 写作 prompt 变长（REWRITE 加真实上下文；多来源的需求），需要验证端点长度限制。
- 重构前所有 run 的 exam 相关数字（P03–P07）不能与重构后的 run 比较。
