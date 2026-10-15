日期：2026-10-15
一句话说明：执行—学习实施方案的第二轮评审 B——简洁性、可扩展性与反过度设计。
对应 log.md 条目：2026-10-15 执行—学习闭环实施方案（第一轮三路评审后）。

---

## 0. 总评

实施方案把 scope v2 落到了字段与接口级，总体是克制的：一张注册表、读取器不落盘、
决策不建注册表、不加 hash、加载期检查沿 check_authoring/check_live_run 先例、
M2/M3 golden 逐字节无 diff 硬闸。第一轮 B 的四条偏移约束（B-1 单注册表、B-2 报告
读取器、B-3 配置形状、B-4 去轮化）在 v2 §10 与本方案中的落实机制均忠实（逐条核对
见 §3.9）。

但把方案逐字段对到代码上，发现**一个结构性缺口**（注册表键集：learn 键是死数据、
evidence/author 两键悬空）和**五处会把冗余或歧义固化进 schema v4 / M4 布局的具体
问题**。共同特点是：每一处的修法都是方案文档里几句话，不动方向、不动 M0–M4 骨架。
其中两处（compare 的 control 形态、n_attempts 语义）在 schema 冻结前必须定形，否则
v4 配置永久带着一个由 mode 派生的冗余字段和一个会算错 D 相位强臂深度的缺省。

证据基线：仓库 HEAD bb0935f（feature/multi-method）；`methods/registry.py:42` 的
`_ENTRIES`、`core/config.py:85` `METHOD_DEFAULTS`、`infra/store.py:29` `KIND_DIRS`、
`composites/live_acceptance.py:125/169`（LIVE_DIR_NAME/_ROUNDS）、`stages/` 全目录、
7 份 v3-schema 配置（4 份 `*_v3.yaml` + 3 份 `*_v2_v3.yaml`）。以下 file:line 均相对
仓库根。

---

## 1. 偏移约束

### B2-1【偏移约束】注册表键集：learn 键是死数据，evidence/author 两键悬空——四族表无法统一分发，键集应改为五个内容族、learn 不入表

**证据**（对应重点判断 2、4）。§3 的注册表只有四族：delivery / execution / learn /
curate。两处矛盾：

1. **learn 键无法被一致地分发**。§3 相位驱动段落定义了 `phases/learn.py`（"一次
   L 相位实例：读报告 → 决策 → 证据/写作/整理 → summary"，来源"从 stage2/3/5 +
   K4/K6 的决策半边抽"）；注册表 learn 行的驱动内容是同一句话——**同一个模块被
   描述了两遍**。序列驱动要分发 kind=learn 时：若走注册表，则 kind=execute 也该
   有一条（execute 相位）注册行，但表里没有——execute 硬编码 import
   `phases/execute.py`，learn 走表，**两种分发方式并存**；若两种 kind 都硬编码
   （freeze 本就如此，§3"独立于序列，永远是 p00"，无注册行），则 (learn, default)
   这一行**永远无人 resolve，是死数据**。
2. **配置里 L 相位的 evidence/author 两键在注册表中无族可查**。§4 学习相位字段表
   有 `evidence: {name: segment, K, retry_limit}` 与 `author: {name:
   distill_diagnose}`，但四族表里没有 evidence、author 族——这两键的 name 对着
   什么表解析、参数用什么校验，方案没说。§3 改造清单写"segmentation 的 Params
   并入 learn 的 evidence 参数"，意味着 evidence/author 参数被手工折叠进 learn
   驱动的 Params——**绕开 `build_params` 的通用校验**（`methods/params.py:21`），
   出现第二套参数校验路径，恰是 B-1 当初要防的。而 curate 又同时是独立族
   （agentbrew/none 已注册），同一个 L 相位的三个模块键两种待遇。

