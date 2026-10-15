日期：2026-10-15
一句话说明：P0+P1a 合并评审的评审 B 意见（扩展性与反过度设计视角），审阅范围 `git diff bcb3ffe..297629f`（f1b236a、08c7985、94eded0、edbe66e、da8a686、297629f），静态阅读、未跑测试、未触 runs/ 与 TB-dev。
对应 log.md 条目：2026-10-15 17:50

---

## 一、总体评价

**通过，无【偏移约束】级发现。** P0+P1a 与方案（docs/decisions/multi_method_framework_20261015.md §2–§10）一致，方向上净减复杂度而不是加：P0.1 净 -70 行、P0.2 净 -15 行、P0.4 净 -28 行，四步全部在消灭真重复（§1 问题②清单逐项对上：`_workers` ×7、stagger/shortfall 各 ×3、插件导入 ×5、stage0 冻结副本、shortfall 读取器 ×3、`_tally` ×2、候选过滤、"名字像但语义不同"的 stage6 per-arm shortfall 记录刻意未合）。methods 层就是方案要的形状：纯数据表 + 按需 import + 薄 stage 前端，没有装饰器仪式、没有基类、没有 Params 提前落地（P1b 的事）。§9 禁止清单逐项核对无违反（无 needs/provides、无 extends、无方法基类/`slot` 字段/类型化 Summary、无新 hash/版本号、无 DI/DAG）。

所有发现都是【琐细】级：一类是迁移残留（死代码、指向旧位置的 docstring/README 行），一类是可选的进一步收紧（见下）。建议在 P1b 的提交里顺手清掉，不构成重审条件。

## 二、主视角三个问题

### 2.1 有无新的过度设计？

**没有。** 逐项看新引入的共享件与机制：

- `RunSettings`（3 个字段的 frozen dataclass）：是 §3 点名要的，替代了 7 处 getattr 死分支，不是额外抽象。
- `composites/shortfall.py`、`composites/skill_status.py`、`benchmarks/aux.py`、`blocks/tasks.tasks_digest`、`core/ids.is_stage3_candidate`：全部是"逐字节相同的副本合一"，且 docstring 明确记录了哪一份刻意不收（stage6 per-arm shortfall 记录形状不同、留在原处）——正是 P0 行"只合并真正等价的重复"的执行。
- `build_manifest` 纯函数：消掉了装配工具的 `_prompt_digests`/`_tasks_digest` 两份副本，方向正确（但留了尾巴，见发现 1）。
- golden 采集器（tests/golden/）：按 §7.1 简化后纪律实现，常驻只有 10 个测试，崩溃点固定 3 分位而非通用遍历框架，测试侧专用，不越界。
- `write_skill(prompt_name=...)`：§4 预定的零风险准备，2 行改动 + 测试，不是投机泛化。

唯一可再简的地方（均【琐细】）：delivery 槽的"resolve 返回审计行 + stage6 条件调 `write_audit`"双入口约定（发现 8），以及 stage1_baseline 下沉后剩下的三个纯转发包装（发现 7）。

### 2.2 有无不适当砍除？run_units_disposed 的取消是否正确？

**没有不当砍除；取消正确，我维持该决定。** 核对三处处置的现状：

- stage3（stage3_generation.py:258-267）：先写终态 JSONL 行 + `terminal` dict，再 `ledger.record_once`，返回字符串标签；
- 4-exam（methods/acceptance/exam.py:156-161）：先 `discard_skill`（store 落盘），再补账本行（崩溃夹缝由下次调用补齐），返回 `(kind, id)` 元组；
- stage2 的处置在 K2 内部（穷尽记录 + skip 结果），形状又是第三种。

三者"先写什么、账本幂等机制、返回形状"各不相同，且**处置顺序本身是各阶段的续跑契约**（stage3 终态行先于账本、exam 丢弃先于账本、K2 有自己的 append-once）。抽象成 `run_units_disposed` 需要参数化"写什么、按什么幂等、返回什么"——回调族雏形，正是用户"避免 over-engineered"裁决禁止的东西，参数化成本确实高于三段各 3 行的重复。方案 §2 已如实记录取舍并写明"评审可推翻"，本评审不推翻。

### 2.3 methods 注册表接口是否最小？（纯 dict + 按需 import vs 方案原文"复用 core/registry.py"）

