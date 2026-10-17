# W3-E 终态组行级回执修复 · 续跑语义评审报告（C 路单路）

- 日期：2026-10-17
- 一句话说明：对分支 `feature/multi-method` 上单提交 `708319c`（"Write a row-level receipt for keep/delete rows on terminal groups"）做的纯静态续跑语义评审——回执守卫与续跑的交互、触发条件边界、金矩阵重生成合规性、审计检查 8 兼容性，以及两个待裁决点的意见；对应 log.md 的 2026-10-17 09:40 条目。
- 评审方式：只读代码与提交差异，未运行任何测试、未执行任何脚本、未修改任何被评审代码。本文件是本次评审唯一产出。

## 总体判断

**通过：0 项阻断、1 项偏移、4 项细节，另附两个待裁决点的建议。**

修复本体实现了裁决指定的语义，且实现方式与账本既有纪律同构：`_terminal_group_receipt`（`src/expedistill/stages/learn_tasks.py:268`）复用 add 路径的目标级行形状（skill_id 与 version 均空），写入前同时检查"成员表非空"与"已有回执"两个短路条件，keep（`learn_tasks.py:294`）与 delete（`learn_tasks.py:323`）两条路径逐字对称接线。三种崩溃重放情形的静态推演全部成立（见第一节），行级回执与成员回执的守卫集合互不干扰。金矩阵重生成符合 generate.py 的"差异落同一提交"纪律，E1–E6 披露报告的变化经静态抽查确实只有 base_commit 一行。审计检查 8 对 skill_id 为空的目标级行"本就跳过"的说法静态核对成立。

唯一的偏移是触发条件本身覆盖不到的一个既有崩溃窗口（发现 1）：它是修复前就存在的缺口，本次修复没有引入它，但让它在单成员组情形下的症状从"丢一条回执"变成"写一条语义错误的回执"，需要留痕或后续裁决，不构成回退本提交的理由。

---

## 一、回执守卫与续跑的交互（评审重点 1，核心核对）

静态推演结论：**三种崩溃情形下，账本都与一次跑完逐字节相同；行级回执（skill_id 空）与成员回执的守卫集合互不干扰。** 推演过程如下。

守卫机制的事实基础：`_receipted_skills`（`learn_tasks.py:254`）按（phase_label, target_id）收集 outcomes 里出现过的全部 skill_id，目标级行的 skill_id 是 `None`，所以一条行级回执使守卫集合变成 `{None}`（非空集合，为真）。

**情形 A：崩溃发生在行级回执写入之前。** 第一趟在 `_terminal_group_receipt` 里 `members or receipted` 判断之后、`_append_outcome` 完成之前中断（含 torn line 的极端形态——`append_jsonl` 的尾部修复语义保证已提交记录永不被截断，半行被移入 `.interrupted-tail` 证据文件，`infra/store.py:248` 起）。重放时决策是冻结报告的纯函数，重推导出同一行（task_list 整文件重写为相同字节），再走一次守卫：`receipted` 仍为空 → 重写这一条 → 账本与一次跑完相同。回执行的每个字段（schema、phase_label、target_kind、target_id、action、skill_id、outcome、version）都来自冻结推导，**不含 reason**，不存在重放间漂移。

**情形 B：崩溃发生在行级回执写入之后、相位摘要之前。** 重放时 `receipted = {None}` 非空 → `_terminal_group_receipt` 返回 False，成员表也为空（组已终态，本相位无任何操作能改变这一点——keep/delete 不创建成员，add 行只作用于 task 目标且与组行不共存于同一任务清单，见第二节）→ 循环零次 → 不写任何东西 → 账本不变。

**情形 C：相位中段重放 / 整段重放。** 相位摘要未落盘时（中段崩溃），`phase_learn` 重跑整个相位：`_write_task_list` 整文件重写为相同字节，逐行落地全部被各自的守卫短路，本行走情形 B。相位摘要已落盘（提交点，`phase_learn.py:107-117`）时，重放整个相位被 `_finished_summary` 短路，连落地代码都不会执行。两个方向都封闭。

**互不干扰核对。** (a) 行级回执 `{None}` 不会抑制任何成员落地：成员循环判断 `skill.skill_id in receipted`，None 不等于任何技能编号；且行级回执只在成员表为空时写入，写入后同一相位重放里成员表不可能再变非空（本相位内没有能往已终态组里加待定成员的路径）。(b) 成员回执（非空集合）使行级回执不触发（`if members or receipted` 的第二支），早趟已回执的目标不叠写——这正是修复承诺的"成员回执已覆盖该行"。(c) 两个守卫读的是同一份账本但语义正交：成员回执以 skill_id 认人，行级回执以"该目标存在任何回执"认行。(d) 与 add 路径的目标级守卫（`learn_tasks.py:353`，`_rows_of` 按同 phase_label+target_id 查任何行）无串扰：同一相位内 add 行（task 目标）与 keep/delete 组行不会共享 target_id——`decide_task_list` 的 current_kind=None 路径只产 add 行，非 None 路径只产组/技能行（`composites/decision.py:658` 起）。

