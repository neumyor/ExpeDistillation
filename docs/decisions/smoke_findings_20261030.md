日期：2026-10-30
一句话：用假模型在真实 SearchQA 插件上把主方法七个相位完整跑通，发现并修复三处缺陷，另有几处未修复的观察留给用户裁定。
对应 log.md：2026-10-30 20:12「论文实验代码」条目

## 做法

假模型提供者（`llm/fake_provider.py`）扩展到主方法的全部 prompt（触发族聚类、考场、逐 trial 审阅、
需求写作、两个技能使用裁判、带范围的改写），在真实 SearchQA 插件与真实评分器上，用 4 道训练题、
4 道测试题、每题 2 次采样，跑通 `searchqa_split_paper_e1_ours` 的七个相位；再跑 e3、e4、e2 的各个
变体，确认每个开关真的生效；再做重跑、删最后一个相位摘要后续跑、`--until-phase` 分段、
五个注入的中途崩溃，结果都与一次跑完逐字节一致。零 API 调用。
产物在 `tmp/smoke_searchqa/`（不进 git）。

## 修复的缺陷

1. **逐 trial 审阅在没有失败 trial 时不产生任何决定。** `trial_level` 相位里全部 trial 通过时，
   聚合没有输入，任务清单为空，待定的技能既不被接受也不被删除，永远到不了终测。TB 的真实 run
   每轮都有失败，掩盖了这一点；换到成功率高的 benchmark 或小任务集就会出现。修复：没有任何发现时
   不调用模型，对每个被测量的技能给出缺省的保留（`methods/review/aggregation.py`）；所有失败 trial
   的分析都出错时仍然不做决定，等续跑重试（`trial_review.py`）。
2. **`skill_usage` 把第一步当作初始提示跳过。** 假定存储的轨迹第一步是 TB 的初始提示；本地
   benchmark 的第一步就是模型的第一个动作。修复了 `first_agent_step`。
3. **考试裁判看不到设计文档说它看到的东西。** 读代码确认：`exam_once` 调用裁判时没有传目录，
   选择理由写死为空，"已打开技能"传的是 `cat <路径>` 命令而不是技能文本。裁判的 prompt
   要求它判断计划是否"在适用处遵循了已打开的技能"，却看不到技能内容、可选的库、也看不到作答者
   给的理由。修复：`Attempt` 新增可选字段 `selection_reason`（带缺省值，不升 schema），
   作答时写入；裁判拿到目录文本、所开文件及其正文、选择理由（`exam_once`、`exam_grading`、
   `exam_answering`）。**影响**：此前 TB 上所有考试（P03/P05 的通过率）是在裁判缺这三项输入时
   得到的，不能与修复后的结果比较；新 run 重新得到。
4. **考试路径的改写没有用到累积的弱模型画像。** `trial_level` 的设计是画像供所有写作与改写使用
   （实战路径已做到），考试路径的 `learn_exam` 却取配置里的画像（`use_weak_profile` 为假时为中性
   段落），所以学到的"弱模型是谁"只进了新需求技能，没进改写。修复：`phase_learn` 把本相位的画像
   交给 `execute_exam_rows`，`_land_revise` 优先使用它。

每处都有回归测试。

## 未修复，需要裁定

- **`trial_level` 对"测不了"的单元缺省保留，`skill_level` 删除。** 两种审阅的行为不一致；
  `trial_level` 是论文的方法。要统一就要决定谁的规则对。
- **考场的目录头里写着 "cat only the skill file ..."。** 目录文本来自 TB 的文件夹交付，进入考场的
  `EXAM_TRIGGER` 时对 ALFWorld 与 SearchQA 的措辞不贴切（那里读取是 `ReadSkill[...]`）。
  考场只让作答者选卡，读取是程序做的，影响应当很小，但属于措辞残留。
- **写作者与审阅者拿到的"任务说明"是整份执行提示词**（`task_instruction_for`），SearchQA 上包含
  空的 "Transcript so far" 段。不影响正确性，增加噪声。
- **学习提示词里仍有少量未槽化的 shell 措辞**：`SKILL_DIAGNOSE` 与归因模板里的
  "unread error before retry"、"A script may appear only as its input ..."。改动会改变 prompt
  digest，所以没有动，等用户决定。

## 更新（2026-10-30 深夜）

**ALFWorld 的房间描述不在存储的步骤里**这一条已在真实 ALFWorld 上确认：轨迹步骤只有动作与
之后的观测，重置时的房间描述没有落盘。已在正式论文 run 之前修复，做法与同一次冒烟暴露的
answer sheet 文件名超长问题一起记在 `alfworld_scene_context_20261030.md`。上面其余各条仍然
有效。
