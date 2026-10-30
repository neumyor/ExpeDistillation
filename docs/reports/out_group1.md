# Group 1（13 题）with_bank trial 失败分析

日期：2026-10-30
范围：`group1_tasks.txt` 的 13 道题 × 3 attempts，共 39 个 with_bank trial（25 成功、14 失败）。
数据：`tmp/p07_analysis/`（`index.jsonl`、`trials/`、`bank.json`、`control.jsonl`）；只读使用
`scripts/tb21/vendor/tasks/<task>/instruction.md` 与 `tests/test_outputs.py` 核对任务要求与判分口径。

判定口径说明：

- 技能库是**全局一份**：39 个 trial 的 `skills_delivered` 都是 `bank:3bb3ee40d638`，即
  `bank.json` 里的 17 条；`/opt/openclaw-skills/<task>/<skill>/SKILL.md` 的任务名前缀只是挂载路径，
  不是逐题技能集。覆盖判断一律对照这 17 条。
- “读了某技能”只算 `cat .../<skill_id>/SKILL.md`；读 `catalog.md` 或任务根目录的
  `SKILL.md`（folder guidance）不算打开技能卡。`index.jsonl` 的 `n_skill_reads` 会把
  catalog/根 SKILL.md 也算进去（例如 adaptive-rejection-sampler a02 记为 5，实际一个技能卡都没打开）。
- trial JSON 不含 verifier 的逐项输出。凡最终文件状态不能直接说明失败点的（git-multibranch a00、
  adaptive-rejection-sampler a01），我按 vendored `tests/test_outputs.py` 对最终产物做只读推断，
  并在对应行注明；其余失败点都能在 transcript 里直接看到。

## 一、类别计数（primary，互斥）

失败 14 个：

| 类别 | n | trial |
|---|---|---|
| `skill_absent` | 6 | circuit-fibsqrt a00–a02；feal-differential-cryptanalysis a00–a02 |
| `skill_followed_still_failed` | 3 | chess-best-move a00–a02 |
| `model_ignored_instruction` | 2 | adaptive-rejection-sampler a01；git-multibranch a00 |
| `other` | 3 | adaptive-rejection-sampler a02；distribution-search a02；feal-linear-cryptanalysis a01 |
| `skill_present_not_read` | 0 | （只有 distribution-search a02 作为 secondary） |
| `skill_read_not_followed` | 0 | — |
| `environment_or_infra` | 0 | （两个终端卡死案例作为 secondary） |

成功 25 个：

| 类别 | n | trial |
|---|---|---|
| `success_skill_followed` | 8 | code-from-image a00–a02；configure-git-webserver a00–a01；distribution-search a00；fix-ocaml-gc a00–a01 |
| `success_no_skill_needed` | 17 | 其余成功 trial（含 adaptive-rejection-sampler a00 翻转题） |

Secondary 计数：`other` 7、`environment_or_infra` 2、`skill_absent` 2、`skill_present_not_read` 1。

逐题主类：

| task | a00 | a01 | a02 |
|---|---|---|---|
| adaptive-rejection-sampler | success_no_skill_needed | model_ignored_instruction | other |
| chess-best-move | skill_followed_still_failed | skill_followed_still_failed | skill_followed_still_failed |
| circuit-fibsqrt | skill_absent | skill_absent | skill_absent |
| code-from-image | success_skill_followed | success_skill_followed | success_skill_followed |
| configure-git-webserver | success_skill_followed | success_skill_followed | success_no_skill_needed |
| constraints-scheduling | success_no_skill_needed | success_no_skill_needed | success_no_skill_needed |
| crack-7z-hash | success_no_skill_needed | success_no_skill_needed | success_no_skill_needed |
| distribution-search | success_skill_followed | success_no_skill_needed | other |
| feal-differential-cryptanalysis | skill_absent | skill_absent | skill_absent |
| feal-linear-cryptanalysis | success_no_skill_needed | other | success_no_skill_needed |
| fix-ocaml-gc | success_skill_followed | success_skill_followed | success_no_skill_needed |
| git-leak-recovery | success_no_skill_needed | success_no_skill_needed | success_no_skill_needed |
| git-multibranch | model_ignored_instruction | success_no_skill_needed | success_no_skill_needed |