单位测试侧的对应证据：`tests/unit/test_w3e_terminal_row_receipt.py` 七例覆盖了情形 A/B（`test_the_row_level_receipt_is_idempotent_across_a_crash_replay` 断言重放前后账本逐字节相同）、"成员回执覆盖行"（两例）、成员级路径不受扰（`test_pending_members_still_land_member_level`，含技能行的成员级 skipped_terminal 保留断言）。

## 二、触发条件的边界（评审重点 2）

**结论：没有发现误触发路径；keep 与 delete 接入对称；decision 侧不应拦、也不能拦。**

- **组行只可能出自真实测量过的组。** `decide_task_list` 的组行只能来自当前报告里 `injected_skills` 非空的行（`decision.py` 的 targets 过滤），而 live 相位只派发 `pending_groups` 里存在的组（`live_measurement.py:262`："A task with no pending candidate is not a unit"）。所以任务清单里任何组行在决策时刻该组必有至少一个待定成员；落地时成员表为空只能是"测量到落地之间状态前移"——B 相位判定提升时 `finalize_group` 已把幸存成员接受（`live_verdicts.py:404` 的优先级表），或更早相位的修订/终局已把成员丢弃。金矩阵 p06_L 的实例正是前者（p05 轮 1 判提升 → 成员被接受 → p06 的 keep 行解析成空组），见发现 5。
- **决策侧拦截既违反 R4 也无必要。** `decide_task_list` 是纯函数、明确不读 store（"Pure: no store, no ledger, no bank mutation"），它在设计上就不可能知道组当前的成员状态；"skipped and recorded"的落地侧记账正是为这类可见性遗留行准备的语义。缺口只在于修复前"recorded"落空，修复把它补齐，接缝选对了。
- **keep / delete 对称。** 两处的接线逐字同构：都是先 `receipted = _receipted_skills(...)`，再 `members = _target_members(...)`，再 `if _terminal_group_receipt(...): return`，再遍历成员。没有只修一条路径的偏斜。
- **技能行不受影响。** `_target_members` 对 skill 目标无条件加载（存在性由 CodeBugError 保证），成员表永远非空，行级回执只可能由组行触发——与修复声称的触发范围一致。

## 三、金矩阵重生成的合规性（评审重点 3）

**结论：合规。** 按 `tests/golden/generate.py` 的纪律（"DELIBERATELY changes run artifacts → land the diff in the same commit"，以及"A step that changes nothing must not touch the matrix"）逐项核对：

1. **差异落同一提交**：是——四个 live 夹具、provenance、披露报告都在 `708319c` 里。
2. **改动范围与"故意变化"精确吻合**：只有 4 个 live_run 组合变化（各增 1–2 条 p06_L 的行级回执、summary 的 outcomes 计数、以及新出现的 outcomes.jsonl 本体与 .lock 空副标，见发现 2）；4 个 exam 组合零改动——exam 组合的任务清单是技能行，技能行不触发本修复，符合"改不动就不碰"。
3. **E1–E6 披露报告"只有 base_commit 变化"的静态抽查**：成立。`tests/fixtures/golden_acceptance/wave4_e1_e6_disclosure.json` 在本提交的 diff 里恰好只有 `golden_matrix_base_commit` 一行从 `686121f` 换成 `910ebbb`；且该字段由 `tests/golden/semantics.py:1132` 直接取自 provenance 的 `base_commit`，机制上不可能"顺手"改别处。
4. **base_commit 记父提交是家规**：见发现 4。
5. E5 的 8×3 续跑等价门是运行期断言（generate.py 对每个组合三档崩溃点重跑并断言快照相等），本评审未复跑；静态侧的支撑是第一节的守卫推演 + keep/delete 路径无模型调用（崩溃点按模型调用序号注入，永远落在 keep/delete 落地代码之外，回执只会在续跑段里被写一次）。

## 四、审计兼容（评审重点 5）