**判断**。这不是"留接缝"而是"无消费者的投机抽象"（上轮 P5 教训原文：无消费者
不落地）：learn 族的"第二驱动"故事被 scope 方案自己否决——v2 §3.2"只有一种 L"，
"不为不同位置造不同的 L"；L 的全部可变性已由 evidence/author/curate 三族与报告
种类承载。evidence/author 反而是**真有消费者**的族：`evidence: {name: segment}` 的
K/retry_limit 今天就是 `SegmentationParams`（`methods/segmentation/__init__.py`），
author 的 exams 开关删除后剩的正是 `AuthoringParams` 的空壳加名字。把两键升为族，
Params 类现成、校验机制现成、单实现族 name 可省（`methods/registry.py:74-79` 的
先例），零新机制。

**建议修法**（§3 两处，共几句话）：

- 注册表键集定为**五个内容族**：delivery（none/whole_bank/retrieval/per_skill/
  group_by_task）、execution（real/exam）、**evidence（segment）**、**author
  （distill_diagnose）**、curate（agentbrew/none）。删去 learn 行。
- 相位驱动按 kind 直接 import（freeze 已是此式）：`phases/execute.py` 解析
  delivery+execution（+compare），`phases/learn.py` 解析 evidence+author+curate。
  序列驱动对三种 kind 一视同仁，无注册表参与。
- §3 改造清单相应改写：`methods/segmentation/__init__.py` 的 Params **改名挂到
  evidence 族**（不是"并入 learn"）；`methods/authoring/__init__.py` 删 exams 开关
  与 check 后作为 author 族的参数包保留。
- 职责切分一句话（回答重点判断 4）：**phases/ 拥有"何时做什么"——序列的一个
  实例：相位目录、次序、shortfall、summary、阶段末抛错、跨相位读数；methods/
  拥有"怎么做"——一张纯数据表里可按名替换的做法与参数契约。** 说得清，且只有在
  删掉 learn 键后才成立；照现文实施，learn 键与 phases/learn.py 就是双层驱动的
  双抽象源。

### B2-2【偏移约束】compare 的 control 形态是 mode 的复写，且 reuse_d_weak 不指名相位——统一为 `against: <相位名>|fresh_bare`

**证据**（对应重点判断 1）。§4：C 形 `compare: {control: fresh_bare|reuse_d_weak}`，
"split→fresh_bare、full→reuse_d_weak，与现行 mode 决定一致，加载期校验"。两点：

1. **control 的取值由 mode 完全决定**（方案自己承认"与现行 mode 决定一致"），写进
   配置后两处持有同一事实，再加一条"control 与 mode 匹配"的加载检查维持复写不漂
   ——一个字段、一个决定它的字段、一条看守一致性的检查，正是用户点名要避免的
   over-engineering 形态。v4 保留全局 `mode`（§4 全局字段不变），对照现行代码
   `stages/stage6_test.py:325-350`：full 复用 stage1 弱臂、split 跑 fresh bare，分支
   就一个 `config.mode` 判断。
2. **reuse_d_weak 是关键字不是相位引用**：局部名用户可改（"序列即身份，进
   manifest"），序列里叫什么名字的相位提供弱臂，`reuse_d_weak` 没说。M4 实施时
   要么发明魔法解析规则（"最早的 delivery:none 执行相位"），要么把关键字偷偷绑
   死在某个局部名上。而 B 形的 `against: D` 已经给出了正确形状。

**判断与修法**。两种形态不必都留：**full 模式的 C 对照本来就是 D 相位的弱臂**
（`stage6_test.py:325-331`，control_source="stage1_reuse"）——它就是一次 against
引用；split 模式的 fresh bare 臂是 C 自己执行的另一半，只需一个标记。统一为一种
形状：

```yaml
compare: {against: <序列中更早的相位名 | fresh_bare>, rule: <具名规则，可选>}
```

- B：`{against: D, rule: strict_rate_gain}`（不变）；
- C（full）：`{against: D}`——删 `reuse_d_weak` 关键字，相位引用指名道姓；
- C（split）：`{against: fresh_bare}`——唯一的保留关键字，意为"本相位自跑裸对照
  臂"（不写"缺 compare = fresh"的推断——那会让 D_test 这类纯执行相位凭空多跑
  一条对照臂）；
