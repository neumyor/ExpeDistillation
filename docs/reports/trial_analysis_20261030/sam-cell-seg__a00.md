# sam-cell-seg / attempt 0
- run_id: ed_sam-cell-seg_4e3fe3b550_a00
- reward: 0
- steps: 135
- skills_read: []

## 1. 为什么失败（卡点）

卡点在"MobileSAM 对部分细胞返回全零 mask 时用什么兜底"。模型第 128 步的兜底是把原始 bbox 的边"细分加点"成折线（`subdivide bbox into polyline to avoid rect-only`），但这在几何上仍是四角矩形；判分器 `test_masks_are_no_longer_rect` 在第一条这样的行上失败：`is_rectangle_shapely([(99, 0), (99, 367), (115, 367), (115, 0)])` 为真，`test_mask_alignment` 报 `IoU is too low: 0.13676560276478958`（阈值 0.5）。九项判分 7 过 2 挂，挂的正是"非矩形"和"与参考 mask 对齐"这两项。

过程：第 3 步用 pip 装 MobileSAM（包内不带权重），第 6–12 步补装 torch/timm 等依赖；第 19–84 步基本都耗在找权重上——第一次 wget 把 GitHub 用户名拼成了 `ChaونغZhang` 拿到 0 字节，之后 HuggingFace 报 `[Errno 101] Network is unreachable`，Google Drive 被反爬挡成空文件，直到第 84 步才从 `.../raw/master/weights/mobile_sam.pt` 拿到 40 MB 权重（第 85 步 `torch.load` 成功）。第 86 步第一次跑通 48/48 后，第 88–131 步反复改重叠处理：第 110 步仍有 19 个矩形 + 1982 个重叠像素，第 121 步降到 11 个矩形 + 36 个重叠像素。第 126–127 步确认根因：部分细胞的三个 SAM 候选 mask 分数都 >0.86 但像素全为 0；第 128 步改用 bbox 细分兜底。第 132 步自述 `37/48 masks with >4 pts (remaining 11 using bbox subdivision fallback)`，第 135 步仍按"点数 >4"判定为折线，第 136 步提交。

直接原因：自检口径错误——"点多于 4"不能排除矩形（细分后的矩形仍有 4 个唯一角），而判分器按几何形状与 IoU 判定；模型自己已经报告有 11 个 mask 是 bbox 兜底，仍按部分通过提交。

## 2. 技能库是否有能解决它的技能

没有能解决它的技能。交付文件夹里 21 个 `SKILL.md`（bank artifact 第 22 条 `traj/tb:caffe-cifar-10/strong/00/seg02/skill` 为 pending，未物化）逐个检索 `segment`/`SAM`/`mask`/`polygon`/`contour`/`opencv`，没有任何技能讲图像分割、SAM 推理、mask 转多边形或轮廓提取；唯一带 "image" 的 0000 是视频/图片序列的 OCR 转录，0009 第 2 步只是图像属性画像，都不是分割。

最接近的通用技能，但都不能解决几何问题：

- `bank/merge/0008`（提交前验证）："never mark complete on a partial pass"、"If any check returns a non-zero exit code, a stack trace, or a failed assertion, stop and debug that specific failure." 它能拦住第 136 步的提交（模型自己已经报告 11 个矩形兜底），但不会告诉模型怎么给 SAM 全零的格子做出 mask。
- `bank/merge/0002`（evaluator 驱动优化）：第 10 步 "Before treating a variant as the winner, validate it independently with the task checker, an exact output comparison, or a separate similarity script." 同样只能推动独立复核。
- `bank/merge/0009`（陌生输入/失败产物）：第 5 步 "For a network fetch, bound it before the full transfer: timeout, HTTP status, content length, redirects." 最多能缩短第 19–84 步的权重绕路、释放步数预算。
- `bank/merge/0005`（CSV/产物校验）：本题的 CSV 形状检查（`test_csv_shape_cols`）反而是通过的，与卡点无关。

缺失的能力：SAM box prompt → mask → 轮廓 → 多边形；mask 全零/退化时的替代策略（例如对全体 mask 并集做最近质心分区、再用 `CHAIN_APPROX_NONE` 精确描边——同题 a02 第 52–59 步走通的正是这条路）；以及"非矩形"必须用唯一角数/IoU 验证而不是点数。

## 3. 能解决问题的技能被触发了吗

