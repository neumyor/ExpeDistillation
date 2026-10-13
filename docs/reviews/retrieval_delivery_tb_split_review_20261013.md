# 整合方案（检索式分发 + TB 冻结划分复用 + 并行实施）评审记录

日期：2026-10-13
一句话说明：对 docs/decisions/retrieval_delivery_tb_split_20261013.md 初稿的三路独立评审（第一轮）汇总与处置；每路均对照真实代码与旧 run 实测数据核验。
对应 log.md 条目：与修订版方案一并记录。

---

## 第一轮（初稿，三路独立）

三路总评一致：**通过但需修改**（两路标 2 个 blocker、一路标 1 个 blocker + 4 major）。
三路独立核验为真的机制断言（零调用复用链全环、F=16、246 轨迹、41/28/20、seed 42
与 revision、3 超时单元在 validation、K5 短路、format_skills_block 子集复用、
skills_injected 既有形状）高度一致，方案核心成立。

### 收敛的关键发现与处置

| # | 发现 | 提出方 | 处置（落修订版 §0 + 正文） |
|---|---|---|---|
| 1 | **[blocker] 库过滤口径 44≠42**：初稿声明全链规则但数字按锚点算；44 会把含 validation/test 溯源的混合 merge 放进库（`bank/merge/0040` 链含 test 题 qemu-alpine-ssh——测试集泄漏） | 三路独立（R1-F1/R2-B2/R3-2，R3 实测分解 97=42+25+26+3+1） | 钉死全链口径、库=42；库过滤与 skill 拷贝共用单一溯源函数（契约 10）；16 条悬空 merged 原件记入 ASSEMBLED_FROM；验收门 2 改 42 |
| 2 | **[blocker] TB 检索输入不存在**：插件接口无 task_prompt(task)；TB 的 Task.instruction 只是任务名，真实指令在远端 | 三路独立（R1-F2/R2-B1/R3-3） | 检索文本钉 `Task.instruction`；如实声明 TB=任务名词面匹配、结论不外推 ALFWorld；新增零成本检索预检门（门 4：48 任务全覆盖检查） |
| 3 | **[blocker] stage0_freeze.py + test_stage0.py 无主**：`_explicit_split_ids` 只并 train\|test，三列表必炸 | 三路独立（R1-F3/R2-M1/R3-1） | 两文件划归 C；manifest/summary 增 reserved 键 |
| 4 | **[major] 复用配置同一性未钉**：模型名/n_attempts 不一致会 ConfigError（n_attempts 偏大会**静默执行**缺失单元烧钱）；token_budgets 缺失 handle 建不起来 | R1-F4/R2-M4/R3-5 | 装配时对照源 manifest 五项同一性校验（benchmark/双模型名/双 n_attempts/三规则名）；配置字段从 tb21_full_v2.yaml 逐字复制 |
| 5 | **[major] namespace"天然逐单元"是错误断言**：`_run_namespace` 每次 batch 调用只算一次（`if pending:` 内），per-task 注入必须下沉单元级 | R2-M2/R3-6（R1 遗漏项 6 同） | 修正方案文字；B2 简报钉死下沉实现 + 同任务 memo + 既有 test_run_namespace 系列语义保持义务 |
| 6 | **[major] 装配负清单缺失**：curation_log.json 拷入必炸（`_read_final_member_ids` → load 缺失成员 FileNotFoundError） | R3-4（另 R1-F5 通用缺口） | allowlist + 负清单断言 + 完整性不变量；K4 账本核验结论落字"不需要拷" |
| 7 | **[major] W1 文档回写必须先于派发**：节标记常量尚未存在，所谓移址实为改 W1 §3.3 决定；不回写则 A2 照旧文档与 A1 双源 | R2-M5/R3-13 | 已执行（stage23 文档修订三，派发前置条件） |
| 8 | **[major] split_tasks 三元组涟漪**：build_skillopt_split_configs.py 二值解包是运行时崩点（纯脚本无测试）；blocks/__init__.py "several agents append here" 违反独占 | R3-7 | 契约 3 钉新返回形状与两个调用点（A1/C 各改一处）；契约 9：__init__ 只由编排者改 |
| 9 | **[minor] 检索审计幂等**：续跑会重复追加 | R2-m1/R3-8 | dispatch 前一次写出（确定性内容），B2 简报 |
| 10 | **[minor] min_score 校准判据必须预注册**（网格、代理指标、一次性冻结、校准不进报告表） | R2-m2/R3-2 | §1.5 门 4 + §4.3；校准属编排者职责 |
| 11 | **[minor] 阈值只门 top-1**（第 2..k 名可低于阈值）属设计选择须钉死 | R3-9 | 契约 11 + docstring 义务 |
| 12 | **[minor] freeze 脚本位置违 AGENTS §五**（analysis 只读） | R1-F8/R2-m5/R3-10 | 移 scripts/run/；正本 = AgentBrew JSON，脚本生成+校验 |
| 13 | **[minor] 杂项**：装配取 RunLock/拒绝已有 manifest（R3-11）、ASSEMBLED_FROM 记播种溯源（R2-m7）、审计预期清单（R3-遗漏 3）、零增量门口径双态可过（R3-遗漏 4）、W1/W3 文档交叉引用（R3-遗漏 5）、v1 规则+v2 守卫的响亮停批语义写进验收门（R2-m8/R3-15）、skill_delivery 键使旧 run 拒续跑知情记录（R2-M4 连带）、IDF 刻度随库变（R2-2）、42 条库上两步路由收益有限的预期管理（R2-m3/R3-m3） | 各自 | 全部落修订版对应小节 |

