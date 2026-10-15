日期：2026-10-15
一句话说明：架构师 B 对多方法框架重构方案的三轮独立审计全文（第一轮独立审计、第二轮交叉讨论、第三轮收敛表决），供 docs/decisions/multi_method_framework_20261015.md 的收敛记录追溯。
对应 log.md 条目：2026-10-15 多方法框架方案定稿，建立 MM 独立副本。

# 架构师 B：三轮审计全文

分工说明：A = 代码事实与可行性，B = 扩展性与反过度设计，C = 迁移路线与可复现风险。评审基于仓库 bcb3ffe，全程只读。

---

## 第一轮：独立审计

# 独立审计 B：多方法框架重构方案 v1（主视角：可扩展性与简洁性 / 反过度设计）

审计对象：/tmp/arch_review/plan_v1.md
仓库：ExpeDistillation-TB-dev @ bcb3ffe（feature/acceptance-live-run），只读静态阅读，未运行任何测试或进程。
下文 file:line 均相对 `src/expedistill/`。

---

## 1. 总体评价

方向对，骨架可以保留：在"阶段"和"积木"之间加一层按槽位登记、按名字选取的方法；阶段只做分发；
删掉懒注册、`_MANIFEST_OPTIONAL_FIELDS`、`METHOD_PROMPT_NAMES` 快照这三处冻结补丁；不给方法加 hash 或版本号。
这几条都符合"框架整洁第一"。

问题出在接口层，有三类：

- **按方案自己的标准算过度设计**：`needs/provides` 是一套通用机制，实际只服务"考题"这一条边；
  `Method` 基类带 6 个 ClassVar，每个槽位还要一个子类和一个类型化的 Summary；`extends:` 用深合并。
- **声明可能失真**：`prompts` 由方法声明，但 prompt 名其实写死在积木里，声明和实际调用会悄悄不一致，正好绕开冻结。
  冻结公式还漏了基础件自己的 prompt。`roles` 用元组表达，丢掉了"默认用哪个模型"这条信息。
- **低估了跨槽位的隐式协议**：skill 正文的结构标记、skill 与 subtask 一一对应、bank 行格式、
  `assemble_reuse` 的身份白名单、`core` 层没法校验方法参数的分层冲突。

做"加一种方法"的思想实验，结果是：curation=none 和新模型 provider 确实不碰核心逻辑；
authoring 新 prompt 策略和 embedding 交付按方案原样做都会碰到核心。
最大的单项遗漏是：每个方法都在重复写"逐单元失败处置"这段样板，方案的 P0 没有消它。

---

## 2. 思想实验

### (a) 新增一种 prompt 策略的 authoring 方法（如 `distill_contrastive`，skill 结构不变）

| 文件 | 改动 | 是否碰核心 |
|---|---|---|
| `prompts/author.py` | 新增 PromptSpec | 否（prompt 规范本来就要求这样做） |
| `blocks/author.py:319-336` | `write_skill` 按 mode 写死了 prompt 名和变量（`SKILL_DISTILL`/`SKILL_DIAGNOSE`），新策略只能加分支或新函数 | **是**：要改一块和现有方法共用的积木 |
| `methods/authoring/contrastive.py` | 新方法（Params、prompts、roles、run） | 否 |
| `methods/__init__.py` | 加一行 import（不做自动发现，所以必须有） | 否 |

如果新策略同时改变了 skill 结构（新的段落标记），还要改：

- `core/models.py` 的标记集合（第 0 层）；
- `blocks/author.py:79` 的 `SKILL_SECTION_FIELDS`，以及 render/validate；
- `composites/curation.py:712-750` 的"种类冲突"检查；
- `blocks/retrieve.py:112` 按标记分节的逻辑；
- README 第二节。

如果新策略产出的 skill 不再和 subtask 一一对应（例如一条 skill 来自多个段），以下几处会同时失效，方案没有提及：

- ID 规则 `make_skill_id(subtask_id)`；
- 考题的唯一关联 `make_exam_id(skill.subtask_id)`（`stages/stage4_acceptance.py:207`）；
- live_run 按任务分组的 `_task_of_skill`（`composites/live_acceptance.py:365-369`）；
- 策展追溯原始来源的 `_original_sources`（`composites/curation.py:659`）。

**评估**：只换 prompt 的情况，前提是积木把 prompt 名作为参数传入（见第 5 节建议），这样只改 3 个文件、不碰核心；
按方案原样做要改共用积木。要换结构或换粒度的情况，是跨槽位的协议变更，方案把这类改动的范围低估了。
另外，方案的槽位表把 authoring 标为"高"，迁移路线却放在 P4（"第二实现出现再迁"）。
用户的目标恰好就是 prompt 策略族，第二实现就是它，应该前移。

### (b) 新增 curation=none（消融用）

| 文件 | 改动 |
|---|---|
| `methods/curation/none.py` | 把 accepted 的 skill 按 skill_id 顺序经 `store.append_bank`（`infra/store.py:365`）写入 bank；续跑时 bank 已存在就校验一致后跳过（`append_bank` 不幂等）；可选参数 `token_budget: Optional[int]`（null = 只测量） |
| `methods/__init__.py` | 一行 import |

隐含依赖检查：

- bank 行就是 `Skill.to_dict()`，`group=None` 的情况由 `inject/skills.py:93-96` 和 `blocks/retrieve.py:255` 正确处理；
- 不需要考题，按方案得保证 none 不声明 `needs={"exam"}`；按我的建议则什么都不用声明。

**评估**：真正隔离，2 个文件。现在之所以做不了，原因是 `stages/stage5_curation.py:63-74` 写死了 `curate_bank`，
加上顶层的 `bank_token_budget`/`curation_rounds` 字段（`core/config.py:470-471`）。方案能解决这个问题。

