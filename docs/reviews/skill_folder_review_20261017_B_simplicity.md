# 技能文件夹功能三路评审·B 路（简洁性）

日期：2026-10-17
说明：对分支 feature/multi-method 上 `36aad02..c36b8c2`（技能文件夹 M1–M5）做的纯静态简洁性评审，只读代码与文档，没有运行任何测试。
对应 log.md 条目：技能文件夹 M1–M5 各条（2026-10-17）。

## 总体判断

通过，有一处必须修的文档错误。五个里程碑的代码结构清楚：新模块的
docstring 都能让没读设计文档的人明白每个函数干什么；常量有名字有注释；
"契约原地复述"的每一处都写了为什么复述、原版在哪；测试名是完整的句子，
断言注释讲道理。要修的问题集中在两处：AGENTS.md 的目录树把同一个文件写
了两遍；一处"M3 再接线"的过渡注释在 M3 完成之后没有删。另有几条可读性
建议，不阻塞合并。

## 逐条发现

### 阻断

**B1. AGENTS.md 目录树里 `composites/skill_folder.py` 出现两次。**

- 证据：`AGENTS.md` 第 170 行（skill_status.py 之后）和第 177 行
  （phase_bank.py 之后）各有一条 `skill_folder.py`，描述措辞还不同
  （一条写"M2：按指纹闸门从相位库读正文写文件"，另一条写"M2/M3：C 相位
  派发前物化一次"）。原因可查：M4 的 log 条目写"目录树补 skill_folder.py
  （M2 遗漏）与 skill_reads.py"，M5 的 log 条目又写"目录树补两行：
  methods/delivery/folder.py、composites/skill_folder.py"，两次各补一次，
  合并后重复。
- 影响：AGENTS.md 的目录树是新人认识仓库结构的第一份材料。同一个文件
  出现两遍会让人以为组合层有两个同名文件。
- 直接修改建议：删掉第 170–171 行那一条（保留 phase_bank.py 之后、与
  skill_reads.py 相邻的那一条），让 `composites/` 一节每个文件只出现一次。
  合并冲突时用"每个文件恰好一行"作为检查标准。

### 偏移

**B2. `composites/skill_folder.py` 的模块 docstring 留着已完成的过渡注释。**

- 证据：模块 docstring 末尾
  "Wiring: M2 ships the function and its tests; the phase driver calls it
  in M3, once the ``real_folder`` execution version exists."
  （`src/expedistill/composites/skill_folder.py` 第 34–36 行）。M3 已经
  合入：`stages/phase_execute/final_test.py` 的 `_materialize_folder`
  在派发前调用它。
- 影响：读者会以为这个函数至今没有生产调用方。这属于任务书里"标注
  归 M3/M4 的过渡注释是否已清理"的检查项，这一处漏了。
- 直接修改建议：把这三行改成陈述事实的接线说明，例如：
  "Wiring: the C-phase driver (`stages/phase_execute/final_test.py`)
  materializes the phase's folder once, before dispatch, into the phase
  directory's `skill_folder/`."

**B3. "这行单元带文件夹清单"这一个判断，三个调用点写了三种样子。**

- 证据：
  - `stages/phase_execute/final_test.py` `_materialize_folder`：
    `if row.aux.get("skill_folder")`（按真值过滤）；
  - 同文件 `_folder_read_sets`：`if row.aux.get("skill_folder") is not None`
    （按非空过滤）；
  - `stages/phase_execute/engine.py` `_plan_units`：
    `row.aux.get("skill_folder") if row.aux else None`（多加了一层
    `row.aux` 判空），之后再判 `manifest.get("files")`。
- 影响：三种写法在当前数据形状下行为相同（清单恒为带四个键的 dict，
  `ExecutionUnitRow.aux` 的类型是必填的 Dict）。初级读者看到三种谓词，
  会怀疑差异是有意设计，需要逐处求证才能放下。这正是简洁性评审要抓的
  隐性成本。
- 直接修改建议：在 `engine.py` 里加一个五行的小函数，例如
  `folder_manifest_of(row) -> Optional[Mapping]`，写一句 docstring 说明
  "清单带 files 才算文件夹单元；空已接受集的清单不挂到任务上（D9 空注入
  教训）"。`final_test.py` 已经 import `engine`，两个调用点直接用它，
  三个判断合成一个。`row.aux` 那层多余判空一并去掉。

**B4. README 5.5 的 folder 交付条目是一个 24 行的单段，塞了约十个主题。**