**裁决：赞同现状，且这是 P1a 约束下唯一正确的实现。** 理由：

1. `core/registry.py` 的装饰器注册在 import 时执行。若 methods 复用它，`methods/__init__` 就必须 import 全部驱动模块——exam 进程会加载 live 模块、触发 live prompt 的懒注册，**冻结 prompt 名集改变，manifest 变**，直接违反 P1a"manifest 不变"的验收标准。纯表 + `importlib.import_module` 按需解析是同时满足"按名分发"和"exam 永不加载 live"的最小机制（4 条目、45 行、零装饰器）。
2. 它比方案原文更简单，方向与用户简洁化裁决一致；理由已如实写进 `methods/__init__.py` 与 `registry.py` 的 docstring。
3. 需要留痕的对齐（【琐细】，建议 P1b 时一并做）：方案 §3 写"注册复用 `core/registry.py`，每槽位一个注册表"、§8 第 1 条写"用 `@method(...)` 注册；在 `methods/__init__.py` 显式 import"，实际实现是 `methods/registry.py` 的 `_ENTRIES` 纯表、无装饰器。这属于"方案字面被实现简化取代"，应在决策文档补一行修订记录，避免 P1b 评审员重新裁一遍同一个问题；同时 §3 要求的"注册表文档写明该槽位签名与允许修改的共享字段"尚未兑现（P1a 的签名是过渡形状，P1b 落 Params 时补，见发现 9）。

`test_layering` 的三项扩展（层号表、相对 import 纳入检查、方法驱动互不 import）与 §2 的要求逐一对应，全部落地。

## 三、发现清单（→分级→file:line→建议）

无【偏移约束】级。以下按重要度排序，全部【琐细】：