### (c) 新增基于 embedding 的 delivery

| 文件 | 改动 | 是否碰核心 |
|---|---|---|
| `methods/delivery/embedding.py` | 输入 (bank, tasks)，输出 `skills_for_task` 解析器加审计行；bank 向量缓存在自己的工作目录 | 否 |
| `llm/client.py:185` 和 `llm/` 新增 embed 调用 | 现有客户端只有 chat；README 1.1 规则 4 要求所有模型调用都走 B1（记账、基础设施重试、请求落盘），但 `call_model` 绑定"prompt 名 + 输出格式"，embedding 套不进去 | **是**（第 2 层，一次性补齐） |
| 角色表 | 需要新角色 `embedder`：①`MODEL_ROLES` 是封闭元组（`core/config.py:83`）；②"未配置的角色默认用强模型"（`core/config.py:567`）对 embedding 模型是错的，必须显式配置；③`_build_handles` 给每个角色都建 chat handle，并按角色取 `max_output_tokens_for`（`stages/pipeline.py:151,170,187`），对 embedding 没有意义 | **是**（方案的 `roles: tuple` 表达不了"无默认、必须显式配置"） |
| 检索查询文本 | TB 的 `Task.instruction` 只是任务名（`benchmarks/tb/plugin.py:450`；`docs/decisions/retrieval_delivery_tb_split_20261013.md` 第 2 条），对任务名做 embedding 没有意义；需要插件接口新增一项"取真实指令文本" | **是**（插件接口 `benchmarks/base.py`） |

**评估**：方法文件本身是隔离的。好消息是，K1 已经有现成的交付接口：`skills_for_task(task) -> (refs, block)`，
stage6 的检索分支（`stages/stage6_test.py:435-442`）和 live_run（`stages/stage4_live_run.py:420`）都在用。
所以 delivery 槽位的签名应该直接复用它，不要另起炉灶。
但"非 chat 模型调用"和"插件提供查询文本"是两处一次性的基础设施改动，方案的"模型接入：低/P5"低估了这一点。

### (d) 接入新的模型 provider

| 文件 | 改动 |
|---|---|
| `llm/<provider>_client.py` | 实现 `complete()`，返回 `CompletionResult`（`llm/client.py:60`），统一用量字段；把错误归类为 Infrastructure/ModelOutput/ProviderRefusal（参照 `llm/client.py:50` 的 `_status_is_permanent`） |
| `stages/pipeline.py:99-111` | `_default_client` 现在固定用 `OpenAICompatClient.from_env`，改成按"模型名 → provider"表选客户端。这张表是运行设置（端点、密钥），不进 manifest；模型名已经冻结 |
| `core/config.py:154` | `MODEL_OPTIONS_RESERVED_KEYS` 是按 OpenAI chat 形状写的，新 provider 可能需要复查 |
| TB 执行臂 | 模型调用在远端 Harbor，靠 TBENCH_* 环境变量控制（`stages/pipeline.py` 第 138 行左右的 docstring），不走 llm/；要让执行臂用新 provider，得改 `benchmarks/tb/` |

**评估**：和方法框架完全正交，方法代码一行都不用改。一个两条目的注册表（或一个 if）就够，"P5 需要时再做"合理。
注意不要把 provider 放进方法的 `roles` 声明。

---

## 3. 缺陷与过度设计（按严重度排序）

### 高-1：prompt 冻结集"= 选中方法声明的并集 + 任务 prompt"既不完整，也会失真

- **公式漏项**：`DEFAULT_SYSTEM`/`REPAIR_INSTRUCTION` 是 B1 自己用的（`llm/call.py:46-47`），每次调用都会用到，不属于任何方法。
  segmentation 的 `SEGMENT`、考题验收用的 `LEAK_AUDIT_JUDGE` 在单实现槽位迁移之前也没有主人。
- **失真**：prompt 名写死在积木里，方法只是间接调用：
  - `blocks/author.py:320,328,390,451`
  - `blocks/exam.py:47,75`
  - `blocks/audit.py:55`
  - `blocks/curate.py:123,171,197`
  - `blocks/attribution.py:133,239`

  方法层手写的 `prompts` 元组和真实调用集之间没有任何机制保证一致。漏写一个 prompt，就意味着这个 prompt 改了字也不会被冻结发现，正好是冻结要防的事。
- **修改建议**：
  1. 冻结集 = 基础件 prompt（固定集合）+ 任务 prompt + 选中方法声明的并集；
  2. 每个组合积木或积木文件导出自己用到的 prompt 名常量（如 `exam_once.PROMPTS = ("ANSWER","JUDGE")`），方法的声明写成这些常量的并集，而不是手写字符串；
  3. 加**一个**通用契约测试：用假模型跑每个方法，记录实际调到的 prompt 名，断言它是声明集合的子集。

  这样声明保持诚实，也不需要任何运行时 hash 或审计。
- **需要先定的政策**：方案第 5 节说"老 run 用老 checkout 续跑"。如果严格执行这条，其实可以直接冻结**全部**已注册 prompt，`prompts` 声明都可以不要，这是最简方案。
  按方法声明的意义只在于"新代码可以续跑旧 run"（懒注册当初正是为了这个）。请明确选一个，不要两套同时存在。
  我的建议：保留声明（这是实际工作流的需要），配上上面的契约测试。

### 高-2：分层冲突，方法参数没法在 core 里校验

- `core/config.py` 是第 0 层，方法的 Params 在第 6 层，所以方案说的"把 `_validate_acceptance`/`_validate_skill_delivery` 搬进方法"意味着配置要分两步解析。
  `assemble_reuse`（第 5 层）重新推导了 stage0 的 manifest（`composites/assemble_reuse.py:290-313` 和 `_canonical`/`_tasks_digest`），以后也需要方法注册表，而它往上 import 违反分层。