- 证据：`README.md` 第 851–875 行。这一段里依次讲了：玩法定位、
  `aux["skill_folder"]` 清单形状、catalog.md 的行格式与使用规则头、
  物化位置与幂等、TB 的沙箱链路、提示词无 `{skills_block}` 槽、插件
  能力位、空已接受集裸跑、`max_injection_tokens` 的口径、读取统计的
  解析与摘要键、已知局限、泄题审计覆盖。全部挤在一个条目里，句子之间
  用分号连成超长句。
- 影响：README 是设计正本，也是初级读者的第一入口。5.5 节其余条目都是
  一个条目一个主题，folder 这条的信息密度比周围高一个量级，第一次读
  很难抓住主线"提示词只带指引，技能放文件夹，agent 自己 cat"。
- 直接修改建议：按主题拆成子条目，保持原文用词，例如：
  1. 玩法与提示词形态（指引段、无 `{skills_block}` 槽）；
  2. 清单的三个字段与"不带正文"；
  3. catalog.md 的行格式与使用规则；
  4. 派发前物化（位置、幂等、指纹闸门、空集裸跑）；
  5. 插件能力位与 E1 拒绝；
  6. `max_injection_tokens` 计目录文本；
  7. 读取统计与已知局限、泄题审计覆盖。

### 细节

**B5. README 的 v4 配置计数少一个。**

- 证据：`README.md` 第 270–271 行"仓库里 8 份 v4 配置
  `configs/experiments/*_v4.yaml`"。按这个通配符去数，仓库里实际有 9 个
  文件：本分支之前已有 8 个（M5 之前的旧文案写 7 份，当时就已经少写了
  `searchqa_split_live_smoke_v4.yaml`），M5 加了 folder 版变成 9 个，文案
  只从 7 改成 8。
- 直接修改建议：要么写 9，要么写"8 份正式配置加 1 份冒烟配置"，
  与通配符的实际结果一致。

**B6. 安全字符集在三个文件各写一份，都留了指针，但没有测试把三份绑在一起。**

- 证据：`methods/delivery/folder.py` 的 `_SAFE_CHARS`、
  `composites/skill_folder.py` 的 `_FILENAME_RE`、
  `composites/skill_reads.py` 的 `_NAME_ALPHABET`。三处的注释都写清了
  复述的原因（组合层不得 import 方法层）和原版位置，这一点合格。但仓库
  对同类"两处必须一致"的契约都有镜像测试先例（`FAILURE_CATEGORIES`、
  `SKILL_SUMMARY_FIELD`，见 `tests/unit/blocks/test_attribution.py`），这
  三份字符集没有对应的一条。
- 影响：将来有人放宽交付层的字符集（比如允许空格），组合层的物化和读取
  统计不会在测试期报错，只会在线上以 CodeBugError 停机。
- 直接修改建议：加一条单元测试，用同一批技能编号（含冒号、斜杠、点、
  中文等边界字符）驱动 `skill_filename`、`materialize_skill_folder` 的
  文件名校验、`skill_files_read` 的定界匹配三个入口，断言三者的接受面
  一致，测试注释里写明镜像关系。

**B7. README 5.5 folder 条目里的两处表述。**

- 证据：第 865 行"（空注入 = 空串，不渲染只剩头的空目录）"拗口，初读
  不知道"只剩头"指什么；第 873–875 行同一句里"供事后审计与技能使用
  分析……folder 单元的读取集供人工事后审计"把"供事后审计"说了两遍。
- 直接修改建议：前者改成"不渲染一个只剩表头的空目录文件"；后者保留
  一处"供事后审计"，删掉句末重复的半句。

**B8. `final_test.py` `_folder_read_sets` 的 docstring 里有一句指向未发生
工作的前瞻注释。**

- 证据："reported for audit (the scripts/audit consumption lands later)"
  （第 114 行）。log.md 的 M4 条目已经记了同样的注意点（"审计脚本消费
  读取集时按 with_bank 臂的 trajectory_id 对账"）。按 AGENTS.md 代码风格
  第 3 条，要做的事记进 log.md 就够，docstring 里留"以后会有"的说法属于
  无主 TODO 的变体。
- 直接修改建议：删掉括号里那半句，或改成指向 log 的中性陈述
  （"a fact for post-hoc audit"）。

**B9. `methods/delivery/folder.py` 的 `resolve` 把已接受集过滤了两遍。**

