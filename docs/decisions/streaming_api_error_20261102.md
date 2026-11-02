日期：2026-11-02
一句话：网关限流时的流式 503 以 openai 基础 APIError 抛出，未被归类为基础设施错误，导致 K1 批次中止；已修并续跑。
对应 log.md：2026-11-02 「流式 503 误判为代码 bug」条目

## 事故

2026-11-02 10:08–10:10，实验四的 `e4_a1_body` 与 `e4_a0` 在终测中先后中止（rc=1）。日志是：

```
openai.APIError: 服务器内部错误: 上游服务错误[503]:
  ... [Too many requests. Your requests are being throttled due to system capacity limits.]
...
ParallelError: 1 of 402 item(s) failed (first: item 188: APIError ...)
```

两个 run 的当前批次因此停下：`e4_a1_body` 跳过 205 个未启动单元，`e4_a0` 跳过 22 个（`skip_audit.jsonl` 的 `gate: error`）。已完成单元都落盘，未丢数据。

## 根因

`llm/client.py` 的流式异常映射覆盖了 `APIStatusError`、`APIConnectionError`、`APIResponseValidationError` 与 `JSONDecodeError`，但没有覆盖 openai SDK 的**基础 `APIError`**。网关的 503 限流响应在流迭代器里抛出的是基础 `APIError`（没有 HTTP status 对象），于是它逃出映射，被 K1 当成“非基础设施错误”的致命异常：停止启动新单元、把队列单元写入 `skip_audit`，并让整个批次抛出 `ParallelError`。

这不是代码 bug 该有的行为：503/429 是端点侧失败，应该进入 `llm-infra-retries` 的重试预算。

## 修复

`src/expedistill/llm/client.py` 的异常链在 `APIConnectionError` 之后增加基础 `APIError` 分支，统一转成 `InfrastructureError`（消息含 `stream error` 与 status）。`APIStatusError` 是它的子类，仍先走原有的 429/永久错误分支，顺序不变。

单元测试：`tests/unit/llm/test_client.py::test_complete_mid_stream_base_api_error_is_infrastructure` 用流中途抛出的 `openai.APIError` 断言映射为 `InfrastructureError`。全量 3117 通过。

## 恢复

- 两个 run 都用原目录续跑：已完成单元不重跑（K1 只派发缺失单元；`e4_a0` 续跑时 402 个里只有 23 个 pending）。
- OS 看门狗（`tmp/campaign_watchdog.py`）同步更新：`infra_resumable` 识别 `APIError` / `ServiceUnavailable` / `Too many requests` / `status=503`，`code_bug` 不再把这些当代码 bug。重启后的看门狗在 supervisor 退出后自动续跑了 `e4_a0`，随后会续跑 `e4_a1_body`，再启动剩余两对臂。

## 边界

- 这次修复只改异常分类，不改 prompt、配置或指标口径；run 的 manifest 仍匹配。
- 网关限流本身没有消失，修复只是把“中止整个批次”变成“单请求重试”；如果限流持续时间超过重试预算，单元仍会记为 infra failure，run 会以 rc=3 停下并可续跑（这是既有语义）。
