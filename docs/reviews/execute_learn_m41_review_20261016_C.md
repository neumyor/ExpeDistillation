日期：2026-10-16
一句话说明：执行—学习重构 M4.1 的验收评审 C——可复现性与 M4.3 前置。
对应 log.md 条目：2026-10-16 M4.1 等价仪器先行落地（E1–E6 断言 + 归一化/布局映射 + 69904f7 基线束 + 自检反证）。

# 评审范围与结论

被审对象：commit 2ca0632（`git diff aee0031 2ca0632`，仅动 `tests/golden/{semantics.py, extract_baseline.py, test_semantic_instrument.py, harness.py}`、`tests/fixtures/golden_semantic_baseline/`（新建）与 log.md；src/、configs/、金矩阵 fixtures 零改动——已逐文件核对）。契约：方案 §8、§9 M4.1 行。本评审视角：这套仪器到 M4.3 真能用、结果真能信。

**结论：M4.1 验收通过**，附 2 条不阻塞的【偏移约束】（须在 M4.3 使用仪器前以小提交修复，见发现清单）与 3 条【琐细】。仪器在其定义的 M4.3 验收路径（金矩阵重生成后 compare_combo 全等 + 白名单①机判 + run_e5 全等）上完整、自洽、可复现；两条偏移约束均在该定义路径之外或属于可预判的适配点，不影响本次验收判定。

# 逐项核查

## 1. 基线束可复现性 —— 通过

- **亲跑重提取**：`.venv/bin/python tests/golden/extract_baseline.py --out <临时目录>`（默认源 69904f7，`git show` 读历史、不 checkout），9 份输出（8 组合视图 + provenance.json）与已提交束**逐字节全等**（逐文件 `cmp`）。README 的提取命令与实际行为一致。
- **常驻测试及其强度**：`test_semantic_instrument.py::test_extraction_reproduces_the_committed_bundle` 以子进程重跑提取器（`--out tmp_path`），先断言**文件名集合**相等（提取侧多写/漏写任一组合都会红），再对每份文件断言 `read_bytes()` 全等。该测试同时传递性地钉住了 `DEFAULT_COMMIT`（改默认提交而束不同步即红）。强度足够。
- **provenance 完整性**：记录来源 commit 全 sha（`69904f7e4e3fb48e6739866499b03b36a8cd50b5`）、源树路径、提取脚本、crash_fractions、逐组合 model_calls 与 crash_points。69904f7 的位置核对无误：它恰在 M1（32d6c86，对象 schema v2 + 金矩阵重生成）之前，确是"最后一个 v1 对象、stage 目录布局"的金矩阵快照，与方案 §1.3 一致。
- **git 缺失/浅克隆的失败行为**：响亮。伪提交 → `SystemExit`（"cannot read … fatal: invalid object name"，退出码 1，零文件写出）；**真实 `--depth 1` 浅克隆亲测**同样退出码 1、报错指名 blob 与 commit、束目录为空——不存在"静默空束"路径。git 二进制缺失时 `subprocess` 直接抛 `FileNotFoundError`，同样响亮。
- 组合覆盖：束内 8 份视图文件与 `harness.COMBOS`（2×2×2）及金矩阵 8 份 fixture 的标签逐一对应。

## 2. skip_audit 容差扩展的影响面 —— 通过（语义未被悄悄放宽）

`assert_resume_equivalent` 的全部使用方（grep 全仓核实）：

| 使用方 | 场景 | 扩展的影响 |
|---|---|---|
| `test_golden_matrix.py::test_crash_resume_matches_golden` | 常驻 2 组合崩溃续跑 | **无影响（惰性）** |
| `generate.py` | 8 组合 × 3 分数 = 24 崩溃变体 | **无影响（惰性）** |
| `semantics.py::run_e5` | E5（3 分数 + live 加 revision_window） | revision_window 变体**确实行使**该容忍 |
| `test_semantic_instrument.py` | 窗口测试 | 同上 |
| `tests/composites/test_acceptance_resume.py`、`test_live_acceptance_resume.py` | 复合层续跑套件 | **不使用该函数**（各自比较 store 状态与 prompt 账），不受影响 |

