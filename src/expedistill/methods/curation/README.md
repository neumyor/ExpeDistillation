# 整理模块（curation）使用说明

整理模块把一批已通过验收的技能，整理成最终交给弱模型使用的技能库：
合并讲同一件事的技能，调和互相矛盾的技能，可选地清掉没有泛化价值的技能，
最后给每条技能分一个能力组。它**不重新判断技能好不好用**——那是验收和决策的事；
它只决定库的最终形态。

读完这份文档，你应该能：配出一个能跑的整理，知道默认配置下会发生什么，
知道想改某个行为时该改哪个参数，以及哪些情况会让整理报错停下。

---

## 1. 它在流程里的位置

一个方法是一串阶段，例如 live-run 方法是 `D L B L B L C`（L 是学习阶段）。
每个学习阶段的最后一步都会产出一份技能库，但**只有序列里最后一个学习阶段真正整理**：

| 学习阶段 | 做什么 | 用到哪些整理参数 |
|---|---|---|
| 最后一个 L | 按配置的方法整理（`agentbrew` 合并，或 `none` 原样输出） | 全部 |
| 中间的 L | 原样输出已接受的技能，不调模型 | 只有 `token_budget`（超长就报错） |

所以中间学习阶段的 `curate` 配置里写的 `rounds`、`merge_check` 等都不会生效。

整理只处理**已接受**（accepted）的技能。待检验（pending）的技能不参与整理，
原样追加在技能库末尾，留给后面的验收阶段去测。

## 2. 核心概念

**整理轮次（round）。** 一轮依次做四件事：找相似（G7）→ 可选地丢弃低价值技能 →
逐组合并（G8，可选回归考试）→ 分组（G9）。合并会产生新技能，新技能又可能和别的
技能相似，所以可以跑多轮。什么时候停下见第 5 节第 1 条，这一点和直觉不同。

**G7：找相似。** 把当前所有技能一次性列给整理模型（curator），让它返回三样东西：

- **相似组**（similar_groups）：教的是同一种做法或同一个教训的技能，至少两条一组。
  来源任务不同、原题报错原文不同、文件名或数值不同，都不影响判断。
- **冲突对**（conflicts）：对同一情境给出相反建议的两条技能。
- **低价值技能**（low_value）：换到别的任务上帮不上忙的技能，比如只对来源那道题成立
  的计算，或者空泛到没法照做的建议。

做法类技能（带 `[When to use]` 段）只和做法类配对，教训类（带 `[Trigger]` 段）只和
教训类配对。混了类型的组会被跳过，不调模型。

**G8：合并。** 每个相似组或冲突对交给整理模型合成一条新技能，泛化是第一要求：

- 去重合并：把几条技能归纳成一条通用经验，触发条件写成通用形式，不围绕某个来源场景；
- 冲突合并：先分析两条建议为什么冲突，再写清什么条件下按哪一条做。

模型返回 JSON：合并后的正文加一句话摘要，摘要直接成为新技能的目录行。
合并成功后，来源技能状态变成 `merged`（退出技能库），新技能（编号形如
`bank/merge/0007`）进入技能库。

**回归考试（merge_check: exam）。** 每条通过纸面验收的技能都有一道考题。开启回归考试
时，合并后的新技能要重做每条来源技能的考题，任何一道没答对，这次合并就撤销，来源技能
原样保留。默认关闭，见第 5 节第 2 条。

**低价值丢弃（drop_low_value）。** 开启后，G7 标出的低价值技能在本轮合并之前就被丢弃
（状态 `discarded`，原因 `low_value`），不会再进入任何合并。关闭时，G7 照样会标，
但只记进日志，不做处理——可以先关着跑一遍，看看它会标哪些。

**G9：分组。** 每轮结束给所有技能分配一个能力组名，写在技能的 `group` 字段上。

**库长度预算（token_budget）。** 技能库总长度的上限（按估算的 token 数）。
超过上限，整理会继续下一轮合并；用完轮数仍超长，整理报错停下，**不会自动删任何技能**，
需要人来决定提高上限还是换整理方式。`null` 表示不设上限。

**整理日志与续跑。** 每一步的决定都先写进整理日志，再改技能状态。进程中途被杀，
用同一个命令续跑即可：已经做出的决定从日志重放，不会重复调模型。

