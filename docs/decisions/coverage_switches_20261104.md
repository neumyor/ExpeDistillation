日期：2026-11-04
一句话：把 P02 的材料覆盖（轨迹、段）做成两个具名规则开关，默认行为不变；任务门槛仍由弱模型失败决定。
对应 log.md：2026-11-04「P02 材料覆盖开关」条目

## 背景

用户希望写作者看到更多学习材料。当前 P02 的漏斗是三层选择：

1. 任务：`task_select: any_weak_failed`，只收弱模型至少失败过一次的训练题；
2. 轨迹：`trajectory_select: strong1_weak1`，每道题最多 1 条成功强轨迹 + 1 条失败弱轨迹；
3. 段：`segment_select: strong_success_weak_failed`，强段只留段级成功，弱段只留段级失败。

最终 K=5 run 的实际漏斗是 39 题 → 20 题 → 40 条轨迹 → 121 个段 → 80 个段进入写作。

## 候选方案

- **方案 A：三层全部放开**（任务、轨迹、段都取全量）。任务门槛消失后，exam 的
  判定语义会变：当前配置 `bare_control: false`，exam 只要求"有成功即 improved"，
  弱模型本来就 3/3 全对的题也会通过验收，库从"修复失败的技能"变成"与成功一致的
  技能"。同时 cluster 的 anchor 可能落在已解任务上，混合簇会被 anchor 的命运决定。
  用户明确不要这个方案。
- **方案 B：只放开轨迹和段，保留任务门槛**。库仍然只覆盖弱模型失败的区域，
  exam 的 gain-based 判定语义不变；写作者看到的是这些失败题上的全部轨迹、全部段。
  **采用这个方案。**

## 实现

两个开关都是 `blocks/rules.py` 的具名规则，按实验配置选择，不是改默认值：

- `trajectory_select: all` 早已注册（返回该任务全部非 0 步轨迹，两臂混排）；
- 新增 `segment_select: all`（`_all_segments`，恒真）。默认仍是
  `strong_success_weak_failed` / `strong1_weak1`，参考臂逐字节不变。

配套的语义修正：`collect_add_evidence` 的写作模式改为跟**段自己的 success**
走（distill = 段达成，diagnose = 段失败），而不是跟模型角色走。默认规则下
两者等价（强段只留成功、弱段只留失败）；覆盖规则收进来的失败强段 / 成功弱段
因此不会撞 `write_skill` 的 mode 校验，也不会让诊断材料被当成提炼材料。

配置生成器给 `Arm` 增加可选的 `rules` 覆盖，新增 `e5_allcover_k{5,3,1}`
三个臂（`segment_select: all` + `trajectory_select: all`，任务门槛不动）。
`--check` 0 problems；矩阵 80 → 89 个配置。

## 影响

- P03–P07 的代码不需要改。变化是规模与成本：轨迹 40 → 234，段 121 → 约 700，
  exam 单元 = 技能数，估计 20 → 150–200；学习侧调用约 20–30% 的总量增量，
  终测执行调用不变。
- 测试面的主要风险是库变大后 catalog 变长，而统一协议仍只允许每局打开
  3 张不同的卡；覆盖臂的测试分数可能因此承压。
- 覆盖臂与参考臂的差异只有 `rules.segment_select` / `rules.trajectory_select`
  （K=3/1 的臂再多一个 `phases.learn_add.review.K`），可作干净的对照。

## 不做什么

- 不改 `task_select`，不新增 `task_select: all`；
- 不改 exam 的 `bare_control` / 比较规则 / cluster anchor；
- 不改执行协议、prompt、profile；
- 不改已启动 run 用过的 v3 配置。
