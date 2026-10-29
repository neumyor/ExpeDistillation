# 在基准主机上运行实验

TB 的 rollout 由基准主机上的 Harbor 执行。driver 也放在这台主机上时，所有命令和
文件操作都在本机完成，不再经过 ssh/scp，所以 driver 所在机器到基准主机之间的网络
波动和 ssh 并发连接限制不再影响实验。run 目录、审计和分析都留在基准主机上，结果
不拉回。

下面用尖括号表示每个部署自己决定的值。真实值只写在被 gitignore 的 `.env` 和基准
主机上的密钥文件里，不写进仓库。

## 目录约定

| 位置 | 内容 |
|---|---|
| `<deploy-base>/<commit>/` | 一个提交一个目录：`src/`、`configs/`、`scripts/`、`.venv/`、`DEPLOYED_FROM`；从不覆盖 |
| `<runs-dir>/<run 名>/` | run 目录：`phases/`、`llm_requests/`、`driver.pid`、`driver.console.log` |
| `<bench-root>/` | Harbor 的工作根（`jobs/`、`logs/`、`skills/`），即 `EXPEDISTILL_TB_ROOT` |
| `<env-file>` | 密钥和连接变量，权限 600，不进 git |

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
  落在其中某个前缀之下，写错路径时报错而不是写进别人的目录；
- `EXPEDISTILL_LLM_PROVIDER` 可选，缺省为 `openai_compat`；
- 本机模式下 runner 继承的是干净环境（与 ssh 登录会话一致），driver 的密钥不会传给
  runner。

## 部署（在 driver 原来所在的机器上）

```bash
scripts/remote/deploy_bench_host.sh          # 部署已提交的 HEAD；有未提交改动时要加 --force
```

脚本用 `rsync` 推送代码（不要求基准主机能访问代码托管服务），在远端建 venv 并
`pip install -e .`，最后一行输出是远端的代码目录。

## 启动与续跑（在基准主机上）

```bash
cd <deploy-base>/<commit>
RUNS_DIR=<runs-dir> WORKERS=64 scripts/remote/start_run_bench_host.sh <配置名> [run 名] [其余 CLI 参数]
```

- run 名已存在就是续跑；不写则新建 `<配置 stem>_<YYYYMMDD_HHMM>`；
- 工作进程数不得低于 32；
- driver 是独立会话并写 pidfile，ssh 断开不影响它；
- 续跑必须使用同一个代码目录和同一个 run 名：TB 的远程 run id 含 run 目录的真实
  路径，换路径等于新 run。

## 查看与分析（都在基准主机上）

```bash
RUN=<runs-dir>/<run 名>
kill -0 "$(cat $RUN/driver.pid)" && echo alive      # 存活只看 pidfile，不用 pgrep -f
tail -f $RUN/driver.console.log
cd <deploy-base>/<commit>
.venv/bin/expedistill summary --run-dir $RUN
.venv/bin/python scripts/audit/audit_live_run.py --run-dir $RUN --out $RUN/audit.json
```

## 注意

- 基准主机若是共享机器，只在自己被分配的目录下工作，不碰他人的进程；
- driver 所在会话被清理或机器重启后，用同一条启动命令续跑。
