# config.py 与 curation.py 冗余与校验评审

- 日期：2026-10-18
- 一句话说明：对 `src/expedistill/core/config.py` 与 `src/expedistill/composites/curation.py` 的纯静态评审，找出重复代码、可复用的部分、不必要的校验、无用代码和过时说明，并标出不能动的部分。
- 对应 log.md 条目：2026-10-18 的“评审 config.py 与 curation.py 的冗余与校验”条。

评审依据是阅读代码、在全仓库搜索名字，以及在 `.venv` 里用小段代码复现行为。没有运行测试，也没有修改代码。

## 一、不能动的部分

两个文件里下面这些内容和正在运行的实验、金矩阵测试或审计脚本直接相关，任何改动都必须保证它们的输出逐字节不变。

**config.py**

1. `ExperimentConfig.to_dict` 与 `phase_spec_to_raw` 的输出：全部字段名、默认值、列表顺序都会冻结进 manifest。正在运行的实验每次续跑时，`infra/manifest.py` 会把 manifest 逐字段比对，`tests/fixtures/golden/*.json` 也固定了这些字段。
2. `__post_init__` 和 `load_config` 补全出来的值：规则名按 `RULE_KEYS` 顺序补全、模型角色表的补全、相对路径改成的绝对路径。这些值都会进 manifest。
3. `CONFIG_SCHEMA_VERSION`、`schema_version`、`prompts_digests`、`DEFAULT_INVALID_REDISPATCH_LIMIT`。
4. 升级函数 `upgrade_v2_config`、`upgrade_v3_config` 的输出：金矩阵的测试工具和装配工具的身份核对都会用到它们。
5. 外部对照文件的 sha256 固定（`ExternalControlSpec`、`_SHA256_RE`），这是现有的完整性检查，审计脚本也会读取它。
6. 相位标签 `phase_label`、`PHASE_NAME_RE` 和各个角色名：每个相位目录的路径都由它们决定。

**curation.py**

1. 整理日志 `curation_log.json` 的全部字段与写入顺序：金矩阵快照固定了 `phases/p08_L/curation_log.json` 与 `phases/p06_L/curation_log.json`。
2. 技能库文件 `skill_bank.jsonl` 的行内容和顺序：它们决定库指纹 `bank_reference`，技能文件夹交付的库指纹检查也依赖它们。
3. 技能库文件旁边的 `skill_bank.jsonl.lock`：这个文件之所以存在，是因为写库用的是 `append_jsonl`，金矩阵快照把它也固定了。改用 `write_jsonl` 会让这个文件消失。
4. 断点续跑的提交顺序：先保存待定的合并结果，再做回归考试，再记录合并决定，最后修改源技能的状态。测试里的 `GuardStore` 按 `store.save` 的调用次数注入崩溃，所以保存次数和顺序都不能变。
5. 测试会拦截 `curation._write_log` 来模拟写日志时崩溃，所以“删除一次重复的日志写入”这类改动会移动崩溃注入点，必须连同测试一起评估。
6. 合并产物的编号格式 `bank/merge/NNNN` 与续号规则：审计脚本据此区分合并产物与原始技能。

## 二、config.py

### 2.1 校验缺口（建议修复）

下面四项都在 `.venv` 中复现过。现有全部 v4 配置都不受影响：测试相位的执行都是 `real` 或 `real_folder`，`sequence` 都是列表，`n_attempts_weak` 都是整数，所以补上这些校验不会让任何现存配置加载失败。

