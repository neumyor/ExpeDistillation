# 技能文件夹 M1–M5 正确性评审（A 路）

- 日期：2026-10-17
- 一句话说明：对 feature/multi-method 上 `36aad02..c36b8c2` 全部提交（M1 summary 字段、M2 folder 交付及 rev-1、M3 real_folder + TB、M4 读取统计、M5 文档）的纯静态正确性评审，不运行任何测试。
- 对应 log.md 条目：2026-10-17 的 M1（10:52）、M2（10:53）、M2 修订（15:40）、M3（17:35，时间更正见 11:30 条）、M4（11:42）、M5（11:42）、收口（18:20）各条。

## 总体判断

**通过，无阻断项。** 两条用户裁决（渐进式披露、[Trigger] 回退对称）在代码里均已落地且没有发现任何"换个地方全量塞"的通道；三铁律（执行不判定、验收不比较、测试结果不流进学习）没有违反；folder↔real_folder 配对检查静态穷举未见绕过组合；旧交付/旧执行路径在 diff 中零触碰，`engine`/`final_test`/E1 的改动对非 folder 路径逐处核对为行为等价。发现 6 条偏移（都不阻塞：2 条参数/守卫的文档与实际行为偏差、4 条文档维护性错误）和若干细节，建议在 M6 或下次文档同步时处理，其中偏移 1（real_folder 两个参数在唯一合法相位全部不被消费）建议优先澄清。

## 逐条发现

### 阻断

无。

### 偏移

**A1. `RealFolderExecutionParams` 的两个参数在唯一合法的相位（C）全部不被消费，README 声称的注入上限没有任何代码执行。**
证据：`src/expedistill/stages/phase_execute/final_test.py` 第 147 行 `_execution_params(spec)` 只做校验、结果丢弃；采样深度固定取 `n_attempts = ctx.config.n_attempts_weak`（第 153 行）；全库 grep `max_injection_tokens` 只有 live（B 相位）路径消费（`live.py`、`live_verdicts.py`、`decision.live_injection_overflows`）。而 `real_folder` 被 `core/config.py` 的角色-执行一致性检查钉死在 test 角色（第 435–444 行），所以它的 `n_attempts` 与 `max_injection_tokens` 没有任何消费点。但 README 2.1 写"`real_folder` 的注入上限计目录文本的 token"，5.5 写"`real_folder` 执行的 `max_injection_tokens` 计目录文本的 token"，参数类 docstring（`methods/execution/__init__.py`）同样描述其语义——读者会以为设了上限就生效。与 `real` 对比：`real` 的同名字段至少在 B 相位被消费，`real_folder` 无任何相位消费，是彻底的死参数。缓解因素：`real` 在 C 相位同样忽略这两个字段（既有行为），M3 是镜像而非新造；且 `_check_attempt_depths` 会拒绝超过 `n_attempts_weak` 的显式值（test_folder_m3_pairing 有钉）。建议：要么在加载期对 test 角色相位的显式 `n_attempts`/`max_injection_tokens` 直接报错（而不是接受后静默忽略），要么在 README/docstring 写明"C 相位不消费这两个字段"。

**A2. E1 能力位拒绝在"不支持的插件 + 空已接受集"组合下静默失效，整个相位以 folder 实验之名产出裸跑数据。**
证据：`core/config.py` 的配对检查不校验 benchmark；`stage0_freeze._prompt_digests` 只对声明能力位的插件做事（searchqa/alfworld 为 None，不报错）；E1 守卫（`blocks/execute.py` 第 98 行）触发条件是 `task.meta.get("skill_folder") is not None`，而 `engine._plan_units` 只在 `manifest.get("files")` 非空时挂清单（第 130 行）。于是：searchqa/alfworld 配 folder、且相位库已接受集为空时，配置照常加载、单元不挂清单、E1 守卫永不触发、全部单元裸跑直到"跑完"。README 2.6 写"searchqa / alfworld 配 folder 的配置能通过加载，单元执行时被 E1 拒绝"——这只在库非空时成立。后果有限（空库的 folder 相位本就等价裸跑，摘要的 `skill_files_read` 全空列表也能反推），但与 README 的句子不符，且"同一个配置，库空则静默、库非空则停机"的分裂行为不理想。建议：加载期或 stage0 对"声明 folder 交付但插件无能力位"直接报配置错误（这一步顺带把 README 的句子变成真话）。

