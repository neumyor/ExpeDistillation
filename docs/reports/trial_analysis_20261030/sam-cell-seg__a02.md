# sam-cell-seg / attempt 2
- run_id: ed_sam-cell-seg_4e3fe3b550_a02
- reward: 0
- steps: 60
- skills_read: []

## 1. 为什么失败（卡点）

卡点是输出 CSV 的列数，不是分割质量。九项判分 8 过 1 挂，唯一失败是 `test_csv_shape_cols`：`Output df shape is incorrect, got (32, 10), expected (32, 11)`。判分器重跑脚本时打印 `Points: min=72, max=291, rects=0`，`test_masks_are_no_longer_rect`、`test_mask_alignment`、`test_no_polyline_overlaps`、`test_single_contiguous_mask_per_cell`、`test_coords_are_flat_lists` 全部通过——几何部分做对了。

原因：最终脚本（第 57 步写入并运行到判分）用 10 个键逐行构造输出，固定 `col_order = ["raw_classification", "main_classification", "super_classification", "type", "xmin", "ymin", "xmax", "ymax", "coords_x", "coords_y"]`，再 `out_df.to_csv(args.output_path, index=False)`；输入 CSV 的第 1 列是索引列 `Unnamed: 0`（同一份 7072 字节的 `demo_metadata.csv` 在 a00 的 step 87 读入为 (48, 11)：`['Unnamed: 0', 'raw_classification', ..., 'coords_y']`），输出因此少一列。题面明确要求 "save the new masks into a csv that matches the input csv"。

过程：第 4 步装依赖；第 8–11 步 git clone MobileSAM，直接使用仓库自带的 `weights/mobile_sam.pt`（没有 a00 的找权重绕路）；第 16–35 步写脚本并修 timm 缺失、`cv2.morphologyOps`、contour 形状、int32 等问题；第 36 步 48/48 全 polyline；第 38–59 步把重叠从 3 对修到 0（最近质心分区 + `CHAIN_APPROX_NONE`）；第 60 步自检 `Overlapping pairs: 0 / Multi-component: 0 / Rectangles: 0 / Points 54-174`，第 61 步提交。

直接原因：几何对了、输出契约错了；而且这个错误在 demo 上就已经存在——第 24 步同时把输入（48 行、11 列）和输出（48 行、10 列）读进内存，却只比较了行数和 `type` 分布，没有比较列名。

## 2. 技能库是否有能解决它的技能

有，而且是直接对症的 CSV 产物校验技能（不需要懂分割）：

- `bank/merge/0005`（数据/产物清点与校验）：第 21 步 "When an output schema is specified, validate delimiter, quoting, line endings, units, column order, and row limit with a parser or diff against the expected header."；第 16 步 "For CSV/TSV: print the first three lines and the line count. Verify the header matches expected column names and line count equals required data rows plus one header row."
- `bank/merge/0008`（提交前验证）：第 12 步 "Verify required keys, types, and global limits. Use a script to check counts and constraints. If coverage or schema fails, regenerate missing entries or fix the schema."；第 1 步要求先固定 "its schema"。
- `bank/merge/0002`（evaluator 驱动优化，相关性较低：本题没有可读的官方 evaluator）：第 7 步要求逐输入集做 "record-count equality; every required field and type present" 和 "every shape rule"。

这些技能命中的正是失败断言：输出列必须与输入列一致。缺的只是实现方式——把输入帧整体复制、只改 6 个列（或补回 `Unnamed: 0`），而不是领域知识。

## 3. 能解决问题的技能被触发了吗

没有触发：`phase_summary.json` 的 `skill_files_read["traj/tb:sam-cell-seg/weak/02"]` 为空；整条轨迹里 `skill`/`catalog`/`openclaw` 只出现在第 1 步的 prompt，没有任何技能读取，也没有按 prompt 要求写"none matches"的一行。

