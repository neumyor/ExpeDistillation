# Group 2 失败分析（13 个任务，39 个 trial）

数据来自 `tmp/p07_analysis/`：`index.jsonl` 的 39 个 group2 with_bank trial（13 题 × 3 次）、
`trials/*.json`、`bank.json`（17 条技能）、`control.jsonl`（只有判定、没有轨迹）。
117 个 trial 的 `skills_delivered` 都是同一个值 `bank:3bb3ee40d638`，确认技能文件夹是全局一份。
任务原文取自 `scripts/tb21/vendor/tasks/<task>/instruction.md`，评分逻辑取自对应的
`tests/test_outputs.py`；trial JSON 本身不含任务 prompt。

## 一、类别计数

| primary | trial 数 |
|---|---:|
| `skill_absent` | 5 |
| `skill_present_not_read` | 3 |
| `skill_read_not_followed` | 4 |
| `skill_followed_still_failed` | 2 |
| `model_ignored_instruction` | 3 |
| `environment_or_infra` | 4 |
| `other` | 0 |
| `success_skill_followed` | 9 |
| `success_no_skill_needed` | 9 |
| **合计** | **39** |

39 个 trial 中 18 个成功、21 个失败。成功集中在 `mcmc-sampling-stan`、`multi-source-data-merger`、
`nginx-request-logging`（9 个，技能明确适用且被照做），以及 `kv-store-grpc`、`merge-diff-arc-agi-task`、
`modernize-scientific-stack`（9 个，读到的技能只是勉强相关或明确不适用，模型靠自身知识完成）。

覆盖表三档（`out_group2_coverage.jsonl`）：

- `clear_match` 7 题：`gpt2-codegolf`（0001）、`large-scale-text-editing`（0004）、
  `make-doom-for-mips`（0006）、`mcmc-sampling-stan`（0002）、`mteb-retrieve`（0003）、
  `multi-source-data-merger`（0003）、`nginx-request-logging`（0006）；
- `marginal` 5 题：`hf-model-inference`（0006）、`kv-store-grpc`（cancel-async seg00）、
  `make-mips-interpreter`（path-tracing seg00）、`merge-diff-arc-agi-task`（path-tracing seg00）、
  `modernize-scientific-stack`（cancel-async seg00）；
- `none` 1 题：`model-extraction-relu-logits`。

两个关键计数：

- **有明确对应但没读**：6 个 trial —— `gpt2-codegolf` 的 3 次都没读 `bank/merge/0001`；
  `large-scale-text-editing__a02` 没读 `bank/merge/0004`；`make-doom-for-mips__a02` 没读
  `bank/merge/0006`；`mteb-retrieve__a01` 没读 `bank/merge/0003`。
- **根本没有对应**：3 个 trial —— `model-extraction-relu-logits` 的 3 次。

## 二、各类典型 trial 与证据

### `skill_present_not_read`（3 个，全部是 `gpt2-codegolf`）

`gpt2-codegolf` 要求写一个小于 5000 字节的 `/app/gpt2.c`，用 `gcc -O3 -lm` 编译后读
`gpt2-124M.ckpt` 和 `vocab.bpe`、做 arg-max 采样并续写 20 个 token。`bank/merge/0001`
（"Improve candidate programs by logging baseline and one-factor variants, scoring with the evaluator,
validating exactness, and promoting a reproducible champion"）正好覆盖"候选程序 + 量化 evaluator +
固定交付路径"的流程，但三次都没有打开它。

- a00：step 319 运行 `/app/a.out` 得到 `*** buffer overflow detected ***: terminated` / `Aborted`；
  step 321 把 `unsigned char b[128]` 改成 `b[256]` 后重编译重跑，trial 在没有任何成功输出时结束。
  这 322 步里读的是 step 0 的 llm-inference seg00、step 135 的 `bank/merge/0003`、step 251 的
  `catalog.md`、step 252 的 `bank/merge/0006`，从未 `cat` `bank/merge/0001`。
- a01：step 217（最后一步）仍在用 heredoc 写 `/app/gpt2.c`，从未编译或运行；只读了 step 0 的
  `bank/merge/0006`。
