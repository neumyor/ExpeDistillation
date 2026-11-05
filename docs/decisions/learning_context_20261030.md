日期：2026-10-30
一句话：学习提示词里的 TB 专有措辞改成基准词汇槽，TB 沿用原措辞，ALFWorld 与 SearchQA 各写一套。
对应 log.md：2026-10-30 20:12「论文实验代码」条目

## 背景

写技能、改写技能、切段、归因、复盘用的提示词原来是为 Terminal Bench 2.1 写的：写着
"weaker terminal agent"、"Terminal Bench V2.1"、"TB21 capability families"、
`mark_task_complete`、shell 命令。论文要在 ALFWorld 和 SearchQA 上跑同一套方法，如果仍用这些提示词，
作者模型会写出终端形状的技能。方法（各遍的结构、输出格式、字数预算）不变，变的只有基准词汇，
所以词汇做成 `str.format` 槽，由基准插件提供取值。

## 机制（沿用，未改）

- `LearningContext(name, slots)`：`prompts/learning_context.py`；`GENERIC` 定义全部槽，措辞与基准无关。
- 插件类属性 `BenchmarkPlugin.learning_context`（默认 `GENERIC`）；三个基准各在
  `benchmarks/<名字>/learning.py` 里写自己的 `*_CONTEXT`，插件里加一行 `learning_context = ...`
  （外加对应的一行 import）。
- `cli._build_handles` 把插件的槽值拷进每个 `ModelHandle.prompt_defaults`；
  `call_model` 用 `GENERIC`、句柄默认值、调用方变量三层合并后 `format`，调用方不必传槽。
- manifest 记录 `learning_context.to_dict()`，改一个词就是新实验。
- 不经 `call_model` 就要用槽值的两处，用 `slots_for(handle.prompt_defaults)` 做同样的合并：
  无画像段 `profile_section()`（取 `agent_kind`），聚类写作驱动 `cluster_by_trigger`（取 `phrase_*`、`meaning_*`）。

## 槽位清单

共 25 个槽。`GENERIC` 和三个基准都定义全部槽；`SLOT_DOCS`（`learning_context.py`）逐个写明用途，
测试固定四个上下文的键集合等于 `SLOT_DOCS`，并检查每个槽至少被一个模板（或聚类驱动）用到。

| 槽 | 用在 | 内容 |
|---|---|---|
| `agent_kind` | SKILL_DISTILL、SKILL_DIAGNOSE、CLUSTER_TRIGGER_FAMILY、CLUSTER_SUBTASKS、CLUSTER_SKILL、SKILL_ATTEMPT_ATTRIBUTION、SKILL_ATTEMPT_REVISION、TRIAL_FAILURE_ANALYSIS、TRIAL_EXAM_ANALYSIS、PROFILE_UPDATE、REQUIREMENT_SKILL，以及无画像段 | 执行者的形容词：`a weaker <kind> agent`、`<kind>-agent trajectory`、`a <kind> task`、`its own <kind> behavior` |
| `benchmark_name` | 上述引入弱智能体的提示词、SEGMENT | `on <name>` / `from <name>` 里的基准名 |
| `skill_description` | REWRITE | "This is <a ... skill>"，冠词在槽内（`an ALFWorld skill`） |
| `task_tag` | SKILL_DISTILL、SKILL_DIAGNOSE、CLUSTER_SKILL | `a DIFFERENT <tag> task`、`a new <tag> task shows`、`separate <tag> discipline nodes` |
| `action_noun` | EXAM_SCENE、TRIAL_EXAM_ANALYSIS | 步骤叫什么：`<noun> lines`、`no <noun> output`、`did not execute <noun>s` |
| `capability_section` | SKILL_DISTILL、SKILL_DIAGNOSE | "不属于任何能力族时用 ... 兜底"那一段加上能力族清单 |
| `capability_example` | SKILL_DISTILL | 能力短语的引号示例 |
| `discipline_examples` | SKILL_DISTILL、SKILL_DIAGNOSE | 织进验证与回退的检查：动手前、重试前、收尾前 |
| `executable_rules` | SKILL_DISTILL、SKILL_DIAGNOSE | "写成可执行"清单里点名基准动作与产物的几条（占位符示例、动作的一般形式、脚本与代码） |
| `observable_signs` | SKILL_DISTILL、SKILL_DIAGNOSE、CLUSTER_SKILL | 可观察迹象清单的前几项；后面的 `tools, required output` 留在模板里（三处写法略有不同，TB 措辞要原样保留） |
| `segment_label_examples` | SEGMENT | 段标签的引号示例 |
| `setup_steps` | SEGMENT | 并入相邻能力段的步骤，句首大写 |
| `group_examples` | GROUP | 能力组名的引号示例 |
| `timing_menu` | SKILL_DISTILL、SKILL_DIAGNOSE | 五个开头短语加各自的时机说明（一整块） |
| `family_menu` | CLUSTER_TRIGGER_FAMILY | 五个族 key 加各自的含义（一整块） |
| `phrase_<key>` ×5 | SKILL_DISTILL、SKILL_DIAGNOSE 的提示句；聚类写作驱动 | 开头短语。聚类驱动还要求技能的 `when_to_use` 以它开头 |
| `meaning_<key>` ×5 | 聚类写作驱动，经 CLUSTER_SUBTASKS 的 `FAMILY MEANING` | 族的含义 |