## 二、逐类典型证据

### `skill_absent`（6）

- **circuit-fibsqrt a00**：step 0 读了 `bank/merge/0000` 与 `bank/merge/0004`，但没有任何一条讲
  逻辑门电路合成。最后 `/app/gates.txt` 仍是 33 行的示例文件：step 20
  `wc -l /app/gates.txt` → `33 /app/gates.txt`；transcript 在 step 25 `cat catalog.md` 后结束，
  没有 `mark_task_complete`。a01、a02 同样停在 33 行（a01 step 25、a02 step 28/51 的
  `wc -l` 都是 `33 /app/gates.txt`）。
- **feal-differential-cryptanalysis a02**：step 1 读 `bank/merge/0000`、step 18 读
  `path-tracing seg00`，都不涉及密码分析。step 43 `cat /app/attack.py` 仍能看到占位实现：
  `pass` 与 `return best_k5 if best_k5 is not None else 0`，trial 在 step 44 结束，无 completion。
  a00 的 step 52 现场验证打印 `MATCH: False`；a01 step 62 的 trace 打印 `match=False`，同样在
  没有完成攻击的情况下结束。

### `skill_followed_still_failed`（3）

- **chess-best-move a00**：step 1 读 `traj/tb:path-tracing/strong/00/seg01/skill`，随后照做了它的
  图像刻画步骤（step 3 `PIL ... Format: PNG Size: (640, 640) Mode: RGB`；step 5–6 采样棋盘格
  颜色）。但重建出的 FEN 是 `r1kq1r2/1p3pp1/n3b3/3bPbnP/8/N7/1P3PP1/R1B1K2R`，step 35 的
  stockfish 输出 `bestmove b2b3`，step 37 `echo -n 'b2b3' > /app/move.txt`。verifier 要求的是
  `sorted(move) == sorted(["g2g4", "e2e4"])`——两个一步杀。a01、a02 也都写了 `b2b3`
  （a01 step 37 `b2b3: 40/40`、step 38 写入；a02 step 39 FEN 出现重复棋子
  `rnqkkrnr/...`、step 85 写入）。也就是说：技能卡里的“图像刻画”部分被照做了，但该卡没有
  棋子识别/局面复原的内容，失败是技能内容不足，而不是没照做。

### `model_ignored_instruction`（2）

- **adaptive-rejection-sampler a01**：任务原文说 `dnorm` 这类“密度函数”是合法输入
  （“…such as 'dnorm' are legitimate inputs”），但 step 284 的最终 `.log_density` 是原样透传：
  `v <- suppressWarnings(f(x, ...))`，并没有取 log；step 295 的自测也全部用
  `function(x) dnorm(x, log=TRUE)`。verifier 的
  `test_can_generate_standard_distribution_samples` 传的是裸 `dnorm`，会被当成 log-密度，
  均值/方差检查无法通过。transcript 还在 step 304 被截断，没有 completion。
- **git-multibranch a00**：step 0 只读了 `catalog.md`，没有打开任何技能卡。step 35
  `rm -rf /var/www/deploy/index.html /var/www/deploy/dev/index.html` 之后立刻
  `mark_task_complete({})`；而 step 32 已经演示过同样的删除会让
  `curl -sk https://localhost:8443/dev/index.html` 返回 `<title>404 Not Found</title>`。
  harness prompt（`TB_TASK_PROMPT_FOLDER`）明确要求“do not declare the task complete until you
  have verified the goal against the task description”，模型在最后一次改动后没有重新验证就提交。

### `other`（3）

