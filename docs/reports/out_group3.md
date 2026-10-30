# Group 3（13 题、39 个 with_bank trial）失败分析

## 一、口径与总量

- 负责的 13 题：`openssl-selfsigned-cert`、`overfull-hbox`、`password-recovery`、`portfolio-optimization`、`pypi-server`、`qemu-alpine-ssh`、`regex-log`、`rstan-to-pystan`、`sam-cell-seg`、`sanitize-git-repo`、`torch-pipeline-parallelism`、`video-processing`、`winning-avg-corewars`。
- 共 39 个 with_bank trial（每题 3 次）：18 成功、21 失败。同题 control 臂合计 15/39。
- 交付物是**全局一份**技能文件夹（17 条 + `catalog.md`），39 个 trial 的 `skills_delivered` 完全相同（`bank:3bb3ee40d638`）。本报告里的"技能是否适用"指这 17 条里是否有语义对应，而不是该题自己有没有技能。
- 所有判断以 `trials/<task>__aNN.json` 的 step 号为准；引文均来自该 trial 的 `steps[i].action/observation`。标注为"benchmark 校验"的结论另引 `benchmark/terminalbench-eval/sources/agentbrew-tb21-tasks/<task>/tests/` 下的判分脚本。

## 二、类别计数

### 失败 trial（21 个，主类互斥）

| 主类 | 个数 | trial |
|---|---|---|
| `other` | 7 | overfull-hbox a00/a02、qemu-alpine-ssh a00/a01/a02、rstan-to-pystan a00/a02 |
| `model_ignored_instruction` | 5 | overfull-hbox a01、sam-cell-seg a02、torch-pipeline-parallelism a00/a01/a02 |
| `skill_present_not_read` | 3 | sanitize-git-repo a00/a01/a02 |
| `skill_absent` | 3 | video-processing a00/a01/a02 |
| `skill_read_not_followed` | 2 | winning-avg-corewars a00/a01 |
| `skill_followed_still_failed` | 1 | winning-avg-corewars a02 |
| `environment_or_infra` | 0 | — |

### 成功 trial（18 个）

| 主类 | 个数 | trial |
|---|---|---|
| `success_no_skill_needed` | 15 | openssl ×3、password-recovery ×3、portfolio a02、pypi a00/a01、regex-log ×3、rstan a01、sam a00/a01 |
| `success_skill_followed` | 3 | portfolio a00/a01（`bank/merge/0001`）、pypi a02（`bank/merge/0006`） |

### 覆盖表三档

| 档位 | 题数 | 题 |
|---|---|---|
| `clear_match` | 4 | portfolio-optimization（0001）、pypi-server（0006）、sanitize-git-repo（filter-js seg02）、winning-avg-corewars（0001） |
| `marginal` | 6 | overfull-hbox（0001）、password-recovery（path-tracing seg00）、qemu-alpine-ssh（0004 第 20 步）、rstan-to-pystan（0002）、sam-cell-seg（cancel-async seg00）、video-processing（0000） |
| `none` | 3 | openssl-selfsigned-cert、regex-log、torch-pipeline-parallelism |

- **"有明确对应但没读"的 trial：6 个**——sanitize 3 个（`filter-js` seg02 全程未 cat）、pypi a00/a01（0006 未读）、portfolio a02（0001 未读）。
- **"根本没有对应"的 trial：9 个**——openssl 3、regex-log 3、torch 3。

## 三、按类别看典型 trial

### 1. `skill_absent`（3 个：video-processing）

**video-processing a01（step 85）**：模型自建 `jump_analyzer.py`，最终输出

> `Takeoff frame: 45`
> `Landing frame: 71`

但它只读了 `catalog.md`（step 0），没有任何 `SKILL.md`；17 条里也没有 cv2 运动/起跳帧检测技能。`bank/merge/0000` 唯一沾"视频"边的条目是 ffmpeg+tesseract OCR 抽取有序文本，和"单次跨栏起跳/落地帧"不是同一能力。

**video-processing a02（step 44）**：同一启发式在不同 step 给出不同答案——step 26 `114/116`、step 28 `51/71`、step 31 `53/71`、step 36 `55/71`、step 42 `57/65`，最后写 `jump_takeoff_frame_number = 57 / jump_land_frame_number = 65`。没有真值可校准，也没有技能可依，属于能力缺口。

