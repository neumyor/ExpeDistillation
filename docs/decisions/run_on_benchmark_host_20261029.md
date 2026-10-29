日期：2026-10-29
一句话：driver 搬到 node29 与 Harbor 同机运行，用 `EXPEDISTILL_TB_HOST=local` 切换传输，不改任何业务代码。
对应 log.md：2026-10-29 的“driver 迁到 node29 运行”条目。

# 决定：driver 与 Harbor 同机运行

## 背景

TB 的每次 trial 都通过本机的 `ssh`/`scp` 到 node29：提交、轮询、取结果、取 verifier 日志、
取轨迹，插件里共有 21 个调用点，轮询间隔内持续产生新的 ssh 连接。32 到 64 个工作线程
并发时会撞上 node29 对短时间内 ssh 连接数的限制；本机到 node29 的 VPN 一旦中断，
一个相位的全部单元会在重试预算内同时失败（P07 曾因此 117 个单元全部落成基础设施失败）。

## 候选方案

1. 继续经 ssh，加连接复用（ControlMaster）和更长的重试：能缓解握手次数，但 VPN 中断和
   对端限流仍然存在，只是降低频率。不采用。
2. 把 driver 搬到 node29，传输层换成本机命令（采用）：所有 ssh/scp 消失，问题从根上去掉。

## 决定

- `RemoteLink` 在 `EXPEDISTILL_TB_HOST=local` 时不走 ssh：`run` 用本机 `bash -c`，
  `put`/`get` 用文件复制。空值仍是配置错误，防止本机漏配后把远程命令跑在本机。
  业务逻辑（插件的提交、轮询、取结果）一行不改，测试用的 `FakeRemoteLink` 不受影响。
- 本机命令使用干净环境（只保留 `HOME`、`USER`、`LANG` 等和去掉 driver 虚拟环境的 `PATH`），
  与 ssh 登录会话看到的一致，driver 的 API 密钥不会传给 runner。
- 代码用 `rsync` 推送（node29 访问不了 GitHub），一个提交一个目录，从不覆盖：
  `/data2/niuyiming/expedistill_code/<commit>/`，符合“正式 run 固定在启动它的 checkout 上”。
- run 目录放 `/data2/niuyiming/expedistill_runs/<run 名>/`，结果、审计、分析都留在 node29，
  不拉回本机。
- 密钥放 `~/niuyiming/expedistill.env`，权限 600，启动脚本检查权限，不进 git。

## 后果

- ssh/scp 并发限制和 VPN 抖动不再影响实验。LLM 调用也从 node29 发出。
- 查看进度要登录 node29；`scripts/remote/README.md` 和根 README 写了完整流程。
- TB 的远程 run id 含 run 目录的真实路径，所以把已有的本机 run 搬到 node29（路径变了）
  不能续跑，只能在新位置新建 run。
- 冒烟结果（node29 本机模式，`git-leak-recovery`）：首跑、同 run id 复用、模拟崩溃后重新
  加入存活的 runner、技能进入沙箱，四个阶段通过。冒烟脚本里“step 0 必须出现
  `/opt/openclaw-skills/<task>/SKILL.md`”的检查是对旧版 vendor 协议写的，当前 vendor 后缀
  指向 `<available_skills>`，已改成检查后者；这是脚本与 vendor 协议的偏差，与本机模式无关。
- 未验证：32 个以上并发时 node29 的负载表现。上线后第一个 run 先从 8 个工作线程观察，
  再到 32。
