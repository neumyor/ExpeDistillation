# 决定：P04/P06 按触发窗口归因，P02/exam 保持旧证据包

日期：2026-10-23
一句话：把 P04/P06 的 group 归因改成“统一 skill_use 判断触发点 + 按窗口归因”；folder 按 cat 调用切前后窗口，group/whole bank 只取 attempt 开头和结尾窗口；一条 skill 得到一个或多个窗口证据，writer 自己综合。
对应 log.md：2026-10-23 19:42 条目。

## 背景

旧的 group 归因一次看整组技能和整段轨迹，既容易把其他技能的行为算到某条 skill 上，
又需要裁切/分块来适配上下文。用户要求：

- 只修改真正 trigger 过的 skill；
- folder 以实际 `cat` 为 trigger；group/whole bank 的 injected skill 视为都 trigger；
- 用真实 attempt 的上下文，而不是整段轨迹硬塞；
- 避免重复归因；
- 归因模块对 task_level / skill_level 两种决策通用；
- P02 add 和 exam 保持旧 EvidencePackage 行为；
- 把“skill 是否在 attempt 被使用、在哪里被使用”集中成一个工具函数。

## 决定

1. **`composites/skill_use.py` 是唯一判断入口**
   - folder / folder_by_task：解析轨迹里的每个 `cat`，记录全部 invocation；
   - group_by_task / whole_bank / per_skill / retrieval：injected 即触发；
   - `skill_used_in_attempt` 返回 `used` / `reason` / `invocations`。
   `skill_usage.py` 也改用它解析读取事实。

2. **folder 窗口**
   - 每个 invocation 取前后各 10 步（`AttributionParams` 可调）；
   - 同一 attempt 中同一窗口内多条 skill 合并一次归因调用；
   - 同一 skill 多次调用产生多个 invocation 窗口。

3. **group/whole bank 窗口**
   - 没有真实 cat 时间点；
   - 每个 attempt 只取开头窗口和结尾窗口，中间轨迹忽略；
   - 所有 injected 的 revise skill 共享窗口，合并一次归因。

4. **证据形状**
   `SkillAttemptEvidence(skill_id, skill_version, windows)`；每个
   `WindowEvidence` 记录 task、attempt、success、window kind、step range、
   trigger steps，以及归因模型输出的 trigger_context /
   behavior_after_trigger / improvement。旧的 `EvidencePackage` 只留给
   P02 add 和 exam。

5. **task instruction**
   - P03 的 TB 插件在 `_finish` 把 raw trajectory 的 step 0 message 存进
     `Trajectory.meta["initial_prompt"]`；
   - 归因窗口从这条 raw message 里只取 `Task Description:` 段，不把
     Terminal agent 的 `analysis/plan/commands` 输出格式和
     `<available_skills>` 列表一起带进 attribution prompt；
   - 缺少该段时回退到 `_task_instruction`。

6. **写作**
   - `methods/author/revise.py` 只接收 `SkillAttemptEvidence`；
   - 多窗口/多 attempt 的 X 由写作模型自己综合；
   - 不再有 retrigger 子模式；task_level 未 trigger 的成员输出 keep，
     不进入 writer。

7. **P02/exam**
   - P02 继续 `learn_add` + `segmentation` + `add_path` + `write_new`；
   - exam 继续 `learn_exam` + `exam_sheet` + `write.revise_group`；
   - 这两条路径不读新证据包。

## 删除/替换

- 删除 `methods/attribution/group.py`。
- 删除 `LIVE_USAGE_ATTRIBUTION` / `LIVE_SKILL_REVISION` /
  `LIVE_SKILL_RETRIGGER`，新增 `SKILL_ATTEMPT_ATTRIBUTION` /
  `SKILL_ATTEMPT_REVISION`。
- 删除 `attribute_usage` / `revise_skill` / `retrigger_skill`。
- `skill_reads.py` 并入 `skill_use.py`。

## 实施补充（2026-10-23 首次正式 P03/P04）

- **folder alias 归一化**：模型常把 folder 的目录名
  （`traj-tb-...-skill`）或 `dir/SKILL.md` 当成 skill_id。`attribute_attempts`
  接收 `skill_aliases`，把目录名/文件名归一化到完整 skill_id；
  同一技能用两种形式重复返回时保留第一条。
- **窗口外条目忽略**：`task_instruction` 曾带整个任务的
  `<available_skills>` 列表，模型会返回其他窗口的技能。attribution 现在
  忽略不在当前窗口 `skills` 集合里的条目；当前窗口的期望技能仍必须
  全部覆盖，缺一个才报 `missing skills`。
- **业务校验重试**：`call_model` 只重试 JSON 解析失败；`skills: null`
  或终端 agent 的 `analysis/plan/commands` JSON 属于业务契约失败，
  `attribute_attempts` 现在对整次调用重试 2 次。
- **首次正式 P04 pass 的历史**：上述三个问题导致 30 个任务组在
  `SKILL_ATTEMPT_ATTRIBUTION` 上失败，105 条 revise 未落地，61 条 delete
  已落地。失败 pass 的账本保留在
  `runs/tb21_p03p04_attempt_20261023_2025/abandoned/p04_learn_attribution_20261023_2258/`；
  修复后续跑得到 105/105 revised、61 skipped_terminal、0 failed。

## 影响

- P04/P06 的归因调用从“每 task 一次”变成“每个窗口一次”；
  同一窗口内多 skill 合并。
- group/whole bank 的中间轨迹不进入归因，这是有意的质量取舍。
- 旧 run 没有 `initial_prompt`，新 run 才有完整 task description；
  缺失时回退到 `_task_instruction`。
- 新 prompt/证据形状需要新 run 和 golden 重生成。
