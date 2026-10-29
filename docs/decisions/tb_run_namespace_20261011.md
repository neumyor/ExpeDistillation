日期：2026-10-11
一句话：TB 远程 run id 加入"臂/实验命名空间"，并实现远程 trial 的续跑（复用已有结果、重连存活 runner、拒绝向陈旧 run 目录重复提交）。
对应 log.md 条目：2026-10-11 11:44（对照设计 review 全部实现，分波修复并经三次独立对抗式复审）。

## 背景

TB 插件的远程 run id 原为 `ed_<task>_<attempt_token>`，K1 的 token 是
`a{attempt:02d}`（infra 重试加 `r{n}`）。id 里没有角色、没有实验臂、没有
run 目录；而 jobs 目录按远程 root 共享，`_poll` 按 run id glob
`<jobs>/<run_id>/<task>__*/result.json`。后果（静默错误结果）：

- 阶段 1 先跑强模型、再跑弱模型，同题同 token → 弱模型那一臂的 poll 立刻
  读到强模型的 result.json，当成弱模型结果入库；
- 阶段 6 带库臂（及 split 模式的裸跑对照臂）与阶段 1 的 run 撞 id；
- 两个实验（不同 run 目录）共用远程 root 时互相撞 id。

这正是 `stage6_arm_stores_20261011.md` 防的"对照臂静默复用另一臂"，
只是出现在远程这一层。另外 K1 docstring 声称"续跑能认出已提交的远程
trial"，但插件每次都重新提交，并未实现。

## 参考实现（AGENTS.md 10.2）

- 外层 `scripts/tb21`：每条 trial 一个互不相同的 run_id（`run_<q|d>_<task>_<n>`，
  模型写进 id 前缀）；补跑队列用新前缀（如 `e1n5_q_`），从不往旧 run_id 里
  再跑；判活只认 pidfile + `kill -0`，禁止 `pgrep -f`。
- 姊妹仓库 `ExpeToSkill-merged` 的 `benchmarks/terminalbench.py`：
  run_id = `skillexpand_r<round>_task<id>_<os.getpid()>`，靠进程 pid 保证唯一，
  因此不撞 id，但也无法续跑（重启后 pid 变了）。本设计需要可续跑，所以不沿用
  pid 方案，改用确定性摘要。

## 决定

1. **命名空间（K1）**：`task.meta["run_namespace"]` = sha256 的前 10 位十六进制，
   输入为 `realpath(store.run_dir)`、`role`、`model_name`、`skills_injected`
   的规范 JSON（`sort_keys`、紧凑分隔符）。同一输入跨续跑恒定；任一不同即不同。
   阶段 1 强/弱两臂角色与模型不同；阶段 6 两臂用各自的臂 store（目录不同）；
   不同实验的 run 目录不同 —— 三种撞车都被区分。`attempt_token` 的取值保持不变。
   `skills_injected` 必须可 JSON 序列化（它本来就要存进 Trajectory），否则 CodeBugError。
2. **run id（插件）**：有命名空间时 `ed_<task>_<namespace>_<token>`，没有时保持
   旧形式 `ed_<task>_<token>`（直接调用方、旧测试仍可用）。命名空间与 token 用同一
   字符集正则校验，run id 总长上限 200（远程文件名 `logs/<run_id>.log`、
   `configs/<run_id>.yaml`）。
3. **远程续跑（插件）**：提交前一次 ssh 探测该 run id：
   - 有 `result.json` → 直接 `_finish` 复用（不提交、不上传 skills，meta 照记
     skills 来源），`meta["resumed"]="result"`；
   - run "已存在"但无结果 → 读 `<workdir>/tb_remote/<run_id>.pid`（提交成功后
     原子写入），该 pid 远程 `kill -0` 仍活 → 不提交，直接继续 poll，
     `meta["resumed"]="alive"`；否则（pid 死了或本地没有 pid 记录）→ 返回
     `infra_error`（"refusing to resubmit"），`meta["resumed"]="stale"`；
   - 不存在 → 正常上传 skills + 提交，`meta["resumed"]=None`。
   "已存在"= jobs 目录下有 `<run_id>/` **或** `logs/<run_id>.log` 存在。后者由提交
   命令的重定向在 echo pid 之前创建，覆盖"远程已启动、本地 pid 还没写下就崩溃"
   的窗口 —— 这种情况判为 stale，不会重复起第二个 runner（外层 AGENTS.md
   2026-09-20 双 driver 事故）。**同一个 run id 永远只提交一次。**
