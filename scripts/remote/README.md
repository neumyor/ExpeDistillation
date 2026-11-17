# 在基准主机上运行实验

TB 的 rollout 由基准主机上的 Harbor 执行。driver 也放在这台主机上时，所有命令和
文件操作都在本机完成，不再经过 ssh/scp，所以 driver 所在机器到基准主机之间的网络
波动和 ssh 并发连接限制不再影响实验。run 目录、审计和分析都留在基准主机上，结果
不拉回。

完整流程：部署代码 →（复用摸底时）`import-baseline` → 启动 → 起 supervisor →
查看进度 → run 结束后审计。下面逐步说明。

尖括号表示每个部署自己决定的值。真实值只写在被 gitignore 的 `.env` 和基准
主机上的密钥文件里，不写进仓库。

## 目录约定

| 位置 | 内容 |
|---|---|
| `<deploy-base>/<commit>/` | 一个提交一个目录：`src/`、`configs/`、`scripts/`、`pyproject.toml`、`README.md`、`AGENTS.md`、`.venv/`、`DEPLOYED_FROM`；从不覆盖 |
| `<runs-dir>/<run 名>/` | run 目录：`phases/`、`llm_requests/` 等 run 产物，以及下表的运维文件 |
| `<bench-root>/` | Harbor 的工作根（`jobs/`、`logs/`、`skills/`、`configs/`），即 `EXPEDISTILL_TB_ROOT` |
| `<env-file>` | 密钥和连接变量，权限 600，不进 git |

run 目录里的运维文件：

| 文件 | 写入方 | 内容 |
|---|---|---|
| `launch.sh` | 启动脚本，每次启动重写 | 原样重复这次启动的可执行脚本，续跑就运行它 |
| `run.env` | 人工 | 只属于这个 run 的环境设置，每次启动都读 |
| `driver.pid` | 启动器 | driver 进程的 PID |
| `driver.pid.exit` | 启动器的守候进程 | driver 结束时的退出码；启动时删除旧文件 |
| `driver.console.log` | driver 与守候进程 | driver 的全部输出，以及守候进程的启动、结束记录 |
| `supervisor.pid` / `supervisor.log` | supervisor | supervisor 的 PID 与日志 |

正式 run 固定在启动它的代码目录上续跑，所以每次改代码都新部署一个目录。

## 一次性准备

**1. 本机 `.env`**（部署脚本读取）：

```bash
EXPEDISTILL_TB_HOST=<user>@<host>        # 基准主机的 ssh 目标
EXPEDISTILL_TB_KEY=<ssh 私钥路径>
EXPEDISTILL_DEPLOY_BASE=<deploy-base>    # 基准主机上存放各提交代码目录的绝对路径
```

**2. 基准主机上的密钥文件**，默认位置 `~/.config/expedistill/expedistill.env`（可用
`EXPEDISTILL_ENV_FILE` 改），权限必须是 600：

```bash
EXPEDISTILL_LLM_ENDPOINT=<LLM 端点>
EXPEDISTILL_LLM_API_KEY=<密钥>
EXPEDISTILL_TB_HOST=local
EXPEDISTILL_TB_ROOT=<bench-root>
EXPEDISTILL_TB_RUNNER=<基准主机上 runner 脚本的绝对路径>
EXPEDISTILL_TB_ALLOWED_ROOTS=<可选：":" 分隔的绝对前缀>
```

- `EXPEDISTILL_TB_HOST=local` 表示 driver 就在基准主机上：命令走本机 `bash`，文件走
  本机复制，不需要 `EXPEDISTILL_TB_KEY`。留空仍是配置错误，防止漏配后把远程命令
  跑在本机；
- `EXPEDISTILL_TB_RUNNER` 没有默认值，缺失时第一次提交就报配置错误；
- `EXPEDISTILL_TB_ALLOWED_ROOTS` 是共享主机上的保护：设置后 `EXPEDISTILL_TB_ROOT` 必须
  在其中某个前缀之下，写错路径时报错，不会写进别人的目录；
- `EXPEDISTILL_LLM_PROVIDER` 可选，缺省为 `openai_compat`；
- `EXPEDISTILL_LLM_MODEL_ALIASES` 可选：网关按请求 ID 限流时，把同一个模型的多个请求 ID
  填成 `逻辑名=别名1,别名2;…`，client 在别名之间轮转，收到 429 的别名休息 20 秒。逻辑名
  仍是 manifest 和用量账本里的身份；Harbor 的模型请求不经过它；
- Harbor 一侧的设置（`MODEL_API_BASE` 与 `TBENCH_*`，清单见 `.env.example`）由插件从
  driver 的环境原样转给 runner；本机模式下 runner 继承的是干净环境（与 ssh 登录会话
  一致），driver 的密钥不会传给 runner；
- **`TBENCH_REASONING_EFFORT` 不写进共用的密钥文件。** 它让 Harbor 的智能体发
  `reasoning_effort`，只用于强模型 run（与 campaign 的强模型对照一致），写在该 run 的
  `run.env` 里；弱模型 run 一律不设。审计脚本会检查弱模型 trial 的 Harbor 配置里有没有它。