- 证据：`resolve` 调 `skill_folder_manifest(bank)`（内部过滤一遍
  accepted），随后又自己调 `units.accepted_bank(bank)` 求 `injected`。
  库很小，性能无关紧要；两遍过滤只是让读者多核对一次"两处口径是否相同"。
- 直接修改建议：可改可不改。要改的话让 `resolve` 先取 accepted，再把
  accepted 传给一个接受"已过滤列表"的 manifest 构建入口。

## 核对通过清单

1. **摘要上限 500**：`SKILL_SUMMARY_MAX_CHARS` 放在 `core/models.py`，
   常量注释讲清了三件事——为什么放 core（两个 blocks 共用且互不
   import）、为什么给 500（只拦失控的输出，prompt 本身要求一两句）、
   为什么 K5 合并产物不受它管（没有模型写它）。写侧校验
   （`blocks/author.py` 与 `blocks/attribution.py` 的 `_validate_summary`）
   共享这一个常量，并有镜像测试。
2. **目录行 200 截断**：`_LINE_MAX_CHARS` 命名自明，截断加显式
   " [truncated]" 标记，docstring 讲了为什么在读取侧截（K5 产物无写侧
   上限）。测试覆盖 200 / 201 / 250 三个边界。
3. **占位符**：`_NO_DESCRIPTION = "(no summary)"` 是显式占位，常量带
   注释（"never a silently blank column"），有测试。
4. **`CATALOG_FILENAME`**：注释解释了为什么是常量（prompt、物化、读取
   统计三个消费方都按它取文件，所以不能做成参数）。
5. **契约复述的指针**：三处字符集复述都注明原版与复述原因；
   `skill_folder.py` 复述清单字段形状时注明 "frozen by the delivery
   module"；`FOLDER_DELIVERY_NAME` / `FOLDER_EXECUTION_NAME` 放 core 的
   原因（core 不得 import 方法注册表，沿用 `EXECUTION_NAMES` 先例）写在
   常量注释里；`RealFolderExecutionParams` 讲清了 `max_injection_tokens`
   对 folder 计目录文本。
6. **过度设计检查**：`FolderParams` 是与家族一致的空参数类；能力位用
   prompt 名（有裁决记录）；物化按相位做一次（逐单元只是复制同字节，
   有测试）；E1 守卫放在单元与插件相遇的唯一边界；`skill_reads.py` 的
   `_name_token_in` 手写定界匹配有成立的理由（'.' 和 '-' 属于文件名字符
   集，正则的 `\b` 用不了），docstring 讲了。
7. **死代码检查**：`render_catalog`、`when_to_use_line`、`skill_filename`
   都有生产调用方或作为交付流程的内部步骤，另有直接测试；没有找到无人
   调用的辅助函数。
8. **测试可读性**：`test_folder_m2/m3/m4` 系列的测试名都是完整句子
   （例如 `test_a_bank_that_moved_under_the_manifest_is_refused`），断言
   注释讲"为什么"（例如 K5 截断测试的三个边界各自标注意图），fixture
   （`_bank`、`_CAT_TRAJECTORY`）自明；读取统计的已知局限（同名文件
   误记、只有 cat 算）各有专门的测试。
9. **配置样例**：`configs/experiments/tb21_split_folder_v4.yaml` 与既有
   v4 配置同构，C 相位两行（`delivery: {name: folder}`、
   `execution: {name: real_folder}`）与 README 2.4 的片段一致；log 记录
   了实际加载验证。初级读者照 README 2.4 的片段与第 7 节的命令行可以
   启动。
10. **对外总览 v2**（`~/Documents/Manager/state/replies/consumed/
    mm_system_overview_v2.md`）第 5 节的 folder 三段白话说明：主线一句
    话讲清（技能放文件夹、提示词只带指引、agent 自己 cat），能力位用
    一句话解释，"目录文件每一行拿 summary 当描述"与代码一致；36 / 144 /
    108 的计数口径与 README 2.6 一致，"folder 只有 tb 可跑、配置能加载、
    执行时拒绝"与 E1 守卫的行为一致。
11. **prompt 与校验器一致**：G2 两个模板、REWRITE、LIVE_SKILL_REVISION
    的输出契约都加了 `summary` 字段，模板键名有测试固定
    （`test_revision_template_output_contract_carries_the_summary` 等）。
12. **log.md 纪律**：M2 的"清单带正文"偏离被用户否决后，修订条目写清了
    原因与新形状；M3 的时间写错按规则追加更正条目，没有改旧条目。
