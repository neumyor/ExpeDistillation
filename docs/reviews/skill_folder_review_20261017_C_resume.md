# 技能文件夹 M1–M5 续跑语义评审（C 路）

2026-10-17
对 feature/multi-method 分支 `36aad02..c36b8c2` 全部提交做的第三路静态评审，主题是崩溃续跑语义：进程中断后重新启动，已有产物能否接住、结果与一次跑完是否相同。纯静态评审，未运行任何测试。
对应 log.md 的技能文件夹 M1–M5 各条目（2026-10-17）与本条评审记录。

术语约定：**物化**指把单元行携带的清单写成真实文件夹；**幂等**指同样输入重复执行，效果与执行一次相同；**指纹**指内容的哈希摘要，内容一变摘要就变；**臂**指同一相位里一组同条件执行的单元（带技能库的一组、裸跑对照的一组）；**infra 空洞**指基础设施故障耗尽重试后仍没有产出的单元，经操作员允许记为接受、不再重试；**规则 1** 指决策模块里"任务与注入都没变就跳过重测"的复用规则。

## 总体判断

**通过。未发现阻断级问题。** 续跑语义的核心链条静态核对全部成立：交付清单从同一相位库确定性推导、物化幂等且带指纹闸门、读取统计是纯函数、C 相位摘要每次全量重算、K1 按确定轨迹 ID 接住已存单元、manifest 逐字段比较拒载旧 run。发现 2 条偏移（窄窗口的目录卫生缺口；阶段层缺真正"断在中间"的续跑用例）与 4 条细节，均不影响续跑等价结论。

## 逐条发现

### 偏移 1：物化目录"永不删除"，而全仓库没有负责清理的调用方

- 证据：`src/expedistill/composites/skill_folder.py:28-32` 的契约写明 "The function never deletes anything -- files the manifest does not name are another caller's business"；`src/expedistill/stages/phase_execute/final_test.py:93-95` 每次直接写入 `phases/<相位>/skill_folder/`；grep 全仓库没有针对该目录的清理代码。
- 场景：同一 run 目录里，C 相位的技能库在两次调用之间发生变化（按序正常续跑不会发生——顺序保证 learn 相位完成后库工件不再改写；人工干预或维护分支热修才可能）。此时旧清单的文件残留在本地物化目录里，与新文件并存。
- 影响面已核实的边界：远端沙箱不受影响——上传按清单文件名逐个进行（`src/expedistill/benchmarks/tb/plugin.py:956-969`），清单外的旧文件不会被发出去；K1 的存储信任检查会在"已有存储轨迹且库摘要变化"时报 ConfigError 停机。剩余窗口是"库变了且带库臂还没有任何存储轨迹"：本地目录会同时装着两个版本的库，直接读目录的人工审计会看到清单之外的文件。
- 建议：物化开始时按清单名字集合清掉目录里清单之外的同后缀文件，或让 C 相位摘要记录物化时的实际文件清单，供审计对照。

### 偏移 2：folder 的 C 相位缺真正"断在中间再续跑"的端到端用例

- 证据：`tests/unit/test_folder_m3_wiring.py:251-267`（幂等测试）与 `tests/unit/test_folder_m4_summary.py:135-151`（读取集重算测试）都是在完整跑完的相位上再次调用，没有制造"部分单元已存、部分未存"的中断状态。m4 那个用例里假插件的输出是确定脚本，即使第二次真的重新执行了全部单元，断言同样通过——它分不出"复用存储轨迹"与"重新执行"两条路径。
- 现状评估：续跑等价实际由三个各自有测试的部件组合成立——K1 按确定轨迹 ID 跳过已存单元（`composites/batch_execute.py` 的既有续跑测试）、物化幂等（`test_folder_m2_materialize.py` 重放不变）、读取集纯函数重算。静态读代码这个组合成立，结论不受影响；按 AGENTS 八.2"组合链路的测试必须包含中途中断再续跑"的精神，folder 的 C 相位接线缺一个直接用例：在带库臂存储仓只写了一部分时杀掉进程，续跑后核对摘要与一次跑完相同。
- 备注：读取集等价的准确口径是"同一批存储轨迹解析出同一结果"。含真实模型的续跑里，重试单元重新执行后轨迹内容可以不同，读取集随轨迹走——这与正确率数字的续跑语义一致，属于既有约定。

