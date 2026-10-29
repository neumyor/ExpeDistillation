# Chunk 5 — P07 失败 trial 归因

日期：2026-10-29
一句话：本 chunk 覆盖 10 个失败 trial（make-mips-interpreter ×3、sam-cell-seg ×3、circuit-fibsqrt ×2、git-multibranch ×2）的逐条归因表与模式归纳。
对应 log.md：2026-10-29 的“P07 失败 trial 归因分析”条目。

| trial | blocker_step | verdict | confidence |
|---|---|---|---|
| traj/tb:make-mips-interpreter/weak/00 | 134 | model_capability | high |
| traj/tb:make-mips-interpreter/weak/01 | 265 | model_capability | high |
| traj/tb:make-mips-interpreter/weak/02 | 77 | environment | high |
| traj/tb:sam-cell-seg/weak/00 | 86 | mixed | medium |
| traj/tb:sam-cell-seg/weak/01 | 69 | model_capability | high |
| traj/tb:sam-cell-seg/weak/02 | 3 | environment | medium |
| traj/tb:circuit-fibsqrt/weak/01 | 98 | model_capability | high |
| traj/tb:circuit-fibsqrt/weak/02 | 23 | model_capability | high |
| traj/tb:git-multibranch/weak/00 | 7 | mixed | medium |
| traj/tb:git-multibranch/weak/01 | 12 | mixed | medium |

计数：`model_capability` 5、`mixed` 3、`environment` 2。10 条里有 5 条以
`AgentTimeoutError`（7200 s）收尾。

**本组反复出现的模式**

1. 最突出的是“整文件重写代替定位一个 bug”：mips/00 与 mips/01 各用 300+
   步把 vm.js 从零重写十余次，而每次运行都暴露同一个具体缺陷——mips/00 是
   入口 `SP=48`、PC 乱跳、BSS 段大小解析成 1 GB，mips/01 是单条指令死循环
   `pc=0x43a254` 且 `sp=0x-40100058`（32 位回绕）。两条轨迹都在看到该信号后
   选择再写一版，直到超时；期间还转向 C 解释器、qemu-mipsel、改 Doom 二进制。
2. sam-cell-seg 的评测环境与题面严重不符：题面写明 torch/torchvision/
   opencv/Pillow/mobile_sam 均已安装，实际容器里连 PIL 都没有，也没有任何
   MobileSAM 权重；三条 trial 因此分化——00 手搭源码 + 从未真跑过的脚本
   （`predict(bbox=...)` 应为 `box`），01 用随机 dummy 权重跑出 48 个 cell
   只返回 1 个 mask 仍提交，02 把 19 分钟全部耗在装 torch/torchvision，
   最终连 convert_masks.py 都没写。环境问题是这组的主线，但 00/01 的交付物
   自检缺失同样是独立可复现的失败点。
3. “本地已有 oracle 却不拿它当门禁”贯穿 sam 与 circuit：sam/01 自己打印
   `Phase 2 done: 1 masks after NMS`、`Shape: (1, 11)` 后仍 mark complete；
   circuit/01 用 /app/sim 测得单门 OR/XOR/AND/NOT 全返回 0，却没有先修对
   信号序号就继续搭整张网表，最终 gates.txt 对所有输入输出 1；circuit/02 的
   生成器一直 SyntaxError，gates.txt 始终是原始示例（输出 N/2）。
4. git-multibranch 两条是同一个“多余改动触发的判分脆弱性”：模型为了免去
   交互输密码，把自造公钥写进 /home/git/.ssh/authorized_keys，于是 verifier
   的 expect clone 直接以 key 登录、看不到 `password:` 提示，脚本在
   `expect eof` 处报 `spawn id exp4 not open` 而中断，push 根本没发生。同题
   attempt 02 全程手动输密码、没有装 key，测试通过——说明功能本身可达，
   失败是“模型留下未授权的 key 认证 + 判分脚本只认密码提示”的叠加。
5. mips/02 是纯粹的执行环境故障：第 77 步一个长 heredoc 之后终端冻死，
   第 77–223 步（147 步、全轨迹 224 步）只有 5 种不同输出，模型反复发送
   PYEOF / Ctrl-C / Ctrl-D / tmux kill-pane 均无响应，任务不可能继续。
6. 技能使用极窄：10 条 trial 一共只打开过 3 张卡——`db-wal-recovery/.../seg02`
   （侦察可执行文件）、`build-cython-ext/.../seg00`（先盘点输入）、
   `bank/merge/0000`（远端资产恢复），且多为 step 0 的仪式性打开。真正对症的
   验收/修错卡（`bank-merge-0003`、`build-cython-ext/.../seg02`、
   `filter-js-from-html/.../seg01`）几乎从未触发；唯一一次打开 0003 的 sam/01
   也没有照做。
7. 交付集里没有任何 git/SSH/Nginx、MIPS 模拟器或门级网表相关技能，所以
   这几条的失败不能归给技能库缺失（missing_skill），而是模型能力与执行；
   但 pending 里 `req/7ebf1db42112`（封闭计划 + 验收命令）、
   `req/cdf02b6dde80`（必须真跑一次交付物）、`req/e20a68bdd559`（诊断装不完
   的安装）恰好吃住本组反复出现的问题——属于“写了但没交付”。