## 部署（在代码所在的机器上）

```bash
scripts/remote/deploy_bench_host.sh            # 部署已提交的 HEAD
scripts/remote/deploy_bench_host.sh --force    # 部署工作区的当前内容
```

- 不加 `--force`：已跟踪文件有未提交改动时拒绝部署。脚本用 `git archive HEAD` 把提交
  导出到临时目录，再从临时目录 `rsync`，所以部署的内容恰好是这个提交；`src/`、
  `configs/`、`scripts/` 下未跟踪的文件不会被带上。目录名是 `<提交>`；
- 加 `--force`：直接从工作区 `rsync`，未提交的改动和未跟踪的文件都会带上。被部署的
  路径与提交有任何差别时，目录名是 `<提交>-dirty-<时间戳>`，每次都是新目录，不覆盖任何
  已有目录（正在跑的 run 的代码不会被改动）；`DEPLOYED_FROM` 里记录了当时的
  `git status` 和 `git diff HEAD` 的 sha256，之后据此找到对应的提交。

脚本用 `rsync` 推送代码（不要求基准主机能访问代码托管服务），在远端建 venv 并
`pip install -e .`，最后一行输出是远端的代码目录。

## 第一次启动前：复用已有的摸底（import-baseline）

新 run 要复用一个已有 run 的摸底轨迹时，在**第一次启动之前**把轨迹导入新 run 目录
（目录名就是之后启动时用的 run 名）：

```bash
cd <deploy-base>/<commit>
.venv/bin/expedistill import-baseline --source-run <runs-dir>/<源 run> \
    --config configs/experiments/<新配置>.yaml --run-dir <runs-dir>/<新 run 名>
```

新 run 目录里不能已有 manifest 或轨迹；导入的检查项与 `BASELINE_ORIGIN.md` 的内容见
README 的命令行一节。导入后正常启动，摸底相位不调用模型，照常写答卷与摘要。

TB 的源轨迹若没有记录判分报告（`meta.grader_report`），导入时按轨迹 `meta.remote.verifier_log`
指向的 Harbor 判分日志重新摘取，所以要在基准主机上执行，并且源 run 的 Harbor jobs 目录还在。
`BASELINE_ORIGIN.md` 的"补充的执行事实"一节写明补上了多少条、多少条没补上；没补上的轨迹，
诊断照旧只看轨迹。

## 启动（在基准主机上，命令立即返回）

```bash
cd <deploy-base>/<commit>
RUNS_DIR=<runs-dir> WORKERS=128 ROLLOUT_WORKERS=64 \
    scripts/remote/start_run_bench_host.sh <配置名> [run 名] [其余 CLI 参数]
```

- run 名已存在就是续跑；不写则新建 `<配置 stem>_<YYYYMMDD_HHMM>`；
- 并行度有两个，分开设置：`WORKERS` 管只调用 LLM 端点的步骤（诊断、切段、归类、写卡、改写），
  缺省 64；`ROLLOUT_WORKERS` 管基准主机上的真实 rollout，不设则等于 `WORKERS`。两者都不得
  低于 32；
- 脚本先读密钥文件，再读 `<run 目录>/run.env`（如果有），然后通过
  `scripts/smoke/launch_detached.py` 启动 driver，确认 driver 活过最初两秒后立即返回，
  打印 run 目录、pidfile、控制台日志和续跑命令；
- driver 和守候它的进程各在自己的会话里，忽略终端的挂断信号，ssh 断开、关闭终端都
  不影响它们。driver 结束时守候进程把退出码写进 `driver.pid.exit`；
- `driver.pid` 指向的进程还活着时，脚本拒绝启动（退出码 1），也不改写 `launch.sh`；
- 只属于某一个 run 的环境设置写在 `<run 目录>/run.env`，不写进共用的密钥文件（见上面
  `TBENCH_REASONING_EFFORT` 一条）。

## 续跑：launch.sh

每次启动都会重写 `<run 目录>/launch.sh`。它记录这次启动的代码目录、配置的绝对路径、
run 名、其余 CLI 参数（例如 `--llm-timeout 1800`）以及 `RUNS_DIR`、`WORKERS`、
`ROLLOUT_WORKERS`、`EXPEDISTILL_ENV_FILE`，参数按 shell 规则转义；密钥不在里面，续跑时
照常从密钥文件和 `run.env` 读取。续跑就是：

```bash
bash <runs-dir>/<run 名>/launch.sh
```

续跑必须使用同一个代码目录和同一个 run 名：TB 的远程 run id 含 run 目录的真实
路径，换路径等于新 run。`launch.sh` 保证这两点。要改运行设置（例如调大超时）时，用新
参数再跑一次启动脚本，`launch.sh` 随之更新。

CLI 的退出码：`0` 完成；`1` 需要人处理（配置错误、manifest 不一致、run 目录被锁、模型
输出错误、代码 bug），先读 `driver.console.log`；`2` 命令行用法错误；`3` 基础设施重试
耗尽，直接续跑会重试失败的单元。

## supervisor：退出码 3 时自动续跑

