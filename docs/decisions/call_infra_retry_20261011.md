日期：2026-10-11
一句话：B1 `call_model` 增加按调用的基础设施重试；请求落盘改为加锁 + 不覆盖发布；本地插件的请求落盘跟随句柄的 run_dir。
对应 log.md 条目：2026-10-11 11:44（对照设计 review 全部实现，分波修复并经三次独立对抗式复审）。

## 背景

- 客户端 `OpenAICompatClient` 故意 `max_retries=0`，把 HTTP 状态 / 连接错误映射成
  `InfrastructureError`，重试预算留给调用方。但只有 K1 在单元级重试；切段、写 skill、
  出题 / 判卷、改写、整理这些调用没有任何重试，开发中两次因一个网关 502 停掉整条流水线；
  本地 ReAct 一局里一个 502 就让整局作废。
- `_RequestLog` 先扫目录取最大序号再写 `NNNN_<prompt>.json`，临时文件名固定为
  `.json.tmp`；K1 的 B5 线程池共用一个 run 目录，两个线程会拿到同一序号，一份落盘覆盖另一份
  （或在 rename 时找不到被别的线程改名走的 tmp 文件）。B7 的 run 锁只防其他进程，不防同进程线程。
- SearchQA / ALFWorld 插件把单元 `workdir` 当作 `run_dir` 传给 `run_react`，覆盖了 K1
  挂在工作句柄上的 run_dir，请求落盘散落到各单元目录，违背 README 第二节
  "B1 把每次请求记录在 `<run>/llm_requests/`"。

## 决定

1. **重试配置挂在 ModelHandle 上**：新增 `infra_retries: int = 0`、
   `infra_backoff_seconds: float = 0.0`。默认 0 次重试，现有调用方与测试行为不变；
   流水线之后通过 `make_handle(..., infra_retries=..., infra_backoff_seconds=...)` 开启。
   放在句柄上而不是 `call_model` 参数上，理由与 handle_attached_accounting 相同：
   E1 → plugin.run → run_react → call_model 这条链不带额外参数，句柄是唯一贯穿的通道；
   K1 的 `dataclasses.replace(handle, usage=..., run_dir=...)` 会原样保留这两个字段。
   参数在 `__post_init__` 校验（负数、非整数、NaN → ConfigError），`replace` 时同样校验。
2. **只重试 InfrastructureError**：与老仓库 `retry_transient` 同一条规则——只重试声明为
   瞬时的类别，其余原样抛出。客户端抛的 ModelOutputError（如空 content）、
   ProviderRefusalError、ConfigError、代码 bug 都不重试。哪些 HTTP 状态算瞬时仍由
   客户端负责映射（本次不改 client.py）。
3. **退避**：第 n 次重试前等待 `infra_backoff_seconds * 2**n`，封顶
   `MAX_INFRA_BACKOFF_SECONDS = 60`（取老仓库 PROVIDER 序列 1,2,4,…,60 的上限）。
   与老仓库不同：老仓库对 provider 无限重试；这里有上限，因为流水线外面还有 K1 的单元级
   重试与阶段级失败记录，B1 内部只需要扛住短暂抖动，长时间宕机应当显式失败、被记录。
   等待通过模块级 `_sleep` 调用，测试 monkeypatch 它，从不真睡。
4. **每次尝试都记账、落盘、打日志**：失败尝试记一行 usage（status "error"，meta 带
   `error`；InfrastructureError 额外带 `infra_attempt` 与 `retried`），落一份 dump
   （带 `infra_attempt` 字段），并 `LOGGER.warning` 一条（沿用老仓库"每次重试都留痕"的教训）。
   最终成功的那行 usage 在发生过重试时 meta 带 `infra_retries`（本次调用累计次数）。
5. **两个预算如何组合**：格式修复重试（`max_format_retries`）与基础设施重试互相独立。
   基础设施重试重发**同一个**请求，不消耗格式预算；每一个请求（首次请求和每一次修复重问）
   都拥有完整的基础设施预算——收到响应即说明端点已恢复，计数归零。
   因此一次 `call_model` 最多发 `(1 + max_format_retries) * (1 + infra_retries)` 次客户端调用。
   未选"整个调用共享一个预算"：那样一次早期抖动会让后面的修复重问失去保护，两者也不再独立。