`<key>` 是五个族的内部 key（`at_task_start`、`after_first_environment_check`、`when_command_fails`、
`before_submitting`、`at_any_step`），不变，存在聚类账本里；`TRIGGER_FAMILY_KEYS` 现在在
`learning_context.py`（第 1 层），`cluster_by_trigger.TRIGGER_FAMILIES` 指向它，`learn_add_cluster` 的
`in` 检查不受影响。`cluster_by_trigger.TRIGGER_PHRASES` 删除，短语改从槽里取。

五个时机里三处措辞各不相同（写作提示词里的时机说明、族分类提示词里的含义、聚类提示词里的 `FAMILY MEANING`），
TB 要保持原措辞，所以分成 `timing_menu`、`family_menu`、`meaning_*` 三组。`trigger_timing_slots()` 用四张按族
key 编号的表（短语、时机说明、族分类含义、聚类含义）生成这十二个槽；ALFWorld 与 SearchQA 三处措辞没有历史包袱，
三张表传同一份。

## 谁填什么

| 谁 | 填什么 |
|---|---|
| `prompts/learning_context.py` | `GENERIC`：全部槽的中性措辞（"text-based agent on the benchmark"、"general procedure"、"At task start," ... "Before finishing,"） |
| `benchmarks/tb/learning.py` | `TB_CONTEXT`：改动前提示词里的原措辞 |
| `benchmarks/alfworld/learning.py` | `ALFWORLD_CONTEXT` |
| `benchmarks/searchqa/learning.py` | `SEARCHQA_CONTEXT` |
| 各插件 `plugin.py` | 一行 `learning_context = <CONTEXT>` |
| `cli._build_handles` | 把插件的槽值放进 `prompt_defaults`（已有） |

## 三个基准的措辞选择与来源

两个新基准的动作名、规则、反馈串都抄自插件里已有的执行提示词和环境桥，没有新造。测试检查
ALFWorld 的命令词出现在 `TASK_PROMPT_TEMPLATE`，SearchQA 的 `Search[<query>]` / `Finish[<answer>]`
出现在 `SEARCHQA_TASK_PROMPT`。

**TB**：改动前的措辞，一字不改（见下一节）。`agent_kind` = terminal，`task_tag` = TB21，
`action_noun` = command，能力族是原来的十条，五个短语是原来的五个。

**ALFWorld**（来源：`alfworld/plugin.py::TASK_PROMPT_TEMPLATE`、`alfworld/env.py`）

- `agent_kind` = household，`benchmark_name` = ALFWorld，`action_noun` = command（执行提示词叫 "Commands"）。
- 能力族：按接收器找物体、拿起并放到接收器、加热/冷却/清洗手中物体、两件物体逐件送达、在台灯下检查物体、
  从被拒绝或重复的动作中恢复、最终放置前检查手中物体与目标。
- `executable_rules`：占位符写物体名、接收器名及其编号、反馈串；动作只写一般形式（`go to <receptacle>`、
  `take <object> from <receptacle>`、`move <object> to <receptacle>`）；只用游戏接受的命令
  （go to / open / close / examine / take ... from / move ... to / inventory / use / clean|heat|cool ... with），
  不得发明命令。
- 时机短语：`At task start,`、`After the first look around,`、`When an action is rejected or repeats,`、
  `Before the final placement,`、`At any step,`。
- 时机说明里用到的游戏事实：被拒绝的命令得到 `Nothing happens.`（`env.py`）；重复同一动作会终止回合；
  只携带一件物体（执行提示词）；只有环境报告任务已解决才算完成。
- `discipline_examples`：动手前看房间和背包，重复命令前读反馈，最终移动前确认手中物体与目标接收器。

