# 阶段 2/3 协议 v2：失败驱动选材 + 结构化 skill（修订版，含三方评审采纳）

日期：2026-10-13（初稿）；2026-10-13 修订一（第一轮三方评审采纳）；2026-10-13 修订二（第二轮三方评审采纳）；2026-10-13 修订三（整合方案评审采纳：节标记常量移址 core/models.py，见 docs/decisions/retrieval_delivery_tb_split_20261013.md）
一句话说明：按七条产品思路重构阶段 2/3 的选材规则与 G2 skill 生成协议（含阶段 4/5 的最小保结构连带修改）；新协议 = 新实验，不向前兼容旧 run。两轮各三路独立评审的全部发现已采纳（处置对照见 docs/reviews/stage23_protocol_v2_review_20261013.md），三路一致结论：核心设计无需重做、无需第三轮评审。
对应 log.md 条目：2026-10-13 设计定稿条目（实施另行记录）。

---

## 1. 背景与动机

TB21 full 实验（`runs/tb21_full_v3_212702`，2026-10-13 完结）结论：整库注入对弱模型
无整体提升（avg@N −0.0037 / pass@N 0.0），任务级 17 好 16 差对冲。逐任务归因给出三层失效：

1. **选材层**：成功/失败轨迹混切，强段连失败的也全部入料，skill 里"怎么做"与
   "教训"混装；
2. **写作层**：skill 是无结构散文，含弱模型（flash 级蒸馏模型）无法操作的默会表述
   （"deeply understand"类），抽象协议零迁移；
3. **交付层**：325k 字符整库平铺注入，注意力税沉重——属阶段 5/6 的库交付问题，
   本协议不修，独立跟踪（见 §4.2 预算预登记）。

AgentBrew 论文（papers/agentbrew_lifelong_knowledge_brewing…，§2.3.2 / §2.4.2 / §3.5）
的可借鉴经验：结构化 note（trigger_pattern / corrective_rule / minimal_steps /
not_applicable_when）、student-aware prompting（声明学生模型的模型类与弱点、禁默会
表述、祈使句 + 显式顺序）、note 只存可迁移护栏不存整题解答。**参考其思想，不照抄其
schema**（我们的 skill 仍是给人读的标记文本，不是带 note_key/quality_score 的记录）。

## 2. 七条思路 → 设计映射

| # | 思路 | 落点 |
|---|---|---|
| 1 | 只挑学生有失败的 task 的段；学生全成功的 task 不切 | 新 `task_select` 规则 `any_weak_failed`（F 定义，README 阶段 1 节） |
| 2 | strong 段只挑成功的，weak 段只挑失败的 | 新 `segment_select` 规则 `strong_success_weak_failed`（D6） |
| 3 | source=strong 总结成执行步骤 | `SKILL_DISTILL` 重写为 procedure（执行步骤）写法 |
| 4 | source=weak 总结成失败参考 | `SKILL_DIAGNOSE` 重写为 failure-reference 写法 |
| 5 | 明确弱模型能力边界来适配 | 两 prompt 强制"先读画像、按画像写作、弱点配防御步骤" |
| 6 | 明确步骤清单、避免默会表述 | 两 prompt 的写作纪律（禁语表）+ JSON 字段化输出 + 程序校验 |
| 7 | skill 有明确结构，成功/失败结构略不同，参考 AgentBrew 不照抄 | 两套固定四节结构 + 确定性渲染 + 校验 |

**协议性质**：prompt 变更 → manifest digest 变化 → 天然要求新 run 目录；规则名变更
→ 新配置。旧 run（TB21）已完结，不需要任何兼容代码、迁移逻辑或双轨分支。

## 3. 逐文件修改方案

### 3.1 `src/expedistill/blocks/rules.py` — 两条新具名规则 + 一处 docstring 更新