- "control 与 mode 匹配"检查删除；如需保留现行"full 不重跑对照"的立场，加一行
  "full 模式下 against 不得为 fresh_bare"即可（比维持字段复写的检查更窄）。
- 附带收益：`against` 形状对 `tasks: test` 的 D_test 相位天然成立（对照可指向任意
  早期相位），control 关键字则永远表达不了。

（说明：上轮 B-3 要求"C 的裸对照显式表达"，本条是在其落实形态上的自我修正——
显式化的正确载体是相位引用，不是 mode 的复写。）

### B2-3【偏移约束】n_attempts 语义欠定义：D 双臂深度缺省会算错，加载检查比错了对象

**证据**（对应重点判断 1、5）。§4 字段表："real 可带 arms（缺省 [executor_weak]；
D 显式写双臂）、n_attempts（缺省 n_attempts_weak）"。两处对不到代码：

1. **D 的强臂深度**。现行 stage 1 强臂跑 `n_attempts_strong`、弱臂跑
   `n_attempts_weak`（`stages/stage1_baseline.py`，两值是全局字段）。按方案文字，
   D 双臂、不带 n_attempts → 缺省 n_attempts_weak → **强臂也只跑弱臂深度**。全部
   7 份 v3 配置两值恰好相等（均 3；pilot 为 1），golden（E1–E5 用这些配置）不会
   暴露；schema 又允许两值不同——这是一颗只在"未来某份 strong≠weak 的配置"上
   炸的语义地雷，且炸的方式是静默改测量。
2. **检查比错对象**。加载期检查"real+compare 的 n_attempts ≤ n_attempts_weak
   （check_live_run 的推广）"引用**全局** n_attempts_weak。但字段表允许各相位自带
   n_attempts：D 带 `n_attempts: 5`、B 带 4、全局 3 → 检查拒绝一个合法配置（B 的
   基线明明深 5）；D 带 `n_attempts: 2`、B 带 3、全局 3 → 检查放过一个真洞（B 的
   基线只有深度 2，`composites/live_acceptance.py:433` load_baseline 会把缺单元
   当 infra 报错——正是 check_live_run 存在要防的事故，
   `methods/acceptance/__init__.py:70-85`）。检查必须比"against 相位的实际弱臂
   深度"，不是全局值。

**建议修法**（§4 字段表两句话）：

- **双臂相位禁止携带 n_attempts**：深度恒为两个全局（strong 臂
  n_attempts_strong、weak 臂 n_attempts_weak）。单臂相位 n_attempts 缺省
  n_attempts_weak、显式值覆盖。D 因此永远零额外字段；未来要单相改深度再说
  （YAGNI）。
- 检查改为："real+compare 的 n_attempts ≤ **compare.against 所指相位的弱臂深度**
  （该相位未带 n_attempts 时为全局 n_attempts_weak）"。against: fresh_bare 时按
  全局。

### B2-4【偏移约束】泄题账本（first-verdict-sticks）的 v4 住所未指定——§5 失败处置表缺行，续跑契约有静默断裂风险

**证据**（对应重点判断 6）。§3 相位目录段："K1 工作目录、轮仓、**账本都在相位
目录下**"。§5 只给改写账本指定了新家（"同一账本随改写搬进 L 相位目录；A 重答键
含相位序号"），**泄题账本（leak_verdicts）一行没有**。而它的续跑契约与改写账本
不同构：

- 改写失败行的键含轮（`retry_round`，`composites/acceptance.py:256-265`），按相位
  分文件后各 L 自洽；
- 泄题判定键是 **(skill_id, skill_version)**，**跨 A 相位恒等**（D5："泄题判定每
  (技能, 版本) 一次"；`composites/acceptance.py:367-376"，判定先落盘、首判为准、
  resume 永不重问判官——判官不确定，重问就破坏"续跑 = 一次跑完"）。exam 序列有
  多达 3 个 A 相位：改写耗尽时版本不变，A(p05)/A(p07) 必须能**找到 A(p03) 写下的
  判定行**。按"账本都在相位目录下"字面实施 = 判定行按 A 实例分片，跨相位读取
  规则无人定义；实现者若让后一个 A 重新问判官，E5 的崩溃续跑变体会抓到（判官
  可能给出不同答案），但那是以最贵的方式发现规格漏洞。