惰性论证（双证据）：崩溃注入走 `CrashAfter`（客户端在模型调用前 raise），而假插件的 `run()` 不发模型调用，全部模型调用都发生在 K2–K6——before_call 崩溃永远不落在 K1 的门控池内，两侧根本不会出现 skip_audit 文件。实证：我亲跑的 E5 中 4 个 before_call 变体目录里 `find -name "skip_audit*"` 为 0；反向证据是 M1 重生成（32d6c86）在**旧比较器**（无容忍）下 24 变体全绿、M3 收口时常驻测试也在旧比较器下全绿——若有 skip 行早该红。

revision_window 变体（崩溃在 K1 单元内）实测产生 `stage4_acceptance/live_run/round01/skip_audit.jsonl`（1 行 gated 兄弟单元）+ `.lock` sidecar，容忍由此被真实行使。其正当性：**方向正确**（resumed 可多、缺 golden 的行仍失败——第三段循环逐行做包含检查）；**无状态消费方**核实——src 内 skip_audit 的读者只有 K1 心跳的行数统计（`_line_count`），装配器把它列入禁止拷贝清单（`_FORBIDDEN_PATHS`，不作为状态），reports/cli/scripts 均无读者；**真丢单元由结果态兜住**——被门控单元的轨迹未持久化，续跑要么重跑（轨迹在，无掩盖）要么缺失（结果态比较红）。相邻风险核实：窗口崩溃下 `failed_units.jsonl` 全程不存在（4 变体 + straight 均 0 份），容忍清单完整。

双披露：提交说明（"teach assert_resume_equivalent that the skip_audit rows … extras allowed, missing rows still fail. Found by the new window variant"）与 log.md 条目（"assert_resume_equivalent 扩展为 skip_audit 记账差异容忍（resumed 可多不可缺；无状态消费方，真丢单元由结果态比较兜住）"）均如实记载。

## 3. E5 的完整闭环 —— 通过（附一条 M4.3 适配点，见偏移约束 2）

- **亲跑抽查 2 组合**：`exam-whole_bank-full`（34 次调用 = provenance 34；3 个 before_call 变体全等价）与 `live_run-retrieval-split`（15 = 15；3 个 before_call + revision_window 全等价），`run_e5` 返回值、变体名、等价性逐项核对。全套里 `test_e5_all_variants_equivalent` 参数化 8 组合亦全绿（含在 2084 内）。
- **窗口与 §8 C-1 一致**：§8 要求续跑变体"必须含'修订已落库、重答未发生'窗口"。`resume_variants` 对 live 加 revision_window；`_crash_in_revision_window` 在返回前**先证明窗口成立**（崩溃确已发生 + 技能仓里已有 G11 标记的修订——标记在注入块 ⇔ store 纯派生 ⇔ 修订已落库），常驻测试再证崩溃点 round01 轨迹仓为空（重答未发生）。exam 组合无改写（金夹具全首轮通过），窗口不存在，由复合层套件钉住——核实 `test_acceptance_resume.py` 的 crash-at-every-write 参数化（每个写点 before/after 都崩，并有 `test_write_counts_are_current` 守卫写点清单不漂移）确实穷举覆盖 exam 侧修订落库边界。
- **M4.3 适配点**：`run_e5(config_path, combo, workdir)` 吃调用方给的配置——v4 配置由 M4.3 既定范围里的 "harness write_config v4" 产生（方案 §9 M4.3 行明列），`run_e5` 本体无需改：崩溃点从**实测**调用数推导（不读 provenance），`RevisionWindowCrash` 按构造与布局无关（只看注入块内容），snapshot/`assert_resume_equivalent` 逐键比较与布局无关（两侧同为 v4）。**未被考虑的一点**：常驻测试 `test_e5_all_variants_equivalent` 里 "straight 调用数 == 69904f7 provenance" 的交叉断言，在 v4 下对 4 个 live 组合**按设计必然红**（白名单①删光出题调用：实测基线 live split/full 各有 3/6 个 (examiner, EXAM_GEN) 调用，v4 应为 12/22），该适配既未写进基线 README 的 M4.3 使用说明，也没有 layout 感知的期望数——见偏移约束 2。

