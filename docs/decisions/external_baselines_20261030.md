日期：2026-10-30
一句话：六个外部基线各自训练产物的格式、导入本仓库 test 相位的做法、对应的交付方式、在三个基准上训练的现有命令，以及强模型在 S 与 T 两种设定下的作用。
对应 log.md：2026-10-30 20:12「论文实验代码」条目

## 范围与证据口径

基线的训练在各自的仓库里进行，本仓库不重写。本仓库负责另一半：拿到基线训练产出的产物，在 test 相位里用同一个弱模型、同一套交付机制、同一种对照臂（`compare: {against: fresh_bare}`）评测。
评测环境取自外层 `benchmark/alfworld-eval`、`benchmark/searchqa-eval`（TB 取自本仓库的 TB 插件），不新建评测环境。

本文关于基线的每一条陈述来自阅读代码，**没有运行任何训练或评测命令**。读不到或没有核对的地方标为"未核验"。路径缩写：

| 缩写 | 目录 |
|---|---|
| `pulled/X` | `repos/pulled/X`（工作区根目录下） |
| `CV3` | `controller_v3/`（工作区根目录下，SkillOpt、GEPA、EvoSkill、Trace2Skill 的窗口式桥接） |
| `ALF` | `benchmark/alfworld-eval` |
| `SQA` | `benchmark/searchqa-eval` |
| `TBE` | `benchmark/terminalbench-eval` |

## 一、导入器 `stages/import_bank.py`

### 1.1 test 相位读什么

对照代码得到：只含 test 相位的 run（`sequence: [test]`）里，`phase_disk._bank_skills` 在磁盘上没有任何 learn 相位库产物时，退回读 `Store.bank()`，也就是 run 根目录的 `skill_bank.jsonl`。`whole_bank`、`retrieval`、`folder` 三种交付都只取其中 `status == accepted` 的成员。
stage-0 冻结（`manifest.json`）在第一次 `run` 时写，它不看技能库，所以技能库的来源只记录在 `BANK_ORIGIN.md` 里。
参照的先例 `runs/tb21_p02bank_p07_20261027_1108/` 也是这样：根目录 `skill_bank.jsonl`（47 行，状态已由 pending 改成 accepted），技能对象仓为空。

### 1.2 导入器写什么

`import_external_bank(source, source_format, run_dir, *, origin)`，按下面顺序写，且只写 `run_dir` 之内：

1. 每条技能写入对象仓 `stage3_generation/skills/`（与完成学习的 run 一致，供按对象仓读技能的分析工具使用；test 相位本身不读）；
2. `skill_bank.jsonl`（全部 accepted）；
3. `BANK_ORIGIN.md`（来源路径、格式、origin、条数、未导入的文件）。

不写 `manifest.json`，不调用模型，不联网。已有 `manifest.json`、`skill_bank.jsonl`、`BANK_ORIGIN.md` 或对象仓里已有技能的目录直接拒绝；来源先读先转换，来源有问题时不会留下目录；写入时持有 run 锁。

### 1.3 三种来源格式与技能的字段

| 格式 | 来源 | 一个条目 | 摘要（`summary`） |
|---|---|---|---|
| `markdown_file` | 一个 Markdown 文件 | 整份文档 | 前置 YAML 头里的 `description`；没有时取第一个标题（代码围栏之外），再没有取第一段的第一句 |
| `skill_folder` | `<源目录>/<名>/SKILL.md` | 每个子目录一条，按目录名排序 | YAML 头的 `description`（缺失时报错） |
| `notes_jsonl` | 每行一个 JSON，含 `text`，可选 `id`、`title`、`scope` | 每行一条 | `title`；没有时同 `markdown_file` 的推导 |

技能字段：

- `content`：来源正文逐字保留（去掉 YAML 头、统一换行、去掉首尾空白）。基线的原话原样送到弱模型，folder 交付里 YAML 的 `description` 就是来源自己的摘要。
- `status = accepted`，`version = 1`，`group`：`notes_jsonl` 的 `scope`（无则为空）。`scope` 会让 `whole_bank` 在块里按 `### <scope>` 分组，也让 `retrieval` 按它路由。
- `skill_id = import/<origin>/<键>`。键来自条目：`skill_folder` 取目录名（目录名在文件系统里唯一；Trace2Skill 发布的两个 combined 技能 YAML 头里的 `name` 都是 `xlsx`，所以不用 YAML 里的名字）；`notes_jsonl` 取 `id`，没有 `id` 时用 `note-<行序号>`；`markdown_file` 取 YAML 头的 `name` 或文件名。键只保留小写字母、数字和连字符。
- `subtask_id = skill_id`，`source_subtasks = []`。依据：`core/ids.py` 里的 id 都从轨迹派生，外部技能没有轨迹，`bank/merge/NNNN` 是先例（不绑定单个子任务的技能有自己的命名空间）。这样的 id 不满足 `ids.is_stage3_candidate`，没有任何"挑候选技能来接受或丢弃"的循环会碰到它们；读 `subtask_id` 的代码在拿不到轨迹时返回空（`live_measurement._task_of_skill` 捕获 `ValueError`）。
- id 必须能通过 `skill_dirname`（Agent Skills 目录名规则）。含 `claude`、`anthropic` 的名字会被该规则拒绝，导入器报错并指出条目，需要在来源里改名。两个条目映射到同一个键或同一个目录名时报错，不覆盖其中一个。
- origin 限小写字母、数字、连字符，至多 24 个字符。

### 1.4 用法

```python
from expedistill.stages.import_bank import import_external_bank
import_external_bank("path/to/best_skill.md", "markdown_file",
                     "runs/<实验名>_<时间>", origin="skillopt")
```

