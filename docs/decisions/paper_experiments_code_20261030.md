日期：2026-10-30
一句话：论文四组实验所需的代码改动清单、接口约定、实验矩阵与各工作包的文件范围。
对应 log.md：2026-10-30 20:12「论文实验代码」条目

## 目标

论文有两个主张：（1）强模型教弱模型时，模型之间的异质使强模型写的技能在弱模型上执行不一致；
（2）渐进式披露下，强模型写的触发条件在弱模型上常常失效，触发条件必须放进技能演化的循环里修正。
方法固定为 exam + trial_level：`baseline → learn_add → exam → learn → exam → learn_trial → test`。
三个基准：TB（39 个测试题，30 个预留题不参与测试）、ALFWorld（134 局）、SearchQA（1400 题）。
ALFWorld 与 SearchQA 取自外层 `benchmark/alfworld-eval`、`benchmark/searchqa-eval`，本仓库只做插件接入，
不开发评测环境。

## 实验矩阵

| 实验 | 要回答的问题 | 臂 |
|---|---|---|
| 一 主表 | 方法整体有效吗 | 本方法；基线 SkillOpt、EvoSkill、Trace2Skill、GEPA、ExpeL、AgentBrew（TB 只跑最强的 3 个）；裸跑 |
| 二 执行不一致 | 强模型写的技能给弱模型用，执行不一致吗 | 教师→学生：D→D、D→Q、Q→Q、Q→D。教师同时担任执行者和作者；examiner 与 judge 固定为 D |
| 三 子任务归因 | 切成子任务比整条轨迹好吗 | `K: 5`（本方法）对 `K: 1`（整条轨迹一个单元） |
| 四 触发条件 | 触发条件为什么必须在循环里演化 | A0 不演化；A1 只演化正文；A2 只演化触发条件；A3 联合；A1、A2、A3 都关掉 `new_skills`，彼此只差 `revise_scope`。另有九相位的分阶段对照：第一轮只改正文、第二轮只改触发条件，对照是两轮都不受限 |

D = deepseek-v4-flash，Q = qwen3.6-flash-distill。实验二的纸面考试矩阵与实验四 a（同一份技能和考题，
换答题者）都用 `exam-replay`：同一批考题，不同答题模型，指标是召回、误触发、计划通过率。

## 已完成的共用底座（本文件之前）

- `ReviewParams` 新增三个开关：`revise_scope`（`both` / `body` / `trigger`，限制改写能改哪一部分）、
  `new_skills`（是否把聚合出的跨任务需求写成新技能）、`accept_new`（P02 写出的技能直接成为已接受）。
  触发条件 = `summary` + `[When to use]`；正文 = `[Steps]` + `[Do not use when]`。详见 `ablation_switches_20261030.md`。
- 考试路径的改写（`REWRITE`）带范围提示，输出再由 `blocks/author.py::enforce_scope` 拼接：范围之外的部分
  逐字节取自当前版本。实战路径不支持 `revise_scope`，配置后报错。
- 学习提示词的基准词汇槽：`ModelHandle.prompt_defaults` + `BenchmarkPlugin.learning_context`，
  `call_model` 按 GENERIC、句柄默认值、调用方变量三层填充；词汇随 manifest 写入 `learning_context`。
- 本地基准的渐进式披露：见 `local_skill_folder_20261030.md`。

## 工作包与结果

| 包 | 内容 | 结果 |
|---|---|---|
| A 学习词汇 | 学习提示词的 benchmark 词汇槽，TB 词句不变，ALFWorld 与 SearchQA 各一份 | 完成，见 `learning_context_20261030.md` |
| B 分析工具 | 适用性与遵循度裁判、触发指标、`exam-replay` | 完成，见 `skill_use_analysis_20261030.md` |
| C 基线导入 | `import-bank`、三种来源格式、三个转换函数、六个基线的映射 | 完成，见 `external_baselines_20261030.md`；另加 `whole_bank` 的 `verbatim` 选项 |
| D1 配置生成 | `scripts/run/build_paper_configs.py`，74 份配置 | 完成 |
| D2 统计与表格 | 配对统计、`paper-tables` | 完成，见 `paper_statistics_20261030.md` |
| E 接线与验证 | CLI 四个子命令、`K: 1` 与开关的相位级测试、黄金矩阵重生、文档 | 完成 |
| F 端到端冒烟 | 假模型在真实 SearchQA 插件上跑通七相位与各变体 | 完成，见 `smoke_findings_20261030.md` |

## 约定

- 并行期间，子智能体只改自己的文件范围；共用文件（`cli.py`、`README.md`、`AGENTS.md`、
  `tests/unit/stages/test_stage0.py`、`prompts/__init__.py`）由主智能体修改。
- 新增 prompt 会改变全部 manifest 的提示词名称集合；子智能体把新名称写进报告，由主智能体更新冻结名称集。
- 分析与导入工具不写已有 run 目录。
- 每个包交付：代码、单元测试、一份决定文档（`docs/decisions/`）、报告（公开函数签名、已知限制）。