6. **请求落盘**：进程级锁 `_SEQ_LOCK` 只保护"预留序号"；每个目录的下一个序号缓存在
   `_NEXT_SEQ`，首次使用时扫一次目录，之后不再每次全目录扫描（一个 run 有数万份 dump，
   逐次扫描是平方复杂度）。内容先写进每次唯一的临时文件 `.<uuid>.json.tmp`，再用
   `os.link` 发布为 `NNNN_<prompt>.json`：`link` 与 `rename` 不同，目标已存在时报错而不是覆盖，
   所以即使别的写者（另一个进程、目录被外部写入）占了这个序号，也只是换下一个号重试，
   永不覆盖已有 dump；发布是原子的，半写的文件不会以正式文件名出现。文件命名不变。
   代价：序号可能因写盘异常留下空号（无害）；依赖文件系统支持硬链接（runs/ 在本地盘）。
7. **本地插件不再传 `run_dir=workdir`**：请求落盘跟随句柄的 run_dir（K1 挂的是 `<run>`）。
   插件单独调用且句柄未挂 run_dir 时不落盘，这与 call_model 的一贯语义一致。
   TB 插件不受影响：它的模型调用在远程 Harbor 中，不走 call_model / run_react。

## 第三轮补充

8. **HTTP 状态分类**（`llm/client.py`）：有了调用级重试之后，原来"所有 APIStatusError
   都映射成 InfrastructureError"会把错误的 key、不存在的模型、超长上下文
   （400/401/403/404/413/422）重发多次，K1 再重试单元，阶段最后以"可续跑"退出，
   supervisor 会自动续跑一个永久错误，`ProviderRefusalError` 也永远到不了。
   现按老仓库 `runtime/models/llm.py` 的 `_api_error_kind` 分类：408/409/429 与 5xx
   （以及不认识的状态）→ InfrastructureError；其余 4xx → ProviderRefusalError。
   连接错误与超时仍是 InfrastructureError。错误信息只含状态码与异常类型，
   不含 SDK 的 message（其中带 provider 返回的错误体，可能回显请求或 key）。
9. **ALFWorld 任务不带机器相关路径**：`Task.meta["gamefile"]` 原来是解析后的绝对路径，
   stage 0 的 `tasks_digest` 会把 `ALFWORLD_DATA` 的位置冻结进 manifest，数据目录搬家
   （内容相同）后续跑报 FrozenProtocolChanged。现 meta 里存相对 `ALFWORLD_DATA` 的路径
   （即 task id），运行时由 `ALFWorldEnv` 按当前 `ALFWORLD_DATA` 重新解析；
   落在数据根下的绝对 id 也规范化为相对 id，数据根之外的绝对 id 保持原样（这是用户
   显式给的位置，无法可移植）。SearchQA 的 meta 只有题目内容（context / answers），
   不含路径，无需改动。

10. **ReAct 中途的 ModelOutputError 算模型失败**（`benchmarks/react_loop.py`）：客户端在模型
    返回空内容时抛 ModelOutputError，call_model 不重试它；原来 run_react 只接
    InfrastructureError，它会穿出 plugin.run，被 E1/K1 当成代码 bug（ParallelError），整条
    流水线停下。现在：本局以 `status="failure"` 结束，`meta["termination"] =
    "model_output_error"`，`meta["error"]` 带错误文本，保留已走的步。理由：给不出动作就是
    在这道题上做错了；不是机器坏了（重试等于对被测模型重新采样直到它表现好，会抬高成绩），
    也不是代码 bug（不该因为一个回复停掉整条流水线）。ProviderRefusalError、ConfigError
    和代码 bug 仍向外抛。

## 未改、需他人跟进

- `benchmarks/base.py` 插件契约里"runner 产生的文件只放在 workdir"一句，可补一句
  "B1 的请求落盘不属于 runner 文件，跟随句柄的 run_dir"。
- `stages/pipeline.py` 的 `make_handle` 尚未开启重试（按任务约定由流水线后续开启）。

## 更正（2026-10-11，文档同步）

"未改、需他人跟进"的两项都已完成：

- `stages/pipeline.py` 已默认开启调用级重试：`run_pipeline(..., llm_infra_retries=3,
  llm_infra_backoff_seconds=5.0)` 由 `_build_handles` 传给每个角色的 handle，CLI 对应
  `--llm-infra-retries`（默认 3）/ `--llm-infra-backoff`（默认 5.0 秒）；见
  `cli_exit_codes_and_default_client_20261011.md` 第三节。上文决定 1 说的"默认 0 次重试"
  仍是 `ModelHandle` 自身的默认值（直接构造 handle 的调用方与测试不受影响）；
- `benchmarks/base.py` 的插件契约已补一句：B1 的请求落盘不属于 runner 文件，跟随句柄的
  run_dir（`<run>/llm_requests/`），runner 不得把 `run_dir=workdir` 传给
  `run_react` / `call_model`。

另：决定 2 的"本次不改 client.py"已被第三轮补充第 8 条取代（HTTP 状态分类改在 client.py）。
