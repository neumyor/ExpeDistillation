# Wave 3 合并结果续跑语义评审报告（C 路）

- 日期：2026-10-16
- 一句话说明：对分支 `feature/multi-method` 上 `fd64e40..HEAD`（W3-S 切段接线 `6b9c93f`、W3-B 审计与脚本迁移 `b916a6f`/`75c8ad3`、W3-A 装配器迁移 `2882623`/`59d3dc3`/`de4306f`、README 同步 `740105e`）做的纯静态续跑语义评审；对应 log.md 的 2026-10-16 23:16（W3-S）、23:22（W3-B）、23:22（W3-A）、23:33（W3-C）四条。
- 评审方式：只读代码与提交差异，未运行任何测试、未执行任何脚本、未修改任何被评审代码。本文件是本次评审唯一产出。

## 总体判断

**不通过：发现 1 项阻断、3 项偏移、6 项细节。**

逐项的续跑机制本身大体扎实：W3-S 把切段接进 add 行时完全复用了 K2（切段组合积木，`composites/segment_checked.py`）的既有续跑语义，没有新造机制；`skipped_no_material` 回执的推理与实现核对成立；`--until-phase -1` 的冻结幂等成立；转换器的不覆盖规则成立。但三处组合语义有问题：

1. **阻断（W3-A）**：装配出来的复用 run 只要"在末个学习相位之前新接受了任何一个技能"，末个学习相位的整理就会在"库已过期"检查处响亮拒绝，且该检查自述的唯一补救（换新 run 目录）与复用协议的目的正面冲突——而这恰恰是 ASSEMBLED_FROM.md 自己披露为预期会发生的事（随库带入的待定成员由新 run 的 A/B 相位真实测量；v3 源的生成失败行由首个学习相位重新尝试）。
2. **偏移（W3-B）**：学习相位在相位内续跑（基础设施失败后的正常恢复，或任意中途崩溃）会为同一（目标，技能）写两条回执（先 `accepted` 后 `already_accepted`），而本次新增的审计检查 8 把"同目标同技能两条回执"定为 ERROR——按仓库规则，一个合法续跑过的 run 会被审计判废。
3. **偏移（W3-B）**：审计把"库产物在、提交摘要缺"定为 ERROR，而这个状态是每个装配 run 预置后的常态、也是崩溃续跑的正常中间态；模块 docstring 自己把这类"续跑会补完的中断提交"归为 WARN，legacy 检查对同场景也是 WARN。
4. **偏移（W3-C 转换器）**：写新配置文件不是"先写临时文件再改名"，一旦写盘中断，半截 `_v4.yaml` 落盘后会被"已存在、不覆盖"规则静默锁死。

---

## 一、W3-S：add 行切段的续跑语义

### 核对结论

**崩溃窗口（轨迹间粒度）推演成立，恢复后 add 行重放幂等。**
`segment_add_material`（`src/expedistill/methods/evidence/segmentation.py:117`）逐条强角色轨迹调用 K2；K2 的三个续跑不变量全部沿用：

- 按存量跳过：`segment_checked` 开头读该轨迹已存的 subtask，有即返回、零模型调用（`src/expedistill/composites/segment_checked.py:141-143`）；subtask id 确定性，重放逐位相同。
- 耗尽终态：审计文件里的 `status: "skipped"` 记录使重入直接返回 None、零调用、不写第二条记录（`segment_checked.py:145-146, 163-170`）。
- 逐单元落盘：每切出一段立即 `store.save`，轨迹之间崩溃留下的是"已完成轨迹集合"，恢复只切缺的（测试 `tests/unit/test_w3s_segmentation_wiring.py::test_a_mid_batch_crash_resumes_without_re_segmenting`、`test_the_add_path_resume_after_a_crash_cuts_only_the_missing` 钉住）。

