# M4.1 验收评审 A——仪器正确性

日期：2026-10-16
一句话说明：执行—学习重构 M4.1（语义等价仪器先行）的验收评审 A——仪器正确性：它断言的东西必须是方案要的东西（E1–E6 投影逐准则忠实性、v4 布局映射表逐行核实、E5 修订窗口因果链、skip_audit 容差扩展健全性、tamper 反证可信度、基线束完备性、全套亲跑）。
对应 log.md 条目：2026-10-16 17:55 M4.1 语义等价仪器先行落地：E1–E6 断言 + 归一化/布局映射 + 69904f7 基线束 + 自检反证。

评审对象：commit 2ca0632（`git diff aee0031 2ca0632`，15 个文件：tests/golden/semantics.py 711 行、tests/golden/test_semantic_instrument.py 485 行、tests/golden/extract_baseline.py、tests/golden/harness.py（+43/−4）、基线束 8+1 JSON + README、log.md）。src/ 零改动、金矩阵 fixtures 零改动。
契约：docs/decisions/execute_learn_implementation_20261015.md §8（E1–E6 与归一化、live 白名单、P6 覆盖面声明）、§9 M4 行的 M4.1 子步、§3 相位目录布局、§4 序列定义。
方法：逐准则把仪器视图的取数路径与 src/ 现行落盘代码对照（stage3/stage4/stage5/stage6、acceptance、live_acceptance、batch_execute、metrics、store、call）；E5 窗口与 skip_audit 的关键断言本机亲验（崩溃 run 目录逐文件检查 + 续跑等价复跑）；全套 pytest/ruff/test_layering 亲跑；基线 provenance 与 69904f7 原始 provenance 逐组合比对。未读 B/C 两份评审。

---

## 逐项核查

### 1. E1–E6 投影忠实性（对照方案 §8"必须相等"清单）

**E1（库行 8 字段）——吻合**。`_e1`（semantics.py:242-249）读 skill_id / content / version（存为 final_version）/ status / discard_reason / group / merged_from / rewrite_history（整条），与方案 §8 E1 的字段集逐一对应，rewrite_history 逐条整体比较（"逐条"落实为整 dict 不拆）。bank 行即 `Skill.to_dict()`（methods/curation/none.py:48-49、composites/curation.py:1303-1304 只写 accepted 成员），字段全部存在（core/models.py:231-248）；对象新字段（source_subtasks、schema_version）与方案未要求比的 subtask_id 都被显式投影剔除——"对象新字段剔除"归一化即投影本身，成立。

**E2（终态/未出生/组终局/账本）——数据面吻合，映射面有一处失实（见 P-A1）**。
- terminals 从 bank 行取 (skill_id, status, discard_reason)（semantics.py:259-261）——金矩阵全接受，bank 即完整终态集；弃用分支的恒空真由方案 P6 预先声明，我逐份验证 8 个基线的 unborn/generation_failures/rewrite_failures/revision_failures 确实全空。
- groups 从 verdicts 末轮行推导（:277-284，round 取 max、`verdict` 或 `better`→improved/not_improved），与 verdicts 行形状（live_acceptance.py:789-815，零执行行带 `verdict` 字段）吻合。
- leak_verdicts 四字段（:289-293）与账本行形状（composites/acceptance.py:18 `{"skill_id","skill_version","leaked","reason"}`）逐字段一致。
- 三类失败账本整行多重集；`_compare_e2` 的"双空跳过"（:433-434）只在两侧皆空时跳过，单侧非空照样报红，不弱化。
- generation_failures 的键映射：`make_skill_id(row["subtask_id"])`（:269）＝ `subtask_id + "/skill"`（core/ids.py:88-89），正确。

**E3（C 相位 summary + retrieval_audit）——吻合，一处死投影（S-1）**。with_bank / control（视图改名 bare）/ delta 三桶整字典比较（:302-312）——delta 的两个分母口径（avg_at_n / avg_at_n_conservative，blocks/metrics.py:118-190、stage6_test.py:195-226）随整字典全量进比较，不存在"要求比而没读"的分母。retrieval_audit 整行多重集（I4 落实）。control_source 别名表 5 个 token（:136-142）：现值域 {stage1_reuse, fresh_bare_run}（stage6_test.py:330,357）全覆盖（基线实测 full 组合全部规范化为 D_weak_arm、split 全部 fresh_bare_run），另 3 个（D / phase_D / p01_D）是 §4"C(full) against: D" 推导的预测 token，且只归并同臂同义名——v4 若错用 fresh_bare 仍会红，不会假绿。

