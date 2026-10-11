日期：2026-10-11
一句话：配置新增 `tasks` 字段、具名规则默认值集中到 core/config.py 并在阶段 0 校验、manifest 冻结任务内容摘要与确定性的 prompt digest 集合。
对应 log.md 条目：2026-10-11 11:44（对照设计 review 全部实现，分波修复并经三次独立对抗式复审）。

背景：
- 配置里没有"任务集"字段，阶段 0 只能传 None（全集）或 split 列表并集；TB 插件
  按设计拒绝 None，于是 TB 的 full 模式与 ratio 划分根本无法配置。
- 三条具名规则的默认值散在阶段 1/2/3 里，阶段 3 缺省即报错——在昂贵的阶段 1
  跑完之后才失败；拼错名字同理；manifest 冻结的是原始配置而不是生效的规则名。
- manifest 只冻结任务 ID，不冻结任务内容：任务文件指令改了，续跑察觉不到。
- 冻结的 prompt digest 取自 `prompts.digests()`，即进程里"此刻碰巧注册了的"
  全部 prompt；导入过别的插件（测试进程常见）就会多出别人的任务 prompt。

决定与理由：
1. `ExperimentConfig.tasks`：None / 任务 ID 列表 / 任务文件路径（插件解释格式）。
   - 列表会规范成 list（tuple 经 JSON 往返后与 list 不相等，会让续跑误判）、
     拒绝空列表和重复 ID。
   - `load_config` 把相对路径按配置文件所在目录解析（与 `weak_profile_file`
     同约定），并**存成绝对路径**（含 `~` 展开）。理由：若存相对于 cwd 的
     路径，从不同 cwd 续跑会得到不同的冻结值而被误拒；代价是整个仓库搬家后
     续跑会因路径变化被拒——这是可接受的保守方向（内容本身另有摘要兜底）。
   - 配置里的相对路径（`tasks`、`weak_profile_file`）一律相对于**真实**配置
     文件所在目录：`load_config` 先对配置路径做 `resolve()`。理由：经符号链接
     加载同一份配置时，若按链接所在目录解析，会静默读到另一份任务文件或画像；
     按真实文件解析则"链接与原文件等价"，最不意外。
2. 显式 split 列表与 `tasks` 同时给出时，两者必须完全一致，阶段 0 报出两个方向
   的差异 ID；只给列表时沿用旧行为（加载并集）；ratio 划分从加载到的任务集派生。
   D1 加载后先检查重复 ID（任务文件由插件解释，配置层查不到）：有重复即
   ConfigError 列出重复 ID，在任何划分派生之前。否则 full 模式会把重复题
   计两次，ratio 划分则视 seed 报出含糊的"train/test 重叠"或静默通过。
3. `DEFAULT_RULES` 定义在 core/config.py，`__post_init__` 补齐缺失键，因此
   `config.rules` 恒含三个键、冻结的是生效名字。阶段 0 在加载任务之前就用
   `blocks/rules.py` 的注册表逐个解析规则名，拼错即在阶段 0 失败、不写任何文件。
   阶段 1/2/3 都直接读 `config.rules[...]`，各阶段的本地默认值已全部删除。
4. `tasks_digest`：按 task_id 排序后对 (task_id, instruction, meta) 做规范 JSON
   （sort_keys、无多余空白，沿用老仓库 `schema._canonical_json` 的做法）再取
   sha256。非 JSON 值的确定性回退：Path→posix 字符串、set→排序列表、bytes→hex、
   Enum→value、dataclass→asdict；其余对象记"类型全名 + repr"，并删掉 repr 里的
   内存地址（否则每次进程启动摘要都不同，续跑必被误拒）。没有用老仓库的
   `default=str`：默认 `str()` 会带内存地址，不确定。
   覆盖范围限于插件放进 Task 的内容：TB 的指令在远端任务目录，Task 只带名字，
   远端改动不在此摘要内（已写入阶段 0 docstring）。
5. prompt digest = `prompts.METHOD_PROMPT_NAMES`（方法 prompt）+ 当前插件的
   `task_prompt_name`，与进程里导入过哪些插件无关。`METHOD_PROMPT_NAMES` 在
   `prompts/__init__.py` 导入完方法 prompt 模块后快照 PROMPTS；之所以精确，是因为
   任何插件都必须先导入 `expedistill.prompts` 包（__init__ 完整执行）才能调用
   `register`。插件的任务 prompt 若未注册，冻结为 null：真实插件都在导入时注册，
   只有测试假插件会走到这里；之后若注册了，比较照样会触发 FrozenProtocolChanged。