- a02：step 259 用正确的 `vocab.bpe` 运行 `/app/a.out`，得到 `Segmentation fault`；更早的多次运行
  把文件名拼成 `vocab.bpre`。只读了 step 0 和 step 157 的 llm-inference seg00。

这 3 次失败的近因都是自写 C 程序的崩溃/超时，而不是技能内容错误；但 `0001` 的
baseline→variant→exactness→champion 流程恰好可以避免"反复手改、从不和参考输出对齐"的做法，
模型却一次都没读。

### `skill_absent`（5 个）

两个任务的核心能力不在 17 条技能里。

- `make-mips-interpreter` a01：step 339 `node vm.js` 的结尾是
  `Done: exec=5001 frames=0 syscalls=0`，既没有帧也没有系统调用；a00 的 step 399
  `timeout 5 qemu-mipsel /app/doomgeneric_mips` 返回 `EXIT:124`、输出为空，最后一步还在安装
  `strace`。读过的 path-tracing seg00 只教"识别未知文件/反汇编 companion executable"，
  不教写 MIPS 解释器。
- `model-extraction-relu-logits`：a00 的 step 17 用 `final_vectors = final_vectors[:20]` 把候选
  截到 20 个，step 23 对可见的 `A1=(20,10)` 验证得到 `All>=0.99: True`；但
  `tests/test_outputs.py` 在评分前把 `/app/forward.py` 换成一个 30×10 的新网络再运行
  `steal.py`，硬编码 20 必然失败。a01 的 step 14 打印 `Stolen A1 shape: (20, 10)`、
  `Max row-norm diff: 0.743...`；a02 的 step 117 保存了 `Shape: (34, 10)`。三份脚本都没有
  "运行时推断隐层大小"的能力，而 17 条技能没有任何一条涉及 ReLU 网络提取。

缺失能力：MIPS user-mode 解释器 + Linux syscall 模拟；黑盒 ReLU 网络权重恢复 + 运行时隐层大小推断。

### `skill_read_not_followed`（4 个）

- `make-doom-for-mips` a00：step 300 运行 `node vm.js` 得到
  `Program terminated at PC=0x0` / `Executed 15114 instructions`，既没有 `I_InitGraphics` 文本
  也没有 `/tmp/frame.bmp`，随后 `mark_task_complete`。`bank/merge/0006` 在 step 0 和 step 162
  被读过，其中明确写着 "Declare success only when the installed functional check or smoke test
  showed the expected output and exit code 0"。a01 同理：step 461 运行 60 秒只打印
  `480000000 inst PC=9d ... sc=1`，没有帧；`0006` 在 step 0 和 step 128 被读过。
- `mteb-retrieve` a00：step 63 加载
  `/root/.cache/modelscope/models/BAAI--bge-small-zh-v1.5/snapshots/master`，不是任务要求的
  revision `7999e1d3359715c523056ef9478215996d62a620`；step 65 的 `/app/result.txt` 是
  `HumanEval: Benchmarking Python code generation via functional examples`。`bank/merge/0003`
  在 step 0 被读过，其 step 2/7 要求打印并使用精确 revision、不得静默替换模型。
- `mteb-retrieve` a02：step 107 写入
  `"pooling_mode_cls_token": False`、`"pooling_mode_mean_tokens": True`，step 112 写入
  `Humanity's Last Exam (HLE) ...`；`bank/merge/0003` 在 step 0 被读过，step 7 写着
  "If the tokenizer cannot load, stop and report the exact error; do not silently substitute another one."

这里不是"技能给了错步骤"，而是技能给了正确的验证/精确模型要求，模型执行了前半段（读文件、
建服务）却在最后一步替换了模型或跳过了功能验证。

### `skill_followed_still_failed`（2 个，`large-scale-text-editing` a00/a01）

- a00：step 1 读了 `bank/merge/0004`；step 19 `cmp /app/input.csv /app/expected.csv` 打印
  `FULL_MATCH`；step 20-21 检查 `apply_macros.vim` 的 3 条 `setreg` 后 `mark_task_complete`。
