日期：2026-10-15
一句话说明：P4（curation 槽位双驱动 + authoring.exams 开关，commit fea4310）与 P5（provider 注册表 + ROLE_DEFAULTS None 档，commit 475e109）合并评审的 A 路意见——代码事实与行为等价视角；评审范围为 `git diff 350b40d..475e109`，对照基点 350b40d 上的源码逐行核实，结论：通过，无【偏移约束】级发现，10 条【琐细】。
对应 log.md 条目：2026-10-15 21:55（P5）；P4 为同日 21:20 条目

---

## 一、总体评价

P4 与 P5 的交付与方案 §7 对应行（P4 行、P5 行含两项跳过留档）逐项吻合，全部关键行为主张我在代码层独立核实成立：

- **agentbrew 薄壳与原 stage5 逐行等价**（curate_bank 十个实参逐一相同、summary 构造相同、常量同值搬家，K5 一行未动）；
- **none 的 bank 行格式与 append_bank 逐字节一致**由两处的 `json.dumps(..., ensure_ascii=False, sort_keys=True)` 参数逐字对上；
- **原子整写的幂等声明成立**，崩溃窗口分析见第二节 1c；
- **stage3 的 exams 门在 resume 语义下正确**，summary 形状不变；
- **check_authoring 的时机、顺序、错误信息与考题消费者闭环**全部核实；
- **P5 两个接缝在缺省路径上行为等价**，ROLE_DEFAULTS None 档在现状下零行为变化；
- **"仅 manifest 漂移"由 golden fixture 的 diff 自证**（8 个 fixture 各恰好 +1 行 `curation.name`）。

两条 P5 跳过项（embedding 非 chat 调用、TB 真实查询文本）的留档与方案 §7 P5 行、§8 思想实验一致，无投机抽象。无【偏移约束】级发现；10 条【琐细】见第四节，其中三条（.env.example 缺登记、ConfigError 双类型在 CLI 的呈现缺口、none 无 stale 防御未披露）建议后续顺手清理，均不阻塞推进。

## 二、逐项核查表

### 1. curation 双驱动（主视角任务 1）

#### 1a. agentbrew 薄壳与原 stage5（350b40d 的 src/expedistill/stages/stage5_curation.py）逐行对照

| 核对项 | 结论 |
|---|---|
| `curate_bank` 调用：store / weak_profile / answerer_handle / judge_handle / curator_handle / bank_token_budget / curation_rounds / usage / run_dir / workers 十个实参 | 逐一相同，仅变量名 `curation.` → `params.`；取值路径（`ctx.store`、`ctx.config.weak_profile`、`ctx.handle(...)`、`ctx.usage`、`ctx.run_dir`、`ctx.run_settings.workers`）不变 |
| members 构造 `[str(row["skill_id"]) for row in bank]`、summary 三键（members/size/bank_reference）、`write_json(..., indent=2)` | 逐行相同 |
| `SUMMARY_DIR`/`SUMMARY_NAME` 常量 | 值不变（"stage5_curation" / "stage5_summary.json"），从 stage5 模块搬到 `methods/curation/__init__.py`；全库无遗留的 `stage5_curation.SUMMARY_DIR` 引用（test_stage5.py 已同步改 import；test_curation_parallel.py 只 import 模块调 `run`；`assemble_reuse.py`、`store.py`、`pipeline.py` 用的是字符串常量，不受影响） |
| K5 本体（composites/curation.py） | P4 diff 未触碰，确认"一行未动" |
| stage5 前端 | 与 stage4 前端（`resolve` + `driver.run`）同形，声明属实；`resolve` 相比旧的 `resolve_params` 多一步按需 import 驱动，agentbrew.py 的 import 无副作用 |
| 行为等价的边角差异 | AgentBrewParams 校验错误文案从 "methods.curation.token_budget" 变为 "methods.curation.agentbrew token_budget"（信息更准确，非契约）；`resolve` 会在每次 stage5 调用时重跑 build_params（旧版 resolve_params 同样如此），check 均为 None，无影响 |

**结论：逐行等价成立。**

#### 1b. none 的过滤 / 排序 / 预算拒绝