建议主智能体接入的子命令（`cli.py`，本包没有改它）：

```
expedistill import-bank --source PATH --format {markdown_file,skill_folder,notes_jsonl} \
    --run-dir DIR --origin NAME
```

导入后用同一个 `--run-dir` 运行 `expedistill run --config <只含 test 相位的配置> --run-dir DIR`，第一次 run 完成冻结，再进入 test 相位。
已用真实产物试过（临时目录，没有写入任何已有 run）：SkillOpt 的 `ckpt/alfworld/gpt5.5_skill.md`（1 条）、Trace2Skill 的 `released_skills/`（4 条，29 个附属文件被列为未导入）、UpSkill 的 `curated_acp`（9 条）、AgentBrew 的一份已冻结池（1 条）。

### 1.5 单元测试与端到端检查

- `tests/unit/stages/test_import_bank.py`：三种格式、id 与目录名、拒绝条件、写入范围、字节级确定性、转换函数。
- `tests/unit/stages/test_import_bank_test_phase.py`：导入微型库后，经 `cli.run_experiment`（`run` 子命令的入口，包含 stage-0 冻结）跑只含 test 相位的 run，对 `folder`、`whole_bank`、`retrieval` 三种交付各检查一次，来源分 `notes_jsonl` 与 `skill_folder` 两种：库里全部技能是 accepted，`phase_summary.json` 的 `bank_reference` 等于对这些技能重新计算的值，交付内容与重新调用渲染函数的结果一致（folder 的目录、YAML 描述等于来源摘要、正文等于来源文本；whole_bank 的块等于 `format_skills_block`；retrieval 的审计文件列出全部技能）。

### 1.6 转换函数

产物格式能从基线代码确定，又不属于上面三种来源格式时，`import_bank.py` 提供纯函数把它转成 `notes_jsonl` 文本（测试用按代码里格式手写的小样例）：

| 函数 | 输入 | 输出 |
|---|---|---|
| `expel_rules_to_notes_jsonl(rules)` | ExpeL 的洞见字符串列表（`rule_items` 或 `cache_rules[折]`） | 每条洞见一行，`id` 为 `rule-001` 起 |
| `upskill_curated_acp_to_notes_jsonl({类别: CLAUDE.md 文本})` | UpSkill 的 `curated_acp/<类别>/CLAUDE.md` | 每个类别一行，`id` 为类别名 |
| `agentbrew_frozen_to_notes_jsonl(manifest)` | TBE 复现的 `frozen/manifest.json`（已解析） | 每条笔记一行，正文是学生看到的渲染文本，`title` 为触发条件，`scope` 为范围 |

SkillOpt、GEPA、Trace2Skill（移植版）的产物是一份 Markdown，EvoSkill 与 Trace2Skill（上游）的产物是 `<名>/SKILL.md` 目录，已经是来源格式，不需要转换函数。EvoSkill 的最佳程序要先用 git 取出（见 4.2）。

## 二、三种交付各自能做什么

| 交付 | 弱模型看到什么 | 对基线的含义 |
|---|---|---|
| `whole_bank` | 每个任务的提示词里放一个"## Skill rules"块，每条技能一个项目符号。**一条技能内部的换行会被合并成空格**（`inject/skills.py::_single_line`） | 适合"整份文档（或整张规则表）始终在提示里"的基线 |
| `folder` | 每条技能一个 `<名>/SKILL.md`（YAML 头 `name`、`description`，描述取自摘要），提示词里只有名称与描述的目录，模型自己打开；TB 用 Terminus 自带的 `<available_skills>`，ALFWorld 与 SearchQA 用 `ReadSkill[<位置>]`，每次尝试最多打开 3 张卡 | 适合"技能目录、按需读取"的基线 |
| `retrieval` | 只对任务指令做词汇检索（TF-IDF 余弦），参数 `top_k`、`max_categories`、`min_score`；类别来自技能的 `group`，没有 `group` 的技能各成一类；`min_score` 只卡第一名。**没有向量检索，没有轨迹示例检索** | 只能近似"取前 k 条笔记"的基线。TB 的 `Task.instruction` 只是任务名（指令在远端，见 `benchmarks/tb/plugin.py`），词汇检索在 TB 上只是在匹配任务名 |

## 三、总表

| 基线 | 训练产物 | 导入格式 | 交付 | 强模型：S / T |
|---|---|---|---|---|
| SkillOpt | `best_skill.md`，一份 Markdown，无 YAML 头，ALFWorld 约 13 KB | `markdown_file` | `whole_bank`（1 条） | 上游配置即可切换（优化模型与执行模型分开配置）；外层桥接只有 S |
| EvoSkill | git 分支 `program/<名>` 上的 `.claude/skills/<技能>/SKILL.md`（YAML 头 `name`、`description`） | `skill_folder` | `folder` | 命令行只有一个模型配置，T 要改代码（Harbor 模式可分开）；外层桥接只有 S |
| Trace2Skill | ALFWorld 与 SearchQA 的移植版：一份 Markdown；上游：技能目录（`SKILL.md` 加 `references/`） | 移植版 `markdown_file`；上游 `skill_folder` | `whole_bank`（移植版）；上游目录用 `folder`（附属文件丢失） | 各阶段各有模型参数，S 与 T 只改参数；外层桥接只有 S |
| GEPA | 最佳候选文本（外层桥接的 `state.json["skill"]`，几百字符） | `markdown_file` | `whole_bank`（1 条） | GEPA 自己有反思模型参数；外层桥接是 S，T 要改代码 |
| ExpeL | 洞见列表（`rule_items`，单句字符串）加成功轨迹池 | `notes_jsonl`（`expel_rules_to_notes_jsonl`） | `whole_bank`；轨迹示例检索无对应 | 三个入口各配一次 `agent.llm`，T 只在洞见提取入口用强模型 |
| AgentBrew | UpSkill：`curated_acp/<类别>/CLAUDE.md`；TBE 复现：`frozen/manifest.json` 里的六字段笔记池 | `notes_jsonl`（`upskill_curated_acp_to_notes_jsonl`、`agentbrew_frozen_to_notes_jsonl`） | `retrieval`（笔记池 `top_k=3, max_categories=2`；类别文档 `top_k=1`） | 仅 TB；教师与学生分开配置，T 是默认，S 要改配置（UpSkill 要改写死的模型名） |

