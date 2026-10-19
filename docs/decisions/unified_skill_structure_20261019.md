日期：2026-10-19
一句话说明：做法类与教训类技能统一为同一套四段结构，类型由来源片段的 `Subtask.source` 表达、不另设字段；恢复教训类技能的产出，开关放在找证据参数里；下游不再按类型分支。
对应 log.md 条目：2026-10-19 技能结构统一与教训类产出恢复（用户裁决）。

---

## 1. 背景

- 系统有两类技能：做法类（强模型成功片段，`SKILL_DISTILL`，`[When to use]/[Steps]/[Verify]/[Do not use when]`）
  与教训类（弱模型失败片段，`SKILL_DIAGNOSE`，`[Trigger]/[Why it fails]/[Do instead]/[Do not use when]`）。
  类型没有字段，完全靠正文段落标记识别。
- 下游多处按类型硬分流：G7 只许同类归组、G8 前的类型门、合并后与改写后的标记一致检查、
  folder 目录与检索各自认两种"何时使用"段。
- 10-16 重构中教训类产出被丢掉，未经用户裁决：修复方案 R6 写成"新增时读取强模型轨迹"
  （边界定稿 §2.5 无此限制），`add_path` 只收强模型片段、写作模式写死 `distill`，
  切段只切强模型轨迹，旧第 3 阶段的 distill/diagnose 分流随之删除。此后新 run 不再产出教训类技能。

## 2. 裁决

1. **不新增类型字段。** 技能是做法还是教训，由来源片段的 `Subtask.source`（strong / weak）说明。
2. **统一为做法类的四段**：`[When to use]` / `[Steps]` / `[Verify]` / `[Do not use when]`。
   `SKILL_DIAGNOSE` 照旧从弱模型失败片段写作，但输出同样的四段：失败征兆写进 `when_to_use`，
   纠正做法写进 `steps`。
3. **旧库不迁移。** 技能会重新生成。旧的 `[Trigger]` 结构技能仍能加载，但被视为不带四段的技能。
4. **恢复教训类产出，作为找证据（evidence `segment` 方法）的参数 `write_lessons`，默认 `true`。**
   - `true`：新增路径同时切强、弱两种轨迹，收集强模型成功段与弱模型失败段
     （哪些段留下仍由 `segment_select` 规则决定），按来源分别用 distill / diagnose 写作；
   - `false`：只切、只收强模型片段（10-16 之后的行为）。
5. **旧写作方式不再兼容，下游不再区分类型。** 删除 `FAILURE_REFERENCE_MARKERS`；
   G7 不再限制同类归组，G8 前的类型门删除，合并与改写后的检查改为"带齐四段"；
   folder 目录与检索只认 `[When to use]`。

## 3. 影响

- 提示词改动：`SKILL_DIAGNOSE`、`REWRITE`、`LIVE_SKILL_REVISION`、`SIMILARITY`、`MERGE`。
  按仓库规矩是新实验，旧 run 不能续跑。
- 旧库中的 `[Trigger]` 技能：folder 目录在缺摘要时显示占位符，改写与合并按"不带四段"处理。