```python
@TASK_SELECT.register("any_weak_failed")
def _any_weak_failed(metrics: dict, n_attempts: int) -> bool:
    """Stage-1 F-set rule, protocol v2: a task enters F when the weak model
    has at least one failed VALID attempt (success < valid). A task whose
    every valid attempt succeeded stays out (nothing to fix on it), and a
    task with no valid attempt at all stays out too (infra is not a model
    failure, README 1.3). A task with a partial sample (0 < valid <
    n_attempts) and a real failure DOES enter F -- and is still listed in
    infra_shortfalls; the two lists are no longer disjoint by construction."""
    return metrics["valid"] > 0 and metrics["success"] < metrics["valid"]


@SEGMENT_SELECT.register("strong_success_weak_failed")
def _strong_success_weak_failed(subtask: Subtask) -> bool:
    """Stage-3 D6 rule, protocol v2: keep a strong segment only when the
    segment itself succeeded (procedure material), a weak segment only when
    it failed (diagnosis material). The v1 rule strong_all_weak_failed stays
    registered but is INCOMPATIBLE with the v2 G2 prompts (a failed strong
    segment would hit write_skill's distill guard)."""
    if subtask.source == "strong":
        return subtask.success is True
    return subtask.success is False
```

- `trajectory_select` **不变**（`strong2_weak2` 的偏好序——强先成功、弱先失败——恰
  是 v2 的材料序；弱成功轨迹只在失败轨迹不足配额时入选，其失败段"学生自己犯错又
  自己恢复"正是 v2 要的材料；强失败轨迹的成功段同理。段级 D6 把关已够，为省 1–2
  次 G1 调用收紧反而是协议自我矛盾）。
- 同文件 `_strong2_weak2` 的 docstring 更新：现文"on F tasks every weak trajectory
  failed, so in practice the weak quota takes the first two by trajectory_id"
  依赖 v1 F 定义，改为如实描述 v2 下"失败优先排序第一次真正生效"。
- 旧规则 `all_valid_failed` / `strong_all_weak_failed` **保留注册**：注册表纪律是
  "名字下行为永不不变"，新增名字是扩展机制而非兼容负担；旧配置文件是历史记录。
  配套防线见 §3.3 的 write_skill 守卫——旧规则与 v2 prompt 组合不再可能静默失真。

### 3.2 `src/expedistill/prompts/author.py` — 两个 G2 prompt 同名重写 + REWRITE 增补

同名替换（digest 进 manifest 自动构成新实验）。核心变化：(a) DISTILL 删除失败分支与
`{success}` 变量（D6 v2 保证强段恒成功，死分支按"不留历史包袱"删除；前提守卫见
§3.3）；(b) 两个 prompt 都改为 JSON 输出契约；(c) 能力边界与禁默会表述写成硬性指令；
(d) 步骤条目要求不带自带编号（渲染器负责编号）。

**SKILL_DISTILL v2**（变量：label / description / steps_view / weak_profile）：

```
You are writing a skill for a weaker model, distilled from ONE subtask of a
strong model's trajectory. The subtask SUCCEEDED: the skill is the executable
procedure the weak model should follow when it faces the same kind of subtask.

SUBTASK LABEL: {label}
SUBTASK DESCRIPTION: {description}
STEPS OF THE SUBTASK:
{steps_view}

THE MODEL THAT WILL READ THE SKILL (weak model profile):
{weak_profile}

Read the weak model profile FIRST. It is the capability boundary of your
reader: every step you write must be an action this exact model can carry out
as written -- vocabulary it knows, tool syntax it handles, one action per
step. Where the profile names a weakness, add an explicit guard step against
it; where it says the model cannot do something, never assume it can.

Write the skill in four sections:
- when_to_use: the concrete, observable trigger -- "when you need to
  <goal>". Surface features the weak model can recognize mid-task, never
  abstract categories.
- steps: an ordered checklist of concrete actions. One action per item,
  imperative voice, WITHOUT its own numbering (the numbering is added
  downstream). Write the exact command FORM to run, with generic
  placeholders (for example <config-file>, <port>) where the subtask used
  a concrete value -- the discipline below forbids copying that value.
- verify: the observable sign that the subtask worked. State what to do
  when the sign is absent (redo the previous step, or report the failure);
  never "keep retrying".
- not_applicable_when: when this skill does NOT apply, so it is not forced
  onto unrelated subtasks.

Writing discipline:
- short, simple sentences; one instruction per sentence;
- imperative and concrete. Banned phrases the weak model cannot
  operationalize: "deeply understand", "deeply analyze", "carefully
  consider", "be aware of", "make sure", "keep in mind". Write the action to
  take instead of any of them;
- no references to this specific task, its file names, or its concrete
  values: the skill must transfer to other tasks of the same kind.

Output ONLY JSON with exactly these fields:
{{"when_to_use": "<one or two sentences>",
  "steps": ["<one action>", "<one action>", ...],
  "verify": "<one or two sentences>",
  "not_applicable_when": "<one sentence>"}}
```