**E4（调用多重集）——吻合**。(role, prompt_name, request_text)（:315-319）与 llm dump 落盘字段（llm/call.py:457,639）一致；T9 的"渲染请求文本"＝ dump 的 request_text。dump 的 `system` 字段不进比较（S-5，方案字面即三元组）。

**E6（live 账本逐行）——吻合**。verdicts + attributions 整行多重集（:322-326），round 经 v4_layout 改写为相位序号（见第 2 项）；attribution 计划行含 skill_actions 逐技能计划（基线实测），I4"防判据计数漂移但 better 布尔不变绕过"由整行比较落实。exam 组合金侧恒空 + "fresh 有则红"的钉住（:477-479）方向正确。

**方案要求比而仪器没读的**：逐条对照 §8 清单，除 S-1 的死投影（aux 实际经 with_bank/delta 传递性覆盖）外没有缺项；live 白名单②（merge_check:none 不可观测）与 D 相位 shortfall 按 §8 明文转 M3 单测/续跑套件/真实 run 披露，仪器不承担，忠实。

### 2. v4_layout 映射表逐行核实（对照 §3"只放"清单与 §4 序列）

SEQUENCES（semantics.py:104-107）：exam=(D,L,A,L,A,L,A,L,C)、live_run=(D,L,B,L,B,L,C)。exam 序列的 A 数取决于 retry_limit 缺省——methods/acceptance/__init__.py:34 `retry_limit: int = 2`，金矩阵 exam 配置不带 acceptance 槽 → R=2 → [A,L]×3，与 §4 转换表"[D,L]+[A,L]×(R+1)+[C]"一致（test_phase_sequences_match_the_upgrade_v3_table 用字面量钉住方案文本）。`phase_dir`＝`phases/p{index+1:02d}_<局部名>`（:110-112），与 §3 `phases/pNN_<name>/`、§6 p00:freeze 之后 pNN 标签一致。

逐行（v4_layout，:145-185；形状由 test_v4_layout_pins_the_predicted_phase_paths 钉住）：

| 表行 | 判定 |
|---|---|
| C 相位 summary.json、retrieval_audit.jsonl → C 相位目录 | §3"相位 summary"+§8 E3；retrieval_audit 是交付驱动在 C 相位内审计的账本，落 C 相位目录合理；§3 未点名 retrieval_audit 与文件名 summary.json 均为预测值——预测失准＝响亮红＋改数据表（安全方向） |
| 逐 B 的 verdicts 分片（round→B 序号 0/1） | §3 verdicts 为逐相位账本；§2"Attempt.retry_round 语义升版"同类，round=相位序号与 v3 轮号 0/1 对齐；分片并接按表序＝相位序＝轮序，确定性成立 |
| B 后 L 的 attributions / revision_failures（round→B 序号−1） | §3 attributions 逐相位；G10/G11 在 B 后的 L 执行（§3 决策清单）；两账本行均带 "round" 字段（live_acceptance.py:567,1016），字段名正确 |
| A 后 L 的 rewrite_failures（retry_round→A 序号 0/1/2） | §3 明文"rewrite_failures——改写失败账本随改写搬进 L 相位目录，键含相位"；行带 retry_round（acceptance.py:259-268）；A 序号 0-2 与 v3 exam 轮 0-2 对齐 |
| 每个 L 的 generation_failures（无 round 字段，多分片并接） | G2 只在首个 L 的 add 路径跑（§3 决策清单），实际只有 p02 分片非空，并接顺序按相位序确定；§7 装配器映射清单把 generation_failures 列为相位目录文件，互证 |
| 对象仓 / skill_bank / leak_verdicts / llm_requests 无映射 | canonicalize 未命中键原样透传（:207-209）；与 P4（对象仓 run 级、目录名不变）与 B2-4（leak_verdicts 留 attempts 仓）逐字一致；`_skill_in_store` 的路径拼法与 store.path_for（store.py:367-370，quote(safe="")）逐位一致，P4 下两布局同样可用 |

