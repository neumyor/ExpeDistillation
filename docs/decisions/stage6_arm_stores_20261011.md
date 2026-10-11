日期：2026-10-11
一句话：阶段 6 的带库臂与对照臂各用独立 Store，因为轨迹 ID 不携带"臂"信息，共享 store 会被续跑机制串臂。
对应 log.md 条目：2026-10-11 09:53（实现 README 蓝图：全部七层代码 + 测试落盘）；本文的更正见 2026-10-11 11:44 条目。

背景：轨迹 ID 是 `traj/<task>/<role>/<attempt>`，不含实验臂。阶段 1 弱模型裸跑、
阶段 6 带库臂、阶段 6 对照裸跑臂在同一任务上的 ID 完全相同。K1 的续跑检查按
"ID 已存储=已完成"跳过，因此共享一个 store 时第二臂会直接继承第一臂的轨迹，
算出假零提升——正是外层 AGENTS.md"对照必须真是对照"警告的事故模式。

决定与理由：
- 阶段 6 两臂分别用 `run_dir/stage6_test/bank/` 与 `stage6_test/bare/` 独立
  Store；ctx.store 保持为阶段 0–5 的主 store。
- full 模式的对照臂复用阶段 1 裸跑轨迹时做完整性检查：缺任何一条对照轨迹
  直接 ConfigError 报缺哪些 ID，不拿残缺对照算 delta。
- 摘要记录 control_source（stage1_reuse / fresh_bare_run），下游分析不用猜。

已知限制（留待 ID 方案修订时处理）：若给轨迹 ID 加实验臂成分（如
`traj/<task>/<role>/<arm>/<attempt>`），两臂可并回主 store；在那之前，任何
"扫描主 store 拿全部弱模型轨迹"的分析都看不到阶段 6 的两臂，需分别读臂 store。

附带：K1 决定 infra_error 的执行结果不落盘（已存储=已完成），只进
failed_units.jsonl，续跑才会重试它们。

## 更正（2026-10-11，文档同步）

上文"full 模式的对照臂复用阶段 1 裸跑轨迹时……缺任何一条对照轨迹直接 ConfigError"
已不成立。现行规则见 `stage6_metrics_denominator_20261011.md` 决定 4 与"更正"一节：

- 缺失的对照轨迹报 **InfrastructureError**（CLI 退出码 3，可续跑），而不是 ConfigError；
- 这个检查在执行带库臂**之前**做：重跑同一条命令会先续跑阶段 1 补洞，补上后 F 和库
  都可能变，先跑的带库轨迹可能对应错误的库；
- 阶段 1 已持久接受的空洞（`stage1_baseline/accepted_infra_shortfall.json`）不算缺失，
  按空洞计分；阶段 6 自己不接受对照空洞。

"两臂各用独立 Store"这一决定本身不变。
