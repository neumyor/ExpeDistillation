# G2 多行值合法化 + extract_json 围栏优先级修复（2026-10-13 事故，三方诊断收敛）

日期：2026-10-14
一句话说明：修复 2026-10-13 run `runs/tb21_split_v2_20261013_2125` 阶段 3 的 72/315（22.9%）model_output_failed 误终态——67 条是校验过紧误杀合法多行命令、5 条是 extract_json 围栏优先级反向失效把合法对象劫持为顶层数组；修复只放宽不收紧，配原地恢复脚本与残余闸门。
对应 log.md 条目：2026-10-14 G2 多行值与 JSON 提取修复条目。

---

## 1. 背景与事故定性

Run `tb21_split_v2_20261013_2125`（协议 v2 TB 复跑，阶段 1 复用 246 轨迹、阶段 2–6 全新）在阶段 3 完成 243/315 单元后，于阶段 5 中途被 SIGTERM 停止。事故面：

- **67/315 单元**：G2 返回的 `steps` / `do_instead` 条目内嵌换行（heredoc 形状的命令，如
  `python3 - <<'PY'\n...\nPY`、`cat > <file> <<'HTML'\n...`），被
  `validate_skill_sections` 的"字段不得含内嵌换行"子句拒绝，全部落
  `model_output_failed` 终态（`generation_failures.jsonl` 72 行中 67 行）。