add 行重放的全部副作用核对：段集合幂等（K2 三不变量）；空轨迹审计行一次性（`_record_empty_skips` 先读 `already` 集合再补缺，`segmentation.py:96-114`，测试钉住）；技能去重（`learn_tasks._land_add` 的 `fresh` 过滤按 skill 是否已存在跳过，`src/expedistill/stages/learn_tasks.py:326-332`）。恢复后的模型成本：切段零调用，只为未落盘的技能重跑写作。

**"K2 耗尽后续跑同一 add 行会不会卡死"的推理与实现核对成立。**
`NoAddMaterial` 是 ValueError 子类（`src/expedistill/methods/evidence/add_path.py:31-42`）。若让它抛出去：`_execute_row` 只捕获 `InfrastructureError` 与 `ModelOutputError`（`learn_tasks.py:199-207`），ValueError 会带 traceback 穿透整个相位；而耗尽是 run 内终态、决策是冻结报告的纯函数，续跑必然重推出同一 add 行、再次切段（零调用）、再次抛错——确实会永远卡在同一行 add 上。`_land_add` 捕获后写 `skipped_no_material` 回执并返回（`learn_tasks.py:316-325`），相位正常完成、正常提交，续跑不再进入。推理成立，实现正确。

**装配 run 的 add 行不会重复切段（代码推演成立，但无测试钉住）。**
装配器把 train 任务的**全部** subtask 按任务集合拷入（`assemble_reuse.py:1450-1452` 的 `kept_subtasks` 只按 `task_id in train_ids` 过滤，不筛角色），轨迹也全部拷入；于是装配 run 里 add 行的 `segment_add_material` 对每条被规则选中的强轨迹都能在 K2 的存量跳过处直接返回，零模型调用；源 run 的耗尽记录随 `segmentation_audit.jsonl` 整文件拷贝（`assemble_reuse.py:1632-1640`），耗尽轨迹照旧返回 None，语义一致。`collect_add_evidence` 读该任务全部 `source == "strong"` 的存量 subtask（`add_path.py:70-75`），与旧世界 stage 3 的读法相同，且 bank 档位的身份校验钉死了三条规则名与源一致，不存在"源按另一套规则切的段被新 run 误用"的窗口。
覆盖缺口见细节 D3。

### 细节 D1：单条轨迹内部的部分保存语义——续跑结果与一次跑完不同（既有设计，随本次接线进入 add 路径）

K2 在**一条轨迹内部**是逐段落盘的：写作中断在两段之间，恢复时"任何已存 subtask 即算该单元完成"，返回的是**前缀**（`segment_checked.py:41-45` 的 PARTIAL SAVE 声明，"Accepting this is cheaper than a completion marker"）。于是同一条轨迹，一次跑完可能切出 5 段、崩溃续跑只剩前 2 段，add 路径就只为这 2 段写技能。这违反 AGENTS.md 第八节"中途中断再续跑，结果与一次跑完相同"的字面要求，是 K2 既有且文档披露的取舍，不是本次引入；但 W3-S 之前它只影响旧 stage 3，现在随接线进入相位世界的 add 路径。W3-S 的测试只覆盖轨迹**之间**的崩溃，轨迹内部的部分保存没有新增测试（旧 K2 测试有 PARTIAL SAVE 的钉住）。建议在 README 或 K2 docstring 的取舍声明里补一句"前缀即最终结果，不会补切"，让 run 之间的差异可预期。

### 细节 D2：未完成相位的重入会让 target 级回执重复累加

`_land_add` 没有（也不需要）回执守卫：一个未提交的学习相位重入时，`skipped_no_material` 与 `skipped_existing` 这类 `skill_id=None` 的回执会再写一条。审计的重复回执检查跳过 `skill_id=None` 的行（`scripts/audit/audit_live_run.py:933`），不会误报；但相位摘要的 `outcomes` 计数是从账本累计的（`phase_learn.py:358-371`），重入一次 `skipped_no_material` 就计 2 次。只影响摘要数字的口径，不影响任何判定，列为细节；若要修，可在 `_land_add` 入口按 `_outcome_skills` 同款判据短路。

---

