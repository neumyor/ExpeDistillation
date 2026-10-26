# 决定：P04/P06 只改写“触发过且没赢”的技能

日期：2026-10-24
一句话：把 P04/P06 的改写条件从“读过就改写”改成“触发过且组级没赢才改写”；已赢的 accepted 技能保留原正文，不再改写；从未触发的技能仍删除。
对应 log.md：2026-10-24 的 P04/P06 规则变更与 P03 前缀重跑条目（装配/dry check 完成后补）。
更新：2026-10-26 起 `skill_level` 先按 UnitSummary 的终态走共享动作表
（control-perfect / not-testable 删除，leak 有余次改写到末次删除，
zero_execution 不产行）；正常单元“触发 + pending + 组没赢”在还有后续
检验时 revise、末次检验时 delete(`live_no_improvement`)。规则 1 复用的
单元经 `carried_units.jsonl` 进入下一学习相位，不再因无新 trial sheet
而隐身。

## 用户裁决（2026-10-24 10:52）

用户原话要点：

> 把 P04 和 P06 的改写条件改成「触发过 且 没赢」才改写，然后继续跑 P05、P06、P07。
>
> - 触发过 + 没赢（组级判定非 KEEP，技能仍是 pending）→ 改写。
> - 触发过 + 赢了（组级 KEEP，技能已 accepted）→ **不改写**，保留原正文与 accepted 状态。
> - 从未被触发 → 删除（与现在一致）。

用户同时要求：先报三个数字；改决策语义要按设计变更流程走；P04/P06 共用一条规则；
开新 run，用 P03 前缀复用，不得在原 run 上原地修补；启动前 dry check。

## 变更前 / 变更后

| 情形 | 变更前（2026-10-23 `skill_level`） | 变更后 |
|---|---|---|
| 触发过 + 组 KEEP / accepted | revise（覆盖已赢技能） | **keep，不改写** |
| 触发过 + 组非 KEEP / pending | revise | revise |
| 从未触发 | delete | delete（口径见下） |
| usage 缺失/不可信 | keep（unverified_delivery） | keep（unverified_delivery） |
| 组 verdict/状态缺失 | 未区分 | keep（unverified_group / unverified_status） |

P03 证据上的重算（166 条）：

| 组级结果 | 技能数 | 读过 | 未读 |
|---|---:|---:|---:|
| `better=True`（KEEP，16 组） | 85 | 53（accepted） | 32（P03 整组 accepted） |
| `better=False`（14 组） | 81 | 52（pending） | 29（pending） |
| 合计 | 166 | 105 | 61 |

因此新规则的核心三个数字固定为：

- 触发且没赢 → **改写 52**
- 触发且赢 → **保留不动 53**
- 从未触发 → **删除 61**（已采用的严格口径 A；备选 B 为 29，见下）

## 32 条“未读但 accepted”的歧义（用户已裁决：严格删除）

P03 的 `live_verdicts.finalize_group` 在组 KEEP 时会把该组**所有 pending 成员**
标为 accepted，即使其中一些技能从未被读取。于是 61 条未读技能里有 32 条是
accepted（分布在 11 个 KEEP 组），29 条是 pending。

**用户裁决（2026-10-24 10:57，飞书原话）：**

> 采用第一种，赢了但是没触发也删掉。

即采用严格版：**从未被触发的技能一律删除，包括那 32 条属于赢组、已被标为
accepted 的**。最终数字：52 改写 / 53 保留 / 61 删除；P04 后库 105 条
（53 accepted + 52 pending）。

两种口径的完整比较（B 为已评估未采用的备选）：

| 口径 | 未读 + accepted | P04 task_list | P04 后 bank | P05 pending 覆盖 | P06 输入（P04 结束时） |
|---|---|---:|---:|---:|---:|
| **A. 严格删除（已采用，代码 `delete`）** | 删除 | revise 52 / keep 53 / delete 61 | **105**（53 accepted + 52 pending） | **14 组 / 52 条** | bank 105；pending 52 |
| B. 组赢了就信任整组（已评估未采用，代码 `keep`） | 保留 accepted | revise 52 / keep 85 / delete 29 | **137**（85 accepted + 52 pending） | **14 组 / 52 条** | bank 137；pending 52 |

说明：

- P05 覆盖在两种口径下相同：P05 只测 pending，A 和 B 的 pending 都是 52 条
  （A 删掉 32 条 accepted-unread，B 保留它们为 accepted；两者都不是 pending）。
- P06 输入规模差 32 条（105 vs 137）；P06 仍只对 P05 后的 pending 做改写/删除，
  accepted 的 85 条（B）或 53 条（A）不会被 P06 重测。
- 32 条 accepted-unread 的组分布：code-from-image 5、count-dataset-tokens 4、
  distribution-search 4、fix-ocaml-gc 4、largest-eigenval 4、feal-linear-cryptanalysis 3、
  mteb-retrieve 3、openssl-selfsigned-cert 2、polyglot-c-py 1、portfolio-optimization 1、
  sparql-university 1。