如果读过 0005/0008：它们要求在提交前把输出的 header/列顺序与输入对比；第 24 步手里已经有 11 列的输入帧和 10 列的输出帧，一次 `list(df_out.columns) == list(df_in.columns)` 就会失败并阻止提交，修复也只是 `out_df = df_in.copy()` 后只改 6 列。题面自己写了 "matches the input csv"，所以这同时也是模型的自检纪律问题；技能的价值在于把它变成显式的 header diff 检查。既然库里有直接对症的技能而模型一条都没读，按五选一口径归到"技能未触发"。

## 4. 结论
- 主因: skill_not_triggered
- 依据: 几何五项全过（判分重跑打印 `rects=0`、无重叠、单连通、IoU 达标），唯一失败是输出 CSV 少一列（(32,10) 对 (32,11)）；库里的 `bank/merge/0005` 第 21 步、`bank/merge/0008` 第 12 步明确要求提交前用 parser/header diff 校验输出 schema，模型一条都没读，而且在 demo 上就有 11 列输入对 10 列输出的现成证据没被比较。不是技能内容错（内容对症），也不是模型做不出（同模型 a01 通过、a02 的几何全过），而是应读未读加自检缺了列名对比这一步。

## 5. 证据

- step 1: 初始 prompt，含任务文本、`<available_skills>`（21 条）和 /app 终端状态；没有任何技能被读取。
- step 3–4: `mobile_sam`/PIL 未安装，模型补装依赖（题面称这些包已安装，实际没有）。
- step 8–11: `git clone MobileSAM` 成功，`weights/mobile_sam.pt` 直接可用。
- step 16: 第一版脚本用显式 dict 逐行构造输出；此后第 22/26/31/34/42/47/49/52/57 步的每次重写都保留同一套 10 列 `col_order`。
- step 18–21: 修 timm 缺失、`cv2.morphologyOps` → `cv2.morphologyEx`。
- step 24: 输出 CSV header 为 10 个命名列；同一命令读入输入后只打印 `Input rows: 48 / Output rows: 48` 和两者的 `type` 分布，没有打印/比较列名。
- step 36: 48/48 全 polyline，点数 6–16，0 矩形。
- step 38–59: 重叠处理迭代（3 对 → 0），最终用最近质心分区 + `CHAIN_APPROX_NONE` 精确描边。
- step 57: 最终脚本 `col_order` 10 列 + `out_df.to_csv(args.output_path, index=False)`（该脚本即判分器重跑的那一版）。
- step 60: 自检 `Overlapping pairs: 0 / Multi-component: 0 / Rectangles: 0 / Points: min=54, max=174 / Total masks: 48`；step 61 提交。
- 判分器: 8 passed / 1 failed；`test_csv_shape_cols` 报 `got (32, 10), expected (32, 11)`；判分重跑脚本的 stdout 为 `Points: min=72, max=291, rects=0`。
- 输入列证据：`demo_metadata.csv` 为 11 列（`Unnamed: 0` + 10 个命名列），a00 的 step 87 在同一份文件上打印过 `Input shape: (48, 11)` 和完整列名；a02 的输出 header 只有 10 个命名列。
- 步号按 trial dump（[1] 是初始 prompt，60 个 agent 动作对应 [2]–[61]）。

```json
{"trial": "sam-cell-seg/a02", "run_id": "ed_sam-cell-seg_4e3fe3b550_a02", "reward": 0, "steps": 60,
 "skills_read": [], "candidate_skills": ["bank/merge/0005", "bank/merge/0008", "bank/merge/0002"], "verdict": "skill_not_triggered", "main_cause": "输出 CSV 用固定 10 列 col_order + to_csv(index=False) 构造，丢掉输入索引列 Unnamed: 0，判分 (32,10) 对 (32,11)；库里的 CSV schema 校验技能未读", "notes": "几何五项全过（rects=0），只差一列；0005/0008 明确要求提交前用 header diff 校验输出 schema，模型一条技能都没读，且在 demo 上就能发现 11 列对 10 列。"}
```