**A3. AGENTS.md 目录树里 `composites/skill_folder.py` 出现两次，描述还不一致。**
证据：`AGENTS.md` 第 170 行（"folder 清单的幂等物化（M2：按指纹闸门……）"，M4 条目补的）与第 177 行（"folder 单元清单的幂等物化（M2/M3）……C 相位派发前物化一次，全部单元共享"，M5 合并带入的）是重复条目，是 b1ec37b 合并冲突"双保留"的直接后果。目录树是结构正本，重复行应去掉其一。

**A4. README 的 v4 配置计数错误：写"8 份"，实际 `configs/experiments/*_v4.yaml` 命中 9 份。**
证据：实际文件为 9 个（alfworld×2、searchqa×2、tb21×5，全部 `schema_version: 4`）。基线 36aad02 时已有 8 个而旧文写"7 份"——M5 只做了"+1"改成"8 份"，没有重新数（`searchqa_split_live_smoke_v4.yaml` 在 b0ba0d6 已入库，旧文当时就漏数了）。顺带核对过 M5 里另外两个计数：36 = 3(benchmark)×2(验收)×2(模式)×3(C 交付)，口径成立；外层总览"可跑 28（folder 的 4 种只在 tb）"= 36 − 2×4，一致。

**A5. 摘要 `skill_files_read` 的键两臂同形、结构上无臂标记，只靠文字约定区分。**
证据：trajectory id 形如 `<task>_weak_<n>`，full 模式下对照臂复用基线相位 run 级 store 里的**同字符串**轨迹（`final_test` 第 173–184 行按同规则生成对照的 trajectory_id）。摘要里只有一个扁平 `{trajectory_id: [...]}`，键上没有任何臂信息；"只盖带技能臂"只写在 `_folder_read_sets` 的 docstring 与 README 5.5 的半句话里，log M4 条目把"靠臂目录区分"留给了 M6 注意点。审计脚本若拿 trajectory_id 去 run 级 store join 会命中对照臂轨迹。不算实现错误（落点已按裁决执行），但这是给 M6 的接口缺口，建议 README 把"键与对照臂同形、必须配合相位目录/摘要内 bank_reference 使用"写全，或键名改成带臂前缀。

**A6. 读取集的三态口径没有披露完整：除"空列表（跑了没读）"和"无键（非 folder 相位）"外，还有第三态"缺键（单元没跑成 / 已接受为空洞）"。**
证据：`_folder_read_sets` 遍历的是 `with_bank`——已过滤掉 `with_bank_accepted`（第 223–224 行），没跑成、没入库的单元天然缺席。README 5.5 只写了"空已接受集报空列表，旧执行相位无键"。消费方要区分"跑了没读"和"根本没跑"，必须交叉 `infra_missing` / `infra_accepted` 两个键。建议 README 补一句。

### 细节

**D1. `skill_reads` 的 cat 判定有两条超出已披露范围的假阳性边缘。**
已披露两条（别的目录同名文件误记；只有 cat 算，grep/head 不算）在 docstring 和 README 都写了，测试也钉了。未披露的：`\bcat\b` 会把 `git cat-file -p <文件名>` 记为一次读取（连字符是词边界）；以及判定是"该行任意位置存在独立的 cat 单词 + 该行任意位置存在文件名 token"，不要求两者构成一条命令——`echo "use cat" && cp a.md b.md` 这种行会误记 b。对 TB 场景（每步一条 bash_command）实际风险低，但既然口径是"事实供审计"，建议 docstring 把这两条一并披露。

**D2. "同相位单元共享一份清单"只由交付驱动的构造保证，阶段层三处取清单的方式不同且无运行期断言。**
`_materialize_folder` / `_folder_read_sets` 都取"第一个带清单的行"（且前者用真值判断、后者用 `is not None`，`_plan_units` 用 `is not None and files`，三处条件字面不同，对现形状等价），`_plan_units` 却用各行**自己的**清单挂 meta。当前交付确实只构造一个 manifest 对象（`folder.resolve` 第 255 行），test_folder_m3_wiring 用 `id()` 钉死共享；但若将来出现逐行清单的交付，读取集会静默用行 0 的清单对账所有单元。可在 `_materialize_folder` 里加一条"所有行的清单是同一对象"的断言，成本一行。

