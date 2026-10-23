# 装配缺口：bank 档位不拷贝 live_run 子树，不能重跑中段 learn

日期：2026-10-23
一句话：记录 `assemble-reuse --level bank` 对 live 类验收 run 的拷贝缺口——它不拷贝 `stage4_acceptance/live_run`，并把最终库预置成末个 learn 相位，无法用于只重跑 P04 的场景。
对应 log.md：2026-10-23 09:00 条目。

## 现象

对 `runs/tb21_split_folder_usage_20261022_1845` 执行
`assemble-reuse --level bank` 后，新目录：

- `phases/p04_learn` 不存在（这点符合预期）；
- `phases/p06_learn/skill_bank.jsonl` 被预置为源 run 的最终库（63 行 =
  30 accepted + 33 pending）；
- `stage4_acceptance/` 只有空的 `attempts/`，**没有 `live_run` 子树**：
  缺 round00/round01 的 trajectories、`verdicts.jsonl`、
  `injection_audit.jsonl`、`skill_usage.jsonl`；
- 源 run 的 `phases/p03_live`（phase.json、units、unit_verdicts、summary、
  skill_folder）也没有被拷贝。

P04 的 `usage_group` 依赖 P03 的 round store、`skill_usage.jsonl` 和
baseline；缺这些输入时 P04 无法重跑源 run 中因归因格式错误而未处理的
5 个任务、33 条 pending 技能。

## 原因

`assemble_reuse` 的 bank 档位按“复用训练侧全链路”设计：它拷贝 stage 1–5 的
对象与整理后的库，但把测量与续跑日志排除在 allowlist 之外；
`stage4_acceptance/live_run` 属于测量/续跑日志，因此不在拷贝白名单。
同时它把最终库预置成末个 learn 相位（P06）的库产物，让整理直接返回。
这套语义适合“整体换 split、复用训练侧全链路”，不适合“只重跑中段一个
learn 相位”。

## 待修（本次不改工具）

需要新增一种装配级别或选项：在拷贝 P01–P03 训练侧对象、P02 bank 与
P03 live 子树/P03 phase 记录的同时，明确排除 P04 及之后的产物，使 P04
可以真实重跑。该改动涉及共享装配工具，必须先设计、测试并单独评审；
本次修复实验改用 `--level baseline`（只复用 P01 轨迹，P02 起全部真实重跑），
不修改装配工具。

## 证据

- 废弃目录：`runs/tb21_split_folder_usage_fix_20261023_0855/`
  （其 `ASSEMBLED_FROM.md` 末尾有 dry check 结论，`ABANDONED.md` 说明放弃原因；
  未启动任何 phase，零模型调用）。
- 源 run：`runs/tb21_split_folder_usage_20261022_1845/`
  （P03 证据在 `stage4_acceptance/live_run/{round00,round01,verdicts.jsonl}`
  与 `phases/p03_live/`）。