"能不能直接用"的结论：**表本身在 M4.3 真实布局出来时可直接用或响亮失配后改表**——失配的全部失败方向都是红（错表→canonical 键空→红；多映射→红），不存在静默绿路径（唯一病态例外是 v4 同时在预测路径写对数据、又在别处放真数据，属自证其罪型）。两处不在"改表即可"逃生舱内的预测：C summary 的文件名、以及 summary 内部字段名（v3 键 `control` vs 方案 §8 用词 `bare`，若 M4.3 改名则 `_e3` KeyError——响亮，见 S-4）。

### 3. E5 修订窗口变体（评审 C-1）

因果链逐环核实，成立：

1. **标记在块 ⇒ 修订已落库**：G11 修订先 `stored.content = new_text; store.save(stored, "skills")`（live_acceptance.py:1237-1239）才返回；下一轮注入块由 `survivors_and_block` 从 store 当前内容纯派生（:1249-1261，docstring 自述 pure derivation 与代码一致）。金夹具里标记唯一来源是 G11 脚本（harness.py:246-249），故标记在块 ⟺ 修订已持久化，无旁路。
2. **崩溃点在重答执行前**：RevisionWindowCrash 在首个携带标记的 `run()` 前抛 SimulatedCrash（semantics.py:616-622）。`run()` 只被 K1 单元执行调用（blocks/execute.py:89）；标记块首次出现即在 round 1 派发（round 0 无标记、no-op 守卫只比不执行）。K1 的 error-stop 门 set 后停止派发（batch_execute.py:521,557-569）。
3. **"零条重答轨迹"证明了什么**：test_revision_window_crash_lands_mid_reanswer（test:461-485）在崩溃点上断言 (a) store 有带标记技能（修订已落库）、(b) round01/stage1_baseline/trajectories 为空（重答零持久化）、(c) 续跑后与 straight 快照全等。加上 `_crash_in_revision_window` 自身的两个守卫（崩溃未触发→AssertionError；store 无标记→"fix the crash predicate, do not weaken the check"，semantics.py:671-676），窗口的两个性质（已落库/未重答）在崩溃点上都被独立证明，且断言不许弱化被写进了守卫。**我本机复跑验证**：崩溃 run 里 store 带标记、round01 轨迹目录为空、崩溃恰好停在 t1 的首个 round-1 单元、续跑后 `assert_resume_equivalent` 通过。
4. **exam 组合无窗口的豁免成立**：金夹具 exam 全部首轮通过（基线 rewrite_history 全空、rewrite_failures 全空，亲验），exam 侧"修订已落库、重答未发生"窗口在金矩阵不可达；docstring（semantics.py:578-580）指认的替代钉子真实存在且更强——tests/composites/test_acceptance_resume.py 的 crash-at-every-model-call / crash-at-every-write（:279,:388）覆盖每个改写边界。

### 4. skip_audit 容差扩展的健全性（harness.py:540-604）

- **动机真实，且我亲验**：窗口变体的崩溃 run 确实多出 1 条 `gate:"error"` 的 skip_audit 行 + lock sidecar（在 round01 的 K1 目录里）——这是 K1 error-stop 门在进程内异常展开时给排队兄弟单元写的记账（batch_execute.py:517-528），真进程死亡写不出来。而三个分数崩溃变体**不产生**任何 skip_audit 文件（exam-whole_bank-full 与 live_run-retrieval-split 各取 0.6 槽亲跑，run 目录无 skip_audit*）——原因结构性的：假模型 run() 不发模型调用，分数崩溃从不落在 K1 派发内。
- **不弱化既有门槛**：assert_resume_equivalent 的既有使用者只有金矩阵站立套件（test_golden_matrix.py:55-65，2 组合×0.6 槽）与 generate.py（再生时 8×3）；它们从不产生 skip_audit 行（上一条 + 旁证：skip_audit 代码 3cfdc90 早于金矩阵生成 a6ec16b，generate.py 当时就全绿），扩展对它们零行为变化。两套组合层续跑套件（test_acceptance_resume.py / test_live_acceptance_resume.py）用各自的 snapshot 比较器（live 套件 :328,:373 本地实现），与该函数无关。金矩阵 24 个崩溃变体的语义不变；扩展只服务于新窗口变体。
- **容忍的行无状态消费方，亲验**：src/ 内 skip_audit 只有 K1 自己写/心跳数行数（batch_execute.py:517,525,580,590）与装配器负清单排除（assemble_reuse.py:169，永不拷贝）；scripts/ 无读者。真丢单元由结果态兜住——单元被错误跳过 ⇒ 其 trajectory 文件缺失 ⇒ 其余快照键报红；错误重跑 ⇒ 请求历史 novel-call 检查报红。因此"可多不可缺"的容差方向不会掩盖真实续跑 bug。
- **披露如实**：commit message（"Found by the new window variant"）与 log.md (a) 都写明了机制、方向与兜底理由。

