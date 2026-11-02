日期：2026-10-31
一句话：主表本方法在 ALFWorld 上的第一批完整结果（134 道测试题 ×3 采样，与同 run 的裸弱模型对照），差值为正但在重跑噪声之内。
对应 log.md：2026-10-31 「主表 ALFWorld 第一批结果」条目

## 数据

- run：`runs/e1_ours_alfworld_20261030_2325`（配置 `configs/experiments/alfworld_split_paper_e1_ours.yaml`）
- 序列：`baseline → learn_add → exam → learn → exam → learn_trial → test`
- 模型：教师/作者/出题/裁判 D = deepseek-v4-flash-0731-tencent；学生 Q = qwen3.6-flash-distill
- 数据：训练 39 题（baseline、两个考场用它），测试 134 题（valid_unseen），每题 3 次采样
- 交付：test 相位为 `folder`（技能目录 + `ReadSkill[...]` 按需打开），对照为同 run 内新跑的裸弱模型
- 库：终测时 `bank:5047fe487b8a`；p03 考场 23 单元里 11 通过、12 失败；p04 审阅 12 条 finding、5 个新需求

## 主结果（条件口径 avg@N，预先登记的主指标）

| 臂 | avg@3 | 对对照差 [95% CI] | W/L/T | pass@3 | 对照噪声 SD |
|---|---|---|---|---|---|
| 带库（本方法） | **0.915** | **+0.024 [-0.017, +0.065]** | 26/21/87 | 1.000 | 0.034 |
| 裸弱模型（对照） | 0.892 | - | - | 0.985 | - |

pass@3 的配对差 +0.0149（CI [0, 0.037]），McNemar 精确检验 p = 0.5（只有 2 道题带库通过而对照没通过，0 道相反）。
有判分的 trial：带库 402/402，对照 399/402（3 个对照 trial 判为 invalid，按预注册口径从所在题的平均里拿掉，不记 0）。

## 怎么读

- 方向为正：带库臂比裸弱模型高 2.36 个百分点，胜负平 26/21/87。
- **但差值小于同一臂对半重抽的噪声（0.034）**，95% 区间跨 0。按 `paper_statistics_20261030.md` 的读法，这个差落在重跑噪声之内，不能据此声称 ALFWorld 上有效。
- 134 题 ×3 采样的最小可分辨差约 0.045（此前按 campaign 种子数据估的 MDE），本结果低于该量级，符合预期。
- 这里测的是"学习后的库 vs 裸跑"的整体效果；实验三、四要回答的是这个差为什么没有更大（子任务归因、触发条件是否参与演化）。

## 复现

```
cd ../ExpeDistillation-MM-submit
.venv/bin/python -m expedistill.cli paper-tables --spec tmp/paper_spec_main.json \
    --out-md docs/reports/main_alfworld_20261031.md --out-json tmp/main_alfworld.json
```

表格由 `stages/paper_tables.py` 从逐 trial 的 answer sheet 重算，并与 `phases/p07_test/phase_summary.json` 逐项比对；任何不一致会直接报错。

## 工件

- run：`runs/e1_ours_alfworld_20261030_2325/`
- 表：`tmp/main_alfworld.md`、`tmp/main_alfworld.json`
- 口径：`docs/decisions/paper_statistics_20261030.md`
