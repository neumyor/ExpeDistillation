日期：2026-10-15
一句话说明：P4（curation 槽位双驱动与 authoring.exams 开关，fea4310）+ P5（provider 注册表与角色表 None 档，475e109）合并评审之评审 C（迁移风险与可复现性视角）——静态审阅 `git diff 350b40d..475e109` 并逐条核验四项验收主张；结论：四项主张均有仓库内证据支撑（"1924 全绿"按评审约束未独立复跑、以记录链自洽采信），续跑与账本、金矩阵覆盖、provider 回归面三个专项均无【偏移约束】级发现；最重要的发现是 7 份 v3 实验配置因 curation 槽未点名而在 P4 后全部拒载（响亮失败、不产生错误数据，建议修复轮处理）。
对应 log.md 条目："2026-10-15 21:55"（P5 完成条目）；本评审同时覆盖同日 "2026-10-15 21:20"（P4 完成条目），两步按用户指令合并为一轮评审。

---

## 一、总体评价

**通过：无【偏移约束】级发现。** 两项【琐细】建议修复轮处理（R1：7 份 v3 配置失效；R2：stage3 exams=false 行为分支零测试），均不阻塞 P4/P5 验收，但都应在预注册消融对（exams:false + curation:none + live_run）首次真实启动之前关闭。

审阅范围与纪律：方案正本 §6/§7/§7.1/§10，`git diff 350b40d..475e109`（两个提交：fea4310 P4、475e109 P5），全程只读（静态阅读 + git 比对），未跑测试、未触碰 runs/ 与 TB-dev、未读 .env。

核心判断：

1. **P4 对既有 8 组合确属"行为不变 + 身份变化"**：金 diff 逐份核对恰为 8×1 行（manifest 的 `config.methods.curation` 各增 `"name": "agentbrew"`），provenance 的 model_calls（34/18/28/15 各两遍）与 8×3 crash_points 逐值不变——后者是行为等价的独立旁证（若 agentbrew 路径有任何行为变化，调用数几乎不可能全等）。金矩阵 harness 配置**缺省** curation 槽（`tests/golden/harness.py:363-369` 不写它），漂移恰来自 `METHOD_DEFAULTS` 加 name，与验收主张的机制吻合。
2. **none 驱动的续跑语义正确且是三个方案要求里最省的那种**：bank 整写 + temp+fsync+rename，内容是 accepted 集的纯函数，崩溃窗口全部收敛到同一终态；初版 append_bank 的续跑翻倍 bug 在自查阶段发现并改为整写（提交说明留痕），这是"改一次、跑一次"教训的正确内化。
3. **三处"半途改配置再续跑"场景全部被 manifest 逐字段比对兜住**（逐一代码验证，见第三节）；`composites/curation.py` 一行未动（git diff 为空），agentbrew 薄壳与原 stage5 的调用参数、summary 构造逐字相同，K5 的短路径（非空 bank 短路、journal 续跑、final append 崩溃补齐）原样保留。
4. **P5 是零行为变化的纯接缝**：真实路径逐字等价（`providers.default_client` → `OpenAICompatClient.from_env(timeout_sec=…)`），make_client 注入点一参数签名不变，金矩阵走注入路径故 P5 对其零影响（P5 提交未触碰 golden，与"行为不变步骤 golden 无 diff"的纪律一致）；误设 `EXPEDISTILL_LLM_PROVIDER` 在任何 client 构建与 stage0 冻结之前响亮失败并列出已知名。两项按简洁化裁决跳过并留档于方案 P5 行，符合 §9 禁止清单（无投机抽象）。
5. **唯一实体性迁移遗留是配套资产失同步**：`upgrade_v2_config` 同步加了 `name`，但 P1b 已生成、已人工审定并宣布"可作为后续实验起点"（`docs/reviews/v3_config_review_20261015.md`）的 7 份 `configs/experiments/*_v3.yaml` 没有跟着改——它们的 curation 槽显式给了 `rounds/token_budget` 却无 `name`，在 P4 后于 stage0 解析处被拒（"methods.curation needs a name; known: ['agentbrew', 'none']"）。失败响亮、不会产生错误数据，故不定为偏移约束，但这是修复轮的第一优先项。

## 二、验收主张逐条核验