### 5. tamper 反证可信度

11 个 test_tamper_*（test:272-416）。抽查的实现：

- **test_tamper_discard_reason_reds_e1_and_e2**（:283-294）：同一 skill_id 在 E1 bank_rows 与 E2 terminal_states 两节同步改 discard_reason——模拟真实弃因变化在两个投影里的一致呈现；红的原因正确（两个多重集各失配一行，diff 指认 discard_reason 字段）。与 test_tamper_e1_bank_row_content（只动 content，仅 E1 红）合起来覆盖了独立与耦合两个方向。
- **test_tamper_snapshot_verdict_deletion_couples_e6_and_e2**（:327-340）：在**快照层**删末轮 verdict 行再重投影——证明 E2/E6 从同一账本取数时的真实耦合（末轮行丢失 ⇒ 组终局翻转），这是 bundle 级篡改证明不了、且测试注释（:311-316）明确区分了的一层。诚实。
- **test_tamper_extra_exam_gen_call_in_live_fresh_fails_whitelist**（:354-368）：fresh 侧（按 v4 形状比较）多一条 examiner/EXAM_GEN → 白名单期望集（v3 删光 EXAM_GEN）之外出现 extra → 红，diff 指名 EXAM_GEN。反向也成立：金基线自身含 3 条 EXAM_GEN，直接按 v4 比较也红（log.md (b) 如实记录，身份自检走 layout="v3" 朴素比较）——白名单是 v3 基线↔v4 产物的单向关系，实现与声明一致（:459-472）。
- 其余核对：unborn 注入只红 E2 不红 E1（未出生投影 E2 独占）、组终局翻转只红 E2、retrieval_audit 换 task_id 是多重集失配（纯重排不算差）、summary 字段篡改 diff 精确到 `with_bank.pass_at_n`。篡改构造均只动声称的字段；`_expected_red` 要求"恰好这些准则红、其余全绿 + 红准则有 diff 文本"，反证是严格的。

**合成 v4 测试是否循环论证**：`_synthetic_v4`（test:177-234）的重命名确实复用同一张 SEQUENCES/phase_dir——但它证明的对象是 canonicalize 的**逆映射性质**（round 字段被分片位置覆盖、分片并接顺序、live 删光 EXAM_GEN 后与基线全等），不是"M4.3 一定会写这些路径"。独立于被测代码的期望来源有两处：v3 侧证据是 69904f7 的金夹具本体（git show 读取、提取器逐字节复现被站立测试钉住——test:136-149）；序列与布局预测锚在方案文本上（§3/§4，字面量钉住）。真正无法预先证明的部分（M4.3 真实布局 vs 预测）被如实留给 M4.3 首跑裁决，失配方向是响亮红。结论：**不是阻塞性的循环论证**，但合成测试的证明范围限于"表的 v4 侧逆映射正确"，这一范围在文档（semantics.py:60-62、README）里声明得清楚。

### 6. 基线束完备性

- 8 组合 = exam×live_run × whole_bank×retrieval × full×split 全展开（harness.py:81-86 COMBOS），8 份视图文件逐一在场；维度差异面在数据里真实出现（full 的 control_source=D_weak_arm vs split 的 fresh_bare_run；live 的 verdicts/groups/attributions vs exam 的 leak_verdicts；retrieval 的 audit 行 vs whole_bank 的空；full 4-6 库行 vs split 2-3）。
- provenance 逐组合与 69904f7 原始 provenance 比对：**8/8 全部一致**（model_calls 34/18/34/18/28/15/28/15，crash_points 逐位相同，crash_fractions 相同；抽查要求 2 组合，实际全核）。test_baseline_provenance_matches_the_variant_formula（test:434-438）另钉 crash_points == crash_points(model_calls)。
- 提取器：git show 不 checkout、输出字节确定（排序键/固定缩进/无时间戳），站立复现测试在套件内。体积 416 KiB 与声明相符。

