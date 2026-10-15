日期：2026-10-15
一句话说明：P1b（schema v3 一次切换）的评审 B 意见——扩展性与反过度设计视角，审 `git diff 297629f..78db71f`，结论：通过，无【偏移约束】级发现，5 条【琐细】建议择机处理。
对应 log.md 条目：2026-10-15 19:30（P1b：schema v3 一次切换）。

---

## 一、总体评价

**通过。** P1b 是这套方案里唯一一次"有意改行为"的步骤，从反过度设计的角度看，它最难的不是"加了多少机制"，而是"有没有忍住不加"。核对结果是：该建的都建了、建的都是最小形态、§9 禁止清单里没有一样东西被偷偷带进来。核心判断三条：

1. **新机制三件套（params.py 构建器、注册表四元组、五个槽位包）每一件都有被它替掉的更啰嗦的旧物**。params.py 的 66 行替掉的是 v2 时代写死在 core.config 里、每加一个方法就要再抄一份的手写校验；注册表四元组是方案 §3 P1a 注记逐字预言的形态（"P1b 的 Params/check 挂在表项上扩展（模块路径， Params, check）元组，不要改回装饰器形式"）；五个槽位包是 §5 配置 v3 形状（五槽位）加 stage0"冻结前全槽位校验"的必然产物——**不是**为 P4 预支的空壳。
2. **"给三个还没有驱动的方法槽位（segmentation/authoring/curation）建包"值不值得**——值得，且这是最小做法，理由见发现清单【赞同】3。延迟到 P4 的两个替代方案（stage 层裸读参数字典、或给无参数槽位开特例跳过校验）都比 29 行的包更贵。
3. **测试没有膨胀**：test_params.py（213 行）实质是 v2 在 test_config.py 里约 31 条校验测试的等价搬移，搬到参数的所有者层，外加两条新钉子（默认值必须合法、不支持的类型注解必须报错）；test_freeze.py（77 行）三条 AST 断言，无框架无依赖，精准兑现方案 R4。

一条主线观察：P1b 之后，"校验"这个动作从 core 层下沉到了 methods 层，但 core 保留的职责（五槽位形状、补全缺省槽、拒绝未知槽位）与 methods 层的职责（每槽位参数契约、跨参数 check）边界干净，没有出现两边都管又都不管的灰区。

## 二、发现清单

分级口径（协议 §10）：【偏移约束】= 违反本方案某条明文；【琐细】= 不影响推进，择机处理；【赞同】= 值得点名肯定的取舍。先结论：**【偏移约束】无，【琐细】5 条，【赞同】9 条。**

### 【赞同】

1. **params.py 通用构建器最小且必要**（`src/expedistill/methods/params.py`，66 行）。
   它不是"提前抽象"：v2 的 retrieval 三参数必填/类型/范围、whole_bank 拒绝参数、live 的 n_attempts≥2/improvement/max_injection_tokens、exam 拒绝其他键，全是 core.config 里逐方法手写的校验，新方法每个都要再抄一份。构建器吃掉的是这部分**必然重复**的样板，方法特有约束（如 n_attempts≥2 的理由）仍留在各槽位包的 `__post_init__`。两处容易写错而它写对了的细节：bool 是 int 子类、YAML 的 `0` 解析为 int——都按 v2 原契约处理（int 字段拒 bool，float 字段收 int）。报错带槽位名、方法名、合法键列表，"全响亮失败"兑现。

2. **注册表四元组与 resolve/resolve_params 二分完全按方案落形**（`src/expedistill/methods/registry.py`）。
   纯数据表（`_Entry(driver, params, params_class, check)`），注册不 import 任何东西；`resolve_params` 只加载槽位的轻量参数包（stage0 冻结前校验五槽位时零驱动加载），`resolve` 才按需 import 驱动——exam 分发链依旧不加载 live 驱动。跨参数约束 `check_live_run`（n_attempts ≤ n_attempts_weak，方案 R7/问题⑦）放在 acceptance 包内、由表项指向，正是 §3 "check 放跨参数/跨槽位约束"的位置。

