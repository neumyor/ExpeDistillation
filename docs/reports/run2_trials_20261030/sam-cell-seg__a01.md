# sam-cell-seg / attempt 1（失败）

- trial：`traj/tb:sam-cell-seg/strong/01`；步数 35；读取的技能：`bank/merge/0006`、`bank/merge/0004`
- 主因：A_model_capability（同分布内波动）

## 1. 为什么失败 / 卡在哪里

- 卡点：判分阶段唯一失败项 `test_mask_alignment`。本 trial 的 pipeline 在可见步骤里运行正常（第 28 步脚本跑通 48 行、覆盖率 38719 像素、重叠 0），卡点不在某一步的显式错误，而在输出多边形相对**隐藏参考**的逐单元保真度：列表顺序上第一个不达标的单元 IoU = 0.49353049907578556，差 0.5 阈值 0.0065。
- 过程：
  - 第 0 步：`/app` 只有 `demo_metadata.csv`（48 行数据，含 rectangle/polyline 两类）与 `demo_rgb.png`（374×355）；`mobile_sam` 未安装，torch/cv2/pandas 等也没有。
  - 第 1 步读 `bank/merge/0006`；第 2–3 步探测 pip 镜像与本地是否已有模型权重（没有）。
  - 第 4–10 步 `pip install` 装 torch/torchvision/opencv/pandas 等（大包，下载约数分钟）。
  - 第 11–16 步 `git clone` MobileSAM 中途被 Ctrl-C，改用源码 tar 包下载解压；导入时报 `No module named 'timm'`（第 15 步）。
  - 第 17–26 步被抽样隐去（策略/掩码选择的实现细节不可见）。
  - 第 27 步：清掉演示输出，`py_compile` 通过，并检查脚本里没有硬编码 `demo_metadata.csv`/`demo_rgb.png`/权重路径（`NO_DEMO_HARDCODE`）。
  - 第 28–32 步：在演示数据上跑脚本，校验 schema、48 行、覆盖率 38719 像素、重叠 0；修了输出 CSV 首列行号列丢失的问题（第 30–32 步），最终打印 `FINAL_VALIDATION_OK rows=48 covered=38719 overlap=0`。
  - 第 33–34 步提交。
- 直接原因：判分器 9 项测试 8 过 1 挂。通过的有：脚本存在、`test_run_script`（实际运行 agent 脚本，14.6 s，"MobileSAM refinement" 32 次迭代）、输出 CSV 存在与列形状、掩码不再是矩形、折线互不重叠、每格单连通、坐标是扁平列表。唯一失败的是 `test_mask_alignment`（test_outputs.py:221）：把输出多边形与 `/app/correct_output.csv` 的参考折线逐行算 `calculate_iou`，`assert iou >= 0.5` 失败，`IoU is too low: 0.49353049907578556`。`correct_output.csv` 不在 agent 解题时的 `/app` 里（第 0 步列目录只有两个输入文件），是判分阶段注入的，因此解题过程中无法对这一指标做任何校准。

### 与同题成功 attempt 的差异

- `successes/sam-cell-seg/a00`（34 步，读取 `bank/merge/0006`）：第 28 步明确做了两处与保真度有关的改动——`cv2.findContours(..., cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)` 改为 `cv2.CHAIN_APPROX_NONE`，并删掉 `approxPolyDP`（直接 `contour.reshape(-1, 2)`），即用**稠密轮廓**出多边形；第 31 步的验证打印 `min_points 56`、无空、无断连、无 bbox 不符、无重叠，通过。
- `successes/sam-cell-seg/a02`（36 步，读取 `bank/merge/0006`）：走的是另一套实现（仓库放 `/opt/MobileSAM`，第 30–33 步还特意 `pip uninstall timm` 后重跑，证明脚本不依赖 timm），输出 `MIN_POINTS 31`，同样通过。
- 差别一句话：a00 与 a02 是**同一思路的两个不同实现**（轮廓稠密度、掩码选择细节不同），一个 56 点、一个 31 点，都过；a01 的可见部分与它们同族（同样的 MobileSAM 加载、同一套结构自检、同样无硬编码），差别只在被抽样隐去的第 17–26 步里的掩码/多边形细节，而 IoU 恰好落在阈值下方 0.0065——没有任何一处系统性缺陷的可见证据（8/9 项断言全过、脚本通用、CPU 推理确定性、无网络依赖）。

## 2. 技能库里有没有能解决它的技能

- 候选：
  - `bank/merge/0006`（相关性 高，已读）——"获取/安装/功能验证"通用卡；本 trial 的环境搭建部分（装 torch、拉 MobileSAM、补 timm）确实由它支撑并成功。
  - `bank/merge/0004`（相关性 中，已读）——提交前门禁：路径、schema、数量、端到端行为；本 trial 第 27–32 步做的正是它的检查项。它第 4 步明说"无参考时就跳过精确参考比对，改验 presence/schema/constraints/behavior"——本案正是"无参考"情形。
  - `bank/merge/0001`（相关性 中-低）——基线 + 单因素变体 + evaluator 打分；本任务在解题时没有可见 evaluator，只有 SAM 自身的 mask score 可用，不完全适配。
  - `traj/tb:path-tracing/strong/00/seg00`、`.../seg01`（相关性 低）——图像/像素级复现与逐像素比对；没有参考图/参考多边形时用不上。
  - 其余成员（视频 OCR、数据清点、异步、SQLite/二进制、DNA、HTML 过滤、R 环境、调度器、源码构建的 C 分支）不相关。
- 缺的能力：**掩码质量与多边形保真**——在无参考下判断 SAM 掩码选得对不对（多提示/多掩码一致性、与矩形框的贴合、面积/连通先验）、以及把掩码转成尽量贴合边界的多边形（稠密轮廓、去近似）。库中没有任何成员覆盖分割质量这一类。

## 3. 能解决问题的技能被触发了吗？为什么没解决

- 触发：读了 `bank/merge/0006` 与 `bank/merge/0004`，且都照做了——0006 支撑了环境搭建（第 4–16 步），0004 的门禁被完整执行（第 27–32 步：`py_compile`、无硬编码检查、schema/行数/覆盖/重叠/连通性逐项校验）。属"读了也照做了"，但仍不够。
- 原因：两张卡都不提供"掩码/多边形质量"的知识，而题面与运行环境不提供参考多边形；0004 第 4 步明确说无参考时只验 presence/schema/constraints/behavior，agent 也正是这么做的。技能能保证"结构对"，保证不了"IoU 过线"。

## 4. 结论与依据

- 主因：A_model_capability
- 一句话依据：**同分布内波动**——9 项测试 8 过，唯一失败项是与隐藏参考的 IoU 0.4935 < 0.5（差 0.0065）；同题 a00（第 28 步改稠密轮廓，min_points 56）与 a02（另一变体，MIN_POINTS 31）都通过，三者共用同族管线，差别只在每次尝试自己写的掩码/多边形细节，且解题时看不到参考 CSV，任何一次尝试都无法校准这个指标。
- 次要因素：D（库里没有分割质量/多边形保真类技能）；可见性保留——a01 的第 17–26 步被抽样隐去，不能完全排除其中存在某个系统性设计差异（例如未做稠密轮廓），但现有证据（其余 8 项断言全过、成功者内部差异达 56 vs 31 点）更支持"边界波动"而非"能力缺失"。
