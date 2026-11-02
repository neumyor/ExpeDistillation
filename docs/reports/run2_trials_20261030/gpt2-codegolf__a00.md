# gpt2-codegolf / attempt 0（失败）

- trial：`traj/tb:gpt2-codegolf/strong/00`；步数 84；读取的技能：无
- 主因：A_model_capability

## 1. 为什么失败 / 卡在哪里
- 卡点：整个 84 步都在逆推 TF ckpt 的扁平 float 布局与矩阵方向；第 79–81 步的补丁后程序仍输出重复词，且 `gpt2.c` 最终 5834 字节，判分器第一条就要求 `< 5000`。
- 过程：
  - 第 0–2 步确认输入是一个 497 MB 的 `.ckpt` 与 `vocab.bpe`，环境没有 numpy/torch/TF；`od`/`strings` 找不到变量名（raw float dump）；
  - 第 10 步写出第一版 `gpt2.c`；第 16 步 BPE 解析脚本因缺 `regex` 模块失败；
  - 第 22–52 步用均值/方差/行范数扫描 float 数组猜 wte/wpe/block 边界，第 46 步得到若干低方差段；
  - 第 52–70 步写 Python 参考实现试不同权重顺序与矩阵方向，不同假设给出互相矛盾的 top-1（30766/36877 等）；
  - 第 76–81 步用 perl 改 `gpt2.c` 的矩阵下标方向；第 81 步跑 `"The meaning of life is"` 输出 `articulated articulated ...` 的重复串；
  - 第 83 步（轨迹末尾）仍在跑参考实现；判分时 `gpt2.c` = 5834 字节。
- 直接原因：判分器断言 `assert gpt2_path.stat().st_size < 5000` 失败：`assert 5834 < 5000`。测试在尺寸检查处就停了，后面的编译、运行与输出断言根本没跑到；但从第 81 步的实际输出看，即使尺寸合格，程序也只会输出重复的 `articulated`，过不了 `"WARRANTY OF ANY KIND, EXPRESS OR IMPLIED"` 的输出断言。

## 2. 技能库里有没有能解决它的技能
- 候选：
  - `bank/merge/0004`（相关性 中）——提交前门禁：精确路径、schema、数量、独立参考、端到端行为、最终字节；[When to use] 明确覆盖"产物会被执行判分、任务给出命名输出路径、下次动作就是标记完成"的场景。它能强制在提交前核对 `<5000`、编译与运行，但教不了 GPT-2 权重布局。
  - `bank/merge/0001`（相关性 中）——第 1 步要求 "Identify ... every metric key and threshold ... Never substitute guessed thresholds"，并记录基线/变体、验证 exactness；能把 5000 字节阈值纳入检查，但同样不含 GPT-2 实现知识。
  - `bank/merge/0002`（相关性 低）——不透明工件的 container/keys/tensor shapes 探查对 ckpt 有用，但它的目标是拟合统计模型，不涉及 Transformer。
  - `traj/tb:db-wal-recovery/strong/00/seg02`（相关性 低）——`struct.unpack('<f', ...)` 的二进制解码/校验可借鉴，但解决不了权重顺序与矩阵方向。
  - `traj/tb:llm-inference-batching-scheduler/strong/00/seg03`（相关性 低）——卡住循环的 instrument/sweep 调试法与模型反复试方向的过程接近，但不给 GPT-2 知识。
- 缺的能力：从 TF ckpt 的扁平 float 顺序还原 GPT-2 权重（wte/wpe/12×block/ln_f、[in,out] 方向）、GPT-2 BPE、argmax 解码，并在 5000 字节内用 C 实现。整库 17 个成员没有 GPT/Transformer/BPE 的实现性内容。

## 3. 能解决问题的技能被触发了吗？为什么没解决
- 触发：没读（`meta.json` 的 `skills_read` 为空、`skills_read.md` 无内容）。
- 原因：整库没有 GPT-2/Transformer 类技能；最接近的 `bank/merge/0004`（提交前门禁）与 `bank/merge/0001`（阈值/精确性）本应命中"命名产物会被执行判分、任务给出明确阈值"的场景，但没有被选中。失败不来自触发覆盖不足，而来自模型自己的 ckpt 布局/矩阵方向试错没有收敛。

## 4. 结论与依据
- 主因：A_model_capability
- 一句话依据：模型在 ckpt 权重顺序与矩阵方向上反复试错仍未得到正确前向（第 81 步输出重复 `articulated`），同时从未把 `gpt2.c` 压到 5000 字节以内（判分时 5834），判分器在 `assert 5834 < 5000` 处即失败。
- 次要因素：技能库没有 GPT-2 实现类技能（D）；`bank/merge/0004`、`bank/merge/0001` 可覆盖"提交前核对阈值"这一环但未被触发（C）。