- **过滤**：`status == "accepted"` 与 K5 `_accepted_members` 的判定逐字一致；排序 `key=skill.skill_id` 与 K5 的 `sorted(members, key=...)` 一致，确定性成立。
- **预算拒绝**：`bank_tokens > params.token_budget` → ConfigError，消息解释"无合并环可缩——提预算或换方法"。与 K5 的硬阀同构：K5 是 rounds 耗尽后仍超才停（有环可缩），none 无环立即停——语义正确。`count_bank_tokens` 复用 inject 的同一函数（D10），预算口径一致。
- **bank 行格式**：none 的 `json.dumps(skill.to_dict(), ensure_ascii=False, sort_keys=True) + "\n"` 与 `store.append_bank` → `append_jsonl` 内部的 `json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"`（UTF-8）**参数逐字相同**。验收主张"逐字节相同"在代码层证实。
- **零模型调用 / 无 curation_log**：驱动不取任何 handle、不写 `curation_log.json`，均有测试断言（test_curation_none.py）。
- **空集有效 bank**：空 payload 也整写，与原 stage5/K5 的"空 bank 是合法 bank"语义一致，有测试。
- **`Path(store.run_dir)` 的防御性包裹**：无害。

#### 1c. 幂等声明：整写 vs 追加的崩溃窗口分析

none 的写入序列 = mkstemp（bank 同目录，保证 `os.replace` 同文件系统原子）→ write → flush → fsync → `os.replace` → 写 summary，异常时清理 temp 文件——与 `infra/store.py` 的 `write_json` 既有原子写模式**逐行一致**。崩溃窗口三段：

1. **replace 之前崩溃**：bank 保持原状（旧 bank 或不存在），temp 残留为垃圾文件（`skill_bank.jsonl.*.tmp`，不会被误读）；重跑重新生成新 temp、重新整写。
2. **replace 之后、summary 写入之前崩溃**：bank 已完整；重跑重算 accepted 集（确定性纯函数）→ payload 逐字节相同 → 再 replace 一次（幂等覆盖）→ 补写 summary。
3. **与追加的对比**：`append_bank` 语义下重跑会把全部行再追加一遍（行翻倍）；K5 靠 journal 的 `final_member_ids` 补齐截断的 append，none 没有 journal，**整写是唯一正确选择**。提交说明里"初版 append_bank 有续跑重复行 bug、自查改为整写"的叙述与代码现状吻合，`test_rerun_is_idempotent_not_doubling` 覆盖重复调用路径。

**结论：幂等由构造保证的声明成立。** 没有模拟"写一半崩溃"的测试，但整写 + replace 的原子性使"一半"状态不可达，测试覆盖重复调用即够。

### 2. exams 开关（主视角任务 2）

#### 2a. stage3 的门在 resume 语义下

- **单元完成判据**：`skill_stored and (not generate_exams or store.exists("exams", exam_id))`。exams=true 时与原条件 `skill_stored and store.exists(...)` **完全一致**；exams=false 时退化为 `skill_stored`——正确（无考题时的完成判据只剩 skill）。
- **exam_missing_advanced 路径**：`if generate_exams and skill_stored and not _untouched_since_stage3(...)`——exams=false 时整个门关闭。注意到达该行时 `skill_stored` 必为 False（第一个门已把 skill_stored=True 的情形全部返回 `_GENERATED`），所以前缀实际是双保险关闭；逻辑正确。
- **终态行恢复路径**（`subtask_id in terminal`）与考题无关，不变。
- **生成路径**：`exam = None if not generate_exams else gen_exam(...)`、`if exam is not None: store.save(exam, "exams")`。exams=false 时崩溃窗口只剩 skill 单 save（save 前崩溃重跑重生成——README 既有续跑语义"未落盘的调用会被重问"，与 exams=true 时对称）；gen_exam 的 ModelOutputError 终态行处置只可能由 write_skill 触发，处置不变。
- **summary 形状不变**：`exam_missing_advanced` 键保留（恒空列表），下游 CLI/审计不破。
- `exam_id = make_exam_id(subtask_id)` 在 exams=false 时仍计算但无害（仅用于已关闭分支的错误文案）。
- stage3 每次调用 `resolve_params("authoring", ctx.config)` 会重跑 check_authoring（stage0 已跑过）——幂等校验、零模型调用，无害。

#### 2b. check_authoring

