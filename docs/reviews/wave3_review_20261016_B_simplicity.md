# Wave 3 简洁性评审报告（B 路）

日期：2026-10-16
一句话说明：对 feature/multi-method 分支 `fd64e40..HEAD` 合并结果（切段接线、装配器迁移、审计脚本迁移、README 重写与配置转换器）做的纯静态可读性评审，不运行任何测试、不改任何代码。
对应 log.md 条目：Wave 3 各条（2026-10-16 23:16 / 23:22 / 23:33 的 Wave 3-S、3-A、3-B、3-C 条目）。

---

## 总体判断

**可以合并。没有阻断项。** README 重写整体质量高：一个没读过设计文档的初级程序员按顺序读下来，能跑起来一个实验、能看懂目录里的每个文件。七模块表、九条分工、十二种回执、两个 v4 配置示例、目录布局表都与代码对得上（核对过程见文末清单，其中两处示例逐字段核对过转换函数的实际输出）。四个代码改动（切段驱动、装配器、审计脚本、转换器）都没有过度设计或死代码，注释密度高但每段都在讲"为什么"而不是复述代码。

发现一个偏移（README 声称的硬约束代码没有实现）和八条细节。偏移建议在合并前处理，因为它会让读者对代码行为产生错误预期。

---

## 偏移（1 条）

### P1. README 说 "`n_attempts ≥ 2` 是硬约束"，代码只强制了 ≥ 1

- **证据**：
  - `README.md` 第七节已知风险 4："`n_attempts ≥ 2` 是硬约束"；
  - 决策文档 `docs/decisions/acceptance_live_run_20261014.md` A2 裁决：live 的 `n_attempts` "必填，**≥2**——1v1 的 H0 假通过率≈真实成功率本身，D1 守卫采纳"；
  - 代码实际检查：`src/expedistill/methods/execution/__init__.py` 第 44 行只要求 `n_attempts >= 1`；`src/expedistill/stages/phase_execute/live.py` 第 127–131 行只查上界（live 深度不得超过 `n_attempts_weak`），没有下界；`src/expedistill/core/config.py` 的加载期检查清单也没有这一条。整个 `src/` 里搜不到任何 "≥ 2" 的下界检查。
- **为什么是偏移**：决策文档把这条定为守卫（D1），README 继承了"硬约束"的说法，但相位世界迁移后这条检查不存在。初级程序员读到"是硬约束"会以为配 1 会被加载期拒绝，实际会一路跑通，然后在统计功效上踩坑——这正是这条守卫本来要拦的事。
- **修改建议**（二选一）：
  1. 在 `core/config.py` 的加载期检查清单（2.3 节列了 7 条，可加第 8 条）或 `_check_attempt_depths` 里对 live 角色相位补上 `n_attempts >= 2` 的下界检查，报错文案引用 D1 守卫的理由；
  2. 若判断这条约束不该由加载期背（比如留给实验设计纪律），把 README 第七节改写为"设计要求 ≥ 2，代码只强制 ≥ 1，配 1 不会被拒绝"，并在第五节 5.4 提一句。
  两条路都要在 `docs/decisions/` 里记一句取舍。

---

## 细节（8 条）

### D1. runbook 出现重复标题（本次补丁引入）

- **证据**：`docs/runbooks/live_run_acceptance_runbook_20261014.md` 第 44–46 行，"## 0. 适用范围与身份声明" 连续出现两次。对照 `git diff`：新增的 0.5 节末尾多写了一个标题行，原有标题还在原处。
- **修改建议**：删掉 0.5 节块末尾（第 44 行附近）多出的那一行标题和空行。

### D2. "基础设施空洞" 首次出现在 3.4 组合积木表，解释在 5.9

- **证据**：`README.md` 第 449 行（3.4 节"空洞记录"行）首次使用"已接受基础设施空洞"，没有解释也没有指引；定义在 5.9 节（第 910 行起）。5.2 节的使用带了"(见 5.9)"指引（第 741 行），3.4 节这处没有。
- **修改建议**：3.4 表该行加"（什么叫空洞见 5.9）"，与 5.2 的写法对齐。

### D3. "待定" 首次出现没有挂上英文对应词