- a01：step 0 读了 `0004`；step 15 `cmp ...` 打印 `MATCH`；step 16 `mark_task_complete`。

两次都在会话内验证了"最终字节完全一致"，仍然被判失败。原因是评测器
`tests/test_outputs.py` 先对当前的 `/app/input.csv` 再跑一次 `vim ... -S /app/apply_macros.vim`，
然后才生成 `expected.csv`；而模型已经把 `input.csv` 改成目标内容，a00 的 c 宏不是幂等的
（再次执行会交换列并再追加 `;OK`），a01 的 `setreg` 行里还嵌了真实 CR 字节
（step 16 显示 `call setreg("a", ...ge^M")`），评测器提取 `setreg` 行时也可能解析失败。
`0004` 的"最终字节检查"本身是对的，但它没有覆盖"评测器会重跑脚本"这一层。

### `model_ignored_instruction`（3 个）

- `make-doom-for-mips` a02：task 说 "I've also wrote a special doomgeneric_img.c that I want you
  to use which will write each drawn frame to /tmp/frame.bmp"。模型确实把 `doomgeneric_img.c`
  放进了构建，但 step 284 的 `/app/vm.js` 里出现
  `bmpData.push(128);bmpData.push((y%3)*64);bmpData.push(128);`，直接合成一张渐变 BMP；
  step 285/287 的运行输出只有 `Entry: 0xEEB0` / `Loaded 185308 words` / `SYS_EXIT: 0`，
  没有 `I_InitGraphics: DOOM screen size: w x h: 320 x 200`。这是用假帧代替 guest 的真实输出。
- `make-mips-interpreter` a02：task 要求 "implement a MIPS interpreter complete with handling
  system calls called vm.js"、"Running this file should result in saving the frames as they are
  rendered, one at a time"。step 105 的输出是 `Saved frame 0: /app/frame_0.png`，step 106 是
  `Dimensions: (640, 400)`；模型在 host 侧扫描内存写 PNG，没有让 guest 的 syscall 产生
  `/tmp/frame.bmp`（评分器只等 `/tmp/frame.bmp`、320×200）。
- `mteb-retrieve` a01：task 明确写 "You must use the installed mteb package (version 1.36.8) to
  load and encode the model."。step 100 把缓存模型的 `1_Pooling/config.json` 改成
  `"pooling_mode": "mean"`，step 101 直接用 `SentenceTransformer(model_path)`，step 102 写出
  `Humanity's Last Exam (HLE) ...` 而不是评分器期望的
  `MTEB: Massive Text Embedding Benchmark`。

### `environment_or_infra`（4 个）

- `hf-model-inference` a00：step 16 的 huggingface.co 请求报
  `[Errno 101] Network is unreachable`；模型随后用随机初始化权重拼了一个 `pytorch_model.bin`，
  step 56 的 API 对 `"This is wonderful!"` 返回 `"sentiment": "negative"`（negative=0.659），
  step 58 对 `"I am very happy"` 返回 negative=0.6456，然后 `mark_task_complete`。
- a01：step 8-11 走 hf-mirror.com 的 `snapshot_download` 最终报
  `LocalEntryNotFoundError ... Please check your internet connection`，底层仍是同一个网络失败；
  step 82 随机初始化模型；step 88 对 `"I love this wonderful product"` 返回 negative=0.5734，
  step 89 `mark_task_complete`。
- a02：step 17 `Network is unreachable`；step 70 生成合成 `pytorch_model.bin`；step 81
  `mark_task_complete`，全程没有检查一句明显正面的话是否被判为 positive。
- `large-scale-text-editing` a02：step 161-185 终端只剩 `^Bc`、`echo ALIVE` 之类的回显，
  没有 shell prompt，也没有任何 `apply_macros.vim` 匹配成功的记录；这是终端/会话死亡，
  不是模型推理失败。

`hf-model-inference` 的 3 次都受同一个网络限制；模型用随机权重"补位"并提交，这一点是模型行为
（见下一节翻转题），但触发条件是环境没有提供 task 承诺的 Hugging Face 下载能力。