## 二、W3-A：装配器的续跑

### 阻断 B1：装配 run 在"末个学习相位之前新接受任何技能"时必然卡死，而这正是协议自己计划的场景

**证据链**（每步都在合并后的代码里核对过）：

1. **预置使短路必然发生。** 装配器把过滤库双写：run 根 `skill_bank.jsonl` 与末个 learn 相位产物 `phases/p08_L/skill_bank.jsonl`（`assemble_reuse.py:1602-1623`）。末个学习相位收尾时走配置的整理（agentbrew），进入 K5 的 dest 模式 `curate_bank`，开头就是"非空产物即短路"（`src/expedistill/composites/curation.py:1008-1032`）：`_read_bank` 读预置产物、剔掉 pending 行后非空 → 直接进入 `_check_bank_not_stale` → 返回。装配 run 没拷 `curation_log.json`（负清单 + phases 树精确断言保证），`_read_final_member_ids` 返回 None，补全循环跳过——这部分本身正确。
2. **但短路里的过期检查用的是"已接受行"口径。** `_read_bank` 把预置产物里 status 为 pending 的行全部滤掉（`curation.py:291-297`）；`_check_bank_not_stale`（`curation.py:927-946`）要求 store 里**每个已接受技能**都出现在这份"已接受行"集合里，缺一个即 ConfigError，docstring 明说唯一补救是"新 run 目录"。
3. **协议自己计划了让已接受集合增长的两条路径。** 其一：随库带入的待定成员（v4 源的未完成材料，`test_pending_members_of_a_v4_source_ride_along_disclosed` 钉住其进入预置产物，ASSEMBLED_FROM 披露"新 run 的 A/B 相位将真实测量它们"）——A 相位的测量宇宙正是上一个学习相位产物里的 pending 成员（`methods/curation/phase_bank.py` 的 `_with_pending_members` 保证它们在产物里；A 相位从产物筛 pending），考过即通过 → 下一个学习相位的 keep 行把它接受（`learn_tasks._land_keep`）→ store 状态变 accepted。其二：v3 源的 generation_failures train 行被披露为"由新 run 首个 learn 相位重新尝试（真实模型调用）"——重新写出的新技能是 pending，同样走 A 相位测量、keep 接受。
4. **增长的结局。** 新接受的技能不在预置产物的"已接受行"里 → 末个学习相位整理在 `_check_bank_not_stale` 处 ConfigError，相位不提交、run 停摆；报错文案指给的补救（换新 run 目录）等于放弃整个复用。也就是说：**只要任何一个随库带入的待定成员、或新 run 自己写出的技能，在末个学习相位之前（或就在末个学习相位里）被接受，整个装配 run 无法完成。** 只有"所有新材料全程考不过"的 run 才能走到 C 相位。

**为什么测试没抓到**：零调用端到端测试（`test_assembled_run_resumes_under_the_v4_pipeline_with_zero_model_calls`）的夹具里没有任何 pending 成员（源 run 的技能全是 accepted/merged/discarded），也没有任何会新接受技能的路径；`test_pending_members_of_a_v4_source_ride_along_disclosed` 只断言带入与披露，没有把它推进到 A 相位与接受。两条路径各自的测试全绿，接缝无人走通。

**修复方向（需协调者裁决）**：短路路径在返回前把 store 与产物对账——产物行缺的已接受成员按 store 当前状态补入、状态已从 pending 变 accepted 的行按 store 刷新（产物是唯一的相位间通货，行状态本就该跟 store 一致），然后再做过期检查；或者装配器在 ASSEMBLED_FROM 如实披露"本 run 不支持在接受新材料后完成"。前者保持协议目标（复用已理过的库 + 真实测量新材料），后者把限制写明。

### 偏移 O1：双写的两份不会在"写"上漂移，但会在"内容"上漂移，且模块文案与实际消费者不符