| # | 主张 | 裁决 | 证据 |
|---|---|---|---|
| 1 | P4 后"仅 manifest 漂移"8/8 验证（curation 缺省加 name）再重生成 golden（provenance base_commit 已换） | **采信** | fixture diff 逐份核对：8 份各恰 +1 行 `config.methods.curation.name`，无任何其他漂移；`provenance.json` 仅 base_commit 变（00971f2→350b40d，即 P4 落地提交之父，与 P1b 评审第八节已裁决的"鸡生蛋约束下诚实记法"一致），model_calls/crash_points 全等是行为等价旁证；harness 缺省 curation ⇒ 漂移来源与主张吻合。"先验证后重生成"的过程顺序无法事后独立复核，但其全部可观察产物与主张精确一致 |
| 2 | none 的 bank 行与 store.append_bank 的行逐字节相同（同 json.dumps 参数） | **代码级证实** | `methods/curation/none.py:48-50`：`json.dumps(skill.to_dict(), ensure_ascii=False, sort_keys=True) + "\n"`，utf-8 落盘；`infra/store.py:253-254`（append_jsonl）：同参数同换行同编码；K5 的 append 侧同样传 `skill.to_dict()`（`composites/curation.py:1273`，final-append 补齐处 :945-946 亦然）。行长、行序约定（K5 final 成员亦按 skill_id 排序，`curate_bank` docstring）一致 |
| 3 | 24 崩溃续跑变体在 P4 后重验 | **采信（机制证据）** | `tests/golden/generate.py:73-84`：每组合 3 个 crash 分数各跑一次 crash+resume 并 `assert_resume_equivalent`，不等价即 SystemExit——采集器无法在续跑不等价时落地矩阵；`provenance.json` 重写（generated_on 2026-10-15、8×3 crash_points 与此前全等）蕴含采集器在 P4 代码上完整跑通全部 24 变体；常驻套件另有 2 组合续跑测试（`test_golden_matrix.py:35,55-65`） |
| 4 | 1924 全绿 | **采信（不可独立复核）** | 本评审约束不跑测试。记录链自洽：1914（P1b 修复轮）+7（none 单测 4 + exams 冲突 3）= 1921（P4 log）+ 3（provider）= 1924（P5 log），与 diff 中新增测试数逐个吻合（test_curation_none 4 条、TestAuthoringExamsSwitch 3 条、TestProviderRegistry 3 条） |

## 三、续跑与账本专项（主视角 1）

1. **none 原子整写在崩溃窗口上的语义**（`methods/curation/none.py:52-70`）：
   - mkstemp 后、`os.replace` 前硬崩（SIGKILL）：残留 `skill_bank.jsonl.<rand>.tmp`，bank 文件保持旧状态（或不存在）；下次重跑写全新 tmp 再 replace，幂等收尾。孤儿 tmp 成为 run 目录垃圾但**无读者**——全库对 skill_bank 只按精确路径读（`read_jsonl`、`assemble_reuse`、审计脚本），无该模式 glob。
   - replace 后、summary `write_json` 前：bank 完整、summary 缺 → 该阶段未完成 → 重跑时纯函数重写 bank（逐字节相同）并补 summary。
   - Python 异常路径有 `except BaseException` + unlink 清理；预算硬阀在**写盘之前**（超限 ConfigError、零写盘，单测断言 `store.bank() == []`）。
   - 结论：全部崩溃窗口收敛到同一终态，无中间态可污染续跑；`test_rerun_is_idempotent_not_doubling` 所测的"重复调用等价"正是其唯一实质窗口（bank 完成而 summary 未写）的结果等价形式。