1. **【琐细】死代码 + 过时理由**：`src/expedistill/stages/assemble_reuse.py:290-320` — P0.4 删掉了本模块的 `_tasks_digest` 副本改用 `build_manifest`，但其唯一支撑 `_canonical` 被留下，现在只剩自递归调用（AST 核实无外部调用者）；docstring 里 "the tasks digest below" 指向已不存在的函数，理由 "Duplicated here because composites may not import stages" 也已失效（本模块现在就在 stages 层、直接 import 了 `stage0_freeze.build_manifest`）。连带 `enum`/`dataclasses`/`PurePath`/`re` 四个 import 仅死代码使用。建议：删除 `_canonical` 及四个 import，删掉第 48-49 行的理由句。
2. **【琐细】README（设计正本）与实际目录矛盾**：`README.md:178` — 装配工具行仍写 `composites/assemble_reuse.py` 且理由是"放 `composites/` 只因它负责落盘与校验"，但 P0.4 已上移到 `stages/`（AGENTS.md 目录树已同步、README 未同步）。README 与 AGENTS 现在互相矛盾，违反 AGENTS §十一.4"提交前检查：相关文档已同步"。建议：P1b（本来就要大改 README）前先修此行。
3. **【琐细】K6 docstring 指向旧位置**：`src/expedistill/composites/live_acceptance.py:15` — "belong to stages/stage4_live_run.py"，该模块 P1a 已迁至 `methods/acceptance/live_run.py`。建议：改引用。
4. **【琐细】共享常量的 docstring 陈述不实**：`src/expedistill/methods/acceptance/__init__.py:4-7` — 称 `STAGE_LABEL` "shared because both drivers write failed_units.jsonl rows under the same stage identity"。事实：live 驱动写账本用的是 `la.FAILURE_STAGE = "stage4_live"`（§6 运维规则：标签不改），exam 用 `STAGE_LABEL = "stage4_acceptance"`，两者**刻意不同**；`STAGE_LABEL` 只有 exam 一个使用者。建议：改 docstring，或把 `STAGE_LABEL` 下放回 exam.py 私有。
5. **【琐细】时间线表述不准**：`src/expedistill/composites/skill_status.py:8` — "a concern the lazy-register removal retired"：懒注册要到 P1b 才删。今天共享安全的原因是 `skill_status` 不 import 任何 prompt/live 模块（exam 的 import 图核过：methods.acceptance.exam → composites.{acceptance,skill_status}，均不触 live）。建议：措辞改为"共享模块不触及 prompt 注册，且 P1b 全量冻结后此顾虑按构造消失"。
6. **【琐细】assemble_reuse 模块 docstring 的定位句过时**：`src/expedistill/stages/assemble_reuse.py:13-17` — "Why a composite and not a script…（the layer-5 contract）"：模块已在第 7 层 stages/，"layer-5" 说法过时（"必须在 src/ 里受测试"这半句仍成立）。建议：按 stages 层改写一句。
7. **【琐细】下沉后的纯转发包装可删**：`src/expedistill/stages/stage1_baseline.py:98-110` — `accepted_shortfall_path`/`accepted_infra_shortfall`/`write_accepted_infra_shortfall` 三个函数在 stage1 的 shortfall 逻辑下沉后只剩对 `composites/shortfall` 的委托，不再持有任何独有知识（连 stage1 目录常量都在 shortfall 里）；使用者只剩 stage6（同层）与测试，可直接调 `shortfall.stage1_accepted_unit_ids` 等。建议：P1b 顺手删，或在 stage6 改直调；保留也不影响推进。
8. **【琐细】delivery 槽的双入口约定可收敛**：`src/expedistill/stages/stage6_test.py:316-319` + `methods/delivery/retrieval.py:53,97` — "resolve 返回 (kwargs, 审计行)；行非 None 则 stage6 调驱动的 write_audit"是一条隐式契约：新 delivery 方法的作者必须知道"返回审计行就得实现 write_audit"。`resolve` 已拿到 `ctx`（含 run_dir），驱动完全可以自己写审计，槽契约收敛为单入口 `resolve`，stage6 少一个条件分支。不是错误，但比单入口多一层默契。建议：P1b 定稿槽签名时二选一：合并为单入口，或把该约定写进注册表文档（见发现 9）。
9. **【琐细】槽签名文档未兑现（§3 的交付物）**：`src/expedistill/methods/registry.py` — §3 要求"注册表文档写明该槽位签名与该槽位允许修改的共享字段"；当前 registry docstring 只写表结构与报错契约，`methods/delivery/__init__.py` 只有一句话。acceptance 槽是 `module.run(ctx)`，delivery 槽是 `resolve(ctx, bank, test_tasks)` + 可选 `write_audit`，两个约定只散在驱动自己的 docstring 里。P1a 的签名本来就是过渡形状，不要求现在补，但 **P1b 落 Params 时必须带上**，否则"加一个方法 = 一行 + 一个文件"的承诺要靠读 stage 源码才能兑现。建议：列为 P1b 交付项。
10. **【琐细】死缺省**：`src/expedistill/stages/stage6_test.py:317` — `ctx.config.skill_delivery.get("mode", "whole_bank")` 的默认值是死分支：`_validate_skill_delivery` 保证 `mode` 恒在且合法（core/config.py:234-276），与 P0.1 清掉的 `getattr(ctx.config,"workers")` 属同类。建议：改 `["mode"]`。
11. **【琐细】无外部使用者的导出**：`src/expedistill/methods/acceptance/exam.py:66,70` — `__all__` 导出 `MODEL_OUTPUT_DISCARD`，全仓无外部使用者；同类：`live_run.py:140,145` 的 `SHORTFALL_NAME` 再导出只有本驱动测试在用。建议：转私有或保留皆可，记录在案。
12. **【琐细】测试注释指向旧模块**：`tests/composites/test_live_acceptance.py:4`、`tests/composites/test_live_acceptance_resume.py:4,218` — 仍以 "stage4_live_run.py" 指称驱动（现已迁 methods）。`tests/unit/methods/acceptance/*.py` 里的 `stage4_live_run` 是**有注释声明的刻意别名**（"call sites reading as before"），不算问题。
13. **【琐细】目录惯例不一致**：`tests/unit/methods/` 无 `__init__.py`（兄弟目录 stages/blocks/benchmarks 都有；tests/golden 有）。套件可跑（文件名不冲突），建议补齐两个空 `__init__.py` 保持一致。

另记两条**不算发现的提醒**：(a) §7.1 的"每槽位两条廉价契约测试"中"未知参数拒绝"依赖 P1b 的 Params，`get_method` 的未知名 ConfigError 目前经配置层不可达（v2 校验先拒绝），P1b 评审时确认这两条随槽签名一起落；(b) `methods/acceptance/__init__.py` docstring 说常量 "read by the audit tooling"——`scripts/audit/` 用的是同名路径字符串、并不 import 这些常量，表述略强，可顺手改准。