没有触发，而且一条都没读：`phase_summary.json` 的 `skill_files_read["traj/tb:sam-cell-seg/weak/00"]` 为空；我又扫了整条轨迹的全部步骤，`skill`/`catalog`/`openclaw` 只出现在第 1 步的 prompt 里，没有任何 `cat SKILL.md` 或 `catalog.md`，也没有按 prompt 要求写"none matches"的一行。

即便读过 0008/0002，最多把"提交"改成"继续改"（考虑到第 19–84 步已烧掉一半预算，甚至可能跑不完），并不会给出缺失的分割做法；因此这次失败不是"应读未读"造成的。技能库里没有 SAM/分割类技能属于结构性覆盖缺口，是次要因素。

## 4. 结论
- 主因: model_capability_insufficient
- 依据: 模型在第 126–128 步已经定位到"SAM 对部分细胞返回全零 mask"，却用 bbox 细分兜底并把它当成折线；第 132 步自己报告还有 11 个这样的 mask，第 136 步仍提交，判分器随即以矩形和 IoU 0.137 判负。同题同模型的 a01（轨迹 36 步，未打开任何技能）通过了全部 9 项，a02 的分割几何也全部通过（判分重跑打印 `rects=0`），说明任务在模型能力范围内；这次失败是流水线设计与自检口径的问题，不是技能库能补的知识缺口（库缺 SAM/分割类技能列为次要因素）。第 19–84 步的权重下载绕路消耗了约一半步数，是预算上的放大因素，但不是判分失败的直接原因（第 84 步已拿到可用权重）。

## 5. 证据

- step 1: 初始 prompt，含任务文本、`<available_skills>`（21 条）和 /app 终端状态；没有任何技能被读取。
- step 3: `pip install git+https://github.com/ChaoningZhang/MobileSAM.git`。
- step 6: `import mobile_sam` 因缺 torch 失败；step 7/9/11 依次补装 torch、pandas/opencv、timm。
- step 19: 第一次下载权重，GitHub 用户名拼错，得到 0 字节文件。
- step 48: 用正确的 `ChaoningZhang` 访问仓库返回 200；step 54 从 README 找到 Google Drive 链接。
- step 58–61: Google Drive 下载为 0 字节，`gdown` 报连接失败；step 70 HuggingFace 报 `[Errno 101] Network is unreachable`。
- step 84: 从 `.../raw/master/weights/mobile_sam.pt` 下载到 40 MB 权重；step 85 `torch.load` 成功（约 10M 参数）。
- step 86: 第一次在 demo 上跑通，48/48 全部输出 polyline。
- step 101: 自述 19 个 mask 仍是矩形，并观察到退化 mask（"mask 6 spans entire image height"）。
- step 110: 19 个矩形 + 1982 个重叠像素；step 121: 11 个矩形 + 36 个重叠像素。
- step 126–127: 定位到部分细胞三个 SAM 候选 mask 分数 >0.86 但像素全零。
- step 128: 兜底逻辑写为 "subdivide bbox into polyline to avoid rect-only"。
- step 132: `37/48 masks with >4 pts (remaining 11 using bbox subdivision fallback)`。
- step 135: 最终自检写下 ">4 pts means truly polyline not rectangle"，仍以点数判定折线；step 136 提交 `task_complete`。
- 判分器: 7 passed / 2 failed；`test_masks_are_no_longer_rect` 报 `[(99, 0), (99, 367), (115, 367), (115, 0)]` 是矩形；`test_mask_alignment` 报 `IoU is too low: 0.13676560276478958`；`test_csv_shape_cols` 通过（输出 11 列；demo 上首列为 `Unnamed: 0`）。
- 步号按 trial dump（[1] 是初始 prompt，135 个 agent 动作对应 [2]–[136]）。

```json
{"trial": "sam-cell-seg/a00", "run_id": "ed_sam-cell-seg_4e3fe3b550_a00", "reward": 0, "steps": 135,
 "skills_read": [], "candidate_skills": ["bank/merge/0008", "bank/merge/0002", "bank/merge/0009", "bank/merge/0005"], "verdict": "model_capability_insufficient", "main_cause": "SAM 对部分细胞返回全零 mask，模型用 bbox 细分兜底（仍是四角矩形），自检按点数>4 误判为折线并提交，判分器以矩形和 IoU 0.137 判负", "notes": "同模型 a01 未打开技能、轨迹 36 步通过，a02 几何全过，说明任务可解；库缺 SAM/分割类技能是次要因素，第 19–84 步的权重下载绕路放大了步数压力。"}
```
