日期：2026-10-15
一句话说明：P1b 评审 C 的偏移约束修复——方案 §7 P1b 验收行"全部 v2 配置升级后的 v3 manifest 人工审定一次"的逐份执行留痕（7 份 `configs/experiments/*_v3.yaml`）。
对应 log.md 条目：2026-10-15 20:10 P1b 评审修复轮。

## 审定方法

每份 v3 配置对照其 v2 源做了两层核对：(1) 结构层——v3 文件内容与 `upgrade_v2_config(v2源)` 的输出逐键相等（脚本比对，7/7 OK，角色表完成是唯一设计内差异）；(2) 语义层——人工确认每个搬动键落在正确的槽位、值未被改动、披露的丢弃项确实丢弃。审定基准：v2 文件是冻结的实验记录，v3 文件是**新实验身份**（转换即换名），因此只审"映射忠实"，不审"参数选择"。

## 逐份结论

| v3 文件 | v2 源 | 关键映射 | 结论 |
|---|---|---|---|
| alfworld_skillopt_split_v3 | alfworld_skillopt_split | K=5→segmentation；retry_limit=2→acceptance(exam)；bank_token_budget=4096→curation；curation_rounds=3→curation | 忠实 |
| alfworld_skillopt_split_v2_v3 | alfworld_skillopt_split_v2 | 同上 + skill_delivery(retrieval)→delivery（top_k/max_categories/min_score 原值） | 忠实 |
| searchqa_skillopt_split_v3 | searchqa_skillopt_split | 同 alfworld 首行模式 | 忠实 |
| tb21_full_v2_v3 | tb21_full_v2 | K=5→segmentation；skillopt 检索参数→delivery | 忠实 |
| tb21_split_live_run_v3 | tb21_split_live_run | **acceptance(mode: live_run, n_attempts=3, improvement=strict_rate_gain, max_injection_tokens=null)→methods.acceptance 逐键平移**；顶层 retry_limit=2（live 从不读取）按披露丢弃；bank_token_budget=96000→curation | 忠实 |
| tb21_split_reuse_v3 | tb21_split_reuse | retrieval 三参数→delivery；其余同上 | 忠实 |
| tb21_split_v2_v3 | tb21_split_v2 | 同上模式 | 忠实 |

## 拒因记录（3 份，未产出 v3）

pilot_alfworld / pilot_searchqa / tb21_full 三份 v1 时代配置携带 `model_options.<模型>.max_tokens`——在 v2 自身规则（保留键表）下即非法，v2 加载器同样会拒；转换器逐文件报拒因并跳过，不在本审定范围（它们是历史记录，不打算再跑）。

## 审定结论

7/7 忠实，0 处发现。`*_v3.yaml` 可以作为后续实验的起点（每份仍应在启动前按 runbook 核对头部注释的实验身份声明）。


## 更正（2026-10-15 22:20）

P4 给 curation 槽位加了第二个方法（none），upgrade_v2 的 curation 映射随之补了 `name: agentbrew`——但本审定覆盖的 7 份 `*_v3.yaml` 是 P4 之前生成的，其 curation 槽带参数而无 name，在 P4 代码上全部拒载（响亮失败，不产错误数据）。已逐份补上 `name: agentbrew` 并重新验证 7/7 可加载（P4+P5 评审 C 的 R1）。“可作为后续实验起点”的结论在补丁后重新成立。