**D3. 物化指纹闸门在现行接线里不可触发，属防御性断言而非现行保护。**
`rows = delivery.resolve(bank, …)` 与 `materialize_skill_folder(manifest, bank, …)` 在 `_run_test` 同一次调用里使用同一个 bank 对象（第 149/198/215 行），指纹必然相等；闸门防的是将来重构或跨调用漂移。docstring"the bank moved under the manifest"读起来像会发生的运行期事件。无碍正确性，措辞问题。

**D4. 物化"永不删除"的本地残留。**
bank 在两次运行之间变化时，相位目录 `skill_folder/` 里会留下已不在新清单里的旧文件（`materialize_skill_folder` 不删）。无害：TB 上传严格按清单文件名走（`_upload_folder_files` 只传 manifest 列出的名字），旧文件进不了沙箱；同名文件靠字节比对覆盖。只是本地目录会越积越多，续跑审计时可能困惑。

**D5. stage0 冻结面变化对所有 tb run 生效（包括不跑 folder 的）。**
`_prompt_digests` 对声明能力位的插件无条件把 `TB_TASK_PROMPT_FOLDER` 的 digest 冻进 manifest，因此本分支上的任何 tb run 的 manifest 都比旧分支多一个 digest，旧 checkout 的在跑 tb run 升级代码后按 B4 逐字段比对会拒载。符合"冻结面变了=新实验"的既定语义（run 固定 checkout 的规则也兜住了），记录在此提醒运维侧。

**D6. TB folder prompt 的本地文本与远端实际 prompt 的一致性只能跨仓库核对。**
`TB_TASK_PROMPT_FOLDER` 本地注册文本自称"the frozen protocol of what the remote agent effectively receives"（远端 Terminus 自己组 prompt、guidance 以 SKILL.md 注入），本仓库测试只能钉"guidance 逐字内嵌、无 `{skills_block}` 槽"（test_folder_m3_prompt），钉不了远端真的这么拼。guidance 的挂载路径措辞（/opt/openclaw-skills）是跨仓库契约，M3 docstring 已如实定位。静态评审范围内无法进一步验证，留给远端 smoke。

**D7. log.md 两处格式问题。**
M2 修订条目（15:40）排在 M3 条目（实际 11:29，已有 11:30 更正条目）之前，条目顺序与"按时间顺序排列"的规则不符（更正条目只更正了时间记载，没处理顺序倒挂）；M4 条目的工件行与 M5 标题之间缺一个空行（log.md 第 888–889 行）。

**D8. M1 侧一处无害的重复校验。**
`write_skill` 在 `validate_skill_sections` 已校验过 summary 后又调用一次 `_validate_summary`（`blocks/author.py` 第 426 行附近）。纯浪费几微秒，不是错误。

## 核对通过清单

以下各项均经静态核对通过（证据文件均已读）：