**建议修法**（§5 表加一行，§3 读者行补半句）：泄题账本沿 (skill, version) 键跨
A 相位单真相——要么固定一条共享路径（如首个 A 相位目录下的
`leak_verdicts.jsonl`，后续 A 相位按序列位置读同一文件），要么明确"读取方聚合
本相位之前的所有 A 相位目录、首行生效"；写入次序（判定行先于该轮动作，
append-once）原文照搬。§3 exam_report 读者行同步注明读取范围。

### B2-5【偏移约束】对象布局未裁决：store.py 的 KIND_DIRS 三处信号互相矛盾，照 §3 字面实施会留下"两套布局并存"的残留

**证据**（对应重点判断 7）。三个信号指向三个方向：

1. §3 相位目录段只说"K1 工作目录、轮仓、账本都在相位目录下"——**对象**
   （trajectories/subtasks/skills/exams/attempts）没提；而 B3 的对象目录是**第 1 层
   硬编码常量** `infra/store.py:29` `KIND_DIRS`（值即旧 stage 目录名：
   "stage1_baseline/trajectories" 等）+ `LAYOUT_DIRS`，§3/§4/§7 全文没有一行提到
   store.py 要改。
2. §7 assemble_reuse 行写"拷贝清单/负清单改相位目录（v3 源按旧 KIND_DIRS 直读）"
   ——暗示 v4 的对象已搬进相位目录，旧 KIND_DIRS 只用来读 v3 源。
3. §8 归一化有"布局映射（旧 stage 目录名 → 相位目录）"——E 验收预期对象位置
   变了。

若实施者按 §3 字面只搬账本/工作目录、对象留在 run 根的 KIND_DIRS，E1–E5 靠归一化
照样全过——**验收拦不住**，仓库从此带一个"对象在旧 stage 树、账本在 phases/ 树"
的永久混合布局，正是重点判断 7 问的"两个系统并存"残留；若改 KIND_DIRS 的值，
store.py 及全部路径常量（`composites/shortfall.py` STAGE1_DIR、
`methods/acceptance/__init__.py` STAGE4_DIR、`composites/live_acceptance.py:125`
LIVE_DIR_NAME、`stages/stage6_test.py:109` TEST_DIR…）都是 M4 跟随者，而 §7 跟随
者表没有 store.py。

**建议修法**（§3 一句话 + §7 加一行）。最省的裁决：**相位 Store 的根 = 相位目录，
KIND_DIRS 的值与嵌套一律不动**（`Store(phases/pNN_<name>/)` 内部仍是
stage1_baseline/…，stage6 双臂仓的既有先例，`stages/stage6_test.py:137-139`）——
store.py 零改动、v3 源直读免费成立、§8 布局映射退化为"run 根 ↔ 相位根"一条；
§7 跟随者表补 `infra/store.py`（如最终选择改 KIND_DIRS，则 store.py 必须入表）。

### B2-6【偏移约束】§7 跟随者清单不完整：scripts/ 下至少四个活工具在 M4 后断裂或失效

**证据**（对应重点判断 7；上轮 T-8 的同类问题在实施层的重现）。§7 自称"M4 内完
成，不留尾巴"，但表里只有 assemble_reuse / audit_live_run / cli / 文档 / golden /
假模型脚本。逐一核对 scripts/：

- `scripts/smoke/smoke_stage01_searchqa.py`、`smoke_stage01_alfworld.py`：**活工具**
  （外层 AGENTS"小样本干跑"纪律的载体），直接 import 并调用
  `stages.stage0_freeze` / `stage1_baseline`——M4 删除 stage1–6 编排后 **import 即
  断**；
- `scripts/run/build_skillopt_split_configs.py:244`：生成 `schema_version: 3` 的配
  置——M4 后 `load_config` 拒 v3（§4），其产物全部不可加载，工具失效；
