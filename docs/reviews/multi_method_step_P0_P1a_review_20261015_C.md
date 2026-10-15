日期:2026-10-15
一句话说明:P0+P1a 合并评审之三(评审 C:迁移风险与可复现性视角)——静态审阅 `git diff bcb3ffe..297629f`(f1b236a、08c7985、94eded0、edbe66e、da8a686、297629f,连同范围内的 1c7c307)并核对验收证据主张(golden 十测试零 diff、每提交全绿 1901、P1a manifest 不变);结论:代码迁移逐字节等价、续跑契约与审计工具链完整无损,验收主张可信;发现 1 条【偏移约束】级流程问题(P0 三评审未先于 P1a 落地且留痕断裂)与若干【琐细】。
对应 log.md 条目:"2026-10-15 17:50"

---

## 一、总体评价

**通过(附 1 条【偏移约束】级流程修复要求,代码本身无需返工)。**

范围与基准:方案正本 `docs/decisions/multi_method_framework_20261015.md`(§0/§2/§6/§7/§7.1/§10)与本步验收标准(manifest 不变、golden 无 diff、测试全绿)。全程静态阅读,未跑测试、未触碰 runs/ 与 TB-dev。

核心判断:

1. **迁移是"逐字节等价替换 + 模块搬家",未发现行为等价破口**。P0.1 的 `RunSettings` 类型化(旧 `_workers`/`_k1_stagger`/`_allow_infra_shortfall` 的 getattr 链 → `ctx.run_settings.*`,死分支 `getattr(ctx.config,"workers")` 已由方案 §1③ A 核查为不存在字段)、P0.2 的 `resolve_plugin` 统一(import-then-get 语义保留,`importlib.import_module` 动作未丢)、P0.3 的四个下沉件(`composites/shortfall.py`、`composites/skill_status.py`、`benchmarks/aux.py`、`blocks/tasks.py` 的 `tasks_digest`/`_canonical`)、P0.4 的 `build_manifest` 纯函数与 `assemble_reuse` 上移 stages、P1a 的方法驱动层——我逐行比对了旧实现与新实现,落盘格式(含 `accepted_infra_shortfall.json` 的 `{"unit_ids": sorted(...)}` + `indent=2`)、枚举分支(mode 由 config 校验补全)、记账形状(`whole_bank` 保 `skills_injected` 标量、retrieval 审计写路径)全部等价。
2. **续跑与账本契约全部未变**(逐项核对,详见第三节):failed_units 阶段标签、pipeline_log 阶段名、run 目录布局、audit 脚本的 import 面与路径常量、cli 两个子命令的模块引用。
3. **验收主张可信**:golden fixtures 只在 a6ec16b(P-1′)落盘,评审范围内 6 个代码提交均未触碰 `tests/fixtures/golden/`——"golden 零 diff"与提交历史一致;归一化(harness.py `_VOLATILE_KEYS` + run 目录路径替换 + pipeline_log 末行视图 + 请求史多重集)声明与实现一致;测试计数链自洽(1899 → P0.2 +1 → P0.3 +2−2 → P1a +1(test_layering 新增)= 1901,与 log.md 各条目声称一一对应)。静态评审不能替代实际运行,但无任何反证。
4. **流程纪律有一处实质偏离**:`docs/reviews/` 中不存在 `multi_method_step_P0_review_20261015_{A,B,C}.md`,而 P1a(commit 297629f,14:13)早于 log.md 16:55 条目所声称的"P0 三评审留档";即 §10"通过后才进入下一步"与"留痕"两条在 P0→P1a 之间未被执行,实际以本次 P0+P1a 合并评审代替。本合并评审恰好覆盖同一完整 diff 范围,实质审查不缺失,但留痕必须补记(见第四节第 1 条)。

## 二、验收主张可信度(golden 能证明什么、盲区由什么兜底)

**能证明**(P0/P1a 的目标恰是"纯重构",仪器对口):

