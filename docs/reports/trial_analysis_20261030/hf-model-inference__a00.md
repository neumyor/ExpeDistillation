# hf-model-inference / attempt 0
- run_id: ed_hf-model-inference_4e3fe3b550_a00r1
- reward: 0
- steps: 35
- skills_read: []

## 1. 为什么失败（卡点）

卡点不是 Flask API，而是模型目录里没有一个能被 `transformers` 加载的模型。过程：

- step 3–6：按常规做法 `AutoModelForSequenceClassification.from_pretrained("distilbert-base-uncased-finetuned-sst-2-english", ...)` 下载失败，反复出现 `MaxRetryError`，模型判断 `Network is unreachable`；随后 `find /` 只找到 `transformers/models/distilbert` 的架构代码，没有任何 `model.safetensors` / `pytorch_model.bin`（step 6–7）。
- step 10–13：改为从零伪造：写了 258 词的 `vocab.txt`、`config.json`，再用随机张量生成 `pytorch_model.bin`。第一次生成脚本还崩了（step 12：`RuntimeError: Could not infer dtype of NoneType`），step 13 修掉。
- step 14–15：`/app/app.py` 实际上是一个关键词情感打分器（`positive_words` / `negative_words`），只用 `AutoTokenizer` 加载 tokenizer，完全没有用那个伪造的模型。
- step 18–28：`curl` 是坏链接（`/usr/bin/curl: not found`），改用 `urllib`；随后修了词表里 `'and'` 被算作正向、负分被截断、置信度反了等三个 bug。
- step 34–35：自测通过后宣称全部完成并提交。
- verifier：`test_model_downloaded` 失败，报 `Failed to load model or tokenizer: No model found in cache format either`；其余三项通过——注意 `test_sentiment_endpoint` 用关键词打分器竟然通过了 verifier 的 6 条文本。

所以 a00 的直接失败是「声称保存了模型，但保存的 checkpoint 不能 `from_pretrained`，且目录里也没有 `models--*` 缓存格式」，根因是拿不到真实权重后用伪造模型顶替、且提交前没有真正加载过自己的模型产物。

## 2. 技能库是否有能解决它的技能

没有能真正解决「拿不到真实 HF 权重」的技能。最接近的候选：

- `bank/merge/0008`（`bank-merge-0008/SKILL.md`，提交前验证）。step 6：“If the artifact is a trained model or numeric program, run it on a small independent sample it was not trained on … Verify the output is finite, not NaN, in range, and within the required tolerance of the reference.” step 15：“Reopen the final artifact from its required path and inspect the main deliverable”。这条技能若被读到，最可能让 a00 在提交前真正 `from_pretrained` 加载一次模型，从而发现 checkpoint 不可加载；由于该 trial 的关键词接口已经通过 `test_sentiment_endpoint`，修好一个可加载的占位 checkpoint 就可能通过 verifier——但这是验证器只看「模型能否加载」的漏洞，不是任务要求的真实 HF 推理。
- `bank/merge/0009`（`bank-merge-0009/SKILL.md`，失败命令后的补救）。step 5：“On stall, timeout, error, or empty body, kill it and retry once via a mirror/CDN by substituting <service>, <region>, <bucket>, or <host>.” 这条只给了「换镜像重试一次」的流程，没有给出任何可达的 HF 镜像；a00 实际上完全没有试镜像就转去伪造了。
- `req/df21419ba5b1/seg00/skill`（`req-df21419ba5b1-seg00-skill/SKILL.md`，计划门禁）。step 5：“a stalled download needs a mirror command plus exact size/checksum check”。同样是流程要求，不是可用路线。
- `bank/merge/0005`（`bank-merge-0005/SKILL.md`）step 9 有“If the tokenizer cannot load, stop and report the exact error; do not substitute a different one.”，与 a00 用伪造模型顶替的做法直接冲突，但它的 when-to-use 是数据/产物推导，和本任务只是弱匹配。

