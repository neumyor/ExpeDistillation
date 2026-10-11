日期：2026-10-11
一句话：K4 验收循环在每个崩溃点续跑都与一次跑完相同（改写后立即落盘、轮次从已有记录推导），阶段 4 对单个 skill 的失败按类别处置、不再整阶段停下。
对应 log.md 条目：2026-10-11 11:44（对照设计 review 全部实现，分波修复并经三次独立对抗式复审）。

## 背景

review 指出两处问题：

1. `_rewrite_version` 只在内存里升版本、追加 `rewrite_history`，只有终态才
   `store.save`。崩溃后续跑从磁盘上的旧 v1 重来：`(skill_id, version, round)`
   可能匹配到"同一版本号、但正文是已丢失的改写稿"上的作答；`_find_accepted`
   不看版本，崩在"v2 带 skill 作答通过（已落盘）"与"状态落盘"之间时，会把
   从未被考过的 v1 标为通过——未验证的 skill 进库。改写修复预算耗尽时轮次
   被消耗却不升版本，"版本 v ⇒ 下一轮 v-1"的推导失效，续跑重复一轮。
2. K3 / G6 修复预算耗尽抛出的 `ModelOutputError`、调用级重试耗尽后的
   `InfrastructureError` 会让整个阶段 4 停下，后面的 skill 全部不处理。

## 决定一：落盘顺序 + 从已有记录推导轮次（不加 Skill 字段）

候选：

- A. 在 Skill 上加 `next_round` 字段显式落盘。要升 `SKILL_SCHEMA_VERSION`
  （core/models.py）并同步 README 第二节，且这个字段只对阶段 4 有意义；
- B. 从已有记录推导。**选 B。**

做法：

- 每次改写成功后**立即** `store.save(skill)`，然后才审计、作答。因此磁盘上
  `skill_version = v` 的作答一定是在磁盘上 v 版正文上做的，"同版本号不同正文"
  不再可能出现；
- 新增 `stage4_acceptance/rewrite_failures.jsonl`：G6 每一次失败的尝试写一行
  （`skill_version`、`retry_round`、`try_index`、`exhausted`），耗尽预算的那一次
  `exhausted: true`。于是"除最后一轮外，每一轮恰好以一个改写结果收尾"：
  要么新版本（已落盘），要么一条 `exhausted` 记录；
- 下一轮 = `(version - 1) + exhausted 记录数`；
- 泄题审计失败的记录按 `(version, round)` 去重，续跑同一轮不重复追加；
- G6 修复预算按已记录的失败次数续上，不因崩溃重新给满预算（否则崩在第二次
  尝试时，续跑会多给一次机会，结果与一次跑完不同）。因此不再用
  `with_retries` 包 G6，改为显式循环逐次落盘；
- "已通过"检查要求作答的 `skill_version` 等于磁盘版本。

不用老仓库实现的理由（AGENTS.md 10.2）：ExpeToSkill-merged 的
`reliability/units.py`（`guard` / `FailureCollector`）是"单元边界失败记录"，
对应的是决定二；老仓库没有与 K4 改写循环对应的轮内续跑机制（L2 loop 的续跑
按 sidecar 推导轮次，思路相同：只从已落盘产物推导，不信内存计数）。

## 决定二：单元失败处置放在阶段，不放在 K4

K4 保持"失败就原样抛出"，便于其他调用方复用；处置策略属于阶段：

- `ModelOutputError`（K3 修复预算耗尽）：写失败账本（unit id = skill_id，
  stage = `stage4_acceptance`），skill 以 `discard_reason = model_output_error`
  终态落盘（通过 K4 提供的 `discard_skill`，它读取磁盘上的最新版本再落盘），
  继续下一个 skill；续跑不再重试它；
- `InfrastructureError`：写失败账本，skill 保持磁盘上的 pending 状态，继续
  其他 skill；全部处理完、摘要（含 `infra_failed` 列表）落盘后，抛出
  `InfrastructureError` 说明数量并提示续跑——流水线不会带着缺了 pending skill
  的库进入阶段 5；
- 其他（ConfigError / ProviderRefusalError / CodeBugError / 普通异常）立即
  抛出，不写账本、不写摘要。