torch 的三个 trial 也把 `skill_absent` 列为 secondary：step 140 的 `=== WS=2 === AttributeError: 'NoneType' object has no attribute 'clone'` 说明 pipeline parallel 的实现问题没有技能可参考（0005 是 async 取消调试，不是 pipeline parallel）。

### 2. `skill_present_not_read`（3 个：sanitize-git-repo）

这是本组最清楚的一类：题目要清洗仓库里的所有 API key，17 条里 **`traj/tb:filter-js-from-html/strong/00/seg02/skill`** 正是"原地 sanitizer + 备份 + 逐规则 marker 验证"，三个 trial 都没有 cat 它。

- **a01（step 11）**：命令里直接写了 `grep -rn ... | grep -v exp_data/datasets/tokenized`，把**唯一还含 HF token 的目录**过滤掉了；step 8-9 只改了 `ray_processing/process.py` 和 `ray_cluster.yaml`，step 14/15 提交。引文：`grep -v exp_data/datasets/tokenized`。
- **a02（step 1）**：`find . -type f -not -path './.git/*' | head -80` 在 `exp_data/datasets/raw_sources` 处截断，从未看到 `exp_data/datasets/tokenized/rw_v2_fasttext_openhermes_vs_rw_v2_bigram_0.1_arcade100k.json`；step 5-6 只修两个文件，step 9/11/12 提交。
- **a00（step 1）**：grep 已经列出 `ray_processing/process.py:122` 的 AWS key、`ray_cluster.yaml` 的 AWS/GitHub/HF token，以及其它 tokenized JSON 的匹配行，但 step 3-4 只替换了两个文件里的值，step 5 提交；目标 JSON 从未被打开。

benchmark 的 `test_output_files`/`test_removal_of_secret_information` 要求三个 `CONTAMINATED_PATHS` 都不含 token，其中第三个就是上述 JSON；`test_correct_replacement_of_secret_information` 还要求逐字节等于参考去污文件。所以三个 trial 的失败点是同一个：**第三个污染文件没改**，不是占位符写法问题。

### 3. `skill_read_not_followed`（2 个：winning-avg-corewars a00/a01）

**a00（step 146）**：已经读了 `bank/merge/0001`（step 1、step 150 又读一次），并在 step 146 跑最终自检：

> `echo 'Final validation:' && for opp in warriors/*.red; do pmars -b -r 10 -f my_warrior.red "$opp" 2>&1 | tail -1; done`
> `Results: 0 10 0` / `0 0 10` / `0 10 0` / `0 10 0` / `10 0 0`

按 `g2-clear, paper, snake, stone, vampire` 的字母序，对 g2-clear/snake/stone 都是 **0 胜**，却当场 `mark_task_complete({})`；后面 100 多步在 `/tmp/t_*.red` 上调参，再也没有把候选写回 `/app/my_warrior.red`（最后一次写是 step 140）。这是 0001 "promote a reproducible champion / validate exactness" 没有执行。

**a01（step 136 之后）**：最后一次写 `/app/my_warrior.red` 是 step 136 的 `cp pmars-0.9.4/warriors/aeka.red my_warrior.red`。step 154 试图跑五对手全检，但命令写成 `warriors/$opp`（漏了 `.red`），observation 只有 `=== my_warrior.red ===` 后直接回到提示符，**没有任何 Results**；step 158 也出现 `Unable to open file 'warriors/stone'`。step 155 只测了 vs stone（`Results: 90 0 10`）。之后 step 162-189 全部在 `/tmp` 生成/扫描变体（step 166 `stone 60 paper 86 vampire 92 snake 52 g2-clear 67`；step 171 最好的 `[BCSG]` 候选 `s=57 p=86 v=89 n=33 g=67`），从未把更好的候选晋升到 `my_warrior.red`，也没有对最终文件跑五对手全检；trial 在 step 189 写 `sweep2.py` 时结束。

### 4. `skill_followed_still_failed`（1 个：winning-avg-corewars a02）

a02 读了 0001（step 0、step 159 重读），并按它的循环在 step 244-253 扫参数。step 253 的 sweep 直接给出上限：