## 3. 最小配置与默认行为

整理配置写在学习阶段的 `curate` 里。最小写法：

```yaml
phases:
  L:
    kind: learn
    evidence: {name: segment, K: 5, retry_limit: 2}
    author: {name: distill_diagnose}
    curate:
      name: agentbrew
      token_budget: null
```

`token_budget` 是唯一必填项（可以写 `null`）。其他参数取默认值，等价于：

```yaml
    curate:
      name: agentbrew
      token_budget: null
      rounds: 3
      merge_check: none
      drop_low_value: false
      similarity_input: full
```

在这套默认配置下，最后一个学习阶段会：

1. G7 看每条技能的完整正文，找出相似组、冲突对和低价值技能；
2. 低价值技能只记进日志，不丢弃；
3. 每个组调一次 G8 合并，不做回归考试，合并结果直接进库；
4. G9 给所有技能分组；
5. **然后就停了**——因为没有长度上限，第一轮结束时库"已经不超长"，后面两轮不会跑
   （第 5 节第 1 条）。

整理需要的模型调用都由 curator 角色发出（默认用强模型，在配置的 `models.curator` 里改）；
回归考试开启时，还会用到 answerer（作答，默认弱模型）和 judge（判卷）。

## 4. 常见场景该怎么配

**想清掉没有泛化价值的技能**，打开丢弃开关。建议先关着跑一次，看日志里
`low_value` 标了哪些，确认合理再打开：

```yaml
    curate: {name: agentbrew, token_budget: null, drop_low_value: true}
```

**想让 G7 只看摘要**（库很大、全文会撑爆上下文时）。前提是**每条技能都有摘要**，
有一条没有，整理就在调模型之前报错停下：

```yaml
    curate: {name: agentbrew, token_budget: null, similarity_input: summary}
```

**想合并得更彻底，跑满多轮。** 只调大 `rounds` 没用，必须同时给一个长度上限，
否则第一轮后就停（第 5 节第 1 条）。下面这样会一直合并，直到库短于 60000 或跑满 5 轮：

```yaml
    curate: {name: agentbrew, token_budget: 60000, rounds: 5}
```

注意：跑满 5 轮仍超过 60000，整理会报错停下，而不是"尽力而为"地交出一个超长的库。

**想让每次合并都经过考试验证。** 只有序列里有纸面验收阶段（exam 方法，有 A 阶段）时
才能开，live-run 方法会在加载配置时直接被拒：

```yaml
    curate: {name: agentbrew, token_budget: null, merge_check: exam}
```

**完全不整理**（对照实验），已接受的技能原样进库：

```yaml
    curate: {name: none, token_budget: null}
```

## 5. 注意事项与容易踩的坑

1. **`token_budget: null` 时整理只跑一轮。** 每轮结束都会检查"库是否已经不超过上限"，
   满足就停；`null` 表示没有上限，这个条件永远满足，所以 `rounds` 写多少都只跑第一轮。
   想跑多轮，必须给一个具体的上限。
2. **`merge_check` 的默认值是 `none`**（2026-10-19 起，此前是 `exam`）。
   - `none`：合并结果直接进库，不额外调模型；
   - `exam`：每次合并都要重做所有来源技能的考题，全部答对才保留。考题是按来源
     任务出的，如果合并的是跨任务的通用技能（比如"做完任务后怎么提交"），合并后的
     技能答不了原任务的题，合并几乎必然被撤销，库里的重复就会一直留着。
   - 由 v3 配置升级而来的 v4 配置，升级时都会把这一项明确写出来（exam 方法写 `exam`，
     live-run 写 `none`），不受默认值变化影响。只有自己手写、又省略了这一项的配置才会
     走默认值。
3. **`similarity_input: summary` 要求每条技能都有摘要。** 旧技能库（2026-10-17 之前生成的）
   全部没有摘要，必须先补摘要再用这个选项。报错信息会列出缺摘要的技能编号。
4. **G7 一次看完整个库。** 默认 `full` 模式下，所有技能的正文压成一行一条，一次传给
   整理模型。参考数据：一个 201 条技能的库，这份列表约 45 万字符、11 万 token 左右。
   库再大，就需要上下文更长的整理模型，或改用 `summary` 模式。
