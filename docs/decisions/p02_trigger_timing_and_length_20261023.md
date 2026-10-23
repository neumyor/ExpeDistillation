# 决定：P02 技能的触发时机分类与 summary / trigger 长度预算

日期：2026-10-23
一句话：P02 的 G2 写作（SKILL_DISTILL / SKILL_DIAGNOSE）按 5 种触发时机给每条技能定一个开头短语，规定 summary 与 [When to use] 的分工和长度预算，并取消 catalog.md 的 200 字符截断。
对应 log.md：2026-10-23 15:35 条目。

## 背景

folder 交付里，弱模型只凭 `<available_skills>` 的一行描述决定打开哪张卡。这行描述由
`summary + " Use when: " + [When to use]` 拼成。上一条完整 run
（`runs/tb21_split_folder_usage_20261022_1845`）P02 库 176 条的实测：

- summary p50 163、max 250 字符；[When to use] p50 313、max 483；拼接后 p50 482、max 704；
- 同一任务 3–10 条技能，描述总量 p50 约 2.9k、最大 4.8k 字符，全部进入弱模型首轮上下文；
- summary 和 [When to use] 都写触发条件，常重复；反例写在 [When to use] 里；
- `catalog.md` 每行截到 200 字符并加 ` [truncated]`，176/176 条全部被截；
- 描述里没有“什么时候去看”的信号，弱模型不知道该在任务的哪个时刻回来查列表。

## 候选方案

时机信息的存放：

| 方案 | 做法 | 结论 |
|---|---|---|
| A | G2 从 5 类里选一类，[When to use] 必须以该类的固定短语开头 | **选用**。只改 prompt，时机直接进入弱模型看到的那行描述；可按开头短语统计 |
| B | 新增 `Skill.trigger_timing` 字段 | 不选：要改数据对象、校验器、渲染器；只有交付端要按时机分组时才值得 |
| C | 复用 `Skill.group` | 不可行：该字段归 P06 整理的 GROUP 使用 |

长度控制：只写进 prompt，不加硬校验。G2 的字段级 `ModelOutputError` 对单元是终态，
硬校验会把“稍长”变成“整条技能丢失”（上一条 baseline run 的 `mteb-retrieve` 就是
这条路径丢掉整个任务）。

## 决定（用户 2026-10-23 裁定）

1. **分工**：summary 只写“这张卡教什么”（动词 + 宾语），不写触发条件；
   [When to use] 只写“什么时候打开”：时机短语 + 可观测信号 + 前置条件；
   “什么时候不适用”只写在 `not_applicable_when`。
2. **长度**：summary 与 [When to use] 合计最好在 400 字符以内，最长不超过 1,000；
   预算不宜压得太低，过短会让描述含糊，反而伤害交付。写完整句子，超长就删细节，
   不许写半句。
3. **5 种触发时机**（方案 A，固定开头短语）：

   | 键 | 开头短语 | 含义 |
   |---|---|---|
   | `task_start` | `At task start,` | 任务理解：开始任务、读任务描述和文件时 |
   | `after_env_check` | `After the first environment check,` | 环境探测：第一次 ls / which / import / 网络探测之后 |
   | `on_failure` | `When a command fails, hangs, or repeats,` | 失败、卡死、循环时的恢复 |
   | `before_submit` | `Before submitting,` | 提交前检查 |
   | `any_step` | `At any step,` | 通用常驻规则：随时可参考的基本行为规范 |

   三点注意（用户认可）：
   - G2 原本看不到段在轨迹里的位置（steps view 从 step 0 重新编号），时机判断会不准。
     因此新增 `segment_position` 变量：“第 i/n 段，覆盖原轨迹第 a–b 步，轨迹共 N 步”，
     由 add 路径的证据采集器从存储算出，经 `write_new` 传给 `write_skill`；
   - `At any step,` 只在其余四类都不合适时使用，防止滥用；
   - 段位置和来源只作提示、不作规则：早段常是任务开始，末段常是提交前，失败段常是失败恢复。
4. **catalog 不截断**：`methods/delivery/folder.py` 删除 200 字符截断与 ` [truncated]`
   标记，catalog 行完整保留描述。`description_text` 的 1,024 字符上限是 Agent Skills
   格式的硬上限，保留；按第 2 条的预算它不会触发。

## 范围

改：`prompts/author.py` 的 SKILL_DISTILL / SKILL_DIAGNOSE、`blocks/author.py`
（`write_skill` 的 `segment_position` 参数）、`methods/evidence/add_path.py`
（算段位置）、`methods/author/write.py`（透传）、`methods/delivery/folder.py`（去截断）。

暂不改（用户裁定先专注 P02）：P04/P06 的 `LIVE_SKILL_RETRIGGER`、
`LIVE_SKILL_REVISION`、P06 的 `MERGE`。在它们同步之前，被 retrigger / revise / merge
的技能可能重新变长、丢掉时机短语。执行 prompt `TB_SKILL_FOLDER_GUIDANCE`（告诉弱模型
“按当前时机去找技能”）也暂不改。

prompt digest 变化，必须新开 run；可用 `assemble-reuse --level baseline` 复用 P01。

## 验证计划

先新建 run，`--until-phase 2` 只跑到 P02，统计：summary / [When to use] / 拼接长度
分布与超 400、超 1,000 的比例；5 类时机的分布（`At any step,` 占比）；开头短语合规率；
P02 的 `ModelOutputError` 数。指标可接受后再跑 P03。
