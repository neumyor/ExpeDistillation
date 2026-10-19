# 交付模块（delivery）

决定每道题注入哪些技能，输出"一道题 + 注入的技能"的执行单元清单。它不看任何历史结果，
也不管题怎么跑。

每个执行阶段都会执行。配置写在执行阶段的 `delivery` 里，`name` 选方法。

## 方法一览

| 方法 | 每道题注入什么 | 用在哪个阶段 | 参数 |
|---|---|---|---|
| `none` | 不注入（裸跑） | baseline（摸底） | 无 |
| `per_skill` | 这道考题对应的那一条技能 | exam（纸面验收） | 无 |
| `group_by_task` | 这道题名下还待验收的整包技能 | live（实战验收） | 无 |
| `whole_bank` | 技能库里所有已接受的技能 | test（最终测试） | 无 |
| `retrieval` | 按题目文字检索出的几条技能 | test（最终测试） | 见下 |
| `folder` | 只注入技能目录，技能正文放成文件夹里的文件，模型按需自己去读 | test（最终测试） | 见下 |

只有 `folder` 在读配置时检查阶段，其余方法配错阶段不会在读配置时报错，请按上表配。

## `retrieval`：检索交付

```yaml
delivery:
  name: retrieval
  top_k: 3              # 必填
  max_categories: 1     # 必填
  min_score: 0.02       # 必填
```

先把题目路由到最相近的几个技能类别，再在类别里按词面相似度（TF-IDF 余弦）取前几条。

| 参数 | 含义 | 可选项 | 默认 |
|---|---|---|---|
| `top_k` | 每道题最多注入几条 | 正整数 | 必填 |
| `max_categories` | 每道题最多路由到几个类别 | 正整数 | 必填 |
| `min_score` | 最相似那条的分数低于它时，这道题不注入任何技能 | `[0, 1)` 的数 | 必填 |

## `folder`：文件夹交付

```yaml
delivery:
  name: folder
  inline_catalog: false
```

必须和执行方法 `real_folder` 配对，只能用在 test 相位，读配置时会检查。

| 参数 | 含义 | 可选项 | 默认 |
|---|---|---|---|
| `inline_catalog` | 技能目录放在哪 | `false` 目录是文件夹里的 `catalog.md`，模型要自己打开；`true` 目录直接写进题目提示里，正文仍在文件夹里 | `false` |

模块边界见 `docs/decisions/execute_learn_module_boundaries_20261016.md`，
检索的设计见 `docs/decisions/retrieval_delivery_tb_split_20261013.md`。
