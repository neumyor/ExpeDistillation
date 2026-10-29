日期：2026-10-29
一句话：chunk 4（11 个 P07 失败 trial）的逐条归因汇总表与模式归纳。
对应 log.md：2026-10-29 的“P07 失败 trial 归因分析”条目。

# chunk 4（make-doom-for-mips / rstan-to-pystan / adaptive-rejection-sampler / fix-ocaml-gc / regex-log）

| trial | blocker_step | verdict | confidence |
|---|---|---|---|
| traj/tb:make-doom-for-mips/weak/00 | 314 | model_capability | high |
| traj/tb:make-doom-for-mips/weak/01 | 168 | model_capability | high |
| traj/tb:make-doom-for-mips/weak/02 | 120 | environment | medium |
| traj/tb:rstan-to-pystan/weak/00 | 101 | environment | medium |
| traj/tb:rstan-to-pystan/weak/01 | 52 | environment | medium |
| traj/tb:rstan-to-pystan/weak/02 | 59 | environment | medium |
| traj/tb:adaptive-rejection-sampler/weak/01 | 288 | model_capability | high |
| traj/tb:adaptive-rejection-sampler/weak/02 | 168 | model_capability | high |
| traj/tb:fix-ocaml-gc/weak/00 | 82 | environment | high |
| traj/tb:fix-ocaml-gc/weak/01 | 344 | model_capability | high |
| traj/tb:regex-log/weak/01 | 10 | model_capability | high |

## 反复出现的模式

1. **提交前没有用任务自己的验收口径验证**（5/11）。doom 两次都是“文件在 + BM 魔数”就收工，ARS/02 在自己测试连打 4 个 FAIL 之后仍 mark complete，regex-log 的自测集合里已经放了 `abc2023-05-01def` 这个同形反例却没看结果。
2. **交付物没落在要求的路径上**（ARS/01）。后半程全部投入 `/app/ars_new.R`，`/app/ars.R` 停在 277 步，verifier source 的还是旧文件；这是本组最典型的“做了但没交”。
3. **用替代品冒充实现**（doom/00、doom/01）。两者最终都把 vm.js 改成 qemu 包装；01 甚至因为 `execSync` 吞掉 stdout，把“没拿到输出”写成 “Process timed out - Doom ran successfully”。
4. **判分带宽比数值噪声还窄**（rstan/01、02）。两次都在 5–6 个判据里只差一个分量，差分 5.6e-4 与 3.9e-4，量级等于 2000 次抽样的 MC 标准误；其余参数（alpha/sigma/beta）全部落在区间内。
5. **终端通道一旦坏掉就无法恢复**（rstan/00、doom/02）。rstan/00 停在第 101 步的嵌套 heredoc，之后 109 步的 observation 恒为 `lines.a`；doom/02 停在未闭合引号里，harness 自己丢了 tmux server（`no server running on /tmp/tmux-0/default`），correctness 为 null，verifier_result 也为 null。
6. **判分侧的基础设施故障会直接作废一个 trial**（fix-ocaml-gc/00）。verifier 为复原 testsuite 做的 `git clone` 因 curl 92/HTTP2 中断失败，tests.txt 从未生成——agent 侧其实已经构建通过并跑通了 basic testsuite。
7. **打开的技能都停在“构建排错”层**。0006/0007 被反复打开，但没有任何一张已交付卡要求“在最终交付路径上用任务的验收命令断言输出”；最对口的 `traj/tb:build-cython-ext/strong/00/seg02`（测试不绿不提交、产物必须补到规定路径）和 `traj/tb:filter-js-from-html/strong/00/seg01`（提示符不回来怎么办）一次都没被打开。
8. **能力缺口：真正需要的两张卡还 pending**。`req/cdf02b6dde80`（构建后跑真实操作+完整测试，不能停在文件存在）与 `req/7ebf1db42112`（动手前把每个交付物路径和验收命令写死）恰好覆盖模式 1 与 2，但都未交付，属于“写了没给”。
9. **难度型失败仍然存在**（fix-ocaml-gc/01）。345 步反复 `make distclean/configure/make` 补丁 runtime，直到步数耗尽也没编译通过；这是唯一一条纯“模型调试能力不够”的失败路径。