5. **整理不检查合并内容的质量。** 代码只检查 G8 输出的格式（字段齐全、摘要非空、只有
   一行、不超过 500 字、段落标记和来源一致）。合并得是否真的通用、冲突分析是否到位，
   全靠整理模型本身。格式不对的合并会被跳过，来源技能保持原样。
6. **G7 输出格式错误会让整理停下。** 和 G8 不同，G7、G9 的格式错误（比如低价值列表里
   出现了库里没有的编号）在格式修复重试之后仍不对，会停止整个整理，需要人处理后续跑。
7. **改了提示词就是新实验。** G7、G8 的提示词文本会冻结进 run 的清单；代码里的提示词
   改过之后，旧 run 拒绝续跑，必须新建 run 目录。
8. **中间学习阶段的 `curate` 配置大部分不生效**，只有 `token_budget` 起作用（第 1 节）。
9. **`none` 方法没有"过期库"检查。** `agentbrew` 发现库写好之后又有新技能被接受，会报错；
   `none` 会直接按新的已接受集合重写库。正常流程里碰不到，只在直接调用驱动时要注意。

## 6. 出了问题去哪里看

一个学习阶段的整理产物都在 `runs/<run>/phases/<阶段标签>/` 下（阶段标签里的冒号换成下划线，例如 `phases/p06_L/`）：

| 文件 | 内容 |
|---|---|
| `skill_bank.jsonl` | 最终技能库，一行一条技能 |
| `bank_summary.json` | 成员编号列表、条数、库指纹 |
| `curation_log.json` | 整理日志（只有真正跑了 `agentbrew` 的最后一个学习阶段才有）：每轮的 G7 输出（含 `low_value` 和实际 `dropped`）、合并派发表、每次合并的结果与原因、G9 分组、是否提前停 |

`curation_log.json` 里 `merge_records` 每条的 `outcome` 是 `kept`（已合并）、`reverted`
（回归考试未过，撤销）或 `skipped`（没合并，`reason` 写明原因：组里不足两条现存技能、
类型混合、G8 输出格式错误等）。

---

## 附录 A：参数表

### `agentbrew`

| 参数 | 类型 | 默认值 | 取值范围 | 含义 | 影响哪一步 |
|---|---|---|---|---|---|
| `token_budget` | 整数或 `null` | 无（必填） | ≥ 1 的整数，或 `null` | 技能库总长度上限（估算 token） | 每轮结束是否提前停；跑满轮数后是否报错；中间学习阶段的原样输出也受它约束 |
| `rounds` | 整数 | `3` | ≥ 1 | 最多整理几轮 | 轮次循环上限（`token_budget: null` 时实际只跑 1 轮） |
| `merge_check` | 字符串 | `"none"` | `"none"`、`"exam"` | 合并后是否做回归考试 | G8 之后：`exam` 时重做来源考题，失败则撤销合并 |
| `drop_low_value` | 布尔 | `false` | `true`、`false` | 是否丢弃 G7 标出的低价值技能 | G7 之后、合并派发之前 |
| `similarity_input` | 字符串 | `"full"` | `"full"`、`"summary"` | G7 看每条技能的什么 | 只影响 G7 的输入；G8、G9 不变 |

### `none`

| 参数 | 类型 | 默认值 | 取值范围 | 含义 |
|---|---|---|---|---|
| `token_budget` | 整数或 `null` | 无（必填） | ≥ 1 的整数，或 `null` | 已接受技能的总长度超过它就报错（没有合并可以缩短库） |

### 不在 `curate` 里、但会影响整理的设置

| 设置 | 写在哪 | 作用 |
|---|---|---|
| `models.curator` | 实验配置顶层 | G7、G8、G9 用的模型（默认强模型） |
| `models.answerer`、`models.judge` | 实验配置顶层 | 回归考试的作答和判卷（只在 `merge_check: exam` 时用到） |
| `token_budgets` 里 curator 角色的输出上限 | 实验配置顶层 | 整理模型单次回答的最大长度 |
| `--workers` | 命令行 | 同一波里互不重叠的合并单元并行几个；并行与串行的结果逐字节相同 |

## 附录 B：参数从配置到行为的传递路径