### 细节 1：bank_reference 指纹不覆盖 summary 字段

`src/expedistill/inject/skills.py:178-193` 的指纹只算 `skill_id@version:content` 行。目录文本的描述列依赖 summary，跨调用一致性靠"summary 变化必然伴随 version 或 content 变化"这条当前成立、但没有机制保证的约束（`learn_tasks._land_revision` 三者同动；K5 产新技能 id）。TB 链路另有目录文本直接进条件摘要与 run_namespace（`plugin.py` `_conditions`、K1 namespace 说明），远端复用安全。将来若出现只改 summary 的路径，指纹闸门与 K1 存储信任检查都察觉不到目录文本漂移。

### 细节 2：`_write_if_changed` 的硬杀窗口留下 .tmp 残片

`src/expedistill/composites/skill_folder.py:71-85` 在临时文件创建与改名之间被 SIGKILL 或断电杀掉时，异常清理路径来不及执行，目录里留下 `<文件名>.XXXX.tmp` 残片，物化层不清理。影响链已核实为空：上传按清单名走，残片不进沙箱；`skill_reads` 的定界匹配里 `.` 属于文件名字符集，"skill.md" 在 "skill.md.abc.tmp" 里不构成定界 token，不会被误记为读取。纯本地磁盘垃圾。

### 细节 3：AGENTS.md 目录树 skill_folder.py 出现两次

`AGENTS.md:170`（M2 描述）与 `AGENTS.md:177`（M2/M3 描述）重复列出 `composites/skill_folder.py`。M4 补目录树行时没有合并 M5 已写的旧行。

### 细节 4：`_materialize_folder` 与 `_folder_read_sets` 取清单的判定不一致

`src/expedistill/stages/phase_execute/final_test.py:88-90` 按真值过滤（`if row.aux.get("skill_folder")`），`:120-122` 按 `is not None` 过滤。交付层产出的清单恒为至少四个键的映射，两处行为恒同；若将来出现假值形态的清单，会出现在一处置化、一处计数的分叉。顺带说明：空已接受集时前者返回 None（不物化）、后者返回全空列表的字典（摘要报空读取集），这是 M4 裁决的既定行为，测试已锁住。

## 核对通过清单

按任务书的六个评审重点逐项核对：

