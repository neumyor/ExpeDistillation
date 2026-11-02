# git-multibranch / attempt 0（失败）

- trial：`traj/tb:git-multibranch/strong/00`；步数 11；读取的技能：无
- 主因：A_model_capability

## 1. 为什么失败 / 卡在哪里
- 卡点：判分脚本用 expect 以密码方式克隆并推送 main/dev 两个分支；这条 trial 的克隆没有出现密码提示，`git_push.exp` 第 9 行的 `expect eof` 报 `expect: spawn id exp4 not open`，push 从未发生，两个 curl 检查取不到内容，`assert "TEST PASSED" in result` 失败。
- 过程：
  - 第 0–1 步：确认 Ubuntu、git/sshd/nginx/openssl 齐全；git 用户不存在，sshd/nginx 未运行，8443 未监听，站点目录为空。
  - 第 2 步：一条命令建 git 用户（密码 password）、生成 root 的 ed25519 密钥并把公钥写入 git 账号的 `authorized_keys`，初始化 bare 仓库，写 nginx TLS 站点并启动 sshd/nginx。
  - 第 4 步：用默认 ssh 直接克隆本机的 git 仓库（没有 sshpass/askpass），克隆成功——证明 root→git 已能仅凭公钥免密登录。
  - 第 5 步：推送 main/dev，curl 两个端点分别返回 `main branch content` 与 `dev branch content`。
  - 第 7 步：重写 post-receive 部署脚本并删除分支引用清空仓库；第 8 步验证 0 引用、端点仍返回旧内容、`passwordauthentication yes`，随后标记完成。
- 直接原因：判分器的推送脚本依赖"克隆时出现密码提示"；a00 额外配置的 root 公钥让克隆无提示直接结束，expect 在 `expect eof` 处因 spawn id 已关闭而中止，分支没有推上去，部署自然没有发生。
- 与同题成功 attempt 的差异：
  - a00 第 2 步把 root 公钥安装为 git 账号的 `authorized_keys`；第 4 步的免密克隆就是直接后果。
  - 成功 a01 第 2 步只建用户 + 密码 + sshd 配置，没有安装任何 `authorized_keys`；第 4 步专门用 `PreferredAuthentications=password -o PubkeyAuthentication=no` + `SSH_ASKPASS` 验证密码路径，第 6 步再核对最终状态后完成。
  - 成功 a02 第 2 步同样只配密码；第 3 步用 askpass 验证密码推送；第 7 步在清空仓库后用脚本走完一次"推送 → 部署 → 两个端点校验"的闭环（打印 `PUSH_DEPLOY_MS:304` 与两段内容），第 8 步确认仓库为空后完成。
  - 两次成功都保证了判分时克隆会停在密码提示、expect 流程可以继续；a00 的免密路径让判分脚本失去了它等待的输入。

## 2. 技能库里有没有能解决它的技能
- 候选：
  - `bank/merge/0004`（相关性 中）——提交前门禁，[When to use] 覆盖"任务命名了产物、下一次动作就是标记完成"，并强调核对端到端行为；a00 第 8 步只核对了端点内容，没有按判分器会采用的连接方式复核。
  - `bank/merge/0006`（相关性 低）——通用的获取/配置/功能验证流程（含 2xx 探测与修复循环），但不涉及 sshd、git 服务端或 nginx。
  - 其余成员（视频 OCR、候选程序打分、模型拟合、数据统计、异步清理、SQLite/二进制恢复、DNA、HTML 过滤、调度器、路径追踪）（相关性 无）。
- 缺的能力：git over SSH 服务端（bare 仓库、post-receive 部署、nginx TLS 反代）以及"按判分器的认证方式复现"这一类；整库没有 git/ssh/nginx 技能。

## 3. 能解决问题的技能被触发了吗？为什么没解决
- 触发：没读（`meta.json` 的 `skills_read` 为空，`skills_read.md` 无内容）。
- 原因：库里没有直接对应的技能；最接近的 `bank/merge/0004` 只能覆盖"完成前按判分方式复核"这一环，且没有被选中。失败不来自技能内容错误，而来自模型自己选择的认证配置破坏了判分脚本的密码交互；读 0004 至多促成更贴近判分的复核，并不能直接教它不要安装 root 公钥。

## 4. 结论与依据
- 主因：A_model_capability
- 一句话依据：第 2 步安装 root 公钥使第 4 步起克隆免密，判分器的 expect 脚本需要密码提示，结果在 `expect eof` 报 `spawn id exp4 not open`，push 未发生、`TEST PASSED` 断言失败；两次成功都没有安装该公钥并显式验证了密码认证路径。
- 次要因素：整库缺少 git/ssh 服务端技能（D）；提交前门禁 `bank/merge/0004` 未被读取（C）；判分器的 expect 脚本对"无密码交互"不健壮（E 的次要面）。