- **修改建议**：
  1. core 只做**形状**规范化：`methods: Dict[str, Dict]`，字符串写法规范化为 `{"name": ...}`；
  2. 由第 6 层以上的 `resolve_methods(config)` 统一完成"取注册项、构造 Params、补全默认值、生成冻结 dict"；
  3. stage0 里抽出一个纯函数 `build_manifest(config, tasks) -> dict`，stage0 和 reuse 工具共用；
  4. `assemble_reuse` 上移到 stages 或 cli 一侧（它本来就是一个工具，不是被各方法复用的组合积木）。这样也顺带消掉了一份复制的冻结逻辑。

### 中高-3：`needs/provides` 用一套通用机制服务一条边，属于过度设计

- 全仓库只有一条跨阶段产物依赖：考题，由 stage3 无条件生成（`stages/stage3_generation.py:288-290`），被 K4 和 K5 回归（`composites/curation.py:687`）使用。
  方案自己定的原则是"第二个实现出现才建接口"，这里应该同样适用。
- 考题只由 subtask 和视图决定（`blocks/author.py:369` 的 `gen_exam` docstring："written from the subtask only, never from a skill"），ID 也是确定的。
- **修改建议**：删掉 `needs/provides` 和启动时的集合检查。在 composites 加一个 `ensure_exam(store, subtask, view, examiner)`：已存在就读，否则生成并保存。
  考题验收和 agentbrew 策展各自调用它，authoring 不再生成考题。这样隐式契约就消失了，不需要声明。
  而且在 live_run 加策展的组合下，只给参与合并的原始 skill 出考题，比方案的"有人需要就全量生成"更省钱。
- 代价：考题模式下考题文件从 stage3 挪到 stage4，产物不再逐字节相同，所以放在 schema v3 一并做。

### 中-4：`roles: tuple` 丢了默认值语义

- 现在的规则是"角色默认用强模型，answerer 默认用弱模型"（`core/config.py:567-568`），embedding 一类的角色则必须显式配置。
  开放角色表之后，还要同步修改 `token_budgets.roles` 的校验（`core/config.py:416`）和 `max_output_tokens_for`（`core/config.py:641`），以及 `_build_handles` 遍历的角色集合（`stages/pipeline.py:151,187`）。
- **修改建议**：`roles: Mapping[str, Optional[str]]`，值是默认锚点 `"strong"|"weak"|None`（None 表示必须显式配置）。
  合法角色 = 执行者两个锚点 + 选中方法 roles 的并集；配置里出现不在并集里的角色就报错（能抓拼写错误）。
  `MethodContext.handle(role)` 拒绝未声明的角色，声明在运行时自动被验证。

### 中-5：`Method` 基类 + 每槽位子类 + 类型化 Summary，比需要的重

- `slot`/`name` 这两个 ClassVar 和注册表的键重复；`needs/provides` 见第 3 条；`AcceptanceSummary` 这类类型化摘要会把各方法不同的摘要字段（考题模式的 `skipped_missing_exam`、live_run 的轮次明细）强行塞进一个 schema。
- 积木规范本来就是"一个函数"（AGENTS.md 第四节"积木规范"第 1 条）。
- **修改建议**：注册项 = 函数 + 元数据（见第 5 节）；摘要就是 dict，各槽位只约定 2-3 个公共键，其余由方法自己定。

### 中-6：`extends:` 深合并有陷阱，而且给 core 加了加载特性

- 例子：基础配置是 `acceptance: {name: live_run, n_attempts: 3, ...}`，覆盖片段是 `acceptance: {name: exam}`，
  深合并后得到 `{name: exam, n_attempts: 3, ...}`。如果两个方法恰好有同名参数（如 `top_k`），旧参数会被悄悄继承。
- **修改建议**：v3 先不做 `extends`，消融矩阵由 `scripts/` 下的生成脚本产出完整的 YAML，这样每个 run 的配置自包含，冻结的就是所见。
  如果一定要做，只支持一层继承，并且 `methods.<slot>` 整体替换，不做深合并。

### 中-7：authoring 槽位的签名和优先级

- 方案的签名"段 + 视图 → 候选 skill"是逐段的，堵住了对比式、多段合成这类策略。
- **修改建议**：签名改为 `run(ctx, params, subtasks: list[Subtask]) -> dict`，由方法自己决定迭代方式和视图；
  把 skill 与 subtask 一一对应写成槽位协议（或明确说明不支持）；迁移提到 P2。

### 低中-8：P1 的"产物逐字节相同"与 `ctx.workspace() → run_dir/<slot>/<name>/` 冲突

- 现有目录：`stage4_acceptance/`、`stage6_test/retrieval_audit.jsonl` 等；`scripts/analysis`、`scripts/audit` 和 `assemble_reuse` 的白名单都按这些路径读。
  `LIVE_DIR_NAME = "stage4_acceptance/live_run"`（`composites/live_acceptance.py:125`）其实已经是"阶段目录/方法名"的形式。
- **修改建议**：`workspace()` 返回 `run_dir/<现有阶段目录>/<方法名>`；已有方法保留现有路径，只有新方法用子目录。

### 低-9：`MethodContext.config` 暴露整份配置

- 方法可以读到别的槽位的参数，等于把 if 分支换了个地方重新长出来。
- **修改建议**：ctx 只暴露实验级字段（benchmark、mode、weak_profile、两个执行者锚点），方法参数只通过 `params` 传入。

### 低-10：Params "通用类型校验"容易越写越大