1. **测试相位可以配成考试执行**（[config.py:481-490](../../src/expedistill/core/config.py)）：测试相位的 `expected_exec` 是 `None`，前面的文件夹配对检查只限制 folder 和 real_folder 必须配对，于是 `{role: test, execution: {name: exam}}` 能正常加载。运行时 `final_test.py` 不使用这个参数，照常做真实执行，配置和实际行为不一致，而且不会报错。第 484 行的注释也写错了。修复：测试相位允许的执行改为 `{real, real_folder}`。
2. **`__post_init__` 先做类型转换，后面的类型检查就永远不会触发**（[config.py:1061-1066](../../src/expedistill/core/config.py)）：
   - `sequence: "DLBLBLC"` 被 `list()` 拆成单个字母的列表，正常加载；
   - `sequence: null` 抛出 `TypeError`，`phases` 写成列表抛出 `AttributeError`。命令行只捕获 `ValueError`，用户会看到一整段调用栈，看不到配置错误提示；
   - `parse_phases` 自己在第 665 行、第 676 行本来就有这两项检查，但经过 `__post_init__` 调用时数据已经被转换过，检查无法生效。

   修复：先检查类型，再做转换。
3. **`n_attempts_strong`、`n_attempts_weak` 的检查不完整**（[config.py:1051](../../src/expedistill/core/config.py)）：只判断 `< 1`，所以 `True`、`1.5` 都能通过，`"2"` 会抛出 `TypeError`。文件里已经有 `_is_positive_int`，这里改用它即可。
4. **`scripts/run/convert_v2_configs.py` 已经失效**：它把 v2 配置升到 v3 之后，又用只接受 v4 的 `from_dict` 去校验，所以每个 v2 文件都输出 REFUSED。实测扫描 `configs/experiments` 时，没有转换出任何文件。`convert_v3_configs.py` 已经能在内存里直接从 v2 转到 v4，失效的脚本可以移入归档，同时更新 AGENTS.md 第五节里对它的说明。

### 2.2 文件内的重复代码

1. **版本号检查写了两遍**：`from_dict`（1211-1229 行）和 `load_config`（1629-1647 行）是同样的三个分支。两处缺省版本不同：`from_dict` 缺省为 4，`load_config` 缺省为 3（YAML 文件不写版本号要拒绝，manifest 里的字典要接受）。可以合并成一个辅助函数，缺省值作为参数传入。约省 17 行。
2. **“出现未知键就拒绝”写了 14 遍**（418、522、551、599、933、956、968、1075、1090、1115、1230、1306、1323、1650 行附近）。可以合并成一个辅助函数，约省 35 行。代价是：测试里约有 106 处 `match=` 断言匹配报错文字，报错措辞变了这些断言要一起改。
3. **“非空字符串或 None”的检查写了 5 遍**（410、527、532、586、1081 行）。约省 8 行。
4. **路径解析写了两遍**：`load_config` 里 tasks（1667-1670 行）和外部对照文件（1684-1687 行）用的是同一段代码。`weak_profile_file`（1660-1662 行）没有做 `expanduser` 和 `resolve`，写法与另外两处不一致。解析出来的路径会冻结进 manifest，合并时结果必须逐字节不变。
5. **同一个默认值定义在多处**：`upgrade_v2_config` 里重复写了 `_V3_METHOD_DEFAULTS` 中的数字（1256-1272 行），1527 行重复写了 `_BASELINE_ARMS`。合并时只改引用的来源，数值不变。

### 2.3 和其他模块重复

1. **`_BASELINE_ARMS`**：`stages/phase_execute/resolve.py:35` 又定义了一遍，内容相同。应由 core 层公开这个常量，resolve 改为导入。
2. **划分列表的校验**：`_validate_split`（875-902 行）和 `blocks/tasks.py` 的 `split_tasks`（97-114 行）都检查键名集合和任务 id 列表。`split_tasks` 还要校验由比例推导出的划分，它的检查必须保留；键名常量由 core 定义，block 导入使用即可。两处有一个行为差异：core 放过 `train: null`、`test: null`，block 会拒绝。应把 core 的放行范围限定到 `reserved`。
3. **版本号判断散落 6 处，缺省值有 3 种**：`from_dict`、`load_config`、`assemble_reuse._source_config`、两个转换脚本、`stage0_freeze`，审计脚本里还有一处。每处的缺省值都是有意设定的，其中审计脚本那一处属于审计口径，不能改。不建议合并。

