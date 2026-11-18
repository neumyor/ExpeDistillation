# 交付模块（delivery）

决定每道题注入哪些技能，输出"一道题 + 注入的技能"的执行单元清单。它不看任何历史结果，
也不管题怎么跑。

每个执行阶段都会执行。配置写在执行阶段的 `delivery` 里，`name` 选方法。

## 方法一览

| 方法 | 每道题注入什么 | 用在哪个阶段 | 参数 |
|---|---|---|---|
| `none` | 不注入（裸跑） | baseline（摸底，只能用它）、test（最终测试） | 无 |
| `whole_bank` | 技能库里所有在用（active）的技能 | test（最终测试） | `verbatim`（缺省 `false`） |
| `folder` | 只注入技能目录，技能正文放成文件夹里的文件，模型按需自己去读 | practice（练习，只能用它）、test（最终测试） | 见下 |

baseline 与 practice 的交付方法在读配置时检查，配错直接报错。

执行阶段写 `playbook: evolved` 时，交付把上一个学习阶段演化出的 playbook 放在每个单元技能块的最前面，
folder 单元另把它写进清单的 `playbook_text`。TB 的技能块不是普通提示词文本，
只在 `folder` 交付下经 skill-read 后缀显示 playbook，`none` / `whole_bank` 配 `evolved` 会在读配置时报错。

## `folder`：文件夹交付

```yaml
delivery:
  name: folder
  description_mode: summary_then_when_to_use
```

必须和执行方法 `real_folder` 配对，用在 practice 与 test 相位，读配置时会检查。

每个在用（active）的技能写成自己的目录，目录里放 `SKILL.md`：

```text
<task>/
  SKILL.md                 # benchmark 写的使用说明，不是技能
  catalog.md               # 兜底目录，模型通常不需要读
  type-001-skill/
    SKILL.md               # YAML 头 + 技能卡全文
  type-002-skill/
    SKILL.md
```

`SKILL.md` 开头的 YAML 头有 `name` 和 `description` 两个字段。Harbor 的 Terminus
agent 自己扫描 `<task>/<skill>/SKILL.md`，解析 YAML 头，把技能名、描述和完整路径
拼成 `<available_skills>` 放进首轮提示；模型按路径 `cat` 需要的那一个。`catalog.md`
只在 `<available_skills>` 缺失时作为兜底。

库里没有在用的技能时（例如第一次学习之前、或全部被删除），单元仍然挂上文件夹清单：
用同一个文件夹执行提示词，playbook 的位置与带库时相同，只是技能目录为空、不写
`catalog.md`；TB 照常上传 skill-read 后缀。带库与空库两种单元的差别只在技能库本身，
不在呈现方式。

| 参数 | 含义 | 可选项 | 默认 |
|---|---|---|---|
| `description_mode` | YAML 头里的 `description` 怎么拼 | `summary` 只放一句话摘要；`when_to_use` 只放技能的 `when_to_use` 字段；`summary_then_when_to_use` 先放摘要，再放触发条件 | `summary_then_when_to_use` |
| `read_rule` | 是否要求先读卡 | `optional` 用插件的文件夹提示词，开卡由模型自己决定；`mandatory` 用插件声明的 `skill_folder_mandatory_prompt_name`，要求第一条任务命令之前至少读一张卡，没有声明的插件在派发前被拒绝 | `optional` |

两个字段都为空时，`SKILL.md` 里写一句 `Skill <id>.`，保证描述非空；`catalog.md`
里保留原来的 `(no summary)` 占位符。

`read_rule: mandatory` 只写进清单的 `read_rule` 键，单元编号不变；缺省的 `optional`
不写这个键，清单与加入这个参数之前逐字节相同。TB 的 `mandatory` 发送 2026-10-29 那次
终测的后缀原文（`TB_SKILL_READ_MANDATORY`：必须先读卡、shell 卫生、阈值留余量，没有
完成前检查），代替 `TB_SKILL_READ` 加 `TB_DISCIPLINE`；ALFWorld、SearchQA 没有这个变体。

模块边界见 `docs/decisions/execute_learn_module_boundaries_20261016.md`，
本布局的决策见 `docs/decisions/folder_agent_skills_layout_20261022.md`。

## `whole_bank`：整库注入

```yaml
delivery:
  name: whole_bank
  verbatim: false       # 缺省
```

缺省把每条技能压成一行、前面加 `- `，这是本方法技能库的固定版式。`verbatim: true` 让每条技能的
文本逐字节原样进入提示词（标题和换行都保留），条目之间空一行；用于其他方法产出的技能文档
（例如 SkillOpt 的一份结构化 markdown），否则压成一行会削弱它们。