- **修改建议**：只用 `dataclass(**kw)` 处理未知键和缺失键（捕获 TypeError，转成 ConfigError 并带上槽位名）；
  类型检查只支持 int/float/str/bool/Optional，重点是 bool 不能当 int 用、int 可以当 float 用；其余约束都放在 `__post_init__`。
  不引入 pydantic（core 只依赖标准库，Python 版本 3.9-3.11）。

### 低-11：把 rules 并进 `methods:`

- 三条具名规则已经有统一的注册表（`blocks/rules.py:68-71`），没有参数。并进 methods 会模糊"方法 = 名字 + Params"这个概念。
- **修改建议**：保留 `rules:` 段不变，迁移成本最低。如果要合并，就明确写出"规则是无参方法"。

---

## 4. 遗漏点

1. **逐单元失败处置样板**：ModelOutputError 终结并写一次账本，InfrastructureError 留待续跑、最后统一抛出。这段逻辑在以下位置各写了一遍：
   - `stages/stage3_generation.py:291-305`
   - `stages/stage4_acceptance.py:235-243`
   - stage2
   - stage4_live_run

   这是新方法最大的样板来源，也最容易写错续跑语义。P0 应该抽出一个 `run_units_disposed(ctx, label, units, fn)`，返回分桶结果，结束时抛出基础设施错误。方案 P0 只列了 `_workers` 等小函数。
2. **skill 结构（种类）协议**：涉及 `core/models.py` 的标记、`blocks/author.py:79`、`composites/curation.py:712-750`、`blocks/retrieve.py:112`。这是 authoring、curation、delivery 三个槽位之间实际存在的协议，方案只提到了 `Skill.status`。
3. **skill 与 subtask 一一对应、ID 规则**：这是跨槽位不变量，相关位置：
   - `composites/live_acceptance.py:365`
   - `composites/curation.py:659`
   - 考题关联 `make_exam_id(skill.subtask_id)`（`stages/stage4_acceptance.py:207`）
4. **bank 行格式**：bank 行就是 `Skill.to_dict()`，经 `append_bank` 写入。这是 curation 和 delivery 之间的协议，应该由一个共享写函数承担，否则 curation=none 这类新实现要自己猜格式。
5. **`assemble_reuse` 在新框架里的定位**：
   - 现有的人工白名单 `_ALLOWED_DIFF_KEYS`（`composites/assemble_reuse.py:118`）可以改成按槽位顺序推导："复用到第 k 个槽位，就钉住第 1 到 k 个槽位的配置，其余放开"；
   - 拷贝白名单也可以按槽位工作目录推导；
   - 这比现在的方案更简单。
6. **非 chat 模型调用和插件"查询文本"接口**：见思想实验 (c)。
7. **注册入口**：不做自动发现，所以需要 `methods/__init__.py` 的显式 import 清单；同时要更新 `tests/test_layering.py:22` 的层号表和 AGENTS.md 第四节的分层表。
8. **manifest 去重**：
   - 顶层的 `prompt_digests`/`rules`/`model_roles` 和 `config.*` 重复（`stages/stage0_freeze.py:342-345`）；
   - `config.prompts_digests` 是 stage0 回填的字段，不属于配置（`core/config.py:500`）；
   - v3 一并删掉。
9. **AGENTS.md 第七节"扩展清单"**：缺"新增一种方法"这一条（改哪些文件、契约测试怎么写、"改行为就注册新名字"）。
   方案没有给方法加版本号，所以"改行为 = 新名字"只是约定，必须写进清单，靠评审保证。
10. **不属于槽位的部分**：stage1 基线和 stage6 评测协议是固定的；live_run 的注入用的是"本任务的 skill 组"，不走 delivery 槽位。这两点应写明，避免以后有人误以为它们可替换。

---

## 5. 建议的修订

### 5.1 注册项：函数 + 元数据，不要类层次

```python
# methods/registry.py  (layer 6)
@dataclass(frozen=True)
class MethodSpec:
    run: Callable[..., Dict[str, Any]]           # 槽位约定签名，见下
    Params: type                                 # dataclass；无参数用 NoParams
    prompts: Tuple[str, ...] = ()                # 组合积木导出常量的并集
    roles: Mapping[str, Optional[str]] = field(default_factory=dict)  # role -> "strong"/"weak"/None(必须显式)

SLOTS = {s: make_registry(f"{s} method") for s in
         ("segmentation", "authoring", "acceptance", "curation", "delivery")}
DEFAULTS = {"segmentation": "llm_segment", "authoring": "distill_diagnose",
            "acceptance": "exam", "curation": "agentbrew_curator",
            "delivery": "whole_bank"}

def method(slot, name, *, Params=NoParams, prompts=(), roles=None):
    def deco(fn):
        SLOTS[slot].register(name)(MethodSpec(fn, Params, tuple(prompts), roles or {}))
        return fn
    return deco
```

各槽位签名，写在 registry 的 docstring 里，不建基类：

```python
segmentation(ctx, p, trajectories) -> dict
authoring   (ctx, p, subtasks)     -> dict     # 方法自己迭代
acceptance  (ctx, p, candidates)   -> dict     # 公共键: accepted, discarded
curation    (ctx, p, accepted)     -> dict     # 公共键: members, bank_reference
delivery    (ctx, p, bank, tasks)  -> (skills_for_task, audit_rows)   # 直接复用 K1 现有解析器形状
```

示例：

```python
@dataclass(frozen=True)
class LiveRunParams:
    n_attempts: int
    improvement: str                      # 无默认：预注册
    max_injection_tokens: Optional[int] = None
    def __post_init__(self):
        if self.n_attempts < 2: raise ConfigError("n_attempts >= 2")
        get_acceptance_improve(self.improvement)   # 启动即失败

@method("acceptance", "live_run", Params=LiveRunParams,
        prompts=K6_PROMPTS,                       # composites/live_acceptance 导出
        roles={"attributor": "strong", "reviser": "strong"})
def live_run(ctx, p, candidates): ...
```

