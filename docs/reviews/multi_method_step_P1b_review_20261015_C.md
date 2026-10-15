日期：2026-10-15
一句话说明：P1b（schema v3 唯一一次切换）三路独立评审之评审 C（迁移风险与可复现性视角）——静态审阅 `git diff 297629f..78db71f`（4949b5d 后的 00971f2 预注册 + 78db71f 切换），并以本人在 P0+P1a 评审第五节留的 10 条前置清单为底单逐条裁决；结论：schema 切换的行为等价性有强机器证据（golden 逐键 diff 仅 manifest、live digest 跨版逐字符相等、24 崩溃变体与调用数全等），底单 8 条落实、2 条部分落实；发现 1 条【偏移约束】（方案 §7 P1b 验收行"全部 v2 配置升级后的 v3 manifest 人工审定一次"无执行留痕）。
对应 log.md 条目："2026-10-15 19:30"

---

## 一、总体评价

**有条件通过：修复 1 条【偏移约束】（补记逐份 v3 配置人工审定）后即可进入 P4；代码本身无需返工。**

范围与基准：方案正本 §6/§7/§7.1/§10，本步验收标准，及本人在 `docs/reviews/multi_method_step_P0_P1a_review_20261015_C.md` 第五节留的 10 条前置清单。全程静态阅读 + 只读 git/脚本比对（比对脚本只写在 /tmp，未跑测试、未触碰 runs/ 与 TB-dev）。

核心判断：

1. **切换的行为等价性不是靠声明，而是被仓库内的机器证据蕴含**。我逐键比对了 00971f2（切换前）与 78db71f（切换后）的 9 份 golden 文件：8 组合每个快照文件内**只有 `manifest.json` 键变化**，provenance 仅 base_commit 变化；manifest 内部的叶子级 diff 全集（8 组合并）恰好是 `config.methods` 替换七个 v2 顶层键、`config.prompts_digests` 与顶层 `prompt_digests` 各增两个 live digest、`schema_version` 2→3——**无一意外键**。由于新 golden 是 v3 代码下新鲜跑出的快照、旧 golden 是 v2 代码的快照，"仅 manifest 键不同"直接蕴含预注册准则"除 manifest 外逐字节一致 8/8 成立"，即使那一瞬间的验证脚本没有留档（`tmp/golden_check3/` 留有 00971f2 版旧 golden 的逐字节副本，是切换验证的材料痕迹）。
2. **迁移映射本身质量高**：`upgrade_v2_config` 纯函数（深拷贝进出，输入不被改有专测）、三种 v2 exam 写法同值映射、live 顶层 retry_limit 丢弃作为身份变更在函数 docstring 与转换器输出双重披露；`METHOD_DEFAULTS` 与 v2 顶层默认逐项相等（K=5/segment_retry 2/retry 2/budget 4096/rounds 3），补全语义使 v2 装配的 A15 保守角案例按设计消失并改为断言新行为（测试 docstring 写明缘由）。
3. **续跑与可复现性专项无破绽**（详见第三节）：给定槽位逐字冻结、解析幂等、v2 run 的三处闸门（load_config 拒 v2 文件、stage0 拒 v2 run 目录并指维护分支、audit 拒 v2 run 并指维护分支）全部响亮。
4. **流程纪律已修复上轮缺口且本轮无重演**：P4 零提交（HEAD == 78db71f），预注册（00971f2，14:36）先于切换（78db71f，15:23），上轮的评审留痕断裂已由 log 18:30 更正条目 + 4949b5d 落档。
5. **唯一实质缺口是验收留痕**：方案 §7 P1b 行的三项验收中，"除 manifest 外产物与 golden 一致"（机器证实）与"prompt 名集与 digest 等于 v2 golden"（机器证实，见下）都有据，唯独"**configs/experiments/ 全部 v2 配置升级后的 v3 manifest 人工审定一次**"在 log.md、提交说明、docs/ 下均无执行留痕；7 条验收主张清单本身也不含此项。这与上轮 P0 评审"声称留档但文件不存在"是同一性质的问题（方案 §10.3 审核基准 + §10.5 留痕），按判例定【偏移约束】。

## 二、底单 10 条逐条裁决