## 4. 布局映射的鲁棒性 —— 通过（附两条琐细）

- **推导忠实**：`SEQUENCES` 与方案 §4 逐项核对一致（exam=[D,L]+[A,L]×3+[C]，retry_limit 缺省 2 已对 `core/config.py:88` 核实；live=[D,L,B,L,B,L,C]）；`phase_dir` 的 `phases/p{i+1:02d}_{local}` 正是方案 §2 P4 裁决明文规定的 `<run>/phases/pNN_<name>/`，不是猜测。`v4_layout` 从序列生成、断言函数不硬编码路径，`test_v4_layout_pins_the_predicted_phase_paths` 钉住推导形状。
- **行内字段映射与方案一致**：A 相位作答复用键 = (skill_id, version, retry_round=A 相位序号)（方案 §2 T6）⇔ `v4_layout` 把 L 分片的 `retry_round` 按分片位置改写回规范轮号；B 相位 verdicts 同理（`round`）。合成 v4 测试故意写"相位绝对值"再被拉回，证明改写生效。
- **"对象仓不动（P4）"依赖一致**：方案 §2 明文 "五个 KIND_DIRS 对象仓保持 run 级、全相位共享、目录名不变（infra/store.py:29 零改动）"，semantics.py 的归一化声明与此逐字吻合；`_skill_in_store` 探针（`KIND_DIRS["skills"] + "/" + quote(skill_id, safe="") + ".json"`）与 store 的实际键编码逐位一致。
- **错位的发现机制**：若 M4.3 实际命名与推导不符（pNN 位数、下划线 vs 冒号、目录名记号）——系统性错位会让 **E3 立即 KeyError**（规范路径缺失）、E6 红（live verdicts 空对非空基线）——一跑就红、不会静默错位；改动成本 = 改 `SEQUENCES`/`v4_layout` 数据表 + 同步一个钉形状测试，与 README 声明一致。弱点见琐细 3/4。

## 5. 白名单①机判的严格性 —— 通过

- **双向都抓，亲测**：以 `live_run-whole_bank-split` 构造 v4 形 fresh（删光 EXAM_GEN 后）——干净形过；再**删一条合法调用** → 红（"1 row(s) missing"并指名首条缺失行）；再**改一条合法调用的 request_text** → 红；`test_tamper_extra_exam_gen_call_in_live_fresh_fails_whitelist` 证**残留出题调用**（多余方向）→ 红且 diff 指名 EXAM_GEN。"恰等于"语义成立：任何多余（含漏删的出题调用）、缺失、变形都失败。
- **作用域正确**：白名单仅在 `acceptance == "live_run" and layout == "v4"` 时套用；exam 组合朴素比较（v4 exam 序列含 A，出题调用保留）——与 §8 "exam 相等；live 见白名单" 一致。身份自检用 layout="v3" 绕开白名单，避免 v3 数据对白名单的假红（log.md 披露的 (b) 项处置正确）。
- **合成 v4 自洽**：`test_synthetic_v4_layout_canonicalises_to_the_baseline` 对 live 组合删光出题调用后经 canonicalize + 白名单全等——白名单语义与布局映射端到端自洽。

## 6. 测试与证据亲验 —— 全部对上

亲跑（HEAD=2ca0632，工作树干净）：