### 5.2 MethodContext 取代动态挂属性（`stages/pipeline.py:363-365`）

```python
@dataclass(frozen=True)
class RunSettings:            # 不进 manifest
    workers: int; k1_stagger: float; allow_infra_shortfall: bool

@dataclass(frozen=True)
class MethodContext:
    run_dir: Path; store: Store; usage: UsageRecorder; plugin: BenchmarkPlugin
    experiment: ExperimentView        # benchmark/mode/weak_profile/执行者锚点，不含 methods
    settings: RunSettings
    _handles: Mapping[str, ModelHandle]; _allowed_roles: FrozenSet[str]; _workspace: Path
    def handle(self, role): ...       # 未声明角色 -> CodeBugError
    def workspace(self) -> Path: ...  # run_dir/<现有阶段目录>[/<新方法名>]
```

### 5.3 配置（schema v3，一次性定形，所有槽位都写出；单实现的槽位只有一个注册项，不建接口）

```yaml
schema_version: 3
benchmark: tb
mode: split
models: {executor_strong: A, executor_weak: B}   # 其余角色按方法声明的默认补全
rules:                                           # 保持现状
  task_select: any_weak_failed
  trajectory_select: strong2_weak2
  segment_select: strong_success_weak_failed
methods:
  segmentation: {name: llm_segment, K: 5, retry_limit: 2}
  authoring: distill_diagnose                    # 字符串 = 名字 + 默认参数
  acceptance: {name: live_run, n_attempts: 3, improvement: strict_rate_gain}
  curation:   {name: agentbrew_curator, token_budget: 96000, rounds: 3}
  delivery:   {name: retrieval, top_k: 5, max_categories: 3, min_score: 0.02}
```

- core 只做形状规范化（字符串转成 `{"name": ...}`，未知槽位报错）；
- `resolve_methods()` 放在第 6 层以上，做补全和校验；
- 冻结形式是 `{"name": n, **asdict(params)}`，缺省的槽位按 `DEFAULTS` 补全后冻结；
- 顶层的 `K`、`segment_retry_limit`、`retry_limit`（只给考题验收用，`stages/stage4_acceptance.py:231`）、`bank_token_budget`、`curation_rounds`、`skill_delivery`、`acceptance` 一律搬进对应方法的 Params；
- 不做 `extends`。

### 5.4 冻结与防腐的定位（三档，框架只管第一档）

| 档 | 机制 | 处置 |
|---|---|---|
| 身份冻结 | `infra/manifest.py:54-69` 逐字段比对；prompt digest；tasks_digest | **保留**，全框架只此一处。manifest v3 = {config（补全后，含 methods）, prompt_digests, tasks_digest, split, aux_metric}，去掉重复字段。prompt 集 = 基础件 + 任务 prompt + 选中方法声明，靠契约测试保证声明诚实。删掉懒注册、`METHOD_PROMPT_NAMES`（`prompts/__init__.py:25`）、`_MANIFEST_OPTIONAL_FIELDS`（`core/config.py:131`） |
| 续跑幂等 | live_run 的版本号守卫（`composites/live_acceptance.py:33-35,1013-1027`）、"首行生效"账本（`_first_row` 264 行）、stage3 的 `record_once`、curation 的日志 | **这是续跑机制，不是审计**，留在方法内部。只抽一个共享原语放进 infra/store（`append_once(path, key, row)` / `first_row(path, key)`），新方法照着用即可 |
| 防篡改自检 | verdict 重算比对（`composites/live_acceptance.py:26-29`）、injection_audit 逐字节比对（`stages/stage4_live_run.py:72-78`）、`_check_store_matches_log`/`_check_bank_not_stale`（`composites/curation.py:838,868`） | 框架**不要求**新方法提供。现有的可以留作廉价断言，不再扩展；系统性审计放到 `scripts/audit/`。框架层面的保证由每个槽位的参数化契约测试承担：假模型跑通、中断续跑结果等价、Params 未知键被拒、实际调用的 prompt ⊆ 声明 |

### 5.5 迁移路线微调

- **P0**：RunSettings/MethodContext；消除重复（加上 `run_units_disposed`、`append_once`/`first_row`、`build_manifest` 纯函数）。
- **P1**：一次性定下 v3 配置形状，建立注册表；acceptance 和 delivery 先迁（delivery 直接复用 `skills_for_task`）。
- **P2**：authoring（prompt 名作为积木参数）、curation（补 none）、`ensure_exam` 取代 needs/provides。
- **P3**：segmentation 只登记、不抽象；`assemble_reuse` 上移，白名单改为按槽位推导。
- **P5**：provider 和非 chat 调用，等真有需求时再做。

---

## 第二轮：交叉讨论（提纲见决策文档附录 A）

# 第二轮：架构师 B 的立场

通读了 review_A、review_C 和 brief，并回到代码复核了关键证据（只读）。路径默认相对 `src/expedistill/`。
先说结论：我在第一轮提的 3 个方案，这一轮**撤回或改为后置**，理由都是另两位给出的证据成立：

| 第一轮方案 | 本轮处置 | 依据 |
|---|---|---|
| `ensure_exam()` 懒生成考题 | 撤回 | 见 D1 |
| 方法声明 `prompts` 并配契约测试 | 改为"冻结全部已注册 prompt" | 见 D2 |
| `roles` 改成映射 | 推迟到出现第一个新角色时再做 | 见 D5 |

---

