日期：2026-10-13
一句话：K1 批量执行的弹性修复——删除断路器，改为每单元 3 次尝试 + 随机退避 + 每单元错峰 + 心跳观测。
对应 log.md 条目：2026-10-13 K1 批弹性修复（待 log.md 同步条目）

## 1. 背景与事故

TB21 阶段 6 的 K1 驱动在 10 轮 kill/重启循环里只推进了 134/267 个单元。每轮 pass 启动时，
32 个 worker 同时发起约 150–200 个 ssh/scp 连接（每个 fresh 单元 6–7 个：`$HOME` 探测 →
probe → mkdir → SKILL.md scp → meta scp → submit ssh），触发 基准主机 sshd 的并发限流
（MaxStartups 类丢弃，表现为 `kex_exchange_identification` / rc=255）。K1 单元级重试只有
2 次尝试、零退避（backoff 默认 0），全部落在约 5 秒的开局风暴窗内耗尽；断路器
（threshold=5，按不同任务计数）在派发后约 3 秒打开，剩余 108 个单元经 circuit 门跳过、逐条
记入 arm 账本；队列秒清，pass 却挂在最长 4.5 小时的长尾轮询上，操作者等 30–90 分钟后 kill
重启，风暴重现。实证：`stage6_test/bank/failed_units.jsonl` 的 1646 行里 1505 行（91%）是
"circuit open"——这些单元根本没有被尝试，断路器在本场景净害大于利。排查的最大教训是
可观测性：失败账本在 arm 目录（`stage6_test/bank/`）而观察者盯的是 run 根的旧账本；circuit
打开没有任何 console 输出；没有心跳，"正常慢"（4.5h 长尾轮询）与"卡死"不可区分。

## 2. 四项决定与理由

四项决定均由用户确认（2026-10-13）。

**决定 1：彻底删除断路器（"快递站始终开着"）。** 批量执行的大门不再因"猜测端点挂了"
而关上，只等真正的错误来敲门。理由：

- **时间尺度错配**：断路器的前提是"连续失败说明端点挂了"，但本场景的失败是亚秒级的
  连接拒绝（sshd 限流），而"端点活着"的证据（第一个成功单元）要小时级才能到达（远程
  trial 最长 4.5h）。开局 5 秒风暴窗内的 5 个快失败足以打开断路器，而它等的第一条反证
  数小时后才可能出现——它必然在看到任何反证之前就关掉整批。
- **单向不复位**：打开之后本 pass 内不关闭。剩余单元不再派发，也就不可能再有单元正常
  结束来清零计数；一次误判锁死一轮 pass。
- **单元级 `with_retries` + 失败账本 + 续跑已经覆盖断路器想管的场景**：端点真挂时，每个
  单元重试耗尽、记 `failed_units.jsonl`、返回 None，阶段汇总发现空洞后以退出码 3 结束、
  可续跑——这正是断路器想跳到的终态，只是不替操作者提前做"不再尝试"的猜测。代价见
  第 3 节。

**决定 2：重试退避改为随机等待。** `infra_retries` 默认 1→2（每单元 3 次尝试），重试之间
uniform 随机等待 15–45 秒。第一次重试最早发生在失败后 15 秒，已落在约 5 秒的风暴窗外；
取随机值而不是固定值，是防止 32 个 worker 的重试在同一时刻再次聚堆——固定间隔等于把
风暴平移一个时刻，随机间隔才把它摊开。

**决定 3：每单元错峰（"出门错峰合理"）。** `dispatch_stagger`（CLI `--k1-stagger`，默认 0），
每个单元由 worker 领取后随机等待 U(0, stagger) 再执行。只在进程启动时错开一波是不够的：
TB 单元时长相近，同一波派发的单元会在相近时刻完成，worker 领取下一单元时再次同时发起
连接——每一波完成都是一个新风暴。抖动放在每个单元上，覆盖每一波，而不是只覆盖第一波。
TB 续跑命令传 20。

**决定 4：加心跳日志。** 事故的直接教训：10 轮循环里操作者没有任何手段区分"4.5h 长尾
正常慢"与"卡死"，唯一的探路手段是 kill。pending 非空时每 300 秒向 stderr 打一行进度
（格式见第 4 节）。"正常慢"从此表现为 in_flight 稳定、done 缓慢增长；"卡死"表现为
elapsed 增长而所有计数不动。

## 3. 删除的代价（知情记录）

三方评审对"删掉断路器后端点故障怎么过"分场景量化：

- **a) sshd 限流快失败（本次事故场景）：正向改善。** 原断路器把 91% 的单元直接跳过、
  零尝试；现在重试 + 随机退避能扛过开局风暴，实际推进比断路器多。
- **b) 基准主机 硬宕（连接秒失败）持续 4 小时：可控。** 全批探尽约 60–90 分钟（每单元
  3 次快失败、重试间 15–45 秒退避，按 32 并发摊墙钟），stage6 报基础设施耗尽、退出码 3、
  可直接续跑；连接建不起来，不烧模型 token。与本次事故中 10 轮人工 kill 循环相比，这
  反而是更短的路径。
- **c) 慢失败（端点接受请求、trial 跑满 4.5h deadline 才报错）：这是删除后失去的唯一
  保险。** 断路器会在 5 道不同题耗尽后停下剩余单元；现在最坏情况是全批烧完：144 个
  单元 × 3 次尝试按 32 并发摊约 60 小时远程沙箱算力，且死端点下每单元每次尝试多烧
  1 个远程 RUN_ID（全批约 369 个空 trial 目录）。缓解：心跳行每 300 秒可见一次，failed
  持续增长即是明确的故障信号，人工介入随时可停——断路器反而给不出这个信号，它区分不了
  "慢失败"与"正常慢"。

