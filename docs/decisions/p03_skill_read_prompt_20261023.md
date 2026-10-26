# 决定：P03 的“至少读一个技能”提示改为三步引导

日期：2026-10-23
一句话：把 P03 skills-mode 实际生效的 `skill_read_suffix` 从一句“必须 cat 一个”改成“先理解任务 → 再读技能目录 → 再选并 cat 一条所需技能”，并部署到远端 runner bundle。
对应 log.md：2026-10-23 15:58 条目。

## 背景

P03 的 folder_by_task 交付走 Harbor/Terminus 的 skills 模式。排查上一条完整 run 的
P03 首轮轨迹后确认：

- 实际进入 agent 首轮提示的“至少读一个技能”文案，不在本仓库的
  `TB_SKILL_FOLDER_GUIDANCE`，而在外层 vendor 的
  `TencentSkillTerminus2.skill_read_suffix`；
- `TB_SKILL_FOLDER_GUIDANCE` 只作为任务级 `SKILL.md` 上传到
  `/opt/openclaw-skills/<task>/SKILL.md`；Harbor 只扫描 depth-2 的
  `<skill>/SKILL.md` 生成 `<available_skills>`，任务级 `SKILL.md` 不会进入首轮提示；
- `TB_TASK_PROMPT_FOLDER` 在当前 TB 远程链路里没有被渲染，只是冻结的 prompt 注册名。

原文案只要求“You MUST cat at least one skill file”，没有要求先理解任务，也没有引导模型
理解每条技能的 `description` 里“什么时候该用”。

## 决定（用户 2026-10-23 选择方案 A）

把外层 vendor 的默认 `skill_read_suffix` 改为三步：

1. 读任务描述，判断任务需要什么；
2. 读 `<available_skills>` 技能目录，理解每条技能教什么、什么时候用；
3. 选与任务最匹配的一条，`cat` 它的 `SKILL.md`；在运行任何任务命令前必须至少这样读一条。

保留“一次只读需要的一条、不要同时打开多条、列表不可见时 cat catalog.md”的原有约束。

## 改动与部署

- 源码：外层 `scripts/tb21/vendor/tb21-tencent-skill/python/tencent_sandbox_terminus.py`
  的 `TencentSkillTerminus2.__init__` 默认值；
- 测试：同目录 `test_tencent_adapters.py` 增加对新步骤文案的断言；
- 远端：`/data2/niuyiming/scripts/vendor/tb21-tencent-skill/python/tencent_sandbox_terminus.py`，
  已备份为 `...bak_20261023_skill_read_suffix`，上传后 sha256 与本地一致。

## 影响与回滚

- 该 vendor 被所有 TB21 skills-mode 实验共用；部署后新启动的 trial 会使用新文案，已在运行的
  trial 不受影响（Python 进程已加载旧代码）。
- 未改本仓库的 `TB_SKILL_FOLDER_GUIDANCE` 和 `TB_TASK_PROMPT_FOLDER`；它们不是实际首轮提示。
- 回滚：用远端备份文件覆盖回原路径。
- 这是 agent 行为变化，不是 ExpeDistillation prompt digest 变化；按新实验记录，不能与旧 run
  的读取率直接配对比较。