### 8 个开放问题的三路裁决（一致）

1. 类别归并：采纳 derive_categories（开局推导，docstring 钉死语义）；
2. 打分：钉死 TF-IDF 余弦；
3. min_score 用 validation 校准：可接受（预注册判据 + 独立冒烟 run）；
4. 节标记移址 core/models.py：同意（硬条件=先回写 W1 文档，已执行）；
5. v1 规则名 + v2 digest 混装：可接受（零调用 + 守卫自检）；
6. ALFWorld v2 同批启用 retrieval：同意 + 预登记联合体归因；whole_bank 消融列为可选后续；
7. 并行矩阵：方向正确，补 stage0_freeze.py/test_stage0.py 归 C、tb21_split_reuse.yaml 移交 C、blocks/__init__.py 编排者独占；
8. 旧库无标记全文同权：采纳（组名不进查询词汇；×3 加权在本 run 不生效的预期写明）。

### 结论

初稿核心（零调用复用、纯词法检索、冻结三划分、8 代理互斥矩阵）三路认可；按上表
修订后产出**逐代理模块级细化方案**（修订版 §3），交第二轮三方评审。

---

## 第二轮（逐代理详细方案，三路全新独立）

三路总评一致：**通过但需修改**。修订版的核心机制链全部实测复核为真（42/44
全链重算、F=16、246 轨迹、K1–K5 五环零调用、pipeline 零涟漪、8 简报 43 文件
两两互斥零交叠），一轮 13 组处置 12 组完全落实。修订全部为文档/编排级，三路
一致结论：**无需第三轮评审，对照收敛即可派发**。

### 二轮发现与处置（全部已并入最终版 §0 十五条裁决）