**结论：检查 8 的"本就跳过"说法静态成立。** `scripts/audit/audit_live_run.py:949`：`continue  # target-level rows (skips) name no skill`——skill_id 为空的行在通过 phase_label（938 行附近）与 schema（944 行附近）校验后被直接跳过，不进"每（目标，技能）一条回执"的重复检查、也不进与技能库的状态对账。新的行级 skipped_terminal 回执三个字段全部合格（schema 对、phase_label 对、skill_id 为 None），因此检查 8 对它零动作。新增测试 `test_the_audit_check8_accepts_the_row_level_receipt` 用实现的真实产物跑过 `check_learn_ledgers` 并断言零 ERROR，覆盖到位。

需要点破的反面：正因为检查 8 对目标级行完全不核对（既不查重复、也不对账、summary 的 outcomes 计数也没有账本核对项），**行级回执的全部防重与正确性都压在写入侧守卫上**——第一节的推演说明守卫成立，但未来任何想在检查 8 里加"summary 计数 ↔ outcomes 行数"核对的改动，都要先面对发现 3 的夹具形状问题。

## 五、逐条发现

**发现 1（偏移，非阻断）：触发条件区分不了"相位开始前就已终态的组"与"本相位自己的落地推动了存储、但没来得及写成员回执"的组，后一情形会写一条语义错误的行级回执。**

证据链：`_land_keep` 的落地顺序是先 `store.save`（成员变 accepted）后 `_append_outcome`（`learn_tasks.py:304-308`），`_land_delete` 同构（`learn_tasks.py:333-335`）；组行成员表经 `pending_groups` 只解析待定成员（`learn_tasks.py:247-248` + `live_measurement.py:282-283` 的 `status != "pending": continue`）。于是单成员组的 keep 行存在这样的窗口：崩溃落在 save 与 append 之间，重放时成员已被本相位接受、不再是待定 → `members=[]`，而该目标尚无任何回执 → `receipted=∅` → 行级 skipped_terminal 落账。这条记录说"本行无事可做"，实际是本相位接受了该成员；账本与一次跑完（成员级 accepted）不逐字节相同，与提交信息里"resumed ledger equals an uninterrupted one's byte for byte"的表述冲突。

定性：该崩溃窗口是**修复前就存在的既有缺口**——多成员组的同窗口重放早已会丢失已落地成员的回执（keep/delete 的成员落地没有 revise 那样的版本号完成证明），本提交没有引入它；新代码只是把该窗口在单成员组下的症状从"丢一条回执"变成"多一条错标的回执"。E5 的崩溃注入按模型调用计数，keep/delete 路径零模型调用，所以等价门永远打不进这个窗口；单元测试也没覆盖它。审计检查 8 对两种症状都不报错（丢回执没有核对项；错标回执被 skill_id=None 跳过）。

建议（不要求回退本提交）：(a) 在 `_terminal_group_receipt` 或模块 docstring 注明该前提——行级回执假定"无落地未回执窗口"，与 keep/delete 成员回执的既有前提一致；(b) 若要彻底收口，应做在审计侧而非写入侧：检查 8 增加一条"组内成员 accepted 且本相位无成员回执"的 WARN 级交叉核对（信息在 verdicts 账本与技能库里都有），作为下一个触及审计的提交的一部分。

**发现 2（细节）：金矩阵的实际差异比提交说明多两样——p06_L 的 outcomes.jsonl 文件本体与其空 .lock 副标此前根本不存在。** 修复前末学习相位一次 append 都不发生，该相位目录里没有 outcomes.jsonl；修复后第一次 append 让文件与 `append_jsonl` 的 flock 副标（`infra/store.py:143-144`）一起出现在快照里。提交说明只写了"each gains one outcomes row and its summary count"，披露口径不完整，但差异本身完全在修复的因果线上，无合规问题。留档仅为后续读 diff 的人少一次困惑。

**发现 3（细节）：审计自测夹具固化的终态组回执形状与实现不一致。** `scripts/audit/audit_live_run.py:1690-1701`：对 p06:Lfinal 的一条 delete 行写了两条 `skill_id: None`、`version: 2` 的目标级行，summary 记 `skipped_terminal: 2`；而实现是每个目标一条、version 为 None。该夹具早于本修复（W3-B 审计迁移时手搭，当时实现甚至一条都不写），检查 8 跳过目标级行所以自测照过；但它把"每成员一条"这个从未被任何实现产出过的形状写成了自测基准，若未来在检查 8 里加 summary 计数核对（见第四节），自测会为错误形状背书。建议下一个触及该文件的提交顺手改成一条、version None。