- `scripts/run/fork_run_stage04.py:18`：调 CLI `--until-stage 0` 并按旧布局拷贝
  stage 目录——flag 改名后断裂；
- `scripts/analysis/tb21_f3_baseline.py`、`scripts/run/seed_tb_stage1.py`：服务旧 run
  的离线工具，本身可活，但需要一句"只服务 v3 run 布局，不随 M4 演进"的处置注
  记，否则就是尾巴。

**建议修法**（§7 加一行）：M4 内对 scripts/ 做一次逐文件处置——活工具（smoke、
build_configs）随相位化改写；旧 run 专用工具（fork/seed/recovery/analysis）标注
"v3 run 布局专用"并保持可运行（它们读旧 run 目录，不经过新管线）。一行表格即可，
不必逐个写改动细节。

---

## 2. 琐细

- **s-1** §5 shortfall 行交叉引用失效：写"不变（§3 决策清单已含）"，但 §3 的
  baseline/exam/live 决策清单并无空洞继承政策（轮 1 不继承轮 0 洞、每轮 skip 含
  D 弱臂洞、full 的 C 对照洞在 D 侧接受——现行语义在
  `methods/acceptance/live_run.py:88-97`、`composites/live_acceptance.py:508`）。
  政策实际由 B 相位编排 + `skip_ids_for` 承载，把"（§3 决策清单已含）"改为指向
  "测量核签名与相位编排（§3 去轮化段）"。
- **s-2** test_report 无消费者（P5 教训）：C 恒为终评相位，v2 §9 明确不做跨 run 闭环，
  闭环内回喂（"测试轨迹喂下一轮学习"）是将来落新方法时再加的事。删 §3 读者表的
  test_report 行；C 的测量与 summary 留在相位驱动内联（即今日 stage6 形状），E3
  直接比对新旧 summary 文件。
- **s-3** gen_exam / audit_leak 应留在 `blocks/`（G3/D8 是模型积木，prompts 成对），
  只搬调用点（M4 从 L 挪进 A）。§3"M2 只搬函数不搬调用点"会让 stage3 反向 import
  methods 层里的积木，与"积木只在 blocks/、methods 只做驱动"的分层精神相悖，且
  无任何收益——blocks 有单一 composite 消费者是常态（G10/G11 亦然）。
- **s-4** §8 live 白名单①"无出题/泄题/回归考调用"中"泄题"是误列：v3 的 live
  本来就没有泄题审计调用（audit_leak 仅 K4 调用，`composites/acceptance.py:370`
  是唯一调用点；live_acceptance 不 import blocks.audit）。白名单多放行一项 = 若
  v4-live 因 bug 引入泄题调用，验收会放过。收紧为"① 无出题（G3）调用、无 K5 回
  归考（G4/G5）调用"，与 E4 行自己的措辞（"出题族 + 无回归考"）对齐。
- **s-5** 加载期检查补两条一行款：相位键集与 kind 匹配（execute 只认
  delivery/execution/tasks/compare，learn 只认 evidence/author/curate——unknown-key
  响亮拒绝是仓库既有纪律，`core/config.py` 顶层键即如此）；相位局部名字符集
  （如 `[A-Za-z0-9_-]+`——局部名进目录名与标签，用户可自造）。
- **s-6** "7 份 `*_v3.yaml`"的指称不精确：v3-schema 配置 7 份 = 4 份 `*_v3.yaml`
  + 3 份 `*_v2_v3.yaml`（v2→v3 转换器的新文件名）。M4 转换器按 glob 找文件时别漏
  后者。
- **s-7** §3 补一句上轮 4.5a 的约定：**evidence 族的输出仍是 Subtask 对象**（写作/
  出题/判卷零改动），否则"不切段"扩展的第一步就要回头改契约。
- **s-8** 去轮化签名遗漏账本写入目标：`judge_round`（
  `composites/live_acceptance.py:573`）不只算判定，还拥有 verdicts 账本的
  append-once 写入与重算比对（续跑的关键半边）。§3"输入 = 轮仓 store + 基线计数 +
  具名规则 + 冻结审计行"要补上"verdicts 账本路径（或写入方）由相位编排传入"，
  否则测量核没了落笔处，golden 会兜底但规格应说全。