2. **curation=none 的 run 半途把配置切到 agentbrew 再续跑**：pipeline 每次启动无条件先跑 stage0（`stages/pipeline.py:398-399`）；agentbrew 参数本身合法 → 通过 `resolve_params` 循环 → `freeze` 深比对（`infra/manifest.py:54-70`）报 `FrozenProtocolChanged`，点名 `config.methods.curation.name`。**拒绝成立**，反方向（agentbrew→none）同理。
3. **stage3 exams=false 的 run 半途把 exams 改回 true 再续跑**：同机制，`config.methods.authoring.exams` 漂移被 manifest 拒绝；`check_authoring` 对 exams=true 直接放行（无冲突），**兜底的正是 manifest**——与预期一致。
4. **pre-P4 冻结的 v3 run 在 post-P4 代码下续跑**（衍生场景）：配置显式给 curation 无 name → stage0 `resolve_params` 处 ConfigError"needs a name"（先于 manifest 比对，同样响亮）；配置缺省 curation → METHOD_DEFAULTS 补了 name → manifest 漂移被拒。两条路都响亮，符合 §6"正式 run 固定维护分支续跑"的纪律。
5. **agentbrew 薄壳与原 stage5 完全等价**：`curate_bank` 的十一个入参逐字相同（store/weak_profile/三个 role handle/budget/rounds/usage/run_dir/workers）；summary 构造逐字相同（`members=[str(row["skill_id"])…]`、`size`、`bank_reference`、`write_json(…, indent=2)`，`methods/curation/agentbrew.py:32-48` vs 被删的旧 stage5 主体）；K5 本体与短路径（非空 bank 短路 + `final_member_ids` 一致性校验 + journal 续跑 + final append 崩溃补齐，`composites/curation.py:929-949`）**一行未动**（`git diff 350b40d..475e109 -- src/expedistill/composites/` 为空）；SUMMARY_DIR/NAME 常量值不变，只是搬家到 `methods/curation/__init__.py:20-21`（§8 第 4 条"落盘目录用常量声明"落实），旧引用全部同提交迁移、无转发 shim（迁移纪律）。

## 四、金矩阵覆盖裁决（主视角 2）

**裁决：curation=none 与 exams=false 不进 8 组合金矩阵是可接受的，不应扩轴；但 exams=false 的 stage3 行为分支目前承担它的测试是缺位的（见 R2）。**

理由：

1. **金矩阵的预注册职责不含新协议**。它的身份是 P-1 采集的"既有协议组合"回归基线，方案 §7 P4 行明确把 curation=none / exams=false 归为"新实验"；§7.1 简化裁决后金矩阵只承担两件事——行为不变步骤无 diff、有意改行为的步骤 diff 即评审对象。P4 对 8 组合是前者（manifest 漂移属身份变化），职责已履行。把新方法塞进矩阵会让快照集合按方法数乘积膨胀，恰是 §7.1"避免 over-engineered"裁决反对的方向。
2. **none 的覆盖由其自带测试承担，且对其风险画像足够**：4 条单测（裸 accepted 集逐字成库并断言无 curation_log、超预算响亮停机、空集合法 bank、重跑幂等不翻倍）。none 没有可中断的中间态，"重跑等价"即"崩溃续跑等价"，4 条已覆盖其全部行为面（含预算硬阀）。
3. **agentbrew 的覆盖由既有套件承担**：`test_stage5.py` 5 条经新前端跑 K5、`tests/composites/test_curation{,_resume,_parallel,…}` 全套未动即仍有效——K5 一行未动是"测试不空转"前提的直接保证（方案 §2 R1 的风险在此兑现为收益）。
4. **exams=false 的冲突面覆盖完备**：test_params 3 条（exam 验收冲突、agentbrew 整理冲突、消融对可解析）；我独立枚举了全库 exams 消费者——`methods/acceptance/exam.py:136-140`、`composites/curation.py:701-708`（K5 回归）、stage3 自身、`assemble_reuse` 拷贝——前两者恰是 check 拒绝的两个组合，live_run / delivery / stage6 零 exams 依赖，装配白名单（只许 mode/split/tasks/methods.delivery/schema_version 不同）也堵住了从 exams=false 源装配出 exam 验收新 run 的口子。
5. **缺口**：`tests/unit/stages/test_stage3.py` 在本区间一行未改、无任何 exams:false 用例——stage3 的 `generate_exams` 门（`stage3_generation.py:223`）、skill-only 完成判据（:223）、`_EXAM_MISSING_ADVANCED` 跳过（:231）、exam=None 不落盘（:262,280-281）这条**续跑关键分支从未被任何测试执行过**；消融对三开关组合也未以流水线级跑过。这不违反 P4 验收的字面（exams=false 是既有方法的参数开关而非"新方法"），故不构成偏移约束，但属"该步验收是否真正被执行"视角下的实质覆盖缺口，修复成本极低（1 条假模型 stage3 单测）。