1. **渐进式披露（用户裁决一）全程无正文**：aux 清单四键 = `bank_fingerprint` / `catalog_filename` / `catalog_text`（一行一技能的目录文本）/ `files[]`（skill_id/version/filename 三键，materialize 用 `set(entry) != _FILE_FIELDS` 精确拒绝多余键）；物化正文只从 bank 读（带指纹与版本比对）；TB 的 trajectory meta 只记 `local_dir` / 指纹 / `catalog_filename` / 文件名列表；C 摘要只落读取集；单元行落盘（`_write_units`）带的 skills_block 是目录文本、aux 无正文。全链路未发现任何技能正文进入清单、报告、摘要或 prompt 的通道。空已接受集：`catalog_text` 空串、不物化、不挂清单、单元裸跑（M3 wiring 测试 + M4 摘要测试钉住）。
2. **[Trigger] 回退对称（用户裁决二）**：`when_to_use_line` 对 `[When to use]`（procedure 首 marker）与 `[Trigger]`（failure reference 首 marker）对称回退，节尾对全部八个 marker；无两节时显式占位 `(no summary)`；K5 拼接超长在目录行截 200 并显式加 `[truncated]`。测试两例（m2_delivery 127/145 行）。
3. **folder↔real_folder 配对完备性（静态穷举）**：`core/config.py` 第 394–444 行——folder 交付非 test 角色先拒；XOR 配对检查拒绝 folder+real 与 real_folder+任何其他交付（含 D/B/A 角色的 real_folder，A 角色会先被 expected_exec 或配对检查拦住）；v3→v4 升级函数不产生新方法名，无法从旧配置绕入；12 例配对测试覆盖上述每个组合。`final_test` 是唯一能见到 folder 行的驱动，`_execution_params` 在其入口校验参数。
4. **E1 守卫先于 plugin.run**：`blocks/execute.py` 第 98–106 行守卫在 `plugin.run`（第 106 行）之前，ConfigError 消息写明能力位与"目前只有 tb"；test_folder_m3_wiring 断言 `plugin.calls == []`。
5. **执行不判定**：TB folder 路径只做上传与记录（plugin diff 全文核对），`skill_reads` 仅被 `final_test`（C 摘要）与测试引用，benchmarks 层零引用；读取集是轨迹解析的事实，无任何判分/比较逻辑。
6. **测试结果不流进学习**：C 相位不写 acceptance report（test_folder_m4_summary 断言报告文件不存在）；`decide_task_list` 对 test 类报告双侧 `FinalTestReportRejected`（test_folder_m4_summary 193–202 行两条断言，decision.py 本 diff 未触碰）；读取集除摘要外无任何落点。
7. **旧路径零改动**：`whole_bank.py` / `retrieval.py` / `per_skill.py` / `group_by_task.py` / `none.py` / `react_loop.py` / `batch_execute.py` / `inject/*` 在区间内 0 diff；`engine._plan_units`、`final_test._run_test`、`blocks/execute.run_one`、`stage0._prompt_digests` 的改动对非 folder 输入逐行核对为行为等价（守护/挂载全部以 `skill_folder` 键存在为条件；非声明插件的 stage0 不加 digest）。金矩阵：M1 按纪律全量重生成，log 声称的差异五类逐处核对与 `exam-retrieval-split.json` 的实际 diff 一致（summary 键、schema 3、四个 prompt digest、请求/响应字节与 token 估算、rewrite_history 旧摘要键）；M2–M5 零重生成、`provenance.json` 的 base_commit 即 36aad02，与声称一致。
8. **指纹闸门**：manifest 指纹 = `bank_reference`（sha256 over `skill_id@version:content`，覆盖内容），与相位库 bank_summary 同一口径；materialize 比对指纹、按 skill_id+version 读正文、三重拒绝（指纹不符/版本漂移/清单-库不一致）各有测试；无绕过物化拿正文的路径——`_plan_units` 有清单无目录即 CodeBugError，TB `_folder_of` 同样拒绝；TB 续跑条件摘要折叠 指纹+目录文本，换库不会误复用远端 trial（sandbox 测试钉）。
9. **能力位与冻结一致**：`skill_folder_prompt_name` 声明名 digest 进 manifest、未注册名 ConfigError（stage0 第 231–243 行）；`test_freeze` 静态钉"声明能力的包恰好多注册一个 prompt"；无能力位插件不多冻结任何 digest（测试）；searchqa/alfworld 插件一行未动（测试断言 None）。
10. **Skill v3 迁移链**：v1→v3 显式补空 summary、v2→v3 显式置空（`_migrate_v2_skill_row`）、v2 行带杂散 summary 键保留、当前 schema 行原对象透传（`migrate_skill_rows` 测试 `is` 断言）；`_land_revision` rewrite_history 存旧摘要、store 漂移守卫比对摘要；G2（两模式）/G6/G11 三条链路的输出契约、校验器（author/attribution 镜像同一批坏载荷）、prompt 模板 JSON 例键与校验器键集的静态钉均在。
11. **README folder 段逐句核对**（含收口提交 c36b8c2 补的 M4 段）：物化位置（相位目录 `skill_folder/`）、幂等（内容不变不重写，mtime 测试）、一次物化全单元共享、guidance 无 `{skills_block}` 槽、目录文本不进提示词、E1 能力位拒绝、空库裸跑、读取集落点 `{轨迹 ID: [技能编号]}` 只盖带技能臂、folder 相位才有键、已知局限两条、不进决策输入——均与代码一致；2.4 的配置片段与 `configs/experiments/tb21_split_folder_v4.yaml` 的 C 相位定义逐字段一致（该配置整体可加载形状由 M5 的实际 load 验证记录在案）。

## 结论

M1–M5 的实现与两条用户裁决、三铁律和文档声称高度一致，工程质量好（守卫位置、测试钉法、幂等与续跑语义都讲究）。需要处理的是 A1（死参数的文档失实，最容易误导后续实验者）与 A2（空库 + 不支持插件的静默裸跑），其余为文档维护性问题，可在 M6 收口时一并修。
