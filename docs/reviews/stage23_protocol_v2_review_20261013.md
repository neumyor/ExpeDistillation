# 阶段 2/3 协议 v2 方案评审记录（第一轮，三方独立）

日期：2026-10-13
一句话说明：对 docs/decisions/stage23_protocol_v2_20261013.md 初稿的三路独立代码评审的汇总与处置；每路评审均独立对照仓库源码逐条核验。
对应 log.md 条目：设计定稿时与修订版方案一并记录。

---

## 总体结论

三路一致：**通过但需修改**，无 blocker。核心设计（两条新规则、G2 结构化四节 JSON
契约 + 确定性渲染、能力边界硬指令、阶段 4/5 三项保结构连带、content 单一表示）被
三路独立核验为与代码事实吻合、与分层纪律兼容。

三路共同确认属实的关键断言：字段级校验失败不被 call_model 重试（重试只覆盖 JSON
解析失败/空回复/截断）；K4/K5 的 ModelOutputError 处置路径现成；D9 单行压缩对标记
文本无损且 G7/G8/D8/bank_reference 全部只读 content；prompt 同名替换 → digest 变
→ manifest 拒绝旧 run；新规则签名与注册契约匹配；`is True/is False` 判定在 G1 保证
success 为真 bool 的前提下安全。

## 三方收敛的发现与处置

（处置对应修订版方案章节；[R1]/[R2]/[R3] = 第几路评审）

