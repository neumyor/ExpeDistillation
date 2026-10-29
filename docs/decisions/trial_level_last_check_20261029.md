# 决定：trial_level 遵守“最后一次检查不写作”规则

日期：2026-10-29
一句话：trial_level 审阅在 `checks_left == 0` 时把 revise 转成 delete、把新需求记为延迟且不写作，与冻结的 4.2 动作表一致。
对应 log.md：2026-10-29 的“trial_level 遵守最后一次检查规则”条目。

## 背景

P07 失败归因发现，分叉 run `tb21_p02p06_exam_small20_20261028_2053_p02fork`
的最终库有 47 条：26 accepted、21 pending。这 21 条 pending 全部是最后一个
learn 相位（P06）写出的：9 条改写、12 条需求技能。终测的 folder 交付只认
accepted 集（`methods/delivery/units.py` 的 `accepted_bank`，10-16 裁决），
而 P06 之后没有任何执行相位能测量它们，所以它们永远到不了终测。

系统里本来有处理这种情况的规则。4.2 动作表（`composites/decision.py` 的
`table_action`）规定：没进步时，后面还有检查就 revise，没有检查就 delete。
`checks_left` 由 `decision.remaining_checks` 从冻结的相位序列算出。
`task_level`、`skill_level` 和 exam 的 `decide_task_list` 都按这条规则走。
新加的 `trial_level` 没有读 `checks_left`，所以在最后一个 learn 相位照样写改写
和新需求，绕过了这条规则。

本次 run 的影响有限。P06 的 keep 判定照常生效，23 个技能进入了交付集；
被浪费的只是写作调用，约占非执行调用输出 token 的 9%。终测数字不受影响：
按原规则这些产物同样不会交付。真正的问题有两个：一是新策略静默违反已冻结的
决策表；二是库里挂着 21 条交付不了的 pending，会让人误读库的规模，也让归因
把它们当成“交付缺口”。

## 候选方案

1. **只改 trial_level，让它遵守 `checks_left`**（本次采用）。
2. 序列末尾补一次 exam，让最后一轮的产物经过验收。这只是配置层面的选择，
   做了方案 1 以后可以随时这样配置，两者不冲突。
3. 终测改为交付 pending。不采用：这会把未经验证、且前一版本已经考砸的改写
   送进终测，也会破坏 accepted 的含义。如果确实想看效果，应作为单独的消融
   实验臂来跑，不改默认行为。

## 决定

`checks_left == 0` 时：

- **revise → delete**：聚合在动作校验之后把 revise 改为 delete，原因用表里
  的 `retry_exhausted`，不收集改写证据，也不调用写作；
- **新需求不写作**：需求照常写进 `new_skill_requirements.jsonl`；
  `requirement_outcomes.jsonl` 每条记一行 `deferred_no_later_check`
  （`skill_id: null`），不调用 `REQUIREMENT_SKILL`；续跑时已有终态行的需求跳过；
- learn 摘要多一项 `new_skill_requirements_deferred`；
- **keep 不变**：keep 仍然把 pending 成员转成 accepted，整理照常进行。

`checks_left > 0` 时行为完全不变。

## 后果

- 最后一个 learn 相位只做决定和整理。库里不会再出现交付不了的 pending
  改写和需求技能；被删的改写对象标 discarded，原因 `retry_exhausted`。
- 要让最后一轮的教训进入终测，只能在 test 前补一次 exam 或 live
  （方案 2）。这样前一个 learn 相位的 `checks_left` 变为 1，正常写作。
- 已有 run（含本次分叉 run）冻结在原代码上，不受影响。新 run 的 P06 写作
  调用会变少，最终库也会变小。
- 新增测试：聚合的 revise → delete 与 keep 不变两例；
  `record_deferred` 的续跑幂等；最后一个 learn 相位延迟需求、不写技能的相位级
  用例。原有的需求落地相位测试改为序列里带一个后续 live，继续覆盖写作路径。
