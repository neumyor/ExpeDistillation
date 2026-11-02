日期：2026-10-31
一句话：实验二 D→Q 臂的第一批完整结果：强模型写技能、弱模型执行，比裸弱模型高 2.1 个百分点，但仍在重跑噪声之内。
对应 log.md：2026-10-31 「实验二 D→Q 第一批结果」条目

## 数据

- run：`runs/e2_d2q_alfworld_20261030_2350`（配置 `configs/experiments/alfworld_split_paper_e2_d2q.yaml`）
- 序列：`baseline → learn_add → exam → test`（P02 写出的技能直接接受，不经演化、不做整理）
- 教师/作者 D = deepseek-v4-flash-0731-tencent；学生 Q = qwen3.6-flash-distill
- 训练 39 题、测试 134 题，每题 3 次采样；test 为 folder 交付，对照是同 run 新跑的裸 Q
- 库：`bank:c3237f16c40a`（P02 原始技能集，15 个成员，未经考试淘汰）

## 结果

| 臂 | avg@3 | 对对照差 [95% CI] | W/L/T | pass@3 | 对照噪声 SD |
|---|---|---|---|---|---|
| 带库（D 写的技能，Q 执行） | **0.893** | **+0.021 [-0.017, +0.060]** | 27/19/88 | 0.993 | 0.038 |
| 裸 Q | 0.872 | - | - | 0.985 | - |

pass@3 配对差 +0.0075（CI [-0.015, 0.037]），McNemar p = 1.0（带库独赢 2 题、对照独赢 1 题）。
有判分 trial：带库 399/402，对照 398/402（其余为 invalid，按预注册口径拿掉）。

## 怎么读

- 方向和主表一致：D 写的技能给 Q 用，平均高 2.1 个百分点，胜负平 27/19/88。
- 差值小于对照臂的对半重抽噪声（0.038），95% 区间跨 0，**不能据此声称有效**。
- 这一臂的意义在于它和 `e1_ours`（带两轮考试与审阅、+2.36）几乎同量级：P02 之后的学习环节没有把这个差显著推高，也没有推低。实验二另外三臂（D→D、Q→Q、Q→D）正在跑，用来判断这个差是否随教师/学生配对变化。

## 复现

```
cd ../ExpeDistillation-MM-submit
.venv/bin/python -m expedistill.cli paper-tables --spec tmp/paper_spec_e2.json \
    --out-md tmp/e2_d2q.md --out-json tmp/e2_d2q.json
```

## 工件

- run：`runs/e2_d2q_alfworld_20261030_2350/`
- 表：`tmp/e2_d2q.md`、`tmp/e2_d2q.json`
- 主表对照：`docs/reports/main_alfworld_20261031.md`
