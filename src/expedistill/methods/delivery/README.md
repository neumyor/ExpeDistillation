# 交付模块（delivery）

决定每道题注入哪些技能，输出"一道题 + 注入的技能"的执行单元清单。它不看任何历史结果，
也不管题怎么跑。

每个执行阶段都会执行。配置写在执行阶段的 `delivery` 里，`name` 选方法。

## 方法一览

| 方法 | 每道题注入什么 | 用在哪个阶段 | 参数 |
|---|---|---|---|
| `none` | 不注入（裸跑） | baseline（摸底） | 无 |
| `per_skill` | 这道考题对应的那一条技能 | exam（纸面验收） | 无 |
| `group_by_task` | 这道题名下还待验收的整包技能（整块注入） | live（实战验收） | 无 |
| `folder_by_task` | 这道题名下还待验收的整包技能（逐技能目录，模型按需读取） | live（实战验收） | 见下 |
| `whole_bank` | 技能库里所有已接受的技能 | test（最终测试） | 无 |
| `retrieval` | 按题目文字检索出的几条技能 | test（最终测试） | 见下 |
| `folder` | 只注入技能目录，技能正文放成文件夹里的文件，模型按需自己去读 | test（最终测试） | 见下 |

`folder` 和 `folder_by_task` 在读配置时检查阶段，其余方法配错阶段不会在读配置时报错，请按上表配。

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

## `folder_by_task`：live 的逐任务文件夹交付

```yaml
delivery:
  name: folder_by_task
  description_mode: when_to_use
```

必须用在 live 相位，和执行方法 `real` 配对（live 的采样深度来自 `real` 的参数）。
每个待验收的任务组物化成自己的目录：

```text
phases/p03_live/skill_folder/<task>/
  SKILL.md                 # 任务级使用说明
  catalog.md               # 列表缺失时的兜底
  <skill-a>/SKILL.md       # YAML 头 + 三段正文
  <skill-b>/SKILL.md
```

和 test 的 `folder` 一样，Harbor 只把每条技能的 `name` 和 `description` 放进
`<available_skills>`；区别是这里的池子是 live 的 pending 组。逐技能读取事实
从该轮的 trial 级 AnswerSheet（`ExecutionRecord.steps` +
`artifacts.skill_folder`）按需提取。参数与 `folder` 相同：

| 参数 | 含义 | 可选项 | 默认 |
|---|---|---|---|
| `description_mode` | YAML 头里的 `description` 怎么拼 | `summary` / `when_to_use` / `summary_then_when_to_use` | `summary_then_when_to_use` |

## `folder`：文件夹交付

```yaml
delivery:
  name: folder
  description_mode: summary_then_when_to_use
```

必须和执行方法 `real_folder` 配对，只能用在 test 相位，读配置时会检查。

每个已接受的技能写成自己的目录，目录里放 `SKILL.md`：

```text
<task>/
  SKILL.md                 # benchmark 写的使用说明，不是技能
  catalog.md               # 兜底目录，模型通常不需要读
  bank-merge-0001/
    SKILL.md               # YAML 头 + 三段正文
  traj-tb-xxx/
    SKILL.md
```

`SKILL.md` 开头的 YAML 头有 `name` 和 `description` 两个字段。Harbor 的 Terminus
agent 自己扫描 `<task>/<skill>/SKILL.md`，解析 YAML 头，把技能名、描述和完整路径
拼成 `<available_skills>` 放进首轮提示；模型按路径 `cat` 需要的那一个。`catalog.md`
只在 `<available_skills>` 缺失时作为兜底。

| 参数 | 含义 | 可选项 | 默认 |
|---|---|---|---|
| `description_mode` | YAML 头里的 `description` 怎么拼 | `summary` 只放一句话摘要；`when_to_use` 只放 `[When to use]` 触发条件；`summary_then_when_to_use` 先放摘要，再放触发条件 | `summary_then_when_to_use` |

两个字段都为空时，`SKILL.md` 里写一句 `Skill <id>.`，保证描述非空；`catalog.md`
里保留原来的 `(no summary)` 占位符。

模块边界见 `docs/decisions/execute_learn_module_boundaries_20261016.md`，
检索的设计见 `docs/decisions/retrieval_delivery_tb_split_20261013.md`，
本布局的决策见 `docs/decisions/folder_agent_skills_layout_20261022.md`，
live 渐进式披露与 usage 修订见
`docs/decisions/usage_aware_live_folder_20261022.md`。
