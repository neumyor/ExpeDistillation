# qemu-alpine-ssh / attempt 0（失败）

- trial：`traj/tb:qemu-alpine-ssh/strong/00`；步数 35；读取的技能：无
- 主因：E_environment

## 1. 为什么失败 / 卡在哪里
- 卡点：不在轨迹内——第 31–32 步模型已用 expect 从宿主机成功登录 2222 端口的 Alpine SSH；卡点在判分阶段：判分器启动 `sshpass` 时立即崩溃。
- 过程：
  - 第 0–4 步盘点 `app/alpine.iso`（957 MB）与 `alpine-disk.qcow2`（稀疏 32 GiB），确认 qemu-system-x86_64、qemu-img、tmux 可用，无 KVM、无 `file`/iso 工具；
  - 第 5–14 步反复处理启动与串口：先 `-nographic` 被 ISOLINUX 的 `boot:` 提示挡住，后改 unix socket 串口 + 自写 Python 桥接（`/tmp/vmconsole.py` + FIFO）；
  - 第 27–30 步在 guest 内 `apk add --no-progress openssh`（输出 `APK_EXIT=0`、`/usr/sbin/sshd` 存在），改 `PermitRootLogin yes`/`PasswordAuthentication yes` 并 `rc-service sshd restart`；
  - 第 31–32 步用 expect 两次从宿主机登录：`SSH_SUCCESS`、`EXACT_SSH_SUCCESS`，`FINAL_SSH_EXIT=0`；guest 内核为 6.6.4-1-lts；
  - 第 33 步提交完成标记。
- 直接原因：判分器 `test_sshpass` 的第一个动作就是 `subprocess.run(["sshpass", ...])`，在 0.0013 秒内抛出 `FileNotFoundError: [Errno 2] No such file or directory: 'sshpass'`，测试从未跑到 `uname -r` 与 `assert "6.6.4-1-lts" in result.stdout`。判分脚本自身负责安装该依赖（`apt-get install -y curl sshpass`），本次输出显示它已经把 sshpass 列入 NEW 包并开始下载，但 pytest 运行时二进制并不存在——安装事务没有落地，属判分环境依赖链失败，不是任务产出问题。

## 2. 技能库里有没有能解决它的技能
- 候选：
  - `bank/merge/0006`（相关性 低）——"缺失工件的探测/安装/构建/功能验证 + 终端纪律（一次一条命令、确认新提示符）"；对排查缺失工具与串口交互有借鉴价值，但完全不涉及 QEMU 启动与 guest 内 SSH 配置。
  - `bank/merge/0000`（相关性 低）——终端门控（无提示符先发空等待、半截命令先 Ctrl-C）；本轨迹里多次出现 `;14R`、把 `C-c` 当命令发等终端噪声，这条能减少摩擦，但与本 trial 的失败点（判分器缺 sshpass）无关。
  - `bank/merge/0005`（相关性 低）——调试挂起/取消/清理类问题；与本次判分器崩溃无关。
- 缺的能力：QEMU 启动 Alpine、串口/登录、guest 内装 openssh 并做端口转发与口令登录的完整流程；库里没有这类系统管理技能。

## 3. 能解决问题的技能被触发了吗？为什么没解决
- 触发：没读（`skills_read` 为空、`skills_read.md` 无内容）。
- 原因：本 trial 的失败发生在判分环境（依赖安装失败），任何技能都无法修复判分器的 apt 安装；库中也没有能改变结局的系统管理技能。

## 4. 结论与依据
- 主因：E_environment
- 一句话依据：判分器 `sshpass` 缺失导致测试在 0.0013 秒内以 `FileNotFoundError` 结束，而模型自己的两次 expect 登录（第 31–32 步）已证明 `ssh -p 2222 root@localhost` + `password123` 可用、guest 内核为 6.6.4-1-lts。
- 次要因素：第 2 步已发现宿主机没有 `sshpass`，模型选择 expect 完成验证但没有在宿主机预装 sshpass；不过任务说明并未要求该工具，判分脚本本应自行安装，因此不作为主因。技能库缺少 QEMU/SSH 类技能（D）。
