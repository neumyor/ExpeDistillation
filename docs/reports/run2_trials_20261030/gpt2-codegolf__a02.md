# gpt2-codegolf / attempt 2（失败）

- trial：`traj/tb:gpt2-codegolf/strong/02`；步数 77；读取的技能：无
- 主因：A_model_capability

## 1. 为什么失败 / 卡在哪里
- 卡点：第 69–75 步——分词映射自检显示当前 byte→id 映射只能解析 1024/50000 条 merge（另一种映射能解析 16262/50000）；第 71–72 步改 `base_init` 后输出仍是固定 token `45083` 的重复；第 75 步 minify 后 `gpt2.c` 仍有 5156 字节。
- 过程：
  - 第 0–2 步：确认 497 MB 的 `.ckpt` 与 50001 行的 `vocab.bpe`；没有 numpy/torch；用 Python 读头尾确认是 raw float dump；
  - 第 14–24 步：写 C 扫描器按 768 float 窗口找低方差段，第 24 步列出若干候选；
  - 第 29 步：第一次改 `gpt2.c` 并 `wc -c` 得 5097；第 39 步跑 `"The"` 用时 34.5 秒、输出 `Pediatrics Pediatrics ...` 重复；
  - 第 54 步改 embedding 指针后 `wc -c` 得 5151；第 59 步 logits 调试打印 top-20（30535/21535/30510…）；
  - 第 69 步分词映射自检：当前映射 1024/50000、另一种映射 16262/50000，说明 BPE 表解析有问题；
  - 第 71–72 步改 `base_init` 的字节映射，输出变成 `45083 45083 ...` 重复；第 74–75 步核对布局并用 minify 脚本压缩，`wc -c` 仍为 5156；第 76 步再改矩阵方向，轨迹结束。
- 直接原因：判分器断言 `assert gpt2_path.stat().st_size < 5000` 失败：`assert 5156 < 5000`。测试在尺寸检查处就停了，编译/运行/输出断言没有跑到；但第 72 步的实际输出是固定 token `45083` 的重复，即使尺寸合格也过不了 `"WARRANTY OF ANY KIND, EXPRESS OR IMPLIED"` 的输出断言。

## 2. 技能库里有没有能解决它的技能
- 候选：
  - `bank/merge/0004`（相关性 中）——提交前门禁：精确路径、schema、数量、独立参考、端到端行为、最终字节；[When to use] 覆盖"产物会被执行判分、任务给出命名输出路径、下次动作就是标记完成"的场景。它能强制核对 `<5000`、编译与运行，但教不了 GPT-2 权重布局。
  - `bank/merge/0001`（相关性 中）——第 1 步要求提取 "every metric key and threshold"、记录基线/变体并验证 exactness；能把 5000 字节阈值纳入检查，但不含 GPT-2 实现知识。
  - `bank/merge/0002`（相关性 低）——不透明工件的 container/keys/tensor shapes 探查对 ckpt 有用，但目标是拟合统计模型。
  - `traj/tb:db-wal-recovery/strong/00/seg02`（相关性 低）——`struct.unpack('<f', ...)` 的二进制解码/校验可借鉴，解决不了权重顺序与 BPE。
  - `traj/tb:llm-inference-batching-scheduler/strong/00/seg03`（相关性 低）——卡住循环的 instrument/sweep 调试法，方向上接近模型反复换布局/方向的过程。
- 缺的能力：从 TF ckpt 扁平 float 顺序还原 GPT-2 权重（wte/wpe/12×block/ln_f、[in,out] 方向）、正确的 GPT-2 BPE 映射、argmax 解码，并在 5000 字节内用 C 实现。整库 17 个成员没有 GPT/Transformer/BPE 的实现性内容。

## 3. 能解决问题的技能被触发了吗？为什么没解决
- 触发：没读（`meta.json` 的 `skills_read` 为空、`skills_read.md` 无内容）。
- 原因：整库没有 GPT-2/Transformer 类技能；最接近的 `bank/merge/0004`（提交前门禁）与 `bank/merge/0001`（阈值/精确性）本应命中"命名产物会被执行判分、任务给出明确阈值"的场景，但没有被选中。失败不来自触发覆盖不足，而来自模型自己的前向/分词/尺寸三重未收敛。

## 4. 结论与依据
- 主因：A_model_capability
- 一句话依据：第 69 步已发现 BPE 映射只能解析 1024/50000 条 merge，第 72 步输出固定 token `45083` 的重复，第 75 步 minify 后 `gpt2.c` 仍为 5156 字节；判分器在 `assert 5156 < 5000` 处即失败。
- 次要因素：技能库没有 GPT-2 实现类技能（D）；`bank/merge/0004`、`bank/merge/0001` 可覆盖"提交前核对阈值"这一环但未被触发（C）。
