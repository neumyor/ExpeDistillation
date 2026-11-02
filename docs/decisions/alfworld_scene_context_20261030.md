日期：2026-10-30
一句话：真实 ALFWorld 冒烟确认房间描述没有随轨迹落盘，且长题目路径会把 answer sheet 文件名顶过 255 字节；两处都在正式论文 run 之前修掉。
对应 log.md：2026-10-30 「ALFWorld 真实冒烟与两处修复」条目

## 背景

ALFWorld 的初始观测（房间描述）只在 `plugin.run` 里被拼进执行者的指令：

```python
initial_observation = env.reset()
instruction = f"{task.instruction}\n\n{initial_observation}"
```

存储的轨迹步骤只有动作与之后的观测。写作者、考场场景、考试裁判都用
`attribution.task_instruction_for` 从任务表重建任务，而任务表里是原始题面（"look at
alarmclock under the desklamp"），没有房间描述。第一次真实 ALFWorld 冒烟确认了这一点：
存下的轨迹是 `go to desk 1` / `Task is SOLVED.` 五步，房间列表不在任何落盘位置。

后果不是"少一段文本"：考场在第 0 步切场景时，弱模型拿到的任务没有列出任何可去的容器，
必须盲探索；真实执行时它看到了完整房间。实验二至四都是 ALFWorld，这个错位会系统性地改变
触发条件的考试难度。

同一次冒烟在 baseline 写 answer sheet 时以 `OSError: [Errno 63] File name too long` 崩掉：
ALFWorld 的题目 id 是游戏路径（约 112 字符），answer sheet id 由
`unit_id + "::" + trajectory_id` 组成，路径在两边各出现一次，percent 编码后约 332 字节，
超过文件系统 255 字节的名字上限。

## 决定

1. **初始观测随执行记录落盘。** `ALFWorldPlugin.run` 把 `env.reset()` 的返回值写进
   `RunOutcome.meta["initial_observation"]`（经 answer sheet 成为 execution 的
   `metrics`）。`composites/trace.py::instruction_with_initial` 统一负责把它追加到重建的
   任务说明后面，且只在尚未包含时追加（防重复）。使用它的地方：
   - `trace.resolve`（考场场景的题目、写作者读到的场景）；
   - `methods/review/attribution.py::_attribute_window`（修订归因）；
   - `stages/judge_skills.py::_judge_trial`（适用性/遵循度裁判）。
   SearchQA 没有重置观测，`initial_observation` 缺省为空，行为不变。
2. **answer sheet id 不再重复轨迹 id。** `execution_from_trajectory` 的
   `sheet_id` 从 `f"{unit_id}::{trajectory.trajectory_id}"` 改为
   `f"{unit_id}::att{attempt_index:02d}"`。unit id 已含角色与注入形状，attempt 序号区分
   同一单元的多次采样，因此唯一性不变；完整轨迹 id 仍写在 `artifacts["trajectory_id"]`，
   `trace_of_trajectory` 等按工件查找的路径不受影响。`object_filename` 不变，旧 run 目录
   仍按旧文件名读取。

## 影响

- 只影响新 run。旧 run 的 answer sheet id 是旧格式，但读取按存下的 `TraceRef.sheet_id`
  精确查找，不重算，因此照常可读。
- 考场场景、写作者视图、归因、裁判的输入文本变了：ALFWorld 的旧 run（目前没有真实 run）
  不可与修复后比较；TB 与 SearchQA 不受影响（无 `initial_observation`，且文件名长度足够）。
- 黄金矩阵重生：只有 answer sheet id 与受它影响的请求文本、`estimated_in` 变化，调用数与
  崩溃点不变。

## 验证

- 单元测试：`instruction_with_initial` 的追加与幂等；`resolve` 还原初始观测；
  ALFWorld `run` 的 meta 携带房间描述；长题目 id 的 sheet 文件名（含临时后缀）不超过
  255 字节。
- 全量 `pytest tests`、`ruff`、`tests/test_layering.py`、`scan_private.py` 通过；
  `tests/golden` 重生后 10 项通过。
- 真实 ALFWorld 七相位冒烟（2 训练题 / 3 测试题，真实模型）复跑，确认 baseline 正常落盘。

## 已知边界

- `initial_observation` 是"环境在重置时返回的观测"，不是模型摘要；它原样进入场景说明，
  不做截断（ALFWorld 房间描述通常几百字符）。
- 该字段只由插件写入，不由 prompt 生成，因此不影响 prompt digest。

## 追加修复（2026-10-31 00:40）：gap sheet 文件名

实验二的 D→Q 臂在同一处又崩了一次：考场里一个"测不了"的单元要写 gap answer sheet，id 是
`gap:{unit_id}:{attempt}`。考场的 unit id 把 exam id 当 task_ref，exam id 又内嵌整条
trajectory id，percent 编码后同样超过 255 字节（实测约 332 + 临时后缀）。修复放在
`make_gap_sheet_id`：能放下的 id 保持原样（旧 run 与现有测试不变），放不下的改成
`gap:{unit_id 前 48 字符}:{attempt}:{sha256 前 16 位}`，仍然由 (unit, attempt) 唯一确定，
没有任何代码解析这个格式。实测失败的那个 unit 现在得到 71 字符的 id、编码后 107 字节。
单元测试覆盖长度、确定性与前缀相同的两个长 unit 不碰撞。

这次崩溃只发生在**有测不了单元的考场**里，所以主表 run 的 p03 考场（全部 23 个单元都有
判分）没有触发；实验二 D→Q 臂触发后修复，重跑 p03 通过并进入终测。