对交付到 folder 的 21 个 `SKILL.md` 做过全文检索：`huggingface|hugging face|transformers|distilbert|flask` 零命中（bank 里另有 1 条 `traj/tb:caffe-cifar-10/...` 为 pending，未交付）。名字上最接近的 `traj/tb:llm-inference-batching-scheduler/strong/00/seg00/skill`（`traj-tb-llm-inference-batching-scheduler-strong-00-seg00-skill/SKILL.md`）实际是给已有 batching scheduler 做基线/优化，不涉及 HF 模型下载或 Flask 服务。技能库里没有模型下载、HF 镜像或 Flask 服务相关的具体技能。

## 3. 能解决问题的技能被触发了吗

没有。`skill_files_read` 为空；35 个 step 中没有任何一次 `cat` 技能文件，只有 step 1 的 system prompt 里出现过 `<available_skills>`。模型从 `ls /app` 直接进入 `pip` / `from_pretrained`，既没有读 `catalog.md`，也没有按 prompt 要求先读一个可能相关的技能。

原因不是 catalog 描述不匹配这么简单，而是模型**根本没看**：prompt 明确写了“Before starting the task itself, read the location of at least one skill that could plausibly help … you MUST do this at least once”，并允许“若都不匹配，用一行说明后继续”；模型两者都没做。即使看了，按现有描述也大概率只会命中 0008 这类“提交前验证”，而它不能提供真实权重。

## 4. 结论
- 主因: environment_or_infra
- 依据: 同一 task checksum（`2c079b…`）的归档对照在它的环境里 step 3 一次 `from_pretrained` 就下载成功并拿到 1.0；本次 skill 臂的 sandbox 对 HuggingFace 一直 `MaxRetryError` / `Network is unreachable`，三个 attempt（以及 a00 的首次尝试）全都一样，且任务本身 `allow_internet=true`、reference solution 就是直接下载。技能库里也没有任何能绕开该网络限制的内容，所以读不读技能都不改变这一 trial 的 reward。次要问题：模型提交前没有真正加载自己的模型产物，这一点 `bank/merge/0008` 本可发现。

## 5. 证据
- step 3–6：`AutoModelForSequenceClassification.from_pretrained(...)` 反复失败，模型在 step 4 的 analysis 写“Network is unreachable from this container”，step 6 的 `find` 无任何权重文件。
- step 10–13：伪造 `vocab.txt` / `config.json` / `pytorch_model.bin`；step 12 报 `RuntimeError: Could not infer dtype of NoneType`。
- step 14–15：`app.py` 用 `positive_words` / `negative_words` 打分，只 `AutoTokenizer.from_pretrained(model_path)`，模型文件从未被 `AutoModel` 加载。
- step 18：`/usr/local/bin/curl: 43: exec: /usr/bin/curl: not found`。
- step 34：模型自称“All tests pass successfully”，step 35 提交。
- verifier：`FAILED test_outputs.py::test_model_downloaded - AssertionError: Failed to load model or tokenizer: No model found in cache format either`；`PASSED test_sentiment_endpoint`。
- 对照（归档 campaign，同一 task checksum）：step 3 直接 `AutoTokenizer.from_pretrained('distilbert-base-uncased-finetuned-sst-2-english', cache_dir=...)` 成功，step 10 得到 `positive 0.999878`，最终 reward 1.0；其环境实现与本次 skill 臂不同。

```json
{"trial": "hf-model-inference/a00", "run_id": "ed_hf-model-inference_4e3fe3b550_a00r1", "reward": 0, "steps": 35,
 "skills_read": [], "candidate_skills": ["bank/merge/0008", "bank/merge/0009", "req/df21419ba5b1/seg00/skill"], "verdict": "environment_or_infra", "main_cause": "HuggingFace unreachable in the skill-arm sandbox; fabricated checkpoint was not loadable and was never validated", "notes": "Same task checksum as the archived control, which downloaded the real model in one step; no skill covers HF download or Flask inference, and no skill was opened."}
```