- **时机**：stage0 的 `run()` 在 freeze 之前对 `METHOD_SLOTS` 全部五槽位 `resolve_params`，每个槽位的 check 在 `build_params` 之后跑——"在冻结前集中拒绝"的声明与代码一致。`experiment.methods` 五槽位由 `_validate_methods` 保证齐全（缺失/空槽位补 `METHOD_DEFAULTS`），`["acceptance"]`/`["curation"]` 下标不会 KeyError。
- **拒绝顺序**：先 acceptance（`"exam"`）再 curation（`"agentbrew"`），两个独立 if（非 elif）——两个冲突都查；test_params.py 的第二个测试专门先设 live_run 再断言撞上 curation 冲突，顺序有测试钉住。
- **错误信息准确性**：exam 验收="文本考试协议用这个开关关掉的考题判 skill"；agentbrew 整理="K5 用回归考题证明每次合并"——与 README K5 行（"回归考试覆盖合并所代表的全部原始 skill 的考题，任一不过就撤销该次合并"）一致；两条都给出修正路径（live_run / curation: none / 开考题）。
- **消费者闭环**：考题的消费者 = exam 验收驱动（exam.py:137 `store.exists("exams", ...)`，含裸答、带 skill 作答、D8）、K5 回归（`_source_exams`）——全部在两个被拒组合之内；live_run、stage6、delivery 不消费考题。exams:false 的合法组合恰为预注册消融对（live_run + none），`test_the_ablation_pair_resolves` 验证可解析。

### 3. P5 两个接缝（主视角任务 3）

#### 3a. providers 注册表与 pipeline `_default_client` 改写

- **缺省路径行为等价**：未设 `EXPEDISTILL_LLM_PROVIDER` 时，`providers.default_client` → `PROVIDERS["openai_compat"]`（即 `OpenAICompatClient.from_env`）→ `from_env(timeout_sec=...)`——与原 `_default_client` 的 `OpenAICompatClient.from_env(timeout_sec=timeout_sec)` 是同一条路径。pipeline 侧的 `partial(_default_client, timeout_sec=llm_timeout_sec)`、每 unique model 一个 client 的布局、`model_name` 形参保留未用，全部不变。
- **未知名响亮**：KeyError → ConfigError，`from None` 断链，消息列出全部已知名；有测试。
- **纯数据表、无自动发现**：与方法注册表同形；新增 provider = 表加一行 + builder 模块，符合方案"explicit list"纪律与 §9 禁止清单。
- **环境变量选择 provider 是运维不是身份**：不进 manifest，与"配置只存身份"一致；方案 P5 行如实记录此裁量。
- **分层**：llm/providers.py 依赖 infra.reliability（层 2 → 层 1 允许）与同包 client.py（第 2 层无互不依赖规则）；`ConfigError` 用 infra 的——与 client.py `from_env` 抛的同一个类，P5 内部一致。
- **测试有效性核对**：`monkeypatch.setitem(PROVIDERS, ...)` 修改的是 `default_client` 内部读的同一模块级 dict 对象（import 不复制容器），stub 生效方式正确。

#### 3b. ROLE_DEFAULTS None 档

- **现状零行为变化**：P5 后 ROLE_DEFAULTS 十个键仍全部为 "strong"/"weak"（executor_strong/executor_weak/segmenter/author/examiner/judge/rewriter/curator/auditor = strong，answerer = weak），无 None 值；`if anchor is None: continue` 永不触发，`setdefault` 循环逐字节不变。pipeline 的缺角色检查（`for role in ROLE_DEFAULTS: if not cfg.models.get(role)` → ConfigError，报角色名 + 配置文件名）不变。
- **未来 None 档语义成立**：None 锚位不补默认 → 该角色在 `cfg.models` 缺失 → 既有检查响亮拒绝。"must-configure 角色零新代码路径"声明属实。
- **与方案的关系**：§5 说"None（必须显式配置）档位等第一个使用者出现再加"——P5 加的是机制（数据表支持 None），没有造使用者（没有任何角色填 None），与 R5"角色表不开放"一致，不算提前抽象。

### 4. 验收主张核对（执行声明 vs 可核实证据）