| # | 发现（严重度） | 提出方 | 处置 |
|---|---|---|---|
| 1 | **write_skill 缺前提守卫**：DISTILL v2 断言"强段恒成功"但该前提只由配置选 v2 规则保证；旧规则保留注册 + searchqa/tb21 配置仍指旧名 → 新代码+旧配置静默喂假前提 [major] | R1#3, R2#8, R3#1（三方独立提出） | 采纳：`write_skill` 加 mode↔success 守卫（distill 要求 `success is True`、diagnose 要求 `success is False`，违反抛 `ValueError`，与现有 mode 校验同先例，致命停批），置于 call_model 前零成本；README 规则行标注旧规则为"v1，与 v2 G2 prompt 不兼容"。见修订版 §3.3 |
| 2 | **K5 同构校验语义不完整**（混合类组未定义且先烧 G8 调用；来源无标记未定义；复用分支未说明）[major] | R1#2, R2#6, R3#2 | 采纳：K5 在 `merge_skills` 调用**前**预检来源标记集合（全部无标记→放行[容错]；集合一致→放行进入合并；不一致[含 有标记+无标记 混合]→抛 `ModelOutputError` 走既有 skip 路径，零模型调用）；合并产物落盘前校验含同一集合（按集合相等，非子串包含）；复用（resume `reuse.pop`）分支不重复校验。见 §3.5 |
| 3 | **K5 校验落层分歧**：R1 主张放 `blocks/curate.py::merge_skills` 块内（与 rewrite_skill 对称）；R2 指出 blocks 互不 import，块内方案需先解决标记常量归属；R3 给出替代（标记词表放 core/models.py） | R1#1 vs R2#6/R3#12 | **裁决：预检+后检都放 `composites/curation.py`（层 5→4 import `blocks.author.skill_markers`，合法）**。理由：分层规则下块内方案要么违反 blocks 互不 import、要么把 G2 的输出契约常量搬去 core（层级 0）污染数据模型层；R1 的核心诉求（不浪费 G8 调用）由前置预检完全满足。取舍记入决策文档 §3.3 |
| 4 | **README 同步清单错标+遗漏**：F 定义实际在 README 阶段 1 节（非阶段 2）；"1.1 积木规则表"不存在（具名规则语义在 1.3 表 D4/D6/G2 三行）；阶段 0 节逐字列默认规则名未列；阶段 2 节"弱轨迹恒为 false"在 v2 下变假 [major] | R1#4, R2#3, R3#6+遗漏项 | 采纳：重写 §3.10 为精确清单（1.3 三行、阶段 0 默认名行、阶段 1 F 定义、阶段 2 两处、阶段 3 全节）；R3 已核实"恒为 false"无代码依赖，纯文档修正 |
| 5 | **事实错误 ①**：ALFWorld train 78 题 → 实为 **39 题**（39×3 采样×2 臂=234 局，弱臂 117 局）[major] | R1#6, R2#2, R3#5（三方独立发现） | 已更正（§4 成本测算与 §5 开放问题 1） |
| 6 | **事实错误 ②**："所有实验配置都显式写规则名"不实：pilot_alfworld / pilot_searchqa 完全省略 rules 块，靠 DEFAULT_RULES 补齐；切默认后这两个配置语义静默变 v2，其旧 run 续跑被 manifest 拒绝 [major] | R1#5, R2#4, R3#7 | 采纳：理由改写 + 在决策文档显式记录 pilot 配置的协议漂移与旧 run 拒续跑为有意识接受（AGENTS 禁止改已启动配置，无法回填）。见 §3.6 |
| 7 | **stage1_baseline.py 模块 docstring 与 README 阶段 1 均写"有效采样不足的题不进 F"**，v2 下 0<valid<n 且有失败的题同时进 F 和 infra_shortfalls，两处文字必须同步 [major] | R2#1, R3#6 | 采纳：明确裁决"部分 infra 但有真实有效失败 → 进 F 且仍在 shortfall 清单"（这才是"任一失败"的定义性后果）；规则 docstring、README、stage1 docstring、测试真值表四处同步。见 §3.1/§3.7/§3.11 |
| 8 | **bank_token_budget 库体积后果未评估**：四节结构拉长单条 skill，F 扩大增加候选，96k 预算更易先于合并撞线 [major] | R2#5 | 采纳：v2 配置预登记处置（96k 保持为安全阀；预算先到 → curation 提前结束并如实报告，可接受结局；注入体积问题按 TB 教训 3 独立跟踪）；上线前干跑量测单条长度与预估库体积。见 §4.2 |
| 9 | **SIMILARITY 同类指令只覆盖 similar_groups、漏 conflicts**（跨类冲突对无合法合并产物，白烧 G8）[minor] | R2#7, R3#9 | 采纳：指令同时覆盖两组字段。见 §3.4 |
| 10 | **skill_markers 语义未钉死**：两套同命中/只含子集（共享 [Do not use when]）时返回什么；rewrite 的"含同一集合"须按集合相等；须行首锚定且只作用于存储多行文本 [minor] | R1#2, R2#9, R3#4 | 采纳：精确语义 = 完整含某套全部 4 标记且不含另一套任何标记才返回该套，否则 None；行首锚定；docstring 注明永不作用于注入压缩单行文本。见 §3.3 |
| 11 | **write_skill JSON 契约漏非 dict 情形**（extract_json 可返回顶层数组）；steps 可能自带编号回来（"1. 1. do X"）[minor] | R2#9, R3#3, R1#10 | 采纳：先 `isinstance(parsed, dict)`（gen_exam 先例）；`validate_skill_sections` 对 checklist 项剥离前导编号后再判非空 + prompt 加 "without numbering"。见 §3.2/§3.3 |
| 12 | **"防御放行"与"不留历史包袱"的表述矛盾未说破** [minor] | R1#11 | 采纳：保留放行分支并注明"test/foreign-content tolerance；纯 v2 run 内不可达"——选择最小改动面（现有 composites 测试 fixture 全数存活），显式记录取舍 |
| 13 | **实施顺序违反 AGENTS §11.5**（README 应先于代码定稿）；设计+评审轮本身应有独立 log.md 条目 [minor] | R1#12, R2#11, R3#8 | 采纳：修订版 §4.1 顺序改为 README → 决策文档 → 代码 → 测试 → log.md；log.md 条目在二轮评审通过、设计定稿时记（本记录即为工件之一） |
| 14 | **scripts/run/build_skillopt_split_configs.py 硬编码 v1 规则名**，日后重新生成会静默回退 v1 [minor] | R1#7, R2#12, R3 遗漏项 | 采纳：生成器改产 v2 名并注明协议版本；v2 配置头注明来源；v1 配置文件不动。见 §3.9 |
| 15 | **杂项连锁**：llm/call.py 两处 + test_call.py 一处注释把 SKILL_DISTILL 列为 plain-text caller（v2 后过时）；`_strong2_weak2` docstring 的 F 假设失效；新配置复制时 trajectory_select 过时注释（"succeeded twice/failed twice"）；test_pipeline 的 `_script` 按 G2 prompt **首行**分发（v2 改首行即断）[minor] | R1 遗漏项, R2#10, R3 遗漏项 | 全部采纳：注释/docstring 更新；新配置注释修正；`_script` 分发锚点改为稳定节标题（"SUBTASK LABEL"）。见 §3.2/§3.6/§3.9/§3.11 |
| 16 | **测试清单精化**：需改 = test_templates（EXPECTED）、test_config（TestRuleDefaults）、test_rules、test_author（write_skill 六用例改 JSON 契约 + 新函数）、test_stage1/2/3 fixture（test_stage3 的 fake author 输出必须改否则全红）；不需动 = test_stage4/5 本体（仅补 K4/K5 新校验用例，其散文 fixture 靠放行分支存活）、test_stage2_parallel、test_stage6、test_cli；新增 = render↔markers 往返、注入单行压缩标记存活、坏字段冒烟、"非对象"一例；test_stage2 的 fake 是 G1 SEGMENT 输出与 skill 无关（初稿笔误） | R3 测试面逐文件核实 + R2#13 | 采纳：修订版 §3.11 按此清单 |
| 17 | 第三种 skill 类型的扩展摩擦点在 stage3 硬二元 source→mode 映射，不阻塞 [minor] | R3#11 | 记录为已知摩擦点，stage3 docstring 注明 mode↔结构映射，本轮不改 |