**SearchQA**（来源：`searchqa/plugin.py::_TASK_TEMPLATE`）

- `agent_kind` = search，`benchmark_name` = SearchQA，`action_noun` = action（执行提示词叫 "action"）。
- 能力族：识别问题问的实体或事实、用问题关键词选查询、结果弱或重复时改写查询、跨段落桥接事实、
  日期数字名字类答案、答案简短并符合所问形式、判断何时停止搜索并作答。
- `executable_rules`：占位符写实体名、日期、数字、查询词、段落文字；动作一般形式 `Search[<query>]`、
  `Finish[<answer>]`；只有这两个动作，不得发明。
- 时机短语：`At question start,`、`After the first search,`、`When a search returns nothing useful or repeats,`、
  `Before answering,`、`At any step,`。
- `discipline_examples`：依赖段落前先核对，重复搜索前先改写查询，调用 `Finish[<answer>]` 前拿答案对照问题。

## TB 渲染与改动前一致的校验

步骤 0 在改任何文件前，用 `template.format(**{变量: f"<{变量}>"})` 渲染了 22 个提示词
（`SKILL_DISTILL`、`SKILL_DIAGNOSE`、`REWRITE`、`SEGMENT`、三个聚类提示词、五个复盘提示词、
两个归因提示词、四个考试提示词、`MERGE`、`SIMILARITY`、`GROUP`、`LEAK_AUDIT_JUDGE`），连同无画像段和
聚类驱动的两张表，存为 `tests/fixtures/prompts/learning_prompts_tb.json`。

`tests/unit/benchmarks/test_learning_context.py` 用 `TB_CONTEXT` 重新渲染并与快照比较：

- 22 个提示词里 14 个与快照逐字节相同；
- 另 8 个（`SKILL_DISTILL`、`SKILL_DIAGNOSE`、`REWRITE`、`SEGMENT`、`CLUSTER_TRIGGER_FAMILY`、
  `TRIAL_FAILURE_ANALYSIS`、`TRIAL_EXAM_ANALYSIS`、`GROUP`）只有换行位置不同，词一个不差。
  原因：同一段 TB 措辞在不同模板里折行位置各异（"a weaker terminal agent on Terminal Bench V2.1" 在七个模板里
  有四种折法，有两处折在名字中间），一个单行的槽值无法同时复现。
  测试因此在折叠空白后逐词比较；无画像段与两张族表则逐字节比较。

没有做到"逐字节相同"，是这条要求里唯一的偏离。

## 刻意没有换成槽的措辞

- `MERGE`、`SIMILARITY`、`REWRITE`、`SKILL_ATTEMPT_REVISION` 里的 "file names"、"A script may appear only as its
  input, output and invocation"：泛指任务特有的细节，三个基准都成立，不影响技能形状。
- `methods/review/attribution.py::_task_description`：解析 TB 的 Terminus 首条消息（"Task Description:" 等标记），
  不是发给模型的措辞；其他基准没有这个字段，直接回退到任务指令。
- `methods/delivery/folder_delivery.py` 的技能文件夹引导（"cat only the skill file ..."）：发给执行者，不属于学习提示词。

## 测试

- `tests/unit/prompts/test_templates.py`：`EXPECTED` 只列调用方变量，新增 `EXPECTED_SLOTS` 列每个提示词用到的槽，
  两者之并等于模板的占位符；`EXPECTED` 补上之前没列的七个提示词（两个归因提示词与五个复盘提示词）。
- `tests/unit/benchmarks/test_learning_context.py`：TB 快照；ALFWorld、SearchQA 和 `GENERIC` 渲染的所有方法提示词
  不含 "Terminal Bench"、"TB21"、"terminal agent"、"bash"、"keystroke"、"mark_task_complete"、"tmux"；四个上下文的槽键
  等于 `SLOT_DOCS`；每个槽被用到；插件声明了各自的上下文；命令词来自插件提示词。
- 直接 `.format()` 模板的几处旧测试（`test_author`、`test_curate`、`test_exam`、`test_fake_provider`）
  补上 `**GENERIC.variables()`；`test_cluster_by_trigger` 改用带槽值的真句柄，并新增一条测短语与含义取自槽。
- `profile_section(header, profile, agent_kind=None)`：`None` 取 `GENERIC` 的 `agent_kind`，与 `call_model`
  对槽的处理一致；原常量 `NO_WEAK_PROFILE_SECTION` 删除（TB 文本由快照保存）。