| # | 底单条目（P0+P1a 评审第五节） | 裁决 | 证据 |
|---|---|---|---|
| 1 | "忽略 manifest 键"比对模式，口径预注册后再用 | **落实** | `tests/golden/harness.py:505-521` `assert_equal_ignoring` 在 00971f2（14:36）先行提交，早于切换 78db71f（15:23）；`test_golden_matrix.py:67-82` 自带防藏漂移测试（篡改 manifest 后 ignore 才通过、不 ignore 必红）；`tmp/golden_check3/` 是旧 golden 的逐字节副本（切换验证材料）；结论由 golden diff 机器蕴含（见总体评价第 1 条） |
| 2 | harness.write_config 同提交改写 v3；8 组合 v2→v3 语义映射逐一核对 | **落实** | `harness.py:360-390` 同提交改 v3（K→segmentation.K=1、acceptance.mode→name、skill_delivery→delivery、split 保留顶层）；8/8 golden manifest 的 methods 形状经我逐键机器验证：exam={name,retry_limit:2}、curation={token_budget:4096,rounds:3}、delivery=retrieval 三参/whole_bank、segmentation={K:1}，与 v2 默认逐项一致 |
| 3 | upgrade_v2 三种写法同值映射 + 两处服务点都有实测路径 | **部分落实** | 映射测试充分：`TestUpgradeV2` 6 用例 + `TestUpgradeCoversEveryV2Config` 遍历仓库全部 v2 配置（test_config.py:1024-1134）；**配置转换侧实测 ✓；装配侧无实测**——`assemble_reuse.py:1032-1039` 的 v2 源升级分支在全 tests/ 无任何 schema_version=2 源 run 夹具（grep 全库为零命中）。修复见风险清单 R2 |
| 4 | 新增 live digest 须等于 v2 live run 同名值；_MANIFEST_OPTIONAL_FIELDS 删除行为有测试 | **落实** | 机器验证：新 exam golden 的 LIVE_RUN_ATTRIBUTION/LIVE_SKILL_REVISION 与旧 live_run golden 同名 digest **逐字符相等**（9ff5b74d…、2a20ea8b…）；`test_stage0.py` golden literal 同步且断言反转（719-744 行区域）；to_dict 无 drop 由 `test_to_dict_covers_every_field` 钉住（config.py:488-497 注释明确 v2 acceptance 特例已死）；历史 run 续跑由维护分支机制兜底（v3 主干不承诺可续跑 v2 run） |
| 5 | audit_live_run 同提交改（按 manifest 判模式、live 缺子树 ERROR）+ 连带核对 scripts/ 读 manifest 路径 | **落实** | `audit_live_run.py:606-640`：按 `config.methods.acceptance.name` 判模式；live run 缺子树→ERROR；**新增** exam 却有 live 子树→ERROR（超出底单要求）；读不出模式（含 v2 run）→ERROR 并指维护分支——**v2 run 审计从静默 INFO 变为响亮报错**，正是底单要求；n_attempts 走 `config.methods.acceptance.n_attempts`。连带核对：smoke 两脚本已 v3 化（smoke_tb_distill_chain.py:68-70、smoke_stage01_alfworld.py:77）；seed_tb_stage1 只读 v3 不变的顶层键；analysis/* 读外部 TSV 与 run manifest 无关；fork_run_stage04 是 v2 维护分支历史工具（v3 下其白名单失配即响亮拒绝，属配套老 run 的旧代码，可接受）。残留：`build_skillopt_split_configs.py` 未同步（风险清单 R3） |
| 6 | 装配身份视图白名单 skill_delivery → methods.delivery | **落实** | `assemble_reuse.py:118-128` `_ALLOWED_DIFF_PREFIXES` 含 `config.methods.delivery`（任意深度叶子）；正反向测试齐（test_assemble_reuse.py:574-586 断言 `config.methods.delivery`/`config.methods.segmentation.K`、631-651 缺 delivery 槽的 pre-slot 源仍可装配）。残留：ASSEMBLED_FROM 写入文本与 docstring 仍写旧拼写（风险清单 R4） |
| 7 | test_stage0.py:133/719/744 夹具预告 + ROLE_DEFAULTS 改名牵动 | **落实** | 夹具按预告修正（test_stage0.py:132-135 K→methods、`_golden_config` v3 literal、exam/live 断言反转）；`n_attempts ≤ n_attempts_weak` 落点 = `check_live_run`（methods/acceptance/__init__.py:75-89，resolve_params 时检查），有直接测试（test_params.py:145-152）与 stage0 规则名检查迁移（`test_live_run_unknown_improve_rule_stops_stage0` 改 n_attempts=2 以穿过约束、保留规则检查语义）。更正本人底单笔误：core/ids.py import 的是 **core.models.MODEL_ROLES**（"strong"/"weak"，轨迹角色），与 config 侧 10-role 表重名消除无关，**不需动、也确实没动**——方案 R5 的改名方向正确执行 |
| 8 | 快照纪律：golden 同提交重生成、provenance 更新、提交说明列 diff 摘要、24 崩溃变体 v3 下重验 | **落实** | 9 份 golden 与代码同在 78db71f；提交说明列明 diff 摘要（"manifest alone + the two live digests, equal to the v2 live runs' values"）；provenance.json base_commit → 00971f2（生成时 HEAD = 切换提交之父，golden 本身属于切换提交，鸡生蛋约束下的诚实记法，且方案 §7.1 明言 provenance 是信息记录非纪律）；generate.py 内建每组合 3 个崩溃点 `assert_resume_equivalent`、失败即 abort（generate.py:72-89），provenance 记录 8×3=24 个 crash_points，golden 落地即传递性证据；**model_calls 与 v2 版全等（34/18/28/15 各两遍）是行为等价的额外旁证** |
| 9 | 配置文件纪律：升级结果写新文件名；先出 diff 清单再逐份过（人工审定） | **部分落实** | 7 份 `*_v3.yaml` 新文件 ✓ 不覆盖 v2 ✓；3 份 v1 时代配置（pilot_alfworld/pilot_searchqa/tb21_full，均带 model_options.max_tokens，v2 自身规则即非法）被逐文件拒，与转换器逻辑及 `TestUpgradeCoversEveryV2Config.PRE_V2_MODELS_OPTIONS` 一致；转换器逐文件打印 moved/dropped 摘要（diff 清单机制 ✓）；我抽验 tb21_split_v2 与 tb21_split_live_run 两份的映射语义逐项正确。**但"逐份 v3 manifest 人工审定"（方案 §7 验收行原文）无执行留痕**——见【偏移约束】 |
| 10 | P1b 三评审先于 P4 落地并留档，修复上轮流程缺口 | **落实** | `git log 78db71f..HEAD` 为空——P4 零提交无抢跑；上轮缺口已由 log 18:30 更正条目 + 4949b5d（"record the P0+P1a review verdict and apply its follow-ups"）修复；本轮评审 B 已落档 docs/reviews/，本文为 C，A 应同步落档 |

## 三、续跑与可复现性专项（主视角核对结果）

1. **v3 manifest 在 v3 代码下的续跑幂等性：无破绽**。给定槽位逐字冻结——`_validate_methods` 对非空槽位 `completed[slot] = dict(given)`，无按键合并（config.py:193-201，方案"防默认参数串槽"落实）；解析幂等——`resolve_params` 在 `dict(raw)` 拷贝上 pop name、不污染 config（registry.py:120-127），返回 frozen dataclass；`to_dict` 全字段原样（v3 无 optional 字段）；续跑比对两侧（load_config 补全 vs 冻结值）同经 `_validate_methods`，补全确定性由 `test_defaults_satisfy_every_contract` + test_config 多处 `== METHOD_DEFAULTS` 断言 pin 住（METHOD_DEFAULTS 补全时 dict 拷贝，嵌套值全标量，无共享突变面）。
2. **装配工具对 v2 源 run 的身份走查：语义对齐正确，唯一坑是无测试**。upgrade(v2 manifest config) 显式构造全五槽，形状与 prospective 端经 `_validate_methods` 补全的形状一致；v2 exam 缺键（v2 to_dict drop None）→ upgrade 为 `{name: exam, retry_limit: <v2 顶层值>}`，v2 默认 2 时与 METHOD_DEFAULTS 相等；prompt digest 差异走"声明披露不拒绝"（`_EXCLUDED_DIFF_KEYS`）；v1 源（无 schema_version 键）→ get 默认 3 → 不升级 → 走查 diff 巨大（config.K 与 config.methods.* 都不在白名单）→ 响亮拒绝，安全。`weak_profile_file` 边界：upgrade_v2_config 不解析该文件键，含它的 v2 配置会被 from_dict 的 unknown-key 检查拒——仓库 10 份 v2 配置全部内联 weak_profile（7 份转换成功即证），实证无影响、失败也是响亮。
3. **审计脚本对 v2 run：行为正确**。v2 manifest 无 config.methods → KeyError → ERROR "cannot read the acceptance mode … a schema-v2 run must be audited on its own maintenance branch"（audit_live_run.py:615-621）——不再像 v2 代码那样对 exam run 静默 INFO。**正是底单要求的行为。**
4. **1912 全绿（主张 7）**：静态不可证，但计数链自洽——静态可数收集数 1641（P1a）→ 1652（P1b），增量 +11 恰等于 1912−1901；两版动态 parametrize 集合完全相同（15 处，逐一比对相等），故增量全部来自可静态枚举的新测试（test_freeze +3、test_params +24、golden +1、test_config −14 及其 parametrize 变化），无矛盾。无任何反证。
5. **audit 自检（主张 5）与 stage0 v2 提示（主张 6）**：`--self-test` 的 fake run 与全部检查逻辑已同步 v3（audit_live_run.py:763-771），"自检通过"无法静态证实但结构自洽；stage0 v2 提示代码落地（stage0_freeze.py:277-303，位于 resolve_params 之后、freeze 比对之前，真正的续跑拒绝防线仍是 manifest 逐字段比对），**但无测试钉住**（风险清单 R5）。

## 四、验收主张 7 条裁决

| # | 主张 | 裁决 |
|---|---|---|
| 1 | 预注册准则"除 manifest 外逐字节一致" | **可信**（00971f2 先行 + 防藏漂移自测 + tmp/golden_check3 材料 + golden diff 机器蕴含结论） |
| 2 | golden 重生成 diff 仅 manifest | **机器证实**（8/8 仅 manifest.json 键、provenance 仅 base_commit；叶子级 diff 全集无一意外键） |
| 3 | 24 崩溃续跑变体 v3 下重验 | **可信**（generate.py abort 语义 + provenance crash_points 8×3 + model_calls 与 v2 全等） |
| 4 | upgrade_v2 三使用者齐备 | **代码齐备、测试有缺口**（config.py 定义 + convert_v2_configs.py + assemble_reuse.py:1032-1039 + 测试；装配侧升级分支无专属测试，见 R2） |
| 5 | audit_live_run 按 manifest 判模式 + live 缺子树 ERROR + 自检 | **可信**（三项落地 + 额外身份一致性 ERROR；自检为外挂工具的自验证机制，结构已 v3 化） |
| 6 | v2 run 目录 stage0 维护分支提示 | **落地，无测试钉住**（R5） |
| 7 | 1912 测试全绿 | **静态不可证、计数链自洽无反证**（1901+11=1912 精确吻合，动态部分两版同集） |

## 五、风险清单（分级 → 位置 → 缓解）

### 【偏移约束】

1. **方案 §7 P1b 验收行"configs/experiments/ 全部 v2 配置升级后的 v3 manifest 人工审定一次"无执行留痕**。
   - 依据：方案 §7 P1b 行验收列、§7.1 第 2 条（"P1b 另做一次性认真检查"）；log.md 19:30 条目只记转换动作（"一次性转换 7 份新文件、3 份……逐文件拒因留档"），逐份审定的执行与结果在 log/提交说明/docs/ 下均无记录；本次 7 条验收主张清单亦不含此项。与上轮 P0 评审留痕断裂同性质（§10.3 审核基准 + §10.5 留痕）。
   - 缓解（修复要求）：逐份核对 7 份 `*_v3.yaml` 对其 v2 源的映射（方法参数逐键、非方法键逐键），核对结果落一份简短审定记录（docs/reviews/ 或 docs/reports/ 下一个清单文件即可，逐份一行：源文件 → 目标文件 → 核对结论），log.md 追加一条指向它；被拒 3 份的拒因如有更多细节一并记入。修复后对本条重审即可，代码无需返工（我抽验的 2 份语义全部正确，预计 7 份都会通过——这更说明补记的成本远低于其证据价值）。

### 【琐细】

1. R2 `src/expedistill/stages/assemble_reuse.py:1032-1039`：v2 源升级分支无专属测试。缓解：给 test_assemble_reuse.py 加一个 schema_version=2 源 manifest 的用例（正例：升级后装配通过；负例：升级后 K 漂移仍拒绝）。
2. R3 `scripts/run/build_skillopt_split_configs.py:185-196`：仍生成 v2 形状配置（顶层 skill_delivery、无 schema_version），v3 下其产物会被 load_config 拒（响亮，不静默）——"同步 scripts/"漏了它。缓解：模板改 v3 或脚本头加"生成物需经 convert_v2_configs.py"注释。
3. R4 `src/expedistill/stages/assemble_reuse.py:35,638,758,854`：docstring 与 ASSEMBLED_FROM.md 写入文本仍用旧白名单拼写（"skill_delivery"）；行为正确（`_path_allowed` 用新前缀，ASSEMBLED_FROM 的"实际发生的允许差异"行用真实计算值），但操作员读到的规则声明与实际不符。缓解：文本更新为 methods.delivery 槽。
4. R5 `src/expedistill/stages/stage0_freeze.py:277-303`：v2 run 维护分支提示无测试钉住（它是 UX 增强，真防线是 manifest 逐字段比对）。缓解：一个写 v2 manifest 再跑 stage0 的单测。
5. R6 `scripts/run/convert_v2_configs.py:55-58`：拒载文件只打印拒因不落档，"逐文件拒因留档"目前仅 log 一句话；与【偏移约束】第 1 条同组修复即可。
6. R7 `tests/unit/methods/test_params.py:194-199`：`test_defaults_satisfy_every_contract` 遍历四槽位不含 acceptance（exam 默认值无 pin；ExamParams 默认接受 2，实际风险≈0）。
7. R8 手写 v3 YAML 若漏写 `schema_version: 3`，load_config 缺省按 v2 拒并提示"convert it"（config.py:626）——报错响亮但提示文案对"忘写版本号的 v3 手写文件"略有误导。缓解：文案加一句"或补写 schema_version: 3"。

### 【赞同】

1. 预注册纪律的执行方式（先提交口径与自测、后提交切换）是外层 AGENTS"先注册再看结果"在重构语境下的正确形态。
2. 三处行为语义披露诚实且三处可见（代码注释 + 提交说明 + 测试 docstring）：A15 保守角案例消失改断言新行为、live 顶层 retry_limit 丢弃、全量冻结的 manifest 噪声。
3. 懒加载约束的新形态：`resolve_params` 只 import 轻量 params 包、永不 import 驱动（registry.py:100-104），`resolve` 才按需 import——exam 进程隔离不变量以更精确的方式保持，且 `_validate_methods`（core 层）与 Params 契约（methods 层）的职责切分干净（core 只管形状，方案 §5 落实）。
4. v2 的三处闸门全部响亮且指向正确（load_config → 转换器；stage0 → 维护分支；audit → 维护分支），v1 源/旧工具失配场景亦全部响亮拒绝，未发现任何静默错配路径。
5. audit 新增"manifest 冻结 exam 却存在 live 子树"的身份一致性 ERROR，超出底单要求。
6. golden 的 model_calls 与 v2 全等、provenance 保留 24 崩溃点记录——等价性证据链比提交说明声称的更完整。
7. `upgrade_v2_config` 的纯度测试（输入 mapping 不被改、深拷贝语义）与其"两个使用者共享一个映射"的防漂移设计。

## 六、【偏移约束】级清单

1. 见第五节第 1 条：方案 §7 P1b 验收行"全部 v2 配置升级后的 v3 manifest 人工审定一次"无执行留痕；修复 = 补做逐份核对并落档 + log 追加条目，然后对该条重审。

其余无。