### 7. 验收亲跑（2026-10-16，本机）

- 全套 pytest：**2084 passed + 3 deselected**（53s），与提交声明 2040→2084（+44）一致；44 条新测试的参数化展开逐一核对（22 个函数，8+2+8+8 参数化）。
- ruff（E9/F）：All checks passed。
- test_layering：3 passed。
- 金矩阵 fixtures：`git diff 32d6c86 2ca0632 -- tests/fixtures/golden/` 零改动。

---

## 发现清单

### 【偏移约束】

**P-A1（不阻塞金矩阵验收）：E2"未出生"映射的判别器失实——v3 侧分支不可达、注释描述与 v3 代码矛盾、"v4 bank discard"说法不成立，方案的 v3未出生↔v4 discarded 桥接无法由该仪器表达。**
semantics.py:263-275 对 generation_failures 行用 `_skill_in_store` 判别：技能在 store → 合成 `terminal(discarded, model_output_error)`；不在 → unborn。三处失实：

1. 注释（:264-266）称 v3 侧"G3 failed after G2 succeeded ⇒ with its skill written"。但 v3 的 stage3 是**成对落盘**：`store.save(skill)` 在 G2、G3 都成功之后才执行（stage3_generation.py:270-279 先写失败行并 return，:284 才存技能）。正常流里 generation_failures 行**永远**没有已存技能，判别器在 v3 侧恒 False——"合成 terminal"分支对真实 v3 数据不可达（唯一可达路径是崩溃恰好落在 :284-286 两次 save 之间且恢复后 G3 再耗尽，而那个角落里 v3 的真实状态是技能 pending 滞留 store，合成映射把它报成 discarded，同样是失实）。
2. 注释（:266-267）称该情形"it is a v4 bank discard"——**discarded 技能不进 bank**（bank 只写 accepted 成员：composites/curation.py:1289,1303-1304、methods/curation/none.py:48-49）。v4 侧被弃技能的持久状态在 **skills store**，而仪器的 terminal_states 只从 bank 读——v4 侧的 discarded 技能对该仪器**结构性不可见**。
3. 因此 §8 E2 要求的跨表示映射（v3 未出生 ↔ v4 discarded）无法成立：真实 G3 耗尽场景下，v3 侧落 unborn、v4 侧（若给失败单元写 generation_failures 行）落 terminal_states → 两侧落在不同视图节 → 假红；若 v4 不写行（A 相位的失败账本不在布局表内）→ v4 侧完全消失 → 也假红。失败模式只有假红没有假绿。

**为什么定级偏移约束而非琐细**：这是仪器对方案条文的实现（§8 E2 映射 + §5"G3……E2 有映射"），而其 v3 侧前提与现行代码矛盾——后续任何人把该仪器用于带弃用分支的场景（例如 M4 验收想偷懒用金仪器代替场景测试）会继承一个坏桥接。**为什么不阻塞**：金矩阵 8 组合全部 unborn/generation_failures/rewrite_failures/revision_failures 为空（亲验），§8 P6 覆盖面声明已把弃用分支钉住移交 M4 验收的场景测试（test_acceptance.py / test_live_acceptance.py），M4.3 金矩阵验收路径完全不经过该分支。**处置**：在 M4.3 提交（或先行小提交）内修正 semantics.py:262-267 注释使其与 v3 成对落盘事实一致，并声明 `_skill_in_store` 的合成分支是 v4 侧预测、金矩阵恒空；若 M4 验收要把仪器扩展到弃用场景，须改为真正的桥接（v4 侧从 skills store 的 status/discard_reason 读终态）而非当前的同侧判别。

### 【琐细】

