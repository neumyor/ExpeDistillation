日期：2026-10-11
一句话：模型句柄（ModelHandle）携带 usage/run_dir，作为 E1→插件→run_react→call_model 链路上记账对象的回退通道。
对应 log.md 条目：2026-10-11 09:53（实现 README 蓝图：全部七层代码 + 测试落盘）。

背景：`plugin.run(task, handle, skills_block, workdir)` 的签名没有 usage/run_dir
参数，本地 benchmark 执行的逐请求记账和 llm_requests 落盘传不进去；给插件接口
加参数要动四个并行实现的插件。

决定与理由：
- ModelHandle 增加 `usage` / `run_dir` 两个可选字段；call_model 未显式收到
  usage/run_dir 时回退到句柄上的值（显式参数优先）。
- K1 用 `dataclasses.replace(handle, usage=..., run_dir=...)` 生成工作句柄，
  原句柄不被改动；TB 插件的远程用量回账同样先看构造注入、再回退句柄。
- 效果：E1 的 usage/run_dir 形参保持"调用点一致性"而实际不使用，所有记账
  通过句柄单通道流动，本地与远程插件机制统一。

配套：每个模型请求（含失败与格式重试）都落一行 usage（token 未知记 null 不记
0），run_dir 存在时请求/响应原文落盘到 llm_requests/（永不落 API key）。
