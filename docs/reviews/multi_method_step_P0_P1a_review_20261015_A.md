日期:2026-10-15
一句话说明:P0+P1a 合并评审的 A 路(代码事实与行为等价)意见——五个代码提交逐条对照 `git diff bcb3ffe..297629f` 静态核查,结论为全部行为等价、无【偏移约束】级发现,五个【琐细】级发现与三个关注点的裁决见正文。
对应 log.md 条目:2026-10-15 17:50(P1a:方法驱动层落地,按名分发);并覆盖 15:05 / 16:40 / 16:55 三条 P0 条目。

---

## 一、总体评价

**通过,无需修复即可继续(P1b 前仅建议三处文档更正,均为【琐细】)。**

审查方式:逐提交(`git diff <c>~1..<c>`)对照基点 `bcb3ffe` 的原始实现做静态逐行比对(不跑测试、不碰 runs/、不碰 TB-dev),重点验证"行为不变"的三类主张:产物逐字节不变、manifest 不变、exam 分发链不加载 live 驱动。

核心结论:

1. **P0 四个提交的每一处合并,函数体都与被删除的副本逐行一致**(shortfall 读写、discard_skill、stage3_candidates、tally_verdicts、aux_wiring、tasks_digest/_canonical、build_manifest 两处手写字典)。仅有的差异全部在不可达或纯错误消息路径上,逐条列在核查表中。
2. **P1a 的驱动迁移是逐行搬运**:exam 驱动 `run()` 与原 stage4 exam 分支、live 驱动除三处 import 换源外零改动、两个 delivery 驱动与原 stage6 分支逐行一致(含 whole_bank 的库指纹记账与 retrieval 的审计写时序)。
3. **"exam 进程永不加载 live 模块"这个表述在基点就不是字面事实**(composites 包 __init__ 一直拉入 K6→blocks.attribution→prompts.attribution),真正的不变量是**冻结 prompt 名集不漂移**(METHOD_PROMPT_NAMES import 时快照 + stage0 的 exam 分支)加**分发链不加载 live 驱动**,两者 P1a 都未动且都有测试守护。但 P1a 的三处新 docstring 丢掉了基点的括号澄清、把"驱动不加载"升格成"进程不加载",与代码事实不符(详见发现 9,建议更正)。
4. 验收证据成立:五个代码提交全部未触碰 `tests/golden/` 与 `tests/fixtures/golden/`(git log 逐一验证),golden 快照覆盖 run 目录全部文件(含 manifest),仅归一化 5 个 volatile 键;"产物逐字节不变、manifest 不变"在如实披露的覆盖面内(假模型/假 benchmark、无远程、无真实端点)由该机制直接钉住。"每提交测试全绿(现 1901)"无法静态验证,但与逐行等价结论互相印证,无矛盾。
5. 流程备注(非偏移):方案 §10 要求"每步三路评审通过后进下一步",实际 P0 未单独评审、P1a 在 P1a 评审前已实施,合并为本轮评审系用户批准的范围裁定;实质审查覆盖无缺口(diff 范围一致),风险仅是"若本评审发现 P0 问题,P1a 需连带返工"的理论成本。

## 二、逐提交核查表