- **5/315 单元**：模型返回**未 fence 的完整 JSON 对象**，其字符串值内嵌 ``` 代码围栏
  （又是 heredoc 命令）。`extract_json` 的"围栏优先"扫描先扎进内嵌代码块，平衡扫描器
  在代码里拾到 `[]` / `[0]` 等碎片并抢先解析成功，整个回复以顶层数组到达调用方，
  `write_skill` / `gen_exam` 的 `isinstance(parsed, dict)` 检查抛
  "expected a JSON object, got list"（dumps 0178/0363/0382/0383/0500 五份实证）。
- **阶段 5 中断现场**：curation 第 1 轮已写出 dispatch map（serials 0–7）与 8 个
  pending 的 in-flight merge（`bank/merge/0000`–`0007`），但无任何 merge 决策入账
  （`merges.attempted == 0`）、无源被消耗（store 无 `status="merged"`）、
  `skill_bank.jsonl` 未写。

**72/315 = 22.9%，触发预注册条款**：`stage23_protocol_v2_20261013.md` §4.2 第 3 条
预注册了"终态率显著（>5%）再回来加'带错误重问'（那本身是新实验）"。触发后的正确
动作不是直接加重问，而是先归因——三方独立诊断（A：校验链路、B：提取链路、C：恢复
面）一致结论：**72 条全部是合法输出被我们的代码误杀，0 条是模型违约**。SKILL_DISTILL
prompt 本身就要求 "Write the exact command FORM to run"，heredoc 是这一指令的正确
答案；因此处置是修代码而不是改 prompt 或加模型重问（见 §2 被否方案）。

## 2. 三方诊断与收敛裁决

三路独立诊断对两个 bug 的机制认定一致，对修复面的裁决如下（含被否方案）。

### 2.1 多行值（67 条）

**机制**：校验的"无换行"子句在协议 v2 设计时是防伪造节标记的（换行 + 行首 `[Trigger]`
会让 `skill_markers` 误读），但它误伤了 prompt 明确要求的 heredoc 命令形态。

**裁决（采纳）**：删除换行拒绝子句；防线移到**逐行行首标记检查**——对 normalize 后
值的 `split("\n")` 每一行做 8 个节标记（`PROCEDURE_MARKERS`+`FAILURE_REFERENCE_MARKERS`）
的 startswith 检查，命中即 `ModelOutputError`（消息含字段位置、行号、标记名）。
论证：`skill_markers` 是行首锚定的，渲染器把多行值的续行按列 0 逐字写入，所以
"伪造标记出现在行首"才是真正的威胁模型——逐行检查恰好封住它，且**通过校验的
sections 渲染后，行首出现节标记的位置只可能是渲染器自己写的**。校验与渲染共用
`_normalize_newlines`（`\r\n`、孤 `\r` 归一为 `\n`），保证**校验检查的字节 ==
渲染实际用的字节**。

**被否方案**：
1. *带错误重问（预注册的 >5% 出口）*——否。重问的适用前提是"模型输出违约"；此处
   72 条全部合法，重问是让模型迁就一个过紧的校验器，白烧 token 且把正确的 heredoc
   改写成单行残句；且重问本身是新实验（prompt digest 变化 → 新 run）。
2. *改 prompt 禁多行*——否。命令的 heredoc 形态正是弱模型需要的"可照抄命令序列"
   （TB21 full 实验的受益案例恰是库中存在可照抄具体命令序列的任务）；禁多行是
   用 skill 质量换校验器的省事，同样触发 prompt digest → 新 run。
3. *校验时把换行压成空格（静默改写）*——否。heredoc 被压平后是另一条（坏掉的）
   命令；存进 `Skill.content` 的字节必须是模型写下的字节，静默改写违反
   "校验的字节 == 渲染的字节"。
4. *维持现状 + 降级为 skip 非终态*——否。合法性不该由容错语义兜底；该过的就过。

**锚点**：单行输入的渲染输出必须与现行逐字节相同——run 里已存的 243 对 (skill,
exam) 是零影响的回归锚点（离线重放门逐字节验证，见 §5）。

### 2.2 extract_json 围栏优先级（5 条）

**机制**：`_json_candidates` 无条件"围栏优先"。围栏优先的本意是"推理垃圾段后
fenced answer"的恢复路径；但当**回复本身就是未 fence 的完整对象、围栏在其字符串
值内部**时，优先级反向失效——内嵌代码块里的 `[]`/`[0]` 碎片 span 抢先解析，
把合法对象劫持为顶层数组。

**裁决（采纳）**：候选序改为四段，"围栏优先"只保留给不在任何全文顶层 span 内的
围栏：
1. strip 后整文本（过 `_load_json_candidate` 全套机制，含尾逗号修复、`json` 前缀
   剥离、literal_eval 兜底）；
2. **不严格落在任何全文顶层 span 内**的围栏（真正的包裹/推理后围栏）→ 围栏内 span
   + 围栏原文；
3. 全文顶层 span 按出现序（first-wins 不变）；
4. **严格落在 span 内部**的围栏（字符串值里的代码块）→ 其候选降为最后手段
   （降级不丢弃：整文本仍解析失败时它们还是能救）。

**被否方案**：
1. *彻底取消围栏优先*——否。会破坏既有恢复路径：推理 prose 里的杂散 `[0]` span
   会抢先于 fenced answer（test_json.py 的优先级守卫用例钉住这一点）。
2. *预扫描时把字符串值里的围栏摘除/反转义*——否。等于在提取前做一层 JSON 预解析，
   循环依赖（提取就是为了解析），且对 Python 引号字面量（literal_eval 路径）同样
   要再造一套，复杂度不成比例。
3. *在 write_skill/gen_exam 里对 list 做二次打捞*——否。修表象不修机制，
   `SEGMENT` 的合法顶层数组与被劫持的碎片数组无法在调用方区分。

`extract_json` 与 `_load_json_candidate`（含 literal_eval 兜底）签名语义不变；
既有全部 test_json.py 用例（fence 优先、first-wins、截断拒绝等）原样保绿。

### 2.3 恢复面（阶段 5 中断现场）

**裁决（采纳，用户明令）**：在**本 run 目录原地恢复**（豁免论证见 §4），恢复
手术 = 归档 `generation_failures.jsonl`（re-arm 72 个终态单元）+ 归档中断轮的
`curation_log.json` + 把 8 个 in-flight merge 置 `discarded`（原因
`stage5_interrupted_restart`，其余字段逐字保留）+ 写 rollback.json。全部经
`scripts/run/prepare_inplace_recovery.py` 执行（默认 dry-run、`--apply` 生效、
幂等、前置断言不满足即退出非 0）。

**被否方案**：
1. *新 run 目录重跑阶段 2–6*——被用户否决（明令原地恢复）。成本与论证见 §4。
2. *保留 8 个 pending merge 让续跑的 K5 认领复用*——否。复用键在 dispatch map
   （journal）上；journal 一旦归档，`_recover_pending_merges` 的 log_lost 路径
   会因"多于一个未决 merge"拒绝启动；而留着旧 journal 续跑则要求新阶段 3/4 的
   产物与旧 `input_skill_ids` 一致（`_check_store_matches_log` 的 stale 检查会
   拒绝新 accepted 的 72 条）。两难只能靠归档 journal + 丢弃 in-flight merge 解开；
   8 个 merge 的 G8 输出未经过回归考试（全部 `attempted == 0`），丢弃零损失，
   新一轮 curation 会重新提议合并。
3. *手工 mv + 手改 JSON*——否。8 个 merge 的手术必须经 Store load/save（字段
   级断言只有 status/discard_reason 变化），且全部操作要落 rollback.json 可审计。

## 3. 修复内容（逐文件）

- `src/expedistill/blocks/author.py`：新增 `_normalize_newlines`；
  `_reject_bad_text` 改逐行行首标记检查（消息含字段位置/行号/标记名）；
  `validate_skill_sections` 标量检查对象与 checklist 条目统一走 normalize；
  `render_skill_content` 渲染结构不变（多行项首行 "N. "、续行列 0 逐字），标量与
  条目统一 normalize，单行输入输出逐字节不变；docstring 记录 2026-10-13 事故。
- `src/expedistill/llm/call.py`：新增位置版 `_balanced_json_span_ranges`，
  `_balanced_json_spans` 基于它；`_json_candidates` 改四段候选序（去重保序不变）；
  模块注释补事故记录。
- `tests/unit/blocks/test_author.py`：坏字段参数表删换行拒绝项、增嵌行首伪造标记项
  （两 mode 各测）；多行 steps 逐字接受（heredoc 形状，续行列 0 + markers 正确 +
  两次渲染逐位同）；标量多行接受；CRLF/孤 `\r` 归一；确定性测试扩展多行。
- `tests/unit/llm/test_json.py`：既有用例原样保绿；新增未 fence 完整对象内嵌代码
  围栏（含 `[]`/`[0]`/`['request_id']`/`argv[1]` 片段与 literal_eval 交互）、
  prose 前缀 + 未 fence 对象 + 内嵌围栏碎片、推理垃圾段 `[0]` + fenced answer
  优先级守卫三类用例。
- `tests/unit/llm/test_call.py`：raw JSON 对象内嵌代码围栏 → parsed 为 dict、
  零 format retry。
- `tests/unit/stages/test_stage3.py`：端到端事故回归（fake 返回 0178 形状原始回复
  → GENERATED 而非 model_output_failed）。
- `tests/unit/stages/test_pipeline.py`：多行 steps + 值内围栏代码的端到端（skill
  落库、markers 正确）。
- `tests/unit/inject/test_skills.py`：多行 content 压缩为单 bullet 后标记文本存活。
- `scripts/run/prepare_inplace_recovery.py`（新建）：见 §2.3 与脚本头注释。
- 测试数据纪律：所有新用例的真实形状均脱敏/截短后进用例，测试不读 `runs/`。

不改任何 prompt 模板、不改配置 schema、不碰 `runs/` 内容（恢复脚本除外）。
README 无需同步：其阶段 3 契约描述（"程序校验字段……直接终态"）从未写明"禁换行"，
本轮是校验向 prompt 本意的收敛，不是设计变更。

## 4. AGENTS §六.8"修复只在新 run 目录"的显式豁免论证

仓库 AGENTS.md 第 6 节第 8 条："修复只在新 run 目录里重建，不在有问题的 run 上
原地修补"。本 run 获用户明令豁免，论证链如下：

1. **用户明令**：恢复方案由用户裁决为原地恢复（三方方案中的"新目录重跑"被明确
   否决）。
2. **manifest 全字段不动**：修复是纯代码修复，不改 prompt 模板（digest 不变）、
   不改配置 schema。续跑时的 manifest 逐字段比对天然通过；实验同一性成立——
   冻结的实验身份（配置、prompt、规则、划分）一个字节都没动。
3. **只放宽不收紧的证据链**：校验删掉的只有"无换行"子句（其余语义逐字保留），
   新增的逐行标记检查是原防线（行首标记）在新威胁模型（多行值）下的等价载体；
   提取的四段候选序对既有 560 个可解析 ok dump 全量离线重放：**72 条事故 payload
   全部通过（67 过新校验且渲染 markers 正确、5 过新提取得 dict）、其余 488 条
   0 回归（解析结果与事故时逐位相同）**，243 个已存 (skill, exam) 对的 content
   过 `skill_markers` 全部正确（208 个 version-1 skill 与重渲染逐字节一致）。
   若修复改动了任何"本该通过"的判据，重放门会当场红。
4. **append-only / 归档手术**：恢复操作全部是 mv（原文件归档不删除）+ 8 个
   in-flight merge 的字段级手术（仅 status/discard_reason，程序断言）+ 新写
   rollback.json（原因、72 个 id 清单、逐文件操作、时间戳）。unit-failure ledger
   （`failed_units.jsonl`）不动（append-only 历史）；被误终态的 72 行只是被
   `generation_failures.jsonl` 的归档 re-arm，其历史记录保留可审计。
5. **对照组不受影响**：阶段 1–2 产物、243 个已验收单元、阶段 4 的 attempts 与
   ledgers 均不在手术面内；恢复只影响 72 个待重生单元与中断的阶段 5。

## 5. 恢复程序与残余闸门

**执行顺序（给编排者）**：

1. `.venv/bin/python scripts/run/prepare_inplace_recovery.py`（dry-run，确认前置
   全过、操作清单正确）；
2. 同命令加 `--apply`；
3. 续跑驱动（`--run-dir runs/tb21_split_v2_20261013_2125`，配置
   `configs/experiments/tb21_split_v2.yaml` 不变）：阶段 3 重生 72 单元 → 阶段 4
   验收新 pair → 阶段 5 从全新 journal 重新 curation → 阶段 6 出库 168 局；
4. **残余闸门**：恢复后续跑的阶段 3 若再次出现 `model_output_failed > 15/315`
   （约 5%），**停下诊断，不带病继续**——预注册条款的反向应用：修完已知两个
   bug 后再显著超 5% 意味着存在未归因的第三类失效。

**顺序不可交换**：复位（本脚本）必须在续跑前完成——K5 的
`_check_store_matches_log` 会把"新 accepted 的 72 条"判为 stale run（ConfigError），
`_recover_pending_merges` 的 log_lost 路径会因 8 个未决 pending merge 拒绝启动。
附带核验（记录性）：K4 的候选枚举（`stages/stage4_acceptance._candidates`）以
`skill_id == make_skill_id(subtask_id)` 过滤，`bank/merge/NNNN` 不满足该等式
（其 `subtask_id` 是锚 subtask），**pending 的 in-flight merge 不是 K4 的待验收
候选**；即便如此，复位仍排在续跑前（见上）。

## 6. 验收记录

- 全量 `.venv/bin/python -m pytest tests/`：**1636 passed / 3 deselected**
  （基线 1622 passed / 3 deselected，本轮净增 14 例）+
  `ruff check --select E9,F src/ tests/` 全过 + `tests/test_layering.py` 通过。
- 离线重放门（一次性脚本 `tmp/replay_g2_fix_20261014.py`，只读 run 目录）：
  560 个可解析 ok stage-3 dump（G2 315 / EXAM 245）全量重放——72 条事故 payload
  全部通过（67 过新校验且渲染 markers 正确；5 过新提取得 dict，其中 3 条 G2
  同时过校验+渲染、2 条 EXAM 的 question_text/reference_answer 字段契约亦通过，
  即 5 个单元经 G3 字段检查可完整恢复）；其余 488 条 0 回归（解析结果与事故时
  逐位相同）；243 个已存 (skill, exam) 对 content 过 `skill_markers` 全部正确，
  其中 208 个 version-1 skill 用修复后代码从 dump payload 重渲染与存储内容
  **逐字节一致**（单行锚点）。
- `prepare_inplace_recovery.py` 对真实 run 目录 dry-run：前置全过（含 stale
  run.lock 持有者已死判定），操作清单正确；`--apply` 路径与幂等性在 run 目录的
  tmp 副本上验证（8 个 merge 仅 status/discard_reason 变化、rollback.json 完整、
  二次运行为 no-op exit 0）。