- **adaptive-rejection-sampler a02**：最终 `/app/ars.R`（step 58 重写，9623 bytes）定义了
  13 个函数（step 64 `grep -c 'function('` → `13`）但没有 `test`（内容里只有 `test_pts`）；`test()`
  被放在 `/app/test.R`（step 60 写入，step 61 本地 `17/17 tests passed`）。verifier 只做
  `source("ars.R"); if (exists("test")) test()`，所以它看到的是 “test function not found”。
- **distribution-search a02**：step 11 的一个大 heredoc/脚本让终端进入长时间前台运行，
  从 step 12 到 234 观察一直是 `Seed 298: fun=1.50e+00`，期间 `C-c`、`^C`、`reset` 都没能
  回到 prompt；trial 在 step 234 直接结束，没有观察到 `/app/dist.npy` 成功保存，也没有 completion。
- **feal-linear-cryptanalysis a01**：step 84 的 K0 扫描结果是 `K0=0/1048576, matches=0`；
  step 86 `cat > /app/solver_final.c << 'CEOF'` 之后终端卡在 `Pair1: ...`，step 87–215 的
  输出完全相同，`C-c`/`CEOF` 都无效，最终没有合法 key，也没有 completion。

### `skill_present_not_read`（仅 secondary，1）

- **distribution-search a02**：step 0 读 `catalog.md`（列表里明确有 `bank/merge/0001`），
  step 1 却打开 `llm-inference seg00`；整条轨迹从未 `cat bank/merge/0001/SKILL.md`。
  `unread_step_range = "2-234"`（step 0 读 catalog、step 1 读 seg00，其余步骤不碰技能目录）。
  同题 a00 读了 0001 并成功，所以这是一次“有明确对应但没读”；不过它的直接死因是终端卡死，
  因此 primary 仍是 `other`。

### 成功侧：`success_skill_followed`（8）与 `success_no_skill_needed`（17）

- **code-from-image a00**：step 0 读 `path-tracing seg01`，step 2 `apt-get install tesseract-ocr`，
  step 13 逐行 OCR 出 `def main():`、`CALT = 6’0000TBENCH-SALT’` 等，step 52 写入正确的
  `bee26a133f...`。a01 用 `bank/merge/0000` 的 OCR 步骤、a02 用 seg01，三连成功。
- **fix-ocaml-gc a00**：step 0 读 `bank/merge/0006`，step 7 `./configure`、step 9 `make -j4`
  复现 `Segmentation fault ... Error 2`，修复后 step 276
  `make -C testsuite one DIR=tests/basic` 输出 `40 tests passed`。a01 同样读 0006：step 62 的
  同一 testsuite 报 `40 tests passed`，step 63 的 diff 确认修复是
  `p += Whsize_hd(hd)` → `p += wh`。a02 没读 0006（只读了 seg00），也成功。
- **distribution-search a00**：step 0 读 `bank/merge/0001`，step 2–16 每个候选都记录
  forward/backward KL error 并择优，step 18 `ALL CHECKS PASSED` 后保存 `/app/dist.npy`，
  step 19 从磁盘重读并复算（error `0.00e+00` / `2.87e-11`）。
- **成功但不需要技能**：git-leak-recovery 三次分别读了 seg00 / db-wal seg00 / 0004，都是
  无关或通用卡片；真正起作用的是 `git reflog`（a00 step 1）、`git show 6b4c75c`（a01 step 3）、
  `git reflog expire --expire=now --all; git gc --prune=now`（a00 step 3）这些模型自带的 git 知识。
  constraints-scheduling 三次只读了 catalog/根 SKILL.md（a00/a02）或 path-tracing seg00（a01），
  靠自己的 ICS 解析与约束求解在 step 14–17 写出 `/app/meeting_scheduled.ics`。

### `skill_read_not_followed`（0）与 `environment_or_infra`（0 primary）

- 没有一例是“技能读了、明确没照做”。chess 最接近，但它执行了 seg01 中适用的图像刻画步骤，
  缺的是卡片本身没有的棋子识别能力，因此归 `skill_followed_still_failed`。