- **续跑（重装配）不产生漂移**：两份都是整文件原子写（`_write_jsonl_atomic`，先写临时文件再改名，`assemble_reuse.py:582-606`），重装配覆盖不追加，有测试钉住；装配后管线一旦启动（manifest 落盘）重装配即被拒。
- **管线运行后内容漂移是真实的**：相位世界里 run 根 `skill_bank.jsonl` 的唯一写方就是装配器（`store.append_bank` 只在 K5 的 run 模式用，相位世界全走 dest 模式，`curation.py:285-297`；全仓 `ctx.store.bank()` 的读方只有 `phase_disk._bank_skills` 的首个学习相位前回退）。末相位产物则会被 `_with_pending_members` 追加新 pending 成员（原子整写、重入幂等、`present` 集合防重），两份从此不同。
- **漂移目前无害但文案失实**：末相位之后的消费者（C 相位交付）只读末相位产物（`phase_disk.py:91-112` 按序号取更早学习相位里最近的一个，预置产物的序号守卫保证它不会被更早的相位误读）；v4 审计不读 run 根库。`assemble_reuse.py:26` 与 1604 行声称 run 根库"read by the audit tooling"，与 v4 审计代码不符（该说法只在 v3 审计路径成立）。建议改文案或在 README 布局一节写明 run 根库在装配 run 中的"冻结快照"定位。

### "首个学习相位在非空产物上短路"的判定依据与崩溃恢复行为

任务书问的短路有两处，分别核对：

- **末个学习相位整理的短路**（`curate_bank`）：判定依据是"dest 产物剔掉 pending 行后非空"且"store 的已接受集全在其中"。崩溃在短路点之前（落地行中途）：相位无提交摘要 → 重入 → 账本守卫让各行跳过（revise 有回执守卫、add/keep/delete 落地幂等）→ 再次短路，幂等。崩溃在短路点之后、`_with_pending_members` 里或摘要提交前：重入 → 产物仍非空 → 再次短路 → `present` 集合使追加不重不漏 → 逐字节相同 → 摘要提交。两側都幂等，核对通过（B1 的过期拒绝是另一回事）。
- **学习相位整体的完成短路**（`phase_learn._finished_summary`，`phase_learn.py:107-116, 159-170`）：判定依据是相位摘要里**存在 `bank` 键**——产物先写、摘要最后写，摘要带 bank 键即提交点。崩溃在提交点之前：摘要无 bank 键 → 重入整相位重跑（靠账本幂等）；之后：整相位跳过，回执不翻倍、产物不按当前 store 重写。核对通过，测试（W2 裁决 5）钉住。

### 假客户端测试是否真的覆盖"前段零调用"

**覆盖成立，但覆盖面有一个盲区。** `FakeClient([])` 在任何模型调用上抛 AssertionError（`src/expedistill/llm/fake.py:40-42`），所以"从 p00 到末个 learn 相位零调用"是硬断言：p00 冻结、D 相位按存储轨迹跳过派发、每个学习相位的决策/证据/写作/整理中任何一处真实调用都会让测试炸掉，且第二次整段重放再次零调用完成。盲区：夹具里弱臂轨迹 `correctness=True`（`test_assemble_reuse.py:333-345`），不存在摸底缺口，**学习相位根本不会推出任何 add 行**——所以这个测试证明不了"装配 run 的 add 行不重复切段"（见 D3）。

---

## 三、W3-B：审计的幂等性与只读性

### 核对结论（只读性）

**内容零写入，核对通过。** 审计全部检查路径（`_audit_v4`、`_audit_legacy`，1–1430 行）没有任何 `save/append/write/unlink` 调用；`write_json`/`write_jsonl`/`mkdir` 只出现在 `--self-test` 的假 run 构造器里，写入目标是临时目录；`--out` 强制落在 run 目录之外（`main` 里 `relative_to` 反查拒绝）。唯一副作用：`Store(run_dir)` 构造时会 `mkdir` 布局目录（`infra/store.py:354-357`，五个对象目录 + stage5_curation、stage6_test、llm_requests）——对跑过管线的 run 这些目录已由管线自己的 `Store` 创建，审计是空操作；只有对着"从未被管线碰过的目录"跑审计才会多出空目录。建议在脚本 docstring 披露这一条，或给 Store 加只读开关，属细节。

