日期：2026-10-15
一句话说明：P1b（schema v3 唯一一次切换）三路独立评审之 A 路（代码事实与行为等价视角），只审本步 diff（297629f..78db71f）与验收证据。
对应 log.md 条目：2026-10-15 19:30（P1b：schema v3 一次切换）。

---

## 总体评价

核心切换是扎实的，验收主张中可静态复核的部分**全部独立复核成立**。我未跑任何测试（按评审约束），而是用 git 两端 JSON 独立比对了 8 份 golden fixture：**8/8 组合除 `manifest.json` 外逐键（逐字节）一致**，这是"切换前后零漂移"的直接证据；`live_run-*` fixture 的 manifest 顶层除 `config` 外完全不变；exam fixture 的 `prompt_digests` 恰好只增两个键，且两个 live digest 与 v2 live fixture 同名值**逐字节相等**（`9ff5b74d…` / `2a20ea8f…`）。`upgrade_v2_config` 对照 v2 字段清单无遗漏、无错位、不越界；三种 exam 写法同值有专门测试钉住；装配角案例的行为变化已按协议披露；两级校验（core 形状补全 + methods 层 Params/check）闭环且续跑重解析幂等。

但发现 **1 条【偏移约束】**：P1b 行要求"同步 scripts/"，同提交改了 `smoke_stage01_alfworld.py` 与 `smoke_tb_distill_chain.py`，**漏掉了 `scripts/smoke/smoke_stage01_searchqa.py`**——它第 30 行 `from expedistill.core.config import MODEL_ROLES, ...` 导入了本提交删除的符号，脚本现在 ImportError 即死。这是本步引入的功能回归（297629f 上可运行 → 78db71f 上不可运行），一行可修。按协议 §10，需修复后对本步重审。

## 逐项核查表

### 主视角 1：upgrade_v2 映射完整性（结论：通过）

| # | 发现 | 分级 | 位置 | 建议 |
|---|---|---|---|---|
| 1.1 | v2 七个方法键（K / segment_retry_limit / retry_limit / bank_token_budget / curation_rounds / acceptance / skill_delivery）全部 `pop` 落入正确槽位，无遗漏；authoring 恒补 `{exams: true}`（v2 无条件出考题，保真）；升级后字典无 v2 残键、其余键（models/rules/token_budgets/model_options/split/tasks/…）原样透传且输入不被修改（`deepcopy` + `test_input_mapping_untouched`） | 【赞同】 | `src/expedistill/core/config.py:537-593`；`tests/unit/core/test_config.py:1087-1094` | — |
| 1.2 | 三种 v2 exam 写法（缺键 / `{}` / `{mode: exam}`）确实同值：全部走 `acceptance is None or not acceptance or get("mode","exam")=="exam"` 同一分支 → `{"name": "exam", "retry_limit": <顶层 retry_limit 或 2>}`；与 `METHOD_DEFAULTS["acceptance"]`、`ExamParams.retry_limit=2` 三处默认一致 | 【赞同】 | `config.py:564-571`；`test_config.py:1029-1038` | — |
| 1.3 | live 的 `retry_limit` 丢弃披露充分（函数 docstring、转换器 MOVED_KEYS 注释、`test_live_run_mapping` 行内注释、log 条目四处）；且事实成立：grep 297629f 的 live 驱动代码无任何 `retry_limit` 引用——v2 live_run 从不读它，丢弃无行为损失 | 【赞同】 | `config.py:573-581`；`scripts/run/convert_v2_configs.py:30-32` | — |
| 1.4 | 全部 v2 配置升级测试 `TestUpgradeCoversEveryV2Config` 逐一断言结构（无 v2 残键、五槽位齐、schema 3），3 份 v1 时代配置（pilot_alfworld / pilot_searchqa / tb21_full，均带 `model_options.max_tokens`，v2 自身保留键规则下本就非法）只跳过 from_dict 载入、结构断言仍执行；仓库实际产物与之相符（7 份 `*_v3.yaml` 生成、3 份无 `_v3`） | 【赞同】 | `test_config.py:1097-1134`；`configs/experiments/` | — |
| 1.5 | `upgrade_v2_config` 对畸形输入（acceptance 为非映射真值，如字符串）在 `.get` 上抛 AttributeError，而转换器只捕获 `(ValueError, TypeError)`——转换器会带 traceback 崩掉而不是按文件打印 REFUSED。只影响本就非法、从未运行过的 v2 文件 | 【琐细】 | `config.py:565-566`；`convert_v2_configs.py:56` | 转换器捕获面加 AttributeError，或 upgrade 前先做 isinstance 检查 |