```
实验配置 YAML  phases.<L>.curate
   │  core/config.py  _method_mapping：只检查是字典、有 name，原样保存
   ▼
LearnPhaseSpec.curate（字典）
   │  core/config.py  _check_merge_check_needs_exams：merge_check: exam 但序列里没有 A 阶段 → 报错
   │  stages/stage0_freeze.py：冻结时调用 resolve_params("curation", …) 先校验一遍
   ▼
stages/phase_learn.py  _produce_bank
   │  最后一个学习阶段 → phase_bank.produce；其余 → phase_bank.produce_as_is
   ▼
methods/curation/phase_bank.py
   │  resolve / resolve_params → methods/params.py  build_params：
   │    未知参数名、缺必填项、类型不对 → 报错；再跑 AgentBrewParams.__post_init__ 的取值范围检查
   ▼
methods/curation/agentbrew.py  produce_phase_bank
   │  把参数逐个传给 curate_bank（另外传入 curator / answerer / judge 三个模型句柄和 workers）
   ▼
composites/curation.py  curate_bank
   │  token_budget → 每轮结束的 stop 判断、最终超长检查
   │  rounds → 轮次循环上限
   │  merge_check → 合并单元里是否取来源考题、是否跑回归考试
   │  drop_low_value → 决定日志里每轮的 dropped 列表，派发合并前执行丢弃
   │  similarity_input → 原样传给 find_similar
   ▼
blocks/curate.py
   │  find_similar(similarity_input) → _listing：full 用正文，summary 用摘要（缺摘要报错）
   │  merge_skills：解析并校验 G8 的 JSON 输出
   ▼
prompts/curate.py  SIMILARITY / MERGE / GROUP 三个提示词
```

逐段核对的结论：

- **每个参数都传到了使用它的地方**，没有“传了没用”或“用了没传”的参数。
- **默认值只有一处**：`AgentBrewParams` 的字段默认值。`curate_bank` 函数签名上的默认值
  （`merge_check="none"`、`drop_low_value=False`、`similarity_input="full"`）与它一致；
  正常流程里参数总是从 `AgentBrewParams` 显式传入，函数签名上的默认值只在测试直接调用时起作用。
- **校验分两层**：`build_params` 管参数名和类型，`AgentBrewParams.__post_init__` 管取值范围；
  `curate_bank` 入口对 `merge_check`、`similarity_input` 再查一遍，保护直接调用它的代码。
- **v3 配置升级**（`core/config_upgrade.py`）只认 `token_budget`、`rounds`、`merge_check` 三个参数，
  并总会把 `merge_check` 显式写出（exam 方法 → `exam`，live-run → `none`）。新加的两个开关
  没有 v3 写法，只能在 v4 配置里使用。
- **名字相近、含义不同的两个地方**：
  - `curate.token_budget` 是技能库总长度上限；实验配置顶层的 `token_budgets` 是各模型角色
    单次请求的长度上限，两者没有关系。
  - v2/v3 时代的配置键叫 `bank_token_budget`、`curation_rounds`，升级后分别变成
    `curate.token_budget`、`curate.rounds`；`curate_bank` 的函数参数仍沿用旧名
    `bank_token_budget`、`curation_rounds`。

## 附录 C：已知限制

1. **`token_budget: null` 与 `rounds` 的组合不直观。** 没有上限时只跑一轮，`rounds` 不起作用
   （第 5 节第 1 条）。这是 2026-10-16“取消长度上限”裁决沿用下来的行为：整理日志的停止条件
   只有“库不超长”一个，而这个条件在没有上限时总是满足。要“没有上限但跑满多轮”，
   目前没有对应写法，只能给一个足够大的具体上限。
2. **旧的单阶段入口没有调用方。** `agentbrew.run` 和 `none.run`（把技能库写到 run 根目录、
   日志写到 `stage5_curation/` 的旧入口）在现在的阶段式流程里已经不会被调用，只有测试还
   用到 `none.run`。新代码请只用 `produce_phase_bank`（经 `phase_bank.produce`）。
3. **低价值判断没有复核。** `drop_low_value: true` 时，G7 标出就丢弃，不经过任何考试或第二次
   确认；也没有数量上限。被丢弃的技能仍保存在存储里（状态 `discarded`，原因 `low_value`），
   可以从整理日志查到是哪一轮丢的。
4. **G7、G9 是一次性看全库的单次调用。** 库的大小受整理模型上下文长度限制（第 5 节第 4 条），
   没有分批处理。

