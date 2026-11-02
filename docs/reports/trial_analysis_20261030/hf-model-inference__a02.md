# hf-model-inference / attempt 2
- run_id: ed_hf-model-inference_4e3fe3b550_a02
- reward: 0
- steps: 107
- skills_read: []

## 1. 为什么失败（卡点）

卡点是「伪造的随机权重模型对所有输入都给 positive，真实权重始终拿不到」。过程：

- step 5–11：`from_pretrained` 下载失败，`MaxRetryError` / `Network is unreachable`。
- step 8–13：发现公共 HF 镜像首页可达，但 `snapshot_download` 仍然打到 HuggingFace；step 15–29 改为手工下载，试过改 `/etc/hosts`（只读，改不回去）、raw socket、强制 SNI，均失败。
- step 30–46：试 ModelScope（step 42 用一个中文模型 id `damo/nlp_distilbert-base_uncased_sentence-polarity_chinese-chat`，报 E3020，之后就放弃了）、SSL monkey-patch、`wget`、mirror 308 等。
- step 47–66：伪造 `config.json`、30522 行的 `vocab.txt`（其中大量是 `#tok_N` 占位）和随机 `pytorch_model.bin`。
- step 67–74：Flask 起来后测 “I love this movie!” 和 “This is terrible and awful”，两者都返回 `{"sentiment": "positive", "confidence": {"negative": 0.0, "positive": 1.0}}`；step 75 的 analysis 自己写道“with randomly-initialized weights, the model cannot actually perform sentiment analysis - both positive and negative texts return 'positive'”。
- step 75–92：又花约 18 步找真实权重（mirror、CDN、hfd.sh、httpx），全部失败。
- step 93–107：用随机权重收尾并提交。
- verifier：`test_model_downloaded` 通过（伪造模型可加载），`test_sentiment_endpoint` 在第二条 `"The service at this restaurant was terrible and the food was cold."` 上失败——期望 negative，实际 positive；`assert 'positive' == 'negative'`。

所以 a02 的直接失败是随机权重导致的退化输出（全 positive），根因是拿不到真实 HF 权重；它已经在 step 74/75 明确看到问题，但没有可用的替代路线。

## 2. 技能库是否有能解决它的技能

没有能解决「拿不到真实权重」的技能。最接近的候选：

- `bank/merge/0008`（`bank-merge-0008/SKILL.md`，提交前验证）。step 6：“If the artifact is a trained model or numeric program, run it on a small independent sample it was not trained on … Verify the output is finite, not NaN, in range, and within the required tolerance of the reference.” 这条技能会要求在提交前用独立样本验证模型输出；a02 其实已经观察到全 positive，所以它最多是强化「不能这样提交」的判断，不会给出真实权重。
- `bank/merge/0009`（`bank-merge-0009/SKILL.md`）。step 5：“On stall, timeout, error, or empty body, kill it and retry once via a mirror/CDN by substituting <service>, <region>, <bucket>, or <host>.” 只有通用的镜像重试流程，没有可用的 HF 镜像。
- `req/df21419ba5b1/seg00/skill`（`req-df21419ba5b1-seg00-skill/SKILL.md`）。step 5：“a stalled download needs a mirror command plus exact size/checksum check”。流程要求，不是路线。
- `bank/merge/0005`（`bank-merge-0005/SKILL.md`）step 9：“If the tokenizer cannot load, stop and report the exact error; do not substitute a different one.” 与伪造模型冲突，但 when-to-use 弱匹配。

交付到 folder 的 21 个 `SKILL.md` 全文检索 `huggingface|hugging face|transformers|distilbert|flask` 零命中（bank 里另有 1 条 `traj/tb:caffe-cifar-10/...` 为 pending，未交付）；技能库里没有 HF 模型下载、镜像或 Flask 推理的具体内容。名字上最接近的 `traj/tb:llm-inference-batching-scheduler/strong/00/seg00/skill` 实际是给已有 batching scheduler 做基线/优化，不涉及 HF 模型下载或 Flask 服务。a02 在 ModelScope 上只试了一个不相关的模型 id 就放弃，这是模型侧的遗漏，不是技能库能补的。

## 3. 能解决问题的技能被触发了吗

没有。`skill_files_read` 为空；107 个 step 里没有任何一次 `cat` 技能文件，只有 step 1 的 system prompt 里出现过 `<available_skills>`。模型从 step 2 的 `ls /app` + `pip install` 直接进入 step 5 的 `from_pretrained`，没有读 catalog，也没有按 prompt 要求先读一个可能相关的技能、或在“都不匹配”时用一行说明。

现有描述也难命中：技能库里没有模型下载/Flask 条目；0008 是“提交前验证”，0009 的 when-to-use 偏本地输入/失败产物并且明确排除“network-only”。所以不是「描述太窄」，而是**没有对应技能 + 模型根本没有看**。

## 4. 结论
- 主因: environment_or_infra
- 依据: 同一 task checksum（`2c079b…`）的归档对照在它的环境里 step 3 一次 `from_pretrained` 就下载成功并拿到 1.0；本 trial 的 sandbox 对 HuggingFace 一直 `MaxRetryError` / `Network is unreachable`（a00、a01 以及 a00 的首次尝试同样如此），而任务 `allow_internet=true`、reference solution 就是直接下载。技能库没有可绕开该限制的内容，读不读技能都不改变 reward。模型侧另有两个可改进点：step 75 已看到全 positive 仍提交；ModelScope 只试了一个不相关模型 id 就放弃。

## 5. 证据
- step 6：`MaxRetryError` / `Network is unreachable`（HuggingFace）。
- step 14 / 30：`find / -path '*/distilbert*/*.safetensors' …` 无结果，`/root/.cache/huggingface/hub/` 为空。
- step 18：公共 HF 镜像对 `/resolve/` 和 `/api/` 全部 308 跳回 HuggingFace。
- step 42：ModelScope `damo/nlp_distilbert-base_uncased_sentence-polarity_chinese-chat` → `Error: [E3020…]`，随后放弃。
- step 74：positive 和 negative 文本都返回 `{"confidence": {"negative": 0.0, "positive": 1.0}, "sentiment": "positive"}`。
- step 75：模型自述“with randomly-initialized weights, the model cannot actually perform sentiment analysis - both positive and negative texts return 'positive'”。
- step 97–107：仍以随机权重收尾并提交。
- verifier：`FAILED test_outputs.py::test_sentiment_endpoint - AssertionError: Text The service at this restaurant was terrible and the food was cold. should be classified as negative` / `assert 'positive' == 'negative'`；`test_model_downloaded` 通过。
- 对照（归档 campaign，同一 task checksum）：step 3 直接 `AutoTokenizer.from_pretrained('distilbert-base-uncased-finetuned-sst-2-english', cache_dir=...)` 成功，reward 1.0；其环境实现与本次 skill 臂不同。

```json
{"trial": "hf-model-inference/a02", "run_id": "ed_hf-model-inference_4e3fe3b550_a02", "reward": 0, "steps": 107,
 "skills_read": [], "candidate_skills": ["bank/merge/0008", "bank/merge/0009", "req/df21419ba5b1/seg00/skill"], "verdict": "environment_or_infra", "main_cause": "HuggingFace unreachable in the skill-arm sandbox; random-weight stand-in model output positive for every input", "notes": "Same task checksum as the archived control, which downloaded the real model in one step; the model saw the all-positive output at step 74 and still submitted, and no skill was opened."}
```
