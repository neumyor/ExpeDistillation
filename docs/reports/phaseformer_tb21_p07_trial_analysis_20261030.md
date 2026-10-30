# TB21 small20 · P07 终测的 trial 级失败分析

日期：2026-10-30
一句话：对刚跑完的 P07（39 题 × 3 次）逐 trial 归因，结论是技能库覆盖不足与「有卡不读」是两类主要可控原因，而两臂的整体差异只体现在分布均匀度上，不是多赢了任务。
对应 log.md：2026-10-30 的「P07 trial-level 分析」条目。

## 1. 数据与口径（先说清限制）

- run：`tb21_p02p06_exam_small20_node2_20261029`（代码 `682d5c636ade`，交付库 `bank:3bb3ee40d638`）。
- 两臂：`with_bank`（本 run 真实执行，117 个 trial，有轨迹）与 `control`（**external campaign**，117 条只有判分）。
- **控制臂没有轨迹、没有步骤、没有技能读取记录**，因此「弱模型为什么失败、技能是否调用正确」只能对 `with_bank` 回答；控制臂只用于成功/失败计数对比。本报告不对控制臂的失败原因做任何推断。
- 证据包从 run 派生到 run 目录之外（`<runs-dir>/p07_analysis_20261030/`），只读生成，未改 run 任何文件、未重跑 trial；结论不回灌到实验结果。
- 交付技能文件夹是**全局一份**：39 道题看到同一个文件夹，17 条技能（7 条 `bank/merge/0000`–`0006` + 10 条血统技能）+ `catalog.md`。已核实 117 个 trial 的 `skills_delivered` 完全相同。

## 2. 总体计数

| | 成功 / 总数 | 比率 | 按题聚合的 pass@3 |
|---|---|---|---|
| with_bank | 61 / 117 | 0.5214 | 0.5897 |
| control | 57 / 117 | 0.4872 | 0.6154 |

- 39 道题里 **13 道两臂都没过**；两臂同向的题共 34 道。
- 只有 with_bank 过的 2 道：`adaptive-rejection-sampler`, `rstan-to-pystan`。
- 只有 control 过的 3 道：`hf-model-inference`, `large-scale-text-editing`, `overfull-hbox`。
- **两臂净差是 1 道题（2 胜 3 负），方向不占优。** avg@3 的 +3.4 个百分点来自分布更均匀（更多题拿到 1/3 或 2/3），不是多赢了任务。

## 3. 技能覆盖表（39 题）

判定标准：这 17 条里有没有一条在语义上适用于该题。`clear_match` 有明显对应，`marginal` 勉强相关，`none` 没有对应。这张表是把 `skill_absent` 与 `skill_present_not_read` 分开的依据。