## 五、provider 改写的回归面（主视角 3）

1. **真实路径无损**：`_default_client` → `providers.default_client(timeout_sec)` → `PROVIDERS["openai_compat"] = OpenAICompatClient.from_env`（`llm/providers.py:25,31-38`）——与改前的 `OpenAICompatClient.from_env(timeout_sec=…)` 逐字等价；"一 client 每唯一模型"布局不变（`client_for` 记忆化，`pipeline.py:165-168`）。make_client 注入点保持一参数 `(model_name)` 签名（partial 只喂 timeout_sec），测试注入与真实路径互不穿透。
2. **金矩阵不经 provider 表**：harness 的 `run_straight` 走 `make_client=log.make_client`（`harness.py:394-397`），即 provider 表只被单元测试的 stub 触达——P5 提交未触碰 golden 与该结论互证。
3. **EXPEDISTILL_LLM_PROVIDER 误设的失败形态**：未知名字 → `infra.reliability.ConfigError`，消息含变量名、原值与已知名列表；触发点在 `_build_handles` 构建首个 client 时、即 stage0 冻结与任何网络请求之前（`pipeline.py:150-163`）；错误类与相邻 `llm/client.py:228`、缺角色检查一致，CLI 处置路径（clean stop）不变。
4. **provider 不进 manifest**：预注册裁决（方案 P5 行"端点选择是运维不是身份"）。披露性注记：换 provider 续跑与换端点 URL 续跑一样不被 manifest 拦截，属既定运维信任面，无新增风险类。
5. **None 档零新代码路径属实**：`ROLE_DEFAULTS` 无任何 None 项（无投机消费者，符合简洁化裁决）；机制验证——anchor 为 None 时 `setdefault` 跳过（`core/config.py:414-416`），`_build_handles` 的既有缺角色检查（`pipeline.py:158-162`）响亮拒绝并点名角色与配置文件。类型注解放宽为 `Dict[str, object]` 仅为容纳 None，全部 10 个现行角色的补全行为逐字节不变（金无 diff 佐证）。

## 六、风险清单（分级 → 位置 → 缓解）