- `environment_or_infra` 没有 primary，但 distribution-search a02 与 feal-linear a01 的
  长时间无响应终端（100+ 步相同输出、Ctrl-C 无效）作为 secondary 记录；这不是模型推理错误，
  但卡死由模型自己发起的长时间前台命令触发，所以没有把整题判成纯环境失败。

## 三、技能库缺失的能力（`skill_absent`）

1. 从 C 模拟器规格合成 `<32,000` 行布尔门电路（circuit-fibsqrt）。
2. FEAL 类 4 轮 Feistel 的差分密码分析，恢复 key[5]（feal-differential）。
3. FEAL 类密码的线性密码分析，恢复 4 个 round key（feal-linear；`bank/merge/0003` 只覆盖
   数据清点/验证）。
4. git reflog / 悬空 object 的密钥恢复与历史清理（git-leak-recovery）。
5. sshd + git bare repo + nginx 多分支 HTTPS 部署钩子（git-multibranch）。
6. ICS 日历冲突解析与约束求解（constraints-scheduling）。
7. R 语言的自适应拒绝采样器实现与随机性检验（adaptive-rejection-sampler）。
8. 7z2john + john 的口令破解流程（crack-7z-hash；`bank/merge/0006` 只能帮装工具）。
9. 棋盘图像识别与一步杀搜索（chess-best-move；`path-tracing seg01` 只做图像刻画）。

## 四、覆盖表（17 条技能对 13 题的语义适用性）

分档计数：`clear_match` 5 题、`marginal` 3 题、`none` 5 题。
按 trial 计：clear_match 15 个、marginal 9 个、none 15 个。

| task | coverage | matched_skill |
|---|---|---|
| adaptive-rejection-sampler | marginal | `traj/tb:cancel-async-tasks/strong/00/seg00/skill` |
| chess-best-move | clear_match | `traj/tb:path-tracing/strong/00/seg01/skill` |
| circuit-fibsqrt | none | — |
| code-from-image | clear_match | `traj/tb:path-tracing/strong/00/seg01/skill` |
| configure-git-webserver | clear_match | `bank/merge/0006` |
| constraints-scheduling | none | — |
| crack-7z-hash | marginal | `bank/merge/0006` |
| distribution-search | clear_match | `bank/merge/0001` |
| feal-differential-cryptanalysis | none | — |
| feal-linear-cryptanalysis | marginal | `bank/merge/0003` |
| fix-ocaml-gc | clear_match | `bank/merge/0006` |
| git-leak-recovery | none | — |
| git-multibranch | none | — |

逐行理由见 `out_group1_coverage.jsonl`。

两个关键计数：

- **有明确对应但没读**：4 个 trial——configure-git-webserver a02（读 0000，未读 0006）、
  fix-ocaml-gc a02（读 seg00，未读 0006）、distribution-search a01（读 0004 提交门，未读 0001）、
  distribution-search a02（读 seg00，未读 0001）。其中 3 个仍然成功（configure a02、fix-ocaml a02、
  distribution a01），唯一失败的 distribution a02 的直接死因是终端卡死而不是缺技能。
  若把通用的 `bank/merge/0004` 提交前门也算作 distribution 的“对应技能”，则严格未读的只剩 3 个。
- **根本没有对应**：15 个 trial（circuit 3、constraints 3、feal-diff 3、git-leak 3、git-multibranch 3）。
  其中 circuit/feal-diff 全败，constraints/git-leak 全胜，git-multibranch 1 败 2 胜——说明这些题
  的成败主要由模型自身能力与执行纪律决定，技能库帮不上也拖不累。

## 五、翻转题：adaptive-rejection-sampler

control 臂三次全败（`control.jsonl` 0/3），with_bank 臂 a00 成功（1/3）。a00 是这组里唯一
with_bank 成功而 control 全败的题。

**那次成功有没有真的读取并执行某条技能？结论：没有；这次提升与技能无关。**

证据：

