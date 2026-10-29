# Chunk 3 — P07 失败 trial 归因

日期：2026-10-29
一句话：本 chunk 覆盖 11 个失败 trial（gpt2-codegolf ×3、qemu-alpine-ssh ×3、winning-avg-corewars ×3、large-scale-text-editing/02、portfolio-optimization/00）的逐条归因表。
对应 log.md：2026-10-29 的“P07 失败 trial 归因分析”条目。

| trial | blocker_step | verdict | confidence |
|---|---|---|---|
| traj/tb:gpt2-codegolf/weak/00 | 211 | model_capability | high |
| traj/tb:gpt2-codegolf/weak/01 | 250 | model_capability | high |
| traj/tb:gpt2-codegolf/weak/02 | 232 | model_capability | high |
| traj/tb:qemu-alpine-ssh/weak/00 | 151 | environment | high |
| traj/tb:qemu-alpine-ssh/weak/01 | 178 | environment | high |
| traj/tb:qemu-alpine-ssh/weak/02 | 309 | mixed | medium |
| traj/tb:winning-avg-corewars/weak/00 | 155 | model_capability | high |
| traj/tb:winning-avg-corewars/weak/01 | 175 | model_capability | high |
| traj/tb:winning-avg-corewars/weak/02 | 130 | model_capability | high |
| traj/tb:large-scale-text-editing/weak/02 | 50 | model_capability | high |
| traj/tb:portfolio-optimization/weak/00 | 7 | environment | medium |

计数：`model_capability` 7、`environment` 3、`mixed` 1。

**本组反复出现的模式**

1. 最大的一类是“模型自己耗尽了执行预算”：gpt2 ×3 与 corewars/01 都以
   `AgentTimeoutError`（7200 s）收尾，超时前都在反复重写实现而不是收敛；
   gpt2/00 最后只剩 334 字节没有 `main` 的桩，gpt2/02 是 5557 字节且编译不过。
2. 与 GPT-2 同类的“交付物自检缺失”也出现在别处：gpt2/02 从未检查
   `gpt2.c < 5000` 字节或能否 gcc 通过；large-scale-text-editing/02 内容
   已 FULL_MATCH，却把任务规定的 `:wq`/`:x` 写成 `:wq!`，只输在格式检查。
3. corewars 三连败都卡在同一个对手 stone.red（66%、5%、68%，门槛 75%），
   模型均在自己看到不达标数字后继续提交；技能库没有 Redcode 策略内容。
4. 两个 qemu trial（00/01）其实已经跑通 `ssh -p 2222`，失败纯在判分侧：
   verifier 的 apt 因 curl/libcurl4 依赖冲突装不上 sshpass，测试直接
   `FileNotFoundError`——这两个 trial 的数字不应计入模型能力。
5. 唯一相关的 qemu 技能只讲串口 console，不含 SSH hostfwd；qemu/02 因此
   一直在用已废弃的 `-redir tcp:2222::22`，端口始终不开。
6. portfolio/00 更像计时边缘问题：正确性全过、6000/8000 过线，只有 N=5000
   实测 1.12x 对 1.2x，模型本地却测得 1.4x。
7. 技能的使用面很窄：11 个 trial 几乎都只打开了 step 0 的“先盘点输入”卡，
   规划/验收类（bank-merge/0003）和 pending 的封闭计划技能从未被触发。
