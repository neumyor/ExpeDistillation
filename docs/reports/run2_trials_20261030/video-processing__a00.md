# video-processing / attempt 0（失败）

- trial：`traj/tb:video-processing/strong/00`；步数 52；读取的技能：`traj/tb:cancel-async-tasks/strong/00/seg00/skill`
- 主因：A_model_capability

## 1. 为什么失败 / 卡在哪里
- 卡点：第 47–48 步。第 47 步把状态机参数改成 `TH=30, GROUND=350` 后得到 `55 62`，第 48 步把这组常量写进 `/app/jump_analyzer.py`（`MOTION_THRESHOLD = 30`、`GROUND_Y = 350` 等），第 49 步复跑确认后直接提交。
- 过程：
  - 第 0–2 步：`/app` 只有 `example_video.mp4`；视频 30 fps、120 帧、960×540。
  - 第 3–4 步：以首帧为背景做逐帧差分，阈值 20/40/80，观察运动区域随选手移动。
  - 第 13–31 步：试了饱和色连通域、分位数阈值、中值背景、光流和 ASCII 掩码；第 44 步状态机给出 `GROUND360 no-window 64 None`，即 takeoff 64、landing 未找到。
  - 第 46–47 步：按帧统计运动底部 y 与低区像素，改为 `GROUND=350, TH=30` 并加中值模糊，得到 `55 62`。
  - 第 48–49 步：写最终脚本，`py_compile` 通过；运行输出 `takeoff=55 landing=62`，`/app/output.toml` 写好；复跑同结果，打印 `SHELL_OK`。
  - 第 50–51 步：两次 `mark_task_complete` 提交。
- 直接原因：判分器对示例视频要求 takeoff∈[50,54]、landing∈[62,64]，对隐藏测试视频要求 takeoff∈[219,223]、landing∈[231,234]（`tests/test_outputs.py` 的 `test_jump_analyzer_example_video` / `test_jump_analyzer_test_video`）。本 trial 示例输出 55/62：landing 在区间内，takeoff 55 比上界晚一帧；隐藏视频输出 takeoff 224，比 [219,223] 上界晚一帧（ctrf 原文 `Takeoff frame 224 not within inclusive range [219, 223]`）。5 个测试中 3 个通过（两个视频存在、imports），失败的只有两段数字区间。

## 2. 技能库里有没有能解决它的技能
- 候选：
  - `bank/merge/0000`（相关性 低）——唯一的视频类成员，但 summary 是 "Extract ordered text, commands, or moves from video or image sequences with ffmpeg and tesseract"；trigger 要求"从视频帧读出有序文本/命令/动作"，没有运动估计、人体/地面接触或帧号定位。
  - `bank/merge/0004`（相关性 中）——提交前门禁：核对精确路径、schema、数量、独立参考、端到端行为；能保证 `/app/output.toml` 的格式与重跑，但没有帧号真值可对照，不能发现 55 与 54 的差别。
  - `bank/merge/0001`（相关性 低-中）——"基线/单因素变体/用 evaluator 打分"的改进协议；本任务没有可见 evaluator，隐藏判分器不可访问，无法实施它的打分循环。
  - `traj/tb:llm-inference-batching-scheduler/strong/00/seg03`（相关性 低-中）——"逐步打印、逐个核对目标、从干净状态复跑"的调参卡；能提醒做参数扫描，但不含"起跳/落地如何定义"的领域知识。
  - 其余 12 个成员（0002、0003、0005、0006、cancel-async weak seg02、db-wal 两条、dna-insert、filter-js、path-tracing 两条、llm-inference seg00）与视频帧事件定位无关，不展开。
- 缺的能力：固定机位下的人体运动测量——把选手从静态背景中分割出来，锚定地面线，跟踪脚/身体相对地面的位置，把 takeoff 定义为"离地前最后一帧触地"、landing 定义为"腾空后第一帧触地"，并用物理合理性（腾空约 0.3–0.5 s、只出现一次飞行段）在无标注视频上自检。库中没有任何成员覆盖这一类。

## 3. 能解决问题的技能被触发了吗？为什么没解决
- 触发：读了但内容不足——本 trial 唯一读取的是 `traj/tb:cancel-async-tasks/strong/00/seg00/skill`，它不是能解决本题的技能；库里也没有真正对口的视频运动测量卡。
- 原因：该卡 summary 是 "Implement and validate a standalone Python module from scratch"，trigger 只要求"空工作区 + 写自包含 Python 模块 + 能 py_compile/冒烟"。题面确实满足这个形状，所以它被触发并被照做（heredoc 写文件、`py_compile`、`python3 /app/jump_analyzer.py /app/example_video.mp4`、复跑）；第 7 步终端里还能看到该卡最后一行 "…instead of writing and validating a standalone script"。但卡的 7 步里没有一步涉及视频、帧、运动或"如何定义起跳"；第 5 步要求 "verify SMOKE_OK and the expected output appear"，而题面没有给出示例视频的期望帧号，模型就把自己的 55/62 当成 "expected output" 验收，形成假阳性。第 6 步的边界例子是 cancellation / HTML / distributed shapes，与本题无关。
- 未读的候选：`bank/merge/0004` 的 trigger（"before sending any completion confirmation when a task produced or changed a deliverable that will be graded on disk or by execution"）在提交前其实覆盖了本 trial，但模型没有读；即使读了，它也只能加强重跑/格式检查，不能给出帧号真值。

## 4. 结论与依据
- 主因：A_model_capability
- 一句话依据：用"地面带运动间隙"状态机在示例视频得到 takeoff 55（比 [50,54] 上界晚一帧）、隐藏视频得到 224（比 [219,223] 上界晚一帧）；模型没有测脚-地面接触，也没有任何真值校准，就在第 49 步复跑通过后提交。
- 次要因素：技能库没有视频运动/接触测量类技能（D）；唯一被读的模块写作卡在"expected output"缺位时把自测变成自证（B 的内容缺口）；题面用 "takeoff/jump begins" 表述，最后一帧触地与首帧腾空的约定本身有一帧歧义，但判分窗口给了 5 帧容差，模型仍未落进窗口。
