# live 渐进式披露与 usage 感知修订

日期：2026-10-22
内容：P03/P05 的 live 交付改为逐任务技能文件夹，验收记录逐技能读取事实，学习相位据此选择 keep / retrigger / revise / delete / observe。
对应 log.md：2026-10-22 的“live 渐进式披露与 usage 感知修订”条目。

## 背景

原来的 live 相位用 `group_by_task` 把同一任务的整组技能拼成一个
`## Skill rules` 块。模型只读取一个 `SKILL.md`，验收只能判断整组是否
比裸跑好，无法知道哪一条技能被读过、哪一条没有被读过。学习相位的
G10/G11 只能看到整组失败视图，无法区分“技能有用但没被选中”和
“技能被选中但没有效果”。

## 决策

### 1. live 交付增加 `folder_by_task`

`folder_by_task` 为每个待验收任务生成一份 folder manifest，物化成：

```text
phases/pNN_live/skill_folder/<task>/
  SKILL.md
  catalog.md
  <skill>/SKILL.md
```

Harbor/Terminus 只把 `name` 和 `description` 放进 `<available_skills>`，
模型按需 `cat`。它使用 plain `real` 执行：live 的采样深度和注入预算来自
`real` 的参数，folder 形态来自交付 manifest。`folder`（test 相位）继续
与 `real_folder` 配对，两者的阶段边界由 core 的加载期检查固定。

### 2. 验收记录读取事实

live folder 单元执行后，`skill_usage.jsonl` 为每次尝试写一行：

- 可用技能 id 与首轮提示词里的 `<available_skills>` 名称；
- 投递状态（`ok` / `unverified` / `manifest_mismatch` / `no_skills`）；
- 每条技能的首次读取步与 cat 命令；
- 已读、未读技能集合；
- 本次尝试的结果。

账本从存储轨迹和冻结 manifest 事后计算，不拦截执行；续跑重算同样的行。
投递失败时学习相位只做 `observe`，不据此判断技能质量。

### 3. 学习相位增加两个槽位

- `group_evidence`：`live_group`（默认，旧行为）或 `usage_group`。
  `usage_group` 在原有组证据上增加读取次数、应用/相关判断、兄弟技能描述
  和模型未读时的前几步动作。
- `group_author`：`distill_diagnose`（默认，旧行为）或 `usage_revise`。
  `usage_revise` 的策略 `usage_v1`：

| 证据 | 结果 |
|---|---|
| 投递失败、整组零读取、证据不足 | `observe` |
| 相关但未读，此前没重写过触发条件 | `retrigger`（只改 summary 和 [When to use]） |
| 已重写触发条件但仍未读 | `delete` |
| 读取但未应用，或应用后仍无效 | `revise`（重写正文） |
| 动作与技能相反 | `delete` |
| 相关且应用正常 | `keep` |

成功组不进入逐技能修订；其读取事实只记录，留给后续整理参考。

## 边界

- 不新增相位种类，不改变序列、CLI 分发、验收报告字段、组级
  `finalize_group` 和 `decide_task_list`。
- `folder_by_task` 只在 live 角色；`folder` 只在 test 角色。两者都不改变
  其他交付方法。
- `group_evidence` / `group_author` 不写时补默认值，旧配置行为不变。
- 新提示词 `LIVE_USAGE_ATTRIBUTION`、`LIVE_SKILL_RETRIGGER` 进入冻结的
  prompt digest 集合；新配置是新实验。

## 已知限制

- 组级终态仍是整组接受或丢弃；最后一轮失败的组里，即使某条技能被读取且
  有效，也会随组一起丢弃。逐技能终态留作后续改动。
- “应用”是强模型看轨迹后的判断，不是硬事实；策略只在读取事实明确时行动。
- 读取解析只统计 `cat`；用 `sed`/`head` 等方式读取不计入。模型按提示使用
  `cat`，这个口径先保持稳定。
- 旧的整块注入 run 无法补出逐技能读取记录，新逻辑必须用新 run。