### 偏移 O2：合法的相位内续跑会被检查 8 判成"重复回执"ERROR

**场景推演**：学习相位的行落地没有回执守卫（模块 docstring 明说 keep/delete/add 不需要守卫，落地本身幂等），但回执是**追加**的。keep 行第一次落地把 pending 技能写成 accepted、写 `accepted` 回执；此后只要相位没到提交点（另一行基础设施失败——`execute_rows` 会记下并让相位在摘要后抛错重入；或任意位置崩溃），续跑重推同一决策、重落同一 keep 行，此时技能已是 accepted，写 `already_accepted` 回执（`learn_tasks.py:261-268`）。两条回执同 `phase_label`、同 `target_id`、同 `skill_id`。检查 8 的重复判据正是 (target_id, skill_id) 二元组，第二条即 ERROR："a second one means a landing replayed"（`audit_live_run.py:936-944`）——但这次重放是文档明文规定的合法续跑行为（`phase_learn.py` 的 Resume 段、"an interrupted one ... reads its landed skills off the phase's ledgers"）。基础设施失败后的正常恢复都能触发，无需人工干预或代码缺陷。后果：按 AGENTS 第六节"有问题的 run 一律作废"，一个只是续跑过的 run 会被审计判废。修法任选其一：keep 的重入走 revise 同款回执守卫（`_outcome_skills`）；或检查 8 对 `already_accepted` 跟在 `accepted` 后的组合降级为 WARN/放行。注意 `test_w2d` 的 `accepted + already_accepted` 出现在**两个不同相位**，不触发本条；相位内重入的重复目前没有测试。

### 偏移 O3："产物在、提交摘要缺"定为 ERROR，与 docstring 的 WARN 定义、与装配预置的常态矛盾

- 模块 docstring 把 WARN 定义为"needs a human look, e.g. an interrupted commit the resume's finalization sweep would finish"，legacy 的 terminals 检查对同类中断提交也确实用 WARN（`audit_live_run.py:320-333`）；但检查 8 把"库产物已写、摘要未提交"定为 ERROR（`audit_live_run.py:992-1001`），文案自己都写着"an interrupted commit the resume must finish, not a settled state"。
- 这个状态不是稀有窗口：**每个装配 run 在末个学习相位跑完之前**，`phases/p08_L/skill_bank.jsonl` 都"在"而摘要都"缺"——预置产物 + 未运行 = 检查 8 的 ERROR。中途崩溃的普通 run 同理。对一个本来会被续跑补完的 run 提前审计，得到的是"数字不得使用"的判废结论。
- 建议二选一：降级为 WARN 并在文案里区分"预置/中断提交"与"摘要被手改"；或在检查里读 run 是否处于未完成序列（后续相位无记录）时降级。需协调者定夺口径，但 ERROR 与 docstring 自相矛盾这一点应当先改齐。

### 细节 D4：--self-test 的六个篡改场景不覆盖"部分写入的账本行"

v4 自测的六个篡改（phase.json 角色翻转、units 重复行、捏造报告行、仓库矛盾回执、终测丢轨迹、判定矛盾状态）全部是"内容错误"类；没有任何一个场景写入**残缺的 JSONL 行**（崩溃续跑最典型的中间态）。底层事实：`read_jsonl` 静默容忍最后一条残行、对非末位的残行抛 ValueError（`infra/store.py:319-341`），`append_jsonl` 会在下一次追加时把残尾截断并留证（`store.py:248-283`）。审计是只读的，不会触发修复，所以对一个崩溃后未续跑的 run 审计：末位残行（可能是最后一条回执）被静默跳过，审计既不报错也不留 INFO。建议补一个篡改场景（往 outcomes.jsonl 末尾拼半行 JSON），断言审计的行为是显式发现（WARN/INFO）而不是静默吞掉。

