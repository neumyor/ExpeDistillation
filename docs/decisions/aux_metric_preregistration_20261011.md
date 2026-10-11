日期：2026-10-11
一句话：为 SearchQA 预登记一个辅助指标 sub_EM，由 benchmark 插件声明、阶段 0 冻结其名字、阶段 1 / 6 与主指标并列报告，永不互换或事后挑选。
对应 log.md 条目：2026-10-11 14:05（试点发现的修复：SearchQA 预登记辅助指标 sub_EM）。

## 背景

pilot 在 SearchQA 上看到：主指标 EM 过严——答案 "lumbar" 对金标
"lumbar vertebrae" 记 0，而按子串匹配（sub_EM，评测仓库已算好、随轨迹存为
`meta["searchqa"]["sub_em"]`）结论会翻转。如果允许"看完结果后再换口径"，
就重演外层 AGENTS.md 记录过的挑口径事故（35 次挑选、排序颠倒）。用户决定：
把 sub_EM 预登记为**辅助指标**，永远与主指标并列报告，不在看到结果后选择。

## 候选方案

1. 辅助指标写进 `ExperimentConfig`（配置字段）——被否：换口径会变成改配置，
   且配置改动只在新 run 生效，不利于"预登记"的严肃性；
2. `blocks/metrics.py` 里写死 SearchQA 分支——被否：违反分层规则
   （`blocks/` 不得出现 benchmark 分支）；
3. benchmark 插件契约里加一个可选的辅助指标声明，核心保持 benchmark 无关
   ——采纳。

## 选定方案（合约）

- **插件契约**（`benchmarks/base.py`）：类属性 `aux_metric_name`（默认 None，
  即该 benchmark 没有辅助指标）+ 一个可选方法
  `aux_success(traj) -> Optional[bool]`（默认返回 None = 未声明）。
  SearchQA 插件声明 `aux_metric_name = "sub_em"`，`aux_success` 读
  `traj.meta["searchqa"]["sub_em"] >= 1.0`。
- **预登记**：阶段 0 把插件声明的名字冻结进 manifest 顶层
  `"aux_metric": "sub_em" | null`（不是 ExperimentConfig 字段）。
  改声明 = 冻结协议变化，旧 run 目录续跑被拒。
- **D3 / 阶段 1 / 阶段 6**：声明了辅助指标时，每个臂在同一套应有单元分母上
  另算辅助 avg@N（条件口径 + 保守口径，与主指标同一对口径），
  summary 增加 `"aux": {"metric": ..., "avg_at_n": ..., "avg_at_n_conservative": ...}`
  一节；阶段 6 的 delta 增加同样的辅助 delta（null 规则与主指标一致）。
  未声明时 `aux` 为 null、打分器不运行、零额外工作。
  缺失 / 基础设施单元不可能算辅助成功（与主指标同分母）。
- **主指标不动**：恒为 EM / 状态口径，辅助指标只并列，不替换、不挑选。

## 缺失 meta 的取舍（用户交由实现者决定并写明）

SearchQA 的评测 meta 只在模型提交过 `Finish[...]` 后存在。因此：

- 从未提交答案的有效失败轨迹（步预算耗尽 / 模型空回复）没有 meta：
  **sub_EM 记 0**——没有答案可匹配，这是真实的 0，不是缺数据；
- `status == "success"` 或终止原因为 `terminated`（一定提交过答案、被评过分）
  却没有 meta：**数据损坏**，抛 `CodeBugError` 停下——这里静默记 0 会系统性
  低估辅助指标，宁可让 run 停下。

## 已知局限

辅助指标的**打分器是代码不是配置**：改打分器不改变任何 prompt digest，所以
旧 run 目录不会因为打分器变化而拒绝续跑（不像 prompt / 任务集）。
跨打分器版本比较 run 时以代码历史为准；改打分器必须在 log.md 记录。