4. **重试游标（K1）**：stale 返回 infra_error 后 K1 换下一个 token 重试。但若只在
   单次调用内从 `r0` 数起，续跑时会重新请求已经用掉的 `a00`/`a00r1`，插件永远判
   stale，该单元被永久卡死。因此 K1 在单元 workdir 写 `k1_retry_cursor.json`
   （`{"next_retry": n}`，每次 infra_error 后原子写 n+1）。续跑从游标处开始：
   崩溃时正在跑的 token 会被再次请求（可复用结果/重连 runner），已判 infra_error
   的 token 不再请求。全新 run 的 token 序列仍是 `a00`、`a00r1`……；每次调用的
   重试预算仍是 `infra_retries + 1` 次执行。

## 第三轮修订（review 发现后追加）

1. **续跑核对已存单元**：K1 跳过已存储的轨迹之前，先核对它记录的 `model_role`、
   `model_name`、`skills_injected`（规范 JSON 比较）是否与本次请求一致；不一致即
   ConfigError，报出单元 ID 和两边的值，提示换新 run 目录。已存文件从不覆盖。
   起因：库重新整理（或换模型）后重跑阶段 6，旧轨迹被原样复用，摘要却标着新库的引用。
2. **错误不再被 ParallelError 包住、出错后不再继续调度**：任一单元抛异常后，
   共享标志让后续单元不再启动（已在跑的跑完并落盘）。失败全是 ConfigError /
   ProviderRefusalError 时，按单元顺序重抛第一个（同类型，消息附"K1: N unit(s)
   failed"）；只要有代码 bug 或其他异常，就原样抛 ParallelError（保留完整 traceback）。
3. **熔断**：`batch_execute(infra_breaker=5)`。（计数口径已在第四轮改为"不同题"，
   阈值在第五轮改为随任务数收缩，以下为本轮原始版本。）按完成顺序连续 N 个单元耗尽 infra
   重试后，剩余单元不再尝试，逐个写进 failed_units（消息含 "circuit open"），
   不落盘，函数正常返回；续跑会重新尝试它们。0 关闭熔断。
4. **命名空间加入渲染后 skills_block 的 sha256**：库引用不反映分组和渲染方式，
   重新分组后的库必须拿到新的 run id。长度、格式不变（仍是 10 位十六进制）。
5. **TB 远程复用收紧**：
   - 提交时写两份启动条件记录（run id、转发环境变量 `_FORWARDED_KNOBS` 取值的
     sha256、SKILL.md 的 sha256）：远程 `<root>/jobs/<run_id>.ed_meta.json`
     （在 runner 启动前用 scp 上传，排在最后），本地 `<workdir>/tb_remote/<run_id>.json`
     （另加 pid）；
   - 远程有任何东西（结果、jobs 目录、日志、meta）时，只有本地记录存在、
     两份记录都与本次请求一致，才允许复用结果或重连存活 runner；否则一律
     stale → infra_error → K1 换新 token；
   - deadline 时只终止本单元记录的 pid：`kill -TERM -- -<pid>`（setsid 使 runner 成为
     进程组组长，组内正是 runner 及其 Harbor 子进程，否则 Harbor 会继续占着沙箱），
     失败再退回 `kill -TERM <pid>`；从不用 pkill / pgrep -f；结果记在 `meta["deadline_kill"]`；
   - 复用或重连的 trial 用 result.json 的 `started_at`/`finished_at` 记真实耗时
     （`timing.source="result.json"`），没有这两个字段就记 None、`source="unknown"`；
   - stale 分支不记用量行（这次调用没有发生 rollout）；传输失败也只在本次真的
     启动或重连过 runner 时才记用量行。

## 第四轮修订（复审发现后追加）