## 四、逐个基线

### 4.1 SkillOpt

- **产物**：训练输出目录 `<out_root>/best_skill.md`，验证集上得分最高的快照（`pulled/SkillOpt/skillopt/engine/trainer.py` 约 1706、2041、2164 行写入；候选只在选择分严格更高时成为 best，所以没有任何提升时它等于种子）。`skills/skill_vNNNN.md` 是每步的当前版本，`steps/step_XXXX/candidate_skill.md` 含被拒绝的候选。一份 Markdown 字符串，没有 YAML 头：第 1 行 `# 标题`，`##` 分节，末尾可能有一对 `<!-- SLOW_UPDATE_START -->`…`<!-- SLOW_UPDATE_END -->` 标记（原样保留，原方法也把它们送给模型）。大小：`pulled/SkillOpt/ckpt/alfworld/gpt5.5_skill.md` 13,179 字节，SearchQA 9,941 字节。
- **导入**：`markdown_file`，摘要取第一个标题。
- **原方法的推理方式**：整份文档每次调用都送。ALFWorld：每一步的用户消息前缀 `## Skill Knowledge …`（`skillopt/envs/alfworld/rollout.py`）；SearchQA：系统提示里的 `## Skill` 段；TB：任务指令前缀 `## Skill Knowledge`（`TBE/src/tbench_eval/skills.py`）。没有检索，没有长度截断。对应 `whole_bank`，一条技能。**已知差异**：`whole_bank` 把文档的换行合并成空格，标题和表格的版面丢失（词句不变）；提示词里的包装句也与原方法不同。
- **在 ALFWorld 与 SearchQA 上训练**：
  - ALFWorld，评测环境版：`python -m controller_v3.skillopt_native_alfworld --condition original --out DIR [--env-workers N]`（`CV3/skillopt_native_alfworld.py` 约 124–133 行）。它用 ETE 训练器，替换成 alfworld-eval 的回合执行器，数据用 `pulled/SkillOpt/data/alfworld_path_split`（训练 39、验证 18、测试 134）。需要 `ALFWORLD_DATA`，用 `benchmark/alfworld-eval/.venv`，端点取自 `benchmark/llm_config.json` 里的 `alfworld-eval` 项。该脚本没有关闭测试集评测的开关（`eval_test` 保持默认的开）。未核验：派生出的工作进程里能否导入 `alfworld_eval`。
  - SearchQA 与上游完整训练器：`python scripts/train.py --config configs/searchqa/default.yaml --optimizer_backend openai_compatible --target_backend openai_compatible --optimizer_model X --target_model Y --out_root … --split_dir <SQA>/data/searchqa_split`（ALFWorld 把配置换成 `configs/alfworld/default.yaml`）。SQA 的数据切分（训练 400、验证 200、测试 1400）与协议已按 SkillOpt 对齐（`SQA/README.md`）。没有把完整训练器接到 SearchQA 评测环境上的桥接。
  - `CV3` 的 `condition_runner` 与 `full_campaign` 跑的是简化版（每个窗口 rollout、反思、合并、选择、应用，再在同一窗口上做闸门；缺少验证集闸门、轮次和慢更新，与论文里的训练器差别很大。
  - `ALF/README.md` 说明 ALFWorld 评测环境里的 SkillOpt 直接评测已发布的论文产物，没有训练步骤。
  - TB：`TBE/scripts/run_skillopt_closed.py --epochs 1 --batch-size 45 --config configs/skillopt_tb2_e2b.yaml --tb-config configs/terminalbench_e2b.yaml`（封闭集：89 个任务的同一个池）或 `run_skillopt.py --config configs/local/skillopt_tb2.yaml`（41/20/28 切分）。它们使用 TB2.0 清单与 `SkillTerminus2`；没有在 TB2.1 或 small20 切分上运行的配置，未核验。已有的一次 `e2b_skillopt_b45` 运行结束时仍是 937 字节的种子（所有批次都因没有补丁而跳过）。最终产物取 `closed_set_summary.json["final_skill"]`。
- **强模型的作用**：
  - 上游训练器：执行模型（`target`）与优化模型（`optimizer`）是两个独立角色，**S 与 T 只改配置**：S 是两者同一个模型；T 是 `--optimizer_model` 与 `--target_model` 不同。各自的端点用环境变量 `OPTIMIZER_OPENAI_COMPATIBLE_{BASE_URL,API_KEY,MODEL,…}` 与 `TARGET_…`（`pulled/SkillOpt/skillopt/model/openai_compatible_backend.py` 约 73–96 行，导入时读取，所以要在启动前导出）。优化模型的输出上限默认 8000。
  - 外层桥接（`CV3`）把一个端点同时用于执行与优化，只有 S；做 T 需要把优化客户端换成第二个配置项（`skillopt_native_alfworld.py` 约 160 行与 243–248 行附近），属于代码修改。
  - TB 驱动：学生在 `--tb-config` 的 `agent.*`，教师在 `model.optimizer` 加 `qwen_chat_base_url` 一组，S/T 是配置修改；做 T 时注意 `qwen_chat_thinking_mode: disabled` 会向网关发 `chat_template_kwargs`，严格网关会拒绝，教师侧改 `optimizer_qwen_chat_thinking_mode: server_default`。