| # | 提交 | 发现 | 分级 | 证据(file:line) | 建议 |
|---|---|---|---|---|---|
| 0 | a6ec16b(P-1′,前置) | golden 基点为 dad6d28(src 与 bcb3ffe 相同,提交说明自证),快照覆盖 run_dir 全部文件(排除 run.lock),volatile 仅 time/duration_ms/request_id/processed_this_call/run_dir 前缀;请求史按多重集比较,续跑语义可执行 | 【赞同】 | `tests/golden/harness.py:420-453`、`test_golden_matrix.py:34-65`、`tests/fixtures/golden/provenance.json` | 无 |
| 1 | f1b236a(P0.1) | RunSettings 默认值(workers=1、k1_stagger=0.0、allow_infra_shortfall=False)与 `run_pipeline` 参数默认值一致;`ctx.workers` 动态挂属性→构造传参;7 处 `_workers` 的 config-first 分支确认是死分支(基点 ExperimentConfig 无 workers 字段) | 【赞同】 | `stages/context.py:18-49`、`stages/pipeline.py:359-369`、`stages/pipeline.py:254-260`、基点 `core/config.py:451` | 无 |
| 2 | f1b236a | 原 `_workers`/`_k1_stagger`/`_allow_infra_shortfall` 带 `int()/float()/bool()` 强转,新代码直接读字段。CLI 侧 argparse 已保证类型(`cli.py:272/289`),仅程序化调用 `run_pipeline(workers="4")` 这类非法输入的行为由"静默转换"变为"原样透传",属 API 契约自然收紧 | 【琐细】 | `stages/context.py:26-31` 对比基点 `stage1_baseline.py:134-149` | 无需处理;若在意可在 P1b 顺带在 RunSettings 加 `__post_init__` 类型断言 |
| 3 | 08c7985(P0.2) | 5 处插件解析收敛到 `resolve_plugin`(execute/tasks/stage1 aux_wiring/stage2 _plugin/stage3 _resolve_plugin),import+get 语义逐处等价;错误消息统一为最详细版本并同步了唯一 pin 旧文案的测试(提交说明已声明) | 【赞同(已声明)】 | `benchmarks/registry.py:35-49`、`tests/unit/blocks/test_tasks.py:148` | 无 |
| 4 | 08c7985 | stage1 `aux_wiring` 与 stage2 `_plugin` 原来对 import 失败**不捕获**(裸抛 ImportError),统一后转为 ConfigError(retryable=False,停止当前阶段而非被异常分类处置)。该路径不可达——两个调用点之前 stage0/load_tasks 已成功 resolve 同一插件——且新方向更符合 B6 的语义 | 【琐细】 | 基点 `stage1_baseline.py:213-231`、基点 `stage2_segmentation.py:105-112`、`benchmarks/registry.py:44-48`、`infra/reliability.py:77-81` | 无 |
| 5 | 08c7985 | `write_skill(prompt_name=None)`:缺省路径逐字节不变(`name = prompt_name or "SKILL_DISTILL"`,局部变量改名,call_model 与错误消息引用同步);新增正向前置测试验证变体 prompt 走 mode 的四段契约 | 【赞同】 | `blocks/author.py:297/333/360/365`、`tests/unit/blocks/test_author.py:197-225` | 无 |
| 6 | 94eded0(P0.3) | 四组共享件合并逐行等价:`shortfall.read_unit_ids/write_unit_ids` 与 stage1 版函数体一致;`discard_skill` 从 composites/acceptance.py 逐字节搬出;两个 `_candidates`/两个 `_tally` 与 `skill_status` 版逐行一致;K6 `pending_groups` 的候选过滤换成 `is_stage3_candidate` 取反(逻辑等价);`aux_wiring` 原样上移;`tasks_digest`+`_canonical` 两份副本逐字节相同(地址剥离正则亦同),换共享版后 digest 值不变 | 【赞同】 | `composites/shortfall.py:42-67`、`composites/skill_status.py:20-67`、`core/ids.py:111-116`、`composites/live_acceptance.py:418`、`blocks/tasks.py:142-200` 对比基点 `stage0_freeze.py` | 无 |
| 7 | 94eded0 | 合并处错误消息统一的唯一行为差异:live 轮 `_read_round_shortfall` 的 "invalid accepted-shortfall record {path}" 原本**不含** unit_ids 值,共享版统一为 "{path}: {unit_ids!r}"(stage1 版)。仅错误路径的消息文本 | 【琐细】 | 基点 `stage4_live_run.py:267` 对比 `composites/shortfall.py:56-58` | 无 |
| 8 | 94eded0 | `assemble_reuse._load_shortfall_units` **保留宽松读是正确的**:它读的是源 run(可能由旧代码写出的记录),缺键/空值视为无空洞、`str()` 容忍非字符串,且每条 id 仍经 `parse_trajectory_id` 校验兜底为 ConfigError;若改用共享的 fail-loud 读取,反而会把"键缺失的旧格式源 run"从静默装配变为拒绝——那才是行为变化。P0 的纪律是行为不变,保留正确 | 【赞同】 | `stages/assemble_reuse.py:480-505` | 无 |
| 9 | 94eded0 | 残留死代码:`_tasks_digest` 删除后,assemble_reuse 的 `_canonical`(290-320 行)无任何调用者但仍保留,`enum`/`dataclasses`/`re` 三个 import 仅被它使用(hashlib 另有用途)。不影响行为,ruff 的 F 检查不覆盖模块级未用函数 | 【琐细】 | `stages/assemble_reuse.py:88-90、290-320、391-399` | P1b 顺带删除 |
| 10 | edbe66e(P0.4) | `build_manifest` 与原两处手写字典逐字段等价:字段集(benchmark/mode/split 三键/config/prompt_digests/rules/model_roles/tasks_digest/aux_metric)、取值路径(`acceptance_mode(config)` 与原 `acceptance` 变量同值、`list()` 包裹幂等)、assembler 的 `_prompt_digests` 副本与 stage0 版逐行一致(含 live_run 懒 import 分支);`frozen_config.to_dict()` 两版相同 | 【赞同】 | `stages/stage0_freeze.py:246-275` 对比基点 `stage0_freeze.py:277-297` 与基点 `composites/assemble_reuse.py:1105-1138` | 无 |
| 11 | edbe66e | assembler 上移 stages/ 修正了复制的根因(组合层不得 import 阶段层);`composites/__init__` 移除其 re-export,全库(含 scripts/)无旧路径残留引用;cli import 同步 | 【赞同】 | `stages/assemble_reuse.py:101`、`composites/__init__.py`、`cli.py` | 无 |
| 12 | da8a686 | 纯文档(决策文档 §2/§7 的 run_units_disposed 取消记录 + log),与代码现状一致 | 【赞同】 | `docs/decisions/multi_method_framework_20261015.md` §2/§7 P0 行 | 无 |
| 13 | 297629f(P1a) | 注册表为纯数据表 `(slot,name)→module_path`,解析时才 import;错误消息风格与 core/registry 对齐("unknown {slot} method {name!r}; known: …")但用 ConfigError(比 KeyError 更贴合 B6 分类);`get_method("delivery", mode)` 的未知 mode 报错与原版"静默按 whole_bank"不同,但 config 层 `SKILL_DELIVERY_MODES` 白名单使该路径两侧都不可达 | 【赞同】 | `methods/registry.py:26-47`、`core/config.py:106`、`stages/stage6_test.py:313-314` | 无 |
| 14 | 297629f | exam 驱动逐行搬运:`run()` 与原 stage4 exam 分支完全一致(枚举、ledger 恢复、MOE 先终态后记账、IE 记账、summary、末尾 raise 一字不差);`STAGE4_DIR/STAGE_LABEL/SUMMARY_FILENAME` 常量值不变,失败账本与 pipeline_log 的阶段标签("stage4_acceptance")不变,审计脚本以路径字符串读取不受影响 | 【赞同】 | `methods/acceptance/exam.py:78-197`、`methods/acceptance/__init__.py`、`pipeline.py:406`、`scripts/audit/audit_live_run.py:86` | 无 |
| 15 | 297629f | live 驱动:除 docstring 一行、常量与 stage-1 空洞读换源外零改动;三处 `stage1_baseline.accepted_infra_shortfall(ctx.run_dir)` → `shortfall.stage1_accepted_unit_ids(ctx.run_dir)`,路径逐字节相同(`run_dir/stage1_baseline/accepted_infra_shortfall.json`)、读取语义相同(同一个 fail-loud `read_unit_ids`),方法层不 import 阶段层的分层要求满足 | 【赞同】 | `methods/acceptance/live_run.py:121、440、781、818-823`、`composites/shortfall.py:29-38` 对比基点 `stage1_baseline.py:106-131` | 无 |
| 16 | 297629f | delivery 等价:whole_bank.resolve 返回 `{skills_injected: bank_reference(bank), skills_block: format_skills_block(bank)}`——**库指纹记账保留**;retrieval.resolve/_selected_references(含 Mapping 分支)/write_audit 与原 stage6 逐行一致,审计文件路径同(`stage6_test/retrieval_audit.jsonl`),写时序仍为 resolve→write_audit→dispatch(decision 15);bank_kwargs 基础字段与 control/bare 分支逐字段未动 | 【赞同】 | `methods/delivery/whole_bank.py:22-27`、`methods/delivery/retrieval.py:39-127`、`stages/stage6_test.py:298-348` | 无 |
| 17 | 297629f | test_layering 三项扩展到位(层号 methods=6/stages=7/cli=8;相对 import 解析后纳入层级检查;方法驱动互不 import 专项测试,__init__ 豁免合理);模块与其测试同提交迁移、旧位置无转发 shim;patch 目标同步迁移且"断言没发生"类测试保有正向前置(test_an_exam_run_never_imports_the_live_modules 的子进程断言 + 进程内控制) | 【赞同】 | `tests/test_layering.py:6-95`、`tests/unit/methods/acceptance/test_exam.py:1046-1060` | 无 |
| 18 | 297629f | 三处新 docstring 称 "an exam process still never loads the live modules"(`stage4_acceptance.py` 模块头、`methods/__init__.py`、`methods/registry.py`),丢失了基点 docstring 的括号澄清。**与代码事实不符**:exam 进程 import K4(`methods/acceptance/exam.py:81` `from expedistill.composites.acceptance import ...`)必先初始化父包 `composites/__init__.py:6`,后者 import live_acceptance→`blocks.attribution`→`prompts.attribution`,live 两个 prompt 事实上已注册——基点 docstring 自己写明这一点,且迁移后的测试注释也承认("the two live prompts ARE registered in the process")。真正的、且被守住的不变量是:(a) 冻结名集 = METHOD_PROMPT_NAMES import 快照不漂移;(b) exam 的**分发链**不加载 live 驱动(sys.modules 断言)。行为未变,但表述升级会误导后来者把模块隔离当成保证 | 【琐细】 | `composites/__init__.py:6`、`blocks/attribution.py:13-21`、`prompts/__init__.py:20`(不含 attribution)、`tests/unit/methods/acceptance/test_exam.py:1015-1030`、`README.md:663-665` | 三处 docstring 各补一句基点的括号澄清;README 663 行的"blocks.attribution / prompts.attribution 不进 exam 的 import 图"同步更正(该句基点即与事实不符) |
| 19 | 全部 | 五个代码提交(f1b236a/08c7985/94eded0/edbe66e/297629f)均未触碰 `tests/golden/`、`tests/fixtures/golden/`(git log 逐一验证为 0 文件),符合"行为不变的步骤不得碰 golden"的纪律 | 【赞同】 | `git log bcb3ffe..297629f -- tests/golden tests/fixtures/golden`(仅 a6ec16b/1c7c307) | 无 |