- **s-9** §3 决策清单补一行：baseline 报告的 add 动作按 `store.exists(skill_id)`
  幂等（L 相位 resume 重放时不得重复入库）——现行 stage3 的跳过语义
  （`stages/stage3_generation.py:226-229`）随抽取平移，写明免得实现者另造判据。
- **s-10** delivery 契约补一句：per_skill 的注入规格是 K3 的单技能块
  （`composites/acceptance.py:398` format_skills_block([working]) 的形状），
  交付输出形状随 execution（K1 kwargs vs K3 kwargs）而定——§3 delivery 行目前
  只写了 K1 形状，M2 定契约时会用到。

---

## 2.5 赞同

- **a-1** 一张注册表：registry.py/params.py 机制逐字节不动、只换键集（§3，v2 D4 落实）。
- **a-2** 报告读取器 = 纯函数视图：不落盘、不建新对象、不新增崩溃窗口，续跑语义免费继承（§3，v2 D3 落实）。
- **a-3** 决策不建注册表、技能库变更只经 discard_skill 等既有提交 helper、终态先写行是回执、不新增 actions 账本（§3 决策清单末两条）。
- **a-4** M2/M3 golden 逐字节无 diff 是硬闸，M4 预注册语义准则 + live 差异白名单收口（§8/§9，v2 D6/D11 落实）。
- **a-5** E1–E5 归一化明确"不比 summary 分桶、中间落盘点、调用顺序"，E4 用多重集不比顺序——无过度比对（§8）。
- **a-6** 去轮化点名的三处耦合（_ROUNDS/_check_round、round_dir、LIVE_DIR_NAME）与代码逐一相符（§3 对照 live_acceptance.py:125/169/231）。
- **a-7** 失败处置表六行中四行（infra/shortfall/代码 bug/改写耗尽）与代码现状逐字相符（§5 对照 acceptance.py:229-276）。
- **a-8** upgrade_v3 映射表逐行成立：exam [D,L]+[A,L]×(R+1)+[C] 与 v2 §4.3 一致，7 份 v3-schema 配置可转（§4）。
- **a-9** arms 用参数不用第二注册名（与 merge_check 裁决同式）；tasks: test 是 C 表达测试集的真数据流（§4）。
- **a-10** pNN 标签、cli.summary 从冻结序列推导（少一个 STAGE_NAMES 式漂移源）、until_phase 重放语义同现行（§6，v2 D7 落实）。
- **a-11** 无新 hash、无运行时硬门；全部新检查都是加载期 ConfigError，沿 check_authoring/check_live_run 先例（§4 对照 methods/authoring/__init__.py、methods/acceptance/__init__.py:70-85）。
- **a-12** 五槽位留/改/删清单与 methods/ 目录逐文件对得上，stages 两个薄前端随 if 链同批消失，无兼容层（§3）。

---

## 3. 九项重点判断的逐项结论

### 3.1 配置 v4 形状（§4）——逐字段

- `execution.arms`：**必要，保留**。D 是唯一双臂相位，但"哪些臂"是真参数；用参数
  而非第二个注册名（real_baseline）与 merge_check 的裁决先例一致，缺省
  [executor_weak] 让 B/C 免写。缺省本身自然。
- `compare`：against/rule 形必要；**control 形不必要**（B2-2）。
- `evidence`/`author`/`curate`：三键不冗余（= 切段/写作/整理三个既有槽位的对应
  物），但前两键必须升为注册族（B2-1）；单实现族的 name 可省（registry.py:74-79
  先例）。
- 缺省值：tasks 缺省 train 正确（C 显式 test 是真数据流，D/L/B 免写）；arms 缺省
  [executor_weak] 正确；**n_attempts 缺省错误**（B2-3）。两种既有方法在修掉 B2-2/
  B2-3 后读起来自然：exam = D(双臂) + A×3 + C(test, compare) + 一份共享 L；
  live = D + B×2 + C + 同一份 L 改 merge_check。

