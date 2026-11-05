日期：2026-11-02
一句话：ALFWorld 插件的执行协议改为与外层 `benchmark/alfworld-eval` 的统一 runner 逐字一致，旧协议的全部结果作废重跑；每种学生模型只裸跑一次，冻结为后续所有对比的对照。
对应 log.md：2026-11-02「ALFWorld 执行协议对齐统一协议」条目

## 背景：同一个模型，两套协议差 25 个点

AgentBrew 会话用外层 `benchmark/alfworld-eval` 的统一协议测裸 `qwen3.6-flash-distill`（Q）：

- 完整 134 题 ×3：**0.6343**（402 个 rollout）；
- 我们从 134 题里抽 12 题（每类 2 题）用统一协议跑：**8/12 = 0.667**。

同一批 12 题、同一模型、同一环境与判分器，用我们插件原来的执行协议跑：**10/11 ≈ 0.909**
（第 12 题当时仍在跑）。我方全量 134 题的裸 Q 是 **0.88**。差距约 25 个百分点，
不是模型差异，是协议差异。

原来的协议比统一协议容易，具体有四处：

1. **记忆**：我们每一步都把初始房间描述重复进 prompt，并给完整历史；统一协议只给
   最近 2 步（观察, 动作），初始房间列表在第 3 步后离开 prompt。
2. **动作**：统一协议要求 `<think>…</think><action>…</action>`，动作必须是环境
   当前给出的可行动作之一，输出含中文即无效；我们接受任意一行命令，还做
   `put → move` 归一化与 think-only 回复。
3. **生成参数**：统一协议 temperature 0、`max_tokens` 4096（我们后来改为 65536，
   因为 4096 下教师 D 的 baseline 回复有 19.3% 被截断）；我们当时默认 temperature
   1.0、上限 65536。
4. **系统消息**：统一协议是 `You are an expert agent operating in the ALFRED
   Embodied Environment.`，我们是默认助手提示。

AgentBrew 的失败轨迹核实了机制：它的失败 episode 平均 28.3 步、在房间里来回走
（两三条观察后就忘了房间布局）；我们的 episode 平均 7.8 步（房间列表一直在
prompt 里）。

## 决定

### 1. 执行协议逐字对齐

新增 `src/expedistill/benchmarks/alfworld/protocol.py`，把外层统一 runner 的
协议原样搬进插件：

- system 消息、step-1 模板与后续步模板逐字复制（`ALFWORLD_TASK_PROMPT_SYSTEM`、
  `ALFWORLD_TASK_PROMPT_NO_HISTORY`、`ALFWORLD_TASK_PROMPT`）；
- 只保留最近 2 步（观察, 动作），当前观察单独给出，可行动作列表来自环境；
- `<think>`/`<action>` 严格解析、动作必须在可行动作列表里、含 CJK 即无效；
- 无效回复只纠错一次（与统一 runner 相同的诊断文本），仍无效则执行 `look`；
- temperature 0、seed 0、50 步（引擎自己的预算，循环预算与引擎预算不一致直接
  报配置错误）；输出上限最初 4096（与统一 runner 一致），2026-11-03 改为 65536，
  因为 4096 下教师 D 的 baseline 回复有 19.3% 被截断并触发纠错。
- 观察文本不再做任何改写：删掉了适配器里 "You arrive at loc …" 的剥离，因为统一
  runner 用原始文本；新增 `step_verbatim`，统一协议下动作原样送给引擎（不做
  `put … in/on` 归一化、不做重复动作终止）。

### 2. 技能交付保留渐进式披露

`{skill_block}` 槽位放 `<available_skills>` 目录；文件夹版本在 prompt 末尾多一段
规则，要求模型用协议格式打开卡片：

```
<think>brief reason</think><action>ReadSkill[<location>]</action>
```

循环在把动作交给环境之前拦下它：返回整份 `SKILL.md`、不占游戏步数、每次尝试最多
3 张。统一解析器会把动作小写化（`readskill[...]`），循环用原始大小写匹配读取
动词、保留目录路径的原始大小写。裸跑臂看不到文件夹模板，prompt 与统一 runner
逐字一致。

2026-11-03 修正：读取不再作为交互进入最近 2 步历史，也不再替换
`current_observation`。旧做法会把两个记忆槽位之一花在读卡上，打开一张无用的卡
就挤掉真实环境历史。卡片正文只放在常驻的 "Opened skill cards" 段落，读卡结果由
一行 "Last skill read" 报告（见
`docs/decisions/deployment_constraints_and_read_slots_20261103.md`）。