**SKILL_DIAGNOSE v2**（变量：label / description / context_summary / steps_view /
weak_profile）：

```
You are writing a failure reference for a weaker model, based on ONE subtask
of the weak model's own trajectory where it went wrong. The weak model will
read it to recognize the same kind of mistake and avoid it.

SUBTASK LABEL: {label}
SUBTASK DESCRIPTION: {description}
WHAT THE PRECEDING PART OF THE TRAJECTORY WAS DOING (context summary):
{context_summary}
STEPS OF THE SUBTASK:
{steps_view}

THE MODEL THAT WILL READ THE SKILL (weak model profile):
{weak_profile}

Work in three passes:
1. Diagnose: find where the weak model started going wrong -- which step or
   decision, and what kind of mistake it made there (a wrong command, a
   skipped check, a misread observation, a repeated failing action, ...).
   Name the missing capability at the weak model's level, not a vague
   "logical error".
2. Adapt: read the weak model profile. It is the capability boundary of your
   reader: every corrective step must be an action this exact model can carry
   out as written -- vocabulary it knows, tool syntax it handles, one action
   per step. Where the profile names a weakness that caused or worsened this
   failure, the fix must guard against it.
3. Write the reference in four sections:
   - trigger: the failure signature -- what the weak model is doing when this
     mistake is happening. Concrete and observable, phrased so the model can
     recognize it in its own behavior.
   - why_it_fails: one or two sentences: what goes wrong and why.
   - do_instead: an ordered checklist of concrete actions, the correct
     procedure for this kind of subtask. One action per item, imperative
     voice, WITHOUT its own numbering; the exact command FORM with generic
     placeholders where the subtask shows one.
   - not_applicable_when: when this reference does NOT apply.

Writing discipline:
- short, simple sentences; one instruction per sentence;
- imperative and concrete. Banned phrases the weak model cannot
  operationalize: "deeply understand", "deeply analyze", "carefully
  consider", "be aware of", "make sure", "keep in mind". Write the action to
  take instead of any of them;
- no references to this specific task, its file names, or its concrete
  values: the reference must transfer to other tasks of the same kind.

Output ONLY JSON with exactly these fields:
{{"trigger": "<one or two sentences>",
  "why_it_fails": "<one or two sentences>",
  "do_instead": ["<one action>", "<one action>", ...],
  "not_applicable_when": "<one sentence>"}}
```

**REWRITE 增补**（G6，阶段 4 一致性）：第 2 步 "Rewrite the skill to fix the
classified problem." 后接 "The rewritten skill must keep the same four bracket
sections as the original skill ([When to use]/[Steps]/[Verify]/[Do not use
when] for a procedure skill, [Trigger]/[Why it fails]/[Do instead]/[Do not use
when] for a failure reference): rewrite each section's content, never collapse
them into prose. Reproduce the bracketed section headers exactly as
written, each on its own line."（原句 "Change the wording, structure,
granularity, or trigger conditions" 中与保结构冲突的 "structure" 一词相应删去。
PromptSpec 的 description 同步：DISTILL 现文 "…into a what-to-do skill or a
failure lesson" 删去 failure lesson 措辞（v2 无失败分支），REWRITE description
补保结构——description 不进 digest，纯文档同步。）

### 3.3 `src/expedistill/blocks/author.py` — 渲染、校验、守卫与 write_skill 改造

