日期：2026-11-04
一句话：P02 材料覆盖消融（全部轨迹 + 全部段，任务门槛不变）的预注册指标、对照与判定规则，在看到结果之前固定。
对应 log.md：2026-11-04「P02 材料覆盖开关」条目

## 假设

写作者看到的材料越多，写出的技能覆盖越完整、对未见题越有用。操纵变量是
`rules.trajectory_select` 与 `rules.segment_select`：参考臂用
`strong1_weak1` + `strong_success_weak_failed`，覆盖臂用 `all` + `all`。
两臂的任务门槛相同（`any_weak_failed`），执行协议、profile、prompt、
学习流程、终测对照全部相同。

覆盖臂同时跑 K=5 / K=3 / K=1，用来检查"材料覆盖"与"分段粒度"是否有交互；
K 的差异仍按实验三的预注册口径报告，不改变实验三的结论。

## 臂与对照

| 臂 | 配置 | 对照 |
|---|---|---|
| 覆盖 K=5 | `configs/experiments/alfworld_split_paper_v3_e5_allcover_k5.yaml` | 参考 K=5 `e1_ours` |
| 覆盖 K=3 | `..._e5_allcover_k3.yaml` | 参考 K=3 `e3_k3` |
| 覆盖 K=1 | `..._e5_allcover_k1.yaml` | 参考 K=1 `e3_k1` |

- 主比较：覆盖 K=5 对参考 K=5（同一最终 profile、同一份复用 baseline）。
- 次比较：覆盖 K=3 对参考 K=3；覆盖 K=1 对参考 K=1。
- 绝对对照：三个覆盖臂都对冻结裸 Q（`configs/seed/alfworld_bare_q_20261102.json`，
  avg@3 0.6269，sha256 已钉在配置里）。

## 预注册指标（先固定，后看结果）

1. **avg@3 / pass@3**：134 题 ×3，条件口径；`paper_tables.load_arm` 重算并与
   `phase_summary.json` 对齐，不一致即作废。
2. **配对差与 95% 区间**：按任务自助法；胜负平、McNemar 精确检验。
3. **机制指标**（`segmentation-metrics`）：初始库 / 最终库大小、初始库与最终库
   "不在任务开始适用"的比例、任务中途打开率、任意打开率。
4. **材料漏斗**：P02 的 add 任务数、切段轨迹数、stored subtask 数、
   `segment_select` 保留段数、cluster 数、初始技能数。用来确认开关确实生效
   （覆盖臂应接近"每题 6 条轨迹、全部段保留"，参考臂应维持 1 强 + 1 弱）。

不新增其他口径，不按结果改分母。无法解析 anchor 的技能单独报告，不计入比例。

## 判定规则

- 134×3 的最小可分辨差约 4.5 个百分点；差值小于它，或配对区间跨 0 时，
  只写方向，不写"有效"。
- 主比较（K=5 覆盖对参考）若区间不跨 0，才可写"覆盖带来提升/下降"。
- 机制指标与成功率分开陈述：材料漏斗和库组成是事实，成功率是效果。
- 覆盖变多也可能变差（卡片更长、catalog 更大、3 张卡的阅读预算被稀释）。
  这是预注册的竞争假设，结果不好照实写。

## 运行计划

1. 三个覆盖臂顺序或并行启动，每臂 `--workers` 不超过 32，避免互相拖慢；
2. 全部用 `assemble-reuse --level baseline` 从
   `runs/exp3_k5_final_20261103_2141` 复用 234 条 train 轨迹（117 强 + 117 弱），
   P02 起全新执行；baseline 相位的执行器、采样数与源 run 相同，规则差异
   不作用于 P01；
3. 每个 run 独立目录，一个代码版本；
4. 每个 run 完成后先做完整性审计（402 唯一轨迹、134×3、402 答卷、
   coverage 804/804、无 failed/skip、infra_missing 空、`load_arm` 重算一致），
   有问题写 `INVALID.md`，不进表；
5. 结果写进 `docs/reports/coverage_ablation_20261104.md`，并与实验三的三臂
   表并列。

## 不做什么

- 不放开任务门槛（`task_select` 保持 `any_weak_failed`）；
- 不改 exam 的 `bare_control`、比较规则、cluster anchor；
- 不为覆盖臂各跑一份裸对照（三臂共用同一份冻结裸 Q）；
- 不把覆盖臂与实验三的参考臂混成一张没有标注的表。