3. **三个无驱动槽位包是 v3 形状的必然产物，不是超前抽象**。
   正面回答任务问题"是否应延后 P4"：**不应**。理由链：(a) §5 的 v3 配置形状明确含五槽位（示例 YAML 五行俱全），§7 P1b 行交付"v3 配置形状 + Params/check"，所以 P1b 必须让五个槽位都能被 stage0 的全槽位 resolve 校验；(b) segmentation 的 K/retry_limit、curation 的 token_budget/rounds 在 stage2/stage5 有**真实消费者**——v3 把这些参数从配置顶层搬进槽位后，不建 Params 类，stage 只能裸读字典键（`config.methods["curation"]["token_budget"]`），失去类型与未知键校验，比 29 行的包更糟；(c) authoring.exams 当前无消费者（stage3_generation 未改）恰恰是 §7 P1b 行的字面执行——"authoring.exams: true 写入形状"，P4 放开 false 时才接行为；(d) 延后的另一方案是给"无参数槽位"开特例（白名单、跳过 resolve），特例逻辑比包本身贵。每包 19–29 行、一个 dataclass、`driver=None` 显式标注，包 docstring 写明"驱动待第二实现/P4"，零隐藏机制。§4 的"何时建"表格指**槽位驱动**（curation 的 driver 确实留到 P4 与 none 同批），与"参数契约随配置形状在 P1b 落地"不矛盾，实现把这两层拆对了。
   空 `WholeBankParams` 同理：它只为统一驱动签名 `resolve(ctx, params, bank, test_tasks)`，让 stage6 无按名分支，空类是统一接口的最低成本。

4. **§9 禁止清单逐项核对：无违禁元素**。
   无 needs/provides、无 extends、无方法基类（驱动是函数 + frozen dataclass）、无 slot 冗余字段、无类型化 Summary（仍返回 dict）、无按方法声明 prompts（反方向：全量冻结）、无运行时成员检查（R4 的两条静态测试替代）、无新 hash/指纹/审计机制、无工作目录迁移、未搬 K4/K5/K6 本体、角色表未开放（ROLE_DEFAULTS 只是改名 + 数据化，§5 原文兑现）、无插件自动发现、无 DAG/DI。

5. **test_params.py 整体是搬移而非膨胀，被删的 v2 校验覆盖逐条有着落**（`tests/unit/methods/test_params.py` ↔ 被删的 test_config.py TestSkillDelivery/TestAcceptance 共约 31 条）。
   抽查等价性：retrieval 参数化类型/范围 case、min_score 含 0 排 1、whole_bank 拒绝参数、live 的全部字段约束、exam 拒绝 live 参数、未知名列出已知方法、无名多方法槽位拒绝——全部在 test_params.py 有对应条目；语义反转的两条（旧 schema 仍可载入 → v3 拒载并指向转换器；exam manifest 不含 LIVE digest → 含且值逐字节相等）是设计变更的正确跟随，不是丢覆盖。别名测试（冻结值不随调用方字典漂移）在 TestMethodSlots.test_not_shared_with_caller_mapping 续存。**特别值得肯定的两条新钉子**：`test_defaults_satisfy_every_contract`（METHOD_DEFAULTS 若被某槽位契约收紧悄悄弄非法，测试当场红）和 `test_build_params_rejects_unsupported_annotations`（构建器的"最小类型集"不是纸面承诺）。
   一个有趣的结构性观察：v2 里"whole_bank 下写 top_k 报错""exam 下写 n_attempts 报错"是两段专门写的校验代码；v3 里这两条约束**退化为 build_params 未知键检查的自然推论**——测试保留它们，钉住的是行为等价（迁移纪律），不是机制重复。

6. **test_freeze.py 精准兑现 R4 且无框架**（`tests/unit/prompts/test_freeze.py`，77 行三条）。
   方案说"两条静态测试"，实现拆成三条函数（import 清单、注册位置、每 benchmark 至多一个任务 prompt）——第三条是方案 §5 原文"(2)"的后半句，拆开是让断言可独立失败，不算蔓延。AST 遍历 ~75 行，无正则地狱、无配置文件、无抽象层。
   局限（仅观察，不建议动）：`register(...)` 只认裸名调用，`import ... as` 别名可绕——静态测试防的是失误不是对抗，加别名追踪才是过度。

7. **upgrade_v2 是真正的单点映射，无第三处手写漏网**。
   唯一实现在 `core/config.py:537 upgrade_v2_config`；两个使用者 `scripts/run/convert_v2_configs.py:50`（转换脚本）与 `src/expedistill/stages/assemble_reuse.py:1039`（v2 源 manifest 身份比对）都 import 它。全库 grep v2 键名（bank_token_budget / segment_retry_limit / curation_rounds / skill_delivery）后，src/ 与 scripts/ 剩余命中全部是注释、docstring、composites 函数**形参名**（形参不构成映射）与 egg-info 生成物。三种 v2 exam 写法同值映射有专测（TestUpgradeV2），且 `TestUpgradeCoversEveryV2Config` 遍历仓库全部 v2 配置——方案 R3 的"golden 测试覆盖全部 v2 配置"兑现。live 分支丢弃顶层 retry_limit 的身份变更在 docstring 与转换脚本输出里双重披露（方案要求的"结果写新文件名 + 人工审定"路径齐备）。

