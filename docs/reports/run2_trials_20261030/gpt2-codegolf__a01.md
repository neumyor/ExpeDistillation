# gpt2-codegolf / attempt 1（失败）

- trial：`traj/tb:gpt2-codegolf/strong/01`；步数 66；读取的技能：`bank/merge/0002`
- 主因：A_model_capability

## 1. 为什么失败 / 卡在哪里
- 卡点：第 58–65 步——模型已经按字母序认出 TF ckpt 的 block 内顺序（`c_attn_b, c_attn_w, c_proj_b, c_proj_w, ln1_b, ln1_g, ln2_b, ln2_g, c_fc_b, c_fc_w, mlp_cproj_b, mlp_cproj_w`），并改回"wte/wpe 在前"的标准布局，但第 61/63/65 步的程序输出仍是 `toddler toddler ...`、`, and is.`、`Peng806 bureaucratic...` 一类重复串；最终 `gpt2.c` 5083 字节。
- 过程：
  - 第 0–4 步：确认输入是 497 MB 的 `.ckpt` 与 `vocab.bpe`；`file`/`xxd` 不存在，用 Python 读头尾，找不到变量名（raw float dump）；
  - 第 8–24 步：按 wte/wpe/12×block 的假设切 float 数组，用行范数与标准差定位层；第 24 步发现第 3079 行均值 0.8678（对应 ln2_g）；
  - 第 16 步确认环境没有 numpy/torch/tensorflow/transformers/jax；第 32–36 步继续核对 block 边界与 top-norm 行；
  - 第 40–44 步编译并重写 `gpt2.c`，第一次跑 `"Hello"` 输出 `Pengnernernernernernecessary...` 一类重复串；第 48 步 tokenizer 调试显示 `"Hello"` → `15492`；
  - 第 58–60 步识别字母序 block 顺序并改成标准布局，第 61 步 std 版本仍输出 `toddler toddler ...`；第 65 步把矩阵下标从 `w[v*o+u]` 改成 `w[u*i+v]`，输出 `Peng806 bureaucratic221806 GMOs...` / `shaman shaman ...`；
  - 第 66 步确认完成；判分时 `gpt2.c` = 5083 字节。
- 直接原因：判分器断言 `assert gpt2_path.stat().st_size < 5000` 失败：`assert 5083 < 5000`，只超 83 字节。测试在尺寸检查处就停了，编译/运行/输出断言没有跑到；但第 65 步的实际输出仍是重复词，即使压到 5000 字节以内也过不了 `"WARRANTY OF ANY KIND, EXPRESS OR IMPLIED"` 的输出断言。

## 2. 技能库里有没有能解决它的技能
- 候选：
  - `bank/merge/0002`（相关性 低）——本 trial 读的就是它；summary 是 "Builds and validates a fitted dependency or Bayesian model plus its intervention-modified version from a numeric table or opaque artifact"，它的 container/keys/tensor shapes 探查对 ckpt 有用，但后续步骤是 OLS/贝叶斯拟合，与 Transformer 前向无关。
  - `bank/merge/0004`（相关性 中）——提交前门禁：精确路径、schema、数量、独立参考、端到端行为、最终字节；能强制核对 `<5000`、编译与运行，但教不了 GPT-2 权重布局。
  - `bank/merge/0001`（相关性 中）——第 1 步要求提取 "every metric key and threshold"、记录基线/变体并验证 exactness；能把 5000 字节阈值纳入检查，但不含 GPT-2 实现知识。
  - `traj/tb:db-wal-recovery/strong/00/seg02`（相关性 低）——`struct.unpack('<f', ...)` 的二进制解码/校验可借鉴，解决不了权重顺序。
  - `traj/tb:llm-inference-batching-scheduler/strong/00/seg03`（相关性 低）——卡住循环的 instrument/sweep 调试法，方向上接近模型反复换布局/方向的过程。
- 缺的能力：从 TF ckpt 扁平 float 顺序还原 GPT-2 权重（wte/wpe/12×block/ln_f、[in,out] 方向）、GPT-2 BPE、argmax 解码，并在 5000 字节内用 C 实现。整库 17 个成员没有 GPT/Transformer/BPE 的实现性内容。

## 3. 能解决问题的技能被触发了吗？为什么没解决
- 触发：读了 `bank/merge/0002`（唯一）。
- 原因：它的 [When to use] 是 "a terminal task supplies numeric tabular data or an opaque model artifact and asks for a fitted dependency or Bayesian model"；ckpt 确实不透明，但本任务的交付物是 C 推理程序而不是拟合模型，正文的 OLS/贝叶斯步骤不适用。属于"读了但内容不适用"；真正需要的 GPT-2 实现技能在库里不存在。

## 4. 结论与依据
- 主因：A_model_capability
- 一句话依据：模型已认出 ckpt 的 block 内顺序并切到标准布局，但矩阵方向/前向仍未收敛（第 61/63/65 步输出均为重复词），同时 `gpt2.c` 停在 5083 字节，判分器在 `assert 5083 < 5000` 处即失败。
- 次要因素：技能库没有 GPT-2 实现类技能（D）；`bank/merge/0004`、`bank/merge/0001` 可覆盖"提交前核对阈值"这一环但未被触发（C）。
