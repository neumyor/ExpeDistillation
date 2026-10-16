# Wave 2（相位驱动取代六阶段 + 集成收口）续跑语义评审报告

- 评审对象：分支 `feature/multi-method` 提交区间 `16c3791..1df37d4`（W2-A 执行相位驱动、W2-B 学习相位驱动、W2-C 配置 v4 与相位循环、收口提交 `1df37d4`）
- 评审代理：Wave 2 续跑语义（C 路）
- 日期：2026-10-16
- 方式：纯静态评审。只读代码与提交差异，未运行任何测试、未执行任何脚本、未修改被评审对象。

## 总体判断

**不通过：发现 1 项阻断。** 执行侧与学习侧各自的续跑机制是扎实的——重派预算的持久计数、修订的版本号重放、live 账本协议的"判定行先落、提交可补"、`--until-phase` 的重放模型，静态推演与测试断言都对得上。但三个代理的接缝上断了一条链：**A 相位（exam）的测量宇宙从上一个学习相位的技能库产物里筛 pending 成员，而库产物的两个写方（`produce_as_is` 与 agentbrew 的 `produce`）只写 accepted 状态的行；学习相位新增的技能恰恰是 pending，永远进不了产物。于是 exam 方法（`D L A L A L C`）的每个 A 相位宇宙恒为空，考题、答卷、判卷一概不发生，技能永远停在 pending，技能库恒为空，最终 C 相位对着空库出终测数字——整个 run 静默地空转完成。** 三个代理的测试各自全绿的原因是：W2-A 的 exam 测试用 `write_bank_artifact` 手工把 pending 行写进产物（真实写方从不产这种文件），W2-B 的测试只跑学习相位、手工播种考卷状态，全仓库没有任何一个测试同时驱动真的学习相位与真的 A 相位。

另有 1 项**偏移**（"从第一个相位重放"的恢复模型会改写已完成相位的盘上工件：跨相位库产物被按当前 store 状态重写、已完成相位的回执账本翻倍）和 5 项**细节**。修复方案 R7"新技能库等于旧库去掉删除的、替换为修改后的、加上新增的"与 R5"交付接收的技能库产物……其中含成员状态"两处文字都支持产物携带 pending 成员，阻断项需要协调者在"产物写进 pending 成员"与"A 相位宇宙改回从 store 取 pending"之间裁决。

## 阻断项

### B1：A 相位的 pending 宇宙永远为空——exam 方法闭环断裂

**证据链**（每一步都在合并后的代码里核对过）：

1. **宇宙来源**。`src/expedistill/stages/phase_execute.py` 的 `_run_exam`（841 行起）先取 `bank = _bank_skills(ctx, phase)`，再 `pending = _pending_bank_skills(bank)`（833–838 行：只留 `skill.status == "pending"` 的成员）。`_bank_skills`（303–324 行）只读两处：`phases/` 下更早学习相位的 `skill_bank.jsonl` 产物，找不到才退到 run 级 `skill_bank.jsonl`。
2. **产物只含 accepted**。`src/expedistill/methods/curation/none.py` 的 `write_as_is`：`accepted = (skill for skill in skills if skill.status == "accepted")`，只写 accepted 行；agentbrew 的 `produce_phase_bank` 走 `composites/curation.py` 的 K5，同样在 accepted 集上工作。`tests/unit/test_w1f_phase_bank.py` 第 181 行明确断言 pending 技能**不进**产物（Wave 1-F 冻结的语义，当时"库 = accepted 集合"还说得通，因为旧 stage4 的验收宇宙另有来源）。
3. **新增技能是 pending**。学习相位的 add 路径经 `blocks/author.py`（367–373 行）落盘 `status="pending"`；`tests/unit/test_w2b_phase_learn.py` 第 445 行断言 `skill.status == "pending"`，第 458 行断言该学习相位之后的产物为**空**（`load_phase_bank(...) == []`）。
4. **旧世界的宇宙来源是 store**。基线提交 `16c3791` 的 `methods/acceptance/exam.py` 宇宙是"every stored pending candidate"（直接扫技能仓的 pending 候选）。Wave 2 把来源改成了库产物，产物却从未携带 pending 成员——两边各自符合自己的冻结契约，接缝上互不咬合。
5. **测试为什么全绿**。W2-A 的 exam 测试（`tests/unit/test_w2a_phase_execute.py` 614、645、669 行）都用 `write_bank_artifact(ctx, "p02:L", [skill])` 手工把 pending 技能写进产物文件——绕过了真实写方；W2-B 的测试从不运行真的 A 相位，考卷、答卷、判定全部 `_seed_exam_phase` 手工播种。grep 证实全仓库没有任何测试文件同时出现 `phase_learn.run` 与 `phase_execute.run`；`test_w2c_run_loop.py` 的"全序列"测试用的是假驱动。
6. **下游后果是静默的**。A 相位宇宙为空 → `units.jsonl`、`acceptance_report.jsonl`、摘要全部为空 → 下一个学习相位虽然从共享答卷仓现推导报告（`phase_learn._read_report` 不读相位报告文件，见核对清单），但仓里根本没有考卷与答卷，报告照样为空 → 决策对新增技能不出任何动作行 → 技能永远 pending、库恒空 → C 相位对着空库算 delta，run "完整"结束。没有任何一处会响亮失败。