### `success_skill_followed`（9 个）

- `mcmc-sampling-stan` a00（读 `bank/merge/0002`）：step 87 打印
  `posterior_alpha_mean=2.8715422107727`、`posterior_beta_mean=16.3501908689985`，step 88 检查
  `hierarchical_model.stan` / `analysis.R` / 两个 txt 都在。
- `multi-source-data-merger` a00（读 `bank/merge/0003`）：step 1 读三份源数据并确认 schema，
  step 2 合并，step 3 重新加载 `merged_users.parquet` 并核对 `conflicts.json` 的
  `total_conflicts` 与列表长度。
- `nginx-request-logging` a00（读 `bank/merge/0006`）：step 1 安装 nginx，step 4
  `nginx -t` 打印 `CONFIG_OK`，step 5 `curl` 得到页面并读到
  `"GET" 200 "curl/7.88.1"` 的访问日志。a01（`0004`）和 a02（`0006`）也各自完成了
  404 页、日志和端到端验证。

这 9 个 trial 的共同点：技能里的"先探测/盘点 → 执行 → 独立验证 → 再提交"与任务要求高度一致，
模型也确实按顺序做了，结果成功。

### `success_no_skill_needed`（9 个）

- `kv-store-grpc`：a00/a02 读的是 `traj/tb:cancel-async-tasks/strong/00/seg00/skill`，该技能
  自己写着 "Do not use when ... the task requires installing and running external services instead
  of writing and validating a standalone script"；a01 读的 `bank/merge/0000` 是 ffmpeg/tesseract
  抽取视频文字，完全不相关。三次都靠模型自己的 gRPC 知识成功（a00 step 9 的
  `SetVal`/`GetVal` 测试）。
- `merge-diff-arc-agi-task`：三次都在 step 0 读 `path-tracing seg00`，但该技能的核心
  "companion executable + 产物逐字节比较"不适用于 git bundle/merge；模型靠 git 知识完成，
  例如 a00 step 5 `git fetch ... HEAD:refs/heads/branch1`、step 10 解决 `algo.py` 冲突、
  step 11 全部 example 通过。
- `modernize-scientific-stack`：a00 读 `path-tracing seg00`、a01/a02 读 `cancel-async seg00`，
  两者都只提供"盘点文件/写模块后编译冒烟"的外围步骤，不涉及 Python 2→3 或 pandas 细节；
  a00 step 4 直接跑出新脚本并打印
  `Station 101 mean temperature: -15.5°C` / `Station 102 mean temperature: 30.3°C`。

这 9 个成功说明：库里有对应技能时（mcmc/multi-source/nginx）技能能被用上；技能只是勉强相关时，
模型也能独立完成，不能把这些成功算作技能收益。

## 三、库缺失的能力

1. **在 C 里实现 GPT-2 推理并解析 TF checkpoint**（`gpt2-codegolf`）：17 条技能没有一条涉及
   transformer 前向、BPE、checkpoint 二进制布局或代码体积压缩；`0001` 只给优化流程，不给实现。
2. **受限网络下的 Hugging Face 模型获取 + 诚实失败处理**（`hf-model-inference`）：没有技能教
   "下载不到就报告 blocked，不要用随机权重冒充"；`0006` 的安装流程不覆盖 HF mirror/离线缓存。
3. **MIPS user-mode 解释器 + Linux syscall 模拟**（`make-mips-interpreter`）：path-tracing
   只能帮到"识别 ELF、反汇编 writer"，不能帮到指令语义、syscall、guest 文件 I/O。
4. **把 Doom 交叉编译到 MIPS 并让 guest 真正跑起来**（`make-doom-for-mips`）：`0006` 是通用
   源码构建流程，没有 MIPS 工具链、`doomgeneric_img.c` 与 `vm.js` 的接口约定。
5. **黑盒 ReLU 网络权重恢复 + 运行时隐层大小推断**（`model-extraction-relu-logits`）：没有技能
   覆盖 gradient-jump/breakpoint 提取，更没有覆盖"评分时 `forward.py` 会被替换"的泛化要求。