- 8 组合全流水线在假模型下跑通,run 目录**全部落盘文件**(manifest、各 stage 摘要、对象库、账本、bank、injection_audit、llm 请求转储)作为快照键逐字节比对——布局、产物内容、manifest 一起 pin;
- 2 组合崩溃续跑的等价准则精确化为"结果状态全同 + 调用史多重集只容忍崩溃痕迹与同内容重问"(`assert_resume_equivalent`),是 README 已知续跑语义的可执行形式;
- pipeline_log 的 stage 键名、`retrieval_audit.jsonl`、stage 摘要的累计字段都在快照内。

**盲区与兜底**(均可接受):

| 盲区 | golden 为何不覆盖 | 兜底 |
|---|---|---|
| 错误路径(ModelOutputError 处置、infra 失败账本、ConfigError/CodeBugError) | 假模型脚本永远产出有效输出,`SimulatedCrash` 是绕过一切处置逻辑的裸 RuntimeError | `tests/unit/methods/acceptance/test_exam.py`(workers4 infra 隔离、resume)、`test_live_run.py`(shortfall/infra)、`test_stage3.py`(model_output_error 隔离)——全部随驱动迁移到新路径且保留 |
| exam/live 懒加载(exam 进程误 import live 模块,golden 不红:manifest 在 stage0 已先冻结,live prompt 注册不进任何后续产物) | manifest 冻结时序在 golden 之前 | `test_exam.py:1031` `test_an_exam_run_never_imports_the_live_modules`——**子进程级 sys.modules 断言**,路径已同步到 `expedistill.methods.acceptance.live_run`;外加 `test_layering` 新增"方法驱动互不 import" |
| 模块重载/patch 目标有效性("断言没发生"类测试空转变绿) | golden 不 patch 任何模块 | 分发测试成对存在:`test_live_run_config_dispatches_to_the_live_driver`(正向:patch 被调用)+ `test_exam_configs_run_the_exam_path_not_the_live_driver`(负向:calls 为空)——patch 打空会被正向测试暴露;`test_an_exam_run…` 的 in-process 控制(pop 后重跑再 re-import)也保留 |
| 真实远程 TB / 真实端点 | 设计取舍,方案 §7.1 如实披露 | 在跑正式 run 与 live 冒烟标记 |

## 三、续跑与账本核对结果(P1a 后全部不变)

- **failed_units 阶段标签**:exam = `"stage4_acceptance"`(`methods/acceptance/__init__.py` STAGE_LABEL,与旧 stage4_acceptance.py 同值)、live = `"stage4_live"`(`composites/live_acceptance.py:155` la.FAILURE_STAGE)、stage1/3/6 标签未动。
- **pipeline_log 阶段名**:`pipeline.py:389-412` record 的七个 stage 名全部未变;cli `summary` 命令读 pipeline_log + usage,不依赖模块路径,可用。
- **run 目录布局**:`stage4_acceptance/`、`stage4_summary.json`、`stage4_acceptance/live_run/roundNN/`(`LIVE_DIR_NAME` 未变)、workdir `stage4_live/roundNN`、`stage6_test/retrieval_audit.jsonl`、`stage1_baseline/accepted_infra_shortfall.json`——全部未变(golden 快照键同时 pin 住)。
- **`scripts/audit/audit_live_run.py`**:评审重点追查——它**并不 import** `composites.live_acceptance` 或任何驱动模块,只 import `expedistill.core.ids` / `core.models` / `infra.store` / `inject.skills`(第 81-84 行),审计逻辑自包含;`LIVE_SUBTREE = Path("stage4_acceptance")/"live_run"`(第 86 行)与 `la.LIVE_DIR_NAME` 一致;manifest 读取路径(第 612 行 `["config"]["acceptance"]["n_attempts"]`)在 v2 下不变。**驱动搬家对它零影响。**
- **cli**:`assemble-reuse` 子命令的 import 已更新到 `stages.assemble_reuse`(cli.py:59);`tests/unit/test_cli.py` 的端到端冒烟引用路径同步。
- **scripts/ 其余引用**(`fork_run_stage04.py`、`smoke_leak_audit_k4.py`)仅为 run 目录路径字符串,不受影响。

## 四、风险清单(分级 → 位置 → 缓解)

