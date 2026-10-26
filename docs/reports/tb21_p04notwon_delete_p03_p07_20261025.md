# TB21 P03/P05/P07 最终报告（run `tb21_p04notwon_delete_20261024_1130`）

日期：2026-10-25
一句话：P04 严格删除口径跑通 P04–P07，P07 完成 84/84；最终库 38 条技能，相对历史外部 campaign 的 held-out avg@3 无提升，pass@3 +1 题（不显著）。
对应 log.md：2026-10-25 的 P07 续跑完成与最终报告条目。

## 0. 结论摘要

1. P03（训练集、166 条库）相对同题无技能 baseline 的 live 成绩为 **19/90 → 35/89，+18.21pp**；这是 in-sample 增益。
2. P04 严格口径得到 **52 改写 / 53 保留 / 61 删除**，库从 166 降到 105；P05 只测 52 条 pending，成绩 **2/42 → 2/41，+0.12pp**，14 组里只有 1 组 better。改写没有救回失败组。
3. P06 后库 **38 条全部 accepted**（6 条合并 + 32 条原件），相对 P02 的 166 条压缩 **77.1%**。
4. P07 held-out 28 题 ×3：with_bank **40/82 有效成功，avg@3 0.47619，pass@3 19/28 (0.67857)**；外部 control **40/84，avg@3 0.47619，pass@3 18/28 (0.64286)**。**avg@3 差 0，pass@3 +3.57pp（多 1 题）**；配对 bootstrap 95% CI 都跨 0。
5. 与上一轮 30 条库的 run 在同一 control 下相比：**avg@3 同为 0.47619；pass@3 30 条库为 0.71429，38 条库为 0.67857，差 −3.57pp（少 1 题）**。两轮都不是对 control 的显著提升。
6. 这是一个 held-out 负结果：P03 的 in-sample 增益没有复现到 28 道 held-out 题；“改写失败技能”也没有带来第二轮增益。

## 1. Run 与审计

```text
run: runs/tb21_p04notwon_delete_20261024_1130
config: configs/experiments/tb21_split_folder_skilllevel_p04notwon_delete_20261024.yaml
P04 bank: bank:984f1a18453b (105)
P06 bank: bank:d25017456036 (38)
P07 complete: true
P07 stored: 84/84
P07 infra_missing: with_bank [], control []
P07 infra_shortfall_accepted: false
p07 resume driver exit: 0
（原 supervisor exit 3 是 2026-10-24 断网那一轮留下的；续跑 driver 正常退出。）
```

只读审计：

```bash
python3 scripts/audit/audit_live_run.py \
  --run-dir runs/tb21_p04notwon_delete_20261024_1130 \
  --out tmp/live_audit_p07_final.json
```

```text
findings: 0 error / 0 warn / 0 info
exit 0
```

审计报告：`tmp/live_audit_p07_final.json`。

## 2. 实际交付技能条数

不是只看库的 38 条，做了三层核对：

| 层 | 检查 | 结果 |
|---|---|---|
| 单元清单 | 28 个 test unit 的 `aux.skill_folder.files` | 每个 unit 38 条，38 个 skill_id 唯一 |
| 磁盘 | `phases/p07_test/skill_folder/*/SKILL.md` | 38 个目录、38 个非空文件、无多余目录、无缺失 |
| 运行时 | 84 条 stored trajectory 的 `<available_skills>` | 84/84 都是 38 条技能（第 39 个 “SKILL.md” 是说明文字），28/28 task 全覆盖 |

**实际交付 = 38 条 distinct skills。** 每个 test task 看到的是同一份 38 条 folder。

## 3. 28 道题的 task 级 infra

两层要分开看：

- **断网 hole**：2026-10-24 17:00–17:13 VPN 中断期间失败的 unit（53 个，全部 `InfrastructureError`，stage `k1`）；续跑后全部补齐，最终 84/84 stored，无 remaining hole。它们不再作为 infra 缺失进入 `infra_missing`，而是作为重新执行后的正常 attempt 计入最终分母；这说明这些 task 曾在断网窗口内重跑过。
- **最终无判分 attempt**：stored trajectory 的 `correctness=None`，来自 tmux/runtime 错误，K1 不把它们当 infra 重试。最终 2 条：
  - `tb:adaptive-rejection-sampler` a0：`RuntimeError: ... failed to send non-blocking keys: command send-keys: unknown flag -I`
  - `tb:qemu-alpine-ssh` a0：`RuntimeError: ... failed to send non-blocking keys: no server running on /tmp/tmux-0/default`