1. **物化的续跑语义**——通过。崩溃在写文件中途：恢复后重入 `materialize_skill_folder`，先重验清单结构、指纹、文件名安全集（`skill_folder.py:131-178`），再逐条补齐——每个文件走"字节相同跳过、缺失或不同则临时文件 + fsync + rename 原子写"（`:62-86`），目录能补齐。指纹闸门：库与清单指纹不符、版本漂移、清单列了库里没有的技能、文件重名、撞 catalog 名，全部响亮拒绝（`:136-195`）。确定性：正文逐字节取自相位库行的 content，目录文件逐字节取自清单的 catalog_text，按清单序写入；同一清单同一库产物字节完全相同（`test_folder_m2_materialize` 的重放测试含 inode 断言，`test_folder_m3_wiring` 的相位层幂等测试含 mtime 断言）。物化时点在复用对照的完整性检查之后、任何带库模型花费之前（`final_test.py:165-191` 在前，`:215` 在后）。
2. **skill_files_read 的重放等价**——通过。解析是纯函数、只读轨迹：`composites/skill_reads.py` 全模块无磁盘写、不改入参，首读序、同文件去重、只认 cat、词边界匹配均有测试。摘要重算路径：每次调用 `_run_test` 全量重算读取集并整文件重写摘要（`final_test.py:225、299-301`），无增量补丁逻辑；键序来自排序的轨迹 ID（`engine.py:323-326`、`infra/store.py:389-392`），`write_json` 按插入序写（`store.py:111-131`），给定同一批存储轨迹时续跑与一次跑完的摘要逐字节相同。两臂重建：读取集只从带库臂的相位内轨迹仓构建（`final_test.py:125-126`），对照臂无清单可读，两臂轨迹仓目录隔离，键不会串。与摘要提交点的交互：摘要写在两臂派发之后、抛错之前；infra 空洞先落盘 shortfall 再写摘要（`:254-266`），读取集稳定排除已接受单元；崩溃在摘要写之前或之后，恢复后都重算到同一结果。
3. **summary 字段的迁移安全**——通过。v2 行经 `_migrate_v2_skill_row` 显式置空串（`core/models.py`），v2 行若已带 summary 键则保留；v1 链同样显式补空串；`migrate_skill_record` / `migrate_skill_rows` 对 v2 行从透传改为迁移，docstring 同步。装配器与审计工具读旧技能行经同一条迁移链（`assemble_reuse.py:1423-1502`、`audit_live_run.py`），两侧同构迁移后一致性比较成立；`audit_live_run.py:283` 读 rewrite_history 用 `.get("content")`，旧条目缺 summary 键兼容。`_land_revision` 的漂移守卫同时比对 summary（`learn_tasks.py:552-563`）。旧 run 续跑另有 manifest 拒载保护：M1 改了四个 prompt 的 digest，旧 run 的 manifest 与新代码算出的冻结面不一致，直接 FrozenProtocolChanged（`infra/manifest.py:44-58`），符合"改 prompt 即新实验"。
4. **TB 沙箱链路的崩溃窗口**——通过。物化完成与派发之间崩溃：恢复后单元重派走既有链路——run_id 由 namespace + attempt token 确定（`plugin.py:785-814`），已完成的远端试次按 result.json 复用、活着的按记录 pid 重入（`_probe` / `_untrusted`，`plugin.py:1023-1098`），无重传（`test_folder_m3_sandbox.py:222` 锁住）。上传中途崩溃：远端"存在"以 jobs 目录、日志、meta 判定（`:1032-1036`），skills 目录部分上传时判 absent，全新启动后按清单名全量重传（put 覆盖），重建幂等。带点任务名双拼写：SKILL.md 与全部清单文件同时上传两拼写目录（`:914-926`、`:952-969`），重传走同一函数，重放一致。库变化防复用：条件摘要把指引段、库指纹、目录文本折进 skill_sha256（`:975-1005`），K1 的 run_namespace 含目录文本的哈希，换库后 run id 与复用判定双双失效。清单文件在物化目录缺失时上传前响亮报错（`:960-966`），半份文件夹不会发出去。
5. **配置与冻结**——通过。stage0 把能力位声明的 folder prompt digest 进 manifest，未注册名 ConfigError（`stage0_freeze.py:220-252`）；不声明能力的插件冻结面不变。同一 run 目录换 folder 配置（例如换 whole_bank）：manifest 逐字段深比较报 FrozenProtocolChanged，旧 run 拒载；新 run 目录是正常新实验（加载检查允许 test 角色 whole_bank + real，`core/config.py` 的第 8 条配对检查只约束 folder/real_folder 本身）。配置样例 `configs/experiments/tb21_split_folder_v4.yaml` 的加载与两族参数解析有记录（log.md M5 条目）。旧 run 在新代码下的拒载由 prompt digest 差异保证，与 AGENTS 六.1 的维护分支规则一致。
6. **跳过键**——通过。folder 单元的 unit_id 与 whole_bank 同构（`make_unit_id(phase_label, benchmark, task_ref, role, skills_injected)`，`engine.py:144-147`；skills_injected 为已接受库摘要），进的是相位内带库臂轨迹仓，与规则 1 没有接触面——规则 1 的消费入口是验收报告行，而 C 相位不写报告（`test_folder_m4_summary.py:174-198` 断言报告文件不存在），decide_task_list 对 test 类报告双侧抛 FinalTestReportRejected。`_plan_units` 只把 (清单, 物化目录) 挂进 Task.meta 供派发（`engine.py:129-139`），不产生任何复用记录；对照臂的裸行走独立计划，不挂清单。"每次全量重跑"的落实：C 相位派发不调 `_run_arm_units` 的预算循环，`skip_unit_ids` 只传 infra 已接受空洞（`final_test.py:221-222`）；耗尽单元进摘要的 infra_missing 并抛错，由相位级续跑重试。

## 评审方法与边界

纯静态：逐文件阅读 `36aad02..c36b8c2` 的差异与相关既有代码（K1 续跑、store、manifest、decision、TB resume、迁移链），未运行任何测试；文中引用的行号以 c36b8c2 工作树为准。测试断言的描述均来自阅读测试代码本身。金矩阵重生成与 E1–E6 披露的逐键核对按 M1 条目的记录采信，本次未独立复核。