### 【偏移约束】

1. **§10 每步三评审的顺序与留痕在 P0→P1a 之间未被执行**。log.md 16:55 条目声称"意见留档 docs/reviews/multi_method_step_P0_review_20261015_{A,B,C}.md,通过后进 P1a",但该文件不存在;P1a 代码于 14:13(commit 297629f)已落地,早于该条目。实际执行为 P0+P1a 合并评审(本文与 A、B 两份)。
   - 依据:方案 §10 第 1 条(通过后才进入下一步)、第 5 条(留痕);git log 提交时间 vs log.md 条目时间。
   - 缓解(修复要求):按 AGENTS 第二节"写错了另追加一条更正"——log.md 追加一条,声明 P0 评审并入本次 P0+P1a 合并评审并指向本目录三份文件,更正 16:55 条目的错误指向;如合并系用户裁决,同步补记决策文档 §10 或 §7.1。代码无需返工(本合并评审覆盖完整范围 bcb3ffe..297629f,实质审查不缺失)。

### 【琐细】

1. `src/expedistill/composites/live_acceptance.py:15` docstring 仍指向 `stages/stage4_live_run.py`(已迁至 `methods/acceptance/live_run.py`);README/AGENTS 都已改,此一处漏网。缓解:一行 docstring 更新。
2. `tests/unit/methods/` 与 `tests/unit/methods/acceptance/` 缺 `__init__.py`(其余全部 tests 子目录均有)。当前 pytest 可正常收集(模块名唯一),但破坏布局惯例、留同名冲突隐患。缓解:补两个空 `__init__.py`。
3. 布局常量多处声明:`shortfall.py:29` STAGE1_DIR、`live_acceptance.py:318` 硬编码 `"stage1_baseline"/"accepted_infra_shortfall.json"`、`retrieval.py:29` `_STAGE6_DIR`、`methods/acceptance/__init__.py` STAGE4_DIR vs `live_acceptance.py:125` LIVE_DIR_NAME(内嵌 `"stage4_acceptance/"`)。方案 §6"布局永久不迁移"使风险可接受,golden 快照键与 audit 脚本是外部守卫;无需动作。
4. 错误消息文本统一化(P0.2 resolve_plugin、P0.3 `read_unit_ids` 多出 `: {unit_ids!r}` 尾巴):三处原本各自为政的消息合并为一份,文本与旧版不完全一致(`test_tasks.py:148` 断言已同步)。错误类型与触发条件不变、非产物,可接受。
5. `tests/golden/harness.py:189` `installed_plugin` 退出时 `_reset_registry()` 清空全部插件注册——套件内潜在的顺序耦合;当前全绿说明无实际问题,留意即可。

### 【赞同】

1. P0.1 frozen `StageContext` + `RunSettings`(删 7 处死分支副本);`pipeline._check_run_settings` 先于 ctx 构造校验。
2. P0.2 `resolve_plugin` 统一(消 5 处重复),import-then-get 语义与 ConfigError 分类保留。
3. P0.3 下沉件逐字节等价(`shortfall`/`skill_status`/`aux_wiring`/`tasks_digest`/`is_stage3_candidate`),含 `write_unit_ids` 的落盘格式与旧三处完全一致;aux_wiring 的两条测试随迁 `tests/unit/benchmarks/test_aux.py`,纯续跑重聚合的插件 import 前提仍被测住。
4. P0.4 `build_manifest` 纯函数;`assemble_reuse` 上移后 `_prompt_digests` 副本删除,live 分支语义由 stage0 版保留——两个 manifest 构造点合一,恰是 R3 所述单侧缺键风险的消除。
5. P1a 注册表纯数据表 + 按需 import(`methods/registry.py`);exam 进程隔离有子进程级 sys.modules 断言;`get_method` 的未知名字 ConfigError 与具名规则同契约(v2 下 mode 枚举被 config 校验限定,错误路径不可达,等价成立)。
6. stage6 分发等价:`skill_delivery` 的 mode 由 `_validate_skill_delivery` 补全(`config.py:234`),`get_method("delivery", …)` 的输入域与旧 if 分支完全一致;`whole_bank` 保住 `skills_injected` 标量记账,retrieval 的审计写入路径与原子写实现逐行未变。
7. patch 目标随迁移同步且带正向前提;`stage4_acceptance` 前端只剩 24 行,`STAGE_LABEL`/`STAGE4_DIR`/`SUMMARY_FILENAME` 落在 `methods/acceptance/__init__.py`,无转发 shim,旧符号引用全仓库零残留(已 grep 核实)。
8. 测试计数链自洽(1899→1901);`test_layering` 三项扩展(层号、相对 import 解析、方法互不 import)与方案 §2 一一对应。
9. `run_units_disposed` 取消:同意。三处形状各异(字符串标签 vs 元组),处置顺序本就是各阶段续跑契约,抽象参数化成本高于收益(方案 §2 已记录,评审可推翻——本评审不推翻)。

