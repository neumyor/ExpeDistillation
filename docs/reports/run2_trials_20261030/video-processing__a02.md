# video-processing / attempt 2（失败）

- trial：`traj/tb:video-processing/strong/02`；步数 108；读取的技能：`traj/tb:cancel-async-tasks/strong/00/seg00/skill`
- 主因：A_model_capability

## 1. 为什么失败 / 卡在哪里
- 卡点：第 103 步的补丁——它给 takeoff 选择加了一个 warmup 阈值，把示例视频的 takeoff 从 47 推到 55；第 105 步只校验了 TOML 的两个字段和整数类型，未对数字做外部校验；第 106–107 步提交。
- 过程：
  - 第 0–1 步：cv2 4.12.0；视频 30 fps、120 帧、960×540。
  - 第 2–12 步：首帧/逐帧差分、ASCII 帧、中值背景（存 `/tmp/bg_med.npy`）。
  - 第 20–44 步：光流、KNN/MOG2 背景减除、HOG 行人检测、组件解析。
  - 第 52–92 步：掩码 ASCII、静态边缘、首差分跟踪、KNN 人体组件、块跟踪。
  - 第 100 步：写第一版 `jump_analyzer.py`（`HURDLE_X = 474`，注释 "same camera/background/hurdle geometry in the task videos"），输出 47/61。
  - 第 103–105 步：加 warmup 阈值（"A real jump is preceded by several ground-running frames, is near the static hurdle, and is followed by a sustained rise"），takeoff 变 55；`py_compile`、`tomllib` 校验两个字段均为整数、复跑 55/61；第 106–107 步提交。
- 直接原因：判分器对示例视频要求 takeoff∈[50,54]、landing∈[62,64]；本 trial 示例输出 55/61——takeoff 比上界晚一帧，landing 比下界早一帧，示例测试失败。隐藏测试视频输出 takeoff 224，比 [219,223] 上界晚一帧（ctrf 原文 `Takeoff frame 224 not within inclusive range [219, 223]`）。5 个测试中 3 个通过（两个视频存在、imports），失败的只有两段数字区间；第 103 步的补丁只是把 47 调到 55 的假设性调整，landing 仍停在 61。

## 2. 技能库里有没有能解决它的技能
- 候选：
  - `bank/merge/0000`（相关性 低）——唯一的视频类成员，但 summary 是 "Extract ordered text, commands, or moves from video or image sequences with ffmpeg and tesseract"；trigger 要求"从视频帧读出有序文本/命令/动作"，没有运动估计、人体/地面接触或帧号定位。
  - `bank/merge/0004`（相关性 中）——提交前门禁：核对精确路径、schema、数量、独立参考、端到端行为；本 trial 第 105 步其实做了路径/schema/整数校验，但门禁没有帧号真值可对照，不能发现 55 与 54 的差别。
  - `bank/merge/0001`（相关性 低-中）——"基线/单因素变体/用 evaluator 打分"的改进协议；本任务没有可见 evaluator，隐藏判分器不可访问，无法实施它的打分循环。
  - `traj/tb:llm-inference-batching-scheduler/strong/00/seg03`（相关性 低-中）——"逐步打印、逐个核对目标、从干净状态复跑"的调参卡；能提醒做参数扫描和结构性变体，但不含"起跳/落地如何定义"的领域知识。
  - `traj/tb:path-tracing/strong/00/seg01`（相关性 低）——"图像表征 + 像素级验证"，本 trial 大量做 ASCII/掩码检查，方法上有重叠；但它面向静态图像和不透明可执行物，不涉及帧间运动与事件边界。
  - 其余 11 个成员（0002、0003、0005、0006、cancel-async weak seg02、db-wal 两条、dna-insert、filter-js、path-tracing seg00、llm-inference seg00）与视频帧事件定位无关，不展开。
- 缺的能力：固定机位下的人体运动测量——把选手从静态背景中分割出来，锚定地面线，跟踪脚/身体相对地面的位置，把 takeoff 定义为"离地前最后一帧触地"、landing 定义为"腾空后第一帧触地"，并用物理合理性（腾空约 0.3–0.5 s、只出现一次飞行段）在无标注视频上自检；还缺"同一规则在不同时长视频上给出一致边界"的跨视频检查。库中没有任何成员覆盖这一类。

## 3. 能解决问题的技能被触发了吗？为什么没解决
- 触发：读了但内容不足——meta 记录本 trial 读了 `traj/tb:cancel-async-tasks/strong/00/seg00/skill`（采样轨迹里没有 cat 步，但后续的 heredoc 写文件、`py_compile`、运行/复跑与卡的第 2/3/5/7 步一致）；它不是能解决本题的技能，库里也没有真正对口的视频运动测量卡。
- 原因：该卡 summary 是 "Implement and validate a standalone Python module from scratch"，trigger 只要求"空工作区 + 写自包含 Python 模块 + 能 py_compile/冒烟"。题面确实满足这个形状，所以它被触发并被照做。但卡的 7 步里没有一步涉及视频、帧、运动或"如何定义起跳"；第 5 步要求 "verify SMOKE_OK and the expected output appear"，题面没有给出示例视频的期望帧号，模型就把自己的 55/61 当成 "expected output" 验收；第 105 步的 `tomllib` 检查只证明两个字段存在且是整数，证明不了数字对，形成假阳性。
- 未读的候选：`bank/merge/0004` 的 trigger（"before sending any completion confirmation when a task produced or changed a deliverable that will be graded on disk or by execution"）在提交前其实覆盖了本 trial，但模型没有读；即使读了，它也只能加强重跑/格式检查，不能给出帧号真值。

## 4. 结论与依据
- 主因：A_model_capability
- 一句话依据：108 步里试了 KNN/MOG2、光流、HOG、块跟踪，最终仍在示例视频输出 55/61（takeoff 比 [50,54] 晚一帧、landing 比 [62,64] 早一帧），隐藏视频 224；第 103 步的 warmup 补丁只是把 47 推到 55 的假设调整，不是触地测量。
- 次要因素：技能库没有视频运动/接触测量类技能（D）；唯一被读的模块写作卡在"expected output"缺位时把自测变成自证（B 的内容缺口）；同一 warmup/高度阈值规则只在 120 帧的示例上定过，隐藏视频更长，起跳与落地的边界仍各差一帧（A 的泛化问题）。