> `Best: DJ=-370 s=41 p=82 v=99 sn=27 gc=64 [NEAR]`
> `DJ=-190: s=52 p=86 v=97 sn=12 gc=48`

即 stone 最好约 52、snake 12–33，达不到题目要求的 stone/vampire/paper ≥75、snake/g2-clear ≥33。技能的执行是到位的（基线、变体、pmars 评分、记录），但 0001 只是通用"优化循环"，不含 CoreWars 战术，属于"技能内容不足/不对"。次要问题是 step 243 写 `my_warrior.red` 后又在 `/tmp` 试了 10 步，没有把最后候选晋升。

### 5. `model_ignored_instruction`（5 个）

- **overfull-hbox a01（step 240/245/254/263/264）**：五次 `mark_task_complete({})` 都发生在后台搜索脚本仍在打印时，例如 step 264 的 observation 仍是 `Seed 41, iter 5: 'rooms' -> 'chambers' (4->3)`；从 step 240 起没有任何一次 `grep Overfull` 或最终编译验证。题面/执行 prompt 明确说 "do not declare the task complete until you have verified the goal against the task description"。
- **sam-cell-seg a02（step 64）**：自己的验证脚本打印
  > `Overlapping mask pairs: 3/1128 possible`
  > `=== ALL CHECKS PASSED ===`

  然后 step 65/66 提交。题面明确写 "there should be no overlap between masks"，模型看到了 3 对重叠却把它当通过。
- **torch a00（step 140/143）**：自测 `=== WS=1 === max_diff=0.0000000000 PASS`，紧接着 `=== WS=2 === AttributeError: 'NoneType' object has no attribute 'clone'`；step 143 只做 `Import OK` 就提交。题面明确 "The tests will use world_size values of 1, 2."
- **torch a01（step 91/96-99）**：WS=2 打印 `Loss: 0.0000`（与随机目标应有的 ~4.6 不符），step 96 删掉测试文件，step 97 只做 import 检查就提交；题面要求"比较 forward/backward activations against a reference model"。
- **torch a02（step 274/275）**：`torchrun --nproc_per_node=2 test_full.py` 后 rank1 在 `dist.gather` 处报 `_check_tensor_list(gather_list, "gather_list")`，step 276-277 未修就提交。

### 6. `other`（7 个）

**overfull-hbox a00/a02（大小写）**：

- a00 step 13：`Forced: Replaced 'middle' with 'hub' on line 8 (saved 3 chars)`。line 8（1-based line 9）原文是 `... in this Middle Western city ...`，替换后变成小写 `hub`；step 14 编译干净（无 Overfull），step 15-16 提交。
- a02 step 15 自己就算出了 `"Middle" (6) -> "hub" (3) [in group]`，step 16 用 `re.sub(pattern, shortest, text, flags=re.IGNORECASE)` 全局替换，step 17 diff、step 18 提交。
- benchmark 的 `test_input_file_matches` 用 `synonyms[w]` 做**区分大小写**的成员检查；`synonyms.txt` 的键是 `middle`，原文 token 是 `Middle`，所以 `Middle -> hub` 被判为非法替换，即使 `hub` 确实是 `middle` 的同义词。这是本组最典型的"任务措辞/判分实现"错位（见第五节 prompt_issue）。

**qemu-alpine-ssh a00/a01/a02**：

- a00 用 `qemu-system-x86_64 ... &` 起 VM（step 3/7/20，step 41 的 `jobs` 仍显示为 shell job），step 59-62 已经用 `ssh -p 2222 root@localhost` + `password123` 拿到 `hello` 和 shell；但 qemu 从未 `-daemonize`/setsid。若评分在 agent shell/进程组退出后运行，VM 已不在，`sshpass` 就找不到服务——transcript 没有 verifier 日志，无法把这一点钉死，因此归 `other`。
- a01 用 `nohup ... &`（step 19/26）但仍是同一进程组；step 117-124 验证了 `SSH_WORKS` 和 root shell，step 117 还显示 `/etc/alpine-release: No such file`（从解包的 initramfs 启动，不是完整 Alpine root）；评分仍失败。transcript 没有 verifier 输出，无法把失败点钉死。
- a02 根本没有走到 SSH 验证：step 415-418 的 `python3 << 'PY'` heredoc 没有结束（observation 只有 `> PY`），trial 在 step 418 结束。

