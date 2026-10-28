# Long-trajectory input audit

日期：2026-10-27

一句话：审计所有把轨迹/窗口直接拼进 prompt 的模型调用，确认长轨迹角色的输入预算，
并为 `trial_level` 增加 512K 配置门。

对应 log.md：`2026-10-27 20:52 — trial_level P04 真实冒烟（纠正第一次误测 skill_level）` 之后的补强。

## 审计范围

把同一批 TB21 P03 的 AnswerSheet 用 TB 的 `ViewRules(max_obs_chars=8000,
head_keep=1000, tail_keep=1000)` 渲染后，按角色列出长轨迹输入：

| 调用 | 角色 | 输入内容 | 角色 `max_input_tokens` |
|---|---|---|---:|
| `TRIAL_FAILURE_ANALYSIS` | author | 整条失败 trial + 整个 skill bank | 524288 |
| `TRIAL_REQUIREMENT_AGGREGATION` | author | 全部 trial findings + 整个 skill bank | 524288 |
| `REQUIREMENT_SKILL` | author | 需求证据里的失败窗口 | 524288 |
| `SKILL_ATTEMPT_ATTRIBUTION` | author | 一个 10+10 的 attempt 窗口 | 524288 |
| `SKILL_ATTEMPT_REVISION` | rewriter | 一个 skill 的全部证据窗口 | 524288 |
| `SEGMENT`（P02） | segmenter | strong/weak 轨迹视图 | 524288 |
| `EXAM_SCENE` / `EXAM_TRIGGER` / `EXAM_PLAN` / `EXAM_JUDGE` | examiner / answerer / judge | 场景、候选目录、打开的技能、计划 | 512K / 256K / 512K |
| `CURATE` / 审计 | curator / auditor | 库成员或轨迹证据 | 524288 |

正式配置 `configs/experiments/tb21_split_folder_cluster_trigger_agentbrew25_20261026.yaml`
和 `tb21_p02bank_p07_20261027.yaml` 都按上表配置了 524288；`tb21_ideal6_p07_20261026.yaml`
没有长轨迹调用，未配置输入上限。

## 实测长度

TB21 P03 的 48 条 trial 中，渲染后的 `trial_steps`：

- 最长 858,447 字符 / 244 步（`schemelike-metacircular-eval` trial 1）；
- 多条 400k–600k 字符；
- 47 张 skill 卡合计约 124,942 字符。

`estimate_tokens = len(text) // 4`，最长单条 trial 加整库 skill 卡约 246k 估算
tokens。实际 provider token 数通常略高：本次 `reshard-c4-data` 三条 trial 的
`in_tokens` / `estimated_in` 约为 1.05–1.11 倍。代码文档还提示 terminal-dump-heavy
文本可能低估 1.4–1.5 倍，因此最长样本实际可能在 260k–370k tokens。

## 结论与改动

- 524288 的配置预算能覆盖当前最长样本的估算范围，但离 512K 上限并不远；
  不能把“估算没超”当成“模型一定完整读了”。
- `trial_steps` 是**整条 trial**，不是窗口；TB 只按每步 8000 字符截断 observation，
  没有 trial 级分段或 map-reduce。
- `max_input_tokens` 在 `call_model` 里仍然只是测量值（`input_violation`），
  不会本地截断或拒绝；真正的上下文仍由端点决定。
- 新增 `stage0_freeze` 检查：`review.decision == "trial_level"` 时，
  `author` 和 `rewriter` 必须声明 `max_input_tokens >= 524288`，否则在冻结步
  以 `ConfigError` 拒绝。
- `TRIAL_FAILURE_ANALYSIS` 的 skill id 现在复用 folder alias 归一化；目录名、
  文件名和 `-SKILL.md` 形式都能解析回完整 id，避免整条 trial finding 因命名
  形式不同而丢失。