1. **熔断只认"端点挂了"的证据**：连续耗尽 infra 重试的单元按**不同 task_id** 计数
   （同一题重复失败不增加计数；任何一次成功清零），覆盖 `infra_breaker` 道不同的题才熔断。
   单元派发顺序改为 attempt-major（所有题的第 0 次，再所有题的第 1 次……），
   同一道坏题的多次 attempt 不再相邻；返回值仍按 trajectory_id 排序。
   起因：4 题 × N=5、t1 永远 infra 时，按单元计数会被 t1 一题触发熔断，t2–t4 一次都不跑，
   且每次续跑都重复（活锁）。
2. **pid 身份校验**：`kill -0` 分不清"我们的 runner"与"复用了这个 pid 的无关进程"。
   探测（重连判活）、poll（判 DEAD）和 deadline kill 都改用同一个条件：
   `/proc/<pid>/environ`（NUL 分隔）里必须有 `RUN_ID=<run_id>` 这一行
   （runner 由 `env RUN_ID=... bash` 启动，pid 经 exec 不变，初始环境就在这里）。
   读不到（无 /proc、无权限）或不匹配都算"不是我们的"：判活为否、重连判 stale、
   绝不发信号。kill 是"先校验再发信号"写在同一条 shell 命令里，缩小竞态窗口；
   结果记在 `meta["deadline_kill"]`（verified / signalled / rc）。
3. **`skip_unit_ids`**：调用方声明为终态的 trajectory id（已接受的 infra 缺口）。
   K1 不执行、不记入失败重试账本；它们没有落盘，所以也不出现在返回值里，其余不变。
   传入裸字符串视为代码 bug（CodeBugError）。

## 第五轮修订（小任务集上的熔断）

有效阈值 T = min(`infra_breaker`, 本次调用待跑单元里不同 task_id 的个数)，
让任务数少于 `infra_breaker` 的小任务集（如 3 题 TB 试跑）在端点真挂时也能尽早停下；
多题中只有一道坏题仍然不会触发（任何成功都会清零计数）。
**只有 1 道待跑题时熔断关闭**（不取 T=1）：单题无法区分"端点挂了"和"这道题坏了"；
T=1 会让一道坏题第一次 attempt 耗尽后跳过自己剩下的 attempt，且每次续跑只推进一次 attempt；
而关闭时端点真挂的代价至多是这一题的 N 个单元，可以接受。
`infra_breaker=0` 仍表示关闭。circuit open 消息写明有效阈值。

## 已知限制

- run 目录被搬到另一路径后续跑，命名空间会变，已提交的远程 trial 不会被认出，
  只会被重新跑一遍（浪费，不会算错）。
- pid 身份校验依赖 Linux `/proc` 且 runner 与本插件同一用户（基准主机 满足）；
  在没有 /proc 的远端上，所有记录的 pid 都会被判为"不是我们的"（只会多换 token 重跑，
  不会误杀、不会算错）。
- 环境变量摘要只覆盖本机转发的变量；远程 `env.sh` 里设置的变量不在其中。
- 若 `_finish` 已记账但在 store.save 之前崩溃，续跑复用结果会再记一行用量
  （极小窗口，可能重复计 1 次 rollout）。

## 测试

- `tests/unit/benchmarks/test_tb_plugin.py`：命名空间进 run id；非法命名空间报错；
  弱臂不读强臂的已有结果；已有 result.json 不提交；run 目录存在 + pid 死/无 pid
  记录 → infra_error 且不提交；pid 存活 → 只 poll 不提交；提交后本地写 pid，
  同单元再次调用重连而不重复提交。
- `tests/composites/test_batch_execute.py`：命名空间确定、跨重试恒定、symlink 同目录
  视为同一实验；换 store / 角色 / 模型 / skills_injected 均得到不同命名空间；
  续跑继续重试序列（不重复已用 token）；续跑重连崩溃时在跑的 token；
  经真实 TBPlugin + 有状态假远程端到端跑阶段 1 形状（强→弱同题同 attempt），
  弱臂得到自己的 failure 而不是强臂的 success。
- 有状态假远程：`tests/unit/benchmarks/tb_fake_remote.py`。