**rstan-to-pystan a00/a02（数值 near-miss）**：

- a00 step 98：`alpha=1.0840255, sigma=0.1341706, rho=0.5793225/0.8843692/1.5015587, beta=-0.0514678/-0.8208188/0.4177393`；只有 `rho[1]=0.88437` 低于 benchmark 的 `[0.886, 1.0]`，其余全过。
- a02 step 95/100：`rho[2]=1.511759 > 1.51`、`beta[2]=0.431607 > 0.43`，各超上界约 0.0016；step 81-94 反复出现 `cholesky_decompose: A is not symmetric. A[1,2] = inf`，且每 chain 只取到 500 draws，说明采样有 divergent/配置差异。

### 7. 成功 trial 里技能是否真被用上

- **portfolio a00/a01**：都读 `bank/merge/0001` 并照做。a00 step 4 先跑 baseline（`Speedup: 1.2x ... requirement NOT met`），step 11 加 OpenMP，step 13 `Speedup: 1.3x ... requirement met`，step 14-15 复跑确认后提交；a01 step 5-10 构建/基准（1.4x），step 11-13 复跑（1.3x，`ALL TESTS PASSED`）后提交。
- **pypi a02**：读 `bank/merge/0006` 并照 probe-first 循环做。step 1 先 `python3 --version; pip --version; pip list`，step 2 mirror 451，step 3 换官方 index，step 6 `setup.py bdist_wheel`，step 8 首次 pypi-server 崩溃，step 9 升级，step 10 起服务，step 11 `pip install --index-url http://localhost:8080/simple vectorops==0.1.0`，step 12 `TEST PASSED`。
- 其余 15 个成功 trial 都只读了**无关或边缘**技能（openssl 读视频 OCR 0000/交付门 0004、regex 读 0000/0004、pypi a00 读 0000、password 读 path-tracing/0003、rstan a01 读 0002、sam 读 cancel-async/path-tracing），成功来自模型自身的 openssl/regex/取证/PyStan/MobileSAM/C 扩展知识，而不是技能内容。

## 四、library 缺什么能力

1. **LaTeX overfull hbox 修复 + 同义词/大小写约束**：17 条里没有 LaTeX 技能；`bank/merge/0001` 只是通用优化循环，且三个 overfull trial 都没读它。
2. **QEMU/Alpine/sshd 的启动与持久化**：没有 qemu 技能；`bank/merge/0004` 第 20 步虽提到 service/boot image 用 nohup 分离启动，但没有 qemu 网络转发、Alpine 网络、sshd_config、root 密码这些内容，且三个 qemu trial 都没读 0004。
3. **PyTorch pipeline parallel（AFAB、P2P、microbatch 梯度）**：完全没有；0005 是 async 取消，不是 pipeline parallel。
4. **视频运动/起跳落地帧检测**：`bank/merge/0000` 是视频 OCR，不是运动分析。
5. **R 脚本 → PyStan 3.10 的转换与 Stan 模型等价性检查**：`bank/merge/0002` 是结构学习/干预模型，其 "Do not use when" 明确排除非线性/单一参数集，不能覆盖本题。
6. **MobileSAM mask 转 polyline、重叠消除、单连通性保证**：没有；cancel-async seg00 只覆盖"写独立 Python 模块"的交付形式。
7. **仓库级 secret 扫描（包括嵌套的 tokenized JSON、逐字节参考对照）**：filter-js seg02 只覆盖原地 sanitizer 的字节保持/验证，不覆盖"找全所有污染文件"。
8. **大小写保持的 token 替换 / 参考文件精确对照**：overfull 的判分是 case-sensitive token 检查，现有技能没有这条规则（0004 的 "independent reference" 只到通用层面）。

## 五、prompt wording 是否是 plausible cause

只有两类具体措辞值得记录：

