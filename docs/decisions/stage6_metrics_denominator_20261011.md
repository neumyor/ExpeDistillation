日期：2026-10-11
一句话：阶段 1 / 6 的成绩统计改为按"应有单元集合"计分，基础设施空洞不得静默通过，并新增两个运维开关。
对应 log.md 条目：2026-10-11 11:44（对照设计 review 全部实现，分波修复并经三次独立对抗式复审）。

## 背景

K1（`composites/batch_execute.py`）从不保存耗尽基础设施重试的单元，所以在真实
数据上，基础设施错误表现为"轨迹缺失"，而不是一条 `infra_error` 轨迹。旧的
D3 只数返回的轨迹，后果：

- 某题所有尝试都耗尽时，该题从所有分母里消失；`avg_at_n_conservative` 与条件
  口径的分母相同，保守口径的 delta 在真实数据上等于没做；
- `infra_tasks` 永远是空列表；pass@N 的分母也少了这些题；
- 两个臂的题数可以不同，却照样报 delta、退出码 0（review 探针：带库臂
  tasks=1、对照臂 tasks=2，delta -1.0，没有任何警告）。

我先前为 stage 6 写的保守口径测试是往 store 里直接写 `infra_error` 轨迹，
K1 实际从不这样存，所以那个测试通过的理由是错的；已删除，改为走真实 K1 路径
（假插件返回 `infra_error`）。

## 决定

1. `blocks/metrics.compute_task_metrics(trajectories, expected=None)`：
   `expected` 是"带前缀 task_id → 冻结的 n_attempts"。每个应有却没有轨迹的
   单元记为一次 infra（计入 attempts）。不在 `expected` 里的轨迹直接报
   ValueError（调用方的单元集合与轨迹对不上，静默扩大分母会掩盖问题）。
   `expected=None` 保持旧行为。
2. 阶段 1、6 对每个臂都传完整的应有单元集合；阶段 6 两个臂共用同一个集合，
   并断言两臂的 metrics 覆盖同一题集（不一致是 CodeBugError）。
3. 空洞不静默：K1 跑完后若有缺失单元，先写 summary（`infra_missing`、
   `complete: false`、`infra_shortfall_accepted`），再抛 InfrastructureError
   （与阶段 2–4 同一处置），CLI 退出码 3，同一条命令重跑即续跑。阶段 1 的
   `infra_missing` 是平铺列表（两臂角色不同，ID 不冲突）；阶段 6 的是
   `{"with_bank": [...], "control": [...]}`，因为轨迹 ID 不含臂信息，平铺会有歧义。
4. full 模式下阶段 6 的对照（复用阶段 1）有缺失：改报 InfrastructureError
   （原为 ConfigError），并且**在执行带库臂之前**就报。理由：重跑同一条命令
   会先续跑阶段 1 补洞；补上后 F 可能变化，库也可能变化，此时先跑的带库轨迹
   可能对应了错误的库。
5. 两个运行开关（都不进 `ExperimentConfig`、不进 manifest）：
   - `allow_infra_shortfall`（`run_pipeline` 参数 / CLI `--allow-infra-shortfall`，
     默认关）：用于 TB 中永久坏掉的题。打开后阶段 1、6 带着空洞继续，空洞仍
     计为 infra；summary 记 `infra_shortfall_accepted: true`，ledger 行顶层也带
     这个标记，并向 stderr 打 warning。**它不是纯运维开关**，见下文"更正"；
   - `infra_breaker`（`run_pipeline` 参数 / CLI `--infra-breaker`，默认 5，0 关闭）：
     传给阶段 1、6 的每次 K1 调用。熔断跳过的单元同样是缺失单元，按第 3 条处置。
   两个开关都写进退出码 3 时打印的续跑命令。
6. 阶段通过 context 上的属性读取这两个设置（`getattr(ctx, ..., 默认值)`，与
   `workers` 相同的做法），因为 `StageContext` 还没有对应字段。

## 更正（同日，re-review 问题 C）

上文第 5 条最初把 `allow_infra_shortfall` 和 `infra_breaker` 一起归为
"运维开关、续跑时可改、不影响测的是什么"。对 `allow_infra_shortfall` 这个
说法是**错的**：带着空洞时，阶段 1 从有洞的样本推出 F，阶段 2–5 据此建库；
若之后不带该开关续跑，空洞被补上，F 改变，库也随之改变，而阶段 6 已存的带库
轨迹对应旧库，K1 会以 ConfigError 拒绝续跑——run 目录就此报废（已用测试复现：
`skill bank is stale ... start a new run directory`）。

更正后的决定：**接受空洞是本 run 的持久决定，不是单次调用的开关。**

1. 阶段 1 带开关遇到新空洞时，先把被接受的单元 ID 写入
   `stage1_baseline/accepted_infra_shortfall.json`（`{"unit_ids": [...]}`），
   再推导 F、写 summary；
2. 阶段 6 同理，按臂写入 `stage6_test/accepted_infra_shortfall.json`
   （`{"with_bank": [...], "control": [...]}`）；
3. 之后每次调用（无论是否带开关）都读这些记录，把 ID 作为 `skip_unit_ids`
   传给 K1，永不重跑；即使某个被接受的单元后来出现了轨迹，也照样按空洞计分，
   所以 F、summary、阶段 6 的 delta 都固定不变；
4. 不在已接受集合里的缺失单元照常走 InfrastructureError；开关只决定"新空洞
   是否接受"；
5. full 模式下阶段 6 的对照空洞就是阶段 1 的空洞，**只能在阶段 1 接受**：
   对照的已接受集合等于阶段 1 的记录，阶段 6 自己不接受对照空洞（否则阶段 1
   不知情，之后续跑阶段 1 会补上它，F 和库又会变）。其余对照缺失一律在执行前
   报 InfrastructureError（带不带开关都一样），重跑时阶段 1 先续跑，带开关就
   在阶段 1 持久接受。
6. 运行设置在 `run_pipeline` 一开始（取锁、建客户端之前）统一校验，非法值
   报 ConfigError，CLI 退出码 1（re-review 问题 D）：`workers` ≥ 1 的整数、
   `llm_infra_retries` ≥ 0 的整数、`llm_infra_backoff_seconds` 为有限且 ≥ 0、
   `infra_breaker` ≥ 0 的整数、`allow_infra_shortfall` 为 bool。顺带修复：
   配置文件不存在 / 读不了时原来漏出 FileNotFoundError traceback，现在同样报
   ConfigError。