### 主视角 2：槽位补全与两级校验（结论：通过，附一条冻结形态边界发现）

| # | 发现 | 分级 | 位置 | 建议 |
|---|---|---|---|---|
| 2.1 | core `_validate_methods`：空/缺/None 槽位补 `METHOD_DEFAULTS` 拷贝、给定槽位逐字冻结（`dict(given)`，不与调用方映射别名）；不按键合并的理由成立——按按键把 `METHOD_DEFAULTS["acceptance"]`（exam 的 name/retry_limit）并进 live_run 槽位会把默认方法的参数串进另一方法；未知槽位/非映射值/多方法槽位缺名全部响亮拒绝 | 【赞同】 | `config.py:196-232`；`methods/registry.py:66-79` | — |
| 2.2 | 两级校验闭环：stage0 在加载任何任务前对全部五槽位 `resolve_params`（build_params 未知键/类型/缺键响亮失败 + `__post_init__` 方法内约束 + `check_live_run` 跨约束 `n_attempts ≤ n_attempts_weak` 与 improvement 已注册）；解析不改 `config.methods`（`raw = dict(raw)` 后才 pop "name"），冻结 dict 与解析语义一致，同代码下续跑重解析幂等（golden 崩溃续跑变体是其实证） | 【赞同】 | `stages/stage0_freeze.py:281-282`；`methods/params.py`；`methods/registry.py:82-100`；`methods/acceptance/__init__.py:70-85` | — |
| 2.3 | **给定槽位缺可选参数时，有效默认值不进 manifest**。给定槽位逐字冻结，dataclass 默认值（`SegmentationParams.retry_limit=2` 等）不物化。对照 v2：v2 的 `to_dict` 序列化实例、物化全部默认（v2 golden fixture 的 config 带 `segment_retry_limit: 2`、`retry_limit: 2`，尽管配置从未写过）；v3 golden fixture 的 segmentation 只有 `{"K": 1}`。后果：(a) 纯手写 v3 配置省略可选参数时，manifest 读者看不到有效值；(b) 代码里 dataclass 默认值中途改动时，续跑的 manifest 逐字段比对不再能发现（v2 会拒绝，v3 静默）——由"每 run 维护分支"规则兜底。行为等价不受影响（golden 8/8 证明），且 upgrade 产物恒物化、仓库全部真实配置无实际暴露。方案 §5"冻结形式：补全默认值后的 methods 明文字典"与实现存在措辞级出入（实现只做槽位级补全） | 【琐细】 | `config.py:228-231`；fixture diff（`tests/fixtures/golden/exam-whole_bank-full.json`） | 二选一：stage0 在 resolve 后把已解析参数物化回冻结 dict；或在方案 §5/README 明文披露"给定槽位逐字冻结、方法默认值归 methods 层代码持有" |
| 2.4 | `test_defaults_satisfy_every_contract` 只遍历 segmentation/authoring/curation/delivery 四槽，`METHOD_DEFAULTS["acceptance"]`（{name: exam, retry_limit: 2}）未被钉住——ExamParams 默认契约将来收紧时这个钉子不响 | 【琐细】 | `tests/unit/methods/test_params.py:197-202` | 遍历加上 "acceptance" |

### 主视角 3：prompt 全量冻结（结论：通过）

