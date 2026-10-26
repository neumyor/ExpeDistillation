# 写作模块（author）

按决策结果和证据包写内容。它不判断技能好不好用，不做 keep/delete 决策，
也不做测量。

## `distill_diagnose`：P02 的提炼 / 诊断写作

```yaml
author:
  name: distill_diagnose
```

没有参数。P02 的 add 行用它：

| 任务清单动作 | 做什么 | 模型 |
|---|---|---|
| 新增 | 每个片段写一条新技能；强模型成功段用提炼，弱模型失败段用诊断；两种都产出三段结构 `[When to use]` / `[Steps]` / `[Do not use when]` | `author` |

## P04/P06 的 revise 写作

新的共享写作入口是 `revise.py` 的 `revise_one`：

- 决策已经给出 per-skill 的 `revise`，这里只写内容；
- 输入是 `SkillAttemptEvidence`（逐 attempt、逐窗口的证据），不再读
  group diagnosis / failure_view / retrigger 判断；
- 多个窗口的 X 可能冲突，由写作模型自己综合；
- 整条重写，保留三段结构；
- 改写失败或输出与原文相同 = `unchanged`，版本不动。

## `revise_group`：exam 兼容路径

`write.py` 的 `revise_group` 仍保留给 exam 的考卷证据路径：它对每个包调用
`rewrite_skill`，不再有 `default_decide` 的 per-skill 策略。

## 参数

| 参数 | 含义 | 默认 |
|---|---|---|
| `use_weak_profile` | 是否把弱模型画像送进写作/改写提示（实验级配置，不在 author family） | `false` |
| `evidence.write_lessons` | P02 是否从弱模型失败段学 | `true` |

设计取舍见 `docs/decisions/p04_decision_strategy_split_20261023.md`、
`docs/decisions/skill_structure_three_sections_20261026.md`、
`docs/decisions/unified_skill_structure_20261019.md`（后者已过时，仅作历史记录）。
