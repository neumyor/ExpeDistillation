# P07 失败 trial 归因汇总

日期：2026-10-29
一句话：64 条失败 trial 的逐条报告已全部落盘；主要归因是模型没有用上已交付的相关技能，以及 11 个 task 族根本没有相关技能，而不是技能内容写错；另有 11 条被判为环境相关问题，其中 4 条已核实为判分/沙箱缺陷。

**更正（2026-10-29）**：初版把“最后一个 learn 相位的写作产物没有交付”说成“最后一轮学习等于没发生”，并把它列为最结构性的问题。这个说法夸大了。P06 的 keep 判定照常生效，并经整理进入了终测交付集；没交付的只是 P06 新写的改写和需求技能。按系统已有的 4.2 动作表，最后一次检查本来就不该写作；问题在于 `trial_level` 没有遵守这条规则，现已修复（`docs/decisions/trial_level_last_check_20261029.md`）。相应地，下文原称的“交付缺口”改称“未验证的教训”：它是反事实判断，不是一个让 trial 失败的缺陷。本次修改涉及 §3.2、§3.3、§3.4、§5、§7。

**范围限制（2026-10-29 13:56）**：本分析基于的 run 存在考场来源缺陷（P03/P05
场景取自错误 attempt，见 `docs/archive/exam_scene_source_sheet_20261029.md`），
因此它分析的“技能库”来自被污染的 P04/P06 决策链，相关终测数字也已 SUPERSEDED。
以下 64 条对 P07 轨迹本身的判读仍然成立（卡点、技能是否被打开、内容是否够用），
但“哪些技能被交付/为什么没被用上”的结论需要在新 run 上复核。
对应 log.md：2026-10-29 的“P07 失败 trial 归因分析”条目。

## 1. 范围与方法

- run：`runs/tb21_p02p06_exam_small20_20261028_2053_p02fork/`
- 失败 trial：**64 条**（62 条判错 + 2 条 `correctness: null`），覆盖 **27 个 task**
  - 其中 16 个 task 是“三次尝试全败”，47 条已落盘 trial；
  - 另 11 个 task 最终通过、但有 17 条失败 trial。
- 未纳入：`traj/tb:circuit-fibsqrt/weak/00` 从未执行成功，没有轨迹（已在主报告里按失败单元处理）。
- 方法：6 个并行子智能体，每人 10–11 条、按 task 成组；固定 schema 见本目录 `README.md`。
- 校验：64/64 份 JSON 存在、`trial_id` 与 `correctness` 与 `index.jsonl` 一致、字段齐全；
  逐条证据引用 ≤200 字符。逐条文件在 `trials/`，分组归纳在 `chunks/`。

## 2. 子智能体的原始判定

| 类别 | 条数 |
|---|---|
| `model_capability` | 44 |
| `mixed` | 9 |
| `environment` | 11 |
| `skill_quality` | 0 |
| `missing_skill` | 0 |
| `uncertain` | 0 |

置信度：high 44、medium 20。按 chunk：1 = 8 model / 3 mixed；2 = 11 model；
3 = 7 model / 1 mixed / 3 env；4 = 6 model / 5 env；5 = 5 model / 3 mixed / 2 env；
6 = 7 model / 2 mixed / 1 env。

卡点类型：execution 25、reasoning 22、verification 9、environment 6、timeout 2。

**这组原始标签不能直接当作最终归因**：`model_capability` 里面混着“没有技能可用”“有技能但没打开”
“打开了但没照做”三种完全不同的情况；`environment` 里面混着判分器缺陷、镜像缺陷和判分容差。
下面拆开。

## 3. 拆开后的归因

### 3.1 环境相关的 11 条，逐条复核

| trial | 原始判定 | 复核后的性质 | 证据 |
|---|---|---|---|
| qemu-alpine-ssh/00、/01 | environment | **判分器缺陷**：容器缺 `sshpass`，verifier 在启动子进程时 `FileNotFoundError`；agent 侧已实测 SSH 2222 成功 | 我复核了 `verifier_test_stdout.txt`：唯一失败是 `test_sshpass` |
| fix-ocaml-gc/00 | environment | **判分器缺陷**：verifier 复原 testsuite 的 `git clone` 被 HTTP/2 中断（curl 92），testsuite 不存在，0 个测试真正执行 | 我复核：`cp: cannot stat 'ocaml-original/testsuite'` |
| feal-linear-cryptanalysis/01 | environment | **harness 缺陷**：第 4 步 harness 抛 `send-keys: unknown flag -e`，判分器未启动 | agent 引用 result.json 异常 |
| sam-cell-seg/02 | environment | **镜像/题面不一致**：prompt 声称预装 torch/torchvision/mobile_sam，实际 `ModuleNotFoundError`，整个预算耗在装依赖 | 我复核第 3 步轨迹 |
| make-mips-interpreter/02 | environment | **沙箱失效 + 模型诱发**：nested heredoc 后终端 147 步无响应 | 轨迹 |
| make-doom-for-mips/02 | environment | **沙箱失效 + 模型诱发**：模型被困在未闭合引号 8 步后 tmux server 消失，`correctness: null` | 轨迹 + result.json |
| rstan-to-pystan/00 | environment | **模型诱发**：nested heredoc 后终端只回显 `lines.a`，100+ 步无法恢复 | 轨迹 |
| rstan-to-pystan/01、/02 | environment | **判分容差**：实现正确，rho 均值分别差 5.6e-4 / 3.9e-4，低于下界；约 1 个 MC 标准误，判分下界正好压在参考均值上 | 我复核断言：`1.49 <= 1.48944` |
| portfolio-optimization/00 | environment | **判分容差**：N=6000/8000 达标，只有 N=5000 是 1.118x vs 1.2；本地同一实现测得 1.4x | 我复核 verifier 输出 |