8. **stage0 的 v2 manifest 拒载提示按 §6 落形**（`src/expedistill/stages/stage0_freeze.py:287-300`）。
   读 manifest 的 config.schema_version，v2 即报"回维护分支续跑"，不为此给 manifest 加字段（§6 原文）。manifest 损坏则落回通用比对拒绝，不留静默通道。audit_live_run.py 的对应改动（按 manifest 判模式、live 子树缺失 ERROR、exam run 出现 live 子树也报"目录与冻结身份不符"）超出方案字面但是正确加强——v2 时代这个错误只能靠"读不到 n_attempts"间接报出。

9. **composites/curation.py 保留 rounds≥1 防御检查与 CurationParams 的"重复"是层规则的必要代价，不是失误**。
   composites（第 5 层）不得 import methods（第 6 层），K5 被直接调用时的自防御必须本地保留。这个重复在分层规则下不可消除，指出它只是防止后来者当垃圾删掉。

### 【琐细】

1. **assemble_reuse.py 的 v2 拼写残留（P1b"同步 scripts/"要求的漏项）** → `src/expedistill/stages/assemble_reuse.py`。
   代码的白名单已改 v3 前缀表（`_ALLOWED_DIFF_PREFIXES` 含 `config.methods.delivery`），但同文件四处文案仍是 v2 拼写：
   - 模块 docstring（约 34–36 行）："the new config may differ from the source run's frozen manifest ONLY in ``{mode, split, tasks, skill_delivery, schema_version}``"；
   - `assemble_reuse` 写出的 ASSEMBLED_FROM.md 文案（约 638 行）："仅允许 {mode, split, tasks, skill_delivery, schema_version} 差异"——**这是用户可见的 run 溯源工件**，审计与运维要读，照它核对会发现实际白名单对不上；
   - baseline 档位的 ASSEMBLED_FROM.md 文案（约 854 行）："规则名、prompt digest、skill_delivery、K、预算等允许不同"；
   - `_baseline_*` docstring（约 758 行）："(rule names, prompt digests, skill_delivery, K, token budgets, weak_profile, ...)"。
   建议：四处统一换成 v3 拼写（`methods.delivery` 槽 / `config.methods.segmentation.K` 等），一次提交了结。README 里装配工具行已改对，漏的只是这个文件内部。

2. **`get_method()` 成为零使用者死 API** → `src/expedistill/methods/registry.py:122`、`src/expedistill/methods/__init__.py` 导出。
   P1b 把 stage4/stage6 都改走 `resolve` 后，`get_method` 在 src/、tests/、scripts/ 已无任何调用者（grep 证实），却仍在 `__all__` 导出并带"kept from P1a"的 docstring。它不是 P1b 新增的，是 P1b 改完之后失业的。建议：直接删除（连同 `__init__` 导出）；若想留作 registry 的原语，docstring 需给出真实保留理由，否则后来的读者会把它当契约。

3. **`acceptance_mode()` 同样只剩测试在用** → `src/expedistill/core/config.py:530`。
   stage0/stage4 改用 resolve_params/resolve 后，生产代码零使用者，唯一引用在 `tests/unit/core/test_config.py:1006-1014`。建议与 `get_method` 一并清理（测试改直读 `config.methods["acceptance"]["name"]`）。不紧急，但它和上一条一样是"接口表面积大于真实使用面"的信号，趁小收拾掉最便宜。

4. **方案文档 §8 清单第 1 条未随 P1a 裁决更新** → `docs/decisions/multi_method_framework_20261015.md` §8。
   §8 仍写"用 `@method(slot, name, Params=..., check=...)` 注册；在 methods/__init__.py 显式 import"，而 §3 P1a 注记已裁决纯数据表、明言"不要改回装饰器形式"。**实现是对的**（注册表一行），文档自相矛盾。建议：§8 第 1 条改写为"在 methods/registry.py 的 _ENTRIES 加一行（驱动模块、Params 类、check）+ 驱动文件"，与实现和 §3 注记对齐。（方案文档不在本评审可写范围，仅建议。）