```bash
cd <deploy-base>/<commit>
.venv/bin/python scripts/remote/supervise_run.py <runs-dir>/<run 名> [--backoff 300] [--max-stalled 5]
```

- 命令立即返回：supervisor 在自己的会话里运行，PID 写进 `supervisor.pid`，日志写进
  `supervisor.log`；`supervisor.pid` 指向的进程还活着时拒绝再起一个；
- 它轮询 `driver.pid` 的存活（`kill -0`，不用 `pgrep -f`），driver 结束后读
  `driver.pid.exit`：退出码 3 时等 `--backoff` 秒（缺省 300）后运行 `launch.sh` 续跑；
  0、1、2 和其他退出码都停下，并在日志里写明原因；driver 已经不在、又没有退出码
  （守候进程被杀）时同样停下；
- 进展判断：每次尝试前后各数一次 run 已完成的相位数（`pipeline_log.jsonl`）、已存的
  对象数（run 级与 test 各臂的轨迹、段、技能）和学习相位 `findings.jsonl` /
  `outcomes.jsonl` 的行数，任何一个数增加就算有进展。连续 `--max-stalled` 次（缺省 5）
  以退出码 3 结束且没有进展时停下，交给人处理；
- 先启动 run，再起 supervisor：它读的是 run 目录里的 `launch.sh`。

## 在已完结 run 的基础上接着跑（fork-run）

要把一个已完结 run 的前几个相位接着用（多加一轮学习、或把同一个库交给另一个模型做 test），
不重跑这些相位：

```bash
cd <deploy-base>/<commit>
.venv/bin/expedistill fork-run --source-run <runs-dir>/<源 run> \
    --config configs/experiments/<新配置>.yaml --run-dir <runs-dir>/<新 run 名> --through p05
RUNS_DIR=<runs-dir> WORKERS=128 ROLLOUT_WORKERS=64 scripts/remote/start_run_bench_host.sh <新配置> <新 run 名>
```

- 新配置必须以同样的前 K 个相位开头、定义相同，其余影响这些相位的键也要一致；不一致、
  源 run 还在跑或前缀未完成，都会在拷贝之前报错。具体规则见 README 的命令行一节；
- 新 run 的 manifest 在它自己第一次启动时按新配置冻结；`FORKED_FROM.md` 记录拷了什么、
  没拷什么。前缀的用量账本留在源 run；
- 先用 `--until-phase <前缀最后一个相位的 0 起下标>` 配一个必然失败的 LLM 端点（例如
  `EXPEDISTILL_LLM_ENDPOINT` 指向本机一个没人监听的端口）空跑一遍前缀：被拷贝的相位应当
  零模型调用地通过，用量账本为空，`tasks_digest` 与源 run 的 manifest 一致；通过后再去掉
  `--until-phase` 正式续跑；
- 同一台基准主机上同时跑两个 run 时，两个 run 共用网关的按请求 ID 限流和 Harbor：并发要
  分开估，不要两个都开满。

## 查看进度（在基准主机上）

```bash
RUN=<runs-dir>/<run 名>
kill -0 "$(cat $RUN/driver.pid)" && echo alive        # 存活只看 pidfile，不用 pgrep -f
cat $RUN/driver.pid.exit 2>/dev/null                    # driver 结束后才有：退出码
tail -f $RUN/driver.console.log
kill -0 "$(cat $RUN/supervisor.pid)" && tail $RUN/supervisor.log
cd <deploy-base>/<commit>
.venv/bin/expedistill summary --run-dir $RUN
```

## 审计（run 结束后，数字进结果表之前）

```bash
cd <deploy-base>/<commit>
.venv/bin/python scripts/audit/audit_run.py $RUN [--out <run 目录之外的路径>/audit.json]
```

- 逐项打印 `PASS` / `FAIL` / `WARN` / `INFO`，有任何 `FAIL` 时退出码为 1。检查项是设计正本
  第七节"run 跑完后的完整性检查"的 1–6 项：进度账本覆盖冻结序列；执行相位没有未接受的
  空洞、每张答卷都已判定、覆盖记录没有缺失（接受过的空洞列为 `WARN`）；学习相位的诊断、
  证据、回执、库工件对得上；test 两臂覆盖冻结的 test 列表。另外检查已存轨迹的编号与文件名、
  相位一致，列出失败账本和执行者被截断的回复；
- benchmark 是 `tb` 时另查：每条 skills 模式的轨迹都上传了读卡后缀；逐相位报告判为
  invalid 的 trial 数；本机能读到 Harbor 配置（`<remote_root>/configs/<run_id>.yaml`）时，
  弱模型 trial 的配置里出现 `reasoning_effort` 判为 `FAIL`；
- 审计只读，不往 run 目录里写；`--out` 把同样的结果写成 JSON，路径不能在 run 目录里；
- 有 `FAIL` 的 run 作废（在 run 目录写 `INVALID.md` 说明原因），不进结果表。

## 注意

- 基准主机若是共享机器，只在自己被分配的目录下工作，不碰他人的进程；
- driver 所在会话被清理或机器重启后，运行 `bash <run 目录>/launch.sh` 续跑，需要时再起
  supervisor。