## 四、思想实验（对照 §8 清单）

**(a) 新 prompt 策略的 authoring 变体（结构不变）**
零件已就位：`write_skill(prompt_name=...)` 已落地并有接线测试（tests/unit/blocks/test_author.py:197-222，断言变体 prompt 被用、四段契约仍按 mode 校验）。P1b 之后的增量 = ① `prompts/` 新模块 + `prompts/__init__` 清单一行；② `methods/authoring/<name>.py` 驱动；③ `registry._ENTRIES` 一行。**外加一次性的开槽成本**：stage3 改薄前端 + `methods/authoring/` 包 + v3 配置 `authoring.name` 键——这正是 §4 预定的"第二种写法出现时立即建"，不是 P1a 引入的新障碍。结论：承诺兑现，P1a 未添堵。

**(b) curation=none（P4）**
① `methods/curation/k5.py`（薄驱动，包 `composites.curation.curate_bank`——K5 本体按 R1 留在 composites，崩溃注入测试 patch 目标不变）；② `methods/curation/none.py`；③ 注册表两行；④ stage5 改成 stage4 同款 4 行薄前端；⑤ v3 配置 `curation.name`；⑥ `authoring.exams: false` 的集中冲突校验（§7 P4 行）。各槽之间无共享可变态、无互相 import（test_layering 静态守着），完全隔离成立。

**(c) embedding delivery（P5）**
驱动本身：`methods/delivery/embedding.py` + 注册表一行即可——现接口够用（`resolve(ctx, bank, test_tasks)`：ctx 可取 handle，逐任务解析与审计行形状自由）。一次性基建与 §8 所列一致：① `llm/` 的非 chat embedding 调用（现在只有 chat completions 一条路）；② embedder 角色（P1b 后 `ROLE_DEFAULTS` 是数据，加角色是小改）；③ 插件提供真实查询文本（`benchmarks/base.py` 加可选钩子，`aux_metric` 是现成先例；今天 TB 的 `Task.instruction` 只是任务名）。**前瞻注记（P5 设计题，非本次缺陷）**：`resolve` 每次 stage6 调用都重跑，续跑也重嵌入——要么接受重复调用（golden 续跑准则把"同内容重问"算合法），要么驱动自持久化嵌入缓存（接口不挡路，照 retrieval 写自己的审计/缓存的先例即可）。

**"加一个方法 = 注册表一行 + 一个驱动文件"的兑现度**：对已存在的槽（acceptance、delivery）成立，已用 §8 的机制核对；对新槽需要先开槽（薄前端 + 包 + 配置键），方案本就如此设计。P1b 落 Params/槽签名文档后该承诺才算对"不看 stage 源码的方法作者"成立（见发现 9）。

## 五、可读性抽查（迁移件 docstring/注释）

- `methods/acceptance/exam.py`：docstring 全部指向当前事实（K4/STAGE_LABEL/AGENTS §6），无旧位置残留。通过。
- `methods/acceptance/live_run.py:3-4`："stages/stage4_acceptance.py dispatches here"——正确。通过（但见发现 4：其引用的共享常量文档不实）。
- `methods/delivery/whole_bank.py`、`retrieval.py`：注释解释了"为何重复 stage6 目录常量而非 import"（methods 在 stages 之下），现状准确。通过。
- `stages/assemble_reuse.py`：**三处过时**（发现 1、6）。
- `composites/live_acceptance.py:15`：**一处过时**（发现 3）。
- `README.md:178`：**一处过时**（发现 2）。

## 六、【偏移约束】级清单

无。

## 七、给后续步骤的输入

- P1b 顺手清理：发现 1–7、10–13（半天内的文档与死代码清扫，P1b 本来要动 README 与配置文档）。
- P1b 交付项（本评审转交）：① 槽签名 + 允许修改的共享字段写进注册表文档（§3、发现 9）；② §3/§8 的"注册机制"文字按纯表实现补一行修订记录；③ 每槽两条契约测试（假模型跑通 + 未知参数拒绝）随 Params 落地；④ 决定 delivery 槽单/双入口（发现 8）。