这两条使 valid 从 84 降到 82（这两题各 2 个有效 attempt）。由于两题的 3 个 attempt 都是 0 success，**把 None 当失败或按有效分母计算，avg@3 与 pass@3 都不变**。`tb:qemu-alpine-ssh` 的 a0 在上一轮 30 条库 run 里也是 None（同样是 tmux server 问题），两轮模式一致，说明这是该题可复现的通道问题，不是本轮新增的偶发故障。

覆盖情况：**27/28 task 在断网窗口内至少有一个 hole；唯一干净的是 `tb:cancel-async-tasks`。** 最终指标层面有 infra 的只有 2 个 task（上表）。完整 28 题明细：

| task | 断网 holes | 无判分 attempt | 最终 success | pass@3 |
|---|---:|---|---:|---:|
| `tb:adaptive-rejection-sampler` | 3 | a0 | 0/3 | no |
| `tb:qemu-alpine-ssh` | 1 | a0 | 0/3 | no |
| `tb:caffe-cifar-10` | 3 | — | 2/3 | yes |
| `tb:feal-differential-cryptanalysis` | 3 | — | 0/3 | no |
| `tb:mailman` | 3 | — | 3/3 | yes |
| `tb:make-doom-for-mips` | 3 | — | 0/3 | no |
| `tb:password-recovery` | 3 | — | 1/3 | yes |
| `tb:regex-chess` | 3 | — | 0/3 | no |
| `tb:rstan-to-pystan` | 3 | — | 1/3 | yes |
| `tb:torch-pipeline-parallelism` | 3 | — | 0/3 | no |
| `tb:winning-avg-corewars` | 3 | — | 0/3 | no |
| `tb:compile-compcert` | 2 | — | 3/3 | yes |
| `tb:crack-7z-hash` | 2 | — | 3/3 | yes |
| `tb:dna-assembly` | 2 | — | 0/3 | no |
| `tb:hf-model-inference` | 2 | — | 1/3 | yes |
| `tb:large-scale-text-editing` | 2 | — | 2/3 | yes |
| `tb:regex-log` | 2 | — | 3/3 | yes |
| `tb:build-cython-ext` | 1 | — | 1/3 | yes |
| `tb:build-pov-ray` | 1 | — | 1/3 | yes |
| `tb:configure-git-webserver` | 1 | — | 3/3 | yes |
| `tb:kv-store-grpc` | 1 | — | 2/3 | yes |
| `tb:mcmc-sampling-stan` | 1 | — | 2/3 | yes |
| `tb:modernize-scientific-stack` | 1 | — | 3/3 | yes |
| `tb:prove-plus-comm` | 1 | — | 3/3 | yes |
| `tb:pytorch-model-cli` | 1 | — | 2/3 | yes |
| `tb:sqlite-with-gcov` | 1 | — | 1/3 | yes |
| `tb:vulnerable-secret` | 1 | — | 3/3 | yes |
| `tb:cancel-async-tasks` | 0 | — | 0/3 | no |

## 4. P03 / P05 / P07 指标与样本量

### P03 live（训练集，30 组，166 条库）

| 项 | 值 |
|---|---:|
| 组 / task 数 | 30 |
| baseline attempts | 90 |
| live attempts | 90（valid 89，1 条 unknown） |
| baseline success | 19/90 = 0.2111 |
| live success | 35/89 = 0.3933 |
| 原始成功率差 | **+18.21pp** |
| 组均值差 | 0.2111 → 0.4000（+18.89pp） |
| 组判定 | 16 better / 14 false |

唯一 live valid=2 的组是 `tb:feal-linear-cryptanalysis`（2/2 success）。原始口径 +18.21pp，组均值口径 +18.89pp；两者都表明 P03 有明显 in-sample 增益。

### P05 live（14 组，52 条 revised/pending）

| 项 | 值 |
|---|---:|
| 组 / task 数 | 14 |
| baseline attempts | 42 |
| live attempts | 42（valid 41，1 条 unknown） |
| baseline success | 2/42 = 0.0476 |
| live success | 2/41 = 0.0488 |
| 原始成功率差 | **+0.12pp** |
| 组均值差 | 0.0476 → 0.0476（+0.00pp） |
| 组判定 | 1 better / 13 false |

唯一 better 组是 `tb:chess-best-move`（4 个成员）。P05 是“改写没赢技能”的检验：几乎没有提升。

### P07 test（28 道 held-out 题 ×3）

| 项 | with_bank | control（外部 campaign） | delta |
|---|---:|---:|---:|
| tasks | 28 | 28 | — |
| attempts | 84 | 84（n=3 slice） | — |
| valid | 82 | 84 | — |
| raw success | 40/82 = 0.4878 | 40/84 = 0.4762 | +1.16pp |
| avg@3（逐题均值，预注册） | **0.47619** | **0.47619** | **0.00000** |
| avg@3 conservative | 0.47619 | 0.47619 | 0.00000 |
| pass@3 | 19/28 = **0.67857** | 18/28 = **0.64286** | **+3.57pp** |
| infra_tasks | 2 | 0 | — |