## S1–S8：是否反对

- **S1**：无。
- **S2**：无。
- **S3**：无。这和我第一轮第 8 条一致；`LIVE_DIR_NAME = "stage4_acceptance/live_run"`（`composites/live_acceptance.py:125`）已经是这个惯例。
- **S4**：无。补充一点理解：全仓库只用一个 RunContext（A 的 4.1），里面可以带 config，供阶段代码使用。
  "方法只读自己的 params 和实验级字段"这条写进 AGENTS 的扩展清单，靠评审保证，不为它建检查机制。
- **S5**：无。补充 C 的 R13：解析放在取锁、建 client 之前，现有"配置错误先于任何 client 构建"的测试照旧成立（`stages/pipeline.py:330-337`）。
- **S6**：无，而且我认为它是 D2、D5、D6 三条简化的**前提**，必须按 C 的 R1 物理落地：打 tag，独立 clone，独立 venv。
  另外加 C 的 R9：stage0 读到旧 schema 时报错，并明确指出该用哪个 tag 续跑。这只是一条提示，不是审计机制。
- **S7**：无。合并有一个顺序约束：K6 和 K4 之间"刻意不共享"的 helper（`stages/stage4_live_run.py:192-198`），理由是保住 exam 的冻结面。
  删掉懒注册、S6 生效之后，这个理由就不存在了，所以同意 A 的意见可以合并，但要放在 golden 矩阵建好**之后**。
  名字相似但语义不同的那几对（C 的 R11：`_expected_units` 与 `expected_units`）不合并。
- **S8**：同意，但要收窄范围。`run_units_disposed` 只覆盖处置语义完全相同的逐单元循环：
  - stage2；
  - stage3（`stages/stage3_generation.py:291-305`）；
  - exam 验收（`stages/stage4_acceptance.py:235-243`）。

  live_run 的组级/轮级处置不一样（不是终态而是轮次推进，还有已接受的空洞），不要硬套。

---

## D1 考题依赖：选 A 和 C 的组合，撤回 B 的 (b)

**C 的指出成立，但它不是 (b) 的设计错误，而是 (b) 会改变实验身份，所以不能在"行为不变"的阶段做。** 复核如下。

现状：
- G2 和 G3 在同一个单元里，任何一个出现 ModelOutputError，整段都进入终态，skill 不会保存（`stage3_generation.py:284-300`）；
- 续跑判据是"skill 和 exam 都在"（`:249`）。

改成懒生成之后：
- **exam 验收**：G3 失败挪到 stage4，skill 被 discard。最终进 bank 的集合不变，但失败统计口径从 stage3 换到了 stage4。
- **live_run 验收**：G3 失败不再淘汰 skill，**候选集变了**。现状其实是"因为一个用不到的考题失败了而丢掉 skill"，算一种偏差，但修正它也是身份变更。
- **K5 回归**：源考题缺失时，合并被跳过（`composites/curation.py:705-707`，"merge unverifiable"），bank 的组成也会变。

所以 C 说的"对现有配置都是死代码"不准确：对 live_run 配置不是空操作，而是**行为变更**。这反过来证明它必须走身份边界。

**最终立场**：
- v3 首切时 stage3 照旧成对生成考题（C）；
- 配置里显式写 `authoring: {exams: true}`，缺省为 true，写进冻结配置（A）；
- 等 `curation: none` 出现时，才允许 `exams: false`。届时由一处集中校验拒绝"exam 验收或 K5 回归开着、却关了考题"的组合；
- 不要 `ensure_exam`，也不要 needs/provides。

---

## D2 冻结 prompt 集：在 S6 成立的前提下，选"冻结全部已注册 prompt"，再加 C 的成员检查

**是否最简且无漏洞：是。**
- 定义："全部" = `prompts/` 包里急切注册的全部 prompt，加上本 run 所配插件的 `task_prompt_name`。
  不包括其他插件的 prompt。复核过：每个插件只注册自己的任务 prompt（`benchmarks/tb/plugin.py:133`、`searchqa/plugin.py:89`、`alfworld/plugin.py:103`），这和现行 stage0 的口径一致（`stages/stage0_freeze.py:58-61`）。
- 为什么没有漏洞：凡是能被调用的 prompt 都必须先注册，而注册了就进冻结集。
  这样就**不需要任何声明**，A 的 BASE_PROMPTS 和我第一轮的"由常量拼成的声明"都不需要。
- 两种做法失败时的表现完全不同：
  - 冻结全部：多冻了，后果是续跑时**响亮地拒绝**（fail-closed）；
  - 按声明冻结：漏声明了，后果是改了 prompt **悄悄不被发现**。
  前者明显更安全。

**代价**：
1. 新增或修改任何 prompt，都会改变之后所有**新** run 的 manifest，这是预期行为；旧 run 在 tag 上续跑，不受影响。
2. manifest 里会出现本 run 用不到的 prompt digest，属于噪声。本 run 实际用了哪些 prompt，由 `llm_requests` 记录。
3. `assemble_reuse` 本来就把 prompt digest 当"披露项"而不是"拒绝项"（`composites/assemble_reuse.py:36-44`），不受影响。
4. **前提是 S6 真的物理落地**。如果有人在同一个 checkout 上续跑，结果是被拒绝，不会算错。

C 的 `call_model` 成员检查（prompt 名不在本 run 冻结集里就报 CodeBugError）保留：只有一行，用来兜住测试或插件里动态注册的边角情况。

---

## D3 方法放哪：保留 `methods/` 作为第 6 层，前提是先完成 S7 的下沉

- A 的反对理由（methods 会 import stage1 和 K4）在 S7 下沉之后就不存在了：
  - shortfall 读写、`discard_skill`/`_tally` 下沉到 composites；
  - `aux_wiring` 下沉到 benchmarks；
  - 候选判定下沉到 `core/ids`。