### 2.4 不必要的校验

1. `from_dict` 里的 `if k in known`（1235 行）：前一行已经拒绝了所有未知键，这个条件永远成立，可以直接写成 `cls(**data)`。
2. `_check_live_needs_a_control` 里的 `EXTERNAL_CAMPAIGN` 分支（781-782 行）：538 行已经禁止非测试相位使用外部对照，live 相位走不到这里。

下面这些检查看起来重复，但需要保留：

- 两个相位规格类自己的 `__post_init__`：解析路径上它们不会触发，但约 10 个测试文件和 stages 层会直接构造规格对象，这时它们是唯一的检查。
- `upgrade_v3_config` 里的模式检查和必填键检查：升级是纯函数，要在 `from_dict` 运行之前就保持严格。
- `max_output_tokens_for` 里对角色名的检查：有测试覆盖，用来发现角色名拼写错误。
- `cli._build_handles` 的“角色表里缺少某个角色”检查（cli.py:203-207）：`__post_init__` 用 `setdefault` 补全角色，配置里显式写成 `""`、`null` 或 `0` 的模型名不会被覆盖，能通过加载（已在 `.venv` 中复现），这个检查是唯一拦住它们的地方。

### 2.5 无用代码

1. `ExperimentConfig.model_for`（1159-1165 行）：业务代码没有调用，只有 `tests/unit/core/test_config.py:114` 一个测试在用。
2. `parse_phases` 的参数 `n_attempts_strong`（626 行）：函数体里没有使用。删除它要同时修改约 6 个测试里的调用。
3. `ROLE_DEFAULTS` 里“角色可以映射为 None”的那套处理：类型标注（99 行）、1106-1107 行的分支和相关注释都可以删除，因为没有任何角色映射为 None。

### 2.6 过时的说明

1. 138-139 行说“pipeline.py 包装 load_config 的 ValueError”，现在做这件事的是 `cli.run_experiment`。93 行和 1102-1104 行写的 “pipeline's handle builder” 现在是 `cli._build_handles`。
2. 1347-1350 行说升级函数的输出“还不能加载”，实际上 `CONFIG_SCHEMA_VERSION` 早已是 4，转换脚本也在加载它的输出。
3. 156-161 行说 `frozen=True` 防止配置被修改，但 `ExperimentConfig` 是普通的可修改 dataclass。
4. 1059-1060 行说相位定义“在内部深拷贝”，实际上 `_method_mapping` 只做了一层浅拷贝。
5. 66-71 行 `DEFAULT_RULES` 的注释还在用已删除的 stage 1–3 的编号。
6. `parse_phases` 的说明第 4 项、`_check_compare_targets` 的说明和报错文字，都漏写了 `external_campaign`。

### 2.7 拆分

1. **升级函数单独成文件**（如 `core/config_upgrade.py`）：`_V3_METHOD_DEFAULTS`、`upgrade_v2_config`、`_refuse_unmapped_keys`、`_v3_methods`、`upgrade_v3_config`，约 360 行。全仓库有 8 处导入它们，在 `config.py` 保留重新导出即可，导入路径不变。这些函数的输出是金矩阵的输入，只能原样搬移，不能改动逻辑。
2. **`ExperimentConfig.__post_init__`（112 行）按步骤提成小函数**：模型选项的校验（1109-1153 行，约 45 行）最大，规则补全和角色补全各约 15 行。每个小函数必须返回和原来完全相同的对象。
3. 相位规格部分（约 700 行）也可以单独成文件，但有约 30 处导入，审计脚本也在其中，收益和风险不成比例，不建议拆。

## 三、curation.py

### 3.1 先要知道的背景