1. **`TB_SKILL_FOLDER_GUIDANCE` 的强制读取句**：
   > "Before starting the task itself, read the location of at least one skill that could plausibly help with the task. You MUST do this at least once before running any command for the task itself, even if the task looks simple."

   它对没有对应技能的题也会逼出一次读技能：openssl a01/a02、regex a00、pypi a00、qemu a00 都因此读了 `bank-merge-0000`（视频/OCR）；overfull a01 因此读了 `path-tracing` seg00（未知文件），随后 265 步都在做与技能方法无关的 LaTeX 随机搜索。这个措辞不是失败的直接原因（模型多数时候读完后没有照做），但它系统性地制造了无关技能读取和上下文浪费，且会让弱模型误以为"必须从技能里找方法"。
2. **overfull-hbox 题面与判分的措辞错位**：
   > "the only edits you may make are to replace words in input.tex with their specified synonyms in synonyms.txt"

   题面没有要求保持大小写，`synonyms.txt` 的 `centre, middle, core, heart, hub` 也不区分大小写地看 `Middle -> hub` 是合法替换；但判分脚本 `synonyms[w]` 是大小写敏感的。a00 step 13 和 a02 step 15/16 都因此"按题面做对、按判分做错"。这是本组唯一能明确归因到措辞/判分口径的失败。

其余失败（qemu、sanitize、torch、corewars、sam、video、rstan）都没有具体措辞导致错误决策的证据；它们的题面要求是明确的，模型是没做到或没验证。例如 torch 题面明写 "The tests will use world_size values of 1, 2."，corewars 明写 75%/33% 阈值，sanitize 明写 "ensure that the sensitive values are not present in the repository"。

## 六、翻转题：rstan-to-pystan、overfull-hbox

### 6.1 rstan-to-pystan（control 0/3，with_bank 1/3）

**成功的是 a01。它读了一条技能，但没有执行该技能的核心步骤，成功与技能无关。**

- 读取：step 0 `cat /opt/openclaw-skills/rstan-to-pystan/bank-merge-0002/SKILL.md`，skill_id = `bank/merge/0002`。
- 只重叠了通用 preflight：step 1 读 `gp_rstan.R`，step 2 `head -5` 三个 CSV + `cat meta_public.json`，step 3 查 Python/pip 版本——对应 0002 step 1-3 的"读任务/探运行时/看数据"。
- 0002 的**独有步骤没有执行**：它要求结构学习、OLS、do-style intervention、合成采样；本题是非线性 GP 后验采样、没有 intervention，0002 自己的 "Do not use when" 就排除了这类任务（"Do not use it when variables are categorical or discrete or non-linear ... the deliverable is a single curve or parameter set with no model modification"）。
- 真正通过靠的是 task-specific 转换：step 4-5 `pip3 install --break-system-packages pystan==3.10.0`（发现实际 import 名是 `stan`），step 6/13/25/59 把 R 里的 Stan 模型改写进 `/app/pystan_analysis.py`，step 47/59-73 用 `stan.build(..., random_seed=1)` + 4 chains × 1000 samples 采样，step 89 写出 `alpha=1.086586270081184`、`sigma=0.1346806055778249`、`rho=0.5836347878096023/0.887831292144057/1.498615329821254`、`beta=-0.04892430719092779/-0.8197952554222513/0.4107190882861316`，全部落在 benchmark 范围内。
- 结论：**这次提升与技能无关**。0002 的读取没有造成可观察的转换步骤；成功来自模型对 RStan→PyStan API 的掌握和多次采样调参。

两次失败都是 near-miss，不是技能问题：

- a00（读 0006，不是 0002）step 98 只有 `rho[1]=0.8843692` 低于 `[0.886, 1.0]`；它为了装 pystan 手工把 `/tmp/pystan_tmp/stan` 拷进 dist-packages（step 13），最后 4 chains × 1000 采样（step 94-98）。
- a02（读 0002）step 95/100 的 `rho[2]=1.511759` 和 `beta[2]=0.431607` 各超上界 0.0016 左右；step 81-94 反复出现 `cholesky_decompose ... A[1,2] = inf`，每 chain 只取到 500 draws，采样质量是直接原因。

### 6.2 overfull-hbox（control 1/3，with_bank 0/3）

逐次失败点：

