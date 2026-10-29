# TB21 small20：exam 审阅修复后的 P02–P07 报告

**SUPERSEDED（2026-10-29 13:56）**：本报告基于的 run 存在考场来源缺陷——P03/P05
的场景全部取自 baseline 的 attempt 02，而技能锚点来自 attempt 00/01（0/37 命中），
另有 4 道题的切点越界。因此 P03/P05 判分、P04/P06 的审阅归因、以及本报告的
终测数字（0.4487 / 0.5897）都来自被污染的考场，**不能作为结果引用**。
修复见 `docs/archive/exam_scene_source_sheet_20261029.md`；需要新建 run 重跑
P03–P07。正文保留为过程记录，不再更新。P07 的逐 trial 失败归因
（`docs/reports/p07_failure_analysis_20261029/synthesis.md`）仍是对那批轨迹的
有效分析，但其“技能库”来自被污染的决策链。

日期：2026-10-29
一句话：这是 exam 验收相位的协议验证报告，不是方法有效性的结论；在 `d6cfbe1` 的审阅口径下跑完 P03–P07，技能库在 avg@3 与 pass@3 上都没有超过裸跑。
对应 log.md：2026-10-28 20:58“从 P02 分叉新 run”与 2026-10-29 报告条目。

## 1. 身份与口径

- run：`runs/tb21_p02p06_exam_small20_20261028_2053_p02fork/`
- 配置：`configs/experiments/tb21_p02p06_exam_small20_20261028.yaml`（schema 7），split = 20 train / 30 reserved / 39 test
- 代码：`d6cfbe1`“Review exam failures as paper answers tied to their own exam”
- 来源：P01/P02 逐字节复用 `runs/tb21_p02p06_exam_small20_20261028_1322/`；37 个 P02 技能取自其 `skill_bank.jsonl` 的 v1 行；P03–P07 全部在新代码下重跑（见该 run 的 `FORKED_FROM.md`）
- 旧 run 因审阅口径被替换而作废，见其 `SUPERSEDED.md`
- 预注册口径：avg@3 与 pass@3 各按框架既有定义报告；`avg_at_n` 以有有效判分的次数为分母，correction 见 §5

**本报告回答“exam 验收链路在修复后能否端到端跑通、结果形态如何”，不宣称技能库有效或无效的方法结论。**

## 2. P01–P06

| 相位 | 内容 | 结果 |
|---|---|---|
| P01 baseline | 20 task × 3 次 × 强/弱 = 40 单元 / 120 sheet | coverage 120/120；强 41/60 判对，弱 19/60 判对、40 判错、1 invalid |
| P02 learn_add | 17 条 add、76 段、5 个 trigger 族、37 个 cluster | 37 条 skill、全部 `added`、0 失败；`bank:7228e9add633`；2 个 add 任务无锚点技能（pytorch-model-recovery、torch-tensor-parallelism，两者证据均达标） |
| P03 exam | 37 道题，每技能一道 | 37/37 coverage present；**7 判对 / 30 判错**；1 道题曾因模型编造不存在的技能 id 失败，人工定点重试后成功（`PHASE3_RETRY.md`） |
| P04 learn_trial | 37 个 measured、30 个失败 trial | **16 keep + 21 revise**；30 条 trial finding；13 条新需求全部写成技能；库 50 |
| P05 exam | 34 道题（21 改写 + 13 新技能） | 34/34 present；**6 判对 / 28 判错** |
| P06 learn_trial | 34 个 measured、28 个失败 trial | **23 keep + 11 revise**；28 条 finding；10 条新需求全部落地；整理 10 次 merge 全部 kept；最终库 47（26 accepted + 21 pending） |

与修复前的同一 P02 库相比，revise 和新需求显著增多（旧：P04 9 改写 / 4 需求，P06 0 / 0），这是把失败归因到技能本身之后的预期变化；它说明审阅链路在工作，不说明改得对。

## 3. P07 终测