正式运行只会经过一条路径：`methods/curation/agentbrew.py` 的 `produce_phase_bank`，它以 dest 模式调用 `curate_bank`。run 模式（`dest=None`）只在 `agentbrew.run` 和测试里使用，而 `agentbrew.run` 在 src 和 scripts 里都没有调用方。三个整理测试套件却全部使用 run 模式。所以 run 模式在正式运行中没有用处，但删除它必须先改写这三个测试套件。

### 3.2 文件内的重复代码

1. **每轮结束的收尾代码写了两遍**（1224-1229 行与 1456-1461 行）：两处都是设置 `done`、按预算计算 `stop`、写日志、满足条件就跳出循环，第一处多出的 `continue` 位于循环体末尾，不起作用。可以合并成一个辅助函数，约省 5 行。日志字段被金矩阵固定，合并后写入的字段和顺序必须不变。
2. **“记录失败后重新抛出”写了两遍**：G7 调用（1203-1208 行）和 G9 调用（1441-1446 行），合并和回归考试两处也是同样的写法。约省 6 行。
3. **“跳过”结果的字典构造了两遍**（1289-1293 行与 1352-1354 行）。约省 4 行。
4. **“分组是不是列表”的兜底判断写了 5 遍**（552、575、582、615、704 行）：`_unit_member_ids(g)` 就等于 `set(_display_sources(g))`。约省 8 行。`_display_sources` 的输出会写进日志，合并后结果必须不变。
5. **“已接受、但不在某个集合里的技能”扫描写了两遍**（985-988 行与 1010-1013 行）。约省 6 行。
6. **读取原始技能记录写了两遍**（969-970 行与 1108 行）。必须继续读原始记录，不能改成 `to_dict()`，因为库行的字节决定库指纹。

### 3.3 和其他模块重复

1. **列出全部已接受技能**：`_accepted_members`（389-400 行）和 `methods/curation/none.py` 的 `write_as_is`（55-59 行）行为相同。可以把它公开，由 none.py 导入（方法层可以导入组合层）。
2. **预算判断**：`_within_budget`（1022-1027 行）与 `none.py:61` 是同一个判断，可以共用。
3. **文件名常量**：`"skill_bank.jsonl"` 在 `composites/phase_bank.py`、`infra/store.py`、审计脚本和金矩阵测试里都写了一遍；整理日志路径 `stage5_curation/curation_log.json` 在 `assemble_reuse.py`、`prepare_inplace_recovery.py` 里也写了一遍。src 内部可以改为导入同一个常量，审计脚本和金矩阵测试里的写法不动。
4. **合并编号的解析**：`core/ids.py` 有生成函数 `make_merged_skill_id`，但没有对应的解析函数，解析写在 curation.py 的 `_MERGED_ID_RE`。可以把解析搬到 ids.py，但必须保留现在宽松的匹配规则，因为它决定哪些技能算合并产物，进而影响续号和审计。

下面两处看起来重复，但不能替换：

- `_apply_reverted` 与 `skill_status.discard_skill` 行为不同：后者会重新读取存储并且每次都保存，前者只在技能处于待定状态时才修改，这个条件正是断点续跑依赖的幂等保证。换用后者还会增加保存次数，导致测试的崩溃注入点移动。
- `_stored_regression_attempts` 与 `acceptance_skill_status.find_judged_attempt` 选的是不同的作答记录：前者取编号最小的，后者取最新的。

### 3.4 不必要的校验

1. 530 行的 `if log_lost and len(legacy) > 1`：只有 `log_lost` 为真时才会往 `legacy` 里添加元素，所以 `log_lost and` 这部分是多余的。
2. 1306 行的 `merged is None and legacy is not None`：`reuse` 和 `legacy_offer` 不会同时非空，前半个条件已经由后半个保证。
3. 1328 行的 `.strip()`：`blocks/curate.py` 的 `merge_skills` 返回前已经做过 `strip()`。
4. 1144 行在空技能库分支里写日志：日志在 1130 行刚写过，或者从加载以来没有被修改，所以这次写入的内容和磁盘上相同。但测试会拦截 `_write_log` 来模拟崩溃，删除这次写入可能改变崩溃测试的行为，删除前要逐个核对相关测试。