- **a00**：step 0 读 `bank/merge/0004`（pre-submission gate）。step 13 的搜索脚本打印 `Forced: Replaced 'middle' with 'hub' on line 8 (saved 3 chars)`；line 8 是 `... in this Middle Western city ...`，即把大写 `Middle` 写成小写 `hub`。step 14 `pdflatex` 输出无 Overfull，step 15 `grep 'Overfull' /app/main.log` 为空、`echo "Compilation verified: $?"` 打出 `1`（这是 grep 的退出码，不是编译结果），step 16 提交。判分 `test_input_file_matches` 的大小写敏感检查拒绝 `Middle -> hub`。
- **a01**：step 0 读 `catalog.md` + `path-tracing` seg00（不是 LaTeX 技能）。随后 265 步都在用随机搜索/多 seed 脚本试同义词；step 240、245、254、263、264 连续 `mark_task_complete({})`，其中 step 264 的 observation 仍是 `Seed 41, iter 5: 'rooms' -> 'chambers' (4->3)`，没有最终编译检查，input.tex 的最终状态也没有被验证。
- **a02**：step 0 读 `bank/merge/0004`。step 15 自己打印 `"Middle" (6) -> "hub" (3) [in group]`；step 16 用 `re.sub(..., flags=re.IGNORECASE)` 把全文所有可替换词换成最短同义词并写回；step 17 `diff` 后 step 18 提交。与 a00 同一个大小写敏感失败。

**有没有"读了技能但被带偏"或"技能不适用却仍执行"？**

- a00/a02 读的 0004 是通用的 pre-submission gate，不是"错误建议"：它要求 independent reference / 逐约束验证，但没提大小写；模型只做了"编译无 Overfull"这一项，没做 token 级约束核对。这更像"技能不够具体 + 模型没按 gate 做完"，不是被技能带偏。
- a01 读的 path-tracing seg00 对 LaTeX **不适用**，它确实执行了其中的文件特征化（`cat main.tex`、`cat input.tex`、`cat synonyms.txt`），但这一步本身无害；失败来自后续没有 LaTeX 方法可依、随机搜索失控和未验证提交。没有证据表明它照搬了 path-tracing 的错误步骤。
- 三道题都**没有读** `bank/merge/0001`——17 条里唯一在语义上接近"候选文本 + pdflatex evaluator + 变体搜索"的技能。这是覆盖表把 overfull 标为 `marginal/0001` 的原因，也是"有明确对应但没读"的一个例子（a00/a02 只读了 0004，a01 只读了 path-tracing）。

## 七、数据问题与限制

1. **数据完整**：13 题 × 3 = 39 个 trial 文件全部存在，没有缺失 trial，也没有空 steps 的 transcript。
2. **observation 有裁剪**：中段 observation 用显式的 `[... N characters omitted ...]` 截断，最后 6 步完整；因此少量中间输出（例如 sanitize 的长 grep 结果、overfull 的完整替换列表）无法逐行核对。`skill_reads[].action` 也在约 200 字符处被截断（例如 overfull a01 的 `.../SKILL.m`），但 observation 能确认实际读取了对应技能。
3. **没有 verifier 输出**：trial JSON 的 `grading` 只有 `{verdict, basis}`，不含 pytest/verifier 日志。qemu a00/a01、overfull a01 的最终失败、corewars a01 的结束状态都无法从 transcript 直接读到判分原因；本报告对这些使用 `other` 并注明证据不足。rstan/sanitize/overfull 的失败点是通过 workspace 里 benchmark 的 `tests/test_outputs.py` 反推的，属于 trial JSON 之外的证据。
4. **`mark_task_complete` 语义**：第一次调用只返回 "Are you sure..."，第二次才真正提交。corewars a01 从未调用；a00 在 step 146 调用后继续跑了 100 多步；a02 在 157/231/243/245/249 多次调用但最后又在 step 250-253 继续试验。因此这些 trial 的 `correctness=false` 可能包含"超时/自动提交"，不一定是一次明确的最终提交；引用时我把最终文件内容和最后一次自检一起写进了 evidence。
5. **交付是全局 bank**：所有 trial 的 `skills_delivered` 都是 `bank:3bb3ee40d638`，17 条技能在每个题目的 `/opt/openclaw-skills/<task>/` 下都可见；不存在"该题自己的技能文件夹"这回事，覆盖判断按 17 条的语义做。