- **证据**：`README.md` 第 55 行（1.2 节执行阶段表）"每条待定技能对应的考题"是"待定"首现。"待定 = pending"的对应要到 4.1 节 Skill 对象的 `status(pending/accepted/discarded/merged)`（第 574 行）和 5.3 节（第 746 行）才能拼出来。
- **修改建议**：1.2 表首现处写成"每条待定（pending）技能对应的考题"，一词解决。

### D4. "金基线" 首现无解释

- **证据**：`README.md` 第 854 行（5.6 节 outcomes 账本）："审计脚本与金基线以它为准"。"金基线"指 `tests/golden/`（目录存在），但正文没说。
- **修改建议**：改成"审计脚本与 `tests/golden/` 的金基线测试以它为准"。

### D5. `weak_profile_file` 只有括号提及，没有说是什么

- **证据**：`README.md` 第 151 行（2.1 字段表）只写"（或 `weak_profile_file`）"。`core/config.py` 第 1447 行的 docstring 说清了（一个路径，相对路径相对配置文件解析，与内联 `weak_profile` 二选一），README 没带。
- **修改建议**：字段表说明栏补一句"二选一：`weak_profile_file` 是画像文本文件的路径（相对路径相对配置文件解析）"。

### D6. 十二种回执里 kept / deleted 共用一行

- **证据**：`README.md` 第 867 行（5.6 节结局表）：`kept` / `deleted` 两个结局合在一行、共用一句含义。任务书要求"12 种回执一句话一个"。
- **修改建议**：拆成两行，各给一句（kept = live 组修订时写作侧决定保留；deleted = 决定删除）。

### D7. 转换器脚本的两个输入错误会以原始回溯暴露

- **证据**：`scripts/run/convert_v3_configs.py`：
  - 第 105 行 `argv.index("--dir") + 1`：`--dir` 作为最后一个参数出现而没有值时，是 IndexError 回溯，不是一句人话；
  - 第 128–129 行把所有非 `--dir` 的参数当文件路径：打错的旗标（如 `--dry`）会走到第 64 行 `open(path)`，抛 FileNotFoundError 回溯；`convert_file` 只对"不是 YAML 映射"和升级失败给了友好拒绝（第 65–87 行），没有兜 OSError。
- **修改建议**：`--dir` 缺值时打印用法并退出 2；`convert_file` 里把 `open` 包进 `except OSError` 返回 `f"{path.name}: REFUSED (cannot read: {exc})"`。不知道 v2/v3/v4 区别的人正是最容易打错参数的人，这两处是给他们的第一道提示。

### D8. log.md 的 Wave 3 条目顺序与时间戳不符

- **证据**：合并后 log.md 末尾四条时间戳依次为 23:16 → 23:33 → 23:22 → 23:16（W3-S、W3-C、W3-A、W3-B）。AGENTS.md 第二节规定"按时间顺序排列，旧的在上、新的在下"。这是四个分支各自追加后合并的自然结果，但违反了字面规定。
- **修改建议**：要么接受"合并顺序即记录顺序"并在 AGENTS 第二节补一句豁免（分支合并时按合并先后落位），要么把这四条按时间戳重排。前者改动小。

---

## 核对通过清单

以下是逐项核对过、没有问题的点（静态核对，未运行）：