### 3.2 注册表键集（§3）——裁决

**learn 不该有注册键**。phases/learn.py 由序列驱动按 kind 直接 import（与
freeze 同式、与 execute 对称）；(learn, default) 是被 v2 §3.2"只有一种 L"否决了
第二消费者的投机键（P5 教训），且使 execute/learn 两种分发方式并存。键集 =
**五个内容族**：delivery / execution / evidence / author / curate（B2-1）。curate
键保留无异议（双实现、merge_check 参数透传）。

### 3.3 报告读取器（§3 composites/reports.py）

字段集最小：每个字段都能在 §3 决策清单里指出消费者（bare_verdict→
weak_already_knows、leak_verdict→revise/delete、judge_reason→revise 的改写输入、
exhausted→同版重答、members/versions→G10、注入块 sha/字节等价→no-op guard）。
四个读取器**分四个函数**正确——它们读不同的账本与行形状，合成一个通用读取器
需要"规格的规格"，必成 god-object。唯一问题：**test_report 无消费者**，删行
（s-2）；三函数 + 相位驱动各自内联的测量，就是最小接口。

### 3.4 phases/ 新层 vs methods/ 层

修掉 learn 键后**不构成双层驱动**：phases/ 是序列的实例化层（何时做什么），methods/
是可替换做法的表（怎么做），一句说得清（B2-1 的修法原文）。照现文实施则 learn 键
与 phases/learn.py 互为影子，双抽象源坐实。freeze 无注册行是既有先例，三种 kind
一致按 kind import 即可。改写账本搬进 L 相位目录不破坏"终态先写、行是回执"：
行是改写尝试的回执、终态在技能对象上，现行次序（`composites/acceptance.py:229-276`
先 append 失败行再落版本）原文保留；A 读前一个 L 的账本由相位屏障保证（infra 错
误 exit 3，L 必然先完整）。

### 3.5 over-engineering 逐条扫描

- 加载期检查六项：sequence 形状、against 引用、merge_check×exam 相位、roles 有效
  ——四项各有真实事故可指（未定义引用、K5 无考题可回归、角色表缺失），正当；
  n_attempts 检查**比错对象**（B2-3）；control-mode 一致性检查是**复写的看守**
  （B2-2，随统一删除）。缺两条一行款（s-5）。
- E1–E5：**无过度比对**。归一化明确"不比 summary 分桶、中间落盘点、调用顺序"；
  E4 用多重集不比顺序（M4 调用点重排天然通过、内容漂移必被抓住）；E2 的映射行
  （"未出生"↔discarded）是行为变化的必要对照，不是过度比对。唯一毛病是白名单①
  多放行了"泄题"（s-4）。
- 相位目录命名（pNN_<局部名>）、pNN 标签、until_phase 重放语义（stage0 恒跑 + 上
  界 + 按确定 ID 跳过，与现行 `--until-stage` 逐点一致，`stages/pipeline.py:281-287`
  语义原文）：**无多余机制**，赞同。until_phase 与 pNN 编号对齐（freeze=p00）自洽。

### 3.6 异常分类与恢复机制保全核查

- 四类异常与处置（`infra/reliability.py:42-86`）：方案全文未触碰，§5"基础设施错误/
  代码 bug"两行"不变"与各 stage 的阶段末抛错实现（exit 3 语义，`cli.py:81`）一致。
- §5 失败处置表逐行核对代码：出题耗尽（现行"技能不入库" =
  `stages/stage3_generation.py:270-279` 先写终态记录、`:284-287` 成对落盘——两
  处与 v4 的映射吻合）；泄题耗尽（现行 discarded(model_output_error) =
  `methods/acceptance/exam.py:164-169`）；改写耗尽（`composites/acceptance.py:229-276`
  的 exhausted 行 + 版本不变 + 下轮重答同版）——**五行全部与代码相符**。缺口两个：
  泄题账本住所未指定（B2-4）、shortfall 行交叉引用失效（s-1）。空洞继承政策的实质
  保全路径（B 相位编排 + skip_ids_for + 逐相位 shortfall 记录）成立，只是引用写错
  了地方。