**修复方向（需裁决）**：二选一——(a) 库产物写进 pending 成员（符合修复方案 R7"加上新增的"与 R5"其中含成员状态"的字面；需同时核对整包交付、注入预算阀与 `bank_reference` 对 pending 成员的语义）；(b) A 相位的宇宙改回从技能仓筛 pending（live 相位已经是这么做的：`_live_phase_groups` 走 `lm.pending_groups(ctx.store)`，所以 live 方法不受此阻断影响）。无论选哪个，需要补一个"真 L 产物 + 真 A 相位"的接缝测试。

## 偏移项

### O1："从第一个相位重放"的恢复模型会改写已完成相位的盘上工件

相位循环（`src/expedistill/cli.py` 443–459 行）崩溃后续跑总是从序列头重放，各相位靠盘上状态跳过已完成单元。这对执行侧成立（D/B/C 有 store 级跳过，A 有考卷/答卷复用），但学习相位的重放**不是纯只读**：

1. **库产物被按当前 store 重写**。每个学习相位收尾无条件调 `_produce_bank`（`phase_learn.py` 139 行），`produce_as_is` / `produce` 按调用时刻的技能仓现状重写 `phases/<label>/skill_bank.jsonl`。场景：exam 方法 `D L A L C`，崩溃发生在 C；重放时 L1 重跑，此时技能已被 L2 接受，L1 的产物从"当时为空"变成"含 L2 的结果"——跨相位库产物不再是该序列位置的真实快照。B1 修复后这会成为真问题：重放的 A 相位按这份被改写的产物筛 pending，已经 accepted 的技能被滤掉，A 的 `units.jsonl` / `acceptance_report.jsonl` / `phase_summary.json` 被**整体清空重写**，`pipeline_log` 里该相位最后一行（summary 按"每相位最后一行获胜"读取）变成"宇宙为空"的假摘要。
2. **回执账本翻倍**。已完成的 keep / delete / add 行在重放时再落一行回执（`already_accepted` / `skipped_terminal` / `skipped_existing`，`learn_tasks.py` 249–321 行），`outcomes.jsonl` 计数翻倍；revise 有回执守卫不会重复。摘要的 `outcomes` 字段按回执行累计（`phase_learn._summary`），重放后数字失真。
3. **下游暂时无恙是靠运气**。学习相位的决策输入是共享仓现推导的报告而非相位报告文件（`_read_report`），所以流程正确性目前不受 1 的影响——但被清空的 A 相位报告文件、失真的产物与翻倍的回执正是 Wave 3 审计脚本要读的东西。

**建议**：重放前按 `phase.json` + `phase_summary.json`（或产物存在性）判定"该相位已完成"则整相位跳过；至少不要在重放时重写已完成学习相位的产物与已完成 A 相位的工件。

### O2："全量绿"的口径需要更正

任务书背景称"集成收口后的全量绿"。收口提交 `1df37d4` 的实际验收（log.md 第 765 条）是 **1865 passed + 36 failed**——36 个红全部是 `test_assemble_reuse`（W3-A 迁移装配器的既定残留红，已在 log 里披露），外加 golden 套件经 `tests/conftest.py` 自愈式跳过。这不是隐瞒，是任务书口径与自报口径不一致，评审按实际口径记录。

## 细节项

1. **`_write_units` / `_write_report` 不是原子整文件写**（`phase_execute.py` 327–345 行）：先 `unlink` 再逐行 `append_jsonl`。崩溃窗口内留下缺失或残缺的文件，与本项目"临时文件 + 改名"的纪律不一致。后果目前有界：重放顺序保证该相位先于任何读者重跑、`phase_learn` 不读相位报告文件、`read_jsonl` 容忍尾部残行。建议改用 `write_jsonl`（temp + fsync + rename，语义完全一致）。
2. **`_run_exam` 的摘要写了两遍**（`phase_execute.py` 958 与 964 行，内容相同，中间只有 infra 分支的 raise）——第二遍是合并残留，应删。
3. **考卷生成失败的"终态首行获胜"有崩溃窗口**（`phase_execute.py` 926–935 行）：模型输出修复预算耗尽 → `generation_failures.jsonl` 落行**之前**崩溃 → 续跑不知道该考卷已终态，会把整轮修复预算再花一遍。"terminal 不再重试"的保证丢一行就失效一次。量级小（每个考卷至多多一轮修复调用）。
4. **`learn_tasks._land_revise` 的 in-flight 分支注释失实**（356–364 行）：注释称"上一次 unchanged 回执会再问"，实际 unchanged 回执带 `skill_id`，在 `receipts` 检查（343–344 行）处已被跳过，该情形到不了这个分支。行为正确，注释应改。
5. **组改写续跑会整组重收一次证据**：`test_a_partially_landed_group_revision_resumes_cleanly` 自述并断言"collects the whole group's evidence once"——组内部分成员已落地时，G10 证据与 author 组计划对整组重问一次模型，只对缺失成员改写。这是测试里披露过的成本取舍，与"只重问没覆盖的"字面承诺有出入，记录在案。