这与老仓库 `FailureCollector` 的处置一致：可重试类收集到阶段末一起抛出，
其他立即停下。

## 决定三（第三轮 review）：只验收阶段 3 候选、摘要累计、先终态后账本

- **只处理阶段 3 候选**：判定条件 `skill_id == make_skill_id(subtask_id)`。
  K5 会把进行中的合并 skill（`bank/merge/NNNN`）先以 pending 落盘再做回归考试；
  阶段 5 在这里中断后续跑，旧实现的阶段 4 会只凭锚点考题就把它验收通过，K5 再
  合并一次，库里就会多出一个未回归的合并 skill 和一个重复。现在阶段 4 跳过它们，
  `accept_skill` 本身遇到非候选 id 直接抛 CodeBugError（双重保险）。选"按 id
  判定"而不是"遍历 subtask / exam"，因为它不依赖阶段 2/3 的其他对象，且合并 skill
  的 id 规则（`ids.make_merged_skill_id`）天然不满足；
- **摘要累计**：`accepted` / `discarded` 从磁盘上全部候选的状态统计（`merged`
  算作通过验收——阶段 5 只合并通过验收的 skill），另给 `processed_this_call`。
  续跑或重跑不再把摘要覆盖成部分或零计数；
- **先终态后账本**：ModelOutputError 时先把 skill 存为
  `discarded / model_output_error`，再写失败账本；崩在两者之间时，下次调用发现
  "已是 model_output_error 丢弃但账本里没有对应行"就补一行（按 unit id + stage +
  错误类去重），保证每个这样的丢弃恰好一行。崩在丢弃落盘之前则什么都没记，续跑
  重新判卷——K3 修复预算不跨崩溃保存（见下），这是唯一会让结果依赖崩溃点的地方；
- **按数字序号排序作答记录**：`_exam_attempts` 与 `_last_with_skill` 的平局
  打破都用 `ids.parse_attempt_id` 解析出的序号，不用 id 字符串（序号 ≥ 100 时
  `att100` 在字符串序里排在 `att99` 前面）。

## 已知局限（不在本次文件范围）

- K3（`composites/exam_once.py`）已改为"先落盘作答（`judge_verdict=None`），
  判卷后以同一 attempt id 再落盘"，续跑时复用同键的未判卷作答、只补判卷。
  因此原先"崩在判卷时续跑会重新作答"的局限**已不存在**。K4 的所有查找
  （裸答、已通过、本轮作答、改写上下文）都要求 `judge_verdict is not None`，
  未判卷的作答不会被当成任何结论；
- K3 内部的修复预算（`_BLOCK_REPAIR_ATTEMPTS`）不跨崩溃保存；
- README 第二节 run 目录布局需要补 `stage4_acceptance/rewrite_failures.jsonl`，
  README 阶段 4 需要补 `discard_reason = model_output_error` 与 InfrastructureError
  的处置（README 不在本次修改范围，已报告）。

## 测试

`tests/composites/test_acceptance_resume.py`：5 个场景（泄题作废 + 改写预算耗尽
+ 通过 / 耗尽 / 泄题丢弃、普通通过、弱模型本来就会），先一次跑完作为参照，
再在**每一次模型调用**和**每一次写盘（之前 / 之后）**注入崩溃并续跑，断言最终
skill、全部作答记录、阶段 4 账本与参照完全相同，且只重问崩溃时尚未落盘的调用（崩溃的那次调用本身；作答或判卷结果尚未写盘时
各自只重问那一次）。另有显式用例覆盖"未判卷作答已落盘、判卷前崩溃"：续跑只补
判卷、不重问弱模型；以及"磁盘上的未判卷作答不决定任何结论"。修复前该文件 30+
个用例失败。

## 更正（2026-10-11，文档同步）

"已知局限"最后一条要求的 README 同步已完成：README 第二节布局已列出
`stage4_acceptance/rewrite_failures.jsonl` 与 `leak_audits.jsonl`，阶段 4 已写明
`discard_reason = model_output_error` 与 InfrastructureError 的处置（以及只处理阶段 3 候选）。