## 4. 语义细则

- **重试与退避**：`infra_retries: int = 2`（每单元首次 + 2 次重试 = 3 次尝试；原默认
  1），`infra_backoff: Tuple[float, float] = (15.0, 45.0)`，每次重试之间按 uniform 随机
  等待 U(15, 45) 秒。每次尝试仍走 B6 重试游标（`k1_retry_cursor.json`）：已判
  `infra_error` 的 attempt_token 不再复用，续跑从游标接着数。重试耗尽的单元不落盘、记
  失败账本，返回 None，等续跑。
- **错峰**：`dispatch_stagger: float = 0.0`（CLI `--k1-stagger`）。每个单元由 worker
  领取后、error 门判定后、执行前随机 sleep U(0, stagger)。三点语义：
  1. 抖动按**单元**计，不按 worker 计——单元完成、worker 领取下一个单元时再次抖动，
     覆盖"完成时刻聚堆"的每一波；
  2. 在 error 门**之后**——已被停止派发标记跳过的单元不等待，排空不受拖延；
  3. 默认 0 完全关闭（本地执行 benchmark 用 0）；TB 续跑命令传 20。
- **派发行**：每批开始时向 stderr 打一行
  `K1 dispatch[<role>]: P pending units (S skipped, T total), workers=W, stagger=Xs, backoff=lo-his`。
- **心跳**：pending 非空时启动 daemon 线程，每 300 秒向 stderr 打一行
  `K1 heartbeat[<role>]: done=.. failed=.. skipped=.. in_flight=.. of N pending, elapsed=..s`。
  计数取**磁盘快照**而非内存计数器（对崩溃/kill 健壮，与磁盘审计同口径）：

  | 字段 | 口径 |
  |---|---|
  | N | dispatch 时的 pending 单元数 |
  | done | pending 中 `store.exists("trajectories", id)` 为真的数量 |
  | failed | `failed_units.jsonl` 总行数 − dispatch 基线（dispatch 时已存在的行数） |
  | skipped | `skip_audit.jsonl` 总行数 − dispatch 基线 |
  | in_flight | N − done − failed − skipped（差值；含 raise 后已停止派发、尚未终结的单元） |
  | elapsed | 自 dispatch 起的秒数 |

- **error 停止语义不变**：非 infra 异常（代码 bug）停止派发新单元、已在跑的跑完并落盘；
  ConfigError / ProviderRefusalError 从 ParallelError 解包上抛；infra 耗尽记 ledger、返回
  None、等续跑。停止瞬间仍即时打印单元 ID 与完整 traceback（2026-10-13 12:10 的修复）。
- **skip_audit 格式不变**：error 门跳过的单元写 `<store.run_dir>/skip_audit.jsonl`，每行
  `{"unit": <trajectory_id>, "gate": "error", "time": <unix 时间戳>}`。原先 circuit 门
  跳过的单元写失败账本（"circuit open" 消息）——该路径随断路器一并删除。

## 5. 与旧文档的关系

以下文档含熔断器的设计/传递描述。按 AGENTS.md "已写的条目不改"的规范，原文保留不动；
自本文起由本文替代其效力（`--infra-breaker` 参数不复存在，退出码 3 的续跑 hint 不再带它）：

- `tb_run_namespace_20261011.md`：第三轮决定 3（熔断）、第四轮"熔断只认端点挂了的证据"
  （按不同 task_id 计数、成功清零）、第五轮"小任务集上的熔断"（有效阈值
  T = min(`infra_breaker`, 不同题数)、单题不熔断）；
- `stage6_metrics_denominator_20261011.md`：第 4 条 `infra_breaker` 传给阶段 1、6，
  熔断跳过的单元按缺失单元处置——**处置口径（同为缺失单元）不变**，跳过机制本身删除；
- `cli_exit_codes_and_default_client_20261011.md`：更正节"退出码 3 时打印的续跑命令
  现在还带上 `--infra-breaker`"；
- `tb_adaptation_20261011.md`："按题熔断"、"路径可见、可熔断、可审计"字样。

## 6. 操作注意事项

- **旧 resume hint 带 `--infra-breaker`**：退出码 3 打印的续跑命令按当时的 CLI 生成，
  新版 CLI 会报 unrecognized argument，粘贴前删掉该参数；
- **旧账本审计**：经历过熔断的旧 run 里，`failed_units.jsonl` 的 "circuit open" 行与
  后续续跑的成功记录并存；审计时按 message（前缀 "circuit open"）与时间戳区分——这些是
  "未尝试"的跳过记录，不是执行失败；
- **TB 续跑**：`--k1-stagger 20`。首轮先看 dispatch 行（pending / workers / stagger 是否
  符合预期）与首两条心跳（in_flight 稳定、done 增长即健康）；若 failed 仍持续增长（真
  故障而非开局风暴），再上调 stagger 或停下排查；
- 心跳走 stderr，`launch_detached.py` 已默认注入 PYTHONUNBUFFERED=1，心跳行实时可见。

## 7. 后续选项

- **若 stagger=20 实测仍聚堆**：改为 worker-index 确定性错开——worker i 的抖动固定为
  i × (stagger / workers)，把随机摊开换成等距摊开，首波连接严格不重叠且跨重启可复现；
- **若慢失败场景的算力代价日后需要保险**：不复活"计数熔断"，考虑"时间窗半开"
  （half-open）的替代设计——只有连续多个单元的尝试都在数分钟内快失败才判定端点死，
  即只对快失败熔断、对慢失败保持耐心（慢失败由心跳 + 人工介入管）。当前不做：心跳的
  failed 计数已给出人工介入所需的全部信号。
