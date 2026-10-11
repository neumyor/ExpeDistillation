日期：2026-10-11
一句话：K5 库整理改为单元级续跑：curation_log.json 作为日志先记决定再改 skill 状态，合并编号永不复用，合并后的回归考试覆盖全部原始来源。
对应 log.md 条目：2026-10-11 11:44（对照设计 review 全部实现，分波修复并经三次独立对抗式复审）。

## 背景（review 发现的问题）

1. `curate_bank` 每次调用都从 `merge_serial = 0` 开始。bank 还没写出时续跑，
   下一次合并会写到 `bank/merge/0000`，覆盖上一次调用已经保留的合并 skill，
   它的来源仍是 `merged`，内容从库中丢失；curation 日志也从头重建，丢掉上一次的合并记录。
2. 保留一次合并时先存合并 skill（accepted），再逐个把来源标 `merged`。中间崩溃会让
   合并 skill 和来源同时 accepted，一起入库；反过来的顺序则会丢掉合并内容。
3. 合并 skill 只锚定 `sources[0].subtask_id`，合并的合并只重考锚点那一道题，
   而 README 阶段 5 要求重做每个来源 skill 的考题。
4. 单元级失败处置没有落实（见下文"失败处置"）。

## 候选方案

- A. 只修编号（从库里最大编号续），续跑时整轮重来（重新调 G7）。实现最简单，
  但续跑的结果和一次跑完的结果不同（G7 看到的成员集合变了、模型调用重复），
  违反 AGENTS.md 第八节"中断再续跑，结果与一次跑完相同"。
- B. 只靠 store 里的状态推断（pending + 来源状态），不写日志。无法区分"已决定撤销但
  还没存 discarded"与"还没决定"，也无法跳过已做完的 G7/G9。
- C. **日志先行（选这个）**：`curation_log.json` 作为日志（schema `curation-log-v2`），
  每一轮的 G7 输出、每个合并单元的决定、每一轮的 G9 输出都先原子写进日志，再把效果写到
  skill 上；续跑时按日志重放，模型输出不再重新请求。与老仓库 SkillExpand 的做法一致
  （`docs/ERROR_HANDLING.md` 第 5 条"先写 batch journal，再写版本库；续跑时核对一致性
  并补写缺失的版本"）。老仓库没有库整理/合并机制本身，可复用的只有这条原则。

## 决定：一次合并的提交顺序

1. 合并 skill 存为 `pending`（G8 输出落盘；pending 永远不是库成员）；
2. 回归考试：K3 先把作答存成 `judge_verdict=None` 的 Attempt，判卷后再存同一 ID。
   续跑时只把**已判卷**（`judge_verdict is not None`）的回归 Attempt 当作结果复用；
   未判卷的交给 K3，它复用已存的答案、只补判卷。合并 skill 的 ID 不复用、每道题只考
   一次，所以每个"合并 × 考题"最多一条已判卷 Attempt；万一多于一条，取 serial 最小的
   （确定性）。若把未判卷的当结果，`None is not True` 会被读成回归失败、错误撤销合并
   （K3 改为先存答案后发现，已加三个崩溃点回归测试）；
3. 把决定（kept / reverted）追加进日志 —— **提交点**；
4. 应用决定：kept → 先逐个把来源标 `merged`，最后把合并 skill 翻成 `accepted`；
   reverted → 合并 skill 存为 `discarded`。

续跑规则（在算成员之前执行）：

- 日志里有决定、合并 skill 仍是 pending → 按日志补做第 4 步（幂等）；
- 日志里没有决定、但来源已有 `merged` → 只可能是 kept 决定所在的日志丢了
  （来源只在 kept 提交后才会被标），向前完成；
- 其余 pending（最多一个）是下一个单元的"在途合并"：若其 `merged_from` 与该单元
  的来源一致就复用（不再调 G8），否则回滚为 `discarded`（`interrupted_merge`），
  编号作废不复用。出现多个在途合并说明 run 不一致，报 ConfigError 由人处理。

合并编号 = store 中已有 `bank/merge/NNNN` 的最大编号 + 1，包括撤销和回滚的，
所以任何已有合并（保留的或撤销的审计记录）都不会被覆盖。

每一轮写 `done` / `stop` 标记，续跑时已完成的轮直接跳过（不会把旧一轮的分组
重新套到新成员上）。日志不是 v2 格式时明确报错，而不是当成全新开始。
没有传 `run_dir` 时日志写在 `store.run_dir` 下（以前是不写日志）：日志是续跑依据，不能缺。

## 决定：回归考试覆盖全部原始来源