## 五、P1b 前置风险清单(schema 切换会动 manifest/golden,必须先解决)

1. **【验收工具缺口】"除 manifest 外产物与 golden 一致"目前无机器化比对手段**:harness 只有全量 dict 相等(`test_golden_matrix.py:47-52`),P1b 重生成后新旧 golden 无法自动验证这条验收。→ P1b 动手前给 harness 加"忽略 manifest 键"的比对模式(或一次性脚本),口径预注册后再用。
2. **golden 配置形状同步**:`harness.write_config` 现写 v2 键(`acceptance`/`skill_delivery`),须与 schema 切换同提交改写 v3;8 组合的 v2→v3 语义映射逐一人工核对。
3. **`upgrade_v2` 的测试前件**:三种 v2 acceptance 写法(缺键、`{}`、`{mode: exam}`)同值映射,以及 golden 覆盖全部 v2 配置——方案 R3 已设计,P1b 落实;它服务的两处(assemble_reuse 读 v2 源 manifest 做身份比对、一次性配置转换)都要有实测路径。
4. **prompt 全量冻结的 manifest 变化**:懒注册删除后,exam run 的 manifest 将新增 LIVE_RUN_ATTRIBUTION/LIVE_SKILL_REVISION 的 digest——须断言新 digest 值等于 v2 live run 中同名字的值(digest 计算未变);`_MANIFEST_OPTIONAL_FIELDS` 删除对历史 None-digest 的行为要有测试。
5. **audit 脚本同提交改**:`audit_live_run.py:612` 读 `["config"]["acceptance"]["n_attempts"]`,v3 下路径变化(方案 P1b 行已列"按 manifest 判断模式、live 子树缺失报 ERROR");连带核对 `scripts/run/`、`scripts/analysis/` 下所有读 manifest 的路径。
6. **assemble_reuse 身份视图白名单**:允许 diff 的键含 `skill_delivery`(README 装配工具行),v3 改 `methods.delivery` 后键名与 upgrade_v2 映射同步。
7. **测试夹具预告**:`test_stage0.py:133/719/744` 将失败(方案已预告),提前定好改法;`ROLE_DEFAULTS` 改名牵动 `core/ids.py`(现 import MODEL_ROLES)与 `n_attempts ≤ n_attempts_weak` 的 check 落点。
8. **快照纪律执行**:golden 重生成同提交、`provenance.json` base_commit 更新、提交说明列 diff 摘要;`generate.py` 生成时的崩溃续跑 abort 语义在 v3 下全量重验(生成时自动执行)。
9. **配置文件纪律**:`configs/experiments/` 全部 v2 配置升级结果写**新文件名**(不改旧文件,AGENTS 六.2);逐份 v3 manifest 人工审定工作量大,先出 diff 清单再逐份过。
10. **流程纪律**:P1b 完成后的三评审必须先于 P4 落地并留档,修复第四节第 1 条暴露的流程缺口,避免同类偏离重演。

## 六、【偏移约束】级清单

1. 见第四节第 1 条(P0→P1a 评审顺序与留痕;修复 = log.md 补记更正 + 指向本合并评审,若系用户裁决则同步方案文档)。

其余无。