归并：**判分器/harness 缺陷 4 条**（qemu×2、fix-ocaml、feal-linear）、
**镜像缺陷 1 条**（sam）、**沙箱失效且模型有责任 2 条**（mips/02、doom/02）、
**判分容差 3 条**（rstan×2、portfolio）、**模型自己把终端弄死 1 条**（rstan/00）。

### 3.2 技能库覆盖与使用

- 32 条 trial 判断**交付集里有相关技能**：
  - **17 条从未打开**该技能（目录/描述没让模型选中，或模型根本没先做技能盘点）；
  - **10 条打开了但没照做**（内容正确，模型仍按自己的做法走）；
  - **2 条打开后发现内容不足**：`fix-ocaml-gc/01`（通用构建排错卡对 GC 语义 bug 无内容）、
    `qemu-alpine-ssh/02`（卡里没有 SSH/hostfwd 验收步骤）——这是唯一算得上“技能写得不够好”的两条；
  - 3 条标为 not applicable（技能与卡点无关）。
- **25 条 trial 既没有交付的相关技能、也没有 pending 的相关技能**，分布在 11 个 task：
  `winning-avg-corewars`、`mteb-retrieve`、`video-processing`、`model-extraction-relu-logits`、
  `sanitize-git-repo`、`feal-differential-cryptanalysis` 各 3 条，
  `rstan-to-pystan`、`feal-linear-cryptanalysis` 各 2 条，
  `portfolio-optimization`、`overfull-hbox`、`regex-log` 各 1 条。
  这些是真正的覆盖空白。
- **30 条 trial 指向至少一个 pending 技能**（P06 写出、未经任何检查、因此未交付），共 6 个不同技能：

| pending 技能 | 被引次数 | 内容 |
|---|---|---|
| `req/7ebf1db42112` | 16 | 动手前和宣布完成前写封闭计划，逐项点名交付物 |
| `req/cdf02b6dde80` | 7 | 构建/安装/训练后必须用真实操作证明产物可用 |
| `req/e20a68bdd559` | 7 | 对合法的长任务等待、保留输出、不要过早中断 |
| `req/a20befa537b9` | 2 | 改动前保留并哈希参考产物 |
| `bn-fit-modify/.../seg03` v3 | 2 | 生成并校验完整的结构化输出文件集 |
| `path-tracing/.../seg01` v3 | 2 | 复现前先盘点并静态检查候选生成器 |

  这些只说明“已经写出过对口的教训”，不能说明交付它们就会通过。其中两条 v3 是改写，
  它们的上一版在 P05 考试中已经失败。

### 3.3 按“可操作杠杆”去重后的分布

上面三类可以重叠（一条 trial 既可能有对口的 pending 教训，也可能同时是模型执行问题）。
若按处置优先级去重——环境/容差优先，其次未验证的教训，再次触发/选择、技能内容、无技能、
最后才是纯执行——64 条的分布是：

| 可操作杠杆 | 条数 |
|---|---|
| 未验证的教训（只有未经检查的 pending 技能对口） | 27 |
| 完全没有相关技能（交付集与 pending 都没有） | 21 |
| 环境（判分/沙箱/镜像） | 7 |
| 判分容差 | 3 |
| 技能内容不足（已交付） | 2 |
| 纯模型执行（有已交付技能但没照做） | 4 |

机器可读版本在 `summary.json`（每条 trial 同时保留原始判定、归一化原因和可操作杠杆；
该类的取值已从 `delivery_gap` 改为 `unvalidated_lesson`）。
“未验证的教训”这一行要谨慎读。这 27 条在去重时被优先归到这里，但其中 **20 条
本来就有已交付的相关技能**：15 条从未打开，5 条打开了没照做。只有 7 条是交付集里
确实没有、仅 pending 里有对口教训。所以主要杠杆仍是技能的选择与执行，而不是再多写
或多交付技能。

### 3.4 最后一个 learn 相位：哪些生效了，哪些没有

最终库 47 条 = **26 accepted + 21 pending**。

- **生效的部分**：P06 判 keep 的 23 个技能转为 accepted，随后整理做了 10 次合并（消费 23 条来源），
  得到终测交付的 26 条。这一半照常进入了 P07。
- **没生效的部分**：21 条 pending **全部是 P06 写出的**——10 条新需求技能，加上 11 条改写
  （9 条原始技能的 v3，2 条 P04 需求技能的 v2）。P06 之后没有任何执行相位，它们永远到不了
  accepted，而终测只交付 accepted 集。
