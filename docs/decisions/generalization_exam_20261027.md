# 决定：用泛化考试替换旧 exam

日期：2026-10-27
一句话：`exam` 执行相位改为按技能版本构造文本考场，检查弱模型能否找到并正确使用技能。
对应 log.md：2026-10-27 21:53 的“用泛化考试替换旧 exam”条目。

> **来源记录已被取代（2026-10-29）**：下文的 `skill_origins.jsonl` / `source_ref` 已由 `provenance.jsonl` / `Exam.scene`（`TraceRef`）取代，见 `docs/decisions/provenance_trace_context_20261029.md`；其余设计不变。

## 背景

旧 `exam` 是“一条技能直接注入、回答一道文本题”。它无法检查弱模型能否在候选技能中找到目标技能，也无法检查它理解后写出的计划是否正确。新需求保留文本考试，不执行真实终端环境；场景由来源轨迹复原，作答分“选技能”和“写计划”两步，最后由 LLM judge 判定。

## 确定事项

1. 继续使用执行角色 `exam`，整体替换旧实现。
2. 每条技能版本一道考题，`exam_id = make_exam_id(skill_id, version)`，返回 `f"{skill_id}/v{version:02d}/exam"`。
3. 从上一学习相位和再上一学习相位的技能库差异中选出新建和版本升高的技能；pending 且尚无已存考题的技能也选。`bank/merge/` 开头的合并产物不选。
4. 候选技能目录包含当前库全部成员，使用与 test/live 相同的描述文字。
5. 一个技能版本只出一道题，场景由来源轨迹的真实前缀复原。
6. 旧 exam 的裸答对照和泄题审计保留，默认关闭。
7. 新技能来自 `learn_add`、`learn_add_cluster`、`learn_requirement`；修改技能来自 `learn_apply`、`learn_exam`。
8. 学习相位写 `skill_origins.jsonl`，记录技能版本的来源 sheet、场景步号和自然语言修改背景。
9. 不构造反例考题；curation 合并产物不出考题。
10. 所有描述性字段使用自然语言；`Exam` 的身份字段仍使用 id / version / sheet id。
11. `Exam` 升到 schema v3；`Attempt` 增加可选 `steps`，不升 schema 版本。
12. `Exam.question_text` 改名为 `scene_text`。

## 数据对象

```text
Exam (schema v3)
  exam_id
  skill_id
  skill_version
  scene_text
  expected_behavior
  reference_answer
  source_ref = {phase_label, unit_id, sheet_id, scene_step}

Attempt (schema v1)
  ... 原有字段 ...
  steps: List[Step] = []
```

`source_ref` 是来源指针，不是枚举。`scene_text`、`expected_behavior`、
`reference_answer` 都是自然语言。

## 来源账本

每个学习相位目录写 `skill_origins.jsonl`：

```text
skill_id
version
phase_label       # 写出该技能的学习相位
change_note       # 自然语言：为什么新建或修改
context_text      # 自然语言：写作时看到的证据
source_ref        # 执行相位、unit/sheet、场景步号
```

同一 `(skill_id, version)` 只保留首条。`find_origin` 从新的学习相位向旧扫描。

## 交付与参数

```text
delivery: exam_folder
  description_mode: summary_then_when_to_use

execution: exam
  max_skill_reads: 3
  bare_control: false
  leak_audit: false
```

`exam_folder` 使用 `folder.skill_folder_manifest(..., accepted_only=False)`，每道考题一行，`aux["skill_folder"]` 是目录清单。

## Prompt

- `EXAM_SCENE`：来源任务描述 + 截断前步骤 + 截断后实际步骤 + 技能正文 + 修改背景 → `scene_text` / `expected_behavior` / `reference_answer`。
- `EXAM_TRIGGER`：场景 + 全库目录 + 最多可打开条数 → `skills`（技能名列表，可为空）+ `reason`。
- `EXAM_PLAN`：场景 + 已打开技能正文 + 选择理由 → `plan`。
- `EXAM_JUDGE`：场景 + 期望行为 + 参考答案 + 目录 + 程序记录的已打开技能 + 选择理由 + 计划 → `verdict`（success/failure）+ `reason`。

## 已知边界

- 未触发而改写的技能依赖 `trial_level` 按技能保存步骤窗口；该前置缺口在本次实现中一并修复。
- `trial_level` 在 exam 相位的分析与动作契约见 `docs/decisions/exam_trial_review_20261028.md`：改写后的版本沿用自己上一版考题的切点。
- 来源行缺失时，exam 记 `exam_generation_failed`，不生成错误考场。
- 旧 Exam v2 记录不迁移；旧 run 不复用旧考题。
- 插件不需要新增真实执行能力；searchqa/alfworld 也可以使用文本考场。
