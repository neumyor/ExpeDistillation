# hf-model-inference / attempt 1（失败）

- trial：`traj/tb:hf-model-inference/strong/01`；步数 66；读取的技能：`bank/merge/0006`
- 主因：A_model_capability

## 1. 为什么失败 / 卡在哪里
- 卡点：判分第 3 项 `test_sentiment_endpoint` 在 `The journey was not bad; I actually had a good time.` 上失败：期望 positive，实际 negative（`assert result["sentiment"] == "positive"`）；其余 3 项通过（模型目录、服务在跑、错误处理）。
- 过程：
  - 第 1–8 步：探明官方 Hugging Face 不可达（curl 坏 wrapper、直连 `Network is unreachable`），改用 Python/requests 试 hf-mirror、ModelScope、GitHub 搜索。
  - 第 24 步：读了 `bank/merge/0006`。
  - 第 28–44 步：继续找模型与数据；第 40 步拿到一个 267,967,963 字节的 `pytorch_model.bin` 头，第 44 步解析到 ModelScope 的权重元数据。
  - 第 48 步：加载权重做特征抽取时出现 `LOAD_UNEXPECTED 6 ['vocab_transform.weight', 'vocab_transform.bias', 'vocab_layer_norm.weight', ...]`——这是 MLM 头，说明手里的是预训练底座而不是 SST-2 微调版；随后用 1200 条样本（600 正/600 负）训分类头，可见 8 个 epoch 准确率只有 0.508–0.593、loss 仍在 16.3–17.8。
  - 第 61 步：把底座 + 自训小分类头保存成完整模型目录（第 63 步可见 `model.safetensors` 267,832,560 字节）；第 62 步重写 Flask，只用 "I love this movie" 这类简单句自测（判 positive 0.81），第 63 步检查 400 错误分支后完成。
- 直接原因：最终部署的是"预训练底座 + 未训练好的自训分类头"，对带否定的正面句判成 negative；判分断言 `result["sentiment"] == "positive"` 失败。
- 与同题成功 attempt 的差异：
  - 成功 a02（48 步）第 2 步下载失败后，第 7–10 步用 Python socket 探明 hf-mirror 的 TLS 可用（`TLS_OK hf-mirror.com`；Hugging Face 直连 `Network is unreachable`），第 25 步安装 ModelScope 客户端，第 31 步用 `HubApi` 查同名模型的真实文件清单，第 42 步加载的 `config.json` 已是 SST-2 微调版（`finetuning_task: sst-2`、`id2label` 正/负），第 45 步正负样例验证后完成。
  - a01 也找到了可达来源并在第 40 步下载了大文件，但拿到的是 MLM 底座；它没有继续找同名的微调版权重，而是花约 20 步自己用 1200 条样本训练分类头（第 48–61 步），训练没有收敛就投入服务。
  - 两者的差别是"是否坚持拿到与任务匹配的已微调权重、并对预测质量做验收"，不是采样运气；a02 全程没读技能，也不是技能有无造成的。

## 2. 技能库里有没有能解决它的技能
- 候选：
  - `bank/merge/0006`（相关性 中，已读）——获取/构建/功能验证的探测式流程，要求继续按来源取上游工件并验证；但没有"如何区分同名底座与微调版"、也没有 HF 模型加载与质量验收的内容。
  - `bank/merge/0004`（相关性 中，未读）——提交前门禁要求核对端到端行为；若执行，至少会要求用多类句子覆盖端点再宣布完成，但不知道判分句集合，也无法保证通过。
  - `bank/merge/0002`（相关性 低）——不透明模型工件的 container/keys 检查，本任务不需要。
  - `bank/merge/0003`（相关性 低）——数据清单/统计，只与训练集准备沾边。
  - 其余成员（视频 OCR、候选程序打分、异步清理、SQLite/二进制、DNA、HTML、调度器、路径追踪）（相关性 无）。
- 缺的能力：受限网络下按 repo 名拿到正确的微调权重（区分 base 与 `-finetuned-sst-2`）、加载 distilbert 分类模型并用代表性样本验收情感质量。整库无此类技能。

## 3. 能解决问题的技能被触发了吗？为什么没解决
- 触发：读了 `bank/merge/0006`（第 24 步），并在第 28–44 步按"继续换来源探测"的方向执行了，因此不属于"没照做"。
- 原因：0006 没有 HF 模型选择/验收知识，无法提示"268 MB 的权重也可能是 MLM 底座"或"自训分类头需要达到可用准确率才能上线"。属于读了但内容不足（B），不是触发问题。

## 4. 结论与依据
- 主因：A_model_capability
- 一句话依据：第 48 步已经看到 MLM 头不匹配（`LOAD_UNEXPECTED` 含 `vocab_transform.*`）且自训分类头准确率仅 0.508–0.593，仍在第 61–62 步把该模型作为最终版本上线，只用两句简单样例自测；判分用带否定的正面句时输出 negative。
- 次要因素：库中无 HF 模型获取/验收技能（D）；`bank/merge/0006` 内容不覆盖该环节（B）；提交前门禁 `bank/merge/0004` 未被读取（C）。