| 主张 | 核实结果 |
|---|---|
| P4 后"仅 manifest 漂移"在 8/8 组合验证过，再重生成 golden | 8 个 golden fixture 的 diff 各恰好 +1 行（`curation` 槽加 `"name": "agentbrew"`），provenance.json 只改 base_commit——fixture 是全产物快照，diff 只有 manifest 一行即自证"除 manifest 外所有产物与旧 golden 一致"。重生成前"先验证后重生成"的过程我无法从代码独立复核，但落盘结果与该结论一致 |
| curation 缺省加 name 属身份变化 | METHOD_DEFAULTS 加 name → 缺省槽位的冻结值变化 → 旧 v3 run（P1b–P4 之间冻结）续跑会被 stage0 逐字段比对拒绝——预期行为（方案 P4 行 manifest 列 = "新实验"），提交说明与 README 已披露 |
| none 的 bank 行与 append_bank 逐字节相同已验证 | 代码层证实（见 1b）：序列化参数逐字相同 |
| 1924 测试全绿 | 按约束未跑测试；算术自洽：1914（P1b 修复尾）+ 7（P4：test_curation_none.py 4 个 + test_params.py 3 个）+ 3（P5 provider 测试）= 1924 |
| 每个新方法自带续跑测试（P4 行验收） | none：`test_rerun_is_idempotent_not_doubling`（对零模型调用的驱动，重复调用即续跑语义，且整写的原子性使"写一半"不可达）；agentbrew：行为不变的薄壳，K5/stage5 的既有续跑测试全部保留（test_stage5.py、test_curation_parallel.py，夹具同步加 name），符合"新名字行为不变则沿用既有覆盖"的合理口径 |

## 三、【偏移约束】级清单

**无。** 对照方案 §2–§9 逐条：

- §2/R1：K5 本体未搬、驱动只薄壳（diff 确认 composites/curation.py 零改动）；
- §3：check 放槽位契约内、stage0 集中跑、Params dataclass + `__post_init__` 约束、纯表注册不回装饰器——全部遵守；驱动写 stage summary 是 P1a 起的既有模式（exam.py:202、live_run.py 同样自写），P4 沿用，非本步偏移；
- §5：逐字冻结语义未破坏（METHOD_DEFAULTS/upgrade_v2_config/COMPLETED_METHODS/golden fixture/test 夹具六处 name 同步一致）；`authoring.exams` 缺省 true 不变；角色表未开放；
- §6：目录名、阶段标签、失败账本未动；
- §7：P4/P5 行内容与交付一致，P5 两跳过项留档于方案（投机抽象避免）；
- §7.1：golden 同提交重生成、diff 即评审材料——遵守；
- §9 禁止清单：无新 hash/版本号/审计机制/DAG/DI/自动发现。

## 四、【琐细】级清单

