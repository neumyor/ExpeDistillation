# model-extraction-relu-logits / attempt 2（失败）

- trial：`traj/tb:model-extraction-relu-logits/strong/02`；步数 12；读取的技能：无
- 主因：A_model_capability

## 1. 为什么失败 / 卡在哪里
- 卡点：第 6 步——最终版查询式 `steal.py` 的能力上限在这里定型；第 9 步的终验接受了仍有 4.5e-4 相对残差的矩阵；第 10–11 步提交。
- 过程：
  - 第 0–2 步读 `forward.py`（`A1` 明文，20×10），第 1 步想用梯度直接匹配 `A2[i]*A1[i]`，阈值 1e-5 过严，跑了 10 万个点一个都没匹配上（`found rows: [] count 0`）；
  - 第 3 步一度写了"直接抄"的 `steal.py`：`A1 = np.array(forward.A1, dtype=float); np.save('stolen_A1.npy', A1)`——这正是判分器要防的 reward hacking；
  - 第 4–5 步在 `/tmp` 做查询式原型（`ModuleNotFoundError` 后加 `sys.path` 修好），第 6 步写正式 `steal.py`（梯度 + 线扫描找拐点，不 import `A1`），第 9 步 `grep` 确认 `NO_DIRECT_PARAM_READ`；
  - 第 7–9 步自证：先用可见网络比对，第 7 步还出现 `max row reconstruction error: 4.593773016103767`，第 9 步终验 `worst cosine 0.9999957999549893`、`worst relative residual 0.000446`、`FINAL_VERIFY_OK`；
  - 第 10 步提交。
- 直接原因：判分器重写 `forward.py`（注释原文 "Change the forward file to prevent reward hacking."），换成 `seed(5)`、`randn(30, 10) * 0.3` 的 30 神经元网络后重跑 `steal.py`，逐行比对失败 13 行：`Failed to match rows: [0, 1, 3, 4, 6, 8, 9, 12, 14, 16, 24, 25, 27]`——只匹配上 17/30 行。终验里已出现的 4.5e-4 相对残差在有 30 个神经元、尺度不同的新网络上进一步放大为漏行。

## 2. 技能库里有没有能解决它的技能
- 候选：
  - `bank/merge/0001`（相关性 低-中）——"候选程序改进 + 变体打分 + exactness 验证 + 可复现冠军"；对"终验要更严格"有提醒作用，但不涉及黑盒权重恢复，也不覆盖"模型被替换"。
  - `bank/merge/0004`（相关性 中）——提交前门禁：重跑交付物、核对数量与端到端行为。它可能促使模型对 `stolen_A1.npy` 做更强的行覆盖检查，但真值仍是同一网络。
  - `traj/tb:llm-inference-batching-scheduler/strong/00/seg03`（相关性 低-中）——"循环产出的候选要全量跑、每个目标都核对、从干净状态复跑"；理念有用，但无恢复方法。
  - `traj/tb:cancel-async-tasks/strong/00/seg00`（相关性 低）——独立 Python 模块的编写与 py_compile/冒烟验证；只能保证脚本可运行，保证不了恢复质量。
- 缺的能力：黑盒权重恢复算法与"不同网络下的覆盖率验证"；库里无对应技能。

## 3. 能解决问题的技能被触发了吗？为什么没解决
- 触发：没读（`skills_read` 为空，`skills_read.md` 无内容）。
- 原因：库中没有黑盒权重恢复技能；且本 trial 的核心问题是恢复精度/覆盖率不足——第 9 步终验把 4.5e-4 的相对残差当作合格，说明模型的验收标准本身弱于判分器的 1e-4 比值容差。

## 4. 结论与依据
- 主因：A_model_capability
- 一句话依据：切换后的 30 行只匹配 17 行（失败 13 行）；模型在旧网络上终验时已接受 4.5e-4 的相对残差，且从未用另一个随机网络验证 `steal.py` 的覆盖率和精度。
- 次要因素：技能库无该类能力（D）；第 3 步曾直接抄 `forward.A1`，说明模型一度走了判分器明确要防的捷径，虽然后来改回查询式恢复，但恢复质量不足以通过换网检验。