### 4.2 EvoSkill

- **产物**：EvoSkill 把每个程序存成 git 分支 `program/<名>`，分支顶端有 `.claude/program.yaml` 与 `.claude/skills/<技能>/SKILL.md`（`pulled/EvoSkill/src/registry/manager.py` 1–6、34–35 行）。前沿程序用标签 `frontier/<名>`，数量默认 3。最佳程序是前沿标签里 `metadata.score`（验证集均值）最高者（`get_best_from_frontier`，366–376 行），可能就是 `base`。`evoskill diff` 取字母序最后的标签，不能用来找最佳。当前检出的工作树不一定是最佳，要用 `git show program/<最佳>:...` 或 `git archive`。SKILL.md 的 YAML 头必填 `name`（等于目录名）与 `description`，可选 `compatibility`。仓库里没有演化出的真实实例，只有随项目带的种子技能（`brainstorming` 3,787 字节，`skill-creator` 17,836 字节）；`program.yaml` 的结构是读代码推断的。每轮最多新增或改一个技能（`runner.py` 520–540 行），默认 7 轮。`prompt_only` 模式写的提示文件不在提交范围内，要用默认的 `skill_only`。
- **导入**：`skill_folder`。取出最佳程序的技能目录：`git archive program/<最佳> .claude/skills | tar -x -C <空目录>`，源目录为其中的 `.claude/skills`。随项目带的两个种子技能原方法的智能体也能看到；是否保留由主智能体决定，建议保留并在报告里说明（与原方法的推理一致）。目录里的 `scripts/`、`references/` 不导入，会被列在 `BANK_ORIGIN.md`。
- **原方法的推理方式**：EvoSkill 自己不往提示里放技能，查询是原始问题字符串；技能靠 Claude Code 的 `Skill` 工具按需发现与加载（其他适配：opencode、openhands、codex、harbor 各自挂载 `.claude/skills`）。对应 `folder`。ALFWorld 与 SearchQA 上的 folder 交付由 `ReadSkill[...]` 实现，与 Claude Code 的机制近似，不相同。
- **在 ALFWorld 与 SearchQA 上训练**：EvoSkill 仓库没有这两个基准的适配器（输入只有 CSV 和 Harbor 任务）。要跑真实的 EvoSkill 循环，需要写一个继承 `Agent` 的回合执行类和打分器，这两样现在都不存在（未核验是否可行）。外层 `CV3` 里名为 `evoskill` 的桥接与真实的 EvoSkill 循环差别很大：ALFWorld 桥接每个窗口调用一次模型，直接改写整段技能字符串，窗口得分上升就接受（`CV3/alfworld_bridges.py` 588–604 行）；SearchQA 桥接每个窗口调用一次 `SelfImprovingLoop._mutate`，生成器与提议者都指向同一个端点，仓库是每个窗口临时建的 git 目录，且按读代码的结果，接受判定恒为真（`CV3/native_bridges.py` 592–708 行；这一点由代码推出，没有运行）。它们的最终技能是 `<out>/<域>/evoskill/<条件>/state.json["skill"]`，一个没有 YAML 头的字符串，可以作为 `markdown_file` 导入（此时对应 `whole_bank`，与技能目录不同）。命令：`python -m controller_v3.full_campaign --domains searchqa alfworld --methods evoskill --out-root DIR --preflight-report R`；单条件：`python -m controller_v3.condition_runner --method evoskill --condition original --domain … --train-path … --heldout-paths … --out …`。`CV3/paired_experiment.py` 自己注明这是"桥接冒烟时间表，不是完整的上游训练框架"。
- **强模型的作用**：命令行里基础、技能提议、提示提议、技能生成都用同一个 `harness.model`，默认 `anthropic/claude-sonnet-4-6`；没有切换 T 的配置项（要改 `HarnessConfig` 并把模型传给 `make_skill_proposer_options`、`make_skill_generator_options`）。Harbor 模式把执行者（`harbor.inner_model`）与提议者、生成者（`harness.model`）分开，是干净的 T。Claude 通道只接受 Anthropic，没有读到自定义端点配置（弱模型能否作为 S 的执行者与提议者，取决于这一点，未核验）。外层桥接只有 S。

### 4.3 Trace2Skill

三条流水线要区分：

1. 上游（`pulled/Trace2Skill`，电子表格任务）：分析、MAP/REDUCE/TRANSLATE/APPLY/VERIFY 的演化器，输出一个技能目录。
2. `ALF` 与 `SQA` 的 `scripts/run_trace2skill.py`：移植版，一次 LLM 整合调用产出一份 Markdown。
3. `CV3` 桥接：对占位的失败记录调用 `SkillEvolver.run_evolution`，是控制器计时的冒烟桥，不含分析员、成功分析与闸门。

