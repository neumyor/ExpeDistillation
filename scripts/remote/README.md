# 在 node29 上运行实验

driver 和 Harbor 都在 node29 上，本机不再发 ssh/scp。run 目录、分析、审计都留在
node29，本机只负责部署代码和查看。

## 目录

| 位置 | 内容 |
|---|---|
| `/data2/niuyiming/expedistill_code/<commit>/` | 一个提交一个目录，含 `src/`、`configs/`、`scripts/`、`.venv/`、`DEPLOYED_FROM`；从不覆盖 |
| `/data2/niuyiming/expedistill_runs/<run 名>/` | run 目录（`phases/`、`llm_requests/`、`driver.pid`、`driver.console.log`） |
| `/data2/niuyiming/expedistill/` | Harbor 的工作根（`jobs/`、`logs/`、`skills/`），已有，不动 |
| `~/niuyiming/expedistill.env` | 密钥与连接变量，权限 600，不进 git |

正式 run 固定在启动它的代码目录上续跑，所以每次改代码都新部署一个目录。

## 一次性准备：密钥文件

在 node29 上创建 `~/niuyiming/expedistill.env`，权限 600：

```bash
EXPEDISTILL_LLM_ENDPOINT=https://llm-center.modelbest.co/v1
EXPEDISTILL_LLM_API_KEY=<密钥>
EXPEDISTILL_TB_HOST=local
EXPEDISTILL_TB_ROOT=/data2/niuyiming/expedistill
EXPEDISTILL_TB_RUNNER=/data2/niuyiming/scripts/run_tencent_tb21_smoke_niuyiming.sh
```

`EXPEDISTILL_LLM_PROVIDER` 可选，缺省为 `openai_compat`。`EXPEDISTILL_TB_HOST=local` 表示 driver 就在基准主机上：命令走本机 `bash`，
文件走本机复制。留空仍是配置错误，防止本机漏配后把远程命令跑在本机。

## 部署

```bash
scripts/remote/deploy_node29.sh          # 提交后的 HEAD；脏工作区需要 --force
```

最后一行输出是 node29 上的代码目录。

## 启动与续跑

```bash
ssh node29
cd /data2/niuyiming/expedistill_code/<commit>
WORKERS=64 scripts/remote/start_run_node29.sh <配置名> [run 名]
```

run 名已存在就是续跑；不写则新建 `<配置 stem>_<时间>`。工人数不得低于 32。

## 查看与分析（都在 node29 上）

```bash
RUN=/data2/niuyiming/expedistill_runs/<run 名>
kill -0 "$(cat $RUN/driver.pid)" && echo alive      # 存活只看 pidfile，不用 pgrep -f
tail -f $RUN/driver.console.log
cd /data2/niuyiming/expedistill_code/<commit>
.venv/bin/python scripts/audit/audit_live_run.py --run-dir $RUN --out $RUN/audit.json
```

分析脚本读 run 目录、写到 `$RUN/analysis/`，不拉回本机。

## 注意

- node29 访问不了 GitHub，代码只能用 `rsync` 推送。
- 共享机器：只在 `~/niuyiming/` 与 `/data2/niuyiming/` 下工作，不碰他人进程。
- 本机模式下 runner 继承的是干净环境（与 ssh 登录会话一致），driver 的密钥不会泄给 runner。
