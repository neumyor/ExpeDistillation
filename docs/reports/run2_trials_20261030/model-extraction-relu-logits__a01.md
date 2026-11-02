# model-extraction-relu-logits / attempt 1（失败）

- trial：`traj/tb:model-extraction-relu-logits/strong/01`；步数 10；读取的技能：无
- 主因：A_model_capability

## 1. 为什么失败 / 卡在哪里
- 卡点：第 7 步——最终版 `steal.py` 写完，只用 `forward.A1` 自证；第 8–9 步提交。
- 过程：
  - 第 0–2 步读 `forward.py` 并确认 `A1` 形状为 (20, 10)、numpy 2.2.5 可用；
  - 第 3 步原型脚本从 `/tmp` 运行报 `ModuleNotFoundError: No module named 'forward'`；第 4–5 步改到 `app/` 下并加 `PYTHONPATH` 后跑通；
  - 第 5–6 步对角扫描遇到方向数不足（部分行相关系数只有 0.56–0.78），第 6 步改用梯度方向重新配对，20 行相关性全部 1.0；
  - 第 7 步写正式 `steal.py`（线性区跳变检测 + 拐点分组），并对 `forward.A1` 断言 `min per-row abs correlation: 0.9999999999999993`、`all matched: True`、`mapping: [0..19]`；
  - 第 8–9 步提交。
- 直接原因：判分器导入测试时重写 `forward.py`（注释原文 "Change the forward file to prevent reward hacking."），换成 `seed(5)`、`randn(30, 10) * 0.3` 的 30 神经元网络，再重跑 `steal.py` 与这 30 行逐行比对（允许缩放，逐元素比值偏差 <1e-4）。结果为 `AssertionError: Failed to match rows: [1, 13, 21, 29]`——只恢复出 26/30 行。

## 2. 技能库里有没有能解决它的技能
- 候选：
  - `bank/merge/0001`（相关性 低-中）——程序改进/变体打分/exactness 验证的通用协议；能督促"用独立参考验证交付物"，但本任务的独立参考（判分时的替换网络）不在环境里，且它不提供权重恢复算法。
  - `bank/merge/0004`（相关性 中）——提交前交付门禁（路径、数量、端到端重跑）；可减少"只验证一次"的风险，但不会提示"模型会被替换"。
  - `traj/tb:llm-inference-batching-scheduler/strong/00/seg03`（相关性 低-中）——"逐点打印、逐一核对每个目标、从干净状态复跑"的调参循环；理念接近"覆盖全部神经元"，但无黑盒恢复方法。
  - `traj/tb:path-tracing/strong/00/seg00`（相关性 低）——未知文件表征与伴随可执行物对比；只沾"逆向未知工件"的边。
- 缺的能力：黑盒 ReLU 网络权重恢复流程本身（以及按神经元计数做完整性判定），库里没有任何成员覆盖。

## 3. 能解决问题的技能被触发了吗？为什么没解决
- 触发：没读（`skills_read` 为空，`skills_read.md` 无内容；采样到的步骤里也没有访问技能目录）。
- 原因：库里没有与权重恢复相关的技能，读与不读都不改变结果；真正的缺口是"恢复不全"。

## 4. 结论与依据
- 主因：A_model_capability
- 一句话依据：同一替换网络下只恢复 26/30 行（`Failed to match rows: [1, 13, 21, 29]`），且脚本的跳变阈值/分组参数只在可见的 20 神经元网络上验证过；找到 20 行后即认为完成，没有做"换一个网络再跑一遍"的鲁棒性检查。
- 次要因素：技能库缺少该类能力（D）；判分器的换网机制对模型不可见，模型的自证参考与被测对象同源。