## 三、三个关注点的裁决

### a. run_units_disposed 取消——**裁决:维持取消,方案 §2 的理由成立,行数描述需微修**

逐处核查(当前 HEAD)的真实形态:

- **stage2**(`stages/stage2_segmentation.py:202-224`):`segment_unit` 只有 **1 个 except 分支**(InfrastructureError,3 行:记账+返回 `_UnitOutcome(id, "infra", 0)` NamedTuple)。**没有 ModelOutputError 处置**——K2(`composites/segment_checked.py:148-152`)在组合积木内部消化了 ModelOutputError(重试→耗尽→审计行+返回 None→"skipped")。
- **stage3**(`stages/stage3_generation.py:258-270`):2 个 except。MOE 分支 **7 行**(终态 failures 行先写、terminal 字典、`ledger.record_once`、返回字符串标签),IE 分支 2 行。
- **exam**(`methods/acceptance/exam.py:156-165`):2 个 except。MOE 分支 2 行(先 discard_skill 终态、后补 ledger 行),IE 分支 2 行,返回 `(kind, skill_id)` 二元组;另有枚举阶段的 crash 恢复(丢弃在而账本行丢的补写)。
- 决策文档 §2 引的 "live 先写组终态" 发生在 **K6 内部**(`composites/live_acceptance.py:856-871`),本就不在"stage 层原语"拟覆盖的三处之内。

