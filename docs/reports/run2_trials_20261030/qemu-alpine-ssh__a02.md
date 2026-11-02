# qemu-alpine-ssh / attempt 2（失败）

- trial：`traj/tb:qemu-alpine-ssh/strong/02`；步数 54；读取的技能：无
- 主因：E_environment

## 1. 为什么失败 / 卡在哪里
- 卡点：不在轨迹内——第 46–51 步模型已修好 SSH 配置并用 expect 证明密码登录可用；卡点在判分阶段：判分器启动 `sshpass` 时立即崩溃。
- 过程：
  - 第 0–4 步盘点环境（ISO、稀疏 qcow2、qemu 5.2.0、tmux、expect、无 KVM），第 4 步一次 `C-c` 作为文本发送触发警告；
  - 第 7–13 步 `-nographic` 启动只得到 "1024x768 Graphic mode"，无法交互；
  - 第 16–25 步自写 ISO9660 解析器从 ISO 中取出内核与 initramfs，改用 `-kernel/-initrd` 直接引导，第 25–28 步进入 OpenRC 与 `localhost login:`；
  - 第 31–46 步在 guest 内确认 uid=0、安装/启动 sshd，第 46 步首次 `ssh -p 2222` 得到 `Permission denied`（root 口令/配置未生效）；
  - 第 47–49 步读取 `/etc/ssh/sshd_config` 的注释项、追加 `PermitRootLogin yes`/`PasswordAuthentication yes` 并重启 sshd，第 49 步 expect 得到 `SSH_OK`/`SSH_TEST_SUCCESS`；
  - 第 51 步交互式 expect 再次成功（`INTERACTIVE_SSH_TEST_SUCCESS`）；第 52 步提交。
- 直接原因：判分器 `test_sshpass` 在 0.0015 秒内抛出 `FileNotFoundError: [Errno 2] No such file or directory: 'sshpass'`，测试从未执行到 `uname -r` 与 `assert "6.6.4-1-lts" in result.stdout`。判分脚本自身的 `apt-get install -y curl sshpass` 已把 sshpass 列入 NEW 包并开始下载，但 pytest 运行时二进制缺失——依赖安装没有落地，属判分环境问题。

## 2. 技能库里有没有能解决它的技能
- 候选：
  - `bank/merge/0006`（相关性 低）——缺失工件探测/安装/验证 + 终端纪律；可指导 guest 内装 openssh，但不涉及 QEMU 引导与串口。
  - `bank/merge/0000`（相关性 低）——终端门控（无提示符时发空等待、避免多行交互块）；本轨迹前 25 步大量卡在交互/图形模式上，这条能省时间，但与判分失败无关。
  - `bank/merge/0005`（相关性 低）——挂起/清理类调试；不适用。
- 缺的能力：QEMU 引导 + guest 网络/SSH 配置的系统管理流程；库里没有。

## 3. 能解决问题的技能被触发了吗？为什么没解决
- 触发：没读（`skills_read` 为空）。
- 原因：失败在判分环境，技能无法让判分器装上 sshpass；库中也没有能改变结局的技能。

## 4. 结论与依据
- 主因：E_environment
- 一句话依据：判分器以 `FileNotFoundError: 'sshpass'` 在 0.0015 秒内失败、未执行任何断言；而模型第 49/51 步的 expect 登录已成功，guest 为 Alpine 3.19 / 6.6.4-1-lts，端口转发可用。
- 次要因素：模型第 46 步才修好 `PermitRootLogin`（前面 30 余步花在引导与串口上），但最终状态正确；第 2/3 步也检查过宿主机没有 sshpass，同样未安装——任务说明未要求，判分脚本应自行安装，不作为主因。技能库缺少该类能力（D）。