- **产物**：
  - 上游：直接修改 `--skill-dir`（先备份到 `<名>_backup_<时间>`），只允许改 `SKILL.md` 与 `references/*.md`。已发布样例 `pulled/Trace2Skill/released_skills/`（4 个）：`xlsx-35B` SKILL.md 5,367 字节加 6 个附属文件，`xlsx-122B` 21,786 字节加 6 个，`trace2skill-xlsx-35B-combined` 22,255 字节，`trace2skill-xlsx-122B-combined` 45,673 字节加 13 个附属文件及 `recalc.py`。`xlsx-35B` 的 YAML 头 `name` 为 `xlsx-35B`，两个 combined 技能保留种子的头：`name: xlsx` 与长 `description`。
  - 移植版：`$T2S_WORK/trace2skill_alfworld.md` 与 `trace2skill_searchqa.md`；必须以 `#` 开头，没有 YAML 头。ALFWorld 种子约 25 行（约 1 KB），SearchQA 种子 16 行。
  - TB（`TBE/src/tbench_eval/trace2skill/official.py`）：`<epoch>/official_evolution/skill/SKILL.md` 加 `references/*.md`；另有合并成一份的 `<epoch>/trace2skill_terminalbench.md`（观测到 42 KB，带 YAML 头，附属文件以 `## Bundled skill file: references/…` 小节并入）。最终产物取 `closed_set_summary.json["final_skill"]`。
- **导入**：移植版与 TB 的合并文档用 `markdown_file`；上游或 TB 的技能目录用 `skill_folder`（只导入 SKILL.md，附属文件列入 `BANK_ORIGIN.md`；以键名区分，不依赖 YAML 里的 `name`）。
- **原方法的推理方式**：上游 `cli_skill_preloaded_agent` 发现 `<目录>/*/SKILL.md`，把第一个技能的完整正文放进系统提示的 `<skill_content>`，并告诉智能体技能目录，附属文件按需读取；ALFWorld 移植版把整份文件每一步都前置在用户消息里（`## Skill Knowledge`），SearchQA 放进系统提示的 `## Skill`；TB 前置在任务指令前并把整个包上传到 `/opt/trace2skill`。没有找到长度上限。对应：移植版与 TB 合并文档用 `whole_bank`；上游目录用 `folder`，其附属文件丢失（若要保留，需要把它们按链接顺序内联进 SKILL.md，这属于另做的转换，本包没有）。**已知差异**：同 SkillOpt，`whole_bank` 合并换行。
- **在 ALFWorld 与 SearchQA 上训练**（读代码确认的命令，见 `ALF/README.md` 578–619 行与 `SQA/README.md` 94–114 行）：
  - ALFWorld（环境变量 `ALFWORLD_DATA`、`ALF_ENDPOINT`、`ALF_MODEL`、`ALF_LLM_KEY`、`OPENAI_API_KEY`、`SKILLOPT_SPLIT_DIR`；切分 训练 39、验证 18、测试 134）：先用 `scripts/run_eval_concurrent.py --method trace2skill --shards 8 … --unified-args "--split train --items $P/train/items.json --seed 42 --record-trajectory --skill-path $T2S_SEED"` 跑训练轨迹，再 `scripts/run_trace2skill.py $T2S --skip-analysis --skip-consolidate` 准备工作区，再 N 个进程做分析（`--skip-prepare --skip-consolidate --analysis-shards N --analysis-shard-index i`），再 `--skip-prepare --skip-analysis` 做整合，最后用 `--skill-path $T2S_SKILL` 评测。产物含 `analysis_summary.json`、`provenance.json`。没有代码比较演化后的技能与种子，演化结果总是被写出并评测。
  - SearchQA（环境变量 `MODEL_BASE_URL`、`MODEL_NAME`、`MODEL_API_KEY`、`T2S_KEY`、`OPENAI_API_KEY`、`SKILLOPT_MANIFEST_DIR`；训练 400、验证 200、测试 1400）：`run_trace2skill.py --base-url … --model … --workers 64 --skip-analysis --skip-consolidate --skip-eval --work-dir outputs/trace2skill_searchqa`，然后 32 个分析分片，再整合。
  - TB：`TBE/scripts/run_trace2skill_closed.py --epochs 1 --config configs/terminalbench_e2b.yaml --work-dir …`（默认 89 个任务），或 `run_trace2skill.py --config configs/local/terminalbench.yaml --model … --base-url …`（41/20/28 切分）。TB2.1 与 small20 切分未核验。
  - 未核验：没有执行 `--help` 或任何运行；上游命令行在缺少 `skills/skill-creator/scripts/quick_validate.py` 时会 `sys.exit(1)`（该文件不在仓库里）。
- **强模型的作用**：上游和移植版的分析员、整合器各有 `--model`、`--base-url`、`--api-key`，**S 与 T 只改参数**：S 是所有阶段用同一个模型；T 是执行（`run_eval_concurrent.py`）用弱模型，分析与整合用强模型。移植版的 `run_trace2skill.py --model` 同时驱动分析员和整合器，所以要用 `--skip-train`、`--skip-eval`，把训练轨迹与评测改由 `run_eval_concurrent.py` 用弱模型单独跑。TB 驱动的教师用 `--model/--base-url/--api-key`，学生在 `--config` 的 `agent.*`；注意封闭集驱动把 `--api-key` 导出成 `OPENAI_API_KEY`，教师与学生的密钥不同时学生子进程可能继承教师密钥（读代码推断，未运行）。`CV3` 桥接把执行与演化绑在一个客户端上，只有 S。

### 4.4 GEPA