G2 的输出契约集中在此文件；**节标记常量定义于 `core/models.py`**（整合方案评审
裁决，修订三），本文件与 `blocks/retrieve.py`、`composites/` 一并从 core import
（blocks 互不 import，第二个 block 消费者使 core 成为唯一合法的家；K5 校验放组合
层的取舍见 §3.5）：

```python
# core/models.py — new "Skill content section markers" subsection (a
# content-structure constant of the Skill object, placed by the field-value
# vocabularies; NOT a _check_vocab vocabulary itself). The REWRITE/MERGE/
# SIMILARITY prompts embed these literally (their outputs must reproduce
# them); the G2 prompts carry the JSON FIELD NAMES that map 1:1 onto the
# four sections (blocks/author.py's renderer adds the markers);
# blocks/retrieve.py weights them for retrieval. test_templates pins the
# prompt literals; the render<->skill_markers roundtrip tests pin the
# renderer side.
PROCEDURE_MARKERS = ("[When to use]", "[Steps]", "[Verify]", "[Do not use when]")
FAILURE_REFERENCE_MARKERS = ("[Trigger]", "[Why it fails]", "[Do instead]",
                              "[Do not use when]")

def skill_markers(content: str) -> Optional[Tuple[str, ...]]:
    """The marker set a stored skill content carries, None when neither.

    Precise semantics: returns PROCEDURE_MARKERS iff content contains all
    four procedure markers AND no failure-only marker; FAILURE_REFERENCE_
    MARKERS iff all four failure markers AND no procedure-only marker
    ([Do not use when] is shared); otherwise None (partial or mixed).
    Line-start anchored, and ONLY ever applied to the stored multi-line
    content -- never to the single-line compressed injection block (the
    compression keeps the marker text but destroys line structure)."""

def render_skill_content(mode: str, sections: Dict[str, Any]) -> str:
    """Deterministic marker-text rendering of one validated sections dict.

    distill:
      [When to use] {when_to_use}
      [Steps]
      1. {steps[0]}
      2. {steps[1]}
      [Verify] {verify}
      [Do not use when] {not_applicable_when}
    diagnose: same layout with [Trigger]/{trigger}, [Why it fails]/{why_it_
    fails}, [Do instead] + numbered {do_instead}, [Do not use when]/{not_
    applicable_when}. The renderer numbers the checklist itself."""

def validate_skill_sections(mode: str, sections: Dict[str, Any]) -> None:
    """Field-level validation: parsed is a dict (extract_json can return a
    top-level array); required keys per mode present; every str field
    non-empty; the checklist field (steps / do_instead) a non-empty list of
    strings, each stripped of a leading self-numbering ("1. ", "1) ")
    and then non-empty. String fields and checklist items must contain no
    embedded newlines and must not START WITH any section marker: a forged
    marker at a line start would make skill_markers read a legal skill as
    None and silently disarm the rewrite/K5 marker checks. Extra keys are
    ignored (lenient read, gen_exam precedent) but the checklist type check
    is never skipped. Raises ModelOutputError otherwise."""
```

`write_skill` 的变化：
- **前提守卫**（置于任何模型调用前，零成本；mode 语义级措辞，无 strong/weak 字样，
  与现有 mode 校验同一先例抛 `ValueError`，stage 3 的 run_units 将其作为致命错误
  立即停批——旧规则 + 新 prompt 的组合从"静默失真"变为"响亮拒绝"）：

  ```python
  if mode == "distill" and subtask.success is not True:
      raise ValueError(
          'write_skill(mode="distill") requires subtask.success is True '
          f"(procedure material), got success={subtask.success!r} on "
          f"{subtask.subtask_id}")
  if mode == "diagnose" and subtask.success is not False:
      raise ValueError(
          'write_skill(mode="diagnose") requires subtask.success is False '
          f"(diagnosis material), got success={subtask.success!r} on "
          f"{subtask.subtask_id}")
  ```
- `call_model(..., output_json=True)`，先 `isinstance(parsed, dict)` 再按 mode 取
  JSON 契约做 `validate_skill_sections`；