5. **test_params.py 对"槽位契约测试自动覆盖"只兑现一半，属正常但建议 §8 措辞如实**。
   通用校验（未知键/缺键/类型）经 build_params 对任何新方法自动生效——这半是自动的；但 test_params.py 的槽位测试类不是自动发现，新方法的正向装载 case 与特有约束仍需手写（如新 delivery 方法的参数范围）。这不是缺陷（特有约束本无法自动生成），只是方案 §8 第 5 条"槽位契约测试自动覆盖"读起来比实际承诺多。建议 §8 措辞改为"通用校验自动覆盖，特有约束随方法手写"。

## 三、新增一个方法：P1b 后的步骤实测清单

以"新增 delivery 方法 `embedding`"为例，P1b 后真实步骤（静态走查得出，未跑测试）：

| # | 步骤 | 文件 | 量级 |
|---|---|---|---|
| 1 | 加参数类（无参数则空类） | `src/expedistill/methods/delivery/__init__.py` | ~5–15 行 |
| 2 | 写驱动（`resolve`（+`write_audit`），签名与 whole_bank/retrieval 对齐） | `src/expedistill/methods/delivery/embedding.py` | 驱动本体 |
| 3 | 注册表加一行 | `src/expedistill/methods/registry.py` `_ENTRIES` | 1 行 |
| 4 | （如需新 prompt）新模块 + import 清单一行 | `src/expedistill/prompts/<name>.py`、`prompts/__init__.py` | 2 处；test_freeze 静态测试强制 |
| 5 | 槽位测试加正向装载 + 特有约束 case | `tests/unit/methods/test_params.py` | ~2–3 case |
| 6 | 续跑测试（该方法的） | `tests/unit/methods/...` 或 composites 测试 | 按方法 |

配置侧只在实验 YAML 写 `delivery: {name: embedding, ...}`；stage 前端零改动（stage6 已经是 `resolve("delivery", ctx.config)` 直通）；stage0 校验自动覆盖（METHOD_SLOTS 循环）。跨槽位影响为零。

**对照方案 §8 原清单：无倒退。**
- 第 1 条（注册）：实现是"表一行"，比 §8 字面（装饰器 + methods/__init__ 显式 import）**少一步**且不触碰 methods/__init__ 的加载语义——这是 P1a 裁决的既定方向（见琐细 4，§8 文字待同步）；
- 第 2 条（新 prompt 进 import 清单、静态测试把关）：一字不差兑现；
- 第 3 条（改行为 = 注册新名字，不改旧名字）：表结构天然支持；
- 第 4 条（只读自己 Params 与实验级视图、目录常量）：P1a 已立（STAGE4_DIR 等），P1b 未破坏；
- 第 5 条（续跑测试 + 契约测试）：续跑测试照写；契约测试"自动覆盖"兑现一半（见琐细 5），通用校验自动、特有约束手写。

对照 §8 思想实验 B 的量级预言（"新 prompt 策略的 authoring 只改 3 个文件"）：P1b 后同一件事 = prompts 新模块 + prompts/__init__ 一行 + registry 一行 + 驱动文件 ≈ 4 处、其中 2 处是一行改动，与预言同量级（prompt 的 import 清单是全量冻结的既定代价，方案 §5 已计入）。

## 四、【偏移约束】级清单

**无。**

对照过的方案条目：§3（接口形态与 P1a 注记——纯表元组、无基类、check 位置）、§4（槽位时机——驱动与参数契约分两层各自兑现）、§5（配置 v3 五槽形状、ROLE_DEFAULTS、authoring.exams 缺省 true、prompt 全量冻结 + 两条静态测试、不做 extends）、§7 P1b 行的全部条目（v3 形状、Params/check、n_attempts ≤ n_attempts_weak、懒注册与 _MANIFEST_OPTIONAL_FIELDS 删除、upgrade_v2 纯函数、转换脚本写新文件名、stage0 v2 提示、scripts/runbook 同步——仅 assemble_reuse.py 内部文案残留见琐细 1、audit_live_run 按 manifest 判模式）、§9（禁止清单全项，见赞同 4）、§10（评审范围限定 diff+验收证据）。

## 五、结论与建议

P1b 通过本路评审，可进入 P4。5 条【琐细】不阻塞：#1（assemble_reuse 文案）建议在下一个提交顺手修——它是唯一影响用户可见工件（ASSEMBLED_FROM.md）准确性的；#2/#3（两个死 API）建议同批清理；#4/#5 是方案文档措辞同步，留给下一次方案文档修订。