- **产物**：`GEPAResult.candidates` 是"组件名到文本"的字典列表，`best_idx` 是验证得分最高者（首个最大值）；字符串种子被包装在组件名 `current_candidate` 下（`pulled/GEPA/src/gepa/core/result.py`、`gepa_launcher.py`）。外层桥接只有一个组件，最终技能是 `<out>/<域>/gepa/<条件>/state.json["skill"]`（一个字符串；ALFWorld 存档里最长 686 字符，一个 `# ALFWorld skill` 下的项目符号列表）。GEPA 原生目录 `outputs/optimize_anything/<…>/gepa_state/` 里的 `candidates.json` 没有分数，要找最佳候选需要读 `gepa_state.bin`。另有 `gskill` 示例，种子是 `{"skills": ""}`，产物是 `<run_dir>/prompts/best_skills.txt`。
- **导入**：`markdown_file`（桥接的 `state.json["skill"]` 或 `best_skills.txt`）。多组件候选没有现成转换，因为外层现有工具只产生单组件候选；需要时按组件名分 `##` 小节自行拼接。
- **原方法的推理方式**：最佳候选文本整体替换提示里的那个组件：SearchQA 进入系统提示的 `## Skill`，ALFWorld 前置在每一步的用户消息，`gskill` 替换 `{{ skills }}` 占位符。对应 `whole_bank`，一条技能。同样有换行合并的差异。
- **在 ALFWorld 与 SearchQA 上训练**：**现有外层工具只实现了 GEPA 的一小部分，称不上完整的 GEPA 训练**。
  - 桥接的每个窗口最多一次 GEPA 提议（`max_candidate_proposals: 1`，`CV3/alfworld_bridges.py` 576 行），GEPA 的验证集就是正在训练的窗口，提议者就是执行模型本身。
  - SearchQA 桥接设 `max_evals = len(items)` 且验证集等于这批题，种子评估就用完全部预算，主循环从不运行（`CV3/native_bridges.py` 467、477–478 行；ALFWorld 桥接用 `2*N+6` 避开了这一点）。存档佐证：SearchQA 的 GEPA 存档里最终技能等于 63 字符的种子，每次尝试都是未接受；17 份 GEPA 存档里有 14 份每次尝试都未接受。
  - 命令：`benchmark/alfworld-eval/.venv/bin/python -m controller_v3.alfworld_paired_experiment --methods gepa …`（`CV3/README.md` 78–86 行，需要 `ALFWORLD_DATA`、`ALFWORLD_WORKER_START_METHOD=fork` 与含 `repos/pulled/GEPA/src` 的 `PYTHONPATH`）；SearchQA：`python -m controller_v3.paired_experiment --methods gepa --data-path … --heldout-path … --out-dir …`；整体活动：`controller_v3.preflight` 后 `controller_v3.full_campaign --domains searchqa alfworld --methods gepa …`。
  - 没有找到 `benchmark/alfworld-eval` 与 `benchmark/searchqa-eval` 中的 GEPA 入口，也没有"多轮迭代加独立验证集"的脚本。要得到真正的 GEPA 训练，需要另写调用 `optimize_anything` 的脚本（反思模型配 `engine_config={"reflection": {"reflection_lm": …, "reflection_lm_kwargs": {...}}}`），现在不存在。
- **强模型的作用**：GEPA 自己的反思模型参数 `reflection_lm`（litellm 模型名，`reflection_lm_kwargs` 里可放 `api_base`、`api_key`）与执行模型分开，是配置项；`gepa.optimize` 另有 `task_lm`；`gskill` 的 `--model` 与 `--reflection-model` 分开（后者默认 `gpt-5.2-pro`，做 S 要显式设成弱模型）。外层桥接把反思放在同一个 `LedgerLLM` 里，是 S；做 T 需要新建第二个客户端传给提议者，并抬高反思调用的 `max_tokens`（默认 512 限制技能长度）。

### 4.5 ExpeL