- **S-1**：E3 视图的 `"aux"` 键读 `summary.get("aux")`（semantics.py:308）——stage6 summary 没有顶层 aux 键（stage6_test.py:401-412；aux 数据在 with_bank/control/delta 的 "aux" 子键，blocks/metrics.py:159-190），该投影恒为 None、双空恒等。实际 aux 已随三桶整字典比较被传递性覆盖，无覆盖缺口；但这个具名投影是死的，注释上会误导人以为 aux 有独立比较通道（若 v4 把 aux 提为顶层键它反而会转红）。
- **S-2**：计数漂移——test_semantic_instrument.py 模块 docstring（:14）说 "nine targeted corruptions"，基线 README（:35）说"9 种定向篡改"，实际 11 个 `test_tamper_*`；commit message 与 log.md 都说 11。两处文档改成 11 即可。
- **S-3**：test_e5_all_variants_equivalent（test:454）断言现跑 model_calls == 69904f7 provenance 的 model_calls——对 live 组合，M4.3 落实白名单①后调用数合法降为 28−6 / 15−3，该断言会转红。这是预期内的 M4.3 测试更新（方案 §7 假模型脚本重写同属 M4.3），但 run_e5 的 docstring 没有预警这一处 v3 特定期望，M4.3 别把它误读成行为漂移。
- **S-4**：仪器逃生舱（"edit SEQUENCES / v4_layout，断言函数不硬编码路径"，semantics.py:60-62）只覆盖路径与 round 字段；`_e3` 读 `summary["control"]`（方案 §8 的用词是 bare）、C summary 文件名 `summary.json`、retrieval_audit 落点都是预测值，M4.3 若采用不同名，失败是 KeyError/红（响亮、安全），但需要改的是视图代码而非数据表——文档应把这层也点名。
- **S-5**：E4 不比 dump 的 `system` 字段——与方案三元组字面一致；金矩阵里 system 恒为同一常量、prompt 受 manifest digest 冻结，残余风险仅剩 system_override 类改动（现只有 benchmarks/react_loop.py 用），记录在案即可。

### 【赞同】

- E1 八字段投影与方案逐字对应，rewrite_history 逐条整比；对象新字段剔除＝显式投影，当前金矩阵（v2 对象）对 v1 基线全等的站立测试同时持续钉住该归一化。
- v4_layout 逐行与 §3/§4 推导一致（第 2 项表）；exam 序列的 retry_limit 缺省 2 核实无误；对象仓/bank/leak_verdicts/llm_requests 无映射与 P4/B2-4 逐字一致；canonicalize 的分片并接按表序确定、round 字段以分片位置为准（合成测试用相位绝对值证明覆盖生效）。
- E5 窗口变体的因果链三环全部核实（第 3 项），且崩溃点守卫自带"不许弱化检查"的断言；exam 侧豁免的理由与替代钉子真实存在。
- skip_audit 容差的动机、影响面、无状态消费方、既有测试零行为变化全部亲验成立（第 4 项）；"可多不可缺"方向正确。
- 11 种篡改各自"只动声称字段 + 恰好对应准则红 + diff 指认"抽查成立；快照级耦合篡改弥补了 bundle 级篡改证明不了的投影层。
- 白名单①机判方向正确（v3 基线↔v4 产物的单向关系，身份自检走朴素比较）；金夹具 live 的 EXAM_GEN 6/3 次与方案 §8 记载一致。
- 基线束 8 组合覆盖全维度差异面；provenance 8/8 与 69904f7 一致；提取字节确定且可站立复现。
- 验收亲跑全过：2084 passed + 3 deselected、ruff、test_layering 3 passed、金矩阵 fixtures 零改动。

---

## 裁决

**M4.1 验收通过。**

理由：仪器的全部金矩阵断言（E1–E4/E6 视图、归一化、v4 布局表、live 白名单①、E5 变体机器与窗口变体、基线束）与方案 §8/§3/§4 逐条对照成立，tamper 反证证明每条断言都能因正确的原因转红，全套亲跑与声明一致。唯一偏移约束 P-A1 位于方案 P6 预先声明的"恒空真"分支内，不影响 M4.3 的任何验收路径，且失败方向是假红而非假绿——按 M3 C-1 的先例，随 M4.3 提交（或先行小提交）修正注释与分支声明即可，不阻塞进入 M4.2/M4.3。琐细五条（S-1…S-5）随下次触碰相应文件顺手处理，其中 S-3 需 M4.3 记住：live 组合的 model_calls 期望值要按白名单①差量改。