- 保留单独一层的收益有两点：
  - "加一个方法该放哪"只有一个答案；
  - "方法之间互不 import"可以按目录机器检查（C 的 R12，在 `test_layering.py` 加一条规则）。
- 成本：RANK 表多一行。
- 什么进 methods：K4 和 K6 迁入 `methods/acceptance/`，因为它们只被各自的方法使用。K1/K2/K3/K5 和共享件留在 composites。

---

## D4 槽位清单与优先级

| 优先级 | 槽位 | 时点 | 说明 |
|---|---|---|---|
| 1 | acceptance（exam、live_run） | P1 | 已有两个实现 |
| 1 | delivery（whole_bank、retrieval） | P2 | 已有两个实现，直接复用 K1 的 `skills_for_task` |
| 2 | authoring | 用户提出第二种写法时立即做 | 接受 A 的事实核查（①f）：distill/diagnose 是按数据来源走的两条必经分支，不是可配置方法，所以现在**只有一个实现**，我第一轮"应前移"的说法撤回一半。现在可以先做两件零风险的准备：`write_skill` 接受 prompt 名参数；把正文标记集和 skill↔subtask 一一对应写进槽位协议（A 的 R7 和我的遗漏 2、3） |
| 3 | curation | 与 `none` 和 `authoring.exams=false` 同批 | — |
| 4 | segmentation | 有第二实现再做 | — |

单实现槽位在配置里只写参数组，不写 `name`（A 的 R9）。以后出现第二个实现时，缺 `name` 就表示用现有实现，在 S6 下不需要再升 schema。

---

## D5 角色表：v3 不开放

- 现在没有任何方法需要新角色：live_run 复用 author/rewriter 是 A12 裁决认可的做法，runbook 的预算包络也是按这两个角色算的。
- 开放角色表会带出两个问题：转换器要删掉不用的角色预算（C 的 R7，`tb21_split_live_run.yaml:211` 里的 auditor），校验时点也会变。v3 首切不开放就没有这些问题。
- 冻结整张角色表，和现在一样，与 D2 的"全冻、不声明"是同一种思路。
- 等第一个"必须显式配置"的角色出现（例如 embedding 用的 embedder），再把 `MODEL_ROLES` 改成 core 里的一个常量映射 `{role: "strong"|"weak"|None}`。这是一处常量改动，**不做按方法声明角色**。
- 顺带同意 A 的意见：把 `MODEL_ROLES` 改名（它和 `core/models.py:42` 的同名常量冲突）。

---

## D6 v2→v3 过渡：以 C 的路线为主，适配器就是 `upgrade_v2`，不另写

- `upgrade_v2(dict) -> dict` 有必要：它是只依赖标准库的纯函数，约 50 行。
  - 理由一：配置转换脚本要用它；
  - 理由二：`assemble_reuse --level bank` 复用 v2 源 run 时，要先把源 manifest 提升到 v3 视图再比对（C 的 R3）。
  - 注意 `--level baseline` 只钉住 benchmark、两个执行者、两个 attempts 这几个顶层字段，在 v2 和 v3 之间不变，不依赖升级器。
- P1 和 P2 期间，YAML 仍是 v2，内部通过 `resolve(upgrade_v2(cfg))` 得到方法和参数。这样 manifest 保持不变，golden 比对可以把 manifest 也包括进去，比 A 的"跳过 manifest"更严。
  这里的适配器就是同一个函数，没有第二套代码，所以算不上"双格式过渡期"。
- P3 一次切换磁盘上的 schema：转换结果写成新文件名，删掉 v2 加载入口。`upgrade_v2` 之后只留给 `assemble_reuse` 读源 manifest 用。

---

## D7 验收标准

- **主要验收**：C 的 golden 产物矩阵加易变字段归一化，只写在测试代码里，就是迁移的验收标准。
  - 矩阵：{exam, live_run} × {whole_bank, retrieval} × {full, split}，外加崩溃续跑变体；
  - 在 tag 提交上生成，永不重新生成。
- **C 的 R5 必须执行**：模块和对应测试在同一提交里迁移，不留 shim，断言"没发生"的测试补一个正向前提。
  否则 golden 全绿也证明不了什么。
- **通用的槽位契约测试只保留两条**：假模型能跑通、未知参数键被拒绝。
  - 我第一轮提的"实际调用 ⊆ 声明"已经被 D2 取代，删掉；
  - "第 k 次写入时崩溃"的通用续跑遍历器是纯测试侧工具，有价值，但放到第一个新方法落地时再做（那时才有回报），不进迁移的关键路径；
  - 各方法现有的专属不变量测试一律保留。

---

## 我可以接受的最终折中

1. **P-1 准备**：打 tag `schema-v2-final`；在飞 run 迁到独立 clone + 独立 venv；把"正式 run 在启动时的 checkout 上续跑"写成常设规则；在 tag 上生成 golden 矩阵。
2. **P0 纯重构**：
   - 一个 frozen 的 RunContext + RunSettings；
   - get_plugin 统一负责 import 插件包；
   - 下沉共享件；
   - 抽出 `build_manifest` 纯函数，`assemble_reuse` 上移到 stages 层；
   - 收窄范围的 `run_units_disposed`；
   - 只合并真正等价的重复。
3. **P1/P2**：建 `methods/` 层（第 6 层），迁 acceptance 和 delivery，用 `core/registry` 登记，参数用 Params dataclass。
   v2 YAML 经 `upgrade_v2` 解析；golden 矩阵连 manifest 一起比对；不留 shim。
