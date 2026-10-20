# 整理模块（curation）

把已通过验收的技能整理成最终的技能库：合并讲同一件事的技能、调和互相矛盾的技能、
可选地丢弃没有泛化价值的技能，再给每条技能分能力组。它不重新判断技能好不好用。

只在序列里**最后一个学习阶段**真正整理；中间的学习阶段原样输出已接受的技能，
只有 `token_budget` 生效。配置写在学习阶段的 `curate` 里，`name` 选方法。

## `agentbrew`：合并整理

```yaml
curate:
  name: agentbrew
  token_budget: null        # 必填
  rounds: 3
  merge_check: none
  drop_low_value: false
  similarity_input: full
  grouping_input: full
```

| 参数 | 含义 | 可选项 | 默认 |
|---|---|---|---|
| `token_budget` | 技能库总长度上限（估算 token）。超出就继续合并，跑满轮数仍超出则报错停下，不会自动删技能 | 正整数；`null` = 不设上限 | 必填 |
| `rounds` | 最多整理几轮（每轮：找相似 → 合并 → 分组） | 正整数 | `3` |
| `merge_check` | 合并后是否做回归考试：合并出的技能重做每条来源技能的考题，有一道答错就撤销合并 | `none` 不考；`exam` 要考（序列里必须有纸面验收 exam 相位） | `none` |
| `drop_low_value` | 是否丢弃找相似时标出的、换到别的任务就没用的技能 | `true` 在合并前丢弃；`false` 只记进日志 | `false` |
| `similarity_input` | 找相似（G7）时让模型看每条技能的什么 | `full` 看正文；`summary` 只看一句话摘要（有技能缺摘要会直接报错） | `full` |
| `grouping_input` | 分组（G9）时让模型看每条技能的什么；与 `similarity_input` 互相独立 | `full` 看正文；`summary` 只看一句话摘要（有技能缺摘要会直接报错） | `full` |

注意：`token_budget: null` 时只跑一轮，`rounds` 不起作用（每轮结束“库不超长”就停，
没有上限时这个条件总成立）。想跑多轮，要给一个具体上限。

## `none`：不整理

已接受的技能原样进库。

| 参数 | 含义 | 可选项 | 默认 |
|---|---|---|---|
| `token_budget` | 技能库总长度上限，超出就报错 | 正整数；`null` = 不设上限 | 必填 |

设计取舍见 `docs/decisions/curation_generalize_20261019.md`。
