日期：2026-10-17
一句话说明：v4 相位世界补上"终测对照复用外部 campaign 成绩"机制（旧系统
control_source: external_campaign 裁决的对应物），以及装配器身份白名单为
folder 复用补上执行名叶子。
对应 log.md 条目：2026-10-17 外部 campaign 对照机制落地。

## 背景

TB 正式实验（tb21_split_live_run_20261015_0045）的旧世界裁决（用户
2026-10-16）：终测的裸跑对照臂**不再重跑**，对照读 node29 AgentBrew
campaign 的既有成绩（同一字节级 harness、同一任务集），run 自己的摘要记
`control_source: external_campaign`、control/delta 置空，对比数字由外部对照
报告持有。旧实现里这不是一个机制，而是一个环境变量开关（EXPEDISTILL_
STAGE6_CONTROL=skip）加一份人工报告。

技能文件夹实验（复用旧 141 成员技能库、只跑 folder 版 C 相位）需要一个
可复跑、可审计的 v4 对应物：对照零执行、delta 按外部成绩算、身份可比。

## 候选方案

1. **照搬旧做法**：对照臂跳过，摘要 control/delta 置空，对比留给事后报告。
   否——手工报告没有加载期校验，成绩文件漂移无从发现，delta 口径不进
   摘要就会每次分析重算一遍；
2. **外部成绩在装配时拷进 run 再当对照仓读**：否——对照臂会被伪装成
   "跑出来的轨迹"，和真实执行不可分辨，审计语义错误；
3. **配置面加一种对照来源**（采纳）：C 相位 compare 加第三个取值
   `external_campaign` + `external` 块钉住成绩文件身份，加载与派发前
   校验，对照臂解析为外部成绩而非轨迹仓读取。

## 裁决内容（方案 3 的设计取舍）

1. **身份钉死在冻结配置里**：`external` 块三键——`path`（相对路径相对
   配置文件解析、冻结为绝对路径，与 tasks 路径同规则）、`sha256`（成绩
   文件字节的校验和；漂移即 ConfigError，新旧摘要并列报出）、`model_key`
   （campaign 汇总里对应本 run 弱模型的前缀，qwen ↔ qwen3.6-flash-distill
   的映射由配置显式声明）。三键缺一不可，不走默认值。
2. **对照口径预登记**：每任务取**前 n_attempts_weak 次采样**的奖励均值
   （campaign 归档每任务 5 次，实验对齐深度 3），与旧外部对照报告的
   "前 3 次"对齐列同口径；归档尝试全部是已判分尝试，对照臂没有 infra
   概念。奖励向量原样进 C 相位摘要的 `external_control` 审计记录，成绩
   文件字节拷进相位目录（`external_control.json`），事后换口径不用重跑。
3. **校验时点**：stage 0 冻结步校验文件存在 + sha256（run 不该为坏文件
   起跑）；C 相位派发前校验任务覆盖、model_key 覆盖、奖励数 ≥ 对齐深度、
   奖励值 ∈ {0,1}——全部在任何带库臂花费之前。
4. **适用范围**：`external_campaign` 只允许 test 角色相位（A/B/D 的对照
   必须是本 run 真跑过的测量）；live 相位的"必须有 baseline 对照"检查
   对它同样拒绝。
5. **成绩文件进仓库**：`configs/seed/tb21_agentbrew_campaign_summary.json`
   是 TB-dev `configs/seed/summary.json` 的字节级拷贝（AgentBrew 最终归档
   89 任务 × 5 次采样 × 两模型），campaign 侧重导出后按新 sha256 换新
   文件名，不修改既有文件。

## 装配器白名单补充（第二个提交）

身份比对（ruling 4）原本只放行终测相位的 delivery 与 compare。folder 交付
与 real_folder 执行由加载期第 8 条检查强制一对一配对——执行名是交付名的
强制孪生，目标配置根本不能自由选它。只放行 delivery 不放行其孪生会让
"retrieval 源 → folder 目标"的复用被拒。裁决：白名单加
`config.phases.<终测名>.execution.name` **叶子**（只有名字；n_attempts、
max_injection_tokens 等改变测量口径的叶子仍然拒绝）。

## 后果

1. C 相位摘要新增 `external_control` 键（仅 external 对照时存在）；
2. `control_source` 词表加 `external_campaign`（沿用旧系统拼写，E3 钉的
   词表加一个值，旧值不变）；
3. 冻结 manifest 的 compare 形状多一个 `external` 键（null 或身份块），
   金矩阵全量重生成随本提交落地；
4. audit 的 check 9 对 external 对照不再找对照单元，改为校验相位目录里
   成绩文件拷贝的 sha256。