- 代码保留两种口径，配置选择：`unread_in_won_group: delete|keep`。
  本轮采用 `delete`；`keep` 配置保留作对照/以后可复用，不作为本轮结果。
- **运行口径说明（避免误读）**：本轮库 105 条是“严格按使用事实剪枝”的结果，
  与保留口径的 137 条不可直接比较；revise 52 也不能和旧 run 的
  revised=42 / retriggered=37 直接对比（旧 run 是 task_level +
  usage_group/usage_revise + retrigger，库 176 条）。

## 实现位置

**只改一处决策规则**：`composites/decision_strategies.py::skill_level`。
`methods/author/revise.py` 和 `stages/learn_ledger.py` 不变；`learn_apply.py` 继续按
keep/revise/delete 落账。P04 和 P06 都通过 `decide(name="skill_level")` 调同一个
函数，规则不会分叉。

`stages/phase_learn.py` 提供两个事实输入：

- `group_better_by_task`：当前 live 相位的 `verdicts.jsonl`，按 round 过滤；
- `skill_status_by_id`：当前 store 中每个 measured skill 的 `accepted/pending`。

决策分支：

```text
usage 不可信/缺失          -> keep（unverified_delivery）
未触发                     -> delete（skill_never_read；won-group unread 按策略）
触发 + accepted            -> keep（already_won，不进 writer）
触发 + pending + 组没赢    -> revise（skill_not_won）
触发 + pending + 组赢了    -> keep（group_won，防御分支）
组 verdict 缺失/终端       -> keep（unverified_group）
status 缺失                -> keep（unverified_status）
```

## 对结果解释的影响

- **revise 数不能再和上一轮直接比较**：上一轮 `revised=42 + retriggered=37` 是
  `task_level + usage_group/usage_revise + retrigger` 的口径；本轮 `revise=52`
  是 `skill_level + 触发且没赢` 的口径。库大小也不同（176 vs 166）。
- **已赢技能不再被覆盖**：53 条 accepted 的正文保持 P03 时的版本；P04 只改
  52 条未赢的 pending 技能。结果解释应说“P04 修正了失败/未赢的技能”，
  不能说“P04 重写了所有被读过的技能”。
- **P05 只测未赢的 revised 技能**：52 条 revised 仍是 pending，P05 覆盖 14 组/52 条；
  这解决了上一轮“53 条 accepted revised 不会被 P05 重测”的问题。
- **P06 的输入规模**在本轮采用口径 A 时是 105 条（口径 B 的 137 条仅作备选
  对照）；P06 实际处理的 pending 仍是 52 条（P05 后可能变化）；accepted 集合
  不进入 P06 的改写。
- 32 条 accepted-unread 的处置是语义选择，不是实现 bug；两种结果都必须
  在报告里明确口径，不能混用。

## 测试

- 触发且赢 → keep（already_won），不调用 writer。
- 触发且没赢 → revise（skill_not_won）。
- 未触发 → delete（skill_never_read）。
- 未读+accepted+组赢：`delete` 口径删除，`keep` 口径保留（won_group_unread）。
- usage 缺失/不可信、组 verdict 缺失、status 缺失 → keep 的安全分支。
- P04/P06 共用同一 `skill_level` 的断言。

## 装配与 dry check

新 run 复用 P01/P02/P03，不复制任何 P04 产物；按 P03 verdict 重建 pre-P04
状态（better 组全员 accepted，其余 pending），断言 166 / accepted 85 /
pending 81。dry check 三项：新目录没有 `p04_learn`、P03 summary complete、
`--until-phase 2` 回放零模型调用且无 P04 记录；再用新 run 的 P03 证据直接调用
`skill_level` 确认 52/53/61。三项全过才允许启动 P04。

## 验证教训（2026-10-24）

第一次装配 run 的 dry check 只验了“决策计数 52/53/61”，没有验“证据数据量”
和端到端路径；结果 P03 round store 轨迹被漏拷（0 条），P04 task_list 仍是
52/53/61，但 outcomes 是 0 revised / 53 already_accepted / 61 discarded /
52 unchanged，llm_requests=0。这个状态比直接报错更危险，因为计划数字看起来
完全正常。

以后凡是装配类改动，dry check 必须同时验：

1. **计数**：决策/状态/对象数量与预期一致；
2. **数据量**：被复制或被消费的数据真实存在且数量正确（本次是 P03 轨迹
   必须 90 条）；
3. **端到端路径**：至少抽一个目标走通真实消费路径（本次是 3 个 revise
   目标的非空窗口 + 1 个 revise 端到端干跑得到 `revised`，而不是
   `unchanged`）。

P04 完成后还必须自动核对 outcomes 与 llm_requests，不能只看 task_list：
`revised=52`、`already_accepted=53`、`discarded=61`，且 revision 请求
至少 52 次；任一不符就停在 P04，不启动 P05。