- 全套 pytest：**2084 passed + 3 deselected**（52.98s；-m 'not live' 挡 3 个 live 冒烟，与 M3 收口口径同构）；`tests/golden` 单独 55 passed（金矩阵 11 + 仪器 44），新增数 2040+44=2084 与提交说明一致。
- 金矩阵 fixtures / src / configs 零改动：`git diff aee0031 2ca0632 -- tests/fixtures/golden/` 为 0 行，diff 文件清单仅 tests/golden + 新束 + log.md。
- `ruff check .`：All checks passed；`tests/test_layering.py`：3 passed；`git status --porcelain` 为空。
- 基线束 8 组合 ↔ 金矩阵 8 组合标签一一对应；束体积实测 399 KiB（README 称"约 416 KiB"，见琐细 5）。

## 7. M4.3 使用说明完备性 —— 基本完备

M4.3 操作者拿得到：`semantics.py` 模块 docstring（E1–E6 逐准则读取清单：路径+字段；归一化三条；明示不比清单 T12）、`compare_combo` 签名与语义（`combo, v4_root, baseline_root=BASELINE_DIR, layout="v4"` → 逐准则 JSON 报告）、`compare_views` 返回形状（`{criteria: {name: {passed, diff}}, passed}`，纯 JSON 可直接落证据）、`assert_semantically_equal`（红准则全名抛出——"the M4.3 gate"）、`run_e5` 签名/返回/响亮中止语义、失败 diff 形态（多重集缺失/多余计数 + 首个失配行与字段指认，11 个 tamper 证明指认有效）、束 README 的"M4.3 验收"一节（重生成 → compare_combo 全等 + 白名单① + run_e5 等价）与"布局不符只改数据表"指引。缺口：E5 provenance 断言的 v4 适配未写（偏移约束 2）；无一段"逐组合循环"的最小验收代码样例（操作者从 README prose 可自行拼出，属可接受）。

# 发现清单

## 【偏移约束】1：canonicalize 对多分片 generation_failures 是"覆盖"不是"拼接"（不阻塞 M4.1，须在 M4.3 前修复）

`v4_layout` 把**每个** L 的 `generation_failures.jsonl` 都登记到同一规范路径（docstring 自注 "generation failures may land in any L"），但 `canonicalize` 对 `round_field=None` 的规则走 `out[canonical] = value` 直接赋值——第二个分片静默顶掉第一个。亲测复现：p02_L 与 p04_L 各一行，canonicalize 后只剩 p04_L 那行。这与 `v4_layout` docstring 的 "Ledger shards concatenate in table order" 直接矛盾。

影响面评估：M4.3 定义的验收路径**不可达**——金矩阵 G2 全成功，generation_failures 恒空（基线 8 组合实测全 0 行），至多一个分片；且按方案 §3 决策规则只有**首个 L** 读 baseline 报告跑 G2，多分片在计划语义下不会发生。但仪器是 M4.3 的信任锚：若 M4.3 的真实形状让多个 L 拥有该分片（恰是"预测 vs 真实"最可能出岔的地方），E2 视图会静默丢行——v4-vs-v3 比较假红、v4-vs-v4 比较可能假绿；且自检的合成 v4 测试只造一个分片，永远看不见这个洞。修复约 3 行（`round_field=None` 的多源也走 shards 合并路径），建议随 M4.2/M4.3 前的小提交落地并加一个双分片合成用例。

## 【偏移约束】2：E5 常驻测试的 provenance 调用数断言在 M4.3 必然转红，适配点未披露（不阻塞 M4.1）

`test_e5_all_variants_equivalent` 断言 straight 调用数 == 69904f7 provenance（防漂移交叉检查，今天是有效证据：M1–M3 未改变任何组合的调用数）。白名单①使 v4 live 调用数**按设计**减少（实测基线：live split 15−3、live full 28−6 → v4 应为 12/22），M4.3 上该测试对 4 个 live 组合必然红。`run_e5` 本体无恙（崩溃点从实测数推导），但操作者面对这个红需要自行诊断"是白名单正确还是真回归"，最坏处置是把交叉检查整段删掉（丢失防漂移闸）。修复二选一：期望数按 layout 感知（v4 live = provenance − EXAM_GEN 数），或在基线 README 的 M4.3 使用说明里写明这一条适配。建议在 M4.3 提交里处理并留痕。