- **产物**：全部是 pickle 的智能体快照（`ExpelAgent.__dict__` 过滤后的列表），没有向量库或 JSON。`train.py` 写 `logs/alfworld/expel/<run>.pkl`；`insight_extraction.py` 写 `logs/alfworld/expel/extracted_insights/<run>.pkl`；每个 pkl 是快照列表，取 `[-1]`。洞见在 `cache_rules`（折号到字符串列表）、`rule_items`（最后一折的字符串列表）、`rule_items_with_count`（带计数）与 `rules`（编号文本）。洞见是以句号结尾的单句，数量默认上限 20（README 的 ALFWorld 命令用 10，上限是软限制）。成功轨迹池 `succeeded_trial_history` 是任务到轨迹的字典，每个任务至多一条成功。仓库里没有真实的洞见样例（`logs/` 被忽略）。反序列化需要 `utils.Count` 与 `memory.episode.Trajectory` 可导入。
- **导入**：先取出字符串列表，再 `expel_rules_to_notes_jsonl(rules)` 转成 `notes_jsonl`（`id` 为 `rule-001` 起，顺序保持），再导入。原生协议是 2 折交叉：第 k 折评测用的规则来自另一折的任务，没有现成的"全局规则集"命令；要得到一份用于独立测试集的规则，需要一个小驱动调用 `create_rules(全部训练任务)`（读代码推断，未运行），或者选定某一折的 `cache_rules[k]` 并在报告里写明。
- **原方法的推理方式**：全部洞见始终放进提示（`insert_before_task_prompt`，模板句 "The following are some experience you gather on a similar task…"，后接编号列表），加上对同一 ALFWorld 任务类型的成功轨迹做相似度检索（FAISS，`all-mpnet-base-v2`，以任务文本为键，取 2 条放进示例位置，替换静态示例）。对应 `whole_bank`（每条洞见一个项目符号，包装句与编号不同）。**轨迹示例检索没有对应**，也没有向量检索；所以这一臂只度量洞见本身。洞见里写着 "Thought and Action"，假定 ExpeL 自己的 ReAct 协议，与 alfworld-eval 的 `<think><action>` 协议可能不完全贴合（推断）。
- **在 ALFWorld 与 SearchQA 上训练**：
  - 没有外层封装：`controller_v3`、`benchmark/alfworld-eval`、`benchmark/searchqa-eval` 里找不到 ExpeL。只能用它自己的入口：`python train.py benchmark=alfworld run_name=R1 testing=false resume=false agent.llm=<m>`，再 `python insight_extraction.py benchmark=alfworld load_run_name=R1 run_name=R2 agent.llm=<m> agent.max_num_rules=10 agent.success_critique_num=8 testing=false resume=false`，评测用 `python eval.py benchmark=alfworld load_run_name=extracted_insights/R2 run_name=R3 …`（`pulled/ExpeL/README.md` 168、202、237 行）。三个配置里 `testing` 默认是 true，此时用 `input()` 代替模型调用，必须显式写 `testing=false`；已有同名 `<run>.pkl` 时程序会用 `input()` 询问是否覆盖，要用新的 run 名。
  - 协议与评测环境不同：ExpeL 用纯文本 ReAct（`> think:` 与 `> 动作`），没有可用动作表，最多 20 步，重复动作终止；alfworld-eval 用 `<think><action>` 标签、带可用动作表、最多 50 步（引擎同为 `AlfredTWEnv`）。环境版本也不同，ExpeL 要独立的 Python 环境（未核验能否与 alfworld-eval 共存）。
  - **训练池就是测试集**：ExpeL 原生的 ALFWorld 任务文件有 134 局，全在 `valid_unseen` 下，每类数量与 alfworld-eval 描述的 134 局测试集一致（按局文件名核对没有做）。要得到训练集产物，需要按同样格式另写一个任务文件（每个任务要有重置环境得到的初始观察）。`train.py` 末尾的统计图要求恰好 134 个任务（`utils.py` 约 347 行），其他规模会在产物落盘之后报错。
  - SearchQA：不支持（仓库只含 hotpotqa、fever、alfworld、webshop）。接入需要新环境类、提示词文件（约 15 项，接进 `prompts/__init__.py` 的每张表）、`setup_vectorstore` 新分支，并改统计函数里的任务数断言。
  - 对 OpenAI 的依赖：`ChatOpenAI` 没有 `base_url` 参数，模型名必须含 `gpt` 且 tiktoken 认得；设置 `OPENAI_API_BASE` 是否可用取决于 langchain 0.0.181 与 openai 0.27.7（推断，未验证）；vLLM 的 `--served-model-name gpt-3.5-turbo` 别名可以通过名字检查。
- **强模型的作用**：只有一个键 `agent.llm`，同一进程里同时用作执行、反思与洞见提取；洞见提取是独立入口，有自己的 `agent.llm`。S 是三个入口都用弱模型；T 是 `train.py` 与 `eval.py` 用弱模型，`insight_extraction.py` 用强模型（README 的做法）。是配置修改。

### 4.6 AgentBrew

论文里的"AgentBrew"有两条代码谱系，需要主智能体确认用哪条（任务说明写的是 UpSkill 仓库）：

- **UpSkill**（`pulled/UpSkill`，TB 流水线 `tb_harbor_2.0`）：
  - 产物：`tb_harbor_2.0/curated_acp/<类别>/{CLAUDE.md, .claude/skills/solve-task.md, memory/feedback.md}`（`scripts/run_curator.py` 176–185 行），9 个类别、27 个文件、共约 76 KB，`CLAUDE.md` 单文件 1.5–4.5 KB（9 份合计 27,113 字节）；没有 `<名>/SKILL.md` 目录。最终库是 `curated_acp`，它不经过 Ralph 验证（Ralph 的修订只写回 `brew_acp/`，不进入 `curated_acp`）。
  - 推理：`scripts/run_serve.sh` 只把 `<类别>/CLAUDE.md` 复制到测试任务目录，类别取自 `split.json`（人工给定的类别），没有检索，没有目录列表，`solve-task.md` 与 `feedback.md` 不部署。Harbor 如何把任务目录里的 `CLAUDE.md` 交给智能体，本仓库里没有，未核验。
  - 导入：读每个类别的 `CLAUDE.md`，`upskill_curated_acp_to_notes_jsonl({类别: 文本})` 转成 `notes_jsonl`。交付最接近的是 `retrieval`、`top_k=1`（用词汇检索代替人工类别标签，是近似），也可以 `whole_bank` 放 9 份约 27 KB 的文档，这是另一种（更重的）条件。摘要取每份文件的第一个标题，实际标题质量不齐（例如 `debugging` 的第一个标题是 "Git Bundle Merge with Algorithm Resolution"），所以不适合 `folder`。
  - 训练命令（`tb_harbor_2.0/REPRODUCE.md`）：`scripts/run_baseline.sh --model student --split all`，`scripts/run_brew.py --student-job … --split train`，`scripts/run_curator.py --acp-dir …`，`scripts/run_ralph.py … --max-rounds 3`，`scripts/run_serve.sh --acp-dir …/curated_acp`。切分固定在 `split.json`（25 训练、64 测试）；需要 Docker 与 `.venv_harbor`，脚本里写死路径。未核验任何命令。
  - ALFWorld 与 SearchQA：**没有**。UpSkill 与 `controller_v3`、`benchmark/*` 里都找不到。移植要替换的东西：brew 与精炼是 `claude-code` 智能体在假任务容器里运行（从 `trajectory.json` 里用正则抓取 Write 与 Edit），学生轨迹取最后 15 条消息各截 250 字符，通过与否来自 Harbor 的 `result.json` 奖励，交付靠复制 `CLAUDE.md`；可复用的是 `===BEGIN_FILE===` 标记协议和 `parse-skill.py`（`upskill-build.sh` 253–282 行）。
  - 强模型：教师与学生各有配置（`configs/strong.env`、`configs/weak.env`），T 是默认；S 要改写死在多个脚本里的模型名（`run_brew.py` 208 行、`run_curator.py` 147 行、`run_ralph.py` 193、280 行、`run_serve.sh` 116 行）。Claude Code 版本（`cc-integration`）用 `UPSKILL_TEACHER` 与 `UPSKILL_STUDENT`，设成相同即 S。