1. a00 只打开过两张卡：step 1 的 `bank/merge/0004`（通用提交前门）和 step 84 的
   `traj/tb:llm-inference-batching-scheduler/strong/00/seg00/skill`（优化任务清点/基线）。
   库里没有任何 ARS/统计采样器技能；0000 是 OCR，0002 是拟合贝叶斯模型，均不适用。
2. 0004 没有被当作提交前门执行：它的特征步骤（`sha256sum`、在完成提示前对最终路径做
   read-back、`mark_task_complete`）在 a00 里都没有出现；最后 12 步（268–279）是反复
   `Rscript /app/ars.R | tee/tail` 调试，trial 在 step 279 直接结束，**没有 completion 调用**。
3. 第 84 步读 llm-inference seg00 之后，第 85 步只是 `pwd; ls -la /app/; cat -n /app/ars.R`
   看文件，不是该卡的“记录可复现基线”流程。
4. 真正让它通过的是模型自己的实现：step 3 `apt-get install r-base-core`，step 4 写出
   `/app/ars.R`，step 82 的 `eval_raw_density` 先试 `fun(x, log="TRUE")` 再试裸 `fun(x)`，
   因而接住了 verifier 传入的裸 `dnorm`；最终 `normal_samples.txt`/`exponential_samples.txt`
   的均值/方差落在 verifier 的宽松阈值内。
5. 这个“成功”本身是阈值/结构性的：a00 的自测在 step 273 打印 `BASIC_NORMAL: FAIL`、
   step 279 仍打印 `COMPARE_BETA: FAIL`，而 verifier 的 `test_formal_testing_with_known_truth`
   只要求输出里出现 `: PASS` 或 `: FAIL`，
   `test_sample_files_generated` 只要求 mean/std 在粗范围内。换句话说，pass 不等于 ARS 采样
   统计正确。

对照另外两次失败，也没有技能参与：a01（step 284）把输入当 log-密度、从不测裸 `dnorm`；
a02（step 58/60）把 `test()` 放在 `/app/test.R` 而 `ars.R` 里没有。两者都是接口/打包问题，
不是技能内容问题。

## 六、数据问题与限制

1. 39/39 trial 文件都在，13 题各 3 次；没有缺文件或空 transcript。步数范围 6（constraints a01）
   到 305（adaptive a01）。
2. **11 个 trial 没有 `mark_task_complete`**：adaptive a00/a01、circuit a00–a02、distribution a02、
   feal-diff a00–a02、feal-linear a00/a01。其中 adaptive a00 与 feal-linear a00 仍被判 correct，
   说明 verifier 只看工作区最终状态；因此不能把“没提交”等同于“没产出”，反之亦然。
3. trial JSON 的中段 observation 被裁剪（带 `[... N characters omitted ...]`），最后 6 步完整。
   大多数失败点都能在最后 6 步或 action 文本里直接看到；被截断的中段只用于补充上下文。
4. `index.jsonl` 的 `n_skill_reads` 把 catalog 与根目录 `SKILL.md` 都算作技能读取；例如
   adaptive a02 记为 5，但实际没有打开 17 条中的任何一条（step 2 还 cat 了一个不存在的
   `/opt/openclaw-skills/traj-tb-path-tracing-.../SKILL.md`）。本分析按 `skill_reads[].action`
   的完整路径逐条核对。
5. trial JSON 没有 verifier 的逐项断言输出。git-multibranch a00 的失败点只能从最终工作区
   （step 35 删除 `/var/www/deploy/{,dev/}index.html` 后直接完成，且 step 32 已出现 `/dev/` 404）
   推断；adaptive a01 的失败点按 verifier 的裸 `dnorm` 调用与 step 284 的实现推断。两行都在
   evidence 里写明了推理链。
6. 两个终端卡死案例（distribution a02 step 11–234、feal-linear a01 step 86–215）在 transcript
   里表现为上百步完全相同的 observation，无法从数据判断按键是否到达进程，只能确认 agent
   没有恢复 shell。这既是模型发起的长前台命令问题，也暴露了 harness 对 Ctrl-C 的处理缺陷，
   因此在 secondary 里保留 `environment_or_infra`。