- **为什么这不是“技能交付通道”的问题**：系统的 4.2 动作表本来就规定，没有后续检查时
  不写改写（revise → delete），`task_level`、`skill_level` 与 exam 决策都遵守。
  只有 `trial_level` 没读 `checks_left`，所以在最后一轮仍然写作。已修复：`checks_left == 0` 时
  revise 转为 delete（`retry_exhausted`），新需求记 `deferred_no_later_check`、不写作。
- **对本次数字的影响**：按原规则这 21 条同样不会交付，所以 **P07 的结果不受影响**；
  浪费的是写作调用，约占非执行调用输出 token 的 9%。

## 4. 反复出现的失败模式

1. **自证循环**：模型用自己的实现/自己的约定去验证自己，而不是用题目给的验收命令或独立参考。
   chunk 1 的 11/11 都是这一类（例如 kv-store-grpc 用自己写的错误 proto 自测通过）。
2. **不读/不检查交付物约束**：文件名、字段名、精确命令、字节比较没被检查就提交。
   例：`large-scale-text-editing/02` 内容全对，但写了 `:wq!` 而不是要求的 `:wq`/`:x`。
3. **不收敛直到预算耗尽**：chunk 5 的 10 条里 5 条 7200 秒超时；
   mips 00/01 十几次重写同一个 `vm.js`，同一个寄存器 bug 反复出现。
   这些 trial 的共同点是**从未按交付物做一次端到端验收**。
4. **相关技能没有被使用**：17 条从未打开、10 条打开后没照做；
   模型通常只打开 1 张卡（52/64 条只打开 1 张，10 条打开 2 张，2 条一张没开）。
5. **环境/判分缺陷被计成失败**：qemu 00/01、fix-ocaml/00 是最明确的假阴性；
   rstan/portfolio 是判分容差；sam/02 是镜像与题面不一致。

## 5. 对系统设计的含义

1. **最后一个 learn 的写作（已修复）**：`trial_level` 现在遵守“最后一次检查不写作”。
   若希望最后一轮的教训进入终测，应在 test 前补一次 exam（或 live），让它先被验收；
   不建议直接交付 pending，因为那会把未验证、且上一版已失败的改写送进终测。
   这是配置层面的选择，不是本次失败的主因。
2. **技能选择/触发**：交付集里有相关技能时，超过一半（17/32）没被打开。
   需要改的是目录/描述的可检索性、或者强制先做技能盘点；这不是内容问题。
3. **执行纪律**：10/32 打开了正确技能但没照做；大量失败可用“按题目验收路径自检”兜住。
   这类教训 P06 已写成 `req/cdf02b6dde80` 和 `req/7ebf1db42112`，但它们未经检查；
   既然已交付的相关技能里 10/32 被打开后也没照做，单靠多交付几张卡片不太可能解决。
4. **覆盖空白**：25 条 trial 没有任何相关技能（11 个 task），这些是需要新技能的族，
   不是改写现有技能能解决的。
5. **评测基础设施**：qemu 的 `sshpass`、fix-ocaml 的 verifier 网络、sam 的镜像依赖、
   harness 的 `send-keys -e`，以及 rstan/portfolio 的容差，必须先修或在统计里显式排除；
   否则方法结论会被这些假阴性拉低。

## 6. 对终测数字的影响（敏感性，不是主结果）

主表保持判分器原样：**avg@3 0.4487、pass@3 0.5897（23/39）**。下表只是说明这些缺陷的量级：

| 情形 | avg@3 | pass@3 |
|---|---|---|
| 按判分原样（主结果） | 0.4487 | 0.5897（23/39） |
| 把已核实的 qemu 00/01 算作成功（该题变为 2/3） | 0.4658 | 0.6154（24/39） |
| 再把 rstan 01/02 与 portfolio 00 按“容差内”算通过 | 0.4829 | 0.6667（26/39） |

第二、三行都不是严格判分结果，不能写进结果表当主数字；它们只说明
“环境/容差缺陷合计可能压低 avg@3 约 0.03、pass@3 约 0.08”。
另外，作为对照的裸跑数字来自 campaign 存档，无法确认它当年是否遇到同样的判分器/镜像缺陷。

## 7. 证据、局限与建议

- 逐条证据：`trials/*.json`（64 份）、`chunks/chunk_*.md`、`index.jsonl`。
- 我独立复核了所有 11 条 environment 判定中证据可查的 6 条（qemu×2、fix-ocaml、portfolio、
  rstan/01、sam/02）；其余（feal-linear/01、mips/02、doom/02、rstan/00、rstan/02）依据子智能体引用。
- “pending 技能本可避免”是反事实判断，没有实测；“有相关技能”也是审阅判断，不是检索器打分。
- 建议下一轮：(a) 修 qemu/fix-ocaml/sam 的评测环境；(b) 如需验证最后一轮教训，在 test 前补一次 exam；
  (c) 针对 17 条“未打开”做触发实验；(d) 把 25 条无技能 trial 的 11 个 task 族作为新技能需求。