- 字段校验失败**直接终态**（与 `gen_exam` 同先例，call_model 的 3 轮格式重试只
  兜 JSON 解析，不兜字段错误；不加块内重试——K2/K4 的重试都在组合层，stage 3
  无中间组合层；上线前干跑量测终态率，见 §4.2）；
- `render_skill_content` 渲染成标记文本存入 `Skill.content`（**单一表示**：注入、
  G7 相似度、G8 合并、D8 审计读的都是 content，零额外改动）；
- `mode` 判定与传参不变（stage 3 的 `distill if source=="strong" else diagnose`；
  docstring 注明 mode→结构映射，第三种 skill 类型出现时此映射是扩展点）；
- 删除 DISTILL 分支里的 `"success": str(subtask.success).lower()` 变量。

`rewrite_skill` 的变化（连带）：解析成功后，`markers = skill_markers(skill.content)`；
`markers is not None` 时要求 `skill_markers(skill_text) == markers`（**集合相等**，
不是子串包含），不合格抛 `ModelOutputError`（K4 已有该错误的处置路径）；
`markers is None`（原 skill 无标记，纯 v2 run 内不可达，test/foreign-content
tolerance）则放行不校验。

### 3.4 `src/expedistill/prompts/curate.py` — SIMILARITY 与 MERGE 增补（阶段 5 连带）

- **SIMILARITY**（G7）加一句，**同时覆盖 similar_groups 与 conflicts 两组字段**：
  "Only group or pair skills of the same kind: procedure skills (whose
  content contains the '[When to use]' section header) with procedure
  skills, failure references (whose content contains the '[Trigger]'
  section header) with failure references -- in similar_groups AND in
  conflicts."（措辞用 "contains the section header"：G7 listing 是
  "- <skill_id>: <content>" 格式，条目并不以标记开头。）
- **MERGE**（G8）加两句："Preserve the bracketed section structure of the
  source skills: the merged skill carries the same four sections, merged
  section by section. Reproduce the bracketed section headers exactly as
  written, each on its own line."（第二句防前言/加粗变体让行首锚定的
  skill_markers 误判 None。）

### 3.5 `src/expedistill/composites/curation.py` — K5 同构校验（层 5 → 层 4 import）

`blocks/curate.py` 不能 import `blocks/author.py`（blocks 互不 import），校验落
组合层（评审分歧的裁决：R1 主张块内，R2/R3 指出分层约束；预检前置满足 R1 的省
调用诉求）：

- **预检**（`merge_skills` 调用前，零模型调用）：取全组来源的 `skill_markers`
  ——全部 `None` → 跳过校验且**后检同样跳过**（test/foreign-content
  tolerance，现有 composites 测试的散文 fixture 因此全数存活；散文进散文出）；
  集合一致 → 放行进入合并；**不一致**（procedure 混 failure-reference，或
  有标记 混 无标记）→ 抛
  `ModelOutputError("merge group mixes skill kinds: ...")` 记因跳过。
- **抛错落点（二轮评审钉死）**：预检/后检的 raise 必须落在 `unit_worker`
  现有把 `ModelOutputError` 转成 skip outcome 的 try 作用域内（curation.py
  `if merged is None:` 的 except）——放在 try 外会被 run_units 当致命错误
  停掉整个阶段 5，且日志重放同一 G7 输出会让续跑永久卡死；skip 记录用预检
  自己的 reason 文案，不沿用 "G8 merge failed:" 前缀（G8 根本没跑）。
- **后检**（合并返回后、`store.save` 前）：来源有标记集合时，
  `skill_markers(merged_text)` 必须等于来源集合，不合格抛 `ModelOutputError`
  → 既有 skip 路径；来源全 `None` 时跳过（见预检）。
- 复用分支（resume 的 `reuse.pop`）：落盘前已校验过，不重复校验（docstring 注明）。

### 3.6 `src/expedistill/core/config.py` — DEFAULT_RULES 切到 v2

```python
DEFAULT_RULES = {
    "task_select": "any_weak_failed",
    "segment_select": "strong_success_weak_failed",
    "trajectory_select": "all",
}
```

