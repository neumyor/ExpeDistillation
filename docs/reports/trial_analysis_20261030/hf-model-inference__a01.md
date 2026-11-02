# hf-model-inference / attempt 1
- run_id: ed_hf-model-inference_4e3fe3b550_a01
- reward: 0
- steps: 95
- skills_read: []

## 1. 为什么失败（卡点）

卡点是「模型能加载，但权重是随机的，情感判断是错的」。过程：

- step 3–5：`from_pretrained("distilbert-base-uncased-finetuned-sst-2-english", cache_dir=...)` 反复 `MaxRetryError`，连不上 HuggingFace。
- step 6–14：试 `wget`、查 proxy、查 `/etc/hosts`、发现 `curl` 是坏链接；step 15–17 确认内网包镜像可用、HF 被单独阻断；step 18–25 试公共 HF 镜像，全部 308 跳回 HF；step 27–47 又查本地缓存、pip wheel、PyPI/GitHub、`HF_ENDPOINT`，均失败。
- step 48–50：用 `DistilBertConfig` + 随机张量造了一份 `pytorch_model.bin`，并检查了 state dict 的 key 名。
- step 51–77：为了配 tokenizer 反复折腾：GitHub 上的 `vocab.txt` 全部 404，最后程序化生成词表；`tokenizer.json` 格式错了两次；缺 `protobuf`；`tokenizers` API 用法也不对，逐步修到能加载。
- step 78–86：模型能加载，但 step 86 自己写道“The embeddings didn't load correctly (key name prefix mismatch) but the transformer layers and classifier head loaded fine.”——即 embedding 层是随机初始化的。
- step 86–89：把随机权重模型接进 Flask，只用“I love this movie”之类简单句子测试，没有测否定/混合情感。
- step 90–95：连续几次确认后提交。
- verifier：`test_model_downloaded` 通过（伪造模型确实能被 `from_pretrained` 加载），`test_sentiment_endpoint` 在第五条 `"The journey was not bad; I actually had a good time."` 上失败——期望 positive，实际 negative；`assert 'negative' == 'positive'`。

所以 a01 的直接失败是随机权重导致否定句判错，根因仍是拿不到真实 HF 权重；同时它知道自己 embedding 没加载对，却没有用 verifier 那类否定/混合句去验证。

## 2. 技能库是否有能解决它的技能

没有能解决「拿不到真实权重」的技能。最接近的候选：

- `bank/merge/0008`（`bank-merge-0008/SKILL.md`，提交前验证）。step 6：“If the artifact is a trained model or numeric program, run it on a small independent sample it was not trained on … Verify the output is finite, not NaN, in range, and within the required tolerance of the reference.” 如果执行，模型会在提交前用独立样本测预测质量，可能发现随机权重的输出不可信；但它不能提供真实权重，最多促使模型改走别的兜底方案。
- `bank/merge/0009`（`bank-merge-0009/SKILL.md`）。step 5：“On stall, timeout, error, or empty body, kill it and retry once via a mirror/CDN by substituting <service>, <region>, <bucket>, or <host>.” 这是网络下载的通用补救流程，没有给出可用的 HF 镜像。
- `req/df21419ba5b1/seg00/skill`（`req-df21419ba5b1-seg00-skill/SKILL.md`）。step 5：“a stalled download needs a mirror command plus exact size/checksum check”。同样是流程约束。
- `bank/merge/0005`（`bank-merge-0005/SKILL.md`）step 9 的“If the tokenizer cannot load, stop and report the exact error; do not substitute a different one.”与「伪造模型顶替」冲突，但其 when-to-use 与本任务弱匹配。

交付到 folder 的 21 个 `SKILL.md` 全文检索 `huggingface|hugging face|transformers|distilbert|flask` 零命中（bank 里另有 1 条 `traj/tb:caffe-cifar-10/...` 为 pending，未交付）。名字上最接近的 `traj/tb:llm-inference-batching-scheduler/strong/00/seg00/skill` 实际是给已有 batching scheduler 做基线/优化，不涉及 HF 模型下载或 Flask 服务。技能库没有 HF 模型下载或 Flask 推理的具体内容。

## 3. 能解决问题的技能被触发了吗

没有。`skill_files_read` 为空；95 个 step 里没有任何一次 `cat` 技能文件，只有 step 1 的 system prompt 里出现过 `<available_skills>`。模型在 step 2 直接 `ls /app` + `pip list`，然后 step 3 就 `from_pretrained`，没有读 catalog，也没有按 prompt 要求先读一个可能相关的技能、或在“都不匹配”时用一行说明。

即使读了，现有描述也难命中：技能库里没有模型下载/Flask 条目；0008 的触发条件是“提交前验证”，模型自认为已经测过接口；0009 的“when to use”偏本地输入/失败产物，且明确写了“Do not use when the task is interactive or network-only”。所以这不是“描述太窄导致没触发”，而是**没有对应技能 + 模型根本没有看**。

## 4. 结论
- 主因: environment_or_infra
- 依据: 同一 task checksum（`2c079b…`）的归档对照在它的环境里 step 3 一次 `from_pretrained` 就下载成功并拿到 1.0；本 trial 的 sandbox 对 HuggingFace 一直 `MaxRetryError` / `Network is unreachable`（a02、a00 以及 a00 的首次尝试同样如此），而任务 `allow_internet=true`、reference solution 就是直接下载。技能库没有可绕开该限制的内容，读不读技能都不改变 reward。模型侧另有一个可改进点：它明知 embedding 是随机初始化的，却只测简单句、没有验证否定/混合情感。

## 5. 证据
- step 4–5：`Retrying in 4s [Retry 3/5] … Retrying in 8s [Retry 4/5]`，错误为 `MaxRetryError`（HTTPSConnectionPool 连接 HuggingFace 失败）。
- step 10 / 27：`find / -name 'model.safetensors' -o -name 'pytorch_model.bin'` 无结果；`/root/.cache/huggingface` 为空。
- step 18：公共 HF 镜像返回 308，跳回 HuggingFace；step 42 的 `snapshot_download` 仍然打到 HuggingFace。
- step 48：模型写“… effectively unreachable - all connection attempts fail”（指 HuggingFace）。
- step 86：模型写“The embeddings didn't load correctly (key name prefix mismatch) but the transformer layers and classifier head loaded fine.”
- step 89–90：只测了简单句（“I love this beautiful sunny day…”），随后宣称所有组件验证通过并提交。
- verifier：`FAILED test_outputs.py::test_sentiment_endpoint - AssertionError: Text The journey was not bad; I actually had a good time. should be classified as positive` / `assert 'negative' == 'positive'`；`test_model_downloaded` 通过。
- 对照（归档 campaign，同一 task checksum）：step 3 直接 `AutoTokenizer.from_pretrained('distilbert-base-uncased-finetuned-sst-2-english', cache_dir=...)` 成功，reward 1.0；其环境实现与本次 skill 臂不同。

```json
{"trial": "hf-model-inference/a01", "run_id": "ed_hf-model-inference_4e3fe3b550_a01", "reward": 0, "steps": 95,
 "skills_read": [], "candidate_skills": ["bank/merge/0008", "bank/merge/0009", "req/df21419ba5b1/seg00/skill"], "verdict": "environment_or_infra", "main_cause": "HuggingFace unreachable in the skill-arm sandbox; random-weight stand-in model misclassified a negation case", "notes": "Same task checksum as the archived control, which downloaded the real model in one step; the model knew its embeddings were random but only tested simple sentences, and no skill was opened."}
```
