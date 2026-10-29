日期：2026-10-29
一句话：driver 搬到基准主机上与 Harbor 同机运行，用 `EXPEDISTILL_TB_HOST=local` 切换传输，不改业务代码。
对应 log.md：2026-10-29 的“driver 迁到基准主机运行”条目。

# 决定：driver 与 Harbor 同机运行

## 背景

TB 的每次 trial 都通过 driver 所在机器的 `ssh`/`scp` 访问基准主机：提交、轮询、取结果、
取 verifier 日志、取轨迹，插件里共有 21 个调用点，轮询间隔内持续产生新的 ssh 连接。
32 到 64 个工作线程并发时会撞上基准主机对短时间内 ssh 连接数的限制；driver 到基准
主机的网络一旦中断，一个相位的全部单元会在重试预算内同时失败（P07 曾因此 117 个单元
全部落成基础设施失败）。

## 候选方案

1. 继续经 ssh，加连接复用（ControlMaster）和更长的重试：能减少握手次数，但网络中断和
   对端限流仍然存在，只是降低频率。不采用。
2. 把 driver 搬到基准主机，传输层换成本机命令（采用）：所有 ssh/scp 消失，问题从根上去掉。

## 决定

- `RemoteLink` 在 `EXPEDISTILL_TB_HOST=local` 时不走 ssh：`run` 用本机 `bash -c`，
  `put`/`get` 用文件复制。空值仍是配置错误，防止漏配后把远程命令跑在本机。业务逻辑
  （插件的提交、轮询、取结果）一行不改，测试用的 `FakeRemoteLink` 不受影响。
- 本机命令使用干净环境（只保留 `HOME`、`USER`、`LANG` 等和去掉 driver 虚拟环境的
  `PATH`），与 ssh 登录会话看到的一致，driver 的 API 密钥不会传给 runner。
- 代码用 `rsync` 推送，一个提交一个目录，从不覆盖，符合“正式 run 固定在启动它的
  checkout 上”。run 目录、审计、分析都留在基准主机，不拉回。
- 密钥放在基准主机上一个权限 600 的文件里，启动脚本检查权限，不进 git。
- 部署相关的路径一律来自配置：工作根 `EXPEDISTILL_TB_ROOT`、runner 脚本
  `EXPEDISTILL_TB_RUNNER`（没有默认值，缺失即配置错误）、可选的
  `EXPEDISTILL_TB_ALLOWED_ROOTS`（共享主机上的路径保护）。仓库里不再出现具体的主机名、
  地址、用户名、目录和端点。

## 后果

- ssh/scp 并发限制和网络抖动不再影响实验。LLM 调用也从基准主机发出。
- 查看进度要登录基准主机；`scripts/remote/README.md` 和根 README 写了完整流程。
- TB 的远程 run id 含 run 目录的真实路径，所以把已有的本机 run 搬过去（路径变了）
  不能续跑，只能在新位置新建 run。
- 冒烟结果（本机模式，单题）：首跑、同 run id 复用、模拟崩溃后重新加入存活的 runner、
  技能进入沙箱，四个阶段通过。冒烟脚本里“step 0 必须出现
  `/opt/openclaw-skills/<task>/SKILL.md`”的检查是对旧版 vendor 协议写的，当前 vendor 后缀
  指向 `<available_skills>`，已改成检查后者；这是脚本与 vendor 协议的偏差，与本机模式无关。
- 未验证：32 个以上并发时基准主机的负载表现。上线后第一个 run 先从 8 个工作线程观察，
  再到 32。

# 补充：LLM 限流与两个并行度

## 背景

driver 搬到基准主机后第一次按 128 个工作线程启动，P02 的切段在第 176 秒起被网关限流
（HTTP 429），5 次重试用尽，没有写出任何结果。限流是按**请求 ID** 计的，而不是按模型；
同一个模型可以用多个请求 ID 访问。另外 `--workers` 同时管两类负载：只调用 LLM 端点的
步骤，和占用基准主机沙箱的真实 rollout，两者受不同资源限制。

## 决定

- **请求层按别名分流**：`OpenAICompatClient` 内部把调用方给的逻辑模型名映射到一组别名，
  请求依次轮转；某个别名收到 429 后休息一段时间再参与，全部休息时选恢复最早的。
  只有线上的 `model` 字段变，`ModelHandle.name` 仍是逻辑名，所以 manifest、用量账本、
  已存轨迹的 `model_name` 校验和 TB 的命名空间都不受影响。别名通过
  `EXPEDISTILL_LLM_MODEL_ALIASES` 配置，不进配置、manifest 和日志。
  放在 client 内而不放在 handle 或 `call_model`：只有 client 同时知道逻辑名、实际用的
  别名和 HTTP 状态码。
- **两个并行度**：新增 `--rollout-workers`（运行设置，不进 manifest，续跑可改），只管
  K1 对真实 rollout 的派发；`--workers` 管其余 LLM 步骤，包括 exam 单元（它只调 LLM）。
  不设 `--rollout-workers` 时等于 `--workers`，现有命令不变。

## 边界

- Harbor 在基准主机上的模型请求不经过这个 client，别名分流不覆盖它；该路径的限流若成为
  问题，需要另外处理，并且要先确认 `MODEL_NAME` 改成别名后命名空间摘要仍用逻辑名。
- 三个别名是否各自独立限流是假设，需要用实测确认（见 log 中的实测记录）。