- 理由（初稿论据已按评审更正）：三个正式系列配置（tb21 × 2、alfworld/searchqa
  skillopt）都显式写规则名，默认值即方法正典，切默认是宣示而非迁移；
- **有意识接受的后果**：`pilot_alfworld.yaml` 与 `pilot_searchqa.yaml` 省略 rules
  块、靠默认补齐——切默认后这两个配置的语义随之变为 v2；其旧 run 续跑将被
  manifest 逐字段比对拒绝（与新实验纪律一致）。AGENTS 六.2 禁止修改已启动配置，
  不回填，在决策文档记录即止。同理受影响的还有两个 smoke 内联配置（二轮审计
  补全）：`scripts/smoke/smoke_tb_distill_chain.py` 的 CONFIG_YAML 无 rules 块，
  其 segment_select 语义随之变 v2——smoke 本就该演练当前协议，接受；
  `scripts/smoke/smoke_stage01_searchqa.py` 的内联配置在 n=1 下 any_weak_failed
  与 all_valid_failed 行为重合，无实际变化。

### 3.7 `src/expedistill/stages/stage1_baseline.py` — 模块 docstring 同步

现文"shortfalls … stay out of F"句改为如实描述 v2：部分采样（0 < valid <
n_attempts）但有真实有效失败的题**进 F 且仍在 infra_shortfalls 清单**，两表不再
构造性不相交；零有效尝试的题仍只进 shortfall 清单。仅 docstring，无代码变化
（F 的计算本来就是配置规则驱动）。

### 3.8 `src/expedistill/llm/call.py` + `tests/unit/llm/test_call.py` — 注释更新

三处注释把 `SKILL_DISTILL` 列为 plain-text caller（call.py 的 bounded-re-ask 注
释段与 docstring、test_call.py 的用法注释），v2 后失效。更正（二轮评审）：
REWRITE 自迁移起就是 `output_json=True` 的 JSON caller，v2 后方法级 plain-text
caller 只剩 MERGE——注释例证改为 "SKILL_DISTILL 等为 JSON caller，MERGE 保留
plain-text 例证"。行为无变化。

### 3.9 配置 — 新实验配置与生成器

- **`configs/experiments/alfworld_skillopt_split_v2.yaml`**：复制
  `alfworld_skillopt_split.yaml`，改 `task_select: any_weak_failed` 与
  `segment_select: strong_success_weak_failed`，头部注明协议 v2、日期、本文档
  引用；顺手修正复制的 `trajectory_select` 过时注释（"succeeded twice and the
  weak failed twice" 是配额排序的误述）。
- **`scripts/run/build_skillopt_split_configs.py`**：硬编码的 v1 规则名改为 v2
  （防止日后重新生成静默回退 v1），脚本头注明协议版本；**`CONFIG_PATHS` 一并切
  到 `*_v2.yaml`**（二轮评审 major：只改规则名不改路径，默认模式会拿 v2 内容
  静默覆写冻结的 v1 配置、`--check` 模式必 FATAL）——v1 路径从脚本退役，生成
  器直接产出 v2 配置（头部含协议注记，与手工复制件字节比对自洽），**不**重新
  生成/覆盖任何 v1 配置文件（它们是历史记录）。
- tb21 系列配置不动（已完结实验的冻结记录）；searchqa 的 v2 配置轮到该实验时
  同法 fork（`searchqa_skillopt_split.yaml` 现仍指 v1 规则名，属预期）。
- `weak_profile` 现文已按能力边界写（四条具体弱点 + "short ordered steps with
  explicit triggers and exact ALFWorld command forms"），与 v2 写作纪律一致，
  不改。
- **bank_token_budget 预登记**（评审新增）：`bank_token_budget: 96000` 保持为
  溢出安全阀；四节结构会拉长单条 skill、F 扩大会增加候选，预算可能先于合并不
  足——预登记的处置是"curation 提前结束并如实报告，可接受结局"，不为塞进库而
  调大预算；单条长度与预估库体积在上线前干跑量测（§4.2）。

### 3.10 `README.md` — 设计正本同步（精确落点）