1. **v4 示例与转换函数一致**：`core/config.py` 第 1400 行的公式是 `[D, L] + [A, L] × (retry_limit + 1) + [C]`；README 2.4 的 exam 示例 `sequence: [D, L, A, L, A, L, A, L, C]`（retry_limit = 2）恰好是它的展开（3 个 A 相位）。live 示例 `[D, L, B, L, B, L, C]` 与第 1208 行的转换说明一致。
2. **示例可以照抄**：A 相位省略 `tasks` 合法（`config.py` 第 366 行默认 `"train"`）；7 份仓库里的 `*_v4.yaml` 转换产物与示例形状相同（抽查了 `tb21_split_v2_v3_v4.yaml` 的 D/A/C 定义与 sequence）。
3. **十二种回执与代码一致**：`stages/learn_tasks.py` 实际写出的 outcome 词表（added / accepted / already_accepted / revised / unchanged / kept / deleted / discarded / skipped_no_material / skipped_existing / skipped_terminal / infra_failed）与 README 5.6 的表逐一对上，数量是 12。
4. **目录布局表覆盖完整**：4.3 节列出的每个文件都在正文有解释或自明注记（manifest、run.lock、pipeline_log、五仓、live 子树、phases 各账本、work、llm_requests、skill_bank.jsonl）。
5. **README 引用的文件全部存在**：`docs/decisions/` 下被引用的 5 份决定文档、`configs/experiments/` 下被引用的配置文件，逐一核对无断链。
6. **AGENTS 与 README 分工清晰、无矛盾**：AGENTS 讲目录/规范、README 讲设计；AGENTS 明确"积木编号与文件的对应以 README 第三节为准"；本次 AGENTS 的改动全是交叉引用修正（第一节→第三节、第二节→第四节）与目录树同步，未引入设计内容重复。唯一的轻微重复（AGENTS 七.1 的 RunOutcome 三字段定义）与 README 3.3 的描述一致。
7. **七模块表与九条分工自明**：模块表给出"输入 → 输出"，九条分工每条可独立读懂；1.1 节先给字母再指向 1.2 表的写法正确处理了前向引用。
8. **切段驱动无过度设计**：`methods/evidence/segmentation.py` 共 170 行、一个公开函数加两个私有帮手；落盘、续跑、重切预算、耗尽审计全部复用 K2 的既有机制，没有新增机制；模块 docstring 每段讲的是"为什么这样做"（惰性、只切强角色、串行重放的理由），与 AGENTS 的注释规范一致。
9. **装配器改动可读**：v3/v4 双源读取拆成 `_source_config` / `_source_bank_rows` 两个独立帮手；v3 generation_failures "不拷贝、按数披露"的处理在 docstring、ASSEMBLED_FROM 文本、摘要三处口径一致；phases/ 树的精确形状断言（`_assert_phase_tree`）报错信息指明了两种可能原因和处置（"a human must look at this directory"）。
10. **审计脚本不是迷宫**：schema 分派只有一个顶层入口（`audit_run` → `_audit_v4` / `_audit_legacy`），两个分支各自扁平；九个检查在模块 docstring 里编号列出，四个新检查（phase-dirs / units-report / learn-ledgers / final-test）每个一个函数、每条发现带一句解释和处置方向（例如"an interrupted commit the resume's finalization sweep would finish"）。
11. **转换器的报错可指导行动**："REFUSED (its v3 record ... already exists; convert THAT file instead)"、"already schema v4, skipped"、"exists, left untouched" 三类拒绝/跳过都写明了下一步该做什么，不依赖读者了解 v2/v3/v4 的历史。
12. **测试命名与断言可读**：抽查 `test_w3s_segmentation_wiring.py`、`test_w3c_convert_v3_configs.py`、`test_w3b_audit_v4.py`、`test_assemble_reuse.py`：测试名都是完整句子（如 `test_an_add_row_without_strong_material_is_recorded_not_raised`），断言旁的注释解释意图，夹具的 docstring 交代契约（如 `_Config` 对 test_w2b 形状的引用）。
13. **`--until-phase -1` 的三处描述一致**：README 第六节、`cli.py` 的报错文案、`seed_tb_stage1.py` 的用法说明，都写成"冻结步落盘即停、不派发任何相位"，且 `cli.py` 的边界检查（`-1 <= N < len(sequence)`）与 README 的"上界是序列长度减一"一致。
14. **README 声明的"转换结果带 `invalid_redispatch_limit` 缺省值 1"**：7 份 v4 配置抽查首行均有 `invalid_redispatch_limit: 1`。

---

## 附：范围外观察（不要求处理）

- `AGENTS.md` 第五节举例提到 `run_experiment.sh`，`scripts/run/` 下没有这个文件（本次改动之前就如此，不在本波范围）。
- `methods/evidence/segmentation.py` 的 `run_dir` 参数带"传了用传的、没传用 `store.run_dir`"的回退；调用方（`learn_tasks._land_add`）总是显式传，回退分支目前只有测试用。两处来源无害，但若后续有人改 store 的 run_dir 语义，这里是个静默分叉点。