- K6 去轮化可行：轮耦合点核实为三处（`_ROUNDS=(0,1)`/`_check_round`
  （live_acceptance.py:169,247）、`round_dir`、`LIVE_DIR_NAME:125`），方案点名准确；
  相位屏障（infra → exit 3）使"A 读前序 L 账本"无新增崩溃窗口。签名要补账本写入
  目标（s-8）。

### 3.7 删除/留存清单（§3 五槽位逐文件处置）

对照 `methods/` 目录逐文件：留（registry/params/delivery 双驱动/curation 双驱动）、
改（acceptance 两驱动内容重分配、authoring 删 exams+check、segmentation Params 改
挂）、删（core/config 三样、methods/acceptance/ 整目录、stage1–6 if 链）——**清单
本身与目录对得上**；`stages/stage4_acceptance.py`、`stage5_curation.py` 两个薄前端
随 if 链死于 M4，无残留。缺两个跟随者：store.py（B2-5）与 scripts/（B2-6）。
`stages/context.py`（StageContext/RunSettings）相位驱动复用，留存正确。

### 3.8 扩展性复核（按 B2-1 修后；文件数为框架文件 + 方法内容文件）

- **检索交付 + 真执行验收**：0 新模块 + 1 份相位配置 + ≥1 条新具名决策规则（检索
  命中后的逐技能归因是新协议，方法内容约 2–3 文件）。前提：B-4 去轮化已进 M2 ✓、
  注入审计行的构造随 delivery 契约走（§3 delivery 行"可选审计行"✓）。
- **不切段学习**：evidence 族加一个驱动 + Params + 注册行 + 测试 + README/log ≈
  **4 文件，0 框架改动**（前提：B2-1 + s-7 的输出约定）。
- **embedding 检索交付**：delivery/embedding.py + llm 非 chat 调用 + providers 表行
  + 角色 ≈ 3–4 文件 + 2 注册行，方案零新机制（与 P5 当初跳过的理由一致）。
- **多轮闭环**：序列写长 = **0 代码**（去轮化后轮数即相位数；"还有后续验证轮"按
  learn 相位计数，对"末位=0"的布尔语义稳健——exam/live 两序列已验算成立）。
- **新 benchmark 插件四件套**：benchmarks/ 层不动，**0 框架文件**，四件套流程原样
  成立。

### 3.9 第一轮 B 四条偏移约束的落实忠实度

- **B-1（单注册表）**：机制忠实（一张表、registry/params 文件不动、五槽位逐文件
  处置清单齐备，v2 §10 D4）；**键集本身有缺口**（B2-1）——落实走样发生在"换键
  集"这一步，根源是 D4"相位/模块"的措辞留了歧义。
- **B-2（报告读取器）**：忠实落实（纯函数、不落盘、不建新对象、字段逐项有消费
  者）；test_report 一行超编（s-2）。
- **B-3（配置形状）**：机制忠实（嵌套参数、name 键、against 引用序列、models 死
  字段删除、bare_exam 魔数省略、跨相位约束进 check——v2 §10 D9 全条对应）；control
  子句的落实形态需再收敛（B2-2，含我对自己上轮建议的自我修正）。
- **B-4（去轮化）**：忠实落实（M2 内容含去轮化，live_run.py 过渡持轮序，M4 归相位
  编排后删除——v2 §10 D5 + 实施 §3/§9 一致）。

---

## 4. 结论

六条偏移约束全部是文档级修法：键集改五族并删 learn 行（B2-1）、compare 统一为
against: 相位名|fresh_bare（B2-2）、n_attempts 双臂禁带 + 检查改比对相位（B2-3）、
泄题账本补一行住址（B2-4）、对象布局一句话裁决 + store.py 入跟随者表（B2-5）、
scripts/ 处置一行（B2-6）。修完后方案可进 M1；E1–E5 仪器、失败处置表、golden 硬闸、
M0–M4 切分均无需变动。