| # | 发现 | 分级 | 位置 | 建议 |
|---|---|---|---|---|
| 3.1 | 冻结面完整性独立核实：`METHOD_PROMPT_NAMES` 15 名 + 本配置插件任务 prompt = 冻结 digest 集（golden exam manifest 16 键，差集恰为 FAKE_TASK）；两个 live prompt 进 `prompts/__init__` 正常 import，懒注册、`_MANIFEST_OPTIONAL_FIELDS`、`blocks/attribution.py` 防御性 import 全部删除；两个 live digest 与 v2 live run 同名值逐字节相等（9ff5b74d… / 2a20ea8f…，v2/v3 fixture 与当前注册表三处一致） | 【赞同】 | `prompts/__init__.py:25-38`；`stage0_freeze.py:217-234`；`tests/unit/stages/test_stage0.py:713-726` | — |
| 3.2 | 两条静态测试（实为三条）堵住主要口子：`prompts/*.py` 模块必须进 `__init__` 的 import 清单（失败方向安全：漏检即红）；`register(...)` 只允许出现在 prompts/ 与 benchmarks/；每 benchmark 至多一个。当前布局（tb/searchqa/alfworld 的 register 全在插件顶层 .py）无漏网 | 【赞同】 | `tests/unit/prompts/test_freeze.py:25-77` | — |
| 3.3 | 静态检查是启发式：只匹配裸名 `register(...)` 调用（`ast.Name`），`prompts.register(...)`（属性式）或 `from ... import register as reg` 别名可绕过。R4 本就裁决以静态近似取代运行时成员检查，可接受；留意即可 | 【赞同】（附留意点） | `test_freeze.py:47-49, 70-72` | 如要加固，补一条 `ast.Attribute`（`prompts.register` 形态）检查 |
| 3.4 | `stage0_freeze` 模块 docstring（:60-67）仍是 v2 懒注册语义（"A live_run acceptance config adds exactly the two lazily-registered G10/G11 prompt names…an exam config's name set is byte-identical to the pre-live one"），与同文件 `_prompt_digests`（:217-234）及实际代码（全量冻结）矛盾。P0+P1a 评审刚修过同类 docstring 漂移，此处漏网 | 【琐细】 | `stages/stage0_freeze.py:60-67` | 改写为 v3 全量冻结表述 |
| 3.5 | `methods/__init__` docstring 仍说"P1a keeps the table metadata-free: the config still speaks the v2 shape (core.config.acceptance_mode / skill_delivery)…"——P1a 过渡态描述，v3 下已失真 | 【琐细】 | `methods/__init__.py:12-16` | 删去或改写为"v3 起槽位即契约" |
| 3.6 | `acceptance_mode()` 在 src 内已无调用方（stage4 改用 `resolve()`），只剩自身测试；与 `registry._entry_for` 语义重复。是否保留属 B 视角，此处仅记事实 | 【琐细】 | `core/config.py:530-534`；调用方仅 `tests/unit/core/test_config.py` | 交 B 裁决（删函数+测试，或注明保留理由） |

### 主视角 4：行为语义披露（结论：通过，附两条指针级问题）

| # | 发现 | 分级 | 位置 | 建议 |
|---|---|---|---|---|
| 4.1 | v2"显式 exam vs 缺省 exam 关键集差异拒绝"角案例消失已正确披露且无害：`test_bank_level_explicit_exam_target_assembles` 改为断言新行为，docstring 写明缘由（补全把两侧规范形到同一槽值；v3 manifest 恒携带完成槽位，关键集差异不再可能）；真正改协议的 exam→live、live→exam 仍被拒（两条拒绝测试都在） | 【赞同】 | `tests/unit/stages/test_assemble_reuse.py:1162-1175`（新名 test_bank_level_explicit_exam_target_assembles）、1142-1157 | — |
| 4.2 | v2 配置文件拒载提示准确（load_config / from_dict 均点名转换器、声明写新文件名、指向维护分支）；v2 源 run 的装配升级路径正确（`schema_version == 2` 才 upgrade，baseline/bank 两档位都覆盖） | 【赞同】 | `config.py:636-640, 516-521`；`stages/assemble_reuse.py:1032-1040` | — |
| 4.3 | stage0 的 v2 run 拒载提示指向不存在的规则："continue it on its maintenance branch (the run's own checkout, AGENTS section 6)"——AGENTS 第六节（配置与产物）**没有**维护分支规则，全文件亦无；方案 §6 标题"（写入 AGENTS 常设规则，R6）"至今未执行；提示词还丢了方案 §6 的"+ 独立 venv" | 【琐细】 | `stages/stage0_freeze.py:296-300`；`AGENTS.md` 第六节 | 把 R6（每 run 维护分支 + 独立 venv）写进 AGENTS §6，提示词对齐 |
| 4.4 | stage0 的 v2-run 拒载分支（全新用户可见错误路径）**无任何测试**（全测试树 grep "maintenance branch"/"frozen under" 无命中） | 【琐细】 | `stage0_freeze.py:287-300` | 补一条：run 目录写 schema_version: 2 的 manifest，断言 ConfigError 消息 |
| 4.5 | pre-v2（schema 1）源 run 不可复用且报错不点名：`== 2` 才 upgrade，schema 1 的真实旧 TB run（旧测试注释点名的复用源）会落到通用身份 diff 拒绝，报错不解释这是 v1 时代 run。方案 R3 只承诺 v2（"当前 TB v2 正式 run"），在范围内。但 `test_identity_diff_allows_only_the_whitelisted_keys` 的场景从"模拟真实 pre-v2 源"（删 skill_delivery）改成了"手删 v3 manifest 的 delivery 槽"，新注释"the upgrade completes it to whole_bank, exactly what a pre-slot source froze"与实际路径不符（schema 3 的手改 manifest 根本不走 upgrade） | 【琐细】 | `stages/assemble_reuse.py:1032`；`test_assemble_reuse.py:630-647` | 注释改写，并在测试或方案注明 pre-v2 源已不支持（响亮拒绝） |
| 4.6 | v2 时代一次性工具未标注/未归档：`fork_run_stage04.py`（白名单 `.config.bank_token_budget` 等在 v3 下永不匹配）、`build_skillopt_split_configs.py`（`verify_config` 调 `load_config` 读自己生成的 v2 配置，v3 下必拒）。两者都失败响亮、产品是冻结记录，无静默风险 | 【琐细】 | `scripts/run/fork_run_stage04.py:97-100`；`scripts/run/build_skillopt_split_configs.py:321` | 加头注"v2-era 工具，v3 代码下 verify 路径不可用"或移 docs/archive 说明 |
| 4.7 | 审计脚本升级正确且比 v2 更准确：按 manifest 判模式；live 缺子树报 ERROR（原为 INFO，符合方案 P1b 行）、exam 多 live 子树报身份不一致 ERROR（v2 时代只会以读不到 n_attempts 报错）、v2 run 报维护分支、n_attempts 走 `config.methods.acceptance.n_attempts` 新路径 | 【赞同】 | `scripts/audit/audit_live_run.py:605-650` | — |
| 4.8 | runbook 未同步：live runbook §3 仍说"mode 词表"（v3 无 mode 词表，是 name 查表），§4"新代码不动它们（manifest 键集逐字节不变）"是 v2 懒注册不变量，v3 全量冻结后不成立（exam manifest 也带两个 live digest；v2 run 在 v3 下 stage0 直接拒载）。安全建议本身（老 run 用老 checkout）仍正确。§7 P1b 行含"同步 runbook" | 【琐细】 | `docs/runbooks/live_run_acceptance_runbook_20261014.md:41-44` | 两处措辞改为 v3 口径 |