| # | 发现 | 提出方 | 处置 |
|---|---|---|---|
| R2-1 | **min_score 校准协议本体丢失**（§4.3 悬空引用；网格值/判据/探测载体无正文；门 4 与启动序列顺序循环）[major] | 三路（R1-N3/R2 裁决5/R3-#2+Q5） | §0 裁决 8：协议本体（网格 {0.02,0.05,0.10}、选定规则、非退化判据、唯一出口、披露句）写进决策文档；独立冒烟 run 取消（纯 select_skills 零执行探测，产物进 tmp/） |
| R2-2 | **8 并发隐含硬依赖链**（A2/B1←A1 常量、A3←A2、A4←A1+A2、B2←A1+B1、C←A1），与"自跑 pytest"义务矛盾 [major] | R3-#3 | §0 裁决 11：三波次派发（A1+D → A2/B1/C → A3/A4/B2）+ ImportError 属预期不得代写义务 |
| R2-3 | **生成器 render_config 不完整**（--check 必炸/静默覆写手写件；默认模式生成 searchqa_v2 无人知）[major] | R1-N4/R3-#4 | §0 裁决 12：逐字节一致义务 + searchqa_v2 声明 |
| R2-4 | **"16 条悬空 merged 原件"归因错误**（实测真悬空 5 条，11 条被库内 merge 正常消费；三路独立实测一致并点名 5 条清单）[major] | 三路（R1-N2/R2-#1/R3-#1） | §0 裁决 1：11+5 两组记录、5 条点名、装配断言不得硬编码 16 |
| R2-5 | **composites/__init__.py 无主** | R2-#2/R3-#9 | §0 裁决 13：两 __init__ 均编排者独占 |
| R2-6 | 一轮 §0 裁决 1 的例证错误（merge/0040 锚点在 test 本就会被锚点口径剔除；真实锚点漏入是 0004/0011 且性质是 validation 溯源非 test 泄漏） | R1-N1 | 最终版裁决 1 措辞已更正（漏入 = 2 条含 validation 溯源的 accepted merge，触发 stale 拒绝或库内混入 reserved 溯源） |
| R2-7 | 同一性校验升级为白名单 diff + tasks_digest 对照 + revision 知情声明 | R1 裁决4/R2 裁决4 | §0 裁决 4 |
| R2-8 | 复制清单漏 segment_retry_limit/model_options/weak_profile | R2-#4/R3-#11 | §0 裁决 6：全方法键逐字复制 |
| R2-9 | §1.3 不变式与 audit 整拷自相矛盾（1 行 validation 空 record） | R2-#3 | §0 裁决 5：不变式适用范围钉为 store 五类对象与库行 |
| R2-10 | 负清单灰区（leak_verdicts/rewrite_failures） | R3-#13 | §0 裁决 5 增补 + "未列即不拷" |
| R2-11 | stage0 恒含三键 / 两键 reserved=[] 未钉（test_stage0 断言改法无依据） | R2-#9 | §0 裁决 7 + 契约 3 补句 |
| R2-12 | routed 语义 / 检索文档定义 / 无组行为 / audit 路径与写出方式 / 空注入取值 五处欠钉 | R2-#8/#12、R3-#6/#12、R1-N8 | §0 裁决 15 + 契约 4 |
| R2-13 | "既有形状"不实（skills_injected 是 Any，dict 列表为新形状） | R3-#5 | §0 裁决 13、§2.3 措辞更正 |
| R2-14 | B2 简报 per-task 求值位置歧义（_check_stored 在 pending 判定前跑）+ 缺续跑比对用例 + 三个 namespace 测试须点名保护 | R1-N5 + 三路 Q3 裁决 | §0 裁决 3 + B2 简报补充 |
| R2-15 | 42 库实为 29 组；归并在该库因巨型类效应近似惰性（0.15→1 类 / 0.20→6 / 0.30→18） | R2-Q6/R3-Q6 实测 | §0 裁决 10：阈值 0.25、8–18 类预期、惰性预期管理句、fixture 改 29 组真实形状 |
| R2-16 | 检索分数双峰实测（18/48 零重叠、median 0.07、max 0.44；qemu 系被滤光 = 防泄漏与检索信号对撞） | R3 实测 | §0 裁决 2 + 裁决 8 披露句（协议冻结前已知分布，如实披露） |
| R2-17 | stage23 §3.3 常量块重复粘贴（修订三编辑事故） | R2-#6/R3-#8 | 已修复（去重）；A1 按契约 1 单一版本为准 |
| R2-18 | §0 交叉引用漂移（§4.3/4.4/4.5 悬空）、§5 问题 6 "62 组"前提错 | R1-N6/R2-#7/R3-#15 | 最终版全文重写时清除 |
| R2-19 | 集成门未列 D 复核步骤；设计定稿 log.md 条目缺失 | R2-#13/R3 | §4 序列补 D 复核；log.md 派发前补记 |
| R2-20 | 旧 run 拒续跑原因链不完整（prompt digest + skill_delivery + split 三键） | R2/R3 | §0 裁决 14 |

### 6 个开放问题裁决（三路）

1. 所有权：互斥零交叠确认；遗漏四处（composites/__init__、校准冒烟载体、生成器 render_config、陈旧注释）全部补齐。
2. 契约 3 对 pipeline.py 零涟漪：三路实测确认（pipeline 只读 summary 两键；seed_tb_stage1.py 只读 manifest split.train）。
3. namespace 语义保持义务：三个既有测试点名保护 + 措辞统一。
4. 同一性校验：白名单 diff 升级 + tasks_digest + revision 声明。
5. 门 4 判据：按 §0 裁决 8 预注册（网格/选定规则/50% 注入率下限/类别数 sanity/唯一出口/披露）。
6. 类别数预期：阈值 0.25、8–18 类、惰性预期管理、29 组 fixture。

### 二轮结论

最终版（修订二）已按上表全部收敛；三路一致免第三轮评审。按用户指令进入实施：
波次派发 8 代理。