4. **P3 一次 schema 切换**：
   - 冻结全部已注册 prompt 加本插件的任务 prompt；`call_model` 做成员检查；
   - 删掉懒注册、`METHOD_PROMPT_NAMES`、`_MANIFEST_OPTIONAL_FIELDS`；
   - 角色表不开放；
   - 配置里显式写 `authoring.exams: true`；
   - 单实现槽位只写参数组；
   - 转换写成新文件；stage0 遇到旧 schema 给出明确提示；同步 scripts/ 和 runbook。
5. **以后按需**：
   - authoring 第二种写法：prompt 名参数加标记协议已经备好；
   - `curation: none` 与 `exams: false` 一起上，届时加一处集中的跨槽位校验；
   - segmentation、provider、非 chat 调用、角色映射：出现第二个实现再做。

整个过程不引入新的 hash、版本号或审计机制。可靠性来源只有四样：manifest 逐字段比对（已有）、固定 checkout 续跑（运维规则）、成员检查（一行）、golden 测试（只在测试侧）。

---

## 第三轮：收敛表决（提纲见决策文档附录 B）

# 第三轮（收敛轮）：架构师 B 的表态

结论：R1–R7 全部接受，无否决。其中 R4、R5、R7 各附一个落地时要注意的条件，均不构成否决。
路径相对 `src/expedistill/`。全程只读。

---

**R1 接受。**
- 我第一轮建议把 K4、K6 搬进 methods，现在撤回。C 指出的"测试空转也变绿"风险是实打实的：`tests/composites/test_live_acceptance_resume.py:710-722` 这类测试 patch 的是 composites 模块，本体不搬，patch 目标就不会落空。
- `methods/` 只放驱动、按目录检查互不 import，这样"加一个方法放哪"仍然只有一个答案。
- 补一句：驱动从 `stages/stage4_live_run.py` 迁出时，patch 驱动的测试（如 `tests/unit/stages/test_stage4.py:773`）要在同一提交里一起改，不留 shim。

**R2 接受。**
- 显式写 `authoring.exams`、缺省 true，所有现有配置的语义不变。只有显式写 false 的新实验才会改变候选集，身份变化看得见、而且是预先登记的。
- A 的补充证据进一步否定了 `ensure_exam`：K5 并行等价性的前提是"同一波里的考题是不可变的共享数据"（`composites/curation.py:45`）。在波内懒写考题会直接破坏这个前提。

**R3 接受。**
- 拆成 P1a 和 P1b，出问题时可以二分定位。P1a 期间 manifest 不变，现有 manifest golden 原样保持绿（`tests/unit/stages/test_stage0.py:599-683`），这是最强的"行为不变"证据。
- `upgrade_v2` 只服务两个用途：`_identity_view` 读 v2 源 manifest，以及一次性的配置转换脚本，不形成第二条加载路径。
- 必须照顾 C 第一轮 R3 提到的一点：v2 里 `acceptance` 的三种写法（缺键、`{}`、`{mode: exam}`）要升级到**同一个** v3 值，否则会出现假差异。

**R4 接受**，前提是这条静态测试同时守住下面两个漏洞（每个约 5 行）。只要守住，它和运行时成员检查等价，我不再坚持加 `call_model` 检查。

1. **`prompts/` 下的每个模块都必须被 `prompts/__init__` 的 import 清单 import。**
   - 原因：快照 `METHOD_PROMPT_NAMES` 是 import 时取的（`prompts/__init__.py:25`）。如果某个 prompt 模块不在清单里，只在别处被 import 才注册，它就会被调用、却不在冻结集里。今天的 attribution 正是这种情况（`blocks/attribution.py:13-21` 的防御性 import）。
   - "只能在 prompts/ 和 benchmarks/ 里注册"这条规则单独拦不住这个漏洞。
2. **每个 `benchmarks/<name>/` 最多注册一个 prompt，并且就是它的 `task_prompt_name`。**
   - 原因：冻结公式只收录本插件的任务 prompt。插件如果多注册一个 prompt，那个 prompt 同样会漏冻。
   - 现状复核：三个插件各注册一个（`benchmarks/tb/plugin.py:133`、`searchqa/plugin.py:89`、`alfworld/plugin.py:103`）；`prompts/` 和 `benchmarks/` 之外没有其他 `register(` 调用。所以这条测试今天就能通过。

**R5 接受**，附一个条件：None 档位先不实现。
- `ROLE_DEFAULTS` 吸收了今天写死在 `core/config.py:567-568` 的默认规则（answerer 用弱模型，其余用强模型），并消除了和 `core/models.py:42` 的重名，值得做。
- 但现在只放 `"strong"` 和 `"weak"` 两个值。等第一个"必须显式配置"的角色出现时，再加 None 档位和对应的校验分支，照"没有使用者就不建"的原则来。

**R6 接受。**
- C 的证据成立：历史上有过在飞 run 必须拿到修复的情况（`scripts/run/prepare_inplace_recovery.py`），所以用维护分支，而不是不可变的 tag。
- stage0 的报错文字写成通用提示即可，比如"此 run 由 schema v2 冻结，请用它的维护分支加独立 venv 续跑"。不为了写出分支名而给 manifest 加字段。

**R7 接受**，附一个落地细节。
- 只有一个 context 类型；方法驱动只看到自己的 Params 加实验级视图，这和 S4 一致。
- `n_attempts ≤ n_attempts_weak` 这条校验放不进 Params 的 `__post_init__`：它要用到实验级的 `n_attempts_weak`，`__post_init__` 看不到。所以要放在方法解析时调用的 `check(params, experiment)` 里。依据：`composites/live_acceptance.py:433` 的 `load_baseline` 会把缺失的单元报成误导性的 InfrastructureError。