### 细节 D5：审计不核对相位摘要的 outcomes 计数与账本

`learn_summary.json` 的 `outcomes` 是从账本累计的（`phase_learn.py:358-371`），审计没有把摘要里的计数与账本逐相位对账（D2 的重复回执会让两者一致地"错"，摘要与账本互相印证不了正确性）。低优先级，列为后续检查项。

---

## 四、`--until-phase -1` 的续跑（freeze 幂等）

**核对通过。**

- **manifest 不重写**：`freeze` 对已存在的 manifest 只做逐字段深比对，一致即通过、不落盘（`infra/manifest.py:54-69`），每次启动的比对本身就是续跑校验；配置变了才 `FrozenProtocolChanged` 拒绝。
- **任务表（stage0_summary.json）每次重写但逐字节相同**：`stage0_freeze.run` 无条件 `write_json` 摘要（`stage0_freeze.py:351-353`），内容是冻结输入的纯函数，重写字节相同且写入是"临时文件 + 改名"的原子写。严格说这是"重写"而非"不重写"，但幂等成立；若要绝对不动，可改成 `freeze` 同款"存在即比对"。
- **-1 停下后用更大的 N 续跑**：`until_phase` 是运维参数、不进 manifest，改 N 合法；重放从 p00 开始，freeze 重跑校验，已完成相位靠各自提交点/存储单元跳过。`test_w3b_until_phase_freeze_only.py` 三例钉住：-1 只冻结零派发、-1 后接 0 只跑 p01:D、越界报错含 -1 释义。种子脚本链路（`seed_tb_stage1.py` 经 `--until-phase -1` 拿到 manifest 与任务表再播种，D 相位按存储轨迹跳过）与零调用测试的 D 相位断言互相印证。
- 一个已知且已披露的语义：每次启动 `pipeline_log.jsonl` 追加一行 `p00:freeze`（有测试钉住，是有意行为，`summary` 子命令按最后一行取值）。

---

## 五、转换器脚本（W3-C）

**不覆盖规则核对通过；写盘原子性不成立（偏移 O4）。**

- 不覆盖：输出已存在即"exists, left untouched"（`scripts/run/convert_v3_configs.py:90-91`，测试钉住）；v4 输入跳过；已有 v3 记录的 v2 文件拒绝并指路（防同一实验冻结出第二身份）；默认目录扫描恰好只挑 schema 3 的文件。这些都对。
- **O4：`save_config` 是裸 `open(path, "w")`**（`core/config.py:1520-1525`），不是仓库 B3 纪律的"临时文件 + 改名"。写盘中断（磁盘满、进程被杀）会留下半截 `_v4.yaml`；此后重跑转换器会报"exists, left untouched"，**静默跳过**，半截文件既是坏文件又挡住了重转——必须人工发现并删除。而且 YAML 截断在缩进边界处可能仍是合法前缀文档，`load_config` 未必能拦住。修法很小：`save_config` 或转换器改为写 `<名>.tmp` 再 `os.replace`，失败自动清理；转换器在"exists"分支里先尝试 `load_config` 验证既有文件，加载失败则报错指路而不是"left untouched"。注：`save_config` 早于本次改动存在（v2 转换器同款用法），本次评审把它算在新转换器的续跑语义头上。

---

## 核对通过清单

以下各项静态推演与实现、测试断言三方一致，无发现：