- **`TBE` 的论文算法复现**（`TBE/docs/AGENTBREW.md`，TB2.1 的 25/64 或 small20 切分，Tencent 执行器）：
  - 产物：`<work-dir>/frozen/manifest.json`，格式 `{"payload": {"memory": {"notes": {键: {"note": {...}, "version", "provenance"}}, "stats", ...}, "index", "contract_hash"}, "digest"}`；笔记六个必填字段 `note_key`、`trigger_pattern`、`corrective_rule`、`minimal_steps`（1 到 20 条）、`tags`、`quality_score`，另有 `scope`（可空，已提交的笔记总有 7 个范围之一：git、build、linux、data、numerical、security、machine-learning）。池很小：small20 的正式运行从 20 个训练任务里冻结了 4 条笔记。读了磁盘上的一份真实冻结池确认了这个结构。
  - 推理：每个任务按范围路由到 `scope_count=2` 个范围，取最多 12 个候选，按 0.8 倍 BGE-small-en-v1.5 余弦加 0.2 倍词汇 Jaccard 排序，再按质量与频率重排，保留 `top_k=3`，渲染成 "Reusable experience notes:" 加每条的 Note、Trigger、Rule 与编号步骤，作为一条系统消息送入。
  - 导入：`agentbrew_frozen_to_notes_jsonl(解析后的 manifest.json)`：每条笔记的文本与送给学生的渲染完全一致（Note、Trigger、Rule 行与编号步骤），触发条件作摘要，`scope` 作 `group`。交付 `retrieval`，`top_k=3`，`max_categories=2`，`min_score=0.0`（AgentBrew 没有分数闸门）。**差异**：本仓库的检索只做词汇匹配，没有 BGE 向量，类别由 `derive_categories` 合并，不等于 AgentBrew 的范围路由；TB 的任务指令只是任务名，所以 TB 上的检索是在匹配任务名。
  - 命令：`scripts/run_agentbrew_remote.py --work-dir D --config configs/agentbrew_tb21_small20_batch.yaml --relay-source R --with-vanilla [--resume]`（总是 `--phase all`，即 brew 加 serve）；只 brew：`scripts/run_agentbrew.py --config … --work-dir … --phase brew`，教师环境变量要直接设置（`AGENTBREW_TEACHER_KEY`，可选 `AGENTBREW_TEACHER_MODEL`、`AGENTBREW_TEACHER_API_BASE`；学生 `TBENCH_LLM_KEY`，沙箱 `E2B_API_KEY`、`E2B_DOMAIN`）。远程包装脚本写死了 Tencent 中继。未核验任何新运行。
  - 强模型：学生 `agent.model_name`（`configs/agentbrew_tb21_small20_eval.yaml`），教师 `teacher.model`、`teacher.api_base`、`teacher.api_key_env`、`max_tokens`（`configs/agentbrew_tb21.yaml`）。原则上 S 与 T 只改配置；提示词 `DRAFT_SYSTEM` 仍写着 "strong teacher"；中继路径只用强教师跑过，弱教师未核验；配置变化会改变合同哈希。

## 五、需要主智能体或用户决定的事项

1. **整文档基线（SkillOpt、GEPA、Trace2Skill 移植版）用 `whole_bank` 会合并换行**：词句完整，标题与表格版面丢失，提示包装句也与原方法不同。本仓库当前没有"把一份文档原样放进提示"的交付。要消除这个差异，需要新增一种交付，或接受并在论文里写明。
2. **外层桥接只是各方法的简化实现**：`CV3` 里的 EvoSkill、GEPA、Trace2Skill、SkillOpt（窗口式）桥接分别是简化版或冒烟桥，GEPA 在 SearchQA 上还有预算缺陷。论文主表若称这些为"该方法的训练结果"，需要先决定用哪条流水线（上游完整训练器、移植版，还是桥接），并在表注里写明。
3. **ExpeL 的训练池就是 ALFWorld 测试集，且没有 SearchQA 适配**：不能直接拿它原生的训练产物在这 134 局上测试。
4. **AgentBrew 取哪条谱系**（UpSkill 的类别文档，还是 `TBE` 的六字段笔记池）。两条的转换函数都已提供。
5. **T 设定需要改代码的基线**：EvoSkill（命令行）、GEPA 与 SkillOpt 的外层桥接、UpSkill 脚本（S 方向）。上游 SkillOpt、Trace2Skill、ExpeL 与 `TBE` 的 AgentBrew 只改配置。
6. 技能在 folder 里的名字：导入的技能在 folder 里名为 `import-<origin>-<键>`；若想让弱模型看到基线原来的技能名（例如 EvoSkill 的 `brainstorming`），需要放宽 id 规则，目前不这样做。
7. TB 上还没有 TB2.1/small20 的 SkillOpt 与 Trace2Skill 训练配置：`TBE` 的封闭集驱动写死了 `tb2_tasks.json`。
