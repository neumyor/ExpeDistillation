# video-processing / attempt 1（失败）

- trial：`traj/tb:video-processing/strong/01`；步数 56；读取的技能：`traj/tb:cancel-async-tasks/strong/00/seg00/skill`
- 主因：A_model_capability

## 1. 为什么失败 / 卡在哪里
- 卡点：第 50 步——最终版光流脚本 `jump_analyzer.py` 写成，第 51 步首次给出 `jump_takeoff_frame_number = 55`、`jump_land_frame_number = 61`，第 52–53 步两次复跑同结果，第 54–55 步提交。
- 过程：
  - 第 0–1 步：视频 30 fps、120 帧、960×540。
  - 第 2–13 步：中值背景、连通域、边缘/ASCII 图；第 3 步 `NameError: frame`、第 4 步 cv2 `Bad argument`，均当场修复。
  - 第 4 步：`cat` 了 `/opt/openclaw-skills/.../SKILL.md`，读了 cancel-async 的模块写作卡（唯一读的技能）。
  - 第 22–28 步：Farneback 光流，阈值 2/3/4 px，写 `/tmp/flow_diag.txt`，看到 47–75 帧的大运动组件。
  - 第 49–50 步：第 49 步用光流原型调边界，第 50 步写最终脚本——"the jump is the first extended runner-shaped motion segment, the first short/high component in that segment is the takeoff, and the next ground-height component is the landing"；内部诊断显示 `best_run [48,49,50,51,52,53]`。
  - 第 51–53 步：运行得到 55/61，两次复跑同结果；第 54–55 步提交。
- 直接原因：判分器对示例视频要求 takeoff∈[50,54]、landing∈[62,64]；本 trial 示例输出 55/61——takeoff 比上界晚一帧，landing 比下界早一帧，示例测试失败。隐藏测试视频输出 takeoff 227，比 [219,223] 上界晚 4–8 帧（ctrf 原文 `Takeoff frame 227 not within inclusive range [219, 223]`）。5 个测试中 3 个通过（两个视频存在、imports），失败的只有两段数字区间；光流边界在更长的隐藏视频上漂移更大。

## 2. 技能库里有没有能解决它的技能
- 候选：
  - `bank/merge/0000`（相关性 低）——唯一的视频类成员，但 summary 是 "Extract ordered text, commands, or moves from video or image sequences with ffmpeg and tesseract"；trigger 要求"从视频帧读出有序文本/命令/动作"，没有运动估计、人体/地面接触或帧号定位。
  - `bank/merge/0004`（相关性 中）——提交前门禁：核对精确路径、schema、数量、独立参考、端到端行为；能保证 `/app/output.toml` 的格式与重跑，但没有帧号真值可对照，不能发现 55 与 54 的差别。
  - `bank/merge/0001`（相关性 低-中）——"基线/单因素变体/用 evaluator 打分"的改进协议；本任务没有可见 evaluator，隐藏判分器不可访问，无法实施它的打分循环。
  - `traj/tb:llm-inference-batching-scheduler/strong/00/seg03`（相关性 低-中）——"逐步打印、逐个核对目标、从干净状态复跑"的调参卡；能提醒做参数扫描和结构性变体，但不含"起跳/落地如何定义"的领域知识。
  - 其余 12 个成员（0002、0003、0005、0006、cancel-async weak seg02、db-wal 两条、dna-insert、filter-js、path-tracing 两条、llm-inference seg00）与视频帧事件定位无关，不展开。
- 缺的能力：固定机位下的人体运动测量——把选手从静态背景中分割出来，锚定地面线，跟踪脚/身体相对地面的位置，把 takeoff 定义为"离地前最后一帧触地"、landing 定义为"腾空后第一帧触地"，并用物理合理性（腾空约 0.3–0.5 s、只出现一次飞行段）在无标注视频上自检；还缺"同一规则在两个不同时长视频上给出一致边界"的跨视频检查。库中没有任何成员覆盖这一类。

## 3. 能解决问题的技能被触发了吗？为什么没解决
- 触发：读了但内容不足——第 4 步 `cat` 了 `traj/tb:cancel-async-tasks/strong/00/seg00/skill`，它是本 trial 唯一读取的技能，但不是能解决本题的技能；库里也没有真正对口的视频运动测量卡。
- 原因：该卡 summary 是 "Implement and validate a standalone Python module from scratch"，trigger 只要求"空工作区 + 写自包含 Python 模块 + 能 py_compile/冒烟"。题面确实满足这个形状，所以它被触发并被照做（heredoc 写文件、`py_compile`、`python3 /app/jump_analyzer.py /app/example_video.mp4`、复跑）。但卡的 7 步里没有一步涉及视频、帧、运动或"如何定义起跳"；第 4 步专门讲 "When importing a third-party package fails with ModuleNotFoundError, do not try to install it"，而本题 cv2 已可用，整条用不上；第 5 步要求 "verify SMOKE_OK and the expected output appear"，题面没有给出示例视频的期望帧号，模型就把自己的 55/61 当成 "expected output" 验收，形成假阳性。
- 未读的候选：`bank/merge/0004` 的 trigger（"before sending any completion confirmation when a task produced or changed a deliverable that will be graded on disk or by execution"）在提交前其实覆盖了本 trial，但模型没有读；即使读了，它也只能加强重跑/格式检查，不能给出帧号真值。

## 4. 结论与依据
- 主因：A_model_capability
- 一句话依据：光流最大组件法在示例视频给出 55/61（takeoff 比 [50,54] 晚一帧、landing 比 [62,64] 早一帧），在更长的隐藏视频上 takeoff 漂到 227（[219,223] 外 4–8 帧）；模型把运动段边界当成起跳/落地，没有测触地，也没有做跨视频一致性检查。
- 次要因素：技能库没有视频运动/接触测量类技能（D）；唯一被读的模块写作卡在"expected output"缺位时把自测变成自证（B 的内容缺口）；光流阈值只在 120 帧的示例上试过，隐藏视频更长，边界随阈值漂移（A 的泛化问题）。
