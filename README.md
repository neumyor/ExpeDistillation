# ExpeDistillation

强模型轨迹蒸馏成 Skill 库、教弱模型的实验仓库（开发名 SkillPool）。

## 系统流程（设计定稿 2026-10-10）

给定任务集，强模型和弱模型各在每道 task 上执行一遍。对弱模型没做出来的题：

1. **切强模型的段**：把强模型在该任务上的执行轨迹切成一段段 Subtask
   （每段是完成一个目标的完整过程，成功或失败轨迹都切），每个 Subtask 生成
   一个 skill，并出一道考题检验弱模型是否掌握该 Subtask 的能力；
2. **切弱模型的段**：把弱模型的轨迹切成 Subtask，由强模型逐段诊断并写 skill，
   同样出考题。

**验收**：把 skill 交给弱模型去做对应的考题，强模型判定是否做对；
靠 skill 做对了，该 skill 入库。

**test**：强模型退出。弱模型启动时读入全部 skill，然后执行任务。

## 代码来源（provenance）

底座代码（runtime / benchmarks / persistence / reliability / 执行循环 /
test 骨架）从姊妹仓库迁移而来，不训练模型权重，只维护自然语言 Skill：

- 源仓库：`../ExpeToSkill-merged`（SkillExpand 系统）
- 源分支与提交：`TB-eval @ 4f436af`
- 迁移时按"仅带依赖闭包"原则拷贝，详见首次迁移提交的说明