来源沿 `merged_from` 递归展开到没有 `merged_from` 的原始 skill（阶段 3 产物），
每个原始 skill 一道考题（`make_exam_id(subtask_id)`），按 exam_id 去重、按原始 skill_id
排序。任一来源 skill 或考题不在 store 中 → 该合并记为 skipped（无法验证）。
合并 skill 的 `subtask_id` 仍取第一个来源的，只作稳定引用，不再用来找考题。

## 决定：失败处置

- `InfrastructureError`（任何调用）→ 记入 `failed_units.jsonl` 后向上抛，阶段停下，
  续跑从磁盘接着做。不允许因为网络问题跳过合并，否则库的内容取决于运气；
- G8 的 `ModelOutputError` → 该合并记为 `skipped`（不占编号），继续；
- 某个合并的回归考试 K3 抛 `ModelOutputError`（K3 内部已重采样一次）→ 该合并记为
  `reverted`，合并 skill 存为 `discarded`、`discard_reason = regression_error`，继续；
- G7 / G9 面向整库，它们的 `ModelOutputError` 记录后向上抛；
- 失败记录的 stage 标签是 `stage5_curation`，unit_id 形如
  `curation/round01/unit000/merge`、`curation/round01/similarity`。
- 已记为 skipped / reverted 的合并在续跑时**不重试**：同一轮后面的合并依赖它的结果，
  重试会让续跑结果偏离一次跑完的结果。

## 已知限制

- （原先的"判卷前崩溃会重新作答"限制已不存在：K3 现在先存答案，续跑只补判卷。）
- 回归判卷耗尽重试（ModelOutputError）时该合并记为 reverted，留下的未判卷 Attempt
  只作审计记录，续跑不会再去完成它（决定已提交）。
- 不在 schema 里加新字段：撤销原因的细节写在日志的 merge_records（含
  `discard_reason`），skill 上只有 `discard_reason`。

## 决定：过期与篡改检查（跨阶段）

阶段 2–3 会跳过模型输出失败的单元，并在每次续跑时重新提供它们，所以阶段 5 开始之后的
某次续跑可能让阶段 4 产生**新的** accepted skill。这个窗口不只在 bank 写出之后：
reviewer 复现过"所有轮都已记 done、bank 还没追加"时插进一个新 skill，K5 续跑会从
store 重算成员，把新 skill 不经 G7/G9 直接追加进库（0 次模型调用）。

决定：

- 日志在第一次启动时记下整理的输入集 `input_skill_ids`（当时所有 accepted skill）；
  之后每次续跑（bank 写没写都一样），先补完被打断的提交，再按日志重放：
  期望的 accepted 集合 = 输入集 − 每个 kept 合并的来源 + kept 合并 skill。
  store 里实际的 accepted 集合必须与之相等：
  - 多出普通 skill → "skill bank is stale: accepted skills not in the bank: [...];
    they were accepted after curation started -- start a new run directory"；
  - 多出合并 skill（没有记录 kept 却是 accepted）、日志里应在的成员不见了、合并 skill
    的 version ≠ 1（K5 只写 version 1）、或有日志时出现"pending 但来源已 merged 且无决定"
    → "corrupted run: ..."；
  - 都是 ConfigError，绝不把它们当成员悄悄收进库。
- bank 已写出时另有一层检查：有 accepted skill 不在 bank 里 → 同样的 stale 报错。
- 报错只建议"start a new run directory"，不建议删 `skill_bank.jsonl` /
  `curation_log.json` 重整：原地重整后，阶段 6 已存的轨迹还带着旧库（K1 另有拒绝），
  那条路走不通。
- 日志缺 `input_skill_ids` 时明确报错，不当成全新开始。

与合并提交顺序的关系：检查只看 `accepted`，并且在补完被打断的提交之后才做。在途合并是
`pending`、合并掉的来源是 `merged`、撤销/回滚的是 `discarded`，都不会触发。
"pending 且来源已 merged"的向前完成只在没有日志时才用；有日志时，来源只会在 kept
决定写进日志之后才被标记，所以这种状态只可能来自 K5 之外的改动。

另外，在途合并（pending 的 `bank/merge/NNNN`）曾被续跑的阶段 4 当成候选、只凭锚点考题
验收（两位 reviewer 复现）。阶段 4 已改为只处理阶段 3 的 skill；
`tests/composites/test_curation_stage4_interleave.py` 覆盖"K5 崩溃 → 阶段 4 → K5 续跑"
这一顺序。K5 这一侧的防线：被外部改成 accepted 的在途合并按 corrupted run 拒绝。
