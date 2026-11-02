# qemu-alpine-ssh / attempt 1（失败）

- trial：`traj/tb:qemu-alpine-ssh/strong/01`；步数 18；读取的技能：无
- 主因：E_environment

## 1. 为什么失败 / 卡在哪里
- 卡点：不在轨迹内——第 12–14 步模型已用 expect 三次成功登录 2222 端口的 Alpine SSH；卡点在判分阶段：判分器启动 `sshpass` 时立即崩溃。
- 过程：
  - 第 0–2 步确认 `app/alpine.iso`、`alpine-disk.qcow2`、qemu-system-x86_64、tmux、expect 均在；
  - 第 3–5 步 `tmux new-session -d -s vm` 启动 QEMU（`-boot d -cdrom ... -hda ... -netdev user,hostfwd=tcp::2222-:22 -nographic -no-reboot`），第 6 步回车越过 ISOLINUX `boot:` 提示，第 6–7 步看到 `Welcome to Alpine Linux 3.19`、`Kernel 6.6.4-1-lts` 并 root 登录；
  - 第 8–10 步在 guest 内启网卡、`udhcpc` 拿到地址、确认 ISO 内 apks 仓库；
  - 第 11 步 `passwd` 设 root 密码 password123、`apk add openssh`、`PermitRootLogin yes`、`rc-service sshd start`（`status: started`）；
  - 第 12–14 步 expect 三次登录成功：`ALPINE_SSH_OK`、`EXACT_CMD_OK`、`FINAL_ALPINE_SSH_OK`，`nc -z localhost 2222` 为 `PORT_OPEN`；
  - 第 16 步提交。
- 直接原因：判分器 `test_sshpass` 第一时间执行 `subprocess.run(["sshpass", ...])`，0.0013 秒内抛出 `FileNotFoundError: [Errno 2] No such file or directory: 'sshpass'`，从未执行到 `uname -r` 的断言。判分脚本自身的 `apt-get install -y curl sshpass` 输出里 sshpass 出现在 NEW 包清单、下载已开始，但 pytest 进程里找不到该二进制——依赖安装没有生效，属判分环境问题。

## 2. 技能库里有没有能解决它的技能
- 候选：
  - `bank/merge/0006`（相关性 低）——缺失工具的探测/安装/功能验证与终端纪律；思路可用于 guest 内装 openssh，但不覆盖 QEMU 启动与端口转发。
  - `bank/merge/0000`（相关性 低）——终端门控；本轨迹里也有 `;14R` 等终端噪声（第 13–15 步 `bash: syntax error`），但最终状态已被 expect 验证。
  - `bank/merge/0005`（相关性 低）——挂起/清理类调试，与本失败无关。
- 缺的能力：QEMU/Alpine/SSH 系统管理流程；库里没有。

## 3. 能解决问题的技能被触发了吗？为什么没解决
- 触发：没读（`skills_read` 为空）。
- 原因：失败发生在判分环境，技能无法修复判分器的依赖安装；库里也没有能改变结局的技能。

## 4. 结论与依据
- 主因：E_environment
- 一句话依据：判分器以 `FileNotFoundError: 'sshpass'` 在 0.0013 秒内失败、未执行任何断言，而模型在第 12–14 步已用 expect 三次证明密码登录可用、`PORT_OPEN` 且内核为 6.6.4-1-lts。
- 次要因素：第 12 步曾检查 `command -v sshpass` 结果为空，模型仍选择 expect 验证而未在宿主机安装 sshpass；任务说明没要求该工具，判分脚本应自行安装，故不作为主因。技能库缺少该类能力（D）。