| 题 | 覆盖 | 对应技能 | 理由 |
|---|---|---|---|
| `adaptive-rejection-sampler` | marginal | `traj/tb:cancel-async-tasks/strong/00/seg00/skill` | ‘Implement and validate a standalone Python module from scratch’ is a  |
| `chess-best-move` | clear_match | `traj/tb:path-tracing/strong/00/seg01/skill` | The task is an image-reading task and seg01 is the image-characterizat |
| `circuit-fibsqrt` | none | `-` | No card covers synthesizing a <32,000-gate boolean circuit from a simu |
| `code-from-image` | clear_match | `traj/tb:path-tracing/strong/00/seg01/skill` | seg01's image-characterization steps and bank/merge/0000's OCR steps d |
| `configure-git-webserver` | clear_match | `bank/merge/0006` | 0006's probe-install-configure-verify workflow directly covers install |
| `constraints-scheduling` | none | `-` | No card covers ICS parsing or calendar constraint solving; the path-tr |
| `crack-7z-hash` | marginal | `bank/merge/0006` | 0006 helps install/build 7z or john, but no card covers 7z2john/john p |
| `distribution-search` | clear_match | `bank/merge/0001` | 0001's baseline/variant/evaluator loop matches constructing a distribu |
| `feal-differential-cryptanalysis` | none | `-` | No card covers differential cryptanalysis of FEAL; path-tracing seg00  |
| `feal-linear-cryptanalysis` | marginal | `bank/merge/0003` | 0003's data inventory/independent verification applies to pairs.txt/ci |
| `fix-ocaml-gc` | clear_match | `bank/merge/0006` | 0006's configure/make/smoke-test/repair workflow matches building this |
| `git-leak-recovery` | none | `-` | No card covers git reflog/object recovery or history cleanup. |
| `git-multibranch` | none | `-` | No card covers sshd/git-server/nginx deployment hooks; bank/merge/0006 |
| `gpt2-codegolf` | clear_match | `bank/merge/0001` | The task names a fixed deliverable (/app/gpt2.c), a quantitative size  |
| `hf-model-inference` | marginal | `bank/merge/0006` | bank/merge/0006 covers installing and functionally verifying software  |
| `kv-store-grpc` | marginal | `traj/tb:cancel-async-tasks/strong/00/seg00/skill` | The cancel-async card teaches writing and validating a standalone Pyth |
| `large-scale-text-editing` | clear_match | `bank/merge/0004` | bank/merge/0004 is a pre-submission gate for byte-preserving filters w |
| `make-doom-for-mips` | clear_match | `bank/merge/0006` | The task is to obtain/configure/build a source artifact (doomgeneric_m |
| `make-mips-interpreter` | marginal | `traj/tb:path-tracing/strong/00/seg00/skill` | Path-tracing seg00 covers characterizing an unknown ELF and disassembl |
| `mcmc-sampling-stan` | clear_match | `bank/merge/0002` | bank/merge/0002 builds and validates a fitted Bayesian model from a nu |
| `merge-diff-arc-agi-task` | marginal | `traj/tb:path-tracing/strong/00/seg00/skill` | The bundle files are unfamiliar binary artifacts to inventory, but pat |
| `model-extraction-relu-logits` | none | `-` | No skill covers black-box ReLU-network weight recovery, hidden-size in |
| `modernize-scientific-stack` | marginal | `traj/tb:cancel-async-tasks/strong/00/seg00/skill` | Cancel-async seg00's write/compile/smoke-test pattern applies to a sta |
| `mteb-retrieve` | clear_match | `bank/merge/0003` | bank/merge/0003 is exactly about deriving a value from data, pinning t |
| `multi-source-data-merger` | clear_match | `bank/merge/0003` | bank/merge/0003's source inventory, schema/field mapping, count checks |
| `nginx-request-logging` | clear_match | `bank/merge/0006` | bank/merge/0006's probe/install/configure/functional-verification work |
| `openssl-selfsigned-cert` | none | `-` | 17 条技能里没有 TLS/证书类内容；通用交付门 bank/merge/0004 不提供 openssl 证书创建与校验能力。 |
| `overfull-hbox` | marginal | `bank/merge/0001` | 0001 的'候选 + 量化 evaluator + 单因子变体 + 晋升'循环可用于 overfull 搜索，但不含 LaTeX/同义词约 |
| `password-recovery` | marginal | `traj/tb:path-tracing/strong/00/seg00/skill` | seg00 教对未知本地文件做特征化与二进制检查，与从 .dat 中找 PASSWORD 部分相关，但没有磁盘取证/已删除文件恢复步骤。 |
| `portfolio-optimization` | clear_match | `bank/merge/0001` | 0001 就是'基线 + 变体 + 评估器 + 精确性 + 晋升冠军'的优化循环，与 C 扩展加速并满足 1.2x 的任务直接对应。 |
| `pypi-server` | clear_match | `bank/merge/0006` | 0006 的 probe-first 获取/构建/安装/功能验证流程覆盖 vectorops 构建与 pypiserver 安装，a02 正 |
| `qemu-alpine-ssh` | marginal | `bank/merge/0004` | 0004 第 20 步要求 service/boot image 用 nohup 分离启动并轮询 endpoint，与 qemu 生命周期相 |
| `regex-log` | none | `-` | 没有正则/日志解析技能；bank/merge/0004 只是通用交付门，不提供 regex 构造能力。 |
| `rstan-to-pystan` | marginal | `bank/merge/0002` | 0002 同属贝叶斯模型/数值表，但其 When to use 要求 model+modified model/合成样本，Do not us |
| `sam-cell-seg` | marginal | `traj/tb:cancel-async-tasks/strong/00/seg00/skill` | seg00 是'从零写并验证独立 Python 模块'，对应 convert_masks.py 的交付形式，但不含 MobileSAM/ma |
| `sanitize-git-repo` | clear_match | `traj/tb:filter-js-from-html/strong/00/seg02/skill` | seg02 教原地 sanitizer 的字节保持重写、raw backup 与逐规则 marker 验证，与本任务清洗仓库并保持其他文件不 |
| `torch-pipeline-parallelism` | none | `-` | 没有 pipeline parallel/分布式训练技能；bank/merge/0005 是 async 取消调试，不相关。 |
| `video-processing` | marginal | `bank/merge/0000` | 0000 处理视频帧序列，但目标是 ffmpeg+tesseract OCR 抽取有序文本，与 cv2 运动/起跳帧检测只共享'视频帧'层面 |
| `winning-avg-corewars` | clear_match | `bank/merge/0001` | 0001 的候选程序 + pmars 评估器 + 阈值/变体/晋升循环与 CoreWars 调优直接对应，a00/a01/a02 都读了它。 |

合计：clear_match 16 题、marginal 14 题、none 9 题。

## 4. 五问逐一回答

### 4.1 弱模型为什么失败（主类别计数，with_bank 的 56 个失败）

| 主类别 | 数量 | 含义 |
|---|---:|---|
| `skill_absent` | 14 | 库中没有任何技能覆盖所需能力 |
| `model_ignored_instruction` | 10 | 题面或框架指令明确，模型未照做（与技能无关） |
| `other` | 10 | 其它；含题面陷阱、案例敏感、近阈值等，逐条在明细里写明 |
| `skill_followed_still_failed` | 6 | 照做了相关技能仍失败（技能内容不足或不对） |
| `skill_present_not_read` | 6 | 有明确对应的技能，模型从未打开 |
| `skill_read_not_followed` | 6 | 打开了相关技能但没照做 |
| `environment_or_infra` | 4 | 沙箱/验证器/网络问题，非模型推理 |

成功 trial 的主类别（61 个）：`success_no_skill_needed` 41、`success_skill_followed` 20。

次要类别（一个 trial 可有多个）：`other` 23、`model_ignored_instruction` 13、`skill_present_not_read` 10、`skill_absent` 8、`skill_read_not_followed` 6、`environment_or_infra` 2。

### 4.2 技能是否调用正确

- **有明确对应但从未打开**：6 个 trial。
  - `gpt2-codegolf` a00：适用 `bank/merge/0001`，未读取的步范围 `1-134,136-250,253-321`。
  - `gpt2-codegolf` a01：适用 `bank/merge/0001`，未读取的步范围 `1-217`。
  - `gpt2-codegolf` a02：适用 `bank/merge/0001`，未读取的步范围 `1-156,158-259`。
  - `sanitize-git-repo` a00：适用 `traj/tb:filter-js-from-html/strong/00/seg02/skill`，未读取的步范围 `0-6`。
  - `sanitize-git-repo` a01：适用 `traj/tb:filter-js-from-html/strong/00/seg02/skill`，未读取的步范围 `0-15`。
  - `sanitize-git-repo` a02：适用 `traj/tb:filter-js-from-html/strong/00/seg02/skill`，未读取的步范围 `0-12`。
- **读了但没照做**：6 个 trial：`make-doom-for-mips` a00、`make-doom-for-mips` a01、`mteb-retrieve` a00、`mteb-retrieve` a02、`winning-avg-corewars` a00、`winning-avg-corewars` a01。
- **照做仍失败**：6 个 trial：`chess-best-move` a00、`chess-best-move` a01、`chess-best-move` a02、`large-scale-text-editing` a00、`large-scale-text-editing` a01、`winning-avg-corewars` a02。
- 成功 trial 里，20 个确实读并执行了技能，41 个不需要技能。

### 4.3 技能库是否不包含所需技能

共 14 个 trial（分布在 5 道题）。缺失的能力：

| 题 | 次数 | 缺失的能力 |
|---|---:|---|
| `circuit-fibsqrt` | 3 | Synthesize a <32,000-line logic-gate circuit computing fib(isqrt(N)) mod 2^32. |
| `feal-differential-cryptanalysis` | 3 | Differential cryptanalysis of the FEAL-like 4-round Feistel cipher to recover key[5]. |
| `make-mips-interpreter` | 2 | A working MIPS user-mode interpreter with Linux syscall emulation sufficient to boot the provid |
| `model-extraction-relu-logits` | 3 | Black-box ReLU-network weight recovery that infers the hidden size at runtime and still works a |
| `video-processing` | 3 | No skill covers detecting takeoff/landing frames of a hurdle jump in video with cv2/TOML; bank/ |

### 4.4 是否是模型未遵从指令

共 10 个 trial：`adaptive-rejection-sampler` a01、`git-multibranch` a00、`make-doom-for-mips` a02、`make-mips-interpreter` a02、`mteb-retrieve` a01、`overfull-hbox` a01、`sam-cell-seg` a02、`torch-pipeline-parallelism` a00、`torch-pipeline-parallelism` a01、`torch-pipeline-parallelism` a02。这些与技能无关，是模型自身没有执行题面或框架的明确要求；逐条证据见明细。

### 4.5 prompt 设计是否合理（只列能指到具体措辞的）

共 15 个 trial 指到了具体措辞。最集中的一条是我们自己的文件夹指引：

> `TB_SKILL_FOLDER_GUIDANCE`：「read the location of at least one skill that could plausibly help with the task. **You MUST do this at least once before running any command for the task itself, even if the task looks simple.**」

对覆盖表判定为 `none` 的 9 道题，这句话强制模型先读一张不相关的卡：出现读了 `bank/merge/0000`（视频 OCR）去解 pypi-server、regex-log 的情况，也有为读卡而消耗步骤的情况。建议改为「先看目录，只有确实相关才打开」或对 `none` 类题放宽强制。

其余措辞问题按题记录：

| 题 | 尝试 | 具体措辞问题 |
|---|---|---|
| `adaptive-rejection-sampler` | a02 | Task says "Save your main implementation in a file named '/app/ars.R'" and separately "Include a test function |
| `large-scale-text-editing` | a00 | The task says 'After editing, input.csv matches expected.csv byte-for-byte', which invites leaving input.csv t |
| `large-scale-text-editing` | a01 | The task says 'After editing, input.csv matches expected.csv byte-for-byte', which invites leaving input.csv t |
| `make-mips-interpreter` | a02 | The task says only 'Running this file should result in saving the frames as they are rendered, one at a time'  |
| `model-extraction-relu-logits` | a00 | The task says 'You do not know the shape of A1', but the visible /app/forward.py sets 'A1 = np.random.randn(20 |
| `model-extraction-relu-logits` | a01 | The task says 'You do not know the shape of A1', but the visible /app/forward.py sets 'A1 = np.random.randn(20 |
| `model-extraction-relu-logits` | a02 | The task says 'You do not know the shape of A1', but the visible /app/forward.py sets 'A1 = np.random.randn(20 |
| `openssl-selfsigned-cert` | a01 | TB_SKILL_FOLDER_GUIDANCE requires reading at least one skill before any task command; with no TLS skill the mo |
| `openssl-selfsigned-cert` | a02 | TB_SKILL_FOLDER_GUIDANCE forces reading a skill before any command; the only read card (bank-merge-0000) is ab |
| `overfull-hbox` | a00 | The task says only 'replace words in input.tex with their specified synonyms' and does not require preserving  |
| `overfull-hbox` | a01 | TB_SKILL_FOLDER_GUIDANCE forces reading at least one skill before any task command; with no LaTeX skill in the |
| `overfull-hbox` | a02 | The task wording does not require case preservation, but the verifier is case-sensitive; 'Middle' is not a syn |
| `video-processing` | a01 | TB_SKILL_FOLDER_GUIDANCE says 'read the location of at least one skill ... You MUST do this at least once'; th |
| `pypi-server` | a00 | TB_SKILL_FOLDER_GUIDANCE forced a skill read; the model read bank-merge-0000 (video/OCR), unrelated to packagi |
| `regex-log` | a00 | TB_SKILL_FOLDER_GUIDANCE forced a skill read; the model read bank-merge-0000 (video/OCR), unrelated to regex c |

## 5. 翻转题：两臂差异的全部来源

只有 5 道题在两臂间方向不同，其余 34 道同向。

### 5.1 with_bank 独过（2 道）：提升是否来自技能

- `adaptive-rejection-sampler`（成功的是 a00，280 步）：第 0 步读 `catalog.md`、第 1 步读 `bank/merge/0004`（提交前交付门）、第 84 步读一条血统卡；但**交付门要求的 sha256 校验与完成前回读从未出现**，第 273 步打印 `BASIC_NORMAL: FAIL`、第 279 步 `COMPARE_BETA: FAIL`，且到结尾没有 `mark_task_complete`。通过靠的是模型自己写的 `ars.R` 与落在验证器宽松阈值内的统计量。**结论：这次提升与技能无关。**
- `rstan-to-pystan`（成功的是 a01，92 步）：第 0 步读 `bank/merge/0002`，但只执行了它的通用预检（读 R 脚本与 CSV、探测 Python），该技能特有的结构学习、干预与合成采样步骤与本题不匹配、也没有执行；成功来自模型自己完成的 R→PyStan 转换（安装 pystan 3.10.0、改写 Stan 模型、`stan.build(..., random_seed=1)` 采样、写出 CSV）。**结论：这次提升与技能无关。**

### 5.2 control 独过（3 道）：with_bank 是怎么输的

- `hf-model-inference`：三次都因 huggingface.co 不可达而改用随机权重并提交，API 对明显正面文本返回 negative。技能在这题上中性/不适用，更像环境与网络运气差异。
- `large-scale-text-editing`：a00/a01 按 `bank/merge/0004` 做了 `cmp` 字节验证并得到 `FULL_MATCH`/`MATCH` 仍被判失败，根因是题面「After editing, input.csv matches expected.csv byte-for-byte」诱导保留已变换的 `input.csv`，而 `tests/test_outputs.py` 会再跑一次 `apply_macros.vim`（宏非幂等）。a02 是终端死亡。**不是技能有害。**
- `overfull-hbox`：三次全败，a00/a02 把大写 `Middle` 换成小写 `hub`，验证器区分大小写；a01 在搜索脚本仍在跑时五次标记完成。三次都没有出现「技能把模型带偏」的证据；库中根本没有 LaTeX/overfull 相关技能，唯一勉强相关的卡三次都没读。

### 5.3 结论

技能在这个实验里的净效应不是「多解题」，而是「让同一道题的成功更稳定」。两臂净差 1 道题（2 胜 3 负）不占优；avg@3 的 +0.0342 来自更多题拿到部分成功，pass@3 反而低 0.0257。

## 6. 逐 trial 明细（with_bank，117 个）

臂只有 with_bank 有轨迹，故明细只覆盖它；control 的 117 条判分见 `phase_summary.json`。

| # | 题 | 尝试 | 结果 | 主类别 | 次类别 | 证据摘要 |
|---:|---|---:|---|---|---|---|
| 1 | `adaptive-rejection-sampler` | a00 | 成功 | `success_no_skill_needed` | - | Step 1 read bank/merge/0004 and step 84 read the llm-inference seg00 card, but neither card's procedure is executed: the |
| 2 | `adaptive-rejection-sampler` | a01 | 失败 | `model_ignored_instruction` | other | The task says 'dnorm' is a legitimate density input, but step 284's final `.log_density` is a passthrough (`v <- suppres |
| 3 | `adaptive-rejection-sampler` | a02 | 失败 | `other` | - | The final ars.R (step 58 rewrite, 9623 bytes) defines 13 functions but no `test` (step 64 `grep -c 'function('` -> `13`; |
| 4 | `chess-best-move` | a00 | 失败 | `skill_followed_still_failed` | - | Read seg01 at step 1 and followed its image-characterization steps (PIL size/mode at step 3, square colour sampling at s |
| 5 | `chess-best-move` | a01 | 失败 | `skill_followed_still_failed` | - | Read seg01 at step 0 and did pixel-based piece detection at step 7, but step 37's 40-position reconstruction printed 'b2 |
| 6 | `chess-best-move` | a02 | 失败 | `skill_followed_still_failed` | - | Read seg01 at step 1 and performed extensive pixel analysis, but step 39's best FEN `rnqkkrnr/pnpppnnp/n1q1n3/3kPqbP/8/P |
| 7 | `circuit-fibsqrt` | a00 | 失败 | `skill_absent` | other | Read bank/merge/0000 and 0004 at step 0, neither of which covers gate synthesis; /app/gates.txt was still the 33-line ex |
| 8 | `circuit-fibsqrt` | a01 | 失败 | `skill_absent` | other | Read path-tracing seg00 at step 0 and bank/merge/0004 at step 13, neither of which covers circuit construction; step 25' |
| 9 | `circuit-fibsqrt` | a02 | 失败 | `skill_absent` | other | Read bank/merge/0004 at step 0 and bank/merge/0001 at step 38, neither of which covers circuit synthesis; /app/gates.txt |
| 10 | `code-from-image` | a00 | 成功 | `success_skill_followed` | - | Read seg01 at step 0 and followed its image-characterization steps (PNG header/length at step 2, tesseract OCR of the co |
| 11 | `code-from-image` | a01 | 成功 | `success_skill_followed` | - | Read bank/merge/0000 at step 0, installed tesseract at step 2 and OCR'd the image in several PSM modes at steps 4-6, the |
| 12 | `code-from-image` | a02 | 成功 | `success_skill_followed` | - | Read seg01 at step 0, characterized the PNG and installed tesseract at steps 1-5, OCR'd 'def main(): ... 0000TBENCH-SALT |
| 13 | `configure-git-webserver` | a00 | 成功 | `success_skill_followed` | - | Read bank/merge/0006 at step 0 and followed its probe-install-verify loop: step 1 `which git` found nothing, step 2 inst |
| 14 | `configure-git-webserver` | a01 | 成功 | `success_skill_followed` | - | Read bank/merge/0006 at step 0; step 1's first command failed with 'git: command not found', step 2 installed git ('GIT_ |
| 15 | `configure-git-webserver` | a02 | 成功 | `success_no_skill_needed` | - | Read only bank/merge/0000 at step 1 (an OCR card unrelated to a git server); after 'git: command not found' at step 2 it |
| 16 | `constraints-scheduling` | a00 | 成功 | `success_no_skill_needed` | - | Read only the catalog and the folder's root SKILL.md at steps 0-1 (no skill card), then parsed the three ICS files and w |
| 17 | `constraints-scheduling` | a01 | 成功 | `success_no_skill_needed` | - | Read path-tracing seg00 at step 1 (file characterization, unrelated to scheduling) and solved the constraints in one Pyt |
| 18 | `constraints-scheduling` | a02 | 成功 | `success_no_skill_needed` | - | Read only the catalog at step 0 (no skill card) and solved with Python at steps 8-13, writing /app/meeting_scheduled.ics |
| 19 | `crack-7z-hash` | a00 | 成功 | `success_no_skill_needed` | - | Read bank/merge/0004 at step 0 and did a final path+sha256 check at step 18, but the cracking came from its own john wor |
| 20 | `crack-7z-hash` | a01 | 成功 | `success_no_skill_needed` | - | Read bank/merge/0000 at step 0 (OCR, unrelated); step 0's `which 7z` printed 'no 7z found', step 1 installed p7zip, step |
| 21 | `crack-7z-hash` | a02 | 成功 | `success_no_skill_needed` | - | Read bank/merge/0000 at step 1 (OCR, unrelated), installed p7zip at step 4, rebuilt john at step 10, cracked the archive |
| 22 | `distribution-search` | a00 | 成功 | `success_skill_followed` | - | Read bank/merge/0001 at step 0 and followed its variant-scoring loop: steps 2-16 log each candidate's forward/backward K |
| 23 | `distribution-search` | a01 | 成功 | `success_no_skill_needed` | - | Read bank/merge/0004 at step 0 but not the matched bank/merge/0001; it solved the two-KL system with its own root-findin |
| 24 | `distribution-search` | a02 | 失败 | `other` | skill_present_not_read,environment_or_infra | At step 11 a long-running script/heredoc left the terminal unresponsive ('Seed 298: fun=1.50e+00' repeats from steps 12  |
| 25 | `feal-differential-cryptanalysis` | a00 | 失败 | `skill_absent` | other | Read only bank/merge/0000 at step 0 and path-tracing seg00 at step 38 (file characterization); step 52's check prints 'M |
| 26 | `feal-differential-cryptanalysis` | a01 | 失败 | `skill_absent` | other | Read only the first five lines of bank/merge/0000 at step 2 (plus the catalog at steps 0/25), none of which covers crypt |
| 27 | `feal-differential-cryptanalysis` | a02 | 失败 | `skill_absent` | other | Read bank/merge/0000 at step 1 and path-tracing seg00 at step 18, neither covering cryptanalysis; step 43's `cat /app/at |
| 28 | `feal-linear-cryptanalysis` | a00 | 成功 | `success_no_skill_needed` | - | Read only bank/merge/0000 at step 0 (OCR, unrelated) plus the catalog at step 1; it recovered the seeds itself and step  |
| 29 | `feal-linear-cryptanalysis` | a01 | 失败 | `other` | skill_absent,environment_or_infra | Step 84's K0 scan reported 'K0=0/1048576, matches=0', then step 86's unterminated `cat > /app/solver_final.c << 'CEOF'`  |
| 30 | `feal-linear-cryptanalysis` | a02 | 成功 | `success_no_skill_needed` | - | Read bank/merge/0003 at step 2 and seg00 at step 55, while the key recovery came from its own C solver (step 120 'Wrote  |
| 31 | `fix-ocaml-gc` | a00 | 成功 | `success_skill_followed` | - | Read bank/merge/0006 at step 0 and followed its probe-build-verify workflow: step 1 found the OCaml tree with configure/ |
| 32 | `fix-ocaml-gc` | a01 | 成功 | `success_skill_followed` | - | Read bank/merge/0006 at step 0, used its configure/make workflow (step 6 `./configure --enable-flambda`, step 7 reproduc |
| 33 | `fix-ocaml-gc` | a02 | 成功 | `success_no_skill_needed` | - | Read path-tracing seg00 at step 4 and the root SKILL.md at step 5 but never bank/merge/0006; it still built the compiler |
| 34 | `git-leak-recovery` | a00 | 成功 | `success_no_skill_needed` | - | Read path-tracing seg00 at step 0 (file inventory only), then used plain git knowledge: step 1's `git reflog` exposed `6 |
| 35 | `git-leak-recovery` | a01 | 成功 | `success_no_skill_needed` | - | Read the SQLite db-wal-recovery card at step 1, which is unrelated; step 3's `git show 6b4c75c` exposed the secret and s |
| 36 | `git-leak-recovery` | a02 | 成功 | `success_no_skill_needed` | - | Read bank/merge/0004 at step 1 (a generic pre-submission gate), recovered the secret via `git show 6b4c75c` at step 2, p |
| 37 | `git-multibranch` | a00 | 失败 | `model_ignored_instruction` | skill_absent | No skill card was read (only the catalog at step 0); step 35 ran `rm -rf /var/www/deploy/index.html /var/www/deploy/dev/ |
| 38 | `git-multibranch` | a01 | 成功 | `success_no_skill_needed` | - | Read only the catalog at step 0 (no card) and set up sshd/git/nginx from its own knowledge; step 13's two curls returned |
| 39 | `git-multibranch` | a02 | 成功 | `success_no_skill_needed` | - | Read bank/merge/0004 at step 1 (generic final gate); after deleting the deployed files it re-ran the hook manually at st |
| 40 | `gpt2-codegolf` | a00 | 失败 | `skill_present_not_read` | other | step 319: the compiled C program aborted with '*** buffer overflow detected ***: terminated'; step 321 patched b[128] to |
| 41 | `gpt2-codegolf` | a01 | 失败 | `skill_present_not_read` | other | step 217 (the last step) was still a heredoc writing /app/gpt2.c and never compiled or ran it; the only skill read was b |
| 42 | `gpt2-codegolf` | a02 | 失败 | `skill_present_not_read` | other | step 259 ran /app/a.out with the correct vocab.bpe and got 'Segmentation fault' (earlier runs used the misspelled 'vocab |
| 43 | `hf-model-inference` | a00 | 失败 | `environment_or_infra` | model_ignored_instruction | step 16 showed '[Errno 101] Network is unreachable' for huggingface.co; after building a random-weight model, the API an |
| 44 | `hf-model-inference` | a01 | 失败 | `environment_or_infra` | model_ignored_instruction | steps 8-11: the hf-mirror download ended in 'LocalEntryNotFoundError ... Please check your internet connection', and the |
| 45 | `hf-model-inference` | a02 | 失败 | `environment_or_infra` | model_ignored_instruction | step 17 showed '[Errno 101] Network is unreachable' for huggingface.co; step 70 built a synthetic pytorch_model.bin and  |
| 46 | `kv-store-grpc` | a00 | 成功 | `success_no_skill_needed` | - | step 1 ran 'python3 --version && ls -la /app/' (the skill's first step), step 8 printed 'COMPILE_OK', and step 9 verifie |
| 47 | `kv-store-grpc` | a01 | 成功 | `success_no_skill_needed` | - | step 0 read bank/merge/0000 (ffmpeg/tesseract extraction, irrelevant here), and step 8's direct gRPC SetVal/GetVal test  |
| 48 | `kv-store-grpc` | a02 | 成功 | `success_no_skill_needed` | - | step 1 ran 'python3 --version && ls -la /app/' (skill step 1), step 4 wrote server.py, and step 5 verified SetVal/GetVal |
| 49 | `large-scale-text-editing` | a00 | 失败 | `skill_followed_still_failed` | - | step 19 'cmp /app/input.csv /app/expected.csv' printed 'FULL_MATCH' and step 21 marked complete, yet the scorer failed;  |
| 50 | `large-scale-text-editing` | a01 | 失败 | `skill_followed_still_failed` | - | step 15 'cmp /app/input.csv /app/expected.csv' printed 'MATCH' and step 16 marked complete, yet the scorer failed; step  |
| 51 | `large-scale-text-editing` | a02 | 失败 | `environment_or_infra` | skill_present_not_read | step 185 was the last step: after repeated '^Bc' control characters the shell only echoed 'echo ALIVE' with no prompt, n |
| 52 | `make-doom-for-mips` | a00 | 失败 | `skill_read_not_followed` | skill_absent,other | step 300 ran 'node vm.js' and got 'Program terminated at PC=0x0' after 'Executed 15114 instructions' with no DOOM init t |
| 53 | `make-doom-for-mips` | a01 | 失败 | `skill_read_not_followed` | skill_absent,other | step 461 ran vm.js for 60s and only printed '480000000 inst PC=9d ... sc=1' with no frame and no DOOM init text; bank/me |
| 54 | `make-doom-for-mips` | a02 | 失败 | `model_ignored_instruction` | skill_present_not_read,other | step 284 shows vm.js writing a synthetic frame ('bmpData.push(128);bmpData.push((y%3)*64);bmpData.push(128);'), and step |
| 55 | `make-mips-interpreter` | a00 | 失败 | `skill_absent` | other | step 399 'timeout 5 qemu-mipsel /app/doomgeneric_mips' returned 'EXIT:124' with empty output, and the trial ended at ste |
| 56 | `make-mips-interpreter` | a01 | 失败 | `skill_absent` | other | step 339 'node vm.js' ended with 'Done: exec=5001 frames=0 syscalls=0', i.e. no frame and no syscall. |
| 57 | `make-mips-interpreter` | a02 | 失败 | `model_ignored_instruction` | skill_absent,other | step 105 saved 'frame 0: /app/frame_0.png' and step 106 reports 'Dimensions: (640, 400)'; the agent wrote PNGs from host |
| 58 | `mcmc-sampling-stan` | a00 | 成功 | `success_skill_followed` | - | step 87 printed posterior_alpha=2.8715422107727 and posterior_beta=16.3501908689985 after 4 chains x 100000 iterations,  |
| 59 | `mcmc-sampling-stan` | a01 | 成功 | `success_skill_followed` | - | step 51 printed posterior_alpha=2.87184797291897 and step 53 recompiled hierarchical_model.stan successfully; bank/merge |
| 60 | `mcmc-sampling-stan` | a02 | 成功 | `success_skill_followed` | - | step 50 printed posterior_alpha=2.855477 and posterior_beta=16.250162 and step 53 verified all four deliverables; bank/m |
| 61 | `merge-diff-arc-agi-task` | a00 | 成功 | `success_no_skill_needed` | - | step 5 fetched both bundles into branch1/branch2, step 10 resolved the algo.py conflict, and step 11's example check pas |
| 62 | `merge-diff-arc-agi-task` | a01 | 成功 | `success_no_skill_needed` | - | step 10 checked all examples with algo.map and step 11 committed the merge; the git knowledge came from the model, not f |
| 63 | `merge-diff-arc-agi-task` | a02 | 成功 | `success_no_skill_needed` | - | step 16's example check passed and step 15 committed the merge; path-tracing seg00 was read at step 0 but its main proce |
| 64 | `model-extraction-relu-logits` | a00 | 失败 | `skill_absent` | model_ignored_instruction,other | step 17 capped the extracted set with 'final_vectors = final_vectors[:20]' and step 23 verified a (20, 10) result agains |
| 65 | `model-extraction-relu-logits` | a01 | 失败 | `skill_absent` | model_ignored_instruction,other | step 14 printed 'Stolen A1 shape: (20, 10)' and 'Max row-norm diff: 0.7432980141392508', and the agent marked complete a |
| 66 | `model-extraction-relu-logits` | a02 | 失败 | `skill_absent` | model_ignored_instruction,other | step 117 saved a (34, 10) matrix and immediately marked complete; the visible A1 is (20,10), and tests/test_outputs.py r |
| 67 | `modernize-scientific-stack` | a00 | 成功 | `success_no_skill_needed` | - | step 1 inventoried the legacy files, step 4 ran the new script and printed 'Station 101 mean temperature: -15.5°C' and ' |
| 68 | `modernize-scientific-stack` | a01 | 成功 | `success_no_skill_needed` | - | step 0 read cancel-async seg00, step 2 wrote analyze_climate_modern.py, and step 3 listed it with requirements.txt; no c |
| 69 | `modernize-scientific-stack` | a02 | 成功 | `success_no_skill_needed` | - | step 3 ran 'python3 -m py_compile ... && echo COMPILE_OK' and the smoke run, matching cancel-async seg00's validation st |
| 70 | `mteb-retrieve` | a00 | 失败 | `skill_read_not_followed` | model_ignored_instruction | step 63 loaded '/root/.cache/modelscope/models/BAAI--bge-small-zh-v1.5/snapshots/master' (not the required revision 7999 |
| 71 | `mteb-retrieve` | a01 | 失败 | `model_ignored_instruction` | skill_present_not_read | step 100 overwrote the cached model's 1_Pooling/config.json with pooling_mode 'mean', step 101 used SentenceTransformer  |
| 72 | `mteb-retrieve` | a02 | 失败 | `skill_read_not_followed` | model_ignored_instruction | step 107 wrote a pooling config with pooling_mode_cls_token False and pooling_mode_mean_tokens True, and step 112 wrote  |
| 73 | `multi-source-data-merger` | a00 | 成功 | `success_skill_followed` | - | step 1 read all three sources, step 2 merged them, and step 3 verified the parquet and conflicts.json; bank/merge/0003 w |
| 74 | `multi-source-data-merger` | a01 | 成功 | `success_skill_followed` | - | step 0 read bank/merge/0003, step 1 inspected all three schemas, and step 5 verified the saved parquet and conflicts.jso |
| 75 | `multi-source-data-merger` | a02 | 成功 | `success_skill_followed` | - | step 0 read bank/merge/0003, steps 1-2 inventoried all three sources, and step 6 verified both deliverables. |
| 76 | `nginx-request-logging` | a00 | 成功 | `success_skill_followed` | - | step 0 read bank/merge/0006, step 1 installed nginx, step 4 'nginx -t' printed 'CONFIG_OK', and step 5 served the page a |
| 77 | `nginx-request-logging` | a01 | 成功 | `success_skill_followed` | - | step 0 read bank/merge/0004, step 3 served both the index and the custom 404 and read the access log; the pre-submission |
| 78 | `nginx-request-logging` | a02 | 成功 | `success_skill_followed` | - | step 0 read bank/merge/0006, step 1 probed 'which nginx ... NGINX_NOT_FOUND', step 2 installed it, and step 4 got '200 - |
| 79 | `openssl-selfsigned-cert` | a00 | 成功 | `success_no_skill_needed` | - | Step 0 read bank/merge/0004 (pre-submission gate); steps 1-8 built /app/ssl/server.key, server.crt, server.pem, verifica |
| 80 | `openssl-selfsigned-cert` | a01 | 成功 | `success_no_skill_needed` | - | Step 0 read bank/merge/0000 (video/OCR), irrelevant to TLS; steps 1-6 created the cert and check_cert.py directly and su |
| 81 | `openssl-selfsigned-cert` | a02 | 成功 | `success_no_skill_needed` | - | Step 0 read bank/merge/0000 (video/OCR), irrelevant; steps 1-4 built the cert and check_cert.py and succeeded without an |
| 82 | `overfull-hbox` | a00 | 失败 | `other` | skill_read_not_followed,skill_present_not_read | Step 13 'Forced: Replaced "middle" with "hub" on line 8' changed the capitalized token 'Middle' in 'this Middle Western  |
| 83 | `overfull-hbox` | a01 | 失败 | `model_ignored_instruction` | skill_present_not_read,other | Step 0 read the path-tracing card (unknown files), not a LaTeX card; steps 240, 245, 254, 263 and 264 called mark_task_c |
| 84 | `overfull-hbox` | a02 | 失败 | `other` | skill_read_not_followed,skill_present_not_read | Step 15 printed '"Middle" (6) -> "hub" (3) [in group]'; step 16 applied re.sub(..., flags=re.IGNORECASE) and rewrote inp |
| 85 | `password-recovery` | a00 | 成功 | `success_no_skill_needed` | - | Step 0 read path-tracing (unknown files); steps 1-13 searched /app/varsea and binary-inspected ae3f4c.dat, and step 14 w |
| 86 | `password-recovery` | a01 | 成功 | `success_no_skill_needed` | - | Step 0 read the data-inventory card; steps 1-10 searched strings and binary offsets, and steps 96-99 concatenated the tw |
| 87 | `password-recovery` | a02 | 成功 | `success_no_skill_needed` | - | Steps 2-3 found the strings 'PASSWORD=8XDP5Q2RT9Z' in the .dat; after much searching step 207 read the data-inventory ca |
| 88 | `portfolio-optimization` | a00 | 成功 | `success_skill_followed` | - | Step 0 read 0001; step 4 ran the baseline ('Speedup: 1.2x ... requirement NOT met'), steps 11-12 added OpenMP, step 13 g |
| 89 | `portfolio-optimization` | a01 | 成功 | `success_skill_followed` | - | Step 1 read 0001; steps 5-10 built and benchmarked ('Speedup: 1.4x ... requirement met'), steps 11-13 reran ('Speedup: 1 |
| 90 | `portfolio-optimization` | a02 | 成功 | `success_no_skill_needed` | - | Step 0 read the baseline-inventory card; step 2 wrote the C extension, step 3 built it, step 4-5 benchmarked 'Speedup: 1 |
| 91 | `pypi-server` | a00 | 成功 | `success_no_skill_needed` | - | Step 0 read bank/merge/0000 (video/OCR), irrelevant; steps 2-8 built vectorops, started pypi-server on 8080, installed w |
| 92 | `pypi-server` | a01 | 成功 | `success_no_skill_needed` | - | Step 0 read the standalone-module card; steps 2-4 wrote vectorops and pyproject, steps 5-7 built it, steps 8-14 fixed py |
| 93 | `pypi-server` | a02 | 成功 | `success_skill_followed` | - | Step 0 read 0006; step 1 probed python/pip/packages, step 2 hit a 451 mirror error, step 3 used the official index, step |
| 94 | `qemu-alpine-ssh` | a00 | 失败 | `other` | skill_present_not_read | Steps 3, 7 and 20 started qemu with a plain '&' (step 41 'jobs' shows it as a shell job); steps 59-62 proved SSH works ( |
| 95 | `qemu-alpine-ssh` | a01 | 失败 | `other` | skill_present_not_read | Steps 19/26 started qemu with nohup but still as a shell job; steps 119-124 verified 'SSH_WORKS' and a root shell, while |
| 96 | `qemu-alpine-ssh` | a02 | 失败 | `other` | skill_present_not_read | Steps 415-418 ran 'python3 <<' and never terminated the heredoc (observations show only '> PY'), so the trial ended at s |
| 97 | `regex-log` | a00 | 成功 | `success_no_skill_needed` | - | Step 0 read bank/merge/0000 (video/OCR), irrelevant; steps 2-8 wrote /app/regex.txt and tested it with Python re.findall |
| 98 | `regex-log` | a01 | 成功 | `success_no_skill_needed` | - | Step 0 read the standalone-module card; steps 1-4 wrote and tested the regex and step 5 submitted; no regex skill exists |
| 99 | `regex-log` | a02 | 成功 | `success_no_skill_needed` | - | Step 0 read the pre-submission gate; steps 2-7 built and tested the regex, step 8-9 read it back and submitted; the gate |
| 100 | `rstan-to-pystan` | a00 | 失败 | `other` | - | Step 98 wrote alpha=1.0840255, sigma=0.1341706, rho=0.5793225/0.8843692/1.5015587 and beta=-0.0514678/-0.8208188/0.41773 |
| 101 | `rstan-to-pystan` | a01 | 成功 | `success_no_skill_needed` | - | Step 0 read 0002, but its distinctive structure-learning/intervention steps were not applicable or executed; success cam |
| 102 | `rstan-to-pystan` | a02 | 失败 | `other` | - | Step 95/100 wrote rho[2]=1.511759 (>1.51) and beta[2]=0.431607 (>0.43), just outside the benchmark ranges; steps 81-94 s |
| 103 | `sam-cell-seg` | a00 | 成功 | `success_no_skill_needed` | - | Step 0 read the standalone-module card; steps 13-15 wrote convert_masks.py and py_compile'd it, step 16 ran it on the de |
| 104 | `sam-cell-seg` | a01 | 成功 | `success_no_skill_needed` | - | Step 1 read path-tracing (unknown-file characterization) and inspected the demo files; steps 12-32 wrote and validated c |
| 105 | `sam-cell-seg` | a02 | 失败 | `model_ignored_instruction` | - | Step 64 printed 'Overlapping mask pairs: 3/1128 possible' and then '=== ALL CHECKS PASSED ==='; steps 65-66 submitted; t |
| 106 | `sanitize-git-repo` | a00 | 失败 | `skill_present_not_read` | model_ignored_instruction,skill_read_not_followed | Step 1 greps found the AWS keys and the HF token in ray_processing; steps 3-4 fixed only process.py and ray_cluster.yaml |
| 107 | `sanitize-git-repo` | a01 | 失败 | `skill_present_not_read` | model_ignored_instruction,skill_read_not_followed | Step 11 used 'grep -rn ... | grep -v exp_data/datasets/tokenized', explicitly filtering out the directory that contains  |
| 108 | `sanitize-git-repo` | a02 | 失败 | `skill_present_not_read` | model_ignored_instruction,skill_read_not_followed | Step 1 'find . -type f -not -path './.git/*' | head -80' stops at exp_data/datasets/raw_sources, before tokenized; steps |
| 109 | `torch-pipeline-parallelism` | a00 | 失败 | `model_ignored_instruction` | skill_absent | Step 140 ran its own test: '=== WS=1 === max_diff=0.0000000000 PASS' then '=== WS=2 === AttributeError: NoneType object  |
| 110 | `torch-pipeline-parallelism` | a01 | 失败 | `model_ignored_instruction` | skill_absent | Step 91's WS=2 test printed 'Loss: 0.0000' (a red flag against the expected cross-entropy), steps 96 removed the test fi |
| 111 | `torch-pipeline-parallelism` | a02 | 失败 | `model_ignored_instruction` | skill_absent | Step 274 ran test_full.py; step 275's observation shows a rank1 traceback in dist.gather ('_check_tensor_list(gather_lis |
| 112 | `video-processing` | a00 | 失败 | `skill_absent` | other | The model wrote a cv2 frame-difference detector (steps 13-33) and ended with output.toml 'jump_takeoff_frame_number = 50 |
| 113 | `video-processing` | a01 | 失败 | `skill_absent` | model_ignored_instruction | The only read was catalog.md (step 0), not any SKILL.md; the model built jump_analyzer.py and ended with 'Takeoff frame: |
| 114 | `video-processing` | a02 | 失败 | `skill_absent` | other | The detector's answer drifted across runs (step 26: 114/116; step 28: 51/71; step 31: 53/71; step 36: 55/71; step 42: 57 |
| 115 | `winning-avg-corewars` | a00 | 失败 | `skill_read_not_followed` | model_ignored_instruction | Step 146 ran 'for opp in warriors/*.red; do pmars -b -r 10 -f my_warrior.red "$opp" | tail -1; done' and got 'Results: 0 |
| 116 | `winning-avg-corewars` | a01 | 失败 | `skill_read_not_followed` | other | After reading 0001 it generated variants and scored them (step 166: stone 60, paper 86, vampire 92, snake 52, g2 67); st |
| 117 | `winning-avg-corewars` | a02 | 失败 | `skill_followed_still_failed` | skill_read_not_followed | It followed 0001's sweep loop (steps 244-253), whose best candidates reach only 's=41..52 p=82..88 v=97..99 sn=12..33 gc |

## 7. 数据局限

- 控制臂无轨迹，第 4 问对 control 无法回答。
- 证据包不含验证器的逐项输出（`grading` 只有 verdict/basis），少数 trial 的最终失败只能推断到「其它」并在明细里注明。
- 中间 observation 有 `[... N characters omitted ...]` 截断，最后 6 步完整；`skill_reads[].action` 约 200 字符。
- `mark_task_complete` 首次调用只是提示，部分 trial 未二次确认即结束，因此 `correctness=false` 里可能混有超时自动提交。

## 8. 复核方法

证据包是从 run 目录只读派生的：读取 `<run_dir>/phases/p07_test/answer_sheets/*.json`
与 `<run_dir>/phases/p06_learn_trial/skill_bank.jsonl`，把 with_bank 的每个 trial 写成一个文件
（长 observation 截断、最后 6 步完整），再按任务切成三组交给 subagent。派生脚本是一次性的，
没有进入仓库；需要复核时按这个说明重建即可。

逐 trial 的原始判定在三份 `out_group<N>.jsonl`（已合并进本报告第 6 节），覆盖表在
`out_group<N>_coverage.jsonl`。