## 七个开放问题的三方裁决（一致）

1. **F 扩大：值**（三方一致），基数更正为 39 题；成本含 G1/G2/G3/K4/K5 全链；上线前量化（TB21 stage-1 数据零成本重算 any_weak_failed 口径 F 作参照 + ALFWorld 先 `--until-stage 1` 看 F 尺寸）。
2. **[Verify] 强制：同意**（三方一致；TB21 被拖死案例恰是缺完成判据）；措辞加防无限重试约束。
3. **字段校验失败直接终态：同意**（三方一致，gen_exam 先例、避免三种重试习语并存）；附干跑条件：刻意注入一次坏字段确认终态账目正确 + 量测 `model_output_failed` 比例，显著（>5%）再回来加"带错误重问"（那本身是新实验）。
4. **未知标记宽松：同意**（三方一致）；多余键忽略但 checklist 类型校验不跳过。
5. **阶段 4/5 三项连带纳入：同意**（三方一致，不算过界），按发现 2/9 精化。
6. **DEFAULT_RULES 切 v2：同意**（三方一致），论据按发现 6 修正 + README 阶段 0 行同步。
7. **trajectory_select 不收紧：同意**（三方一致；偏好序恰产 v2 材料序，段级 D6 把关已够，等 v2 run 成本数据再议）。

---

## 第二轮评审（修订版方案，三路全新独立评审）

三路总评一致：**通过但需修改**。第一轮 17 条处置逐条核验：三路合计认定
"已正确落实"42 项次、"落实有偏差"6 项次（全部属下述新发现）、无"未落实"；
7 个开放问题裁决 **7/7 认可**（三路全票）。核心协议未发现新设计问题，需修的
全部是方案文字缺陷（2 major 两路独立撞出同一处、若干 minor 精确化）。

### 二轮发现与处置（已全部落进修订版方案）