- **1.3 积木表**：D4 行（"阶段 1 的规则是'有效采样数达到 N 且全部失败'"）改为
  规则名引用 + v2 语义；D6 行（"当前规则：强模型段全要，弱模型段只要 success =
  false"）改为 v2 语义；G2 行（"提炼用于强模型段（成功段提炼做法、失败段提炼教
  训）"）改为 procedure / failure-reference 双写法与四节结构；旧规则名标注
  "v1；与 v2 G2 prompt 不兼容"。
- **阶段 0 节**：逐字列出的三个默认规则名行改为 v2 名。
- **阶段 1 节**：F 定义（"失败题集合 F = 弱模型有效采样全部失败的题……只要有一
  次做出来，该题就不进入后续流程"）改为"由配置的 task_select 规则决定（v2 �
  认 any_weak_failed：任一有效尝试失败即入 F）"，并写明"部分 infra + 有失败
  → 进 F 且仍在 shortfall 清单"。
- **阶段 2 节**："trajectory_success：弱模型轨迹都来自 F，恒为 false"句改为
  如实描述（v2 下混合题的弱成功轨迹可经配额补位入切，`trajectory_success` 不再
  恒 false；已核实无代码依赖）；"待定"段（轨迹挑选规则）标注已由
  `strong2_weak2` 落地。
- **阶段 3 节**：D6 v2 语义、G2 两种写法的四节结构契约（JSON 输出 + 确定性渲
  染 + 字段校验 + write_skill 前提守卫）。
- **阶段 4 节**：G6 改写保持节结构（保结构落点按"每个阶段标出用到的积木"的
  纪律分节写——二轮评审修正）。
- **阶段 5 节**：G7 同类分组（含 conflicts）与 G8 逐节合并、节头原样复现。

### 3.11 测试（评审逐文件核实后的精确清单）

**需改**：
- `tests/unit/prompts/test_templates.py`：EXPECTED 变量表同步（DISTILL 去
  success、DIAGNOSE 不变）；钉死两侧（二轮修正：G2 模板不含方括号标记，标记由
  渲染器加）——REWRITE/MERGE/SIMILARITY 模板文本含节标记字面量（含
  SIMILARITY 增补句里的两处）、两个 G2 模板的 JSON 契约字段名 ==
  `validate_skill_sections` 所需键集；字段名↔节标记的绑定由 render↔markers
  往返测试覆盖；
- `tests/unit/core/test_config.py`：`TestRuleDefaults` 断言 v2 名；
- `tests/unit/blocks/test_rules.py`：`any_weak_failed`（全成功不进 / 全 infra
  不进 / 混合进 / 全失败进 / **部分 infra+有失败 → 进**）、
  `strong_success_weak_failed`（四象限）；
- `tests/unit/blocks/test_author.py`：write_skill 用例按 v2 契约重写（现 4 例
  扩展为目标清单：合法、非 dict、缺字段、steps 空列表、steps 项自带编号被剥
  离、**前提守卫两例**）；`render_skill_content` 逐行断言；`skill_markers`
  （精确语义：完整集、部分集、混合集、行首锚定）；`render → skill_markers`
  往返识别；`rewrite_skill` 标记保持（含集合相等、无标记放行）；
- `tests/unit/stages/test_stage1.py`：fixture 规则名/期望 F 随新语义；
- `tests/unit/stages/test_stage2.py`：仅规则名引用处（fake 是 G1 SEGMENT 输出，
  与 skill 无关）；
- `tests/unit/stages/test_stage3.py`：fake author 输出改合法 JSON、期望 content
  改渲染文本（否则全红）；
- `tests/unit/stages/test_pipeline.py`：**完整 fixture 改造**（二轮修正，不止
  分发键）——`_write_config` 规则名切 v2；SEGMENT fake 的强臂段改
  `success: true`（否则强失败段被 D6 滤掉/撞守卫）；G2 两个 fake 从纯文本改
  合法 JSON 契约输出；REWRITE fake 的 `skill_text` 改保节文本；`_script` 分发
  键改为**各自独有**的稳定锚点（DISTILL 锚 "The subtask SUCCEEDED"、DIAGNOSE
  锚 "WHAT THE PRECEDING PART OF THE TRAJECTORY WAS DOING"——"SUBTASK
  LABEL" 两个模板共有，无法区分）；