结论:三处的**异常集**(stage2 无 MOE)、**处置动作**、**返回形状**(NamedTuple / 字符串 / 元组)、**crash 恢复逻辑**互不相同,抽象必须参数化全部四维,成本确实高于收益;"ModelOutputError 的处置顺序是各阶段的续跑契约"经核属实(stage3 先写终态行、exam 先丢弃 skill、live 先写组终态——三者顺序各异)。**取消正确。**

唯一瑕疵:§2 的"各约 3 行 try/except"低估了 stage3(单分支 7 行),且未写明 stage2 实际没有 ModelOutputError 分支(K2 内部消化)——把 live 的处置拉进来佐证也越出了三处范围。这些不影响结论,反而说明重复的形状差异比文档描述的更大。建议在 §2 补一句:"stage2 的 ModelOutputError 由 K2 内部处置,stage 层仅处置 InfrastructureError;live 的组终态处置在 K6 内部"。

### b. P0.3 提交说明"隔离理由已随懒注册删除而消失"——**裁决:理由链不成立(懒注册仍在,删于 P1b),但合并本身正确;真实的等价依据是共享件不含 prompt 面 + golden 钉住;建议文档更正**

事实:懒注册在 HEAD 297629f 仍然存在——`prompts/__init__.py:20` 的 import 清单不含 attribution,`config.py:131` 的 `_MANIFEST_OPTIONAL_FIELDS` 仍在;方案 §5/§7 明确两者都是 **P1b** 的删除项。因此 P0.3 提交说明的因果链("懒注册已删除→隔离理由消失→可以合并")是错的:隔离理由**当时并没有消失**。