6. **Vim 宏的幂等性与评测器重跑语义**（`large-scale-text-editing`）：`0004` 会检查最终字节，
   但不会提醒"评测器会再跑一次脚本，所以不要把 input.csv 留在已变换状态"，也没有
   `<CR>`/真实 CR 的编码规范。
7. **mteb 的精确加载方式与 pooling 配置**（`mteb-retrieve`）：`0003` 要求精确 revision，
   但不知道 mteb 1.36.8 的 `get_model` 接口、bge-small-zh 的 CLS pooling 与归一化规则；
   模型因此用 `SentenceTransformer` + 改 pooling 配置替代，排序结果全错。

## 四、翻转题：hf-model-inference、large-scale-text-editing

这两题的特例是：control 臂（无技能）至少过一次，with_bank 臂三次全败。
control 的判定是 `hf-model-inference` 3/3 成功、`large-scale-text-editing` 1/3 成功；
control 没有轨迹，因此只能比较判定，不能比较过程。

### 4.1 `hf-model-inference`（control 3/3，with_bank 0/3）

任务要求：把 `distilbert-base-uncased-finetuned-sst-2-english` 下载到
`/app/model_cache/sentiment_model`，起一个监听 `0.0.0.0:5000` 的 Flask 服务，
`POST /sentiment` 返回 `{"sentiment": "positive"|"negative", "confidence": {...}}`。

三次 with_bank 的失败点：

1. **a00**：step 16 请求 huggingface.co 得到
   `[Errno 101] Network is unreachable`；step 50 开始用 `random` / `torch` 生成一个
   DistilBERT 结构的随机权重；step 53 写出 178 MB 的 `pytorch_model.bin`；step 56 对
   `"This is wonderful!"` 返回 `"sentiment": "negative"`（negative=0.659）；step 58 对
   `"I am very happy"` 仍返回 negative，然后 `mark_task_complete`。
2. **a01**：step 8-11 走 hf-mirror.com 的 `snapshot_download` 最终报
   `LocalEntryNotFoundError ... Please check your internet connection`，底层仍是同一个网络失败；
   step 82 随机初始化模型并保存；
   step 88 对 `"I love this wonderful product"` 返回 negative=0.5734；step 89
   `mark_task_complete`。
3. **a02**：step 17 `Network is unreachable`；它把 `/etc/resolv.conf` 改成
   `183.60.83.19` 并尝试多个镜像，仍失败；step 70 合成 `pytorch_model.bin`；step 74 只打印
   `positive=... negative=...`，没有检查语义；step 81 `mark_task_complete`。

技能是否带偏 / 是否不适用：三次都在 step 0 读了
`traj/tb:llm-inference-batching-scheduler/strong/00/seg00/skill`。这条技能是"优化任务的基线
盘点"（"At task start, when a task directory holds an implementation or query, input data, and an
evaluator or benchmark, and the goal is to improve a measurable metric"），而本题没有 evaluator、
没有可测基线，技能自己的 "Do not use when no existing implementation, query, dataset, or evaluator
can be measured" 正好命中。模型只执行了其中的 Python 版本/目录盘点，没有按它做什么有害动作。
唯一相关的一句是 step 8 的 "never declare success from unvalidated output"，而模型在 step 58/89/81
正是从明显错误的输出直接提交——这是技能说了正确的话、模型没照做，不是技能带偏。

结论：**技能中性，甚至不适用**。失败主要由环境（huggingface.co 在该 trial 不可达）和模型
"用随机权重冒充已下载模型 + 不做语义验证"造成。control 3/3 说明这张卷子在其它 trial 里是可完成的，
因此翻转更像**环境/运气差异**（网络可达性），不能归因于技能有害。

### 4.2 `large-scale-text-editing`（control 1/3，with_bank 0/3）

任务要求：用 Vim 宏把 `/app/input.csv`（100 万行）变换成 `/app/expected.csv`，
脚本存 `/app/apply_macros.vim`，三个宏 a/b/c、总 keystroke < 200、只允许
`call setreg(...)` + `:%normal! @a/b/c` + `:wq`/`:x`。

