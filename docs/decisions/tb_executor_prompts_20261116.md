日期：2026-11-16
说明：整理 TB 2.1 执行侧的提示词，登记的文本与实际发给智能体的文本一致，读卡规则与 ALFWorld 对齐，整库交付改走后缀。
对应 log.md：2026-11-16 "规范化 TB 执行侧提示词"

# TB 执行侧提示词的规范化

## 背景

TB 的 trial 由 node29 上的 Harbor 运行 Terminus-2 智能体。首轮消息由 Harbor 组装，依次是：

1. Terminus 自己的模板（`claw-harbor` 的 `terminus-json-plain.txt`）和任务描述；
2. vendor 的 `TencentSkillTerminus2` 拼在任务描述后面的后缀：任务目录里有 `SKILL_READ_SUFFIX.md` 时用它，没有时用内置默认值；
3. Terminus 自己生成的 `<available_skills>`：只扫描 `<task>/<skill>/SKILL.md`（`find -mindepth 2`），并且只收带 YAML 头的文件。

整理前登记了 5 个 TB 提示词，加上一段没登记的文本，与实际情况对不上：

| 原名称 | 实际情况 |
|---|---|
| `TB_TASK_PROMPT` | 从没发给智能体，只用于登记 digest；学习侧用它给诊断渲染任务文本 |
| `TB_SKILL_FOLDER_GUIDANCE`（未登记） | 作为任务级 `SKILL.md` 上传，Terminus 不读，智能体看不到。其中"必须先读至少一张卡"已在 10-30 决定删除 |
| `TB_TASK_PROMPT_FOLDER` | 从没发出去，内容是上一行加一段不用的模板 |
| `TB_TASK_PROMPT_READ` | 实际送到智能体的读卡说明，混有两阶段选卡、读后复述和工作纪律 |
| `TB_TASK_PROMPT_READ_PLAYBOOK` | 有 playbook 时在上一行前面加 playbook |

另外，`whole_bank` 交付把技能块写成任务级 `SKILL.md`，按代码与 Harbor 源码，智能体看不到技能文本，拿到的是 vendor 默认后缀，里面说的 `<available_skills>` 并不存在。node29 上没有这类轨迹，没有实跑证据。

## 决定

原则：登记的执行提示词必须逐字节等于实际发出的文本；每段文本只有一个来源；执行侧唯一可控的入口是后缀。

| 新名称 | 内容 |
|---|---|
| `TB_TERMINUS_TEMPLATE` | Terminus 模板的逐字节副本（本机 vendor、node29 的 vendor 与 venv 里三份 sha256 相同）。作为插件的 `task_prompt_name`：manifest 冻结真实的执行协议，学习侧用它渲染任务文本 |
| `TB_SKILL_READ` | ALFWorld `_READ_RULE` 的终端版：卡可选；用 `cat <位置>` 打开，全文留在对话里；每个任务最多 3 张；描述与当前情况相符才打开，都不符就不开。作为插件的 `skill_folder_prompt_name` |
| `TB_DISCIPLINE` | 原 `TB_TASK_PROMPT_READ` 里的 shell 卫生、阈值留余量、完成前自检，逐字保留 |
| `TB_PLAYBOOK_UPDATE` | 保留，改为嵌入 `TB_SKILL_READ` 与 `TB_DISCIPLINE`，删掉一句旧方法遗留的话 |

后缀由 `benchmarks/tb/protocol.py` 的 `render_suffix` 按固定顺序拼装：playbook（标题 `Global playbook:`，与 ALFWorld 相同）、整库技能块（只在 `whole_bank` 时有）、`TB_SKILL_READ`（只在 folder 单元有）、`TB_DISCIPLINE`。拼装不另登记提示词：各段文本都已登记，顺序由代码固定，与 ALFWorld 在代码里拼装技能槽的做法相同。

其余改动：

- 任务级 `SKILL.md` 改为一行占位文本 `MOUNT_PLACEHOLDER`，只为通过沙箱的挂载检查；
- `whole_bank` 单元同样上传后缀，技能块放在后缀里；
- 不带技能的单元不上传后缀，行为不变；
- 写作者读的环境协议段补一句读卡方式与上限；
- `skill_read.py` 并入新的 `protocol.py`，`plugin.py` 只保留远程执行逻辑；
- vendor 文件不改。

## 用户的取舍

- 读卡规则与 ALFWorld 对齐，删掉开局两阶段选卡和读后复述；
- 工作纪律条款保留，放在读卡规则之后（用户决定）。代价是带技能的臂一直比不带技能的对照多一段人工规则；
- 取代 `tb_skill_read_suffix_20261030.md` 的选卡时机与读后复述两条，以及 `tb_playbook_in_read_suffix_20261111.md` 里"后缀与 vendor 默认值逐字节相同"一条。

## 影响

- TB 的执行与学习提示词 digest 改变，与 11-12 之前的 TB run 不再同口径；
- ALFWorld、SearchQA 的 26 个提示词 digest 不变，golden 快照逐字节不变；
- 学习侧给 TB 诊断渲染的任务文本，从 `TB_TASK_PROMPT` 换成 Terminus 模板；两者都只拿到任务名，拿不到任务描述（见下）。

## 遗留问题

TB 的任务描述只在远程数据集里，本地 `load_tasks` 只给任务名，所以学习侧的诊断（`TRIAL_FAILURE_ANALYSIS` 的 TASK 一节）只能看到任务名。每条 TB 轨迹的 `metrics.initial_prompt` 记录了真实的首轮消息（10-28 摸底 120 条里有 119 条），但 `composites/trace.execution_instruction` 优先用渲染出的任务文本，只在它为空时才用记录。这个问题在本次改动之前就存在，本次未修。