但合并的正确性不依赖这个理由链:

1. 被合并的三个 helper(discard_skill、stage3_candidates/tally_verdicts、shortfall 读写)是纯 store/JSON 操作,**不含任何 prompt 注册、不触发任何模块加载差异**——exam 侧 import `composites.skill_status`/`composites.shortfall` 与原 import `composites.acceptance` 相比,新进入 import 图的模块没有任何一个会改变 prompt 名集(真正会注册 live prompt 的 blocks.attribution 经 composites/__init__ 本来就在 exam 进程里,见发现 18)。
2. 行为等价的直接证据是 golden:8 组合中 4 个 exam 组合全过,快照含 manifest,而 manifest 的 `prompt_digests` 字段逐字节编码了冻结名集——名集不漂移是被钉住的,与懒注册在不在无关。
3. 原隔离(决策文档 §1 表②的根因是分层规则,exam/live 两份候选过滤与 tally 的隔离另有 docstring 称"保护 exam 路径的冻结 prompt 名面")在机制上本就保护不了任何东西——两份副本间不存在经 import 传播的污染路径。

建议:P0.3 的更正不必改写历史提交,但应在决策文档 §2(或 log 更正条)补记:"合并 exam/live 共享 helper 的依据是 helper 不含 prompt 面且 golden(含 manifest)钉住等价;与懒注册删除无关(懒注册 P1b 才删)"。提交说明里的不实表述因此有了书面更正,避免 P1b 评审再被它误导。