说明：`avg@3` 是 28 个 task 的成功率的算术平均（每个 task 用其有效 attempt 作分母），不是 40/82 的 pooled rate；两者在本题集上分别给出 0.47619 与 0.4878。**预注册主指标是 avg@3**，所以 headline 是“0 差”。

pass@3 的 discordant pairs（with_bank vs control）：

```text
with_bank 过、control 不过: tb:build-cython-ext, tb:rstan-to-pystan  (+2)
control 过、with_bank 不过: tb:cancel-async-tasks                    (-1)
净 +1 题
```

配对 bootstrap（按 task 重采样，20k 次，事后描述，不是预注册检验）：

```text
avg@3 diff 95% CI: [-9.52pp, +8.33pp]
pass@3 diff 95% CI: [-7.14pp, +14.29pp]
```

两个区间都跨 0。n=28，任何 1–2 题的差异都不足以下结论。

## 5. 对照口径与限制

1. **control 是复用的历史外部 campaign，不是本 run 新跑的 bare arm。** 来源 `configs/seed/tb21_agentbrew_campaign_summary.json`，sha256 `1b5c483cebdc35d8c44c2370de2a76c4e6ef06f607fe9368e06815b730666da2`。P07 的 28 个 task 与 seed 中 qwen rewards 的 task 逐条匹配（28/28）；control 取 `rewards[:3]`，与 with_bank 的 3 次 attempt 对齐。
2. **seed 每个 task 只有 `n / rewards / success_rate` 三个字段，`n=5`，没有 attempt 序号、时间戳、难度或排序元数据。** 因此 `rewards[:3]` 依赖“5 次抽样可交换”的假设；seed 无法证明这一点。报告里明确写“无法确定排序语义”。
3. **same tasks / same model / same depth，但不是随机化对照。** with_bank 是 2026-10-25 新执行，control 是历史 campaign；执行时间、节点状态、模型服务状态都不同。结论只能表述为“相对历史 campaign 的对照”，不能表述为“随机对照实验”。
4. **上一轮 run 的对照可比，但不是同一套库的重复测量。** `runs/tb21_split_folder_usage_20261022_1845` 使用相同 seed/sha、相同 28 题、相同 3 次深度；但 P02 起点是 176 条（本 run 166 条），P04 协议是 task_level（本 run 是 skill_level 严格口径），最终交付 30 条（本 run 38 条）。
5. **两轮都使用同一个 control。** 因此 30 vs 38 的差值是“同一对照下的两个 with_bank 臂”，比各自与历史 campaign 的差值更有信息量；但受第 4 条限制。

## 6. 30 条库 vs 38 条库（同一 control）

| run | P02 起点 | 交付技能 | with_bank avg@3 | control avg@3 | Δavg | with_bank pass@3 | control pass@3 | Δpass |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `tb21_split_folder_usage_20261022_1845` | 176 | 30 | 0.47619 | 0.47619 | 0.00000 | 0.71429 (20/28) | 0.64286 (18/28) | +7.14pp |
| `tb21_p04notwon_delete_20261024_1130` | 166 | 38 | 0.47619 | 0.47619 | 0.00000 | 0.67857 (19/28) | 0.64286 (18/28) | +3.57pp |
| **new − old** | — | **+8** | **0.00000** | — | **0.00000** | **−3.57pp** | — | **−3.57pp** |

**同一对照下的差值：avg@3 完全相同；pass@3 新库少 1 题（−3.57pp）。** 也就是说，把库从 30 条扩到 38 条，没有带来 held-out avg 提升，pass@3 反而少 1 题；两个差都远小于抽样噪声。

### 6.1 口径敏感性（重要）

两轮的 `avg@3 = 0.47619` 不是“同样的原始成功率”：

| 口径 | 30 条库 run | 38 条库 run |
|---|---:|---:|
| raw success / valid | 39/81 = 0.4815 | 40/82 = 0.4878 |
| 预注册 avg@3（有效 attempt 作分母） | 0.47619 | 0.47619 |
| 缺失 attempt 记失败的 avg@3（非预注册） | **0.46429** | **0.47619** |
| pass@3 | 20/28 = 0.71429 | 19/28 = 0.67857 |

30 条库的 run 有 3 条 `correctness=None`（qemu a0、build-pov-ray a2、feal a2），38 条库的 run 有 2 条（adaptive a0、qemu a0）。预注册指标把 None 从分母排除；如果改用“缺失=失败”的更严格口径，30 条库降到 0.46429，38 条库仍是 0.47619，**新库反而高 1.19pp**。因此“两轮 avg 相同”只在预注册口径下成立，方向对口径敏感。报告 headline 用预注册口径，但不把“相同”说成稳健结论。

