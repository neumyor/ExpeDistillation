日期：2026-11-04
一句话：在通用 LLM 客户端里按模型开启 Qwen 显式缓存（cache_control），默认仍是隐式缓存；不改任何 benchmark 的 prompt。
对应 log.md：2026-11-04「通用显式缓存」条目

## 背景

Qwen3.x plus 系列在蒸馏渠道上默认不建缓存，llm-center 的缓存命中会显示 0；
需要在请求里显式打 `cache_control` 断点。这个系统不只跑 ALFWorld，还要跑 TB
（以及 SearchQA），因此缓存不能做进某个 benchmark 的 prompt，只能做在
`llm/client.py` 的通用请求路径里。

## 协议事实（阿里云百炼显式缓存）

- 断点写在 `messages[].content` 的 content block 上：
  `{"type": "text", "text": ..., "cache_control": {"type": "ephemeral"}}`；
  content 必须是数组，纯字符串带不了断点。
- 缓存覆盖从请求开头到断点的全部前缀；最小 1024 token；单请求最多 4 个断点；
  Qwen3.5+ 每条消息只保留最后一个断点。
- tools 算进 system 前缀，但断点不能打在 tools 字段上（本系统不发 tools）。
- 显式缓存与隐式缓存互斥；缓存 5 分钟，命中续期。
- 响应 usage 里读 `prompt_tokens_details.cached_tokens`（已有代码会记进
  usage 行）。

## 为什么不是"给 system prompt 打点"

实测参考 run（`exp3_k5_final_20261103_2141`）的 folder 请求：system 消息只有
**69 字符**（约 15 token），远低于 1024 token 的最小缓存长度；单独标它不会建
缓存。真正稳定的是 user 消息里的技能块（目录 + 已打开卡）：单条 prompt
8.9k–16.4k 字符，其中技能块 6.8k–14.2k 字符，前缀约占 78%。所以断点必须落在
**user 消息内部**，而不是 system 上。

## 设计

改动只在通用请求层（`llm/client.py`，`llm/call.py` 的句柄注释），不碰任何
benchmark、prompt 模板或 manifest 的 prompt digest：

1. `model_options` 里每个模型可以写：
   ```yaml
   model_options:
     qwen3.6-flash-distill:
       cache_mode: explicit        # 缺省 implicit：请求与旧行为逐字节一致
       cache_prefix_tokens: 1200   # 可选，默认 1200
   ```
   `model_options` 本来就会原样进入 `ModelHandle.extra_body`，不需要改
   配置 schema；两个键不是 `MODEL_OPTIONS_RESERVED_KEYS` 的成员。

2. `client.complete` 从 `extra` 的副本里弹出这两个键（不发给网关），按模型
   决定是否把 user 消息切成 content block：
   - `cache_mode: implicit`（缺省）：user 仍是纯字符串，行为不变；
   - `cache_mode: explicit`：user 前 `cache_prefix_tokens` 个 token（按仓库的
     4 字符/token 估算，最少 1024）作为一个 block，带
     `cache_control: {"type": "ephemeral"}`；其余部分是第二个不带标记的
     block。prompt 短于预算时整条 user 打一个断点。
   - 未知 mode 或非法 token 数直接 `ConfigError`，不静默降级。

3. `call.py` 只更新 `ModelHandle.extra_body` 的说明；请求组装、重试、usage
   记录都不变（`cached_tokens` 已经在 `_result_meta` 里）。

## 行为与代价

- 开启后，**该模型的每个请求**都会带断点（用户明确要求"所有请求都标创建
  缓存"）：第一次建缓存，后续前缀相同则命中。前缀不同（例如每条 prompt 的
  变量都在最前面）时不会命中，但仍会付建缓存的成本；`cache_prefix_tokens`
  应按工作负载的稳定头长度调。
- ALFWorld 的 folder 请求在同一个 episode 内前缀稳定（task 描述 + 技能块），
  通常从第 2 步起命中；打开一张新卡会让技能块变化，下一次请求重建缓存，之后
  继续命中。缓存 5 分钟、命中续期，episode 内步与步之间不会过期。
- TB 的请求结构不同，但同样是"稳定任务说明 + 变化的环境状态"；固定前缀
  策略对它是通用的，具体预算要按 TB 的 prompt 长度调。
- 显式缓存与隐式缓存互斥；开启后不能再指望网关的隐式缓存。
- 模型/渠道不支持时可能直接 4xx；先做探针再全量开启。

## 如何验证

1. 用同一个模型发两次前缀相同、长度 >1024 token 的请求；
2. 看第二次响应的 `usage.prompt_tokens_details.cached_tokens` 是否 > 0，以及
   `cache_creation_input_tokens` 是否在第一次出现；
3. 在 run 的 `llm_requests/usage.jsonl` 里，`meta.cached_tokens` 就是实际命中
   的 prompt token 数（已有记录）。

## 2026-11-04 探针结果（重要）

用真实网关对 `qwen3.6-flash-distill` / `qwen3.6-plus` / `qwen3.7-plus` 发了
对照请求（同一段长前缀 + 不同尾部，间隔 3 秒）：

| 条件 | 第一次 | 第二次 |
|---|---|---|
| 同一前缀，不同尾部 | `cache_creation_input_tokens > 0` | **仍然 create > 0，`cached_tokens = 0`** |
| 同一条完整 prompt | create > 0 | `cached_tokens` = 整条 prompt，命中 |

结论：网关**接受** `cache_control` 并真的创建了缓存，但它当前的实现是
**整条 prompt 命中**，不是文档描述的前缀命中；`cache_control` 放在单条 user
消息的 content block 里，缓存键仍然覆盖整条消息。`qwen3.8-27b` 则完全不返回
缓存字段。

因此：

- 客户端实现已就绪，但**默认关闭**；在没有确认网关的前缀行为之前，不为
  正式 run 开启，否则只会为每条唯一 prompt 付建缓存成本而没有命中。
- 仍待验证的两种结构（探针在端点拥塞时超时，等 run 结束后重试）：
  1. 把稳定前缀放进 **system 消息**、动态尾部放进 user；system 只保留一条；
  2. 把稳定前缀单独做成一条 **user 消息**、动态尾部做第二条 user 消息。
  如果其中一种能让"同前缀、不同尾部"命中，就把客户端的切分方式改成那种
  消息结构；改动仍然只在 `llm/client.py`，配置键不变。

## 不做什么

- 不改任何 prompt 模板或 benchmark 代码；
- 不把 `cache_control` 放进请求顶层或 tools；
- 不默认开启：缺省 `implicit`，旧 run 的请求与现在完全一致；
- 不在已经启动的 run 上改配置（manifest 已冻结）；要开启就新建配置/新 run。