**发现 4（细节）：provenance 的 base_commit 记父提交，单看该字段无法复现矩阵。** `708319c` 记 `910ebbb`（父），生成实际跑在带未提交修复的脏树上。查历史：a6ec16b 起全部七次金矩阵再生成的 base_commit 都是当时提交的父（910ebbb/686121f/7fadee2/84d280e/350b40d/00971f2/dad6d28 逐一比对确认），这是既定家规，本提交未偏离；留档说明其含义——base_commit 表示"生成时的 HEAD"，代码差异要看同提交的 src diff。

**发现 5（细节，兼对裁决点 a 的事实输入）：归档金基线里行级 skipped_terminal 的全部现存实例都是"成员已被接受"类。** 四个 live 组合的 p05 轮 1 判提升、`finalize_group` 接受了幸存成员，p06 的 keep 行因此解析成空组——p06_L 的 skill_bank.jsonl 里带着修订标记的成员可证。也就是说，若裁决点 a 最终选择给"已接受类"另立回执名，金矩阵里现存的每一条行级回执都要改名重生成；这个事实应进入该裁决的成本核算。

## 六、两个待裁决点的建议（评审意见，不是裁决）

### a. 已接受类是否另立回执名（行级 already_accepted 形态）？

**建议：维持统一 skipped_terminal，不改名；补一句 docstring 说明覆盖范围。**

从账本消费者的角度：

1. **行级视角没有可命名的单一成员。** `pending_groups` 返回空表时，"接受还是丢弃"这个信息已经被解析过程抹掉了；要区分就得额外按任务重查全状态成员表。且混合组（部分成员早被接受、部分被丢弃）下，already_accepted 对被丢弃的那部分是说谎的；skipped_terminal（"本行无可解析的待定成员，是可见性遗留"）对一切混合都为真。
2. **审计检查 8 对目标级行一律放行，改名对审计零收益**；人工读账要分"接受/丢弃"，verdicts 账本与技能库就在旁边，审计检查 1 已经做了组级判定与成员状态的对账——不需要回执行自己承载这个维度，账本保持单一职责（"这一行做了什么"）更可读。
3. 支持改名的最强论据是成员级词表确实区分 already_accepted（keep 路径，`learn_tasks.py:310`）——但成员级能区分的前提是"被点名的那个成员的状态直接可知"，行级没有这个前提（见 1）。
4. 成本是实打实的：金矩阵四个 live 组合再重生成一轮 + 归档基线语义漂移（发现 5），换来的只是一个读账时的提示。

建议的落地动作：在 `_terminal_group_receipt` 的 docstring 补半句"terminal 包括被丢弃与已被更早动作接受两类"（现有"no resolvable pending member"已基本表达），并把这条命名裁决记入 docs/decisions，免得下个读者再复议一遍。

### b. revise 行的同类缺口（指向已终态组零回执）——本提交一起修还是留待下次？

**建议：留待下一个提交单独修，不回改本提交，也不长期搁置。**

理由：

1. **缺口确认存在且形状完全同构**：`_land_revise` 在 `actable` 为空时遍历 `members` 写成员级 skipped_terminal（`learn_tasks.py:415-422`），`members` 为空时循环零次、静默 return——与修复前的 keep/delete 一模一样。技能行不受影响（成员表永非空、终态成员有成员级回执），缺的只有"组解析为空"这一种形状。
2. **不是续跑等价缺陷，无紧迫性**：什么都不写在崩溃重放下同样稳定，E5 不受影响；问题只是模块"skipped and recorded"承诺在 revise 行上同样落空——与本次悬案同级、同级就意味着不值得为它打乱已落地的提交。
3. **修法现成、成本低**：`_terminal_group_receipt` 已按 row 参数化，`_land_revise` 的 `if not actable:` 分支在 `members` 为空时调用它即可，keep/delete/revise 三路同构。注意触发条件要镜像 keep/delete——只在 members 整表为空时写行级回执，成员非空但全终态时保持现有的成员级回执。
4. **流程干净**：AGENTS 十一"一个提交只做一件事"；修 revise 大概率还要再过一轮金矩阵重生成（若金矩阵的 live 组合里 revise 行实际出现该形状——本次评审未在夹具中见到 revise 行的终态组实例，修时需先确认，未出现则矩阵零差异），分开落地让每份 diff 各自可读。

## 七、结论

`708319c` 可以按原样留在分支上：修复语义正确、续跑守卫推演封闭、金矩阵重生成合规、审计兼容成立。带走的后续事项按优先级：(1) 发现 1 的 docstring 留痕与审计侧交叉核对（下个触及审计的提交）；(2) 裁决点 b 的 revise 缺口（下一个提交）；(3) 发现 3 的自测夹具形状顺手修正；裁决点 a 建议按"不改名 + decisions 记录"关闭。