下面这些检查都是在防磁盘损坏或防断点续跑出错，需要保留：

- 日志的版本和 `input_skill_ids` 检查、`final_ids` 的子集检查、`_check_merged_versions`、`_check_store_matches_log`；
- 506 行的 `store.exists` 过滤、522 行“这个单元已经有决定”的报错；
- 对分组格式的各处兜底：模型刚生成的分组不会触发它们，只有人工改过日志时才会触发。这些兜底遇到异常时会静默跳过，没有报错，和其他日志检查的处理方式不一致。是否改成报错需要单独决定，本次评审不建议改动。

### 3.5 无用代码

1. `_build_dispatch` 的参数 `round_index`（648 行）、`commit` 的参数 `unit_index`（1164 行）：函数体里都没有用到，各自只有一个调用方。
2. `curate_bank` 的参数 `weak_profile`：没有使用，代码注释也写明了这一点。删除它要同时修改约 7 处调用。

### 3.6 过时的说明

1. 文件开头（3-7 行）和多处注释还在用已删除的 stage 3–6 来描述执行时机，现在整理发生在序列中最后一个 learn 相位。
2. 174-185 行、217-220 行、1067-1068 行说 run 模式是“冻结的 stage 5 路径”、参数是为了和“stage 入口”保持一致，这些入口已经不存在了。
3. 多处引用 “README stage 5”（220、1045、1222、1437 行），README 已经没有这一节，整理流程现在在 §3.4 和 §5.6。
4. 1047 行说返回 `store.bank()`，在 dest 模式下实际返回的是 dest 文件里非待定的行。
5. `_corrupted` 的报错固定以“只有 K5 可以修改合并产物”结尾（446-447 行），但它也用于和合并无关的损坏情况，报错文字会误导读者。

### 3.7 拆分

`curate_bank` 有 457 行，内部可以分成 8 个顺序执行的步骤：

1. 参数校验（1076-1083 行）；
2. 已完成技能库的短路返回（1085-1119 行）；
3. 加载日志并恢复续跑状态（1121-1136 行）；
4. 空技能库提前返回（1138-1147 行）；
5. 提交用的闭包函数（1149-1186 行）；
6. 逐轮循环（1188-1461 行），其中 `unit_worker` 闭包占 146 行；
7. 回滚旧版本遗留的进行中合并（1463-1465 行）；
8. 收尾：预算检查、写入最终成员、追加技能库（1467-1486 行）。

可以单独成文件的部分：

- **整理日志的读写与续跑恢复**（约 275 行）：日志路径常量、读写日志的函数、各个一致性检查，可以放进 `curation_journal.py`。测试会拦截 `curation._write_log`，所以 `curate_bank` 必须继续通过 curation 模块里的这个名字调用它。
- **分波与派发规划**（543-669 行，约 127 行）：全是纯函数，不读写磁盘。测试直接引用其中的 `_partition_waves` 和 `_round_units`，在 curation.py 里保留重新导出即可。
- **技能库文件读写与过期检查**（约 120 行）：可以放到 `composites/phase_bank.py` 旁边。测试直接调用 `_refresh_dest_artifact`，同样需要重新导出。

## 四、建议的处理顺序

1. config.py 的 4 项校验缺口：直接影响正确性，改动小，现有配置不受影响。
2. 两个文件的过时说明与无用代码：只改说明和死代码，不改行为。
3. 两个文件内的重复代码合并：每处合并都要保证日志字段、manifest 字段、保存次数完全不变，并跑全量测试。
4. 拆分：config.py 的升级函数单独成文件，curation.py 的 `curate_bank` 按步骤提成函数。两者都必须原样搬移，不改动逻辑。

run 模式的删除、分组兜底改成报错、`ExperimentConfig` 相位部分的拆分，牵涉面大或需要先做设计决定，不在本次建议之内。