- 39 个 test task × 3 次；**116/117 个尝试落盘**，其中 114 个有判分
- 交付技能：**26 条**（库 47 条里的 accepted 集，交付指纹 `bank:3966be421464`）
- 最后一个单元 `traj/tb:circuit-fibsqrt/weak/00`：两轮完整重试后仍远端超时；按用户裁定**计为失败单元**，不写 accepted shortfall
- 该题另两次尝试已落盘且都判错，因此“计为失败”与“按 2 次有效样本计算”的 avg/pass 相同（该题都是 0）
- 另有 2 个尝试执行后没有判分（`make-doom-for-mips`、`feal-linear-cryptanalysis` 各 1 次），按框架口径记为 invalid、不计入 valid 分母

### 指标（预注册口径）

| 指标 | with_bank | 裸跑（qwen 外部 campaign，前 3 次） | 差值 |
|---|---|---|---|
| avg@3（conditional，仅计有判分次数） | **0.4487** | **0.4872** | −0.0385 |
| avg@3（严格分母 3，缺失/未判分记 0） | 0.4444 | — | — |
| pass@3 | **0.5897（23/39）** | **0.6154（24/39）** | −0.0256 |

- 裸跑不是本次重跑，而是配置钉住的 AgentBrew campaign（同 39 题、同 harness），按预注册对齐取每题前 3 次奖励；该臂没有缺失单元。
- 逐题配对（39 题）：**5 题 with_bank 更好、6 题更差、28 题持平**；pass@3 上多 1 题、少 2 题。胜负比与抛硬币无法区分。
- 三个 task 被标为 infra（`circuit-fibsqrt` 有 1 个缺失尝试；另两个是上面的未判分尝试），不是结果错误，但读数时要连同 §3 的说明一起看。

## 4. 完整性审计

`scripts/audit/audit_live_run.py --run-dir <run> --out tmp/audit_p02fork_20261029.json`：

- **0 error / 0 warn / 2 info**
- INFO 1：P07 不完整（1 个 infra 缺口，summary 的 `infra_missing` 列出）
- INFO 2：exam 模式没有 `stage4_acceptance/live_run/` 子树，该部分无可审计内容（这是 exam 模式的浅层审计）

## 5. 需要记住的偏离与事故

1. P03 有 1 道题在 `EXAM_TRIGGER` 被业务校验拒绝（模型给了不存在的 `seg00`），代码将其视为本 run 终态；人工删行重试后成功。报告按“1 次业务校验失败 + 定点重试成功”记，不写成零错误。
2. 第一次 P07 启动时 VPN 路由消失，117 个单元全部耗尽 infra 重试、零落盘；VPN 恢复后按原命令续跑，只重试未完成单元。
3. 最后一个单元两轮重试仍超时，按用户裁定计失败；run 的 phase summary 仍保留 `infra_missing` 记录，没有写 accepted shortfall。
4. `console.log` 第一次为 0 字节是 Python 重定向时的块缓冲；后续启动带 `PYTHONUNBUFFERED=1`。exam 相位本身没有 K1 心跳，进度看 phase 行与 answer_sheets。

## 6. 结论

在本次协议验证中，exam → 逐 trial 审阅 → keep/revise/新增需求 → 复考 → 终测这条链路可以端到端跑通，账本自洽（审计 0 error / 0 warn）。修复后的审阅确实开始把失败归因到技能本身（P04/P06 产生 21/11 条改写与 13/10 条新需求），但最终技能库在 39 个 test task 上没有超过裸跑：avg@3 低 0.0385、pass@3 低 0.0256，逐题多数持平。这个数字只能作为该协议验证的一部分，不能当作方法有效性的结论；它也不能与口径被替换前的旧 run 比较。

## 7. 复现

```bash
.venv/bin/expedistill run \
  --config configs/experiments/tb21_p02p06_exam_small20_20261028.yaml \
  --run-dir runs/tb21_p02p06_exam_small20_20261028_2053_p02fork \
  --workers 32 --k1-stagger 30 --llm-timeout 1500 \
  --llm-infra-retries 5 --llm-infra-backoff 10 --until-phase 6
```

P01/P02 的重建见 `tmp/fork_p02_run.py`（一次性脚本，不进 git）与该 run 的 `FORKED_FROM.md`。原始请求级证据在 `llm_requests/`（547 次调用：529 ok、16 infra 重试、2 unscored），逐 trial 结果在 `phases/*/answer_sheets/`。