1. **W3-S 驱动的规则与角色过滤**：具名 `trajectory_select` 在双角色混合集上按任务调用、只切强角色、按 trajectory_id 排序（重放序列不变）；0 步轨迹在规则前排除、审计行一次性；批次串行是文档披露的有意选择。
2. **W3-S 崩溃窗口（轨迹间）**：切段中崩溃 → K2 按存量跳过 → 重放零多余模型调用；空轨迹审计行的"先记账后切段"顺序使任何崩溃点都能幂等重入。
3. **W3-S 耗尽终态**：重入零调用、不写第二条审计行；`skipped_no_material` 回执防止"续跑永远卡在同一行 add"；该回执重复累加仅影响摘要计数（D2）。
4. **W3-S 惰性**：无 add 行的序列（exam-only / live-only）不碰切段句柄，测试用空脚本硬断言。
5. **装配 run 的 add 行不重复切段**（代码推演；测试覆盖缺口见 D3）：train 任务的 subtask 与轨迹全量拷入 + K2 存量跳过 + 耗尽审计整文件拷贝，三条合起来保证零重切、语义与源一致。
6. **装配的续跑幂等**：两份库整文件原子写、重装配覆盖不重复（测试钉住）；装配后管线启动即冻结 manifest，重装配被拒；负清单与 phases 树精确断言在每次装配时重查，脏目录响亮拒绝。
7. **末相位整理短路点的崩溃恢复**：短路前后、追加 pending 与摘要提交之间，任何崩溃点重入都幂等（`present` 集合防重、整文件原子写、摘要带 bank 键为提交点）。
8. **学习相位完成短路**：摘要 bank 键为提交点，重放整相位跳过、回执不翻倍、产物不按当前 store 重写（W2 裁决 5 的测试仍钉住）。
9. **假客户端零调用测试**：`FakeClient([])` 使任何模型调用必炸，"p00 到末个 learn 相位零调用 + 整段重放再零调用"是硬断言（盲区见第二节末段）。
10. **审计只读性（内容层面）**：检查路径零写入；`--out` 强制在 run 目录外；自测只写临时目录（Store 构造的 mkdir 副作用已在第三节披露）。
11. **审计 v4/legacy 分派与模式判型**：schema 4 或带 sequence/phases 走相位路径；模式从冻结序列判（含 exam 即 exam、否则含 live 即 live_run）；v4 前的 run 走原样保留的旧路径。
12. **审计检查 9（终测双臂）**：complete 摘要下 with_bank 用自身相位仓、复用对照回落 run 级仓，缺口对着已接受空洞豁免；篡改场景被自测捕获。
13. **`--until-phase -1`**：manifest 不重写、比对即续跑校验；stage0_summary 逐字节幂等重写；更大 N 续跑合法且有测试。
14. **转换器不覆盖规则**：已存在不动、v4 跳过、v2 有 v3 记录拒绝、目录扫描恰好挑 schema 3、坏输入按文件拒绝且不留半截输出（REFUSED 分支在写盘前返回）。

## 发现汇总

| 编号 | 级别 | 主题 |
|---|---|---|
| B1 | 阻断 | 装配 run 末相位整理短路 + 过期检查：新接受任何技能即卡死整个复用 run（协议自述场景） |
| O2 | 偏移 | 检查 8 把合法相位内续跑的 keep 双回执判 ERROR → 正常续跑过的 run 被判废 |
| O3 | 偏移 | "产物在、摘要缺"定 ERROR：与 docstring 的 WARN 定义矛盾，且是每个装配 run 预置后的常态 |
| O4 | 偏移 | 转换器写盘非原子 + 不覆盖规则会静默锁死半截 `_v4.yaml` |
| O1 | 偏移（轻） | 装配双写在管线运行后内容漂移（无消费者受损），但"audit tooling 读 run 根库"文案失实 |
| D1 | 细节 | K2 轨迹内部分保存：续跑=前缀，与一次跑完不同（既有披露取舍，进入 add 路径） |
| D2 | 细节 | 未完成相位重入使 `skipped_no_material`/`skipped_existing` 回执与摘要计数累加 |
| D3 | 细节 | "装配 run 的 add 行不重切"无测试（零调用测试夹具无 add 行） |
| D4 | 细节 | --self-test 六篡改不含残缺账本行；末位残行被 read_jsonl 静默吞掉 |
| D5 | 细节 | 审计不核对摘要 outcomes 计数与账本 |

阻断 B1 与两项审计偏移（O2/O3）需要协调者裁决修法；O4 与各细节可直接按上文建议修。
