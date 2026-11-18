日期：2026-11-18
一句话：新增 benchmark 插件 `swebench`，让我们的方法在 SWE-bench Verified 上完整运行一遍，执行交给外层 `benchmark/swebench-eval` 的现有运行器。
对应 log.md：2026-11-18「SWE-bench 插件与桥接脚本」条目

# SWE-bench 插件

## 背景

外层工作区的 `benchmark/swebench-eval`（独立仓库）已经在 SWE-bench Verified 上测过 Vanilla、Trace2Skill、AgentBrew、SkillOpt：mini-SWE-agent（只有 bash 工具）在 E2B 沙箱里改代码，官方 swebench 流程判分。学生是 `qwen3.6-flash-distill`，分析模型是 `deepseek-v4-flash-0731--ali`，test 30 题 × 3 次的 avg@3 分别是 0.600、0.656、0.622、0.622。三个方法的技能文本都放在提示词 `<pr_description>` 前面的 `## Skill Knowledge` 块里。

我们的方法需要在同一个环境、同一个学生、同一份 test 上得到成绩，和这四行放进同一张表。

## 用户的决定

1. 技能用文件夹交付，和我们在 TB、ALFWorld 上的做法相同。
2. 加人工写的规则（`SWEBENCH_DISCIPLINE`），让我们可以预先规定某些问题的处理方式，给后续优化留出余地。
3. 强模型的 baseline 重新运行。
4. test 直接和 swebench-eval 的 `runs/test_bare`（Qwen，30 题 × 3 次）比较，不加 Vanilla + 人工规则对照组。
5. 读卡用强制规则：practice 和 test 的文件夹单元在第一条任务命令之前必须 `cat` 至少一张卡（`read_rule: mandatory`）。冒烟里可选读卡规则下的带库单元一张卡都没有打开。

## 需求

1. 相位序列 baseline → learn → practice → learn → practice → learn → test，相位参数、轮数、具名规则与 `tb21_paper_e1_ours.yaml` 相同。
2. 模型：`executor_weak` 是 `qwen3.6-flash-distill`；`executor_strong`、`segmenter`、`author`、`rewriter`、`judge` 是 `deepseek-v4-flash-0731--ali`。两个执行角色的输入上限都是 131072，与 swebench-eval 的设置相同。
3. agent、沙箱、判分、请求参数、步数上限、断网方式全部用 swebench-eval 现有的实现，插件不重写其中任何一项。
4. 划分：train 是 `split_v1.json` 中金标准补丁能判过的 30 道 train 题，test 是同样条件下的 30 道 test 题；val 不列入配置。两份列表在 `scripts/run/paper_splits.py`，配置由 `build_paper_configs.py` 生成为 `swebench_paper_e1_ours.yaml`。
5. 交付：技能文件放在 agent 沙箱的 `/skills/<名字>/SKILL.md`（`/testbed` 之外，补丁里不会出现），提示词里列出技能清单，agent 自己 `cat`。
6. 提示词：插入位置与三个基线相同（`<pr_description>` 前，`## Skill Knowledge` 标题由桥接脚本加）。块内依次是：学到的 playbook、`## Skills` 与 `<available_skills>` 清单、读卡规则、人工规则 `SWEBENCH_DISCIPLINE`。读卡规则在 `read_rule: mandatory` 时是 `SWEBENCH_SKILL_READ_MANDATORY`（TB 强制读卡规则的读卡部分，题面换成 PR 描述，清单丢失时读 `/skills/catalog.md`），否则是可选的 `SWEBENCH_SKILL_READ`；配置 `swebench_paper_e1_ours` 的 practice 和 test 都用强制规则。baseline 相位（`delivery: none`）不插入任何内容，提示词与 test_bare 逐字相同。
7. 人工规则只根据 train 和 val 的轨迹起草，不看 test 轨迹，用户审阅后固定。
8. 失败诊断看到的判分信息：补丁是否应用成功、失败的测试名称、测试日志最后 40 行。金标准补丁和测试代码不提供。
9. 对照：`compare.against: external_campaign`，成绩文件由 node29 上的 `runs/test_bare` 导出到 `configs/seed/`，`model_key` 为 `qwen`。

## 两个仓库之间的接口

ExpeDistill（Python 3.10）与 swebench-eval（Python 3.12）不能在一个进程里运行。插件每执行一个单元，就用 swebench-eval 自己的环境启动一次桥接脚本：

```
<EXPEDISTILL_SWEBENCH_ROOT>/.venv/bin/python <EXPEDISTILL_SWEBENCH_ROOT>/scripts/run_unit.py run \
  --instance <任务 id> --out <目录> --model <模型 id> --max-input-tokens <N> \
  [--skill-block-file <文本文件>] [--skill-dir <本地技能目录>]

<EXPEDISTILL_SWEBENCH_ROOT>/.venv/bin/python <EXPEDISTILL_SWEBENCH_ROOT>/scripts/run_unit.py describe \
  --instance <任务 id> [--instance <任务 id> ...]

<EXPEDISTILL_SWEBENCH_ROOT>/.venv/bin/python <EXPEDISTILL_SWEBENCH_ROOT>/scripts/run_unit.py prompt \
  --instance <任务 id> [--skill-block-file <文本文件>]
```

`run`：

