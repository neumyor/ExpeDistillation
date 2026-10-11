日期：2026-10-11
一句话：真实端点下 pipeline 的客户端构建方式，以及 CLI 对基础设施耗尽 / 服务方拒绝的退出码约定。
对应 log.md 条目：2026-10-11 11:44（对照设计 review 全部实现，分波修复并经三次独立对抗式复审）。

## 一、默认客户端：每个模型名一个客户端

背景：`stages/pipeline.py` 的默认构建器直接把 `OpenAICompatClient.from_env`
当成 `make_client(model_name)` 用，而 `from_env()` 不接受参数，第一次真实端点
运行就 TypeError。所有测试都注入了 `make_client`，默认路径从未被执行过。

候选：
1. 全部角色共用一个客户端（客户端本来就按调用传 model id）；
2. 每个不同的模型名建一个客户端（`from_env()` 各调一次）。

选 2。理由：与 pipeline 文档、`make_client` 钩子的语义、已有测试
`test_one_client_per_unique_model` 一致；将来若要给某个模型单独配端点或超时，
只改 `_default_client` 一处，pipeline 不动。代价是多一个 SDK 客户端对象，
可以忽略。实现为模块级 `_default_client(model_name)`，忽略模型名、调用
`OpenAICompatClient.from_env()`。

## 二、CLI 退出码

- `0`：正常完成；
- `1`：需要人改东西才能继续——FrozenProtocolChanged、RunLockedError、
  ConfigError、ProviderRefusalError（重复同一请求没用，提示检查模型名、
  凭据、被拒内容）；
- `3`（`cli.EXIT_INFRA_RESUMABLE`）：InfrastructureError——某阶段跑完其余
  单元后，仍有单元耗尽基础设施重试。已完成单元都已落盘，提示"用同一条命令
  重跑即可续跑"，并打印完整的 `expedistill run --config ... --run-dir ...
  --workers ...` 命令；
- `2`：argparse 的用法错误（不变）。

CodeBugError 和未知异常不捕获，保留 traceback（AGENTS.md 第六节：代码 bug
立即停止整个流程，需要完整现场）。

单独用 3 而不是 1 的理由：supervisor（`scripts/run/`）可以据此区分
"可以直接续跑"与"必须人工处理"，例如对 3 做有限次自动续跑，对 1 直接停。
不捕获 `ReliabilityError` 基类，因为 CodeBugError 也是它的子类。

## 三、单次请求的基础设施重试参数放在哪里

`ModelHandle` 新增 `infra_retries` / `infra_backoff_seconds`（call_model 内对
InfrastructureError 重发，指数退避，单次等待上限 60 秒）。这两个值**不进
`ExperimentConfig`、不进 manifest**：它们和 `--workers` 一样是运维参数，只影响
"多努力地去跑"，不影响"测的是什么"；续跑时允许改（例如端点不稳时调大）。
放进冻结的配置会让"调大重试"变成"新实验"，没有道理。

落点：`run_pipeline(..., llm_infra_retries=3, llm_infra_backoff_seconds=5.0)`
关键字参数，由 `_build_handles` 原样传给每个角色的 handle；CLI `run` 对应
`--llm-infra-retries`（默认 3）/ `--llm-infra-backoff`（默认 5.0 秒）。退出码 3
时打印的续跑命令带上这两个参数，所有参数用 `shlex.quote` 转义，可直接粘贴。

## 更正（2026-10-11，文档同步）

第三节"只影响'多努力地去跑'，不影响'测的是什么'"只对这两个重试参数（以及 `--workers`）
成立。之后新增的 `--allow-infra-shortfall` 起初也被归为同类运维开关，这个说法已撤回：
接受空洞会改变 F 与库，现为本 run 的持久决定，见
`stage6_metrics_denominator_20261011.md` 的"更正"一节。退出码 3 时打印的续跑命令现在
还带上 `--infra-breaker` 和（开着时）`--allow-infra-shortfall`。
