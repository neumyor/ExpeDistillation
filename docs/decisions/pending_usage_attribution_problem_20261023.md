# 决定：LIVE_USAGE_ATTRIBUTION 的 not_applied / problem 校验口径

日期：2026-10-23
一句话：记录 P04 usage 归因中 `application=not_applied` 时 `problem` 可空性校验与 prompt 不一致的两种修法；用户于 2026-10-23 选择方案 B（改 prompt 要求非空 problem，校验器不动）。
对应 log.md：2026-10-23 05:25、08:50 与 09:00 条目。

## 背景

run `runs/tb21_split_folder_usage_20261022_1845` 的 P04 learn 有 5 次
`LIVE_USAGE_ATTRIBUTION` 输出被 `blocks/attribution.py` 拒绝，消息是
`skills[i] (application 'not_applied') needs a non-empty problem`。受影响的是
`tb:overfull-hbox`、`tb:gcode-to-text`、`tb:mteb-retrieve`、
`tb:torch-tensor-parallelism`、`tb:gpt2-codegolf` 共 33 条技能；P04 的
outcomes/revisions 没有这 5 个任务，33 条技能在 P06 库中仍是 pending，
P07 终测只交付了 30 条 accepted，33 条未进入终测。

## 选项（已比较）

| 选项 | 做法 | 利 | 弊 |
|---|---|---|---|
| A. 放宽校验 | 允许 `application=not_applied` 时 `problem` 为空（`contradicted` 是否同样放宽需一并定） | 与当前 prompt 的“没有问题时 problem 可以为空”一致，不改 prompt digest，不新增格式失败 | 失去“not_applied 必须给出具体问题”的证据约束，`usage_revise` 可能基于更弱的归因事实做 revise/delete |
| B. 改 prompt | 在 `LIVE_USAGE_ATTRIBUTION` 中明确要求 `not_applied`/`contradicted` 必须带非空 `problem` | 保留证据约束，每次 non-application 判断都有具体问题，修订依据更硬 | prompt digest 变化，必须新开 run；模型可能为满足格式编造 problem，或仍有格式失败 |

## 决定

采用方案 B。2026-10-23 用户裁决：在 `LIVE_USAGE_ATTRIBUTION` 中明确要求
`application` 为 `not_applied` 或 `contradicted` 时 `problem` 必须非空；
`blocks/attribution.py` 的严格校验保持不变，其他 prompt 不改。已按此修改并
重新生成 golden；prompt digest 从 `ff85eeb2…` 变为 `da7262e9…`，因此后续
run 必须新开 run 目录，旧 run 不续跑、不补跑。文件名保留 `pending_` 前缀是
为了不影响 log.md 中已记录的历史工件路径。