**新增**：
- `tests/composites/test_curation.py`：混合类组预检 skip（零模型调用）、合并产
  物缺节后检 skip；现有散文 fixture 靠放行分支存活；
- `tests/composites/test_acceptance.py`（或 test_stage4 相应处）：rewrite 保标
  记校验一例；
- `tests/unit/inject/test_skills.py`：节标记在单行压缩后存活一例（把"无损"从
  人眼断言变成测试）；
- 坏字段冒烟用例（validate 抛错路径）。

**不动**（已核实不引用规则名与 write_skill）：test_stage4/5 本体、
test_stage2_parallel、test_stage6、test_cli、test_stage0 / test_metrics /
test_select（引用旧规则名但旧规则保留注册，仍绿）。

### 3.12 `src/expedistill/stages/stage3_generation.py` — docstring 一处

mode 分支行 docstring 补注 mode→结构映射（procedure / failure-reference），
为第三种 skill 类型留扩展点说明。无代码变化。

## 4. 实施顺序、上线前检查与验收

### 4.1 实施顺序（AGENTS §11.5：README → decisions → 代码 → log.md）

1. README 同步（§3.10，与本文档同批定稿）；
2. 代码按文件序：rules.py → prompts/author.py → blocks/author.py →
   prompts/curate.py → composites/curation.py → core/config.py →
   stage1_baseline.py docstring → llm/call.py 注释 → stage3 docstring →
   配置与生成器；
3. 测试（§3.11 清单）；
4. 全量 `pytest` + `ruff check --select E9,F` + `tests/test_layering.py` 全绿；
5. log.md 记录（设计定稿条目 + 实施条目）；
6. 提交（征用户同意后，本分支）。

### 4.2 上线前检查（新 run 启动时执行，写入 ALFWorld runbook）

1. **F 尺寸参照**（已实测）：用 TB21 `stage1_summary.json` 的逐题弱臂 metrics
   零成本重算，**新口径 F=64/89（旧口径 36/89，+78%；两路二评独立重算结果
   一致）**；ALFWorld 实测前，阶段 2–5 单元量按约 1.8 倍旧口径估算，写进
   runbook 作成本预期；
2. **ALFWorld 先 `--until-stage 1`**：看新口径 F 实际尺寸与 infra_shortfalls
   交集，确认 G1/G2/G3/K4/K5 增量可接受再放行全量；
3. **1–2 题干跑量测**（外层 AGENTS 第一节硬性要求）：单条 skill 平均 content
   长度、预估库体积 vs 96k 预算、`model_output_failed` 终态率——刻意注入一次
   坏字段（如 steps: []）确认终态路径与账目正确；终态率显著（>5%）再回来加
   "带错误重问"（那本身是新实验）；
4. **不变量检查**：干跑确认同一 subtask 的渲染输出确定性（同输入两次渲染逐位
   相同）、注入块内标记完整。

## 5. 七个开放问题的裁决记录（三方评审一致，详见评审记录）

1. **F 扩大：采纳**（基数更正为 ALFWorld train 39 题、弱臂 117 局、两臂 234
   局）；成本含全链（G1/G2/G3/K4/K5），上线前量化（§4.2）。
2. **[Verify] 强制：采纳**；措辞含防无限重试约束（"never keep retrying"）。
3. **字段校验失败直接终态：采纳**（gen_exam 先例）；干跑量测终态率，
   >5% 再议重问。
4. **未知标记宽松：采纳**；checklist 类型校验不因多余键跳过。
5. **阶段 4/5 三项连带纳入：采纳**；K5 校验落组合层 + G8 调用前预检；
   SIMILARITY 覆盖 conflicts。
6. **DEFAULT_RULES 切 v2：采纳**；论据更正 + pilot 漂移显式记录（§3.6）。
7. **trajectory_select 不收紧：采纳**；`_strong2_weak2` docstring 随之更新
   （§3.1），README"恒为 false"句修正（§3.10）。