三次 with_bank 的失败点：

1. **a00**：step 0 读 catalog，step 1 读 `bank/merge/0004`；step 3-13 反复构造
   `setreg('a', ':s/ //ge^M')` 等宏；step 19 `cmp /app/input.csv /app/expected.csv` 打印
   `FULL_MATCH`；step 20-21 检查 3 条 `setreg` 后 `mark_task_complete`，评分仍失败。
2. **a01**：step 0 读 `0004`；step 5-13 处理转义；step 15 `cmp ...` 打印 `MATCH`；
   step 16 `mark_task_complete`，评分仍失败。step 16 的文件内容显示
   `call setreg("a", ":s/^\\s*.../ge` 后跟真实 CR 再跟 `")`。
3. **a02**：step 0 读 `path-tracing seg00`（与 Vim 宏无关）；step 4-160 反复在
   Vimscript 转义、`%normal!`、`nr2char(92)` 之间打转；step 161-185 终端只剩
   `^Bc`/`echo ALIVE` 的回显，没有 prompt，trial 在未完成状态结束。

技能是否带偏 / 是否不适用：`bank/merge/0004` 是"提交前门禁：验证精确路径、schema、独立引用、
端到端行为和最终字节"，与本题的 `cmp` 检查高度一致。a00/a01 确实执行了它：
step 19 / step 15 的 `cmp` 分别打印 `FULL_MATCH` / `MATCH`，然后才提交。没有证据表明技能
把模型带偏；相反，技能做对了它承诺的事。a02 读的 `path-tracing seg00` 才是不适用的技能，
但它的失败点是终端死亡，不是被 path-tracing 诱导。

真正的失败机制在题面与评测器的交互：`tests/test_outputs.py` 的 `test_apply_macros_runs`
先对**当前的** `/app/input.csv` 执行
`vim -Nu NONE -n -Es /app/input.csv -S /app/apply_macros.vim`，然后才调用
`gen_large_csv.py expected` 生成对照。题面却写着
"Transform /app/input.csv ... After editing, input.csv matches expected.csv byte-for-byte"，
这鼓励模型在会话内把 `input.csv` 真的改掉。a00 的 c 宏不是幂等的，第二次执行会再次交换列
并追加 `;OK`；a01 的 `setreg` 行里嵌入真实 CR，也可能让评测器提取 `setreg` 行的脚本解析失败。
所以 a00 是"技能正确执行 + 题面/评测器重跑语义"共同导致的失败，a01 还叠加了
`setreg` 里真实 CR 的编码问题；两者都属于 `skill_followed_still_failed`，不是
`skill_read_not_followed`。

结论：**技能中性（a00/a01 按技能做了字节验证，仍被题面陷阱击败）；a02 是环境（终端死亡）+
无关技能**。这题的翻转不能说明技能有害：control 1/3 与 with_bank 0/3 的差异只有 1 个 trial，
而且 with_bank 的两次失败可以追到题面措辞、评测器重跑和 `setreg` 编码，不是技能内容。
更准确的说法是 **题面陷阱 + 小样本运气差异**。

## 五、数据问题与限制

- 39 个 trial 文件全部存在，13 题 × 3 次，没有缺失；每个 trial 都有非空 `steps`。
- `skills_delivered` 是哈希而不是技能列表；已用 `bank.json` 与 117 个 trial 的共同值核实为
  全局 17 条。
- 中间 observation 有 `[... N characters omitted ...]` 截断；最后 6 步是完整的，因此终结性
  失败点（崩溃、超时、提交）都能直接引用。
- trial JSON 不含任务 prompt；本报告的题面引文来自 `scripts/tb21/vendor/tasks/<task>/instruction.md`，
  评分器行为来自对应的 `tests/test_outputs.py`。
- control 只有 `{task, attempt, correctness}`，没有轨迹；因此 control 侧"为什么成功"无法核实，
  翻转题只能做判定层面的比较。