## 【琐细】3：E3 的 KeyError 响亮但不指路；E2 空账本分支对"仅相位目录内文件名漂移"静默

布局系统性错位会被 E3 的 raw `KeyError('stage6_test/stage6_summary.json')` 立即抓住（响亮、好），但报错不提示"改 SEQUENCES/v4_layout 数据表"（README 里有指引，操作者需自行联想）。另一侧：E2 视图对 failure 账本全用 `.get(..., [])`，若错位**只**影响账本文件名而 summary/verdicts 恰好匹配，基线里恒空的 rewrite/revision/generation 分支会静默双空通过——该形态需要刻意构造才会出现（映射键都由同一 `phase_dir` 格式生成），且这些分支本就由 M4 验收场景测试钉住（§8 覆盖面声明 P6），不构成实质风险。可在 M4.3 顺手加一条"phases/ 下未映射 jsonl 需在白名单"的守卫，非必须。

## 【琐细】4：篡改计数不一致

`test_semantic_instrument.py` 模块 docstring 与束 README 都写"9 种定向篡改"，实际 tamper 测试 11 个（log.md 写 11，正确）。计数偏少是保守方向的笔误，但两处文档应改为 11。

## 【琐细】5：束体积标注不准

README 与提交说明称"约 416 KiB"，实测 9 份 JSON 共 408,892 字节 ≈ 399 KiB。无实质影响。

## 【赞同】

- 基线束"预注册"设计正确：视图口径与束同提交落地、重提取常驻钉住、基线不是 69904f7 时代不存在的视图代码的产物而是"当前投影 ∘ 历史 fixture"（单一投影真相），视图演进必然显式改束。
- skip_audit 容差扩展是我见过的同类改动里论证最完整的一次：方向性（可多不可缺）、无状态消费方逐个核实、结果态兜底论证、惰性影响面的双证据（旧比较器下 24 变体历史全绿 + 亲测 before_call 零 skip 文件）、提交与 log 双披露。
- E5 的窗口变体先证明窗口真实（崩溃已发生 + 修订已入库）再返回，"不能靠削弱检查让测试变绿"的态度正确；exam 侧窗口由带 write-count 守卫的穷举套件钉住的说法经核实属实。
- 白名单①做成 `layout` 参数化的机判而非口头约定，身份自检与 M4.3 验收共用同一 `compare_views`，v3 假红问题（log 披露的 (b) 项）用 layout="v3" 朴素比较正确解决。
- 11 个 tamper 的"恰只红被篡准则 + diff 指认字段"全部核实有效，包括快照级 vs 视图级 verdict 删除的耦合分离测试——这类"断言真能失败"的自证正是仪器可信的根基。
- 测试代码纯新增、src 零改、金矩阵零改、常驻身份证明（当前 v2 对象矩阵 == v1 基线）顺带构成 M1–M3 无行为漂移的独立证据。

# 亲验记录（复算命令）

- 重提取逐字节比对：`extract_baseline.py --out /tmp/...` → 9 文件 `cmp` 全等；`--commit deadbeef` → exit 1 零文件；`git clone --depth 1 file://... → 跑提取` → exit 1 "fatal: invalid object name" 零文件。
- E5 抽查：`run_e5(exam-whole_bank-full)` → calls=34=provenance，3 变体等价；`run_e5(live_run-retrieval-split)` → calls=15=provenance，4 变体（含 revision_window）等价；revision_window 目录见 1 行 skip_audit + .lock，before_call 目录 0 份 skip_audit，failed_units 全程不存在。
- 白名单双向：合成 v4 fresh 删 EXAM_GEN 过；再删 1 条合法调用红（"1 row(s) missing"）；再改 1 条 request_text 红。
- canonicalize 多分片：两 L 各 1 行 generation_failures → 合并后仅剩 1 行（偏移约束 1 的复现）。
- 全套：`pytest -q` → 2084 passed + 3 deselected；`ruff check .` 过；`pytest tests/test_layering.py` → 3 passed；`git status` 干净，HEAD=2ca0632。
