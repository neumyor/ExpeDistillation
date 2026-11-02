日期：2026-10-30
一句话：新开两个 run 所需的两处改动——`fork-run`（把已完结 run 的前 K 个相位拷进新 run）和 test 相位的 `executor`（让强模型用同一个库做 test）——的取舍、证据和已知混杂。
对应 log.md：2026-10-30 「分叉前缀与强模型 test 相位」条目

## 背景

用户要求在已完结的 P02–P07 正式 run 之上新开两个 run：

1. **多一轮学习**：复用该 run 的 P01–P05 产物，从 P06 起续跑；P06 不再是最终验收，
   所以序列变成 9 个相位：P06 学习、P07 照原 P05 的方式验收（exam）、P08 做最终修改
   决策、P09 照原 P07 的方式在 test 上跑。`checks_left` 要跟着更新。
2. **强模型做 test**：复用该 run 的 P06 产出的库（`bank:3bb3ee40d638`），沿用同样的
   7 个相位，只重跑 P07，但执行者是强模型（deepseek v4 flash）。

两件事都要求"新 run 里前几个相位是旧 run 的，后面的相位按新配置跑"。

## 为什么不能直接整目录拷贝

run 目录里的五个对象库是运行级共享的，只有 learn 相位会改它，而且是原地改。旧 run 的
P06 是最后一个 learn 相位，它：

- 对所有 41 个 P04 成员都改写了技能文件（状态、分组、废弃原因）；
- 新建了 7 个整理产物 `bank/merge/*`，把原件置为 `merged`。

所以旧 run 完结时的技能库是"P06 之后"的状态，而新 run 的 P06 要从"P05 之后"的状态出发。
实测（旧 run 的存储与 P06 账本）：

- 48 个技能文件的修改时间全部晚于 P05 的相位摘要；轨迹（120）、子任务（136）、考题（56）、
  作答（55）四个库没有一个文件晚于它；
- P04 的库产物（41 行：17 accepted、24 pending）与最终技能库逐条比较，差异只出现在
  `status` / `group` / `discard_reason` 三个字段，并且全部能被 P06 的 outcomes 和整理日志解释：
  13 条 pending→accepted→merged、9 条 accepted→merged、9 条 pending→discarded、
  8 条 accepted 只改了分组、2 条 pending→accepted 并分组；其余字段（内容、版本、摘要、
  改写史）逐字节相同；
- 最终库比 P04 产物多的 7 条都是 `bank/merge/*`，`merged_from` 全部指向 P04 成员。

另外，旧 P06 在 `checks_left == 0` 下运行：revise 转成 delete，新需求只记延迟、不写技能
（`trial_level_last_check_20261029.md`）。新序列里 P06 之后还有 P07 的验收，`checks_left`
是 1，行为不同，因此 P06 必须重跑，不能沿用旧结果。`checks_left` 本来就由冻结序列的
`decision.remaining_checks` 推出，改序列即更新，不需要单独维护。

## 候选与选择

### 分叉

| 方案 | 结论 |
|---|---|
| 整目录拷贝 | 否：技能库已被旧 P06 改写，见上 |
| `assemble-reuse` 加一档 | 否：它的白名单、负清单和 `phases/` 树断言都是为"过滤后的库"设计的，前缀拷贝要放宽其中的大半；该文件已有 1200 多行 |
| 一次性脚本 | 否：两个正式 run 的结论依赖它，按 AGENTS 第五节必须有测试、进仓库 |
| **新的 `fork-run` 子命令（`stages/fork_run.py`）** | 采用 |

`fork-run` 做的事（与 README 命令行一节一致）：

- 拷贝前 K 个相位的相位目录和 `stage1`–`stage4` 运行级目录（不含 `*.lock`）；不拷
  manifest、进度账本、用量账本、`work/`、`driver.*`、`run.lock`。manifest 由新 run 自己的
  第一次启动按新配置冻结；
- 身份核对在拷贝之前：新配置的前 K 个相位与源 run 相同、定义相同，其余影响这些相位的键
  （模型、深度、规则、预算、train 划分……）一致；后面的相位、test 划分、任务清单可以不同。
  被拷贝的 learn 相位在两条序列里必须处于同一侧：是否还有后续检查（`checks_left` 是否为 0，
  决定末次决策行为），以及是否序列最后一个 learn（只有最后一个跑配置的整理）。这两点是
  本次扩序列时唯一会让前缀结果"换了含义"的地方；
- 技能库：前缀之后源 run 还有 learn 相位时，按前缀最后一个 learn 的库产物重建（先核对库产物
  与旁边记录的 `bank_reference` 一致；其余已存技能必须能证明是前缀成员的后续合并产物，
  `req/` 子任务必须属于库产物的成员，否则拒绝），没有则原样拷贝；前缀之后若有会写共享
  对象库的 execute 相位（baseline / exam / live）则拒绝，test 相位自带库，不算；
- 源 run 必须已完结且空闲：前缀各相位有完成记录，没有 `run.lock`。

两个 run 的分叉点：

- run 1 取 `--through p05`。前缀里的 P02、P04 是 learn 相位：它们在源序列里后面还有 2 个和 1 个
  检查，在新序列里是 3 个和 2 个，都大于 0；两条序列里它们都不是最后一个 learn（源序列的是
  P06，新序列的是 P08）。源序列里 P05 之后还有 P06 这个 learn，所以技能库走重建；