## 7. 压缩与读取分析

### 7.1 压缩

```text
P02 新增技能        166
P04 后库            105   （严格删除 61；改写 52、保留 53）
P05 待测            52    （14 组）
P06 后交付          38    （6 条合并 + 32 条原件，全部 accepted）
P02 → P07 压缩      77.1%（166 → 38）
P04 → P06 压缩      63.8%（105 → 38）
```

上一轮 30 条库的压缩是 176 → 30 = **82.95%**。本轮的库更大（38 vs 30），起点更小（166 vs 176）。

### 7.2 P07 实际读取

- 84 个 attempt 中 **82 个至少读 1 条技能**，2 个 0 读。
- 38 条交付技能中 **只有 15 条被读过，23 条从未被读**。
- 124 次读取事件中，4 张合并卡占 **101 次（81.5%）**：

```text
bank/merge/0002  37
bank/merge/0003  31
bank/merge/0001  20
bank/merge/0000  13
其余 11 条原件        23
```

- 按读取次数分层（描述性，混杂）：

| 读取次数 | attempts | valid | success | rate |
|---|---:|---:|---:|---:|
| 0 | 2 | 2 | 1 | 0.500 |
| 1 | 50 | 48 | 30 | 0.625 |
| 2+ | 32 | 32 | 9 | 0.281 |

读得多的 attempt 成功率更低，最可能的解释是“任务更难/更不确定时才多读”，不能读成“多读导致失败”。

P03/P05 的读取几乎是全量：P03 90/90 attempt 至少读 1 条，105/166 条技能被读；P05 42/42 至少读 1 条，40/52 条被读。因此 P03/P05 层面没有“读 vs 不读”的干净对照，只有技能级“是否被读过”的分布。

### 7.3 P03 won/lost 分层（附录，混杂）

最终 38 条库里，**37 条来自 P03 better 组，1 条来自 P03 lost 组**：`traj/tb:chess-best-move/strong/02/seg00/skill`（该组在 P05 变成 better，随后 accepted）。因此按 P03 won/lost 分层几乎是退化的：P07 里 79 个 attempt 只读 won 组技能（39/77 = 50.6%），1 个只读 lost 组技能（0/1），2 个 mixed（0/2），2 个 0 读（1/2）。这个分层不能支持“won 组技能更有效”的因果结论；它只说明严格剪枝后库里几乎只剩 won 组来源的技能。

## 8. 结论与限制

1. **P03 的 in-sample 增益在 30 道训练题上成立（+18.21pp），但没有复现到 held-out P07（avg@3 0）。** 两轮不同规模的库（30/38）都在同一 control 下得到 avg@3 0 差；这是本次最稳的负结果。
2. **P05 的改写没有救回失败组**：14 组只有 1 组 better，2/42 → 2/41。P04 的“改写没赢技能”在当前协议下没有产生第二轮增益。
3. **P07 pass@3 +3.57pp（多 1 题）不足以支撑“技能提升 pass”**：n=28，CI 跨 0，且同一对照下 30 条库的 pass 更高。
4. **库规模从 30 扩到 38 没有收益**；在预注册 avg@3 上两轮相同，在 pass@3 上新库少 1 题，在“缺失=失败”口径上新库高 1.19pp。方向不一致，不能声称 38 条优于 30 条。
5. **读取集中度很高**：38 条里只有 15 条被读，4 张合并卡占 81.5% 的读取。交付 38 条不等于 38 条都被使用；后续若要压缩，优先看这 4 张卡和 23 条从未被读的技能。
6. **控制限制**：历史外部 campaign、非随机化、seed n=5 取前 3、无排序元数据；不能把差值解释为纯技能效应。
7. 本次未提交、未推送；所有改动仍在工作区。

## 工件

- run：`runs/tb21_p04notwon_delete_20261024_1130/`
- 审计：`tmp/live_audit_p07_final.json`
- P07 summary：`runs/tb21_p04notwon_delete_20261024_1130/phases/p07_test/phase_summary.json`
- 任务级 infra 明细：本文件第 3 节；机器可读来源 `phases/p07_test/with_bank/store/failed_units.jsonl` 与 `stage1_baseline/trajectories/`
- 交付核对：`phases/p07_test/units.jsonl`（28 个 unit 的 `aux.skill_folder.files`）、`phases/p07_test/skill_folder/`
- 决策记录：`docs/decisions/p04_p06_revise_only_not_won_20261024.md`
- 上一轮对照：`runs/tb21_split_folder_usage_20261022_1845/phases/p07_test/phase_summary.json`