- 桥接脚本自己读取 swebench-eval 的 `.env` 放进进程环境；密钥不经过 ExpeDistill，也不出现在输出里。
- `--model` 必须等于 `.env` 的 `SWEBENCH_LLM_MODEL`（学生）或 `SWEBENCH_STRONG_MODEL`（强模型，本部署为 `deepseek-v4-flash-0731--ali`）。选强模型时，请求的模型名就是 `SWEBENCH_STRONG_MODEL`，端点、密钥、温度、思考强度、输出上限用 `SWEBENCH_REFLECTOR_*` 的值，并且不使用 `SWEBENCH_LLM_BASE_URLS` 的分流。其他值以退出码 2 拒绝。swebench-eval 的分析模型 `SWEBENCH_REFLECTOR_MODEL` 是同一网关上同一个模型的另一个 id（两者回报的模型名都是 `deepseek-v4-flash-0731`），强模型用 `SWEBENCH_STRONG_MODEL` 是为了让请求的 id 与我们配置里的模型名相同。
- `--max-input-tokens` 设置 agent 的输入上限。
- `--skill-block-file` 的文本按基线的方式插入：`with_skill_slot` 加插槽，`skill_block()` 加 `## Skill Knowledge` 标题；文本为空或省略时不插入任何内容。
- `--skill-dir` 下的全部普通文件在 agent 启动前写进 agent 沙箱的 `/skills/<相对路径>`；判分沙箱不写。省略时不上传。符号链接或越出目录的路径以退出码 2 拒绝。
- 单元目录是 `<out>/<任务 id>/rep0/`，内容与现有运行器相同（`unit.json`、`traj.json`、`patch.diff`、`skill_injected.txt`、`sandboxes.json`、`score/`）。同一个 `--out` 不重复使用：插件每次尝试给一个新目录。
- 运行器的基础设施重试（最多 3 次）在一次调用内完成。
- 正常结束时退出码 0，标准输出最后一行是一个 JSON：

```
{"status": "final" | "infra", "unit_dir": "...", "resolved": true | false | null,
 "exit_status": "...", "score_status": "...", "n_steps": 0,
 "prompt_tokens": 0, "completion_tokens": 0, "agent_wall_sec": 0.0, "error": "..."}
```

  `status` 为 `infra` 的情况：单元以基础设施失败关闭（`infra_failure`），或者判分状态是 `infra_error`。其余为 `final`。
- 退出码 2：参数或配置错误（未知模型、任务不在划分里、任务没有金标准对照或金标准判不过、技能目录有问题）。其他非零退出码：桥接脚本自身的错误。

`describe`：退出码 0，标准输出是一个 JSON 列表，每项 `{"instance_id", "repo", "problem_statement"}`；任务不在 `split_v1.json`、没有金标准对照或金标准判不过时以退出码 2 拒绝。

`prompt`：不创建沙箱、不调用模型，在标准输出打印 agent 收到的第一条用户消息（与 `run` 的渲染方式完全相同），用于检查。

插件一侧的映射：

- 退出码 0 且 `status` 为 `final`：`RunOutcome(correctness=resolved)`；
- 退出码 0 且 `status` 为 `infra`：抛出 `InfrastructureError`，由 K1 重试；
- 退出码 2：抛出 `ConfigError`；
- 其他非零退出码：抛出 `CodeBugError`。

## 插件

位置 `src/expedistill/benchmarks/swebench/`，名字 `swebench`，任务 id 是 SWE-bench 的 instance id（例如 `django__django-16429`）。

- 环境变量：`EXPEDISTILL_SWEBENCH_ROOT`（swebench-eval 在本机的目录），在加载任务或执行时读取，导入时不读取。
- `load_tasks`：任务 id 列表，经桥接脚本 `describe` 取题面；`instruction` 是 `problem_statement`。
- `run`：
  - 没有技能文件夹且 `skills_block` 为空：不传 `--skill-block-file`；
  - 技能文件夹单元：块文本按需求第 6 条拼接，`--skill-dir` 传物化后的本地目录（`task.meta["skill_folder_dir"]`）；
  - 轨迹转换：每条 assistant 消息一步，`action` 是这一步的 bash 命令（多条按行拼接），`observation` 是随后的工具输出；
  - `meta`：`initial_prompt`（`traj.json` 里第一条用户消息）、`grader_report`（未解决的单元，按需求第 8 条）、退出状态、判分状态、步数、token、耗时、单元目录；
  - token 用量按 TB 插件的方式记进 `handle.usage`。
- 提示词全部登记在本目录下，名字以 `SWEBENCH_` 开头：执行提示词 `SWEBENCH_INSTANCE_TEMPLATE`（`swebench_bare.yaml` 模板的逐字副本）、`SWEBENCH_SKILL_READ`、`SWEBENCH_SKILL_READ_MANDATORY`（插件的 `skill_folder_mandatory_prompt_name`）、`SWEBENCH_DISCIPLINE`、失败诊断的替换提示词 `SWEBENCH_TRIAL_FAILURE_ANALYSIS` 和判分报告段 `SWEBENCH_DIAGNOSIS_GRADER_REPORT`。
- playbook 用方法自带的 `PLAYBOOK_UPDATE`，不提供 baseline 事实提取和必备部分检查。

## 规模

| 相位 | 单元 |
|---|---|
| baseline：train 30 题 × 强、弱各 3 次 | 180 |
| practice × 2：train 30 题 × 2 次 | 120 |
| test：30 题 × 3 次 | 90 |

共 390 个 agent 单元，按已有记录约 9.5 亿输入 token。

## 运行前检查

1. 假模型、不连网络，2 道题走完全部相位。
2. node29 上真实冒烟：2 道 train 题强、弱各 1 次，加 1 个带两张手写技能卡的文件夹单元；检查第一条用户消息、读卡检测、断网、补丁内容、token 记录。
3. `export_settings.py` 的设置快照与 test_bare 对比，除插入块以外相同。
4. 每个相位结束后审计；学到的技能库用 swebench-eval 的 `scan_artifacts.py --leak` 扫描。
