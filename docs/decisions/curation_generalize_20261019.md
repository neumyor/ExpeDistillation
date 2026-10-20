日期：2026-10-19
一句话说明：整理模块（agentbrew / K5）改造的用户裁决：默认关闭回归考试、修正 G7 相似标准、G7 可标记低价值技能、G8 改为泛化优先并输出摘要、G7 输入方式可选。
对应 log.md 条目：2026-10-19 整理模块改造方案定稿（用户裁决）。

---

## 1. 背景

folder 版 C 终测（`runs/tb21_split_folder_20261017_1339`）复用了源 run
`tb21_split_live_run_20261015_0045` 整理出的 141 条技能库。事后分析发现库的问题
有相当一部分出在整理阶段：

- 源 run 用 `merge_check: exam`，三轮共尝试合并 100 次，47 次被回归考试撤回。
  回归考试让合并产物重做每条来源技能的原题，而原题考的是完整任务，跨任务的
  通用技能（提交仪式 13 条一组、Hugging Face 下载教训一组）合并后必然在某道原题上
  失败，三轮每轮都被重新提名、每轮都被撤回；
- 整理没有删除动作，任务专属、缺乏泛化能力的技能（约 85 条）彼此不相似，三轮
  一次都没被碰过；
- G7 提示词要求相似组 "same trigger conditions"，又要求 "judge by literal meaning,
  not surface wording"，两条标准互相矛盾；
- G8 提示词要求 "keep every piece of advice" 且 "do not invent new advice"，等于
  禁止把多个任务专属的触发条件归纳成一个通用条件；合并只能逐段拼接；
- G8 输出纯文本，没有摘要，合并技能的摘要由来源摘要用分号拼接，旧库全为空。

## 2. 裁决

直接修改现有 agentbrew 实现，不新建可选的整理分支。

### 2.1 回归考试默认关闭

`AgentBrewParams.merge_check` 默认值由 `exam` 改为 `none`。显式写了
`merge_check: exam` 的配置行为不变。实现时核实：v2/v3 配置经升级链转成 v4
时，升级函数总会显式写出 `merge_check`（exam 验收写 `exam`，live 验收写
`none`），仓库里现有的 v4 配置也都显式写了这一项，所以默认值的变化只影响
今后手写、且省略了这一项的 v4 配置。参数说明里注明了日期与语义变更。

### 2.2 G7 相似标准统一

删除 "same trigger conditions"。统一为一条标准：两条技能教的是同一种做法或
同一个教训，即为相似。来源任务名、原题特有的报错原文、文件名、数值上的差异
不影响相似判断。"做法类只和做法类、教训类只和教训类"的限制保留，代码中的
类型门不变。

### 2.3 G7 标记低价值技能，开关决定是否丢弃

- G7 的 JSON 输出新增字段 `low_value`：缺乏泛化能力的低价值技能 id 列表；
- 新增参数 `drop_low_value: bool`，**默认 `False`**：
  - `True`：被标记的技能在本轮合并单元派发之前退出接受集，丢弃原因记为
    `low_value`，不进入 G8；
  - `False`：标记结果只写进整理日志，不做任何处理；
- 设计从简：不做二次复核、不跑考试、不设数量上限。

### 2.4 G8 提示词与输出格式

1. 输出改为 JSON：`{"content": "<合并后技能正文>", "summary": "<一句话摘要>"}`。
   K5 把 `summary` 直接写进合并技能，取代分号拼接来源摘要的做法；
2. 泛化性是第一要求，写在提示词最前；
3. 合并时重构为通用形式，不逐段拼接；来源中任务专属的触发条件要归纳成一个
   通用触发条件。原规则 "do not invent new advice" 改写为允许这种归纳（仍不得
   加入来源中没有的做法）；
4. 两种合并方式分别要求：
   - 去重（dedup）：汇总成可推广的经验，不过度围绕某一个具体场景；
   - 冲突（conflict）：先分析两条建议冲突的原因，再写清在什么条件下按哪一条做。

### 2.5 只检查格式，不检查内容质量

代码层只做下列检查，不满足即为 ModelOutputError，走现有的格式修复与重试路径：

- G8 输出是合法 JSON，`content` 与 `summary` 两个字段都在；
- `summary` 非空、单行、不超过 `SKILL_SUMMARY_MAX_CHARS`（500）；
- 合并技能的段落标记与来源一致（现有后置检查，不变）；
- G7 的 `low_value` 中每个 id 都在本轮输入的技能里。

泛化程度、冲突分析的质量完全依赖模型自觉，单元测试不验证这些。

### 2.6 G7 输入方式可选

新增参数 `similarity_input`，取值 `full` | `summary`，**默认 `full`**：

- `full`：保持现状，每条技能传 id + 压成一行的正文；
- `summary`：每条技能只传 id + 摘要；任一技能摘要为空时**响亮报错**
  （ConfigError，在调用模型之前），不退回全文。后续产出的技能默认都带摘要，
  旧库的摘要由用户另行补充。

该开关只作用于 G7；G9 分组的输入不变。

## 3. 实现要点

- 参数：`AgentBrewParams` 增加 `drop_low_value`、`similarity_input`，
  `merge_check` 默认值改为 `none`，未知取值响亮拒绝（沿用现有 `__post_init__`）；
- G7：`blocks/curate.find_similar` 返回值增加 `low_value`；校验沿用
  "id 必须在输入中"的响亮失败规则（含既有的 `/skill` 后缀恢复）；
- 日志：`low_value` 随 G7 输出一起写进该轮记录，续跑时重放日志、不重问模型；
  读到不含该字段的旧记录按空列表处理。`_check_store_matches_log` 的期望接受集
  公式补上"减去本轮按 `low_value` 丢弃的技能"；
- 丢弃：`drop_low_value=True` 时，在派发合并单元前由主线程把被标记技能
  置为 discarded（原因 `low_value`），随后 `_resolve_group` 自然把它们从
  相似组和冲突对里剔除（剩余不足两条的组照旧记为 skipped）；
- G8：`blocks/curate.merge_skills` 改为 `output_json=True`，返回
  `(content, summary)`；K5 构造合并技能时用模型给出的 `summary`；
- 提示词：SIMILARITY、MERGE 两个提示词文本改动，digest 随之变化。按仓库
  规矩，提示词改一个字即为新实验，必须新建 run 目录，不得在旧 run 上续跑；
- 测试：假模型脚本里 MERGE 的返回从纯文本改为 JSON；新增覆盖
  `low_value` 两种开关、`similarity_input=summary` 的正常与缺摘要报错、
  G8 摘要格式校验的用例；续跑等价测试照旧要求通过。

## 4. 影响与边界

- 改动不会自动修复旧的 141 条技能库。要看效果，需用新代码对源 run 的 201 条
  已接受技能重新整理，再重跑 C 相位；
- 默认参数下（`drop_low_value=False`、`similarity_input=full`），与现状的差异
  只有：回归考试默认关闭、G7/G8 提示词文本变化、G8 输出带摘要；
- 本裁决不改动找证据、写作、决策、验收的边界；"整理不重新判定技能是否有效"
  这一边界由 `low_value` 开关在 G7 层面放宽，只在显式开启时生效。

## 5. 后续：G9 的输入开关

2026-10-20 新增独立的 `grouping_input`，只控制 G9 看正文还是摘要；本文件的
`similarity_input` 仍然只控制 G7。见
`docs/decisions/curation_grouping_input_20261020.md`。