| # | 分级 | 发现 | 位置 | 缓解 |
|---|---|---|---|---|
| R1 | 【琐细】（建议修复轮第一优先） | 7 份 v3 实验配置在 P4 后全部拒载：curation 槽显式给了 `rounds/token_budget` 却无 `name`，stage0 解析报 "methods.curation needs a name"。`upgrade_v2` 同步加了 name 但已生成文件未重生成（转换器 `NEVER overwrites`，`convert_v2_configs.py:65-67`）；P1b 审定文档"可作为后续实验起点"的结论随之失真。失败响亮、不产错误数据，无方案条目被违反，故非偏移约束 | `configs/experiments/{alfworld_skillopt_split,alfworld_skillopt_split_v2,searchqa_skillopt_split,tb21_full_v2,tb21_split_live_run,tb21_split_reuse,tb21_split_v2}_v3.yaml` 的 curation 段；根因 `methods/registry.py:84-92`（双方法槽须点名） | 每份补一行 `name: agentbrew`（v3 文件未启动正式 run，AGENTS 六.2 允许修改；转换器不覆盖故需手改），并在 `v3_config_review_20261015.md` 追加一条更正注记 |
| R2 | 【琐细】（建议修复轮处理） | stage3 的 exams=false 行为分支零测试：完成判据、`_EXAM_MISSING_ADVANCED` 跳过、exam=None 不落盘从未被执行过；消融对 (exams:false + curation:none + live_run) 亦无流水线级用例。这是预注册消融对首次真实启动前的实质覆盖缺口 | `src/expedistill/stages/stage3_generation.py:223,231,262,280-281`；`tests/unit/stages/test_stage3.py`（本区间零改动） | 补 1 条假模型 stage3 单测（exams=false：单元以 skill-only 完成、无 exams 落盘、重跑幂等、summary 无考题相关污染）；有余力再加一条消融对 `until_stage=5` 组合用例 |
| R3 | 【琐细】 | `EXPEDISTILL_LLM_PROVIDER` 未列入 `.env.example`（AGENTS 六.1"需要的环境变量列在 .env.example"）；缺省可用故非阻塞，但运维者需读源码才知道此旋钮 | `.env.example`（LLM endpoint 段）；`llm/providers.py:30` | 加一行说明（含缺省 openai_compat、未知名字响亮报错） |
| R4 | 【琐细】 | none 驱动硬崩（SIGKILL）残留 `skill_bank.jsonl.*.tmp` 孤儿且不清理；mkstemp 的 0600 权限随 rename 带到最终 bank 文件（append 路径是 0644）。全库无该模式读者，纯观感/垃圾文件问题 | `methods/curation/none.py:53-56,64-70` | 可选：驱动开头清一次陈旧 tmp；或接受现状并在 docstring 已有注释处补一句"硬崩残留 tmp 无害" |
| R5 | 【琐细】 | none 驱动对每个 skill 双次 `store.load`（列表推导的 if 条件里再 load 一次）——纯性能（每 skill 多一次文件读），store 不可变故无行为影响 | `methods/curation/none.py:27-32` | 一次 load 后按 status 过滤 |
| R6 | 【琐细】 | 方案 §5 的 v3 示例配置仍写 `curation: {token_budget: 96000, rounds: 3}`（无 name）——该形状在 P4 后非法，与实现矛盾；属方案文档内部滞后（§4 槽位表已预告 P4 双驱动，§5 注释也写明"单实现槽位不写 name"） | `docs/decisions/multi_method_framework_20261015.md:88` | 下次触碰该文档时按 P5 行先例加执行注记，或与 R1 一并在修复轮补注 |
| R7 | 【琐细】（聚合） | 微瑕三处：`check_authoring` 的 `.get("name", "exam"/"agentbrew")` 回退与 METHOD_DEFAULTS 双源（completed methods 必含 name，回退实际只在"槽位本身非法"时触发，报错信息略有出入但同为响亮 ConfigError）；`"skill_bank.jsonl"` 字面量在 none.py/store.py/assemble_reuse/prepare_inplace_recovery 四处重复（P4 前已如此）；test_params 的 curation 用例把 none 参数检查并入 agentbrew 用例而非独立成条 | `methods/authoring/__init__.py:29,34`；`methods/curation/none.py:51` | 均可不改；要改则 check 改 `["name"]`、文件名收进 store 常量 |

## 七、【偏移约束】级清单

无。

## 八、【赞同】清单（存档）

1. **append→整写的修复方向**：初版 `append_bank` 在重跑时翻倍、自查发现后改为"内容 = accepted 集纯函数"的原子整写并补幂等测试——这正是"幂等由构造保证"优于"幂等由流程保证"的正解，且行长/行序与 K5 约定一致（第二节主张 2）。
2. **双方法槽必须点名 + 缺省补全的边界**：`_entry_for` 对多方法槽无 name 即拒、`_validate_methods` 对给定槽逐字冻结不做按键合并——方案 §5"逐字冻结语义"的忠实执行；METHOD_DEFAULTS 与 upgrade_v2 同步加 name，未来转换即正确。
3. **集中冲突校验落位**：`check_authoring` 放在 authoring 槽位 check（方案 §3 预注册位置），stage0 对全槽 resolve 先于冻结 → 冲突在任务加载前响亮；拒绝面与 exams 消费者枚举精确一致（第四节第 4 条）。
4. **stage5 前端化与常量声明**：stage4 同形的 resolve-and-call 前端、SUMMARY 常量进 methods/curation（§8 第 4 条）、旧引用同提交迁移无 shim（迁移纪律）、composites.curation 一行未动（§2 R1）。
5. **金矩阵纪律**：P4 在同提交重生成并在提交说明写明 "Golden regenerated after verifying manifest-only drift"（§7.1 第 2 条）；provenance base_commit 沿用 P1b 评审已裁决的记法。
6. **P5 的最小实现与跳过留档**：两项落地均为零行为变化接缝，provider 表与 methods 注册表同形（纯数据表、无自动发现，§9），两项跳过（embedding、TB 真实查询文本）在方案 P5 行写明理由——"建了就是投机抽象"的简洁化裁决执行到位。
7. **provider 误设的失败形态**：早（冻结与请求之前）、响亮（列已知名）、同类（与既有配置错误同处置路径）。