## 核对通过清单

以下各项静态推演成立，或测试断言与代码一致：

- **五个对象仓 run 级共享、目录名不变**：`infra/store.py` 的 `KIND_DIRS`（`stage1_baseline/trajectories` 等五个路径）在评审区间一行未动；D 相位裸臂与 live 轮继续写历史位置，C 相位双臂才用相位内私仓（无 ID 冲突，docstring 论证与代码一致）。
- **跳过键未变**：K1 的跳过判定仍是 `store.exists("trajectories", id)` + `_check_stored`（角色/模型/注入一致才信任存量）+ `skip_unit_ids`，`composites/batch_execute.py` 在区间内未改；stage0 的续跑键仍是 manifest 逐字段比对（`infra/manifest.py` 未改）。
- **pipeline_log 幂等口径**：行只追加、key 保持 `"stage"`、值为 `pNN:<局部名>`；重跑必然重复写行，`summary` 子命令按每相位最后一行读取（`cli.py` 613–627 行），`test_w2c_run_loop` 三条测试断言了行形状、重复行序列与 `--until-phase` 停跑后的账本双份形状。相位完成与账本行之间崩溃 → 重放该相位（盘上跳过，零新成本）后补行。
- **`--until-phase` 续跑**：越界校验在配置加载后、以冻结序列长度为界（`cli.py` 388–392 行，测试断言拒绝先于锁与冻结）；停止后再续 = 从头重放 + 各相位盘上跳过，`test_a_stopped_run_replays_from_the_first_phase` 断言 2+5 次派发与账本双份行；manifest 冻结序列在重放后通过 B4 复验（`test_the_frozen_sequence_survives_the_resume_check`）。
- **重派预算持久性**：`redispatch.json` 经 `write_json` 原子写；每 offer 一次 `passes` 先落盘再派发（`phase_execute.py` 692–697 行），崩溃恢复后计数仍在、耗尽单元不再进 K1（`open_budget` 过滤，680–683 行），不会重复扣。`test_redispatch_budget_then_exhausted_hole` 断言了预算耗尽、恢复零新调用、带 flag 接受后 all_holes 行三个环节。
- **learn 相位的修订重放**：`revisions.jsonl` 先写 `from_version` 再调改写；版本已加一 → 只补回执、不再问模型（348–353 行）；组内部分落地 → 回执 + 版本号双守卫只补缺口；终态先写 store 再记回执（keep/delete/revise 三类落地顺序核对过）。`test_a_crashed_revision_is_replayed_from_the_ledger` 与 `test_a_partially_landed_group_revision_resumes_cleanly` 断言与推演一致。
- **live 协议续跑**：判定行先落、`finalize_group` 后提交，相位入口有 finalization sweep 收尾崩溃悬置的提交（`_run_live` 1211–1213 行）；已判组重放时 `_round_has_row` 短路不派发；`judge_round` 对已存行重推导并逐字节比对，不符即 CodeBugError；注入审计行冻结后重推导逐字节比对（`_freeze_audit_row`）。
- **空洞（短板）继承与续跑**：跳过集 = stage1 已接受弱臂空洞 ∪ 本轮已接受空洞，按轨迹单元 ID 表达（`lm.skip_ids_for`），三处"先写接受记录、判定再读缩样"的顺序核对过（D 相位 767–785 行、live 轮 1327–1343 行、C 相位 1608–1622 行）；恢复时先读记录再派发，记录原子写。
- **旧 run 兼容**：stage0 对 v2/v3 manifest 的拒载消息明确指路维护分支与装配新 run（`stage0_freeze.py` 315–331 行）；`scripts/audit/audit_live_run.py` 在区间内未改动，读取的 `stage4_acceptance/live_run`、`stage1_baseline` 正是 B 相位与 D 相位实际写入的位置。
- **相位目录命名**：`pNN:<名>` 冒号映射 `_`，正则冻结恰一个冒号，`p1:a` 与 `p1_a` 不可能撞目录（Wave 1 收口的 D3 裁决，未回退）。

## 与任务书评审要点的对应

1. 相位间崩溃恢复：推演完成，见"核对通过清单"与偏移 O1（重放非只读的部分）。
2. 相位目录原子性：`phase.json` / `phase_summary.json` / `task_list.jsonl` / 库产物走原子写；`units.jsonl` / `acceptance_report.jsonl` 的 unlink+append 见细节 1；learn 账本的"终态先写再写账本行"顺序成立。
3. 跳过键不变：成立（见清单）；相位内完成判定用单元 ID / 回执 / 版本号，与 run 级键不冲突。
4. `--until-phase` 续跑：成立，测试断言与静态推演一致。
5. 重派预算持久性：成立，不重复扣。
6. 旧 run 兼容：成立；v3 拒载消息指路维护分支。
7. 空洞继承：live 轮与 C 相位的空洞继承与续跑推演成立（exam 方法受 B1 影响，宇宙本身为空，谈不上空洞）。

—— 评审代理：Wave 2 续跑语义（C 路），2026-10-16