### 3. 冻结面扩展到协议的全部 prompt

插件接口新增可选位 `protocol_prompt_names`（`benchmarks/base.py`）。stage 0 除
`task_prompt_name` 与 `skill_folder_prompt_name` 外，也冻结这些名字的 digest；
声明了未注册的名字是配置错误。`tests/unit/prompts/test_freeze.py` 的静态检查
从"每个 benchmark 最多注册 N 个 prompt"改为"注册的每个名字都必须在插件声明里"，
这样协议里任何一个模板改动都无法悄悄跨 run 续跑。

### 4. 每种模型只裸跑一次

旧规则是每个 run 各跑一份同 run 裸对照（`fresh_bare`），裸 Q 在不同 run 间本身
就有 2 个点左右的波动。新规则：

- ALFWorld 的每种学生模型（Q、D、X）各只跑**一次**裸跑（测试集 134 ×3，空库），
  冻结成 `configs/seed/alfworld_bare_<model>_<date>.json`；
- 之后所有带库臂（主表、实验二、三、四）的 test 相位一律用
  `external_campaign` 指向对应学生模型的冻结文件，不再各自重跑对照；
- 这份规则在看到任何新协议的带库结果之前定死，不按结果挑对照。

## 作废与重跑

- 旧协议的 ALFWorld 结果**全部作废**：主表、实验二四臂、实验三、实验四 a0/a1、
  以及旧协议的裸 X。它们仍留在各自 run 目录里，但不再进任何表。
- 新协议先跑裸 Q 的 134 ×3；用 12 题探针与外层统一 runner 的 8/12 对齐后，再跑
  裸 D、裸 X，最后跑带库臂。
- 旧协议下已经启动的 campaign（`e2_x2q`、`e4_a0`、`e4_a1_body`）在 2026-11-02
  下午停止，不续跑。

## 不改的部分

- 方法内部的考试场景、作者/审阅 prompt 不变。那是方法自己的学习信号，不是评测
  协议；这次只对齐"学生模型与环境交互"的那一层。
- SearchQA 仍用同 run 裸对照（`fresh_bare`），TB 仍用外部 campaign；本次改动只
  涉及 ALFWorld。
- 学习侧的 ALFWorld 词汇表（`learning.py`）不变：命令词汇仍是引擎的命令集，只是
  执行 prompt 不再枚举它（每一步由环境给出可行动作列表）。

## 验证

- 全量测试 3130 通过；分层检查通过；`ruff --select E9,F` 无命中。
- 12 题探针（同一批题、裸 Q、新协议）与统一 runner 的 8/12 比较；差异在二项
  噪声内才算实现正确。
- 134 ×3 全量裸 Q 与外层 AgentBrew 的 0.634 比较；这是最终验收。

## 冻结的裸跑结果（2026-11-03 用户确认）

用户明确不再重跑裸 Q，也不跑裸 X；以当前已知的两次裸跑冻结为后续全部带库臂的对照。

| 角色 | 文件 | 输出预算 | avg@3 | pass@3 | run |
|---|---|---|---|---|---|
| 学生 Q | `configs/seed/alfworld_bare_q_20261102.json`（sha256 `df45c9b7…`） | 4096 | 0.6269 | 0.7687 | `runs/e1_bare_q_20261102_1735/` |
| 学生 D | `configs/seed/alfworld_bare_d_20261103.json`（sha256 `8eb4d56c…`） | 65536 | 0.7587 | 0.8134 | `runs/diag_bare_d_64k_20261103_0933/` |

- Q 在 4096 下截断很少（402 次 rollout 中几乎没有 `finish_reason=length`），用户决定不为统一预算重跑；
- D 在 4096 下有约 19–20% 的回复被截断，65536 重跑后 avg@3 从 0.7090 升到 0.7587（配对 +0.0498，
  95% CI [+0.0100, +0.0920]），因此 D 的冻结对照用 65536 那次；
- 早先的 `alfworld_bare_d_20261102.json`（D@4096，0.7090）保留在磁盘上作为记录，不再被任何配置钉住；
- 该冻结发生在任何 65536 口径的带库臂启动之前，不按带库结果挑选。

学生模型之间因此存在预算差异（Q 4096、D 65536）；这是用户明确接受的取舍，报告里必须逐行标注
`output_budget`。