1. **`.env.example` 未登记 `EXPEDISTILL_LLM_PROVIDER`**（P5）。仓库纪律是"需要的环境变量列在 .env.example"；且 `OpenAICompatClient.from_env` 的错误信息本来就指向 .env.example，运维会去那里找这个新旋钮而找不到。一行补登记即可。
2. **`check_authoring` 的缺省方法名硬编码**（`"exam"` / `"agentbrew"`）与 `METHOD_DEFAULTS` 重复（两处真相）；且该 `.get` 默认分支只在"槽位非空但缺 name"时可达——这种配置本会被 `_entry_for` 以 "needs a name" 拒绝（双方法槽位），但 authoring 的 check 先跑，会先给出冲突错误，掩盖真正的"缺 name"问题。错误都响亮，仅误导性轻微。
3. **stage3 的 module docstring 未同步 exams 开关**：仍写"A subtask whose (skill, exam) pair is already stored is skipped"，exams=false 时判据只剩 skill。门内的新注释解释了行为，但模块级文档漂移。
4. **core.ConfigError 不是 CLI main 捕获的 infra.ConfigError**：`check_authoring`/`check_live_run`/`build_params` 抛 core 的 `ConfigError(ValueError)`，而 `cli.main` 只捕获 `infra.reliability.ConfigError`——配置冲突错误经 CLI 启动时以 Python traceback 呈现而非干净的 "error: ..."。这是 **P1b 起的遗留缺口**（check_live_run 同模式），P4 沿用非回归；test_cli.py 只覆盖 infra.ConfigError 路径。建议后续统一（stage0 边界转换或两类型合一）。
5. **none 驱动没有 K5 的 stale 拒绝**：bank 写成后 accepted 集再变化时，K5 停机拒绝（"accepted after curation started → start a new run directory"，防御 stage6 已有轨迹携带旧 bank），none 会静默重算重写。正常 pipeline 流程下不可达（stage3/4 有 infra 洞时该阶段 raise、到不了 stage5；resume 时全绿无洞可补），异常路径下两者的防御姿态不一致；none 的"bank = accepted 集纯函数"世界观自洽，但这个与 K5 的差异未在 README/驱动 docstring 披露。建议补一句披露（或明确记录"纯函数语义、无需 stale 检查"的裁决）。
6. **none.py 对每个 skill `store.load` 两次**（列表推导 + 过滤条件各一次）；K5 的 `_accepted_members` 只 load 一次。纯性能，单元数量小，无正确性影响。
7. **`ROLE_DEFAULTS: Dict[str, object]` 注解宽于必要**：`Dict[str, Optional[str]]` 更准确表达"strong / weak / None 三态"；object 放行任意类型。既有行为未恶化（anchor 非 "weak"/None 的字符串仍会静默归 strong，P5 前后一致），仅注解未表达意图。
8. **`_default_client` / `providers.default_client` 的返回注解从 `OpenAICompatClient` 弱化为 `Any`**：类型信息丢失（表内唯一 builder 就是 OpenAICompatClient），可用返回类型参数化或保留原注解。
9. **provider 的三个测试放在 `tests/unit/methods/test_params.py`**（TestProviderRegistry）：与"tests/unit/ 目录与 src 对应"的惯例不符（应在 tests/unit/llm/ 下）。
10. **provenance.json 的 `base_commit` 语义**：记录的是生成时刻的 HEAD（350b40d），而 golden 实际由 fea4310 的内容生成（提交前 working tree）；按字面 checkout base_commit 无法复现含 `curation.name` 的 manifest。P1b 起同模式（00971f2），方案 §7.1 已声明"信息记录，不是纪律"，fixture 内容自证生成代码为新代码——但"base_commit"字面语义与事实不符，值得在 generate.py docstring 里写明是"生成时刻的 HEAD"。

## 五、【赞同】级清单（正面确认，供修复轮参考）

1. agentbrew 薄壳的逐行等价与"K5 一行未动"（1a 全表）；stage4/stage5 前端同形声明属实。
2. none 的 bank 行与 append_bank 逐字节一致的代码级证实；原子整写与 `store.write_json` 既有模式逐行一致；整写 vs 追加的取舍论证（K5 追加因 journal 可中途恢复、none 无环无 journal）在代码与注释里自洽，初版 bug 的自查与修复过程留痕诚实。
3. stage3 exams 门的三处修改（完成判据、exam_missing_advanced 前缀、save 条件）各自最小且正确，summary 形状不变保护下游。
4. check_authoring 的集中校验选址（槽位契约内、stage0 冻结前跑）正符合方案 §3"跨参数/跨槽位约束放 check"的设计；拒绝顺序有测试钉住；错误信息与 README 协议描述一致、给出修正路径。
5. 消融对的预注册组合（exams:false × curation:none × live_run）在 check、测试、README 三处一致表达。
6. providers 表的纯数据表形态与"未知名响亮"的实现、测试；缺省路径与原 `_default_client` 逐字节等价。
7. ROLE_DEFAULTS None 档的零行为变化论证与"零新代码路径"的实现（复用既有缺角色检查）；机制先行、不造使用者，避免了 §9 禁止的投机抽象。
8. golden 重生成纪律的执行（同提交落 diff、8 fixture 仅 +1 行、provenance 更新），以及 METHOD_DEFAULTS/upgrade_v2_config/测试夹具六处 name 同步的一致性。
9. 分层检查的静态核对通过：agentbrew.py（层 6 依赖层 5/1/0 + 自己的槽位包）、none.py（层 0/1 + 槽位包）、providers.py（层 1 + 同包），方法驱动互不 import，test_layering 的规则对新文件自动覆盖。
10. 两项 P5 跳过的决策质量：以"无消费者即不建"的口径留档于方案，且为 embedding 留了明确的未来入口（provider 表即接缝）。