### c. 注册表用纯 dict 而非复用 core/registry.py——**裁决:接受,且这是保住 P1a 验收主张的唯一正确实现;建议在决策文档 §3 记录此偏离与 P1b 的衔接**

核查事实:`core/registry.py` 的机制是**值注册表**——装饰器 `register(name)` 把**已 import 的对象**存进表(注册即要求模块已加载)。方案 §3 的 `@method(...)` 装饰器形式 + §8 清单第 1 条"在 methods/__init__.py 显式 import"意味着:**exam 进程 import methods 包就会加载 live_run 驱动模块**。这与方案 §2 目标结构及 AGENTS 第四节"exam 进程永不加载 live 模块"(基点靠 stage4 顶部的懒 import 实现)直接冲突——**按 §3 原样实现反而是行为变化**,而 P1a 的验收主张恰恰是 manifest 不变、分发链行为不变。

实现选择了纯数据表(`_ENTRIES` 的值是模块路径字符串,`get_method` 解析时才 import)——这是唯一同时满足"按名分发 + 按需加载 + manifest 不变"的形式。用户"简洁化"裁决亦覆盖此偏离。配套事实:错误消息风格对齐 core/registry("unknown {slot} method …; known: …"),测试守护(`test_methods_do_not_import_each_other` + `test_an_exam_run_never_imports_the_live_modules`)到位。

衔接建议:P1b 落地 Params/check 时,沿纯表扩展(值改为 `(module_path, Params, check)` 或同义的元组),**不要**改回装饰器形式——并请在决策文档 §3 加一行脚注,记录"注册表实现为路径表而非装饰器值注册表,理由:保住驱动级懒加载(§2 的行为约束优先于 §3 的接口形式);Params/check 挂在表项上"。否则 P1b 评审时会被 §3 原文与实现的差异反复绊住。

## 四、【偏移约束】级清单

**无。**

对方案 §2–§9 逐条对照的结果:P0 行全部计划项按方案落地(run_units_disposed 取消有决策记录,归关注点 a);P1a 行三项(methods 层与注册表、acceptance/delivery 驱动迁移、test_layering 扩展)全部落地;§2 目标结构与 AGENTS 层表同步更新;§9 禁止清单未引入任何新元素(needs/provides、extends、方法基类、类型化 Summary、新 hash/审计机制、目录迁移均无);迁移纪律(模块+测试同提交、无 shim、断言没发生类测试补正向前提)全部遵守。§3 的注册表形式偏离经关注点 c 裁决为必要且正确,不算偏移;§10 的"每步评审后进下一步"由用户批准的合并评审替代,实质覆盖无缺口,已在总体评价第 5 点记录。

## 五、附:验收主张的证据强度

| 主张 | 证据 | 强度 |
|---|---|---|
| 每提交测试全绿(现 1901) | 提交说明与 log 四条目一致声明;静态审查未发现任何会致红的矛盾;1900→1901 与新增 1 个 layering 测试吻合 | 间接(本评审未运行) |
| golden 十测试零 diff = 产物逐字节不变 | 快照机制覆盖 run_dir 全部文件含 manifest,volatile 归一化仅 5 键;五个代码提交零触碰 golden fixtures | 高(机制直接钉住) |
| manifest 不变 | manifest 在 golden 快照内逐字段比对;`build_manifest` 字段集与原手写字典逐字段等价(发现 10) | 高 |
| exam 分发链不加载 live 驱动 | `test_an_exam_run_never_imports_the_live_modules` 子进程断言 + `sys.modules` 控制;registry 纯表解析时才 import;exam.py 不 import live_run | 高 |
| 冻结 prompt 名集不漂移 | METHOD_PROMPT_NAMES 快照未动;stage0 `_prompt_digests` exam 分支未动;golden 含 prompt_digests 逐字节比对 | 高 |

覆盖面限制(决策文档 §7.1 已如实披露):假模型、无远程 TB 链路、无真实端点——远程与端点路径由在跑正式 run 与 live 冒烟验证,该缺口不由本轮评审补足,属既定边界。