- run 2 取 `--through p06`。P06 在两条序列里都是最后一个 learn、后面都没有检查；前缀之后
  只剩 test，技能库原样拷贝，P06 的库产物（`bank:3bb3ee40d638`）随相位目录一起过来。

被拷贝的 learn 相位在摘要里记录的 `checks_left` 是源序列下的值（P02 为 2、P04 为 1），
新序列下是 3 和 2。数值不同但"是否大于 0"相同，决策行为一致，这是分叉时校验的内容。

### 强模型做 test

test 相位原先写死弱模型（单元、轨迹、控制记录的角色、采样深度、取哪个模型句柄）。

| 方案 | 结论 |
|---|---|
| 配置里把 `executor_weak` 换成强模型名 | 否：复用的 P01 弱臂轨迹是 qwen 跑的，新 manifest 会把它们登记成 deepseek 的 |
| **`real_folder` 加 `executor` 参数** | 采用 |

`execution: {name: real_folder, executor: executor_strong}`，默认 `executor_weak`，旧配置
字节不变。`real_folder` 本来只允许在 test 相位和 `folder` 交付配对，所以不需要新增角色检查；
plain `real` 的 test 相位仍然只跑弱模型。改动：`methods/execution`（参数）、
`stages/phase_execute/final_test.py`（一个 `role` 贯穿原来六处写死的 `"weak"`）、
`composites/answer_sheets.execution_from_external`（`role` 改成必填，外部对照的记录跟随执行者）。
对照取 campaign 的 `dpsk` 列（同一份冻结文件、同一个 sha256），按强模型的采样次数 3 对齐。

强模型在 Harbor 里的推理强度不属于配置：它来自进程环境变量 `TBENCH_REASONING_EFFORT`，
campaign 的强模型对照用的是 `max`，弱模型对照不发。所以 run 2 的启动环境要带
`TBENCH_REASONING_EFFORT=max`，且不能放进共用的密钥文件（那会让 run 1 的弱模型请求也带上）。
启动脚本现在会在密钥文件之后读 `<run 目录>/run.env`，续跑自动带同一组设置。

## 两个 run 的配置

| run | 配置 | 序列 | 分叉 | 对照 |
|---|---|---|---|---|
| 1 | `tb21_p02p08_exam_small20_20261030.yaml` | baseline, learn_add, exam, learn, exam, learn_trial, exam, learn_trial, test | `--through p05` | 外部 campaign `qwen` 列 |
| 2 | `tb21_p02p06_exam_small20_strong_test_20261030.yaml` | 与源 run 相同的 7 个相位 | `--through p06` | 外部 campaign `dpsk` 列 |

两份配置相对源 run 的配置，run 1 只多了序列里的两项，run 2 只改了 test 相位的
`execution.executor` 和 `compare.external.model_key`。

## 已知的混杂（结果解读时必须带上）

- **技能读取文本已经换了。** 2026-10-30 外部依赖里的 `skill_read_suffix` 改成了两阶段选卡 +
  读后复述 + 完成前自检门（`tb_skill_read_suffix_20261030.md`），它已经在基准主机上生效。
  两个新 run 的 test 相位都在新文本下跑，而源 run 的 P07 在旧文本下跑。因此：run 1 的 P09
  与源 run 的 P07 的差别包含"多一轮学习"和"新文本"两个因素；run 2 与源 run 的 P07 的
  差别包含"换了模型"和"新文本"两个因素。要单独看新文本的效果，需要再跑一个
  "P06 的库 + 弱模型 + 新文本，只重跑 test"的 run（本次没有要求，没有跑）。
- **强模型的上下文预算与对照不同**：配置给 `executor_strong` 的输入预算是 512K，campaign 的
  dpsk 对照用的是 runner 缺省的 131072；弱模型同理（256K 对 131072）。这是配置层面既有的
  设定，不是本次引入的，但读强模型结果时同样适用。
- **Harbor 侧请求不经过请求 ID 别名池**，强模型 rollout 只走配置里的那一个请求 ID；
  它和 run 1 的 driver 侧调用共用同一个网关限流，两个 run 同时跑时 429 会比单独跑多。

## 部署与可追溯

这两个 run 在一份未提交的工作区快照上启动（目录名 `<提交>-dirty-<时间戳>`，
`DEPLOYED_FROM` 记录了当时的 `git status` 与 `git diff HEAD` 的 sha256）。提交之后可用
该 sha256 把快照对上提交的内容。`deploy_bench_host.sh --force` 现在每次生成新目录，
不再覆盖同名的 dirty 目录（原先第二次 dirty 部署会改动正在跑的 run 的代码）。

## 验证清单（启动前）

1. 单测：`fork_run`、test 相位的 `executor`（含外部对照的角色）、`real_folder` 参数；全量、
   ruff、分层检查。
2. 对真实源 run 分叉后，在基准主机上用不可用的 LLM 端点空跑前缀：被拷贝的相位零模型调用
   通过，新 run 的用量账本为空，`tasks_digest` 与源 run 一致，prompt 摘要与源 run 一致。
3. 技能库重建后，`bank_reference` 与源 P04 记录一致（`fork-run` 内部已核对）。
4. 强模型 rollout 的一次真实烟测：请求里带 `reasoning_effort`，结果文件落在预期位置。