### 验收主张复核汇总

| 主张 | 复核结果 |
|---|---|
| "除 manifest.json 外逐字节一致" 8/8 | **独立复核成立**：git 两端 8 份 fixture 逐键 JSON 比对，漂移键全部只有 `manifest.json`（键集 45/33/44/32/61/45/60/44） |
| 1912 测试全绿 | 未验证（按约束不跑测试）；静态证据自洽（夹具、测试与源码同提交更新，无互相矛盾处） |
| golden 重生成 diff 仅 manifest；methods 形状 + 两个 live digest 与 v2 live run 同名值相等 | **复核成立**：fixture diff 全部落在 manifest 区；live digest 三处（v2 fixture / v3 fixture / 当前注册表）一致；live_run fixture 的 manifest 顶层除 config 外零变化 |
| 24 崩溃续跑变体在 v3 下全部等价 | 未直接验证；证据链自洽：generate.py 在生成时断言续跑等价否则中止，provenance base_commit 更新为 00971f2（注意：这是生成时 HEAD，实际生成代码是 P1b 工作树——provenance 是信息性记录，真正的守门是常驻 golden 测试与 git diff 本身） |
| upgrade_v2 三种 exam 写法同值 | **复核成立**（代码路径 + 专门测试） |
| 全部 v2 配置升级测试通过（3 份 v1 时代按 v2 规则拒因留档） | **复核成立**（测试结构 + configs/ 实际产物 7+3 相符；"留档"记录在 log 条目） |

## 【偏移约束】级清单

1. **`scripts/smoke/smoke_stage01_searchqa.py` 被 P1b 切破坏且漏同步**（引用方案 §7 P1b 行"同步 scripts/ 与 runbook"）。
   - 事实：第 30 行 `from expedistill.core.config import MODEL_ROLES, ExperimentConfig` 导入本提交删除的符号（`MODEL_ROLES` 改名 `ROLE_DEFAULTS`），脚本 ImportError 即死；第 107 行 `for role in MODEL_ROLES:` 同步失效。同提交内其两个兄弟冒烟脚本（`smoke_stage01_alfworld.py`、`smoke_tb_distill_chain.py`）都已改为 v3 形状，唯独这份漏掉。该脚本未被标记为退役（AGENTS 五.1 约定 scripts/ 下是保留工具），在 297629f 上可运行 → 78db71f 上不可运行，是本步引入的功能回归。测试全绿主张不能覆盖它（scripts/ 无测试）。
   - 修复：第 30 行与第 107 行 `MODEL_ROLES` → `ROLE_DEFAULTS`（该脚本的 ExperimentConfig 构造本身已是 v3 兼容形状，无 K= 等旧字段，改名即可用）。
   - 按 §10 协议：修复后对本步重审，通过才继续。

其余发现均为【琐细】（8 条，见上表）与【赞同】（11 条）。