| # | 发现（严重度） | 提出方 | 处置 |
|---|---|---|---|
| R2-1 | **生成器 CONFIG_PATHS 仍指向 v1 配置文件**：只改规则名不改输出路径，默认模式会拿 v2 内容静默覆写冻结的 v1 配置、`--check` 必 FATAL，与方案自己"不覆盖 v1"的承诺矛盾 [major] | 二评 R1-N1、R2-N2（两路独立） | §3.9：CONFIG_PATHS 一并切 `*_v2.yaml`，v1 路径从脚本退役，生成器直接产出 v2 配置 |
| R2-2 | **"G2 模板内嵌节标记"断言自相矛盾**：G2 模板输出 JSON 字段名（标记由渲染器加），§3.11 却要求 test_templates 断言 G2 模板文本含全部标记，照写必红 [major] | 二评 R2-N1、R3-N2（两路独立） | §3.3/§3.11：标记字面量只钉 REWRITE/MERGE/SIMILARITY 模板；G2 侧钉 JSON 契约字段名 == validate 键集；绑定由 render↔markers 往返测试覆盖 |
| R2-3 | **K5 预检抛错落点未钉死**：放在既有 except ModelOutputError 作用域外会被 run_units 当致命停批，且 G7 日志重放同一混合组 → 续跑永久卡死；skip reason 会误标 "G8 merge failed:"；来源全 None 时后检行为未写明 [major→已按 minor 精确化处理] | 二评 R1-N3、R2-N7、R3-N1（三路独立） | §3.5：raise 必须落在 `unit_worker` 既有 skip 作用域内 + 独立 reason 文案 + 全 None 组后检同样跳过（散文进散文出） |
| R2-4 | **test_pipeline 改动远不止 `_script` 锚点**：fixture 用 v1 规则名、SEGMENT fake 两臂全 success=False、G2/REWRITE fake 输出散文——v2 下撞守卫或全红 [major] | 二评 R1-N2、R2-N4 | §3.11：完整 fixture 改造清单（规则名、强臂段 success=true、G2 JSON fake、REWRITE 保节 fake、锚点改各自独有短语） |
| R2-5 | REWRITE 本就是 JSON caller，"仍是 plain-text 例证"是事实错误；v2 后 plain-text 只剩 MERGE [minor] | 二评 R1-N7、R2-N5、R3-N3（三路独立） | §3.8 更正 |
| R2-6 | 字段值内嵌换行/行首伪标记可击穿 skill_markers 往返（合法 skill 被判 None → rewrite 静默放行、K5 误跳合并）[minor] | 二评 R1-N4、R2-N6 | §3.3：validate 拒绝字符串字段/checklist 项内嵌换行与行首标记 |
| R2-7 | SIMILARITY "starting with" 与 G7 listing 格式不符；MERGE/REWRITE 产物可能带前言或加粗变体致行首锚定误判 [minor] | 二评 R2-N3、R3-N7 | §3.4："contains the section header" + MERGE/REWRITE 加 "Reproduce the bracketed section headers exactly as written, each on its own line." |
| R2-8 | README 保结构落点应为阶段 4/5 节（G6/G7/G8 各属其节），非阶段 3 节 [minor] | 二评 R3-N5 | §3.10 分节改写 |
| R2-9 | DEFAULT_RULES 漂移声明漏 smoke 内联配置：smoke_tb_distill_chain.py（无 rules，真实漂移，接受——smoke 演练当前协议）、smoke_stage01_searchqa.py（n=1 行为重合）[minor] | 二评 R3-N8 | §3.6 补声明 |
| R2-10 | DISTILL prompt 内部张力："spell out exact command forms, file paths, and values" vs "no references to this specific task's file names/values" [minor] | 二评 R2-N8 | §3.2：改为"写确切命令 FORM + 通用占位符，禁抄本题值"（DIAGNOSE 同步） |
| R2-11 | PromptSpec description 过时（DISTILL 仍说 failure lesson）；`_script` 锚点 "SUBTASK LABEL" 两模板共有无法区分；SIMILARITY 模板标记字面量未纳入钉死；"不动"清单可补 test_stage0/test_metrics/test_select [minor] | 二评 R3-N9、R3-N4、R1-N5、R3 附注 | §3.2/§3.11 相应补入 |
| R2-12 | 本评审记录自身两处章节指针错（第 6/7 行处置指向）[minor] | 二评 R1-N6 | 本文件已更正（§3.7→§3.6、§3.8→§3.7） |

### 二轮附加实测

TB21 弱臂逐题 metrics 按 `any_weak_failed` 零成本重算：**新口径 F=64/89**
（旧口径 36/89，+78%），两路二评独立重算一致；ALFWorld 实测前按约 1.8 倍
旧口径估算阶段 2–5 单元量（已写进修订版 §4.2）。

### 二轮结论

- 核心设计（四节 JSON 契约 + 确定性渲染 + content 单一表示 + 组合层 K5 +
  前提守卫 + 三项保结构连带）三方认可，无需重做；
- 上述 12 组发现全部为方案文字修订，已逐条落进修订版决策文档；
- 三路一致意见：**无需第三轮评审**，修订版经逐条对照即可放行实施；
- 实施启动等待用户确认（用户明确要求）。
